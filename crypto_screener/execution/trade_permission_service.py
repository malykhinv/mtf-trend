from __future__ import annotations

from typing import Dict, Tuple

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.allowed_trade import AllowedTrade
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.logger import log

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
        trade = AllowedTrade(symbol=symbol, timeframe=timeframe, message_id=message_id, context=context)
        if key in self._allowed_trades:
            self._allowed_trades[key] = trade
            log.d(
                f"Обновлено разрешение на торговлю {symbol} на {timeframe.tf} без повторного уведомления."
            )
            return
        self._allowed_trades[key] = trade
        log.i(f"Разрешена торговля {symbol} на {timeframe.tf} по нажатию кнопки.")

    def clear_allowance(self, symbol: str, timeframe: Timeframe) -> None:
        removed = self._allowed_trades.pop((symbol, timeframe), None)
        if removed:
            log.d(f"Удалено разрешение на торговлю {symbol} на {timeframe.tf}.")

    def clear_all(self) -> None:
        if not self._allowed_trades:
            return
        self._allowed_trades.clear()
        log.d("Сброшены все разрешения на торговлю.")
