from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from crypto_screener.config import ppo_cfg
from crypto_screener.domain.models.allowed_trade import AllowedTrade
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.time import utc_now


@dataclass(frozen=True)
class AllowedTradeKey:
    symbol: str
    timeframe: Timeframe


@dataclass
class AllowedTradesStorage:
    _allowed_trades: dict[AllowedTradeKey, AllowedTrade] = field(default_factory=dict)

    def add(self, key: AllowedTradeKey, trade: AllowedTrade) -> bool:
        replaced = key in self._allowed_trades
        self._allowed_trades[key] = trade
        return replaced

    def get(self, key: AllowedTradeKey) -> Optional[AllowedTrade]:
        return self._allowed_trades.get(key)

    def remove(self, key: AllowedTradeKey) -> Optional[AllowedTrade]:
        return self._allowed_trades.pop(key, None)

    # noinspection DuplicatedCode
    def has(self, key: AllowedTradeKey, now: Optional[datetime] = None) -> tuple[bool, Optional[AllowedTrade]]:
        allowance = self._allowed_trades.get(key)
        if not allowance:
            return False, None
        check_time = now or utc_now()
        if check_time >= allowance.deadline:
            expired = self._allowed_trades.pop(key)
            return False, expired
        return True, allowance

    def pop_expired(self, now: datetime) -> list[tuple[AllowedTradeKey, AllowedTrade]]:
        expired: list[tuple[AllowedTradeKey, AllowedTrade]] = []
        for key, allowance in list(self._allowed_trades.items()):
            if now >= allowance.deadline:
                expired.append((key, self._allowed_trades.pop(key)))
        return expired

    def clear(self) -> None:
        self._allowed_trades.clear()

    def is_empty(self) -> bool:
        return not self._allowed_trades


@dataclass
class AllowedTradeRegistry:
    _storage: AllowedTradesStorage = field(default_factory=AllowedTradesStorage)

    @staticmethod
    def _build_trade(
            key: AllowedTradeKey,
            message_id: str,
            context: Context,
            issued_at: Optional[datetime] = None,
    ) -> AllowedTrade:
        issued_at = issued_at or utc_now()
        deadline = issued_at + timedelta(minutes=ppo_cfg.CAPTURE_TIMEOUT_MULTIPLIER * key.timeframe.minutes)
        return AllowedTrade(
            symbol=key.symbol,
            timeframe=key.timeframe,
            message_id=message_id,
            context=context,
            issued_at=issued_at,
            deadline=deadline,
        )

    def add(self, key: AllowedTradeKey, message_id: str, context: Context) -> tuple[AllowedTrade, bool]:
        trade = self._build_trade(key=key, message_id=message_id, context=context)
        replaced = self._storage.add(key, trade)
        return trade, replaced

    def get(self, key: AllowedTradeKey) -> Optional[AllowedTrade]:
        return self._storage.get(key)

    def remove(self, key: AllowedTradeKey) -> Optional[AllowedTrade]:
        return self._storage.remove(key)

    def has(self, key: AllowedTradeKey, now: Optional[datetime] = None) -> tuple[bool, Optional[AllowedTrade]]:
        return self._storage.has(key, now)

    def pop_expired(self, now: datetime) -> list[tuple[AllowedTradeKey, AllowedTrade]]:
        return self._storage.pop_expired(now)

    def clear(self) -> None:
        self._storage.clear()

    def is_empty(self) -> bool:
        return self._storage.is_empty()
