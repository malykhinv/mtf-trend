"""SQLite-backed trade log repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from domain.models.enums import Side

from .models import TradeRecord
from .repository import TradeLogRepository


class SQLiteTradeLog(TradeLogRepository):
    """Persist :class:`TradeRecord` entries inside a SQLite database."""

    def __init__(self, db_path: str | Path = Path("data/runtime/trades.db")) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        self._connection.close()

    def append(self, record: TradeRecord) -> None:
        """Persist a single trade record atomically."""

        with self._connection:  # ensures atomic transaction
            self._connection.execute(
                """
                INSERT INTO trade_records (
                    timestamp,
                    symbol,
                    side,
                    outcome,
                    entry,
                    take_profit,
                    stop_loss,
                    quantity,
                    fees
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.timestamp,
                    record.symbol,
                    record.side.value,
                    record.outcome,
                    record.entry,
                    record.take_profit,
                    record.stop_loss,
                    record.quantity,
                    record.fees,
                ),
            )

    def fetch_all(self) -> Sequence[TradeRecord]:
        """Return stored trade history ordered by insertion time."""

        cursor = self._connection.execute(
            """
            SELECT timestamp, symbol, side, outcome, entry, take_profit, stop_loss, quantity, fees
            FROM trade_records
            ORDER BY timestamp ASC, rowid ASC
            """
        )
        rows = cursor.fetchall()
        return [
            TradeRecord(
                timestamp=row["timestamp"],
                symbol=row["symbol"],
                side=Side(row["side"]),
                outcome=row["outcome"],
                entry=row["entry"],
                take_profit=row["take_profit"],
                stop_loss=row["stop_loss"],
                quantity=row["quantity"],
                fees=row["fees"],
            )
            for row in rows
        ]

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    entry REAL,
                    take_profit REAL,
                    stop_loss REAL,
                    quantity REAL,
                    fees REAL
                )
                """
            )
