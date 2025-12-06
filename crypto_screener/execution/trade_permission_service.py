from __future__ import annotations

from datetime import datetime
from typing import Optional

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.allowed_trade import AllowedTrade
from crypto_screener.domain.models.allowed_trade_registry import AllowedTradeKey, AllowedTradeRegistry
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.logger import log

class TradePermissionService:
    def __init__(self) -> None:
        self._allowed_trades = AllowedTradeRegistry()

    def allow_symbol_for_trading(
            self,
            symbol: str,
            timeframe: Timeframe,
            message_id: str,
            context: Context,
    ) -> None:
        trade, replaced = self._allowed_trades.add(
            key=AllowedTradeKey(symbol, timeframe),
            message_id=message_id,
            context=context,
        )
        if replaced:
            log.d(
                f"Обновлено разрешение на торговлю {symbol} на {timeframe.tf} без повторного уведомления."
            )
            return
        log.i(f"Разрешена торговля {symbol} на {timeframe.tf} по нажатию кнопки.")

    def clear_allowance(self, symbol: str, timeframe: Timeframe) -> Optional[AllowedTrade]:
        removed = self._allowed_trades.remove(AllowedTradeKey(symbol, timeframe))
        if removed:
            log.d(
                f"Удалено разрешение на торговлю {symbol} на {timeframe.tf}. "
                f"Выдано: {removed.issued_at.isoformat()}, дедлайн: {removed.deadline.isoformat()}."
            )
        return removed

    def clear_all(self) -> None:
        if self._allowed_trades.is_empty():
            return
        self._allowed_trades.clear()
        log.d("Сброшены все разрешения на торговлю.")

    def has_allowance(self, symbol: str, timeframe: Timeframe) -> bool:
        has_allowance, expired_allowance = self._allowed_trades.has(
            AllowedTradeKey(symbol, timeframe)
        )
        if expired_allowance:
            self._log_expired_allowance(expired_allowance)
            return False
        return has_allowance

    def pop_expired(self, now: datetime) -> list[tuple[AllowedTradeKey, AllowedTrade]]:
        expired = self._allowed_trades.pop_expired(now)
        for _, allowance in expired:
            self._log_expired_allowance(allowance)
        return expired

    def _log_expired_allowance(self, allowance: AllowedTrade) -> None:
        log.i(
            f"Истекло разрешение на торговлю {allowance.symbol} на {allowance.timeframe.tf}. "
            f"Выдано: {allowance.issued_at.isoformat()}, дедлайн: {allowance.deadline.isoformat()}."
        )
