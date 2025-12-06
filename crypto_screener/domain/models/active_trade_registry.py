from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from crypto_screener.domain.models.active_trade import ActiveTrade
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class ActiveTradeKey:
    symbol: str
    timeframe: Timeframe


@dataclass
class ActiveTradeRegistry:
    trades: dict[ActiveTradeKey, ActiveTrade] = field(default_factory=dict)

    def get(self, key: ActiveTradeKey) -> Optional[ActiveTrade]:
        return self.trades.get(key)

    def set(self, key: ActiveTradeKey, trade: ActiveTrade) -> None:
        self.trades[key] = trade

    def pop(self, key: ActiveTradeKey) -> Optional[ActiveTrade]:
        return self.trades.pop(key, None)

    def has(self, key: ActiveTradeKey) -> bool:
        return key in self.trades
