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
        start: int,
        end: int,
        iterations: int,
) -> DowntrendResult:
    """
    Ищем нисходящую структуру d2..d5 в окне [s..e].
    Возвращаем САМУЮ СВЕЖУЮ валидную эпоху (если внутри окна их несколько).
    После фиксации валидной эпохи сдвигаем baseline на её bar3 и полностью
    ПЕРЕЗАПУСКАЕМ набор итераций (folds_found = 0), как и просили.
    """
    if end - start < 3:
        log("  [_has_downtrend] Окно слишком короткое (< 3 бара). Возврат has_downtrend=False.")
        return DowntrendResult(has_downtrend=False)

    freshest: DowntrendResult | None = None  # лучшая (самая правая) найденная эпоха
    baseline = start                         # откуда начинаем искать очередную эпоху

    log(f"  [_has_downtrend] Старт окна: [{start}..{end}], требуемых складок={iterations}")

    # оставляем минимум места для d2..d5
    while baseline < end - 2:
        folds_found = 0
        current_start = baseline
        log(f"  [_has_downtrend] Новый baseline={current_start} — начинаем попытку собрать эпоху.")

        # Пытаемся последовательно собрать до `iterations` складок
        while current_start < end - 1:
            # d2: первый кандидат на перелой (LL) относительно initial_low
            initial_low = bars[current_start].low
            j = None
            for idx in range(current_start + 1, end + 1):
                if _is_ll(initial_low, bars[idx].low, atrs[idx]):
                    j = idx
                    break
            if j is None:
                log(f"  [_has_downtrend] d2: не найден LL относительно initial_low={initial_low:.6f} в [{current_start+1}..{end}].")
                break

            log(f"  [_has_downtrend] d2: LL-кандидат j={j}, L[j]={bars[j].low:.6f}, ATR[j]={atrs[j]:.6f}.")

            # d3: bar1 — минимальный i≥j, для которого есть подъём ≥K*ATR без перелоя
            bar1_idx = None
            for cand in range(j, end + 1):
                k_idx = _exists_upmove_without_ll(bars, atrs, cand)
                if k_idx is not None:
                    bar1_idx = cand
                    log(f"  [_has_downtrend] d3: bar1=i={bar1_idx} (L={bars[bar1_idx].low:.6f}); подъём без перелоя подтверждён → k={k_idx}.")
                    break
            if bar1_idx is None:
                log(f"  [_has_downtrend] d3: подъём ≥ {ATR_BREAKOUT_MULTIPLIER}×ATR без перелоя не найден после j={j}.")
                break

            last_ll_idx = bar1_idx

            # d4: bar3 — первая свеча с закрытием ниже L[bar1]
            bar3_idx = _find_bar3(bars, bar1_idx)
            if bar3_idx is None or bar3_idx > end:
                log(f"  [_has_downtrend] d4: bar3 не найден (нет C < L[bar1]={bars[bar1_idx].low:.6f}).")
                break
            log(f"  [_has_downtrend] d4: bar3={bar3_idx} (C={bars[bar3_idx].close:.6f} < L[bar1]={bars[bar1_idx].low:.6f}).")

            # d5: bar2/sh_last — максимум high на [bar1..bar3] (ранний при равенстве)
            sh_last_idx = _find_bar2_on_range_max_high(bars, bar1_idx, bar3_idx)
            log(f"  [_has_downtrend] d5: sh_last=bar2={sh_last_idx} (H={bars[sh_last_idx].high:.6f}) на диапазоне [{bar1_idx}..{bar3_idx}].")

            # складка подтверждена
            folds_found += 1
            log(f"  [_has_downtrend] d6: складка {folds_found}/{iterations} подтверждена.")

            if folds_found >= iterations:
                # эпоха валидна — запоминаем как «самую свежую» и будем искать ещё свежее
                freshest = DowntrendResult(has_downtrend=True, bar1_idx=bar1_idx, sh_last_idx=sh_last_idx, last_ll_idx=last_ll_idx)
                log(f"  [_has_downtrend] ✔ Эпоха подтверждена: bar1={bar1_idx}, sh_last={sh_last_idx}, last_ll={last_ll_idx}. "
                    f"Пробуем найти ещё более свежую правее bar3={bar3_idx} с ПОЛНЫМ перезапуском итераций.")
                # Сдвигаем внешний baseline на bar3 данной эпохи и ВЫХОДИМ из внутреннего цикла.
                baseline = bar3_idx
                break

            # Иначе — продолжаем собирать следующую складку от bar3
            current_start = bar3_idx

        else:
            # теоретически сюда почти не попадём; страховка от бесконечного цикла
            baseline += 1
            continue

        # Если не набрали нужное число складок — эта попытка baseline неудачна, двигаем baseline на 1 вправо
        if folds_found < iterations:
            baseline += 1
            continue

        # Если эпоха валидна — baseline уже передвинут на её bar3 и цикл пойдёт дальше для «ещё более свежей»
        # (folds_found будет пересчитан заново с нуля при входе в новую попытку)
        continue

    if freshest is None:
        log(f"  [_has_downtrend] В окне [{start}..{end}] валидных эпох не найдено — ok=False.")
        return DowntrendResult(has_downtrend=False)

    log(f"  [_has_downtrend] Итог: возвращаю самую свежую эпоху: "
        f"bar1={freshest.bar1_idx}, sh_last={freshest.sh_last_idx}, last_ll={freshest.last_ll_idx}.")
    return freshest


