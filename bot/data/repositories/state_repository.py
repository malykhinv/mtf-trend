from __future__ import annotations

from datetime import datetime
from threading import RLock
from ...domain.models.state import (
    get_deposit_amount,
    get_used_amount,
    set_deposit,
    set_used_amount,
)
from ...utils.clock import utcnow
from ..io.storage import Storage


class StateRepository:
    """Persisted state of the trading capital."""

    def __init__(self, storage: Storage, key: str | None = None) -> None:
        self._storage = storage
        self._lock = RLock()
        self._key = key or "default"
        self._asset = "USDT"
        self._deposit_updated_at: datetime | None = None
        self.load()

    def load(self) -> None:
        with self._lock:
            asset, amount, updated_at_raw, used_amount = self._storage.load_state(self._key)
            self._asset = asset
            updated_at = (
                datetime.fromisoformat(updated_at_raw)
                if isinstance(updated_at_raw, str)
                else None
            )
            self._deposit_updated_at = updated_at
            set_deposit(amount, self._asset, updated_at, key=self._key)
            set_used_amount(float(used_amount), key=self._key)

    def _persist(self) -> None:
        self._storage.save_state(
            asset=self._asset,
            deposit_amount=get_deposit_amount(self._key),
            updated_at=self._deposit_updated_at,
            used=get_used_amount(self._key),
            key=self._key,
        )

    def update_deposit(
        self, amount: float, asset: str = "USDT", updated_at: datetime | None = None
    ) -> None:
        with self._lock:
            self._asset = asset
            self._deposit_updated_at = updated_at or utcnow()
            set_deposit(amount, self._asset, self._deposit_updated_at, key=self._key)
            self._persist()

    def get_deposit(self) -> float:
        return get_deposit_amount(self._key)

    def get_deposit_asset(self) -> str:
        return self._asset

    def get_last_deposit_update(self) -> datetime | None:
        return self._deposit_updated_at

    def set_used_amount(self, amount: float) -> None:
        with self._lock:
            set_used_amount(amount, key=self._key)
            self._persist()

    def get_used_amount(self) -> float:
        return get_used_amount(self._key)

    def derive(self, key: str) -> "StateRepository":
        return StateRepository(self._storage, key)
