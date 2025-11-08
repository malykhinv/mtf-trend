from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Deque,
    Dict,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)
from urllib.error import URLError
from urllib.request import urlopen

from config.config import (
    ALLOWED_GAP,
    CONFIG,
    MAX_ACTIVE_STREAMS,
)
from config.models.trading_profile import TradingProfile
from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink
from domain.models import Exchange, OrderBookLevel, OrderBookSnapshot, OrderBookUpdate, Side, SymbolFilters, Trade
from .binance_session import BinanceStreamSession, ResubscribeCooldownState, StreamRegistration
from .command_budget import CommandBudget
from .events import ResyncReason, StreamEvent, StreamEventType
from .limits import StreamLimit, StreamLimits, load_stream_limits
from .stream_buffer import StreamBuffer
from .stream_consumer import StreamConsumer, StreamValidationError
from .stream_metrics import StreamMetrics
from .stream_subscription import StreamLimitError, StreamSubscription


@dataclass(frozen=True, slots=True)
class BestBidAsk:
    bid_price: float
    ask_price: float
    event_time: datetime

    @property
    def bid(self) -> float:
        return self.bid_price

    @property
    def ask(self) -> float:
        return self.ask_price

    @property
    def timestamp(self) -> datetime:
        return self.event_time


DepthStreamData = OrderBookUpdate | OrderBookSnapshot


def _milliseconds_to_datetime(value: Any) -> datetime:
    try:
        millis = float(value)
    except (TypeError, ValueError):
        return datetime.now(tz=CURRENT_TIMEZONE)
    seconds = millis / 1000.0
    return datetime.fromtimestamp(seconds, tz=CURRENT_TIMEZONE)


def _build_level(price: float, quantity: float, timestamp: datetime) -> OrderBookLevel:
    notional = price * quantity
    return OrderBookLevel(
        price=price,
        quantity=quantity,
        notional=notional,
        first_seen_at=timestamp,
        last_update_at=timestamp,
        min_quantity_seen=quantity,
        max_quantity_seen=quantity,
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class BinanceSymbolStreams:
    exchange_data: "BinanceExchangeData"
    depth: "StreamSubscription[DepthStreamData]"
    trades: "StreamSubscription[Trade]"
    ticker: "StreamSubscription[BestBidAsk]"


class BinanceExchangeData:
    """A pragmatic placeholder implementation of the data interface."""

    _PROFILE_STREAM_WEIGHTS = CONFIG.profile_stream_weights
    PROFILE_WEIGHTS: Dict[str, Dict[str, float]] = {
        "depth": {
            TradingProfile.TOP.value: float(_PROFILE_STREAM_WEIGHTS.depth.top),
            TradingProfile.LISTING.value: float(
                _PROFILE_STREAM_WEIGHTS.depth.listing
            ),
            TradingProfile.ALT.value: float(_PROFILE_STREAM_WEIGHTS.depth.alt),
            TradingProfile.AUTO.value: float(_PROFILE_STREAM_WEIGHTS.depth.auto),
        },
        "trades": {
            TradingProfile.TOP.value: float(_PROFILE_STREAM_WEIGHTS.trades.top),
            TradingProfile.LISTING.value: float(
                _PROFILE_STREAM_WEIGHTS.trades.listing
            ),
            TradingProfile.ALT.value: float(_PROFILE_STREAM_WEIGHTS.trades.alt),
            TradingProfile.AUTO.value: float(_PROFILE_STREAM_WEIGHTS.trades.auto),
        },
        "book_ticker": {
            TradingProfile.TOP.value: float(
                _PROFILE_STREAM_WEIGHTS.book_ticker.top
            ),
            TradingProfile.LISTING.value: float(
                _PROFILE_STREAM_WEIGHTS.book_ticker.listing
            ),
            TradingProfile.ALT.value: float(
                _PROFILE_STREAM_WEIGHTS.book_ticker.alt
            ),
            TradingProfile.AUTO.value: float(
                _PROFILE_STREAM_WEIGHTS.book_ticker.auto
            ),
        },
    }

    @classmethod
    def get_profile_weight(
            cls,
            stream: str,
            profile: TradingProfile | str,
    ) -> float:
        profile_key = profile.value if isinstance(profile, TradingProfile) else str(profile)
        stream_weights = cls.PROFILE_WEIGHTS.get(stream, {})
        return float(stream_weights.get(profile_key, 0.0))

    def __init__(
            self,
            *,
            symbol: str,
            loop_interval_ms: int,
            silence_timeout_ms: int,
            log_writer: LogSink,
            api_key: Optional[str] = None,
            api_secret: Optional[str] = None,
            exchange_info: Optional[Mapping[str, Mapping[str, object]]] = None,
    ) -> None:
        self._symbol = symbol.upper()
        self._loop_interval = max(loop_interval_ms, 100)
        self._silence_timeout = max(silence_timeout_ms, 1_000)
        self._log_writer = log_writer
        self._api_key = api_key
        self._api_secret = api_secret
        self._limits: StreamLimits = load_stream_limits()
        self._exchange_info: Dict[str, Dict[str, object]] = {}
        self._metrics: Dict[str, StreamMetrics] = {
            "depth": StreamMetrics("depth"),
            "trades": StreamMetrics("trades"),
            "book_ticker": StreamMetrics("book_ticker"),
        }
        self._command_budgets: Dict[str, CommandBudget] = {
            "depth": CommandBudget(self._limits.depth),
            "trades": CommandBudget(self._limits.trades),
            "book_ticker": CommandBudget(self._limits.book_ticker),
        }
        depth_update_ms = getattr(CONFIG.general, "binance_depth_update_ms", 100)
        try:
            depth_update_ms = int(depth_update_ms)
        except (TypeError, ValueError):
            depth_update_ms = 100
        if depth_update_ms <= 0:
            depth_update_ms = 100
        self._depth_update_ms = depth_update_ms
        if exchange_info:
            self.update_exchange_info(exchange_info)
        if self._symbol not in self._exchange_info:
            self._load_exchange_info()

    # ------------------------------------------------------------------
    # Interface implementation expected by the application
    def fetch_symbol_filters(self) -> SymbolFilters:
        info = self._exchange_info.get(self._symbol)
        if not isinstance(info, Mapping):
            try:
                self._log_writer(
                    f"[ERROR] missing exchange info for {self._symbol}, refreshing cache"
                )
            except Exception:
                pass
            try:
                self._load_exchange_info()
            except Exception as exc:  # pragma: no cover - network safety
                try:
                    self._log_writer(
                        f"[ERROR] failed to refresh exchange info for {self._symbol}: {exc}"
                    )
                except Exception:
                    pass
            info = self._exchange_info.get(self._symbol)
            if not isinstance(info, Mapping):
                try:
                    self._log_writer(
                        f"[CRITICAL] exchange info unavailable for {self._symbol}; aborting"
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    f"binance exchange info unavailable for {self._symbol}"
                )
        base_asset = self._symbol.replace("USDT", "")
        quote_asset = "USDT"
        price_tick = 0.1
        qty_step = 0.001
        min_notional = 5.0
        min_price = 0.0
        max_price = math.inf
        min_qty = qty_step
        max_qty = math.inf
        if isinstance(info, Mapping):
            base_asset = str(info.get("baseAsset", base_asset))
            quote_asset = str(info.get("quoteAsset", quote_asset))
            filters = info.get("filters")
            if isinstance(filters, Mapping):
                price_filter = filters.get("price")
                if isinstance(price_filter, Mapping):
                    price_tick = _safe_float(price_filter.get("tickSize"), price_tick)
                    min_price = _safe_float(price_filter.get("minPrice"), min_price)
                    max_price_value = _safe_float(price_filter.get("maxPrice"), max_price)
                    if max_price_value > 0.0:
                        max_price = max_price_value
                lot_filter = filters.get("lot")
                if isinstance(lot_filter, Mapping):
                    qty_step = _safe_float(lot_filter.get("stepSize"), qty_step)
                    min_qty_value = _safe_float(lot_filter.get("minQty"), min_qty)
                    min_qty = max(qty_step, min_qty_value)
                    max_qty_value = _safe_float(lot_filter.get("maxQty"), max_qty)
                    if max_qty_value > 0.0:
                        max_qty = max_qty_value
                notional_filter = filters.get("notional")
                if isinstance(notional_filter, Mapping):
                    min_notional = _safe_float(
                        notional_filter.get("minNotional"),
                        min_notional,
                    )
            price_tick = _safe_float(info.get("tickSize"), price_tick)
            qty_step = _safe_float(info.get("stepSize"), qty_step)
            min_notional = _safe_float(info.get("notional"), min_notional)
        return SymbolFilters(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_tick_size=price_tick,
            quantity_step_size=qty_step,
            min_price=min_price,
            max_price=max_price,
            min_qty=min_qty,
            max_qty=max_qty,
            min_notional=min_notional,
        )

    def _depth_stream_param(self) -> str:
        interval = max(int(self._depth_update_ms), 100)
        return f"{self._symbol.lower()}@depth@{interval}ms"

    def update_exchange_info(self, info: Mapping[str, Mapping[str, object]]) -> None:
        if not info:
            return
        updates: Dict[str, Dict[str, object]] = {}
        for symbol, entry in info.items():
            normalized = symbol.upper()
            if not normalized:
                continue
            if not isinstance(entry, Mapping):
                continue
            current = self._exchange_info.get(normalized)
            merged: Dict[str, object] = {}
            if isinstance(current, Mapping):
                merged.update(current)
            merged.update(dict(entry))
            updates[normalized] = merged
        if updates:
            self._exchange_info.update(updates)

    def set_symbol_profile(self, symbol: str, profile: TradingProfile) -> None:
        normalized = symbol.upper()
        entry = self._exchange_info.get(normalized)
        if isinstance(entry, Mapping):
            updated = dict(entry)
        else:
            updated = {}
        updated["profile"] = profile
        self._exchange_info[normalized] = updated

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        url = (
            "https://fapi.binance.com/fapi/v1/depth"
            f"?symbol={self._symbol}&limit=1000"
        )
        timeout_s = getattr(CONFIG.general, "orderbook_snapshot_timeout_s", 5.0)
        max_attempts = 3
        base_delay = 0.5
        last_exception: Exception | None = None
        payload: Mapping[str, Any] | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                with urlopen(url, timeout=timeout_s) as response:  # noqa: S310
                    payload = json.load(response)
                break
            except (
                    URLError,
                    TimeoutError,
                    OSError,
                    json.JSONDecodeError,
            ) as exc:  # pragma: no cover - network
                last_exception = exc
                if attempt < max_attempts:
                    backoff = base_delay * (2 ** (attempt - 1))
                    try:
                        self._log_writer(
                            (
                                f"[WARNING] depth snapshot attempt {attempt} failed for"
                                f" {self._symbol}: {exc}. Retrying in {backoff:.2f}s"
                            )
                        )
                    except Exception:  # pragma: no cover - logging
                        pass
                    time.sleep(backoff)
                else:
                    try:
                        self._log_writer(
                            f"[ERROR] failed to fetch depth snapshot for {self._symbol}: {exc}"
                        )
                    except Exception:  # pragma: no cover - logging
                        pass

        if payload is None:
            assert last_exception is not None
            raise RuntimeError(
                f"failed to fetch orderbook snapshot for {self._symbol}"
            ) from last_exception

        if not isinstance(payload, Mapping):
            raise RuntimeError("depth snapshot payload malformed")

        try:
            last_update_id = int(payload.get("lastUpdateId"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("depth snapshot missing lastUpdateId") from exc

        received_at = datetime.now(tz=CURRENT_TIMEZONE)

        def build_levels(entries: Any, side: str) -> Tuple[OrderBookLevel, ...]:
            if not isinstance(entries, Iterable) or isinstance(entries, (str, bytes)):
                return ()
            levels: list[OrderBookLevel] = []
            for entry in entries:
                if not isinstance(entry, (Sequence, list, tuple)) or len(entry) < 2:
                    continue
                price_raw, quantity_raw = entry[0], entry[1]
                try:
                    price = float(price_raw)
                    quantity = float(quantity_raw)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"depth snapshot {side} level malformed"
                    ) from exc
                if not math.isfinite(price) or not math.isfinite(quantity):
                    raise RuntimeError(
                        f"depth snapshot {side} level non finite"
                    )
                if quantity < 0:
                    raise RuntimeError(
                        f"depth snapshot {side} level negative quantity"
                    )
                levels.append(_build_level(price, quantity, received_at))
            return tuple(levels)

        bids = build_levels(payload.get("bids"), "bid")
        asks = build_levels(payload.get("asks"), "ask")

        return OrderBookSnapshot(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            last_update_id=last_update_id,
            bids=bids,
            asks=asks,
            received_at=received_at,
        )

    def fetch_next_funding_time(self) -> Optional[datetime]:
        url = (
            "https://fapi.binance.com/fapi/v1/premiumIndex"
            f"?symbol={self._symbol}"
        )
        timeout_s = getattr(CONFIG.general, "orderbook_snapshot_timeout_s", 5.0)
        max_attempts = 3
        base_delay = 0.5
        payload: Any | None = None
        last_exception: Exception | None = None

        def _log(message: str) -> None:
            try:
                self._log_writer(message)
            except Exception:  # pragma: no cover - defensive logging
                pass

        for attempt in range(1, max_attempts + 1):
            try:
                with urlopen(url, timeout=timeout_s) as response:  # noqa: S310
                    payload = json.load(response)
                break
            except (
                    URLError,
                    TimeoutError,
                    OSError,
                    json.JSONDecodeError,
            ) as exc:  # pragma: no cover - network access
                last_exception = exc
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    _log(
                        (
                            f"[WARNING] funding info attempt {attempt} failed for "
                            f"{self._symbol}: {exc}. Retrying in {delay:.2f}s"
                        )
                    )
                    time.sleep(delay)
                else:
                    _log(
                        (
                            f"[ERROR] funding info attempt {attempt} failed for "
                            f"{self._symbol}: {exc}"
                        )
                    )

        if payload is None:
            if last_exception is not None:
                raise RuntimeError(
                    f"failed to fetch funding info for {self._symbol}"
                ) from last_exception
            raise RuntimeError(f"funding info unavailable for {self._symbol}")

        entries: Tuple[Mapping[str, Any], ...]
        if isinstance(payload, Mapping):
            entries = (payload,)
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            entries = tuple(
                entry for entry in payload if isinstance(entry, Mapping)
            )
        else:
            raise RuntimeError("funding info payload malformed")

        if not entries:
            raise RuntimeError("funding info payload empty")

        symbol = self._symbol.upper()
        entry = next(
            (
                item
                for item in entries
                if str(item.get("symbol") or "").upper() == symbol
            ),
            None,
        )
        if entry is None:
            entry = entries[0]
            entry_symbol = str(entry.get("symbol") or "").upper()
            if entry_symbol and entry_symbol != symbol:
                _log(
                    (
                        "[WARNING] funding info symbol mismatch: expected "
                        f"{symbol}, received {entry_symbol}"
                    )
                )

        raw_timestamp = (
                entry.get("nextFundingTime")
                or entry.get("nextFundingTimestamp")
                or entry.get("fundingTime")
        )
        if raw_timestamp is None:
            raise RuntimeError("funding info missing next funding timestamp")

        try:
            timestamp_value = float(raw_timestamp)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("funding info invalid next funding timestamp") from exc

        if not math.isfinite(timestamp_value) or timestamp_value <= 0:
            raise RuntimeError("funding info invalid next funding timestamp")

        seconds = timestamp_value / 1000.0 if timestamp_value >= 1e12 else timestamp_value
        funding_time = datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(
            CURRENT_TIMEZONE
        )
        return funding_time

    def _stream_iterator(
            self, buffer: StreamBuffer[T]
    ) -> Iterator[StreamEvent[T]]:
        interval = self._loop_interval / 1000.0
        while True:
            pending = buffer.drain_pending()
            if pending:
                for event in pending:
                    yield event
                continue
            time.sleep(interval)

    def _register_depth_stream(self) -> StreamRegistration[DepthStreamData]:
        symbol_stream = self._depth_stream_param()
        buffer: StreamBuffer[DepthStreamData] = StreamBuffer()
        last_final_id: Optional[int] = None
        last_event_time: Optional[datetime] = None
        snapshot_ready = False
        pending_updates: Deque[tuple[OrderBookUpdate, int]] = deque()

        def apply_update(
                update: OrderBookUpdate,
                prev_final: int,
        ) -> Iterable[StreamEvent[DepthStreamData]]:
            nonlocal last_final_id, last_event_time
            if last_final_id is not None:
                if update.last_update_id <= last_final_id:
                    return ()
                expected_next = last_final_id + 1
                if prev_final > last_final_id:
                    missing = prev_final - last_final_id
                    gap_seconds_text = "н/д"
                    if last_event_time is not None:
                        gap_seconds = max(
                            (update.event_time - last_event_time).total_seconds(),
                            0.0,
                        )
                        gap_seconds_text = f"{gap_seconds:.3f}"
                    raise StreamValidationError(
                        ResyncReason.SEQUENCE_GAP,
                        (
                            "depth sequence gap: "
                            f"пропущено {missing} обновлений, пауза {gap_seconds_text}с"
                        ),
                    )
                if (
                        prev_final < last_final_id
                        and not (update.first_update_id <= expected_next <= update.last_update_id)
                ):
                    raise StreamValidationError(
                        ResyncReason.SEQUENCE_GAP,
                        (
                            "depth sequence gap: "
                            f"missing {expected_next} in update range "
                            f"[{update.first_update_id}, {update.last_update_id}]"
                        ),
                    )
            event = StreamEvent(
                StreamEventType.DATA,
                update,
                update.event_time,
            )
            last_final_id = update.last_update_id
            last_event_time = update.event_time
            return (event,)

        def snapshot_factory() -> tuple[
            Optional[StreamEvent[DepthStreamData]],
            Iterable[StreamEvent[DepthStreamData]],
        ]:
            nonlocal last_final_id, last_event_time, snapshot_ready
            snapshot_ready = False
            snapshot = self.fetch_orderbook_snapshot()
            last_final_id = snapshot.last_update_id
            last_event_time = snapshot.received_at
            replay_events: list[StreamEvent[DepthStreamData]] = []
            while pending_updates:
                update, prev_final = pending_updates.popleft()
                try:
                    events = apply_update(update, prev_final)
                except StreamValidationError:
                    pending_updates.clear()
                    last_final_id = None
                    snapshot_ready = False
                    raise
                if not events:
                    continue
                replay_events.extend(events)
            snapshot_ready = True
            snapshot_event = StreamEvent(
                StreamEventType.SNAPSHOT,
                snapshot,
                snapshot.received_at,
                details=f"replay={len(replay_events)}",
            )
            return snapshot_event, tuple(replay_events)

        def parser(message: Dict[str, Any]) -> Iterable[StreamEvent[DepthStreamData]]:
            nonlocal last_final_id, snapshot_ready
            event_type = str(message.get("e", "")).lower()
            if event_type != "depthupdate":
                return ()
            try:
                final_id = int(message.get("u"))
                prev_final = int(message.get("pu", final_id - 1))
                first_id = int(message.get("U", final_id))
            except (TypeError, ValueError):
                raise StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    "depth update ids missing",
                )
            event_time = _milliseconds_to_datetime(message.get("E"))
            bids = self._parse_levels(message.get("b", ()), event_time, "bid")
            asks = self._parse_levels(message.get("a", ()), event_time, "ask")
            update = OrderBookUpdate(
                exchange=Exchange.BINANCE,
                symbol=self._symbol,
                first_update_id=first_id,
                last_update_id=final_id,
                bids=bids,
                asks=asks,
                event_time=event_time,
            )
            if not snapshot_ready:
                pending_updates.append((update, prev_final))
                return ()
            return apply_update(update, prev_final)

        consumer = StreamConsumer(
            stream="depth",
            symbol=self._symbol,
            params=(symbol_stream,),
            buffer=buffer,
            parser=parser,
            metrics=self._metrics["depth"],
            log=self._log_writer,
            silence_timeout_ms=self._silence_timeout,
            snapshot_factory=snapshot_factory,
        )
        subscription = StreamSubscription(
            self._stream_iterator(buffer),
            buffer,
        )
        return StreamRegistration(consumer=consumer, subscription=subscription)

    def _register_trades_stream(self) -> StreamRegistration[Trade]:
        symbol_stream = f"{self._symbol.lower()}@aggTrade"
        last_trade_id: Optional[int] = None
        last_trade_time: Optional[datetime] = None

        def reset_trade_sequence_state() -> None:
            nonlocal last_trade_id
            nonlocal last_trade_time
            last_trade_id = None
            last_trade_time = None

        def parser(message: Dict[str, Any]) -> Iterable[StreamEvent[Trade]]:
            nonlocal last_trade_id
            nonlocal last_trade_time
            event_type = str(message.get("e", "")).lower()
            if event_type != "aggtrade":
                return ()
            try:
                trade_id_raw = message.get("a")
                trade_id = int(trade_id_raw)
            except (TypeError, ValueError):
                reset_trade_sequence_state()
                raise StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    "trade id missing",
                )
            event_time = _milliseconds_to_datetime(message.get("T"))
            if last_trade_id is not None:
                gap = trade_id - last_trade_id
                if gap <= 0:
                    reset_trade_sequence_state()
                    raise StreamValidationError(
                        ResyncReason.SEQUENCE_GAP,
                        "out-of-order trade sequence",
                    )
                if gap == 1:
                    pass
                elif gap <= ALLOWED_GAP:
                    pause_seconds: Optional[float]
                    if last_trade_time is not None:
                        pause_seconds = max(
                            (event_time - last_trade_time).total_seconds(),
                            0.0,
                        )
                    else:
                        pause_seconds = None
                    pause_suffix = (
                        f", пауза {pause_seconds:.2f}с"
                        if pause_seconds is not None
                        else ", пауза н/д"
                    )
                    try:
                        self._log_writer(
                            (
                                f"Поток сделок {self._symbol}: пропущено"
                                f" {gap - 1} id",
                                f" (gap={gap}, допуск до {ALLOWED_GAP}{pause_suffix}).",
                            )
                        )
                    except Exception:
                        pass
                else:
                    reset_trade_sequence_state()
                    raise StreamValidationError(
                        ResyncReason.SEQUENCE_GAP,
                        "trade sequence gap exceeds allowance",
                    )
            price = _safe_float(message.get("p"), 0.0)
            quantity = _safe_float(message.get("q"), 0.0)
            is_buyer_maker = bool(message.get("m", False))
            # Binance reports whether the buyer was the market maker. When the buyer is
            # the maker, the aggressive order was a sell (ask); otherwise, it was a buy
            # (bid). The Side enum only defines BID/ASK, so convert the flag
            # accordingly.
            side = Side.ASK if is_buyer_maker else Side.BID
            trade = Trade(
                exchange=Exchange.BINANCE,
                symbol=self._symbol,
                trade_id=str(trade_id),
                price=price,
                quantity=quantity,
                side=side,
                executed_at=event_time,
            )
            last_trade_time = event_time
            last_trade_id = trade_id
            return (StreamEvent(StreamEventType.DATA, trade, event_time),)

        buffer: StreamBuffer[Trade] = StreamBuffer()
        consumer = StreamConsumer(
            stream="trades",
            symbol=self._symbol,
            params=(symbol_stream,),
            buffer=buffer,
            parser=parser,
            metrics=self._metrics["trades"],
            log=self._log_writer,
            silence_timeout_ms=self._silence_timeout,
        )
        subscription = StreamSubscription(
            self._stream_iterator(buffer),
            buffer,
        )
        return StreamRegistration(consumer=consumer, subscription=subscription)

    def _register_ticker_stream(self) -> StreamRegistration[BestBidAsk]:
        symbol_stream = f"{self._symbol.lower()}@bookTicker"

        def parser(message: Dict[str, Any]) -> Iterable[StreamEvent[BestBidAsk]]:
            event_type = str(message.get("e", "")).lower()
            if event_type != "bookticker":
                return ()
            best_bid = _safe_float(message.get("b", 0.0), 0.0)
            best_ask = _safe_float(message.get("a", 0.0), 0.0)
            event_time = _milliseconds_to_datetime(message.get("E"))
            ticker = BestBidAsk(
                bid_price=best_bid,
                ask_price=best_ask,
                event_time=event_time,
            )
            return (StreamEvent(StreamEventType.DATA, ticker, event_time),)

        buffer: StreamBuffer[BestBidAsk] = StreamBuffer()
        consumer = StreamConsumer(
            stream="book_ticker",
            symbol=self._symbol,
            params=(symbol_stream,),
            buffer=buffer,
            parser=parser,
            metrics=self._metrics["book_ticker"],
            log=self._log_writer,
            silence_timeout_ms=self._silence_timeout,
        )
        subscription = StreamSubscription(
            self._stream_iterator(buffer),
            buffer,
        )
        return StreamRegistration(consumer=consumer, subscription=subscription)

    def create_stream_bundle(
            self,
    ) -> tuple[
        BinanceSymbolStreams,
        Dict[str, StreamRegistration[Any]],
    ]:
        depth_registration = self._register_depth_stream()
        trades_registration = self._register_trades_stream()
        ticker_registration = self._register_ticker_stream()
        streams = BinanceSymbolStreams(
            exchange_data=self,
            depth=depth_registration.subscription,
            trades=trades_registration.subscription,
            ticker=ticker_registration.subscription,
        )
        registrations: Dict[str, StreamRegistration[Any]] = {
            "depth": depth_registration,
            "trades": trades_registration,
            "book_ticker": ticker_registration,
        }
        return streams, registrations

    def stream_depth(self) -> StreamSubscription[DepthStreamData]:
        streams, registrations = self.create_stream_bundle()
        depth_registration = registrations.get("depth")
        if depth_registration is not None:
            depth_registration.consumer.push_snapshot()
        return streams.depth

    def stream_trades(self) -> StreamSubscription[Trade]:
        streams, _ = self.create_stream_bundle()
        return streams.trades

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        streams, _ = self.create_stream_bundle()
        return streams.ticker

    # ------------------------------------------------------------------
    def _parse_levels(
            self,
            entries: Iterable[Iterable[Any]],
            timestamp: datetime,
            side: str,
    ) -> Tuple[OrderBookLevel, ...]:
        levels: list[OrderBookLevel] = []
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            price_raw, quantity_raw = entry[0], entry[1]
            try:
                price = float(price_raw)
                quantity = float(quantity_raw)
            except (TypeError, ValueError):
                raise StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"{side} level malformed",
                )
            if not math.isfinite(price) or not math.isfinite(quantity):
                raise StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"{side} level non finite",
                )
            if quantity < 0:
                raise StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"{side} level negative quantity",
                )
            levels.append(_build_level(price, quantity, timestamp))
        return tuple(levels)

    def _load_exchange_info(self) -> None:
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        try:
            with urlopen(url, timeout=5) as response:  # noqa: S310
                payload = json.load(response)
        except (URLError, TimeoutError, ValueError, OSError):
            return
        symbols: Sequence[Mapping[str, object]] = ()
        if isinstance(payload, Mapping):
            raw_symbols = payload.get("symbols")
            if isinstance(raw_symbols, Sequence) and not isinstance(raw_symbols, (str, bytes)):
                symbols = tuple(
                    entry
                    for entry in raw_symbols
                    if isinstance(entry, Mapping)
                )
        existing = self._exchange_info
        info: Dict[str, Dict[str, object]] = {}
        for entry in symbols:
            status = str(entry.get("status") or "").upper()
            if status != "TRADING":
                continue
            contract_status = str(entry.get("contractStatus") or "").upper()
            if contract_status and contract_status != "TRADING":
                continue
            contract_type = str(entry.get("contractType") or "").upper()
            if contract_type not in {"PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER"}:
                continue
            permissions = entry.get("permissions")
            if not (
                    isinstance(permissions, Sequence)
                    and not isinstance(permissions, (str, bytes))
            ):
                continue
            normalized_permissions = {str(value).upper() for value in permissions}
            if not normalized_permissions.intersection({"UMFUTURE", "CMFUTURE"}):
                continue
            permissions_tuple: Tuple[str, ...] = tuple(str(value) for value in permissions)
            symbol = str(entry.get("symbol") or "").upper()
            if not symbol:
                continue
            filter_entries: Sequence[Mapping[str, object]] = ()
            filters = entry.get("filters")
            if isinstance(filters, Sequence) and not isinstance(filters, (str, bytes)):
                filter_entries = tuple(
                    item for item in filters if isinstance(item, Mapping)
                )

            def _find_filter(filter_type: str) -> Mapping[str, object]:
                return next(
                    (
                        item
                        for item in filter_entries
                        if str(item.get("filterType") or "").upper() == filter_type
                    ),
                    {},
                )

            price_filter: Mapping[str, object] = _find_filter("PRICE_FILTER")
            lot_filter: Mapping[str, object] = _find_filter("LOT_SIZE")
            market_lot_filter: Mapping[str, object] = _find_filter("MARKET_LOT_SIZE")
            notional_filter: Mapping[str, object] = next(
                (
                    item
                    for item in filter_entries
                    if str(item.get("filterType") or "").upper() in {"MIN_NOTIONAL", "NOTIONAL"}
                ),
                {},
            )
            percent_price_filter: Mapping[str, object] = _find_filter("PERCENT_PRICE")
            current = existing.get(symbol) if isinstance(existing, Mapping) else None
            previous_tick = 0.1
            previous_step = 0.001
            previous_market_step = previous_step
            previous_notional = 5.0
            previous_multiplier_down = 0.0
            previous_multiplier_up = 0.0
            if isinstance(current, Mapping):
                current_filters = current.get("filters")
                if isinstance(current_filters, Mapping):
                    prev_price = current_filters.get("price")
                    if isinstance(prev_price, Mapping):
                        previous_tick = _safe_float(prev_price.get("tickSize"), previous_tick)
                    prev_lot = current_filters.get("lot")
                    if isinstance(prev_lot, Mapping):
                        previous_step = _safe_float(prev_lot.get("stepSize"), previous_step)
                    prev_market_lot = current_filters.get("marketLot")
                    if isinstance(prev_market_lot, Mapping):
                        previous_market_step = _safe_float(
                            prev_market_lot.get("stepSize"), previous_market_step
                        )
                    prev_notional = current_filters.get("notional")
                    if isinstance(prev_notional, Mapping):
                        previous_notional = _safe_float(
                            prev_notional.get("minNotional"), previous_notional
                        )
                    prev_percent = current_filters.get("percentPrice")
                    if isinstance(prev_percent, Mapping):
                        previous_multiplier_down = _safe_float(
                            prev_percent.get("multiplierDown"), previous_multiplier_down
                        )
                        previous_multiplier_up = _safe_float(
                            prev_percent.get("multiplierUp"), previous_multiplier_up
                        )
                else:
                    previous_tick = _safe_float(current.get("tickSize"), previous_tick)
                    previous_step = _safe_float(current.get("stepSize"), previous_step)
                    previous_notional = _safe_float(current.get("notional"), previous_notional)
            min_price_value = max(_safe_float(price_filter.get("minPrice"), 0.0), 0.0)
            max_price_value = _safe_float(price_filter.get("maxPrice"), math.inf)
            if max_price_value <= 0.0:
                max_price_value = math.inf
            price_filters = {
                "minPrice": min_price_value,
                "maxPrice": max_price_value,
                "tickSize": max(_safe_float(price_filter.get("tickSize"), previous_tick), 10 ** -8),
            }
            step_size_value = max(_safe_float(lot_filter.get("stepSize"), previous_step), 10 ** -8)
            min_qty_value = max(_safe_float(lot_filter.get("minQty"), previous_step), step_size_value)
            max_qty_value = _safe_float(lot_filter.get("maxQty"), math.inf)
            if max_qty_value <= 0.0:
                max_qty_value = math.inf
            lot_filters = {
                "minQty": min_qty_value,
                "maxQty": max_qty_value,
                "stepSize": step_size_value,
            }
            market_step_size_value = max(
                _safe_float(market_lot_filter.get("stepSize"), previous_market_step),
                10 ** -8,
            )
            market_min_qty_value = max(
                _safe_float(market_lot_filter.get("minQty"), market_step_size_value),
                market_step_size_value,
            )
            market_max_qty_value = _safe_float(market_lot_filter.get("maxQty"), math.inf)
            if market_max_qty_value <= 0.0:
                market_max_qty_value = math.inf
            market_lot_filters = {
                "minQty": market_min_qty_value,
                "maxQty": market_max_qty_value,
                "stepSize": market_step_size_value,
            }
            min_notional_value = max(
                _safe_float(notional_filter.get("minNotional"), previous_notional),
                0.0,
            )
            notional_filters = {
                "minNotional": min_notional_value,
            }
            multiplier_down_value = max(
                _safe_float(percent_price_filter.get("multiplierDown"), previous_multiplier_down),
                0.0,
            )
            multiplier_up_value = max(
                _safe_float(percent_price_filter.get("multiplierUp"), previous_multiplier_up),
                0.0,
            )
            percent_price_filters = {
                "multiplierDown": multiplier_down_value,
                "multiplierUp": multiplier_up_value,
                "multiplierDecimal": percent_price_filter.get("multiplierDecimal"),
            }
            entry_info: Dict[str, object] = {
                "baseAsset": str(entry.get("baseAsset", "")),
                "quoteAsset": str(entry.get("quoteAsset", "")),
                "marginAsset": str(entry.get("marginAsset", "")),
                "contractType": contract_type,
                "contractStatus": contract_status or status,
                "tickSize": price_filters["tickSize"],
                "stepSize": lot_filters["stepSize"],
                "marketStepSize": market_lot_filters["stepSize"],
                "notional": notional_filters["minNotional"],
                "filters": {
                    "price": price_filters,
                    "lot": lot_filters,
                    "marketLot": market_lot_filters,
                    "notional": notional_filters,
                    "percentPrice": percent_price_filters,
                },
                "meta": {
                    "status": status,
                    "contractStatus": contract_status or status,
                    "contractType": contract_type,
                    "onboardDate": entry.get("onboardDate"),
                    "permissions": permissions_tuple,
                    "baseAssetPrecision": entry.get("baseAssetPrecision"),
                    "quoteAssetPrecision": entry.get("quoteAssetPrecision"),
                    "quotePrecision": entry.get("quotePrecision"),
                    "pricePrecision": entry.get("pricePrecision"),
                    "quantityPrecision": entry.get("quantityPrecision"),
                    "deliveryDate": entry.get("deliveryDate"),
                    "pair": entry.get("pair"),
                    "contractSize": entry.get("contractSize"),
                    "maintMarginPercent": entry.get("maintMarginPercent"),
                    "requiredMarginPercent": entry.get("requiredMarginPercent"),
                    "triggerProtect": entry.get("triggerProtect"),
                    "underlyingSubType": entry.get("underlyingSubType"),
                    "underlyingType": entry.get("underlyingType"),
                    "liquidationFee": entry.get("liquidationFee"),
                    "marketTakeBound": entry.get("marketTakeBound"),
                },
            }
            if isinstance(current, Mapping) and "profile" in current:
                entry_info["profile"] = current["profile"]
            info[symbol] = entry_info
        if info:
            self._exchange_info = info


class BinanceStreamManager:
    """Manages Binance stream subscriptions with centralized limits."""

    def __init__(
            self,
            *,
            loop_interval_ms: int,
            silence_timeout_ms: int,
            log_writer: LogSink,
            api_key: Optional[str] = None,
            api_secret: Optional[str] = None,
    ) -> None:
        self._loop_interval_ms = loop_interval_ms
        self._silence_timeout_ms = silence_timeout_ms
        self._log_writer = log_writer
        self._api_key = api_key
        self._api_secret = api_secret
        self._limits = load_stream_limits()
        self._limit_map: Dict[str, StreamLimit] = {
            "depth": self._limits.depth,
            "trades": self._limits.trades,
            "book_ticker": self._limits.book_ticker,
        }
        self._exchange_data: Dict[str, BinanceExchangeData] = {}
        self._streams: Dict[str, BinanceSymbolStreams] = {}
        self._profiles: Dict[str, TradingProfile] = {}
        self._weights: Dict[str, Dict[str, float]] = {}
        self._sessions: Dict[str, list[BinanceStreamSession]] = {}
        self._symbol_consumers: Dict[
            str, Dict[str, tuple[BinanceStreamSession, StreamConsumer[Any]]]
        ] = {}
        self._exchange_info_cache: Dict[str, Dict[str, object]] = {}
        self._active: set[str] = set()
        self._max_active_streams = MAX_ACTIVE_STREAMS
        self._resubscribe_state: Dict[str, ResubscribeCooldownState] = {}
        self._backoff_base_seconds = max(float(self._silence_timeout_ms) / 1000.0, 1.0)
        self._backoff_max_seconds = 300.0
        self._backoff_reset_after = timedelta(minutes=10)
        for name in self._limit_map:
            self._weights[name] = {}
            session = self._create_session(name)
            self._sessions[name] = [session]

    def _create_session(self, stream: str) -> BinanceStreamSession:
        session = BinanceStreamSession(
            stream=stream,
            limit=self._limit_map[stream],
            silence_timeout_ms=self._silence_timeout_ms,
            log_writer=self._log_writer,
        )
        session.start()
        return session

    def update_profiles(self, profiles: Mapping[str, TradingProfile]) -> None:
        for symbol, profile in profiles.items():
            normalized = symbol.upper()
            self._profiles[normalized] = profile
            exchange_data = self._exchange_data.get(normalized)
            if exchange_data is not None:
                exchange_data.set_symbol_profile(normalized, profile)

    def update_weights(self, weights: Mapping[str, Mapping[str, float] | float]) -> None:
        for symbol, weight_map in weights.items():
            normalized = symbol.upper()
            if not normalized:
                continue
            if isinstance(weight_map, Mapping):
                for stream, value in weight_map.items():
                    if stream not in self._limit_map:
                        continue
                    stream_weights = self._weights.setdefault(stream, {})
                    stream_weights[normalized] = float(value)
                continue
            for stream in self._limit_map:
                stream_weights = self._weights.setdefault(stream, {})
                stream_weights[normalized] = float(weight_map)

    def update_exchange_info(self, info: Mapping[str, Mapping[str, object]]) -> None:
        if not info:
            return
        normalized: Dict[str, Dict[str, object]] = {}
        for symbol, entry in info.items():
            normalized_symbol = symbol.upper()
            if not normalized_symbol:
                continue
            if not isinstance(entry, Mapping):
                continue
            normalized[normalized_symbol] = dict(entry)
        if not normalized:
            return
        self._exchange_info_cache = normalized
        for exchange_data in self._exchange_data.values():
            exchange_data.update_exchange_info(normalized)

    def plan_subscriptions(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        if not symbols:
            return symbols
        streams_per_symbol = len(self._limit_map)
        if streams_per_symbol <= 0:
            streams_per_symbol = 1
        burst_limits = [
            limit.burst_per_5s
            for limit in self._limit_map.values()
            if limit.burst_per_5s > 0
        ]
        steady_limits = [
            limit.steady_per_min
            for limit in self._limit_map.values()
            if limit.steady_per_min > 0
        ]
        burst_capacity = min(burst_limits) if burst_limits else float("inf")
        steady_capacity = min(steady_limits) if steady_limits else float("inf")
        command_capacity = min(burst_capacity, steady_capacity)
        if math.isinf(command_capacity):
            max_new_batch: Optional[int] = None
        else:
            max_new_batch = int(command_capacity // streams_per_symbol)
        if max_new_batch is not None and max_new_batch <= 0:
            return tuple()
        available_weight_by_stream = {
            stream: self._available_weight_for_stream(stream)
            for stream in self._limit_map
        }
        available_symbols_by_stream = {
            stream: self._available_symbol_capacity(stream)
            for stream in self._limit_map
        }
        remaining_global = (
            float("inf")
            if self._max_active_streams <= 0
            else max(self._max_active_streams - len(self._active), 0)
        )
        if remaining_global <= 0:
            return tuple()
        has_capacity = any(
            (math.isinf(available_weight_by_stream[stream])
             or available_weight_by_stream[stream] > 0.0)
            and (
                    math.isinf(available_symbols_by_stream[stream])
                    or available_symbols_by_stream[stream] > 0.0
            )
            for stream in self._limit_map
        )
        if not has_capacity:
            return tuple()

        def total_weight(symbol: str) -> float:
            normalized_symbol = symbol.upper()
            return sum(
                max(self.stream_weight(normalized_symbol, stream), 0.0)
                for stream in self._limit_map
            )

        ordered = sorted(symbols, key=total_weight, reverse=True)
        planned: list[str] = []
        for symbol in ordered:
            if max_new_batch is not None and len(planned) >= max_new_batch:
                break
            normalized = symbol.upper()
            if normalized in self._active:
                continue
            requirements = {
                stream: max(self.stream_weight(normalized, stream), 0.0)
                for stream in self._limit_map
            }
            if all(
                    (
                            math.isinf(available_weight_by_stream[stream])
                            or available_weight_by_stream[stream] >= weight
                    )
                    and (
                            math.isinf(available_symbols_by_stream[stream])
                            or available_symbols_by_stream[stream] >= 1.0
                    )
                    for stream, weight in requirements.items()
            ):
                planned.append(normalized)
                if not math.isinf(remaining_global):
                    remaining_global = max(remaining_global - 1, 0)
                    if remaining_global <= 0:
                        break
                for stream, weight in requirements.items():
                    if not math.isinf(available_symbols_by_stream[stream]):
                        available_symbols_by_stream[stream] = max(
                            available_symbols_by_stream[stream] - 1.0,
                            0.0,
                        )
                    if math.isinf(available_weight_by_stream[stream]):
                        continue
                    if weight <= 0.0:
                        continue
                    available_weight_by_stream[stream] = max(
                        available_weight_by_stream[stream] - weight,
                        0.0,
                    )
        return tuple(planned)

    def _available_slots(self) -> float:
        capacities = [
            self._available_weight_for_stream(name) for name in self._limit_map
        ]
        if not capacities:
            return 0.0
        finite = [value for value in capacities if not math.isinf(value)]
        if not finite:
            return float("inf")
        return max(min(finite), 0.0)

    def _available_weight_for_stream(self, stream: str) -> float:
        sessions = self._sessions.get(stream, [])
        if not sessions:
            return self._session_weight_capacity(stream)
        total = 0.0
        for session in sessions:
            capacity = session.available_capacity()
            if math.isinf(capacity):
                return float("inf")
            total += capacity
        additional = self._session_weight_capacity(stream)
        if math.isinf(additional):
            return float("inf")
        return total + additional

    def get_cooldown_until(self, symbol: str) -> Optional[datetime]:
        normalized = symbol.upper()
        state = self._resubscribe_state.get(normalized)
        if state is None:
            return None
        now = datetime.now(tz=CURRENT_TIMEZONE)
        if now - state.last_failure_at >= self._backoff_reset_after:
            self._resubscribe_state.pop(normalized, None)
            return None
        if now >= state.next_allowed_at:
            return None
        return state.next_allowed_at

    def _clear_cooldown(self, symbol: str) -> None:
        self._resubscribe_state.pop(symbol.upper(), None)

    def _available_symbol_capacity(self, stream: str) -> float:
        sessions = self._sessions.get(stream, [])
        if not sessions:
            capacity = self._session_capacity(stream)
            if capacity <= 0:
                return float("inf")
            return float(capacity)
        total = 0.0
        for session in sessions:
            capacity = session.available_symbols()
            if math.isinf(capacity):
                return float("inf")
            total += capacity
        additional = self._session_capacity(stream)
        if additional <= 0:
            return float("inf")
        return total + float(additional)

    def _session_capacity(self, stream: str) -> int:
        limit = self._limit_map[stream]
        if limit.max_symbols <= 0:
            return 1_000_000
        reserve = max(0, limit.resubscribe_buffer)
        usable = max(limit.max_symbols - reserve, 0)
        return usable if usable > 0 else limit.max_symbols

    def _session_weight_capacity(self, stream: str) -> float:
        limit = self._limit_map[stream]
        if limit.max_weight > 0:
            return float(limit.max_weight)
        capacity = self._session_capacity(stream)
        if capacity <= 0:
            return float("inf")
        return float(capacity)

    def _profile_weight(self, symbol: str, stream: str) -> float:
        normalized = symbol.upper()
        stream_weights = self._weights.get(stream)
        if stream_weights is not None:
            weight = stream_weights.get(normalized)
            if weight is not None:
                return weight
        profile = self._profiles.get(normalized, TradingProfile.AUTO)
        return BinanceExchangeData.get_profile_weight(stream, profile)

    def stream_weight(self, symbol: str, stream: str) -> float:
        return self._profile_weight(symbol, stream)

    def _acquire_session(
            self,
            stream: str,
            *,
            symbol: str | None = None,
            weight: float | None = None,
    ) -> BinanceStreamSession:
        sessions = self._sessions.setdefault(stream, [])
        for session in sessions:
            can_accept, reason, info = session.can_accept(weight)
            if can_accept:
                return session
            sym_text = symbol or "unknown"
            try:
                budget_state = session.diagnostic_state().get("budget", {})
                self._log_writer(
                    (
                        f"[{stream}] Сессия отказала {sym_text}: причина={reason},"
                        f" остаток_слотов={info.get('capacity_left')},"
                        f" остаток_веса={info.get('weight_left'):.2f},"
                        f" бюджет={budget_state}."
                    )
                )
            except Exception:
                pass
        session = self._create_session(stream)
        sessions.append(session)
        return session

    def subscribe(self, symbol: str) -> BinanceSymbolStreams:
        symbol = symbol.upper()
        self._clear_cooldown(symbol)
        existing = self._streams.get(symbol)
        if existing is not None:
            return existing
        if 0 < self._max_active_streams <= len(self._active):
            msg = (
                f"Глобальный лимит: {symbol} не может быть добавлен. "
                f"Активных символов {len(self._active)}/{self._max_active_streams}."
            )
            self._log_writer(msg)
            raise StreamLimitError(
                f"достигнут лимит активных стримов {self._max_active_streams}"
            )
        queue_depth = 0
        diagnostic_snapshots: Dict[str, Any] = {}
        try:
            for stream_name, session_list in self._sessions.items():
                stream_queue = 0
                for session in session_list:
                    state = session.diagnostic_state()
                    stream_queue += state.get("queue", 0)
                    diagnostic_snapshots.setdefault(stream_name, []).append(state)
                queue_depth += stream_queue
        except Exception:
            diagnostic_snapshots = {}
        self._log_writer(
            (
                f"Подписка: {symbol}. Активных символов {len(self._active)}, "
                f"очередь команд {queue_depth}."
            )
        )
        exchange_data = self.get_exchange_data(symbol)
        streams, registrations = exchange_data.create_stream_bundle()
        stream_weights = {
            name: max(self.stream_weight(symbol, name), 0.0)
            for name in registrations
        }
        try:
            self._log_writer(
                (
                    f"Подписка: {symbol}. Профильные веса {stream_weights}."
                )
            )
        except Exception:
            pass
        stream_priorities = {
            name: max(stream_weights.get(name, 0.0), 0.1)
            for name in registrations
        }
        assignments: Dict[str, tuple[BinanceStreamSession, StreamConsumer[Any]]] = {}
        try:
            for stream_name, registration in registrations.items():
                weight = stream_weights.get(stream_name, 0.0)
                session = self._acquire_session(
                    stream_name,
                    symbol=symbol,
                    weight=weight,
                )
                weight = stream_weights.get(stream_name, 0.0)
                priority = stream_priorities.get(stream_name, 0.1)
                try:
                    session.register_consumer(
                        registration.consumer,
                        priority=priority,
                        weight=weight,
                    )
                except StreamLimitError:
                    self._log_writer(
                        (
                            f"[{stream_name}] StreamLimitError для {symbol}, "
                            f"повторная попытка через новую сессию."
                        )
                    )
                    session = self._acquire_session(
                        stream_name,
                        symbol=symbol,
                        weight=weight,
                    )
                    session.register_consumer(
                        registration.consumer,
                        priority=priority,
                        weight=weight,
                    )
                assignments[stream_name] = (session, registration.consumer)
        except Exception:
            for stream_name, (session, consumer) in assignments.items():
                priority = stream_priorities.get(stream_name, 0.1)
                session.unregister_consumer(consumer, priority=priority)
            raise
        for registration in registrations.values():
            registration.consumer.push_snapshot()
        streams.depth.assign_stop(lambda s=symbol: self.unsubscribe(s))
        streams.trades.assign_stop(lambda s=symbol: self.unsubscribe(s))
        streams.ticker.assign_stop(lambda s=symbol: self.unsubscribe(s))
        self._streams[symbol] = streams
        self._symbol_consumers[symbol] = assignments
        self._active.add(symbol)
        return streams

    def get_exchange_data(self, symbol: str) -> BinanceExchangeData:
        symbol = symbol.upper()
        exchange_data = self._exchange_data.get(symbol)
        if exchange_data is None:
            exchange_data = BinanceExchangeData(
                symbol=symbol,
                loop_interval_ms=self._loop_interval_ms,
                silence_timeout_ms=self._silence_timeout_ms,
                log_writer=self._log_writer,
                api_key=self._api_key,
                api_secret=self._api_secret,
                exchange_info=self._exchange_info_cache,
            )
            self._exchange_data[symbol] = exchange_data
            profile = self._profiles.get(symbol)
            if profile is not None:
                exchange_data.set_symbol_profile(symbol, profile)
        return exchange_data

    def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        assignments = self._symbol_consumers.pop(symbol, {})
        for stream_name, (session, consumer) in assignments.items():
            weight = max(self.stream_weight(symbol, stream_name), 0.0)
            priority = max(weight, 0.1)
            session.unregister_consumer(consumer, priority=priority)
        self._streams.pop(symbol, None)
        self._active.discard(symbol)
        self._clear_cooldown(symbol)

    def resubscribe(self, symbol: str) -> BinanceSymbolStreams:
        symbol = symbol.upper()
        streams = self._streams.get(symbol)
        if streams is None:
            return self.subscribe(symbol)
        assignments = self._symbol_consumers.get(symbol, {})
        if not assignments:
            return streams
        now = datetime.now(tz=CURRENT_TIMEZONE)
        state = self._resubscribe_state.get(symbol)
        if state is not None:
            if now - state.last_failure_at >= self._backoff_reset_after:
                self._clear_cooldown(symbol)
                state = None
            elif now < state.next_allowed_at:
                remaining = state.next_allowed_at - now
                try:
                    self._log_writer(
                        (
                            f"Переподписка {symbol} отложена:"
                            f" ещё {remaining.total_seconds():.1f}с cooldown"
                        )
                    )
                except Exception:
                    pass
                self._active.discard(symbol)
                return streams
        attempts = 0 if state is None else state.attempts
        attempts += 1
        delay_seconds = min(
            self._backoff_base_seconds * (2 ** max(attempts - 1, 0)),
            self._backoff_max_seconds,
        )
        next_allowed_at = now + timedelta(seconds=delay_seconds)
        self._resubscribe_state[symbol] = ResubscribeCooldownState(
            attempts=attempts,
            next_allowed_at=next_allowed_at,
            last_failure_at=now,
        )
        try:
            reasons: set[str] = set()
            for session, consumer in assignments.values():
                reason = consumer.get_last_resync_reason()
                if reason:
                    reasons.add(reason)
            reason_text = ", ".join(sorted(reasons)) or "n/a"
            self._log_writer(
                (
                    f"Переподписка {symbol}: попытка {attempts},"
                    f" следующий интервал ожидания {delay_seconds:.1f}с,"
                    f" причина={reason_text}"
                )
            )
        except Exception:
            pass
        self._active.add(symbol)
        for stream_name, (session, consumer) in assignments.items():
            weight = max(self.stream_weight(symbol, stream_name), 0.0)
            priority = max(weight, 1.0)
            session.resubscribe_consumer(consumer, priority=priority)
        return streams

    def cancel(self, symbol: str) -> None:
        symbol = symbol.upper()
        self._streams.pop(symbol, None)
        self._active.discard(symbol)
        self._symbol_consumers.pop(symbol, None)
        self._clear_cooldown(symbol)
