from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Any, Dict

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

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._lock = RLock()
        self._asset = "USDT"
        self._deposit_updated_at: datetime | None = None
        self.load()

    def load(self) -> None:
        with self._lock:
            raw = self._storage.read("state") or {}
            deposit_raw = raw.get("deposit")
            if isinstance(deposit_raw, dict):
                self._asset = str(deposit_raw.get("asset") or "USDT")
                amount = float(deposit_raw.get("amount", 0.0))
                updated_at_raw = deposit_raw.get("updated_at")
                updated_at = (
                    datetime.fromisoformat(updated_at_raw)
                    if isinstance(updated_at_raw, str)
                    else None
                )
                self._deposit_updated_at = updated_at
                set_deposit(amount, self._asset, updated_at)
            else:
                amount = float(raw.get("deposit_usdt", 0.0))
                self._deposit_updated_at = None
                set_deposit(amount, self._asset, None)
            used_raw = raw.get("used_usdt", raw.get("used_amount", 0.0))
            set_used_amount(float(used_raw))

    def _persist(self) -> None:
        payload: Dict[str, Any] = {
            "deposit": {
                "asset": self._asset,
                "amount": get_deposit_amount(),
            },
            "used_usdt": get_used_amount(),
        }
        if self._deposit_updated_at:
            payload["deposit"]["updated_at"] = self._deposit_updated_at.isoformat()
        self._storage.write("state", payload)

    def update_deposit(
        self, amount: float, asset: str = "USDT", updated_at: datetime | None = None
    ) -> None:
        with self._lock:
            self._asset = asset
            self._deposit_updated_at = updated_at or utcnow()
            set_deposit(amount, self._asset, self._deposit_updated_at)
            self._persist()

    def get_deposit(self) -> float:
        return get_deposit_amount()

    def get_deposit_asset(self) -> str:
        return self._asset

    def get_last_deposit_update(self) -> datetime | None:
        return self._deposit_updated_at

    def set_used_amount(self, amount: float) -> None:
        with self._lock:
            set_used_amount(amount)
            self._persist()

    def get_used_amount(self) -> float:
        return get_used_amount()
