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
        if signal.timeframe:
            data["timeframe"] = signal.timeframe.value
        else:
            data.pop("timeframe", None)
        data["candle"]["started_at"] = signal.candle.started_at.isoformat()
        data["candle"]["closed_at"] = signal.candle.closed_at.isoformat()
        data["candle"]["exchange"] = signal.candle.exchange.value
        data["candle"]["timeframe"] = signal.candle.timeframe.value
        if signal.created_at:
            data["created_at"] = signal.created_at.isoformat()
        if signal.updated_at:
            data["updated_at"] = signal.updated_at.isoformat()
        data["side"] = signal.side.value
        data["direction"] = signal.direction.value
        data["triggered_at"] = signal.triggered_at.isoformat()
        thresholds = data.get("thresholds")
        if isinstance(thresholds, dict):
            if signal.thresholds.created_at:
                thresholds["created_at"] = signal.thresholds.created_at.isoformat()
            if signal.thresholds.updated_at:
                thresholds["updated_at"] = signal.thresholds.updated_at.isoformat()
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
