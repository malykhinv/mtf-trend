"""Простые утилиты для логирования сделок в Excel.

Модуль предоставляет небольшую функцию для сохранения информации о сделках
в файл ``.xlsx``. Каждая сделка записывается одной строкой со следующими
колонками:

``symbol``
    Символ торговой пары.
``entry_time`` / ``exit_time``
    Временные метки открытия и закрытия позиции в формате ISO.
``entry_futures_price`` / ``exit_futures_price``
    Цена входа и выхода на фьючерсе.
``entry_spot_price`` / ``exit_spot_price``
    Цена спота при входе и выходе.
``entry_basis`` / ``exit_basis``
    Рассчитанный базис спот/фьючерс в процентах при входе и выходе.
``basis_pct``
    Базис на момент создания записи.
``funding``
    Ставка фондирования, зафиксированная для сделки.
``quantity``
    Размер позиции.
``volume_usd``
    Номинальная стоимость сделки в USD.
``pnl`` / ``pnl_pct``
    Прибыль и убыток в абсолютном выражении и в процентах от ``volume_usd``.
``commissions``
    Совокупные комиссионные по позиции.
``funding_accrued``
    Накопленные выплаты по фондированию (положительные — полученные, отрицательные — уплаченные).
``slippage``
    Наблюдаемое проскальзывание относительно цены входа.
``exit_reasons``
    Список причин закрытия позиции.
``notes``
    Произвольные заметки.

Функция :func:`log_trade` добавляет новую строку в ``data/funding_bot_log.xlsx``,
создавая файл и его родительский каталог при необходимости. Функция следит
за наличием всех колонок и избегает повреждения данных, используя ``openpyxl``
для добавления строк без полного переписывания файла через pandas.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from openpyxl import Workbook, load_workbook

# Расположение файла журнала по умолчанию.
LOG_PATH: Path = Path("data") / "funding_bot_log.xlsx"

# Упорядоченный список колонок, ожидаемых для каждой сделки.
LOG_COLUMNS: list[str] = [
    "symbol",
    "exchange",
    "entry_time",
    "exit_time",
    "entry_futures_price",
    "exit_futures_price",
    "entry_spot_price",
    "exit_spot_price",
    "entry_basis",
    "exit_basis",
    "basis_pct",
    "funding",
    "quantity",
    "volume_usd",
    "pnl",
    "pnl_pct",
    "commissions",
    "funding_accrued",
    "slippage",
    "exit_reasons",
    "notes",
]

# Глобальная блокировка для сериализации доступа к файлу журнала.
_log_lock = asyncio.Lock()


def _ensure_parent(path: Path) -> None:
    """Создаёт родительскую директорию для ``path``, если она не существует."""

    path.parent.mkdir(parents=True, exist_ok=True)


def _validate_entry(entry: Mapping[str, Any], columns: Iterable[str]) -> None:
    """Проверяет, что в ``entry`` присутствуют все поля ``columns``.

    Исключения
    ----------
    ValueError
        Если какое-либо из обязательных полей отсутствует в ``entry``.
    """

    missing = [col for col in columns if col not in entry]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


async def log_trade(trade: Mapping[str, Any], path: Path = LOG_PATH) -> None:
    """Добавляет информацию о сделке в Excel-журнал.

    Параметры
    ---------
    trade:
        Словарь, содержащий все ключи из :data:`LOG_COLUMNS`.
    path:
        Необязательный путь к файлу Excel. По умолчанию
        ``data/funding_bot_log.xlsx``.

    Функция асинхронная и использует глобальную блокировку, чтобы
    предотвращать одновременную запись в файл из разных задач.
    """

    path = Path(path)
    _ensure_parent(path)
    _validate_entry(trade, LOG_COLUMNS)

    # Нормализуем сложные типы перед записью в книгу
    normalized: Dict[str, Any] = {}
    for col in LOG_COLUMNS:
        val = trade.get(col)
        if isinstance(val, (list, tuple)):
            val = ",".join(map(str, val))
        normalized[col] = val

    async with _log_lock:
        if path.exists():
            wb = load_workbook(path)
            ws = wb.active
            # Повторно создаём заголовок, если файл был изменён вручную
            if ws.max_row == 0 or [cell.value for cell in ws[1]] != LOG_COLUMNS:
                ws.delete_rows(1, ws.max_row)
                ws.append(LOG_COLUMNS)
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(LOG_COLUMNS)

        ws.append([normalized[col] for col in LOG_COLUMNS])
        wb.save(path)
        wb.close()
