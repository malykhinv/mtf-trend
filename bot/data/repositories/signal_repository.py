from __future__ import annotations

from threading import RLock
from typing import Dict

from ...domain.models.entities import Signal
from ..io.storage import Storage


class SignalRepository:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._lock = RLock()
        self._cache: Dict[str, Signal] = {}
        self.load()

    def save(self, signal: Signal) -> None:
        with self._lock:
            self._cache[signal.id] = signal
            self._storage.save_signal(signal)

    def load(self) -> None:
        with self._lock:
            self._cache.clear()
            signals = self._storage.load_signals()
            for signal in signals:
                self._cache[signal.id] = signal
