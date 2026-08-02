"""Causal coarse local-flow, OI, and liquidation features at selected events."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from anomaly_science.strategy.residual_absorption.data import read_is_event_window_minutes
from anomaly_science.strategy.residual_absorption.spec import RESIDUAL_ABSORPTION_RESEARCH_SPLIT

LOCAL_ACTIVITY_SCHEMA_VERSION = "residual_absorption_local_activity_v1"
WINDOWS_MINUTES = (5, 15, 30)
MIN_ABSOLUTE_RETURN = 1e-4
MIN_ABSOLUTE_SIGNED_QUOTE = 1_000.0

RAW_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "trade_count",
    "taker_buy_quote_volume",
    "open_interest",
    "long_liquidations_vol",
    "short_liquidations_vol",
    "oi_available",
    "missing_oi_flag",
    "liquidation_available",
    "missing_liquidation_flag",
)


def compute_local_window_features(
    minutes: pd.DataFrame,
    *,
    snapshot_time_ms: int,
    window_minutes: int,
) -> dict[str, object]:
    """Compute one registered window from bars closed no later than snapshot."""

    start = snapshot_time_ms - window_minutes * 60_000
    window = minutes.loc[
        minutes["timestamp"].ge(start) & minutes["timestamp"].lt(snapshot_time_ms)
    ].sort_values("timestamp", kind="mergesort")
    prefix = f"local_{window_minutes}m_"
    coverage = len(window) / window_minutes
    output: dict[str, object] = {
        f"{prefix}minute_count": len(window),
        f"{prefix}coverage": coverage,
        f"{prefix}missing": len(window) != window_minutes,
    }
    if window.empty:
        return output
    first_open = float(window.iloc[0]["open"])
    last_close = float(window.iloc[-1]["close"])
    log_return = math.log(last_close / first_open) if first_open > 0 and last_close > 0 else np.nan
    quote = float(window["quote_volume"].sum())
    trades = float(window["trade_count"].sum())
    taker_buy = float(window["taker_buy_quote_volume"].sum())
    signed_quote = 2.0 * taker_buy - quote
    prices = np.concatenate(([first_open], window["close"].to_numpy(dtype=float)))
    log_steps = np.diff(np.log(prices)) if np.all(prices > 0.0) else np.asarray([np.nan])
    path_length = float(np.nansum(np.abs(log_steps)))
    oi_mask = window["oi_available"].astype(bool) & window["open_interest"].notna()
    oi_values = window.loc[oi_mask, "open_interest"].to_numpy(dtype=float)
    oi_change = float(oi_values[-1] - oi_values[0]) if len(oi_values) >= 2 else np.nan
    oi_relative = (
        oi_change / float(oi_values[0])
        if len(oi_values) >= 2 and float(oi_values[0]) != 0.0
        else np.nan
    )
    long_liq = float(window["long_liquidations_vol"].fillna(0.0).sum())
    short_liq = float(window["short_liquidations_vol"].fillna(0.0).sum())
    liquidation_total = long_liq + short_liq
    output.update(
        {
            f"{prefix}log_return": log_return,
            f"{prefix}quote_volume": quote,
            f"{prefix}trade_count": trades,
            f"{prefix}signed_taker_quote_volume": signed_quote,
            f"{prefix}taker_imbalance": signed_quote / quote if quote > 0.0 else np.nan,
            f"{prefix}high_low_range": (
                float(window["high"].max() / window["low"].min() - 1.0)
                if float(window["low"].min()) > 0.0
                else np.nan
            ),
            f"{prefix}realized_volatility": float(np.sqrt(np.nansum(log_steps**2))),
            f"{prefix}path_efficiency": (
                abs(log_return) / path_length
                if path_length > 0.0 and np.isfinite(log_return)
                else np.nan
            ),
            f"{prefix}return_per_million_quote": (
                log_return / (quote / 1_000_000.0)
                if quote > 0.0 and np.isfinite(log_return)
                else np.nan
            ),
            f"{prefix}oi_start": float(oi_values[0]) if len(oi_values) else np.nan,
            f"{prefix}oi_end": float(oi_values[-1]) if len(oi_values) else np.nan,
            f"{prefix}oi_change": oi_change,
            f"{prefix}oi_relative_change": oi_relative,
            f"{prefix}oi_coverage": float(oi_mask.mean()),
            f"{prefix}oi_missing_flag_rate": float(window["missing_oi_flag"].astype(bool).mean()),
            f"{prefix}long_liquidation_volume": long_liq,
            f"{prefix}short_liquidation_volume": short_liq,
            f"{prefix}liquidation_imbalance": (
                (short_liq - long_liq) / liquidation_total
                if liquidation_total > 0.0
                else np.nan
            ),
            f"{prefix}liquidation_observed": liquidation_total > 0.0,
            f"{prefix}liquidation_coverage": float(
                window["liquidation_available"].astype(bool).mean()
            ),
            f"{prefix}liquidation_missing_flag_rate": float(
                window["missing_liquidation_flag"].astype(bool).mean()
            ),
        }
    )
    return output


def build_stage2_local_features(
    *,
    stage1_dir: str | Path,
    source_dir: str | Path,
    progress: Callable[[str], None] | None = print,
) -> Path:
    root = Path(stage1_dir)
    profiles = pd.read_parquet(root / "residual_response_profiles_is.parquet")
    rows: list[dict[str, object]] = []
    groups = tuple(profiles.groupby("symbol", sort=True))
    for completed, (symbol, group) in enumerate(groups, start=1):
        source = Path(source_dir) / f"{symbol}.parquet"
        if not source.is_file():
            continue
        minutes = read_is_event_window_minutes(
            source,
            snapshot_times_ms=group["snapshot_time_ms"],
            window_minutes=max(WINDOWS_MINUTES),
            columns=RAW_COLUMNS,
        )
        rows.extend(
            compute_symbol_local_features(
                minutes,
                events=group,
                symbol=str(symbol),
            ).to_dict(orient="records")
        )
        if progress and (completed % 50 == 0 or completed == len(groups)):
            progress(f"stage2 local activity {completed}/{len(groups)}")
    output = root / "stage2_local_activity_features_is.parquet"
    frame = pd.DataFrame(rows).sort_values(["snapshot_time_ms", "symbol"])
    if bool((frame["feature_cutoff_time_ms"] > frame["snapshot_time_ms"]).any()):
        raise ValueError("stage2 local feature cutoff exceeds snapshot")
    frame.to_parquet(output, index=False, compression="zstd")
    audit = {
        "audit_version": "stage2_local_activity_audit_v1",
        "status": "PASS" if len(frame) == len(profiles) else "FAIL",
        "feature_row_count": len(frame),
        "profile_row_count": len(profiles),
        "key_unique": not bool(frame.duplicated(["event_id", "symbol"]).any()),
        "oos_rows": int((frame["snapshot_time_ms"] >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms).sum()),
    }
    (root / "stage2_local_activity_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    if audit["status"] != "PASS" or not audit["key_unique"] or audit["oos_rows"]:
        raise ValueError("stage2 local activity audit failed")
    return output


def compute_symbol_local_features(
    minutes: pd.DataFrame,
    *,
    events: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    """Vectorized exact equivalent of all registered windows for one symbol."""

    required_events = {"event_id", "snapshot_time_ms", "impulse_direction"}
    missing = sorted(required_events.difference(events.columns))
    if missing:
        raise ValueError(f"local feature events missing columns: {missing}")
    ordered = events.sort_values("snapshot_time_ms", kind="mergesort").reset_index(drop=True)
    snapshots = ordered["snapshot_time_ms"].to_numpy(dtype=np.int64)
    for snapshot in snapshots:
        RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(int(snapshot))
    event_count = len(ordered)
    offsets = np.arange(max(WINDOWS_MINUTES), 0, -1, dtype=np.int16)
    grid = pd.DataFrame(
        {
            "event_row": np.repeat(np.arange(event_count, dtype=np.int64), len(offsets)),
            "minute_offset": np.tile(offsets, event_count),
            "timestamp": np.repeat(snapshots, len(offsets))
            - np.tile(offsets.astype(np.int64), event_count) * 60_000,
        }
    )
    expanded = grid.merge(
        minutes,
        on="timestamp",
        how="left",
        validate="many_to_one",
        sort=False,
    ).sort_values(["event_row", "timestamp"], kind="mergesort")
    result = pd.DataFrame(
        {
            "schema_version": LOCAL_ACTIVITY_SCHEMA_VERSION,
            "event_id": ordered["event_id"].astype(str),
            "symbol": symbol,
            "snapshot_time_ms": snapshots,
            "feature_cutoff_time_ms": snapshots,
            "impulse_direction": ordered["impulse_direction"].to_numpy(dtype=np.int8),
        }
    )
    for window_minutes in WINDOWS_MINUTES:
        window = _aggregate_vectorized_window(
            expanded,
            window_minutes=window_minutes,
            event_count=event_count,
        )
        result = pd.concat([result, window.reset_index(drop=True)], axis=1)
    signed_flow = result["local_15m_signed_taker_quote_volume"].to_numpy(dtype=float)
    price_return = result["local_15m_log_return"].to_numpy(dtype=float)
    oi_change = result["local_15m_oi_change"].to_numpy(dtype=float)
    direction = result["impulse_direction"].to_numpy(dtype=float)
    flow_valid = np.isfinite(signed_flow) & (
        np.abs(signed_flow) >= MIN_ABSOLUTE_SIGNED_QUOTE
    )
    return_valid = np.isfinite(price_return) & (
        np.abs(price_return) >= MIN_ABSOLUTE_RETURN
    )
    result["local_15m_price_flow_alignment"] = price_return * signed_flow
    result["local_15m_direction_adjusted_taker_imbalance"] = direction * result[
        "local_15m_taker_imbalance"
    ].to_numpy(dtype=float)
    result["local_15m_direction_adjusted_oi_change"] = direction * oi_change
    result["local_15m_oi_change_per_signed_million"] = np.divide(
        oi_change,
        signed_flow / 1_000_000.0,
        out=np.full(event_count, np.nan),
        where=flow_valid,
    )
    result["local_15m_return_per_signed_million"] = np.divide(
        price_return,
        signed_flow / 1_000_000.0,
        out=np.full(event_count, np.nan),
        where=flow_valid,
    )
    result["local_15m_aggressive_flow_per_return_floor"] = np.where(
        np.isfinite(signed_flow) & np.isfinite(price_return),
        np.abs(signed_flow) / np.maximum(np.abs(price_return), MIN_ABSOLUTE_RETURN),
        np.nan,
    )
    result["local_15m_signed_flow_denominator_valid"] = flow_valid
    result["local_15m_return_denominator_valid"] = return_valid
    return result


def _aggregate_vectorized_window(
    expanded: pd.DataFrame,
    *,
    window_minutes: int,
    event_count: int,
) -> pd.DataFrame:
    prefix = f"local_{window_minutes}m_"
    work = expanded.loc[
        expanded["minute_offset"].le(window_minutes) & expanded["open"].notna()
    ].copy()
    group = work.groupby("event_row", sort=False)
    count = group.size().reindex(range(event_count), fill_value=0).astype(int)
    first_open = group["open"].first().reindex(range(event_count))
    last_close = group["close"].last().reindex(range(event_count))
    log_return = np.log(last_close / first_open).where(
        first_open.gt(0.0) & last_close.gt(0.0)
    )
    previous = group["close"].shift(1)
    previous = previous.where(previous.notna(), work["open"])
    valid_step = work["close"].gt(0.0) & previous.gt(0.0)
    work["log_step"] = np.log(work["close"] / previous).where(valid_step)
    work["absolute_log_step"] = work["log_step"].abs()
    work["squared_log_step"] = work["log_step"] ** 2
    group = work.groupby("event_row", sort=False)
    quote = group["quote_volume"].sum().reindex(range(event_count))
    trades = group["trade_count"].sum().reindex(range(event_count))
    taker_buy = group["taker_buy_quote_volume"].sum().reindex(range(event_count))
    signed_quote = 2.0 * taker_buy - quote
    path_length = group["absolute_log_step"].sum().reindex(range(event_count))
    realized = np.sqrt(group["squared_log_step"].sum().reindex(range(event_count)))
    high = group["high"].max().reindex(range(event_count))
    low = group["low"].min().reindex(range(event_count))
    oi_valid = work["oi_available"].fillna(False).astype(bool) & work[
        "open_interest"
    ].notna()
    work["valid_oi"] = work["open_interest"].where(oi_valid)
    group = work.groupby("event_row", sort=False)
    oi_start = group["valid_oi"].first().reindex(range(event_count))
    oi_end = group["valid_oi"].last().reindex(range(event_count))
    oi_count = group["valid_oi"].count().reindex(range(event_count), fill_value=0)
    oi_change = (oi_end - oi_start).where(oi_count.ge(2))
    long_liq = (
        work["long_liquidations_vol"].fillna(0.0).groupby(work["event_row"]).sum()
    ).reindex(range(event_count), fill_value=0.0)
    short_liq = (
        work["short_liquidations_vol"].fillna(0.0).groupby(work["event_row"]).sum()
    ).reindex(range(event_count), fill_value=0.0)
    liquidation_total = long_liq + short_liq
    denominator = count.replace(0, np.nan)
    frame = pd.DataFrame(index=range(event_count))
    frame[f"{prefix}minute_count"] = count
    frame[f"{prefix}coverage"] = count / window_minutes
    frame[f"{prefix}missing"] = count.ne(window_minutes)
    frame[f"{prefix}log_return"] = log_return
    frame[f"{prefix}quote_volume"] = quote
    frame[f"{prefix}trade_count"] = trades
    frame[f"{prefix}signed_taker_quote_volume"] = signed_quote
    frame[f"{prefix}taker_imbalance"] = (signed_quote / quote).where(quote.gt(0.0))
    frame[f"{prefix}high_low_range"] = (high / low - 1.0).where(low.gt(0.0))
    frame[f"{prefix}realized_volatility"] = realized
    frame[f"{prefix}path_efficiency"] = (log_return.abs() / path_length).where(
        path_length.gt(0.0) & log_return.notna()
    )
    frame[f"{prefix}return_per_million_quote"] = (
        log_return / (quote / 1_000_000.0)
    ).where(quote.gt(0.0) & log_return.notna())
    frame[f"{prefix}oi_start"] = oi_start
    frame[f"{prefix}oi_end"] = oi_end
    frame[f"{prefix}oi_change"] = oi_change
    frame[f"{prefix}oi_relative_change"] = (oi_change / oi_start).where(
        oi_count.ge(2) & oi_start.ne(0.0)
    )
    frame[f"{prefix}oi_coverage"] = oi_count / denominator
    frame[f"{prefix}oi_missing_flag_rate"] = (
        work["missing_oi_flag"].fillna(False).astype(bool).groupby(work["event_row"]).mean()
    ).reindex(range(event_count))
    frame[f"{prefix}long_liquidation_volume"] = long_liq
    frame[f"{prefix}short_liquidation_volume"] = short_liq
    frame[f"{prefix}liquidation_imbalance"] = (
        (short_liq - long_liq) / liquidation_total
    ).where(liquidation_total.gt(0.0))
    frame[f"{prefix}liquidation_observed"] = liquidation_total.gt(0.0)
    frame[f"{prefix}liquidation_coverage"] = (
        work["liquidation_available"]
        .fillna(False)
        .astype(bool)
        .groupby(work["event_row"])
        .mean()
    ).reindex(range(event_count))
    frame[f"{prefix}liquidation_missing_flag_rate"] = (
        work["missing_liquidation_flag"]
        .fillna(False)
        .astype(bool)
        .groupby(work["event_row"])
        .mean()
    ).reindex(range(event_count))
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Build causal coarse stage-2 local features.")
    parser.add_argument("--stage1-dir", type=Path, default=Path(".output/research/residual_absorption/stage1_is_v1"))
    parser.add_argument("--source", type=Path, default=Path(".output/market/binance_vision/um_futures/enriched_1m"))
    args = parser.parse_args()
    print(build_stage2_local_features(stage1_dir=args.stage1_dir, source_dir=args.source))


__all__ = [
    "LOCAL_ACTIVITY_SCHEMA_VERSION",
    "build_stage2_local_features",
    "compute_local_window_features",
    "compute_symbol_local_features",
]


if __name__ == "__main__":
    main()
