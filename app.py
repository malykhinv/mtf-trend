from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, Iterable, Optional, Sequence, Tuple, cast

from application import FeedMonitor, GUARDS
from application.market_scanner import MarketScanner
from config.config import CONFIG
from config.models.balance_source import BalanceSource as ConfigBalanceSource
from config.models.exchange_name import ExchangeName
from config.models.trading_profile import TradingProfile
from config.secrets import SECRETS
from config.timezone import CURRENT_TIMEZONE
from data.exchanges import (
    BestBidAsk,
    BinanceStreamManager,
    BinanceSymbolStreams,
    BinanceTradingAdapter,
    BybitExchangeData,
    BybitTradingAdapter,
    DepthStreamData,
    IExchangeData,
    ResyncReason as StreamResyncReason,
    StreamEventType,
    StreamSubscription,
    StreamLimitError,
)
from data.exchanges.binance_trade import BinanceAPIError
from data.exchanges.bybit_trade import BybitAPIError
from data.logger import LogSink, create_log_writer, create_text_log_sink
from data.telegram import TelegramClient
from domain.book import OrderBook
from domain.detectors import check_if_has_near_wall, check_if_has_opposite_wall, compute_odr
from domain.execution import create_execution_handlers, initialize_account
from domain.models import (
    BalanceSource,
    Exchange,
    ExecutionReport,
    LogLine,
    MarginMode,
    OrderBookSnapshot,
    OrderBookUpdate,
    Side,
    StopTrigger,
    SymbolFilters,
    Trade,
    Wall,
)
from domain.strategy import (
    EventLogger,
    FocusController,
    KillSwitch,
    MarketObservation,
    PositionController,
    Strategy,
    SubscriptionManager,
    TelegramNotifier,
)
from domain.strategy.resync import ResyncReason as StrategyResyncReason
from domain.strategy.state import StrategyState
from domain.trading_adapter import TradingAdapter
from utils import get_current_time


class VolumeSpikeTracker:
    def __init__(
        self,
        *,
        window: timedelta = timedelta(seconds=30),
        smoothing: float = 0.2,
        min_samples: int = 20,
    ) -> None:
        self._window = window
        self._smoothing = smoothing
        self._min_samples = min_samples
        self._entries: Deque[Tuple[datetime, float]] = deque()
        self._total = 0.0
        self._average = 0.0
        self._samples = 0

    def observe(self, timestamp: datetime, price: float, quantity: float) -> Tuple[float, float, float, bool]:
        volume = abs(price * quantity)
        self._entries.append((timestamp, volume))
        self._total += volume
        self._trim(timestamp)
        current = max(self._total, 0.0)
        baseline = self._average if self._average > 0.0 else current
        ratio = current / baseline if baseline > 0.0 else 0.0
        self._samples += 1
        if self._samples == 1:
            self._average = current
        else:
            alpha = self._smoothing
            self._average = (1.0 - alpha) * self._average + alpha * current
        ready = self._samples >= self._min_samples and self._average > 0.0
        return current, self._average, ratio, ready

    def _trim(self, now: datetime) -> None:
        cutoff = now - self._window
        while self._entries and self._entries[0][0] < cutoff:
            _, volume = self._entries.popleft()
            self._total -= volume
        if self._total < 0.0:
            self._total = 0.0


@dataclass
class SymbolContext:
    symbol: str
    exchange_data: IExchangeData
    order_book: OrderBook
    feed_monitor: FeedMonitor
    depth_subscription: StreamSubscription[DepthStreamData]
    trade_subscription: StreamSubscription[Trade]
    ticker_subscription: StreamSubscription[BestBidAsk]
    filters: SymbolFilters
    trading_adapter: TradingAdapter
    next_funding_at: Optional[datetime] = None
    next_funding_updated_at: Optional[datetime] = None
    funding_block_logged: bool = False
    last_update_id: Optional[int] = None
    last_trade_price: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    order_book_updated_at: Optional[datetime] = None
    trade_updated_at: Optional[datetime] = None
    ticker_updated_at: Optional[datetime] = None
    active: bool = False
    cycle_started: bool = False
    balance: float = 0.0
    balance_updated_at: Optional[datetime] = None
    profile: TradingProfile = CONFIG.general.profile
    volume_tracker: VolumeSpikeTracker = field(default_factory=VolumeSpikeTracker)
    volume_ratio: float = 0.0
    volume_spike: bool = False
    last_depth_silence_recovery_at: Optional[datetime] = None


class TradingAdapterRouter(TradingAdapter):
    def __init__(self, contexts: Dict[str, SymbolContext]) -> None:
        self._contexts = contexts
        self._current_symbol: Optional[str] = None

    def set_current_symbol(self, symbol: str) -> None:
        self._current_symbol = symbol.upper()

    def _resolve(self) -> TradingAdapter:
        if self._current_symbol is None:
            raise RuntimeError("trading symbol is not selected")
        context = self._contexts.get(self._current_symbol)
        if context is None:
            raise RuntimeError(f"unknown trading symbol {self._current_symbol}")
        return context.trading_adapter

    def get_balance(self, source: BalanceSource) -> float:
        return self._resolve().get_balance(source)

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        return self._resolve().set_leverage(leverage, margin_mode)

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        return self._resolve().place_market(side, quantity, reason=reason)

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        self._resolve().place_stop_market(side, stop_price, quantity, trigger)


class Application:
    def __init__(self, sink: LogSink) -> None:
        GUARDS.set_exchange(CONFIG.general.exchange)
        GUARDS.set_profile(CONFIG.general.profile)
        self._sink = sink
        self._log_writer = create_log_writer(sink)
        self._event_logger = EventLogger(write=self._log_writer)
        self._focused_symbol: Optional[str] = None
        self._desired_symbols: Tuple[str, ...] = ()
        self._symbol_profiles: Dict[str, TradingProfile] = {}
        self._symbol_weights: Dict[str, float] = {}
        self._telegram_client = TelegramClient(
            token=SECRETS.tg_bot_token,
            chat_id=CONFIG.telegram.chat_id,
            silent=CONFIG.telegram.silent,
        )
        self._telegram_notifier = TelegramNotifier(send_message=self._send_telegram)
        self._contexts: Dict[str, SymbolContext] = {}
        self._trading_router = TradingAdapterRouter(self._contexts)
        self._balance_source = self._map_balance_source(CONFIG.position.balance_source)
        self._balance_refresh_interval = timedelta(hours=CONFIG.position.balance_refresh_h)
        self._last_balance_refresh_at: Optional[datetime] = None
        self._silence_recovery_cooldown = timedelta(seconds=5)
        self._data_freshness_threshold = timedelta(
            milliseconds=CONFIG.general.ws_silence_timeout_ms
        )
        self._stale_warnings: Dict[str, set[str]] = {}
        self._focus_controller = FocusController(
            focus_symbol=self._focus_symbol,
            defocus_symbol=self._defocus_symbol,
            logger=self._event_logger,
        )
        self._exchange = Exchange(CONFIG.general.exchange.value)
        api_key, api_secret = self._exchange_credentials()
        self._binance_streams: Optional[BinanceStreamManager] = None
        if CONFIG.general.exchange is ExchangeName.BINANCE:
            self._binance_streams = BinanceStreamManager(
                loop_interval_ms=CONFIG.general.loop_interval_ms,
                silence_timeout_ms=CONFIG.general.ws_silence_timeout_ms,
                log_writer=self._sink,
                api_key=api_key,
                api_secret=api_secret,
            )
        prioritizer = (
            self._binance_streams.plan_subscriptions
            if self._binance_streams is not None
            else None
        )
        self._subscription_manager = SubscriptionManager(
            subscribe=self._subscribe_symbol,
            unsubscribe=self._unsubscribe_symbol,
            logger=self._event_logger,
            prioritizer=prioritizer,
        )
        self._scanner = MarketScanner(
            CONFIG.general.exchange,
            CONFIG.general.profile,
            CONFIG.turnover,
            CONFIG.profile_weights,
            log=self._log_scanner_message,
        )
        self._last_scan_at: Optional[datetime] = None
        self._scanner_interval = timedelta(seconds=CONFIG.turnover.market_scan_interval_s)
        self._current_symbol: Optional[str] = None
        entry, exit_, move_stop = create_execution_handlers(
            self._trading_router,
            self._provide_filters,
            self._provide_balance,
        )
        self._position_controller = PositionController(
            enter_position=entry,
            exit_position=exit_,
            move_stop=move_stop,
            logger=self._event_logger,
        )
        ks_settings = CONFIG.funding_ks
        self._kill_switch = KillSwitch(
            logger=self._event_logger,
            funding_block=timedelta(seconds=ks_settings.funding_block_s),
            block_duration=timedelta(minutes=ks_settings.block_min),
            stop_interval=timedelta(minutes=1),
            max_stops=ks_settings.max_stops_per_min,
            max_drawdown=ks_settings.max_drawdown_frac,
            position_fraction=CONFIG.position.position_fraction,
        )
        self._funding_block_window = timedelta(seconds=ks_settings.funding_block_s)
        self._funding_refresh_interval = timedelta(minutes=5)
        self._strategy = Strategy(
            subscriptions=self._subscription_manager,
            focus=self._focus_controller,
            position=self._position_controller,
            notifier=self._telegram_notifier,
            logger=self._event_logger,
            resync=self._handle_resync,
            safety=self._kill_switch,
        )

    @staticmethod
    def _exchange_credentials() -> tuple[str | None, str | None]:
        if CONFIG.general.exchange is ExchangeName.BINANCE:
            return SECRETS.binance_api_key, SECRETS.binance_api_secret
        if CONFIG.general.exchange is ExchangeName.BYBIT:
            return SECRETS.bybit_api_key, SECRETS.bybit_api_secret
        return None, None

    def _create_exchange_data(self, symbol: str) -> IExchangeData:
        if CONFIG.general.exchange is ExchangeName.BINANCE:
            if self._binance_streams is None:
                raise RuntimeError("Binance stream manager is not initialized")
            return self._binance_streams.get_exchange_data(symbol)
        if CONFIG.general.exchange is ExchangeName.BYBIT:
            api_key, api_secret = self._exchange_credentials()
            return BybitExchangeData(
                symbol=symbol,
                loop_interval_ms=CONFIG.general.loop_interval_ms,
                silence_timeout_ms=CONFIG.general.ws_silence_timeout_ms,
                log_writer=self._sink,
                api_key=api_key,
                api_secret=api_secret,
            )
        raise RuntimeError("unsupported exchange")

    def _provide_filters(self, symbol: str) -> SymbolFilters:
        symbol = symbol.upper()
        context = self._contexts.get(symbol)
        if context is None:
            raise ValueError(f"unknown symbol {symbol}")
        self._trading_router.set_current_symbol(symbol)
        return context.filters

    @staticmethod
    def _map_balance_source(source: ConfigBalanceSource) -> BalanceSource:
        if source is ConfigBalanceSource.AVAILABLE_BALANCE:
            return BalanceSource.AVAILABLE
        if source is ConfigBalanceSource.WALLET_BALANCE:
            return BalanceSource.WALLET
        raise ValueError("unsupported balance source")

    def _provide_balance(self, symbol: str) -> float:
        symbol = symbol.upper()
        context = self._contexts.get(symbol)
        if context is None:
            raise ValueError(f"unknown symbol {symbol}")
        return context.balance

    def _subscribe_symbol(self, symbol: str) -> None:
        symbol = symbol.upper()
        context = self._contexts.get(symbol)
        new_context: Optional[SymbolContext] = None
        manager_streams: Optional[BinanceSymbolStreams] = None
        manager = self._binance_streams
        if manager is not None:
            profile = self._symbol_profiles.get(symbol, CONFIG.general.profile)
            weight = self._symbol_weights.get(symbol)
            if weight is None:
                weight = self._scanner.get_symbol_weight(symbol)
            manager.update_profiles({symbol: profile})
            manager.update_weights({symbol: weight})
        try:
            if manager is not None:
                manager_streams = manager.subscribe(symbol)
            if context is None:
                new_context = self._create_context(symbol, streams=manager_streams)
                context = new_context
            if context is None:
                return
            context.active = True
            context.cycle_started = False
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"Не удалось активировать {symbol}: {exc}")
            if manager is not None and manager_streams is not None:
                try:
                    manager.unsubscribe(symbol)
                except StreamLimitError:
                    manager.cancel(symbol)
            if new_context is not None:
                self._dispose_context(new_context)
            elif context is not None:
                self._dispose_context(context)
            raise
        if new_context is not None:
            self._contexts[symbol] = new_context

    @staticmethod
    def _stop_stream_subscription(subscription: StreamSubscription[Any]) -> None:
        try:
            subscription.stop()
        except Exception:
            pass

    def _resubscribe_symbol_streams(self, context: SymbolContext) -> None:
        manager = self._binance_streams
        if manager is None:
            return
        try:
            streams = manager.resubscribe(context.symbol)
        except StreamLimitError as exc:
            timestamp = get_current_time()
            self._event_logger.log(
                f"Не удалось переподписать {context.symbol}: {exc}",
                timestamp,
            )
            return
        subscriptions = (
            context.depth_subscription,
            context.trade_subscription,
            context.ticker_subscription,
        )
        for subscription in subscriptions:
            self._stop_stream_subscription(subscription)
        context.depth_subscription = streams.depth
        context.trade_subscription = streams.trades
        context.ticker_subscription = streams.ticker
        context.exchange_data = streams.exchange_data

    def _dispose_context(self, context: SymbolContext) -> None:
        context.active = False
        subscriptions = (
            context.depth_subscription,
            context.trade_subscription,
            context.ticker_subscription,
        )
        for subscription in subscriptions:
            self._stop_stream_subscription(subscription)
        if self._focus_controller.current == context.symbol:
            timestamp = get_current_time()
            self._focus_controller.defocus(timestamp)

    def _unsubscribe_symbol(self, symbol: str) -> None:
        symbol = symbol.upper()
        context = self._contexts.get(symbol)
        if context is None:
            return
        manager = self._binance_streams
        if manager is not None:
            try:
                manager.unsubscribe(symbol)
            except StreamLimitError as exc:
                timestamp = get_current_time()
                self._event_logger.log(
                    f"Не удалось отписаться от {symbol}: {exc}",
                    timestamp,
                )
                raise
        context = self._contexts.pop(symbol)
        self._stale_warnings.pop(symbol, None)
        self._dispose_context(context)

    def _focus_symbol(self, symbol: str) -> None:
        symbol = symbol.upper()
        if symbol not in self._contexts:
            raise ValueError("focus symbol mismatch")
        self._focused_symbol = symbol

    def _defocus_symbol(self) -> None:
        self._focused_symbol = None

    def _send_telegram(self, message: str) -> None:
        self._telegram_client.send_message(message)

    def _handle_resync(self, reason: StrategyResyncReason) -> None:
        symbol = self._current_symbol
        if symbol is None:
            return
        context = self._contexts.get(symbol)
        if context is None:
            return
        try:
            snapshot = context.exchange_data.fetch_orderbook_snapshot()
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"Не удалось выполнить ресинк стакана {symbol}: {exc}")
            return
        self._apply_snapshot(context, snapshot)
        timestamp = snapshot.received_at.astimezone(CURRENT_TIMEZONE)
        context.feed_monitor.clear()
        self._strategy.complete_resync(timestamp)

    def _initialize_order_book(self, context: SymbolContext) -> None:
        while True:
            event = next(context.depth_subscription.events)
            if event.type is StreamEventType.SNAPSHOT:
                snapshot = cast(OrderBookSnapshot, event.data)
                if snapshot is None:
                    continue
                self._apply_snapshot(context, snapshot)
                break
            if event.type is StreamEventType.RESYNC and event.reason is not None:
                context.feed_monitor.flag(event.reason)
                context.last_update_id = None
                self._resubscribe_symbol_streams(context)

    def _apply_snapshot(self, context: SymbolContext, snapshot: OrderBookSnapshot) -> None:
        context.order_book.reset_odr_history()
        context.order_book.apply_snapshot(snapshot)
        context.last_update_id = snapshot.last_update_id
        self._update_best_from_book(context)
        timestamp = snapshot.received_at
        context.order_book_updated_at = timestamp
        self._event_logger.log(
            (
                f"Снимок стакана {context.symbol} применён. "
                f"ID {snapshot.last_update_id}."
            ),
            timestamp,
        )

    def _apply_update(self, context: SymbolContext, update: OrderBookUpdate) -> None:
        if context.last_update_id is None:
            return
        if update.last_update_id <= context.last_update_id:
            return
        expected = context.last_update_id + 1
        if update.first_update_id > expected:
            context.feed_monitor.flag(StreamResyncReason.SEQUENCE_GAP)
            context.last_update_id = None
            context.order_book_updated_at = None
            return
        if expected > update.last_update_id:
            return
        context.order_book.apply_update(update)
        context.last_update_id = update.last_update_id
        context.order_book_updated_at = update.event_time
        self._update_best_from_book(context)

    @staticmethod
    def _update_best_from_book(context: SymbolContext) -> None:
        bid_level = context.order_book.best_bid()
        ask_level = context.order_book.best_ask()
        context.best_bid = None if bid_level is None else bid_level.price
        context.best_ask = None if ask_level is None else ask_level.price

    def _recover_depth_from_silence(
        self,
        context: SymbolContext,
        details: Optional[str],
        occurred_at: datetime,
    ) -> bool:
        now = get_current_time()
        last = context.last_depth_silence_recovery_at
        if last is not None and now - last < self._silence_recovery_cooldown:
            context.feed_monitor.has_silence_timeout = False
            return True
        try:
            snapshot = context.exchange_data.fetch_orderbook_snapshot()
        except Exception as exc:  # noqa: BLE001
            message = (
                f"Не удалось восстановить стакан {context.symbol} после тишины: {exc}"
            )
            timestamp = occurred_at.astimezone(CURRENT_TIMEZONE)
            self._event_logger.log(message, timestamp)
            return False
        self._apply_snapshot(context, snapshot)
        context.feed_monitor.has_silence_timeout = False
        context.last_depth_silence_recovery_at = now
        timestamp = occurred_at.astimezone(CURRENT_TIMEZONE)
        suffix = f" {details}" if details else ""
        self._event_logger.log(
            f"Стакан {context.symbol} восстановлен после тишины.{suffix}",
            timestamp,
        )
        return True

    def _is_data_fresh(
        self, timestamp: Optional[datetime], now: Optional[datetime] = None
    ) -> bool:
        if timestamp is None:
            return False
        current = now or get_current_time()
        if timestamp > current:
            return True
        return current - timestamp <= self._data_freshness_threshold

    def _process_depth_stream(self, context: SymbolContext) -> bool:
        buffer = context.depth_subscription._buffer
        events = buffer.drain_pending()
        if not events:
            try:
                primary = next(context.depth_subscription.events)
            except StopIteration:
                return False
            events = [primary]
            events.extend(buffer.drain_pending())
        processed = False
        for event in events:
            processed = True
            if event.type is StreamEventType.DATA:
                update = cast(OrderBookUpdate, event.data)
                if update is not None:
                    self._apply_update(context, update)
            elif event.type is StreamEventType.SNAPSHOT:
                snapshot = cast(OrderBookSnapshot, event.data)
                if snapshot is not None:
                    self._apply_snapshot(context, snapshot)
                    context.feed_monitor.clear()
            elif event.type is StreamEventType.RESYNC and event.reason is not None:
                if (
                    event.reason is StreamResyncReason.SILENCE_TIMEOUT
                    and self._recover_depth_from_silence(
                        context,
                        event.details,
                        event.timestamp,
                    )
                ):
                    continue
                context.feed_monitor.flag(event.reason)
                context.last_update_id = None
                context.order_book_updated_at = None
                timestamp = event.timestamp.astimezone(CURRENT_TIMEZONE)
                details = f" {event.details}." if event.details else ""
                self._event_logger.log(
                    (
                        f"Поток стакана {context.symbol} требует ресинк: "
                        f"{event.reason.value}.{details}"
                    ),
                    timestamp,
                )
                self._resubscribe_symbol_streams(context)
        return processed

    def _process_trade_stream(self, context: SymbolContext) -> bool:
        buffer = context.trade_subscription._buffer
        events = buffer.drain_pending()
        if not events:
            try:
                primary = next(context.trade_subscription.events)
            except StopIteration:
                return False
            events = [primary]
            events.extend(buffer.drain_pending())
        processed = False
        for event in events:
            processed = True
            if event.type is StreamEventType.DATA:
                trade = cast(Trade, event.data)
                if trade is not None:
                    trade_time = trade.executed_at
                    context.trade_updated_at = trade_time
                    context.last_trade_price = trade.price
                    now = get_current_time()
                    if self._is_data_fresh(trade_time, now):
                        _, _, ratio, ready = context.volume_tracker.observe(
                            trade_time, trade.price, trade.quantity
                        )
                        context.volume_ratio = ratio
                        context.volume_spike = (
                            ready and ratio >= CONFIG.general.vol_spike_mult
                        )
                    else:
                        context.volume_ratio = 0.0
                        context.volume_spike = False
        return processed

    @staticmethod
    def _process_ticker_stream(context: SymbolContext) -> bool:
        buffer = context.ticker_subscription._buffer
        events = buffer.drain_pending()
        if not events:
            try:
                primary = next(context.ticker_subscription.events)
            except StopIteration:
                return False
            events = [primary]
            events.extend(buffer.drain_pending())
        processed = False
        latest_ticker: BestBidAsk | None = None
        for event in events:
            processed = True
            if event.type is StreamEventType.DATA:
                ticker = cast(BestBidAsk, event.data)
                if ticker is not None:
                    latest_ticker = ticker
        if latest_ticker is not None:
            context.best_bid = latest_ticker.bid_price
            context.best_ask = latest_ticker.ask_price
            context.ticker_updated_at = latest_ticker.event_time
        return processed

    @staticmethod
    def _resolve_last_price(context: SymbolContext) -> float:
        if context.last_trade_price is not None and context.last_trade_price > 0.0:
            return context.last_trade_price
        if context.best_bid is not None and context.best_ask is not None:
            return (context.best_bid + context.best_ask) / 2.0
        return 0.0

    @staticmethod
    def _assign_symbol(context: SymbolContext, wall: Wall) -> Wall:
        return Wall(
            exchange=wall.exchange,
            symbol=context.symbol,
            side=wall.side,
            price=wall.price,
            quantity=wall.quantity,
            notional=wall.notional,
            first_seen_at=wall.first_seen_at,
            last_seen_at=wall.last_seen_at,
        )

    def _build_observation(self, context: SymbolContext) -> MarketObservation:
        now = get_current_time()
        stale_entries = []
        if not self._is_data_fresh(context.order_book_updated_at, now):
            order_book_updated_at = context.order_book_updated_at
            if order_book_updated_at is None:
                reason = "стакан не инициализирован"
            else:
                localized = order_book_updated_at.astimezone(CURRENT_TIMEZONE)
                reason = (
                    "стакан устарел (обновлён "
                    f"{localized.isoformat(timespec='seconds')})"
                )
            stale_entries.append(("order_book", reason))
        if not self._is_data_fresh(context.trade_updated_at, now):
            trade_updated_at = context.trade_updated_at
            if trade_updated_at is None:
                reason = "нет свежих сделок"
            else:
                localized = trade_updated_at.astimezone(CURRENT_TIMEZONE)
                reason = (
                    "сделки устарели (последняя "
                    f"{localized.isoformat(timespec='seconds')})"
                )
            stale_entries.append(("trade", reason))
        if stale_entries:
            recorded = self._stale_warnings.get(context.symbol, set())
            current_keys = {key for key, _ in stale_entries}
            new_messages = [message for key, message in stale_entries if key not in recorded]
            if new_messages:
                details = "; ".join(new_messages)
                log_timestamp = now.astimezone(CURRENT_TIMEZONE)
                self._event_logger.log(
                    f"Пропуск наблюдения {context.symbol}: {details}.",
                    log_timestamp,
                )
            self._stale_warnings[context.symbol] = current_keys
            return MarketObservation(
                timestamp=now,
                symbol=context.symbol,
                last_price=self._resolve_last_price(context),
                tick_size=context.filters.price_tick_size,
                pressure=None,
                bid_wall=None,
                ask_wall=None,
                bid_opposite_wall_blocks=False,
                ask_opposite_wall_blocks=False,
                available_symbols=self._desired_symbols,
                feed_status=context.feed_monitor.snapshot(),
                volume_ratio=0.0,
                volume_spike=False,
            )
        self._stale_warnings.pop(context.symbol, None)
        last_price = self._resolve_last_price(context)
        tick_size = context.filters.price_tick_size
        pressure = None
        if last_price > 0.0 and tick_size > 0.0:
            pressure = compute_odr(context.order_book, last_price, tick_size)
        bid_wall_candidate = check_if_has_near_wall(
            context.order_book, Side.BID, context.profile
        )
        ask_wall_candidate = check_if_has_near_wall(
            context.order_book, Side.ASK, context.profile
        )
        bid_wall = (
            self._assign_symbol(context, bid_wall_candidate)
            if bid_wall_candidate is not None
            else None
        )
        ask_wall = (
            self._assign_symbol(context, ask_wall_candidate)
            if ask_wall_candidate is not None
            else None
        )
        bid_opposite_blocks = False
        if bid_wall is not None:
            bid_opposite_blocks = check_if_has_opposite_wall(
                context.order_book,
                Side.ASK,
                bid_wall,
                context.profile,
            )
        ask_opposite_blocks = False
        if ask_wall is not None:
            ask_opposite_blocks = check_if_has_opposite_wall(
                context.order_book,
                Side.BID,
                ask_wall,
                context.profile,
            )
        return MarketObservation(
            timestamp=now,
            symbol=context.symbol,
            last_price=last_price,
            tick_size=tick_size,
            pressure=pressure,
            bid_wall=bid_wall,
            ask_wall=ask_wall,
            bid_opposite_wall_blocks=bid_opposite_blocks,
            ask_opposite_wall_blocks=ask_opposite_blocks,
            available_symbols=self._desired_symbols,
            feed_status=context.feed_monitor.snapshot(),
            volume_ratio=context.volume_ratio,
            volume_spike=context.volume_spike,
        )

    def _refresh_symbol_scan(self, timestamp: Optional[datetime] = None, *, force: bool = False) -> None:
        timestamp = timestamp or get_current_time()
        if not force and self._last_scan_at is not None:
            if timestamp - self._last_scan_at < self._scanner_interval:
                return
        scan_result = self._scanner.scan()
        profile_by_symbol = {symbol: profile for symbol, profile in scan_result}
        self._symbol_profiles = profile_by_symbol
        weights_by_symbol = self._scanner.symbol_weights
        self._symbol_weights = weights_by_symbol
        symbols = tuple(profile_by_symbol.keys())
        if self._binance_streams is not None:
            exchange_info = self._scanner.binance_exchange_info
            if exchange_info:
                self._binance_streams.update_exchange_info(exchange_info)
            self._binance_streams.update_profiles(profile_by_symbol)
            self._binance_streams.update_weights(weights_by_symbol)
            first_symbol = next((sym for sym in symbols if sym.endswith("USDT")), None)
            if first_symbol is not None:
                try:
                    self._binance_streams.get_exchange_data(first_symbol).fetch_symbol_filters()
                except Exception:
                    pass
        self._desired_symbols = symbols
        for symbol, context in self._contexts.items():
            profile = profile_by_symbol.get(symbol)
            if profile is not None:
                context.profile = profile
        self._subscription_manager.update(symbols, timestamp)
        self._last_scan_at = timestamp

    def _iter_active_contexts(self) -> Iterable[SymbolContext]:
        ordered: Tuple[str, ...] = self._desired_symbols
        seen: set[str] = set()
        for symbol in ordered:
            context = self._contexts.get(symbol)
            if context is None or not context.active:
                continue
            seen.add(symbol)
            yield context
        for symbol, context in self._contexts.items():
            if not context.active:
                continue
            if symbol in seen:
                continue
            yield context

    def _ensure_cycle_logged(self, context: SymbolContext) -> None:
        if context.cycle_started:
            return
        timestamp = get_current_time()
        self._event_logger.log(
            f"Запущен цикл обработки для {context.symbol}.",
            timestamp,
        )
        context.cycle_started = True

    def _create_context(
        self, symbol: str, streams: Optional[BinanceSymbolStreams] = None
    ) -> SymbolContext:
        if streams is not None:
            exchange_data = streams.exchange_data
            depth_subscription = streams.depth
            trade_subscription = streams.trades
            ticker_subscription = streams.ticker
        else:
            exchange_data = self._create_exchange_data(symbol)
            depth_subscription = exchange_data.stream_depth()
            trade_subscription = exchange_data.stream_trades()
            ticker_subscription = exchange_data.stream_book_ticker()
        filters = exchange_data.fetch_symbol_filters()
        trading_adapter = self._create_trading_adapter(symbol, filters)
        context = SymbolContext(
            symbol=symbol,
            exchange_data=exchange_data,
            order_book=OrderBook(
                recent_band_window_s=CONFIG.general.recent_band_s,
                recent_band_volume_boost=CONFIG.general.recent_band_s_vol_boost,
            ),
            feed_monitor=FeedMonitor(),
            depth_subscription=depth_subscription,
            trade_subscription=trade_subscription,
            ticker_subscription=ticker_subscription,
            filters=filters,
            trading_adapter=trading_adapter,
            profile=self._symbol_profiles.get(symbol, CONFIG.general.profile),
        )
        context.order_book.reset_odr_history()
        startup_timestamp = get_current_time()
        self._event_logger.log(
            f"Старт бота для {symbol} на {self._exchange.value}.",
            startup_timestamp,
        )
        filters_timestamp = get_current_time()
        self._event_logger.log(
            (
                f"Получены фильтры {symbol}: "
                f"шаг цены {context.filters.price_tick_size:g}."
            ),
            filters_timestamp,
        )
        initialize_account(context.trading_adapter)
        self._update_context_balance(context)
        self._initialize_order_book(context)
        self._refresh_context_funding(context, force=True)
        return context

    def _create_trading_adapter(self, symbol: str, filters: SymbolFilters) -> TradingAdapter:
        api_key, api_secret = self._exchange_credentials()
        if not api_key or not api_secret:
            raise RuntimeError("API credentials are required for trading operations")
        if self._exchange is Exchange.BINANCE:
            return BinanceTradingAdapter(
                symbol=symbol,
                quote_asset=filters.quote_asset,
                api_key=api_key,
                api_secret=api_secret,
            )
        if self._exchange is Exchange.BYBIT:
            return BybitTradingAdapter(
                symbol=symbol,
                settle_coin=filters.quote_asset,
                api_key=api_key,
                api_secret=api_secret,
            )
        raise RuntimeError("unsupported exchange")

    def _log_error(self, message: str) -> None:
        timestamp = get_current_time()
        self._log_writer(LogLine(timestamp=timestamp, message=message, level="ERROR"))

    def _log_scanner_message(self, message: str) -> None:
        timestamp = get_current_time()
        self._event_logger.log(
            f"Сканер рынка: {message}",
            timestamp,
        )

    def _update_context_balance(self, context: SymbolContext) -> None:
        try:
            balance = context.trading_adapter.get_balance(self._balance_source)
        except (BinanceAPIError, BybitAPIError, Exception) as error:
            self._log_error(
                f"Не удалось обновить баланс {context.symbol}: {error}"
            )
            return
        timestamp = get_current_time()
        context.balance = balance
        context.balance_updated_at = timestamp
        self._event_logger.log(
            f"Баланс {context.symbol} обновлён: {balance:g}.",
            timestamp,
        )

    def _refresh_context_funding(
        self,
        context: SymbolContext,
        *,
        force: bool = False,
    ) -> None:
        now = get_current_time()
        needs_refresh = force
        if not needs_refresh:
            if context.next_funding_updated_at is None:
                needs_refresh = True
            elif context.next_funding_at is None:
                needs_refresh = (
                    now - context.next_funding_updated_at
                    >= self._funding_refresh_interval
                )
            else:
                if now - context.next_funding_updated_at >= self._funding_refresh_interval:
                    needs_refresh = True
                elif now > context.next_funding_at + self._funding_block_window:
                    needs_refresh = True
        if not needs_refresh:
            return
        try:
            next_funding = context.exchange_data.fetch_next_funding_time()
        except Exception as exc:
            self._event_logger.log(
                f"Не удалось обновить время фандинга для {context.symbol}: {exc}",
                now,
            )
            context.next_funding_updated_at = now
            return
        previous = context.next_funding_at
        context.next_funding_at = next_funding
        context.next_funding_updated_at = now
        context.funding_block_logged = False
        if next_funding is None:
            if previous is not None:
                self._event_logger.log(
                    f"Следующее время фандинга для {context.symbol} недоступно.",
                    now,
                )
            return
        if previous is None or next_funding != previous:
            localized = next_funding.astimezone(CURRENT_TIMEZONE)
            self._event_logger.log(
                (
                    f"Следующее время фандинга для {context.symbol}: "
                    f"{localized:%Y-%m-%d %H:%M:%S %Z}."
                ),
                now,
            )

    def _handle_funding_window(self, context: SymbolContext, timestamp: datetime) -> None:
        window = self._funding_block_window
        if window <= timedelta(0):
            return
        next_funding = context.next_funding_at
        if next_funding is None:
            if context.funding_block_logged:
                context.funding_block_logged = False
            return
        start = next_funding - window
        end = next_funding + window
        if start <= timestamp <= end:
            self._kill_switch.handle_funding(next_funding)
            if not context.funding_block_logged:
                blocked_until = self._kill_switch.blocked_until()
                funding_local = next_funding.astimezone(CURRENT_TIMEZONE)
                if blocked_until is not None:
                    block_local = blocked_until.astimezone(CURRENT_TIMEZONE)
                    message = (
                        f"{context.symbol}: блокировка из-за фандинга до "
                        f"{block_local:%H:%M:%S %Z}. Фандинг в {funding_local:%H:%M:%S %Z}."
                    )
                else:
                    message = (
                        f"{context.symbol}: блокировка из-за фандинга. "
                        f"Фандинг в {funding_local:%H:%M:%S %Z}."
                    )
                self._event_logger.log(message, timestamp)
                context.funding_block_logged = True
        elif context.funding_block_logged and timestamp > end:
            context.funding_block_logged = False

    def _refresh_balances(self, *, force: bool = False) -> None:
        if not self._contexts:
            return
        now = get_current_time()
        if (
            not force
            and self._balance_refresh_interval.total_seconds() > 0.0
            and self._last_balance_refresh_at is not None
            and now - self._last_balance_refresh_at < self._balance_refresh_interval
        ):
            return
        for context in self._contexts.values():
            if not context.active:
                continue
            self._update_context_balance(context)
        self._last_balance_refresh_at = now

    def run(self) -> None:
        interval = CONFIG.general.loop_interval_ms / 1000.0
        self._refresh_symbol_scan(force=True)
        self._refresh_balances(force=True)
        while True:
            self._refresh_symbol_scan()
            self._refresh_balances()
            resync_triggered = False
            work_done = False
            for context in self._iter_active_contexts():
                self._current_symbol = context.symbol
                self._ensure_cycle_logged(context)
                self._refresh_context_funding(context)
                now = get_current_time()
                self._handle_funding_window(context, now)
                if self._process_depth_stream(context):
                    work_done = True
                if self._process_trade_stream(context):
                    work_done = True
                if self._process_ticker_stream(context):
                    work_done = True
                observation = self._build_observation(context)
                state = self._strategy.process(observation)
                if state is StrategyState.RESYNC:
                    resync_triggered = True
                    break
            self._current_symbol = None
            if not work_done:
                time.sleep(interval)
            if resync_triggered:
                continue


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    return parser.parse_args(list(argv))


def main() -> None:
    parse_args(sys.argv[1:])
    sink = create_text_log_sink()
    app = Application(sink)
    app.run()


if __name__ == "__main__":
    main()
