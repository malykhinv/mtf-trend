from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from crypto_screener.domain.models.active_trade import ActiveTrade
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class ActiveTradeKey:
    symbol: str
    timeframe: Timeframe


@dataclass
class ActiveTrades:
    _trades: dict[ActiveTradeKey, ActiveTrade] = field(default_factory=dict)

    def add(self, key: ActiveTradeKey, trade: ActiveTrade) -> None:
        self._trades[key] = trade

    def get(self, key: ActiveTradeKey) -> Optional[ActiveTrade]:
        return self._trades.get(key)

    def remove(self, key: ActiveTradeKey) -> Optional[ActiveTrade]:
        return self._trades.pop(key, None)

    def has(self, key: ActiveTradeKey) -> bool:
        return key in self._trades

    def items(self) -> Iterable[tuple[ActiveTradeKey, ActiveTrade]]:
        return self._trades.items()


# Backward compatibility
ActiveTradeRegistry = ActiveTrades
