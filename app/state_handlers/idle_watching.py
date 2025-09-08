from __future__ import annotations

from domain.models.enums import BotState
from domain.models.state import SymbolState
from domain.services.signal_engine import SignalEngine
from domain.services.risk_manager import RiskManager
from domain.services.trade_manager import TradeManager
from domain.services.symbol_registry import SymbolRegistry


class IdleWatchingHandler:
    """Handle IDLE/WATCHING states: detect pumps and enter positions."""

    def __init__(
        self,
        registry: SymbolRegistry,
        signal_engine: SignalEngine,
        risk_manager: RiskManager,
        trade_manager: TradeManager,
    ) -> None:
        self._registry = registry
        self._signal_engine = signal_engine
        self._risk_manager = risk_manager
        self._trade_manager = trade_manager

    async def handle(self, symbol: str, state: SymbolState) -> None:
        pump = self._signal_engine.on_minute_close(symbol)
        if pump is None:
            if state.state is BotState.WATCHING:
                state.state = BotState.IDLE
                self._registry.update(symbol, state)
            return

        state.state = BotState.WATCHING
        self._registry.update(symbol, state)

        if self._signal_engine.confirm_failure(symbol, pump.window):
            state.state = BotState.IDLE
            self._registry.update(symbol, state)
            return

        entry = self._signal_engine.make_entry(symbol, pump.window)
        if entry is None:
            return

        plan = await self._risk_manager.build_plan(symbol, entry.price, pump.window)
        if plan is None or not self._risk_manager.allow_trade(plan):
            return

        await self._trade_manager.open_position(plan, entry.side)
        state.state = BotState.ENTERED
        self._registry.update(symbol, state)


__all__ = ["IdleWatchingHandler"]
