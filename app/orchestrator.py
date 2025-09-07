import asyncio

from domain.models.enums import BotState
from domain.models.state import GlobalState, SymbolState
from domain.models.config import ProfileConfig
from domain.services.rest_client import RestClient
from domain.services.risk_manager import RiskManager
from domain.services.signal_engine import SignalEngine
from domain.services.symbol_registry import SymbolRegistry
from domain.services.trade_manager import TradeManager
from domain.services.trader import Trader
from domain.services.ws_client import WsClient
import constants

from .ws import ws_stream
from .metrics import bar_maker
from .rest_pollers import rest_pollers
from .state_machine import fsm_loop


def run(cfg: ProfileConfig) -> None:
    gstate = GlobalState(profile=cfg.profile, btc_pause_until_ms=None)

    registry = SymbolRegistry()
    rest = RestClient()
    ws = WsClient()
    trader = Trader()

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

    signal_engine = SignalEngine(cfg, registry)
    risk_manager = RiskManager(cfg)
    trade_manager = TradeManager(trader, risk_manager)

    async def _run() -> None:
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

    asyncio.run(_run())
