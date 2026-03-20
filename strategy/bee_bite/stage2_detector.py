"""Stage-2 detector for bee_bite: confirmed highs and upper balance ranges."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from data.liquidity.bee_bite_stage1_selector import BeeBiteStage1Result


@dataclass(slots=True, frozen=True)
class BeeBiteStage2ConfirmedHigh:
    idx: int
    timestamp: int
    price: float


@dataclass(slots=True, frozen=True)
class BeeBiteStage2Range:
    start_idx: int
    start_timestamp: int
    end_idx: int
    end_timestamp: int
    confirmed_high_idx: int
    confirmed_high_timestamp: int
    confirmed_high_price: float
    high: float
    low: float
    bars: int


@dataclass(slots=True, frozen=True)
class BeeBiteStage2MergedRange:
    start_idx: int
    start_timestamp: int
    end_idx: int
    end_timestamp: int
    high: float
    low: float
    bars: int
    source_ranges: int
    confirmed_high_timestamps: tuple[int, ...]
    confirmed_high_prices: tuple[float, ...]


@dataclass(slots=True, frozen=True)
class BeeBiteStage2LiquidityZone:
    side: str
    start_idx: int
    start_timestamp: int
    end_idx: int
    end_timestamp: int
    last_touch_idx: int
    last_touch_timestamp: int
    low: float
    high: float
    touch_count: int


@dataclass(slots=True, frozen=True)
class BeeBiteStage2Result:
    symbol: str
    passed: bool
    reason: str
    analysis_start_timestamp: int | None = None
    analysis_end_timestamp: int | None = None
    confirmed_highs: tuple[BeeBiteStage2ConfirmedHigh, ...] = ()
    local_ranges: tuple[BeeBiteStage2Range, ...] = ()
    merged_ranges: tuple[BeeBiteStage2MergedRange, ...] = ()
    liquidity_zones: tuple[BeeBiteStage2LiquidityZone, ...] = ()
    box_start_timestamp: int | None = None
    box_end_timestamp: int | None = None
    box_high: float | None = None
    box_low: float | None = None


@dataclass(slots=True)
class _PreparedStage2Frame:
    timestamps: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray


class BeeBiteStage2Detector:
    """Detect confirmed highs and merged upper ranges after a valid stage-1 event."""

    _EPSILON = 1e-12
    _ADJACENT_BREAKOUT_MULTIPLIER = 0.25
    _BODY_CLOSE_BREAKOUT_MULTIPLIER = 0.35
    _DEFAULT_BREAKOUT_MULTIPLIER = 1.0
    _RANGE_OVERLAP_THRESHOLD = 0.5
    _MIN_BODY_RATIO_FOR_BREAKOUT = 0.35
    _LIQUIDITY_CLUSTER_TOLERANCE_TO_CANDLE = 0.35
    _LIQUIDITY_CLUSTER_TOLERANCE_TO_RANGE = 0.015
    _LIQUIDITY_ZONE_RELATIVE_MARGIN = 0.35
    _LIQUIDITY_MIN_TOUCHES = 2
    _LIQUIDITY_SWEEP_TOLERANCE_MULTIPLIER = 0.15
    _LIQUIDITY_ZONE_WIDTH_MULTIPLIER = 1.0
    _LIQUIDITY_ZONE_OFFSET_MULTIPLIER = 0.1
    _LIQUIDITY_UPPER_ZONE_OFFSET_MULTIPLIER = 0.1
    _LIQUIDITY_MIN_BARS = 4
    _LIQUIDITY_UPPER_PRESTART_BARS = 2
    _LIQUIDITY_LOWER_PRESTART_BARS = 1

    def __init__(self) -> None:
        self._prepared_frame_cache: dict[tuple[object, ...], _PreparedStage2Frame | BeeBiteStage2Result] = {}

    def detect(
        self,
        *,
        symbol: str,
        frame: pd.DataFrame,
        stage1: BeeBiteStage1Result,
        analysis_end_timestamp: int | None = None,
    ) -> BeeBiteStage2Result:
        if not stage1.passed:
            return BeeBiteStage2Result(symbol=symbol, passed=False, reason="stage1_not_passed")
        if stage1.pump_peak_timestamp is None or stage1.pump_peak_price is None:
            return BeeBiteStage2Result(symbol=symbol, passed=False, reason="stage1_peak_missing")

        prepared = self._prepare_frame(symbol=symbol, frame=frame)
        if isinstance(prepared, BeeBiteStage2Result):
            return prepared

        peak_idx = self._resolve_index_by_timestamp(
            timestamps=prepared.timestamps,
            timestamp=int(stage1.pump_peak_timestamp),
        )
        if peak_idx is None:
            return BeeBiteStage2Result(symbol=symbol, passed=False, reason="stage1_peak_not_in_frame")
        analysis_end_idx = len(prepared.timestamps) - 1
        if analysis_end_timestamp is not None:
            resolved_end_idx = self._resolve_index_by_timestamp(
                timestamps=prepared.timestamps,
                timestamp=int(analysis_end_timestamp),
            )
            if resolved_end_idx is not None:
                analysis_end_idx = resolved_end_idx
        if analysis_end_idx <= peak_idx:
            return BeeBiteStage2Result(
                symbol=symbol,
                passed=False,
                reason="no_data_after_peak",
                analysis_start_timestamp=int(prepared.timestamps[peak_idx]),
                analysis_end_timestamp=int(prepared.timestamps[min(peak_idx, analysis_end_idx)]),
            )

        initial_peak_price = max(
            float(stage1.pump_peak_price),
            self._effective_high_at(prepared=prepared, idx=peak_idx),
        )
        confirmed_highs: list[BeeBiteStage2ConfirmedHigh] = [
            BeeBiteStage2ConfirmedHigh(
                idx=peak_idx,
                timestamp=int(prepared.timestamps[peak_idx]),
                price=initial_peak_price,
            )
        ]
        local_ranges: list[BeeBiteStage2Range] = []

        current_high_idx = peak_idx
        current_high_price = initial_peak_price
        current_range_start_idx = peak_idx + 1

        for idx in range(peak_idx + 1, analysis_end_idx + 1):
            if not self._is_new_confirmed_high(
                prepared=prepared,
                current_high_idx=current_high_idx,
                current_high_price=current_high_price,
                breakout_idx=idx,
            ):
                continue

            local_range = self._build_local_range(
                prepared=prepared,
                current_range_start_idx=current_range_start_idx,
                current_range_end_idx=idx - 1,
                confirmed_high=confirmed_highs[-1],
            )
            if local_range is not None:
                local_ranges.append(local_range)

            breakout_price = self._confirmed_high_price_at(
                prepared=prepared,
                idx=idx,
                previous_high_price=current_high_price,
            )
            confirmed_highs.append(
                BeeBiteStage2ConfirmedHigh(
                    idx=idx,
                    timestamp=int(prepared.timestamps[idx]),
                    price=breakout_price,
                )
            )
            current_high_idx = idx
            current_high_price = breakout_price
            current_range_start_idx = idx + 1

        trailing_range = self._build_local_range(
            prepared=prepared,
            current_range_start_idx=current_range_start_idx,
            current_range_end_idx=analysis_end_idx,
            confirmed_high=confirmed_highs[-1],
        )
        if trailing_range is not None:
            local_ranges.append(trailing_range)

        if not local_ranges:
            return BeeBiteStage2Result(
                symbol=symbol,
                passed=False,
                reason="no_ranges_after_peak",
                analysis_start_timestamp=int(prepared.timestamps[peak_idx]),
                analysis_end_timestamp=int(prepared.timestamps[analysis_end_idx]),
                confirmed_highs=tuple(confirmed_highs),
            )

        if len(confirmed_highs) > len(local_ranges):
            confirmed_highs = confirmed_highs[: len(local_ranges)]

        confirmed_highs, local_ranges = self._refine_confirmed_highs_and_ranges(
            prepared=prepared,
            confirmed_highs=confirmed_highs,
            local_ranges=local_ranges,
        )
        merged_ranges = self._merge_ranges(local_ranges)
        active_box = merged_ranges[-1]
        liquidity_zones = self._resolve_active_liquidity_zones(
            prepared=prepared,
            active_box=active_box,
        )

        return BeeBiteStage2Result(
            symbol=symbol,
            passed=True,
            reason="passed",
            analysis_start_timestamp=int(prepared.timestamps[peak_idx]),
            analysis_end_timestamp=int(prepared.timestamps[analysis_end_idx]),
            confirmed_highs=tuple(confirmed_highs),
            local_ranges=tuple(local_ranges),
            merged_ranges=tuple(merged_ranges),
            liquidity_zones=tuple(liquidity_zones),
            box_start_timestamp=active_box.start_timestamp,
            box_end_timestamp=active_box.end_timestamp,
            box_high=active_box.high,
            box_low=active_box.low,
        )

    def _prepare_frame(
        self,
        *,
        symbol: str,
        frame: pd.DataFrame,
    ) -> _PreparedStage2Frame | BeeBiteStage2Result:
        cache_key = self._build_frame_cache_key(frame)
        cached = self._prepared_frame_cache.get(cache_key)
        if cached is not None:
            return cached
        required_columns = {"timestamp", "open", "high", "low", "close"}
        if frame.empty:
            result = BeeBiteStage2Result(symbol=symbol, passed=False, reason="empty_frame")
            self._remember_prepared_frame(cache_key, result)
            return result
        if not required_columns.issubset(frame.columns):
            result = BeeBiteStage2Result(symbol=symbol, passed=False, reason="missing_columns")
            self._remember_prepared_frame(cache_key, result)
            return result

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close"]].copy()
        for column in ("timestamp", "open", "high", "low", "close"):
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
        prepared = prepared.reset_index(drop=True)
        if len(prepared) < 3:
            result = BeeBiteStage2Result(symbol=symbol, passed=False, reason="insufficient_history")
            self._remember_prepared_frame(cache_key, result)
            return result

        prepared_frame = _PreparedStage2Frame(
            timestamps=prepared["timestamp"].astype("int64").to_numpy(),
            opens=prepared["open"].astype("float64").to_numpy(),
            highs=prepared["high"].astype("float64").to_numpy(),
            lows=prepared["low"].astype("float64").to_numpy(),
            closes=prepared["close"].astype("float64").to_numpy(),
        )
        self._remember_prepared_frame(cache_key, prepared_frame)
        return prepared_frame

    @staticmethod
    def _build_frame_cache_key(frame: pd.DataFrame) -> tuple[object, ...]:
        if frame.empty or "timestamp" not in frame.columns:
            return (id(frame), len(frame), None, None)
        return (
            id(frame),
            len(frame),
            frame["timestamp"].iloc[0],
            frame["timestamp"].iloc[-1],
        )

    def _remember_prepared_frame(
        self,
        cache_key: tuple[object, ...],
        prepared_frame: _PreparedStage2Frame | BeeBiteStage2Result,
    ) -> None:
        if len(self._prepared_frame_cache) >= 64:
            self._prepared_frame_cache.clear()
        self._prepared_frame_cache[cache_key] = prepared_frame

    @staticmethod
    def _resolve_index_by_timestamp(*, timestamps: np.ndarray, timestamp: int) -> int | None:
        matches = np.where(timestamps == timestamp)[0]
        if matches.size == 0:
            return None
        return int(matches[-1])

    def _build_local_range(
        self,
        *,
        prepared: _PreparedStage2Frame,
        current_range_start_idx: int,
        current_range_end_idx: int,
        confirmed_high: BeeBiteStage2ConfirmedHigh,
    ) -> BeeBiteStage2Range | None:
        if current_range_start_idx > current_range_end_idx:
            return None

        effective_lows = self._effective_lows(prepared)[current_range_start_idx : current_range_end_idx + 1]
        range_low = self._resolve_balance_low(
            prepared=prepared,
            segment_start_idx=current_range_start_idx,
            segment_end_idx=current_range_end_idx,
            range_high=confirmed_high.price,
            effective_lows=effective_lows,
        )
        return BeeBiteStage2Range(
            start_idx=current_range_start_idx,
            start_timestamp=int(prepared.timestamps[current_range_start_idx]),
            end_idx=current_range_end_idx,
            end_timestamp=int(prepared.timestamps[current_range_end_idx]),
            confirmed_high_idx=confirmed_high.idx,
            confirmed_high_timestamp=confirmed_high.timestamp,
            confirmed_high_price=confirmed_high.price,
            high=confirmed_high.price,
            low=range_low,
            bars=(current_range_end_idx - current_range_start_idx) + 1,
        )

    def _refine_confirmed_highs_and_ranges(
        self,
        *,
        prepared: _PreparedStage2Frame,
        confirmed_highs: list[BeeBiteStage2ConfirmedHigh],
        local_ranges: list[BeeBiteStage2Range],
    ) -> tuple[list[BeeBiteStage2ConfirmedHigh], list[BeeBiteStage2Range]]:
        refined_highs: list[BeeBiteStage2ConfirmedHigh] = []
        refined_ranges: list[BeeBiteStage2Range] = []

        pair_count = min(len(confirmed_highs), len(local_ranges))
        for confirmed_high, local_range in zip(confirmed_highs[:pair_count], local_ranges[:pair_count], strict=False):
            resolved_high = self._resolve_balance_high(
                prepared=prepared,
                anchor_high=confirmed_high,
                segment_end_idx=local_range.end_idx,
            )
            refined_high = BeeBiteStage2ConfirmedHigh(
                idx=resolved_high.idx,
                timestamp=resolved_high.timestamp,
                price=resolved_high.price,
            )
            refined_range = BeeBiteStage2Range(
                start_idx=local_range.start_idx,
                start_timestamp=local_range.start_timestamp,
                end_idx=local_range.end_idx,
                end_timestamp=local_range.end_timestamp,
                confirmed_high_idx=refined_high.idx,
                confirmed_high_timestamp=refined_high.timestamp,
                confirmed_high_price=refined_high.price,
                high=refined_high.price,
                low=local_range.low,
                bars=local_range.bars,
            )
            refined_highs.append(refined_high)
            refined_ranges.append(refined_range)

        return refined_highs, refined_ranges

    def _resolve_balance_high(
        self,
        *,
        prepared: _PreparedStage2Frame,
        anchor_high: BeeBiteStage2ConfirmedHigh,
        segment_end_idx: int,
    ) -> BeeBiteStage2ConfirmedHigh:
        if segment_end_idx <= anchor_high.idx:
            return anchor_high

        segment_highs = prepared.highs[anchor_high.idx : segment_end_idx + 1]
        if segment_highs.size == 0:
            return anchor_high

        segment_lows = prepared.lows[anchor_high.idx : segment_end_idx + 1]
        candle_sizes = segment_highs - segment_lows
        mean_candle_size = float(np.mean(candle_sizes)) if candle_sizes.size > 0 else 0.0
        segment_high = float(np.max(segment_highs))
        segment_low = float(np.min(segment_lows))
        range_height = max(segment_high - segment_low, self._EPSILON)
        tolerance = max(
            mean_candle_size * self._LIQUIDITY_CLUSTER_TOLERANCE_TO_CANDLE,
            range_height * self._LIQUIDITY_CLUSTER_TOLERANCE_TO_RANGE,
            self._EPSILON,
        )
        threshold = segment_high - (range_height * self._LIQUIDITY_ZONE_RELATIVE_MARGIN)
        swing_highs = self._extract_swing_highs(
            highs=segment_highs,
            segment_start_idx=anchor_high.idx,
        )
        filtered_points = [point for point in swing_highs if point[1] >= threshold]
        if not filtered_points:
            return anchor_high

        best_cluster: list[tuple[int, float]] | None = None
        best_score: tuple[int, int, float, float] | None = None
        for anchor_idx, anchor_price in filtered_points:
            cluster = [
                (point_idx, point_price)
                for point_idx, point_price in filtered_points
                if abs(point_price - anchor_price) <= tolerance
            ]
            if len(cluster) < self._LIQUIDITY_MIN_TOUCHES:
                continue

            cluster_prices = [price for _, price in cluster]
            cluster_min = float(min(cluster_prices))
            cluster_max = float(max(cluster_prices))
            last_touch_idx = max(point_idx for point_idx, _ in cluster)
            score: tuple[int, int, float, float] = (
                len(cluster),
                last_touch_idx,
                -float(cluster_max - cluster_min),
                cluster_max,
            )
            if best_score is not None and score <= best_score:
                continue
            best_score = score
            best_cluster = cluster

        if best_cluster is None:
            return anchor_high

        top_idx, top_price = max(best_cluster, key=lambda item: (item[1], item[0]))
        if top_price <= (anchor_high.price + self._EPSILON):
            return anchor_high

        return BeeBiteStage2ConfirmedHigh(
            idx=int(top_idx),
            timestamp=int(prepared.timestamps[top_idx]),
            price=float(top_price),
        )

    def _merge_ranges(self, ranges: list[BeeBiteStage2Range]) -> list[BeeBiteStage2MergedRange]:
        merged: list[BeeBiteStage2MergedRange] = []
        for current in ranges:
            if not merged:
                merged.append(
                    BeeBiteStage2MergedRange(
                        start_idx=current.start_idx,
                        start_timestamp=current.start_timestamp,
                        end_idx=current.end_idx,
                        end_timestamp=current.end_timestamp,
                        high=current.high,
                        low=current.low,
                        bars=current.bars,
                        source_ranges=1,
                        confirmed_high_timestamps=(current.confirmed_high_timestamp,),
                        confirmed_high_prices=(current.confirmed_high_price,),
                    )
                )
                continue

            previous = merged[-1]
            if self._ranges_overlap_enough(previous=previous, current=current):
                merged[-1] = BeeBiteStage2MergedRange(
                    start_idx=previous.start_idx,
                    start_timestamp=previous.start_timestamp,
                    end_idx=current.end_idx,
                    end_timestamp=current.end_timestamp,
                    high=max(previous.high, current.high),
                    low=min(previous.low, current.low),
                    bars=previous.bars + current.bars,
                    source_ranges=previous.source_ranges + 1,
                    confirmed_high_timestamps=previous.confirmed_high_timestamps + (current.confirmed_high_timestamp,),
                    confirmed_high_prices=previous.confirmed_high_prices + (current.confirmed_high_price,),
                )
                continue

            merged.append(
                BeeBiteStage2MergedRange(
                    start_idx=current.start_idx,
                    start_timestamp=current.start_timestamp,
                    end_idx=current.end_idx,
                    end_timestamp=current.end_timestamp,
                    high=current.high,
                    low=current.low,
                    bars=current.bars,
                    source_ranges=1,
                    confirmed_high_timestamps=(current.confirmed_high_timestamp,),
                    confirmed_high_prices=(current.confirmed_high_price,),
                )
            )
        return merged

    def _ranges_overlap_enough(
        self,
        *,
        previous: BeeBiteStage2MergedRange,
        current: BeeBiteStage2Range,
    ) -> bool:
        overlap = min(previous.high, current.high) - max(previous.low, current.low)
        if overlap <= self._EPSILON:
            return False
        previous_height = max(previous.high - previous.low, self._EPSILON)
        current_height = max(current.high - current.low, self._EPSILON)
        overlap_ratio = overlap / min(previous_height, current_height)
        return overlap_ratio >= self._RANGE_OVERLAP_THRESHOLD

    def _is_new_confirmed_high(
        self,
        *,
        prepared: _PreparedStage2Frame,
        current_high_idx: int,
        current_high_price: float,
        breakout_idx: int,
    ) -> bool:
        bars_since_high = breakout_idx - current_high_idx
        candle_sizes = prepared.highs[current_high_idx : breakout_idx + 1] - prepared.lows[current_high_idx : breakout_idx + 1]
        mean_candle_size = float(np.mean(candle_sizes)) if candle_sizes.size > 0 else 0.0

        breakout_close = float(prepared.closes[breakout_idx])
        breakout_open = float(prepared.opens[breakout_idx])
        breakout_high = float(prepared.highs[breakout_idx])
        breakout_low = float(prepared.lows[breakout_idx])
        body_high = max(breakout_open, breakout_close)
        spread = max(breakout_high - breakout_low, self._EPSILON)
        body_ratio = abs(breakout_close - breakout_open) / spread

        if breakout_close > (current_high_price + self._EPSILON) and body_ratio >= self._MIN_BODY_RATIO_FOR_BREAKOUT:
            breakout_excess = breakout_high - current_high_price
            if breakout_excess <= self._EPSILON:
                return False
            close_threshold_multiplier = (
                self._ADJACENT_BREAKOUT_MULTIPLIER if bars_since_high <= 2 else self._BODY_CLOSE_BREAKOUT_MULTIPLIER
            )
            close_threshold = max(mean_candle_size * close_threshold_multiplier, self._EPSILON)
            return breakout_excess >= close_threshold

        breakout_price = self._effective_high_at(prepared=prepared, idx=breakout_idx)
        breakout_excess = breakout_price - current_high_price
        if breakout_excess <= self._EPSILON:
            return False

        threshold_multiplier = (
            self._ADJACENT_BREAKOUT_MULTIPLIER if bars_since_high <= 2 else self._DEFAULT_BREAKOUT_MULTIPLIER
        )
        threshold = max(mean_candle_size * threshold_multiplier, self._EPSILON)
        if breakout_excess < threshold:
            return False

        return body_high > (current_high_price + self._EPSILON) and body_ratio >= self._MIN_BODY_RATIO_FOR_BREAKOUT

    def _confirmed_high_price_at(
        self,
        *,
        prepared: _PreparedStage2Frame,
        idx: int,
        previous_high_price: float,
    ) -> float:
        breakout_close = float(prepared.closes[idx])
        breakout_open = float(prepared.opens[idx])
        breakout_high = float(prepared.highs[idx])
        breakout_low = float(prepared.lows[idx])
        spread = max(breakout_high - breakout_low, self._EPSILON)
        body_ratio = abs(breakout_close - breakout_open) / spread
        if breakout_close > (previous_high_price + self._EPSILON) and body_ratio >= self._MIN_BODY_RATIO_FOR_BREAKOUT:
            return breakout_high
        return self._effective_high_at(prepared=prepared, idx=idx)

    @staticmethod
    def _effective_high_at(*, prepared: _PreparedStage2Frame, idx: int) -> float:
        body_high = max(float(prepared.opens[idx]), float(prepared.closes[idx]))
        body_size = abs(float(prepared.closes[idx]) - float(prepared.opens[idx]))
        upper_wick = float(prepared.highs[idx]) - body_high
        if upper_wick > body_size:
            return body_high
        return float(prepared.highs[idx])

    @staticmethod
    def _effective_lows(prepared: _PreparedStage2Frame) -> np.ndarray:
        # Stage-2 box lows should reflect the obvious swing low that traders place
        # stops behind, so we preserve wick lows instead of smoothing them to body
        # lows as we do for some peak-related logic.
        return prepared.lows.copy()

    def _resolve_balance_low(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_start_idx: int,
        segment_end_idx: int,
        range_high: float,
        effective_lows: np.ndarray,
    ) -> float:
        del prepared, segment_end_idx
        if effective_lows.size == 0:
            return float(range_high)

        raw_min_low = float(np.min(effective_lows))
        if effective_lows.size <= 2:
            return raw_min_low

        swing_lows = self._extract_swing_lows(
            effective_lows=effective_lows,
            segment_start_idx=segment_start_idx,
        )
        if swing_lows:
            return float(min(item[1] for item in swing_lows))
        return raw_min_low

    @staticmethod
    def _extract_swing_lows(
        *,
        effective_lows: np.ndarray,
        segment_start_idx: int,
    ) -> list[tuple[int, float]]:
        segment_length = int(effective_lows.size)
        if segment_length == 0:
            return []
        if segment_length < 3:
            return [(segment_start_idx + idx, float(price)) for idx, price in enumerate(effective_lows)]

        swing_lows: list[tuple[int, float]] = []
        for local_idx in range(1, segment_length - 1):
            current_low = float(effective_lows[local_idx])
            prev_low = float(effective_lows[local_idx - 1])
            next_low = float(effective_lows[local_idx + 1])
            if current_low <= prev_low and current_low <= next_low and (current_low < prev_low or current_low < next_low):
                swing_lows.append((segment_start_idx + local_idx, current_low))

        if swing_lows:
            return swing_lows

        lowest_idx = int(np.argmin(effective_lows))
        return [(segment_start_idx + lowest_idx, float(effective_lows[lowest_idx]))]

    def _resolve_active_liquidity_zones(
        self,
        *,
        prepared: _PreparedStage2Frame,
        active_box: BeeBiteStage2MergedRange,
    ) -> list[BeeBiteStage2LiquidityZone]:
        segment_start_idx = int(active_box.start_idx)
        segment_end_idx = int(active_box.end_idx)
        if segment_end_idx <= segment_start_idx:
            return []

        segment_highs = prepared.highs[segment_start_idx : segment_end_idx + 1]
        segment_lows = prepared.lows[segment_start_idx : segment_end_idx + 1]
        candle_sizes = segment_highs - segment_lows
        mean_candle_size = float(np.mean(candle_sizes)) if candle_sizes.size > 0 else 0.0
        range_height = max(float(active_box.high - active_box.low), self._EPSILON)
        tolerance = max(
            mean_candle_size * self._LIQUIDITY_CLUSTER_TOLERANCE_TO_CANDLE,
            range_height * self._LIQUIDITY_CLUSTER_TOLERANCE_TO_RANGE,
            self._EPSILON,
        )

        upper_zones = self._resolve_sequential_liquidity_zones(
            prepared=prepared,
            segment_start_idx=segment_start_idx,
            segment_end_idx=segment_end_idx,
            box_low=float(active_box.low),
            box_high=float(active_box.high),
            tolerance=tolerance,
            side="upper",
            max_zones=2,
        )
        fallback_upper_zone = self._resolve_peak_backed_upper_liquidity_zone(
            prepared=prepared,
            segment_start_idx=segment_start_idx,
            segment_end_idx=segment_end_idx,
            confirmed_high_timestamps=active_box.confirmed_high_timestamps,
            box_high=float(active_box.high),
            tolerance=tolerance,
        )
        if fallback_upper_zone is not None:
            overlaps_existing = any(
                self._zones_overlap_enough(candidate=fallback_upper_zone, existing=existing)
                for existing in upper_zones
            )
            if not overlaps_existing:
                upper_zones = sorted(
                    [*upper_zones, fallback_upper_zone],
                    key=lambda zone: (zone.start_idx, zone.low),
                )
        elif not upper_zones:
            upper_zones = []

        lower_zones = self._resolve_sequential_liquidity_zones(
            prepared=prepared,
            segment_start_idx=segment_start_idx,
            segment_end_idx=segment_end_idx,
            box_low=float(active_box.low),
            box_high=float(active_box.high),
            tolerance=tolerance,
            side="lower",
            max_zones=3,
        )
        fallback_lower_zone = self._resolve_box_low_liquidity_zone(
            prepared=prepared,
            segment_start_idx=segment_start_idx,
            segment_end_idx=segment_end_idx,
            box_low=float(active_box.low),
            box_high=float(active_box.high),
            tolerance=tolerance,
        )
        if fallback_lower_zone is not None:
            overlapping_indices = [
                idx
                for idx, existing in enumerate(lower_zones)
                if self._zones_overlap_enough(candidate=fallback_lower_zone, existing=existing)
            ]
            if overlapping_indices:
                merged_lowers: list[BeeBiteStage2LiquidityZone] = []
                for idx, existing_zone in enumerate(lower_zones):
                    if idx not in overlapping_indices:
                        merged_lowers.append(existing_zone)
                        continue
                    effective_start_idx = min(existing_zone.start_idx, fallback_lower_zone.start_idx)
                    effective_end_idx = max(existing_zone.end_idx, fallback_lower_zone.end_idx)
                    merged_lowers.append(
                        BeeBiteStage2LiquidityZone(
                            side=existing_zone.side,
                            start_idx=effective_start_idx,
                            start_timestamp=int(prepared.timestamps[effective_start_idx]),
                            end_idx=effective_end_idx,
                            end_timestamp=int(prepared.timestamps[effective_end_idx]),
                            last_touch_idx=max(existing_zone.last_touch_idx, fallback_lower_zone.last_touch_idx),
                            last_touch_timestamp=int(
                                prepared.timestamps[max(existing_zone.last_touch_idx, fallback_lower_zone.last_touch_idx)]
                            ),
                            low=min(existing_zone.low, fallback_lower_zone.low),
                            high=max(existing_zone.high, fallback_lower_zone.high),
                            touch_count=max(existing_zone.touch_count, fallback_lower_zone.touch_count),
                        )
                    )
                lower_zones = sorted(merged_lowers, key=lambda item: (item.start_idx, item.low))
            else:
                clipped_lowers: list[BeeBiteStage2LiquidityZone] = []
                for existing_zone in lower_zones:
                    effective_end_idx = existing_zone.end_idx
                    if existing_zone.start_idx < fallback_lower_zone.start_idx <= existing_zone.end_idx:
                        effective_end_idx = fallback_lower_zone.start_idx - 1
                    if effective_end_idx < existing_zone.start_idx:
                        continue
                    if ((effective_end_idx - existing_zone.start_idx) + 1) < self._LIQUIDITY_MIN_BARS:
                        continue
                    clipped_lowers.append(
                        BeeBiteStage2LiquidityZone(
                            side=existing_zone.side,
                            start_idx=existing_zone.start_idx,
                            start_timestamp=existing_zone.start_timestamp,
                            end_idx=effective_end_idx,
                            end_timestamp=int(prepared.timestamps[effective_end_idx]),
                            last_touch_idx=existing_zone.last_touch_idx,
                            last_touch_timestamp=existing_zone.last_touch_timestamp,
                            low=existing_zone.low,
                            high=existing_zone.high,
                            touch_count=existing_zone.touch_count,
                        )
                    )
                clipped_lowers.append(fallback_lower_zone)
                lower_zones = sorted(clipped_lowers, key=lambda item: (item.start_idx, item.low))
        zones: list[BeeBiteStage2LiquidityZone] = []
        zones.extend(upper_zones)
        zones.extend(lower_zones)
        return zones

    def _resolve_sequential_liquidity_zones(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_start_idx: int,
        segment_end_idx: int,
        box_low: float,
        box_high: float,
        tolerance: float,
        side: str,
        max_zones: int,
    ) -> list[BeeBiteStage2LiquidityZone]:
        range_height = max(box_high - box_low, self._EPSILON)
        if side == "upper":
            threshold = box_high - (range_height * self._LIQUIDITY_ZONE_RELATIVE_MARGIN)
            side_tolerance = tolerance
            swing_points = self._extract_swing_highs(
                highs=prepared.highs[segment_start_idx : segment_end_idx + 1],
                segment_start_idx=segment_start_idx,
            )
            filtered_points = [point for point in swing_points if point[1] >= threshold]
        else:
            threshold = box_low + (range_height * self._LIQUIDITY_ZONE_RELATIVE_MARGIN)
            side_tolerance = max(tolerance * 0.7, self._EPSILON)
            swing_points = self._extract_swing_lows(
                effective_lows=prepared.lows[segment_start_idx : segment_end_idx + 1],
                segment_start_idx=segment_start_idx,
            )
            filtered_points = [point for point in swing_points if point[1] <= threshold]

        filtered_points = sorted(filtered_points, key=lambda item: item[0])
        if len(filtered_points) < self._LIQUIDITY_MIN_TOUCHES:
            return []

        raw_zones: list[tuple[float, BeeBiteStage2LiquidityZone]] = []
        current_cluster: list[tuple[int, float]] = [filtered_points[0]]
        for point_idx, point_price in filtered_points[1:]:
            cluster_prices = [price for _, price in current_cluster]
            if side == "lower":
                cluster_anchor = float(min(cluster_prices))
            else:
                cluster_anchor = float(np.median(cluster_prices))
            if abs(point_price - cluster_anchor) <= side_tolerance:
                current_cluster.append((point_idx, point_price))
                continue
            candidate = self._build_liquidity_zone_from_cluster(
                prepared=prepared,
                segment_start_idx=segment_start_idx,
                segment_end_idx=segment_end_idx,
                cluster=current_cluster,
                tolerance=side_tolerance,
                side=side,
            )
            if candidate is not None:
                cluster_prices = [price for _, price in current_cluster]
                score = float(np.mean(cluster_prices))
                raw_zones.append((score, candidate))
            current_cluster = [(point_idx, point_price)]

        trailing_candidate = self._build_liquidity_zone_from_cluster(
            prepared=prepared,
            segment_start_idx=segment_start_idx,
            segment_end_idx=segment_end_idx,
            cluster=current_cluster,
            tolerance=side_tolerance,
            side=side,
        )
        if trailing_candidate is not None:
            cluster_prices = [price for _, price in current_cluster]
            score = float(np.mean(cluster_prices))
            raw_zones.append((score, trailing_candidate))

        if not raw_zones:
            return []

        ordered = sorted((zone for _, zone in raw_zones), key=lambda zone: (zone.start_idx, zone.low))
        clipped: list[BeeBiteStage2LiquidityZone] = []
        for zone in ordered:
            effective_end_idx = zone.end_idx
            if effective_end_idx < zone.start_idx:
                continue
            if ((effective_end_idx - zone.start_idx) + 1) < self._LIQUIDITY_MIN_BARS:
                continue
            clipped.append(
                BeeBiteStage2LiquidityZone(
                    side=zone.side,
                    start_idx=zone.start_idx,
                    start_timestamp=zone.start_timestamp,
                    end_idx=effective_end_idx,
                    end_timestamp=int(prepared.timestamps[effective_end_idx]),
                    last_touch_idx=zone.last_touch_idx,
                    last_touch_timestamp=zone.last_touch_timestamp,
                    low=zone.low,
                    high=zone.high,
                    touch_count=zone.touch_count,
                )
            )

        if side == "upper":
            ranked = sorted(
                clipped,
                key=lambda zone: (zone.touch_count, zone.last_touch_idx, zone.high),
                reverse=True,
            )
            return ranked[:max_zones]

        selected_lowers: list[BeeBiteStage2LiquidityZone] = []
        current_ceiling: float | None = None
        for zone in clipped:
            if current_ceiling is not None and zone.high > (current_ceiling + (tolerance * 0.25)):
                continue
            selected_lowers.append(zone)
            current_ceiling = zone.high if current_ceiling is None else min(current_ceiling, zone.high)
            if len(selected_lowers) >= max_zones:
                break

        return selected_lowers

    def _build_liquidity_zone_from_cluster(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_start_idx: int,
        segment_end_idx: int,
        cluster: list[tuple[int, float]],
        tolerance: float,
        side: str,
    ) -> BeeBiteStage2LiquidityZone | None:
        if len(cluster) < self._LIQUIDITY_MIN_TOUCHES:
            return None

        ordered_touches = sorted(point_idx for point_idx, _ in cluster)
        cluster_prices = [price for _, price in cluster]
        cluster_low = float(min(cluster_prices))
        cluster_high = float(max(cluster_prices))
        prestart_bars = self._LIQUIDITY_UPPER_PRESTART_BARS if side == "upper" else self._LIQUIDITY_LOWER_PRESTART_BARS
        start_idx = max(segment_start_idx, int(ordered_touches[0]) - prestart_bars)
        last_touch_idx = int(ordered_touches[-1])

        if side == "lower":
            support_window_low = float(np.min(prepared.lows[start_idx : last_touch_idx + 1]))
            cluster_low = min(cluster_low, support_window_low)

        if ((last_touch_idx - ordered_touches[0]) + 1) < self._LIQUIDITY_MIN_BARS:
            return None
        zone_low, zone_high = self._project_liquidity_zone_bounds(
            level_low=cluster_low,
            level_high=cluster_high,
            tolerance=tolerance,
            side=side,
        )
        sweep_idx = self._resolve_liquidity_zone_sweep_idx(
            prepared=prepared,
            segment_end_idx=segment_end_idx,
            last_touch_idx=last_touch_idx,
            zone_low=zone_low,
            zone_high=zone_high,
            side=side,
        )
        effective_end_idx = (sweep_idx - 1) if sweep_idx is not None else segment_end_idx
        if effective_end_idx < start_idx:
            return None
        if ((effective_end_idx - start_idx) + 1) < self._LIQUIDITY_MIN_BARS:
            return None

        return BeeBiteStage2LiquidityZone(
            side=side,
            start_idx=start_idx,
            start_timestamp=int(prepared.timestamps[start_idx]),
            end_idx=effective_end_idx,
            end_timestamp=int(prepared.timestamps[effective_end_idx]),
            last_touch_idx=last_touch_idx,
            last_touch_timestamp=int(prepared.timestamps[last_touch_idx]),
            low=zone_low,
            high=zone_high,
            touch_count=len(cluster),
        )

    def _resolve_peak_backed_upper_liquidity_zone(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_start_idx: int,
        segment_end_idx: int,
        confirmed_high_timestamps: tuple[int, ...],
        box_high: float,
        tolerance: float,
    ) -> BeeBiteStage2LiquidityZone | None:
        if segment_end_idx <= segment_start_idx:
            return None

        anchor_idx = None
        if confirmed_high_timestamps:
            anchor_timestamp = int(confirmed_high_timestamps[-1])
            anchor_idx = self._resolve_index_by_timestamp(timestamps=prepared.timestamps, timestamp=anchor_timestamp)

        if anchor_idx is None:
            peak_local_idx = int(np.argmax(prepared.highs[segment_start_idx : segment_end_idx + 1]))
            anchor_idx = segment_start_idx + peak_local_idx

        start_idx = max(0, anchor_idx - self._LIQUIDITY_UPPER_PRESTART_BARS)
        zone_low, zone_high = self._project_liquidity_zone_bounds(
            level_low=box_high,
            level_high=box_high,
            tolerance=tolerance,
            side="upper",
        )
        sweep_idx = self._resolve_liquidity_zone_sweep_idx(
            prepared=prepared,
            segment_end_idx=segment_end_idx,
            last_touch_idx=anchor_idx,
            zone_low=zone_low,
            zone_high=zone_high,
            side="upper",
        )
        effective_end_idx = (sweep_idx - 1) if sweep_idx is not None else segment_end_idx
        if effective_end_idx < start_idx:
            return None
        if ((effective_end_idx - start_idx) + 1) < self._LIQUIDITY_MIN_BARS:
            return None

        return BeeBiteStage2LiquidityZone(
            side="upper",
            start_idx=start_idx,
            start_timestamp=int(prepared.timestamps[start_idx]),
            end_idx=effective_end_idx,
            end_timestamp=int(prepared.timestamps[effective_end_idx]),
            last_touch_idx=anchor_idx,
            last_touch_timestamp=int(prepared.timestamps[anchor_idx]),
            low=zone_low,
            high=zone_high,
            touch_count=1,
        )

    def _resolve_box_low_liquidity_zone(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_start_idx: int,
        segment_end_idx: int,
        box_low: float,
        box_high: float,
        tolerance: float,
    ) -> BeeBiteStage2LiquidityZone | None:
        range_height = max(box_high - box_low, self._EPSILON)
        boundary_band_high = box_low + max(range_height * 0.2, tolerance * 2.0, self._EPSILON)
        touch_indices = [
            idx
            for idx in range(segment_start_idx, segment_end_idx + 1)
            if (box_low - tolerance) <= float(prepared.lows[idx]) <= boundary_band_high
        ]
        if len(touch_indices) < 2:
            return None

        start_idx = max(segment_start_idx, touch_indices[0] - self._LIQUIDITY_LOWER_PRESTART_BARS)
        last_touch_idx = int(touch_indices[-1])
        if ((last_touch_idx - start_idx) + 1) < self._LIQUIDITY_MIN_BARS:
            return None

        touch_prices = [float(prepared.lows[idx]) for idx in touch_indices]
        touch_low = float(min(touch_prices))
        touch_high = float(max(touch_prices))
        zone_low, zone_high = self._project_liquidity_zone_bounds(
            level_low=touch_low,
            level_high=touch_high,
            tolerance=tolerance,
            side="lower",
        )
        sweep_idx = self._resolve_liquidity_zone_sweep_idx(
            prepared=prepared,
            segment_end_idx=segment_end_idx,
            last_touch_idx=last_touch_idx,
            zone_low=zone_low,
            zone_high=zone_high,
            side="lower",
        )
        effective_end_idx = (sweep_idx - 1) if sweep_idx is not None else segment_end_idx
        if effective_end_idx < start_idx:
            return None
        if ((effective_end_idx - start_idx) + 1) < self._LIQUIDITY_MIN_BARS:
            return None

        return BeeBiteStage2LiquidityZone(
            side="lower",
            start_idx=start_idx,
            start_timestamp=int(prepared.timestamps[start_idx]),
            end_idx=effective_end_idx,
            end_timestamp=int(prepared.timestamps[effective_end_idx]),
            last_touch_idx=last_touch_idx,
            last_touch_timestamp=int(prepared.timestamps[last_touch_idx]),
            low=zone_low,
            high=zone_high,
            touch_count=len(touch_indices),
        )

    def _zones_overlap_enough(
        self,
        *,
        candidate: BeeBiteStage2LiquidityZone,
        existing: BeeBiteStage2LiquidityZone,
    ) -> bool:
        overlap = min(candidate.high, existing.high) - max(candidate.low, existing.low)
        if overlap <= self._EPSILON:
            return False
        candidate_height = max(candidate.high - candidate.low, self._EPSILON)
        existing_height = max(existing.high - existing.low, self._EPSILON)
        overlap_ratio = overlap / min(candidate_height, existing_height)
        return overlap_ratio >= self._RANGE_OVERLAP_THRESHOLD

    def _project_liquidity_zone_bounds(
        self,
        *,
        level_low: float,
        level_high: float,
        tolerance: float,
        side: str,
    ) -> tuple[float, float]:
        zone_width = max((level_high - level_low) * self._LIQUIDITY_ZONE_WIDTH_MULTIPLIER, tolerance, self._EPSILON)
        if side == "upper":
            zone_offset = max(tolerance * self._LIQUIDITY_UPPER_ZONE_OFFSET_MULTIPLIER, self._EPSILON)
            zone_low = float(level_high + zone_offset)
            return zone_low, float(zone_low + zone_width)
        zone_offset = max(tolerance * self._LIQUIDITY_ZONE_OFFSET_MULTIPLIER, self._EPSILON)
        zone_high = float(level_low - zone_offset)
        return float(zone_high - zone_width), zone_high

    def _resolve_liquidity_zone_sweep_idx(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_end_idx: int,
        last_touch_idx: int,
        zone_low: float,
        zone_high: float,
        side: str,
    ) -> int | None:
        if last_touch_idx >= segment_end_idx:
            return None

        if side == "upper":
            for idx in range(last_touch_idx + 1, segment_end_idx + 1):
                if float(prepared.highs[idx]) >= (zone_high - self._EPSILON):
                    return idx
            return None

        for idx in range(last_touch_idx + 1, segment_end_idx + 1):
            if float(prepared.lows[idx]) <= (zone_low + self._EPSILON):
                return idx
        return None

    @staticmethod
    def _extract_swing_highs(
        *,
        highs: np.ndarray,
        segment_start_idx: int,
    ) -> list[tuple[int, float]]:
        segment_length = int(highs.size)
        if segment_length == 0:
            return []
        if segment_length < 3:
            return [(segment_start_idx + idx, float(price)) for idx, price in enumerate(highs)]

        swing_highs: list[tuple[int, float]] = []
        for local_idx in range(1, segment_length - 1):
            current_high = float(highs[local_idx])
            prev_high = float(highs[local_idx - 1])
            next_high = float(highs[local_idx + 1])
            if current_high >= prev_high and current_high >= next_high and (current_high > prev_high or current_high > next_high):
                swing_highs.append((segment_start_idx + local_idx, current_high))

        if swing_highs:
            return swing_highs

        highest_idx = int(np.argmax(highs))
        return [(segment_start_idx + highest_idx, float(highs[highest_idx]))]
