from config.constants import (
    STRONG_TREND_ATR_FACTOR,
    MIN_HL_DISTANCE_ATR,
    MIN_TREND_HH_COUNT,
    MIN_TREND_HL_COUNT, MIN_BARS_BETWEEN_SWINGS
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

    def analyze(self, has_plot: bool = False) -> MTFState:
        swings = self.detect_swing_points(atr_factor=1.0)
        swings = self.clean_swings(swings)

        # Итерационный процесс корректировки
        while True:
            prev_indices = [s.index for s in swings]

            # Последовательная доработка каждого свинга индивидуально, до сходимости
            swings = self.move_swings_in_range(swings, SwingType.LOW)
            swings = self.move_swings_in_range(swings, SwingType.HIGH)

            new_indices = [s.index for s in swings]
            if new_indices == prev_indices:
                break

        if has_plot:
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
            is_range=phase.is_flat or is_in_correction,
            range_high=max(highs),
            range_low=min(lows)
        )

    def detect_swing_points(self, atr_factor: float = 1.0, min_bars_between_swing: int = MIN_BARS_BETWEEN_SWINGS):
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
                continue

            if is_potential_high:
                if not last_swing or bar.high > last_swing.price + atr_threshold:
                    swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH, confirmed=True))
                    last_swing = swings[-1]

            elif is_potential_low:
                if not last_swing or bar.low < last_swing.price - atr_threshold:
                    swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW, confirmed=True))
                    last_swing = swings[-1]

        return swings

    @staticmethod
    def clean_swings(swings, min_bars_between_swing=MIN_BARS_BETWEEN_SWINGS):
        cleaned = []
        for swing in swings:
            if not cleaned:
                cleaned.append(swing)
                continue
            last = cleaned[-1]
            if swing.type == last.type:
                if abs(swing.index - last.index) >= min_bars_between_swing:
                    if swing.type.is_high and swing.price > last.price:
                        cleaned[-1] = swing
                    elif swing.type.is_low and swing.price < last.price:
                        cleaned[-1] = swing
            else:
                cleaned.append(swing)
        return cleaned

    def move_swings_in_range(self, swings, type_to_adjust, min_bars_between_swing=MIN_BARS_BETWEEN_SWINGS):
        adjusted = swings.copy()

        if type_to_adjust == SwingType.LOW:
            opposite_indices = [s.index for s in swings if s.type == SwingType.HIGH]
        else:
            opposite_indices = [s.index for s in swings if s.type == SwingType.LOW]

        all_indices = [0] + opposite_indices + [len(self.bars) - 1]

        for i in range(len(all_indices) - 1):
            start_idx = all_indices[i]
            end_idx = all_indices[i + 1]

            for swing in adjusted:
                if swing.type == type_to_adjust and start_idx < swing.index < end_idx:
                    changed = True
                    while changed:
                        prev_index = swing.index

                        bars_segment = self.bars[start_idx:end_idx + 1]

                        if type_to_adjust == SwingType.LOW:
                            min_bar = min(bars_segment, key=lambda b: b.low)
                            new_index = self.bars.index(min_bar)
                            if new_index != swing.index:
                                # Проверяем минимальную дистанцию
                                left_neighbor = next((s for s in reversed(adjusted) if s.index < swing.index), None)
                                right_neighbor = next((s for s in adjusted if s.index > swing.index), None)

                                if (
                                        (left_neighbor is None or abs(
                                            new_index - left_neighbor.index) >= min_bars_between_swing)
                                        and (right_neighbor is None or abs(
                                    new_index - right_neighbor.index) >= min_bars_between_swing)
                                ):
                                    swing.index = new_index
                                    swing.price = min_bar.low
                                else:
                                    # Не двигаем, если нарушается минимальная дистанция
                                    changed = False
                                    continue
                        elif type_to_adjust == SwingType.HIGH:
                            max_bar = max(bars_segment, key=lambda b: b.high)
                            new_index = self.bars.index(max_bar)
                            if new_index != swing.index:
                                left_neighbor = next((s for s in reversed(adjusted) if s.index < swing.index), None)
                                right_neighbor = next((s for s in adjusted if s.index > swing.index), None)

                                if (
                                        (left_neighbor is None or abs(
                                            new_index - left_neighbor.index) >= min_bars_between_swing)
                                        and (right_neighbor is None or abs(
                                    new_index - right_neighbor.index) >= min_bars_between_swing)
                                ):
                                    swing.index = new_index
                                    swing.price = max_bar.high
                                else:
                                    changed = False
                                    continue

                        changed = prev_index != swing.index

        return adjusted

    @staticmethod
    def is_strong_uptrend(swings, atr: float):
        if len(swings) < 4:
            return False

        hh_count = 0
        hl_count = 0

        for i in range(1, len(swings) - 1, 2):
            prev = swings[i - 1]
            curr = swings[i]

            if prev.type.is_low and curr.type.is_high:
                if curr.price > prev.price + STRONG_TREND_ATR_FACTOR * atr:
                    hh_count += 1

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

            if prev.type.is_high and curr.type.is_low:
                if curr.price < prev.price - STRONG_TREND_ATR_FACTOR * atr:
                    ll_count += 1

            if i + 1 < len(swings):
                next_high = swings[i + 1]
                if next_high.type.is_high:
                    max_expected = prev.price - MIN_HL_DISTANCE_ATR * atr
                    if next_high.price < max_expected:
                        lh_count += 1

        return ll_count >= MIN_TREND_HH_COUNT and lh_count >= MIN_TREND_HL_COUNT
