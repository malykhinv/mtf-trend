# domain/detection.py
from __future__ import annotations

from statistics import median
from typing import Optional, Sequence
import os

from config.constants import (
    ATR_PERIOD,
    ATR_BREAKOUT_MULTIPLIER,
    OUTPUT_PLOT_PATH, FRESH_MAX_AGE, TREND_ITERATIONS, WINDOW_TAIL, MIN_PULLBACK_BARS, HIGH_SHIFT,
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
        *,
        test_mode: bool = False,  # в тесте рисуем график всегда
) -> Optional[Signal]:
    """
    Главная функция детекции разворота даун-тренда в лонг.
    Возвращает Signal только на закрытии бара i=N-1 при выполнении всех условий.
    В тестовом режиме (test_mode=True) сохраняет график в любом случае.
    """
    # Внешний переключатель через ENV (удобно для запуска без правки кода)
    bars = _cut_bars_from_high(bars)
    force_plot_env = os.getenv("DETECT_FORCE_PLOT", "").strip().lower() in ("1", "true", "yes", "y")
    force_plot = test_mode or force_plot_env

    n = len(bars)
    log(f"[{exchange.name} {symbol} {timeframe.value}] Старт детекции. Баров={n}")
    min_needed = max(ATR_PERIOD + 20, 200)
    if n < min_needed:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Недостаточно баров для анализа (нужно ≥ {min_needed}). Пропускаю.")
        # В тестовом режиме всё равно сохраним «чистый» график без меток
        if force_plot:
            _save_chart(exchange, symbol, timeframe, plotter, bars, exts=[])
        return None

    atrs = atr(bars, ATR_PERIOD)
    log(f"[{exchange.name} {symbol} {timeframe.value}] ATR рассчитан (period={ATR_PERIOD}).")

    # базовый хвост по времени (как в спецификации)
    e = n - 1
    base_s = max(0, n - WINDOW_TAIL)

    # якорь = локальный максимум внутри базового хвоста
    max_idx = max(range(base_s, e + 1), key=lambda i: bars[i].high)

    # динамическая длина хвоста: от якоря до правого края, но с минимальной длиной
    min_tail = max(ATR_PERIOD + 6, 20)  # безопасный минимум под ATR и d2..d5
    tail_len = max(min_tail, e - max_idx + 1)
    tail_len = min(tail_len, e - base_s + 1)  # не выходить левее базового хвоста

    s = e - tail_len + 1
    log(f"[{exchange.name} {symbol} {timeframe.value}] Окно анализа: s={s}, e={e}, длина={e - s + 1}, "
        f"WINDOW_TAIL={WINDOW_TAIL}→{tail_len} (якорь по high: {max_idx}, base_s={base_s}).")

    # Строим структуру на хвосте
    r = _has_downtrend(bars, atrs, s, e, TREND_ITERATIONS)
    if not r.has_downtrend:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Нисходящая структура в окне не найдена. Сигнал не формируется.")
        if force_plot:
            _save_chart(exchange, symbol, timeframe, plotter, bars, exts=[])
        return None

    bar1_idx, sh_last_idx, last_ll_idx = r.bar1_idx, r.sh_last_idx, r.last_ll_idx

    # Подготовка экстремумов для графика: все складки эпохи, если они есть
    if r.folds:
        exts = _compress_significant_exts(r.folds, bars)
    else:
        exts = [
            Extremum(bar=bars[bar1_idx], type=ExtremumType.LOW),
            Extremum(bar=bars[sh_last_idx], type=ExtremumType.HIGH),
        ]
        if last_ll_idx != bar1_idx:
            exts.append(Extremum(bar=bars[last_ll_idx], type=ExtremumType.LOW))

    # Подтверждение разворота: две закрытые свечи телом выше level
    level = max(bars[sh_last_idx].open, bars[sh_last_idx].close)
    if e < 1:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Недостаточно закрытых свечей для подтверждения (e < 1). Пропускаю.")
        if force_plot:
            _save_chart(exchange, symbol, timeframe, plotter, bars, exts=exts)
        return None

    # найти первый бар с закрытием выше level
    t_break = None
    for idx in range(sh_last_idx + 1, e + 1):
        if bars[idx].close > level:
            t_break = idx
            break

    if not t_break:
        for idx in range(sh_last_idx + 1, e + 1):
            if bars[idx].high > level:
                t_break = idx
                break

    # пересчёт возраста: до пересечения считаем от last_ll, после — от первого пересечения
    age_anchor = t_break if t_break is not None else last_ll_idx
    age = e - age_anchor
    if age > FRESH_MAX_AGE:
        log(f"[{exchange.name} {symbol} {timeframe.value}] Эпоха устарела после "
            f"{'пересечения' if t_break is not None else 'LL'}: прошло {age} баров > {FRESH_MAX_AGE}.")
        if force_plot:
            _save_chart(exchange, symbol, timeframe, plotter, bars, exts=exts)
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
        if force_plot:
            _save_chart(exchange, symbol, timeframe, plotter, bars, exts=exts)
        return None

    # Новый значимый экстремум: лой между sh_last и первым пересечением уровня sh_last (t_break)
    pivot_low_idx = None
    if t_break is not None and t_break > sh_last_idx:
        left = sh_last_idx + 1
        right = min(t_break, e)  # страховка
        if left <= right:
            pivot_low_idx = _find_on_range_min_low(bars, left, right)
            exts.append(Extremum(bar=bars[pivot_low_idx], type=ExtremumType.LOW))
            log(f"[{exchange.name} {symbol} {timeframe.value}] Добавлен pivot-low между sh_last={sh_last_idx} "
                f"и t_break={t_break}: idx={pivot_low_idx}, L={bars[pivot_low_idx].low:.6f}.")

    # Если дошли сюда — сигнал подтверждён. Рисуем и возвращаем Signal.
    chart_path = _save_chart(exchange, symbol, timeframe, plotter, bars, exts=exts)

    log(
        f"[{exchange.name} {symbol} {timeframe.value}] Сигнал подтверждён. "
        f"bar1={bar1_idx} (L={bars[bar1_idx].low:.6f}), "
        f"bar2/sh_last={sh_last_idx} (H={bars[sh_last_idx].high:.6f}), "
        f"last_ll={last_ll_idx}, level={level:.6f}."
    )
    # Добавляем bar3 среди значимых экстремумов
    bar3_idx = _find_bar3(bars, bar1_idx)
    if bar3_idx is not None and bar3_idx > e:
        bar3_idx = None  # страховка от выхода за правый край окна

    extremum_bars: list[Bar] = [bars[bar1_idx], bars[sh_last_idx]]
    if bar3_idx is not None:
        extremum_bars.append(bars[bar3_idx])
    if last_ll_idx not in (bar1_idx, sh_last_idx, (bar3_idx if bar3_idx is not None else -1)):
        extremum_bars.append(bars[last_ll_idx])
    if pivot_low_idx and pivot_low_idx not in (
            bar1_idx, sh_last_idx, last_ll_idx, (bar3_idx if bar3_idx is not None else -1)
    ):
        extremum_bars.append(bars[pivot_low_idx])

    return Signal(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        extremums=extremum_bars,
        chart_path=chart_path,
    )

def _cut_bars_from_high(bars: list[Bar]) -> list[Bar]:
    if not bars:
        return []

    # Находим индекс бара с максимальным high (при равенстве берём первый)
    max_idx = max(range(len(bars)), key=lambda i: bars[i].high)
    # Левая граница со сдвигом влево, но не меньше нуля
    start_idx = max(0, max_idx - HIGH_SHIFT)

    return bars[start_idx:]


def _save_chart(exchange, symbol, timeframe, plotter, bars, exts):
    os.makedirs(OUTPUT_PLOT_PATH, exist_ok=True)
    ts = bars[-1].time.strftime("%Y%m%d_%H%M%S") if bars else "na"
    chart_path = os.path.join(
        OUTPUT_PLOT_PATH,
        f"{exchange.name.lower()}__{symbol.replace('/', '-')}__{timeframe.value}__{ts}.png",
    )
    try:
        plotter.plot(bars, exts, path=chart_path, symbol=symbol, timeframe=timeframe)
        log(f"[{exchange.name} {symbol} {timeframe.value}] График сохранён: {chart_path}")
    except Exception as e_plot:
        logw(f"[{exchange.name} {symbol} {timeframe.value}] Не удалось построить график ({e_plot}). Продолжаю без изображения.")
    return chart_path



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
    Ищем нисходящую структуру d2..d5 в окне [start..end].
    Возвращаем САМУЮ СВЕЖУЮ валидную эпоху в правой части окна.
    После фиксации валидной эпохи сдвигаем baseline на её bar3
    и ПОЛНОСТЬЮ ПЕРЕЗАПУСКАЕМ набор итераций (folds_found = 0).
    """
    if end - start < 3:
        log("  [_has_downtrend] Окно слишком короткое (< 3 бара). Возврат has_downtrend=False.")
        return DowntrendResult(has_downtrend=False)

    freshest: DowntrendResult | None = None
    baseline = start
    log(f"  [_has_downtrend] Старт окна: [{start}..{end}], требуемых складок={iterations}")

    # Оставляем минимум места для d2..d5
    while baseline < end - 2:
        folds_found = 0
        current_start = baseline
        current_folds: list[tuple[int, int, int, int]] = []  # (bar1, sh_last, bar3, last_ll)
        best_after_threshold: DowntrendResult | None = None
        log(f"  [_has_downtrend] Новый baseline={current_start} — начинаем попытку собрать эпоху.")

        # Пытаемся последовательно собрать до `iterations` складок
        last_bar3_attempt = None
        while current_start < end - 1:
            # d2: поиск LL-кандидата j относительно initial_low = L[current_start]
            initial_low = bars[current_start].low
            j = None
            for idx in range(current_start + 1, end + 1):
                window_start = idx-ATR_PERIOD if idx > ATR_PERIOD else 0
                if _is_ll(initial_low, bars[idx].low, median(atrs[window_start: idx+1])):
                    j = idx
                    break
            if j is None:
                log(f"  [_has_downtrend] d2: не найден LL относительно initial_low={initial_low:.6f} "
                    f"в [{current_start + 1}..{end}].")
                break

            log(f"  [_has_downtrend] d2: LL-кандидат j={j}, L[j]={bars[j].low:.6f}, ATR[j]={atrs[j]:.6f}.")

            # d3: bar1 — минимальный i ≥ j с ап-движением ≥K*ATR без перелоя
            bar1_idx = None
            for cand in range(j, end + 1):
                k_try = _exists_upmove_without_ll(bars, atrs, cand)
                if k_try is not None:
                    bar1_idx = cand
                    k_idx = k_try
                    log(f"  [_has_downtrend] d3: bar1=i={bar1_idx} (L={bars[bar1_idx].low:.6f}); "
                        f"подъём без перелоя подтверждён → k={k_idx}.")
                    break
            if bar1_idx is None:
                log(f"  [_has_downtrend] d3: подъём ≥ {ATR_BREAKOUT_MULTIPLIER}×ATR без перелоя "
                    f"не найден после j={j}.")
                break

            last_ll_idx = bar1_idx

            # d4: bar3 — первая свеча с закрытием ниже L[bar1]
            bar3_idx = _find_bar3(bars, bar1_idx)
            if bar3_idx is None or bar3_idx > end:
                log(f"  [_has_downtrend] d4: bar3 не найден (нет C < L[bar1]={bars[bar1_idx].low:.6f}).")
                break
            if bar3_idx - bar1_idx < MIN_PULLBACK_BARS:
                current_start = bar3_idx
                continue
            log(f"  [_has_downtrend] d4: bar3={bar3_idx} (C={bars[bar3_idx].close:.6f} < "
                f"L[bar1]={bars[bar1_idx].low:.6f}).")

            # d5: bar2/sh_last — максимум high на [bar1..bar3] (при равенстве — ранний индекс)
            sh_last_idx = _find_bar2_on_range_max_high(bars, bar1_idx + 1, bar3_idx - 1)
            log(f"  [_has_downtrend] d5: sh_last=bar2={sh_last_idx} (H={bars[sh_last_idx].high:.6f}) "
                f"на диапазоне [{bar1_idx + 1}..{bar3_idx - 1}].")

            # складка подтверждена
            folds_found += 1
            last_bar3_attempt = bar3_idx
            current_folds.append((bar1_idx, sh_last_idx, bar3_idx, last_ll_idx))

            # === УТОЧНЕНИЕ ПРЕДЫДУЩИХ ЭКСТРЕМУМОВ ПОСЛЕ НОВОГО l1 ===
            if len(current_folds) >= 2:
                l1, _, _, _ = current_folds[-1]
                l0, h0, b3prev, llprev = current_folds[-2]

                # 1) новый, более высокий хай на [h0..l1]?
                left, right = min(h0, l1), max(h0, l1)
                h0_new = _find_bar2_on_range_max_high(bars, left, right)
                moved_h0 = bars[h0_new].high > bars[h0].high
                if moved_h0:
                    h0 = h0_new
                    # 2) обновляем лой на [l0..h0]
                    l0 = _find_on_range_min_low(bars, min(l0, h0), max(l0, h0))
                    # 3) ВАЖНО: вместе с b1 «переезжает» и last_ll
                    current_folds[-2] = (l0, h0, b3prev, l0)  # ll = l0
            # === конец уточнения ===

            # === УТОЧНЕНИЕ ПРЕДЫДУЩЕЙ СКЛАДКИ ПРИ ПОГЛОЩЕНИИ (новые l и h расширяют старые) ===
            if len(current_folds) >= 2:
                l1, _, _, _ = current_folds[-1]
                l0, h0, b3prev, _ = current_folds[-2]

                left, right = (l0, l1) if l0 <= l1 else (l1, l0)
                h_new = _find_bar2_on_range_max_high(bars, left, right)
                l_new = _find_on_range_min_low(bars, left, right)

                # Поглощение: новый хай выше старого И новый лой ниже старого
                if bars[h_new].high > bars[h0].high and bars[l_new].low < bars[l0].low:
                    # Расширяем предыдущую складку: b1= l_new, sh_last= h_new, ll = l_new
                    current_folds[-2] = (l_new, h_new, b3prev, l_new)
            # === конец уточнения ===

            log(f"  [_has_downtrend] d6: складка {folds_found}/{iterations} подтверждена "
                f"(bar3={bar3_idx}, time={bars[bar3_idx].time.isoformat()}).")

            if folds_found >= iterations:
                # достигнут порог — обновляем «лучшего» кандидата,
                # но НЕ прерываем цикл: продолжаем собирать складки правее
                best_after_threshold = DowntrendResult(
                    has_downtrend=True,
                    bar1_idx=bar1_idx,
                    sh_last_idx=sh_last_idx,
                    last_ll_idx=last_ll_idx,
                    folds=current_folds.copy(),
                )
                log(f"  [_has_downtrend] ✔ Порог достигнут: обновлён последний значимый хай (sh_last={sh_last_idx})."
                    f" Продолжаю искать новые складки правее bar3={bar3_idx}.")

            # Продолжаем собирать следующую складку от bar3
            current_start = bar3_idx

        else:
            # Страховка от зависаний: если внутренний цикл не break'нулся, двигаем baseline на 1
            baseline += 1
            continue

        # Если дальше складок нет: отдаем лучшую эпоху после достижения порога
        if best_after_threshold is not None:
            freshest = best_after_threshold
            log("  [_has_downtrend] ▶ Складки закончились после достижения порога — беру последний sh_last текущей эпохи и завершаю поиск.")
            break  # выходим из внешнего while: эпоха полностью расширена вправо

        # Иначе (порог не набран) — baseline++ и новая попытка
        if folds_found < iterations:
            if folds_found > 0 and last_bar3_attempt is not None:
                log(f"  [_has_downtrend] ⏭ ускоренный сдвиг baseline на bar3={last_bar3_attempt} "
                    f"т.к. собрали только {folds_found}/{iterations}.")
                baseline = max(baseline + 1, last_bar3_attempt)
            else:
                baseline += 1
            continue

    if freshest is None:
        log(f"  [_has_downtrend] В окне [{start}..{end}] валидных эпох не найдено — ok=False.")
        return DowntrendResult(has_downtrend=False)

    log(f"  [_has_downtrend] Итог: возвращаю самую свежую эпоху: "
        f"bar1={freshest.bar1_idx}, sh_last={freshest.sh_last_idx}, last_ll={freshest.last_ll_idx}.")
    return freshest


def _find_on_range_min_low(bars: list[Bar], left: int, right: int) -> int:
    m = left
    min_l = bars[left].low
    for idx in range(left + 1, right + 1):
        l = bars[idx].low
        if l < min_l:
            min_l = l
            m = idx
    return m

def _compress_significant_exts(
    folds: list[tuple[int, int, int, int]],
    bars: list[Bar],
) -> list[Extremum]:
    """
    Оставляет только «несжатые» пары l/h: если новая пара (l,h) поглощает предыдущую,
    то заменяем предыдущую на (l,h), не добавляя новый элемент.
    """
    pairs: list[tuple[int, int]] = []
    for (l, h, _b3, _ll) in folds:
        if not pairs:
            pairs.append((l, h))
            continue
        prev_l, prev_h = pairs[-1]
        if bars[l].low < bars[prev_l].low and bars[h].high > bars[prev_h].high:
            pairs[-1] = (l, h)  # расширяем предыдущую
        else:
            pairs.append((l, h))

    exts: list[Extremum] = []
    for l, h in pairs:
        exts.append(Extremum(bar=bars[l], type=ExtremumType.LOW))
        exts.append(Extremum(bar=bars[h], type=ExtremumType.HIGH))
    return exts
