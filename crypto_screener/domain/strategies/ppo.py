from __future__ import annotations

import numpy as np

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.cascade_level import CascadeLevel
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Capture, Setup, Trade, Unfilled
from crypto_screener.domain.models.setup_data import Ppo
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_levels import TradeLevels
from crypto_screener.config.ppo_config import PpoConfig, ppo_cfg
from crypto_screener.domain.strategies.strategy import (
    Strategy,
    StrategyRuntimeConfig,
    StrategyVolumeConfig,
)
from crypto_screener.domain.swing_detector import add_swings


class PpoStrategy(Strategy):
    name = "ППО"

    def __init__(self, config: PpoConfig = ppo_cfg) -> None:
        self._config = config

    def detect_setup(
            self,
            symbol: str,
            bars: list[Bar],
            timeframe: Timeframe,
            context: Context,
    ) -> Setup:
        setup = Unfilled(
            data=Ppo(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                main_low_swing=None,
                main_high_swing=None,
                cascade_swings=None,
                resistance_swings=None,
                support_swings=None
            )
        )

        # Анализ повышения объемов.
        trimmed_bars = self.trim_by_volume(bars)
        if trimmed_bars:
            bars = trimmed_bars
        elif not context.is_top:
            return setup

        # Анализ теней.
        shadows_pct = self._get_shadow_range_pct(bars)
        if shadows_pct > self._config.SHADOW_RANGE_PCT_MAX:
            return setup

        bars = add_swings(bars, timeframe)
        setup.data.bars = bars

        # Анализ участка роста.
        main_rising_swings = (self._get_main_rising_swings_indexed_by_high(bars) or
                              self._get_main_rising_swings_indexed_by_low(bars))
        if not main_rising_swings:
            return setup
        main_low, main_high = main_rising_swings
        if not main_low or not main_high:
            return setup
        main_low_index, main_low_swing = main_low
        main_high_index, main_high_swing = main_high

        # Центрирование относительно main_low_index.
        start_index = max(0, main_low_index - (len(bars) - main_low_index - 1))
        bars = bars[start_index:]
        setup.data.bars = bars
        main_low_index -= start_index
        main_high_index -= start_index

        # Анализ роста.
        setup.data.main_low_swing = main_low_swing
        setup.data.main_high_swing = main_high_swing
        rise = main_high_swing.extremum_price - main_low_swing.extremum_price
        rise_pct = 100 * rise / main_low_swing.extremum_price

        # Проверка максимального отката на участке роста.
        max_retrace = 0.0
        max_high = main_low_swing.extremum_price
        for i in range(main_low_index + 1, main_high_index + 1):
            bar = bars[i]
            max_high = max(max_high, bar.high)
            retrace = max_high - bar.low
            if rise > 0:  # Избегаем деления на ноль
                retrace_ratio = retrace / rise
                max_retrace = max(max_retrace, retrace_ratio)
        is_rise_valid = (
                rise > 0 and
                rise_pct >= self._config.PRICE_RISE_PCT_MIN and
                max_retrace <= self._config.MAX_RETRACE_RATIO
        )

        if not is_rise_valid:
            return setup

        # Анализ коррекции.
        correction_bars = bars[main_high_index + 1:]
        if not correction_bars:
            return setup
        rise_bars_count = max(0, main_high_index - main_low_index)
        correction_bars_count = len(correction_bars)
        rise_correction_ratio = (rise_bars_count / correction_bars_count
                                 if correction_bars_count > 0 else 0)
        is_rise_age_valid = rise_correction_ratio < self._config.RISE_AGE_LIMIT_MULTIPLIER
        if not is_rise_age_valid:
            return setup
        correction_low = self._get_first_open_swing_indexed(correction_bars, SwingType.LOW)
        if not correction_low:
            return setup
        correction_low_index, correction_low_swing = correction_low
        retrace_range = main_high_swing.extremum_price - correction_low_swing.extremum_price
        retrace_ratio = retrace_range / rise
        is_retrace_valid = retrace_range >= 0 and retrace_ratio <= self._config.RETRACE_RATIO_MAX
        if not is_retrace_valid:
            return setup

        # Анализ цены до роста.
        pre_low_window_end = main_low_index
        pre_low_window_start = max(0, pre_low_window_end - len(correction_bars) + 1)
        pre_low_window = bars[pre_low_window_start:pre_low_window_end + 1]
        if pre_low_window:
            pre_low_above_correction_low_fraction = (
                    sum(1 for bar in pre_low_window if bar.low > correction_low_swing.extremum_price) / len(pre_low_window)
            )
            is_pre_low_above_correction_low_valid = (pre_low_above_correction_low_fraction <=
                                                     self._config.PRE_LOW_ABOVE_CORRECTION_LOW_FRACTION_MAX)
            if not is_pre_low_above_correction_low_valid:
                return setup

        # Анализ лонгового каскада.
        cascade_price_min = correction_low_swing.close_price + retrace_range * self._config.CASCADE_RETRACE_RATIO_MIN
        cascade_price_max = main_high_swing.close_price
        cascade_long = self._get_cascade_long(
            correction_bars,
            cascade_price_min,
            cascade_price_max,
        )
        if not cascade_long:
            return setup
        cascade_top = max(cascade_long, key=lambda swing: swing.extremum_price).extremum_price
        resistance_gap = retrace_range * self._config.RESISTANCE_GAP_RATIO_MIN
        open_high_swings = self._get_open_swings(correction_bars, SwingType.HIGH)
        open_high_swings = self._filter_by_price(open_high_swings, cascade_price_min, cascade_price_max)
        extra_cascade_swings = [
            swing for swing in open_high_swings
            if cascade_top < swing.extremum_price <= cascade_top + resistance_gap
        ]
        cascade_long = [swing for swing in extra_cascade_swings if swing not in cascade_long] + cascade_long
        setup.data.cascade_swings = cascade_long
        if not cascade_long:
            return setup

        # Анализ сопротивления над каскадом.
        cascade_top = max(cascade_long, key=lambda swing: swing.extremum_price).extremum_price
        resistance_gap = retrace_range * self._config.RESISTANCE_GAP_RATIO_MIN
        resistance_price_min = cascade_top + resistance_gap
        resistance_price_max = main_high_swing.extremum_price - resistance_gap
        resistance_swings = []
        if resistance_price_min < resistance_price_max:
            resistance_swings = self._filter_by_price(open_high_swings, resistance_price_min, resistance_price_max)
        setup.data.resistance_swings = resistance_swings
        has_resistance = len(resistance_swings) > self._config.RESISTANCE_COUNT_MAX
        if has_resistance:
            return setup

        # Анализ поддержки под каскадом.
        initial_swing = cascade_long[0]
        consolidation_range = initial_swing.extremum_price - correction_low_swing.extremum_price
        open_low_swings = self._get_open_swings(correction_bars, SwingType.LOW)
        setup.data.support_swings = open_low_swings
        target_swing = cascade_long[-1]

        support_price_min = correction_low_swing.extremum_price + consolidation_range * self._config.SUPPORT_CONSOLIDATION_RATIO_MIN
        support_price_max = target_swing.extremum_price
        open_low_swings = self._filter_by_price(open_low_swings, support_price_min, support_price_max)
        support_swing = open_low_swings[-1] if open_low_swings else None
        if not support_swing:
            last_red_bar = next((bar for bar in reversed(correction_bars) if bar.close < bar.open), None)
            if not last_red_bar:
                return setup
            support_swing = Swing(
                time=last_red_bar.time,
                extremum_price=last_red_bar.low,
                close_price=last_red_bar.close,
                type=SwingType.LOW,
                is_open=True
            )
        has_support = support_swing is not None
        if not has_support:
            return setup

        # Анализ пробоя поддержки под каскадом.
        current_bar = correction_bars[-1]
        current_price = current_bar.close
        has_breakout_short = current_price < support_swing.extremum_price
        if has_breakout_short:
            return setup

        # Анализ риска и вознаграждения.
        is_main_high_close_crossed = any(bar.high > main_high_swing.close_price for bar in correction_bars)
        profit_price = main_high_swing.extremum_price if is_main_high_close_crossed else main_high_swing.close_price
        if context == Context.LOW_CAP_A:
            profit_price = profit_price + 2 * (main_high_swing.close_price - correction_low_swing.close_price)
        elif context == Context.LOW_CAP_B:
            profit_price = profit_price + (main_high_swing.close_price - correction_low_swing.close_price)
        loss_price = support_swing.extremum_price
        loss_pct = 100 * (loss_price - current_price) / loss_price
        is_loss_valid = abs(loss_pct) > self._config.LOSS_PCT_MIN
        if not is_loss_valid:
            return setup
        profit_pct = 100 * (profit_price - current_price) / current_price
        is_profit_valid = profit_pct > self._config.PROFIT_PCT_MIN
        if not is_profit_valid:
            return setup
        reward_risk = abs(profit_pct / loss_pct)
        is_reward_risk_valid = reward_risk >= self._config.REWARD_RISK_RATIO_MIN
        if not is_reward_risk_valid and not context.is_test:
            return setup

        # Проторговка после отката с лонговым каскадом.
        setup = Capture(
            data=Ppo(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                main_low_swing=main_low_swing,
                main_high_swing=main_high_swing,
                cascade_swings=cascade_long,
                resistance_swings=resistance_swings,
                support_swings=open_low_swings
            )
        )

        # Анализ пробоя лонгового каскада.
        has_breakout_long = current_price > target_swing.extremum_price
        if not has_breakout_long:
            return setup

        # Пробой лонгового каскада.
        partial_close_price = None
        breakeven_price = None
        partial_close_side_pct = self._config.PARTIAL_CLOSE_SIDE_PCT_MIN
        if resistance_swings:
            nearest_resistance_price = resistance_swings[-1].extremum_price
            nearest_resistance_distance_pct = 100 * (nearest_resistance_price - current_price) / current_price
            has_partial_close = partial_close_side_pct <= nearest_resistance_distance_pct < profit_pct - partial_close_side_pct
            if has_partial_close:
                partial_close_price = nearest_resistance_price if has_partial_close else None
                breakeven_price = current_price + self._config.BREAKEVEN_PARTIAL_CLOSE_RATIO * (partial_close_price - current_price)
        elif context.is_top:
            main_high_distance_pct = 100 * (main_high_swing.extremum_price - current_price) / current_price
            has_partial_close = partial_close_side_pct <= main_high_distance_pct < profit_pct - partial_close_side_pct
            if has_partial_close:
                partial_close_price = main_high_swing.extremum_price
                breakeven_price = current_price + self._config.BREAKEVEN_PARTIAL_CLOSE_RATIO * (partial_close_price - current_price)
        entry_slippage_ratio = 1 + self._config.TEST_SLIPPAGE_PCT / 100 if context.is_test else 1
        trade_levels = TradeLevels(
            entry_price=target_swing.extremum_price * entry_slippage_ratio,
            take_profit_price=profit_price,
            stop_loss_price=loss_price,
            partial_close_price=partial_close_price,
            breakeven_price=breakeven_price,
        )
        setup = Trade(
            data=Ppo(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                main_low_swing=main_low_swing,
                main_high_swing=main_high_swing,
                cascade_swings=cascade_long,
                resistance_swings=resistance_swings,
                support_swings=open_low_swings,
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

    def _get_main_rising_swings_indexed_by_high(
            self,
            bars: list[Bar]
    ) -> list[tuple[int, Swing]]:
        main_high = self._get_first_open_swing_indexed(bars, SwingType.HIGH)
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


    def _get_main_rising_swings_indexed_by_low(
            self,
            bars: list[Bar]
    ) -> list[tuple[int, Swing]]:
        main_low = self._get_first_open_swing_indexed(bars, SwingType.LOW)
        if not main_low:
            return []
        main_low_index, main_low_swing_raw = main_low

        max_high_price = float('-inf')
        max_high_swing = None
        max_high_index = -1

        for i in range(main_low_index, len(bars)):
            bar = bars[i]
            swing = bar.swing
            if swing and swing.type == SwingType.HIGH and swing.is_open:
                if bar.high > max_high_price:
                    max_high_price = bar.high
                    max_high_swing = swing
                    max_high_index = i

        if not max_high_swing or max_high_index == -1:
            return []

        main_low_price = bars[main_low_index].low
        main_low_swing = Swing(
            time=main_low_swing_raw.time,
            extremum_price=main_low_price,
            close_price=bars[main_low_index].close,
            type=SwingType.LOW,
            is_open=True
        )

        main_high_swing = Swing(
            time=max_high_swing.time,
            extremum_price=max_high_price,
            close_price=bars[max_high_index].close,
            type=SwingType.HIGH,
            is_open=True
        )

        return [(main_low_index, main_low_swing), (max_high_index, main_high_swing)]

    def _get_cascade_long(
            self,
            bars: list[Bar],
            price_min: float,
            price_max: float,
    ) -> list[Swing]:
        if not bars or price_min >= price_max or len(bars) < 2:
            return []

        bars = bars[:-1]
        bars_count = len(bars)

        def calculate_average_range(window: int) -> float:
            if not bars or window <= 0:
                return 0.0
            window = min(window, bars_count)
            ranges = [bar.high - bar.low for bar in bars[-window:]]
            return float(np.mean(ranges)) if ranges else 0.0

        avg_range = calculate_average_range(self._config.CASCADE_ATR_WINDOW)
        touch_tolerance = max(self._config.CASCADE_TOUCH_EPS_NATR * avg_range, 0.0)
        min_pullback = max(self._config.CASCADE_MIN_PULLBACK_NATR * avg_range, 0.0)
        min_pullback_bars = self._config.CASCADE_MIN_PULLBACK_BARS
        min_gap_bars = self._config.CASCADE_MIN_GAP_BARS
        levels: list[CascadeLevel] = []
        bar_data = [(i, bar.high, bar.open, bar.close)
                    for i, bar in enumerate(bars)
                    if price_min <= bar.high <= price_max]
        for index, high_price, open_price, close_price in bar_data:
            level_price = high_price
            cross_index = None
            atr_crossed = False
            for look_ahead in range(index + 1, bars_count):
                look_bar = bars[look_ahead]
                if look_bar.high > level_price + avg_range or look_bar.high > level_price + avg_range:
                    atr_crossed = True
                    break
                if look_bar.high > level_price or look_bar.high > level_price:
                    cross_index = look_ahead
                    break
            if atr_crossed:
                continue
            end_idx = cross_index if cross_index is not None else bars_count
            touches_raw = [
                i for i in range(index, end_idx)
                if level_price - bars[i].close <= touch_tolerance
                   and bars[i].close <= level_price
            ]
            if touches_raw:
                levels.append(CascadeLevel(
                    price=level_price,
                    is_crossed=cross_index is not None,
                    distance=(cross_index - index) if cross_index is not None else (bars_count - 1 - index),
                    touches_raw=touches_raw,
                ))

        def _refine_touches(
                price: float,
                touches_count: list[int]
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
                    pullback_depth = max((price - pullback_bar.low) for pullback_bar in pullback_bars)
                    consecutive = 0
                    max_consecutive = 0
                    for pullback_bar in pullback_bars:
                        if pullback_bar.close <= price - min_pullback:
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
            touches = _refine_touches(level.price, level.touches_raw)
            touches_count = len(touches)

            if touches_count > best_level_touches_count or \
                    (touches_count == best_level_touches_count and
                     (not best_level_touches or touches[-1] > best_level_touches[-1])):
                best_level_touches = touches
                best_level_touches_count = touches_count
                best_level_price = level.price

        if not best_level_touches or best_level_touches_count < self._config.CASCADE_LENGTH_MIN:
            return []
        first_touch_index = min(best_level_touches)
        last_touch_index = max(best_level_touches)
        last_touch_segment_low = min(bar.low for bar in bars[last_touch_index:])
        first_touch_segment_low = min(bar.low for bar in bars[first_touch_index:last_touch_index - 1])
        if last_touch_segment_low <= first_touch_segment_low:
            return []

        cascade_swings = []
        for touch_index in best_level_touches:
            bar = bars[touch_index]
            swing = Swing(
                time=bar.time,
                extremum_price=best_level_price,
                close_price=bar.close,
                type=SwingType.HIGH,
                is_open=True
            )
            cascade_swings.append(swing)

        return cascade_swings

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
