from __future__ import annotations

import logging

from domain.models.enums import BotState
from domain.models.state import SymbolState
from domain.services.signal_engine import SignalEngine
from domain.services.risk_manager import RiskManager
from domain.services.trade_manager import TradeManager
from domain.services.symbol_registry import SymbolRegistry
from domain.ports.ws_client import WsClient

logger = logging.getLogger(__name__)


class IdleWatchingHandler:
    """Handle IDLE/WATCHING states: detect pumps and enter positions."""

    def __init__(
        self,
        registry: SymbolRegistry,
        signal_engine: SignalEngine,
        risk_manager: RiskManager,
        trade_manager: TradeManager,
        ws: WsClient,
    ) -> None:
        self._registry = registry
        self._signal_engine = signal_engine
        self._risk_manager = risk_manager
        self._trade_manager = trade_manager
        self._ws = ws

    async def handle(self, symbol: str, state: SymbolState) -> None:
        pump = self._signal_engine.on_minute_close(symbol)
        if pump is None:
            reason = "no pump window"
            if state.state is BotState.WATCHING:
                self._ws.remove_detail_streams((symbol,))
                state.state = BotState.IDLE
                self._registry.update(symbol, state)
                logger.debug("%s -> IDLE: %s", symbol, reason)
            else:
                logger.debug("%s skipped: %s", symbol, reason)
            return

        if state.state is not BotState.WATCHING:
            self._ws.add_detail_streams((symbol,))
        state.state = BotState.WATCHING
        self._registry.update(symbol, state)
        logger.info(f"{symbol}: обнаружена аномалия, ждём подтверждения")

        if self._signal_engine.confirm_failure(symbol):
            logger.info(f"{symbol}: сигнал отклонён (см. причину выше)")
            self._ws.remove_detail_streams((symbol,))
            state.state = BotState.IDLE
            self._registry.update(symbol, state)
            logger.debug("%s -> IDLE: confirmation failure", symbol)
            return

        entry = self._signal_engine.make_entry(symbol, pump.window)
        if entry is None:
            logger.info(f"{symbol}: сигнал отклонён — условия входа")
            return

        plan = await self._risk_manager.build_plan(
            symbol, entry.price, pump.window, entry.side
        )
        if plan is None or not self._risk_manager.allow_trade(plan):
            logger.info(f"{symbol}: сигнал отклонён — риски превышены")
            return

        logger.info(f"{symbol}: сигнал подтверждён, открываем позицию")
        await self._trade_manager.open_position(plan, entry.side)
        self._ws.remove_detail_streams((symbol,))
        state.state = BotState.ENTERED
        self._registry.update(symbol, state)
        logger.debug("%s -> ENTERED", symbol)


__all__ = ["IdleWatchingHandler"]
