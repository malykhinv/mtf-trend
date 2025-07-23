from config.constants import MAX_RANGE_PERCENT, NUM_CONTEXT_CANDLES
from domain.models.bar import Bar
from typing import List

def has_gaps(bars: List[Bar], eps: float = 1e-8) -> bool:
    """
    Проверяет наличие реальных гэпов: когда диапазоны [low, high] двух соседних свечей не пересекаются.

    Args:
        bars (List[Bar]): Список баров для проверки.
        eps (float): Допуск для сравнения на float.
    Returns:
        bool: True, если найден хотя бы один гэп.
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

    Args:
        bars (List[Bar]): Список баров для проверки.
        sequence_len (int): Длина последовательности одинаковых баров.
    Returns:
        bool: True, если есть хотя бы такая последовательность.
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
    Вычисляет относительный диапазон последних 7 баров и сравнивает с максимально допустимым.

    Args:
        bars (List[Bar]): Список баров для анализа.
    Returns:
        bool: True, если диапазон меньше MAX_RANGE_SIZE_PCT.
    """
    bars = bars[-7:]
    if len(bars) < 2:
        return False

    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    if low == 0:
        return False

    range_pct = (high - low) / low * 100
    return range_pct <= MAX_RANGE_PERCENT

def is_rising(bars: List[Bar]) -> bool:
    """
    Проверяет, превысили ли последние (актуальные) свечи предыдущий максимум (разворот).

    Args:
        bars (List[Bar]): Массив баров.
    Returns:
        bool: True, если свежие свечи обновили максимум относительно истории.
    """
    if len(bars) <= NUM_CONTEXT_CANDLES:
        return False

    recent = bars[-NUM_CONTEXT_CANDLES:]
    context = bars[:-NUM_CONTEXT_CANDLES]

    max_recent_high = max(bar.high for bar in recent)
    max_context_high = max(bar.high for bar in context)

    return max_recent_high > max_context_high