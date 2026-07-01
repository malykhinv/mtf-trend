from __future__ import annotations

from bisect import insort
import math
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pyarrow.dataset as ds

from anomaly_science.binance_vision_cache import is_delivery_contract_symbol
from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig
from anomaly_science.strategy.pump_fade.cvd import (
    PUMP_FADE_CVD_SCHEMA_VERSION,
    build_pump_fade_cvd_features,
)
from anomaly_science.strategy.pump_fade.event_memory import (
    PUMP_FADE_EVENT_MEMORY_SCHEMA_VERSION,
    PumpEventMemoryRecord,
    build_event_memory_features,
    recurrence_chain_id,
)
from anomaly_science.strategy.pump_fade.path_dynamics import (
    PUMP_FADE_PATH_DYNAMICS_SCHEMA_VERSION,
    build_path_dynamics_features,
)


PUMP_FADE_LABEL_SCHEMA_VERSION = "pump_fade_close_race_horizon_free_v1"
PUMP_FADE_NATURE_LABEL_SCHEMA_VERSION = "pump_fade_event_peak_close_race_v2"
PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION = "pump_fade_online_state_v4"


_SUPERVISED_JOIN_COLUMNS = (
    "event_id",
    "group",
    "symbol",
    "snapshot_time_ms",
    "feature_cutoff_time_ms",
)

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
    memory: PumpEventMemoryRecord


@dataclass(frozen=True, slots=True)
class PumpFadeSymbolBuildResult:
    online_states: pd.DataFrame
    labels: pd.DataFrame
    quality: dict[str, object]

    @property
    def decisions(self) -> pd.DataFrame:
        """Explicit supervised join retained for research consumers.

        ``online_states`` is the deployable causal surface.  Future-derived
        columns exist only in ``labels`` and enter this view through this
        audited one-to-one join.
        """

        return join_pump_fade_states_and_labels(self.online_states, self.labels)


def join_pump_fade_states_and_labels(
    online_states: pd.DataFrame,
    labels: pd.DataFrame,
) -> pd.DataFrame:
    """Create a supervised research view without weakening the online schema."""

    if online_states.empty:
        if labels.empty:
            return pd.DataFrame()
        raise PumpFadeBuildError("offline labels exist without online states")
    missing_state_keys = sorted(set(_SUPERVISED_JOIN_COLUMNS) - set(online_states.columns))
    missing_label_keys = sorted(set(_SUPERVISED_JOIN_COLUMNS) - set(labels.columns))
    if missing_state_keys or missing_label_keys:
        raise PumpFadeBuildError(
            "pump-fade supervised join keys are missing: "
            f"states={missing_state_keys}; labels={missing_label_keys}"
        )
    if online_states[list(_SUPERVISED_JOIN_COLUMNS)].duplicated().any():
        raise PumpFadeBuildError("online states contain duplicate supervised join keys")
    if labels[list(_SUPERVISED_JOIN_COLUMNS)].duplicated().any():
        raise PumpFadeBuildError("offline labels contain duplicate supervised join keys")
    unknown = labels.merge(
        online_states[list(_SUPERVISED_JOIN_COLUMNS)],
        on=list(_SUPERVISED_JOIN_COLUMNS),
        how="left",
        indicator=True,
    )
    if (unknown["_merge"] != "both").any():
        raise PumpFadeBuildError("offline labels contain rows absent from online states")
    joined = online_states.merge(
        labels,
        on=list(_SUPERVISED_JOIN_COLUMNS),
        how="left",
        validate="one_to_one",
        sort=False,
    )
    if len(joined) != len(online_states):
        raise PumpFadeBuildError("supervised join changed the online-state row count")
    return joined


@dataclass(frozen=True, slots=True)
class PumpFadeCacheUniverse:
    perpetual_paths: tuple[Path, ...]
    excluded_delivery_symbols: tuple[str, ...]


def resolve_pump_fade_cache_universe(cache_dir: Path) -> PumpFadeCacheUniverse:
    all_paths = tuple(sorted(cache_dir.glob("*.parquet"), key=lambda item: item.stem))
    return PumpFadeCacheUniverse(
        perpetual_paths=tuple(
            path for path in all_paths if not is_delivery_contract_symbol(path.stem)
        ),
        excluded_delivery_symbols=tuple(
            path.stem for path in all_paths if is_delivery_contract_symbol(path.stem)
        ),
    )


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
        level_start = size // 2
        while level_start:
            child_start = level_start * 2
            child_stop = level_start * 4
            child_minimum = self.minimum[child_start:child_stop]
            child_maximum = self.maximum[child_start:child_stop]
            self.minimum[level_start : level_start * 2] = np.minimum(
                child_minimum[0::2], child_minimum[1::2]
            )
            self.maximum[level_start : level_start * 2] = np.maximum(
                child_maximum[0::2], child_maximum[1::2]
            )
            level_start //= 2

    def first_close_at_or_below(self, start: int, stop: int, threshold: float) -> int | None:
        result = self._search(1, 0, self.size, start, stop, threshold, seek_below=True)
        return None if result < 0 or result >= self.count else result

    def first_close_above(self, start: int, stop: int, threshold: float) -> int | None:
        result = self._search(1, 0, self.size, start, stop, threshold, seek_below=False)
        return None if result < 0 or result >= self.count else result

    def last_value_above(self, start: int, stop: int, threshold: float) -> int | None:
        result = self._search_last_above(1, 0, self.size, start, stop, threshold)
        return None if result < 0 or result >= self.count else result

    def _search_last_above(
        self,
        node: int,
        left: int,
        right: int,
        query_left: int,
        query_right: int,
        threshold: float,
    ) -> int:
        if right <= query_left or query_right <= left or self.maximum[node] <= threshold:
            return -1
        if right - left == 1:
            return left
        middle = (left + right) // 2
        last = self._search_last_above(
            node * 2 + 1, middle, right, query_left, query_right, threshold
        )
        if last >= 0:
            return last
        return self._search_last_above(
            node * 2, left, middle, query_left, query_right, threshold
        )

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
        block = values[start:stop]
        if np.isfinite(block).all():
            rolling_values = (
                pl.Series(block)
                .rolling_median(window_size=window, min_samples=min_periods)
                .to_numpy()
            )
            rolling = pd.Series(rolling_values, copy=False)
        else:
            rolling = pd.Series(block).rolling(window, min_periods=min_periods).median()
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


def _block_ema(values: np.ndarray, timestamps: np.ndarray, *, span: int, interval_ms: int) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    for start, stop in _contiguous_slices(timestamps, interval_ms):
        result[start:stop] = pd.Series(values[start:stop]).ewm(span=span, adjust=False).mean().to_numpy()
    return result


def _contiguous_block_starts(timestamps: np.ndarray, interval_ms: int) -> np.ndarray:
    if len(timestamps) == 0:
        return np.empty(0, dtype=np.int64)
    indices = np.arange(len(timestamps), dtype=np.int64)
    new_block = np.concatenate(
        (np.asarray([True]), np.diff(timestamps) != interval_ms)
    )
    return np.maximum.accumulate(np.where(new_block, indices, 0))


def _contiguous_block_ends(timestamps: np.ndarray, interval_ms: int) -> np.ndarray:
    if len(timestamps) == 0:
        return np.empty(0, dtype=np.int64)
    indices = np.arange(len(timestamps), dtype=np.int64)
    end_block = np.concatenate(
        (np.diff(timestamps) != interval_ms, np.asarray([True]))
    )
    candidates = np.where(end_block, indices, len(timestamps))
    return np.minimum.accumulate(candidates[::-1])[::-1]


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


def _causal_return(
    values: np.ndarray,
    timestamps: np.ndarray,
    end_index: int,
    lag: int,
    interval_ms: int,
) -> float:
    start_index = end_index - lag
    if start_index < 0 or timestamps[end_index] - timestamps[start_index] != lag * interval_ms:
        return 0.0
    start_value = float(values[start_index])
    end_value = float(values[end_index])
    if not math.isfinite(start_value) or not math.isfinite(end_value) or start_value <= 0.0:
        return 0.0
    return end_value / start_value - 1.0


def _causal_fractional_change(
    values: np.ndarray,
    timestamps: np.ndarray,
    end_index: int,
    lag: int,
    interval_ms: int,
) -> float:
    start_index = end_index - lag
    if start_index < 0 or timestamps[end_index] - timestamps[start_index] != lag * interval_ms:
        return float("nan")
    return _fractional_change(values, start_index, end_index)


def _fractional_change(values: np.ndarray, start_index: int, end_index: int) -> float:
    start_value = float(values[start_index])
    end_value = float(values[end_index])
    if not math.isfinite(start_value) or not math.isfinite(end_value) or start_value <= 0.0:
        return float("nan")
    return end_value / start_value - 1.0


def _load_symbol(path: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    dataset = ds.dataset(path)
    required = {"timestamp", "open", "high", "low", "close", "quote_volume", "trade_count"}
    missing = required - set(dataset.schema.names)
    if missing:
        raise PumpFadeBuildError(f"{path.name} is missing columns: {sorted(missing)}")
    columns = sorted(required)
    for optional in ("taker_buy_quote_volume", "open_interest"):
        if optional in dataset.schema.names:
            columns.append(optional)
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


def _finalize_pump_fade_labels(
    online_states: pd.DataFrame,
    *,
    events: list[_EventRecord],
    timestamps: np.ndarray,
    closes: np.ndarray,
    block_ends: np.ndarray,
    candle_interval_ms: int,
) -> pd.DataFrame:
    """Compute future-derived outcomes after online state emission is complete."""

    if online_states.empty:
        return pd.DataFrame()
    event_by_id = {event.event_id: event for event in events}
    if len(event_by_id) != len(events):
        raise PumpFadeBuildError("offline finalizer received duplicate event ids")
    barriers = _BarrierIndex(closes)
    rows: list[dict[str, object]] = []
    for state in online_states.to_dict(orient="records"):
        event_id = str(state["event_id"])
        event = event_by_id.get(event_id)
        if event is None:
            raise PumpFadeBuildError(
                f"offline finalizer has no lifecycle record for {event_id}"
            )
        snapshot_time_ms = int(state["snapshot_time_ms"])
        observed_open_time_ms = snapshot_time_ms - candle_interval_ms
        absolute_index = int(np.searchsorted(timestamps, observed_open_time_ms))
        if (
            absolute_index >= len(timestamps)
            or int(timestamps[absolute_index]) != observed_open_time_ms
        ):
            raise PumpFadeBuildError(
                f"offline finalizer cannot locate snapshot candle for {event_id}"
            )
        label, resolution_index = _race(
            barriers,
            start=absolute_index + 1,
            stop=int(block_ends[absolute_index]) + 1,
            base=float(state["base_level"]),
            anchor_high=float(state["anchor_high"]),
        )
        peak_label, peak_resolution_index = _race(
            barriers,
            start=event.peak_index + 1,
            stop=int(block_ends[event.peak_index]) + 1,
            base=event.base,
            anchor_high=event.peak,
        )
        nature_eligible = event.peak_index >= absolute_index
        rows.append(
            {
                **{column: state[column] for column in _SUPERVISED_JOIN_COLUMNS},
                "label_schema_version": PUMP_FADE_LABEL_SCHEMA_VERSION,
                "nature_label_schema_version": PUMP_FADE_NATURE_LABEL_SCHEMA_VERSION,
                "future_start_time_ms": snapshot_time_ms + candle_interval_ms,
                "nature_future_start_time_ms": (
                    pd.NA
                    if not nature_eligible
                    else int(timestamps[event.peak_index] + 2 * candle_interval_ms)
                ),
                "nature_resolution_time_ms": (
                    pd.NA
                    if not nature_eligible or peak_resolution_index is None
                    else int(
                        timestamps[peak_resolution_index] + candle_interval_ms
                    )
                ),
                "nature_label_available": (
                    nature_eligible and peak_resolution_index is not None
                ),
                "nature_y": (
                    pd.NA
                    if not nature_eligible or peak_resolution_index is None
                    else int(peak_label)
                ),
                "event_peak_time_ms": int(
                    timestamps[event.peak_index] + candle_interval_ms
                ),
                "event_end_time_ms": int(
                    timestamps[event.end_index] + candle_interval_ms
                ),
                "is_event_peak_decision": absolute_index == event.peak_index,
                "resolution_time_ms": (
                    pd.NA
                    if resolution_index is None
                    else int(timestamps[resolution_index] + candle_interval_ms)
                ),
                "label_available": resolution_index is not None,
                "y": pd.NA if label is None else int(label),
            }
        )
    labels = pd.DataFrame(rows)
    labels["y"] = labels["y"].astype("Int8")
    labels["resolution_time_ms"] = labels["resolution_time_ms"].astype("Int64")
    labels["nature_y"] = labels["nature_y"].astype("Int8")
    labels["nature_future_start_time_ms"] = labels[
        "nature_future_start_time_ms"
    ].astype("Int64")
    labels["nature_resolution_time_ms"] = labels[
        "nature_resolution_time_ms"
    ].astype("Int64")
    return labels


def _build_pump_fade_symbol_result(
    path: Path,
    *,
    symbol: str | None = None,
    config: PumpFadeDecisionConfig | None = None,
    finalize_labels: bool,
) -> PumpFadeSymbolBuildResult:
    config = config or PumpFadeDecisionConfig()
    symbol = symbol or path.stem
    frame, quality_counts = _load_symbol(path)
    if len(frame) < config.baseline_min_periods + 2:
        return PumpFadeSymbolBuildResult(
            online_states=pd.DataFrame(),
            labels=pd.DataFrame(),
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
    open_interest = (
        frame["open_interest"].to_numpy(dtype=float)
        if "open_interest" in frame
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
    average_trade_notional = quote_volume / np.maximum(trades, 1.0)
    baseline_average_trade_notional = _block_rolling_median(
        average_trade_notional,
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
    quote_24h = _block_rolling_sum(
        quote_volume,
        timestamps,
        window=1440,
        min_periods=1440,
        interval_ms=config.candle_interval_ms,
    )
    trades_60m = _block_rolling_sum(
        trades, timestamps, window=60, min_periods=60, interval_ms=config.candle_interval_ms
    )
    trades_240m = _block_rolling_sum(
        trades, timestamps, window=240, min_periods=240, interval_ms=config.candle_interval_ms
    )
    quote_240m = _block_rolling_sum(
        quote_volume, timestamps, window=240, min_periods=240, interval_ms=config.candle_interval_ms
    )
    true_range_60m = _block_rolling_sum(
        true_range, timestamps, window=60, min_periods=60, interval_ms=config.candle_interval_ms
    )
    true_range_240m = _block_rolling_sum(
        true_range, timestamps, window=240, min_periods=240, interval_ms=config.candle_interval_ms
    )
    ema_60 = _block_ema(close, timestamps, span=60, interval_ms=config.candle_interval_ms)
    ema_240 = _block_ema(close, timestamps, span=240, interval_ms=config.candle_interval_ms)
    ema_1440 = _block_ema(close, timestamps, span=1440, interval_ms=config.candle_interval_ms)
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
    block_starts = _contiguous_block_starts(timestamps, config.candle_interval_ms)
    barriers = _BarrierIndex(close)
    high_index = _BarrierIndex(high)

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
        lower_wick = (np.minimum(open_[segment], close[segment]) - low[segment]) / np.maximum(
            high[segment] - low[segment], 1e-12
        )
        body_fraction = np.abs(close[segment] - open_[segment]) / np.maximum(
            high[segment] - low[segment], 1e-12
        )
        close_location = (close[segment] - low[segment]) / np.maximum(
            high[segment] - low[segment], 1e-12
        )
        one_minute_high_return = high[segment] / np.maximum(open_[segment], 1e-12) - 1.0
        one_minute_close_return = close[segment] / np.maximum(open_[segment], 1e-12) - 1.0
        path_length = np.cumsum(np.abs(np.diff(np.concatenate(([base], close[segment])))))
        candle_range = (high[segment] - low[segment]) / np.maximum(close[segment], 1e-12)
        taker_ratio = taker_buy_quote[segment] / np.maximum(quote_volume[segment], 1e-12)
        candles_since_red = np.zeros(end_index - ignition_index + 1, dtype=np.int64)
        since_red = 0
        for relative_index in range(len(candles_since_red)):
            absolute_index = ignition_index + relative_index
            since_red = 0 if close[absolute_index] < open_[absolute_index] else since_red + 1
            candles_since_red[relative_index] = since_red

        qualification_relative: int | None = None
        sorted_true_ranges: list[float] = []
        for relative_index in range(len(running_high)):
            insort(sorted_true_ranges, float(true_range[ignition_index + relative_index]))
            middle = len(sorted_true_ranges) // 2
            if len(sorted_true_ranges) % 2:
                running_median = sorted_true_ranges[middle]
            else:
                running_median = (
                    sorted_true_ranges[middle - 1] + sorted_true_ranges[middle]
                ) / 2.0
            running_atr_multiple = running_median / baseline_atr_value
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
        event_id = f"{symbol}:{int(timestamps[ignition_index])}"
        ignition_datetime = pd.Timestamp(timestamps[ignition_index], unit="ms", tz="UTC")
        ignition_minute = int(ignition_datetime.minute)
        prior_events = list(event_records)
        prior_memory = tuple(record.memory for record in prior_events)
        ignition_time_ms = int(timestamps[ignition_index] + config.candle_interval_ms)
        chain_id = recurrence_chain_id(
            symbol=symbol,
            ignition_time_ms=ignition_time_ms,
            prior_records=prior_memory,
        )

        previous_running_high = -np.inf
        new_high_index = 0
        nature_anchor_index: int | None = None
        previous_new_high_relative: int | None = None
        previous_new_high_value: float | None = None
        pullbacks_between_highs: list[float] = []
        minutes_between_highs: list[int] = []
        extensions_between_highs: list[float] = []
        for relative_index, anchor_high in enumerate(running_high):
            absolute_index = ignition_index + relative_index
            is_new_high = bool(high[absolute_index] > previous_running_high)
            previous_running_high = float(anchor_high)
            if not is_new_high:
                continue
            if previous_new_high_relative is not None and previous_new_high_value is not None:
                between_start = previous_new_high_relative + 1
                between_stop = relative_index
                valley = (
                    float(np.min(low[ignition_index + between_start : ignition_index + between_stop]))
                    if between_stop > between_start
                    else previous_new_high_value
                )
                pullbacks_between_highs.append(
                    max(previous_new_high_value - valley, 0.0) / max(previous_new_high_value, 1e-12)
                )
                minutes_between_highs.append(relative_index - previous_new_high_relative)
                extensions_between_highs.append(
                    max(float(anchor_high) - previous_new_high_value, 0.0)
                    / max(previous_new_high_value, 1e-12)
                )
            previous_new_high_relative = relative_index
            previous_new_high_value = float(anchor_high)
            new_high_index += 1
            if absolute_index < qualification_index:
                continue
            is_nature_anchor = nature_anchor_index is None
            if is_nature_anchor:
                nature_anchor_index = absolute_index
            snapshot_time = int(timestamps[absolute_index] + config.candle_interval_ms)
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
            event_quote = quote_volume[ignition_index : absolute_index + 1]
            event_trades = trades[ignition_index : absolute_index + 1]
            event_taker = taker_buy_quote[ignition_index : absolute_index + 1]
            pre_path_start = max(
                int(block_starts[ignition_index]), ignition_index - 240
            )
            path_dynamics = build_path_dynamics_features(
                event_open=open_[ignition_index : absolute_index + 1],
                event_high=high[ignition_index : absolute_index + 1],
                event_low=low[ignition_index : absolute_index + 1],
                event_close=close[ignition_index : absolute_index + 1],
                event_quote_volume=event_quote,
                event_trade_count=event_trades,
                event_taker_buy_quote=event_taker,
                pre_open=open_[pre_path_start:ignition_index],
                pre_high=high[pre_path_start:ignition_index],
                pre_low=low[pre_path_start:ignition_index],
                pre_close=close[pre_path_start:ignition_index],
            )
            cvd_features = build_pump_fade_cvd_features(
                quote_volume=event_quote,
                taker_buy_quote_volume=event_taker,
                close=close[ignition_index : absolute_index + 1],
            )
            event_activity = np.nan_to_num(activity[ignition_index : absolute_index + 1], nan=0.0)
            event_turnover = float(np.sum(event_quote))
            event_trade_count = float(np.sum(event_trades))
            event_avg_trade_notional = event_turnover / max(event_trade_count, 1.0)
            baseline_trade_notional = baseline_average_trade_notional[ignition_index]
            has_taker_buy_quote_data = bool(np.isfinite(event_taker).any())
            taker_buy_share_event = (
                float(np.nansum(event_taker) / max(event_turnover, 1e-12))
                if has_taker_buy_quote_data
                else float("nan")
            )
            q_mult = float(quote_multiple[absolute_index]) if math.isfinite(quote_multiple[absolute_index]) else float("nan")
            t_mult = float(trade_multiple[absolute_index]) if math.isfinite(trade_multiple[absolute_index]) else float("nan")
            pre_ignition_index = max(ignition_index - 1, int(block_starts[ignition_index]))
            return_15m = _causal_return(close, timestamps, pre_ignition_index, 15, config.candle_interval_ms)
            return_60m = _causal_return(close, timestamps, pre_ignition_index, 60, config.candle_interval_ms)
            return_240m = _causal_return(close, timestamps, pre_ignition_index, 240, config.candle_interval_ms)
            return_1440m = _causal_return(close, timestamps, pre_ignition_index, 1440, config.candle_interval_ms)
            oi_change_5m = _causal_fractional_change(open_interest, timestamps, absolute_index, 5, config.candle_interval_ms)
            oi_change_15m = _causal_fractional_change(open_interest, timestamps, absolute_index, 15, config.candle_interval_ms)
            oi_change_60m = _causal_fractional_change(open_interest, timestamps, absolute_index, 60, config.candle_interval_ms)
            oi_change_240m = _causal_fractional_change(open_interest, timestamps, absolute_index, 240, config.candle_interval_ms)
            oi_change_since_ignition = _fractional_change(open_interest, ignition_index, absolute_index)
            prior_higher_index = high_index.last_value_above(
                max(int(block_starts[ignition_index]), ignition_index - 14_400),
                ignition_index,
                float(anchor_high),
            )
            pre_high_60 = float(np.max(high[max(int(block_starts[ignition_index]), ignition_index - 60) : ignition_index])) if ignition_index > int(block_starts[ignition_index]) else close[ignition_index]
            pre_high_240 = float(np.max(high[max(int(block_starts[ignition_index]), ignition_index - 240) : ignition_index])) if ignition_index > int(block_starts[ignition_index]) else close[ignition_index]
            event_path_efficiency = max(current_close - base, 0.0) / max(float(path_length[relative_index]), 1e-12)
            event_activity_mean = float(np.mean(event_activity))
            event_activity_cv = float(np.std(event_activity) / max(event_activity_mean, 1e-12))
            price_change_60 = _causal_return(close, timestamps, absolute_index, 60, config.candle_interval_ms)
            oi_available = math.isfinite(oi_change_60m)
            quote_volume_24h_value = float(quote_24h[absolute_index]) if math.isfinite(quote_24h[absolute_index]) else float("nan")
            has_quote_volume_24h = math.isfinite(quote_volume_24h_value)
            has_average_trade_notional_baseline = math.isfinite(baseline_trade_notional) and baseline_trade_notional > 0.0
            average_trade_notional_vs_24h = (
                event_avg_trade_notional / baseline_trade_notional
                if has_average_trade_notional_baseline
                else float("nan")
            )
            act_now_value = float(activity_segment[relative_index]) if math.isfinite(activity_segment[relative_index]) else float("nan")
            act_now_over_peak_value = (
                act_now_value / activity_peak[relative_index]
                if math.isfinite(act_now_value) and activity_peak[relative_index] > 0.0
                else float("nan")
            )
            rel_vol_phase_value = (
                float(relative_volume_phase[absolute_index])
                if math.isfinite(relative_volume_phase[absolute_index])
                else float("nan")
            )
            trade_activity_1h_vs_4h = (
                float(trades_60m[absolute_index] / max(trades_240m[absolute_index] / 4.0, 1e-12))
                if math.isfinite(trades_60m[absolute_index]) and math.isfinite(trades_240m[absolute_index])
                else float("nan")
            )
            quote_activity_1h_vs_4h = (
                float(quote_60m[absolute_index] / max(quote_240m[absolute_index] / 4.0, 1e-12))
                if math.isfinite(quote_60m[absolute_index]) and math.isfinite(quote_240m[absolute_index])
                else float("nan")
            )
            atr_activity_1h_vs_4h = (
                float(true_range_60m[absolute_index] / max(true_range_240m[absolute_index] / 4.0, 1e-12))
                if math.isfinite(true_range_60m[absolute_index]) and math.isfinite(true_range_240m[absolute_index])
                else float("nan")
            )
            has_prior_event = last_prior is not None
            has_resolved_prior_48h = bool(resolved_prior_48h)
            has_last_resolved_prior = last_resolved is not None
            has_prior_higher_price = prior_higher_index is not None
            event_memory = build_event_memory_features(
                prior_memory,
                snapshot_time_ms=snapshot_time,
                current_base=base,
                current_anchor_high=float(anchor_high),
                current_close=current_close,
            )
            output_rows.append(
                {
                    "event_id": event_id,
                    "group": event_id,
                    "recurrence_chain_id": chain_id,
                    "event_memory_schema_version": PUMP_FADE_EVENT_MEMORY_SCHEMA_VERSION,
                    "cvd_schema_version": PUMP_FADE_CVD_SCHEMA_VERSION,
                    "path_dynamics_schema_version": PUMP_FADE_PATH_DYNAMICS_SCHEMA_VERSION,
                    "symbol": symbol,
                    "decision_index": new_high_index,
                    "ignition_time_ms": ignition_time_ms,
                    "ignition_hour_utc": int(ignition_datetime.hour),
                    "ignition_minute_of_hour": ignition_minute,
                    "ignition_minutes_from_round_hour": min(ignition_minute, 60 - ignition_minute),
                    "ignition_day_of_week_utc": int(ignition_datetime.dayofweek),
                    "snapshot_time_ms": snapshot_time,
                    "feature_cutoff_time_ms": snapshot_time,
                    "is_nature_anchor": is_nature_anchor,
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
                    "max_1m_high_return": float(np.max(one_minute_high_return[: relative_index + 1])),
                    "max_1m_close_return": float(np.max(one_minute_close_return[: relative_index + 1])),
                    "event_path_efficiency": event_path_efficiency,
                    "mean_pullback_between_highs": float(np.mean(pullbacks_between_highs)) if pullbacks_between_highs else 0.0,
                    "max_pullback_between_highs": float(np.max(pullbacks_between_highs)) if pullbacks_between_highs else 0.0,
                    "mean_minutes_between_highs": float(np.mean(minutes_between_highs)) if minutes_between_highs else 0.0,
                    "rehigh_count": len(pullbacks_between_highs),
                    "latest_high_extension": (
                        extensions_between_highs[-1]
                        if extensions_between_highs
                        else size_so_far
                    ),
                    "high_extension_decay_ratio": (
                        extensions_between_highs[-1]
                        / max(float(np.mean(extensions_between_highs[:-1])), 1e-12)
                        if len(extensions_between_highs) > 1
                        else 1.0
                    ),
                    "high_interval_change_ratio": (
                        minutes_between_highs[-1]
                        / max(float(np.mean(minutes_between_highs[:-1])), 1e-12)
                        if len(minutes_between_highs) > 1
                        else 1.0
                    ),
                    "current_upper_wick_fraction": float(upper_wick[relative_index]),
                    "current_lower_wick_fraction": float(lower_wick[relative_index]),
                    "current_body_fraction": float(body_fraction[relative_index]),
                    "current_close_location": float(close_location[relative_index]),
                    "ignition_upper_wick_fraction": float(upper_wick[0]),
                    "mean_upper_wick_fraction": float(np.mean(upper_wick[: relative_index + 1])),
                    "max_upper_wick_fraction": float(np.max(upper_wick[: relative_index + 1])),
                    "green_candle_fraction": float(np.mean(close[ignition_index : absolute_index + 1] >= open_[ignition_index : absolute_index + 1])),
                    "turnover_top_candle_share": float(np.max(event_quote) / max(event_turnover, 1e-12)),
                    "trade_count_top_candle_share": float(np.max(event_trades) / max(event_trade_count, 1e-12)),
                    "turnover": float(cumulative_turnover[relative_index]),
                    "quote_volume_24h": quote_volume_24h_value,
                    "has_quote_volume_24h": has_quote_volume_24h,
                    "event_trade_count": event_trade_count,
                    "event_average_trade_notional": event_avg_trade_notional,
                    "average_trade_notional_vs_24h": average_trade_notional_vs_24h,
                    "has_average_trade_notional_baseline": has_average_trade_notional_baseline,
                    "taker_buy_share_event": taker_buy_share_event,
                    "has_taker_buy_quote_data": has_taker_buy_quote_data,
                    "taker_imbalance_event": (2.0 * taker_buy_share_event - 1.0 if has_taker_buy_quote_data else float("nan")),
                    **cvd_features,
                    "retail_frenzy_proxy": (t_mult / max(q_mult, 1e-12) if math.isfinite(t_mult) and math.isfinite(q_mult) and q_mult > 0.0 else float("nan")),
                    "large_print_proxy": (q_mult / max(t_mult, 1e-12) if math.isfinite(t_mult) and math.isfinite(q_mult) and t_mult > 0.0 else float("nan")),
                    "algorithmic_persistence_proxy": 1.0 / (1.0 + event_activity_cv),
                    "activity_above_10x_fraction": float(np.mean(event_activity >= 10.0)),
                    "atr_mult": running_atr_multiple,
                    "act_now": act_now_value,
                    "act_now_over_peak": act_now_over_peak_value,
                    "rel_vol_phase": rel_vol_phase_value,
                    "has_rel_vol_phase": math.isfinite(rel_vol_phase_value),
                    "session": _session(int((timestamps[ignition_index] // 3_600_000) % 24)),
                    "min_since_ign": relative_index,
                    "n_prior_24h": len(prior_24h),
                    "n_prior_48h": len(prior_48h),
                    "has_prior_event": has_prior_event,
                    "min_since_last_prior": (
                        float("nan")
                        if last_prior is None
                        else (
                            snapshot_time
                            - (timestamps[last_prior.ignition_index] + config.candle_interval_ms)
                        )
                        / config.candle_interval_ms
                    ),
                    "has_resolved_prior_48h": has_resolved_prior_48h,
                    "frac_prior_faded_48h": (
                        float("nan")
                        if not resolved_prior_48h
                        else float(np.mean([prior.faded for prior in resolved_prior_48h]))
                    ),
                    "has_last_resolved_prior": has_last_resolved_prior,
                    "last_prior_faded": float("nan") if last_resolved is None else float(last_resolved.faded),
                    # The final peak of an unresolved prior event is offline knowledge.
                    # Keep this legacy coordinate causal by exposing qualification size.
                    "last_prior_size": (
                        float("nan")
                        if last_prior is None
                        else float(last_prior.memory.qualification_size)
                    ),
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
                    **path_dynamics,
                    **event_memory,
                    "pre_return_15m": return_15m,
                    "pre_return_60m": return_60m,
                    "pre_return_240m": return_240m,
                    "pre_return_1440m": return_1440m,
                    "pre_dump_depth_60m": close[pre_ignition_index] / max(pre_high_60, 1e-12) - 1.0,
                    "pre_dump_depth_240m": close[pre_ignition_index] / max(pre_high_240, 1e-12) - 1.0,
                    "price_vs_ema_60": current_close / max(ema_60[absolute_index], 1e-12) - 1.0,
                    "price_vs_ema_240": current_close / max(ema_240[absolute_index], 1e-12) - 1.0,
                    "price_vs_ema_1440": current_close / max(ema_1440[absolute_index], 1e-12) - 1.0,
                    "ema_240_slope_60m": _causal_fractional_change(ema_240, timestamps, absolute_index, 60, config.candle_interval_ms),
                    "trade_activity_1h_vs_4h": trade_activity_1h_vs_4h,
                    "has_trade_activity_1h_vs_4h": math.isfinite(trade_activity_1h_vs_4h),
                    "quote_activity_1h_vs_4h": quote_activity_1h_vs_4h,
                    "has_quote_activity_1h_vs_4h": math.isfinite(quote_activity_1h_vs_4h),
                    "atr_activity_1h_vs_4h": atr_activity_1h_vs_4h,
                    "has_atr_activity_1h_vs_4h": math.isfinite(atr_activity_1h_vs_4h),
                    "has_prior_higher_price": has_prior_higher_price,
                    "minutes_since_prior_higher_price": float("nan") if prior_higher_index is None else float(ignition_index - prior_higher_index),
                    "oi_available": oi_available,
                    "oi_change_5m": oi_change_5m,
                    "oi_change_15m": oi_change_15m,
                    "oi_change_60m": oi_change_60m,
                    "oi_change_240m": oi_change_240m,
                    "oi_change_since_ignition": oi_change_since_ignition,
                    "price_up_oi_up_60m": (
                        float(price_change_60 > 0.0 and oi_change_60m > 0.0)
                        if oi_available
                        else float("nan")
                    ),
                    "price_up_oi_down_60m": (
                        float(price_change_60 > 0.0 and oi_change_60m < 0.0)
                        if oi_available
                        else float("nan")
                    ),
                    "price_down_oi_up_60m": (
                        float(price_change_60 < 0.0 and oi_change_60m > 0.0)
                        if oi_available
                        else float("nan")
                    ),
                    "price_down_oi_down_60m": (
                        float(price_change_60 < 0.0 and oi_change_60m < 0.0)
                        if oi_available
                        else float("nan")
                    ),
                }
            )
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
        qualification_high = float(running_high[qualification_relative])
        qualification_size = (qualification_high - base) / base
        if peak_resolution is None:
            memory = PumpEventMemoryRecord(
                event_id=event_id,
                chain_id=chain_id,
                ignition_time_ms=ignition_time_ms,
                qualification_time_ms=int(
                    timestamps[qualification_index] + config.candle_interval_ms
                ),
                base_level=base,
                qualification_high=qualification_high,
                qualification_size=qualification_size,
                resolution_time_ms=None,
                faded=None,
                peak_level=None,
                peak_time_ms=None,
                resolution_close=None,
                turnover_to_resolution=None,
                trade_count_to_resolution=None,
                average_trade_notional_to_resolution=None,
                taker_buy_share_to_resolution=None,
                path_efficiency_to_resolution=None,
                max_1m_high_return_to_peak=None,
                max_1m_close_return_to_peak=None,
                mean_upper_wick_fraction_to_peak=None,
                max_upper_wick_fraction_to_peak=None,
            )
        else:
            summary_slice = slice(ignition_index, peak_resolution + 1)
            summary_quote = float(np.sum(quote_volume[summary_slice]))
            summary_trades = float(np.sum(trades[summary_slice]))
            summary_taker = taker_buy_quote[summary_slice]
            taker_available = bool(
                np.isfinite(summary_taker).all()
                and np.isfinite(quote_volume[summary_slice]).all()
                and summary_quote > 0.0
            )
            summary_closes = close[summary_slice]
            path_denominator = float(np.sum(np.abs(np.diff(summary_closes))))
            peak_relative_in_event = peak_index - ignition_index
            memory = PumpEventMemoryRecord(
                event_id=event_id,
                chain_id=chain_id,
                ignition_time_ms=ignition_time_ms,
                qualification_time_ms=int(
                    timestamps[qualification_index] + config.candle_interval_ms
                ),
                base_level=base,
                qualification_high=qualification_high,
                qualification_size=qualification_size,
                resolution_time_ms=int(
                    timestamps[peak_resolution] + config.candle_interval_ms
                ),
                faded=peak_label,
                peak_level=peak,
                peak_time_ms=int(timestamps[peak_index] + config.candle_interval_ms),
                resolution_close=float(close[peak_resolution]),
                turnover_to_resolution=summary_quote,
                trade_count_to_resolution=summary_trades,
                average_trade_notional_to_resolution=(
                    summary_quote / summary_trades if summary_trades > 0.0 else 0.0
                ),
                taker_buy_share_to_resolution=(
                    float(np.sum(summary_taker) / summary_quote)
                    if taker_available
                    else float("nan")
                ),
                path_efficiency_to_resolution=(
                    abs(float(summary_closes[-1] - summary_closes[0])) / path_denominator
                    if path_denominator > 0.0
                    else 0.0
                ),
                max_1m_high_return_to_peak=float(
                    np.max(one_minute_high_return[: peak_relative_in_event + 1])
                ),
                max_1m_close_return_to_peak=float(
                    np.max(one_minute_close_return[: peak_relative_in_event + 1])
                ),
                mean_upper_wick_fraction_to_peak=float(
                    np.mean(upper_wick[: peak_relative_in_event + 1])
                ),
                max_upper_wick_fraction_to_peak=float(
                    np.max(upper_wick[: peak_relative_in_event + 1])
                ),
            )
        event_records.append(
            _EventRecord(
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
                memory=memory,
            )
        )
    online_states = pd.DataFrame(output_rows)
    if not online_states.empty:
        online_states.insert(
            0,
            "online_state_schema_version",
            PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION,
        )
        labels = (
            _finalize_pump_fade_labels(
                online_states,
                events=event_records,
                timestamps=timestamps,
                closes=close,
                block_ends=block_ends,
                candle_interval_ms=config.candle_interval_ms,
            )
            if finalize_labels
            else pd.DataFrame()
        )
    else:
        labels = pd.DataFrame()
    return PumpFadeSymbolBuildResult(
        online_states=online_states,
        labels=labels,
        quality={
            "symbol": symbol,
            **quality_counts,
            "decision_row_count": len(online_states),
            "event_count": int(online_states["event_id"].nunique()) if not online_states.empty else 0,
            "oi_covered_decision_row_count": int(online_states["oi_available"].sum()) if not online_states.empty else 0,
            "oi_covered_decision_row_fraction": float(online_states["oi_available"].mean()) if not online_states.empty else 0.0,
            "status": "OK_WITH_DROPPED_ROWS" if quality_counts["dropped_row_count"] else "OK",
            "reason": (
                "invalid required market rows were removed and became explicit time gaps"
                if quality_counts["dropped_row_count"]
                else "all required market rows passed validation"
            ),
        },
    )


def build_pump_fade_symbol_result(
    path: Path,
    *,
    symbol: str | None = None,
    config: PumpFadeDecisionConfig | None = None,
) -> PumpFadeSymbolBuildResult:
    """Build separated online states and offline labels for one symbol."""

    return _build_pump_fade_symbol_result(
        path,
        symbol=symbol,
        config=config,
        finalize_labels=True,
    )


def build_pump_fade_symbol(
    path: Path,
    *,
    symbol: str | None = None,
    config: PumpFadeDecisionConfig | None = None,
) -> pd.DataFrame:
    return build_pump_fade_symbol_result(path, symbol=symbol, config=config).decisions


def build_pump_fade_online_symbol(
    path: Path,
    *,
    symbol: str | None = None,
    config: PumpFadeDecisionConfig | None = None,
) -> pd.DataFrame:
    """Build only point-in-time state rows; no future-derived columns."""

    return _build_pump_fade_symbol_result(
        path,
        symbol=symbol,
        config=config,
        finalize_labels=False,
    ).online_states


def build_pump_fade_decisions(
    *,
    cache_dir: Path,
    config: PumpFadeDecisionConfig | None = None,
    limit_symbols: int | None = None,
    workers: int = 1,
    max_inflight_symbols: int | None = None,
    progress_callback: Callable[[int, int, str, int], None] | None = None,
) -> pd.DataFrame:
    decisions, _ = build_pump_fade_decisions_with_quality(
        cache_dir=cache_dir,
        config=config,
        limit_symbols=limit_symbols,
        workers=workers,
        max_inflight_symbols=max_inflight_symbols,
        progress_callback=progress_callback,
    )
    return decisions


def _resolve_max_inflight_symbols(*, workers: int, max_inflight_symbols: int | None) -> int:
    if max_inflight_symbols is None:
        return max(1, workers * 2)
    if max_inflight_symbols <= 0:
        raise PumpFadeBuildError("max_inflight_symbols must be positive")
    if max_inflight_symbols < workers:
        raise PumpFadeBuildError("max_inflight_symbols must be greater than or equal to workers")
    return max_inflight_symbols


def _iter_bounded_symbol_builds(
    *,
    paths: list[Path],
    config: PumpFadeDecisionConfig,
    workers: int,
    max_inflight_symbols: int,
) -> Iterator[tuple[Path, PumpFadeSymbolBuildResult | str]]:
    if workers == 1:
        for path in paths:
            yield path, _build_symbol_or_error(path, config)
        return

    executor = ProcessPoolExecutor(max_workers=workers)
    pending: dict[Future[PumpFadeSymbolBuildResult | str], tuple[int, Path]] = {}
    completed: dict[int, tuple[Path, PumpFadeSymbolBuildResult | str]] = {}
    next_submit = 0
    next_emit = 0

    def submit_until_capacity() -> None:
        nonlocal next_submit
        while (
            next_submit < len(paths)
            and len(pending) + len(completed) < max_inflight_symbols
        ):
            path = paths[next_submit]
            future = executor.submit(_build_symbol_or_error, path, config)
            pending[future] = (next_submit, path)
            next_submit += 1

    try:
        submit_until_capacity()
        while next_emit < len(paths):
            while next_emit not in completed:
                if not pending:
                    raise PumpFadeBuildError("internal error: bounded pump-fade worker pool stalled")
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index, path = pending.pop(future)
                    completed[index] = (path, future.result())
                submit_until_capacity()
            yield completed.pop(next_emit)
            next_emit += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def build_pump_fade_datasets_with_quality(
    *,
    cache_dir: Path,
    config: PumpFadeDecisionConfig | None = None,
    limit_symbols: int | None = None,
    workers: int = 1,
    max_inflight_symbols: int | None = None,
    progress_callback: Callable[[int, int, str, int], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    config = config or PumpFadeDecisionConfig()
    if not 1 <= workers <= 16:
        raise PumpFadeBuildError("workers must be between 1 and 16")
    resolved_max_inflight = _resolve_max_inflight_symbols(
        workers=workers, max_inflight_symbols=max_inflight_symbols
    )
    paths = list(resolve_pump_fade_cache_universe(cache_dir).perpetual_paths)
    if limit_symbols is not None:
        if limit_symbols <= 0:
            raise PumpFadeBuildError("limit_symbols must be positive")
        paths = paths[:limit_symbols]
    if not paths:
        raise PumpFadeBuildError(f"no parquet symbol caches found in {cache_dir}")
    state_frames: list[pd.DataFrame] = []
    label_frames: list[pd.DataFrame] = []
    quality_rows: list[dict[str, object]] = []
    built = _iter_bounded_symbol_builds(
        paths=paths,
        config=config,
        workers=workers,
        max_inflight_symbols=resolved_max_inflight,
    )
    for index, (path, outcome) in enumerate(built, start=1):
        if isinstance(outcome, PumpFadeSymbolBuildResult):
            states = outcome.online_states
            labels = outcome.labels
            quality_rows.append(outcome.quality)
        else:
            states = pd.DataFrame()
            labels = pd.DataFrame()
            quality_rows.append(_rejected_quality_row(path=path, reason=outcome))
        if not states.empty:
            state_frames.append(states)
            label_frames.append(labels)
        if progress_callback is not None:
            progress_callback(index, len(paths), path.stem, len(states))
    online_states = (
        pd.concat(state_frames, ignore_index=True) if state_frames else pd.DataFrame()
    )
    labels = (
        pd.concat(label_frames, ignore_index=True) if label_frames else pd.DataFrame()
    )
    quality = pd.DataFrame(quality_rows)
    return online_states, labels, quality


def build_pump_fade_decisions_with_quality(
    *,
    cache_dir: Path,
    config: PumpFadeDecisionConfig | None = None,
    limit_symbols: int | None = None,
    workers: int = 1,
    max_inflight_symbols: int | None = None,
    progress_callback: Callable[[int, int, str, int], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the explicit supervised research view and quality audit."""

    online_states, labels, quality = build_pump_fade_datasets_with_quality(
        cache_dir=cache_dir,
        config=config,
        limit_symbols=limit_symbols,
        workers=workers,
        max_inflight_symbols=max_inflight_symbols,
        progress_callback=progress_callback,
    )
    return join_pump_fade_states_and_labels(online_states, labels), quality


def _build_symbol_or_error(
    path: Path,
    config: PumpFadeDecisionConfig,
) -> PumpFadeSymbolBuildResult | str:
    try:
        return build_pump_fade_symbol_result(path, symbol=path.stem, config=config)
    except PumpFadeBuildError as exc:
        return str(exc)


def _rejected_quality_row(*, path: Path, reason: str) -> dict[str, object]:
    return {
        "symbol": path.stem,
        "source_row_count": 0,
        "valid_row_count": 0,
        "dropped_row_count": 0,
        "non_finite_row_count": 0,
        "invalid_ohlc_row_count": 0,
        "negative_activity_row_count": 0,
        "decision_row_count": 0,
        "event_count": 0,
        "oi_covered_decision_row_count": 0,
        "oi_covered_decision_row_fraction": 0.0,
        "status": "REJECTED",
        "reason": reason,
    }
