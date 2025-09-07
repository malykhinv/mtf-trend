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
from domain.services.trader import Trader
import constants


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
    ) -> None:
        self._cfg = cfg
        self._gstate = gstate
        self._registry = registry
        self._signal_engine = signal_engine
        self._risk_manager = risk_manager
        self._trade_manager = trade_manager
        self._trader = trader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def run(self) -> None:
        while True:
            now_ms = int(time.time() * 1000)
            if self._check_global_pause(now_ms):
                await asyncio.sleep(0)
                continue

            for symbol in self._registry.all_symbols():
                state = self._registry.get(symbol)
                now = time.time()

                if state.state is BotState.COOLDOWN:
                    self._handle_cooldown(symbol, state, now)
                    continue

                if state.state is BotState.ENTERED:
                    self._handle_entered(symbol, state)
                    continue

                self._handle_idle_or_watching(symbol, state)

            await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _check_global_pause(self, now_ms: int) -> bool:
        """Return True if processing should pause due to BTC spike."""
        pause_until = self._gstate.btc_pause_until_ms
        if pause_until is not None:
            if now_ms < pause_until:
                return True
            self._gstate.btc_pause_until_ms = None

        if self._registry.has("BTCUSDT"):
            btc_state = self._registry.get("BTCUSDT")
            if btc_state.metrics.z_px > constants.GLOBAL_BTC_PAUSE_Z:
                self._gstate.btc_pause_until_ms = (
                    now_ms + constants.GLOBAL_BTC_PAUSE_SEC * 1000
                )
                return True
        return False

    def _handle_cooldown(self, symbol: str, state: SymbolState, now: float) -> None:
        if (
            state.last_signal_ts is not None
            and now - state.last_signal_ts >= constants.COOLDOWN_AFTER_TRADE_SEC
        ):
            state.state = BotState.IDLE
            state.last_signal_ts = None
            self._registry.update(symbol, state)

    def _handle_entered(self, symbol: str, state: SymbolState) -> None:
        price = state.metrics.last_price
        if price <= 0.0:
            return
        exits, new_plan = self._trade_manager.on_tick_manage(symbol, price=price)
        for _exit in exits:
            self._trader.cancel(symbol, order_id=None)
        if new_plan is None:
            state.state = BotState.COOLDOWN
            state.last_signal_ts = time.time()
            self._registry.update(symbol, state)

    def _handle_idle_or_watching(self, symbol: str, state: SymbolState) -> None:
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

        plan = self._risk_manager.build_plan(symbol, entry.price, pump.window)
        if plan is None or not self._risk_manager.allow_trade(plan):
            return

        self._trade_manager.open_position(plan, entry.side)
        state.state = BotState.ENTERED
        self._registry.update(symbol, state)


__all__ = ["BotStateMachine"]

