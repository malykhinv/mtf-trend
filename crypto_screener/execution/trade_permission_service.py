from __future__ import annotations

from datetime import datetime
from typing import Optional

from crypto_screener.domain.models.allowed_trade import AllowedTrade
from crypto_screener.domain.models.allowed_trade_registry import AllowedTradeKey, AllowedTradeRegistry
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.ignored_symbol import IgnoredSymbol
from crypto_screener.domain.models.ignored_symbol_registry import IgnoredSymbolRegistry
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.logger import log
from crypto_screener.utils.time import ensure_utc, utc_now


class TradePermissionService:
    def __init__(self) -> None:
        self._allowed_trades = AllowedTradeRegistry()
        self._ignored_symbols = IgnoredSymbolRegistry()

    def allow_symbol_for_trading(
            self,
            symbol: str,
            timeframe: Timeframe,
            strategy: str,
            message_id: str,
            context: Context,
    ) -> None:
        trade, replaced = self._allowed_trades.add(
            key=AllowedTradeKey(symbol, timeframe, strategy),
            message_id=message_id,
            context=context,
        )
        if replaced:
            log.d(
                f"Обновлено разрешение на торговлю {symbol} на {timeframe.tf} "
                f"({strategy}) без повторного уведомления."
            )
            return
        log.i(f"Разрешена торговля {symbol} на {timeframe.tf} ({strategy}) по нажатию кнопки.")

    def ignore_symbol(
            self,
            symbol: str,
            timeframe: Timeframe,
            strategy: str,
            message_id: str,
            context: Context,
    ) -> None:
        ignored_symbol, replaced = self._ignored_symbols.add(
            key=AllowedTradeKey(symbol, timeframe, strategy),
            message_id=message_id,
            context=context,
        )
        if replaced:
            log.d(
                f"Обновлено игнорирование {symbol} на {timeframe.tf} ({strategy}) без повторного уведомления."
            )
            return
        log.i(
            f"Добавлен запрет на анализ {ignored_symbol.symbol} на {ignored_symbol.timeframe.tf} "
            f"({strategy}) по нажатию кнопки."
        )

    def clear_allowance(
            self,
            symbol: str,
            timeframe: Timeframe,
            strategy: str,
    ) -> Optional[AllowedTrade]:
        removed = self._allowed_trades.remove(AllowedTradeKey(symbol, timeframe, strategy))
        if removed:
            log.d(
                f"Удалено разрешение на торговлю {symbol} на {timeframe.tf} ({strategy}). "
                f"Выдано: {removed.issued_at.isoformat()}, дедлайн: {removed.deadline.isoformat()}."
            )
        return removed

    def clear_ignore(
            self,
            symbol: str,
            timeframe: Timeframe,
            strategy: str,
    ) -> Optional[IgnoredSymbol]:
        removed = self._ignored_symbols.remove(AllowedTradeKey(symbol, timeframe, strategy))
        if removed:
            log.d(
                f"Удалено игнорирование {symbol} на {timeframe.tf} ({strategy}). "
                f"Выдано: {removed.issued_at.isoformat()}, дедлайн: {removed.deadline.isoformat()}."
            )
        return removed

    def clear_all(self) -> None:
        if not self._allowed_trades.is_empty():
            self._allowed_trades.clear()
            log.d("Сброшены все разрешения на торговлю.")
        if not self._ignored_symbols.is_empty():
            self._ignored_symbols.clear()
            log.d("Сброшены все правила игнорирования.")

    def has_allowance(
            self,
            symbol: str,
            timeframe: Timeframe,
            strategy: str,
    ) -> bool:
        has_allowance, expired_allowance = self._allowed_trades.has(
            AllowedTradeKey(symbol, timeframe, strategy)
        )
        if expired_allowance:
            self._log_expired_allowance(expired_allowance)
            return False
        return has_allowance

    def has_ignore(
            self,
            symbol: str,
            timeframe: Timeframe,
            strategy: str,
            now: Optional[datetime] = None,
    ) -> bool:
        check_time = ensure_utc(now) if now else utc_now()
        has_ignore, expired_ignore = self._ignored_symbols.has(
            AllowedTradeKey(symbol, timeframe, strategy),
            check_time,
        )
        if expired_ignore:
            self._log_expired_ignore(expired_ignore)
            return False
        return has_ignore

    def pop_expired(
            self,
            now: datetime
    ) -> list[tuple[AllowedTradeKey, AllowedTrade]]:
        expired = self._allowed_trades.pop_expired(now)
        for _, allowance in expired:
            self._log_expired_allowance(allowance)
        return expired

    def pop_expired_ignored(
            self,
            now: Optional[datetime] = None,
    ) -> list[tuple[AllowedTradeKey, IgnoredSymbol]]:
        check_time = ensure_utc(now) if now else utc_now()
        expired = self._ignored_symbols.pop_expired(check_time)
        for _, ignored_symbol in expired:
            self._log_expired_ignore(ignored_symbol)
        return expired

    @staticmethod
    def _log_expired_allowance(allowance: AllowedTrade) -> None:
        log.i(
            f"Истекло разрешение на торговлю {allowance.symbol} на {allowance.timeframe.tf} "
            f"по стратегии {allowance.strategy}. "
            f"Выдано: {allowance.issued_at.isoformat()}, дедлайн: {allowance.deadline.isoformat()}."
        )

    @staticmethod
    def _log_expired_ignore(ignored_symbol: IgnoredSymbol) -> None:
        log.i(
            f"Истек срок игнорирования {ignored_symbol.symbol} на {ignored_symbol.timeframe.tf} "
            f"по стратегии {ignored_symbol.strategy}. "
            f"Выдано: {ignored_symbol.issued_at.isoformat()}, "
            f"дедлайн: {ignored_symbol.deadline.isoformat()}."
        )
