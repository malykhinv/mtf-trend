"""Build the outcome-independent failed-break reclaim signal universe."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from anomaly_science.market_context.sessions import (
    UTC_SESSION_BY_SEQ,
    block_seq_for_ms,
    predecessor_sequential,
    utc_day_for_ms,
)
from anomaly_science.strategy.session_reclaim.spec import (
    HOUR_MS,
    IS_END_EXCLUSIVE_MS,
    IS_START_MS,
    PROTOCOL_FREEZE_ID,
    UNIVERSE_SCHEMA_VERSION,
    UniverseConfig,
)


CACHE_DIR = Path(".output/market/binance_vision/um_futures/enriched_1m")
OUTPUT_DIR = Path(".output/research/session_reclaim_short/universe_is")
WARMUP_START_MS = IS_START_MS - 31 * 24 * HOUR_MS
SOURCE_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "trade_count",
    "taker_buy_quote_volume",
)
FORBIDDEN_UNIVERSE_COLUMNS: frozenset[str] = frozenset(
    {
        "label",
        "outcome",
        "gross_r",
        "net_r",
        "pnl",
        "mfe",
        "mae",
        "exit_time_ms",
        "target_hit",
        "stop_hit",
        "score",
    }
)


def _event_id(symbol: str, signal_time_ms: int) -> str:
    digest = hashlib.blake2b(
        f"session_reclaim_v1|{symbol}|{signal_time_ms}".encode("utf-8"),
        digest_size=10,
    ).hexdigest()
    return f"sr_{digest}"


def validate_source_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(SOURCE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"session-reclaim source columns missing: {missing}")
    source = frame.loc[:, SOURCE_COLUMNS].copy()
    if source.empty:
        return source
    for column in SOURCE_COLUMNS:
        source[column] = pd.to_numeric(source[column], errors="raise")
    source = source.sort_values("timestamp").reset_index(drop=True)
    if source["timestamp"].duplicated().any():
        raise ValueError("source timestamps must be unique")
    if not source["timestamp"].lt(IS_END_EXCLUSIVE_MS).all():
        raise ValueError("source contains forbidden 2026 rows")
    if not (source["timestamp"] % 60_000 == 0).all():
        raise ValueError("source timestamps must align to closed-minute buckets")
    if not (
        (source["low"] > 0)
        & (source["high"] >= source[["open", "close"]].max(axis=1))
        & (source["low"] <= source[["open", "close"]].min(axis=1))
    ).all():
        raise ValueError("source OHLC geometry is invalid")
    return source


def resample_signal_bars(frame: pd.DataFrame, *, timeframe_minutes: int = 60) -> pd.DataFrame:
    source = validate_source_frame(frame)
    if source.empty:
        return pd.DataFrame()
    width_ms = timeframe_minutes * 60_000
    buckets = (source["timestamp"].astype("int64") // width_ms) * width_ms
    grouped = source.groupby(buckets, sort=True)
    bars = pd.DataFrame(
        {
            "timestamp": grouped["timestamp"].first().astype("int64"),
            "open": grouped["open"].first().astype(float),
            "high": grouped["high"].max().astype(float),
            "low": grouped["low"].min().astype(float),
            "close": grouped["close"].last().astype(float),
            "quote_volume": grouped["quote_volume"].sum().astype(float),
            "trade_count": grouped["trade_count"].sum().astype(float),
            "taker_buy_quote_volume": grouped["taker_buy_quote_volume"].sum().astype(float),
            "minute_count": grouped.size().astype("int64"),
        }
    ).reset_index(drop=True)
    bars["complete"] = bars["minute_count"].eq(timeframe_minutes)
    return bars


def _causal_atr(bars: pd.DataFrame, window: int) -> np.ndarray:
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    close = bars["close"].to_numpy(float)
    prior_close = np.concatenate(([np.nan], close[:-1]))
    true_range = np.maximum(high - low, np.maximum(np.abs(high - prior_close), np.abs(low - prior_close)))
    true_range[0] = high[0] - low[0]
    return pd.Series(true_range).rolling(window, min_periods=window).mean().to_numpy(float)


def _runs(day: np.ndarray, seq: np.ndarray) -> list[tuple[int, int, int, int]]:
    keys = day * 10 + seq
    changes = np.flatnonzero(np.diff(keys)) + 1
    starts = np.concatenate(([0], changes))
    ends = np.concatenate((changes, [len(keys)]))
    return [(int(day[start]), int(seq[start]), int(start), int(end)) for start, end in zip(starts, ends)]


def _confirmed_pivot_highs(high: np.ndarray, *, clearance: int) -> np.ndarray:
    pivots = np.zeros(len(high), dtype=bool)
    for index in range(clearance, len(high) - clearance):
        window = high[index - clearance : index + clearance + 1]
        pivots[index] = bool(high[index] == np.max(window) and np.sum(window == high[index]) == 1)
    return pivots


def detect_symbol_events(
    frame: pd.DataFrame,
    *,
    symbol: str,
    config: UniverseConfig = UniverseConfig(),
) -> pd.DataFrame:
    """Detect every signal using only candles closed by its signal time."""

    bars = resample_signal_bars(frame, timeframe_minutes=config.timeframe_minutes)
    if bars.empty:
        return pd.DataFrame()
    ts = bars["timestamp"].to_numpy(np.int64)
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    close = bars["close"].to_numpy(float)
    complete = bars["complete"].to_numpy(bool)
    atr = _causal_atr(bars, config.atr_window_bars)
    day = utc_day_for_ms(ts)
    seq = block_seq_for_ms(ts)
    runs = _runs(day, seq)
    by_key = {(run_day, run_seq): (start, end) for run_day, run_seq, start, end in runs}
    pivots = _confirmed_pivot_highs(h, clearance=config.pivot_clearance_bars)
    rows: list[dict[str, object]] = []

    for current_day, current_seq, current_start, current_end in runs:
        if current_end - current_start < 2:
            continue
        reference_key = predecessor_sequential(current_day, current_seq)
        if reference_key not in by_key:
            continue
        reference_start, reference_end = by_key[reference_key]
        reference_block = UTC_SESSION_BY_SEQ[reference_key[1]]
        expected_reference_bars = reference_block.duration_minutes // config.timeframe_minutes
        if reference_end - reference_start != expected_reference_bars:
            continue
        if not complete[reference_start:reference_end].all():
            continue
        atr_reference = atr[reference_end - 1]
        if not (np.isfinite(atr_reference) and atr_reference > 0):
            continue

        ref_slice = slice(reference_start, reference_end)
        high_relative = int(np.argmax(h[ref_slice]))
        high_index = reference_start + high_relative
        clearance = config.pivot_clearance_bars
        if high_relative < clearance or reference_end - 1 - high_index < clearance:
            continue
        reference_high = float(h[high_index])
        reference_low = float(np.min(low[ref_slice]))
        if not reference_high > reference_low:
            continue
        rise = reference_high - float(np.min(low[reference_start : high_index + 1]))
        fall = reference_high - float(np.min(low[high_index + 1 : reference_end]))
        if min(rise, fall) < config.minimum_swing_atr * atr_reference:
            continue
        reference_mid = 0.5 * (reference_high + reference_low)
        if not (complete[current_start] and o[current_start] < reference_high):
            continue

        crossed = np.flatnonzero(h[current_start:current_end] > reference_high)
        if not len(crossed):
            continue
        poke_start = current_start + int(crossed[0])
        peak_price = float(h[poke_start])
        peak_index = poke_start
        signal_index: int | None = None
        for index in range(poke_start, current_end):
            if not complete[index]:
                break
            if h[index] > peak_price:
                peak_price = float(h[index])
                peak_index = index
            if index > poke_start and close[index] < reference_high:
                signal_index = index
                break
        if signal_index is None:
            continue
        signal_time_ms = int(ts[signal_index] + config.timeframe_minutes * 60_000)
        if signal_time_ms < IS_START_MS or signal_time_ms >= IS_END_EXCLUSIVE_MS:
            continue

        confirmation_cutoff = signal_index - config.pivot_clearance_bars
        lookback_start_ms = signal_time_ms - config.upper_structure_lookback_hours * HOUR_MS
        upper_candidates = np.flatnonzero(
            pivots
            & (np.arange(len(h)) <= confirmation_cutoff)
            & (ts >= lookback_start_ms)
            & (h > peak_price)
        )
        upper_structure_price = np.nan
        upper_structure_time_ms = pd.NA
        if len(upper_candidates):
            prices = h[upper_candidates]
            nearest_price = float(np.min(prices))
            nearest_indices = upper_candidates[np.flatnonzero(prices == nearest_price)]
            chosen = int(nearest_indices[-1])
            upper_structure_price = nearest_price
            upper_structure_time_ms = int(ts[chosen])

        signal_quote_volume = float(bars.iloc[signal_index]["quote_volume"])
        signal_trade_count = float(bars.iloc[signal_index]["trade_count"])
        signal_taker_quote = float(bars.iloc[signal_index]["taker_buy_quote_volume"])
        rows.append(
            {
                "universe_schema_version": UNIVERSE_SCHEMA_VERSION,
                "protocol_freeze_id": PROTOCOL_FREEZE_ID,
                "event_id": _event_id(symbol, signal_time_ms),
                "symbol": symbol,
                "timeframe_minutes": config.timeframe_minutes,
                "current_session_seq": current_seq,
                "reference_session_seq": reference_key[1],
                "reference_start_time_ms": int(ts[reference_start]),
                "reference_end_time_ms": int(ts[reference_end - 1] + config.timeframe_minutes * 60_000),
                "reference_high_time_ms": int(ts[high_index]),
                "reference_high": reference_high,
                "reference_mid": reference_mid,
                "reference_low": reference_low,
                "reference_swing_rise_atr": rise / atr_reference,
                "reference_swing_fall_atr": fall / atr_reference,
                "poke_start_time_ms": int(ts[poke_start]),
                "poke_peak_time_ms": int(ts[peak_index]),
                "poke_high": peak_price,
                "poke_depth_fraction_of_range": (peak_price - reference_high) / (reference_high - reference_low),
                "signal_bar_start_time_ms": int(ts[signal_index]),
                "signal_time_ms": signal_time_ms,
                "feature_cutoff_time_ms": signal_time_ms,
                "order_activation_time_ms": signal_time_ms,
                "signal_close": float(close[signal_index]),
                "signal_quote_volume": signal_quote_volume,
                "signal_trade_count": signal_trade_count,
                "signal_taker_buy_share": (
                    signal_taker_quote / signal_quote_volume if signal_quote_volume > 0 else np.nan
                ),
                "upper_structure_time_ms": upper_structure_time_ms,
                "upper_structure_price": upper_structure_price,
                "source_max_timestamp_ms": int(ts[signal_index]),
                "untouched_2026_rows_used": 0,
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["upper_structure_time_ms"] = result["upper_structure_time_ms"].astype("Int64")
    if result["event_id"].duplicated().any():
        raise AssertionError("detector emitted duplicate event ids")
    if not (result["feature_cutoff_time_ms"] == result["signal_time_ms"]).all():
        raise AssertionError("feature cutoff must equal the signal time")
    if not (result["source_max_timestamp_ms"] < result["signal_time_ms"]).all():
        raise AssertionError("source bars must close by the signal time")
    if FORBIDDEN_UNIVERSE_COLUMNS & set(result.columns):
        raise AssertionError("future/outcome columns leaked into the universe")
    return result.sort_values(["signal_time_ms", "symbol", "event_id"]).reset_index(drop=True)


def _read_is_source(path: Path) -> pd.DataFrame:
    return pd.read_parquet(
        path,
        columns=list(SOURCE_COLUMNS),
        filters=[
            ("timestamp", ">=", WARMUP_START_MS),
            ("timestamp", "<", IS_END_EXCLUSIVE_MS),
        ],
    )


def _build_path(args: tuple[str, UniverseConfig]) -> pd.DataFrame:
    path_text, config = args
    path = Path(path_text)
    return detect_symbol_events(_read_is_source(path), symbol=path.stem, config=config)


def partition_eligible_sources(paths: Iterable[Path]) -> tuple[tuple[Path, ...], tuple[dict[str, object], ...]]:
    """Freeze source eligibility before reading prices or detecting events."""

    eligible: list[Path] = []
    excluded: list[dict[str, object]] = []
    required = set(SOURCE_COLUMNS)
    for path in sorted(Path(item) for item in paths):
        columns = set(pq.read_schema(path).names)
        missing = sorted(required - columns)
        # Dated Binance symbols are delivery contracts, not the perpetual
        # instruments that the live strategy can trade under this contract.
        dated_delivery = len(path.stem.rsplit("_", 1)) == 2 and path.stem.rsplit("_", 1)[1].isdigit()
        reasons: list[str] = []
        if dated_delivery:
            reasons.append("dated_delivery_contract")
        if missing:
            reasons.append("missing_required_columns")
        if reasons:
            excluded.append({"symbol": path.stem, "reasons": reasons, "missing_columns": missing})
        else:
            eligible.append(path)
    return tuple(eligible), tuple(excluded)


def _canonical_identity_hash(frame: pd.DataFrame) -> str:
    identity = frame.loc[:, ["event_id", "symbol", "signal_time_ms"]].sort_values("event_id")
    payload = "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in identity.to_dict("records")
    )
    return hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest()


def build_universe(
    paths: Iterable[Path],
    *,
    output_dir: Path = OUTPUT_DIR,
    config: UniverseConfig = UniverseConfig(),
    workers: int = 6,
) -> pd.DataFrame:
    requested_paths = tuple(sorted(Path(path) for path in paths))
    if not requested_paths:
        raise ValueError("no source paths")
    source_paths, exclusions = partition_eligible_sources(requested_paths)
    if not source_paths:
        raise ValueError("no source paths satisfy the frozen data contract")
    if workers <= 0:
        raise ValueError("workers must be positive")
    tasks = tuple((str(path), config) for path in source_paths)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        frames = list(executor.map(_build_path, tasks, chunksize=8))
    populated = [frame for frame in frames if not frame.empty]
    universe = pd.concat(populated, ignore_index=True) if populated else pd.DataFrame()
    if universe.empty:
        raise ValueError("no session-reclaim events detected")
    universe = universe.sort_values(["signal_time_ms", "symbol", "event_id"]).reset_index(drop=True)
    if universe["event_id"].duplicated().any():
        raise ValueError("event ids must be globally unique")
    output_dir.mkdir(parents=True, exist_ok=True)
    universe.to_parquet(output_dir / "population.parquet", index=False)
    report = {
        "protocol_freeze_id": PROTOCOL_FREEZE_ID,
        "universe_schema_version": UNIVERSE_SCHEMA_VERSION,
        "config": asdict(config),
        "requested_source_files": len(requested_paths),
        "source_files": len(source_paths),
        "excluded_source_files": list(exclusions),
        "source_rows_from_2026": 0,
        "events": len(universe),
        "symbols": int(universe["symbol"].nunique()),
        "months": {str(key): int(value) for key, value in pd.to_datetime(
            universe["signal_time_ms"], unit="ms", utc=True
        ).dt.strftime("%Y-%m").value_counts().sort_index().items()},
        "upper_structure_coverage": float(universe["upper_structure_price"].notna().mean()),
        "identity_sha256": _canonical_identity_hash(universe),
        "lookahead_audit": "PASS",
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return universe


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the causal session-reclaim IS universe.")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    paths = sorted(args.cache_dir.glob("*.parquet"))
    if args.limit:
        paths = paths[: args.limit]
    universe = build_universe(paths, output_dir=args.output_dir, workers=args.workers)
    print(f"session-reclaim events={len(universe):,} -> {args.output_dir}")


if __name__ == "__main__":
    main()
