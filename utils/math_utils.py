from config.constants import ATR_PERIOD
from utils.decorator import log_duration_ms


@log_duration_ms
def calculate_atr(bars, period=ATR_PERIOD) -> list[float]:
    """Возвращает список ATR по заданным барам."""
    import numpy as np

    if not bars:
        return []

    highs = np.asarray([b.high for b in bars], dtype=float)
    lows = np.asarray([b.low for b in bars], dtype=float)
    closes = np.asarray([b.close for b in bars], dtype=float)

    prev_closes = np.concatenate(([closes[0]], closes[:-1]))
    tr = np.maximum.reduce([
        highs - lows,
        np.abs(highs - prev_closes),
        np.abs(lows - prev_closes)
    ])

    cumsum = np.cumsum(tr)
    atr = np.empty_like(tr)

    p = min(period, len(tr))
    atr[:p] = cumsum[:p] / (np.arange(p) + 1)
    if len(tr) > period:
        atr[period:] = (cumsum[period:] - cumsum[:-period]) / period

    return atr.tolist()

@log_duration_ms
def most(items: list, predicate=None) -> bool:
    if predicate is None:
        predicate = bool

    n = len(items)
    half = n >> 1
    passed = 0

    for i in range(n):
        if predicate(items[i]):
            passed += 1
            if passed > half:
                return True
        elif i - passed + 1 > half:
            return False

    return passed > half
