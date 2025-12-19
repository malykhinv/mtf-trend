from __future__ import annotations

import numpy as np

from crypto_screener.config.gu_config import PuConfig, pu_cfg
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


class GuStrategy(Strategy):
    name = "ГУ"

    def __init__(self, config: PuConfig = pu_cfg) -> None:
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

        setup.data.cascade_swings = cascade

        # Центрирование относительно начала каскада.
        todo()

        # Анализ поддержки под каскадом.
        todo() # переписать этот блок правильно с учетом типа каскада (лонговый или шортовый - определить по свингам в нем).
        todo() # блок должен определить свинги, относящиеся к поддержке этого каскада на финальном участке
        initial_swing = cascade_long[0]
        support_swing = None
        if cascade.is_long:
            todo()
            consolidation_range = initial_swing.extremum_price - cascade_low_swing.extremum_price
            open_low_swings = self._get_open_swings(cascade_bars, SwingType.LOW)
            setup.data.support_swings = open_low_swings
            target_swing = cascade_long[-1]

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
            todo()
        has_support = support_swing is not None
        if not has_support:
            return setup

        # Анализ пробоя поддержки каскада.
        current_bar = bars[-1]
        current_price = current_bar.close
        todo() # надо нормально реализовать is_long и is_short
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
                cascade_swings=cascade,
                support_swings=open_low_swings
            )
        )

        # Анализ риска и вознаграждения.
        todo() # сделать блок. потенциалы:
        # TP для top-контекстов и has_high_volume - 2 * ширина каскада (считается как top-bottom на участке от первого свинга каскада до текущей свечи)
        # TP для остальных случаем - 2/3 * ширина каскада
        # SL всегда support_swing.extremum_price

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
        todo() # пока что partial close не делаем, сделаем позднее.
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
                cascade_swings=cascade,
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
    ) -> list[Swing]:
        return self._get_cascade(bars, cascade_type=CascadeType.LONG)

    def _get_cascade_short(
            self,
            bars: list[Bar]
    ) -> list[Swing]:
        return self._get_cascade(bars, cascade_type=CascadeType.SHORT)

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
    def _get_daily_extremes(
            bars: list[Bar],
            day_groups: list[tuple[int, int]],
            *,
            cascade_type: CascadeType,
    ) -> list[dict[str, float]]:
        daily_extremes: list[dict[str, float]] = []
        for start_index, end_index in day_groups:
            day_bars = bars[start_index:end_index + 1]
            if cascade_type is CascadeType.LONG:
                day_price = max(bar.high for bar in day_bars)
                for index in range(start_index, end_index + 1):
                    if bars[index].high == day_price:
                        daily_extremes.append({"index": float(index), "price": float(day_price)})
                        break
            else:
                day_price = min(bar.low for bar in day_bars)
                for index in range(start_index, end_index + 1):
                    if bars[index].low == day_price:
                        daily_extremes.append({"index": float(index), "price": float(day_price)})
                        break
        return daily_extremes

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
    ) -> bool:
        if len(pullbacks) < 2:
            return False
        first_depth = pullbacks[0][0]
        second_depth = pullbacks[1][0]
        if first_depth <= 0 or second_depth <= 0:
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

        day_groups = self._group_bars_by_day(bars)
        daily_extremes = self._get_daily_extremes(bars, day_groups, cascade_type=cascade_type)
        if len(daily_extremes) < min_touches:
            return []

        best_candidate = None
        best_duration = None
        best_last_time = None

        for start_day in range(len(daily_extremes) - 1):
            level_price = daily_extremes[start_day]["price"]
            next_day_price = daily_extremes[start_day + 1]["price"]
            if abs(next_day_price - level_price) > touch_tolerance:
                continue

            touch_indices = []
            for day_idx in range(start_day, len(daily_extremes)):
                day_price = daily_extremes[day_idx]["price"]
                if is_long and day_price > level_price + touch_tolerance:
                    break
                if not is_long and day_price < level_price - touch_tolerance:
                    break
                if abs(day_price - level_price) <= touch_tolerance:
                    touch_indices.append(int(daily_extremes[day_idx]["index"]))

            if len(touch_indices) < min_touches:
                continue

            pullbacks = self._calculate_pullbacks(
                bars,
                touch_indices,
                level_price,
                cascade_type=cascade_type,
            )
            if not pullbacks:
                continue
            if not self._passes_pullback_rules(pullbacks):
                continue
            if not self._is_price_squeezed(pullbacks, cascade_type=cascade_type):
                continue

            first_index = touch_indices[0]
            last_index = touch_indices[-1]
            duration = bars[last_index].time - bars[first_index].time
            last_time = bars[last_index].time
            if best_duration is None or duration > best_duration:
                best_candidate = (touch_indices, level_price)
                best_duration = duration
                best_last_time = last_time
            elif duration == best_duration and best_last_time is not None and last_time > best_last_time:
                best_candidate = (touch_indices, level_price)
                best_duration = duration
                best_last_time = last_time

        if not best_candidate:
            return []

        touch_indices, level_price = best_candidate
        swing_type = SwingType.HIGH if is_long else SwingType.LOW
        cascade_swings = []
        for touch_index in touch_indices:
            bar = bars[touch_index]
            cascade_swings.append(Swing(
                time=bar.time,
                extremum_price=level_price,
                close_price=bar.close,
                type=swing_type,
                is_open=True
            ))

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
