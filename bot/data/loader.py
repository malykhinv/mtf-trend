"""Market data loader abstractions and concrete implementations."""
from __future__ import annotations

import json
import threading
import time
import bisect
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Iterable

import ccxt
from websocket import WebSocketApp

from bot import config
from bot.domain.models.bar import Bar, BarMetrics, BreakDirection
from bot.domain.models.exchange import Exchange
from bot.domain.models.timeframe import Timeframe
from bot.utils.logging import get_logger
from bot.utils.prints_aggregator import PrintsAggregator, TradePrint

_TIMEFRAME_TO_DELTA: dict[Timeframe, timedelta] = {
    timeframe: timedelta(minutes=timeframe.minutes) for timeframe in Timeframe
}

_EMPTY_METRICS = BarMetrics(
    pct_move=0.0,
    relative_volume=0.0,
    atr_mult=0.0,
    upper_wick_pct=0.0,
    body_pct=0.0,
    lower_wick_pct=0.0,
    pct_to_low_break=0.0,
    pct_to_high_break=0.0,
    break_direction=BreakDirection.NONE,
)


class _BarMetricsHelper:
    """Maintain rolling statistics to calculate bar metrics."""

    def __init__(
        self,
        *,
        vol_window: int = config.VOL_WINDOW,
        atr_window: int = config.ATR_WINDOW,
        enable_break_direction: bool = False,
    ) -> None:
        self._vol_window = max(1, vol_window)
        self._atr_window = max(1, atr_window)
        self._volumes: deque[float] = deque()
        self._sorted_volumes: list[float] = []
        self._true_ranges: deque[float] = deque()
        self._true_range_sum: float = 0.0
        self._prev_close: float | None = None
        self._enable_break_direction = enable_break_direction

    def calculate(
        self,
        *,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> BarMetrics:
        pct_move = self._calc_pct_move(open_price=open_price, high=high)
        median_volume = self._update_volume(volume)
        relative_volume = volume / median_volume if median_volume > 0.0 else 0.0
        atr = self._update_atr(high=high, low=low, close=close)
        range_value = max(high - low, 0.0)
        atr_mult = range_value / atr if atr > 0.0 else 0.0
        upper_wick_pct, body_pct, lower_wick_pct = self._calc_wicks(
            open_price=open_price,
            close=close,
            high=high,
            low=low,
            range_value=range_value,
        )
        pct_to_low_break, pct_to_high_break = self._calc_break_distances(
            close=close,
            high=high,
            low=low,
        )
        return BarMetrics(
            pct_move=pct_move,
            relative_volume=relative_volume,
            atr_mult=atr_mult,
            upper_wick_pct=upper_wick_pct,
            body_pct=body_pct,
            lower_wick_pct=lower_wick_pct,
            pct_to_low_break=pct_to_low_break,
            pct_to_high_break=pct_to_high_break,
            break_direction=BreakDirection.NONE,
        )

    def _update_volume(self, volume: float) -> float:
        if len(self._volumes) == self._vol_window:
            removed = self._volumes.popleft()
            idx = bisect.bisect_left(self._sorted_volumes, removed)
            if idx < len(self._sorted_volumes) and self._sorted_volumes[idx] == removed:
                self._sorted_volumes.pop(idx)
        self._volumes.append(volume)
        bisect.insort(self._sorted_volumes, volume)
        count = len(self._sorted_volumes)
        if count == 0:
            return 0.0
        mid = count // 2
        if count % 2 == 1:
            return self._sorted_volumes[mid]
        return (self._sorted_volumes[mid - 1] + self._sorted_volumes[mid]) / 2.0

    def _update_atr(self, *, high: float, low: float, close: float) -> float:
        range_value = max(high - low, 0.0)
        if self._prev_close is None:
            true_range = range_value
        else:
            true_range = max(
                range_value,
                abs(high - self._prev_close),
                abs(low - self._prev_close),
            )
        if len(self._true_ranges) == self._atr_window:
            removed = self._true_ranges.popleft()
            self._true_range_sum -= removed
        self._true_ranges.append(true_range)
        self._true_range_sum += true_range
        self._prev_close = close
        if not self._true_ranges:
            return 0.0
        return self._true_range_sum / len(self._true_ranges)

    @staticmethod
    def _calc_pct_move(*, open_price: float, high: float) -> float:
        if open_price == 0.0:
            return 0.0
        return (high - open_price) / open_price * 100.0

    @staticmethod
    def _calc_wicks(
        *,
        open_price: float,
        close: float,
        high: float,
        low: float,
        range_value: float,
    ) -> tuple[float, float, float]:
        if range_value <= 0.0:
            return 0.0, 0.0, 0.0
        upper_wick = (high - max(open_price, close)) / range_value * 100.0
        body = abs(close - open_price) / range_value * 100.0
        lower_wick = (min(open_price, close) - low) / range_value * 100.0
        return upper_wick, body, lower_wick

    @staticmethod
    def _calc_break_distances(*, close: float, high: float, low: float) -> tuple[float, float]:
        if close == 0.0:
            return 0.0, 0.0
        pct_to_low = (close - low) / close * 100.0 if close > 0.0 else 0.0
        pct_to_high = (high - close) / close * 100.0 if close > 0.0 else 0.0
        return pct_to_low, pct_to_high


class BreakDirectionResolver:
    """Resolve the first breakout direction for closed bars."""

    def __init__(self) -> None:
        self._pending: deque[Bar] = deque()

    def process(self, bar: Bar) -> list[Bar]:
        resolved: list[Bar] = []
        next_pending: deque[Bar] = deque()
        for pending_bar in self._pending:
            direction = self._detect_direction(pending_bar, bar)
            if direction is BreakDirection.NONE:
                next_pending.append(pending_bar)
                continue
            resolved.append(self._apply_direction(pending_bar, direction))
        self._pending = next_pending
        self._pending.append(bar)
        return resolved

    def flush(self) -> list[Bar]:
        remaining = list(self._pending)
        self._pending.clear()
        return remaining

    @staticmethod
    def _detect_direction(source: Bar, candidate: Bar) -> BreakDirection:
        broke_high = candidate.high >= source.high
        broke_low = candidate.low <= source.low
        if broke_high and broke_low:
            return BreakDirection.BOTH
        if broke_high:
            return BreakDirection.HIGH_FIRST
        if broke_low:
            return BreakDirection.LOW_FIRST
        return BreakDirection.NONE

    @staticmethod
    def _apply_direction(bar: Bar, direction: BreakDirection) -> Bar:
        metrics = replace(bar.metrics, break_direction=direction)
        return replace(bar, metrics=metrics)


RECONNECT_DELAY_SEC: int = 5


@dataclass(frozen=True)
class HistoricalRequest:
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    limit: int | None = None


class MarketDataLoader:
    """Base synchronous interface for loading historical bars."""

    def load(self, request: HistoricalRequest) -> Iterable[Bar]:  # pragma: no cover - interface definition
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class LiveBarEvent:
    bar: Bar
    imbalance: float


class LiveDataStream:
    """Streaming interface that delivers closed bars to listeners."""

    def subscribe(self, listener: Callable[[LiveBarEvent], None]) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError


class CcxtMarketDataLoader(MarketDataLoader):
    """Historical market data loader that relies on synchronous ccxt clients."""

    def __init__(self, *, logger=None) -> None:
        self._logger = logger or get_logger(__name__)
        self._clients: dict[Exchange, object] = {
            Exchange.BINANCE: ccxt.binanceusdm({"enableRateLimit": True}),
            Exchange.BYBIT: ccxt.bybit(
                {
                    "enableRateLimit": True,
                    "options": {
                        "defaultType": "swap",
                        "defaultSubType": "linear",
                        "defaultSettle": "USDT",
                    },
                }
            ),
        }

    def load(self, request: HistoricalRequest) -> Iterable[Bar]:
        client = self._clients.get(request.exchange)
        if client is None:
            raise ValueError(f"Unsupported exchange: {request.exchange}")

        metrics_helper = _BarMetricsHelper(enable_break_direction=True)
        direction_resolver = BreakDirectionResolver()
        timeframe = request.timeframe.value
        since = _to_millis(request.start)
        end_ts = _to_millis(request.end)
        limit = request.limit or 1000
        cursor = since

        self._logger.info(
            "Загружаем OHLCV: %s %s %s с %s по %s (лимит %s)",
            request.exchange.value,
            request.symbol,
            timeframe,
            request.start,
            request.end,
            limit,
        )

        timeframe_delta = _TIMEFRAME_TO_DELTA[request.timeframe]
        timeframe_ms = int(timeframe_delta.total_seconds() * 1000)

        should_stop = False
        while True:
            batch = client.fetch_ohlcv(  # type: ignore[attr-defined]
                request.symbol,
                timeframe=timeframe,
                since=cursor,
                limit=limit,
            )
            if not batch:
                break

            for candle in batch:
                open_ts = int(candle[0])
                if open_ts >= end_ts:
                    should_stop = True
                    break
                close_ts = open_ts + timeframe_ms
                bar = _bar_from_ohlcv(
                    exchange=request.exchange,
                    symbol=request.symbol,
                    timeframe=request.timeframe,
                    open_ts=open_ts,
                    close_ts=close_ts,
                    ohlcv=candle,
                    metrics_helper=metrics_helper,
                )
                for resolved in direction_resolver.process(bar):
                    yield resolved

            last_ts = int(batch[-1][0])
            cursor = last_ts + timeframe_ms
            if should_stop or cursor >= end_ts:
                break
        for remaining in direction_resolver.flush():
            yield remaining


class WsLiveDataStream(LiveDataStream):
    """Live websocket data stream with reconnection and subscription capping."""

    _BINANCE_WS = "wss://fstream.binance.com/stream"
    _BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"

    def __init__(
        self,
        *,
        timeframe: Timeframe,
        top_n: int = 50,
        logger=None,
    ) -> None:
        self._timeframe = timeframe
        self._top_n = top_n
        self._logger = logger or get_logger(__name__)
        self._listeners: list[Callable[[LiveBarEvent], None]] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._apps: list[WebSocketApp] = []
        self._market_clients: dict[Exchange, object] = {
            Exchange.BINANCE: ccxt.binanceusdm({"enableRateLimit": True}),
            Exchange.BYBIT: ccxt.bybit(
                {
                    "enableRateLimit": True,
                    "options": {
                        "defaultType": "swap",
                        "defaultSubType": "linear",
                        "defaultSettle": "USDT",
                    },
                }
            ),
        }

    def subscribe(self, listener: Callable[[LiveBarEvent], None]) -> None:
        with self._lock:
            self._listeners.append(listener)
            if self._threads:
                return

            self._threads = [
                threading.Thread(target=self._run_binance, name="binance-ws", daemon=True),
                threading.Thread(target=self._run_bybit, name="bybit-ws", daemon=True),
            ]
            for thread in self._threads:
                thread.start()

    def close(self) -> None:
        self._stop_event.set()
        for app in list(self._apps):
            close = getattr(app, "close", None)
            if callable(close):
                close()
        for thread in self._threads:
            thread.join(timeout=1.0)

    def _notify(self, event: LiveBarEvent) -> None:
        listeners_snapshot = list(self._listeners)
        for listener in listeners_snapshot:
            try:
                listener(event)
            except Exception:  # pragma: no cover - defensive logging
                self._logger.exception("Ошибка обработчика бара %s", event.bar.bar_id)

    def _load_top_symbols(self, exchange: Exchange) -> list[str]:
        client = self._market_clients[exchange]
        tickers = client.fetch_tickers()  # type: ignore[attr-defined]
        ranked: list[tuple[str, float]] = []
        for symbol, ticker in tickers.items():
            base_symbol = symbol.split(":")[0]
            if not base_symbol.endswith("/USDT"):
                continue
            volume_candidates = [
                ticker.get("quoteVolume"),
                ticker.get("info", {}).get("quoteVolume"),
                ticker.get("info", {}).get("turnover"),
                ticker.get("info", {}).get("turnover24h"),
                ticker.get("info", {}).get("turnoverUsd24h"),
                ticker.get("info", {}).get("volume24h"),
            ]
            quote_volume = 0.0
            for candidate in volume_candidates:
                if candidate in (None, ""):
                    continue
                try:
                    quote_volume = float(candidate)
                except (TypeError, ValueError):
                    continue
                else:
                    break
            ranked.append((base_symbol, quote_volume))
        ranked.sort(key=lambda item: item[1], reverse=True)
        symbols = [symbol for symbol, _ in ranked[: self._top_n]]
        self._logger.info(
            "Выбраны пары %s (топ %s по объёму на %s)",
            symbols,
            self._top_n,
            exchange.value,
        )
        return symbols

    def _run_binance(self) -> None:
        symbols = self._load_top_symbols(Exchange.BINANCE)
        if not symbols:
            self._logger.warning("Нет доступных символов Binance для подписки")
            return

        symbol_streams: list[str] = []
        aggregators: dict[str, PrintsAggregator] = {}
        metrics_helpers: dict[str, _BarMetricsHelper] = {}
        for symbol in symbols:
            stream_symbol = symbol.replace("/", "").lower()
            raw_symbol = symbol.replace("/", "")
            aggregators[raw_symbol.upper()] = PrintsAggregator()
            metrics_helpers[raw_symbol.upper()] = _BarMetricsHelper()
            symbol_streams.append(f"{stream_symbol}@kline_{self._timeframe.value}")
            symbol_streams.append(f"{stream_symbol}@aggTrade")
        url = f"{self._BINANCE_WS}?streams={'/'.join(symbol_streams)}"

        state_last_bar: dict[str, int] = defaultdict(int)
        state_last_trade: dict[str, int] = defaultdict(int)

        def _on_message(_: WebSocketApp, message: str) -> None:
            payload = json.loads(message)
            data = payload.get("data", {})
            stream = payload.get("stream", "")
            if stream.endswith("aggTrade"):
                trade_id = int(data.get("a", 0))
                symbol = data.get("s", "")
                last_id = state_last_trade.get(symbol)
                if last_id == trade_id:
                    return
                state_last_trade[symbol] = trade_id
                aggregator = aggregators.get(symbol)
                if aggregator is None:
                    return
                timestamp_ms = int(data.get("T", 0))
                quantity = float(data.get("q", 0.0))
                if timestamp_ms == 0 or quantity <= 0.0:
                    return
                timestamp = _from_millis(timestamp_ms)
                is_buy = not bool(data.get("m", False))
                aggregator.add_print(
                    TradePrint(timestamp=timestamp, is_buy=is_buy, quantity=quantity)
                )
                return

            event = data.get("e")
            if event != "kline":
                return
            kline = data.get("k", {})
            is_closed = bool(kline.get("x"))
            if not is_closed:
                return
            symbol = kline.get("s", "")
            open_ts = int(kline.get("t", 0))
            if state_last_bar[symbol] == open_ts:
                return
            state_last_bar[symbol] = open_ts
            close_ts = int(kline.get("T", 0))
            ohlcv = [
                open_ts,
                float(kline.get("o", 0.0)),
                float(kline.get("h", 0.0)),
                float(kline.get("l", 0.0)),
                float(kline.get("c", 0.0)),
                float(kline.get("v", 0.0)),
            ]
            metrics_helper = metrics_helpers.get(symbol)
            bar = _bar_from_ohlcv(
                exchange=Exchange.BINANCE,
                symbol=_format_usdt_symbol(symbol),
                timeframe=self._timeframe,
                open_ts=open_ts,
                close_ts=close_ts,
                ohlcv=ohlcv,
                metrics_helper=metrics_helper,
            )
            aggregator = aggregators.get(symbol)
            imbalance = aggregator.imbalance(now=bar.close_time) if aggregator else 0.0
            if aggregator:
                aggregator.clear()
            self._notify(LiveBarEvent(bar=bar, imbalance=imbalance))

        def _on_error(_: WebSocketApp, error: Exception) -> None:
            self._logger.error("Ошибка Binance WS: %s", error)

        def _create_app() -> WebSocketApp:
            return WebSocketApp(url, on_message=_on_message, on_error=_on_error)

        self._run_with_reconnect("Binance", url, _create_app)

    def _run_bybit(self) -> None:
        symbols = self._load_top_symbols(Exchange.BYBIT)
        if not symbols:
            self._logger.warning("Нет доступных символов Bybit для подписки")
            return

        bybit_interval = self._to_bybit_interval(self._timeframe)
        topic_kline = [
            f"kline.{bybit_interval}.{symbol.replace('/', '')}"
            for symbol in symbols
        ]
        topic_trade = [f"publicTrade.{symbol.replace('/', '')}" for symbol in symbols]
        aggregators: dict[str, PrintsAggregator] = {
            symbol.replace("/", "").upper(): PrintsAggregator() for symbol in symbols
        }
        metrics_helpers: dict[str, _BarMetricsHelper] = {
            symbol.replace("/", "").upper(): _BarMetricsHelper() for symbol in symbols
        }
        subscribe_message = json.dumps({
            "op": "subscribe",
            "args": topic_kline + topic_trade,
        })

        state_last_bar: dict[str, int] = defaultdict(int)
        state_last_trade: dict[str, str] = {}

        def _on_open(ws: WebSocketApp) -> None:
            ws.send(subscribe_message)

        def _on_message(_: WebSocketApp, message: str) -> None:
            payload = json.loads(message)
            topic = payload.get("topic", "")
            if topic.startswith("publicTrade"):
                data = payload.get("data", [])
                if not data:
                    return
                symbol = payload.get("topic", "").split(".")[-1].upper()
                aggregator = aggregators.get(symbol)
                if aggregator is None:
                    return
                for trade in data:
                    trade_id = trade.get("i")
                    if trade_id is None:
                        continue
                    if state_last_trade.get(symbol) == trade_id:
                        continue
                    state_last_trade[symbol] = trade_id
                    quantity = float(trade.get("v", 0.0))
                    timestamp_ms = int(float(trade.get("T", 0.0)))
                    if quantity <= 0.0 or timestamp_ms == 0:
                        continue
                    timestamp = _from_millis(timestamp_ms)
                    is_buy = str(trade.get("S", "")).lower() == "buy"
                    aggregator.add_print(
                        TradePrint(timestamp=timestamp, is_buy=is_buy, quantity=quantity)
                    )
                return

            if not topic.startswith("kline"):
                return
            data = payload.get("data", [])
            if not data:
                return
            kline = data[0]
            if not bool(kline.get("confirm", False)):
                return
            symbol = topic.split(".")[-1].upper()
            open_ts = int(float(kline.get("start", 0.0)) * 1000)
            if state_last_bar[symbol] == open_ts:
                return
            state_last_bar[symbol] = open_ts
            close_ts = int(float(kline.get("end", 0.0)) * 1000)
            ohlcv = [
                open_ts,
                float(kline.get("open", 0.0)),
                float(kline.get("high", 0.0)),
                float(kline.get("low", 0.0)),
                float(kline.get("close", 0.0)),
                float(kline.get("volume", 0.0)),
            ]
            metrics_helper = metrics_helpers.get(symbol)
            bar = _bar_from_ohlcv(
                exchange=Exchange.BYBIT,
                symbol=_format_usdt_symbol(symbol),
                timeframe=self._timeframe,
                open_ts=open_ts,
                close_ts=close_ts,
                ohlcv=ohlcv,
                metrics_helper=metrics_helper,
            )
            aggregator = aggregators.get(symbol)
            imbalance = aggregator.imbalance(now=bar.close_time) if aggregator else 0.0
            if aggregator:
                aggregator.clear()
            self._notify(LiveBarEvent(bar=bar, imbalance=imbalance))

        def _on_error(_: WebSocketApp, error: Exception) -> None:
            self._logger.error("Ошибка Bybit WS: %s", error)

        def _create_app() -> WebSocketApp:
            return WebSocketApp(
                self._BYBIT_WS,
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
            )

        self._run_with_reconnect("Bybit", self._BYBIT_WS, _create_app)

    def _run_with_reconnect(
        self,
        name: str,
        url: str,
        factory: Callable[[], WebSocketApp],
    ) -> None:
        while not self._stop_event.is_set():
            self._logger.info("Подключаемся к %s WS: %s", name, url)
            app = factory()
            self._apps.append(app)
            app.run_forever(ping_interval=10, ping_timeout=5)
            self._apps.remove(app)
            if self._stop_event.is_set():
                break
            self._logger.info(
                "Переподключение к %s WS через %s с",
                name,
                RECONNECT_DELAY_SEC,
            )
            time.sleep(RECONNECT_DELAY_SEC)

    @staticmethod
    def _to_bybit_interval(timeframe: Timeframe) -> str:
        return str(timeframe.minutes)


def _bar_from_ohlcv(
    *,
    exchange: Exchange,
    symbol: str,
    timeframe: Timeframe,
    open_ts: int,
    close_ts: int,
    ohlcv: list[float],
    metrics_helper: _BarMetricsHelper | None = None,
) -> Bar:
    open_time = datetime.fromtimestamp(open_ts / 1000, tz=config.UTC)
    close_time = datetime.fromtimestamp(close_ts / 1000, tz=config.UTC)
    bar_id = f"{exchange.value}:{symbol}:{timeframe.value}:{int(open_time.timestamp())}"
    open_price = float(ohlcv[1])
    high = float(ohlcv[2])
    low = float(ohlcv[3])
    close = float(ohlcv[4])
    volume = float(ohlcv[5])
    metrics = (
        metrics_helper.calculate(
            open_price=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
        if metrics_helper
        else _EMPTY_METRICS
    )
    return Bar(
        bar_id=bar_id,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=close_time,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        metrics=metrics,
    )


def _to_millis(moment: datetime) -> int:
    if moment.tzinfo is None:
        aware = moment.replace(tzinfo=config.UTC)
    else:
        aware = moment.astimezone(config.UTC)
    return int(aware.timestamp() * 1000)


def _from_millis(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=config.UTC)


def _format_usdt_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    if symbol.endswith("-USDT"):
        return f"{symbol[:-5]}/USDT"
    return symbol
