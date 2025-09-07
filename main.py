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
from collections import deque

from domain.models.enums import BotState, OrderType, Profile
from domain.models.state import GlobalState, SymbolState
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

    The coroutine consumes raw market data from :class:`WsClient` and derives a
    number of lightweight aggregates which are then stored in the
    :class:`SymbolRegistry`.  ``SignalEngine`` later inspects these metrics when
    making decisions.
    """

    # Rolling windows used for z-score calculations are kept per symbol inside
    # the ``metrics`` object stored in ``SymbolState``.  The helper below
    # retrieves such a window or creates a new one when necessary.
    def _window(metrics: dict, name: str) -> deque:
        win = metrics.get(name)
        if win is None:
            win = deque(maxlen=constants.Z_BASE_WINDOW_MIN)
            metrics[name] = win
        return win

    def _zscore(value: float, window: deque) -> float:
        """Return z-score of ``value`` within ``window`` values."""
        if len(window) < 2:
            return 0.0
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / len(window)
        std = var ** 0.5
        return 0.0 if std == 0 else (value - mean) / std

    while True:
        trade = ws.next_agg_trade()
        if trade:
            state = registry.get(trade.symbol)
            metrics = getattr(state, "metrics", {})

            # ------------------------ price & volume z-scores -----------------
            price_win = _window(metrics, "price_win")
            vol_win = _window(metrics, "vol_win")
            price_win.append(trade.price)
            vol_win.append(trade.quantity)
            z_px = _zscore(trade.price, price_win)
            z_vol = _zscore(trade.quantity, vol_win)

            # ----------------------- candle construction ----------------------
            start_ts = metrics.get("start_ts", trade.timestamp)
            # Reset the candle every minute
            if trade.timestamp - start_ts >= 60_000:
                start_ts = trade.timestamp
                metrics["high"] = trade.price
                metrics["low"] = trade.price
            else:
                metrics["high"] = max(metrics.get("high", trade.price), trade.price)
                metrics["low"] = min(metrics.get("low", trade.price), trade.price)
            metrics["start_ts"] = start_ts
            metrics["end_ts"] = trade.timestamp

            high = metrics["high"]
            low = metrics["low"]
            rng = high - low

            mean_price = sum(price_win) / len(price_win)
            var_price = sum((p - mean_price) ** 2 for p in price_win) / len(price_win)
            std_price = var_price ** 0.5
            delta_sigma = rng / std_price if std_price > 0 else 0.0
            delta_abs = (high / low - 1.0) * 100 if low > 0 else 0.0
            close_pos = (trade.price - low) / rng if rng > 0 else 0.0

            metrics.update(
                {
                    "z_px": z_px,
                    "z_vol": z_vol,
                    "delta_price_sigma_mult": delta_sigma,
                    "delta_price_abs_pct": delta_abs,
                    "close_pos": close_pos,
                    "last_price": trade.price,
                }
            )
            state.metrics = metrics
            registry.update(trade.symbol, state)

        depth = ws.next_depth()
        if depth:
            state = registry.get(depth.symbol)
            metrics = getattr(state, "metrics", {})
            if depth.bids and depth.asks:
                best_bid = depth.bids[0][0]
                best_ask = depth.asks[0][0]
                mid = (best_bid + best_ask) / 2.0
                last_price = metrics.get("last_price", mid)
                premium_pct = (
                    (mid - last_price) / last_price * 100.0 if last_price > 0 else 0.0
                )
                metrics.update(
                    {
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "premium_pct": premium_pct,
                    }
                )
            state.metrics = metrics
            registry.update(depth.symbol, state)

        liq = ws.next_liquidation()
        if liq:
            state = registry.get(liq.symbol)
            metrics = getattr(state, "metrics", {})
            liq_win = _window(metrics, "liq_win")
            liq_win.append(liq.quantity)
            liqs_z = _zscore(liq.quantity, liq_win) if len(liq_win) > 1 else 0.0
            metrics.update({"liqs_z": liqs_z, "last_liq_side": liq.side.value})
            state.metrics = metrics
            registry.update(liq.symbol, state)

        await asyncio.sleep(0)


async def rest_pollers(rest: RestClient, registry: SymbolRegistry) -> None:
    """Poll REST endpoints for additional metrics.

    Three independent pollers run concurrently, each using only the intervals
    defined in :mod:`constants`.  Results are written to the registry which is
    later consumed by the signal engine.
    """

    async def poll_oi() -> None:
        """Poll open interest and update delta percentage metric."""

        last_oi: dict[str, float] = {}
        while True:
            for symbol in registry.all_symbols():
                oi = rest.get_open_interest(symbol)
                prev = last_oi.get(symbol)
                delta_pct = ((oi - prev) / prev * 100.0) if prev else 0.0
                last_oi[symbol] = oi

                state = registry.get(symbol)
                metrics = getattr(state, "metrics", {}) or {}
                metrics["delta_oi_pct"] = delta_pct
                state.metrics = metrics
                registry.update(symbol, state)
            await asyncio.sleep(constants.REST_POLL_SEC_OI)

    async def poll_taker() -> None:
        """Poll taker buy/sell volumes and store them in metrics."""

        while True:
            for symbol in registry.all_symbols():
                buy, sell = rest.get_taker_ratio(symbol)
                state = registry.get(symbol)
                metrics = getattr(state, "metrics", {}) or {}
                metrics["taker_buy_volume"] = buy
                metrics["taker_sell_volume"] = sell
                state.metrics = metrics
                registry.update(symbol, state)
            await asyncio.sleep(constants.REST_POLL_SEC_TAKER)

    async def poll_premium() -> None:
        """Poll premium index percentage and update metric."""

        while True:
            for symbol in registry.all_symbols():
                premium = rest.get_premium_pct(symbol)
                state = registry.get(symbol)
                metrics = getattr(state, "metrics", {}) or {}
                metrics["premium_pct"] = premium
                state.metrics = metrics
                registry.update(symbol, state)
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

    # Discover symbols satisfying universe requirements
    symbols: list[str] = []
    r = rest._client.get("/fapi/v1/ticker/24hr")
    r.raise_for_status()
    for info in r.json():
        sym = info["symbol"]
        if not sym.endswith("USDT"):
            continue

        quote_vol, _ = rest.get_24h_stats(sym)
        if quote_vol < constants.UNIVERSE_MIN_24H_USDT:
            continue

        bid = float(info["bidPrice"])
        ask = float(info["askPrice"])
        if bid <= 0:
            continue
        spread_bps = (ask - bid) / bid * 10_000.0
        if spread_bps > constants.UNIVERSE_MAX_SPREAD_BPS:
            continue

        depth = rest._client.get("/fapi/v1/depth", params={"symbol": sym, "limit": 10})
        depth.raise_for_status()
        bids = depth.json().get("bids", [])
        top10_bid_usdt = sum(float(p) * float(q) for p, q in bids)
        if top10_bid_usdt < constants.UNIVERSE_MIN_TOP10_BID_USDT:
            continue

        registry.put(SymbolState(symbol=sym, state=BotState.IDLE))
        symbols.append(sym)

    ws.subscribe_symbols(tuple(symbols))

    # Placeholder services for the FSM; in real deployment these would be
    # properly implemented classes.  They are included here to expose the
    # expected interfaces for :func:`fsm_loop`.
    signal_engine = SignalEngine(cfg, registry)
    risk_manager = RiskManager(cfg)
    trade_manager = TradeManager(trader)

    # 3) coroutines: ws_stream, bar_maker, rest_pollers, fsm_loop
    #    - strict separation: input→metrics→signals→trading
    #    - all parameters are taken only from cfg and constants
    async def run() -> None:
        await asyncio.gather(
            ws_stream(ws),
            bar_maker(ws, registry),
            rest_pollers(rest, registry),
            fsm_loop(
                cfg,
                gstate,
                registry,
                signal_engine,
                risk_manager,
                trade_manager,
                trader,
            ),
        )

    asyncio.run(run())


if __name__ == "__main__":
    main()
