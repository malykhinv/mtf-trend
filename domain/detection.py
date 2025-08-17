# domain/detection.py
from __future__ import annotations

from typing import Optional

import os

from config.constants import (
    ATR_PERIOD,
    ATR_BREAKOUT_MULTIPLIER,
    OUTPUT_PLOT_PATH, FRESH_MAX_AGE, TREND_ITERATIONS, WINDOW_TAIL,
)
from domain.models.DowntrendResult import DowntrendResult
from domain.models.Bar import Bar
from domain.models.Exchange import Exchange
from domain.models.Timeframe import Timeframe
from domain.models.Signal import Signal
from domain.models.Extremum import Extremum
from domain.models.ExtremumType import ExtremumType
from services.Plotter import Plotter
from utils.atr import atr


def detect(
        symbol: str,
        exchange: Exchange,
        bars: list[Bar],
        timeframe: Timeframe,
        plotter: Plotter,
) -> Optional[Signal]:
    """
    Главная функция детекции разворота даун-тренда в лонг.
    Возвращает Signal только на закрытии бара i=N-1 при выполнении всех условий.
    """
    n = len(bars)
    if n < max(ATR_PERIOD + 20, 200):
        return None

    atrs = atr(bars, ATR_PERIOD)

    # Окно анализа «хвост»
    e = n - 1
    s = max(0, n - WINDOW_TAIL)

    # Если эпохи нет — строим структуру на хвосте
    r = _has_downtrend(bars, atrs, s, e, TREND_ITERATIONS)
    if not r.ok:
        return None

    bar1_idx, sh_last_idx, last_ll_idx = r.bar1_idx, r.sh_last_idx, r.last_ll_idx

    # Свежесть эпохи
    if (e - last_ll_idx) > FRESH_MAX_AGE:
        return None

    # Подтверждение разворота: две закрытые свечи телом выше level
    level = max(bars[sh_last_idx].open, bars[sh_last_idx].close)
    if e < 1:
        return None
    prev_ok = min(bars[e - 1].open, bars[e - 1].close) > level
    curr_ok = min(bars[e].open, bars[e].close) > level
    if not (prev_ok and curr_ok):
        return None

    # Подготовка экстремумов для графика
    exts: list[Extremum] = [Extremum(bar=bars[bar1_idx], type=ExtremumType.LOW),
                            Extremum(bar=bars[sh_last_idx], type=ExtremumType.HIGH)]
    if last_ll_idx != bar1_idx:
        exts.append(Extremum(bar=bars[last_ll_idx], type=ExtremumType.LOW))

    # Генерация графика
    os.makedirs(OUTPUT_PLOT_PATH, exist_ok=True)
    chart_path = os.path.join(
        OUTPUT_PLOT_PATH,
        f"{exchange.name.lower()}__{symbol.replace('/', '-')}__{timeframe.value}.png",
    )
    try:
        plotter.plot(bars, exts, path=chart_path)
    except Exception:
        # даже если отрисовка не удалась — лучше всё равно отдать сигнал
        pass

    # Сигнал (extremums: список ключевых баров — как в твоём Signal)
    extremum_bars: list[Bar] = [bars[bar1_idx], bars[sh_last_idx]]
    if last_ll_idx not in (bar1_idx, sh_last_idx):
        extremum_bars.append(bars[last_ll_idx])

    return Signal(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        extremums=extremum_bars,
        chart_path=chart_path,
    )


def _is_ll(ref_low: float, low_j: float, atr_j: float) -> bool:
    # «Ниже на ≥ ATR_BREAKOUT_MULTIPLIER×ATR»
    return (ref_low - low_j) >= ATR_BREAKOUT_MULTIPLIER * atr_j


def _exists_upmove_without_ll(bars: list[Bar], atrs: list[float], i: int) -> Optional[int]:
    """
    Для кандидата bar1=i: существует минимальный k>i:
      (C[k] - L[i]) ≥ 5*ATR[k] и для всех t∈(i,k): L[t] ≥ L[i]
    Вернёт k (минимальный), если условие выполнено, иначе None.
    """
    base_low = bars[i].low
    n = len(bars)
    for k in range(i + 1, n):
        if bars[k].close - base_low >= ATR_BREAKOUT_MULTIPLIER * atrs[k]:
            ok = True
            for t in range(i + 1, k):
                if bars[t].low < base_low:
                    ok = False
                    break
            if ok:
                return k
    return None


def _find_bar3(bars: list[Bar], bar1_idx: int) -> Optional[int]:
    """Первая свеча после bar1 с закрытием ниже low[bar1]."""
    low_bar1 = bars[bar1_idx].low
    for x in range(bar1_idx + 1, len(bars)):
        if bars[x].close < low_bar1:
            return x
    return None


def _find_bar2_on_range_max_high(bars: list[Bar], left: int, right: int) -> int:
    """
    Самый высокий high на [left..right], при равенстве — ранний индекс.
    Гарантируется left<=right.
    """
    m = left
    max_h = bars[left].high
    for idx in range(left + 1, right + 1):
        h = bars[idx].high
        if h > max_h:
            max_h = h
            m = idx
        # при равенстве оставляем ранний индекс (ничего не делаем)
    return m


def _has_downtrend(
        bars: list[Bar],
        atrs: list[float],
        s: int,
        e: int,
        iterations: int,
) -> DowntrendResult:
    """
    Реализация d1..d6 из спецификации для окна [s..e], включительно.
    """
    if e - s < 3:
        return DowntrendResult(ok=False)

    bar0 = s
    done = 0
    bar1_idx = sh_last_idx = last_ll_idx = -1

    while bar0 < e:
        # d2: найти bar1-кандидата j: initial_low - L[j] ≥ 5*ATR[j]
        initial_low = bars[bar0].low
        j = None
        for idx in range(bar0 + 1, e + 1):
            if _is_ll(initial_low, bars[idx].low, atrs[idx]):
                j = idx
                break
        if j is None:
            return DowntrendResult(ok=False)

        # d3: выбрать bar1: минимальный i≥j, для которого есть подъём ≥5*ATR без перелоя
        i = None
        for cand in range(j, e + 1):
            k = _exists_upmove_without_ll(bars, atrs, cand)
            if k is not None:
                i = cand
                break
        if i is None:
            return DowntrendResult(ok=False)

        bar1_idx = i
        last_ll_idx = i

        # d4: bar3 — первая свеча close < low[bar1]
        bar3 = _find_bar3(bars, bar1_idx)
        if bar3 is None or bar3 > e:
            return DowntrendResult(ok=False)

        # d5: bar2/sh_last — максимум high на [bar1..bar3] (ранний при равенстве)
        sh_last_idx = _find_bar2_on_range_max_high(bars, bar1_idx, bar3)

        # d6: итерация
        done += 1
        if done >= iterations:
            return DowntrendResult(ok=True, bar1_idx=bar1_idx, sh_last_idx=sh_last_idx, last_ll_idx=last_ll_idx)

        bar0 = bar3  # продолжаем искать следующую итерацию
        # цикл while -> d2..d5 снова

    return DowntrendResult(ok=False)
