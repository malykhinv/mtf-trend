#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Backtest Pump (STRONG) — постмортем по всем setup-ТФ, автозапуск без параметров.
Правила:
- вход по открытию следующего бара после STRONG
- стоп: signal.sl, иначе минимум последних N correction-like баров
- считаем peak RR до стопа или до лимита удержания
- после разрешения позиции перематываем индекс (вариант b)
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import csv
import math
from bisect import bisect_right

from config.constants import FLOAT_UNDEFINED  # типовые константы
from data.loader import Loader
from domain.detection.pump.pump_setup import PumpSetup
from domain.models.timeframe import Timeframe
from domain.models.mtf_profile import MTFProfile
from utils.logger import log, logw

# --- параметры по умолчанию
DEFAULT_PROFILES: list[MTFProfile] = [
    MTFProfile(Timeframe.D1, Timeframe.M30, Timeframe.M1),
    MTFProfile(Timeframe.D1, Timeframe.H1, Timeframe.M3),
    MTFProfile(Timeframe.D1, Timeframe.H1, Timeframe.M5),
]
SETUP_BARS = 1500
CONTEXT_BARS = 120
MACRO_BARS = 60
WARMUP_BARS = 200
CORR_WINDOW = 8
MAX_HOLD_MIN = 240
COOLDOWN_MIN = 15


# --- Utils
def _slice_to_ts(bars, ts):
    """Вернуть prefix-блок баров с timestamp <= ts (быстро через bisect)."""
    # Предполагаем сортировку по времени по возрастанию
    # Чтобы не выделять список на каждом вызове, можно кэшировать массив timestamps при загрузке
    idx = bisect_right([b.timestamp for b in bars], ts)
    return bars[:idx]

def _is_defined(x: float) -> bool:
    return (x is not None) and (not math.isnan(x)) and (x != FLOAT_UNDEFINED)

def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def _rr(entry: float, sl: float, px: float) -> float:
    R = max(entry - sl, 1e-12)
    return max(0.0, (px - entry) / R)


# --- Core
def backtest_symbol(
    loader: 'Loader',
    symbol: str,
    profiles,
    writer,
    setup_bars: int = SETUP_BARS,
    context_bars: int = CONTEXT_BARS,
    macro_bars: int = MACRO_BARS,
    warmup_bars: int = WARMUP_BARS,
    corr_window: int = CORR_WINDOW,
    max_hold_min: int = MAX_HOLD_MIN,
    cooldown_min: int = COOLDOWN_MIN,
):
    log(f"Анализ {symbol}")
    for tfs in profiles:
        # загрузка рядов
        bars_setup = loader.fetch_ohlcvi(symbol, tfs.setup, limit=setup_bars, has_oi=True)
        if not bars_setup or len(bars_setup) < warmup_bars + 2:
            continue
        bars_context = loader.fetch_ohlcvi(symbol, tfs.context, limit=context_bars, has_oi=True)
        bars_macro = loader.fetch_ohlcvi(symbol, tfs.macro, limit=macro_bars, has_oi=True)

        # двигаемся слева-направо по setup
        i = warmup_bars
        while i < len(bars_setup) - 1:
            ts_i = bars_setup[i].timestamp

            # срезы по времени для всех ТФ (<= ts_i)
            bars_by_tf = {
                tfs.setup: bars_setup[: i + 1],
                tfs.context: _slice_to_ts(bars_context, ts_i),
                tfs.macro: _slice_to_ts(bars_macro, ts_i),
            }
            if not bars_by_tf[tfs.context] or not bars_by_tf[tfs.macro]:
                i += 1
                continue

            setup = PumpSetup(symbol, bars_by_tf, tfs)
            signal = setup.validated_or_none()

            if signal is None:
                i += 10
                continue

            if not signal.confidence.is_strong:
                i += 1
                continue

            # вход на следующем баре
            entry_bar = bars_setup[i + 1]
            entry = entry_bar.open

            # SL: из сигнала, иначе минимум последних corr_window баров до i
            if _is_defined(signal.sl):
                sl = float(signal.sl)
            else:
                left = max(0, i - corr_window + 1)
                sl = min(b.low for b in bars_setup[left : i + 1])

            if entry <= sl:  # невалидный риск
                i += 1
                continue

            # TP по сигналу (для справки)
            tp = float(signal.tp) if _is_defined(signal.tp) else FLOAT_UNDEFINED

            # постмортем
            rr_peak = 0.0
            rr_at_tp = FLOAT_UNDEFINED
            peak_px = entry
            j = i + 1

            # лимит по времени
            max_hold_bars = max(1, int(max_hold_min / tfs.setup.minutes))
            end_j = min(len(bars_setup) - 1, i + max_hold_bars)

            stopped = False
            tp_hit = False
            bars_to_peak = 0

            while j <= end_j:
                b = bars_setup[j]
                # стоп
                if b.low <= sl:
                    # учесть high текущего бара до выбивания
                    if b.high > peak_px:
                        peak_px = b.high
                        rr_peak = _rr(entry, sl, peak_px)
                        bars_to_peak = j - (i + 1)
                    stopped = True
                    break

                # обновить пик
                if b.high > peak_px:
                    peak_px = b.high
                    rr_peak = _rr(entry, sl, peak_px)
                    bars_to_peak = j - (i + 1)

                # TP (если задан и выше входа)
                if _is_defined(tp) and (tp > entry) and (b.high >= tp) and not tp_hit:
                    rr_at_tp = _rr(entry, sl, tp)
                    tp_hit = True

                j += 1

            # поля Bar гарантированы моделью (tbq/atr/oi заданы инициализаторами)
            tbq_val = entry_bar.tbq if _is_defined(entry_bar.tbq) else ""
            atr_val = entry_bar.atr if _is_defined(entry_bar.atr) else ""
            oi_val  = entry_bar.oi if _is_defined(entry_bar.oi) else ""

            writer.writerow([
                symbol,
                f"{tfs.macro.value}/{tfs.context.value}->{tfs.setup.value}",
                ts_i.isoformat(),
                tfs.setup.value,
                round(entry, 8),
                round(sl, 8),
                (round(tp, 8) if _is_defined(tp) else ""),
                round(rr_peak, 4),
                (round(rr_at_tp, 4) if _is_defined(rr_at_tp) else ""),
                bars_to_peak,
                (bars_to_peak * tfs.setup.minutes),
                tbq_val,
                atr_val,
                oi_val,
                "stopped" if stopped else ("time_limit" if j > end_j else "open"),
            ])

            # перемотка до разрешения + cooldown
            # если выбило — на j; если time_limit — на end_j
            next_i = (j if stopped else end_j) + max(1, int(cooldown_min / tfs.setup.minutes))
            i = max(i + 1, next_i)

def main():
    # 1) symbols
    loader = Loader()
    symbols = loader.get_filtered_symbols()

    # 2) out path — авто
    out_dir = ".generated/backtest"
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"pump_backtest_{ts}.csv")
    _ensure_dir(out_path)

    # 3) write CSV
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "profile", "strong_ts", "setup_tf",
            "entry", "sl", "tp", "rr_peak", "rr_at_tp",
            "bars_to_peak", "minutes_to_peak",
            "tbq_at_entry", "atr_at_entry", "oi_at_entry",
            "outcome"
        ])

        for sym in symbols:
            try:
                backtest_symbol(
                    loader=loader,
                    symbol=sym,
                    profiles=DEFAULT_PROFILES,
                    writer=writer,
                    setup_bars=SETUP_BARS,
                    context_bars=CONTEXT_BARS,
                    macro_bars=MACRO_BARS,
                    warmup_bars=WARMUP_BARS,
                    corr_window=CORR_WINDOW,
                    max_hold_min=MAX_HOLD_MIN,
                    cooldown_min=COOLDOWN_MIN,
                )
            except Exception as e:
                logw(f"[WARN] {sym}: {e}")

    log(f"[OK] Saved CSV to: {out_path}")


if __name__ == "__main__":
    main()
