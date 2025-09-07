import asyncio
import time

from domain.models.enums import BotState
from domain.models.state import GlobalState
from domain.models.config import ProfileConfig
from domain.services.symbol_registry import SymbolRegistry
from domain.services.signal_engine import SignalEngine
from domain.services.risk_manager import RiskManager
from domain.services.trade_manager import TradeManager
from domain.services.trader import Trader
import constants


async def fsm_loop(
    cfg: ProfileConfig,
    gstate: GlobalState,
    registry: SymbolRegistry,
    signal_engine: SignalEngine,
    risk_manager: RiskManager,
    trade_manager: TradeManager,
    trader: Trader,
) -> None:
    """Finite-state machine coordinating signals and trading."""

    while True:
        now_ms = int(time.time() * 1000)

        # --------------------------------------------------------------
        # Global BTC pause – skip processing when active or trigger pause
        pause_until = gstate.btc_pause_until_ms
        if pause_until is not None:
            if now_ms < pause_until:
                await asyncio.sleep(0)
                continue
            gstate.btc_pause_until_ms = None

        if registry.has("BTCUSDT"):
            btc_state = registry.get("BTCUSDT")
            if btc_state.metrics.z_px > constants.GLOBAL_BTC_PAUSE_Z:
                gstate.btc_pause_until_ms = (
                    now_ms + constants.GLOBAL_BTC_PAUSE_SEC * 1000
                )
                await asyncio.sleep(0)
                continue

        for symbol in registry.all_symbols():
            state = registry.get(symbol)
            now = time.time()

            # -------------------------------------------------- cooldown
            if state.state is BotState.COOLDOWN:
                if (
                    state.last_signal_ts is not None
                    and now - state.last_signal_ts >= constants.COOLDOWN_AFTER_TRADE_SEC
                ):
                    state.state = BotState.IDLE
                    state.last_signal_ts = None
                    registry.update(symbol, state)
                continue

            # -------------------------------------------------- in position
            if state.state is BotState.ENTERED:
                price = state.metrics.last_price
                if price <= 0.0:
                    continue
                exits, new_plan = trade_manager.on_tick_manage(symbol, price=price)
                for _exit in exits:
                    trader.cancel(symbol, order_id=None)
                if new_plan is None:
                    state.state = BotState.COOLDOWN
                    state.last_signal_ts = now
                    registry.update(symbol, state)
                continue

            # -------------------------------------------------- idle/watching
            pump = signal_engine.on_minute_close(symbol)
            if pump is None:
                if state.state is BotState.WATCHING:
                    state.state = BotState.IDLE
                    registry.update(symbol, state)
                continue

            state.state = BotState.WATCHING
            registry.update(symbol, state)

            if signal_engine.confirm_failure(symbol, pump.window):
                state.state = BotState.IDLE
                registry.update(symbol, state)
                continue

            entry = signal_engine.make_entry(symbol, pump.window)
            if entry is None:
                continue

            plan = risk_manager.build_plan(symbol, entry.price, pump.window)
            if plan is None or not risk_manager.allow_trade(plan):
                continue

            trade_manager.open_position(plan, entry.side)

            state.state = BotState.ENTERED
            registry.update(symbol, state)

        await asyncio.sleep(0)
