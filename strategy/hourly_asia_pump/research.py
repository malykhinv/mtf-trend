"""Research helpers for hourly Asia-session top-of-hour pumps."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from data.liquidity.daily_volume_ranker import DailyVolumeRanker
from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.config import (
    DEFAULT_ATR_WINDOW_MINUTES,
    DEFAULT_BREAKOUT_LOOKBACK_MINUTES,
    DEFAULT_VOLUME_WINDOW_MINUTES,
    HOURLY_ASIA_PUMP_SUPPORTED_TIMEFRAMES,
    HourlyAsiaPumpParams,
    HourlyAsiaPumpProfileId,
    build_hourly_asia_pump_grid,
    build_hourly_asia_pump_profile,
)
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

_HORIZON_MINUTES: tuple[int, ...] = (5, 15, 30, 60)


def _timeframe_minutes(timeframe: Timeframe) -> int:
    return timeframe.to_milliseconds() // 60_000


def _bars_for_minutes(timeframe: Timeframe, minutes: int) -> int:
    return max(1, math.ceil(minutes / _timeframe_minutes(timeframe)))


def _safe_numeric(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
        return float(value)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _format_value(column_name: str, value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    numeric = _safe_numeric(value)
    if numeric is None:
        return str(value)
    if column_name.endswith("_rate") or column_name.endswith("_pct"):
        return f"{numeric * 100:.2f}%"
    return f"{numeric:.4f}" if not float(numeric).is_integer() else f"{int(numeric)}"


def _frame_to_markdown(frame: pd.DataFrame, *, columns: Sequence[str], limit: int | None = None) -> str:
    if frame.empty:
        return "_No data._"
    prepared = frame.copy()
    existing_columns = [column for column in columns if column in prepared.columns]
    if existing_columns:
        prepared = prepared[existing_columns]
    if limit is not None and limit >= 0:
        prepared = prepared.head(limit)
    lines = [
        "| " + " | ".join(prepared.columns.astype(str)) + " |",
        "| " + " | ".join("---" for _ in prepared.columns) + " |",
    ]
    for _, row in prepared.iterrows():
        lines.append(
            "| "
            + " | ".join(_format_value(str(column), row[column]) for column in prepared.columns)
            + " |"
        )
    return "\n".join(lines)


def _mean_or_none(series: pd.Series) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    return float(cleaned.mean())


def _median_or_none(series: pd.Series) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    return float(cleaned.median())


def _quantile_or_none(series: pd.Series, quantile: float) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    return float(cleaned.quantile(quantile))


def _rate_ge(series: pd.Series, threshold: float) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    return float((cleaned >= threshold).mean())


def _session_mask(hours: pd.Series, *, start_hour_utc: int, end_hour_utc: int) -> pd.Series:
    if start_hour_utc == end_hour_utc:
        return pd.Series(True, index=hours.index)
    if start_hour_utc < end_hour_utc:
        return (hours >= start_hour_utc) & (hours < end_hour_utc)
    return (hours >= start_hour_utc) | (hours < end_hour_utc)


def _resolve_symbols_for_timeframe(
    *,
    preparer: DataPreparer,
    ranker: DailyVolumeRanker,
    timeframe: Timeframe,
    symbols: Sequence[str] | None,
    top_n: int | None,
    logger: logging.Logger,
) -> list[str]:
    if symbols:
        resolved = list(dict.fromkeys(symbols))
    else:
        resolved = preparer.list_symbols(timeframe)
    if top_n is None or top_n <= 0 or len(resolved) <= top_n:
        return resolved
    volumes = ranker.calculate_avg_daily_volume_usd(resolved, timeframe, logger)
    ordered = sorted(volumes.items(), key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in ordered[:top_n]]


def _chunk_symbols(symbols: Sequence[str], chunk_size: int) -> list[list[str]]:
    return [
        list(symbols[index : index + chunk_size])
        for index in range(0, len(symbols), chunk_size)
    ]


def _build_symbol_candidates(
    *,
    preparer: DataPreparer,
    timeframe: Timeframe,
    symbol: str,
    base_params: HourlyAsiaPumpParams,
) -> pd.DataFrame:
    frame = preparer.load_symbol_data(symbol, timeframe)
    if frame.empty:
        return pd.DataFrame()

    atr_window_bars = _bars_for_minutes(timeframe, base_params.atr_window_minutes)
    volume_window_bars = _bars_for_minutes(timeframe, base_params.volume_window_minutes)
    breakout_lookback_bars = _bars_for_minutes(timeframe, base_params.breakout_lookback_minutes)
    timestamps = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    hours = timestamps.dt.hour
    event_mask = (
        (timestamps.dt.minute == base_params.trigger_minute)
        & _session_mask(
            hours,
            start_hour_utc=base_params.asia_start_hour_utc,
            end_hour_utc=base_params.asia_end_hour_utc,
        )
        & (frame["close"] > frame["open"])
    )
    if not bool(event_mask.any()):
        return pd.DataFrame()

    loose_profile = build_hourly_asia_pump_profile(
        timeframe=timeframe,
        profile_id="loose",
        asia_start_hour_utc=base_params.asia_start_hour_utc,
        asia_end_hour_utc=base_params.asia_end_hour_utc,
        trigger_minute=base_params.trigger_minute,
        max_follow_minutes=base_params.max_follow_minutes,
    )
    true_range = frame["high"] - frame["low"]
    atr = true_range.rolling(
        atr_window_bars,
        min_periods=max(5, atr_window_bars // 3),
    ).mean().shift(1)
    volume_baseline = frame["volume"].rolling(
        volume_window_bars,
        min_periods=max(10, volume_window_bars // 4),
    ).median().shift(1)
    prior_high = frame["high"].rolling(
        breakout_lookback_bars,
        min_periods=max(3, breakout_lookback_bars // 3),
    ).max().shift(1)
    close_to_high_frac = (frame["high"] - frame["close"]) / true_range.where(true_range > 0)

    events = pd.DataFrame(
        {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "row_index": frame.index,
            "timestamp_ms": frame["timestamp"].astype("int64"),
            "timestamp_utc": timestamps.dt.strftime("%Y-%m-%d %H:%M:%S"),
            "date_utc": timestamps.dt.strftime("%Y-%m-%d"),
            "hour_utc": hours.astype("int64"),
            "trigger_open": pd.to_numeric(frame["open"], errors="coerce"),
            "trigger_high": pd.to_numeric(frame["high"], errors="coerce"),
            "trigger_low": pd.to_numeric(frame["low"], errors="coerce"),
            "trigger_close": pd.to_numeric(frame["close"], errors="coerce"),
            "trigger_volume": pd.to_numeric(frame["volume"], errors="coerce"),
            "trigger_return_pct": pd.to_numeric(frame["close"] / frame["open"] - 1.0, errors="coerce"),
            "trigger_range_pct": pd.to_numeric(true_range / frame["open"], errors="coerce"),
            "range_atr": pd.to_numeric(true_range / atr, errors="coerce"),
            "body_atr": pd.to_numeric((frame["close"] - frame["open"]) / atr, errors="coerce"),
            "volume_mult": pd.to_numeric(frame["volume"] / volume_baseline, errors="coerce"),
            "close_to_high_frac": pd.to_numeric(close_to_high_frac.fillna(1.0), errors="coerce"),
            "breakout_pct": pd.to_numeric(frame["high"] / prior_high - 1.0, errors="coerce"),
            "atr_window_minutes": base_params.atr_window_minutes,
            "volume_window_minutes": base_params.volume_window_minutes,
            "breakout_lookback_minutes": base_params.breakout_lookback_minutes,
            "max_follow_minutes": base_params.max_follow_minutes,
        }
    )
    events = events[event_mask].copy()
    if events.empty:
        return pd.DataFrame()

    loose_mask = (
        (events["range_atr"] >= loose_profile.min_range_atr)
        & (events["body_atr"] >= loose_profile.min_body_atr)
        & (events["volume_mult"] >= loose_profile.min_volume_mult)
        & (events["close_to_high_frac"] <= loose_profile.max_close_to_high_frac)
        & (events["breakout_pct"] >= loose_profile.min_breakout_pct)
    )
    events = events[loose_mask.fillna(False)].copy()
    if events.empty:
        return pd.DataFrame()

    return _append_lifecycle_metrics(
        frame=frame,
        timeframe=timeframe,
        events=events,
        max_follow_minutes=base_params.max_follow_minutes,
    )


def _build_symbol_candidates_process(
    *,
    cache_dir: str,
    timeframe_value: str,
    symbols: Sequence[str],
    base_params: HourlyAsiaPumpParams,
) -> pd.DataFrame:
    preparer = DataPreparer(Path(cache_dir))
    parts: list[pd.DataFrame] = []
    timeframe = Timeframe(timeframe_value)
    for symbol in symbols:
        events = _build_symbol_candidates(
            preparer=preparer,
            timeframe=timeframe,
            symbol=symbol,
            base_params=base_params,
        )
        if not events.empty:
            parts.append(events)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _build_timeframe_candidates(
    *,
    preparer: DataPreparer,
    timeframe: Timeframe,
    symbols: Sequence[str],
    base_params: HourlyAsiaPumpParams,
    logger: logging.Logger,
) -> pd.DataFrame:
    candidate_parts: list[pd.DataFrame] = []
    total_candidates = 0
    max_workers = min(6, max(1, os.cpu_count() or 1))

    if len(symbols) < max_workers * 2:
        for index, symbol in enumerate(symbols, start=1):
            events = _build_symbol_candidates(
                preparer=preparer,
                timeframe=timeframe,
                symbol=symbol,
                base_params=base_params,
            )
            if not events.empty:
                candidate_parts.append(events)
                total_candidates += len(events)
            if index % 25 == 0 or index == len(symbols):
                logger.info(
                    "hourly-asia-pump: timeframe=%s scanned_symbols=%s/%s candidate_rows=%s mode=local",
                    timeframe.value,
                    index,
                    len(symbols),
                    total_candidates,
                )
    else:
        chunk_size = max(4, min(16, math.ceil(len(symbols) / (max_workers * 3))))
        symbol_chunks = _chunk_symbols(symbols, chunk_size)
        processed_symbols = 0
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _build_symbol_candidates_process,
                    cache_dir=str(preparer._cache_dir),
                    timeframe_value=timeframe.value,
                    symbols=symbol_chunk,
                    base_params=base_params,
                ): symbol_chunk
                for symbol_chunk in symbol_chunks
            }
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                events = future.result()
                processed_symbols += len(futures[future])
                if not events.empty:
                    candidate_parts.append(events)
                    total_candidates += len(events)
                if index % 4 == 0 or processed_symbols >= len(symbols):
                    logger.info(
                        "hourly-asia-pump: timeframe=%s scanned_symbols=%s/%s candidate_rows=%s workers=%s mode=process chunk_size=%s",
                        timeframe.value,
                        processed_symbols,
                        len(symbols),
                        total_candidates,
                        max_workers,
                        chunk_size,
                    )

    if not candidate_parts:
        return pd.DataFrame()
    result = pd.concat(candidate_parts, ignore_index=True)
    return result.sort_values(["timestamp_ms", "symbol"]).reset_index(drop=True)


def _append_lifecycle_metrics(
    *,
    frame: pd.DataFrame,
    timeframe: Timeframe,
    events: pd.DataFrame,
    max_follow_minutes: int,
) -> pd.DataFrame:
    if events.empty:
        return events

    open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype="float64")
    high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64")
    low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64")
    close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")
    timestamp_values = pd.to_numeric(frame["timestamp"], errors="coerce").fillna(0).astype("int64").to_numpy()
    tf_minutes = _timeframe_minutes(timeframe)
    max_follow_bars = _bars_for_minutes(timeframe, max_follow_minutes)
    horizon_bars = {horizon: _bars_for_minutes(timeframe, horizon) for horizon in _HORIZON_MINUTES}

    metric_rows: list[dict[str, object]] = []
    for row_index in events["row_index"].astype("int64").tolist():
        start_idx = int(row_index)
        end_idx = min(len(frame) - 1, start_idx + max_follow_bars)
        start_open = float(open_values[start_idx])
        start_close = float(close_values[start_idx])

        peak_idx = start_idx
        peak_price = float(high_values[start_idx])
        retrace_idx: int | None = None
        for idx in range(start_idx, end_idx + 1):
            if high_values[idx] > peak_price:
                peak_price = float(high_values[idx])
                peak_idx = idx
                continue
            if idx > peak_idx and peak_price > start_open:
                retrace_level = start_open + 0.5 * (peak_price - start_open)
                if low_values[idx] <= retrace_level:
                    retrace_idx = idx
                    break

        effective_end_idx = retrace_idx if retrace_idx is not None else end_idx
        continuation_slice = high_values[start_idx + 1 : effective_end_idx + 1]
        continuation_peak_price = float(continuation_slice.max()) if continuation_slice.size else start_close
        peak_timestamp_ms = int(timestamp_values[peak_idx])
        retrace_timestamp_ms = int(timestamp_values[retrace_idx]) if retrace_idx is not None else None

        metrics: dict[str, object] = {
            "peak_price_before_50pct_retrace": peak_price,
            "peak_timestamp_ms": peak_timestamp_ms,
            "peak_timestamp_utc": pd.to_datetime(peak_timestamp_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S"),
            "peak_return_pct": (peak_price / start_open) - 1.0 if start_open > 0 else None,
            "continuation_peak_return_pct": (continuation_peak_price / start_close) - 1.0 if start_close > 0 else None,
            "bars_to_peak": peak_idx - start_idx,
            "minutes_to_peak": (peak_idx - start_idx) * tf_minutes,
            "retraced_50pct_within_window": retrace_idx is not None,
            "retrace_50_timestamp_ms": retrace_timestamp_ms,
            "retrace_50_timestamp_utc": (
                pd.to_datetime(retrace_timestamp_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S")
                if retrace_timestamp_ms is not None
                else None
            ),
            "bars_to_50pct_retrace": (retrace_idx - start_idx) if retrace_idx is not None else None,
            "minutes_to_50pct_retrace": ((retrace_idx - start_idx) * tf_minutes) if retrace_idx is not None else None,
        }
        for horizon, bars in horizon_bars.items():
            horizon_end_idx = min(len(frame) - 1, start_idx + bars)
            future_high_slice = high_values[start_idx + 1 : horizon_end_idx + 1]
            future_close_idx = min(len(frame) - 1, start_idx + bars)
            metrics[f"max_return_next_{horizon}m_pct"] = (
                (float(future_high_slice.max()) / start_close) - 1.0
                if future_high_slice.size and start_close > 0
                else 0.0
            )
            metrics[f"close_return_{horizon}m_pct"] = (
                (float(close_values[future_close_idx]) / start_close) - 1.0
                if future_close_idx > start_idx and start_close > 0
                else 0.0
            )
            metrics[f"survived_{horizon}m"] = retrace_idx is None or retrace_idx > start_idx + bars
        metric_rows.append(metrics)

    metrics_frame = pd.DataFrame(metric_rows)
    return pd.concat([events.reset_index(drop=True), metrics_frame], axis=1)


def _filter_events_for_params(events: pd.DataFrame, params: HourlyAsiaPumpParams) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    mask = (
        (pd.to_numeric(events["range_atr"], errors="coerce") >= params.min_range_atr)
        & (pd.to_numeric(events["body_atr"], errors="coerce") >= params.min_body_atr)
        & (pd.to_numeric(events["volume_mult"], errors="coerce") >= params.min_volume_mult)
        & (pd.to_numeric(events["close_to_high_frac"], errors="coerce") <= params.max_close_to_high_frac)
        & (pd.to_numeric(events["breakout_pct"], errors="coerce") >= params.min_breakout_pct)
    )
    filtered = events[mask.fillna(False)].copy()
    if filtered.empty:
        return filtered
    filtered["grid_id"] = params.grid_id
    filtered["profile_id"] = params.profile_id
    filtered["selection_min_range_atr"] = params.min_range_atr
    filtered["selection_min_body_atr"] = params.min_body_atr
    filtered["selection_min_volume_mult"] = params.min_volume_mult
    filtered["selection_max_close_to_high_frac"] = params.max_close_to_high_frac
    filtered["selection_min_breakout_pct"] = params.min_breakout_pct
    return filtered


def _summarize_params(
    *,
    timeframe: Timeframe,
    params: HourlyAsiaPumpParams,
    filtered_events: pd.DataFrame,
) -> dict[str, object]:
    row: dict[str, object] = {
        "timeframe": timeframe.value,
        "grid_id": params.grid_id,
        "profile_id": params.profile_id,
        "asia_start_hour_utc": params.asia_start_hour_utc,
        "asia_end_hour_utc": params.asia_end_hour_utc,
        "trigger_minute": params.trigger_minute,
        "atr_window_minutes": params.atr_window_minutes,
        "volume_window_minutes": params.volume_window_minutes,
        "breakout_lookback_minutes": params.breakout_lookback_minutes,
        "max_follow_minutes": params.max_follow_minutes,
        "min_range_atr": params.min_range_atr,
        "min_body_atr": params.min_body_atr,
        "min_volume_mult": params.min_volume_mult,
        "max_close_to_high_frac": params.max_close_to_high_frac,
        "min_breakout_pct": params.min_breakout_pct,
        "events_count": int(len(filtered_events)),
        "symbols_count": int(filtered_events["symbol"].nunique()) if "symbol" in filtered_events.columns and not filtered_events.empty else 0,
        "days_count": int(filtered_events["date_utc"].nunique()) if "date_utc" in filtered_events.columns and not filtered_events.empty else 0,
        "median_trigger_return_pct": _median_or_none(filtered_events.get("trigger_return_pct", pd.Series(dtype="float64"))),
        "median_range_atr": _median_or_none(filtered_events.get("range_atr", pd.Series(dtype="float64"))),
        "median_body_atr": _median_or_none(filtered_events.get("body_atr", pd.Series(dtype="float64"))),
        "median_volume_mult": _median_or_none(filtered_events.get("volume_mult", pd.Series(dtype="float64"))),
        "median_breakout_pct": _median_or_none(filtered_events.get("breakout_pct", pd.Series(dtype="float64"))),
        "median_peak_return_pct": _median_or_none(filtered_events.get("peak_return_pct", pd.Series(dtype="float64"))),
        "p75_peak_return_pct": _quantile_or_none(filtered_events.get("peak_return_pct", pd.Series(dtype="float64")), 0.75),
        "median_continuation_peak_return_pct": _median_or_none(filtered_events.get("continuation_peak_return_pct", pd.Series(dtype="float64"))),
        "p75_continuation_peak_return_pct": _quantile_or_none(filtered_events.get("continuation_peak_return_pct", pd.Series(dtype="float64")), 0.75),
        "median_minutes_to_peak": _median_or_none(filtered_events.get("minutes_to_peak", pd.Series(dtype="float64"))),
        "median_minutes_to_50pct_retrace": _median_or_none(filtered_events.get("minutes_to_50pct_retrace", pd.Series(dtype="float64"))),
        "retraced_50pct_within_window_rate": _mean_or_none(filtered_events.get("retraced_50pct_within_window", pd.Series(dtype="float64"))),
        "survived_5m_rate": _mean_or_none(filtered_events.get("survived_5m", pd.Series(dtype="float64"))),
        "survived_15m_rate": _mean_or_none(filtered_events.get("survived_15m", pd.Series(dtype="float64"))),
        "survived_30m_rate": _mean_or_none(filtered_events.get("survived_30m", pd.Series(dtype="float64"))),
        "survived_60m_rate": _mean_or_none(filtered_events.get("survived_60m", pd.Series(dtype="float64"))),
        "continuation_ge_0_5pct_rate": _rate_ge(filtered_events.get("continuation_peak_return_pct", pd.Series(dtype="float64")), 0.005),
        "continuation_ge_1pct_rate": _rate_ge(filtered_events.get("continuation_peak_return_pct", pd.Series(dtype="float64")), 0.01),
        "continuation_ge_2pct_rate": _rate_ge(filtered_events.get("continuation_peak_return_pct", pd.Series(dtype="float64")), 0.02),
    }
    return row


def _build_grid_summary(
    *,
    timeframe: Timeframe,
    events: pd.DataFrame,
    grid: Sequence[HourlyAsiaPumpParams],
) -> pd.DataFrame:
    rows = [
        _summarize_params(timeframe=timeframe, params=params, filtered_events=_filter_events_for_params(events, params))
        for params in grid
    ]
    return pd.DataFrame(rows).sort_values(
        ["timeframe", "median_continuation_peak_return_pct", "events_count"],
        ascending=[True, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _build_common_patterns(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for timeframe, group in events.groupby("timeframe", sort=True):
        top_hours = group["hour_utc"].value_counts(normalize=True).head(2) if "hour_utc" in group.columns else pd.Series(dtype="float64")
        rows.append(
            {
                "timeframe": timeframe,
                "events_count": int(len(group)),
                "symbols_count": int(group["symbol"].nunique()) if "symbol" in group.columns else 0,
                "days_count": int(group["date_utc"].nunique()) if "date_utc" in group.columns else 0,
                "median_trigger_return_pct": _median_or_none(group["trigger_return_pct"]),
                "median_trigger_range_pct": _median_or_none(group["trigger_range_pct"]),
                "median_range_atr": _median_or_none(group["range_atr"]),
                "median_body_atr": _median_or_none(group["body_atr"]),
                "median_volume_mult": _median_or_none(group["volume_mult"]),
                "median_breakout_pct": _median_or_none(group["breakout_pct"]),
                "median_peak_return_pct": _median_or_none(group["peak_return_pct"]),
                "median_continuation_peak_return_pct": _median_or_none(group["continuation_peak_return_pct"]),
                "median_minutes_to_peak": _median_or_none(group["minutes_to_peak"]),
                "median_minutes_to_50pct_retrace": _median_or_none(group["minutes_to_50pct_retrace"]),
                "survived_5m_rate": _mean_or_none(group["survived_5m"]),
                "survived_15m_rate": _mean_or_none(group["survived_15m"]),
                "survived_30m_rate": _mean_or_none(group["survived_30m"]),
                "survived_60m_rate": _mean_or_none(group["survived_60m"]),
                "top_hour_utc": int(top_hours.index[0]) if len(top_hours.index) >= 1 else None,
                "top_hour_share": float(top_hours.iloc[0]) if len(top_hours.index) >= 1 else None,
                "second_hour_utc": int(top_hours.index[1]) if len(top_hours.index) >= 2 else None,
                "second_hour_share": float(top_hours.iloc[1]) if len(top_hours.index) >= 2 else None,
            }
        )
    return pd.DataFrame(rows).sort_values("timeframe").reset_index(drop=True)


def _build_selected_symbol_summary(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for (timeframe, symbol), group in events.groupby(["timeframe", "symbol"], sort=True):
        rows.append(
            {
                "timeframe": timeframe,
                "symbol": symbol,
                "events_count": int(len(group)),
                "median_peak_return_pct": _median_or_none(group["peak_return_pct"]),
                "median_continuation_peak_return_pct": _median_or_none(group["continuation_peak_return_pct"]),
                "median_minutes_to_50pct_retrace": _median_or_none(group["minutes_to_50pct_retrace"]),
                "survived_15m_rate": _mean_or_none(group["survived_15m"]),
                "survived_30m_rate": _mean_or_none(group["survived_30m"]),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["timeframe", "events_count", "median_continuation_peak_return_pct"],
        ascending=[True, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _top_grid_rows_by_timeframe(grid_summary: pd.DataFrame, *, limit: int = 10) -> pd.DataFrame:
    if grid_summary.empty:
        return pd.DataFrame()
    parts = [
        group.sort_values(
            ["median_continuation_peak_return_pct", "events_count"],
            ascending=[False, False],
            na_position="last",
        ).head(limit)
        for _, group in grid_summary.groupby("timeframe", sort=True)
    ]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _candidate_cache_path(
    *,
    candidate_cache_dir: Path,
    timeframe: Timeframe,
    symbols: Sequence[str],
    asia_start_hour_utc: int,
    asia_end_hour_utc: int,
    trigger_minute: int,
    max_follow_minutes: int,
) -> Path:
    payload = {
        "timeframe": timeframe.value,
        "symbols": list(symbols),
        "asia_start_hour_utc": asia_start_hour_utc,
        "asia_end_hour_utc": asia_end_hour_utc,
        "trigger_minute": trigger_minute,
        "max_follow_minutes": max_follow_minutes,
        "atr_window_minutes": DEFAULT_ATR_WINDOW_MINUTES,
        "volume_window_minutes": DEFAULT_VOLUME_WINDOW_MINUTES,
        "breakout_lookback_minutes": DEFAULT_BREAKOUT_LOOKBACK_MINUTES,
        "candidate_version": 1,
    }
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return candidate_cache_dir / timeframe.value / f"{digest}.parquet"


def _write_report(
    *,
    output_dir: Path,
    run_context: dict[str, Any],
    profile_summary: pd.DataFrame,
    selected_common_patterns: pd.DataFrame,
    top_grid_rows: pd.DataFrame,
    selection_profile: HourlyAsiaPumpProfileId,
) -> Path:
    context_frame = pd.DataFrame(
        [
            {
                "key": key,
                "value": json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value,
            }
            for key, value in run_context.items()
        ]
    )
    selected_profile_rows = (
        profile_summary[profile_summary["profile_id"].astype(str) == str(selection_profile)].copy()
        if not profile_summary.empty and "profile_id" in profile_summary.columns
        else pd.DataFrame()
    )

    lines = [
        "# Hourly Asia Pump Research",
        "",
        "## Run Context",
        "",
        _frame_to_markdown(context_frame, columns=("key", "value")),
        "",
        "## Selected Profile Summary",
        "",
        _frame_to_markdown(
            selected_profile_rows,
            columns=(
                "timeframe",
                "profile_id",
                "events_count",
                "symbols_count",
                "median_peak_return_pct",
                "median_continuation_peak_return_pct",
                "median_minutes_to_50pct_retrace",
                "survived_15m_rate",
                "survived_30m_rate",
                "survived_60m_rate",
            ),
        ),
        "",
        "## Common Patterns",
        "",
        _frame_to_markdown(
            selected_common_patterns,
            columns=(
                "timeframe",
                "events_count",
                "symbols_count",
                "median_trigger_return_pct",
                "median_range_atr",
                "median_body_atr",
                "median_volume_mult",
                "median_breakout_pct",
                "median_peak_return_pct",
                "median_continuation_peak_return_pct",
                "median_minutes_to_50pct_retrace",
                "top_hour_utc",
                "top_hour_share",
            ),
        ),
        "",
        "## Top Grid Rows",
        "",
    ]
    if top_grid_rows.empty:
        lines.append("_No grid rows matched._")
    else:
        for timeframe, group in top_grid_rows.groupby("timeframe", sort=True):
            lines.extend(
                [
                    f"### {timeframe}",
                    "",
                    _frame_to_markdown(
                        group,
                        columns=(
                            "grid_id",
                            "profile_id",
                            "events_count",
                            "symbols_count",
                            "median_peak_return_pct",
                            "median_continuation_peak_return_pct",
                            "median_minutes_to_50pct_retrace",
                            "survived_15m_rate",
                            "survived_30m_rate",
                            "continuation_ge_1pct_rate",
                            "continuation_ge_2pct_rate",
                        ),
                    ),
                    "",
                ]
            )
    lines.extend(
        [
            "## Artifacts",
            "",
            f"- grid summary: `{output_dir / 'grid_summary.csv'}`",
            f"- profile summary: `{output_dir / 'profile_summary.csv'}`",
            f"- selected profile events: `{output_dir / 'selected_profile_events.csv'}`",
            f"- selected profile symbol summary: `{output_dir / 'selected_profile_symbol_summary.csv'}`",
            f"- common patterns: `{output_dir / 'common_patterns_by_timeframe.csv'}`",
            "",
        ]
    )
    report_path = output_dir / "research_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_hourly_asia_pump_research_artifacts(
    *,
    cache_dir: Path,
    output_dir: Path,
    timeframes: Sequence[Timeframe],
    symbols: Sequence[str] | None = None,
    top_n: int | None = None,
    asia_start_hour_utc: int,
    asia_end_hour_utc: int,
    trigger_minute: int,
    max_follow_minutes: int,
    selection_profile: HourlyAsiaPumpProfileId,
    candidate_cache_dir: Path | None = None,
    reuse_candidate_cache: bool = True,
    logger: logging.Logger | None = None,
) -> dict[str, Path]:
    active_logger = logger or module_logger
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    preparer = DataPreparer(cache_dir)
    ranker = DailyVolumeRanker(cache_dir)

    grid_frames: list[pd.DataFrame] = []
    profile_frames: list[pd.DataFrame] = []
    selected_event_frames: list[pd.DataFrame] = []
    common_pattern_frames: list[pd.DataFrame] = []
    symbol_summary_frames: list[pd.DataFrame] = []
    resolved_symbols_by_timeframe: dict[str, list[str]] = {}

    for timeframe in timeframes:
        if timeframe not in HOURLY_ASIA_PUMP_SUPPORTED_TIMEFRAMES:
            raise ValueError(f"Unsupported hourly Asia pump timeframe: {timeframe.value}")

        resolved_symbols = _resolve_symbols_for_timeframe(
            preparer=preparer,
            ranker=ranker,
            timeframe=timeframe,
            symbols=symbols,
            top_n=top_n,
            logger=active_logger,
        )
        resolved_symbols_by_timeframe[timeframe.value] = resolved_symbols
        active_logger.info("hourly-asia-pump: timeframe=%s symbols=%s", timeframe.value, len(resolved_symbols))

        grid = build_hourly_asia_pump_grid(
            timeframe=timeframe,
            asia_start_hour_utc=asia_start_hour_utc,
            asia_end_hour_utc=asia_end_hour_utc,
            trigger_minute=trigger_minute,
            max_follow_minutes=max_follow_minutes,
        )
        selection_params = build_hourly_asia_pump_profile(
            timeframe=timeframe,
            profile_id=selection_profile,
            asia_start_hour_utc=asia_start_hour_utc,
            asia_end_hour_utc=asia_end_hour_utc,
            trigger_minute=trigger_minute,
            max_follow_minutes=max_follow_minutes,
        )
        candidates_cache_path: Path | None = None
        candidates = pd.DataFrame()
        if candidate_cache_dir is not None:
            candidates_cache_path = _candidate_cache_path(
                candidate_cache_dir=Path(candidate_cache_dir),
                timeframe=timeframe,
                symbols=resolved_symbols,
                asia_start_hour_utc=asia_start_hour_utc,
                asia_end_hour_utc=asia_end_hour_utc,
                trigger_minute=trigger_minute,
                max_follow_minutes=max_follow_minutes,
            )
            if reuse_candidate_cache and candidates_cache_path.exists():
                candidates = pd.read_parquet(candidates_cache_path)
                active_logger.info(
                    "hourly-asia-pump: timeframe=%s reused candidate cache=%s rows=%s",
                    timeframe.value,
                    candidates_cache_path,
                    len(candidates),
                )

        if candidates.empty:
            candidates = _build_timeframe_candidates(
                preparer=preparer,
                timeframe=timeframe,
                symbols=resolved_symbols,
                base_params=selection_params,
                logger=active_logger,
            )
            if candidates_cache_path is not None:
                candidates_cache_path.parent.mkdir(parents=True, exist_ok=True)
                candidates.to_parquet(candidates_cache_path, index=False)
                active_logger.info(
                    "hourly-asia-pump: timeframe=%s saved candidate cache=%s rows=%s",
                    timeframe.value,
                    candidates_cache_path,
                    len(candidates),
                )
        grid_summary = _build_grid_summary(timeframe=timeframe, events=candidates, grid=grid)
        grid_frames.append(grid_summary)

        profile_summary = grid_summary[grid_summary["profile_id"].notna()].copy() if not grid_summary.empty else pd.DataFrame()
        profile_frames.append(profile_summary)

        selected_events = _filter_events_for_params(candidates, selection_params)
        if not selected_events.empty:
            selected_events["selection_profile"] = selection_profile
        selected_event_frames.append(selected_events)
        common_pattern_frames.append(_build_common_patterns(selected_events))
        symbol_summary_frames.append(_build_selected_symbol_summary(selected_events))

        active_logger.info(
            "hourly-asia-pump: timeframe=%s candidates=%s selected_events=%s",
            timeframe.value,
            len(candidates),
            len(selected_events),
        )

    grid_summary_all = pd.concat(grid_frames, ignore_index=True) if grid_frames else pd.DataFrame()
    profile_summary_all = pd.concat(profile_frames, ignore_index=True) if profile_frames else pd.DataFrame()
    selected_events_all = pd.concat(selected_event_frames, ignore_index=True) if selected_event_frames else pd.DataFrame()
    common_patterns_all = pd.concat(common_pattern_frames, ignore_index=True) if common_pattern_frames else pd.DataFrame()
    symbol_summary_all = pd.concat(symbol_summary_frames, ignore_index=True) if symbol_summary_frames else pd.DataFrame()
    top_grid_rows = _top_grid_rows_by_timeframe(grid_summary_all, limit=10)

    grid_summary_path = output_dir / "grid_summary.csv"
    grid_summary_all.to_csv(grid_summary_path, index=False)

    profile_summary_path = output_dir / "profile_summary.csv"
    profile_summary_all.to_csv(profile_summary_path, index=False)

    selected_events_path = output_dir / "selected_profile_events.csv"
    selected_events_all.to_csv(selected_events_path, index=False)

    common_patterns_path = output_dir / "common_patterns_by_timeframe.csv"
    common_patterns_all.to_csv(common_patterns_path, index=False)

    symbol_summary_path = output_dir / "selected_profile_symbol_summary.csv"
    symbol_summary_all.to_csv(symbol_summary_path, index=False)

    top_grid_rows_path = output_dir / "top_grid_by_timeframe.csv"
    top_grid_rows.to_csv(top_grid_rows_path, index=False)

    run_context = {
        "strategy": "hourly_asia_pump",
        "timeframes": [timeframe.value for timeframe in timeframes],
        "selection_profile": selection_profile,
        "asia_start_hour_utc": asia_start_hour_utc,
        "asia_end_hour_utc": asia_end_hour_utc,
        "trigger_minute": trigger_minute,
        "atr_window_minutes": DEFAULT_ATR_WINDOW_MINUTES,
        "volume_window_minutes": DEFAULT_VOLUME_WINDOW_MINUTES,
        "breakout_lookback_minutes": DEFAULT_BREAKOUT_LOOKBACK_MINUTES,
        "max_follow_minutes": max_follow_minutes,
        "top_n": top_n,
        "symbols": list(symbols or []),
        "resolved_symbols_by_timeframe": resolved_symbols_by_timeframe,
    }
    context_path = output_dir / "research_context.json"
    context_path.write_text(json.dumps(run_context, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = _write_report(
        output_dir=output_dir,
        run_context=run_context,
        profile_summary=profile_summary_all,
        selected_common_patterns=common_patterns_all,
        top_grid_rows=top_grid_rows,
        selection_profile=selection_profile,
    )

    active_logger.info(
        "hourly-asia-pump research artifacts saved: output_dir=%s timeframes=%s selected_events=%s",
        output_dir,
        ",".join(timeframe.value for timeframe in timeframes),
        len(selected_events_all),
    )
    return {
        "grid_summary": grid_summary_path,
        "profile_summary": profile_summary_path,
        "selected_profile_events": selected_events_path,
        "common_patterns": common_patterns_path,
        "selected_profile_symbol_summary": symbol_summary_path,
        "top_grid_by_timeframe": top_grid_rows_path,
        "research_context": context_path,
        "report": report_path,
    }
