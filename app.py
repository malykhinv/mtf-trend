from __future__ import annotations

import argparse
import sys
import time
from typing import Optional, Sequence, Tuple, cast

from config.config import CONFIG
from config.models.exchange_name import ExchangeName
from config.secrets import SECRETS
from config.timezone import BELGRADE_TIMEZONE
from data.exchanges import (
    BestBidAsk,
    BinanceExchangeData,
    BybitExchangeData,
    IExchangeData,
    ResyncReason as StreamResyncReason,
    StreamEventType,
)
from data.logger import LogSink, create_log_writer, create_text_log_sink
from data.telegram import TelegramClient
from domain.book import OrderBook
from domain.detectors import check_if_has_near_wall, check_if_has_opposite_wall, compute_odr
from domain.execution import create_execution_handlers, initialize_account
from domain.models import (
    Exchange,
    OrderBookSnapshot,
    OrderBookUpdate,
    Side,
    SymbolFilters,
    Trade,
    Wall,
)
from domain.strategy import (
    EventLogger,
    FocusController,
    MarketObservation,
    PositionController,
    Strategy,
    SubscriptionManager,
    TelegramNotifier,
)
from domain.strategy.resync import ResyncReason as StrategyResyncReason
from domain.strategy.state import StrategyState
from utils import get_current_time
from application import FeedMonitor, GUARDS, NoopTradingAdapter


class Application:
    def __init__(self, symbol: str, sink: LogSink) -> None:
        GUARDS.set_exchange(CONFIG.general.exchange)
        GUARDS.set_profile(CONFIG.general.profile)
        self._symbol = symbol.upper()
        self._sink = sink
        self._log_writer = create_log_writer(sink)
        self._event_logger = EventLogger(write=self._log_writer)
        self._feed_monitor = FeedMonitor()
        self._order_book = OrderBook(recent_band_capacity=256)
        self._last_update_id: Optional[int] = None
        self._last_trade_price: Optional[float] = None
        self._best_bid: Optional[float] = None
        self._best_ask: Optional[float] = None
        self._focused_symbol: Optional[str] = None
        self._available_symbols: Tuple[str, ...] = (self._symbol,)
        self._telegram_client = TelegramClient(
            token=SECRETS.tg_bot_token,
            chat_id=CONFIG.telegram.chat_id,
            silent=CONFIG.telegram.silent,
        )
        self._telegram_notifier = TelegramNotifier(send_message=self._send_telegram)
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
        self._exchange_data = self._create_exchange_data()
        self._filters = self._exchange_data.fetch_symbol_filters()
        self._trading_adapter = NoopTradingAdapter(self._exchange, self._symbol)
        initialize_account(self._trading_adapter)
        entry, exit_, move_stop = create_execution_handlers(
            self._trading_adapter,
            self._provide_filters,
        )
        self._position_controller = PositionController(
            enter_position=entry,
            exit_position=exit_,
            move_stop=move_stop,
            logger=self._event_logger,
        )
        self._strategy = Strategy(
            subscriptions=self._subscription_manager,
            focus=self._focus_controller,
            position=self._position_controller,
            notifier=self._telegram_notifier,
            logger=self._event_logger,
            resync=self._handle_resync,
        )
        self._depth_stream = self._exchange_data.stream_depth()
        self._trade_stream = self._exchange_data.stream_trades()
        self._ticker_stream = self._exchange_data.stream_book_ticker()
        self._initialize_order_book()

    def _create_exchange_data(self) -> IExchangeData:
        if CONFIG.general.exchange is ExchangeName.BINANCE:
            return BinanceExchangeData(
                symbol=self._symbol,
                loop_interval_ms=CONFIG.general.loop_interval_ms,
                log_writer=self._sink,
            )
        if CONFIG.general.exchange is ExchangeName.BYBIT:
            return BybitExchangeData(
                symbol=self._symbol,
                loop_interval_ms=CONFIG.general.loop_interval_ms,
                log_writer=self._sink,
            )
        raise RuntimeError("unsupported exchange")

    def _provide_filters(self, symbol: str) -> SymbolFilters:
        if symbol.upper() != self._symbol:
            raise ValueError("unknown symbol")
        return self._filters

    def _subscribe_symbol(self, symbol: str) -> None:
        if symbol.upper() != self._symbol:
            return
        self._available_symbols = (self._symbol,)

    def _unsubscribe_symbol(self, symbol: str) -> None:
        if symbol.upper() != self._symbol:
            return
        self._available_symbols = (self._symbol,)

    def _focus_symbol(self, symbol: str) -> None:
        if symbol.upper() != self._symbol:
            raise ValueError("focus symbol mismatch")
        self._focused_symbol = symbol.upper()

    def _defocus_symbol(self) -> None:
        self._focused_symbol = None

    def _send_telegram(self, message: str) -> None:
        self._telegram_client.send_message(message)

    def _handle_resync(self, reason: StrategyResyncReason) -> None:
        snapshot = self._exchange_data.fetch_orderbook_snapshot()
        self._apply_snapshot(snapshot)
        timestamp = snapshot.received_at.astimezone(BELGRADE_TIMEZONE)
        self._feed_monitor.clear()
        self._strategy.complete_resync(timestamp)

    def _initialize_order_book(self) -> None:
        while True:
            event = next(self._depth_stream)
            if event.type is StreamEventType.SNAPSHOT:
                snapshot = cast(OrderBookSnapshot, event.data)
                if snapshot is None:
                    continue
                self._apply_snapshot(snapshot)
                break
            if event.type is StreamEventType.RESYNC and event.reason is not None:
                self._feed_monitor.flag(event.reason)
                self._last_update_id = None

    def _apply_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        self._order_book.apply_snapshot(snapshot)
        self._last_update_id = snapshot.last_update_id
        self._update_best_from_book()

    def _apply_update(self, update: OrderBookUpdate) -> None:
        if self._last_update_id is None:
            return
        if update.last_update_id <= self._last_update_id:
            return
        expected = self._last_update_id + 1
        if update.first_update_id > expected:
            self._feed_monitor.flag(StreamResyncReason.SEQUENCE_GAP)
            self._last_update_id = None
            return
        if expected > update.last_update_id:
            return
        self._order_book.apply_update(update)
        self._last_update_id = update.last_update_id
        self._update_best_from_book()

    def _update_best_from_book(self) -> None:
        bid_level = self._order_book.best_bid()
        ask_level = self._order_book.best_ask()
        self._best_bid = None if bid_level is None else bid_level.price
        self._best_ask = None if ask_level is None else ask_level.price

    def _process_depth_stream(self) -> None:
        event = next(self._depth_stream)
        if event.type is StreamEventType.DATA:
            update = cast(OrderBookUpdate, event.data)
            if update is not None:
                self._apply_update(update)
        elif event.type is StreamEventType.SNAPSHOT:
            snapshot = cast(OrderBookSnapshot, event.data)
            if snapshot is not None:
                self._apply_snapshot(snapshot)
                self._feed_monitor.clear()
        elif event.type is StreamEventType.RESYNC and event.reason is not None:
            self._feed_monitor.flag(event.reason)
            self._last_update_id = None

    def _process_trade_stream(self) -> None:
        event = next(self._trade_stream)
        if event.type is StreamEventType.DATA:
            trade = cast(Trade, event.data)
            if trade is not None:
                self._last_trade_price = trade.price

    def _process_ticker_stream(self) -> None:
        event = next(self._ticker_stream)
        if event.type is StreamEventType.DATA:
            ticker = cast(BestBidAsk, event.data)
            if ticker is not None:
                self._best_bid = ticker.bid_price
                self._best_ask = ticker.ask_price

    def _resolve_last_price(self) -> float:
        if self._last_trade_price is not None and self._last_trade_price > 0.0:
            return self._last_trade_price
        if self._best_bid is not None and self._best_ask is not None:
            return (self._best_bid + self._best_ask) / 2.0
        return 0.0

    def _assign_symbol(self, wall: Wall) -> Wall:
        return Wall(
            exchange=wall.exchange,
            symbol=self._symbol,
            side=wall.side,
            price=wall.price,
            quantity=wall.quantity,
            notional=wall.notional,
            first_seen_at=wall.first_seen_at,
            last_seen_at=wall.last_seen_at,
        )

    def _choose_wall(self) -> Optional[Wall]:
        bid_wall = check_if_has_near_wall(self._order_book, Side.BID)
        ask_wall = check_if_has_near_wall(self._order_book, Side.ASK)
        candidate: Optional[Wall] = None
        if bid_wall is not None and ask_wall is not None:
            candidate = bid_wall if bid_wall.notional >= ask_wall.notional else ask_wall
        elif bid_wall is not None:
            candidate = bid_wall
        elif ask_wall is not None:
            candidate = ask_wall
        if candidate is None:
            return None
        return self._assign_symbol(candidate)

    def _build_observation(self) -> MarketObservation:
        last_price = self._resolve_last_price()
        tick_size = self._filters.price_tick_size
        pressure = None
        if last_price > 0.0 and tick_size > 0.0:
            pressure = compute_odr(self._order_book, last_price, tick_size)
        wall = self._choose_wall()
        opposite_blocks = False
        if wall is not None:
            move_side = Side.ASK if wall.side is Side.BID else Side.BID
            opposite_blocks = check_if_has_opposite_wall(self._order_book, move_side, wall)
        return MarketObservation(
            timestamp=get_current_time(),
            symbol=self._symbol,
            last_price=last_price,
            tick_size=tick_size,
            pressure=pressure,
            near_wall=wall,
            opposite_wall_blocks=opposite_blocks,
            available_symbols=self._available_symbols,
            feed_status=self._feed_monitor.snapshot(),
        )

    def run(self) -> None:
        interval = CONFIG.general.loop_interval_ms / 1000.0
        while True:
            self._process_depth_stream()
            self._process_trade_stream()
            self._process_ticker_stream()
            observation = self._build_observation()
            state = self._strategy.process(observation)
            if state is StrategyState.RESYNC:
                time.sleep(interval)
                continue
            time.sleep(interval)


def parse_args(argv: Sequence[str]) -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parsed = parser.parse_args(list(argv))
    return parsed.symbol


def main() -> None:
    symbol = parse_args(sys.argv[1:])
    sink = create_text_log_sink()
    app = Application(symbol, sink)
    app.run()


if __name__ == "__main__":
    main()
