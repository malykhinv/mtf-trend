from config.constants import MAX_RANGE_SIZE_PCT
from domain.models.bar import Bar
from typing import List

# TODO FIXME
def is_stablecoin(symbol: str) -> bool:
    return False
    return any(stable in symbol.upper() for stable in ["USDC", "BUSD", "DAI", "TUSD"])

# TODO FIXME
def has_messy_candles(bars: List[Bar], tail_ratio_threshold: float = 0.5, body_threshold: float = 0.1) -> bool:
    """
    tail_ratio_threshold: доля свечей с длинными хвостами (tail/total range > 0.5)
    body_threshold: минимальный средний body size / range для чистых свечей
    """
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