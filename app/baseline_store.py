from __future__ import annotations

import atexit
import json
import os
import sqlite3
from collections import deque
from typing import Deque, Dict, Iterable, Tuple

import constants

Baseline = Tuple[Deque[float], Deque[float]]

DB_PATH = "data/cache/baseline.sqlite"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
CONN: sqlite3.Connection = sqlite3.connect(DB_PATH)
CONN.execute(
    (
        "CREATE TABLE IF NOT EXISTS baseline ("
        "symbol TEXT PRIMARY KEY, "
        "price_win TEXT NOT NULL, "
        "vol_win TEXT NOT NULL)"
    )
)
atexit.register(CONN.close)


def load(symbols: Iterable[str]) -> Dict[str, Baseline]:
    cur = CONN.cursor()
    result: Dict[str, Baseline] = {}
    for sym in symbols:
        cur.execute(
            "SELECT price_win, vol_win FROM baseline WHERE symbol=?",
            (sym,),
        )
        row = cur.fetchone()
        if row:
            price = json.loads(row[0])
            vol = json.loads(row[1])
            result[sym] = (
                deque(price, maxlen=constants.Z_BASE_WINDOW_MIN),
                deque(vol, maxlen=constants.Z_BASE_WINDOW_MIN),
            )
    cur.close()
    return result


def save(baselines: Dict[str, Baseline]) -> None:
    if not baselines:
        return
    cur = CONN.cursor()
    rows = [
        (sym, json.dumps(list(px)), json.dumps(list(vol)))
        for sym, (px, vol) in baselines.items()
    ]
    cur.executemany(
        (
            "INSERT OR REPLACE INTO baseline(symbol, price_win, vol_win) "
            "VALUES (?, ?, ?)"
        ),
        rows,
    )
    CONN.commit()
    cur.close()
