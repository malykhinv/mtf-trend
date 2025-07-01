from config.constants import (
    STRONG_TREND_ATR_FACTOR,
    MIN_HL_DISTANCE_ATR,
    MIN_TREND_HH_COUNT,
    MIN_TREND_HL_COUNT
)
from domain.models.phase import Phase
from domain.models.mtf_state import MTFState
from domain.models.swing_point import SwingPoint
from domain.models.price_direction import PriceDirection
from domain.models.swing_type import SwingType
from domain.models.timeframe import Timeframe
from utils.logger import log
from utils.plot_trend import plot_trend


class MTFAnalyzer:
    def __init__(self, bars, timeframe: Timeframe, atr: float):
        self.bars = bars
        self.timeframe = timeframe
        self.atr = atr

    def analyze(self) -> MTFState:
        swings = self.detect_swing_points()
        plot_trend(self.bars, swings, timeframe_name=self.timeframe.value, file_name=f"trend_{self.timeframe.value}")

        if self.is_strong_uptrend(swings, self.atr):
            phase = Phase.UPTREND
        elif self.is_strong_downtrend(swings, self.atr):
            phase = Phase.DOWNTREND
        else:
            phase = Phase.FLAT

        is_in_correction = False
        correction_direction = PriceDirection.UNDEFINED

        if phase.is_uptrend and swings and swings[-1].type.is_low:
            is_in_correction = True
            correction_direction = PriceDirection.DOWN
        elif phase.is_downtrend and swings and swings[-1].type.is_high:
            is_in_correction = True
            correction_direction = PriceDirection.UP

        highs = [b.high for b in self.bars[-30:]]
        lows = [b.low for b in self.bars[-30:]]

        log(f"{self.timeframe.value:<4} {phase.value.capitalize()}{', коррекция' if is_in_correction else ''}")

        return MTFState(
            timeframe=self.timeframe,
            phase=phase,
            structure=swings,
            is_in_correction=is_in_correction,
            correction_direction=correction_direction,
            is_range=phase.is_flat,
            range_high=max(highs),
            range_low=min(lows)
        )

    def detect_swing_points(self, atr_factor: float = 1.0, min_bars_between_swing: int = 3):
        swings = []
        last_swing = None
        atr_threshold = self.atr * atr_factor

        for i in range(1, len(self.bars) - 1):
            bar = self.bars[i]
            prev_bar = self.bars[i - 1]
            next_bar = self.bars[i + 1]

            is_potential_high = bar.high > prev_bar.high and bar.high > next_bar.high
            is_potential_low = bar.low < prev_bar.low and bar.low < next_bar.low

            if last_swing and (i - last_swing.index) < min_bars_between_swing:
                continue  # слишком близко к предыдущему свингу

            if is_potential_high:
                if last_swing and last_swing.type.is_low:
                    if bar.high > last_swing.price + atr_threshold:
                        swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH, confirmed=True))
                        last_swing = swings[-1]
                elif last_swing and last_swing.type.is_high:
                    if bar.high > last_swing.price + atr_threshold:
                        swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH, confirmed=True))
                        last_swing = swings[-1]
                elif not last_swing:
                    swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH, confirmed=True))
                    last_swing = swings[-1]

            elif is_potential_low:
                if last_swing and last_swing.type.is_high:
                    if bar.low < last_swing.price - atr_threshold:
                        swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW, confirmed=True))
                        last_swing = swings[-1]
                elif last_swing and last_swing.type.is_low:
                    if bar.low < last_swing.price - atr_threshold:
                        swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW, confirmed=True))
                        last_swing = swings[-1]
                elif not last_swing:
                    swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW, confirmed=True))
                    last_swing = swings[-1]

        return swings

    @staticmethod
    def is_strong_uptrend(swings, atr: float):
        if len(swings) < 4:
            return False

        hh_count = 0
        hl_count = 0

        for i in range(1, len(swings) - 1, 2):
            prev = swings[i - 1]
            curr = swings[i]

            # Проверка сильного HH
            if prev.type.is_low and curr.type.is_high:
                if curr.price > prev.price + STRONG_TREND_ATR_FACTOR * atr:
                    hh_count += 1

            # Проверка высокого HL
            if i + 1 < len(swings):
                next_low = swings[i + 1]
                if next_low.type.is_low:
                    min_expected = prev.price + MIN_HL_DISTANCE_ATR * atr
                    if next_low.price > min_expected:
                        hl_count += 1

        return hh_count >= MIN_TREND_HH_COUNT and hl_count >= MIN_TREND_HL_COUNT

    @staticmethod
    def is_strong_downtrend(swings, atr: float):
        if len(swings) < 4:
            return False

        ll_count = 0
        lh_count = 0

        for i in range(1, len(swings) - 1, 2):
            prev = swings[i - 1]
            curr = swings[i]

            # Проверка сильного LL
            if prev.type.is_high and curr.type.is_low:
                if curr.price < prev.price - STRONG_TREND_ATR_FACTOR * atr:
                    ll_count += 1

            # Проверка низкого LH
            if i + 1 < len(swings):
                next_high = swings[i + 1]
                if next_high.type.is_high:
                    max_expected = prev.price - MIN_HL_DISTANCE_ATR * atr
                    if next_high.price < max_expected:
                        lh_count += 1

        return ll_count >= MIN_TREND_HH_COUNT and lh_count >= MIN_TREND_HL_COUNT
