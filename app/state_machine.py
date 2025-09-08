"""Finite-state machine orchestrating signal flow and trading."""

from __future__ import annotations

import asyncio
import time

from domain.models.enums import BotState
from domain.models.state import GlobalState, SymbolState
from domain.models.config import ProfileConfig
from domain.services.symbol_registry import SymbolRegistry
from domain.services.signal_engine import SignalEngine
from domain.services.risk_manager import RiskManager
from domain.services.trade_manager import TradeManager
from domain.services.notification import NotificationService
from domain.ports.trader import Trader

from .global_pause_guard import GlobalPauseGuard
from .state_handlers import (
    CooldownHandler,
    EnteredHandler,
    IdleWatchingHandler,
)


class BotStateMachine:
    """Coordinate signals, risk checks and trading actions."""

    def __init__(
        self,
        cfg: ProfileConfig,
        gstate: GlobalState,
        registry: SymbolRegistry,
        signal_engine: SignalEngine,
        risk_manager: RiskManager,
        trade_manager: TradeManager,
        trader: Trader,
        notifier: NotificationService | None = None,
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._pause_guard = GlobalPauseGuard(gstate, registry)
        self._cooldown_handler = CooldownHandler(registry)
        self._entered_handler = EnteredHandler(registry, trade_manager, trader)
        self._idle_watching_handler = IdleWatchingHandler(
            registry, signal_engine, risk_manager, trade_manager
        )
        self._notifier = notifier

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def run(self) -> None:
        while True:
            now_ms = int(time.time() * 1000)
            if self._pause_guard.should_pause(now_ms):
                await asyncio.sleep(0)
                continue

            for symbol in self._registry.all_symbols():
                state = self._registry.get(symbol)
                now = time.time()

                if state.state is BotState.COOLDOWN:
                    self._cooldown_handler.handle(symbol, state, now)
                    continue

                if state.state is BotState.ENTERED:
                    await self._entered_handler.handle(symbol, state)
                    continue

                await self._idle_watching_handler.handle(symbol, state)

            await asyncio.sleep(0)


__all__ = ["BotStateMachine"]

