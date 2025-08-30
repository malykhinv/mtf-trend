#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Backtest Pump (STRONG) -> XLSX с файловым локом (openpyxl) + инкрементальные сохранения
- Единый файл: .generated/backtest/pump_backtest.xlsx
- Файл создаётся и сохраняется СРАЗУ (шапка видна всегда)
- Каждая STRONG-сделка — отдельная строка (append в конец)
- Периодический save: каждые N строк и после каждого символа
"""

from __future__ import annotations

import os
import math
import time
import atexit
from bisect import bisect_right

from openpyxl import Workbook, load_workbook  # pip install openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from config.constants import FLOAT_UNDEFINED, MTF_PROFILE_MACRO_1, MTF_PROFILE_MACRO_3, MTF_PROFILE_MACRO_5
from data.loader import Loader
from domain.detection.pump.pump_setup import PumpSetup
from domain.models.mtf_profile import MTFProfile
from utils.logger import log, logw

# --- параметры по умолчанию
DEFAULT_PROFILES: list[MTFProfile] = [
    MTF_PROFILE_MACRO_1,
    MTF_PROFILE_MACRO_3,
    MTF_PROFILE_MACRO_5,
]
SETUP_BARS = 1500
CONTEXT_BARS = 120
MACRO_BARS = 60
WARMUP_BARS = 200
CORR_WINDOW = 8
MAX_HOLD_MIN = 240
COOLDOWN_MIN = 15

# Лок: таймаут ожидания
LOCK_TIMEOUT_SEC = int(os.getenv("BACKTEST_LOCK_TIMEOUT_SEC", "60"))
LOCK_STALE_SEC   = int(os.getenv("BACKTEST_LOCK_STALE_SEC", "3600"))

# Инкрементальные сохранения
FLUSH_EVERY = int(os.getenv("BACKTEST_FLUSH_EVERY", "50"))

HEADERS = [
    "symbol", "profile", "strong_ts", "setup_tf",
    "entry_ts", "exit_ts",
    "entry", "sl", "tp",
    "peak_price", "rr_peak", "rr_at_tp",
    "bars_to_peak", "minutes_to_peak", "hold_minutes",
    "tbq_at_entry", "atr_at_entry", "oi_at_entry",
    "outcome",
]

# --- Utils
def _is_defined(x: float) -> bool:
    return (x is not None) and (not math.isnan(x)) and (x != FLOAT_UNDEFINED)


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _rr(entry: float, sl: float, px: float) -> float:
    r = max(entry - sl, 1e-12)
    return max(0.0, (px - entry) / r)

def _excel_lock_present(xlsx_path: str) -> bool:
    # Excel/LibreOffice создают временный "~$<name>.xlsx" рядом с файлом
    d, name = os.path.split(xlsx_path)
    return os.path.exists(os.path.join(d, f"~${name}"))

def _col_letter_1based(n: int) -> str:
    # 1 -> A, 26 -> Z, 27 -> AA, ...
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s

class FileLock:
    """Кроссплатформенный лок через атомарный .lock-файл."""
    def __init__(self, target_path: str, timeout_sec: int = LOCK_TIMEOUT_SEC, stale_sec: int = LOCK_STALE_SEC):
        self.lock_path = target_path + ".lock"
        self.timeout = timeout_sec
        self.stale = stale_sec
        self.acquired = False

    def acquire(self) -> None:
        deadline = time.time() + self.timeout
        while True:
            try:
                # атомарное создание файла; если существует — другой процесс держит лок
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, str(os.getpid()).encode("utf-8"))
                finally:
                    os.close(fd)
                self.acquired = True
                return
            except FileExistsError:
                # проверка на «протухший» лок
                try:
                    mtime = os.path.getmtime(self.lock_path)
                    if (time.time() - mtime) > self.stale:
                        # протух — снимаем
                        os.remove(self.lock_path)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise TimeoutError(f"Lock busy: {self.lock_path}")
                time.sleep(0.5)

    def release(self) -> None:
        if self.acquired:
            try:
                os.remove(self.lock_path)
            except FileNotFoundError:
                pass
            self.acquired = False

    # контекст-менеджер
    def __enter__(self):
        self.acquire()
        return self
    def __exit__(self, exc_type, exc, tb):
        self.release()

def _open_or_create_book(path: str) -> tuple[Workbook, Worksheet]:
    """Создаёт файл (с шапкой) если отсутствует. Возвращает (wb, ws)."""
    if os.path.exists(path):
        wb = load_workbook(path)
        ws = wb.active
        # если вдруг файл без шапки — добавим
        if ws.max_row == 0 or [c.value for c in ws[1]] != HEADERS:
            ws.delete_rows(1, ws.max_row or 1)
            ws.append(HEADERS)
        return wb, ws
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "STRONG"
        ws.freeze_panes = "A2"
        ws.append(HEADERS)
        # ширины колонок для наглядности
        widths = {
            1:12, 2:22, 3:20, 4:9, 5:20, 6:20,
            7:12, 8:12, 9:12, 10:12, 11:10, 12:10,
            13:12, 14:14, 15:12, 16:12, 17:12, 18:12, 19:10
        }
        for col, w in widths.items():
            ws.column_dimensions[_col_letter_1based(col)].width = w
        # автофильтр пока только на шапку; диапазон обновим в конце
        ws.auto_filter.ref = f"A1:{_col_letter_1based(len(HEADERS))}1"
        wb.save(path)  # <— файл появляется СРАЗУ
        return wb, ws

# --- Core
def backtest_symbol(
    loader: 'Loader',
    symbol: str,
    profiles,
    writerow,
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

        # КЭШ таймстемпов (один раз)
        ts_setup = [b.timestamp for b in bars_setup]
        ts_context = [b.timestamp for b in bars_context] if bars_context else []
        ts_macro = [b.timestamp for b in bars_macro] if bars_macro else []

        # Предвычисленные лимиты (вне горячего цикла)
        max_hold_bars = max(1, int(max_hold_min / tfs.setup.minutes))
        cooldown_bars = max(1, int(cooldown_min / tfs.setup.minutes))

        # Начальные индексы контекста/макро (двигаем только вперёд)
        i = warmup_bars
        idx_ctx = bisect_right(ts_context, ts_setup[i]) if ts_context else 0
        idx_mac = bisect_right(ts_macro, ts_setup[i]) if ts_macro else 0

        # двигаемся слева-направо по setup
        while i < len(bars_setup) - 1:
            ts_i = ts_setup[i]

            # Двигаем «указатели» без копий списков timestamps
            if ts_context:
                idx_ctx = bisect_right(ts_context, ts_i, lo=idx_ctx)
            if ts_macro:
                idx_mac = bisect_right(ts_macro, ts_i, lo=idx_mac)

            if idx_ctx == 0 or idx_mac == 0:
                i += 1
                continue

            # срезы по времени для всех ТФ (<= ts_i)
            bars_by_tf = {
                tfs.setup:   bars_setup[: i + 1],  # минимально необходимый срез
                tfs.context: bars_context[: idx_ctx],
                tfs.macro:   bars_macro[: idx_mac],
            }
            if not bars_by_tf[tfs.context] or not bars_by_tf[tfs.macro]:
                i += 1
                continue

            # Быстрый предфильтр (дешёвый моментум + шаг по сетапу)
            if (i % 3) != 0:
                i += 1
                continue
            if i >= 3:
                w0, w1, w2, w3 = bars_setup[i - 3:i + 1]
                if not (w3.close > w3.open and w3.close > max(w.high for w in (w0, w1, w2))):
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
                sl = min(b.low for b in bars_setup[left: i + 1])

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

            # лимит по времени (предвычисленный)
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

            # итоговые метки
            exit_index = (j if stopped else end_j)
            exit_bar = bars_setup[exit_index]
            entry_ts = entry_bar.timestamp
            exit_ts = exit_bar.timestamp
            hold_minutes = (exit_index - (i + 1)) * tfs.setup.minutes

            # поля Bar могут быть пустыми — пишем "" если не определены
            tbq_val = entry_bar.tbq if _is_defined(entry_bar.tbq) else ""
            atr_val = entry_bar.atr if _is_defined(entry_bar.atr) else ""
            oi_val  = entry_bar.oi  if _is_defined(entry_bar.oi)  else ""

            # строка результата (одна STRONG-сделка = одна строка)
            writerow([
                symbol,
                f"{tfs.macro.value}/{tfs.context.value}->{tfs.setup.value}",
                ts_i.isoformat(),
                tfs.setup.value,
                entry_ts.isoformat(),
                exit_ts.isoformat(),
                round(entry, 8),
                round(sl, 8),
                (round(tp, 8) if _is_defined(tp) else ""),
                round(peak_px, 8),
                round(rr_peak, 4),
                (round(rr_at_tp, 4) if _is_defined(rr_at_tp) else ""),
                bars_to_peak,
                bars_to_peak * tfs.setup.minutes,
                hold_minutes,
                tbq_val,
                atr_val,
                oi_val,
                "stopped" if stopped else ("time_limit" if j > end_j else "open"),
            ])

            next_i = exit_index + cooldown_bars
            i = max(i + 1, next_i)


def main():
    # 1) symbols
    loader = Loader()
    symbols = loader.get_filtered_symbols()

    # 2) out path — один файл на запуск
    out_dir = ".generated/backtest"
    out_path = os.path.join(out_dir, "pump_backtest.xlsx")
    _ensure_dir(out_path)

    lock = FileLock(out_path)
    atexit.register(lock.release)
    with lock:
        if _excel_lock_present(out_path):
            raise RuntimeError("Файл открыт в Excel/LibreOffice (найден ~$.xlsx). Закрой файл и повтори запуск.")

        # создаём/открываем XLSX и сохраняем сразу (файл гарантированно существует)
        wb, ws = _open_or_create_book(out_path)

        # writerow-адаптер (append в конец) + инкрементальный save
        rows_since_save = 0

        def flush():
            nonlocal rows_since_save
            if rows_since_save > 0:
                wb.save(out_path)
                rows_since_save = 0

        def writerow(values: list):
            nonlocal rows_since_save
            ws.append(values)
            rows_since_save += 1
            if rows_since_save >= FLUSH_EVERY:
                flush()

        # прогон
        for sym in symbols:
            try:
                backtest_symbol(
                    loader=loader,
                    symbol=sym,
                    profiles=DEFAULT_PROFILES,
                    writerow=writerow,
                    setup_bars=SETUP_BARS,
                    context_bars=CONTEXT_BARS,
                    macro_bars=MACRO_BARS,
                    warmup_bars=WARMUP_BARS,
                    corr_window=CORR_WINDOW,
                    max_hold_min=MAX_HOLD_MIN,
                    cooldown_min=COOLDOWN_MIN,
                )
            except Exception as e:
                logw(f"{sym}: {e}")
            finally:
                # сейвим хотя бы по завершении каждого символа
                flush()

        # автофильтр на фактический диапазон
        last_row = ws.max_row
        ws.auto_filter.ref = f"A1:{_col_letter_1based(len(HEADERS))}{last_row}"
        wb.save(out_path)

    log(f"[OK] Saved XLSX to: {out_path}")

if __name__ == "__main__":
    main()
