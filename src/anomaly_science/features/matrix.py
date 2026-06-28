from __future__ import annotations

import csv
import heapq
import json
import math
import os
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence, TypeVar, get_args, get_origin, get_type_hints

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.artifacts.manifest import sha256_file
from anomaly_science.artifacts.writer import ArtifactWriteError, link_or_copy_identical_artifact
from anomaly_science.contracts.artifacts import (
    ArtifactSchema,
    get_artifact_schema,
    get_strategy_artifact_companion_names,
    should_materialize_strategy_artifact_alias,
)
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.contracts.features import StrategyFeatureMatrixRow
from anomaly_science.contracts.market import FIVE_MINUTES_MS, Candle1m, LiquidationEvent, MarketDataContractError, ONE_MINUTE_MS, OpenInterest5m, SymbolDayUniverseRow
from anomaly_science.contracts.state import StrategyState1mRow
from anomaly_science.contracts.time import utc_ms_to_datetime
from anomaly_science.memory_guard import check_memory_budget
from anomaly_science.data.normalized import normalize_candles_1m, normalize_liquidations, normalize_open_interest_5m, normalize_symbol_universe_by_day
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource, MarketDataSource
from anomaly_science.features.catalog import FEATURE_SCHEMA_VERSION, build_default_feature_catalog, feature_rows_to_artifact
from anomaly_science.features.config import FeatureMatrixConfig
from anomaly_science.future.atr import AtrComputationError, compute_atr_1d_asof
from anomaly_science.future.builder import iter_candles_1m_csv, iter_strategy_state_1m_csv, load_strategy_state_1m_csv
from anomaly_science.state.parquet_sidecar import iter_state_1m_parquet_sidecar_mappings, state_1m_parquet_sidecar_exists
from anomaly_science.progress import ProgressCallback, ProgressUpdate

EPS = 1e-12
T = TypeVar("T")


class _CandleSeries:
    def __init__(self, rows: Sequence[Candle1m]) -> None:
        self._rows = tuple(sorted(rows, key=lambda item: (item.available_time_ms, item.open_time_ms)))
        self._available_times = tuple(item.available_time_ms for item in self._rows)
        self._open_times = tuple(item.open_time_ms for item in self._rows)
        prefix: list[float] = []
        volume_prefix: list[float] = []
        volume_square_prefix: list[float] = []
        quote_volume_prefix: list[float] = []
        quote_volume_square_prefix: list[float] = []
        delta_quote_prefix: list[float] = []
        missing_taker_buy_quote_prefix: list[int] = []
        one_minute_return_times: list[int] = []
        one_minute_returns: list[float | None] = []
        running_sum = 0.0
        running_volume_sum = 0.0
        running_volume_square_sum = 0.0
        running_quote_volume_sum = 0.0
        running_quote_volume_square_sum = 0.0
        running_delta_quote_sum = 0.0
        running_missing_taker_buy_quote = 0
        for index, candle in enumerate(self._rows):
            running_volume_sum += candle.volume
            running_volume_square_sum += candle.volume * candle.volume
            running_quote_volume_sum += candle.quote_volume
            running_quote_volume_square_sum += candle.quote_volume * candle.quote_volume
            if candle.taker_buy_quote_volume is None:
                running_missing_taker_buy_quote += 1
            else:
                running_delta_quote_sum += 2.0 * candle.taker_buy_quote_volume - candle.quote_volume
            volume_prefix.append(running_volume_sum)
            volume_square_prefix.append(running_volume_square_sum)
            quote_volume_prefix.append(running_quote_volume_sum)
            quote_volume_square_prefix.append(running_quote_volume_square_sum)
            delta_quote_prefix.append(running_delta_quote_sum)
            missing_taker_buy_quote_prefix.append(running_missing_taker_buy_quote)
            if index == 0:
                prefix.append(0.0)
                continue
            previous_close = self._rows[index - 1].close
            one_minute_return_times.append(candle.available_time_ms)
            one_minute_returns.append(None if previous_close <= 0 else candle.close / previous_close - 1.0)
            running_sum += max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
            prefix.append(running_sum)
        self._true_range_prefix_sums = tuple(prefix)
        self._volume_prefix_sums = tuple(volume_prefix)
        self._volume_square_prefix_sums = tuple(volume_square_prefix)
        self._quote_volume_prefix_sums = tuple(quote_volume_prefix)
        self._quote_volume_square_prefix_sums = tuple(quote_volume_square_prefix)
        self._delta_quote_prefix_sums = tuple(delta_quote_prefix)
        self._missing_taker_buy_quote_prefix_counts = tuple(missing_taker_buy_quote_prefix)
        self._one_minute_return_times = tuple(one_minute_return_times)
        self._one_minute_returns = tuple(one_minute_returns)
        self._rolling_median_cache: dict[tuple[str, int], tuple[float | None, ...]] = {}
        self._return_correlation_cache: dict[int, _ReturnCorrelationIndex] = {}

    def __iter__(self):
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, index):
        return self._rows[index]

    def asof(self, snapshot_time_ms: int) -> tuple[Candle1m, ...]:
        end = bisect_right(self._available_times, snapshot_time_ms)
        return self._rows[:end]

    def asof_count(self, snapshot_time_ms: int) -> int:
        return bisect_right(self._available_times, snapshot_time_ms)

    def trailing_asof(self, snapshot_time_ms: int, count: int) -> tuple[Candle1m, ...]:
        end = self.asof_count(snapshot_time_ms)
        return self._rows[max(0, end - count) : end]

    def current(self, snapshot_time_ms: int) -> Candle1m | None:
        index = bisect_right(self._available_times, snapshot_time_ms) - 1
        if index < 0:
            return None
        current = self._rows[index]
        if current.available_time_ms != snapshot_time_ms:
            return None
        return current

    def previous_and_current(self, snapshot_time_ms: int) -> tuple[Candle1m, Candle1m] | None:
        current_index = bisect_right(self._available_times, snapshot_time_ms) - 1
        previous_index = current_index - 1
        if previous_index < 0:
            return None
        current = self._rows[current_index]
        if current.available_time_ms != snapshot_time_ms:
            return None
        return self._rows[previous_index], current

    def history_before(self, available_time_ms: int, count: int) -> tuple[Candle1m, ...]:
        end = bisect_left(self._available_times, available_time_ms)
        if end < count:
            return ()
        return self._rows[max(0, end - count) : end]

    def trailing_moments_before(self, available_time_ms: int, count: int, field_name: str) -> tuple[int, float, float] | None:
        end = bisect_left(self._available_times, available_time_ms)
        start = end - count
        if start < 0:
            return None
        if field_name == "volume":
            sums = self._volume_prefix_sums
            square_sums = self._volume_square_prefix_sums
        elif field_name == "quote_volume":
            sums = self._quote_volume_prefix_sums
            square_sums = self._quote_volume_square_prefix_sums
        else:
            raise ValueError(f"unsupported candle moment field: {field_name}")
        total = sums[end - 1] - (sums[start - 1] if start > 0 else 0.0)
        square_total = square_sums[end - 1] - (square_sums[start - 1] if start > 0 else 0.0)
        return count, total, square_total

    def trailing_median_before(self, available_time_ms: int, count: int, field_name: str) -> float | None:
        index = bisect_left(self._available_times, available_time_ms)
        medians = self._rolling_medians(field_name=field_name, count=count)
        if index < 0 or index >= len(medians):
            return None
        return medians[index]

    def _rolling_medians(self, *, field_name: str, count: int) -> tuple[float | None, ...]:
        cache_key = (field_name, count)
        cached = self._rolling_median_cache.get(cache_key)
        if cached is not None:
            return cached
        if field_name == "quote_volume":
            values = [row.quote_volume for row in self._rows]
        elif field_name == "volume":
            values = [row.volume for row in self._rows]
        else:
            raise ValueError(f"unsupported candle median field: {field_name}")
        medians: list[float | None] = [None] * len(values)
        if count > 0 and len(values) > count:
            import pandas as pd

            rolling = pd.Series(values, dtype="float64").rolling(window=count).median()
            for index in range(count, len(values)):
                value = rolling.iat[index - 1]
                medians[index] = None if math.isnan(value) else float(value)
        result = tuple(medians)
        self._rolling_median_cache[cache_key] = result
        return result

    def window(self, snapshot_time_ms: int, window_minutes: int) -> tuple[Candle1m, ...]:
        end = bisect_right(self._available_times, snapshot_time_ms)
        rows = self._rows[max(0, end - window_minutes) : end]
        if len(rows) < window_minutes:
            return ()
        if rows[-1].available_time_ms != snapshot_time_ms:
            return ()
        window_start_ms = snapshot_time_ms - window_minutes * ONE_MINUTE_MS
        if rows[0].open_time_ms < window_start_ms:
            return ()
        return rows

    def window_return(self, *, snapshot_time_ms: int, window_minutes: int) -> float | None:
        end = bisect_right(self._available_times, snapshot_time_ms)
        start = end - window_minutes
        if start < 0:
            return None
        first = self._rows[start]
        last = self._rows[end - 1]
        if last.available_time_ms != snapshot_time_ms or first.open <= 0:
            return None
        window_start_ms = snapshot_time_ms - window_minutes * ONE_MINUTE_MS
        if first.open_time_ms < window_start_ms:
            return None
        return last.close / first.open - 1.0

    def one_minute_returns_window(
        self,
        *,
        snapshot_time_ms: int,
        window_minutes: int,
    ) -> tuple[tuple[int, ...], tuple[float, ...]]:
        end = bisect_right(self._available_times, snapshot_time_ms)
        start = end - window_minutes - 1
        if start < 0:
            return (), ()
        if self._rows[end - 1].available_time_ms != snapshot_time_ms:
            return (), ()
        times: list[int] = []
        values: list[float] = []
        for index in range(start + 1, end):
            previous = self._rows[index - 1]
            current = self._rows[index]
            if previous.close <= 0:
                continue
            times.append(current.available_time_ms)
            values.append(current.close / previous.close - 1.0)
        if len(values) < window_minutes:
            return (), ()
        return tuple(times), tuple(values)

    def rolling_return_correlation_with(
        self,
        *,
        other: _CandleSeries,
        snapshot_time_ms: int,
        window_minutes: int,
    ) -> float | None:
        cache_key = id(other)
        correlation_index = self._return_correlation_cache.get(cache_key)
        if correlation_index is None:
            correlation_index = _ReturnCorrelationIndex(left=self, right=other)
            self._return_correlation_cache[cache_key] = correlation_index
        return correlation_index.correlation(snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)

    def event_window(self, *, event_start_time_ms: int, snapshot_time_ms: int) -> tuple[Candle1m, ...]:
        end = bisect_right(self._available_times, snapshot_time_ms)
        start = bisect_left(self._open_times, event_start_time_ms, 0, end)
        return self._rows[start:end]

    def event_quote_volume_sum(self, *, event_start_time_ms: int, snapshot_time_ms: int) -> float:
        end = bisect_right(self._available_times, snapshot_time_ms)
        start = bisect_left(self._open_times, event_start_time_ms, 0, end)
        if start >= end:
            return 0.0
        return self._quote_volume_prefix_sums[end - 1] - (
            self._quote_volume_prefix_sums[start - 1] if start > 0 else 0.0
        )

    def event_cvd_ratio(self, *, event_start_time_ms: int, snapshot_time_ms: int) -> tuple[bool, bool, float | None]:
        end = bisect_right(self._available_times, snapshot_time_ms)
        start = bisect_left(self._open_times, event_start_time_ms, 0, end)
        if start >= end:
            return False, False, None
        missing_count = self._missing_taker_buy_quote_prefix_counts[end - 1] - (
            self._missing_taker_buy_quote_prefix_counts[start - 1] if start > 0 else 0
        )
        if missing_count > 0:
            return True, True, None
        quote_volume = self._quote_volume_prefix_sums[end - 1] - (
            self._quote_volume_prefix_sums[start - 1] if start > 0 else 0.0
        )
        if quote_volume <= 0:
            return True, False, None
        delta_quote = self._delta_quote_prefix_sums[end - 1] - (
            self._delta_quote_prefix_sums[start - 1] if start > 0 else 0.0
        )
        return True, False, delta_quote / quote_volume

    def window_cvd_and_price_change_atr(
        self,
        *,
        snapshot_time_ms: int,
        window_minutes: int,
        atr_value: float | None,
    ) -> tuple[float | None, float | None, float | None]:
        end = bisect_right(self._available_times, snapshot_time_ms)
        start = end - window_minutes
        if start < 0:
            return None, None, None
        first = self._rows[start]
        last = self._rows[end - 1]
        if last.available_time_ms != snapshot_time_ms:
            return None, None, None
        window_start_ms = snapshot_time_ms - window_minutes * ONE_MINUTE_MS
        if first.open_time_ms < window_start_ms:
            return None, None, None
        missing_count = self._missing_taker_buy_quote_prefix_counts[end - 1] - (
            self._missing_taker_buy_quote_prefix_counts[start - 1] if start > 0 else 0
        )
        cvd_ratio = None
        if missing_count == 0:
            quote_volume = self._quote_volume_prefix_sums[end - 1] - (
                self._quote_volume_prefix_sums[start - 1] if start > 0 else 0.0
            )
            if quote_volume > 0:
                delta_quote = self._delta_quote_prefix_sums[end - 1] - (
                    self._delta_quote_prefix_sums[start - 1] if start > 0 else 0.0
                )
                cvd_ratio = delta_quote / quote_volume
        raw_price_change = last.close - first.open
        price_change_atr = None
        if atr_value is not None and atr_value > 0:
            price_change_atr = raw_price_change / atr_value
        return cvd_ratio, price_change_atr, raw_price_change

    def atr_asof(self, *, symbol: str, snapshot_time_ms: int, atr_window_minutes: int):
        history_count = self.asof_count(snapshot_time_ms)
        required_candle_count = atr_window_minutes + 1
        if history_count < required_candle_count:
            raise AtrComputationError(
                "insufficient as-of 1m candle history for ATR: "
                f"need {required_candle_count} closed candles for {atr_window_minutes} true ranges, "
                f"got {history_count} for {symbol} at {snapshot_time_ms}"
            )
        source_start_index = history_count - atr_window_minutes
        last_source_index = history_count - 1
        prefix_before_window = self._true_range_prefix_sums[source_start_index - 1]
        prefix_at_window_end = self._true_range_prefix_sums[last_source_index]
        atr = (prefix_at_window_end - prefix_before_window) / atr_window_minutes
        last_close = self._rows[last_source_index].close
        if last_close <= 0 or not math.isfinite(atr) or atr <= 0:
            raise AtrComputationError("computed ATR must be positive and finite")
        return atr, atr / last_close


class _ReturnCorrelationIndex:
    def __init__(self, *, left: _CandleSeries, right: _CandleSeries) -> None:
        times: list[int] = []
        left_prefix: list[float] = []
        right_prefix: list[float] = []
        left_square_prefix: list[float] = []
        right_square_prefix: list[float] = []
        product_prefix: list[float] = []
        left_sum = 0.0
        right_sum = 0.0
        left_square_sum = 0.0
        right_square_sum = 0.0
        product_sum = 0.0
        left_index = 0
        right_index = 0
        while left_index < len(left._one_minute_return_times) and right_index < len(right._one_minute_return_times):
            left_time = left._one_minute_return_times[left_index]
            right_time = right._one_minute_return_times[right_index]
            if left_time == right_time:
                left_value = left._one_minute_returns[left_index]
                right_value = right._one_minute_returns[right_index]
                if left_value is not None and right_value is not None:
                    times.append(left_time)
                    left_sum += left_value
                    right_sum += right_value
                    left_square_sum += left_value * left_value
                    right_square_sum += right_value * right_value
                    product_sum += left_value * right_value
                    left_prefix.append(left_sum)
                    right_prefix.append(right_sum)
                    left_square_prefix.append(left_square_sum)
                    right_square_prefix.append(right_square_sum)
                    product_prefix.append(product_sum)
                left_index += 1
                right_index += 1
            elif left_time < right_time:
                left_index += 1
            else:
                right_index += 1
        self._times = tuple(times)
        self._left_prefix = tuple(left_prefix)
        self._right_prefix = tuple(right_prefix)
        self._left_square_prefix = tuple(left_square_prefix)
        self._right_square_prefix = tuple(right_square_prefix)
        self._product_prefix = tuple(product_prefix)

    def correlation(self, *, snapshot_time_ms: int, window_minutes: int) -> float | None:
        end = bisect_right(self._times, snapshot_time_ms)
        start = end - window_minutes
        if start < 0 or end <= 0 or self._times[end - 1] != snapshot_time_ms:
            return None
        left_sum = self._range_sum(self._left_prefix, start, end)
        right_sum = self._range_sum(self._right_prefix, start, end)
        left_square_sum = self._range_sum(self._left_square_prefix, start, end)
        right_square_sum = self._range_sum(self._right_square_prefix, start, end)
        product_sum = self._range_sum(self._product_prefix, start, end)
        left_var = left_square_sum - (left_sum * left_sum) / window_minutes
        right_var = right_square_sum - (right_sum * right_sum) / window_minutes
        if left_var <= 0 or right_var <= 0:
            return None
        covariance = product_sum - (left_sum * right_sum) / window_minutes
        corr = covariance / math.sqrt(left_var * right_var)
        return min(max(corr, -1.0), 1.0)

    @staticmethod
    def _range_sum(prefix: tuple[float, ...], start: int, end: int) -> float:
        return prefix[end - 1] - (prefix[start - 1] if start > 0 else 0.0)


class _OpenInterestSeries:
    def __init__(self, rows: Sequence[OpenInterest5m]) -> None:
        self._rows = tuple(sorted(rows, key=lambda item: (item.timestamp_ms, item.available_time_ms)))
        self._timestamps = tuple(item.timestamp_ms for item in self._rows)

    def asof_latest(self, snapshot_time_ms: int) -> OpenInterest5m | None:
        return self.at_or_before_timestamp(snapshot_time_ms, snapshot_time_ms)

    def at_or_before_timestamp(self, timestamp_ms: int, snapshot_time_ms: int) -> OpenInterest5m | None:
        index = bisect_right(self._timestamps, timestamp_ms) - 1
        while index >= 0:
            item = self._rows[index]
            if item.timestamp_ms <= timestamp_ms and (
                item.available_time_ms <= snapshot_time_ms
            ):
                return item
            index -= 1
        return None


class _LiquidationSeries:
    def __init__(self, rows: Sequence[LiquidationEvent]) -> None:
        self._rows = tuple(sorted(rows, key=lambda item: (item.event_time_ms, item.available_time_ms)))
        self._event_times = tuple(item.event_time_ms for item in self._rows)

    def quote_by_side_in_window_asof(
        self,
        *,
        start_time_ms: int,
        end_time_ms: int,
        snapshot_time_ms: int,
    ) -> tuple[float, float]:
        short_quote = 0.0
        long_quote = 0.0
        for item in self._window_asof(
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            snapshot_time_ms=snapshot_time_ms,
        ):
            if item.side == "short":
                short_quote += item.quote_quantity
            elif item.side == "long":
                long_quote += item.quote_quantity
        return short_quote, long_quote

    def quote_in_window_asof(
        self,
        *,
        start_time_ms: int,
        end_time_ms: int,
        snapshot_time_ms: int,
    ) -> float:
        return sum(
            item.quote_quantity
            for item in self._window_asof(
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                snapshot_time_ms=snapshot_time_ms,
            )
        )

    def _window_asof(
        self,
        *,
        start_time_ms: int,
        end_time_ms: int,
        snapshot_time_ms: int,
    ) -> tuple[LiquidationEvent, ...]:
        start_index = bisect_left(self._event_times, start_time_ms)
        end_index = bisect_left(self._event_times, end_time_ms, start_index)
        return tuple(
            item
            for item in self._rows[start_index:end_index]
            if item.available_time_ms <= snapshot_time_ms
        )


class _AtrSpeedFeatures:
    def __init__(
        self,
        *,
        atr_value: float | None,
        atr_pct_value: float | None,
        price_speed_atr: float | None,
        status: str,
    ) -> None:
        self.atr_value = atr_value
        self.atr_pct_value = atr_pct_value
        self.price_speed_atr = price_speed_atr
        self.status = status


class _SymbolFeatureIndex:
    """Exact per-symbol/per-snapshot feature cache for the feature-matrix hot path.

    The index only memoizes features whose inputs are fully determined by
    (symbol, snapshot_time_ms) and the frozen feature config. Event-specific
    features continue to be computed from the event state to avoid changing
    strategy semantics.
    """

    def __init__(
        self,
        *,
        symbol: str,
        candles: _CandleSeries,
        open_interest_rows: Sequence[OpenInterest5m] | _OpenInterestSeries | None,
        config: FeatureMatrixConfig,
    ) -> None:
        self.symbol = symbol
        self.candles = candles
        self._open_interest_rows = open_interest_rows
        self._config = config
        self._atr_speed_by_snapshot: dict[int, _AtrSpeedFeatures] = {}
        self._volume_by_snapshot: dict[int, _VolumeFeatures] = {}
        self._oi_by_snapshot: dict[int, _OiFeatures] = {}

    def atr_speed_features(self, *, snapshot_time_ms: int) -> _AtrSpeedFeatures:
        cached = self._atr_speed_by_snapshot.get(snapshot_time_ms)
        if cached is not None:
            return cached
        atr_value: float | None = None
        atr_pct_value: float | None = None
        price_speed_atr: float | None = None
        status = "ok"
        try:
            atr_value, atr_pct_value = self.candles.atr_asof(
                symbol=self.symbol,
                snapshot_time_ms=snapshot_time_ms,
                atr_window_minutes=self._config.atr_window_minutes,
            )
        except AtrComputationError:
            status = "insufficient_atr_history"
        if atr_value is not None:
            price_speed_atr = _price_speed_atr(
                candles=self.candles,
                snapshot_time_ms=snapshot_time_ms,
                atr_value=atr_value,
                atr_window_minutes=self._config.atr_window_minutes,
            )
            if price_speed_atr is None and status == "ok":
                status = "missing_previous_close_for_speed"
        result = _AtrSpeedFeatures(
            atr_value=atr_value,
            atr_pct_value=atr_pct_value,
            price_speed_atr=price_speed_atr,
            status=status,
        )
        self._atr_speed_by_snapshot[snapshot_time_ms] = result
        return result

    def volume_features(self, *, state: StrategyState1mRow) -> _VolumeFeatures:
        cached = self._volume_by_snapshot.get(state.snapshot_time_ms)
        if cached is not None:
            return cached
        result = _volume_features(candles=self.candles, state=state, config=self._config)
        self._volume_by_snapshot[state.snapshot_time_ms] = result
        return result

    def oi_features(self, *, snapshot_time_ms: int) -> _OiFeatures:
        cached = self._oi_by_snapshot.get(snapshot_time_ms)
        if cached is not None:
            return cached
        result = _oi_features(open_interest_rows=self._open_interest_rows, snapshot_time_ms=snapshot_time_ms)
        self._oi_by_snapshot[snapshot_time_ms] = result
        return result


class _StateSnapshotRow:
    def __init__(
        self,
        *,
        symbol: str,
        event_id: str,
        current_return_from_start: float | None,
        event_alive: bool,
    ) -> None:
        self.symbol = symbol
        self.event_id = event_id
        self.current_return_from_start = current_return_from_start
        self.event_alive = event_alive


def build_price_time_feature_matrix(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    open_interest_5m: Sequence[OpenInterest5m] | Iterable[OpenInterest5m] | None = None,
    liquidations: Sequence[LiquidationEvent] | Iterable[LiquidationEvent] | None = None,
    symbol_universe_by_day: Sequence[SymbolDayUniverseRow] | Iterable[SymbolDayUniverseRow] | None = None,
    config: FeatureMatrixConfig | None = None,
) -> tuple[StrategyFeatureMatrixRow, ...]:
    return tuple(
        iter_price_time_feature_matrix(
            candles_1m=candles_1m,
            state_rows=state_rows,
            open_interest_5m=open_interest_5m,
            liquidations=liquidations,
            symbol_universe_by_day=symbol_universe_by_day,
            config=config,
            sort_state_rows=True,
        )
    )


def iter_price_time_feature_matrix(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    open_interest_5m: Sequence[OpenInterest5m] | Iterable[OpenInterest5m] | None = None,
    liquidations: Sequence[LiquidationEvent] | Iterable[LiquidationEvent] | None = None,
    symbol_universe_by_day: Sequence[SymbolDayUniverseRow] | Iterable[SymbolDayUniverseRow] | None = None,
    states_by_snapshot: Mapping[int, Sequence[StrategyState1mRow | _StateSnapshotRow]] | None = None,
    config: FeatureMatrixConfig | None = None,
    sort_state_rows: bool = False,
    state_rows_sorted_by_snapshot: bool = False,
    cross_section_store_dir: Path | None = None,
) -> Iterable[StrategyFeatureMatrixRow]:
    """Build as-of feature rows from state rows.

    This builder deliberately does not read `anomaly_future_paths.csv`. ATR and
    all rolling/self-history features are recomputed from normalized market data
    with `available_time_ms <= snapshot_time_ms`, so the feature matrix has no
    dependency on future outcome artifacts.
    """
    cfg = config or FeatureMatrixConfig()
    if sort_state_rows or states_by_snapshot is None:
        state_rows = tuple(state_rows)
    else:
        state_rows = state_rows
    raw_candles_by_symbol: dict[str, list[Candle1m]] = {}
    _loaded_candle_count = 0
    for candle in candles_1m:
        raw_candles_by_symbol.setdefault(candle.symbol, []).append(candle)
        _loaded_candle_count += 1
        if _loaded_candle_count % 2_000_000 == 0:
            check_memory_budget(
                label="feature_matrix cross-section candle load (in-memory candle universe)"
            )
    candles_by_symbol: dict[str, _CandleSeries] = {
        symbol: _CandleSeries(rows)
        for symbol, rows in raw_candles_by_symbol.items()
    }

    oi_by_symbol: dict[str, _OpenInterestSeries] | None = None
    if open_interest_5m is not None:
        raw_oi_by_symbol: dict[str, list[OpenInterest5m]] = {}
        for item in open_interest_5m:
            raw_oi_by_symbol.setdefault(item.symbol, []).append(item)
        oi_by_symbol = {symbol: _OpenInterestSeries(rows) for symbol, rows in raw_oi_by_symbol.items()}

    liquidations_by_symbol: dict[str, _LiquidationSeries] | None = None
    if liquidations is not None:
        raw_liquidations_by_symbol: dict[str, list[LiquidationEvent]] = {}
        for item in liquidations:
            raw_liquidations_by_symbol.setdefault(item.symbol, []).append(item)
        liquidations_by_symbol = {
            symbol: _LiquidationSeries(rows)
            for symbol, rows in raw_liquidations_by_symbol.items()
        }

    universe_by_day = _universe_symbols_by_day(symbol_universe_by_day)
    if states_by_snapshot is None:
        built_states_by_snapshot: dict[int, list[StrategyState1mRow | _StateSnapshotRow]] = {}
        for state in state_rows:
            built_states_by_snapshot.setdefault(state.snapshot_time_ms, []).append(state)
        states_by_snapshot = built_states_by_snapshot

    ordered_state_rows = (
        sorted(state_rows, key=lambda item: (item.snapshot_time_ms, item.symbol, item.event_id))
        if sort_state_rows
        else state_rows
    )
    cross_section_by_snapshot: dict[int, tuple[dict[str, _CrossSectionFeatures], _CrossSectionFeatures]] = {}
    cached_snapshot_time_ms: int | None = None
    cached_cross_section: tuple[dict[str, _CrossSectionFeatures], _CrossSectionFeatures] | None = None
    cross_section_reader: _CrossSectionFeatureSidecarReader | None = None
    if cross_section_store_dir is not None:
        sidecar_path = cross_section_store_dir / "strategy_feature_matrix.cross_section_features.csv.tmp"
        _write_cross_section_feature_sidecar_from_snapshots(
            sidecar_path=sidecar_path,
            snapshot_times=sorted(states_by_snapshot),
            candles_by_symbol=candles_by_symbol,
            open_interest_by_symbol=oi_by_symbol,
            liquidations_by_symbol=liquidations_by_symbol,
            universe_by_day=universe_by_day,
            states_by_snapshot=states_by_snapshot,
            min_cross_section_symbols=cfg.min_cross_section_symbols,
        )
        cross_section_reader = _CrossSectionFeatureSidecarReader(sidecar_path, remove_on_close=True)
    try:
        for state in ordered_state_rows:
            symbol_candles = candles_by_symbol.get(state.symbol, [])
            if cross_section_reader is not None:
                cross_section_features = cross_section_reader.get(
                    snapshot_time_ms=state.snapshot_time_ms,
                    symbol=state.symbol,
                )
            elif state_rows_sorted_by_snapshot:
                if cached_snapshot_time_ms != state.snapshot_time_ms:
                    snapshot_states = states_by_snapshot.get(state.snapshot_time_ms, [])
                    cached_cross_section = _cross_section_features_by_symbol(
                        snapshot_time_ms=state.snapshot_time_ms,
                        candles_by_symbol=candles_by_symbol,
                        open_interest_by_symbol=oi_by_symbol,
                        liquidations_by_symbol=liquidations_by_symbol,
                        universe_by_day=universe_by_day,
                        states_at_snapshot=snapshot_states,
                        min_cross_section_symbols=cfg.min_cross_section_symbols,
                        target_symbols={row.symbol for row in snapshot_states},
                    )
                    cached_snapshot_time_ms = state.snapshot_time_ms
                if cached_cross_section is None:
                    raise RuntimeError("snapshot-sorted cross-section cache was not initialized")
                cross_section_by_symbol, missing_cross_section = cached_cross_section
                cross_section_features = cross_section_by_symbol.get(state.symbol, missing_cross_section)
            else:
                if state.snapshot_time_ms not in cross_section_by_snapshot:
                    snapshot_states = states_by_snapshot.get(state.snapshot_time_ms, [])
                    cross_section_by_snapshot[state.snapshot_time_ms] = _cross_section_features_by_symbol(
                        snapshot_time_ms=state.snapshot_time_ms,
                        candles_by_symbol=candles_by_symbol,
                        open_interest_by_symbol=oi_by_symbol,
                        liquidations_by_symbol=liquidations_by_symbol,
                        universe_by_day=universe_by_day,
                        states_at_snapshot=snapshot_states,
                        min_cross_section_symbols=cfg.min_cross_section_symbols,
                        target_symbols={row.symbol for row in snapshot_states},
                    )
                cross_section_by_symbol, missing_cross_section = cross_section_by_snapshot[state.snapshot_time_ms]
                cross_section_features = cross_section_by_symbol.get(state.symbol, missing_cross_section)
            yield _build_state_feature_row(
                state=state,
                candles=symbol_candles,
                open_interest_rows=None if oi_by_symbol is None else oi_by_symbol.get(state.symbol, []),
                liquidation_rows=None
                if liquidations_by_symbol is None
                else liquidations_by_symbol.get(state.symbol, []),
                candles_by_symbol=candles_by_symbol,
                open_interest_by_symbol=oi_by_symbol,
                liquidations_by_symbol=liquidations_by_symbol,
                universe_by_day=universe_by_day,
                states_at_snapshot=states_by_snapshot.get(state.snapshot_time_ms, []),
                cross_section_features=cross_section_features,
                config=cfg,
            )
    finally:
        if cross_section_reader is not None:
            cross_section_reader.close()


def build_price_time_feature_matrix_from_source(
    *,
    source: MarketDataSource,
    state_path: str | Path,
    config: FeatureMatrixConfig | None = None,
) -> tuple[StrategyFeatureMatrixRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    oi_frame = source.read_frame("open_interest_5m", required=False)
    liquidation_frame = source.read_frame("liquidations", required=False)
    universe_frame = source.read_frame("symbol_universe_by_day", required=False)
    state_rows = load_strategy_state_1m_csv(state_path)
    return build_price_time_feature_matrix(
        candles_1m=normalize_candles_1m(frame),
        open_interest_5m=None if oi_frame is None else normalize_open_interest_5m(oi_frame),
        liquidations=None if liquidation_frame is None else normalize_liquidations(liquidation_frame),
        symbol_universe_by_day=None if universe_frame is None else normalize_symbol_universe_by_day(universe_frame),
        state_rows=state_rows,
        config=config,
    )


def feature_matrix_rows_to_artifact(rows: Sequence[StrategyFeatureMatrixRow]) -> list[dict[str, object]]:
    return [feature_matrix_row_to_artifact(row) for row in rows]


def feature_matrix_row_to_artifact(row: StrategyFeatureMatrixRow) -> dict[str, object]:
    return _feature_matrix_row_to_artifact_for_columns(
        row=row,
        fieldnames=get_artifact_schema("strategy_feature_matrix.csv").required_columns,
    )


def _feature_matrix_row_to_artifact_for_columns(
    *,
    row: StrategyFeatureMatrixRow,
    fieldnames: Sequence[str],
) -> dict[str, object]:
    attribute_names = [_feature_matrix_row_attribute_name(fieldname) for fieldname in fieldnames]
    return _feature_matrix_row_to_artifact_for_attributes(
        row=row,
        fieldnames=fieldnames,
        attribute_names=attribute_names,
    )


def _feature_matrix_row_to_artifact_for_attributes(
    *,
    row: StrategyFeatureMatrixRow,
    fieldnames: Sequence[str],
    attribute_names: Sequence[str],
) -> dict[str, object]:
    return {
        fieldname: _csv_value(getattr(row, attribute_name))
        for fieldname, attribute_name in zip(fieldnames, attribute_names)
    }


def _validate_feature_matrix_fieldnames(fieldnames: Sequence[str]) -> None:
    row_fields = set(StrategyFeatureMatrixRow.__dataclass_fields__)
    missing = [
        fieldname
        for fieldname in fieldnames
        if _feature_matrix_row_attribute_name(fieldname) not in row_fields
    ]
    if missing:
        raise ArtifactWriteError(f"strategy_feature_matrix.csv schema has unknown row fields: {missing}")


def _validate_direct_feature_matrix_fieldnames(fieldnames: Sequence[str]) -> None:
    _validate_feature_matrix_fieldnames(fieldnames)
    schema_attribute_order = tuple(_feature_matrix_row_attribute_name(fieldname) for fieldname in fieldnames)
    dataclass_attribute_order = tuple(StrategyFeatureMatrixRow.__dataclass_fields__)
    if schema_attribute_order != dataclass_attribute_order:
        raise ArtifactWriteError(
            "strategy_feature_matrix.csv direct writer requires schema order to match "
            "StrategyFeatureMatrixRow dataclass order"
        )


def _feature_matrix_row_attribute_name(fieldname: str) -> str:
    if fieldname == "ATR_1d_asof_t":
        return "core_atr_1440"
    return fieldname


class AnomalyFeatureMatrixArtifactError(ValueError):
    """Raised when anomaly_feature_matrix.csv violates its declared schema."""


StrategyFeatureMatrixArtifactError = AnomalyFeatureMatrixArtifactError


FEATURE_MATRIX_PARQUET_SIDECAR_VERSION = "strategy_feature_matrix_parquet_sidecar_v1"
FEATURE_MATRIX_PARQUET_BATCH_SIZE = 100_000


def _feature_matrix_parquet_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(".parquet")


def _feature_matrix_parquet_manifest_path(csv_path: Path) -> Path:
    return csv_path.with_name(csv_path.stem + ".parquet_manifest.json")


def strategy_feature_matrix_parquet_sidecar_row_count(path: str | Path) -> int | None:
    feature_path = Path(path)
    manifest_path = _feature_matrix_parquet_manifest_path(feature_path)
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    row_count = payload.get("row_count")
    if not isinstance(row_count, int) or row_count < 0:
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar manifest has invalid row_count")
    return row_count


def _import_pyarrow_for_feature_matrix_sidecar():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only in incomplete envs
        raise AnomalyFeatureMatrixArtifactError(
            "pyarrow is required for strict strategy_feature_matrix.parquet sidecars; "
            "install project dependencies instead of silently falling back to CSV"
        ) from exc
    return pa, pq


def _feature_matrix_arrow_schema(fieldnames: Sequence[str]):
    pa, _pq = _import_pyarrow_for_feature_matrix_sidecar()
    type_hints = get_type_hints(StrategyFeatureMatrixRow)
    fields = []
    for fieldname in fieldnames:
        attribute_name = _feature_matrix_row_attribute_name(fieldname)
        if attribute_name not in type_hints:
            raise ArtifactWriteError(f"strategy_feature_matrix.parquet schema has unknown field {fieldname!r}")
        fields.append(pa.field(fieldname, _arrow_type_for_python_type(type_hints[attribute_name], pa)))
    return pa.schema(fields)


def _arrow_type_for_python_type(type_hint: object, pa):
    origin = get_origin(type_hint)
    args = tuple(item for item in get_args(type_hint) if item is not type(None))
    if origin is not None and args:
        return _arrow_type_for_python_type(args[0], pa)
    if type_hint is str:
        return pa.string()
    if type_hint is int:
        return pa.int64()
    if type_hint is float:
        return pa.float64()
    if type_hint is bool:
        return pa.bool_()
    raise ArtifactWriteError(f"unsupported strategy_feature_matrix.parquet field type {type_hint!r}")


def _parquet_sidecar_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class _FeatureMatrixParquetSidecarWriter:
    def __init__(
        self,
        *,
        csv_path: Path,
        fieldnames: Sequence[str],
        row_count: int | None = None,
        csv_delivery: str = "full",
    ) -> None:
        self.csv_path = csv_path
        self.parquet_path = _feature_matrix_parquet_path(csv_path)
        self.manifest_path = _feature_matrix_parquet_manifest_path(csv_path)
        self.tmp_parquet_path = self.parquet_path.with_suffix(self.parquet_path.suffix + ".tmp")
        self.fieldnames = tuple(fieldnames)
        self.csv_delivery = csv_delivery
        self._row_count_hint = row_count
        self._row_count = 0
        self._pa, self._pq = _import_pyarrow_for_feature_matrix_sidecar()
        self._schema = _feature_matrix_arrow_schema(self.fieldnames)
        self._writer = self._pq.ParquetWriter(self.tmp_parquet_path, self._schema, compression="zstd")
        self._columns: dict[str, list[object]] = {fieldname: [] for fieldname in self.fieldnames}
        # Ordered list of the same column lists; appending through this avoids a
        # per-cell dict lookup on the hot path (fields x rows).
        self._column_lists: list[list[object]] = [self._columns[fieldname] for fieldname in self.fieldnames]
        self._closed = False

    @property
    def row_count(self) -> int:
        return self._row_count

    def append(self, row_values: Sequence[object]) -> None:
        if self._closed:
            raise ArtifactWriteError("strategy_feature_matrix.parquet sidecar writer is already closed")
        if len(row_values) != len(self.fieldnames):
            raise ArtifactWriteError(
                f"strategy_feature_matrix.parquet sidecar row has {len(row_values)} values, "
                f"expected {len(self.fieldnames)}"
            )
        for column, value in zip(self._column_lists, row_values):
            column.append(_parquet_sidecar_value(value))
        self._row_count += 1
        if self._row_count % FEATURE_MATRIX_PARQUET_BATCH_SIZE == 0:
            self.flush()

    def flush(self) -> None:
        batch_size = len(next(iter(self._columns.values()))) if self._columns else 0
        if batch_size == 0:
            return
        arrays = [
            self._pa.array(self._columns[fieldname], type=self._schema.field(fieldname).type)
            for fieldname in self.fieldnames
        ]
        table = self._pa.Table.from_arrays(arrays, schema=self._schema)
        self._writer.write_table(table)
        self._columns = {fieldname: [] for fieldname in self.fieldnames}
        self._column_lists = [self._columns[fieldname] for fieldname in self.fieldnames]

    def close(self) -> tuple[Path, Path]:
        if self._closed:
            return self.parquet_path, self.manifest_path
        self.flush()
        self._writer.close()
        os.replace(self.tmp_parquet_path, self.parquet_path)
        if self._row_count_hint is not None and self._row_count != self._row_count_hint:
            raise ArtifactWriteError(
                f"strategy_feature_matrix.parquet sidecar row count {self._row_count} "
                f"does not match CSV row count {self._row_count_hint}"
            )
        _write_feature_matrix_parquet_manifest(
            manifest_path=self.manifest_path,
            csv_path=self.csv_path,
            parquet_path=self.parquet_path,
            fieldnames=self.fieldnames,
            row_count=self._row_count,
            csv_delivery=self.csv_delivery,
        )
        self._closed = True
        return self.parquet_path, self.manifest_path


def _write_feature_matrix_parquet_manifest(
    *,
    manifest_path: Path,
    csv_path: Path,
    parquet_path: Path,
    fieldnames: Sequence[str],
    row_count: int,
    csv_delivery: str = "full",
) -> Path:
    payload = {
        "sidecar_version": FEATURE_MATRIX_PARQUET_SIDECAR_VERSION,
        "artifact_name": "strategy_feature_matrix.csv",
        "csv_path": csv_path.name,
        "csv_size_bytes": csv_path.stat().st_size,
        "csv_sha256": sha256_file(csv_path),
        "csv_delivery": csv_delivery,
        "parquet_path": parquet_path.name,
        "parquet_size_bytes": parquet_path.stat().st_size,
        "parquet_sha256": sha256_file(parquet_path),
        "required_columns": list(fieldnames),
        "row_count": row_count,
        "parquet_delivery": "canonical_feature_matrix",
        "delivery": "strict_typed_parquet_sidecar",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, manifest_path)
    return manifest_path


def _load_feature_matrix_parquet_sidecar(
    *,
    csv_path: Path,
    manifest_path: Path,
    expected_columns: Sequence[str],
) -> tuple[StrategyFeatureMatrixRow, ...]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_feature_matrix_parquet_manifest(
        payload=payload,
        csv_path=csv_path,
        manifest_path=manifest_path,
        expected_columns=expected_columns,
    )
    _pa, pq = _import_pyarrow_for_feature_matrix_sidecar()
    parquet_path = manifest_path.parent / str(payload["parquet_path"])
    table = pq.read_table(parquet_path, columns=list(expected_columns))
    if table.column_names != list(expected_columns):
        raise AnomalyFeatureMatrixArtifactError(
            f"strategy_feature_matrix.parquet columns must match {list(expected_columns)}, got {table.column_names}"
        )
    rows: list[StrategyFeatureMatrixRow] = []
    for row_index, row in enumerate(table.to_pylist()):
        try:
            rows.append(_feature_matrix_row_from_csv(row))
        except (TypeError, ValueError, MarketDataContractError) as exc:
            raise AnomalyFeatureMatrixArtifactError(
                f"invalid strategy_feature_matrix.parquet row {row_index}: {exc}"
            ) from exc
    expected_row_count = int(payload["row_count"])
    if len(rows) != expected_row_count:
        raise AnomalyFeatureMatrixArtifactError(
            f"strategy_feature_matrix.parquet row count mismatch: manifest={expected_row_count} actual={len(rows)}"
        )
    return tuple(rows)


def iter_strategy_feature_matrix_frame_chunks_prefer_parquet(
    *,
    csv_path: str | Path | None = None,
    path: str | Path | None = None,
    usecols: Sequence[str],
    chunksize: int,
):
    """Yield strict feature-matrix pandas chunks, preferring the validated Parquet sidecar.

    The Parquet sidecar is used only when both the sidecar file and its manifest are
    present. A partially written or invalid sidecar is an explicit error rather than
    a silent CSV fallback. If no sidecar exists, callers keep the historical strict
    CSV path. ``path`` is accepted as a compatibility alias for the artifact path so
    caller/callee keyword drift fails in tests instead of during a multi-hour run.
    """
    import pandas as pd

    if (csv_path is None) == (path is None):
        raise TypeError("exactly one of csv_path or path is required")
    feature_matrix_path = csv_path if csv_path is not None else path
    assert feature_matrix_path is not None
    path = Path(feature_matrix_path)
    manifest_path = _feature_matrix_parquet_manifest_path(path)
    sidecar_path = _feature_matrix_parquet_path(path)
    manifest_exists = manifest_path.is_file()
    sidecar_exists = sidecar_path.is_file()
    if manifest_exists != sidecar_exists:
        missing = sidecar_path if manifest_exists else manifest_path
        raise AnomalyFeatureMatrixArtifactError(f"incomplete strategy_feature_matrix.parquet sidecar, missing {missing}")
    if not manifest_exists:
        schema = get_artifact_schema(path.name)
        actual_columns = list(pd.read_csv(path, nrows=0).columns)
        expected_columns = list(schema.required_columns)
        if actual_columns != expected_columns:
            raise AnomalyFeatureMatrixArtifactError(
                f"{path.name} columns must match {expected_columns}, got {actual_columns}"
            )
        return pd.read_csv(path, usecols=list(usecols), low_memory=False, chunksize=chunksize)

    return _iter_strategy_feature_matrix_parquet_sidecar_frame_chunks(
        csv_path=path,
        manifest_path=manifest_path,
        usecols=usecols,
        chunksize=chunksize,
    )


def _iter_strategy_feature_matrix_parquet_sidecar_frame_chunks(
    *,
    csv_path: Path,
    manifest_path: Path,
    usecols: Sequence[str],
    chunksize: int,
):
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_feature_matrix_parquet_manifest_for_chunked_read(
        payload=payload,
        csv_path=csv_path,
        manifest_path=manifest_path,
        usecols=usecols,
    )
    _pa, pq = _import_pyarrow_for_feature_matrix_sidecar()
    parquet_path = manifest_path.parent / str(payload["parquet_path"])
    parquet_file = pq.ParquetFile(parquet_path)
    for batch in parquet_file.iter_batches(batch_size=chunksize, columns=list(usecols)):
        yield batch.to_pandas()


def _validate_feature_matrix_parquet_manifest_for_chunked_read(
    *,
    payload: Mapping[str, object],
    csv_path: Path,
    manifest_path: Path,
    usecols: Sequence[str],
) -> None:
    import pandas as pd

    if payload.get("sidecar_version") != FEATURE_MATRIX_PARQUET_SIDECAR_VERSION:
        raise AnomalyFeatureMatrixArtifactError(
            f"unsupported strategy_feature_matrix.parquet sidecar version in {manifest_path}"
        )
    if payload.get("artifact_name") != "strategy_feature_matrix.csv":
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar is bound to the wrong artifact")
    if payload.get("csv_path") != csv_path.name:
        raise AnomalyFeatureMatrixArtifactError(
            f"feature matrix parquet sidecar is bound to {payload.get('csv_path')!r}, not {csv_path.name!r}"
        )
    schema = get_artifact_schema(csv_path.name)
    if not csv_path.is_file():
        raise AnomalyFeatureMatrixArtifactError(f"feature matrix CSV artifact is missing: {csv_path}")
    actual_csv_columns = list(pd.read_csv(csv_path, nrows=0).columns)
    expected_columns = list(schema.required_columns)
    if actual_csv_columns != expected_columns:
        raise AnomalyFeatureMatrixArtifactError(
            f"{csv_path.name} columns must match {expected_columns}, got {actual_csv_columns}"
        )
    required_columns = payload.get("required_columns") or ()
    if tuple(required_columns) != tuple(expected_columns):
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar schema does not match CSV schema")
    requested_columns = tuple(usecols)
    missing_columns = [column for column in requested_columns if column not in required_columns]
    if missing_columns:
        raise AnomalyFeatureMatrixArtifactError(
            f"feature matrix parquet sidecar is missing requested columns: {missing_columns}"
        )
    parquet_name = payload.get("parquet_path")
    if not isinstance(parquet_name, str) or not parquet_name:
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar manifest has no parquet_path")
    parquet_path = manifest_path.parent / parquet_name
    if not parquet_path.is_file():
        raise AnomalyFeatureMatrixArtifactError(f"feature matrix parquet sidecar is missing: {parquet_path}")
    expected_size = payload.get("parquet_size_bytes")
    if not isinstance(expected_size, int) or expected_size < 0:
        raise AnomalyFeatureMatrixArtifactError("strategy_feature_matrix.parquet sidecar manifest has invalid size")
    actual_size = parquet_path.stat().st_size
    if actual_size != expected_size:
        raise AnomalyFeatureMatrixArtifactError(
            f"strategy_feature_matrix.parquet size mismatch: manifest={expected_size} actual={actual_size}"
        )
    row_count = payload.get("row_count")
    if not isinstance(row_count, int) or row_count < 0:
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar manifest has invalid row_count")
    _pa, pq = _import_pyarrow_for_feature_matrix_sidecar()
    parquet_file = pq.ParquetFile(parquet_path)
    parquet_columns = list(parquet_file.schema_arrow.names)
    if parquet_columns != expected_columns:
        raise AnomalyFeatureMatrixArtifactError(
            f"strategy_feature_matrix.parquet columns must match {expected_columns}, got {parquet_columns}"
        )
    actual_rows = int(parquet_file.metadata.num_rows)
    if actual_rows != row_count:
        raise AnomalyFeatureMatrixArtifactError(
            f"strategy_feature_matrix.parquet row count mismatch: manifest={row_count} actual={actual_rows}"
        )


def _validate_feature_matrix_parquet_manifest(
    *,
    payload: Mapping[str, object],
    csv_path: Path,
    manifest_path: Path,
    expected_columns: Sequence[str],
) -> None:
    if payload.get("sidecar_version") != FEATURE_MATRIX_PARQUET_SIDECAR_VERSION:
        raise AnomalyFeatureMatrixArtifactError(
            f"unsupported strategy_feature_matrix.parquet sidecar version in {manifest_path}"
        )
    if payload.get("artifact_name") != "strategy_feature_matrix.csv":
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar is bound to the wrong artifact")
    if payload.get("csv_path") != csv_path.name:
        raise AnomalyFeatureMatrixArtifactError(
            f"feature matrix parquet sidecar is bound to {payload.get('csv_path')!r}, not {csv_path.name!r}"
        )
    if tuple(payload.get("required_columns") or ()) != tuple(expected_columns):
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar schema does not match CSV schema")
    parquet_name = payload.get("parquet_path")
    if not isinstance(parquet_name, str) or not parquet_name:
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar manifest has no parquet_path")
    parquet_path = manifest_path.parent / parquet_name
    if not csv_path.is_file():
        raise AnomalyFeatureMatrixArtifactError(f"feature matrix CSV artifact is missing: {csv_path}")
    if not parquet_path.is_file():
        raise AnomalyFeatureMatrixArtifactError(f"feature matrix parquet sidecar is missing: {parquet_path}")
    _assert_bound_file(
        path=csv_path,
        expected_size=payload.get("csv_size_bytes"),
        expected_sha256=payload.get("csv_sha256"),
        label="strategy_feature_matrix.csv",
    )
    _assert_bound_file(
        path=parquet_path,
        expected_size=payload.get("parquet_size_bytes"),
        expected_sha256=payload.get("parquet_sha256"),
        label="strategy_feature_matrix.parquet",
    )
    row_count = payload.get("row_count")
    if not isinstance(row_count, int) or row_count < 0:
        raise AnomalyFeatureMatrixArtifactError("feature matrix parquet sidecar manifest has invalid row_count")


def _assert_bound_file(*, path: Path, expected_size: object, expected_sha256: object, label: str) -> None:
    if not isinstance(expected_size, int) or expected_size < 0:
        raise AnomalyFeatureMatrixArtifactError(f"{label} sidecar manifest has invalid size")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise AnomalyFeatureMatrixArtifactError(f"{label} sidecar manifest has invalid sha256")
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise AnomalyFeatureMatrixArtifactError(
            f"{label} size mismatch: manifest={expected_size} actual={actual_size}"
        )
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise AnomalyFeatureMatrixArtifactError(
            f"{label} sha256 mismatch: manifest={expected_sha256} actual={actual_sha256}"
        )


def load_strategy_feature_matrix_csv(path: str | Path) -> tuple[StrategyFeatureMatrixRow, ...]:
    """Read anomaly_feature_matrix.csv through the strict artifact schema."""
    feature_path = Path(path)
    if not feature_path.exists():
        raise AnomalyFeatureMatrixArtifactError(f"feature matrix artifact is missing: {feature_path}")

    schema = get_artifact_schema("anomaly_feature_matrix.csv")
    expected_columns = list(schema.required_columns)
    sidecar_manifest_path = _feature_matrix_parquet_manifest_path(feature_path)
    if sidecar_manifest_path.exists():
        return _load_feature_matrix_parquet_sidecar(
            csv_path=feature_path,
            manifest_path=sidecar_manifest_path,
            expected_columns=expected_columns,
        )

    with feature_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = list(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise AnomalyFeatureMatrixArtifactError(
                f"feature matrix artifact columns must match {expected_columns}, got {actual_columns}"
            )
        rows: list[StrategyFeatureMatrixRow] = []
        for row_index, row in enumerate(reader):
            try:
                rows.append(_feature_matrix_row_from_csv(row))
            except (TypeError, ValueError, MarketDataContractError) as exc:
                raise AnomalyFeatureMatrixArtifactError(
                    f"invalid anomaly_feature_matrix.csv row {row_index}: {exc}"
                ) from exc
    return tuple(rows)


load_anomaly_feature_matrix_csv = load_strategy_feature_matrix_csv



def iter_strategy_feature_matrix_csv(
    path: str | Path,
    *,
    chunksize: int = 100_000,
) -> Iterable[StrategyFeatureMatrixRow]:
    """Stream strategy_feature_matrix rows through the strict artifact boundary.

    The canonical heavy feature matrix may live in the Parquet sidecar while the
    CSV artifact is only a schema stub. This iterator validates the same
    schema/manifest contract as the tuple loader but yields rows in bounded
    pandas chunks so prediction/control stages do not materialize an additional
    full feature-matrix table before joining inputs.
    """
    feature_path = Path(path)
    if not feature_path.exists():
        raise AnomalyFeatureMatrixArtifactError(f"feature matrix artifact is missing: {feature_path}")
    if chunksize <= 0:
        raise AnomalyFeatureMatrixArtifactError("feature matrix chunksize must be positive")

    schema = get_artifact_schema("anomaly_feature_matrix.csv")
    expected_columns = list(schema.required_columns)
    row_offset = 0
    for frame in iter_strategy_feature_matrix_frame_chunks_prefer_parquet(
        path=feature_path,
        usecols=expected_columns,
        chunksize=chunksize,
    ):
        actual_columns = list(frame.columns)
        if actual_columns != expected_columns:
            raise AnomalyFeatureMatrixArtifactError(
                f"feature matrix artifact columns must match {expected_columns}, got {actual_columns}"
            )
        for row_index, row in enumerate(frame.to_dict("records"), start=row_offset):
            try:
                yield _feature_matrix_row_from_csv(row)
            except (TypeError, ValueError, MarketDataContractError) as exc:
                raise AnomalyFeatureMatrixArtifactError(
                    f"invalid anomaly_feature_matrix.csv row {row_index}: {exc}"
                ) from exc
        row_offset += len(frame)


iter_anomaly_feature_matrix_csv = iter_strategy_feature_matrix_csv

def _state_snapshot_index_from_csv(path: Path) -> tuple[dict[int, tuple[_StateSnapshotRow, ...]], int]:
    # The cross-section index needs only four state fields, so read a projection
    # of those sidecar columns instead of materializing every full state row; the
    # main feature pass still reads full validated state rows.
    raw: dict[int, list[_StateSnapshotRow]] = {}
    row_count = 0
    for row in _iter_state_snapshot_projection(Path(path)):
        row_count += 1
        raw.setdefault(row["snapshot_time_ms"], []).append(
            _StateSnapshotRow(
                symbol=row["symbol"],
                event_id=row["event_id"],
                current_return_from_start=row["current_return_from_start"],
                event_alive=row["event_alive"],
            )
        )
    return {snapshot_time_ms: tuple(rows) for snapshot_time_ms, rows in raw.items()}, row_count


_STATE_SNAPSHOT_PROJECTION_COLUMNS = (
    "snapshot_time_ms",
    "symbol",
    "event_id",
    "current_return_from_start",
    "event_alive",
)


def _iter_state_snapshot_projection(state_path: Path) -> Iterable[Mapping[str, object]]:
    expected_columns = list(get_artifact_schema("strategy_state_1m.csv").required_columns)
    if state_1m_parquet_sidecar_exists(state_path):
        yield from iter_state_1m_parquet_sidecar_mappings(
            csv_path=state_path,
            expected_columns=expected_columns,
            columns=list(_STATE_SNAPSHOT_PROJECTION_COLUMNS),
        )
        return
    for state in iter_strategy_state_1m_csv(state_path):
        yield {
            "snapshot_time_ms": state.snapshot_time_ms,
            "symbol": state.symbol,
            "event_id": state.event_id,
            "current_return_from_start": state.current_return_from_start,
            "event_alive": state.event_alive,
        }


def _symbol_groups(
    rows: Iterable[T],
    *,
    symbol_getter: Callable[[T], str],
    source_name: str,
) -> Iterable[tuple[str, list[T]]]:
    current_symbol: str | None = None
    current_rows: list[T] = []
    completed_symbols: set[str] = set()
    for row in rows:
        symbol = symbol_getter(row)
        if current_symbol is None:
            current_symbol = symbol
        if symbol != current_symbol:
            completed_symbols.add(current_symbol)
            yield current_symbol, current_rows
            current_rows = []
            current_symbol = symbol
            if current_symbol in completed_symbols:
                raise CsvDataSourceError(
                    f"{source_name} must be grouped by symbol for streaming feature-matrix build; "
                    f"symbol {current_symbol!r} appears in multiple groups"
                )
        current_rows.append(row)
    if current_symbol is not None:
        yield current_symbol, current_rows


def _load_symbol_candle_series(candles_path: Path, symbol: str, *, max_input_time_ms: int | None = None) -> _CandleSeries:
    rows = list(
        iter_candles_1m_csv(candles_path, max_open_time_ms=max_input_time_ms, symbol=symbol)
    )
    return _CandleSeries(rows)


def _iter_feature_matrix_rows_from_grouped_csv(
    *,
    candles_path: Path,
    state_path: Path,
    states_by_snapshot: Mapping[int, Sequence[StrategyState1mRow | _StateSnapshotRow]],
    open_interest_by_symbol: Mapping[str, _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    cross_section_reader: _CrossSectionFeatureSidecarReader,
    config: FeatureMatrixConfig,
    max_input_time_ms: int | None = None,
) -> Iterable[StrategyFeatureMatrixRow]:
    for raw_values in _iter_feature_matrix_value_rows_from_grouped_csv(
        candles_path=candles_path,
        state_path=state_path,
        states_by_snapshot=states_by_snapshot,
        open_interest_by_symbol=open_interest_by_symbol,
        liquidations_by_symbol=liquidations_by_symbol,
        universe_by_day=universe_by_day,
        cross_section_reader=cross_section_reader,
        config=config,
        max_input_time_ms=max_input_time_ms,
    ):
        yield StrategyFeatureMatrixRow(*raw_values)


def _iter_feature_matrix_value_rows_from_grouped_csv(
    *,
    candles_path: Path,
    state_path: Path,
    states_by_snapshot: Mapping[int, Sequence[StrategyState1mRow | _StateSnapshotRow]],
    open_interest_by_symbol: Mapping[str, _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    cross_section_reader: _CrossSectionFeatureSidecarReader,
    config: FeatureMatrixConfig,
    max_input_time_ms: int | None = None,
) -> Iterable[tuple[object, ...]]:
    btc_candles = _load_symbol_candle_series(candles_path, config.btc_symbol, max_input_time_ms=max_input_time_ms)
    candle_groups = _symbol_groups(
        iter_candles_1m_csv(candles_path, max_open_time_ms=max_input_time_ms),
        symbol_getter=lambda row: row.symbol,
        source_name="candles_1m.csv",
    )
    try:
        current_candle_symbol, current_candles = next(candle_groups)
    except StopIteration:
        current_candle_symbol, current_candles = None, []

    for state_symbol, states in _symbol_groups(
        iter_strategy_state_1m_csv(state_path),
        symbol_getter=lambda row: row.symbol,
        source_name="strategy_state_1m.csv",
    ):
        while current_candle_symbol is not None and current_candle_symbol < state_symbol:
            try:
                current_candle_symbol, current_candles = next(candle_groups)
            except StopIteration:
                current_candle_symbol, current_candles = None, []
                break
        symbol_candles = (
            _CandleSeries(current_candles)
            if current_candle_symbol == state_symbol
            else _CandleSeries(())
        )
        symbol_open_interest_rows = None if open_interest_by_symbol is None else open_interest_by_symbol.get(state_symbol, [])
        symbol_feature_index = _SymbolFeatureIndex(
            symbol=state_symbol,
            candles=symbol_candles,
            open_interest_rows=symbol_open_interest_rows,
            config=config,
        )
        candles_by_symbol: dict[str, Sequence[Candle1m]] = {state_symbol: symbol_candles}
        if config.btc_symbol != state_symbol:
            candles_by_symbol[config.btc_symbol] = btc_candles
        for state in states:
            yield _build_state_feature_row_raw_values(
                state=state,
                candles=symbol_candles,
                open_interest_rows=symbol_open_interest_rows,
                liquidation_rows=None
                if liquidations_by_symbol is None
                else liquidations_by_symbol.get(state.symbol, []),
                candles_by_symbol=candles_by_symbol,
                open_interest_by_symbol=open_interest_by_symbol,
                liquidations_by_symbol=liquidations_by_symbol,
                universe_by_day=universe_by_day,
                states_at_snapshot=states_by_snapshot.get(state.snapshot_time_ms, ()),
                cross_section_features=cross_section_reader.get(
                    snapshot_time_ms=state.snapshot_time_ms,
                    symbol=state.symbol,
                ),
                config=config,
                symbol_feature_index=symbol_feature_index,
            )


_CROSS_SECTION_SORT_CHUNK_ROWS = 200_000
_CROSS_SECTION_FEATURE_COLUMNS = (
    "volume_market_percentile",
    "quote_volume_market_percentile",
    "return_1m_market_percentile",
    "return_from_event_market_percentile",
    "oi_growth_market_percentile",
    "liq_intensity_market_percentile",
    "range_expansion_market_percentile",
    "cross_section_available",
    "cross_section_symbol_count",
)
_CROSS_SECTION_METRIC_COLUMNS = (
    "snapshot_time_ms",
    "symbol",
    "volume",
    "quote_volume",
    "return_1m",
    "range_expansion",
)
_CrossSectionMetricRow = tuple[int, str, float, float, float | None, float | None]
_CrossSectionFeatureSidecarRow = tuple[str, int, object]


class _CrossSectionFeatureSidecarReader:
    """Read exact cross-section features from a symbol-sorted sidecar.

    The hot feature-matrix loop is grouped by symbol, so this reader loads one
    symbol slice at a time and never performs per-row random SQL lookups.
    """

    def __init__(self, path: Path, *, remove_on_close: bool = True) -> None:
        self._path = path
        self._remove_on_close = remove_on_close
        self._file_obj = path.open(encoding="utf-8-sig", newline="")
        self._reader = csv.DictReader(self._file_obj)
        expected_columns = ["symbol", "snapshot_time_ms", *_CROSS_SECTION_FEATURE_COLUMNS]
        actual_columns = list(self._reader.fieldnames or [])
        if actual_columns != expected_columns:
            self._file_obj.close()
            raise CsvDataSourceError(
                f"cross-section sidecar columns must match {expected_columns}, got {actual_columns}"
            )
        self._pending: dict[str, str] | None = next(self._reader, None)
        self._loaded_symbol: str | None = None
        self._loaded_features: dict[int, _CrossSectionFeatures] = {}

    def get(self, *, snapshot_time_ms: int, symbol: str) -> _CrossSectionFeatures:
        if self._loaded_symbol != symbol:
            self._load_symbol(symbol)
        return self._loaded_features.get(snapshot_time_ms, _missing_cross_section_features(symbol_count=0))

    def close(self) -> None:
        self._file_obj.close()
        if self._remove_on_close:
            _remove_temp_file(self._path)

    def _load_symbol(self, symbol: str) -> None:
        if self._loaded_symbol is not None and symbol < self._loaded_symbol:
            raise CsvDataSourceError(
                "strategy_state_1m.csv must be grouped in non-decreasing symbol order for "
                "cross-section sidecar streaming"
            )
        self._loaded_symbol = symbol
        self._loaded_features = {}
        while self._pending is not None and str(self._pending["symbol"]) < symbol:
            self._pending = next(self._reader, None)
        while self._pending is not None and str(self._pending["symbol"]) == symbol:
            snapshot_time_ms = int(self._pending["snapshot_time_ms"])
            self._loaded_features[snapshot_time_ms] = _cross_section_features_from_sidecar_row(self._pending)
            self._pending = next(self._reader, None)


class _CrossSectionCurrentCandle:
    def __init__(self, *, quote_volume: float) -> None:
        self.quote_volume = quote_volume


def _write_cross_section_feature_sidecar_from_candles_csv(
    *,
    sidecar_path: Path,
    metrics_path: Path,
    candles_path: Path,
    snapshot_times: Sequence[int],
    open_interest_by_symbol: Mapping[str, _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    states_by_snapshot: Mapping[int, Sequence[StrategyState1mRow | _StateSnapshotRow]],
    min_cross_section_symbols: int,
    max_input_time_ms: int | None = None,
) -> None:
    _remove_temp_file(sidecar_path)
    _remove_temp_file(metrics_path)
    try:
        _write_sorted_cross_section_metric_sidecar(
            path=metrics_path,
            rows=_iter_cross_section_metric_rows_from_candles_csv(
                candles_path=candles_path,
                snapshot_times=snapshot_times,
                universe_by_day=universe_by_day,
                max_input_time_ms=max_input_time_ms,
            ),
        )
        _write_sorted_cross_section_feature_sidecar(
            path=sidecar_path,
            rows=_iter_cross_section_feature_rows_from_metric_sidecar(
                metrics_path=metrics_path,
                snapshot_times=snapshot_times,
                open_interest_by_symbol=open_interest_by_symbol,
                liquidations_by_symbol=liquidations_by_symbol,
                states_by_snapshot=states_by_snapshot,
                min_cross_section_symbols=min_cross_section_symbols,
            ),
        )
    finally:
        _remove_temp_file(metrics_path)


def _write_cross_section_feature_sidecar_from_snapshots(
    *,
    sidecar_path: Path,
    snapshot_times: Sequence[int],
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m] | _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent] | _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    states_by_snapshot: Mapping[int, Sequence[StrategyState1mRow | _StateSnapshotRow]],
    min_cross_section_symbols: int,
) -> None:
    _remove_temp_file(sidecar_path)
    _write_sorted_cross_section_feature_sidecar(
        path=sidecar_path,
        rows=_iter_cross_section_feature_rows_from_snapshots(
            snapshot_times=snapshot_times,
            candles_by_symbol=candles_by_symbol,
            open_interest_by_symbol=open_interest_by_symbol,
            liquidations_by_symbol=liquidations_by_symbol,
            universe_by_day=universe_by_day,
            states_by_snapshot=states_by_snapshot,
            min_cross_section_symbols=min_cross_section_symbols,
        ),
    )


def _iter_cross_section_metric_rows_from_candles_csv(
    *,
    candles_path: Path,
    snapshot_times: Sequence[int],
    universe_by_day: Mapping[str, set[str]],
    max_input_time_ms: int | None,
) -> Iterable[_CrossSectionMetricRow]:
    snapshot_time_set = set(snapshot_times)
    previous_close_by_symbol: dict[str, float] = {}
    for candle in iter_candles_1m_csv(candles_path, max_open_time_ms=max_input_time_ms):
        previous_close = previous_close_by_symbol.get(candle.symbol)
        previous_close_by_symbol[candle.symbol] = candle.close
        if candle.available_time_ms not in snapshot_time_set:
            continue
        trade_date = utc_ms_to_datetime(candle.available_time_ms).date().isoformat()
        universe_symbols = universe_by_day.get(trade_date, set())
        if not universe_symbols or candle.symbol not in universe_symbols:
            continue
        return_1m = None if previous_close is None or previous_close <= 0 else candle.close / previous_close - 1.0
        range_expansion = None if candle.close <= 0 else (candle.high - candle.low) / candle.close
        yield (
            candle.available_time_ms,
            candle.symbol,
            candle.volume,
            candle.quote_volume,
            return_1m,
            range_expansion,
        )


def _iter_cross_section_feature_rows_from_metric_sidecar(
    *,
    metrics_path: Path,
    snapshot_times: Sequence[int],
    open_interest_by_symbol: Mapping[str, _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, _LiquidationSeries] | None,
    states_by_snapshot: Mapping[int, Sequence[StrategyState1mRow | _StateSnapshotRow]],
    min_cross_section_symbols: int,
) -> Iterable[_CrossSectionFeatureSidecarRow]:
    metric_iter = iter(_iter_cross_section_metric_sidecar(metrics_path))
    pending_row = next(metric_iter, None)
    for snapshot_time_ms in snapshot_times:
        states_at_snapshot = states_by_snapshot.get(snapshot_time_ms, ())
        target_symbols = {row.symbol for row in states_at_snapshot}
        rows: list[_CrossSectionMetricRow] = []
        while pending_row is not None and pending_row[0] < snapshot_time_ms:
            pending_row = next(metric_iter, None)
        while pending_row is not None and pending_row[0] == snapshot_time_ms:
            rows.append(pending_row)
            pending_row = next(metric_iter, None)
        yield from _cross_section_feature_rows_for_metric_rows(
            snapshot_time_ms=snapshot_time_ms,
            rows=rows,
            target_symbols=target_symbols,
            states_at_snapshot=states_at_snapshot,
            open_interest_by_symbol=open_interest_by_symbol,
            liquidations_by_symbol=liquidations_by_symbol,
            min_cross_section_symbols=min_cross_section_symbols,
        )


def _iter_cross_section_feature_rows_from_snapshots(
    *,
    snapshot_times: Sequence[int],
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m] | _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent] | _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    states_by_snapshot: Mapping[int, Sequence[StrategyState1mRow | _StateSnapshotRow]],
    min_cross_section_symbols: int,
) -> Iterable[_CrossSectionFeatureSidecarRow]:
    for snapshot_time_ms in snapshot_times:
        states_at_snapshot = states_by_snapshot.get(snapshot_time_ms, ())
        by_symbol, missing = _cross_section_features_by_symbol(
            snapshot_time_ms=snapshot_time_ms,
            candles_by_symbol=candles_by_symbol,
            open_interest_by_symbol=open_interest_by_symbol,
            liquidations_by_symbol=liquidations_by_symbol,
            universe_by_day=universe_by_day,
            states_at_snapshot=states_at_snapshot,
            min_cross_section_symbols=min_cross_section_symbols,
            target_symbols={row.symbol for row in states_at_snapshot},
        )
        for symbol in sorted({row.symbol for row in states_at_snapshot}):
            yield symbol, snapshot_time_ms, by_symbol.get(symbol, missing)


def _cross_section_feature_rows_for_metric_rows(
    *,
    snapshot_time_ms: int,
    rows: Sequence[_CrossSectionMetricRow],
    target_symbols: set[str],
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    open_interest_by_symbol: Mapping[str, _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, _LiquidationSeries] | None,
    min_cross_section_symbols: int,
) -> Iterable[_CrossSectionFeatureSidecarRow]:
    symbols_with_current_candle = len(rows)
    missing = _missing_cross_section_features(symbol_count=symbols_with_current_candle)
    if symbols_with_current_candle < min_cross_section_symbols:
        for symbol in sorted(target_symbols):
            yield symbol, snapshot_time_ms, missing
        return

    metric_values: dict[str, dict[str, float]] = {
        "volume": {},
        "quote_volume": {},
        "return_1m": {},
        "oi_growth": {},
        "liq_intensity": {},
        "range_expansion": {},
    }
    for _snapshot_time_ms, symbol, volume, quote_volume, return_1m, range_expansion in rows:
        metric_values["volume"][symbol] = volume
        metric_values["quote_volume"][symbol] = quote_volume
        if return_1m is not None:
            metric_values["return_1m"][symbol] = return_1m
        if range_expansion is not None:
            metric_values["range_expansion"][symbol] = range_expansion
        if open_interest_by_symbol is not None:
            oi = _oi_features(
                open_interest_rows=open_interest_by_symbol.get(symbol, ()),
                snapshot_time_ms=snapshot_time_ms,
            )
            if oi.oi_change_5m_pct_of_oi is not None:
                metric_values["oi_growth"][symbol] = oi.oi_change_5m_pct_of_oi
        if liquidations_by_symbol is not None:
            liq_intensity = _minute_liq_intensity(
                current=_CrossSectionCurrentCandle(quote_volume=quote_volume),
                liquidation_rows=liquidations_by_symbol.get(symbol, ()),
                snapshot_time_ms=snapshot_time_ms,
            )
            if liq_intensity is not None:
                metric_values["liq_intensity"][symbol] = liq_intensity

    ranks_by_metric = {
        metric_name: _rank_percentiles_by_symbol(values)
        for metric_name, values in metric_values.items()
    }
    return_from_event_ranks = _return_from_event_percentiles_by_symbol(
        states_at_snapshot=states_at_snapshot,
        min_cross_section_symbols=min_cross_section_symbols,
    )
    for symbol in sorted(target_symbols):
        yield symbol, snapshot_time_ms, _CrossSectionFeatures(
            volume_market_percentile=ranks_by_metric["volume"].get(symbol),
            quote_volume_market_percentile=ranks_by_metric["quote_volume"].get(symbol),
            return_1m_market_percentile=ranks_by_metric["return_1m"].get(symbol),
            return_from_event_market_percentile=return_from_event_ranks.get(symbol),
            oi_growth_market_percentile=ranks_by_metric["oi_growth"].get(symbol),
            liq_intensity_market_percentile=ranks_by_metric["liq_intensity"].get(symbol),
            range_expansion_market_percentile=ranks_by_metric["range_expansion"].get(symbol),
            cross_section_available=True,
            cross_section_symbol_count=symbols_with_current_candle,
        )


def _write_sorted_cross_section_metric_sidecar(
    *,
    path: Path,
    rows: Iterable[_CrossSectionMetricRow],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chunk_paths: list[Path] = []
    batch: list[_CrossSectionMetricRow] = []
    try:
        for row in rows:
            batch.append(row)
            if len(batch) >= _CROSS_SECTION_SORT_CHUNK_ROWS:
                chunk_paths.append(_write_cross_section_metric_chunk(path=path, chunk_index=len(chunk_paths), rows=batch))
                batch = []
        if not chunk_paths:
            batch.sort(key=lambda item: (item[0], item[1]))
            _write_cross_section_metric_rows(path=path, rows=batch)
            return
        if batch:
            chunk_paths.append(_write_cross_section_metric_chunk(path=path, chunk_index=len(chunk_paths), rows=batch))
        merged = heapq.merge(
            *(_iter_cross_section_metric_sidecar(chunk_path) for chunk_path in chunk_paths),
            key=lambda item: (item[0], item[1]),
        )
        _write_cross_section_metric_rows(path=path, rows=merged)
    finally:
        for chunk_path in chunk_paths:
            _remove_temp_file(chunk_path)


def _write_sorted_cross_section_feature_sidecar(
    *,
    path: Path,
    rows: Iterable[_CrossSectionFeatureSidecarRow],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chunk_paths: list[Path] = []
    batch: list[_CrossSectionFeatureSidecarRow] = []
    try:
        for row in rows:
            batch.append(row)
            if len(batch) >= _CROSS_SECTION_SORT_CHUNK_ROWS:
                chunk_paths.append(_write_cross_section_feature_chunk(path=path, chunk_index=len(chunk_paths), rows=batch))
                batch = []
        if not chunk_paths:
            batch.sort(key=lambda item: (item[0], item[1]))
            _write_cross_section_feature_rows(path=path, rows=batch)
            return
        if batch:
            chunk_paths.append(_write_cross_section_feature_chunk(path=path, chunk_index=len(chunk_paths), rows=batch))
        merged = heapq.merge(
            *(_iter_cross_section_feature_sidecar(chunk_path) for chunk_path in chunk_paths),
            key=lambda item: (item[0], item[1]),
        )
        _write_cross_section_feature_rows(path=path, rows=merged)
    finally:
        for chunk_path in chunk_paths:
            _remove_temp_file(chunk_path)


def _write_cross_section_metric_chunk(
    *,
    path: Path,
    chunk_index: int,
    rows: list[_CrossSectionMetricRow],
) -> Path:
    rows.sort(key=lambda item: (item[0], item[1]))
    chunk_path = _cross_section_chunk_path(path=path, chunk_index=chunk_index)
    _write_cross_section_metric_rows(path=chunk_path, rows=rows)
    return chunk_path


def _write_cross_section_feature_chunk(
    *,
    path: Path,
    chunk_index: int,
    rows: list[_CrossSectionFeatureSidecarRow],
) -> Path:
    rows.sort(key=lambda item: (item[0], item[1]))
    chunk_path = _cross_section_chunk_path(path=path, chunk_index=chunk_index)
    _write_cross_section_feature_rows(path=chunk_path, rows=rows)
    return chunk_path


def _cross_section_chunk_path(*, path: Path, chunk_index: int) -> Path:
    return path.with_name(f"{path.name}.chunk_{chunk_index:06d}.tmp")


def _write_cross_section_metric_rows(
    *,
    path: Path,
    rows: Iterable[_CrossSectionMetricRow],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.writer(file_obj)
        for row in rows:
            writer.writerow([
                row[0],
                row[1],
                row[2],
                row[3],
                "" if row[4] is None else row[4],
                "" if row[5] is None else row[5],
            ])


def _write_cross_section_feature_rows(
    *,
    path: Path,
    rows: Iterable[_CrossSectionFeatureSidecarRow],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(["symbol", "snapshot_time_ms", *_CROSS_SECTION_FEATURE_COLUMNS])
        for symbol, snapshot_time_ms, features in rows:
            writer.writerow([symbol, snapshot_time_ms, *_cross_section_feature_values(features)])


def _iter_cross_section_metric_sidecar(path: Path) -> Iterable[_CrossSectionMetricRow]:
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as file_obj:
        reader = csv.reader(file_obj)
        for row in reader:
            if not row:
                continue
            yield (
                int(row[0]),
                row[1],
                float(row[2]),
                float(row[3]),
                None if row[4] == "" else float(row[4]),
                None if row[5] == "" else float(row[5]),
            )


def _iter_cross_section_feature_sidecar(path: Path) -> Iterable[_CrossSectionFeatureSidecarRow]:
    with path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        for row in reader:
            yield row["symbol"], int(row["snapshot_time_ms"]), _cross_section_features_from_sidecar_row(row)


def _cross_section_feature_values(features: _CrossSectionFeatures) -> tuple[object, ...]:
    return (
        features.volume_market_percentile if features.volume_market_percentile is not None else "",
        features.quote_volume_market_percentile if features.quote_volume_market_percentile is not None else "",
        features.return_1m_market_percentile if features.return_1m_market_percentile is not None else "",
        features.return_from_event_market_percentile if features.return_from_event_market_percentile is not None else "",
        features.oi_growth_market_percentile if features.oi_growth_market_percentile is not None else "",
        features.liq_intensity_market_percentile if features.liq_intensity_market_percentile is not None else "",
        features.range_expansion_market_percentile if features.range_expansion_market_percentile is not None else "",
        1 if features.cross_section_available else 0,
        features.cross_section_symbol_count,
    )


def _cross_section_features_from_sidecar_row(row: Mapping[str, object]) -> _CrossSectionFeatures:
    return _CrossSectionFeatures(
        volume_market_percentile=_optional_sidecar_float(row["volume_market_percentile"]),
        quote_volume_market_percentile=_optional_sidecar_float(row["quote_volume_market_percentile"]),
        return_1m_market_percentile=_optional_sidecar_float(row["return_1m_market_percentile"]),
        return_from_event_market_percentile=_optional_sidecar_float(row["return_from_event_market_percentile"]),
        oi_growth_market_percentile=_optional_sidecar_float(row["oi_growth_market_percentile"]),
        liq_intensity_market_percentile=_optional_sidecar_float(row["liq_intensity_market_percentile"]),
        range_expansion_market_percentile=_optional_sidecar_float(row["range_expansion_market_percentile"]),
        cross_section_available=_sidecar_bool(row["cross_section_available"]),
        cross_section_symbol_count=int(row["cross_section_symbol_count"]),
    )


def _optional_sidecar_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _sidecar_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value) in {"1", "True", "true"}


def _remove_temp_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _feature_matrix_row_from_csv(row: Mapping[str, object]) -> StrategyFeatureMatrixRow:
    return StrategyFeatureMatrixRow(
        feature_schema_version=_required_str(row, "feature_schema_version"),
        feature_matrix_version=_required_str(row, "feature_matrix_version"),
        event_id=_required_str(row, "event_id"),
        symbol=_required_str(row, "symbol"),
        snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
        feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
        minutes_since_trigger=_required_int(row, "minutes_since_trigger"),
        core_atr_1440=_optional_float(row, "ATR_1d_asof_t"),
        ATR_1d_pct_asof_t=_optional_float(row, "ATR_1d_pct_asof_t"),
        current_return_from_start=_required_float(row, "current_return_from_start"),
        range_since_start_atr=_optional_float(row, "range_since_start_atr"),
        distance_to_running_high_atr=_optional_float(row, "distance_to_running_high_atr"),
        distance_to_running_low_atr=_optional_float(row, "distance_to_running_low_atr"),
        retracement_from_high_atr=_optional_float(row, "retracement_from_high_atr"),
        price_speed_atr=_optional_float(row, "price_speed_atr"),
        clock_maturity=_required_float(row, "clock_maturity"),
        event_age_ratio=_required_float(row, "event_age_ratio"),
        alpha_decay_bucket=_required_str(row, "alpha_decay_bucket"),
        feature_source_status=_required_str(row, "feature_source_status"),
        quote_volume_1m_to_24h_median=_optional_float(row, "quote_volume_1m_to_24h_median"),
        volume_zscore=_optional_float(row, "volume_zscore"),
        quote_volume_zscore=_optional_float(row, "quote_volume_zscore"),
        closed_5m_oi_asof_t=_optional_float(row, "closed_5m_oi_asof_t"),
        oi_change_5m=_optional_float(row, "oi_change_5m"),
        oi_change_10m=_optional_float(row, "oi_change_10m"),
        oi_change_5m_pct_of_oi=_optional_float(row, "oi_change_5m_pct_of_oi"),
        oi_change_10m_pct_of_oi=_optional_float(row, "oi_change_10m_pct_of_oi"),
        missing_oi_flag=_required_bool(row, "missing_oi_flag"),
        short_liq_intensity=_optional_float(row, "short_liq_intensity"),
        long_liq_intensity=_optional_float(row, "long_liq_intensity"),
        liquidation_imbalance=_optional_float(row, "liquidation_imbalance"),
        cumulative_liq_intensity_since_event_start=_optional_float(row, "cumulative_liq_intensity_since_event_start"),
        missing_liquidation_flag=_required_bool(row, "missing_liquidation_flag"),
        cvd_quote_since_event_start=_optional_float(row, "cvd_quote_since_event_start"),
        cvd_change_3m=_optional_float(row, "cvd_change_3m"),
        cvd_change_5m=_optional_float(row, "cvd_change_5m"),
        cvd_change_10m=_optional_float(row, "cvd_change_10m"),
        cvd_price_divergence_3m=_optional_float(row, "cvd_price_divergence_3m"),
        cvd_price_divergence_5m=_optional_float(row, "cvd_price_divergence_5m"),
        cvd_price_divergence_10m=_optional_float(row, "cvd_price_divergence_10m"),
        price_up_cvd_down_flag=_required_bool(row, "price_up_cvd_down_flag"),
        price_down_cvd_up_flag=_required_bool(row, "price_down_cvd_up_flag"),
        cvd_failed_to_confirm_high_flag=_required_bool(row, "cvd_failed_to_confirm_high_flag"),
        volume_market_percentile=_optional_float(row, "volume_market_percentile"),
        quote_volume_market_percentile=_optional_float(row, "quote_volume_market_percentile"),
        return_1m_market_percentile=_optional_float(row, "return_1m_market_percentile"),
        return_from_event_market_percentile=_optional_float(row, "return_from_event_market_percentile"),
        oi_growth_market_percentile=_optional_float(row, "oi_growth_market_percentile"),
        liq_intensity_market_percentile=_optional_float(row, "liq_intensity_market_percentile"),
        range_expansion_market_percentile=_optional_float(row, "range_expansion_market_percentile"),
        cross_section_available=_required_bool(row, "cross_section_available"),
        cross_section_symbol_count=_required_int(row, "cross_section_symbol_count"),
        corr_with_btc_15m=_optional_float(row, "corr_with_btc_15m"),
        corr_with_btc_30m=_optional_float(row, "corr_with_btc_30m"),
        corr_with_btc_60m=_optional_float(row, "corr_with_btc_60m"),
        symbol_return_minus_btc_return_5m=_optional_float(row, "symbol_return_minus_btc_return_5m"),
        symbol_return_minus_btc_return_15m=_optional_float(row, "symbol_return_minus_btc_return_15m"),
        idiosyncratic_momentum_score=_optional_float(row, "idiosyncratic_momentum_score"),
        simultaneous_anomalies_count_1m=_required_int(row, "simultaneous_anomalies_count_1m"),
        simultaneous_anomalies_share_1m=_optional_float(row, "simultaneous_anomalies_share_1m"),
        systemic_cluster_regime=_required_str(row, "systemic_cluster_regime"),
        market_shock_id=_required_str(row, "market_shock_id"),
        initial_pump_height_core_atr_1440=_optional_float(row, "initial_pump_height_core_atr_1440"),
        post_pump_consolidation_minutes=_optional_int(row, "post_pump_consolidation_minutes"),
        consolidation_width_ratio=_optional_float(row, "consolidation_width_ratio"),
        shelf_low_asof_t=_optional_float(row, "shelf_low_asof_t"),
        shelf_high_asof_t=_optional_float(row, "shelf_high_asof_t"),
        current_low_minus_shelf_low_core_atr_1440=_optional_float(row, "current_low_minus_shelf_low_core_atr_1440"),
        current_close_minus_shelf_low_core_atr_1440=_optional_float(row, "current_close_minus_shelf_low_core_atr_1440"),
        current_high_minus_shelf_high_core_atr_1440=_optional_float(row, "current_high_minus_shelf_high_core_atr_1440"),
        minutes_spent_below_shelf=_optional_int(row, "minutes_spent_below_shelf"),
        minutes_since_reclaim=_optional_int(row, "minutes_since_reclaim"),
        volume_on_sweep_percentile=_optional_float(row, "volume_on_sweep_percentile"),
        trade_count_on_sweep_percentile=_optional_float(row, "trade_count_on_sweep_percentile"),
        cvd_change_during_sweep=_optional_float(row, "cvd_change_during_sweep"),
        oi_change_during_sweep=_optional_float(row, "oi_change_during_sweep"),
        liq_intensity_during_sweep=_optional_float(row, "liq_intensity_during_sweep"),
    )


def run_mvp1_feature_matrix(
    *,
    input_dir: str | Path,
    state_path: str | Path,
    out_dir: str | Path,
    config: FeatureMatrixConfig | None = None,
    max_input_time_ms: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    input_path = Path(input_dir)
    state_artifact_path = Path(state_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or FeatureMatrixConfig()

    source = CsvDirectoryDataSource(input_path, max_time_ms=max_input_time_ms)
    states_by_snapshot, state_row_count = _state_snapshot_index_from_csv(state_artifact_path)
    oi_frame = source.read_frame("open_interest_5m", required=False)
    liquidation_frame = source.read_frame("liquidations", required=False)
    universe_frame = source.read_frame("symbol_universe_by_day", required=False)
    oi_by_symbol: dict[str, _OpenInterestSeries] | None = None
    if oi_frame is not None:
        raw_oi_by_symbol: dict[str, list[OpenInterest5m]] = {}
        for item in normalize_open_interest_5m(oi_frame):
            raw_oi_by_symbol.setdefault(item.symbol, []).append(item)
        oi_by_symbol = {symbol: _OpenInterestSeries(rows) for symbol, rows in raw_oi_by_symbol.items()}
    liquidations_by_symbol: dict[str, _LiquidationSeries] | None = None
    if liquidation_frame is not None:
        raw_liquidations_by_symbol: dict[str, list[LiquidationEvent]] = {}
        for item in normalize_liquidations(liquidation_frame):
            raw_liquidations_by_symbol.setdefault(item.symbol, []).append(item)
        liquidations_by_symbol = {
            symbol: _LiquidationSeries(rows)
            for symbol, rows in raw_liquidations_by_symbol.items()
        }
    universe_by_day = _universe_symbols_by_day(
        None if universe_frame is None else normalize_symbol_universe_by_day(universe_frame)
    )
    cross_section_sidecar_path = output_path / "strategy_feature_matrix.cross_section_features.csv.tmp"
    cross_section_metrics_path = output_path / "strategy_feature_matrix.cross_section_metrics.csv.tmp"
    if progress_callback is not None:
        progress_callback(
            ProgressUpdate(
                done=0,
                total=state_row_count,
                unit="rows",
                detail="building cross-section sidecar",
                force=True,
            )
        )
    _write_cross_section_feature_sidecar_from_candles_csv(
        sidecar_path=cross_section_sidecar_path,
        metrics_path=cross_section_metrics_path,
        candles_path=input_path / "candles_1m.csv",
        snapshot_times=sorted(states_by_snapshot),
        open_interest_by_symbol=oi_by_symbol,
        liquidations_by_symbol=liquidations_by_symbol,
        universe_by_day=universe_by_day,
        states_by_snapshot=states_by_snapshot,
        min_cross_section_symbols=cfg.min_cross_section_symbols,
        max_input_time_ms=max_input_time_ms,
    )
    if progress_callback is not None:
        progress_callback(
            ProgressUpdate(
                done=0,
                total=state_row_count,
                unit="rows",
                detail="writing parquet feature matrix",
                force=True,
            )
        )
    cross_section_reader = _CrossSectionFeatureSidecarReader(cross_section_sidecar_path, remove_on_close=True)
    try:
        written: list[Path] = []
        feature_written, feature_row_count = _write_feature_matrix_value_rows_with_aliases(
            output_path / "strategy_feature_matrix.csv",
            _iter_feature_matrix_value_rows_from_grouped_csv(
                candles_path=input_path / "candles_1m.csv",
                state_path=state_artifact_path,
                states_by_snapshot=states_by_snapshot,
                open_interest_by_symbol=oi_by_symbol,
                liquidations_by_symbol=liquidations_by_symbol,
                universe_by_day=universe_by_day,
                cross_section_reader=cross_section_reader,
                config=cfg,
                max_input_time_ms=max_input_time_ms,
            ),
            get_artifact_schema("strategy_feature_matrix.csv"),
            expected_row_count=state_row_count,
            progress_callback=progress_callback,
        )
    finally:
        cross_section_reader.close()
    written.extend(feature_written)
    protocol_rows = _protocol_rows(state_row_count=state_row_count, feature_row_count=feature_row_count)
    run_config_rows = _run_config_rows(
        input_path=input_path,
        state_path=state_artifact_path,
        output_path=output_path,
        config=cfg,
        max_input_time_ms=max_input_time_ms,
    )

    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_feature_catalog.csv",
            feature_rows_to_artifact(build_default_feature_catalog()),
            get_artifact_schema("strategy_feature_catalog.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("strategy_protocol_audit.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("strategy_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _write_feature_matrix_rows_with_aliases(
    path: Path,
    rows: Iterable[StrategyFeatureMatrixRow],
    schema: ArtifactSchema,
) -> tuple[list[Path], int]:
    return _write_feature_matrix_artifact_with_aliases(
        path=path,
        row_count_writer=lambda canonical_path: _write_feature_matrix_rows(path=canonical_path, rows=rows, schema=schema),
        schema=schema,
    )


def _write_feature_matrix_value_rows_with_aliases(
    path: Path,
    rows: Iterable[Sequence[object]],
    schema: ArtifactSchema,
    expected_row_count: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[Path], int]:
    sidecar_paths: list[Path] = []

    def _write_canonical_with_sidecar(canonical_path: Path) -> int:
        row_count, written_sidecars = _write_feature_matrix_value_rows(
            path=canonical_path,
            rows=rows,
            schema=schema,
            write_parquet_sidecar=True,
            csv_delivery="schema_header_only",
            expected_row_count=expected_row_count,
            progress_callback=progress_callback,
        )
        sidecar_paths.extend(written_sidecars)
        return row_count

    written, row_count = _write_feature_matrix_artifact_with_aliases(
        path=path,
        row_count_writer=_write_canonical_with_sidecar,
        schema=schema,
    )
    return [*written, *sidecar_paths], row_count


def _write_feature_matrix_artifact_with_aliases(
    *,
    path: Path,
    row_count_writer: Callable[[Path], int],
    schema: ArtifactSchema,
) -> tuple[list[Path], int]:
    if path.name != schema.name:
        raise ArtifactWriteError(f"path name {path.name!r} does not match schema name {schema.name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = row_count_writer(path)
    written = [path]
    for alias_name in get_strategy_artifact_companion_names(schema.name):
        if not should_materialize_strategy_artifact_alias(schema.name, alias_name):
            continue
        alias_path = path.with_name(alias_name)
        alias_schema = get_artifact_schema(alias_name)
        if tuple(alias_schema.required_columns) != tuple(schema.required_columns):
            raise ArtifactWriteError(f"{schema.name} streaming alias {alias_name} must have identical columns")
        link_or_copy_identical_artifact(path, alias_path)
        written.append(alias_path)
    return written, row_count


def _write_feature_matrix_rows(
    *,
    path: Path,
    rows: Iterable[StrategyFeatureMatrixRow],
    schema: ArtifactSchema,
) -> int:
    fieldnames = list(schema.required_columns)
    _validate_feature_matrix_fieldnames(fieldnames)
    attribute_names = [_feature_matrix_row_attribute_name(fieldname) for fieldname in fieldnames]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    row_count = 0
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow(_feature_matrix_row_values_for_attributes(row=row, attribute_names=attribute_names))
            row_count += 1
            if row_count % 100_000 == 0:
                file_obj.flush()
    os.replace(tmp_path, path)
    return row_count


def _write_feature_matrix_value_rows(
    *,
    path: Path,
    rows: Iterable[Sequence[object]],
    schema: ArtifactSchema,
    write_parquet_sidecar: bool = False,
    csv_delivery: str = "full",
    expected_row_count: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[int, list[Path]]:
    fieldnames = list(schema.required_columns)
    _validate_direct_feature_matrix_fieldnames(fieldnames)
    expected_value_count = len(fieldnames)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    row_count = 0
    if csv_delivery not in {"full", "schema_header_only"}:
        raise ArtifactWriteError(f"unknown strategy_feature_matrix.csv delivery mode: {csv_delivery!r}")
    if csv_delivery == "schema_header_only" and not write_parquet_sidecar:
        raise ArtifactWriteError("schema_header_only feature-matrix CSV delivery requires the Parquet sidecar")
    sidecar_writer = (
        _FeatureMatrixParquetSidecarWriter(
            csv_path=path,
            fieldnames=fieldnames,
            csv_delivery=csv_delivery,
        )
        if write_parquet_sidecar
        else None
    )
    if progress_callback is not None:
        progress_callback(
            ProgressUpdate(
                done=0,
                total=expected_row_count,
                unit="rows",
                detail="writing feature matrix",
                force=True,
            )
        )
    try:
        with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.writer(file_obj)
            writer.writerow(fieldnames)
            if csv_delivery == "full":
                for row_values in rows:
                    if len(row_values) != expected_value_count:
                        raise ArtifactWriteError(
                            f"strategy_feature_matrix.csv direct row has {len(row_values)} values, "
                            f"expected {expected_value_count}"
                        )
                    writer.writerow([_csv_value(value) for value in row_values])
                    if sidecar_writer is not None:
                        sidecar_writer.append(row_values)
                    row_count += 1
                    if row_count % 100_000 == 0:
                        file_obj.flush()
                        if progress_callback is not None:
                            progress_callback(
                                ProgressUpdate(
                                    done=row_count,
                                    total=expected_row_count,
                                    unit="rows",
                                    detail="writing feature matrix",
                                )
                            )
            else:
                for row_values in rows:
                    if len(row_values) != expected_value_count:
                        raise ArtifactWriteError(
                            f"strategy_feature_matrix.csv direct row has {len(row_values)} values, "
                            f"expected {expected_value_count}"
                        )
                    if sidecar_writer is None:
                        raise ArtifactWriteError("missing Parquet sidecar writer for schema_header_only delivery")
                    sidecar_writer.append(row_values)
                    row_count += 1
                    if progress_callback is not None and row_count % 100_000 == 0:
                        progress_callback(
                            ProgressUpdate(
                                done=row_count,
                                total=expected_row_count,
                                unit="rows",
                                detail="writing feature matrix",
                            )
                        )
        os.replace(tmp_path, path)
        sidecar_paths: list[Path] = []
        if sidecar_writer is not None:
            sidecar_paths.extend(sidecar_writer.close())
        if progress_callback is not None:
            progress_callback(
                ProgressUpdate(
                    done=row_count,
                    total=expected_row_count,
                    unit="rows",
                    detail="feature matrix written",
                    force=True,
                )
            )
        return row_count, sidecar_paths
    except Exception:
        if sidecar_writer is not None:
            try:
                sidecar_writer._writer.close()
            except Exception:
                pass
            _remove_temp_file(sidecar_writer.tmp_parquet_path)
        _remove_temp_file(tmp_path)
        if csv_delivery == "schema_header_only":
            _remove_temp_file(path)
        raise


def _feature_matrix_row_values_for_attributes(*, row: StrategyFeatureMatrixRow, attribute_names: Sequence[str]) -> list[object]:
    return [_csv_value(getattr(row, attribute_name)) for attribute_name in attribute_names]


def _build_state_feature_row(
    *,
    state: StrategyState1mRow,
    candles: Sequence[Candle1m],
    open_interest_rows: Sequence[OpenInterest5m] | _OpenInterestSeries | None,
    liquidation_rows: Sequence[LiquidationEvent] | _LiquidationSeries | None,
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m] | _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent] | _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    config: FeatureMatrixConfig,
    cross_section_features: _CrossSectionFeatures | None = None,
    symbol_feature_index: _SymbolFeatureIndex | None = None,
) -> StrategyFeatureMatrixRow:
    return StrategyFeatureMatrixRow(
        *_build_state_feature_row_raw_values(
            state=state,
            candles=candles,
            open_interest_rows=open_interest_rows,
            liquidation_rows=liquidation_rows,
            candles_by_symbol=candles_by_symbol,
            open_interest_by_symbol=open_interest_by_symbol,
            liquidations_by_symbol=liquidations_by_symbol,
            universe_by_day=universe_by_day,
            states_at_snapshot=states_at_snapshot,
            config=config,
            cross_section_features=cross_section_features,
            symbol_feature_index=symbol_feature_index,
        )
    )


def _build_state_feature_row_raw_values(
    *,
    state: StrategyState1mRow,
    candles: Sequence[Candle1m],
    open_interest_rows: Sequence[OpenInterest5m] | _OpenInterestSeries | None,
    liquidation_rows: Sequence[LiquidationEvent] | _LiquidationSeries | None,
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m] | _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent] | _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    config: FeatureMatrixConfig,
    cross_section_features: _CrossSectionFeatures | None = None,
    symbol_feature_index: _SymbolFeatureIndex | None = None,
) -> tuple[object, ...]:
    atr_value: float | None = None
    atr_pct_value: float | None = None
    range_since_start_atr: float | None = None
    distance_to_running_high_atr: float | None = None
    distance_to_running_low_atr: float | None = None
    retracement_from_high_atr: float | None = None
    price_speed_atr: float | None = None
    status = "ok"

    if symbol_feature_index is not None:
        atr_speed_features = symbol_feature_index.atr_speed_features(snapshot_time_ms=state.snapshot_time_ms)
        atr_value = atr_speed_features.atr_value
        atr_pct_value = atr_speed_features.atr_pct_value
        price_speed_atr = atr_speed_features.price_speed_atr
        status = atr_speed_features.status
    else:
        try:
            if isinstance(candles, _CandleSeries):
                atr_value, atr_pct_value = candles.atr_asof(
                    symbol=state.symbol,
                    snapshot_time_ms=state.snapshot_time_ms,
                    atr_window_minutes=config.atr_window_minutes,
                )
            else:
                atr = compute_atr_1d_asof(
                    candles_1m=candles,
                    symbol=state.symbol,
                    snapshot_time_ms=state.snapshot_time_ms,
                    atr_window_minutes=config.atr_window_minutes,
                )
                atr_value = atr.core_atr_1440
                atr_pct_value = atr.atr_1d_pct_asof_t
        except AtrComputationError:
            status = "insufficient_atr_history"

        if atr_value is not None:
            price_speed_atr = _price_speed_atr(
                candles=candles,
                snapshot_time_ms=state.snapshot_time_ms,
                atr_value=atr_value,
                atr_window_minutes=config.atr_window_minutes,
            )
            if price_speed_atr is None and status == "ok":
                status = "missing_previous_close_for_speed"

    if atr_value is not None:
        range_since_start_atr = (state.running_high_asof_t - state.running_low_asof_t) / atr_value
        distance_to_running_high_atr = max(state.running_high_asof_t - state.current_close, 0.0) / atr_value
        distance_to_running_low_atr = max(state.current_close - state.running_low_asof_t, 0.0) / atr_value
        retracement_from_high_atr = distance_to_running_high_atr

    if symbol_feature_index is not None:
        volume_features = symbol_feature_index.volume_features(state=state)
        oi_features = symbol_feature_index.oi_features(snapshot_time_ms=state.snapshot_time_ms)
    else:
        volume_features = _volume_features(candles=candles, state=state, config=config)
        oi_features = _oi_features(open_interest_rows=open_interest_rows, snapshot_time_ms=state.snapshot_time_ms)
    liquidation_features = _liquidation_features(
        candles=candles,
        liquidation_rows=liquidation_rows,
        state=state,
    )
    cvd_features = _cvd_features(
        candles=candles,
        state=state,
        atr_value=atr_value,
        windows_minutes=config.cvd_windows_minutes,
    )
    if cross_section_features is None:
        cross_section_features = _cross_section_features(
            state=state,
            candles_by_symbol=candles_by_symbol,
            open_interest_by_symbol=open_interest_by_symbol,
            liquidations_by_symbol=liquidations_by_symbol,
            universe_by_day=universe_by_day,
            states_at_snapshot=states_at_snapshot,
            min_cross_section_symbols=config.min_cross_section_symbols,
        )
    market_context_features = _market_context_features(
        state=state,
        symbol_candles=candles,
        btc_candles=candles_by_symbol.get(config.btc_symbol, ()),
        states_at_snapshot=states_at_snapshot,
        cross_section_symbol_count=cross_section_features.cross_section_symbol_count,
        volume_market_percentile=cross_section_features.volume_market_percentile,
        ATR_1d_pct_asof_t=atr_pct_value,
        corr_windows_minutes=config.btc_corr_window_minutes,
        return_windows_minutes=config.btc_relative_return_windows_minutes,
        moderate_cluster_min_count=config.moderate_cluster_min_count,
        systemic_cluster_min_count=config.systemic_cluster_min_count,
    )
    geometry_features = _relaxed_geometry_features(
        candles=candles,
        state=state,
        atr_value=atr_value,
        oi_features=oi_features,
        liquidation_features=liquidation_features,
    )

    time_to_running_high = max(state.minutes_since_event_start - state.time_since_running_high_minutes, 0)
    clock_maturity = state.time_since_running_high_minutes / max(time_to_running_high, 1)
    event_age_ratio = state.minutes_since_detection / max(config.expected_event_lifetime_minutes, 1)

    return (
        FEATURE_SCHEMA_VERSION,
        config.feature_matrix_version,
        state.event_id,
        state.symbol,
        state.snapshot_time_ms,
        state.feature_cutoff_time_ms,
        state.minutes_since_detection,
        atr_value,
        atr_pct_value,
        state.current_return_from_start,
        range_since_start_atr,
        distance_to_running_high_atr,
        distance_to_running_low_atr,
        retracement_from_high_atr,
        price_speed_atr,
        clock_maturity,
        event_age_ratio,
        alpha_decay_bucket(state.minutes_since_detection),
        status,
        volume_features.quote_volume_1m_to_24h_median,
        volume_features.volume_zscore,
        volume_features.quote_volume_zscore,
        oi_features.closed_5m_oi_asof_t,
        oi_features.oi_change_5m,
        oi_features.oi_change_10m,
        oi_features.oi_change_5m_pct_of_oi,
        oi_features.oi_change_10m_pct_of_oi,
        oi_features.missing_oi_flag,
        liquidation_features.short_liq_intensity,
        liquidation_features.long_liq_intensity,
        liquidation_features.liquidation_imbalance,
        liquidation_features.cumulative_liq_intensity_since_event_start,
        liquidation_features.missing_liquidation_flag,
        cvd_features.cvd_quote_since_event_start,
        cvd_features.cvd_change_by_window.get(3),
        cvd_features.cvd_change_by_window.get(5),
        cvd_features.cvd_change_by_window.get(10),
        cvd_features.cvd_price_divergence_by_window.get(3),
        cvd_features.cvd_price_divergence_by_window.get(5),
        cvd_features.cvd_price_divergence_by_window.get(10),
        cvd_features.price_up_cvd_down_flag,
        cvd_features.price_down_cvd_up_flag,
        cvd_features.cvd_failed_to_confirm_high_flag,
        cross_section_features.volume_market_percentile,
        cross_section_features.quote_volume_market_percentile,
        cross_section_features.return_1m_market_percentile,
        cross_section_features.return_from_event_market_percentile,
        cross_section_features.oi_growth_market_percentile,
        cross_section_features.liq_intensity_market_percentile,
        cross_section_features.range_expansion_market_percentile,
        cross_section_features.cross_section_available,
        cross_section_features.cross_section_symbol_count,
        market_context_features.corr_with_btc_by_window.get(15),
        market_context_features.corr_with_btc_by_window.get(30),
        market_context_features.corr_with_btc_by_window.get(60),
        market_context_features.symbol_return_minus_btc_by_window.get(5),
        market_context_features.symbol_return_minus_btc_by_window.get(15),
        market_context_features.idiosyncratic_momentum_score,
        market_context_features.simultaneous_anomalies_count_1m,
        market_context_features.simultaneous_anomalies_share_1m,
        market_context_features.systemic_cluster_regime,
        market_context_features.market_shock_id,
        geometry_features.initial_pump_height_core_atr_1440,
        geometry_features.post_pump_consolidation_minutes,
        geometry_features.consolidation_width_ratio,
        geometry_features.shelf_low_asof_t,
        geometry_features.shelf_high_asof_t,
        geometry_features.current_low_minus_shelf_low_core_atr_1440,
        geometry_features.current_close_minus_shelf_low_core_atr_1440,
        geometry_features.current_high_minus_shelf_high_core_atr_1440,
        geometry_features.minutes_spent_below_shelf,
        geometry_features.minutes_since_reclaim,
        geometry_features.volume_on_sweep_percentile,
        geometry_features.trade_count_on_sweep_percentile,
        geometry_features.cvd_change_during_sweep,
        geometry_features.oi_change_during_sweep,
        geometry_features.liq_intensity_during_sweep,
    )


def alpha_decay_bucket(minutes_since_trigger: int) -> str:
    if minutes_since_trigger < 0:
        raise ValueError("minutes_since_trigger must be non-negative")
    if minutes_since_trigger <= 2:
        return "0-2m"
    if minutes_since_trigger <= 5:
        return "3-5m"
    if minutes_since_trigger <= 10:
        return "6-10m"
    if minutes_since_trigger <= 20:
        return "11-20m"
    if minutes_since_trigger <= 40:
        return "21-40m"
    return ">40m"


def _price_speed_atr(
    *,
    candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    atr_value: float,
    atr_window_minutes: int,
) -> float | None:
    if isinstance(candles, _CandleSeries):
        previous_current = candles.previous_and_current(snapshot_time_ms)
        if previous_current is None:
            return None
        previous, current = previous_current
    else:
        asof_candles = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
        if len(asof_candles) < 2:
            return None
        current = asof_candles[-1]
        previous = asof_candles[-2]
        if current.available_time_ms != snapshot_time_ms:
            return None
    if current.available_time_ms != snapshot_time_ms:
        return None
    expected_one_minute_atr = atr_value / max(atr_window_minutes, 1)
    if not math.isfinite(expected_one_minute_atr) or expected_one_minute_atr <= 0:
        return None
    return (current.close - previous.close) / expected_one_minute_atr


class _RelaxedGeometryFeatures:
    def __init__(
        self,
        *,
        initial_pump_height_core_atr_1440: float | None,
        post_pump_consolidation_minutes: int | None,
        consolidation_width_ratio: float | None,
        shelf_low_asof_t: float | None,
        shelf_high_asof_t: float | None,
        current_low_minus_shelf_low_core_atr_1440: float | None,
        current_close_minus_shelf_low_core_atr_1440: float | None,
        current_high_minus_shelf_high_core_atr_1440: float | None,
        minutes_spent_below_shelf: int | None,
        minutes_since_reclaim: int | None,
        volume_on_sweep_percentile: float | None,
        trade_count_on_sweep_percentile: float | None,
        cvd_change_during_sweep: float | None,
        oi_change_during_sweep: float | None,
        liq_intensity_during_sweep: float | None,
    ) -> None:
        self.initial_pump_height_core_atr_1440 = initial_pump_height_core_atr_1440
        self.post_pump_consolidation_minutes = post_pump_consolidation_minutes
        self.consolidation_width_ratio = consolidation_width_ratio
        self.shelf_low_asof_t = shelf_low_asof_t
        self.shelf_high_asof_t = shelf_high_asof_t
        self.current_low_minus_shelf_low_core_atr_1440 = current_low_minus_shelf_low_core_atr_1440
        self.current_close_minus_shelf_low_core_atr_1440 = current_close_minus_shelf_low_core_atr_1440
        self.current_high_minus_shelf_high_core_atr_1440 = current_high_minus_shelf_high_core_atr_1440
        self.minutes_spent_below_shelf = minutes_spent_below_shelf
        self.minutes_since_reclaim = minutes_since_reclaim
        self.volume_on_sweep_percentile = volume_on_sweep_percentile
        self.trade_count_on_sweep_percentile = trade_count_on_sweep_percentile
        self.cvd_change_during_sweep = cvd_change_during_sweep
        self.oi_change_during_sweep = oi_change_during_sweep
        self.liq_intensity_during_sweep = liq_intensity_during_sweep


def _relaxed_geometry_features(
    *,
    candles: Sequence[Candle1m],
    state: StrategyState1mRow,
    atr_value: float | None,
    oi_features: _OiFeatures,
    liquidation_features: _LiquidationFeatures,
) -> _RelaxedGeometryFeatures:
    current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
    event_start_time_ms = _event_start_time_ms(state)
    if current is None:
        return _missing_relaxed_geometry()
    if isinstance(candles, _CandleSeries):
        event_window = list(candles.event_window(event_start_time_ms=event_start_time_ms, snapshot_time_ms=state.snapshot_time_ms))
    else:
        event_window = [
            candle
            for candle in candles
            if candle.open_time_ms >= event_start_time_ms and candle.available_time_ms <= state.snapshot_time_ms
        ]
        event_window.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    if not event_window:
        return _missing_relaxed_geometry()

    first = event_window[0]
    initial_pump_height_core_atr_1440 = None
    if atr_value is not None:
        initial_pump_height_core_atr_1440 = max(state.running_high_asof_t - first.open, 0.0) / atr_value

    shelf_low = state.structural_low_asof_t
    shelf_high = state.structural_high_asof_t
    post_pump_consolidation_minutes = None
    consolidation_width_ratio = None
    current_low_minus_shelf_low_core_atr_1440 = None
    current_close_minus_shelf_low_core_atr_1440 = None
    current_high_minus_shelf_high_core_atr_1440 = None
    minutes_spent_below_shelf = None
    minutes_since_reclaim = None
    volume_on_sweep_percentile = None
    trade_count_on_sweep_percentile = None
    cvd_change_during_sweep = None
    oi_change_during_sweep = None
    liq_intensity_during_sweep = None

    if shelf_low is not None:
        minutes_spent_below_shelf = sum(1 for candle in event_window if candle.close < shelf_low)
        minutes_since_reclaim = _minutes_since_latest_reclaim(event_window=event_window, shelf_low=shelf_low, snapshot_time_ms=state.snapshot_time_ms)
        if atr_value is not None:
            current_low_minus_shelf_low_core_atr_1440 = (current.low - shelf_low) / atr_value
            current_close_minus_shelf_low_core_atr_1440 = (current.close - shelf_low) / atr_value

    if shelf_high is not None and atr_value is not None:
        current_high_minus_shelf_high_core_atr_1440 = (current.high - shelf_high) / atr_value

    if shelf_low is not None and shelf_high is not None:
        post_pump_consolidation_minutes = state.time_since_running_high_minutes
        pump_height_price = max(state.running_high_asof_t - first.open, 0.0)
        if pump_height_price > EPS:
            consolidation_width_ratio = max(shelf_high - shelf_low, 0.0) / pump_height_price

    if shelf_low is not None and current.low < shelf_low:
        volume_on_sweep_percentile = _value_rank_percentile(current.volume, [candle.volume for candle in event_window])
        trade_values = [candle.number_of_trades for candle in event_window if candle.number_of_trades is not None]
        if current.number_of_trades is not None and trade_values:
            trade_count_on_sweep_percentile = _value_rank_percentile(current.number_of_trades, trade_values)
        if current.taker_buy_quote_volume is not None:
            cvd_change_during_sweep = _candle_delta_quote(current) / max(current.quote_volume, EPS)
        oi_change_during_sweep = oi_features.oi_change_5m_pct_of_oi
        if not liquidation_features.missing_liquidation_flag:
            short_intensity = liquidation_features.short_liq_intensity or 0.0
            long_intensity = liquidation_features.long_liq_intensity or 0.0
            liq_intensity_during_sweep = short_intensity + long_intensity

    return _RelaxedGeometryFeatures(
        initial_pump_height_core_atr_1440=initial_pump_height_core_atr_1440,
        post_pump_consolidation_minutes=post_pump_consolidation_minutes,
        consolidation_width_ratio=consolidation_width_ratio,
        shelf_low_asof_t=shelf_low,
        shelf_high_asof_t=shelf_high,
        current_low_minus_shelf_low_core_atr_1440=current_low_minus_shelf_low_core_atr_1440,
        current_close_minus_shelf_low_core_atr_1440=current_close_minus_shelf_low_core_atr_1440,
        current_high_minus_shelf_high_core_atr_1440=current_high_minus_shelf_high_core_atr_1440,
        minutes_spent_below_shelf=minutes_spent_below_shelf,
        minutes_since_reclaim=minutes_since_reclaim,
        volume_on_sweep_percentile=volume_on_sweep_percentile,
        trade_count_on_sweep_percentile=trade_count_on_sweep_percentile,
        cvd_change_during_sweep=cvd_change_during_sweep,
        oi_change_during_sweep=oi_change_during_sweep,
        liq_intensity_during_sweep=liq_intensity_during_sweep,
    )


def _missing_relaxed_geometry() -> _RelaxedGeometryFeatures:
    return _RelaxedGeometryFeatures(
        initial_pump_height_core_atr_1440=None,
        post_pump_consolidation_minutes=None,
        consolidation_width_ratio=None,
        shelf_low_asof_t=None,
        shelf_high_asof_t=None,
        current_low_minus_shelf_low_core_atr_1440=None,
        current_close_minus_shelf_low_core_atr_1440=None,
        current_high_minus_shelf_high_core_atr_1440=None,
        minutes_spent_below_shelf=None,
        minutes_since_reclaim=None,
        volume_on_sweep_percentile=None,
        trade_count_on_sweep_percentile=None,
        cvd_change_during_sweep=None,
        oi_change_during_sweep=None,
        liq_intensity_during_sweep=None,
    )


def _minutes_since_latest_reclaim(*, event_window: Sequence[Candle1m], shelf_low: float, snapshot_time_ms: int) -> int | None:
    latest_reclaim_time_ms: int | None = None
    previous_close: float | None = None
    for candle in event_window:
        if previous_close is not None and previous_close < shelf_low <= candle.close:
            latest_reclaim_time_ms = candle.available_time_ms
        previous_close = candle.close
    if latest_reclaim_time_ms is None:
        return None
    return max((snapshot_time_ms - latest_reclaim_time_ms) // ONE_MINUTE_MS, 0)


def _value_rank_percentile(value: float, values: Sequence[float]) -> float | None:
    if not values:
        return None
    less_or_equal = sum(1 for item in values if item <= value)
    return less_or_equal / len(values)


class _VolumeFeatures:
    def __init__(
        self,
        *,
        quote_volume_1m_to_24h_median: float | None,
        volume_zscore: float | None,
        quote_volume_zscore: float | None,
    ) -> None:
        self.quote_volume_1m_to_24h_median = quote_volume_1m_to_24h_median
        self.volume_zscore = volume_zscore
        self.quote_volume_zscore = quote_volume_zscore


def _volume_features(*, candles: Sequence[Candle1m], state: StrategyState1mRow, config: FeatureMatrixConfig) -> _VolumeFeatures:
    current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
    if current is None:
        return _VolumeFeatures(quote_volume_1m_to_24h_median=None, volume_zscore=None, quote_volume_zscore=None)
    if isinstance(candles, _CandleSeries):
        volume_moments = candles.trailing_moments_before(
            current.available_time_ms,
            config.volume_baseline_window_minutes,
            "volume",
        )
        quote_moments = candles.trailing_moments_before(
            current.available_time_ms,
            config.volume_baseline_window_minutes,
            "quote_volume",
        )
        quote_median = candles.trailing_median_before(
            current.available_time_ms,
            config.volume_baseline_window_minutes,
            "quote_volume",
        )
        if volume_moments is None or quote_moments is None or quote_median is None:
            return _VolumeFeatures(quote_volume_1m_to_24h_median=None, volume_zscore=None, quote_volume_zscore=None)
        quote_ratio = None if quote_median <= 0 else current.quote_volume / quote_median
        return _VolumeFeatures(
            quote_volume_1m_to_24h_median=quote_ratio,
            volume_zscore=_zscore_from_moments(current.volume, volume_moments),
            quote_volume_zscore=_zscore_from_moments(current.quote_volume, quote_moments),
        )
    else:
        history = [candle for candle in candles if candle.available_time_ms < current.available_time_ms]
        history.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
        history = history[-config.volume_baseline_window_minutes :]
        volume_moments = None
        quote_moments = None
    if len(history) < config.volume_baseline_window_minutes:
        return _VolumeFeatures(quote_volume_1m_to_24h_median=None, volume_zscore=None, quote_volume_zscore=None)

    quote_values = [candle.quote_volume for candle in history]
    volume_values = [] if volume_moments is not None else [candle.volume for candle in history]
    quote_median = statistics.median(quote_values)
    quote_ratio = None if quote_median <= 0 else current.quote_volume / quote_median
    return _VolumeFeatures(
        quote_volume_1m_to_24h_median=quote_ratio,
        volume_zscore=(
            _zscore_from_moments(current.volume, volume_moments)
            if volume_moments is not None
            else _zscore(current.volume, volume_values)
        ),
        quote_volume_zscore=(
            _zscore_from_moments(current.quote_volume, quote_moments)
            if quote_moments is not None
            else _zscore(current.quote_volume, quote_values)
        ),
    )


class _OiFeatures:
    def __init__(
        self,
        *,
        closed_5m_oi_asof_t: float | None,
        oi_change_5m: float | None,
        oi_change_10m: float | None,
        oi_change_5m_pct_of_oi: float | None,
        oi_change_10m_pct_of_oi: float | None,
        missing_oi_flag: bool,
    ) -> None:
        self.closed_5m_oi_asof_t = closed_5m_oi_asof_t
        self.oi_change_5m = oi_change_5m
        self.oi_change_10m = oi_change_10m
        self.oi_change_5m_pct_of_oi = oi_change_5m_pct_of_oi
        self.oi_change_10m_pct_of_oi = oi_change_10m_pct_of_oi
        self.missing_oi_flag = missing_oi_flag


def _oi_features(
    *,
    open_interest_rows: Sequence[OpenInterest5m] | _OpenInterestSeries | None,
    snapshot_time_ms: int,
) -> _OiFeatures:
    if open_interest_rows is None:
        return _missing_oi_features()
    if isinstance(open_interest_rows, _OpenInterestSeries):
        latest = open_interest_rows.asof_latest(snapshot_time_ms)
        if latest is None:
            return _missing_oi_features()
        prev_5m = open_interest_rows.at_or_before_timestamp(latest.timestamp_ms - FIVE_MINUTES_MS, snapshot_time_ms)
        prev_10m = open_interest_rows.at_or_before_timestamp(latest.timestamp_ms - 2 * FIVE_MINUTES_MS, snapshot_time_ms)
        return _oi_features_from_points(latest=latest, prev_5m=prev_5m, prev_10m=prev_10m)
    asof_rows = [item for item in open_interest_rows if item.available_time_ms <= snapshot_time_ms]
    if not asof_rows:
        return _missing_oi_features()
    asof_rows.sort(key=lambda item: (item.timestamp_ms, item.available_time_ms))
    latest = asof_rows[-1]
    prev_5m = _last_oi_at_or_before(asof_rows, latest.timestamp_ms - FIVE_MINUTES_MS)
    prev_10m = _last_oi_at_or_before(asof_rows, latest.timestamp_ms - 2 * FIVE_MINUTES_MS)
    return _oi_features_from_points(latest=latest, prev_5m=prev_5m, prev_10m=prev_10m)


def _oi_features_from_points(
    *,
    latest: OpenInterest5m,
    prev_5m: OpenInterest5m | None,
    prev_10m: OpenInterest5m | None,
) -> _OiFeatures:
    change_5m = None if prev_5m is None else latest.open_interest - prev_5m.open_interest
    change_10m = None if prev_10m is None else latest.open_interest - prev_10m.open_interest
    pct_5m = None if change_5m is None or latest.open_interest <= 0 else change_5m / latest.open_interest
    pct_10m = None if change_10m is None or latest.open_interest <= 0 else change_10m / latest.open_interest
    return _OiFeatures(
        closed_5m_oi_asof_t=latest.open_interest,
        oi_change_5m=change_5m,
        oi_change_10m=change_10m,
        oi_change_5m_pct_of_oi=pct_5m,
        oi_change_10m_pct_of_oi=pct_10m,
        missing_oi_flag=False,
    )


def _missing_oi_features() -> _OiFeatures:
    return _OiFeatures(
        closed_5m_oi_asof_t=None,
        oi_change_5m=None,
        oi_change_10m=None,
        oi_change_5m_pct_of_oi=None,
        oi_change_10m_pct_of_oi=None,
        missing_oi_flag=True,
    )


class _LiquidationFeatures:
    def __init__(
        self,
        *,
        short_liq_intensity: float | None,
        long_liq_intensity: float | None,
        liquidation_imbalance: float | None,
        cumulative_liq_intensity_since_event_start: float | None,
        missing_liquidation_flag: bool,
    ) -> None:
        self.short_liq_intensity = short_liq_intensity
        self.long_liq_intensity = long_liq_intensity
        self.liquidation_imbalance = liquidation_imbalance
        self.cumulative_liq_intensity_since_event_start = cumulative_liq_intensity_since_event_start
        self.missing_liquidation_flag = missing_liquidation_flag


def _liquidation_features(
    *,
    candles: Sequence[Candle1m],
    liquidation_rows: Sequence[LiquidationEvent] | _LiquidationSeries | None,
    state: StrategyState1mRow,
) -> _LiquidationFeatures:
    if liquidation_rows is None:
        return _missing_liquidation_features()
    current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
    if current is None:
        return _missing_liquidation_features()
    minute_start_ms = state.snapshot_time_ms - ONE_MINUTE_MS
    if isinstance(liquidation_rows, _LiquidationSeries):
        short_quote, long_quote = liquidation_rows.quote_by_side_in_window_asof(
            start_time_ms=minute_start_ms,
            end_time_ms=state.snapshot_time_ms,
            snapshot_time_ms=state.snapshot_time_ms,
        )
    else:
        asof_rows = [item for item in liquidation_rows if item.available_time_ms <= state.snapshot_time_ms]
        minute_rows = [item for item in asof_rows if minute_start_ms <= item.event_time_ms < state.snapshot_time_ms]
        short_quote = sum(item.quote_quantity for item in minute_rows if item.side == "short")
        long_quote = sum(item.quote_quantity for item in minute_rows if item.side == "long")
    total_quote = short_quote + long_quote
    quote_volume = max(current.quote_volume, EPS)
    event_start_time_ms = _event_start_time_ms(state)
    if isinstance(liquidation_rows, _LiquidationSeries):
        cumulative_liq_quote = liquidation_rows.quote_in_window_asof(
            start_time_ms=event_start_time_ms,
            end_time_ms=state.snapshot_time_ms,
            snapshot_time_ms=state.snapshot_time_ms,
        )
    else:
        cumulative_liq_quote = sum(
            item.quote_quantity for item in asof_rows if event_start_time_ms <= item.event_time_ms < state.snapshot_time_ms
        )
    if isinstance(candles, _CandleSeries):
        cumulative_quote_volume = candles.event_quote_volume_sum(
            event_start_time_ms=event_start_time_ms,
            snapshot_time_ms=state.snapshot_time_ms,
        )
    else:
        cumulative_quote_volume = sum(
            candle.quote_volume
            for candle in candles
            if candle.open_time_ms >= event_start_time_ms and candle.available_time_ms <= state.snapshot_time_ms
        )
    return _LiquidationFeatures(
        short_liq_intensity=short_quote / quote_volume,
        long_liq_intensity=long_quote / quote_volume,
        liquidation_imbalance=0.0 if total_quote <= 0 else (short_quote - long_quote) / total_quote,
        cumulative_liq_intensity_since_event_start=None
        if cumulative_quote_volume <= 0
        else cumulative_liq_quote / cumulative_quote_volume,
        missing_liquidation_flag=False,
    )


def _missing_liquidation_features() -> _LiquidationFeatures:
    return _LiquidationFeatures(
        short_liq_intensity=None,
        long_liq_intensity=None,
        liquidation_imbalance=None,
        cumulative_liq_intensity_since_event_start=None,
        missing_liquidation_flag=True,
    )


class _CvdFeatures:
    def __init__(
        self,
        *,
        cvd_quote_since_event_start: float | None,
        cvd_change_by_window: dict[int, float | None],
        cvd_price_divergence_by_window: dict[int, float | None],
        price_up_cvd_down_flag: bool,
        price_down_cvd_up_flag: bool,
        cvd_failed_to_confirm_high_flag: bool,
    ) -> None:
        self.cvd_quote_since_event_start = cvd_quote_since_event_start
        self.cvd_change_by_window = cvd_change_by_window
        self.cvd_price_divergence_by_window = cvd_price_divergence_by_window
        self.price_up_cvd_down_flag = price_up_cvd_down_flag
        self.price_down_cvd_up_flag = price_down_cvd_up_flag
        self.cvd_failed_to_confirm_high_flag = cvd_failed_to_confirm_high_flag


def _cvd_features(
    *,
    candles: Sequence[Candle1m],
    state: StrategyState1mRow,
    atr_value: float | None,
    windows_minutes: tuple[int, ...],
) -> _CvdFeatures:
    current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
    missing = _CvdFeatures(
        cvd_quote_since_event_start=None,
        cvd_change_by_window={window: None for window in windows_minutes},
        cvd_price_divergence_by_window={window: None for window in windows_minutes},
        price_up_cvd_down_flag=False,
        price_down_cvd_up_flag=False,
        cvd_failed_to_confirm_high_flag=False,
    )
    if current is None:
        return missing
    event_start_time_ms = _event_start_time_ms(state)
    if isinstance(candles, _CandleSeries):
        has_event_candles, missing_event_cvd, cvd_since_event = candles.event_cvd_ratio(
            event_start_time_ms=event_start_time_ms,
            snapshot_time_ms=state.snapshot_time_ms,
        )
        if not has_event_candles or missing_event_cvd or cvd_since_event is None:
            return missing
    else:
        event_candles = [
            candle
            for candle in candles
            if candle.open_time_ms >= event_start_time_ms and candle.available_time_ms <= state.snapshot_time_ms
        ]
        event_candles.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
        if not event_candles or any(candle.taker_buy_quote_volume is None for candle in event_candles):
            return missing
        cumulative_quote = sum(candle.quote_volume for candle in event_candles)
        if cumulative_quote <= 0:
            return missing
        cvd_since_event = sum(_candle_delta_quote(candle) for candle in event_candles) / cumulative_quote

    cvd_change_by_window: dict[int, float | None] = {}
    divergence_by_window: dict[int, float | None] = {}
    price_up_cvd_down = False
    price_down_cvd_up = False
    for window in windows_minutes:
        if isinstance(candles, _CandleSeries):
            cvd_change, price_change_atr, raw_price_change = candles.window_cvd_and_price_change_atr(
                snapshot_time_ms=state.snapshot_time_ms,
                window_minutes=window,
                atr_value=atr_value,
            )
            has_window = raw_price_change is not None
        else:
            window_candles = _window_candles(candles=candles, snapshot_time_ms=state.snapshot_time_ms, window_minutes=window)
            cvd_change = _window_cvd_ratio(window_candles)
            price_change_atr = _window_price_change_atr(window_candles=window_candles, atr_value=atr_value)
            raw_price_change = window_candles[-1].close - window_candles[0].open if window_candles else None
            has_window = bool(window_candles)
        cvd_change_by_window[window] = cvd_change
        divergence_by_window[window] = None if cvd_change is None or price_change_atr is None else price_change_atr - cvd_change
        if window == 5 and cvd_change is not None and has_window and raw_price_change is not None:
            price_up_cvd_down = raw_price_change > 0 and cvd_change < 0
            price_down_cvd_up = raw_price_change < 0 and cvd_change > 0

    cvd5 = cvd_change_by_window.get(5)
    failed_to_confirm_high = bool(cvd5 is not None and cvd5 <= 0 and math.isclose(current.close, state.running_high_asof_t, rel_tol=0.0, abs_tol=EPS))
    return _CvdFeatures(
        cvd_quote_since_event_start=cvd_since_event,
        cvd_change_by_window=cvd_change_by_window,
        cvd_price_divergence_by_window=divergence_by_window,
        price_up_cvd_down_flag=price_up_cvd_down,
        price_down_cvd_up_flag=price_down_cvd_up,
        cvd_failed_to_confirm_high_flag=failed_to_confirm_high,
    )


def _asof_candles(*, candles: Sequence[Candle1m], snapshot_time_ms: int) -> list[Candle1m]:
    if isinstance(candles, _CandleSeries):
        return list(candles.asof(snapshot_time_ms))
    rows = [candle for candle in candles if candle.available_time_ms <= snapshot_time_ms]
    rows.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    return rows


def _current_candle(*, candles: Sequence[Candle1m], snapshot_time_ms: int) -> Candle1m | None:
    if isinstance(candles, _CandleSeries):
        return candles.current(snapshot_time_ms)
    asof_rows = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
    if not asof_rows:
        return None
    current = asof_rows[-1]
    if current.available_time_ms != snapshot_time_ms:
        return None
    return current


def _event_start_time_ms(state: StrategyState1mRow) -> int:
    return state.snapshot_time_ms - max(state.minutes_since_event_start, 0) * ONE_MINUTE_MS


def _zscore(value: float, history_values: Sequence[float]) -> float | None:
    if len(history_values) < 2:
        return None
    mean_value = statistics.fmean(history_values)
    std_value = statistics.stdev(history_values)
    if std_value <= 0 or not math.isfinite(std_value):
        return None
    return (value - mean_value) / std_value


def _zscore_from_moments(value: float, moments: tuple[int, float, float] | None) -> float | None:
    if moments is None:
        return None
    count, total, square_total = moments
    if count < 2:
        return None
    mean_value = total / count
    variance_numerator = square_total - (total * total / count)
    if variance_numerator <= 0:
        return None
    std_value = math.sqrt(variance_numerator / (count - 1))
    if std_value <= 0 or not math.isfinite(std_value):
        return None
    return (value - mean_value) / std_value


def _last_oi_at_or_before(rows: Sequence[OpenInterest5m], timestamp_ms: int) -> OpenInterest5m | None:
    eligible = [item for item in rows if item.timestamp_ms <= timestamp_ms]
    if not eligible:
        return None
    eligible.sort(key=lambda item: (item.timestamp_ms, item.available_time_ms))
    return eligible[-1]


def _candle_delta_quote(candle: Candle1m) -> float:
    if candle.taker_buy_quote_volume is None:
        raise ValueError("taker_buy_quote_volume is required for CVD computation")
    return 2.0 * candle.taker_buy_quote_volume - candle.quote_volume


def _window_candles(*, candles: Sequence[Candle1m], snapshot_time_ms: int, window_minutes: int) -> list[Candle1m]:
    if isinstance(candles, _CandleSeries):
        return list(candles.window(snapshot_time_ms, window_minutes))
    window_start_ms = snapshot_time_ms - window_minutes * ONE_MINUTE_MS
    rows = [candle for candle in candles if window_start_ms <= candle.open_time_ms and candle.available_time_ms <= snapshot_time_ms]
    rows.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    if len(rows) < window_minutes:
        return []
    return rows[-window_minutes:]


def _window_cvd_ratio(window_candles: Sequence[Candle1m]) -> float | None:
    if not window_candles or any(candle.taker_buy_quote_volume is None for candle in window_candles):
        return None
    quote_volume = sum(candle.quote_volume for candle in window_candles)
    if quote_volume <= 0:
        return None
    return sum(_candle_delta_quote(candle) for candle in window_candles) / quote_volume


def _window_price_change_atr(*, window_candles: Sequence[Candle1m], atr_value: float | None) -> float | None:
    if not window_candles or atr_value is None or atr_value <= 0:
        return None
    return (window_candles[-1].close - window_candles[0].open) / atr_value


class _CrossSectionFeatures:
    def __init__(
        self,
        *,
        volume_market_percentile: float | None,
        quote_volume_market_percentile: float | None,
        return_1m_market_percentile: float | None,
        return_from_event_market_percentile: float | None,
        oi_growth_market_percentile: float | None,
        liq_intensity_market_percentile: float | None,
        range_expansion_market_percentile: float | None,
        cross_section_available: bool,
        cross_section_symbol_count: int,
    ) -> None:
        self.volume_market_percentile = volume_market_percentile
        self.quote_volume_market_percentile = quote_volume_market_percentile
        self.return_1m_market_percentile = return_1m_market_percentile
        self.return_from_event_market_percentile = return_from_event_market_percentile
        self.oi_growth_market_percentile = oi_growth_market_percentile
        self.liq_intensity_market_percentile = liq_intensity_market_percentile
        self.range_expansion_market_percentile = range_expansion_market_percentile
        self.cross_section_available = cross_section_available
        self.cross_section_symbol_count = cross_section_symbol_count


def _cross_section_features(
    *,
    state: StrategyState1mRow,
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m] | _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent] | _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    min_cross_section_symbols: int,
) -> _CrossSectionFeatures:
    trade_date = utc_ms_to_datetime(state.snapshot_time_ms).date().isoformat()
    universe_symbols = universe_by_day.get(trade_date, set())
    if not universe_symbols:
        return _missing_cross_section_features(symbol_count=0)

    metric_values: dict[str, dict[str, float]] = {
        "volume": {},
        "quote_volume": {},
        "return_1m": {},
        "oi_growth": {},
        "liq_intensity": {},
        "range_expansion": {},
    }
    symbols_with_current_candle = 0
    for symbol in sorted(universe_symbols):
        candles = candles_by_symbol.get(symbol, ())
        current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
        if current is None:
            continue
        symbols_with_current_candle += 1
        metric_values["volume"][symbol] = current.volume
        metric_values["quote_volume"][symbol] = current.quote_volume
        return_1m = _one_minute_return(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
        if return_1m is not None:
            metric_values["return_1m"][symbol] = return_1m
        if current.close > 0:
            metric_values["range_expansion"][symbol] = (current.high - current.low) / current.close
        if open_interest_by_symbol is not None:
            oi = _oi_features(
                open_interest_rows=open_interest_by_symbol.get(symbol, ()),
                snapshot_time_ms=state.snapshot_time_ms,
            )
            if oi.oi_change_5m_pct_of_oi is not None:
                metric_values["oi_growth"][symbol] = oi.oi_change_5m_pct_of_oi
        if liquidations_by_symbol is not None:
            liq_intensity = _minute_liq_intensity(
                current=current,
                liquidation_rows=liquidations_by_symbol.get(symbol, ()),
                snapshot_time_ms=state.snapshot_time_ms,
            )
            if liq_intensity is not None:
                metric_values["liq_intensity"][symbol] = liq_intensity

    if symbols_with_current_candle < min_cross_section_symbols:
        return _missing_cross_section_features(symbol_count=symbols_with_current_candle)

    return _CrossSectionFeatures(
        volume_market_percentile=_rank_percentile(metric_values["volume"], state.symbol),
        quote_volume_market_percentile=_rank_percentile(metric_values["quote_volume"], state.symbol),
        return_1m_market_percentile=_rank_percentile(metric_values["return_1m"], state.symbol),
        return_from_event_market_percentile=_return_from_event_percentile(
            states_at_snapshot=states_at_snapshot,
            symbol=state.symbol,
            min_cross_section_symbols=min_cross_section_symbols,
        ),
        oi_growth_market_percentile=_rank_percentile(metric_values["oi_growth"], state.symbol),
        liq_intensity_market_percentile=_rank_percentile(metric_values["liq_intensity"], state.symbol),
        range_expansion_market_percentile=_rank_percentile(metric_values["range_expansion"], state.symbol),
        cross_section_available=True,
        cross_section_symbol_count=symbols_with_current_candle,
    )


def _cross_section_features_by_symbol(
    *,
    snapshot_time_ms: int,
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m] | _OpenInterestSeries] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent] | _LiquidationSeries] | None,
    universe_by_day: Mapping[str, set[str]],
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    min_cross_section_symbols: int,
    target_symbols: set[str] | None = None,
) -> tuple[dict[str, _CrossSectionFeatures], _CrossSectionFeatures]:
    trade_date = utc_ms_to_datetime(snapshot_time_ms).date().isoformat()
    universe_symbols = universe_by_day.get(trade_date, set())
    if not universe_symbols:
        missing = _missing_cross_section_features(symbol_count=0)
        return {}, missing

    metric_values: dict[str, dict[str, float]] = {
        "volume": {},
        "quote_volume": {},
        "return_1m": {},
        "oi_growth": {},
        "liq_intensity": {},
        "range_expansion": {},
    }
    symbols_with_current_candle = 0
    for symbol in sorted(universe_symbols):
        candles = candles_by_symbol.get(symbol, ())
        current = _current_candle(candles=candles, snapshot_time_ms=snapshot_time_ms)
        if current is None:
            continue
        symbols_with_current_candle += 1
        metric_values["volume"][symbol] = current.volume
        metric_values["quote_volume"][symbol] = current.quote_volume
        return_1m = _one_minute_return(candles=candles, snapshot_time_ms=snapshot_time_ms)
        if return_1m is not None:
            metric_values["return_1m"][symbol] = return_1m
        if current.close > 0:
            metric_values["range_expansion"][symbol] = (current.high - current.low) / current.close
        if open_interest_by_symbol is not None:
            oi = _oi_features(
                open_interest_rows=open_interest_by_symbol.get(symbol, ()),
                snapshot_time_ms=snapshot_time_ms,
            )
            if oi.oi_change_5m_pct_of_oi is not None:
                metric_values["oi_growth"][symbol] = oi.oi_change_5m_pct_of_oi
        if liquidations_by_symbol is not None:
            liq_intensity = _minute_liq_intensity(
                current=current,
                liquidation_rows=liquidations_by_symbol.get(symbol, ()),
                snapshot_time_ms=snapshot_time_ms,
            )
            if liq_intensity is not None:
                metric_values["liq_intensity"][symbol] = liq_intensity

    missing = _missing_cross_section_features(symbol_count=symbols_with_current_candle)
    if symbols_with_current_candle < min_cross_section_symbols:
        return {}, missing

    ranks_by_metric = {
        metric_name: _rank_percentiles_by_symbol(values)
        for metric_name, values in metric_values.items()
    }
    return_from_event_ranks = _return_from_event_percentiles_by_symbol(
        states_at_snapshot=states_at_snapshot,
        min_cross_section_symbols=min_cross_section_symbols,
    )
    if target_symbols is None:
        target_symbols = set().union(*(set(values) for values in metric_values.values()))
        target_symbols.update(row.symbol for row in states_at_snapshot)
    return {
        symbol: _CrossSectionFeatures(
            volume_market_percentile=ranks_by_metric["volume"].get(symbol),
            quote_volume_market_percentile=ranks_by_metric["quote_volume"].get(symbol),
            return_1m_market_percentile=ranks_by_metric["return_1m"].get(symbol),
            return_from_event_market_percentile=return_from_event_ranks.get(symbol),
            oi_growth_market_percentile=ranks_by_metric["oi_growth"].get(symbol),
            liq_intensity_market_percentile=ranks_by_metric["liq_intensity"].get(symbol),
            range_expansion_market_percentile=ranks_by_metric["range_expansion"].get(symbol),
            cross_section_available=True,
            cross_section_symbol_count=symbols_with_current_candle,
        )
        for symbol in target_symbols
    }, missing


def _missing_cross_section_features(*, symbol_count: int) -> _CrossSectionFeatures:
    return _CrossSectionFeatures(
        volume_market_percentile=None,
        quote_volume_market_percentile=None,
        return_1m_market_percentile=None,
        return_from_event_market_percentile=None,
        oi_growth_market_percentile=None,
        liq_intensity_market_percentile=None,
        range_expansion_market_percentile=None,
        cross_section_available=False,
        cross_section_symbol_count=symbol_count,
    )


def _universe_symbols_by_day(rows: Sequence[SymbolDayUniverseRow] | Iterable[SymbolDayUniverseRow] | None) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if rows is None:
        return result
    for row in rows:
        if row.eligible_for_cross_section and row.tradable_on_day and row.liquidity_eligible_on_day and row.has_1m_data:
            result.setdefault(row.trade_date, set()).add(row.symbol)
    return result


def _one_minute_return(*, candles: Sequence[Candle1m], snapshot_time_ms: int) -> float | None:
    if isinstance(candles, _CandleSeries):
        asof_rows = candles.trailing_asof(snapshot_time_ms, 2)
    else:
        asof_rows = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
    if len(asof_rows) < 2:
        return None
    current = asof_rows[-1]
    previous = asof_rows[-2]
    if current.available_time_ms != snapshot_time_ms or previous.close <= 0:
        return None
    return current.close / previous.close - 1.0


def _minute_liq_intensity(
    *,
    current: Candle1m,
    liquidation_rows: Sequence[LiquidationEvent] | _LiquidationSeries,
    snapshot_time_ms: int,
) -> float | None:
    minute_start_ms = snapshot_time_ms - ONE_MINUTE_MS
    if isinstance(liquidation_rows, _LiquidationSeries):
        quote = liquidation_rows.quote_in_window_asof(
            start_time_ms=minute_start_ms,
            end_time_ms=snapshot_time_ms,
            snapshot_time_ms=snapshot_time_ms,
        )
    else:
        quote = sum(
            item.quote_quantity
            for item in liquidation_rows
            if item.available_time_ms <= snapshot_time_ms and minute_start_ms <= item.event_time_ms < snapshot_time_ms
        )
    if current.quote_volume <= 0:
        return None
    return quote / current.quote_volume


def _return_from_event_percentile(
    *,
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    symbol: str,
    min_cross_section_symbols: int,
) -> float | None:
    values = {
        f"{item.symbol}::{item.event_id}": item.current_return_from_start
        for item in states_at_snapshot
        if item.current_return_from_start is not None
    }
    current_keys = [key for key in values if key.startswith(f"{symbol}::")]
    if len(values) < min_cross_section_symbols or not current_keys:
        return None
    # If several events for the same symbol are active at the same snapshot,
    # use the strongest as-of return for this symbol instead of leaking any future outcome.
    symbol_value = max(values[key] for key in current_keys)
    symbol_values: dict[str, float] = {}
    for key, value in values.items():
        row_symbol = key.split("::", 1)[0]
        previous = symbol_values.get(row_symbol)
        if previous is None or value > previous:
            symbol_values[row_symbol] = value
    return _rank_percentile(symbol_values, symbol)


def _return_from_event_percentiles_by_symbol(
    *,
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    min_cross_section_symbols: int,
) -> dict[str, float]:
    values = [
        item
        for item in states_at_snapshot
        if item.current_return_from_start is not None
    ]
    if len(values) < min_cross_section_symbols:
        return {}
    symbol_values: dict[str, float] = {}
    for item in values:
        previous = symbol_values.get(item.symbol)
        if previous is None or item.current_return_from_start > previous:
            symbol_values[item.symbol] = item.current_return_from_start
    return _rank_percentiles_by_symbol(symbol_values)


def _rank_percentile(values_by_symbol: Mapping[str, float], symbol: str) -> float | None:
    if symbol not in values_by_symbol:
        return None
    items = [(item_symbol, value) for item_symbol, value in values_by_symbol.items() if math.isfinite(value)]
    if not items or symbol not in {item_symbol for item_symbol, _ in items}:
        return None
    sorted_values = sorted(value for _, value in items)
    value = values_by_symbol[symbol]
    tied_positions = [index + 1 for index, item_value in enumerate(sorted_values) if item_value == value]
    if not tied_positions:
        return None
    average_rank = statistics.fmean(tied_positions)
    denominator = max(len(sorted_values) - 1, 1)
    percentile = (average_rank - 1.0) / denominator
    return min(max(percentile, 0.0), 1.0)


def _rank_percentiles_by_symbol(values_by_symbol: Mapping[str, float]) -> dict[str, float]:
    items = [(item_symbol, value) for item_symbol, value in values_by_symbol.items() if math.isfinite(value)]
    if not items:
        return {}
    sorted_values = sorted(value for _, value in items)
    average_rank_by_value: dict[float, float] = {}
    for value in sorted_values:
        if value in average_rank_by_value:
            continue
        tied_positions = [index + 1 for index, item_value in enumerate(sorted_values) if item_value == value]
        average_rank_by_value[value] = statistics.fmean(tied_positions)
    denominator = max(len(sorted_values) - 1, 1)
    return {
        item_symbol: min(max((average_rank_by_value[value] - 1.0) / denominator, 0.0), 1.0)
        for item_symbol, value in items
    }



class _MarketContextFeatures:
    def __init__(
        self,
        *,
        corr_with_btc_by_window: dict[int, float | None],
        symbol_return_minus_btc_by_window: dict[int, float | None],
        idiosyncratic_momentum_score: float | None,
        simultaneous_anomalies_count_1m: int,
        simultaneous_anomalies_share_1m: float | None,
        systemic_cluster_regime: str,
        market_shock_id: str,
    ) -> None:
        self.corr_with_btc_by_window = corr_with_btc_by_window
        self.symbol_return_minus_btc_by_window = symbol_return_minus_btc_by_window
        self.idiosyncratic_momentum_score = idiosyncratic_momentum_score
        self.simultaneous_anomalies_count_1m = simultaneous_anomalies_count_1m
        self.simultaneous_anomalies_share_1m = simultaneous_anomalies_share_1m
        self.systemic_cluster_regime = systemic_cluster_regime
        self.market_shock_id = market_shock_id


def _market_context_features(
    *,
    state: StrategyState1mRow,
    symbol_candles: Sequence[Candle1m],
    btc_candles: Sequence[Candle1m],
    states_at_snapshot: Sequence[StrategyState1mRow | _StateSnapshotRow],
    cross_section_symbol_count: int,
    volume_market_percentile: float | None,
    ATR_1d_pct_asof_t: float | None,
    corr_windows_minutes: tuple[int, ...],
    return_windows_minutes: tuple[int, ...],
    moderate_cluster_min_count: int,
    systemic_cluster_min_count: int,
) -> _MarketContextFeatures:
    corr_by_window = {
        window: _rolling_return_correlation_with_btc(
            symbol_candles=symbol_candles,
            btc_candles=btc_candles,
            snapshot_time_ms=state.snapshot_time_ms,
            window_minutes=window,
        )
        for window in corr_windows_minutes
    }
    return_minus_btc_by_window = {
        window: _symbol_return_minus_btc_return(
            symbol_candles=symbol_candles,
            btc_candles=btc_candles,
            snapshot_time_ms=state.snapshot_time_ms,
            window_minutes=window,
        )
        for window in return_windows_minutes
    }
    simultaneous_count = len({row.symbol for row in states_at_snapshot if row.event_alive})
    simultaneous_share = None
    if cross_section_symbol_count > 0:
        simultaneous_share = min(simultaneous_count / cross_section_symbol_count, 1.0)

    regime = _systemic_cluster_regime(
        simultaneous_count=simultaneous_count,
        cross_section_symbol_count=cross_section_symbol_count,
        moderate_cluster_min_count=moderate_cluster_min_count,
        systemic_cluster_min_count=systemic_cluster_min_count,
    )
    market_shock_id = _market_shock_id(
        snapshot_time_ms=state.snapshot_time_ms,
        regime=regime,
        symbol=state.symbol,
    )

    return_15m = return_minus_btc_by_window.get(15)
    corr_30m = corr_by_window.get(30)
    idiosyncratic_score = _idiosyncratic_momentum_score(
        volume_market_percentile=volume_market_percentile,
        symbol_return_minus_btc_return_15m=return_15m,
        corr_with_btc_30m=corr_30m,
        ATR_1d_pct_asof_t=ATR_1d_pct_asof_t,
    )

    return _MarketContextFeatures(
        corr_with_btc_by_window=corr_by_window,
        symbol_return_minus_btc_by_window=return_minus_btc_by_window,
        idiosyncratic_momentum_score=idiosyncratic_score,
        simultaneous_anomalies_count_1m=simultaneous_count,
        simultaneous_anomalies_share_1m=simultaneous_share,
        systemic_cluster_regime=regime,
        market_shock_id=market_shock_id,
    )


def _systemic_cluster_regime(
    *,
    simultaneous_count: int,
    cross_section_symbol_count: int,
    moderate_cluster_min_count: int,
    systemic_cluster_min_count: int,
) -> str:
    if cross_section_symbol_count <= 0:
        return "unknown"
    if simultaneous_count >= systemic_cluster_min_count:
        return "systemic_beta_shock"
    if simultaneous_count >= moderate_cluster_min_count:
        return "moderate_cluster"
    return "idiosyncratic"


def _market_shock_id(*, snapshot_time_ms: int, regime: str, symbol: str) -> str:
    if regime in {"moderate_cluster", "systemic_beta_shock"}:
        return f"market_shock:{snapshot_time_ms}"
    if regime == "idiosyncratic":
        return f"idiosyncratic:{symbol}:{snapshot_time_ms}"
    return f"unknown:{snapshot_time_ms}"


def _idiosyncratic_momentum_score(
    *,
    volume_market_percentile: float | None,
    symbol_return_minus_btc_return_15m: float | None,
    corr_with_btc_30m: float | None,
    ATR_1d_pct_asof_t: float | None,
) -> float | None:
    if volume_market_percentile is None:
        return None
    if symbol_return_minus_btc_return_15m is None:
        return None
    if corr_with_btc_30m is None:
        return None
    if ATR_1d_pct_asof_t is None or ATR_1d_pct_asof_t <= 0:
        return None
    btc_relative_return_atr = symbol_return_minus_btc_return_15m / max(ATR_1d_pct_asof_t, EPS)
    return volume_market_percentile * max(btc_relative_return_atr, 0.0) * max(1.0 - corr_with_btc_30m, 0.0)


def _symbol_return_minus_btc_return(
    *,
    symbol_candles: Sequence[Candle1m],
    btc_candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    window_minutes: int,
) -> float | None:
    symbol_return = _window_return(candles=symbol_candles, snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)
    btc_return = _window_return(candles=btc_candles, snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)
    if symbol_return is None or btc_return is None:
        return None
    return symbol_return - btc_return


def _window_return(*, candles: Sequence[Candle1m], snapshot_time_ms: int, window_minutes: int) -> float | None:
    if isinstance(candles, _CandleSeries):
        return candles.window_return(snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)
    window_candles = _window_candles(candles=candles, snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)
    if len(window_candles) < window_minutes:
        return None
    first = window_candles[0]
    last = window_candles[-1]
    if last.available_time_ms != snapshot_time_ms or first.open <= 0:
        return None
    return last.close / first.open - 1.0


def _rolling_return_correlation_with_btc(
    *,
    symbol_candles: Sequence[Candle1m],
    btc_candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    window_minutes: int,
) -> float | None:
    if isinstance(symbol_candles, _CandleSeries) and isinstance(btc_candles, _CandleSeries):
        return symbol_candles.rolling_return_correlation_with(
            other=btc_candles,
            snapshot_time_ms=snapshot_time_ms,
            window_minutes=window_minutes,
        )
    symbol_returns = _one_minute_returns_by_available_time(
        candles=symbol_candles,
        snapshot_time_ms=snapshot_time_ms,
        window_minutes=window_minutes,
    )
    btc_returns = _one_minute_returns_by_available_time(
        candles=btc_candles,
        snapshot_time_ms=snapshot_time_ms,
        window_minutes=window_minutes,
    )
    common_times = sorted(set(symbol_returns) & set(btc_returns))
    if len(common_times) < window_minutes:
        return None
    symbol_values = [symbol_returns[item] for item in common_times[-window_minutes:]]
    btc_values = [btc_returns[item] for item in common_times[-window_minutes:]]
    return _correlation(symbol_values, btc_values)


def _correlation_for_aligned_times(
    *,
    left_times: Sequence[int],
    left_values: Sequence[float],
    right_times: Sequence[int],
    right_values: Sequence[float],
    window_minutes: int,
) -> float | None:
    left_index = 0
    right_index = 0
    aligned_left: list[float] = []
    aligned_right: list[float] = []
    while left_index < len(left_times) and right_index < len(right_times):
        left_time = left_times[left_index]
        right_time = right_times[right_index]
        if left_time == right_time:
            aligned_left.append(left_values[left_index])
            aligned_right.append(right_values[right_index])
            left_index += 1
            right_index += 1
        elif left_time < right_time:
            left_index += 1
        else:
            right_index += 1
    if len(aligned_left) < window_minutes:
        return None
    return _correlation(aligned_left[-window_minutes:], aligned_right[-window_minutes:])


def _one_minute_returns_by_available_time(
    *,
    candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    window_minutes: int,
) -> dict[int, float]:
    if isinstance(candles, _CandleSeries):
        asof_rows = candles.trailing_asof(snapshot_time_ms, window_minutes + 1)
    else:
        asof_rows = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
    if len(asof_rows) < window_minutes + 1:
        return {}
    rows = asof_rows[-(window_minutes + 1) :]
    if rows[-1].available_time_ms != snapshot_time_ms:
        return {}
    result: dict[int, float] = {}
    for previous, current in zip(rows, rows[1:]):
        if previous.close <= 0:
            continue
        result[current.available_time_ms] = current.close / previous.close - 1.0
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    left_var = sum(value * value for value in left_centered)
    right_var = sum(value * value for value in right_centered)
    if left_var <= 0 or right_var <= 0:
        return None
    corr = sum(lval * rval for lval, rval in zip(left_centered, right_centered)) / math.sqrt(left_var * right_var)
    return min(max(corr, -1.0), 1.0)

def _protocol_rows(*, state_row_count: int, feature_row_count: int) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_feature_matrix_scope",
            status=AuditStatus.PASS,
            message="price/time/alpha-decay plus volume/OI/liquidation/CVD, point-in-time cross-sectional, BTC-relative, and systemic cluster feature matrix only; no ML/decision/trade simulation",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_state_1m.csv accepted through strict schema boundary with {state_row_count} state rows",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_rows_written",
            status=AuditStatus.PASS if feature_row_count == state_row_count else AuditStatus.FAIL,
            message=f"strategy_feature_matrix.csv written with {feature_row_count} rows for {state_row_count} state rows",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_no_future_artifact_dependency",
            status=AuditStatus.PASS,
            message="feature matrix recomputes ATR and all rolling features from normalized as-of market data and does not read strategy_future_paths.csv",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_flow_oi_liquidation_cvd_asof",
            status=AuditStatus.PASS,
            message="OI uses closed 5m rows available <= snapshot; liquidation and CVD use events/candles available <= snapshot only",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_cross_section_point_in_time",
            status=AuditStatus.PASS,
            message="cross-sectional percentiles use only point-in-time universe symbols with current candles available <= snapshot_time_ms",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_columnar_sidecar_manifest_bound",
            status=AuditStatus.PASS,
            message="strategy_feature_matrix.parquet is written in the same direct row pass and bound to strategy_feature_matrix.csv by size, sha256, row count, and schema manifest; invalid sidecars fail strict load instead of falling back silently",
            artifact="strategy_feature_matrix.parquet",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 feature matrix uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="relative_over_absolute_feature_contract_enforced",
            status=AuditStatus.PASS,
            message="materialized feature matrix contains ATR-normalized, self-history-relative, dimensionless, categorical, boolean, or audit-only fields; no raw absolute model features",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="custom_features_causality_gate_enforced",
            status=AuditStatus.PASS,
            message="feature matrix builders use state/future joins by point-in-time keys and do not call non-causal strategy custom feature operations",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="ATR_1d_asof_t_computed_from_closed_past_candles",
            status=AuditStatus.PASS,
            message="feature matrix computes core_atr_1440 from closed 1m candles available <= snapshot_time_ms and leaves ATR features null when history is insufficient; CSV alias is ATR_1d_asof_t",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="market_shock_id_assigned",
            status=AuditStatus.PASS,
            message="feature matrix assigns a point-in-time market_shock_id for every row; clustered rows share the snapshot-level market shock id",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="simultaneous_anomalies_count_1m_point_in_time",
            status=AuditStatus.PASS,
            message="simultaneous anomaly counts are computed from strategy_state_1m rows at the same snapshot_time_ms and normalized by the point-in-time cross-section when available",
            artifact="strategy_feature_matrix.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_feature_matrix",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(
    *,
    input_path: Path,
    state_path: Path,
    output_path: Path,
    config: FeatureMatrixConfig,
    max_input_time_ms: int | None = None,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-feature-matrix", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="max_input_time_ms", value="" if max_input_time_ms is None else str(max_input_time_ms), source="cli"),
        *runtime_reproducibility_rows(
            data_paths=(input_path, state_path),
            config=config,
            extra_config={"command": "run-mvp1-feature-matrix", "stage": "mvp1_feature_matrix"},
        ),
        RunConfigRow(key="stage", value="mvp1_feature_matrix", source="runtime"),
        RunConfigRow(key="feature_schema_version", value=FEATURE_SCHEMA_VERSION, source="runtime"),
        RunConfigRow(key="feature_matrix_version", value=config.feature_matrix_version, source="runtime"),
        RunConfigRow(key="atr_window_minutes", value=str(config.atr_window_minutes), source="runtime"),
        RunConfigRow(
            key="expected_event_lifetime_minutes",
            value=str(config.expected_event_lifetime_minutes),
            source="runtime",
        ),
        RunConfigRow(
            key="volume_baseline_window_minutes",
            value=str(config.volume_baseline_window_minutes),
            source="runtime",
        ),
        RunConfigRow(key="cvd_windows_minutes", value=",".join(str(item) for item in config.cvd_windows_minutes), source="runtime"),
        RunConfigRow(key="min_cross_section_symbols", value=str(config.min_cross_section_symbols), source="runtime"),
        RunConfigRow(key="cross_section_delivery", value="exact_sorted_sidecar_by_symbol", source="runtime"),
        RunConfigRow(key="feature_matrix_row_delivery", value="direct_schema_ordered_values", source="runtime"),
        RunConfigRow(key="feature_matrix_columnar_sidecar", value="strict_typed_parquet_manifest_bound", source="runtime"),
        RunConfigRow(key="symbol_feature_delivery", value="exact_per_symbol_snapshot_index", source="runtime"),
        RunConfigRow(key="btc_symbol", value=config.btc_symbol, source="runtime"),
        RunConfigRow(
            key="btc_corr_window_minutes",
            value=",".join(str(item) for item in config.btc_corr_window_minutes),
            source="runtime",
        ),
        RunConfigRow(
            key="btc_relative_return_windows_minutes",
            value=",".join(str(item) for item in config.btc_relative_return_windows_minutes),
            source="runtime",
        ),
        RunConfigRow(key="moderate_cluster_min_count", value=str(config.moderate_cluster_min_count), source="runtime"),
        RunConfigRow(key="systemic_cluster_min_count", value=str(config.systemic_cluster_min_count), source="runtime"),
    ]


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_float(row: Mapping[str, object], name: str) -> float | None:
    value = row[name]
    if _is_missing(value):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when provided")
    return result


def _optional_int(row: Mapping[str, object], name: str) -> int | None:
    value = row[name]
    if _is_missing(value):
        return None
    return int(value)


def _required_bool(row: Mapping[str, object], name: str) -> bool:
    value = row[name]
    if isinstance(value, bool):
        return value
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _is_missing(value: object) -> bool:
    # The Parquet sidecar stores absent optional values as null; pandas reads
    # those back as float NaN. Treat NaN as missing so the canonical Parquet read
    # path matches CSV "" semantics instead of failing the finite-value contract.
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def _run_id() -> str:
    return "mvp1-feature-matrix-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
