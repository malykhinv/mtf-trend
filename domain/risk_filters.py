from config.constants import MAX_RANGE_SIZE_PCT
from domain.models.bar import Bar
from typing import List

def has_repeating_ohlc(bars: List[Bar], sequence_len: int = 2) -> bool:
    """
    Проверяет, есть ли хотя бы одна последовательность из `sequence_len` свечей подряд с одинаковыми OHLC.
    """
    if len(bars) < sequence_len:
        return False

    count = 1
    prev = bars[0]
    for curr in bars[1:]:
        if (curr.open == prev.open and
            curr.high == prev.high and
            curr.low == prev.low and
            curr.close == prev.close):
            count += 1
            if count >= sequence_len:
                return True
        else:
            count = 1
        prev = curr
    return False


def is_calm(bars_1d: list[Bar]) -> bool:
    """
    Вычисляет диапазон (max - min) из уже загруженных баров 1d.
    """
    bars_1d = bars_1d[-7:]
    if not bars_1d or len(bars_1d) < 2:
        return False

    high = max(b.high for b in bars_1d)
    low = min(b.low for b in bars_1d)
    if low == 0:
        return False

    range_pct = (high - low) / low * 100
    return range_pct <= MAX_RANGE_SIZE_PCT