from __future__ import annotations

import numpy as np

from crypto_screener.config.gu_config import PuConfig, pu_cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.cascade_level import CascadeLevel
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
        todo() # Этот метод должен найти такой лонговый каскад, который удовлетворяет условиям:
        # Первый свинг произошел в день N и является максимумом для N (это 1 касание)
        # В день N+1 есть валидное касание (это 2 касание)
        # В день N+1 или последующие дни есть еще касания (это 3+ касания)
        # Размер отката после 2 касания составляет 50-100% от размера отката после 1 касания
        # Размер отката после 3+ касаний составляет 10-90% от размера отката после 2 касания
        # Метод ищет хорошо наторгованный горизонтальный уровень, к которому цена поджимается


        bars = bars[:-1]
        bars_count = len(bars)

        avg_range = self._calculate_average_range(self._config.CASCADE_ATR_WINDOW)
        touch_tolerance = max(self._config.CASCADE_TOUCH_EPS_NATR * avg_range, 0.0)
        min_pullback = max(self._config.CASCADE_MIN_PULLBACK_NATR * avg_range, 0.0)
        min_pullback_bars = self._config.CASCADE_MIN_PULLBACK_BARS
        min_gap_bars = self._config.CASCADE_MIN_GAP_BARS
        levels: list[CascadeLevel] = []
        bar_data = [(i, bar.high, bar.open, bar.close) for i, bar in enumerate(bars)]
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
