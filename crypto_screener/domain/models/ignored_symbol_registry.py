from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from crypto_screener.config import ppo_cfg
from crypto_screener.domain.models.allowed_trade_registry import AllowedTradeKey
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.ignored_symbol import IgnoredSymbol
from crypto_screener.utils.time import ensure_utc, utc_now


@dataclass
class IgnoredSymbolsStorage:
    _ignored_symbols: dict[AllowedTradeKey, IgnoredSymbol] = field(default_factory=dict)

    def add(self, key: AllowedTradeKey, ignored_symbol: IgnoredSymbol) -> bool:
        replaced = key in self._ignored_symbols
        self._ignored_symbols[key] = ignored_symbol
        return replaced

    def get(self, key: AllowedTradeKey) -> Optional[IgnoredSymbol]:
        return self._ignored_symbols.get(key)

    def remove(self, key: AllowedTradeKey) -> Optional[IgnoredSymbol]:
        return self._ignored_symbols.pop(key, None)

    # noinspection DuplicatedCode
    def has(
            self,
            key: AllowedTradeKey,
            now: Optional[datetime] = None,
    ) -> tuple[bool, Optional[IgnoredSymbol]]:
        ignored_symbol = self._ignored_symbols.get(key)
        if not ignored_symbol:
            return False, None
        check_time = ensure_utc(now) if now else utc_now()
        if check_time >= ignored_symbol.deadline:
            expired = self._ignored_symbols.pop(key)
            return False, expired
        return True, ignored_symbol

    def pop_expired(self, now: datetime) -> list[tuple[AllowedTradeKey, IgnoredSymbol]]:
        check_time = ensure_utc(now)
        expired: list[tuple[AllowedTradeKey, IgnoredSymbol]] = []
        for key, ignored_symbol in list(self._ignored_symbols.items()):
            if check_time >= ignored_symbol.deadline:
                expired.append((key, self._ignored_symbols.pop(key)))
        return expired

    def clear(self) -> None:
        self._ignored_symbols.clear()

    def is_empty(self) -> bool:
        return not self._ignored_symbols


@dataclass
class IgnoredSymbolRegistry:
    _storage: IgnoredSymbolsStorage = field(default_factory=IgnoredSymbolsStorage)

    @staticmethod
    def _build_ignored_symbol(
            key: AllowedTradeKey,
            message_id: str,
            context: Context,
            issued_at: Optional[datetime] = None,
    ) -> IgnoredSymbol:
        issued_at = ensure_utc(issued_at) if issued_at else utc_now()
        deadline = issued_at + timedelta(minutes=ppo_cfg.CAPTURE_TIMEOUT_MULTIPLIER * key.timeframe.minutes)
        return IgnoredSymbol(
            symbol=key.symbol,
            timeframe=key.timeframe,
            message_id=message_id,
            context=context,
            issued_at=issued_at,
            deadline=deadline,
        )

    def add(self, key: AllowedTradeKey, message_id: str, context: Context) -> tuple[IgnoredSymbol, bool]:
        ignored_symbol = self._build_ignored_symbol(key=key, message_id=message_id, context=context)
        replaced = self._storage.add(key, ignored_symbol)
        return ignored_symbol, replaced

    def get(self, key: AllowedTradeKey) -> Optional[IgnoredSymbol]:
        return self._storage.get(key)

    def remove(self, key: AllowedTradeKey) -> Optional[IgnoredSymbol]:
        return self._storage.remove(key)

    def has(self, key: AllowedTradeKey, now: Optional[datetime] = None) -> tuple[bool, Optional[IgnoredSymbol]]:
        return self._storage.has(key, now)

    def pop_expired(self, now: datetime) -> list[tuple[AllowedTradeKey, IgnoredSymbol]]:
        return self._storage.pop_expired(now)

    def clear(self) -> None:
        self._storage.clear()

    def is_empty(self) -> bool:
        return self._storage.is_empty()
