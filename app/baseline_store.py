from __future__ import annotations

import json
import os
import sqlite3
from collections import deque
from typing import Deque, Dict, Iterable, Tuple

import constants

DB_PATH = "data/cache/baseline.sqlite"


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS baseline (symbol TEXT PRIMARY KEY, price_win TEXT NOT NULL, vol_win TEXT NOT NULL)"
    )
    return conn


def load(symbols: Iterable[str]) -> Dict[str, Tuple[Deque[float], Deque[float]]]:
    conn = _connect()
    cur = conn.cursor()
    result: Dict[str, Tuple[Deque[float], Deque[float]]] = {}
    for sym in symbols:
        cur.execute("SELECT price_win, vol_win FROM baseline WHERE symbol=?", (sym,))
        row = cur.fetchone()
        if row:
            price = json.loads(row[0])
            vol = json.loads(row[1])
            result[sym] = (
                deque(price, maxlen=constants.Z_BASE_WINDOW_MIN),
                deque(vol, maxlen=constants.Z_BASE_WINDOW_MIN),
            )
    conn.close()
    return result


def save(baselines: Dict[str, Tuple[Deque[float], Deque[float]]]) -> None:
    if not baselines:
        return
    conn = _connect()
    cur = conn.cursor()
    rows = [
        (sym, json.dumps(list(px)), json.dumps(list(vol)))
        for sym, (px, vol) in baselines.items()
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO baseline(symbol, price_win, vol_win) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
