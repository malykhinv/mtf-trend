from domain.models.Bar import Bar


def atr(bars: list[Bar], period: int) -> list[float]:
    """Расчёт среднего истинного диапазона (ATR)."""
    if period <= 0:
        raise ValueError("period must be > 0")
    trs: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            tr = bar.high - bar.low
        else:
            prev_close = bars[i - 1].close
            tr = max(bar.high - bar.low, abs(bar.high - prev_close), abs(prev_close - bar.low))
        trs.append(tr)
    atrs: list[float] = []
    for i in range(len(trs)):
        if i + 1 < period:
            atrs.append(sum(trs[: i + 1]) / (i + 1))
        else:
            prev_atr = atrs[-1] if atrs else sum(trs[:period]) / period
            atrs.append((prev_atr * (period - 1) + trs[i]) / period)
    return atrs

