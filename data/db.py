import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List

from config.constants import FLOAT_UNDEFINED
from domain.models.timeframe import Timeframe
from utils.decorator import log_duration_ms

DB_PATH = Path(__file__).parent.parent / ".generated" / "db" / "trades.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

@log_duration_ms
def get_connection() -> sqlite3.Connection:
    """
    Создаёт и возвращает соединение с SQLite-базой данных.
    """
    conn = sqlite3.connect(DB_PATH)

    # Таблица трейдов
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT CHECK(side IN ('long', 'short')) NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            atr REAL NOT NULL,
            amount_usdt REAL NOT NULL,
            active INTEGER DEFAULT 1,
            partial_exit_done INTEGER DEFAULT 0,
            cooldown_until DATETIME,
            last_action_ts DATETIME DEFAULT CURRENT_TIMESTAMP,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица истории OI
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            tf TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            oi REAL NOT NULL
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_oi_unique ON oi_history (symbol, tf, timestamp)")

    return conn

@log_duration_ms
def save_oi(symbol: str, tf: Timeframe, timestamp: datetime, oi: float) -> None:
    """
    Сохраняет OI для конкретного тикера и таймфрейма в базу, ограничивая историю 1500 записями.
    """
    ts = timestamp.replace(second=0, microsecond=0).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO oi_history (symbol, tf, timestamp, oi)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, tf, timestamp) DO NOTHING
        """, (symbol, tf.value, ts, oi))
        conn.commit()

        # Оставляем только 1500 последних записей
        conn.execute("""
            DELETE FROM oi_history WHERE id NOT IN (
                SELECT id FROM oi_history
                WHERE symbol = ? AND tf = ?
                ORDER BY timestamp DESC
                LIMIT 1500
            )
        """, (symbol, tf.value))
        conn.commit()

@log_duration_ms
def load_oi(symbol: str, tf: Timeframe, timestamps: List[datetime]) -> List[float]:
    """
    Загружает значения OI из базы данных, соответствующие заданным временным меткам.
    """
    ts_strs = [ts.replace(second=0, microsecond=0).isoformat() for ts in timestamps]
    if not ts_strs:
        return []

    placeholders = ",".join("?" for _ in ts_strs)
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT timestamp, oi FROM oi_history
            WHERE symbol = ? AND tf = ? AND timestamp IN ({placeholders})
        """, (symbol, tf.value, *ts_strs)).fetchall()

    ts_to_oi = {row[0]: row[1] for row in rows}
    return [ts_to_oi.get(ts.isoformat(), FLOAT_UNDEFINED) for ts in timestamps]
