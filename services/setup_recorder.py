import os
import threading
import time
from typing import List
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from openpyxl import Workbook, load_workbook
from domain.models.timeframe import Timeframe
from data.loader import Loader

from config.constants import FILL_OUTDATED_ENTRY, CROSS_MONITOR_HISTORY_BARS
from utils.logger import logw
from utils.setup_log_utils import HEADERS, row_from_signal
from domain.models.setup_log_column import SetupLogColumn

FILE_PATH = os.path.join('.generated', 'xls', 'setup_log.xlsx')



_CONF_ORDER = {
    'weak': 0,
    'moderate': 1,
    'strong': 2,
}


class SetupLog:
    """Класс для работы с журналом сетапов в XLSX-файле."""

    def __init__(self, file_path: str = FILE_PATH) -> None:
        self.file_path = file_path
        self.lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._thread: threading.Thread | None = None

    def _ensure_workbook(self):
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        if os.path.exists(self.file_path):
            wb = load_workbook(self.file_path)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(HEADERS)
        return wb, ws

    def _write_setup(self, signal, setup_tf: Timeframe) -> None:
        with self.lock:
            wb, ws = self._ensure_workbook()
            new_row = row_from_signal(signal, setup_tf)
            last_idx = None
            for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=False), start=2):
                if row[1].value == signal.symbol:
                    last_idx = idx
            inserted = False
            if last_idx:
                prev_conf = ws.cell(row=last_idx, column=4).value
                if prev_conf in _CONF_ORDER and _CONF_ORDER.get(new_row[3], 0) > _CONF_ORDER.get(prev_conf, 0):
                    for cell in ws[last_idx]:
                        cell.fill = FILL_OUTDATED_ENTRY
                    ws.insert_rows(last_idx + 1)
                    for col, val in enumerate(new_row, start=1):
                        ws.cell(row=last_idx + 1, column=col, value=val)
                    inserted = True
            if not inserted:
                ws.append(new_row)
            wb.save(self.file_path)

    def record_setup(self, signal, setup_tf: Timeframe) -> None:
        """Записать данные сигнала асинхронно."""
        self._executor.submit(self._write_setup, signal, setup_tf)

    def _update_row(self, timestamp: str, symbol: str,
                    main_high_crossed: bool | None = None,
                    correction_low_crossed: bool | None = None) -> None:
        with self.lock:
            wb, ws = self._ensure_workbook()
            headers = [c.value for c in ws[1]]
            try:
                ts_idx = headers.index(SetupLogColumn.TIMESTAMP.value) + 1
                symbol_idx = headers.index(SetupLogColumn.SYMBOL.value) + 1
                mh_idx = headers.index(SetupLogColumn.MAIN_HIGH_CROSSED.value) + 1
                cl_idx = headers.index(SetupLogColumn.CORRECTION_LOW_CROSSED.value) + 1
            except ValueError:
                return
            for row in ws.iter_rows(min_row=2):
                if row[ts_idx - 1].value == timestamp and row[symbol_idx - 1].value == symbol:
                    if main_high_crossed is not None and row[mh_idx - 1].value in (None, ''):
                        row[mh_idx - 1].value = str(bool(main_high_crossed))
                    if correction_low_crossed is not None and row[cl_idx - 1].value in (None, ''):
                        row[cl_idx - 1].value = str(bool(correction_low_crossed))
                    break
            wb.save(self.file_path)

    def update_setup_row(self, timestamp: str, symbol: str,
                         main_high_crossed: bool | None = None,
                         correction_low_crossed: bool | None = None) -> None:
        self._executor.submit(
            self._update_row,
            timestamp,
            symbol,
            main_high_crossed,
            correction_low_crossed,
        )

    def check_crossings(self, loader: Loader) -> None:
        with self.lock:
            if not os.path.exists(self.file_path):
                return
            wb = load_workbook(self.file_path)
            ws = wb.active
            headers = [c.value for c in ws[1]]
            rows = [row for row in ws.iter_rows(min_row=2, values_only=True)]

        try:
            ts_idx = headers.index(SetupLogColumn.TIMESTAMP.value)
            symbol_idx = headers.index(SetupLogColumn.SYMBOL.value)
            sl_idx = headers.index(SetupLogColumn.SL.value)
            tp_idx = headers.index(SetupLogColumn.TP.value)
            tf_idx = headers.index(SetupLogColumn.TF.value)
            mh_idx = headers.index(SetupLogColumn.MAIN_HIGH_CROSSED.value)
            cl_idx = headers.index(SetupLogColumn.CORRECTION_LOW_CROSSED.value)
        except ValueError:
            return

        for row in rows:
            ts_str = row[ts_idx]
            symbol = row[symbol_idx]
            if not ts_str or not symbol:
                continue
            main_val = row[mh_idx]
            corr_val = row[cl_idx]
            if main_val not in (None, '') and corr_val not in (None, ''):
                continue
            try:
                pump_time = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            tf_str = row[tf_idx]
            try:
                tf = Timeframe(tf_str)
            except Exception:
                tf = Timeframe.M1
            if datetime.now() - pump_time > timedelta(minutes=CROSS_MONITOR_HISTORY_BARS * tf.minutes):
                continue
            bars = loader.fetch_ohlcvi(symbol, tf, limit=CROSS_MONITOR_HISTORY_BARS)
            main_crossed = bool(main_val)
            corr_crossed = bool(corr_val)
            for b in bars:
                if b.timestamp < pump_time:
                    continue
                if not main_crossed and b.high >= float(row[tp_idx]):
                    main_crossed = True
                if not corr_crossed and b.low <= float(row[sl_idx]):
                    corr_crossed = True
            if (main_crossed and main_val in (None, '')) or (corr_crossed and corr_val in (None, '')):
                self.update_setup_row(
                    timestamp=ts_str,
                    symbol=symbol,
                    main_high_crossed=main_crossed if main_val in (None, '') else None,
                    correction_low_crossed=corr_crossed if corr_val in (None, '') else None,
                )

    def start_monitor(self, loader: Loader, interval: int = 60) -> None:
        """Запускает фоновую проверку файла на достижение уровней."""

        if self._thread:
            return

        def _loop() -> None:
            while True:
                time.sleep(interval)
                try:
                    self.check_crossings(loader)
                except Exception as error:
                    logw(f"Ошибка проверки лога: {error}")

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

