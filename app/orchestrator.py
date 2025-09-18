import asyncio
import logging
import signal
from pathlib import Path
from typing import Awaitable, Callable, Literal

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
from trade_log import SQLiteTradeLog
from constants import TASK_MAX_RESTARTS, TASK_RESTART_DELAY_SEC

from .ws import ws_stream
from .metrics import bar_maker
from .pollers import (
    OpenInterestPoller,
    TakerRatioPoller,
    PremiumIndexPoller,
)
from .state_machine import BotStateMachine
from .universe import UniverseBuilder

logger = logging.getLogger(__name__)


async def _run_with_restart(
    coro_fn: Callable[[], Awaitable[None]],
    name: str,
    retries: int = TASK_MAX_RESTARTS,
) -> None:
    attempt = 0
    while attempt <= retries:
        try:
            await coro_fn()
            logger.info("%s task finished (attempt %d)", name, attempt + 1)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s task failed (attempt %d)", name, attempt + 1)
            if attempt == retries:
                raise
            await asyncio.sleep(TASK_RESTART_DELAY_SEC)
            logger.info("Restarting %s", name)
            attempt += 1


NotificationType = Literal["orders", "events"]


async def run(
    cfg: ProfileConfig, notification_type: NotificationType = "orders"
) -> None:
    """Run orchestrator with the given configuration.

    Args:
        cfg: Profile configuration.
        notification_type: Type of notifications to emit. Must be either
            ``"orders"`` or ``"events"``.

    Raises:
        ValueError: If ``notification_type`` is not supported.
    """
    if notification_type not in {"orders", "events"}:
        raise ValueError(f"Unsupported notification_type: {notification_type}")

    logger.info("Orchestrator starting with profile %s", cfg.profile)
    gstate = GlobalState(profile=cfg.profile, btc_pause_until_ms=None)

    registry = SymbolRegistry()
    rest: RestClientPort = RestClient()

    logger.info("Building trading universe")
    builder = UniverseBuilder(rest)
    symbols = await builder.build()
    logger.info("Universe built with %d symbols", len(symbols))
    for sym in symbols:
        registry.put(SymbolState(symbol=sym, state=BotState.IDLE))

    ws: WsClientPort = WsClient()
    ws.subscribe_symbols(tuple(symbols))
    logger.info("Subscribed to websocket for %d symbols", len(symbols))
    trader: TraderPort = Trader()

    signal_engine = SignalEngine(cfg, registry)
    risk_manager = RiskManager(cfg, trader)
    await risk_manager.sync_balance()
    token = (
        TELEGRAM.orders_bot_token
        if notification_type == "orders"
        else TELEGRAM.events_bot_token
    )
    notifier = NotificationService(TelegramClient(token, TELEGRAM.chat_id))
    trade_log_repo = SQLiteTradeLog(Path("data/runtime/trades.db"))
    trade_manager = TradeManager(
        trader,
        risk_manager,
        registry,
        notifier if notification_type == "orders" else None,
        trade_log=trade_log_repo,
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
        ws,
        notifier if notification_type == "events" else None,
    )

    tasks: list[asyncio.Task[None]] = []

    loop = asyncio.get_running_loop()

    logger.info("Starting pipeline tasks")

    def _shutdown() -> None:
        for t in tasks:
            t.cancel()

    try:
        if hasattr(asyncio, "TaskGroup"):
            try:
                async with asyncio.TaskGroup() as tg:
                    tasks.append(
                        tg.create_task(
                            _run_with_restart(lambda: ws_stream(ws), "ws_stream")
                        )
                    )
                    tasks.append(
                        tg.create_task(
                            _run_with_restart(
                                lambda: bar_maker(ws, registry), "bar_maker"
                            )
                        )
                    )
                    for p in pollers:
                        tasks.append(
                            tg.create_task(
                                _run_with_restart(p.run, p.__class__.__name__)
                            )
                        )
                    tasks.append(
                        tg.create_task(
                            _run_with_restart(state_machine.run, "state_machine")
                        )
                    )

                    for sig in (signal.SIGINT, signal.SIGTERM):
                        try:
                            loop.add_signal_handler(sig, lambda: _shutdown())
                        except NotImplementedError:
                            pass
            except* Exception as eg:
                for exc in eg.exceptions:
                    logger.exception("Task failed", exc_info=exc)
                raise
        else:
            tasks.extend(
                [
                    asyncio.create_task(
                        _run_with_restart(lambda: ws_stream(ws), "ws_stream")
                    ),
                    asyncio.create_task(
                        _run_with_restart(
                            lambda: bar_maker(ws, registry), "bar_maker"
                        )
                    ),
                    *[
                        asyncio.create_task(
                            _run_with_restart(p.run, p.__class__.__name__)
                        )
                        for p in pollers
                    ],
                    asyncio.create_task(
                        _run_with_restart(state_machine.run, "state_machine")
                    ),
                ]
            )
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, lambda: _shutdown())
                except NotImplementedError:
                    pass
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.exception("Task failed", exc_info=res)
                    for t in tasks:
                        t.cancel()
                    break
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down orchestrator")
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.remove_signal_handler(sig)
            except NotImplementedError:
                pass
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await rest.aclose()
        await ws.close()
        await risk_manager.aclose()
        trade_log_repo.close()
        logger.info("Orchestrator stopped")
