from config.constants import ATR_PERIOD

def calculate_atr(bars, period=ATR_PERIOD) -> list[float]:
    """Возвращает список ATR по заданным барам."""
    atr_values = []
    tr_values = []
    for i in range(len(bars)):
        high = bars[i].high
        low = bars[i].low

        if i == 0:
            tr = high - low  # без prev_close
        else:
            prev_close = bars[i - 1].close
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )

        tr_values.append(tr)

        if i < period:
            avg_tr = sum(tr_values) / len(tr_values)
        else:
            avg_tr = sum(tr_values[-period:]) / period

        atr_values.append(avg_tr)

    return atr_values

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
