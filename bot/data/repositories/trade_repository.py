from __future__ import annotations

from dataclasses import asdict
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

    def _serialize(self, trade: Trade) -> Dict[str, object]:
        data = asdict(trade)
        data["exchange"] = trade.exchange.value
        data["side"] = trade.side.value
        data["status"] = trade.status.value
        if trade.opened_at:
            data["opened_at"] = trade.opened_at.isoformat()
        if trade.closed_at:
            data["closed_at"] = trade.closed_at.isoformat()
        if data.get("thresholds_snapshot") is None:
            data.pop("thresholds_snapshot", None)
        return data

    def save(self, trade: Trade) -> None:
        with self._lock:
            self._cache[trade.id] = trade
            payload = {tid: self._serialize(td) for tid, td in self._cache.items()}
            self._storage.write("trades", payload)

    def get(self, trade_id: str) -> Optional[Trade]:
        with self._lock:
            return self._cache.get(trade_id)

    def all(self) -> List[Trade]:
        with self._lock:
            return list(self._cache.values())

    def load(self) -> None:
        with self._lock:
            data = self._storage.read("trades") or {}
            for key, raw in data.items():
                trade = self._storage.deserialize_trade(raw)
                self._cache[key] = trade
