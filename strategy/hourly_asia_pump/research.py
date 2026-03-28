"""Research helpers for hourly Asia-session top-of-hour pumps."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import math
import os
import time
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
    HourlyAsiaPumpTradePortfolio,
    HourlyAsiaPumpTradePortfolioComponent,
    HourlyAsiaPumpTradeModel,
    build_hourly_asia_pump_grid,
    build_hourly_asia_pump_profile,
    build_hourly_asia_pump_trade_portfolios,
    build_hourly_asia_pump_trade_models,
)
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

_HORIZON_MINUTES: tuple[int, ...] = (5, 15, 30, 60)
_EARLY_ENTRY_TARGET_PCTS: tuple[float, ...] = (0.01, 0.02, 0.05, 0.10)
_LAST_RED_LOOKBACK_MINUTES = 60


def _timeframe_minutes(timeframe: Timeframe) -> int:
    return timeframe.to_milliseconds() // 60_000


def _bars_for_minutes(timeframe: Timeframe, minutes: int) -> int:
    return max(1, math.ceil(minutes / _timeframe_minutes(timeframe)))


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "n/a"
    rounded = int(round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _progress_snapshot(*, completed: int, total: int, started_at: float) -> tuple[float, float, float | None]:
    elapsed = max(0.0, time.time() - started_at)
    if total <= 0:
        return 100.0, elapsed, 0.0
    ratio = min(1.0, completed / total)
    if completed <= 0 or ratio <= 0:
        return 0.0, elapsed, None
    eta_seconds = (elapsed / ratio) - elapsed
    return ratio * 100.0, elapsed, max(0.0, eta_seconds)


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


def _min_or_none(series: pd.Series) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    return float(cleaned.min())


def _max_or_none(series: pd.Series) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    return float(cleaned.max())


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
    pre_base_window_bars = _bars_for_minutes(timeframe, 60)
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
    pre_base_high = frame["high"].rolling(
        pre_base_window_bars,
        min_periods=max(3, pre_base_window_bars // 2),
    ).max().shift(1)
    pre_base_low = frame["low"].rolling(
        pre_base_window_bars,
        min_periods=max(3, pre_base_window_bars // 2),
    ).min().shift(1)
    pre_base_open = frame["open"].shift(pre_base_window_bars)
    pre_base_close = frame["close"].shift(1)
    close_to_high_frac = (frame["high"] - frame["close"]) / true_range.where(true_range > 0)
    pre_base_range_pct = pd.to_numeric((pre_base_high - pre_base_low) / frame["open"], errors="coerce")
    pre_base_drift_pct = pd.to_numeric((pre_base_close / pre_base_open - 1.0).abs(), errors="coerce")
    trigger_range_pct = pd.to_numeric(true_range / frame["open"], errors="coerce")
    pre_base_range_vs_trigger = pd.to_numeric(
        pre_base_range_pct / trigger_range_pct.where(trigger_range_pct > 0),
        errors="coerce",
    )

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
            "trigger_range_pct": trigger_range_pct,
            "range_atr": pd.to_numeric(true_range / atr, errors="coerce"),
            "body_atr": pd.to_numeric((frame["close"] - frame["open"]) / atr, errors="coerce"),
            "volume_mult": pd.to_numeric(frame["volume"] / volume_baseline, errors="coerce"),
            "close_to_high_frac": pd.to_numeric(close_to_high_frac.fillna(1.0), errors="coerce"),
            "breakout_pct": pd.to_numeric(frame["high"] / prior_high - 1.0, errors="coerce"),
            "pre_base_range_pct_60m": pre_base_range_pct,
            "pre_base_drift_pct_60m": pre_base_drift_pct,
            "pre_base_range_vs_trigger": pre_base_range_vs_trigger,
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
    scan_started_at = time.time()

    logger.info(
        "hourly-asia-pump: timeframe=%s stage=candidate-scan symbols_total=%s workers=%s",
        timeframe.value,
        len(symbols),
        max_workers,
    )

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
                progress_pct, elapsed, eta_seconds = _progress_snapshot(
                    completed=index,
                    total=len(symbols),
                    started_at=scan_started_at,
                )
                logger.info(
                    "hourly-asia-pump: timeframe=%s stage=candidate-scan progress=%.1f%% scanned_symbols=%s/%s candidate_rows=%s mode=local elapsed=%s eta=%s",
                    timeframe.value,
                    progress_pct,
                    index,
                    len(symbols),
                    total_candidates,
                    _format_duration(elapsed),
                    _format_duration(eta_seconds),
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
                    progress_pct, elapsed, eta_seconds = _progress_snapshot(
                        completed=processed_symbols,
                        total=len(symbols),
                        started_at=scan_started_at,
                    )
                    logger.info(
                        "hourly-asia-pump: timeframe=%s stage=candidate-scan progress=%.1f%% scanned_symbols=%s/%s candidate_rows=%s workers=%s mode=process chunk_size=%s elapsed=%s eta=%s",
                        timeframe.value,
                        progress_pct,
                        processed_symbols,
                        len(symbols),
                        total_candidates,
                        max_workers,
                        chunk_size,
                        _format_duration(elapsed),
                        _format_duration(eta_seconds),
                    )

    if not candidate_parts:
        progress_pct, elapsed, eta_seconds = _progress_snapshot(
            completed=len(symbols),
            total=len(symbols),
            started_at=scan_started_at,
        )
        logger.info(
            "hourly-asia-pump: timeframe=%s stage=candidate-scan progress=%.1f%% scanned_symbols=%s/%s candidate_rows=0 elapsed=%s eta=%s",
            timeframe.value,
            progress_pct,
            len(symbols),
            len(symbols),
            _format_duration(elapsed),
            _format_duration(eta_seconds),
        )
        return pd.DataFrame()
    result = pd.concat(candidate_parts, ignore_index=True)
    progress_pct, elapsed, eta_seconds = _progress_snapshot(
        completed=len(symbols),
        total=len(symbols),
        started_at=scan_started_at,
    )
    logger.info(
        "hourly-asia-pump: timeframe=%s stage=candidate-scan-finished progress=%.1f%% scanned_symbols=%s/%s candidate_rows=%s elapsed=%s eta=%s",
        timeframe.value,
        progress_pct,
        len(symbols),
        len(symbols),
        len(result),
        _format_duration(elapsed),
        _format_duration(eta_seconds),
    )
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


def _simulate_break_first_high_trailing_stop(
    *,
    open_values: Any,
    high_values: Any,
    low_values: Any,
    close_values: Any,
    timestamp_values: Any,
    timeframe: Timeframe,
    row_index: int,
    max_follow_minutes: int,
) -> dict[str, object]:
    tf_minutes = _timeframe_minutes(timeframe)
    max_follow_bars = _bars_for_minutes(timeframe, max_follow_minutes)
    end_idx = min(len(open_values) - 1, row_index + max_follow_bars)
    trigger_high = float(high_values[row_index])

    entry_idx: int | None = None
    for idx in range(row_index + 1, end_idx + 1):
        if float(high_values[idx]) >= trigger_high:
            entry_idx = idx
            break

    base_result: dict[str, object] = {
        "entry_model": "break_first_high_trail_last_red",
        "entry_triggered": entry_idx is not None,
        "entry_price": trigger_high if trigger_high > 0 else None,
        "entry_timestamp_ms": None,
        "entry_timestamp_utc": None,
        "entry_delay_bars": None,
        "entry_delay_minutes": None,
        "initial_stop_price": None,
        "initial_stop_timestamp_ms": None,
        "initial_stop_timestamp_utc": None,
        "initial_risk_pct": None,
        "max_return_after_entry_pct": None,
        "max_r_multiple": None,
        "exit_reason": "no_entry",
        "exit_price": None,
        "exit_timestamp_ms": None,
        "exit_timestamp_utc": None,
        "exit_return_pct": None,
        "exit_r_multiple": None,
        "bars_held_after_entry": None,
        "minutes_held_after_entry": None,
    }
    for target_pct in _EARLY_ENTRY_TARGET_PCTS:
        pct_label = int(round(target_pct * 100))
        base_result[f"target_{pct_label}pct_hit"] = False

    if entry_idx is None:
        return base_result

    entry_timestamp_ms = int(timestamp_values[entry_idx])
    base_result.update(
        {
            "entry_timestamp_ms": entry_timestamp_ms,
            "entry_timestamp_utc": pd.to_datetime(entry_timestamp_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S"),
            "entry_delay_bars": entry_idx - row_index,
            "entry_delay_minutes": (entry_idx - row_index) * tf_minutes,
        }
    )

    lookback_bars = _bars_for_minutes(timeframe, _LAST_RED_LOOKBACK_MINUTES)
    stop_idx: int | None = None
    lookback_start = max(0, row_index - lookback_bars)
    for idx in range(row_index - 1, lookback_start - 1, -1):
        if float(close_values[idx]) < float(open_values[idx]):
            stop_idx = idx
            break
    if stop_idx is None and row_index > 0:
        stop_idx = row_index - 1
    if stop_idx is None:
        return base_result

    entry_price = float(trigger_high)
    initial_stop_price = float(low_values[stop_idx])
    risk = entry_price - initial_stop_price
    stop_timestamp_ms = int(timestamp_values[stop_idx])
    base_result.update(
        {
            "initial_stop_price": initial_stop_price,
            "initial_stop_timestamp_ms": stop_timestamp_ms,
            "initial_stop_timestamp_utc": pd.to_datetime(stop_timestamp_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    if entry_price <= 0 or risk <= 0:
        base_result["exit_reason"] = "invalid_risk"
        return base_result

    current_stop = initial_stop_price
    candidate_stop: float | None = None
    highest_high = max(entry_price, float(high_values[entry_idx]))
    max_high_after_entry = highest_high
    exit_idx: int | None = None
    exit_price: float | None = None
    exit_reason = "time_exit"

    for idx in range(entry_idx + 1, end_idx + 1):
        low_value = float(low_values[idx])
        high_value = float(high_values[idx])
        open_value = float(open_values[idx])
        close_value = float(close_values[idx])

        if low_value <= current_stop:
            exit_idx = idx
            exit_price = current_stop
            exit_reason = "stop"
            break

        if high_value > max_high_after_entry:
            max_high_after_entry = high_value

        if high_value > highest_high:
            highest_high = high_value
            if candidate_stop is not None and candidate_stop > current_stop:
                current_stop = candidate_stop

        if close_value < open_value:
            candidate_stop = low_value

    if exit_idx is None:
        exit_idx = end_idx
        exit_price = float(close_values[end_idx])

    max_return_after_entry_pct = (max_high_after_entry / entry_price) - 1.0
    exit_return_pct = (float(exit_price) / entry_price) - 1.0
    exit_timestamp_ms = int(timestamp_values[exit_idx])
    result = {
        **base_result,
        "initial_risk_pct": risk / entry_price,
        "max_return_after_entry_pct": max_return_after_entry_pct,
        "max_r_multiple": max_return_after_entry_pct / (risk / entry_price) if risk > 0 else None,
        "exit_reason": exit_reason,
        "exit_price": exit_price,
        "exit_timestamp_ms": exit_timestamp_ms,
        "exit_timestamp_utc": pd.to_datetime(exit_timestamp_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S"),
        "exit_return_pct": exit_return_pct,
        "exit_r_multiple": exit_return_pct / (risk / entry_price) if risk > 0 else None,
        "bars_held_after_entry": exit_idx - entry_idx,
        "minutes_held_after_entry": (exit_idx - entry_idx) * tf_minutes,
    }
    for target_pct in _EARLY_ENTRY_TARGET_PCTS:
        pct_label = int(round(target_pct * 100))
        result[f"target_{pct_label}pct_hit"] = max_return_after_entry_pct >= target_pct
    return result


def _build_early_entry_events_for_timeframe(
    *,
    preparer: DataPreparer,
    timeframe: Timeframe,
    selected_events: pd.DataFrame,
    max_follow_minutes: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    if selected_events.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    grouped = list(selected_events.groupby("symbol", sort=True))
    total_symbols = len(grouped)
    started_at = time.time()
    for symbol_index, (symbol, group) in enumerate(grouped, start=1):
        frame = preparer.load_symbol_data(symbol, timeframe)
        if frame.empty:
            continue
        open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype="float64")
        high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64")
        low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64")
        close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")
        timestamp_values = pd.to_numeric(frame["timestamp"], errors="coerce").fillna(0).astype("int64").to_numpy()

        for event in group.to_dict("records"):
            row_index = int(event["row_index"])
            trailing = _simulate_break_first_high_trailing_stop(
                open_values=open_values,
                high_values=high_values,
                low_values=low_values,
                close_values=close_values,
                timestamp_values=timestamp_values,
                timeframe=timeframe,
                row_index=row_index,
                max_follow_minutes=max_follow_minutes,
            )
            rows.append({**event, **trailing})

        if symbol_index % 25 == 0 or symbol_index == total_symbols:
            progress_pct, elapsed, eta_seconds = _progress_snapshot(
                completed=symbol_index,
                total=total_symbols,
                started_at=started_at,
            )
            logger.info(
                "hourly-asia-pump: timeframe=%s stage=early-entry-analysis progress=%.1f%% scanned_symbols=%s/%s analyzed_events=%s elapsed=%s eta=%s",
                timeframe.value,
                progress_pct,
                symbol_index,
                total_symbols,
                len(rows),
                _format_duration(elapsed),
                _format_duration(eta_seconds),
            )
    return pd.DataFrame(rows)


def _build_early_entry_summary(early_entry_events: pd.DataFrame) -> pd.DataFrame:
    if early_entry_events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for timeframe, group in early_entry_events.groupby("timeframe", sort=True):
        entered = group[group["entry_triggered"].astype(bool)].copy()
        rows.append(
            {
                "timeframe": timeframe,
                "selected_events_count": int(len(group)),
                "entries_triggered_count": int(len(entered)),
                "entry_rate": (len(entered) / len(group)) if len(group) > 0 else None,
                "median_entry_delay_minutes": _median_or_none(entered.get("entry_delay_minutes", pd.Series(dtype="float64"))),
                "median_initial_risk_pct": _median_or_none(entered.get("initial_risk_pct", pd.Series(dtype="float64"))),
                "median_max_return_after_entry_pct": _median_or_none(entered.get("max_return_after_entry_pct", pd.Series(dtype="float64"))),
                "p75_max_return_after_entry_pct": _quantile_or_none(entered.get("max_return_after_entry_pct", pd.Series(dtype="float64")), 0.75),
                "median_exit_return_pct": _median_or_none(entered.get("exit_return_pct", pd.Series(dtype="float64"))),
                "p75_exit_return_pct": _quantile_or_none(entered.get("exit_return_pct", pd.Series(dtype="float64")), 0.75),
                "median_max_r_multiple": _median_or_none(entered.get("max_r_multiple", pd.Series(dtype="float64"))),
                "median_exit_r_multiple": _median_or_none(entered.get("exit_r_multiple", pd.Series(dtype="float64"))),
                "exit_positive_rate": _mean_or_none((pd.to_numeric(entered.get("exit_return_pct", pd.Series(dtype="float64")), errors="coerce") > 0).astype("float64")),
                "stop_exit_rate": _mean_or_none((entered.get("exit_reason", pd.Series(dtype="object")).astype(str) == "stop").astype("float64")),
                "time_exit_rate": _mean_or_none((entered.get("exit_reason", pd.Series(dtype="object")).astype(str) == "time_exit").astype("float64")),
                "target_1pct_hit_rate": _mean_or_none(entered.get("target_1pct_hit", pd.Series(dtype="float64"))),
                "target_2pct_hit_rate": _mean_or_none(entered.get("target_2pct_hit", pd.Series(dtype="float64"))),
                "target_5pct_hit_rate": _mean_or_none(entered.get("target_5pct_hit", pd.Series(dtype="float64"))),
                "target_10pct_hit_rate": _mean_or_none(entered.get("target_10pct_hit", pd.Series(dtype="float64"))),
            }
        )
    return pd.DataFrame(rows).sort_values("timeframe").reset_index(drop=True)


def _event_matches_trade_model(event: dict[str, object], model: HourlyAsiaPumpTradeModel) -> bool:
    trigger_return_pct = _safe_numeric(event.get("trigger_return_pct"))
    range_atr = _safe_numeric(event.get("range_atr"))
    body_atr = _safe_numeric(event.get("body_atr"))
    volume_mult = _safe_numeric(event.get("volume_mult"))
    close_to_high_frac = _safe_numeric(event.get("close_to_high_frac"))
    if trigger_return_pct is None or range_atr is None or body_atr is None or volume_mult is None or close_to_high_frac is None:
        return False
    matches = (
        trigger_return_pct >= model.min_trigger_return_pct
        and range_atr >= model.min_range_atr
        and body_atr >= model.min_body_atr
        and volume_mult >= model.min_volume_mult
        and close_to_high_frac <= model.max_close_to_high_frac
    )
    if not matches:
        return False

    pre_base_range_pct_60m = _safe_numeric(event.get("pre_base_range_pct_60m"))
    pre_base_drift_pct_60m = _safe_numeric(event.get("pre_base_drift_pct_60m"))
    pre_base_range_vs_trigger = _safe_numeric(event.get("pre_base_range_vs_trigger"))
    if model.max_pre_base_range_pct_60m is not None:
        if pre_base_range_pct_60m is None or pre_base_range_pct_60m > model.max_pre_base_range_pct_60m:
            return False
    if model.max_pre_base_drift_pct_60m is not None:
        if pre_base_drift_pct_60m is None or pre_base_drift_pct_60m > model.max_pre_base_drift_pct_60m:
            return False
    if model.max_pre_base_range_vs_trigger is not None:
        if pre_base_range_vs_trigger is None or pre_base_range_vs_trigger > model.max_pre_base_range_vs_trigger:
            return False
    return True


def _build_pre_entry_context(
    *,
    row_index: int,
    entry_idx: int,
    trigger_high: float,
    trigger_low: float,
    trigger_range: float,
    trigger_volume: float,
    open_values: Any,
    low_values: Any,
    close_values: Any,
    volume_values: Any,
) -> dict[str, float]:
    if entry_idx <= row_index + 1:
        return {
            "pre_entry_pullback_frac": 0.0,
            "pre_entry_red_volume_frac": 0.0,
            "pre_entry_low_frac_of_trigger_range": 1.0,
        }

    pre_slice = slice(row_index + 1, entry_idx)
    pre_lows = low_values[pre_slice]
    min_low = float(pre_lows.min()) if len(pre_lows) > 0 else trigger_high
    red_volume_frac = 0.0
    for idx in range(row_index + 1, entry_idx):
        if float(close_values[idx]) < float(open_values[idx]) and trigger_volume > 0:
            red_volume_frac = max(red_volume_frac, float(volume_values[idx]) / trigger_volume)
    return {
        "pre_entry_pullback_frac": max(0.0, (trigger_high - min_low) / trigger_range) if trigger_range > 0 else 0.0,
        "pre_entry_red_volume_frac": red_volume_frac,
        "pre_entry_low_frac_of_trigger_range": ((min_low - trigger_low) / trigger_range) if trigger_range > 0 else 0.0,
    }


def _context_entry_allowed(model: HourlyAsiaPumpTradeModel, context: dict[str, float]) -> bool:
    if model.max_pre_entry_pullback_frac is not None and context["pre_entry_pullback_frac"] > model.max_pre_entry_pullback_frac:
        return False
    if model.max_pre_entry_red_volume_frac is not None and context["pre_entry_red_volume_frac"] > model.max_pre_entry_red_volume_frac:
        return False
    if (
        model.min_pre_entry_low_frac_of_trigger_range is not None
        and context["pre_entry_low_frac_of_trigger_range"] < model.min_pre_entry_low_frac_of_trigger_range
    ):
        return False
    return True


def _find_trade_model_entry(
    *,
    model: HourlyAsiaPumpTradeModel,
    event: dict[str, object],
    open_values: Any,
    high_values: Any,
    low_values: Any,
    close_values: Any,
    volume_values: Any,
    timestamp_values: Any,
    row_index: int,
) -> dict[str, object]:
    trigger_open = float(open_values[row_index])
    trigger_high = float(high_values[row_index])
    trigger_low = float(low_values[row_index])
    trigger_close = float(close_values[row_index])
    trigger_volume = float(volume_values[row_index])
    trigger_range = max(1e-12, trigger_high - trigger_low)
    trigger_body_mid = trigger_open + 0.5 * (trigger_close - trigger_open)
    trigger_body_upper_quarter = trigger_open + 0.75 * (trigger_close - trigger_open)

    def _resolve_initial_stop(default_price: float) -> float:
        if model.initial_stop_style == "trigger_low":
            return trigger_low
        if model.initial_stop_style == "trigger_body_mid":
            return trigger_body_mid
        if model.initial_stop_style == "trigger_body_upper_quarter":
            return trigger_body_upper_quarter
        if model.initial_stop_style == "trigger_close":
            return trigger_close
        return default_price

    no_entry: dict[str, object] = {
        "trade_triggered": False,
        "entry_idx": None,
        "entry_price": None,
        "entry_reason": "no_entry",
        "initial_stop_price": None,
        "initial_stop_reason": None,
    }

    if model.entry_style == "break_trigger_high":
        entry_end_idx = min(len(high_values) - 1, row_index + model.max_entry_bars)
        for idx in range(row_index + 1, entry_end_idx + 1):
            if float(high_values[idx]) >= trigger_high:
                initial_stop_price = _resolve_initial_stop(trigger_body_mid)
                context = _build_pre_entry_context(
                    row_index=row_index,
                    entry_idx=idx,
                    trigger_high=trigger_high,
                    trigger_low=trigger_low,
                    trigger_range=trigger_range,
                    trigger_volume=trigger_volume,
                    open_values=open_values,
                    low_values=low_values,
                    close_values=close_values,
                    volume_values=volume_values,
                )
                if not _context_entry_allowed(model, context):
                    continue
                return {
                    "trade_triggered": True,
                    "entry_idx": idx,
                    "entry_price": trigger_high,
                    "entry_reason": "break_trigger_high",
                    "initial_stop_price": initial_stop_price,
                    "initial_stop_reason": model.initial_stop_style,
                    **context,
                }
        return no_entry

    if model.entry_style in {"pullback_reclaim", "pressure_reclaim"}:
        pullback_end_idx = min(len(high_values) - 2, row_index + model.max_entry_bars)
        for pullback_idx in range(row_index + 1, pullback_end_idx + 1):
            pullback_low = float(low_values[pullback_idx])
            retrace_frac = (trigger_high - pullback_low) / trigger_range
            is_red_or_weaker = float(close_values[pullback_idx]) <= float(open_values[pullback_idx]) or float(close_values[pullback_idx]) < float(close_values[pullback_idx - 1])
            if retrace_frac > model.max_pullback_frac or not is_red_or_weaker:
                continue
            if model.entry_style == "pressure_reclaim" and float(volume_values[pullback_idx]) > (trigger_volume * model.pullback_volume_frac):
                continue
            pullback_high = float(high_values[pullback_idx])
            entry_end_idx = min(len(high_values) - 1, pullback_idx + model.max_entry_bars)
            for idx in range(pullback_idx + 1, entry_end_idx + 1):
                if float(high_values[idx]) >= pullback_high:
                    context = _build_pre_entry_context(
                        row_index=row_index,
                        entry_idx=idx,
                        trigger_high=trigger_high,
                        trigger_low=trigger_low,
                        trigger_range=trigger_range,
                        trigger_volume=trigger_volume,
                        open_values=open_values,
                        low_values=low_values,
                        close_values=close_values,
                        volume_values=volume_values,
                    )
                    if not _context_entry_allowed(model, context):
                        continue
                    initial_stop_price = _resolve_initial_stop(pullback_low)
                    return {
                        "trade_triggered": True,
                        "entry_idx": idx,
                        "entry_price": pullback_high,
                        "entry_reason": model.entry_style,
                        "initial_stop_price": initial_stop_price,
                        "initial_stop_reason": model.initial_stop_style,
                        **context,
                    }
        return no_entry

    if model.entry_style == "flag_break":
        flag_start = row_index + 1
        flag_end = min(len(high_values) - 2, flag_start + model.flag_bars - 1)
        if flag_end <= flag_start:
            return no_entry
        flag_high = float(max(high_values[flag_start : flag_end + 1]))
        flag_low = float(min(low_values[flag_start : flag_end + 1]))
        flag_range = flag_high - flag_low
        retrace_frac = (trigger_high - flag_low) / trigger_range
        if retrace_frac > model.max_pullback_frac or flag_range > (trigger_range * model.flag_max_range_frac):
            return no_entry
        entry_end_idx = min(len(high_values) - 1, flag_end + model.max_entry_bars)
        for idx in range(flag_end + 1, entry_end_idx + 1):
            if float(high_values[idx]) >= flag_high:
                context = _build_pre_entry_context(
                    row_index=row_index,
                    entry_idx=idx,
                    trigger_high=trigger_high,
                    trigger_low=trigger_low,
                    trigger_range=trigger_range,
                    trigger_volume=trigger_volume,
                    open_values=open_values,
                    low_values=low_values,
                    close_values=close_values,
                    volume_values=volume_values,
                )
                if not _context_entry_allowed(model, context):
                    continue
                initial_stop_price = _resolve_initial_stop(flag_low)
                return {
                    "trade_triggered": True,
                    "entry_idx": idx,
                    "entry_price": flag_high,
                    "entry_reason": "flag_break",
                    "initial_stop_price": initial_stop_price,
                    "initial_stop_reason": model.initial_stop_style,
                    **context,
                }
        return no_entry

    return no_entry


def _simulate_trade_model_from_entry(
    *,
    model: HourlyAsiaPumpTradeModel,
    timeframe: Timeframe,
    open_values: Any,
    high_values: Any,
    low_values: Any,
    close_values: Any,
    timestamp_values: Any,
    row_index: int,
    entry_idx: int,
    entry_price: float,
    initial_stop_price: float,
    commission_rate: float,
) -> dict[str, object]:
    tf_minutes = _timeframe_minutes(timeframe)
    end_idx = min(len(close_values) - 1, entry_idx + _bars_for_minutes(timeframe, model.max_hold_minutes))
    risk_pct = (entry_price - initial_stop_price) / entry_price if entry_price > 0 else None

    base_result: dict[str, object] = {
        "trade_triggered": True,
        "entry_timestamp_ms": int(timestamp_values[entry_idx]),
        "entry_timestamp_utc": pd.to_datetime(int(timestamp_values[entry_idx]), unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_delay_bars": entry_idx - row_index,
        "entry_delay_minutes": (entry_idx - row_index) * tf_minutes,
        "initial_risk_pct": risk_pct,
        "exit_reason": "invalid_risk",
        "exit_timestamp_ms": None,
        "exit_timestamp_utc": None,
        "exit_return_pct": None,
        "exit_r_multiple": None,
        "max_return_after_entry_pct": None,
        "max_r_multiple": None,
        "bars_held_after_entry": None,
        "minutes_held_after_entry": None,
        "partial_taken": False,
        "realized_ge_5pct": False,
        "realized_ge_10pct": False,
    }
    for target_pct in _EARLY_ENTRY_TARGET_PCTS:
        pct_label = int(round(target_pct * 100))
        base_result[f"mfe_ge_{pct_label}pct"] = False

    if risk_pct is None or risk_pct <= 0:
        return base_result

    target_from_r = model.partial_take_r * risk_pct
    partial_target_pct = max(model.partial_take_pct, target_from_r)
    partial_target_price = entry_price * (1.0 + partial_target_pct)

    current_stop = initial_stop_price
    remaining_fraction = 1.0
    realized_return_pct = -commission_rate
    partial_taken = False
    last_red_low: float | None = None
    highest_high = max(entry_price, float(high_values[entry_idx]))
    exit_idx: int | None = None
    exit_reason = "time_exit"
    trail_active = model.trail_activation_pct <= 0.0

    for idx in range(entry_idx + 1, end_idx + 1):
        low_value = float(low_values[idx])
        high_value = float(high_values[idx])
        open_value = float(open_values[idx])
        close_value = float(close_values[idx])

        if low_value <= current_stop:
            realized_return_pct += remaining_fraction * (((current_stop / entry_price) - 1.0) - commission_rate)
            remaining_fraction = 0.0
            exit_idx = idx
            exit_reason = "stop"
            break

        if (
            model.partial_fraction > 0.0
            and partial_target_pct > 0.0
            and not partial_taken
            and high_value >= partial_target_price
        ):
            realized_return_pct += model.partial_fraction * (partial_target_pct - commission_rate)
            remaining_fraction = max(0.0, remaining_fraction - model.partial_fraction)
            partial_taken = True
            if model.move_stop_to_be_after_partial:
                current_stop = max(current_stop, entry_price)

        if high_value > highest_high:
            highest_high = high_value
            if not trail_active and highest_high >= (entry_price * (1.0 + model.trail_activation_pct)):
                trail_active = True
            if trail_active:
                if model.trail_style == "prev_bar_low" and idx - 1 >= entry_idx:
                    current_stop = max(current_stop, float(low_values[idx - 1]))
                elif model.trail_style == "last_red_low" and last_red_low is not None:
                    current_stop = max(current_stop, last_red_low)

        if model.fast_fail_bars > 0 and (idx - entry_idx) == model.fast_fail_bars:
            min_progress_price = entry_price * (1.0 + model.fast_fail_min_return_pct)
            if highest_high < min_progress_price:
                realized_return_pct += remaining_fraction * (((close_value / entry_price) - 1.0) - commission_rate)
                remaining_fraction = 0.0
                exit_idx = idx
                exit_reason = "fast_fail"
                break

        if close_value < open_value:
            last_red_low = low_value

    if remaining_fraction > 0.0:
        realized_return_pct += remaining_fraction * (((float(close_values[end_idx]) / entry_price) - 1.0) - commission_rate)
        exit_idx = end_idx

    exit_timestamp_ms = int(timestamp_values[exit_idx]) if exit_idx is not None else None
    max_return_after_entry_pct = (highest_high / entry_price) - 1.0
    result = {
        **base_result,
        "exit_reason": exit_reason,
        "exit_timestamp_ms": exit_timestamp_ms,
        "exit_timestamp_utc": (
            pd.to_datetime(exit_timestamp_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S")
            if exit_timestamp_ms is not None
            else None
        ),
        "exit_return_pct": realized_return_pct,
        "exit_r_multiple": realized_return_pct / risk_pct if risk_pct > 0 else None,
        "max_return_after_entry_pct": max_return_after_entry_pct,
        "max_r_multiple": max_return_after_entry_pct / risk_pct if risk_pct > 0 else None,
        "bars_held_after_entry": (exit_idx - entry_idx) if exit_idx is not None else None,
        "minutes_held_after_entry": ((exit_idx - entry_idx) * tf_minutes) if exit_idx is not None else None,
        "partial_taken": partial_taken,
        "realized_ge_5pct": realized_return_pct >= 0.05,
        "realized_ge_10pct": realized_return_pct >= 0.10,
    }
    for target_pct in _EARLY_ENTRY_TARGET_PCTS:
        pct_label = int(round(target_pct * 100))
        result[f"mfe_ge_{pct_label}pct"] = max_return_after_entry_pct >= target_pct
    return result


def _build_trade_model_events_for_timeframe(
    *,
    preparer: DataPreparer,
    timeframe: Timeframe,
    selected_events: pd.DataFrame,
    trade_models: Sequence[HourlyAsiaPumpTradeModel],
    commission_rate: float,
    logger: logging.Logger,
) -> pd.DataFrame:
    if selected_events.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    grouped = list(selected_events.groupby("symbol", sort=True))
    total_symbols = len(grouped)
    started_at = time.time()
    for symbol_index, (symbol, group) in enumerate(grouped, start=1):
        frame = preparer.load_symbol_data(symbol, timeframe)
        if frame.empty:
            continue
        open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype="float64")
        high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64")
        low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64")
        close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")
        volume_values = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype="float64")
        timestamp_values = pd.to_numeric(frame["timestamp"], errors="coerce").fillna(0).astype("int64").to_numpy()

        for event in group.to_dict("records"):
            row_index = int(event["row_index"])
            for model in trade_models:
                if not _event_matches_trade_model(event, model):
                    rows.append(
                        {
                            **event,
                            "trade_model_id": model.model_id,
                            "trade_model_label": model.label,
                            "trade_model_timeframe": timeframe.value,
                            "trade_triggered": False,
                            "entry_reason": "filtered_out",
                            "exit_reason": "filtered_out",
                        }
                    )
                    continue

                entry = _find_trade_model_entry(
                    model=model,
                    event=event,
                    open_values=open_values,
                    high_values=high_values,
                    low_values=low_values,
                    close_values=close_values,
                    volume_values=volume_values,
                    timestamp_values=timestamp_values,
                    row_index=row_index,
                )
                if not bool(entry.get("trade_triggered")):
                    rows.append(
                        {
                            **event,
                            "trade_model_id": model.model_id,
                            "trade_model_label": model.label,
                            "trade_model_timeframe": timeframe.value,
                            **entry,
                        }
                    )
                    continue

                trade = _simulate_trade_model_from_entry(
                    model=model,
                    timeframe=timeframe,
                    open_values=open_values,
                    high_values=high_values,
                    low_values=low_values,
                    close_values=close_values,
                    timestamp_values=timestamp_values,
                    row_index=row_index,
                    entry_idx=int(entry["entry_idx"]),
                    entry_price=float(entry["entry_price"]),
                    initial_stop_price=float(entry["initial_stop_price"]),
                    commission_rate=commission_rate,
                )
                rows.append(
                    {
                        **event,
                        "trade_model_id": model.model_id,
                        "trade_model_label": model.label,
                        "trade_model_timeframe": timeframe.value,
                        **entry,
                        **trade,
                    }
                )

        if symbol_index % 25 == 0 or symbol_index == total_symbols:
            progress_pct, elapsed, eta_seconds = _progress_snapshot(
                completed=symbol_index,
                total=total_symbols,
                started_at=started_at,
            )
            logger.info(
                "hourly-asia-pump: timeframe=%s stage=trade-models progress=%.1f%% scanned_symbols=%s/%s model_rows=%s elapsed=%s eta=%s",
                timeframe.value,
                progress_pct,
                symbol_index,
                total_symbols,
                len(rows),
                _format_duration(elapsed),
                _format_duration(eta_seconds),
            )

    return pd.DataFrame(rows)


def _profit_factor(series: pd.Series) -> float | None:
    cleaned = pd.to_numeric(series, errors="coerce").dropna()
    if cleaned.empty:
        return None
    wins = cleaned[cleaned > 0]
    losses = cleaned[cleaned < 0]
    if losses.empty:
        return None if wins.empty else float("inf")
    return float(wins.sum() / abs(losses.sum())) if not wins.empty else 0.0


def _build_trade_model_summary(trade_model_events: pd.DataFrame) -> pd.DataFrame:
    if trade_model_events.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    grouped = trade_model_events.groupby(["timeframe", "trade_model_id", "trade_model_label"], sort=True)
    for (timeframe, model_id, model_label), group in grouped:
        trades = group[group["trade_triggered"].astype(bool)].copy()
        entry_timestamps = pd.to_datetime(trades.get("entry_timestamp_ms", pd.Series(dtype="float64")), unit="ms", utc=True, errors="coerce").dropna()
        span_days = max(1.0, ((entry_timestamps.max() - entry_timestamps.min()).total_seconds() / 86_400) if len(entry_timestamps) >= 2 else float(max(1, trades.get("date_utc", pd.Series(dtype='object')).nunique())))
        trades_per_year = (len(trades) / span_days) * 365.0 if len(trades) > 0 else 0.0
        exit_returns = pd.to_numeric(trades.get("exit_return_pct", pd.Series(dtype="float64")), errors="coerce").dropna()
        rows.append(
            {
                "timeframe": timeframe,
                "trade_model_id": model_id,
                "trade_model_label": model_label,
                "setups_count": int(len(group)),
                "trades_count": int(len(trades)),
                "trade_trigger_rate": (len(trades) / len(group)) if len(group) > 0 else None,
                "trade_days_count": int(trades["date_utc"].nunique()) if not trades.empty and "date_utc" in trades.columns else 0,
                "trades_per_year": trades_per_year,
                "mean_return_pct": _mean_or_none(exit_returns),
                "median_return_pct": _median_or_none(exit_returns),
                "p75_return_pct": _quantile_or_none(exit_returns, 0.75),
                "win_rate": _mean_or_none((exit_returns > 0).astype("float64")),
                "profit_factor": _profit_factor(exit_returns),
                "avg_win_pct": _mean_or_none(exit_returns[exit_returns > 0]),
                "avg_loss_pct": _mean_or_none(exit_returns[exit_returns < 0]),
                "median_mfe_pct": _median_or_none(trades.get("max_return_after_entry_pct", pd.Series(dtype="float64"))),
                "mfe_ge_5pct_rate": _mean_or_none(trades.get("mfe_ge_5pct", pd.Series(dtype="float64"))),
                "mfe_ge_10pct_rate": _mean_or_none(trades.get("mfe_ge_10pct", pd.Series(dtype="float64"))),
                "realized_ge_5pct_rate": _mean_or_none(trades.get("realized_ge_5pct", pd.Series(dtype="float64"))),
                "realized_ge_10pct_rate": _mean_or_none(trades.get("realized_ge_10pct", pd.Series(dtype="float64"))),
                "median_hold_minutes": _median_or_none(trades.get("minutes_held_after_entry", pd.Series(dtype="float64"))),
                "median_initial_risk_pct": _median_or_none(trades.get("initial_risk_pct", pd.Series(dtype="float64"))),
                "meets_50_trades_per_year": trades_per_year >= 50.0,
                "meets_150_trades_per_year": trades_per_year >= 150.0,
            }
        )

    summary = pd.DataFrame(rows)
    return summary.sort_values(
        ["timeframe", "meets_150_trades_per_year", "profit_factor", "mean_return_pct", "trades_count"],
        ascending=[True, False, False, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _filter_trade_portfolio_component_rows(
    trade_model_events: pd.DataFrame,
    component: HourlyAsiaPumpTradePortfolioComponent,
) -> pd.DataFrame:
    if trade_model_events.empty:
        return pd.DataFrame()
    rows = trade_model_events[
        trade_model_events["trade_triggered"].astype(bool)
        & (trade_model_events["trade_model_id"].astype(str) == component.model_id)
    ].copy()
    if rows.empty:
        return rows
    hours = pd.to_numeric(rows.get("hour_utc", pd.Series(dtype="float64")), errors="coerce")
    if component.include_hours_utc is not None:
        rows = rows[hours.isin(component.include_hours_utc)].copy()
        hours = pd.to_numeric(rows.get("hour_utc", pd.Series(dtype="float64")), errors="coerce")
    if component.exclude_hours_utc is not None:
        rows = rows[~hours.isin(component.exclude_hours_utc)].copy()
    return rows


def _build_trade_portfolio_events(
    trade_model_events: pd.DataFrame,
    portfolios: Sequence[HourlyAsiaPumpTradePortfolio],
) -> pd.DataFrame:
    if trade_model_events.empty or not portfolios:
        return pd.DataFrame()

    rows: list[pd.DataFrame] = []
    for portfolio in portfolios:
        if portfolio.mode != "stacked":
            raise ValueError(f"Unsupported hourly Asia pump trade portfolio mode: {portfolio.mode}")
        for component in portfolio.components:
            component_rows = _filter_trade_portfolio_component_rows(trade_model_events, component)
            if component_rows.empty:
                continue
            component_rows = component_rows.copy()
            component_rows["trade_portfolio_id"] = portfolio.portfolio_id
            component_rows["trade_portfolio_label"] = portfolio.label
            component_rows["trade_portfolio_mode"] = portfolio.mode
            component_rows["portfolio_component_model_id"] = component.model_id
            component_rows["portfolio_component_include_hours_utc"] = (
                ",".join(str(hour) for hour in component.include_hours_utc)
                if component.include_hours_utc is not None
                else ""
            )
            component_rows["portfolio_component_exclude_hours_utc"] = (
                ",".join(str(hour) for hour in component.exclude_hours_utc)
                if component.exclude_hours_utc is not None
                else ""
            )
            rows.append(component_rows)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _build_trade_portfolio_summary(trade_portfolio_events: pd.DataFrame) -> pd.DataFrame:
    if trade_portfolio_events.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    grouped = trade_portfolio_events.groupby(["timeframe", "trade_portfolio_id", "trade_portfolio_label"], sort=True)
    for (timeframe, portfolio_id, portfolio_label), group in grouped:
        entry_timestamps = pd.to_datetime(
            group.get("entry_timestamp_ms", pd.Series(dtype="float64")),
            unit="ms",
            utc=True,
            errors="coerce",
        ).dropna()
        span_days = max(
            1.0,
            ((entry_timestamps.max() - entry_timestamps.min()).total_seconds() / 86_400)
            if len(entry_timestamps) >= 2
            else float(max(1, group.get("date_utc", pd.Series(dtype="object")).nunique())),
        )
        trades_per_year = (len(group) / span_days) * 365.0 if len(group) > 0 else 0.0
        exit_returns = pd.to_numeric(group.get("exit_return_pct", pd.Series(dtype="float64")), errors="coerce").dropna()
        months = pd.to_datetime(
            group.get("entry_timestamp_ms", pd.Series(dtype="float64")),
            unit="ms",
            utc=True,
            errors="coerce",
        ).dropna()
        monthly_returns = (
            pd.DataFrame(
                {
                    "month_utc": months.dt.tz_localize(None).dt.to_period("M").astype(str),
                    "exit_return_pct": pd.to_numeric(
                        group.loc[months.index, "exit_return_pct"],
                        errors="coerce",
                    ).to_numpy(),
                }
            )
            .groupby("month_utc", sort=True)["exit_return_pct"]
            .sum()
        )
        rows.append(
            {
                "timeframe": timeframe,
                "trade_portfolio_id": portfolio_id,
                "trade_portfolio_label": portfolio_label,
                "trades_count": int(len(group)),
                "trade_days_count": int(group["date_utc"].nunique()) if "date_utc" in group.columns else 0,
                "trades_per_year": trades_per_year,
                "mean_return_pct": _mean_or_none(exit_returns),
                "median_return_pct": _median_or_none(exit_returns),
                "p75_return_pct": _quantile_or_none(exit_returns, 0.75),
                "win_rate": _mean_or_none((exit_returns > 0).astype("float64")),
                "profit_factor": _profit_factor(exit_returns),
                "avg_win_pct": _mean_or_none(exit_returns[exit_returns > 0]),
                "avg_loss_pct": _mean_or_none(exit_returns[exit_returns < 0]),
                "median_mfe_pct": _median_or_none(group.get("max_return_after_entry_pct", pd.Series(dtype="float64"))),
                "realized_ge_5pct_rate": _mean_or_none(group.get("realized_ge_5pct", pd.Series(dtype="float64"))),
                "realized_ge_10pct_rate": _mean_or_none(group.get("realized_ge_10pct", pd.Series(dtype="float64"))),
                "median_hold_minutes": _median_or_none(group.get("minutes_held_after_entry", pd.Series(dtype="float64"))),
                "positive_months_count": int((monthly_returns > 0).sum()),
                "non_positive_months_count": int((monthly_returns <= 0).sum()),
                "all_active_months_positive": bool((monthly_returns > 0).all()) if not monthly_returns.empty else False,
                "worst_month_return_pct": _min_or_none(monthly_returns),
                "best_month_return_pct": _max_or_none(monthly_returns),
                "annual_sum_return_pct": float(exit_returns.sum()) if not exit_returns.empty else None,
                "meets_50_trades_per_year": trades_per_year >= 50.0,
                "meets_150_trades_per_year": trades_per_year >= 150.0,
            }
        )

    summary = pd.DataFrame(rows)
    return summary.sort_values(
        ["timeframe", "all_active_months_positive", "mean_return_pct", "profit_factor", "trades_count"],
        ascending=[True, False, False, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _build_trade_portfolio_monthly(trade_portfolio_events: pd.DataFrame) -> pd.DataFrame:
    if trade_portfolio_events.empty:
        return pd.DataFrame()

    frame = trade_portfolio_events.copy()
    entry_timestamps = pd.to_datetime(
        frame.get("entry_timestamp_ms", pd.Series(dtype="float64")),
        unit="ms",
        utc=True,
        errors="coerce",
    )
    frame["month_utc"] = entry_timestamps.dt.tz_localize(None).dt.to_period("M").astype(str)
    frame["exit_return_pct"] = pd.to_numeric(frame.get("exit_return_pct", pd.Series(dtype="float64")), errors="coerce")
    monthly = (
        frame.groupby(["timeframe", "trade_portfolio_id", "trade_portfolio_label", "month_utc"], sort=True)
        .agg(
            trades_count=("exit_return_pct", "size"),
            wins_count=("exit_return_pct", lambda s: int((pd.to_numeric(s, errors="coerce") > 0).sum())),
            mean_return_pct=("exit_return_pct", "mean"),
            total_return_pct=("exit_return_pct", "sum"),
        )
        .reset_index()
    )
    monthly["month_positive"] = pd.to_numeric(monthly["total_return_pct"], errors="coerce") > 0
    return monthly


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
        "candidate_version": 2,
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
    early_entry_summary: pd.DataFrame,
    trade_model_summary: pd.DataFrame,
    trade_portfolio_summary: pd.DataFrame,
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
        "## Early Entry Trail Model",
        "",
        _frame_to_markdown(
            early_entry_summary,
            columns=(
                "timeframe",
                "selected_events_count",
                "entries_triggered_count",
                "entry_rate",
                "median_entry_delay_minutes",
                "median_initial_risk_pct",
                "median_max_return_after_entry_pct",
                "median_exit_return_pct",
                "median_max_r_multiple",
                "median_exit_r_multiple",
                "target_5pct_hit_rate",
                "target_10pct_hit_rate",
            ),
        ),
        "",
        "## Trade Model Comparison",
        "",
        _frame_to_markdown(
            trade_model_summary,
            columns=(
                "timeframe",
                "trade_model_id",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "profit_factor",
                "mfe_ge_5pct_rate",
                "realized_ge_5pct_rate",
                "meets_150_trades_per_year",
            ),
        ),
        "",
        "## Trade Portfolio Comparison",
        "",
        _frame_to_markdown(
            trade_portfolio_summary,
            columns=(
                "timeframe",
                "trade_portfolio_id",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "profit_factor",
                "positive_months_count",
                "non_positive_months_count",
                "all_active_months_positive",
                "annual_sum_return_pct",
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
            f"- early entry events: `{output_dir / 'early_entry_events.csv'}`",
            f"- early entry summary: `{output_dir / 'early_entry_summary.csv'}`",
            f"- trade model events: `{output_dir / 'trade_model_events.csv'}`",
            f"- trade model summary: `{output_dir / 'trade_model_summary.csv'}`",
            f"- trade portfolio events: `{output_dir / 'trade_portfolio_events.csv'}`",
            f"- trade portfolio summary: `{output_dir / 'trade_portfolio_summary.csv'}`",
            f"- trade portfolio monthly: `{output_dir / 'trade_portfolio_monthly.csv'}`",
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
    commission_rate: float = 0.0,
    candidate_cache_dir: Path | None = None,
    reuse_candidate_cache: bool = True,
    logger: logging.Logger | None = None,
) -> dict[str, Path]:
    active_logger = logger or module_logger
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_started_at = time.time()

    preparer = DataPreparer(cache_dir)
    ranker = DailyVolumeRanker(cache_dir)

    grid_frames: list[pd.DataFrame] = []
    profile_frames: list[pd.DataFrame] = []
    selected_event_frames: list[pd.DataFrame] = []
    common_pattern_frames: list[pd.DataFrame] = []
    symbol_summary_frames: list[pd.DataFrame] = []
    early_entry_event_frames: list[pd.DataFrame] = []
    trade_model_event_frames: list[pd.DataFrame] = []
    trade_portfolio_event_frames: list[pd.DataFrame] = []
    resolved_symbols_by_timeframe: dict[str, list[str]] = {}
    timeframe_durations: list[float] = []
    total_timeframes = len(timeframes)
    trade_models = build_hourly_asia_pump_trade_models()
    trade_portfolios = build_hourly_asia_pump_trade_portfolios()

    for timeframe_index, timeframe in enumerate(timeframes, start=1):
        if timeframe not in HOURLY_ASIA_PUMP_SUPPORTED_TIMEFRAMES:
            raise ValueError(f"Unsupported hourly Asia pump timeframe: {timeframe.value}")
        timeframe_started_at = time.time()
        overall_progress_pct, overall_elapsed, overall_eta_seconds = _progress_snapshot(
            completed=timeframe_index - 1,
            total=total_timeframes,
            started_at=run_started_at,
        )
        active_logger.info(
            "hourly-asia-pump: overall progress=%.1f%% timeframe_step=%s/%s timeframe=%s elapsed=%s eta=%s stage=resolve-symbols",
            overall_progress_pct,
            timeframe_index,
            total_timeframes,
            timeframe.value,
            _format_duration(overall_elapsed),
            _format_duration(overall_eta_seconds),
        )

        resolved_symbols = _resolve_symbols_for_timeframe(
            preparer=preparer,
            ranker=ranker,
            timeframe=timeframe,
            symbols=symbols,
            top_n=top_n,
            logger=active_logger,
        )
        resolved_symbols_by_timeframe[timeframe.value] = resolved_symbols
        active_logger.info(
            "hourly-asia-pump: timeframe=%s stage=resolve-symbols symbols=%s timeframe_step=%s/%s",
            timeframe.value,
            len(resolved_symbols),
            timeframe_index,
            total_timeframes,
        )

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
                    "hourly-asia-pump: timeframe=%s stage=candidate-cache cache=reused rows=%s path=%s",
                    timeframe.value,
                    len(candidates),
                    candidates_cache_path,
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
                    "hourly-asia-pump: timeframe=%s stage=candidate-cache cache=saved rows=%s path=%s",
                    timeframe.value,
                    len(candidates),
                    candidates_cache_path,
                )
        active_logger.info(
            "hourly-asia-pump: timeframe=%s stage=grid-eval grid_rows=%s candidate_rows=%s",
            timeframe.value,
            len(grid),
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
        early_entry_events = _build_early_entry_events_for_timeframe(
            preparer=preparer,
            timeframe=timeframe,
            selected_events=selected_events,
            max_follow_minutes=max_follow_minutes,
            logger=active_logger,
        )
        early_entry_event_frames.append(early_entry_events)
        trade_model_events = _build_trade_model_events_for_timeframe(
            preparer=preparer,
            timeframe=timeframe,
            selected_events=selected_events,
            trade_models=trade_models,
            commission_rate=commission_rate,
            logger=active_logger,
        )
        trade_model_event_frames.append(trade_model_events)
        trade_portfolio_events = _build_trade_portfolio_events(trade_model_events, trade_portfolios)
        trade_portfolio_event_frames.append(trade_portfolio_events)

        timeframe_elapsed = max(0.0, time.time() - timeframe_started_at)
        timeframe_durations.append(timeframe_elapsed)
        completed_timeframes = timeframe_index
        overall_progress_pct, overall_elapsed, _ = _progress_snapshot(
            completed=completed_timeframes,
            total=total_timeframes,
            started_at=run_started_at,
        )
        avg_timeframe_seconds = sum(timeframe_durations) / len(timeframe_durations)
        remaining_timeframes = total_timeframes - completed_timeframes
        overall_eta_seconds = avg_timeframe_seconds * remaining_timeframes
        active_logger.info(
            "hourly-asia-pump: overall progress=%.1f%% timeframe_step=%s/%s timeframe=%s candidates=%s selected_events=%s timeframe_elapsed=%s total_elapsed=%s eta=%s",
            overall_progress_pct,
            completed_timeframes,
            total_timeframes,
            timeframe.value,
            len(candidates),
            len(selected_events),
            _format_duration(timeframe_elapsed),
            _format_duration(overall_elapsed),
            _format_duration(overall_eta_seconds),
        )

    grid_summary_all = pd.concat(grid_frames, ignore_index=True) if grid_frames else pd.DataFrame()
    profile_summary_all = pd.concat(profile_frames, ignore_index=True) if profile_frames else pd.DataFrame()
    selected_events_all = pd.concat(selected_event_frames, ignore_index=True) if selected_event_frames else pd.DataFrame()
    common_patterns_all = pd.concat(common_pattern_frames, ignore_index=True) if common_pattern_frames else pd.DataFrame()
    symbol_summary_all = pd.concat(symbol_summary_frames, ignore_index=True) if symbol_summary_frames else pd.DataFrame()
    early_entry_events_all = pd.concat(early_entry_event_frames, ignore_index=True) if early_entry_event_frames else pd.DataFrame()
    early_entry_summary_all = _build_early_entry_summary(early_entry_events_all)
    trade_model_events_all = pd.concat(trade_model_event_frames, ignore_index=True) if trade_model_event_frames else pd.DataFrame()
    trade_model_summary_all = _build_trade_model_summary(trade_model_events_all)
    trade_portfolio_events_all = (
        pd.concat(trade_portfolio_event_frames, ignore_index=True)
        if trade_portfolio_event_frames
        else pd.DataFrame()
    )
    trade_portfolio_summary_all = _build_trade_portfolio_summary(trade_portfolio_events_all)
    trade_portfolio_monthly_all = _build_trade_portfolio_monthly(trade_portfolio_events_all)
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

    early_entry_events_path = output_dir / "early_entry_events.csv"
    early_entry_events_all.to_csv(early_entry_events_path, index=False)

    early_entry_summary_path = output_dir / "early_entry_summary.csv"
    early_entry_summary_all.to_csv(early_entry_summary_path, index=False)

    trade_model_events_path = output_dir / "trade_model_events.csv"
    trade_model_events_all.to_csv(trade_model_events_path, index=False)

    trade_model_summary_path = output_dir / "trade_model_summary.csv"
    trade_model_summary_all.to_csv(trade_model_summary_path, index=False)

    trade_portfolio_events_path = output_dir / "trade_portfolio_events.csv"
    trade_portfolio_events_all.to_csv(trade_portfolio_events_path, index=False)

    trade_portfolio_summary_path = output_dir / "trade_portfolio_summary.csv"
    trade_portfolio_summary_all.to_csv(trade_portfolio_summary_path, index=False)

    trade_portfolio_monthly_path = output_dir / "trade_portfolio_monthly.csv"
    trade_portfolio_monthly_all.to_csv(trade_portfolio_monthly_path, index=False)

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
        "commission_rate": commission_rate,
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
        early_entry_summary=early_entry_summary_all,
        trade_model_summary=trade_model_summary_all,
        trade_portfolio_summary=trade_portfolio_summary_all,
        selection_profile=selection_profile,
    )

    total_elapsed = max(0.0, time.time() - run_started_at)
    active_logger.info(
        "hourly-asia-pump research artifacts saved: output_dir=%s timeframes=%s selected_events=%s elapsed=%s",
        output_dir,
        ",".join(timeframe.value for timeframe in timeframes),
        len(selected_events_all),
        _format_duration(total_elapsed),
    )
    return {
        "grid_summary": grid_summary_path,
        "profile_summary": profile_summary_path,
        "selected_profile_events": selected_events_path,
        "common_patterns": common_patterns_path,
        "selected_profile_symbol_summary": symbol_summary_path,
        "early_entry_events": early_entry_events_path,
        "early_entry_summary": early_entry_summary_path,
        "trade_model_events": trade_model_events_path,
        "trade_model_summary": trade_model_summary_path,
        "trade_portfolio_events": trade_portfolio_events_path,
        "trade_portfolio_summary": trade_portfolio_summary_path,
        "trade_portfolio_monthly": trade_portfolio_monthly_path,
        "top_grid_by_timeframe": top_grid_rows_path,
        "research_context": context_path,
        "report": report_path,
    }
