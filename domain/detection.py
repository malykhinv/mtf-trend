# domain/detection.py
from __future__ import annotations

from typing import Optional
import os

from config.constants import (
    ATR_PERIOD,
    ATR_BREAKOUT_MULTIPLIER,
    OUTPUT_PLOT_PATH, FRESH_MAX_AGE, TREND_ITERATIONS, WINDOW_TAIL, MIN_PULLBACK_BARS, MIN_SWING_WIDTH_BARS,
    SWING_FILTER_AMP_ATR_MULTIPLIER,
)
from domain.models.DowntrendResult import DowntrendResult
from domain.models.Bar import Bar
from domain.models.Exchange import Exchange
from domain.models.Timeframe import Timeframe
from domain.models.Signal import Signal
from domain.models.Extremum import Extremum
from domain.models.ExtremumType import ExtremumType
from services.Plotter import Plotter
from utils.logger import log, logw
from utils.postmortem import log_postmortem_from_confirm
from utils.safe_name import safe_name

def detect(
        symbol: str,
        exchange: Exchange,
        bars: list[Bar],
        timeframe: Timeframe,
        plotter: Plotter,
        *,
        test_mode: bool = False,
) -> Optional[Signal]:
    def log_symbol(text: str):
        log(f"{symbol.split('/', 1)[0]:<12}{exchange.name.capitalize():<12}{timeframe.value:<6}{text}")

    """
    Главная функция детекции разворота даун-тренда в лонг.
    Возвращает Signal только на закрытии бара i=N-1 при выполнении всех условий.
    В тестовом режиме (test_mode=True) сохраняет график в любом случае.
    """
    # Внешний переключатель через ENV (удобно для запуска без правки кода)
    force_plot_env = os.getenv("DETECT_FORCE_PLOT", "").strip().lower() in ("1", "true", "yes", "y")
    force_plot = test_mode or force_plot_env

    n = len(bars)
    min_needed = max(ATR_PERIOD + 20, 200)
    if n < min_needed:
        log_symbol(f"Недостаточно баров для анализа (нужно ≥ {min_needed}).")
        if force_plot:
            _save_chart(exchange, symbol, timeframe, plotter, bars, exts=[])
        return None

    # базовый хвост по времени (как в спецификации)
    e = n - 1
    base_s = max(0, n - WINDOW_TAIL)

    # якорь = локальный максимум внутри базового хвоста
    max_idx = max(range(base_s, e + 1), key=lambda index: bars[index].high)

    # динамическая длина хвоста: от якоря до правого края, но с минимальной длиной
    min_tail = max(ATR_PERIOD + 6, 20)  # безопасный минимум под ATR и d2..d5
    tail_len = max(min_tail, e - max_idx + 1)
    tail_len = min(tail_len, e - base_s + 1)  # не выходить левее базового хвоста

    s = e - tail_len + 1

    # Строим структуру на хвосте
    atr_med = _rolling_median_atr(bars, ATR_PERIOD)
    r = _has_downtrend(bars, s, e, TREND_ITERATIONS, atr_med)
    if not r.has_downtrend:
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
        log_symbol(f"Недостаточно закрытых свечей для подтверждения (e < 1).")
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
    # --- Постмортем-подготовка: bars_after_confirm и sl ---
    # Уровень закрепления (как в спецификации) — телом выше level = max(open, close) на sh_last
    # 1) Находим индекс подтверждающей свечи: первая пара подряд, где обе свечи закрыты телом выше level.
    confirm_idx = None
    for i in range(sh_last_idx + 2, e + 1):
        if min(bars[i - 1].open, bars[i - 1].close) > level and min(bars[i].open, bars[i].close) > level:
            confirm_idx = i
            break

    # 2) bars_after_confirm — начиная с подтверждающей свечи (если нашли)
    bars_after_confirm = bars[confirm_idx:] if confirm_idx is not None else []

    # 3) SL — лой на участке (sh_last..t_break], если t_break найден; иначе — (sh_last..confirm_idx], если подтверждение нашли
    sl = None
    _left = sh_last_idx + 1
    _right = None
    if t_break is not None and t_break > sh_last_idx:
        _right = min(t_break, e)
    elif confirm_idx is not None:
        _right = confirm_idx
    if _right is not None and _left <= _right:
        sl_idx = _find_on_range_min_low(bars, _left, _right)
        sl = bars[sl_idx].low


    # пересчёт возраста: до пересечения считаем от last_ll, после — от первого пересечения
    age_anchor = t_break if t_break is not None else last_ll_idx
    age = e - age_anchor
    if age > FRESH_MAX_AGE:
        # Вызов постмортема только если есть подтверждение и валидный SL
        if confirm_idx is not None and sl is not None and bars_after_confirm:
            try:
                log_postmortem_from_confirm(symbol, exchange, timeframe, bars_after_confirm, sl)
            except Exception as _exc:
                log_symbol(f"Постмортем не выполнен: {_exc}")
        if force_plot:
            _save_chart(exchange, symbol, timeframe, plotter, bars, exts=exts)
        return None

    prev_ok = min(bars[e - 1].open, bars[e - 1].close) > level
    curr_ok = min(bars[e].open, bars[e].close) > level
    if not (prev_ok and curr_ok):
        log_symbol(f"Нет двух подряд закрытых свечей выше уровня.")
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

    # Если дошли сюда — сигнал подтверждён. Рисуем и возвращаем Signal.
    chart_path = _save_chart(exchange, symbol, timeframe, plotter, bars, exts=exts)

    log_symbol(
        f"Сигнал подтверждён. "
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
    if pivot_low_idx is not None and pivot_low_idx not in (
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


def _save_chart(exchange, symbol, timeframe, plotter, bars, exts):
    os.makedirs(OUTPUT_PLOT_PATH, exist_ok=True)
    ts = bars[-1].time.strftime("%Y%m%d_%H%M%S") if bars else "na"
    chart_path = os.path.join(
        OUTPUT_PLOT_PATH,
        f"{exchange.name.lower()}_{safe_name(symbol)}_{timeframe.value}_{ts}.png",
    )
    try:
        plotter.plot(bars, exts, path=chart_path, symbol=symbol, timeframe=timeframe)
        log(f"График сохранён: {chart_path}")
    except Exception as e_plot:
        logw(
            f"Не удалось построить график ({e_plot}).")
    return chart_path


def _is_ll(ref_low: float, low_j: float, atr_j: float) -> bool:
    # «Ниже на ≥ ATR_BREAKOUT_MULTIPLIER×ATR»
    return (ref_low - low_j) >= ATR_BREAKOUT_MULTIPLIER * atr_j


def _exists_upmove_without_ll(bars: list[Bar], i: int) -> Optional[int]:
    """
    Для кандидата bar1=i: существует минимальный k>i:
      (C[k] - L[i]) ≥ ATR_BREAKOUT_MULTIPLIER*ATR[k] и для всех t∈(i,k): L[t] ≥ L[i]
    Вернёт k (минимальный), если условие выполнено, иначе None.
    """
    base_low = bars[i].low
    n = len(bars)
    if i + 2 >= n:
        return None
    # минимум low на (i, k) — поддерживаем на лету
    running_min = bars[i + 1].low
    for k in range(i + 2, n):
        # сначала проверка условия подъёма без перелоя
        if running_min >= base_low and (bars[k].close - base_low) >= ATR_BREAKOUT_MULTIPLIER * bars[k].atr:
            return k
        # затем расширяем окно и обновляем минимум
        if bars[k].low < running_min:
            running_min = bars[k].low
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
        start: int,
        end: int,
        iterations: int,
        atr_med: list[float]
) -> DowntrendResult:
    """
    Ищем нисходящую структуру d2..d5 в окне [start..end].
    Возвращаем САМУЮ СВЕЖУЮ валидную эпоху в правой части окна.
    После фиксации валидной эпохи сдвигаем baseline на её bar3
    и ПОЛНОСТЬЮ ПЕРЕЗАПУСКАЕМ набор итераций (folds_found = 0).
    """
    if end - start < 3:
        log("Окно слишком короткое (< 3 бара).")
        return DowntrendResult(has_downtrend=False)

    freshest: DowntrendResult | None = None
    baseline = start

    # Оставляем минимум места для d2..d5
    while baseline < end - 2:
        folds_found = 0
        current_start = baseline
        current_folds: list[tuple[int, int, int, int]] = []  # (bar1, sh_last, bar3, last_ll)
        best_after_threshold: DowntrendResult | None = None

        # Пытаемся последовательно собрать до `iterations` складок
        last_bar3_attempt = None
        ll_search_from = current_start + 1  # будем сдвигать при отклонённых парах (j)
        while current_start < end - 1:
            # d2: поиск LL-кандидата j относительно initial_low = L[current_start]
            initial_low = bars[current_start].low
            j = None
            start_j = max(current_start + 1, ll_search_from)  # не искать левее текущего старта
            for idx in range(start_j, end + 1):
                if _is_ll(initial_low, bars[idx].low, atr_med[idx]):
                    j = idx
                    break
            if j is None:
                break

            # d3: bar1 — минимальный i ≥ j с ап-движением ≥K*ATR без перелоя
            bar1_idx = None
            for cand in range(j, end + 1):
                k_try = _exists_upmove_without_ll(bars, cand)
                if k_try is not None:
                    bar1_idx = cand
                    break
            if bar1_idx is None:
                break

            last_ll_idx = bar1_idx

            # d4: bar3 — первая свеча с закрытием ниже L[bar1]
            bar3_idx = _find_bar3(bars, bar1_idx)
            if bar3_idx is None or bar3_idx > end:
                break
            if bar3_idx - bar1_idx < MIN_PULLBACK_BARS:
                current_start = bar3_idx
                ll_search_from = current_start + 1  # сбрасываем позицию поиска нового j
                continue

            # === УТОЧНЕНИЕ: l1 -> минимум на [b3(prev)+1 .. l1], затем пересчёт h1 ===
            if current_folds:
                _l0_prev, _h0_prev, b3prev, _llprev = current_folds[-1]
                if b3prev < bar1_idx:
                    left, right = b3prev + 1, bar1_idx
                    if left <= right:
                        l1_new = _find_on_range_min_low(bars, left, right)
                        if l1_new != bar1_idx:
                            bar1_idx = l1_new
                        last_ll_idx = bar1_idx

            # d5: bar2/sh_last — максимум high на [bar1..bar3] (при равенстве — ранний индекс)
            sh_last_idx = _find_bar2_on_range_max_high(bars, bar1_idx + 1, bar3_idx - 1)

            # --- ФИЛЬТР ЗНАЧИМОСТИ ПАРЫ (l/h) ДО подтверждения складки ---
            from statistics import median
            left = bar1_idx
            right = sh_last_idx
            swing_amp = bars[sh_last_idx].high - bars[bar1_idx].low  # High-Low
            swing_len = sh_last_idx - bar1_idx                       # число баров между l и h
            atr_loc = median(atr_med[left:right + 1]) if left <= right else atr_med[bar1_idx]

            if swing_len < MIN_SWING_WIDTH_BARS or swing_amp < SWING_FILTER_AMP_ATR_MULTIPLIER * atr_loc:
                # Пара слабая: не подтверждаем складку.
                # Ищем следующий LL относительно того же initial_low, пропуская текущий j.
                ll_search_from = j + 1
                continue
            # --- конец фильтра ---

            # === УТОЧНЕНИЕ ПРЕДЫДУЩЕЙ СКЛАДКИ ПРИ ПОГЛОЩЕНИИ (выполняем ДО подтверждения новой) ===
            # Используем кандидата (l1=bar1_idx, h1=sh_last_idx) против предыдущей складки.
            if len(current_folds) >= 1:
                l0, h0, b3prev, _ = current_folds[-1]
                l1 = bar1_idx
                left, right = (l0, l1) if l0 <= l1 else (l1, l0)
                h_new = _find_bar2_on_range_max_high(bars, left, right)
                l_new = _find_on_range_min_low(bars, left, right)
                # Поглощение: новый хай выше старого И новый лой ниже старого — расширяем предыдущую складку,
                # новую не добавляем.
                if bars[h_new].high > bars[h0].high and bars[l_new].low < bars[l0].low:
                    current_folds[-1] = (l_new, h_new, b3prev, l_new)  # ll = l_new
                    # Перезапускаем поиск правее текущего bar3
                    current_start = bar3_idx
                    ll_search_from = current_start + 1
                    continue
            # === конец уточнения (поглощение) ===

            # складка подтверждена
            folds_found += 1
            last_bar3_attempt = bar3_idx
            current_folds.append((bar1_idx, sh_last_idx, bar3_idx, last_ll_idx))

            # --- порог по СЖАТЫМ парам (как на графике) ---
            # строим сжатую последовательность l/h из current_folds
            pairs: list[tuple[int, int]] = []
            for (l, h, _b3, _ll) in current_folds:
                if not pairs:
                    pairs.append((l, h))
                else:
                    prev_l, prev_h = pairs[-1]
                    if bars[l].low < bars[prev_l].low and bars[h].high > bars[prev_h].high:
                        pairs[-1] = (l, h)
                    else:
                        pairs.append((l, h))
            compressed_count = len(pairs)
            # ------------------------------------------------

            if compressed_count >= iterations:
                best_after_threshold = DowntrendResult(
                    has_downtrend=True,
                    bar1_idx=bar1_idx,
                    sh_last_idx=sh_last_idx,
                    last_ll_idx=last_ll_idx,
                    folds=current_folds.copy(),
                )

            # Продолжаем собирать следующую складку от bar3
            current_start = bar3_idx
            ll_search_from = current_start + 1  # сброс для нового цикла поиска j

        else:
            # Страховка от зависаний: если внутренний цикл не break'нулся, двигаем baseline на 1
            baseline += 1
            continue

        # Если дальше складок нет: отдаем лучшую эпоху после достижения порога
        if best_after_threshold is not None:
            freshest = best_after_threshold
            break  # выходим из внешнего while: эпоха полностью расширена вправо

        # если порог не набран в этом проходе — проверяем его по сжатым парам
        pairs: list[tuple[int, int]] = []
        for (l, h, _b3, _ll) in current_folds:
            if not pairs:
                pairs.append((l, h))
            else:
                prev_l, prev_h = pairs[-1]
                if bars[l].low < bars[prev_l].low and bars[h].high > bars[prev_h].high:
                    pairs[-1] = (l, h)
                else:
                    pairs.append((l, h))
        compressed_count_end = len(pairs)

        if compressed_count_end < iterations:
            if folds_found > 0 and last_bar3_attempt is not None:
                baseline = max(baseline + 1, last_bar3_attempt)
            else:
                baseline += 1
            continue

    if freshest is None:
        return DowntrendResult(has_downtrend=False)

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


def _rolling_median_atr(bars: list[Bar], period: int) -> list[float]:
    from statistics import median
    n = len(bars)
    out = [0.0] * n
    for i in range(n):
        left = 0 if i <= period else i - period
        out[i] = median(b.atr for b in bars[left:i + 1])
    return out
