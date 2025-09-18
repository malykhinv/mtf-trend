from __future__ import annotations

from dataclasses import asdict
from threading import RLock
from typing import Dict, List, Optional

from ...domain.models.entities import Signal
from ..io.storage import Storage


class SignalRepository:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._lock = RLock()
        self._cache: Dict[str, Signal] = {}
        self.load()

    def _serialize(self, signal: Signal) -> Dict[str, object]:
        data = asdict(signal)
        data["candle"]["started_at"] = signal.candle.started_at.isoformat()
        data["candle"]["closed_at"] = signal.candle.closed_at.isoformat()
        data["candle"]["exchange"] = signal.candle.exchange.value
        data["candle"]["timeframe"] = signal.candle.timeframe.value
        data["side"] = signal.side.value
        data["direction"] = signal.direction.value
        data["triggered_at"] = signal.triggered_at.isoformat()
        return data

    def save(self, signal: Signal) -> None:
        with self._lock:
            self._cache[signal.id] = signal
            payload = {sid: self._serialize(sig) for sid, sig in self._cache.items()}
            self._storage.write("signals", payload)

    def get(self, signal_id: str) -> Optional[Signal]:
        with self._lock:
            return self._cache.get(signal_id)

    def all(self) -> List[Signal]:
        with self._lock:
            return list(self._cache.values())

    def load(self) -> None:
        with self._lock:
            data = self._storage.read("signals") or {}
            for key, raw in data.items():
                signal = self._storage.deserialize_signal(raw)
                self._cache[key] = signal
