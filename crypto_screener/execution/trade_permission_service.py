from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional

from crypto_screener.config.config import AppConfig as cfg
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.allowed_trade import AllowedTrade
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.logger import log
from crypto_screener.utils.time import utc_now

class TradePermissionService:
    def __init__(self) -> None:
        self._allowed_trades: Dict[Tuple[str, Timeframe], AllowedTrade] = {}

    def allow_symbol_for_trading(
            self,
            symbol: str,
            timeframe: Timeframe,
            message_id: str,
            context: Context,
    ) -> None:
        key = (symbol, timeframe)
        issued_at = utc_now()
        deadline = issued_at + timedelta(minutes=cfg.CAPTURE_TIMEOUT_MULTIPLIER * timeframe.minutes)
        trade = AllowedTrade(
            symbol=symbol,
            timeframe=timeframe,
            message_id=message_id,
            context=context,
            issued_at=issued_at,
            deadline=deadline,
        )
        if key in self._allowed_trades:
            self._allowed_trades[key] = trade
            log.d(
                f"Обновлено разрешение на торговлю {symbol} на {timeframe.tf} без повторного уведомления."
            )
            return
        self._allowed_trades[key] = trade
        log.i(f"Разрешена торговля {symbol} на {timeframe.tf} по нажатию кнопки.")

    def clear_allowance(self, symbol: str, timeframe: Timeframe) -> Optional[AllowedTrade]:
        removed = self._allowed_trades.pop((symbol, timeframe), None)
        if removed:
            log.d(
                f"Удалено разрешение на торговлю {symbol} на {timeframe.tf}. "
                f"Выдано: {removed.issued_at.isoformat()}, дедлайн: {removed.deadline.isoformat()}."
            )
        return removed

    def clear_all(self) -> None:
        if not self._allowed_trades:
            return
        self._allowed_trades.clear()
        log.d("Сброшены все разрешения на торговлю.")

    def has_allowance(self, symbol: str, timeframe: Timeframe) -> bool:
        allowance = self._allowed_trades.get((symbol, timeframe))
        if not allowance:
            return False
        if utc_now() >= allowance.deadline:
            self._expire_allowance((symbol, timeframe), allowance)
            return False
        return True

    def pop_expired(self, now: datetime) -> list[tuple[Tuple[str, Timeframe], AllowedTrade]]:
        expired: list[tuple[Tuple[str, Timeframe], AllowedTrade]] = []
        for key, allowance in list(self._allowed_trades.items()):
            if now >= allowance.deadline:
                self._expire_allowance(key, allowance)
                expired.append((key, allowance))
        return expired

    def _expire_allowance(
            self,
            key: Tuple[str, Timeframe],
            allowance: AllowedTrade,
    ) -> None:
        self._allowed_trades.pop(key, None)
        log.i(
            f"Истекло разрешение на торговлю {allowance.symbol} на {allowance.timeframe.tf}. "
            f"Выдано: {allowance.issued_at.isoformat()}, дедлайн: {allowance.deadline.isoformat()}."
        )
