import asyncio
import time
from typing import Awaitable, Callable

from domain.models.enums import BotState
from domain.models.state import GlobalState
from domain.models.config import ProfileConfig
from domain.services.symbol_registry import SymbolRegistry
from domain.services.signal_engine import SignalEngine
from domain.services.risk_manager import RiskManager
from domain.services.trade_manager import TradeManager
from domain.services.trader import Trader
import constants


class BotStateMachine:
    """Finite-state machine coordinating signals and trading."""

    def __init__(
        self,
        cfg: ProfileConfig,
        gstate: GlobalState,
        registry: SymbolRegistry,
        signal_engine: SignalEngine,
        risk_manager: RiskManager,
        trade_manager: TradeManager,
        trader: Trader,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.gstate = gstate
        self.registry = registry
        self.signal_engine = signal_engine
        self.risk_manager = risk_manager
        self.trade_manager = trade_manager
        self.trader = trader
        self._time = time_fn or time.time
        self._sleep = sleep_fn or asyncio.sleep

    # ------------------------------------------------------------------
    # state handlers
    def _handle_cooldown(self, symbol: str, state) -> bool:
        now = self._time()
        if state.last_signal_ts is not None and now - state.last_signal_ts >= constants.COOLDOWN_AFTER_TRADE_SEC:
            state.state = BotState.IDLE
            state.last_signal_ts = None
            self.registry.update(symbol, state)
        return True

    def _handle_entered(self, symbol: str, state) -> bool:
        price = state.metrics.last_price
        if price <= 0.0:
            return True
        exits, new_plan = self.trade_manager.on_tick_manage(symbol, price=price)
        for _exit in exits:
            self.trader.cancel(symbol, order_id=None)
        if new_plan is None:
            now = self._time()
            state.state = BotState.COOLDOWN
            state.last_signal_ts = now
            self.registry.update(symbol, state)
        return True

    def _handle_idle_or_watching(self, symbol: str, state) -> bool:
        pump = self.signal_engine.on_minute_close(symbol)
        if pump is None:
            if state.state is BotState.WATCHING:
                state.state = BotState.IDLE
                self.registry.update(symbol, state)
            return True

        state.state = BotState.WATCHING
        self.registry.update(symbol, state)

        if self.signal_engine.confirm_failure(symbol, pump.window):
            state.state = BotState.IDLE
            self.registry.update(symbol, state)
            return True

        entry = self.signal_engine.make_entry(symbol, pump.window)
        if entry is None:
            return True

        plan = self.risk_manager.build_plan(symbol, entry.price, pump.window)
        if plan is None or not self.risk_manager.allow_trade(plan):
            return True

        self.trade_manager.open_position(plan, entry.side)
        state.state = BotState.ENTERED
        self.registry.update(symbol, state)
        return True

    # ------------------------------------------------------------------
    def _check_global_pause(self, now_ms: int) -> bool:
        pause_until = self.gstate.btc_pause_until_ms
        if pause_until is not None:
            if now_ms < pause_until:
                return True
            self.gstate.btc_pause_until_ms = None

        if self.registry.has("BTCUSDT"):
            btc_state = self.registry.get("BTCUSDT")
            if btc_state.metrics.z_px > constants.GLOBAL_BTC_PAUSE_Z:
                self.gstate.btc_pause_until_ms = now_ms + constants.GLOBAL_BTC_PAUSE_SEC * 1000
                return True

        return False

    # ------------------------------------------------------------------
    async def run(self) -> None:
        while True:
            now_ms = int(self._time() * 1000)
            if self._check_global_pause(now_ms):
                await self._sleep(0)
                continue

            for symbol in self.registry.all_symbols():
                state = self.registry.get(symbol)

                if state.state is BotState.COOLDOWN:
                    self._handle_cooldown(symbol, state)
                    continue

                if state.state is BotState.ENTERED:
                    self._handle_entered(symbol, state)
                    continue

                self._handle_idle_or_watching(symbol, state)

            await self._sleep(0)


async def fsm_loop(
    cfg: ProfileConfig,
    gstate: GlobalState,
    registry: SymbolRegistry,
    signal_engine: SignalEngine,
    risk_manager: RiskManager,
    trade_manager: TradeManager,
    trader: Trader,
) -> None:
    machine = BotStateMachine(
        cfg,
        gstate,
        registry,
        signal_engine,
        risk_manager,
        trade_manager,
        trader,
    )
    await machine.run()
