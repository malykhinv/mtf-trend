import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "trades.sqlite"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        direction TEXT CHECK(direction IN ('long', 'short')),
        amount_usdt REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    return conn