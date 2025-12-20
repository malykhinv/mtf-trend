from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from crypto_screener.config.gu_config import CascadeGroupingMode, GuConfig, gu_cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.cascade_type import CascadeType
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Capture, Setup, Trade, Unfilled
from crypto_screener.domain.models.setup_data import Gu
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_levels import TradeLevels
from crypto_screener.domain.strategies.strategy import (
    Strategy,
    StrategyRuntimeConfig,
    StrategyVolumeConfig,
)
from crypto_screener.domain.swing_detector import add_swings


@dataclass(frozen=True)
class Cascade:
    swings: list[Swing]
    direction: CascadeType

    @property
    def is_long(self) -> bool:
        return self.direction is CascadeType.LONG

    @property
    def is_short(self) -> bool:
        return self.direction is CascadeType.SHORT


@dataclass(frozen=True)
class CascadeExtreme:
    index: int
    price: float


class GuStrategy(Strategy):
    name = "ГУ"

    def __init__(self, config: GuConfig = gu_cfg) -> None:
        self._config = config

    def detect_setup(
            self,
            symbol: str,
            bars: list[Bar],
            timeframe: Timeframe,
            context: Context,
    ) -> Setup:
        setup = Unfilled(
            data=Gu(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                cascade_swings=None,
                support_swings=None
            )
        )

        # Анализ повышения объемов.
        trimmed_bars = self.trim_by_volume(bars)
        has_high_volume = False
        if trimmed_bars:
            bars = trimmed_bars
            has_high_volume = True
        elif not context.is_top:
            return setup

        # Анализ теней.
        shadows_pct = self._get_shadow_range_pct(bars)
        if shadows_pct > self._config.SHADOW_RANGE_PCT_MAX:
            return setup

        bars = add_swings(bars, timeframe)
        setup.data.bars = bars

        # Анализ каскадов.
        cascade_long = self._get_cascade_long(bars)
        cascade_short = self._get_cascade_short(bars) # написать метод по аналогии с _get_cascade_long. Выделить общие методы
        cascade = self._longest(cascade_long, cascade_short) # сравнить по длине каскада (во временном диапазоне) и выбрать больший
        if not cascade:
            return setup

        setup.data.cascade_swings = cascade.swings

        # Центрирование относительно начала каскада.
        first_touch_index = next(
            (index for index, bar in enumerate(bars) if bar.time == cascade.swings[0].time),
            None,
        )
        if first_touch_index is None:
            return setup
        start_index = max(0, first_touch_index - (len(bars) - first_touch_index - 1))
        bars = bars[start_index:]
        setup.data.bars = bars
        first_touch_index -= start_index

        cascade_bars = bars[first_touch_index:]
        if not cascade_bars:
            return setup

        # Анализ поддержки под каскадом.
        initial_swing = cascade.swings[0]
        support_swing = None
        target_swing = cascade.swings[-1]
        support_swings: list[Swing] = []
        if cascade.is_long:
            open_low_swings = self._get_open_swings(cascade_bars, SwingType.LOW)
            cascade_low_swing = min(open_low_swings, key=lambda swing: swing.extremum_price, default=None)
            if not cascade_low_swing:
                lowest_bar = min(cascade_bars, key=lambda bar: bar.low, default=None)
                if not lowest_bar:
                    return setup
                cascade_low_swing = Swing(
                    time=lowest_bar.time,
                    extremum_price=lowest_bar.low,
                    close_price=lowest_bar.close,
                    type=SwingType.LOW,
                    is_open=True,
                )
            consolidation_range = initial_swing.extremum_price - cascade_low_swing.extremum_price
            if consolidation_range <= 0:
                return setup
            support_swings = open_low_swings

            support_price_min = cascade_low_swing.extremum_price + consolidation_range * self._config.SUPPORT_CONSOLIDATION_RATIO_MIN
            support_price_max = target_swing.extremum_price
            open_low_swings = self._filter_by_price(open_low_swings, support_price_min, support_price_max)
            support_swing = open_low_swings[-1] if open_low_swings else None
            if not support_swing:
                last_red_bar = next((bar for bar in reversed(cascade_bars) if bar.close < bar.open), None)
                if not last_red_bar:
                    return setup
                support_swing = Swing(
                    time=last_red_bar.time,
                    extremum_price=last_red_bar.low,
                    close_price=last_red_bar.close,
                    type=SwingType.LOW,
                    is_open=True
                )
        elif cascade.is_short:
            open_high_swings = self._get_open_swings(cascade_bars, SwingType.HIGH)
            cascade_high_swing = max(open_high_swings, key=lambda swing: swing.extremum_price, default=None)
            if not cascade_high_swing:
                highest_bar = max(cascade_bars, key=lambda bar: bar.high, default=None)
                if not highest_bar:
                    return setup
                cascade_high_swing = Swing(
                    time=highest_bar.time,
                    extremum_price=highest_bar.high,
                    close_price=highest_bar.close,
                    type=SwingType.HIGH,
                    is_open=True,
                )
            consolidation_range = cascade_high_swing.extremum_price - initial_swing.extremum_price
            if consolidation_range <= 0:
                return setup
            support_swings = open_high_swings

            resistance_price_min = target_swing.extremum_price
            resistance_price_max = cascade_high_swing.extremum_price - consolidation_range * self._config.SUPPORT_CONSOLIDATION_RATIO_MIN
            if resistance_price_min < resistance_price_max:
                open_high_swings = self._filter_by_price(open_high_swings, resistance_price_min, resistance_price_max)
            else:
                open_high_swings = []
            support_swing = open_high_swings[-1] if open_high_swings else None
            if not support_swing:
                last_green_bar = next((bar for bar in reversed(cascade_bars) if bar.close > bar.open), None)
                if not last_green_bar:
                    return setup
                support_swing = Swing(
                    time=last_green_bar.time,
                    extremum_price=last_green_bar.high,
                    close_price=last_green_bar.close,
                    type=SwingType.HIGH,
                    is_open=True
                )
        setup.data.support_swings = support_swings
        has_support = support_swing is not None
        if not has_support:
            return setup

        # Анализ пробоя поддержки каскада.
        current_bar = bars[-1]
        current_price = current_bar.close
        if cascade.is_long:
            has_breakout_short = current_price < support_swing.extremum_price
            if has_breakout_short:
                return setup
        if cascade.is_short:
            has_breakout_long = current_price > support_swing.extremum_price
            if has_breakout_long:
                return setup

        # Горизонтальный уровень.
        setup = Capture(
            data=Gu(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                cascade_swings=cascade.swings,
                support_swings=support_swings
            )
        )

        # Анализ риска и вознаграждения.
        highs = [bar.high for bar in cascade_bars]
        lows = [bar.low for bar in cascade_bars]
        cascade_range = max(highs) - min(lows) if highs and lows else 0.0
        if cascade_range <= 0:
            return setup
        entry_price = target_swing.extremum_price
        is_tp_top = context.is_top or has_high_volume
        tp_multiplier = (
            self._config.TP_MULTIPLIER_TOP_HIGH_VOLUME
            if is_tp_top
            else self._config.TP_MULTIPLIER_DEFAULT
        )
        if cascade.is_long:
            profit_price = entry_price + tp_multiplier * cascade_range
            loss_price = support_swing.extremum_price
            profit_pct = 100 * (profit_price - entry_price) / entry_price
            loss_pct = 100 * (loss_price - entry_price) / entry_price
        else:
            profit_price = entry_price - tp_multiplier * cascade_range
            loss_price = support_swing.extremum_price
            profit_pct = 100 * (entry_price - profit_price) / entry_price
            loss_pct = 100 * (loss_price - entry_price) / entry_price
        if loss_pct == 0:
            return setup
        is_loss_valid = abs(loss_pct) > self._config.LOSS_PCT_MIN
        if not is_loss_valid:
            return setup
        is_profit_valid = profit_pct > self._config.PROFIT_PCT_MIN
        if not is_profit_valid:
            return setup
        reward_risk = abs(profit_pct / loss_pct)
        is_reward_risk_valid = reward_risk >= self._config.REWARD_RISK_RATIO_MIN
        if not is_reward_risk_valid and not context.is_test:
            return setup

        # Анализ пробоя каскада.
        if cascade.is_long:
            has_breakout_long = current_price > target_swing.extremum_price
            if not has_breakout_long:
                return setup
        elif cascade.is_short:
            has_breakout_short = current_price < target_swing.extremum_price
            if not has_breakout_short:
                return setup

        # Пробой лонгового каскада.
        partial_close_price = None
        breakeven_price = None
        partial_close_side_pct = self._config.PARTIAL_CLOSE_SIDE_PCT_MIN
        # TODO пока что partial close не делаем, сделаем позднее.
        entry_slippage_ratio = 1 + self._config.TEST_SLIPPAGE_PCT / 100 if context.is_test else 1
        trade_levels = TradeLevels(
            entry_price=target_swing.extremum_price * entry_slippage_ratio,
            take_profit_price=profit_price,
            stop_loss_price=loss_price,
            partial_close_price=partial_close_price,
            breakeven_price=breakeven_price,
        )
        setup = Trade(
            data=Gu(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                cascade_swings=cascade.swings,
                support_swings=support_swings,
            ),
            trade_levels=trade_levels,
        )

        return setup

    @staticmethod
    def _get_open_swings(
            bars: list[Bar],
            swing_type: SwingType,
    ) -> list[Swing]:
        return [
            bar.swing
            for bar in bars
            if bar.swing is not None
            and bar.swing.is_open
            and bar.swing.type == swing_type
        ]

    @staticmethod
    def _get_first_open_swing_indexed(
            bars: list[Bar],
            swing_type: SwingType,
    ) -> tuple[int, Swing] | None:
        for index, bar in enumerate(bars):
            swing = bar.swing
            if swing is None:
                continue
            if not swing.is_open:
                continue
            if swing.type != swing_type:
                continue
            return index, swing
        return None

    @staticmethod
    def _filter_by_price(
            swings: list[Swing],
            price_min: float,
            price_max: float,
    ) -> list[Swing]:
        if price_min <= 0 or price_min >= price_max:
            raise ValueError(f"Некорректные границы цены: {price_min}..{price_max}.")
        return [swing for swing in swings if price_min <= swing.extremum_price <= price_max]

    @staticmethod
    def _get_shadow_range_pct(bars: list[Bar]) -> float:
        if not bars:
            return 0.0
        highs = np.array([bar.high for bar in bars])
        lows = np.array([bar.low for bar in bars])
        opens = np.array([bar.open for bar in bars])
        closes = np.array([bar.close for bar in bars])
        bar_ranges = highs - lows
        valid_ranges = bar_ranges > 0
        if not np.any(valid_ranges):
            return 0.0
        bodies = np.abs(opens - closes)
        shadows = np.maximum(bar_ranges - bodies, 0.0)
        total_range = np.sum(bar_ranges[valid_ranges])
        total_shadow = np.sum(shadows[valid_ranges])
        return 100 * total_shadow / total_range if total_range > 0 else 0.0

    @classmethod
    def _get_main_rising_swings_indexed(cls, bars: list[Bar]) -> list[tuple[int, Swing]]:
        main_high = cls._get_first_open_swing_indexed(bars, SwingType.HIGH)
        if not main_high:
            return []
        main_high_index, main_high_swing = main_high

        min_low_price = float('inf')
        min_low_swing = None
        min_low_index = -1

        for i in range(main_high_index + 1):
            bar = bars[i]
            if bar.swing and bar.swing.type == SwingType.LOW and bar.swing.is_open:
                if bar.low < min_low_price:
                    min_low_price = bar.low
                    min_low_swing = bar.swing
                    min_low_index = i

        if not min_low_swing or min_low_index == -1:
            return []

        main_low_swing = Swing(
            time=min_low_swing.time,
            extremum_price=min_low_price,
            close_price=bars[min_low_index].close,
            type=SwingType.LOW,
            is_open=True
        )

        return [(min_low_index, main_low_swing), main_high]

    @staticmethod
    def _calculate_average_range(bars: list[Bar], window: int) -> float:
        if not bars or window <= 0:
            return 0.0
        window = min(window, len(bars))
        ranges = [bar.high - bar.low for bar in bars[-window:]]
        return float(np.mean(ranges)) if ranges else 0.0

    def _get_cascade_long(
            self,
            bars: list[Bar]
    ) -> Cascade | None:
        swings = self._get_cascade(bars, cascade_type=CascadeType.LONG)
        if not swings:
            return None
        return Cascade(swings=swings, direction=CascadeType.LONG)

    def _get_cascade_short(
            self,
            bars: list[Bar]
    ) -> Cascade | None:
        swings = self._get_cascade(bars, cascade_type=CascadeType.SHORT)
        if not swings:
            return None
        return Cascade(swings=swings, direction=CascadeType.SHORT)

    @staticmethod
    def _longest(*cascades: Cascade | None) -> Cascade | None:
        candidates = [cascade for cascade in cascades if cascade and cascade.swings]
        if not candidates:
            return None

        def key(cascade: Cascade) -> tuple[float, object]:
            duration = (cascade.swings[-1].time - cascade.swings[0].time).total_seconds()
            last_time = cascade.swings[-1].time
            return duration, last_time

        return max(candidates, key=key)

    @staticmethod
    def _group_bars_by_day(bars: list[Bar]) -> list[tuple[int, int]]:
        if not bars:
            return []
        groups: list[tuple[int, int]] = []
        start_index = 0
        current_date = bars[0].time.date()
        for index, bar in enumerate(bars):
            bar_date = bar.time.date()
            if bar_date != current_date:
                groups.append((start_index, index - 1))
                current_date = bar_date
                start_index = index
        groups.append((start_index, len(bars) - 1))
        return groups

    @staticmethod
    def _group_bars_by_time_window(
            bars: list[Bar],
            *,
            min_hours: int = 12,
            max_hours: int = 36,
    ) -> list[tuple[int, int, datetime, datetime]]:
        if not bars:
            return []
        groups: list[tuple[int, int, datetime, datetime]] = []
        start_index = 0
        while start_index < len(bars):
            start_time = bars[start_index].time
            window_start = start_time + timedelta(hours=min_hours)
            window_end = start_time + timedelta(hours=max_hours)
            end_index = start_index
            for index in range(start_index + 1, len(bars)):
                if bars[index].time <= window_end:
                    end_index = index
                else:
                    break
            groups.append((start_index, end_index, window_start, window_end))
            start_index = end_index + 1
        return groups

    @staticmethod
    def _get_window_extremes(
            bars: list[Bar],
            time_groups: list[tuple[int, int, datetime, datetime]],
            *,
            cascade_type: CascadeType,
    ) -> list[CascadeExtreme]:
        window_extremes: list[CascadeExtreme] = []
        for start_index, end_index, _window_start, _window_end in time_groups:
            # Экстремумы окна: лонг — максимум окна, шорт — минимум окна (аналогично дневным экстремумам).
            window_indices = list(range(start_index, end_index + 1))
            if not window_indices:
                continue
            if cascade_type is CascadeType.LONG:
                window_price = max(bars[index].high for index in window_indices)
                for index in window_indices:
                    if bars[index].high == window_price:
                        window_extremes.append(CascadeExtreme(index=index, price=float(window_price)))
                        break
            else:
                window_price = min(bars[index].low for index in window_indices)
                for index in window_indices:
                    if bars[index].low == window_price:
                        window_extremes.append(CascadeExtreme(index=index, price=float(window_price)))
                        break
        return window_extremes

    @staticmethod
    def _get_daily_extremes(
            bars: list[Bar],
            day_groups: list[tuple[int, int]],
            *,
            cascade_type: CascadeType,
    ) -> list[CascadeExtreme]:
        daily_extremes: list[CascadeExtreme] = []
        for start_index, end_index in day_groups:
            day_bars = bars[start_index:end_index + 1]
            if cascade_type is CascadeType.LONG:
                day_price = max(bar.high for bar in day_bars)
                for index in range(start_index, end_index + 1):
                    if bars[index].high == day_price:
                        daily_extremes.append(CascadeExtreme(index=index, price=float(day_price)))
                        break
            else:
                day_price = min(bar.low for bar in day_bars)
                for index in range(start_index, end_index + 1):
                    if bars[index].low == day_price:
                        daily_extremes.append(CascadeExtreme(index=index, price=float(day_price)))
                        break
        return daily_extremes

    @staticmethod
    def _get_swing_extremes(
            bars: list[Bar],
            *,
            cascade_type: CascadeType,
    ) -> list[CascadeExtreme]:
        swing_type = SwingType.HIGH if cascade_type is CascadeType.LONG else SwingType.LOW
        swing_extremes: list[CascadeExtreme] = []
        for index, bar in enumerate(bars):
            swing = bar.swing
            if swing is None:
                continue
            if not swing.is_open:
                continue
            if swing.type != swing_type:
                continue
            swing_extremes.append(CascadeExtreme(index=index, price=float(swing.extremum_price)))
        return swing_extremes

    @staticmethod
    def _merge_extremes(
            bars: list[Bar],
            *extremes: list[CascadeExtreme],
    ) -> list[CascadeExtreme]:
        unique: dict[tuple[int, float], CascadeExtreme] = {}
        for extremes_list in extremes:
            for extreme in extremes_list:
                unique[(extreme.index, extreme.price)] = extreme
        return sorted(unique.values(), key=lambda item: bars[item.index].time)

    def _get_cascade_extremes(
            self,
            bars: list[Bar],
            *,
            cascade_type: CascadeType,
            grouping_mode: CascadeGroupingMode,
    ) -> list[CascadeExtreme]:
        swing_extremes = self._get_swing_extremes(bars, cascade_type=cascade_type)
        if grouping_mode is CascadeGroupingMode.ROLLING_WINDOW:
            time_groups = self._group_bars_by_time_window(
                bars,
                min_hours=self._config.CASCADE_ROLLING_WINDOW_MIN_HOURS,
                max_hours=self._config.CASCADE_ROLLING_WINDOW_MAX_HOURS,
            )
            window_extremes = self._get_window_extremes(bars, time_groups, cascade_type=cascade_type)
            return self._merge_extremes(bars, window_extremes, swing_extremes)
        day_groups = self._group_bars_by_day(bars)
        daily_extremes = self._get_daily_extremes(bars, day_groups, cascade_type=cascade_type)
        return self._merge_extremes(bars, daily_extremes, swing_extremes)

    @staticmethod
    def _calculate_pullbacks(
            bars: list[Bar],
            touch_indices: list[int],
            level_price: float,
            *,
            cascade_type: CascadeType,
    ) -> list[tuple[float, float]]:
        pullbacks: list[tuple[float, float]] = []
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
            pullbacks.append((depth, segment_extreme))
        return pullbacks

    @staticmethod
    def _is_price_squeezed(
            pullbacks: list[tuple[float, float]],
            *,
            cascade_type: CascadeType,
    ) -> bool:
        extremes = [extreme for _, extreme in pullbacks]
        if len(extremes) < 2:
            return False
        if cascade_type is CascadeType.LONG:
            return all(curr >= prev for prev, curr in zip(extremes, extremes[1:]))
        return all(curr <= prev for prev, curr in zip(extremes, extremes[1:]))

    def _passes_pullback_rules(
            self,
            pullbacks: list[tuple[float, float]],
            avg_range: float,
    ) -> bool:
        if len(pullbacks) < 2:
            return False
        first_depth = pullbacks[0][0]
        second_depth = pullbacks[1][0]
        if first_depth <= 0 or second_depth <= 0:
            return False
        min_pullback = self._config.CASCADE_MIN_PULLBACK_NATR * avg_range
        if first_depth < min_pullback or second_depth < min_pullback:
            return False
        ratio = second_depth / first_depth
        if not (
                self._config.CASCADE_PULLBACK_SECOND_RATIO_MIN
                <= ratio
                <= self._config.CASCADE_PULLBACK_SECOND_RATIO_MAX
        ):
            return False
        for depth, _ in pullbacks[2:]:
            ratio = depth / second_depth
            if not (
                    self._config.CASCADE_PULLBACK_NEXT_RATIO_MIN
                    <= ratio
                    <= self._config.CASCADE_PULLBACK_NEXT_RATIO_MAX
            ):
                return False
        return True

    def _get_cascade(
            self,
            bars: list[Bar],
            *,
            cascade_type: CascadeType,
    ) -> list[Swing]:
        if not bars or len(bars) < 2:
            return []

        bars = bars[:-1]
        if not bars:
            return []

        avg_range = self._calculate_average_range(bars, self._config.CASCADE_ATR_WINDOW)
        touch_tolerance = max(self._config.CASCADE_TOUCH_EPS_NATR * avg_range, 0.0)
        min_touches = max(self._config.CASCADE_LENGTH_MIN, 3)
        is_long = cascade_type is CascadeType.LONG
        swing_type = SwingType.HIGH if is_long else SwingType.LOW
        for grouping_mode in (
                CascadeGroupingMode.ROLLING_WINDOW,
                CascadeGroupingMode.CALENDAR,
        ):
            cascade_extremes = self._get_cascade_extremes(
                bars,
                cascade_type=cascade_type,
                grouping_mode=grouping_mode,
            )
            if len(cascade_extremes) < min_touches:
                continue

            best_candidate = None
            best_duration = None
            best_last_time = None

            for start_day in range(len(cascade_extremes) - 1):
                level_price = cascade_extremes[start_day].price
                next_day_price = cascade_extremes[start_day + 1].price
                if abs(next_day_price - level_price) > touch_tolerance:
                    continue

                touch_indices = []
                for day_idx in range(start_day, len(cascade_extremes)):
                    day_price = cascade_extremes[day_idx].price
                    if is_long and day_price > level_price + touch_tolerance:
                        break
                    if not is_long and day_price < level_price - touch_tolerance:
                        break
                    if abs(day_price - level_price) > touch_tolerance:
                        continue
                    bar = bars[cascade_extremes[day_idx].index]
                    close_price = bar.close
                    if is_long:
                        if close_price <= level_price and (level_price - close_price) <= touch_tolerance:
                            touch_indices.append(cascade_extremes[day_idx].index)
                    else:
                        if close_price >= level_price and (close_price - level_price) <= touch_tolerance:
                            touch_indices.append(cascade_extremes[day_idx].index)

                if len(touch_indices) < min_touches:
                    continue

                if is_long:
                    touch_prices = [bars[index].high for index in touch_indices]
                else:
                    touch_prices = [bars[index].low for index in touch_indices]
                if not touch_prices:
                    continue
                level_price = float(np.median(touch_prices))
                if any(abs(price - level_price) > touch_tolerance for price in touch_prices):
                    continue

                first_index = touch_indices[0]
                first_swing = bars[first_index].swing
                if (
                        first_swing is None
                        or not first_swing.is_open
                        or first_swing.type != swing_type
                        or abs(first_swing.extremum_price - level_price) > touch_tolerance
                ):
                    continue

                pullbacks = self._calculate_pullbacks(
                    bars,
                    touch_indices,
                    level_price,
                    cascade_type=cascade_type,
                )
                if not pullbacks:
                    continue
                if not self._passes_pullback_rules(pullbacks, avg_range):
                    continue
                if not self._is_price_squeezed(pullbacks, cascade_type=cascade_type):
                    continue

                last_index = touch_indices[-1]
                duration = bars[last_index].time - bars[first_index].time
                last_time = bars[last_index].time
                if best_duration is None or duration > best_duration:
                    best_candidate = (touch_indices, level_price, first_swing)
                    best_duration = duration
                    best_last_time = last_time
                elif duration == best_duration and best_last_time is not None and last_time > best_last_time:
                    best_candidate = (touch_indices, level_price, first_swing)
                    best_duration = duration
                    best_last_time = last_time

            if not best_candidate:
                continue

            touch_indices, level_price, first_swing = best_candidate
            cascade_swings = [first_swing]
            first_index = touch_indices[0]
            for touch_index in touch_indices:
                if touch_index == first_index:
                    continue
                bar = bars[touch_index]
                cascade_swings.append(Swing(
                    time=bar.time,
                    extremum_price=level_price,
                    close_price=bar.close,
                    type=swing_type,
                    is_open=True
                ))

            return cascade_swings

        return []

    def get_runtime_config(self) -> StrategyRuntimeConfig:
        return StrategyRuntimeConfig(
            capture_timeout_multiplier=self._config.CAPTURE_TIMEOUT_MULTIPLIER,
            risk_per_trade_usdt=self._config.RISK_PER_TRADE_USDT,
            min_position_notional_usdt=self._config.MIN_POSITION_NOTIONAL_USDT,
            min_position_quantity=self._config.MIN_POSITION_QUANTITY,
        )

    def get_volume_config(self) -> StrategyVolumeConfig:
        return StrategyVolumeConfig(
            min_side_bars=self._config.VOLUME_TRIM_SIDE_BARS_MIN,
            high_volume_factor=self._config.HIGH_VOLUME_THRESHOLD,
            min_high_fraction=self._config.HIGH_VOLUME_FRACTION_MIN,
        )
