import numpy as np
from domain.models.phase import Phase
from domain.models.mtf_state import MTFState
from domain.models.swing_point import SwingPoint
from domain.models.price_direction import PriceDirection
from domain.models.swing_type import SwingType
from domain.models.timeframe import Timeframe
from utils.logger import log, logw


class MTFAnalyzer:
    def __init__(self, bars, timeframe: Timeframe, atr: float):
        self.bars = bars
        self.timeframe = timeframe
        self.atr = atr

    def analyze(self) -> MTFState:
        swings = self.detect_swing_points()
        highs = [b.high for b in self.bars[-30:]]
        lows = [b.low for b in self.bars[-30:]]

        x = np.arange(len(highs))
        high_slope, _ = np.polyfit(x, highs, 1)
        low_slope, _ = np.polyfit(x, lows, 1)

        slope_diff = abs(high_slope - low_slope)
        slope_avg = (high_slope + low_slope) / 2

        # Swing-структура
        hh_count, hl_count, ll_count, lh_count = self.count_swing_structures(swings)

        # Порог согласованности уклонов
        range_size = max(highs) - min(lows)
        bars_count = len(highs)
        avg_slope_per_bar = range_size / bars_count
        slope_trend_threshold = 0.3 * avg_slope_per_bar

        slope_diff_threshold = 0.02

        if slope_diff < slope_diff_threshold:
            if hh_count >= 3 and hl_count >= 3 and slope_avg > slope_trend_threshold:
                phase = Phase.UPTREND
            elif ll_count >= 3 and lh_count >= 3 and slope_avg < -slope_trend_threshold:
                phase = Phase.DOWNTREND
            else:
                phase = Phase.FLAT
        else:
            phase = Phase.FLAT
            logw(f"{self.timeframe.value}: "
                 f"уклоны не согласованы (slope_diff {round(slope_diff, 5)} >= {slope_diff_threshold})")

        is_in_correction = False
        correction_direction = PriceDirection.UNDEFINED

        if phase.is_uptrend and swings and swings[-1].type.is_low:
            is_in_correction = True
            correction_direction = PriceDirection.DOWN
        elif phase.is_downtrend and swings and swings[-1].type.is_high:
            is_in_correction = True
            correction_direction = PriceDirection.UP

        log(f"{self.timeframe.value}: тренд — {phase.value}, коррекция — {'да' if is_in_correction else 'нет'}")

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

    def detect_swing_points(self):
        swings = []
        direction = PriceDirection.UNDEFINED
        threshold = self.atr * 0.8

        for i in range(1, len(self.bars) - 1):
            bar = self.bars[i]
            prev_bar = self.bars[i - 1]
            next_bar = self.bars[i + 1]

            is_high = bar.high > prev_bar.high and bar.high > next_bar.high
            is_low = bar.low < prev_bar.low and bar.low < next_bar.low

            if is_high:
                if direction != PriceDirection.DOWN or abs(bar.high - prev_bar.high) > threshold:
                    swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH, confirmed=True))
                    direction = PriceDirection.DOWN
            elif is_low:
                if direction != PriceDirection.UP or abs(bar.low - prev_bar.low) > threshold:
                    swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW, confirmed=True))
                    direction = PriceDirection.UP

        return swings

    @staticmethod
    def count_swing_structures(swings):
        hh, hl, ll, lh = 0, 0, 0, 0
        prev_high, prev_low = None, None

        for s in swings:
            if s.type == SwingType.HIGH:
                if prev_high is not None:
                    if s.price > prev_high:
                        hh += 1
                    elif s.price < prev_high:
                        lh += 1
                prev_high = s.price
            elif s.type == SwingType.LOW:
                if prev_low is not None:
                    if s.price > prev_low:
                        hl += 1
                    elif s.price < prev_low:
                        ll += 1
                prev_low = s.price

        return hh, hl, ll, lh
