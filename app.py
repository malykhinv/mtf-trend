from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, Iterator, Optional, Sequence, Tuple, cast

from config.config import CONFIG
from config.models.balance_source import BalanceSource as ConfigBalanceSource
from config.models.exchange_name import ExchangeName
from config.models.trading_profile import TradingProfile
from config.secrets import SECRETS
from config.timezone import CURRENT_TIMEZONE
from data.exchanges import (
    BestBidAsk,
    BinanceExchangeData,
    BinanceTradingAdapter,
    BybitExchangeData,
    BybitTradingAdapter,
    DepthStreamData,
    IExchangeData,
    ResyncReason as StreamResyncReason,
    StreamEvent,
    StreamEventType,
)
from data.logger import LogSink, create_log_writer, create_text_log_sink
from data.telegram import TelegramClient
from domain.book import OrderBook
from domain.detectors import check_if_has_near_wall, check_if_has_opposite_wall, compute_odr
from domain.execution import create_execution_handlers, initialize_account
from domain.models import (
    BalanceSource,
    Exchange,
    ExecutionReport,
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
from utils import get_current_time
from application import FeedMonitor, GUARDS
from application.market_scanner import MarketScanner
from domain.trading_adapter import TradingAdapter


@dataclass
class SymbolContext:
    symbol: str
    exchange_data: IExchangeData
    order_book: OrderBook
    feed_monitor: FeedMonitor
    depth_stream: Iterator[StreamEvent[DepthStreamData]]
    trade_stream: Iterator[StreamEvent[Trade]]
    ticker_stream: Iterator[StreamEvent[BestBidAsk]]
    filters: SymbolFilters
    trading_adapter: TradingAdapter
    last_update_id: Optional[int] = None
    last_trade_price: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    active: bool = False
    cycle_started: bool = False
    balance: float = 0.0
    balance_updated_at: Optional[datetime] = None
    profile: TradingProfile = CONFIG.general.profile


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

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> None:
        self._resolve().set_leverage(leverage, margin_mode)

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
        self._subscription_manager = SubscriptionManager(
            subscribe=self._subscribe_symbol,
            unsubscribe=self._unsubscribe_symbol,
            logger=self._event_logger,
        )
        self._focus_controller = FocusController(
            focus_symbol=self._focus_symbol,
            defocus_symbol=self._defocus_symbol,
            logger=self._event_logger,
        )
        self._exchange = Exchange(CONFIG.general.exchange.value)
        self._scanner = MarketScanner(
            CONFIG.general.exchange,
            CONFIG.general.profile,
            CONFIG.turnover,
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
        api_key, api_secret = self._exchange_credentials()
        if CONFIG.general.exchange is ExchangeName.BINANCE:
            return BinanceExchangeData(
                symbol=symbol,
                loop_interval_ms=CONFIG.general.loop_interval_ms,
                log_writer=self._sink,
                api_key=api_key,
                api_secret=api_secret,
            )
        if CONFIG.general.exchange is ExchangeName.BYBIT:
            return BybitExchangeData(
                symbol=symbol,
                loop_interval_ms=CONFIG.general.loop_interval_ms,
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
        if context is None:
            context = self._create_context(symbol)
            self._contexts[symbol] = context
        context.active = True
        context.cycle_started = False

    def _unsubscribe_symbol(self, symbol: str) -> None:
        symbol = symbol.upper()
        context = self._contexts.get(symbol)
        if context is None:
            return
        context.active = False
        if self._focus_controller.current == symbol:
            timestamp = get_current_time()
            self._focus_controller.defocus(timestamp)

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
        snapshot = context.exchange_data.fetch_orderbook_snapshot()
        self._apply_snapshot(context, snapshot)
        timestamp = snapshot.received_at.astimezone(CURRENT_TIMEZONE)
        context.feed_monitor.clear()
        self._strategy.complete_resync(timestamp)

    def _initialize_order_book(self, context: SymbolContext) -> None:
        while True:
            event = next(context.depth_stream)
            if event.type is StreamEventType.SNAPSHOT:
                snapshot = cast(OrderBookSnapshot, event.data)
                if snapshot is None:
                    continue
                self._apply_snapshot(context, snapshot)
                break
            if event.type is StreamEventType.RESYNC and event.reason is not None:
                context.feed_monitor.flag(event.reason)
                context.last_update_id = None

    def _apply_snapshot(self, context: SymbolContext, snapshot: OrderBookSnapshot) -> None:
        context.order_book.apply_snapshot(snapshot)
        context.last_update_id = snapshot.last_update_id
        self._update_best_from_book(context)
        timestamp = snapshot.received_at
        self._event_logger.log(
            (
                f"{timestamp:%H:%M:%S} Снимок стакана {context.symbol} применён. "
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
            return
        if expected > update.last_update_id:
            return
        context.order_book.apply_update(update)
        context.last_update_id = update.last_update_id
        self._update_best_from_book(context)

    @staticmethod
    def _update_best_from_book(context: SymbolContext) -> None:
        bid_level = context.order_book.best_bid()
        ask_level = context.order_book.best_ask()
        context.best_bid = None if bid_level is None else bid_level.price
        context.best_ask = None if ask_level is None else ask_level.price

    def _process_depth_stream(self, context: SymbolContext) -> None:
        event = next(context.depth_stream)
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
            context.feed_monitor.flag(event.reason)
            context.last_update_id = None
            timestamp = event.timestamp.astimezone(CURRENT_TIMEZONE)
            details = f" {event.details}." if event.details else ""
            self._event_logger.log(
                (
                    f"{timestamp:%H:%M:%S} Поток стакана {context.symbol} требует ресинк: "
                    f"{event.reason.value}.{details}"
                ),
                timestamp,
            )

    @staticmethod
    def _process_trade_stream(context: SymbolContext) -> None:
        event = next(context.trade_stream)
        if event.type is StreamEventType.DATA:
            trade = cast(Trade, event.data)
            if trade is not None:
                context.last_trade_price = trade.price

    @staticmethod
    def _process_ticker_stream(context: SymbolContext) -> None:
        event = next(context.ticker_stream)
        if event.type is StreamEventType.DATA:
            ticker = cast(BestBidAsk, event.data)
            if ticker is not None:
                context.best_bid = ticker.bid_price
                context.best_ask = ticker.ask_price

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

    def _choose_wall(self, context: SymbolContext) -> Optional[Wall]:
        bid_wall = check_if_has_near_wall(context.order_book, Side.BID, context.profile)
        ask_wall = check_if_has_near_wall(context.order_book, Side.ASK, context.profile)
        candidate: Optional[Wall] = None
        if bid_wall is not None and ask_wall is not None:
            candidate = bid_wall if bid_wall.notional >= ask_wall.notional else ask_wall
        elif bid_wall is not None:
            candidate = bid_wall
        elif ask_wall is not None:
            candidate = ask_wall
        if candidate is None:
            return None
        return self._assign_symbol(context, candidate)

    def _build_observation(self, context: SymbolContext) -> MarketObservation:
        last_price = self._resolve_last_price(context)
        tick_size = context.filters.price_tick_size
        pressure = None
        if last_price > 0.0 and tick_size > 0.0:
            pressure = compute_odr(context.order_book, last_price, tick_size)
        wall = self._choose_wall(context)
        opposite_blocks = False
        if wall is not None:
            move_side = Side.ASK if wall.side is Side.BID else Side.BID
            opposite_blocks = check_if_has_opposite_wall(
                context.order_book,
                move_side,
                wall,
                context.profile,
            )
        return MarketObservation(
            timestamp=get_current_time(),
            symbol=context.symbol,
            last_price=last_price,
            tick_size=tick_size,
            pressure=pressure,
            near_wall=wall,
            opposite_wall_blocks=opposite_blocks,
            available_symbols=self._desired_symbols,
            feed_status=context.feed_monitor.snapshot(),
        )

    def _refresh_symbol_scan(self, timestamp: Optional[datetime] = None, *, force: bool = False) -> None:
        timestamp = timestamp or get_current_time()
        if not force and self._last_scan_at is not None:
            if timestamp - self._last_scan_at < self._scanner_interval:
                return
        scan_result = self._scanner.scan()
        self._symbol_profiles = {symbol: profile for symbol, profile in scan_result}
        symbols = tuple(symbol for symbol, _ in scan_result)
        self._desired_symbols = symbols
        for symbol, profile in self._symbol_profiles.items():
            context = self._contexts.get(symbol)
            if context is not None:
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
            f"{timestamp:%H:%M:%S} Запущен цикл обработки для {context.symbol}.",
            timestamp,
        )
        context.cycle_started = True

    def _create_context(self, symbol: str) -> SymbolContext:
        exchange_data = self._create_exchange_data(symbol)
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
            depth_stream=exchange_data.stream_depth(),
            trade_stream=exchange_data.stream_trades(),
            ticker_stream=exchange_data.stream_book_ticker(),
            filters=filters,
            trading_adapter=trading_adapter,
            profile=self._symbol_profiles.get(symbol, CONFIG.general.profile),
        )
        startup_timestamp = get_current_time()
        self._event_logger.log(
            f"{startup_timestamp:%H:%M:%S} Старт бота для {symbol} на {self._exchange.value}.",
            startup_timestamp,
        )
        filters_timestamp = get_current_time()
        self._event_logger.log(
            (
                f"{filters_timestamp:%H:%M:%S} Получены фильтры {symbol}: "
                f"шаг цены {context.filters.price_tick_size:g}."
            ),
            filters_timestamp,
        )
        initialize_account(context.trading_adapter)
        account_timestamp = get_current_time()
        self._event_logger.log(
            f"{account_timestamp:%H:%M:%S} Торговый адаптер инициализирован для {symbol}.",
            account_timestamp,
        )
        self._update_context_balance(context)
        self._initialize_order_book(context)
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

    def _log_scanner_message(self, message: str) -> None:
        timestamp = get_current_time()
        self._event_logger.log(
            f"{timestamp:%H:%M:%S} Сканер рынка: {message}",
            timestamp,
        )

    def _update_context_balance(self, context: SymbolContext) -> None:
        balance = context.trading_adapter.get_balance(self._balance_source)
        timestamp = get_current_time()
        context.balance = balance
        context.balance_updated_at = timestamp
        self._event_logger.log(
            f"{timestamp:%H:%M:%S} Баланс {context.symbol} обновлён: {balance:g}.",
            timestamp,
        )

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
            for context in self._iter_active_contexts():
                self._current_symbol = context.symbol
                self._ensure_cycle_logged(context)
                self._process_depth_stream(context)
                self._process_trade_stream(context)
                self._process_ticker_stream(context)
                observation = self._build_observation(context)
                state = self._strategy.process(observation)
                if state is StrategyState.RESYNC:
                    resync_triggered = True
                    break
            self._current_symbol = None
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
