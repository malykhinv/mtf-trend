from config.constants import MIN_BARS_BETWEEN_SWINGS
from domain.models.bar import Bar
from domain.models.swing_point import SwingPoint
from typing import List

from domain.models.swing_type import SwingType
from utils.math_utils import calculate_atr


class StructureDetector:

    def detect_swing_points(self, bars: List[Bar]) -> List[SwingPoint]:
        swings = []
        if len(bars) == 0:
            return swings

        last_swing = None
        atr = calculate_atr(bars)
        atr_factor = 1.0
        atr_threshold = atr * atr_factor

        for i in range(1, len(bars) - 1):
            bar = bars[i]
            prev_bar = bars[i - 1]
            next_bar = bars[i + 1]

            is_potential_high = bar.high > prev_bar.high and bar.high > next_bar.high
            is_potential_low = bar.low < prev_bar.low and bar.low < next_bar.low

            if last_swing and (i - last_swing.index) < MIN_BARS_BETWEEN_SWINGS:
                continue

            if is_potential_high:
                if not last_swing or bar.high > last_swing.price + atr_threshold:
                    swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH))
                    last_swing = swings[-1]

            elif is_potential_low:
                if not last_swing or bar.low < last_swing.price - atr_threshold:
                    swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW))
                    last_swing = swings[-1]

        swings = self._clean_swings(swings)

        while True:
            prev_indices = [s.index for s in swings]

            # Последовательная доработка каждого свинга индивидуально, до сходимости
            swings = self._move_swings_in_range(bars, swings, SwingType.LOW)
            swings = self._move_swings_in_range(bars, swings, SwingType.HIGH)

            new_indices = [s.index for s in swings]
            if new_indices == prev_indices:
                break

        return swings

    @staticmethod
    def _clean_swings(swings):
        cleaned = []
        for swing in swings:
            if not cleaned:
                cleaned.append(swing)
                continue
            last = cleaned[-1]
            if swing.type == last.type:
                if abs(swing.index - last.index) >= MIN_BARS_BETWEEN_SWINGS:
                    if swing.type.is_high and swing.price > last.price:
                        cleaned[-1] = swing
                    elif swing.type.is_low and swing.price < last.price:
                        cleaned[-1] = swing
            else:
                cleaned.append(swing)
        return cleaned

    @staticmethod
    def _move_swings_in_range(bars, swings, type_to_adjust):
        adjusted = swings.copy()

        if type_to_adjust.is_low:
            opposite_indices = [s.index for s in swings if s.type.is_high]
        else:
            opposite_indices = [s.index for s in swings if s.type.is_low]

        all_indices = [0] + opposite_indices + [len(bars) - 1]

        for i in range(len(all_indices) - 1):
            start_idx = all_indices[i]
            end_idx = all_indices[i + 1]

            for swing in adjusted:
                if swing.type == type_to_adjust and start_idx < swing.index < end_idx:
                    changed = True
                    while changed:
                        prev_index = swing.index

                        bars_segment = bars[start_idx:end_idx + 1]

                        if type_to_adjust.is_low:
                            min_bar = min(bars_segment, key=lambda b: b.low)
                            new_index = bars.index(min_bar)
                            if new_index != swing.index:
                                # Проверяем минимальную дистанцию
                                left_neighbor = next((s for s in reversed(adjusted) if s.index < swing.index), None)
                                right_neighbor = next((s for s in adjusted if s.index > swing.index), None)

                                if (
                                        (left_neighbor is None or abs(
                                            new_index - left_neighbor.index) >= MIN_BARS_BETWEEN_SWINGS)
                                        and (right_neighbor is None or abs(
                                    new_index - right_neighbor.index) >= MIN_BARS_BETWEEN_SWINGS)
                                ):
                                    swing.index = new_index
                                    swing.price = min_bar.low
                                else:
                                    # Не двигаем, если нарушается минимальная дистанция
                                    changed = False
                                    continue
                        elif type_to_adjust.is_high:
                            max_bar = max(bars_segment, key=lambda b: b.high)
                            new_index = bars.index(max_bar)
                            if new_index != swing.index:
                                left_neighbor = next((s for s in reversed(adjusted) if s.index < swing.index), None)
                                right_neighbor = next((s for s in adjusted if s.index > swing.index), None)

                                if (
                                        (left_neighbor is None or abs(
                                            new_index - left_neighbor.index) >= MIN_BARS_BETWEEN_SWINGS)
                                        and (right_neighbor is None or abs(
                                    new_index - right_neighbor.index) >= MIN_BARS_BETWEEN_SWINGS)
                                ):
                                    swing.index = new_index
                                    swing.price = max_bar.high
                                else:
                                    changed = False
                                    continue

                        changed = prev_index != swing.index

        return adjusted
