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
from utils.logger import log, logw


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
    log(f"[{exchange.name} {symbol} {timeframe.value}] Старт детекции. Баров={n}")
    min_needed = max(ATR_PERIOD + 20, 200)
    if n < min_needed:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Недостаточно баров для анализа (нужно ≥ {min_needed}). Пропускаю.")
        return None

    atrs = atr(bars, ATR_PERIOD)
    log(f"[{exchange.name} {symbol} {timeframe.value}] ATR рассчитан (period={ATR_PERIOD}).")

    # Окно анализа «хвост»
    e = n - 1
    s = max(0, n - WINDOW_TAIL)
    log(f"[{exchange.name} {symbol} {timeframe.value}] Окно анализа: s={s}, e={e}, длина={e - s + 1}, WINDOW_TAIL={WINDOW_TAIL}.")

    # Строим структуру на хвосте
    r = _has_downtrend(bars, atrs, s, e, TREND_ITERATIONS)
    if not r.has_downtrend:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Нисходящая структура в окне не найдена. Сигнал не формируется.")
        return None

    bar1_idx, sh_last_idx, last_ll_idx = r.bar1_idx, r.sh_last_idx, r.last_ll_idx

    # Свежесть эпохи
    age = e - last_ll_idx
    if age > FRESH_MAX_AGE:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Эпоха устарела: прошло {age} баров > FRESH_MAX_AGE={FRESH_MAX_AGE}. Сбрасываю.")
        return None

    # Подтверждение разворота: две закрытые свечи телом выше level
    level = max(bars[sh_last_idx].open, bars[sh_last_idx].close)
    if e < 1:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Недостаточно закрытых свечей для подтверждения (e < 1). Пропускаю.")
        return None
    prev_ok = min(bars[e - 1].open, bars[e - 1].close) > level
    curr_ok = min(bars[e].open, bars[e].close) > level
    log(
        f"[{exchange.name} {symbol} {timeframe.value}] Проверка закрепления над уровнем {level:.6f}: "
        f"prev_ok={prev_ok} (min(O,C)={min(bars[e - 1].open, bars[e - 1].close):.6f}), "
        f"curr_ok={curr_ok} (min(O,C)={min(bars[e].open, bars[e].close):.6f})."
    )
    if not (prev_ok and curr_ok):
        log(f"[{exchange.name} {symbol} {timeframe.value}] Нет двух подряд закрытых свечей выше уровня. Сигнал не формируется.")
        return None

    # Подготовка экстремумов для графика
    exts: list[Extremum] = [
        Extremum(bar=bars[bar1_idx], type=ExtremumType.LOW),
        Extremum(bar=bars[sh_last_idx], type=ExtremumType.HIGH),
    ]
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
        log(f"[{exchange.name} {symbol} {timeframe.value}] График сохранён: {chart_path}")
    except Exception as e_plot:
        # даже если отрисовка не удалась — лучше всё равно отдать сигнал
        logw(f"[{exchange.name} {symbol} {timeframe.value}] Не удалось построить график ({e_plot}). Продолжаю без изображения.")

    # Сигнал (extremums: список ключевых баров — как в твоём Signal)
    extremum_bars: list[Bar] = [bars[bar1_idx], bars[sh_last_idx]]
    if last_ll_idx not in (bar1_idx, sh_last_idx):
        extremum_bars.append(bars[last_ll_idx])

    log(
        f"[{exchange.name} {symbol} {timeframe.value}] Сигнал подтверждён. "
        f"bar1={bar1_idx} (L={bars[bar1_idx].low:.6f}), "
        f"bar2/sh_last={sh_last_idx} (H={bars[sh_last_idx].high:.6f}), "
        f"last_ll={last_ll_idx}, level={level:.6f}."
    )
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
      (C[k] - L[i]) ≥ ATR_BREAKOUT_MULTIPLIER*ATR[k] и для всех t∈(i,k): L[t] ≥ L[i]
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
        s: int,             # начало окна (включительно)
        e: int,             # конец окна (включительно)
        iterations: int,    # сколько «складок» даун-тренда надо подтвердить
) -> DowntrendResult:
    """
    Ищем нисходящую структуру в окне [s..e].
    Возвращаем самую «свежую» (правее) эпоху, если в окне нашлось несколько.
    Алгоритм: d2..d5 — одна складка; повторяем, пока не наберём iterations.
    После первой найденной валидной эпохи продолжаем искать правее (перезапуск с 1 итерацией),
    чтобы вернуть максимально свежую.
    """
    window_start = s
    window_end = e

    if window_end - window_start < 3:
        return DowntrendResult(has_downtrend=False)

    # С какого индекса начинаем искать очередную «складку» (baseline)
    scan_start = window_start

    # Самая свежая найденная эпоха в этом окне (если появится)
    freshest_epoch: DowntrendResult | None = None

    # Сколько складок подтверждено подряд от текущего baseline
    folds_found = 0

    # Немного «запаса» справа, чтобы хватило места на d2..d5
    while scan_start < window_end - 2:
        # ---- d2: ищем первый допустимый перелой (LL) относительно текущего baseline ----
        initial_low = bars[scan_start].low
        ll_candidate_idx: int | None = None
        for idx in range(scan_start + 1, window_end + 1):
            if _is_ll(initial_low, bars[idx].low, atrs[idx]):
                ll_candidate_idx = idx
                break
        if ll_candidate_idx is None:
            # С этим baseline перелоя не нашлось — сдвигаем baseline вправо и пробуем снова
            scan_start += 1
            folds_found = 0  # новая попытка — складки заново
            continue

        # ---- d3: выбираем bar1 — минимальный i ≥ ll_candidate_idx с подъёмом ≥ 5×ATR без перелоя ----
        bar1_idx: int | None = None
        upmove_confirm_idx: int | None = None  # индекс k, где подъём подтвердился
        for candidate_idx in range(ll_candidate_idx, window_end + 1):
            k = _exists_upmove_without_ll(bars, atrs, candidate_idx)
            if k is not None:
                bar1_idx = candidate_idx
                upmove_confirm_idx = k
                break
        if bar1_idx is None:
            # Подъём без перелоя не подтвердился — меняем baseline и пробуем снова
            scan_start += 1
            folds_found = 0
            continue

        last_ll_idx = bar1_idx

        # ---- d4: bar3 — первая свеча, закрывшаяся ниже L[bar1] ----
        bar3_idx = _find_bar3(bars, bar1_idx)
        if bar3_idx is None or bar3_idx > window_end:
            # Нет подтверждения вниз — baseline смещаем чуть правее bar1 и продолжаем
            scan_start = bar1_idx + 1
            folds_found = 0
            continue

        # ---- d5: bar2/sh_last — максимум high на [bar1..bar3] (ранний при равенстве) ----
        sh_last_idx = _find_bar2_on_range_max_high(bars, bar1_idx, bar3_idx)

        # ---- d6: складка подтверждена ----
        folds_found += 1

        if folds_found >= iterations:
            # Набрали нужное количество складок — эпоха валидна.
            # Запомним её как «кандидата» и продолжим поиск правее,
            # чтобы (в этом же окне) вернуть максимально свежую эпоху.
            freshest_epoch = DowntrendResult(
                has_downtrend=True,
                bar1_idx=bar1_idx,
                sh_last_idx=sh_last_idx,
                last_ll_idx=last_ll_idx,
            )
            # Для дальнейшего поиска смещаем baseline на bar3 данной эпохи
            # и считаем, что уже нашли 1 складку (перезапуск внутри эпохи).
            scan_start = bar3_idx
            folds_found = 1
            continue

        # Иначе складка есть, но нужно ещё — двигаем baseline на bar3 и ищем следующую
        scan_start = bar3_idx

    # Если в окне что-то нашли — вернём самую свежую эпоху; иначе ok=False
    return freshest_epoch if freshest_epoch is not None else DowntrendResult(has_downtrend=False)

