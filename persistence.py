from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from constants import CACHE_DIR, BASELINE_DB_PATH, RUNTIME_DIR


def _ensure_dirs() -> None:
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    Path(RUNTIME_DIR).mkdir(parents=True, exist_ok=True)


class BaselineCache:
    """Simple key-value storage backed by SQLite."""

    def __init__(self, path: Path = BASELINE_DB_PATH) -> None:
        _ensure_dirs()
        self._path = path
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS baseline (
                    symbol TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
                """
            )

    def save(self, symbol: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data)
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO baseline (symbol, data) VALUES (?, ?)",
                (symbol, payload),
            )

    def load(self, symbol: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._path) as conn:
            cur = conn.execute("SELECT data FROM baseline WHERE symbol = ?", (symbol,))
            row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])


def write_runtime(name: str, content: str) -> Path:
    _ensure_dirs()
    path = Path(RUNTIME_DIR) / name
    path.write_text(content)
    return path


def read_runtime(name: str) -> str | None:
    path = Path(RUNTIME_DIR) / name
    if not path.exists():
        return None
    return path.read_text()
