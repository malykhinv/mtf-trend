import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "trades.sqlite"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT CHECK(direction IN ('long', 'short')),
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            scenario TEXT NOT NULL,
            atr REAL NOT NULL,
            amount_usdt REAL NOT NULL,
            active INTEGER DEFAULT 1,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn
