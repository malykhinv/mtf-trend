from __future__ import annotations

import asyncio, os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import aiosqlite

from config.constants import TIMEZONE
from domain.models.Timeframe import Timeframe

@dataclass
class Database:
    path: str = ".generated/db/signals.db"
    _conn: Optional[aiosqlite.Connection] = None
    _init_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _op_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def connect(self) -> None:
        if self._conn:
            return
        async with self._init_lock:
            if self._conn:
                return
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._conn = await aiosqlite.connect(self.path)
            await self._conn.execute("PRAGMA journal_mode=WAL;")
            await self._conn.execute("PRAGMA synchronous=NORMAL;")
            await self._conn.execute("PRAGMA foreign_keys=ON;")
            await self._conn.execute("PRAGMA busy_timeout=5000;")
            await self._conn.commit()
            await self.ensure_schema()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def ensure_schema(self) -> None:
        assert self._conn is not None
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signals_last (
                symbol     TEXT NOT NULL,
                timeframe  TEXT NOT NULL,
                last_ts    REAL NOT NULL,
                PRIMARY KEY (symbol, timeframe)
            );
        """)
        await self._conn.commit()

    async def check_if_recent(self, symbol: str, timeframe: Timeframe, minutes: int) -> bool:
        await self.connect()
        assert self._conn is not None

        now_sec = datetime.now(TIMEZONE).timestamp()
        delta_sec = minutes * 60

        # опциональная сериализация операций:
        async with self._op_lock:
            async with self._conn.execute(
                "SELECT last_ts FROM signals_last WHERE symbol=? AND timeframe=?",
                (symbol, timeframe.value),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            return False
        return (now_sec - float(row[0])) < delta_sec

    async def save_signal(self, symbol: str, timeframe: Timeframe) -> None:
        await self.connect()
        assert self._conn is not None
        ts = datetime.now(TIMEZONE).timestamp()

        async with self._op_lock:
            await self._conn.execute(
                """
                INSERT INTO signals_last(symbol, timeframe, last_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                  last_ts = CASE
                              WHEN excluded.last_ts > signals_last.last_ts
                              THEN excluded.last_ts
                              ELSE signals_last.last_ts
                            END
                """,
                (symbol, timeframe.value, ts),
            )
            await self._conn.commit()