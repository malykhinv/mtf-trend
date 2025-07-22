from typing import List

from config.constants import MIN_BARS_BETWEEN_SWINGS
from domain.models.bar import Bar
from domain.models.swing_point import SwingPoint
from domain.models.swing_type import SwingType


class StructureDetector:
    def detect_swing_points(self, bars: List[Bar], atrs: List[float]) -> List[SwingPoint]:
        swings = []
        if not bars or not atrs or len(bars) != len(atrs):
            return swings

        last_swing = None

        for i in range(1, len(bars) - 1):
            bar = bars[i]
            prev_bar = bars[i - 1]
            next_bar = bars[i + 1]
            atr_threshold = atrs[i] * (0.5 if len(swings) < 2 else 1.0)

            is_potential_high = bar.high > prev_bar.high and bar.high > next_bar.high
            is_potential_low = bar.low < prev_bar.low and bar.low < next_bar.low

            if last_swing and (i - last_swing.index) < MIN_BARS_BETWEEN_SWINGS:
                continue

            if is_potential_high:
                if not last_swing or bar.high > last_swing.price + atr_threshold:
                    swings.append(SwingPoint(timestamp=bar.timestamp, index=i, price=bar.high, type=SwingType.HIGH))
                    last_swing = swings[-1]

            elif is_potential_low:
                if not last_swing or bar.low < last_swing.price - atr_threshold:
                    swings.append(SwingPoint(timestamp=bar.timestamp, index=i, price=bar.low, type=SwingType.LOW))
                    last_swing = swings[-1]

        swings = self._clean_swings(swings)

        while True:
            prev_indices = [s.index for s in swings]
            swings = self._move_swings_in_range(bars, swings, SwingType.LOW)
            swings = self._move_swings_in_range(bars, swings, SwingType.HIGH)
            new_indices = [s.index for s in swings]
            if new_indices == prev_indices:
                break

        return swings

    @staticmethod
    def _clean_swings(swings: List[SwingPoint]) -> List[SwingPoint]:
        """Сохраняем только значимые свинги без затирания близких по типу."""
        cleaned = []
        for swing in swings:
            if not cleaned:
                cleaned.append(swing)
                continue
            last = cleaned[-1]
            if swing.type != last.type:
                cleaned.append(swing)
            else:
                distance_ok = abs(swing.index - last.index) >= MIN_BARS_BETWEEN_SWINGS
                if not distance_ok:
                    continue
                # Оставляем оба, если оба локальные и достаточно далеко
                cleaned.append(swing)
        return cleaned

    @staticmethod
    def _move_swings_in_range(bars: List[Bar], swings: List[SwingPoint], type_to_adjust: SwingType) -> List[SwingPoint]:
        adjusted = swings.copy()

        opposite_indices = [s.index for s in swings if s.type != type_to_adjust]
        if not opposite_indices:
            return swings

        all_indices = [0] + opposite_indices + [len(bars) - 1]

        for i in range(len(all_indices) - 1):
            start_idx = all_indices[i]
            end_idx = all_indices[i + 1]

            for swing in adjusted:
                if swing.type != type_to_adjust or not (start_idx < swing.index < end_idx):
                    continue

                changed = True
                while changed:
                    prev_index = swing.index
                    bars_segment = bars[start_idx:end_idx + 1]

                    if type_to_adjust.is_low:
                        min_bar = min(bars_segment, key=lambda b: b.low)
                        new_index = start_idx + bars_segment.index(min_bar)
                        if new_index != swing.index:
                            if StructureDetector._check_swing_spacing(adjusted, swing, new_index):
                                swing.index = new_index
                                swing.price = min_bar.low
                                swing.timestamp = bars[new_index].timestamp
                            else:
                                changed = False
                                continue
                    elif type_to_adjust.is_high:
                        max_bar = max(bars_segment, key=lambda b: b.high)
                        new_index = start_idx + bars_segment.index(max_bar)
                        if new_index != swing.index:
                            if StructureDetector._check_swing_spacing(adjusted, swing, new_index):
                                swing.index = new_index
                                swing.price = max_bar.high
                                swing.timestamp = bars[new_index].timestamp
                            else:
                                changed = False
                                continue

                    changed = prev_index != swing.index

        return adjusted

    @staticmethod
    def _check_swing_spacing(swings: List[SwingPoint], current: SwingPoint, new_index: int) -> bool:
        """Проверка дистанции до ближайших соседей."""
        left_neighbor = next((s for s in reversed(swings) if s.index < current.index and s != current), None)
        right_neighbor = next((s for s in swings if s.index > current.index and s != current), None)

        left_ok = left_neighbor is None or abs(new_index - left_neighbor.index) >= MIN_BARS_BETWEEN_SWINGS
        right_ok = right_neighbor is None or abs(new_index - right_neighbor.index) >= MIN_BARS_BETWEEN_SWINGS

        return left_ok and right_ok
