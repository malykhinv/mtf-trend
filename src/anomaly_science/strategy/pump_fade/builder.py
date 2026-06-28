from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig


PUMP_FADE_LABEL_SCHEMA_VERSION = "pump_fade_close_race_horizon_free_v1"


class PumpFadeBuildError(ValueError):
    """Raised when market data cannot produce a causal pump-fade decision dataset."""


@dataclass(slots=True)
class _EventRecord:
    event_id: str
    ignition_index: int
    end_index: int
    qualification_index: int
    base: float
    peak: float
    peak_index: int
    resolution_index: int | None
    faded: int | None
    size: float


@dataclass(frozen=True, slots=True)
class PumpFadeSymbolBuildResult:
    decisions: pd.DataFrame
    quality: dict[str, object]


class _BarrierIndex:
    """First future close crossing a threshold in O(log n)."""

    def __init__(self, values: np.ndarray) -> None:
        count = len(values)
        size = 1
        while size < count:
            size *= 2
        self.count = count
        self.size = size
        self.minimum = np.full(2 * size, np.inf, dtype=np.float64)
        self.maximum = np.full(2 * size, -np.inf, dtype=np.float64)
        self.minimum[size : size + count] = values
        self.maximum[size : size + count] = values
        for node in range(size - 1, 0, -1):
            self.minimum[node] = min(self.minimum[2 * node], self.minimum[2 * node + 1])
            self.maximum[node] = max(self.maximum[2 * node], self.maximum[2 * node + 1])

    def first_close_at_or_below(self, start: int, stop: int, threshold: float) -> int | None:
        result = self._search(1, 0, self.size, start, stop, threshold, seek_below=True)
        return None if result < 0 or result >= self.count else result

    def first_close_above(self, start: int, stop: int, threshold: float) -> int | None:
        result = self._search(1, 0, self.size, start, stop, threshold, seek_below=False)
        return None if result < 0 or result >= self.count else result

    def _search(
        self,
        node: int,
        left: int,
        right: int,
        query_left: int,
        query_right: int,
        threshold: float,
        *,
        seek_below: bool,
    ) -> int:
        if right <= query_left or query_right <= left:
            return -1
        if seek_below:
            if self.minimum[node] > threshold:
                return -1
        elif self.maximum[node] <= threshold:
            return -1
        if right - left == 1:
            return left
        middle = (left + right) // 2
        first = self._search(
            node * 2,
            left,
            middle,
            query_left,
            query_right,
            threshold,
            seek_below=seek_below,
        )
        if first >= 0:
            return first
        return self._search(
            node * 2 + 1,
            middle,
            right,
            query_left,
            query_right,
            threshold,
            seek_below=seek_below,
        )


def _race(
    barriers: _BarrierIndex,
    *,
    start: int,
    stop: int,
    base: float,
    anchor_high: float,
) -> tuple[int | None, int | None]:
    fade_index = barriers.first_close_at_or_below(start, stop, base)
    invalidation_index = barriers.first_close_above(start, stop, anchor_high)
    if fade_index is None and invalidation_index is None:
        return None, None
    if fade_index is not None and (
        invalidation_index is None or fade_index < invalidation_index
    ):
        return 1, fade_index
    return 0, invalidation_index


def _contiguous_slices(timestamps: np.ndarray, interval_ms: int) -> list[tuple[int, int]]:
    if len(timestamps) == 0:
        return []
    breaks = np.flatnonzero(np.diff(timestamps) != interval_ms) + 1
    boundaries = np.concatenate(([0], breaks, [len(timestamps)]))
    return [(int(boundaries[index]), int(boundaries[index + 1])) for index in range(len(boundaries) - 1)]


def _block_rolling_median(
    values: np.ndarray,
    timestamps: np.ndarray,
    *,
    window: int,
    min_periods: int,
    interval_ms: int,
    exclude_current: bool,
) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    for start, stop in _contiguous_slices(timestamps, interval_ms):
        rolling = pd.Series(values[start:stop]).rolling(window, min_periods=min_periods).median()
        if exclude_current:
            rolling = rolling.shift(1)
        result[start:stop] = rolling.to_numpy()
    return result


def _block_rolling_sum(
    values: np.ndarray,
    timestamps: np.ndarray,
    *,
    window: int,
    min_periods: int,
    interval_ms: int,
) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    for start, stop in _contiguous_slices(timestamps, interval_ms):
        result[start:stop] = (
            pd.Series(values[start:stop]).rolling(window, min_periods=min_periods).sum().to_numpy()
        )
    return result


def _contiguous_block_ends(timestamps: np.ndarray, interval_ms: int) -> np.ndarray:
    ends = np.empty(len(timestamps), dtype=np.int64)
    block_end = len(timestamps) - 1
    for index in range(len(timestamps) - 1, -1, -1):
        if index == len(timestamps) - 1 or timestamps[index + 1] - timestamps[index] != interval_ms:
            block_end = index
        ends[index] = block_end
    return ends


def _last_red_lows(open_: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    red_low = np.where(close < open_, low, np.nan)
    return pd.Series(red_low).ffill().shift(1).to_numpy()


def _session(hour: int) -> str:
    if hour < 7:
        return "asia"
    if hour < 13:
        return "eu"
    if hour < 21:
        return "us"
    return "offhours"


def _window_mean(values: np.ndarray, end: int, width: int) -> float:
    start = max(0, end - width + 1)
    window = values[start : end + 1]
    return float(np.nanmean(window)) if len(window) else 0.0


def _load_symbol(path: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    dataset = ds.dataset(path)
    required = {"timestamp", "open", "high", "low", "close", "quote_volume", "trade_count"}
    missing = required - set(dataset.schema.names)
    if missing:
        raise PumpFadeBuildError(f"{path.name} is missing columns: {sorted(missing)}")
    columns = sorted(required)
    if "taker_buy_quote_volume" in dataset.schema.names:
        columns.append("taker_buy_quote_volume")
    frame = dataset.to_table(columns=columns).to_pandas()
    source_rows = len(frame)
    numeric_required = sorted(required)
    numeric = frame[numeric_required].apply(pd.to_numeric, errors="coerce")
    non_finite = ~np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
    finite = ~non_finite
    invalid_ohlc = finite & (
        (numeric["high"] < numeric[["open", "close"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close"]].min(axis=1))
        | (numeric["low"] > numeric["high"])
    ).to_numpy()
    negative_activity = finite & (
        (numeric["quote_volume"] < 0) | (numeric["trade_count"] < 0)
    ).to_numpy()
    invalid = non_finite | invalid_ohlc | negative_activity
    frame = frame.loc[~invalid].sort_values("timestamp").reset_index(drop=True)
    if frame["timestamp"].duplicated().any():
        raise PumpFadeBuildError(f"{path.name} contains duplicate timestamps")
    return frame, {
        "source_row_count": source_rows,
        "valid_row_count": len(frame),
        "dropped_row_count": int(invalid.sum()),
        "non_finite_row_count": int(non_finite.sum()),
        "invalid_ohlc_row_count": int(invalid_ohlc.sum()),
        "negative_activity_row_count": int(negative_activity.sum()),
    }


def build_pump_fade_symbol_result(
    path: Path,
    *,
    symbol: str | None = None,
    config: PumpFadeDecisionConfig | None = None,
) -> PumpFadeSymbolBuildResult:
    config = config or PumpFadeDecisionConfig()
    symbol = symbol or path.stem
    frame, quality_counts = _load_symbol(path)
    if len(frame) < config.baseline_min_periods + 2:
        return PumpFadeSymbolBuildResult(
            decisions=pd.DataFrame(),
            quality={
                "symbol": symbol,
                **quality_counts,
                "decision_row_count": 0,
                "event_count": 0,
                "status": "INSUFFICIENT_VALID_HISTORY",
                "reason": "valid rows do not satisfy baseline warm-up",
            },
        )
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    if np.any(np.diff(timestamps) <= 0):
        raise PumpFadeBuildError(f"{symbol} timestamps must be strictly increasing")
    if np.any(timestamps % config.candle_interval_ms != 0):
        raise PumpFadeBuildError(f"{symbol} timestamps are not aligned to the candle interval")
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    quote_volume = frame["quote_volume"].to_numpy(dtype=float)
    trades = frame["trade_count"].to_numpy(dtype=float)
    taker_buy_quote = (
        frame["taker_buy_quote_volume"].to_numpy(dtype=float)
        if "taker_buy_quote_volume" in frame
        else np.full(len(frame), np.nan)
    )
    previous_close = np.concatenate(([close[0]], close[:-1]))
    true_range = np.maximum.reduce(
        (high - low, np.abs(high - previous_close), np.abs(low - previous_close))
    )
    baseline_quote = _block_rolling_median(
        quote_volume,
        timestamps,
        window=config.baseline_window_minutes,
        min_periods=config.baseline_min_periods,
        interval_ms=config.candle_interval_ms,
        exclude_current=True,
    )
    baseline_trades = _block_rolling_median(
        trades,
        timestamps,
        window=config.baseline_window_minutes,
        min_periods=config.baseline_min_periods,
        interval_ms=config.candle_interval_ms,
        exclude_current=True,
    )
    baseline_atr = _block_rolling_median(
        true_range,
        timestamps,
        window=config.baseline_window_minutes,
        min_periods=config.baseline_min_periods,
        interval_ms=config.candle_interval_ms,
        exclude_current=True,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        trade_multiple = np.where(baseline_trades > 0.0, trades / baseline_trades, np.nan)
        quote_multiple = np.where(baseline_quote > 0.0, quote_volume / baseline_quote, np.nan)
    activity = np.fmax(trade_multiple, quote_multiple)
    ignition = np.nan_to_num(activity, nan=-np.inf) >= config.activity_ignition_multiple
    quote_60m = _block_rolling_sum(
        quote_volume,
        timestamps,
        window=60,
        min_periods=10,
        interval_ms=config.candle_interval_ms,
    )
    quote_60m_baseline = _block_rolling_median(
        quote_60m,
        timestamps,
        window=config.baseline_window_minutes,
        min_periods=max(1, config.baseline_min_periods - 59),
        interval_ms=config.candle_interval_ms,
        exclude_current=True,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        relative_volume_phase = quote_60m / quote_60m_baseline
    last_red_low = _last_red_lows(open_, low, close)
    block_ends = _contiguous_block_ends(timestamps, config.candle_interval_ms)
    barriers = _BarrierIndex(close)

    event_records: list[_EventRecord] = []
    output_rows: list[dict[str, object]] = []
    consumed_until = -1
    for ignition_index in np.flatnonzero(ignition):
        ignition_index = int(ignition_index)
        if ignition_index <= consumed_until:
            continue
        base = float(last_red_low[ignition_index])
        baseline_atr_value = float(baseline_atr[ignition_index])
        if not math.isfinite(base) or base <= 0.0:
            continue
        if not math.isfinite(baseline_atr_value) or baseline_atr_value <= 0.0:
            continue
        maximum_end = min(
            ignition_index + config.maximum_period_minutes - 1,
            int(block_ends[ignition_index]),
        )
        quiet_count = 0
        peak_activity = 0.0
        end_index = maximum_end
        for index in range(ignition_index, maximum_end + 1):
            current_activity = float(activity[index]) if math.isfinite(activity[index]) else 0.0
            peak_activity = max(peak_activity, current_activity)
            if current_activity < config.quiet_peak_fraction * peak_activity:
                quiet_count += 1
            else:
                quiet_count = 0
            if quiet_count >= config.quiet_confirmation_minutes:
                end_index = index
                break
        consumed_until = end_index
        segment = slice(ignition_index, end_index + 1)
        running_high = np.maximum.accumulate(high[segment])
        cumulative_turnover = np.cumsum(quote_volume[segment])
        activity_segment = activity[segment]
        activity_peak = np.fmax.accumulate(np.nan_to_num(activity_segment, nan=0.0))
        green_rise = np.maximum(close[segment] - open_[segment], 0.0)
        cumulative_green_rise = np.cumsum(green_rise)
        maximum_green_rise = np.maximum.accumulate(green_rise)
        base_broken = np.maximum.accumulate((low[segment] <= base).astype(np.int8))
        typical = (high[segment] + low[segment] + close[segment]) / 3.0
        cumulative_pv = np.cumsum(typical * quote_volume[segment])
        vwap = cumulative_pv / np.maximum(cumulative_turnover, 1e-12)
        upper_wick = (high[segment] - close[segment]) / np.maximum(
            high[segment] - low[segment], 1e-12
        )
        candle_range = (high[segment] - low[segment]) / np.maximum(close[segment], 1e-12)
        taker_ratio = taker_buy_quote[segment] / np.maximum(quote_volume[segment], 1e-12)
        candles_since_red = np.zeros(end_index - ignition_index + 1, dtype=np.int64)
        since_red = 0
        for relative_index in range(len(candles_since_red)):
            absolute_index = ignition_index + relative_index
            since_red = 0 if close[absolute_index] < open_[absolute_index] else since_red + 1
            candles_since_red[relative_index] = since_red

        qualification_relative: int | None = None
        for relative_index in range(len(running_high)):
            running_atr_multiple = (
                float(np.median(true_range[ignition_index : ignition_index + relative_index + 1]))
                / baseline_atr_value
            )
            size_so_far = (running_high[relative_index] - base) / base
            if (
                size_so_far >= config.minimum_pump_size
                and cumulative_turnover[relative_index] >= config.minimum_turnover
                and running_atr_multiple >= config.minimum_atr_multiple
            ):
                qualification_relative = relative_index
                break
        if qualification_relative is None:
            continue
        qualification_index = ignition_index + qualification_relative
        peak_relative = int(np.argmax(high[segment]))
        peak_index = ignition_index + peak_relative
        peak = float(high[peak_index])
        peak_label, peak_resolution = _race(
            barriers,
            start=peak_index + 1,
            stop=int(block_ends[peak_index]) + 1,
            base=base,
            anchor_high=peak,
        )
        event_id = f"{symbol}:{int(timestamps[ignition_index])}"
        ignition_datetime = pd.Timestamp(timestamps[ignition_index], unit="ms", tz="UTC")
        ignition_minute = int(ignition_datetime.minute)
        event = _EventRecord(
            event_id=event_id,
            ignition_index=ignition_index,
            end_index=end_index,
            qualification_index=qualification_index,
            base=base,
            peak=peak,
            peak_index=peak_index,
            resolution_index=peak_resolution,
            faded=peak_label,
            size=(peak - base) / base,
        )
        prior_events = list(event_records)
        event_records.append(event)

        previous_running_high = -np.inf
        new_high_index = 0
        for relative_index, anchor_high in enumerate(running_high):
            absolute_index = ignition_index + relative_index
            is_new_high = bool(high[absolute_index] > previous_running_high)
            previous_running_high = float(anchor_high)
            if not is_new_high:
                continue
            new_high_index += 1
            if absolute_index < qualification_index:
                continue
            snapshot_time = int(timestamps[absolute_index] + config.candle_interval_ms)
            future_start = snapshot_time + config.candle_interval_ms
            label, resolution_index = _race(
                barriers,
                start=absolute_index + 1,
                stop=int(block_ends[absolute_index]) + 1,
                base=base,
                anchor_high=float(anchor_high),
            )
            qualified_prior = [
                prior
                for prior in prior_events
                if timestamps[prior.qualification_index] + config.candle_interval_ms <= snapshot_time
            ]
            prior_24h = [
                prior
                for prior in qualified_prior
                if snapshot_time - (timestamps[prior.ignition_index] + config.candle_interval_ms)
                <= 24 * 60 * 60 * 1000
            ]
            prior_48h = [
                prior
                for prior in qualified_prior
                if snapshot_time - (timestamps[prior.ignition_index] + config.candle_interval_ms)
                <= 48 * 60 * 60 * 1000
            ]
            resolved_prior_48h = [
                prior
                for prior in prior_48h
                if prior.resolution_index is not None
                and timestamps[prior.resolution_index] + config.candle_interval_ms <= snapshot_time
            ]
            last_prior = qualified_prior[-1] if qualified_prior else None
            last_resolved = resolved_prior_48h[-1] if resolved_prior_48h else None
            running_atr_multiple = (
                float(np.median(true_range[ignition_index : absolute_index + 1]))
                / baseline_atr_value
            )
            size_so_far = (float(anchor_high) - base) / base
            current_close = float(close[absolute_index])
            elapsed = relative_index + 1
            early_taker = _window_mean(taker_ratio, min(4, relative_index), 5)
            recent_taker = _window_mean(taker_ratio, relative_index, 5)
            return_last_5 = current_close / close[max(absolute_index - 5, ignition_index)] - 1.0
            previous_5_end = max(absolute_index - 5, ignition_index)
            return_previous_5 = (
                close[previous_5_end] / close[max(absolute_index - 10, ignition_index)] - 1.0
            )
            early_range_end = max(relative_index - 2, 0)
            output_rows.append(
                {
                    "label_schema_version": PUMP_FADE_LABEL_SCHEMA_VERSION,
                    "event_id": event_id,
                    "group": event_id,
                    "symbol": symbol,
                    "decision_index": new_high_index,
                    "ignition_time_ms": int(timestamps[ignition_index] + config.candle_interval_ms),
                    "ignition_hour_utc": int(ignition_datetime.hour),
                    "ignition_minute_of_hour": ignition_minute,
                    "ignition_minutes_from_round_hour": min(ignition_minute, 60 - ignition_minute),
                    "ignition_day_of_week_utc": int(ignition_datetime.dayofweek),
                    "snapshot_time_ms": snapshot_time,
                    "feature_cutoff_time_ms": snapshot_time,
                    "future_start_time_ms": future_start,
                    "resolution_time_ms": (
                        pd.NA
                        if resolution_index is None
                        else int(timestamps[resolution_index] + config.candle_interval_ms)
                    ),
                    "label_available": resolution_index is not None,
                    "y": pd.NA if label is None else int(label),
                    "base_level": base,
                    "anchor_high": float(anchor_high),
                    "current_close": current_close,
                    "remaining_to_base": (current_close - base) / current_close,
                    "close_drawdown_from_high": (float(anchor_high) - current_close) / float(anchor_high),
                    "pump_size": size_so_far,
                    "pump_elapsed_min": elapsed,
                    "pump_ncandles": elapsed,
                    "verticality": size_so_far * 100.0 / max(elapsed, 1),
                    "max_candle_share": (
                        float(maximum_green_rise[relative_index] / cumulative_green_rise[relative_index])
                        if cumulative_green_rise[relative_index] > 0.0
                        else 0.0
                    ),
                    "turnover": float(cumulative_turnover[relative_index]),
                    "atr_mult": running_atr_multiple,
                    "act_now": float(np.nan_to_num(activity_segment[relative_index], nan=0.0)),
                    "act_now_over_peak": float(
                        np.nan_to_num(activity_segment[relative_index], nan=0.0)
                        / max(activity_peak[relative_index], 1e-12)
                    ),
                    "rel_vol_phase": float(
                        relative_volume_phase[absolute_index]
                        if math.isfinite(relative_volume_phase[absolute_index])
                        else 0.0
                    ),
                    "session": _session(int((timestamps[ignition_index] // 3_600_000) % 24)),
                    "min_since_ign": relative_index,
                    "n_prior_24h": len(prior_24h),
                    "n_prior_48h": len(prior_48h),
                    "min_since_last_prior": (
                        1_000_000.0
                        if last_prior is None
                        else (
                            snapshot_time
                            - (timestamps[last_prior.ignition_index] + config.candle_interval_ms)
                        )
                        / config.candle_interval_ms
                    ),
                    "frac_prior_faded_48h": (
                        0.5
                        if not resolved_prior_48h
                        else float(np.mean([prior.faded for prior in resolved_prior_48h]))
                    ),
                    "last_prior_faded": 0.5 if last_resolved is None else float(last_resolved.faded),
                    "last_prior_size": 0.0 if last_prior is None else float(last_prior.size),
                    "cluster_idx_day": sum(
                        1
                        for prior in qualified_prior
                        if timestamps[prior.ignition_index] // 86_400_000
                        == timestamps[ignition_index] // 86_400_000
                    ),
                    "base_broken_before": float(base_broken[relative_index]),
                    "ext_vwap": (current_close - vwap[relative_index]) / current_close,
                    "upper_wick5": _window_mean(upper_wick, relative_index, 5),
                    "taker_decline": early_taker - recent_taker,
                    "price_decel": return_previous_5 - return_last_5,
                    "range_contraction": (
                        _window_mean(candle_range, early_range_end, 3)
                        - _window_mean(candle_range, relative_index, 3)
                    ),
                    "candles_since_red": float(candles_since_red[relative_index]),
                }
            )
    result = pd.DataFrame(output_rows)
    if not result.empty:
        result["y"] = result["y"].astype("Int8")
        result["resolution_time_ms"] = result["resolution_time_ms"].astype("Int64")
    return PumpFadeSymbolBuildResult(
        decisions=result,
        quality={
            "symbol": symbol,
            **quality_counts,
            "decision_row_count": len(result),
            "event_count": int(result["event_id"].nunique()) if not result.empty else 0,
            "status": "OK_WITH_DROPPED_ROWS" if quality_counts["dropped_row_count"] else "OK",
            "reason": (
                "invalid required market rows were removed and became explicit time gaps"
                if quality_counts["dropped_row_count"]
                else "all required market rows passed validation"
            ),
        },
    )


def build_pump_fade_symbol(
    path: Path,
    *,
    symbol: str | None = None,
    config: PumpFadeDecisionConfig | None = None,
) -> pd.DataFrame:
    return build_pump_fade_symbol_result(path, symbol=symbol, config=config).decisions


def build_pump_fade_decisions(
    *,
    cache_dir: Path,
    config: PumpFadeDecisionConfig | None = None,
    limit_symbols: int | None = None,
    progress_callback: Callable[[int, int, str, int], None] | None = None,
) -> pd.DataFrame:
    decisions, _ = build_pump_fade_decisions_with_quality(
        cache_dir=cache_dir,
        config=config,
        limit_symbols=limit_symbols,
        progress_callback=progress_callback,
    )
    return decisions


def build_pump_fade_decisions_with_quality(
    *,
    cache_dir: Path,
    config: PumpFadeDecisionConfig | None = None,
    limit_symbols: int | None = None,
    progress_callback: Callable[[int, int, str, int], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or PumpFadeDecisionConfig()
    paths = sorted(cache_dir.glob("*.parquet"), key=lambda item: item.stem)
    if limit_symbols is not None:
        if limit_symbols <= 0:
            raise PumpFadeBuildError("limit_symbols must be positive")
        paths = paths[:limit_symbols]
    if not paths:
        raise PumpFadeBuildError(f"no parquet symbol caches found in {cache_dir}")
    frames: list[pd.DataFrame] = []
    quality_rows: list[dict[str, object]] = []
    for index, path in enumerate(paths, start=1):
        try:
            symbol_result = build_pump_fade_symbol_result(
                path, symbol=path.stem, config=config
            )
            frame = symbol_result.decisions
            quality_rows.append(symbol_result.quality)
        except PumpFadeBuildError as exc:
            frame = pd.DataFrame()
            quality_rows.append(
                {
                    "symbol": path.stem,
                    "source_row_count": 0,
                    "valid_row_count": 0,
                    "dropped_row_count": 0,
                    "non_finite_row_count": 0,
                    "invalid_ohlc_row_count": 0,
                    "negative_activity_row_count": 0,
                    "decision_row_count": 0,
                    "event_count": 0,
                    "status": "REJECTED",
                    "reason": str(exc),
                }
            )
        if not frame.empty:
            frames.append(frame)
        if progress_callback is not None:
            progress_callback(index, len(paths), path.stem, len(frame))
    decisions = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    quality = pd.DataFrame(quality_rows)
    return decisions, quality
