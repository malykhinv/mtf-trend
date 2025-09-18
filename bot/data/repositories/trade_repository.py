from __future__ import annotations

from threading import RLock
from typing import Dict, List, Optional

from ...domain.models.entities import Trade
from ..io.storage import Storage


class TradeRepository:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._lock = RLock()
        self._cache: Dict[str, Trade] = {}
        self.load()

    def save(self, trade: Trade) -> None:
        with self._lock:
            self._cache[trade.id] = trade
            self._storage.save_trade(trade)

    def get(self, trade_id: str) -> Optional[Trade]:
        with self._lock:
            return self._cache.get(trade_id)

    def all(self) -> List[Trade]:
        with self._lock:
            return list(self._cache.values())

    def load(self) -> None:
        with self._lock:
            self._cache.clear()
            trades = self._storage.load_trades()
            for trade in trades:
                self._cache[trade.id] = trade
