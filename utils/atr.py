from statistics import median
from domain.models.Bar import Bar


def atr(bars: list[Bar], period: int, *, clip_k: float = 3.5, min_win: int = 5) -> list[float]:
    """
    ATR (Wilder) с накоплением на старте и подавлением аномальных свечей.
    Выбросы TR клиппируются верхней границей: median(prev_TR) + clip_k * 1.4826 * MAD(prev_TR),
    где prev_TR — окно предыдущих TR (до текущей свечи), размером до `period`.
    """
    if period <= 0:
        raise ValueError("period must be > 0")

    trs: list[float] = []
    for i, bar in enumerate(bars):
        # raw True Range
        if i == 0:
            tr = bar.high - bar.low
        else:
            prev_close = bars[i - 1].close
            tr = max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(prev_close - bar.low),
            )

        # робастное верхнее отсечение по окну прошлых TR (не включая текущую свечу)
        if i > 0:
            left = max(0, i - period)
            window = trs[left:i]  # уже накопленные/отклиппированные TR
            if len(window) >= min_win:
                m = median(window)
                mad = median(abs(x - m) for x in window)
                scale = 1.4826 * mad  # консистентная оценка σ
                upper = (m + clip_k * scale) if scale > 0 else m  # если MAD=0 — жёстко прижимаем к медиане
                if tr > upper:
                    tr = upper

        trs.append(tr)

    # Wilder smoothing с «разгоном» на первых барах
    atrs: list[float] = []
    for i in range(len(trs)):
        if i + 1 < period:
            atrs.append(sum(trs[: i + 1]) / (i + 1))
        else:
            prev_atr = atrs[-1] if atrs else sum(trs[:period]) / period
            atrs.append((prev_atr * (period - 1) + trs[i]) / period)

    return atrs
