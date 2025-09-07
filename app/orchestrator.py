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
from .universe import UniverseBuilder


def run(cfg: ProfileConfig) -> None:
    gstate = GlobalState(profile=cfg.profile, btc_pause_until_ms=None)

    registry = SymbolRegistry()
    rest = RestClient()

    builder = UniverseBuilder(rest)
    symbols = builder.build()
    for sym in symbols:
        registry.put(SymbolState(symbol=sym, state=BotState.IDLE))

    ws = WsClient()
    ws.subscribe_symbols(tuple(symbols))
    trader = Trader()

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
