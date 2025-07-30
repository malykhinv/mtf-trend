import os
import threading
from typing import List
from concurrent.futures import ThreadPoolExecutor
from openpyxl import Workbook, load_workbook
from config.constants import FILL_OUTDATED_ENTRY

FILE_PATH = os.path.join('.generated', 'xls', 'setup_log.xlsx')
LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=1)

HEADERS = [
    'Timestamp',
    'Symbol',
    'Side',
    'Confidence',
    'Entry',
    'SL',
    'TP',
    'RR',
]

_CONF_ORDER = {
    'weak': 0,
    'moderate': 1,
    'strong': 2,
}

def _ensure_workbook():
    os.makedirs(os.path.dirname(FILE_PATH), exist_ok=True)
    if os.path.exists(FILE_PATH):
        wb = load_workbook(FILE_PATH)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(HEADERS)
    return wb, ws

def _row_from_signal(signal) -> List:
    return [
        signal.timestamp.isoformat(),
        signal.symbol,
        getattr(signal.side, 'value', str(signal.side)),
        getattr(signal.confidence, 'value', str(signal.confidence)),
        signal.entry,
        signal.sl,
        signal.tp,
        signal.rr,
    ]

def _write_setup(signal) -> None:
    with LOCK:
        wb, ws = _ensure_workbook()
        new_row = _row_from_signal(signal)
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
        wb.save(FILE_PATH)


def record_setup(signal) -> None:
    """Записать данные сигнала в файл XLSX в отдельном потоке."""
    _EXECUTOR.submit(_write_setup, signal)
