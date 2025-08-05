from __future__ import annotations

from typing import Iterable, List

from .bar import Bar
from .extremum import Extremum, ExtremumType


def detect_extremums(bars: Iterable[Bar]) -> List[Extremum]:
    """Простейший алгоритм поиска локальных максимумов/минимумов."""
    bar_list = list(bars)
    extremums: List[Extremum] = []
    for i in range(1, len(bar_list) - 1):
        prev_bar = bar_list[i - 1]
        bar = bar_list[i]
        next_bar = bar_list[i + 1]
        if bar.high > prev_bar.high and bar.high > next_bar.high:
            extremums.append(Extremum(time=bar.time, price=bar.high, type=ExtremumType.HIGH))
        if bar.low < prev_bar.low and bar.low < next_bar.low:
            extremums.append(Extremum(time=bar.time, price=bar.low, type=ExtremumType.LOW))
    return extremums

