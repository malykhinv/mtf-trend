from __future__ import annotations

"""Simple Excel logging utilities for trades.

This module provides a small helper to persist trade information to an
``.xlsx`` file.  Each trade is stored as a single row containing the
following columns:

``symbol``
    Trading pair symbol.
``entry_time`` / ``exit_time``
    ISO formatted timestamps for when the position was opened and closed.
``entry_price`` / ``exit_price``
    Prices at which the position was entered and exited.
``funding``
    Funding rate captured for the trade.
``pnl``
    Profit and loss of the completed trade.

The :func:`log_trade` function appends a new row to ``data/funding_bot_log.xlsx``
creating the file and its parent directory if necessary.  The function guards
against missing columns and avoids data corruption by using ``openpyxl`` to
append rows to the workbook instead of rewriting the whole file via pandas.
"""

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from openpyxl import Workbook, load_workbook

# Default location of the log file used by the bot.
LOG_PATH = Path("data") / "funding_bot_log.xlsx"

# Ordered list of columns expected for each trade entry.
LOG_COLUMNS = [
    "symbol",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "funding",
    "pnl",
]


def _ensure_parent(path: Path) -> None:
    """Create the parent directory for ``path`` if it does not exist."""

    path.parent.mkdir(parents=True, exist_ok=True)


def _validate_entry(entry: Mapping[str, Any], columns: Iterable[str]) -> None:
    """Ensure ``entry`` contains all ``columns``.

    Raises
    ------
    ValueError
        If any of the required columns is missing from ``entry``.
    """

    missing = [col for col in columns if col not in entry]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


def log_trade(trade: Mapping[str, Any], path: Path = LOG_PATH) -> None:
    """Append ``trade`` information to an Excel log file.

    Parameters
    ----------
    trade:
        Mapping containing all keys listed in :data:`LOG_COLUMNS`.
    path:
        Optional path to the Excel file.  Defaults to
        ``data/funding_bot_log.xlsx``.

    The function is intentionally small and synchronous; it is expected to be
    called outside of performance critical sections.
    """

    path = Path(path)
    _ensure_parent(path)
    _validate_entry(trade, LOG_COLUMNS)

    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
        # Re-create header if the file was manually modified
        if ws.max_row == 0 or [cell.value for cell in ws[1]] != LOG_COLUMNS:
            ws.delete_rows(1, ws.max_row)
            ws.append(LOG_COLUMNS)
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(LOG_COLUMNS)

    ws.append([trade[col] for col in LOG_COLUMNS])
    wb.save(path)
    wb.close()
