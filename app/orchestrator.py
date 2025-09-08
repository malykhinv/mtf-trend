import asyncio
import signal
from typing import Literal

from domain.models.enums import BotState
from domain.models.state import GlobalState, SymbolState
from domain.models.config import ProfileConfig
from domain.ports.rest_client import RestClient as RestClientPort
from domain.ports.ws_client import WsClient as WsClientPort
from domain.ports.trader import Trader as TraderPort
from domain.services.risk_manager import RiskManager
from domain.services.signal_engine import SignalEngine
from domain.services.symbol_registry import SymbolRegistry
from domain.services.trade_manager import TradeManager
from domain.services.notification import NotificationService, TelegramClient
from infrastructure.binance.rest_client import RestClient
from infrastructure.binance.ws_client import WsClient
from infrastructure.binance.trader import Trader
from config.credentials import TELEGRAM

from .ws import ws_stream
from .metrics import bar_maker
from .pollers import (
    OpenInterestPoller,
    TakerRatioPoller,
    PremiumIndexPoller,
)
from .state_machine import BotStateMachine
from .universe import UniverseBuilder


async def run(
    cfg: ProfileConfig, notification_type: Literal["orders", "events"] = "orders"
) -> None:
    gstate = GlobalState(profile=cfg.profile, btc_pause_until_ms=None)

    registry = SymbolRegistry()
    rest: RestClientPort = RestClient()

    builder = UniverseBuilder(rest)
    symbols = await builder.build()
    for sym in symbols:
        registry.put(SymbolState(symbol=sym, state=BotState.IDLE))

    ws: WsClientPort = WsClient()
    ws.subscribe_symbols(tuple(symbols))
    trader: TraderPort = Trader()

    signal_engine = SignalEngine(cfg, registry)
    risk_manager = RiskManager(cfg)
    token = (
        TELEGRAM.orders_bot_token
        if notification_type == "orders"
        else TELEGRAM.events_bot_token
    )
    notifier = NotificationService(TelegramClient(token, TELEGRAM.chat_id))
    trade_manager = TradeManager(
        trader, risk_manager, registry, notifier if notification_type == "orders" else None
    )

    pollers = [
        OpenInterestPoller(rest, registry),
        TakerRatioPoller(rest, registry),
        PremiumIndexPoller(rest, registry),
    ]
    state_machine = BotStateMachine(
        cfg,
        gstate,
        registry,
        signal_engine,
        risk_manager,
        trade_manager,
        trader,
        notifier if notification_type == "events" else None,
    )

    tasks = [
        asyncio.create_task(ws_stream(ws)),
        asyncio.create_task(bar_maker(ws, registry)),
        *[asyncio.create_task(p.run()) for p in pollers],
        asyncio.create_task(state_machine.run()),
    ]

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        for t in tasks:
            t.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await rest._client.aclose()
        await ws.close()
