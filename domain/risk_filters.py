from config.constants import MAX_RANGE_SIZE_PCT, ACTUAL_CONTEXT_CANDLES
from domain.models.bar import Bar
from typing import List

def has_gaps(bars: List[Bar], eps: float = 1e-8) -> bool:
    """
    Проверяет наличие реальных гэпов: когда диапазоны [low, high] двух соседних свечей не пересекаются.
    """
    if len(bars) < 2:
        return False

    for prev, curr in zip(bars, bars[1:]):
        if prev.high < curr.low - eps or curr.high < prev.low - eps:
            return True
    return False

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

def is_calm(bars: List[Bar]) -> bool:
    """
    Вычисляет диапазон (max - min) из уже загруженных баров.
    """
    bars = bars[-7:]
    if len(bars) < 2:
        return False

    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    if low == 0:
        return False

    range_pct = (high - low) / low * 100
    return range_pct <= MAX_RANGE_SIZE_PCT

def is_rising(bars: List[Bar]) -> bool:
    """
    Проверяет, превысили ли последние свечи предыдущий максимум.
    """
    if len(bars) <= ACTUAL_CONTEXT_CANDLES:
        return False

    recent = bars[-ACTUAL_CONTEXT_CANDLES:]
    context = bars[:-ACTUAL_CONTEXT_CANDLES]

    max_recent_high = max(bar.high for bar in recent)
    max_context_high = max(bar.high for bar in context)

    return max_recent_high > max_context_high