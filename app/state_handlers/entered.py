from __future__ import annotations

import time

from domain.models.enums import BotState
from domain.models.state import SymbolState
from domain.services.symbol_registry import SymbolRegistry
from domain.services.trade_manager import TradeManager
from domain.ports.trader import Trader


class EnteredHandler:
    """Manage positions while in ENTERED state."""

    def __init__(
        self,
        registry: SymbolRegistry,
        trade_manager: TradeManager,
        trader: Trader,
    ) -> None:
        self._registry = registry
        self._trade_manager = trade_manager
        self._trader = trader

    async def handle(self, symbol: str, state: SymbolState) -> None:
        price = state.metrics.last_price
        if price <= 0.0:
            return
        exits, new_plan = await self._trade_manager.on_tick_manage(symbol, price=price)
        for _exit in exits:
            await self._trader.cancel(symbol, order_id=None)
        if new_plan is None:
            state.state = BotState.COOLDOWN
            state.last_signal_ts = time.time()
            self._registry.update(symbol, state)


__all__ = ["EnteredHandler"]
