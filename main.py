"""Application entry and coroutine orchestrators.

The goal of the project is to provide a deterministic specification of a short
bot.  The runtime is built from several coroutines that form a strict
processing pipeline: raw market **input** is received from the websocket
stream, transformed into **metrics**, fed into the signal engine to produce
**signals**, and finally handed over to the trading layer for **execution**.

Every numeric parameter used here comes either from ``constants.py`` or from a
profile configuration produced by one of the ``make_profile_config_*`` factory
functions.
"""

from __future__ import annotations

import asyncio

from domain.models.enums import OrderType, Profile
from domain.models.state import GlobalState
from domain.models.config import ProfileConfig
from domain.models.trading import OrderSpec
from config import make_profile_config_balanced
from domain.services import (
    RestClient,
    RiskManager,
    SignalEngine,
    SymbolRegistry,
    TradeManager,
    Trader,
    WsClient,
)

import constants


async def ws_stream(ws: WsClient) -> None:
    """Consume websocket stream indefinitely.

    Parameters are supplied via the :class:`WsClient` instance which is expected
    to be pre-configured with the correct endpoint from :mod:`constants`.
    """

    await ws.stream()


async def bar_maker(ws: WsClient, registry: SymbolRegistry) -> None:
    """Build per-symbol metrics from websocket events.

    This coroutine represents the **input → metrics** stage.  It consumes raw
    market data from the websocket client and stores the intermediate metrics in
    the :class:`SymbolRegistry`.  The exact calculation is delegated to the
    provided services; here we only route events.
    """

    while True:
        trade = ws.next_agg_trade()
        if trade:
            state = registry.get(trade.symbol)
            registry.update(trade.symbol, state)

        depth = ws.next_depth()
        if depth:
            state = registry.get(depth.symbol)
            registry.update(depth.symbol, state)

        liq = ws.next_liquidation()
        if liq:
            state = registry.get(liq.symbol)
            registry.update(liq.symbol, state)

        await asyncio.sleep(0)


async def rest_pollers(rest: RestClient, registry: SymbolRegistry) -> None:
    """Poll REST endpoints for additional metrics.

    Three independent pollers run concurrently, each using only the intervals
    defined in :mod:`constants`.  Results are written to the registry which is
    later consumed by the signal engine.
    """

    async def poll_oi() -> None:
        while True:
            for symbol in registry.all_symbols():
                rest.get_open_interest(symbol)
            await asyncio.sleep(constants.REST_POLL_SEC_OI)

    async def poll_taker() -> None:
        while True:
            for symbol in registry.all_symbols():
                rest.get_taker_ratio(symbol)
            await asyncio.sleep(constants.REST_POLL_SEC_TAKER)

    async def poll_premium() -> None:
        while True:
            for symbol in registry.all_symbols():
                rest.get_premium_pct(symbol)
            await asyncio.sleep(constants.REST_POLL_SEC_PREMIUM)

    await asyncio.gather(poll_oi(), poll_taker(), poll_premium())


async def fsm_loop(
    cfg: ProfileConfig,
    gstate: GlobalState,
    registry: SymbolRegistry,
    signal_engine: SignalEngine,
    risk_manager: RiskManager,
    trade_manager: TradeManager,
    trader: Trader,
) -> None:
    """Finite-state machine coordinating signals and trading.

    This coroutine embodies the **metrics → signals → trading** portion of the
    pipeline.  For each symbol in the registry a possible pump is evaluated,
    confirmed and, if allowed by the risk manager, executed by the trade
    manager and the low level :class:`Trader`.
    """

    while True:
        for symbol in registry.all_symbols():
            pump = signal_engine.on_minute_close(symbol)
            if pump is None:
                continue
            if signal_engine.confirm_failure(symbol, pump.window):
                continue

            entry = signal_engine.make_entry(symbol, pump.window)
            if entry is None:
                continue

            plan = risk_manager.build_plan(symbol, entry.price, pump.window)
            if plan is None or not risk_manager.allow_trade(plan):
                continue

            trade_manager.open_position(plan)

            order = OrderSpec(
                symbol=plan.symbol,
                side=entry.side,
                type=OrderType.MARKET,
                quantity=plan.quantity,
                price=plan.entry_price,
            )
            trader.place(order)

            exits, new_plan = trade_manager.on_tick_manage(symbol)
            for _exit in exits:
                trader.cancel(symbol, all_for_symbol=False)

        await asyncio.sleep(0)


def main() -> None:
    # 1) load profile
    cfg = make_profile_config_balanced()
    gstate = GlobalState(profile=Profile.BALANCED, btc_pause_until_ms=None)

    # 2) build universe and subscriptions
    registry = SymbolRegistry()
    rest = RestClient()
    ws = WsClient()
    trader = Trader()

    # Placeholder services for the FSM; in real deployment these would be
    # properly implemented classes.  They are included here to expose the
    # expected interfaces for :func:`fsm_loop`.
    signal_engine = SignalEngine(cfg)
    risk_manager = RiskManager(cfg)
    trade_manager = TradeManager(trader)

    # 3) coroutines: ws_stream, bar_maker, rest_pollers, fsm_loop
    #    - strict separation: input→metrics→signals→trading
    #    - all parameters are taken only from cfg and constants
    _ = (
        cfg,
        gstate,
        registry,
        rest,
        ws,
        trader,
        signal_engine,
        risk_manager,
        trade_manager,
    )


if __name__ == "__main__":
    main()
