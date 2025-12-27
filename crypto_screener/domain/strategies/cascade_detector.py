from __future__ import annotations

from dataclasses import dataclass
from math import inf

import numpy as np

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.cascade_level import CascadeLevel
from crypto_screener.domain.models.cascade_type import CascadeType
from crypto_screener.domain.models.swing import Swing, SwingType


@dataclass(frozen=True)
class CascadeDetectorConfig:
    atr_window: int
    touch_eps_natr: float
    min_pullback_natr: float
    min_pullback_bars: int
    min_gap_bars: int
    length_min: int
    pullback_second_ratio_min: float
    pullback_second_ratio_max: float
    pullback_next_ratio_min: float
    pullback_next_ratio_max: float

    @classmethod
    def from_strategy_config(cls, config: object) -> "CascadeDetectorConfig":
        return cls(
            atr_window=config.CASCADE_ATR_WINDOW,
            touch_eps_natr=config.CASCADE_TOUCH_EPS_NATR,
            min_pullback_natr=config.CASCADE_MIN_PULLBACK_NATR,
            min_pullback_bars=config.CASCADE_MIN_PULLBACK_BARS,
            min_gap_bars=config.CASCADE_MIN_GAP_BARS,
            length_min=config.CASCADE_LENGTH_MIN,
            pullback_second_ratio_min=config.CASCADE_PULLBACK_SECOND_RATIO_MIN,
            pullback_second_ratio_max=config.CASCADE_PULLBACK_SECOND_RATIO_MAX,
            pullback_next_ratio_min=config.CASCADE_PULLBACK_NEXT_RATIO_MIN,
            pullback_next_ratio_max=config.CASCADE_PULLBACK_NEXT_RATIO_MAX,
        )


class CascadeDetector:
    def __init__(self, config: CascadeDetectorConfig) -> None:
        self._config = config

    def detect(
            self,
            bars: list[Bar],
            *,
            cascade_type: CascadeType,
            price_min: float | None = None,
            price_max: float | None = None,
    ) -> list[Swing]:
        if not bars or len(bars) < 2:
            return []

        bars = bars[:-1]
        if not bars:
            return []

        if price_min is not None and price_max is not None and price_min >= price_max:
            return []

        bounds_min = price_min if price_min is not None else -inf
        bounds_max = price_max if price_max is not None else inf

        bars_count = len(bars)
        avg_range = self._calculate_average_range(bars, self._config.atr_window)
        touch_tolerance = max(self._config.touch_eps_natr * avg_range, 0.0)
        min_pullback = max(self._config.min_pullback_natr * avg_range, 0.0)
        min_pullback_bars = self._config.min_pullback_bars
        min_gap_bars = self._config.min_gap_bars
        min_touches = max(self._config.length_min, 3)
        is_long = cascade_type is CascadeType.LONG

        bar_data = []
        for index, bar in enumerate(bars):
            price = bar.high if is_long else bar.low
            if bounds_min <= price <= bounds_max:
                bar_data.append((index, price, bar.open, bar.close))

        levels: list[CascadeLevel] = []
        for index, level_price, _open, _close in bar_data:
            cross_index = None
            atr_crossed = False
            for look_ahead in range(index + 1, bars_count):
                look_bar = bars[look_ahead]
                if is_long:
                    if look_bar.high > level_price + avg_range:
                        atr_crossed = True
                        break
                    if look_bar.high > level_price:
                        cross_index = look_ahead
                        break
                else:
                    if look_bar.low < level_price - avg_range:
                        atr_crossed = True
                        break
                    if look_bar.low < level_price:
                        cross_index = look_ahead
                        break
            if atr_crossed:
                continue
            end_idx = cross_index if cross_index is not None else bars_count
            if is_long:
                touches_raw = [
                    i for i in range(index, end_idx)
                    if level_price - bars[i].close <= touch_tolerance
                    and bars[i].close <= level_price
                ]
            else:
                touches_raw = [
                    i for i in range(index, end_idx)
                    if bars[i].close - level_price <= touch_tolerance
                    and bars[i].close >= level_price
                ]
            if touches_raw:
                levels.append(CascadeLevel(
                    price=level_price,
                    is_crossed=cross_index is not None,
                    distance=(cross_index - index) if cross_index is not None else (bars_count - 1 - index),
                    touches_raw=touches_raw,
                ))

        def refine_touches(
                price: float,
                touches_count: list[int],
        ) -> list[int]:
            if not touches_count:
                return []
            refined = touches_count[:]
            while True:
                changed = False
                new_touches = []
                last_touch = None
                for touch_idx in refined:
                    if last_touch is None:
                        new_touches.append(touch_idx)
                        last_touch = touch_idx
                        continue
                    if touch_idx - last_touch < min_gap_bars:
                        changed = True
                        continue
                    pullback_bars = bars[last_touch + 1:touch_idx]
                    if not pullback_bars:
                        changed = True
                        continue
                    if is_long:
                        pullback_depth = max((price - pullback_bar.low) for pullback_bar in pullback_bars)
                    else:
                        pullback_depth = max((pullback_bar.high - price) for pullback_bar in pullback_bars)
                    consecutive = 0
                    max_consecutive = 0
                    for pullback_bar in pullback_bars:
                        if is_long:
                            is_pullback = pullback_bar.close <= price - min_pullback
                        else:
                            is_pullback = pullback_bar.close >= price + min_pullback
                        if is_pullback:
                            consecutive += 1
                            max_consecutive = max(max_consecutive, consecutive)
                        else:
                            consecutive = 0
                    has_pullback = pullback_depth >= min_pullback and max_consecutive >= min_pullback_bars
                    if not has_pullback:
                        changed = True
                        continue
                    new_touches.append(touch_idx)
                    last_touch = touch_idx
                if not changed:
                    return new_touches
                refined = new_touches

        best_level_touches = []
        best_level_touches_count = 0
        best_level_price = None

        for level in levels:
            if level.is_crossed:
                continue
            touches = refine_touches(level.price, level.touches_raw)
            touches_count = len(touches)

            if touches_count > best_level_touches_count or (
                    touches_count == best_level_touches_count and
                    (not best_level_touches or touches[-1] > best_level_touches[-1])
            ):
                best_level_touches = touches
                best_level_touches_count = touches_count
                best_level_price = level.price

        if not best_level_touches or best_level_touches_count < min_touches or best_level_price is None:
            return []

        if not self._passes_pullback_ratios(
                bars,
                best_level_touches,
                best_level_price,
                avg_range,
                cascade_type=cascade_type,
        ):
            return []

        first_touch_index = min(best_level_touches)
        last_touch_index = max(best_level_touches)
        if is_long:
            last_touch_segment_extreme = min(bar.low for bar in bars[last_touch_index:])
            first_touch_segment_extreme = min(bar.low for bar in bars[first_touch_index:last_touch_index - 1])
            if last_touch_segment_extreme <= first_touch_segment_extreme:
                return []
        else:
            last_touch_segment_extreme = max(bar.high for bar in bars[last_touch_index:])
            first_touch_segment_extreme = max(bar.high for bar in bars[first_touch_index:last_touch_index - 1])
            if last_touch_segment_extreme >= first_touch_segment_extreme:
                return []

        swing_type = SwingType.HIGH if is_long else SwingType.LOW
        cascade_swings = []
        for touch_index in best_level_touches:
            bar = bars[touch_index]
            swing = Swing(
                time=bar.time,
                extremum_price=best_level_price,
                close_price=bar.close,
                type=swing_type,
                is_open=True,
            )
            cascade_swings.append(swing)

        return cascade_swings

    @staticmethod
    def _calculate_average_range(bars: list[Bar], window: int) -> float:
        if len(bars) < 2 or window <= 0:
            return 0.0
        window = min(window, len(bars) - 1)
        start_index = len(bars) - window

        true_ranges: list[float] = []
        for index in range(start_index, len(bars)):
            bar = bars[index]
            prev_close = bars[index - 1].close
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            )
            true_ranges.append(true_range)

        return float(np.mean(true_ranges)) if true_ranges else 0.0

    def _calculate_pullbacks(
            self,
            bars: list[Bar],
            touch_indices: list[int],
            level_price: float,
            *,
            cascade_type: CascadeType,
    ) -> list[float]:
        pullbacks: list[float] = []
        for left_idx, right_idx in zip(touch_indices, touch_indices[1:]):
            segment = bars[left_idx + 1:right_idx]
            if not segment:
                return []
            if cascade_type is CascadeType.LONG:
                segment_extreme = min(bar.low for bar in segment)
                depth = level_price - segment_extreme
            else:
                segment_extreme = max(bar.high for bar in segment)
                depth = segment_extreme - level_price
            if depth <= 0:
                return []
            pullbacks.append(depth)
        return pullbacks

    def _passes_pullback_ratios(
            self,
            bars: list[Bar],
            touch_indices: list[int],
            level_price: float,
            avg_range: float,
            *,
            cascade_type: CascadeType,
    ) -> bool:
        pullbacks = self._calculate_pullbacks(
            bars,
            touch_indices,
            level_price,
            cascade_type=cascade_type,
        )
        if len(pullbacks) < 2:
            return False
        first_depth = pullbacks[0]
        second_depth = pullbacks[1]
        if first_depth <= 0 or second_depth <= 0:
            return False
        min_pullback = self._config.min_pullback_natr * avg_range
        if first_depth < min_pullback or second_depth < min_pullback:
            return False
        ratio = second_depth / first_depth
        if not (
                self._config.pullback_second_ratio_min
                <= ratio
                <= self._config.pullback_second_ratio_max
        ):
            return False
        for depth in pullbacks[2:]:
            ratio = depth / second_depth
            if not (
                    self._config.pullback_next_ratio_min
                    <= ratio
                    <= self._config.pullback_next_ratio_max
            ):
                return False
        return True
