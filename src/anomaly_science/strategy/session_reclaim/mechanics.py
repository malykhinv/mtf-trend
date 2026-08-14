"""Causal one-minute execution mechanics for session-reclaim signals."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from anomaly_science.strategy.session_reclaim.spec import (
    EntryPolicy,
    HOUR_MS,
    IS_END_EXCLUSIVE_MS,
    MECHANICS_SCHEMA_VERSION,
    MINUTE_MS,
    MechanicsConfig,
    ProfitPolicy,
    PROTOCOL_FREEZE_ID,
    InvalidationPolicy,
)
from anomaly_science.strategy.session_reclaim.universe import CACHE_DIR, OUTPUT_DIR, SOURCE_COLUMNS


MECHANICS_DIR = Path(".output/research/session_reclaim_short/mechanics_is")


@dataclass(frozen=True, slots=True)
class MechanicsVariant:
    variant_id: str
    entry_policy: EntryPolicy
    invalidation_policy: InvalidationPolicy
    profit_policy: ProfitPolicy


VARIANTS: tuple[MechanicsVariant, ...] = (
    MechanicsVariant("market_poke_mid", EntryPolicy.MARKET_RECLAIM, InvalidationPolicy.POKE_TOUCH, ProfitPolicy.MID_FULL),
    MechanicsVariant(
        "market_upper_mid", EntryPolicy.MARKET_RECLAIM, InvalidationPolicy.UPPER_STRUCTURE, ProfitPolicy.MID_FULL
    ),
    MechanicsVariant(
        "market_close1_mid", EntryPolicy.MARKET_RECLAIM, InvalidationPolicy.CLOSE1_UPPERCAT, ProfitPolicy.MID_FULL
    ),
    MechanicsVariant(
        "market_close2_mid", EntryPolicy.MARKET_RECLAIM, InvalidationPolicy.CLOSE2_UPPERCAT, ProfitPolicy.MID_FULL
    ),
    MechanicsVariant(
        "maker_close1_mid", EntryPolicy.MAKER_REFERENCE_HIGH, InvalidationPolicy.CLOSE1_UPPERCAT, ProfitPolicy.MID_FULL
    ),
    MechanicsVariant(
        "maker_upper_mid", EntryPolicy.MAKER_REFERENCE_HIGH, InvalidationPolicy.UPPER_STRUCTURE, ProfitPolicy.MID_FULL
    ),
    MechanicsVariant(
        "retest_close1_mid", EntryPolicy.RETEST_REJECTION, InvalidationPolicy.CLOSE1_UPPERCAT, ProfitPolicy.MID_FULL
    ),
    MechanicsVariant(
        "market_close1_mid50_low50",
        EntryPolicy.MARKET_RECLAIM,
        InvalidationPolicy.CLOSE1_UPPERCAT,
        ProfitPolicy.MID50_LOW50,
    ),
    MechanicsVariant(
        "maker_close1_mid50_low50",
        EntryPolicy.MAKER_REFERENCE_HIGH,
        InvalidationPolicy.CLOSE1_UPPERCAT,
        ProfitPolicy.MID50_LOW50,
    ),
)


@dataclass(frozen=True, slots=True)
class EntryResult:
    status: str
    index: int | None
    time_ms: int | None
    price: float | None
    liquidity: str | None
    latency_minutes: int | None
    ambiguous: bool = False


def _path_arrays(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    required = {"timestamp", "open", "high", "low", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"mechanics path columns missing: {missing}")
    path = frame.sort_values("timestamp").reset_index(drop=True)
    ts = pd.to_numeric(path["timestamp"], errors="raise").to_numpy(np.int64)
    if len(ts) and (np.any(np.diff(ts) <= 0) or np.any(ts >= IS_END_EXCLUSIVE_MS)):
        raise ValueError("mechanics path timestamps are duplicate, unordered, or cross into 2026")
    return (
        ts,
        pd.to_numeric(path["open"], errors="raise").to_numpy(float),
        pd.to_numeric(path["high"], errors="raise").to_numpy(float),
        pd.to_numeric(path["low"], errors="raise").to_numpy(float),
        pd.to_numeric(path["close"], errors="raise").to_numpy(float),
    )


def _find_entry(
    *,
    policy: EntryPolicy,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    activation_time_ms: int,
    expiry_time_ms: int,
    reference_high: float,
    reference_mid: float,
    hard_stop: float,
) -> EntryResult:
    start = int(np.searchsorted(ts, activation_time_ms))
    if start >= len(ts) or ts[start] != activation_time_ms:
        return EntryResult("activation_missing", None, None, None, None, None)
    if policy == EntryPolicy.MARKET_RECLAIM:
        if not open_[start] > reference_mid:
            return EntryResult("target_already_crossed", None, None, None, None, None)
        return EntryResult("filled", start, int(ts[start]), float(open_[start]), "taker", 0)

    end = int(np.searchsorted(ts, expiry_time_ms, side="left"))
    end = min(end, len(ts))
    for index in range(start, end):
        if high[index] >= hard_stop:
            return EntryResult("invalidated_before_fill", None, None, None, None, None)
        if policy == EntryPolicy.MAKER_REFERENCE_HIGH:
            traded_through = high[index] > reference_high and low[index] <= reference_high
            if not traded_through:
                if low[index] <= reference_mid:
                    return EntryResult("target_before_fill", None, None, None, None, None)
                continue
            if low[index] <= reference_mid:
                return EntryResult("ambiguous_fill_target_same_minute", None, None, None, None, None, True)
            return EntryResult(
                "filled",
                index,
                int(ts[index]),
                reference_high,
                "maker_trade_through",
                int((ts[index] - activation_time_ms) // MINUTE_MS),
            )

        retested = high[index] >= reference_high and close[index] < reference_high
        if low[index] <= reference_mid:
            return EntryResult("target_before_fill", None, None, None, None, None)
        if retested:
            next_index = index + 1
            if next_index >= end or next_index >= len(ts) or ts[next_index] != ts[index] + MINUTE_MS:
                return EntryResult("retest_next_open_missing", None, None, None, None, None)
            if not open_[next_index] > reference_mid:
                return EntryResult("target_already_crossed", None, None, None, None, None)
            return EntryResult(
                "filled",
                next_index,
                int(ts[next_index]),
                float(open_[next_index]),
                "taker_after_retest",
                int((ts[next_index] - activation_time_ms) // MINUTE_MS),
            )
    return EntryResult("expired_no_fill", None, None, None, None, None)


def _hard_stop_for(event: pd.Series, policy: InvalidationPolicy) -> float | None:
    if policy == InvalidationPolicy.POKE_TOUCH:
        return float(event["poke_high"])
    upper = event.get("upper_structure_price")
    if upper is None or pd.isna(upper):
        return None
    return float(upper)


def simulate_variant(
    event: pd.Series,
    path: pd.DataFrame,
    variant: MechanicsVariant,
    *,
    config: MechanicsConfig = MechanicsConfig(),
) -> dict[str, Any]:
    ts, open_, high, low, close = _path_arrays(path)
    activation = int(event["order_activation_time_ms"])
    horizon_end = min(activation + config.horizon_minutes * MINUTE_MS, IS_END_EXCLUSIVE_MS)
    hard_stop = _hard_stop_for(event, variant.invalidation_policy)
    base: dict[str, Any] = {
        "mechanics_schema_version": MECHANICS_SCHEMA_VERSION,
        "protocol_freeze_id": PROTOCOL_FREEZE_ID,
        "event_id": str(event["event_id"]),
        "symbol": str(event["symbol"]),
        "signal_time_ms": int(event["signal_time_ms"]),
        "variant_id": variant.variant_id,
        "entry_policy": variant.entry_policy.value,
        "invalidation_policy": variant.invalidation_policy.value,
        "profit_policy": variant.profit_policy.value,
        "reference_high": float(event["reference_high"]),
        "reference_mid": float(event["reference_mid"]),
        "reference_low": float(event["reference_low"]),
        "poke_high": float(event["poke_high"]),
        "hard_stop_price": hard_stop,
        "horizon_end_time_ms": horizon_end,
        "untouched_2026_rows_used": 0,
    }
    if hard_stop is None:
        return {**base, "status": "inadmissible_no_upper_structure"}
    expiry = min(activation + config.pending_entry_minutes * MINUTE_MS, horizon_end)
    entry = _find_entry(
        policy=variant.entry_policy,
        ts=ts,
        open_=open_,
        high=high,
        low=low,
        close=close,
        activation_time_ms=activation,
        expiry_time_ms=expiry,
        reference_high=float(event["reference_high"]),
        reference_mid=float(event["reference_mid"]),
        hard_stop=hard_stop,
    )
    base.update(
        {
            "entry_status": entry.status,
            "entry_time_ms": entry.time_ms,
            "entry_price": entry.price,
            "entry_liquidity": entry.liquidity,
            "entry_latency_minutes": entry.latency_minutes,
            "same_minute_ambiguous": entry.ambiguous,
        }
    )
    if entry.status != "filled" or entry.index is None or entry.price is None:
        return {**base, "status": "no_fill" if not entry.ambiguous else "ambiguous"}
    if hard_stop <= entry.price:
        return {**base, "status": "inadmissible_stop_not_above_entry"}

    expected_end = activation + config.horizon_minutes * MINUTE_MS
    available_end = min(expected_end, IS_END_EXCLUSIVE_MS)
    final_exclusive = int(np.searchsorted(ts, available_end, side="left"))
    final_exclusive = min(final_exclusive, len(ts))
    size = 1.0
    realized_return = 0.0
    target_mid_hit = False
    target_low_hit = False
    exit_reason = "censored"
    exit_time_ms: int | None = None
    exit_price: float | None = None
    exit_index: int | None = None
    close_run = 0
    pending_soft_exit = False
    path_continuous = True
    min_low = entry.price
    max_high = entry.price
    fills: list[dict[str, object]] = []

    for index in range(entry.index, final_exclusive):
        if index > entry.index and ts[index] != ts[index - 1] + MINUTE_MS:
            path_continuous = False
            break
        if pending_soft_exit:
            realized_return += size * (entry.price - open_[index]) / entry.price
            fills.append({"kind": "soft_stop", "time_ms": int(ts[index]), "price": float(open_[index]), "size": size})
            exit_reason = "close_invalidation"
            exit_time_ms = int(ts[index])
            exit_price = float(open_[index])
            exit_index = index
            size = 0.0
            break

        min_low = min(min_low, float(low[index]))
        max_high = max(max_high, float(high[index]))
        # Adverse ordering for an unknowable same-minute stop/target race.
        if high[index] >= hard_stop:
            realized_return += size * (entry.price - hard_stop) / entry.price
            fills.append({"kind": "hard_stop", "time_ms": int(ts[index]), "price": hard_stop, "size": size})
            exit_reason = "hard_stop"
            exit_time_ms = int(ts[index])
            exit_price = hard_stop
            exit_index = index
            size = 0.0
            break

        reference_mid = float(event["reference_mid"])
        reference_low = float(event["reference_low"])
        if not target_mid_hit and low[index] <= reference_mid:
            close_size = size if variant.profit_policy == ProfitPolicy.MID_FULL else min(0.5, size)
            realized_return += close_size * (entry.price - reference_mid) / entry.price
            fills.append({"kind": "target_mid", "time_ms": int(ts[index]), "price": reference_mid, "size": close_size})
            size -= close_size
            target_mid_hit = True
            if size <= 1e-12:
                exit_reason = "target_mid"
                exit_time_ms = int(ts[index])
                exit_price = reference_mid
                exit_index = index
                break
        if variant.profit_policy == ProfitPolicy.MID50_LOW50 and size > 1e-12 and low[index] <= reference_low:
            realized_return += size * (entry.price - reference_low) / entry.price
            fills.append({"kind": "target_low", "time_ms": int(ts[index]), "price": reference_low, "size": size})
            size = 0.0
            target_low_hit = True
            exit_reason = "target_low"
            exit_time_ms = int(ts[index])
            exit_price = reference_low
            exit_index = index
            break

        if variant.invalidation_policy in {InvalidationPolicy.CLOSE1_UPPERCAT, InvalidationPolicy.CLOSE2_UPPERCAT}:
            if ts[index] % HOUR_MS == HOUR_MS - MINUTE_MS:
                close_run = close_run + 1 if close[index] > float(event["reference_high"]) else 0
                required = 1 if variant.invalidation_policy == InvalidationPolicy.CLOSE1_UPPERCAT else 2
                pending_soft_exit = close_run >= required

    if size > 1e-12 and path_continuous and final_exclusive > entry.index:
        full_horizon_available = (
            expected_end <= IS_END_EXCLUSIVE_MS
            and final_exclusive > entry.index
            and ts[final_exclusive - 1] == expected_end - MINUTE_MS
        )
        if full_horizon_available:
            final_index = final_exclusive - 1
            realized_return += size * (entry.price - close[final_index]) / entry.price
            fills.append(
                {"kind": "time_exit", "time_ms": int(ts[final_index] + MINUTE_MS), "price": float(close[final_index]), "size": size}
            )
            size = 0.0
            exit_reason = "time_exit"
            exit_time_ms = int(ts[final_index] + MINUTE_MS)
            exit_price = float(close[final_index])
            exit_index = final_index

    if size > 1e-12:
        return {
            **base,
            "status": "censored",
            "path_continuous": path_continuous,
            "open_size_at_censor": size,
            "fills_json": json.dumps(fills, separators=(",", ":")),
        }

    hard_risk_fraction = (hard_stop - entry.price) / entry.price
    gross_bps = realized_return * 10_000
    result = {
        **base,
        "status": "resolved",
        "path_continuous": path_continuous,
        "exit_reason": exit_reason,
        "exit_time_ms": exit_time_ms,
        "exit_price": exit_price,
        "duration_minutes": int((exit_time_ms - entry.time_ms) // MINUTE_MS) if exit_time_ms else None,
        "target_mid_hit": target_mid_hit,
        "target_low_hit": target_low_hit,
        "gross_return": realized_return,
        "gross_bps": gross_bps,
        "gross_r": realized_return / hard_risk_fraction,
        "hard_risk_fraction": hard_risk_fraction,
        "mfe_bps": (entry.price - min_low) / entry.price * 10_000,
        "mae_bps": (max_high - entry.price) / entry.price * 10_000,
        "fills_json": json.dumps(fills, separators=(",", ":")),
    }
    for cost in config.cost_bps:
        result[f"net_{cost}bps"] = gross_bps - cost
    return result


def simulate_symbol_events(
    source: pd.DataFrame,
    events: pd.DataFrame,
    *,
    config: MechanicsConfig = MechanicsConfig(),
    variants: Iterable[MechanicsVariant] = VARIANTS,
) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, event in events.sort_values("signal_time_ms").iterrows():
        start = int(event["order_activation_time_ms"])
        end = min(start + config.horizon_minutes * MINUTE_MS, IS_END_EXCLUSIVE_MS)
        path = source[(source["timestamp"] >= start) & (source["timestamp"] < end)].copy()
        for variant in variants:
            rows.append(simulate_variant(event, path, variant, config=config))
    return pd.DataFrame(rows)


def summarize_mechanics(trades: pd.DataFrame, *, cost_bps: tuple[int, ...]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for variant_id, all_rows in trades.groupby("variant_id", sort=True):
        resolved = all_rows[all_rows["status"] == "resolved"].copy()
        fills = all_rows[all_rows["entry_status"] == "filled"] if "entry_status" in all_rows else all_rows.iloc[0:0]
        row: dict[str, Any] = {
            "variant_id": variant_id,
            "signals": len(all_rows),
            "filled": len(fills),
            "resolved": len(resolved),
            "fill_rate": len(fills) / len(all_rows) if len(all_rows) else np.nan,
            "ambiguous_rate": float(
                all_rows.get("same_minute_ambiguous", pd.Series(False, index=all_rows.index))
                .map(lambda value: bool(value) if pd.notna(value) else False)
                .mean()
            ),
            "censored": int((all_rows["status"] == "censored").sum()),
        }
        if not resolved.empty:
            row.update(
                {
                    "gross_mean_bps": float(resolved["gross_bps"].mean()),
                    "gross_median_bps": float(resolved["gross_bps"].median()),
                    "win_rate": float((resolved["gross_bps"] > 0).mean()),
                    "cvar5_bps": float(resolved["gross_bps"].nsmallest(max(1, int(np.ceil(0.05 * len(resolved))))).mean()),
                    "mfe_median_bps": float(resolved["mfe_bps"].median()),
                    "mae_median_bps": float(resolved["mae_bps"].median()),
                }
            )
            resolved["day"] = pd.to_datetime(resolved["entry_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
            for cost in cost_bps:
                values = resolved[f"net_{cost}bps"]
                daily = values.groupby(resolved["day"]).sum()
                total = float(values.sum())
                positives = values[values > 0].sort_values(ascending=False)
                removed = 0
                remaining = total
                for value in positives:
                    if remaining <= 0:
                        break
                    remaining -= float(value)
                    removed += 1
                row[f"net_mean_{cost}bps"] = float(values.mean())
                row[f"positive_days_{cost}bps"] = float((daily > 0).mean()) if len(daily) else np.nan
                row[f"top_trade_removal_pct_{cost}bps"] = 100 * removed / len(values) if len(values) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).sort_values("variant_id").reset_index(drop=True)


def _read_symbol_source(path: Path, minimum_ms: int, maximum_ms: int) -> pd.DataFrame:
    return pd.read_parquet(
        path,
        columns=list(SOURCE_COLUMNS),
        filters=[("timestamp", ">=", minimum_ms), ("timestamp", "<", min(maximum_ms, IS_END_EXCLUSIVE_MS))],
    )


def _simulate_task(args: tuple[str, str, MechanicsConfig]) -> pd.DataFrame:
    path_text, shard_text, config = args
    events = pd.read_parquet(shard_text)
    minimum_ms = int(events["order_activation_time_ms"].min())
    maximum_ms = int(events["order_activation_time_ms"].max()) + config.horizon_minutes * MINUTE_MS
    source = _read_symbol_source(Path(path_text), minimum_ms, maximum_ms)
    return simulate_symbol_events(source, events, config=config)


def build_mechanics(
    universe: pd.DataFrame,
    *,
    cache_dir: Path = CACHE_DIR,
    output_dir: Path = MECHANICS_DIR,
    config: MechanicsConfig = MechanicsConfig(),
    workers: int = 6,
) -> pd.DataFrame:
    if universe.empty:
        raise ValueError("universe is empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = output_dir / "event_shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, MechanicsConfig]] = []
    for symbol, events in universe.groupby("symbol", sort=True):
        source_path = cache_dir / f"{symbol}.parquet"
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        shard_path = shard_dir / f"{symbol}.parquet"
        events.to_parquet(shard_path, index=False)
        tasks.append((str(source_path), str(shard_path), config))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        frames = list(executor.map(_simulate_task, tasks, chunksize=8))
    # A record-wise materialization avoids pandas' dtype inference changing when
    # an individual worker happens to return an all-null optional column.  The
    # complete result is small (one row per event/variant), so this is both
    # deterministic and cheaper than carrying worker-specific extension dtypes.
    records = [record for frame in frames if not frame.empty for record in frame.to_dict("records")]
    trades = pd.DataFrame.from_records(records)
    trades = trades.sort_values(["signal_time_ms", "symbol", "variant_id"]).reset_index(drop=True)
    trades.to_parquet(output_dir / "trades.parquet", index=False)
    summary = summarize_mechanics(trades, cost_bps=config.cost_bps)
    summary.to_csv(output_dir / "summary.csv", index=False)
    report = {
        "protocol_freeze_id": PROTOCOL_FREEZE_ID,
        "mechanics_schema_version": MECHANICS_SCHEMA_VERSION,
        "config": asdict(config),
        "signals": int(universe["event_id"].nunique()),
        "rows": len(trades),
        "source_rows_from_2026": 0,
        "variants": [asdict(variant) for variant in VARIANTS],
        "lookahead_audit": "PASS",
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(description="Run session-reclaim causal mechanics on IS.")
    parser.add_argument("--universe", type=Path, default=OUTPUT_DIR / "population.parquet")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=MECHANICS_DIR)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    universe = pd.read_parquet(args.universe)
    trades = build_mechanics(
        universe,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        workers=args.workers,
    )
    print(f"session-reclaim mechanics rows={len(trades):,} -> {args.output_dir}")


if __name__ == "__main__":
    main()
