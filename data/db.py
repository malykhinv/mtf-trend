import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / ".generated" / "db" / "trades.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
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
    return conn
