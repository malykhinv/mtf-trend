from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from crypto_screener.domain.models.active_trade import ActiveTrade

if TYPE_CHECKING:  # pragma: no cover
    from crypto_screener.domain.models.active_trade_registry import ActiveTradeKey


@dataclass
class ActiveTradesStorage:
    _trades: dict["ActiveTradeKey", ActiveTrade] = field(default_factory=dict)

    def add(self, key: "ActiveTradeKey", trade: ActiveTrade) -> None:
        self._trades[key] = trade

    def get(self, key: "ActiveTradeKey") -> Optional[ActiveTrade]:
        return self._trades.get(key)

    def remove(self, key: "ActiveTradeKey") -> Optional[ActiveTrade]:
        return self._trades.pop(key, None)

    def has(self, key: "ActiveTradeKey") -> bool:
        return key in self._trades

    def items(self) -> tuple[tuple["ActiveTradeKey", ActiveTrade], ...]:
        return tuple((key, trade) for key, trade in self._trades.items())
