from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from crypto_screener.domain.models.active_trade import ActiveTrade
from crypto_screener.domain.models.active_trades_storage import ActiveTradesStorage
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class ActiveTradeKey:
    symbol: str
    timeframe: Timeframe


@dataclass
class ActiveTrades:
    _storage: ActiveTradesStorage = field(default_factory=ActiveTradesStorage)

    def add(self, key: ActiveTradeKey, trade: ActiveTrade) -> None:
        self._storage.add(key, trade)

    def get(self, key: ActiveTradeKey) -> Optional[ActiveTrade]:
        return self._storage.get(key)

    def remove(self, key: ActiveTradeKey) -> Optional[ActiveTrade]:
        return self._storage.remove(key)

    def has(self, key: ActiveTradeKey) -> bool:
        return self._storage.has(key)

    def items(self) -> tuple[tuple[ActiveTradeKey, ActiveTrade], ...]:
        return self._storage.items()


# Backward compatibility
ActiveTradeRegistry = ActiveTrades
