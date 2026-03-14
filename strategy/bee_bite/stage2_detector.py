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
        required_columns = {"timestamp", "open", "high", "low", "close"}
        if frame.empty:
            return BeeBiteStage2Result(symbol=symbol, passed=False, reason="empty_frame")
        if not required_columns.issubset(frame.columns):
            return BeeBiteStage2Result(symbol=symbol, passed=False, reason="missing_columns")

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close"]].copy()
        for column in ("timestamp", "open", "high", "low", "close"):
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
        prepared = prepared.reset_index(drop=True)
        if len(prepared) < 3:
            return BeeBiteStage2Result(symbol=symbol, passed=False, reason="insufficient_history")

        return _PreparedStage2Frame(
            timestamps=prepared["timestamp"].astype("int64").to_numpy(),
            opens=prepared["open"].astype("float64").to_numpy(),
            highs=prepared["high"].astype("float64").to_numpy(),
            lows=prepared["low"].astype("float64").to_numpy(),
            closes=prepared["close"].astype("float64").to_numpy(),
        )

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

    def _effective_high_at(self, *, prepared: _PreparedStage2Frame, idx: int) -> float:
        body_high = max(float(prepared.opens[idx]), float(prepared.closes[idx]))
        body_size = abs(float(prepared.closes[idx]) - float(prepared.opens[idx]))
        upper_wick = float(prepared.highs[idx]) - body_high
        if upper_wick > body_size:
            return body_high
        return float(prepared.highs[idx])

    def _effective_lows(self, prepared: _PreparedStage2Frame) -> np.ndarray:
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

    def _extract_swing_lows(
        self,
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

        upper_zone = self._resolve_liquidity_zone(
            prepared=prepared,
            segment_start_idx=segment_start_idx,
            segment_end_idx=segment_end_idx,
            box_low=float(active_box.low),
            box_high=float(active_box.high),
            tolerance=tolerance,
            side="upper",
        )
        lower_zone = self._resolve_liquidity_zone(
            prepared=prepared,
            segment_start_idx=segment_start_idx,
            segment_end_idx=segment_end_idx,
            box_low=float(active_box.low),
            box_high=float(active_box.high),
            tolerance=tolerance,
            side="lower",
        )
        return [zone for zone in (upper_zone, lower_zone) if zone is not None]

    def _resolve_liquidity_zone(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_start_idx: int,
        segment_end_idx: int,
        box_low: float,
        box_high: float,
        tolerance: float,
        side: str,
    ) -> BeeBiteStage2LiquidityZone | None:
        range_height = max(box_high - box_low, self._EPSILON)
        if side == "upper":
            threshold = box_high - (range_height * self._LIQUIDITY_ZONE_RELATIVE_MARGIN)
            swing_points = self._extract_swing_highs(
                highs=prepared.highs[segment_start_idx : segment_end_idx + 1],
                segment_start_idx=segment_start_idx,
            )
            filtered_points = [point for point in swing_points if point[1] >= threshold]
        else:
            threshold = box_low + (range_height * self._LIQUIDITY_ZONE_RELATIVE_MARGIN)
            swing_points = self._extract_swing_lows(
                effective_lows=prepared.lows[segment_start_idx : segment_end_idx + 1],
                segment_start_idx=segment_start_idx,
            )
            filtered_points = [point for point in swing_points if point[1] <= threshold]

        if len(filtered_points) < self._LIQUIDITY_MIN_TOUCHES:
            return None

        best_zone: BeeBiteStage2LiquidityZone | None = None
        best_score: tuple[int, int, float] | None = None
        for anchor_idx, anchor_price in filtered_points:
            cluster = [
                (point_idx, point_price)
                for point_idx, point_price in filtered_points
                if abs(point_price - anchor_price) <= tolerance
            ]
            if len(cluster) < self._LIQUIDITY_MIN_TOUCHES:
                continue

            cluster_prices = [price for _, price in cluster]
            cluster_low = float(min(cluster_prices))
            cluster_high = float(max(cluster_prices))
            last_touch_idx = max(point_idx for point_idx, _ in cluster)
            if self._is_liquidity_zone_swept(
                prepared=prepared,
                segment_end_idx=segment_end_idx,
                last_touch_idx=last_touch_idx,
                zone_low=cluster_low,
                zone_high=cluster_high,
                tolerance=tolerance,
                side=side,
            ):
                continue

            score = (
                len(cluster),
                last_touch_idx,
                -float(cluster_high - cluster_low),
            )
            if best_score is not None and score <= best_score:
                continue

            start_idx = min(point_idx for point_idx, _ in cluster)
            best_score = score
            best_zone = BeeBiteStage2LiquidityZone(
                side=side,
                start_idx=start_idx,
                start_timestamp=int(prepared.timestamps[start_idx]),
                end_idx=segment_end_idx,
                end_timestamp=int(prepared.timestamps[segment_end_idx]),
                last_touch_idx=last_touch_idx,
                last_touch_timestamp=int(prepared.timestamps[last_touch_idx]),
                low=cluster_low,
                high=cluster_high,
                touch_count=len(cluster),
            )
        return best_zone

    def _is_liquidity_zone_swept(
        self,
        *,
        prepared: _PreparedStage2Frame,
        segment_end_idx: int,
        last_touch_idx: int,
        zone_low: float,
        zone_high: float,
        tolerance: float,
        side: str,
    ) -> bool:
        if last_touch_idx >= segment_end_idx:
            return False

        sweep_tolerance = max(tolerance * self._LIQUIDITY_SWEEP_TOLERANCE_MULTIPLIER, self._EPSILON)
        if side == "upper":
            later_highs = prepared.highs[last_touch_idx + 1 : segment_end_idx + 1]
            return bool(later_highs.size > 0 and float(np.max(later_highs)) > (zone_high + sweep_tolerance))

        later_lows = prepared.lows[last_touch_idx + 1 : segment_end_idx + 1]
        return bool(later_lows.size > 0 and float(np.min(later_lows)) < (zone_low - sweep_tolerance))

    def _extract_swing_highs(
        self,
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
