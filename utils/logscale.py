# utils/logscale.py
from __future__ import annotations

import math
from dataclasses import replace
from typing import Sequence, List

from domain.models.Bar import Bar


def to_log_scale(
    bars: Sequence[Bar],
    *,
    base: float = math.e,
    reassert_hilo: bool = True,
) -> List[Bar]:
    """
    Преобразовать цены (open, high, low, close) в логарифмы с указанным основанием.

    - Время и объём сохраняются без изменений.
    - ATR обнуляется (его нужно пересчитать на лог-ценах).
    - Если встречается неположительная цена — кидаем ValueError.
    - reassert_hilo=True: после лог-преобразования заново вычисляем low/high
      как min/max из (O,H,L,C), чтобы гарантировать инвариант L <= O,C <= H.
    """
    out: List[Bar] = []
    for b in bars:
        o, h, l, c = b.open, b.high, b.low, b.close
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            raise ValueError(
                f"Non-positive price at {b.time.isoformat()}: "
                f"o={o}, h={h}, l={l}, c={c}"
            )

        lo, lh, ll, lc = _log(o, base), _log(h, base), _log(l, base), _log(c, base)
        if reassert_hilo:
            hi_val = max(lo, lh, ll, lc)
            lo_val = min(lo, lh, ll, lc)
        else:
            hi_val = lh
            lo_val = ll

        out.append(
            Bar(
                time=b.time,
                open=lo,
                high=hi_val,
                low=lo_val,
                close=lc,
                volume=b.volume,
                atr=None,  # ATR всегда пересчитываем после лог-преобразования
            )
        )
    return out


def from_log_scale(
    bars: Sequence[Bar],
    *,
    base: float = math.e,
    reassert_hilo: bool = True,
) -> List[Bar]:
    """
    Обратное преобразование: экспонента (или base**x) для O/H/L/C.
    ATR обнуляется (его тоже нужно пересчитать при необходимости).
    """
    out: List[Bar] = []
    for b in bars:
        oo, hh, ll, cc = _pow(base, b.open), _pow(base, b.high), _pow(base, b.low), _pow(base, b.close)
        if reassert_hilo:
            hi_val = max(oo, hh, ll, cc)
            lo_val = min(oo, hh, ll, cc)
        else:
            hi_val = hh
            lo_val = ll

        out.append(
            replace(b, open=oo, high=hi_val, low=lo_val, close=cc, atr=None)
        )
    return out


def _log(x: float, base: float) -> float:
    """Быстрый логарифм с произвольным основанием."""
    if base == math.e:
        return math.log(x)
    # change-of-base: ln(x) / ln(base)
    return math.log(x) / math.log(base)


def _pow(base: float, x: float) -> float:
    """Обратное преобразование: base**x (exp для e)."""
    if base == math.e:
        return math.exp(x)
    return base ** x