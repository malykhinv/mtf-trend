"""In-memory aggTrade candle rings for anomaly live2."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Live2AggTradeEvent:
    """Normalized Binance aggTrade event."""

    symbol: str
    market_id: str
    aggregate_trade_id: int | None
    event_time_ms: int | None
    trade_time_ms: int
    price: float
    quantity: float
    quote_quantity: float
    taker_buy_quote_quantity: float
    buyer_is_maker: bool
    source: str


@dataclass(slots=True)
class Live2Candle:
    """Single non-synthetic candle bucket built only from real aggTrade events."""

    timeframe_ms: int
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    base_volume: float
    quote_volume: float
    number_of_trades: int
    taker_buy_quote_volume: float
    first_trade_time_ms: int
    last_trade_time_ms: int
    first_agg_trade_id: int | None = None
    last_agg_trade_id: int | None = None
    first_source: str = ""
    last_source: str = ""
    startup_rest_trade_count: int = 0
    live_ws_trade_count: int = 0

    @classmethod
    def from_trade(cls, *, timeframe_ms: int, bucket_open_ms: int, trade: Live2AggTradeEvent) -> "Live2Candle":
        return cls(
            timeframe_ms=timeframe_ms,
            open_time_ms=bucket_open_ms,
            close_time_ms=bucket_open_ms + timeframe_ms,
            open=trade.price,
            high=trade.price,
            low=trade.price,
            close=trade.price,
            base_volume=trade.quantity,
            quote_volume=trade.quote_quantity,
            number_of_trades=1,
            taker_buy_quote_volume=trade.taker_buy_quote_quantity,
            first_trade_time_ms=trade.trade_time_ms,
            last_trade_time_ms=trade.trade_time_ms,
            first_agg_trade_id=trade.aggregate_trade_id,
            last_agg_trade_id=trade.aggregate_trade_id,
            first_source=trade.source,
            last_source=trade.source,
            startup_rest_trade_count=1 if _is_startup_rest_source(trade.source) else 0,
            live_ws_trade_count=1 if _is_live_ws_source(trade.source) else 0,
        )

    def update(self, trade: Live2AggTradeEvent) -> None:
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.close = trade.price
        self.base_volume += trade.quantity
        self.quote_volume += trade.quote_quantity
        self.number_of_trades += 1
        self.taker_buy_quote_volume += trade.taker_buy_quote_quantity
        self.last_trade_time_ms = trade.trade_time_ms
        self.last_agg_trade_id = trade.aggregate_trade_id
        self.last_source = trade.source
        if _is_startup_rest_source(trade.source):
            self.startup_rest_trade_count += 1
        elif _is_live_ws_source(trade.source):
            self.live_ws_trade_count += 1

    def to_summary_dict(self, *, prefix: str) -> dict[str, object]:
        return {
            f"{prefix}_open_time_ms": self.open_time_ms,
            f"{prefix}_close_time_ms": self.close_time_ms,
            f"{prefix}_open": self.open,
            f"{prefix}_high": self.high,
            f"{prefix}_low": self.low,
            f"{prefix}_close": self.close,
            f"{prefix}_quote_volume": self.quote_volume,
            f"{prefix}_number_of_trades": self.number_of_trades,
            f"{prefix}_taker_buy_quote_volume": self.taker_buy_quote_volume,
            f"{prefix}_first_source": self.first_source,
            f"{prefix}_last_source": self.last_source,
            f"{prefix}_startup_rest_trade_count": self.startup_rest_trade_count,
            f"{prefix}_live_ws_trade_count": self.live_ws_trade_count,
        }


def _is_startup_rest_source(source: str) -> bool:
    return source == "binance_futures_aggTrades_startup_rest"


def _is_live_ws_source(source: str) -> bool:
    return source == "binance_futures_aggtrade_ws"


@dataclass(frozen=True, slots=True)
class Live2CandleUpdateResult:
    updated: bool
    closed_count: int
    gap_count: int
    out_of_order: bool


class Live2CandleRing:
    """Tiny O(1) candle accumulator for one timeframe.

    Missing buckets are not filled with synthetic zero-volume candles. A skipped
    bucket increments gap_count and keeps coverage honest for later decision
    gates.
    """

    def __init__(self, *, timeframe_ms: int, max_closed_candles: int) -> None:
        if timeframe_ms <= 0:
            raise ValueError("timeframe_ms must be > 0")
        if max_closed_candles <= 0:
            raise ValueError("max_closed_candles must be > 0")
        self.timeframe_ms = int(timeframe_ms)
        self.max_closed_candles = int(max_closed_candles)
        self.current: Live2Candle | None = None
        self.closed: deque[Live2Candle] = deque(maxlen=self.max_closed_candles)
        self.last_closed_open_time_ms: int | None = None
        self.gap_count = 0
        self.out_of_order_count = 0
        self.closed_count = 0

    def add_trade(self, trade: Live2AggTradeEvent) -> Live2CandleUpdateResult:
        bucket_open_ms = (int(trade.trade_time_ms) // self.timeframe_ms) * self.timeframe_ms
        if self.current is None:
            gap_count = 0
            if self.last_closed_open_time_ms is not None:
                if bucket_open_ms <= self.last_closed_open_time_ms:
                    self.out_of_order_count += 1
                    return Live2CandleUpdateResult(updated=False, closed_count=0, gap_count=0, out_of_order=True)
                gap_count = max(0, (bucket_open_ms - self.last_closed_open_time_ms) // self.timeframe_ms - 1)
                self.gap_count += gap_count
            self.current = Live2Candle.from_trade(
                timeframe_ms=self.timeframe_ms,
                bucket_open_ms=bucket_open_ms,
                trade=trade,
            )
            return Live2CandleUpdateResult(updated=True, closed_count=0, gap_count=gap_count, out_of_order=False)

        if bucket_open_ms < self.current.open_time_ms:
            self.out_of_order_count += 1
            return Live2CandleUpdateResult(updated=False, closed_count=0, gap_count=0, out_of_order=True)

        if bucket_open_ms == self.current.open_time_ms:
            self.current.update(trade)
            return Live2CandleUpdateResult(updated=True, closed_count=0, gap_count=0, out_of_order=False)

        previous_open_ms = self.current.open_time_ms
        self._append_closed_current()
        skipped = max(0, (bucket_open_ms - previous_open_ms) // self.timeframe_ms - 1)
        self.gap_count += skipped
        self.current = Live2Candle.from_trade(
            timeframe_ms=self.timeframe_ms,
            bucket_open_ms=bucket_open_ms,
            trade=trade,
        )
        return Live2CandleUpdateResult(updated=True, closed_count=1, gap_count=skipped, out_of_order=False)

    def close_due(self, *, now_ms: int) -> Live2CandleUpdateResult:
        """Close a real-trade candle whose wall-clock bucket has ended.

        This creates no synthetic candles and does not invent volume. It only
        makes the latest already-ended real-trade bucket available to the
        decision engine without waiting for the next trade.
        """

        if self.current is None:
            return Live2CandleUpdateResult(updated=False, closed_count=0, gap_count=0, out_of_order=False)
        if int(now_ms) < self.current.close_time_ms:
            return Live2CandleUpdateResult(updated=False, closed_count=0, gap_count=0, out_of_order=False)
        self._append_closed_current()
        self.current = None
        return Live2CandleUpdateResult(updated=True, closed_count=1, gap_count=0, out_of_order=False)

    def _append_closed_current(self) -> None:
        if self.current is None:
            return
        self.closed.append(self.current)
        self.last_closed_open_time_ms = self.current.open_time_ms
        self.closed_count += 1

    def latest_closed(self) -> Live2Candle | None:
        return self.closed[-1] if self.closed else None

    def latest_any(self) -> Live2Candle | None:
        return self.current or self.latest_closed()

    def closed_snapshot(self) -> tuple[Live2Candle, ...]:
        return tuple(self.closed)


class Live2CandleBook:
    """Per-symbol candle rings for the live2 hot market-data path."""

    def __init__(self, *, timeframes_ms: Iterable[int], max_closed_candles: int) -> None:
        self.rings = {
            int(timeframe_ms): Live2CandleRing(
                timeframe_ms=int(timeframe_ms),
                max_closed_candles=max_closed_candles,
            )
            for timeframe_ms in timeframes_ms
        }
        if not self.rings:
            raise ValueError("at least one timeframe is required")

    def add_trade(self, trade: Live2AggTradeEvent) -> Live2CandleUpdateResult:
        closed_count = 0
        gap_count = 0
        out_of_order = False
        updated = False
        for ring in self.rings.values():
            result = ring.add_trade(trade)
            updated = updated or result.updated
            closed_count += result.closed_count
            gap_count += result.gap_count
            out_of_order = out_of_order or result.out_of_order
        return Live2CandleUpdateResult(
            updated=updated,
            closed_count=closed_count,
            gap_count=gap_count,
            out_of_order=out_of_order,
        )

    def close_due(self, *, now_ms: int) -> Live2CandleUpdateResult:
        closed_count = 0
        updated = False
        for ring in self.rings.values():
            result = ring.close_due(now_ms=now_ms)
            updated = updated or result.updated
            closed_count += result.closed_count
        return Live2CandleUpdateResult(
            updated=updated,
            closed_count=closed_count,
            gap_count=0,
            out_of_order=False,
        )

    def coverage_summary(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        for timeframe_ms, ring in sorted(self.rings.items()):
            label = _timeframe_label(timeframe_ms)
            latest_closed = ring.latest_closed()
            latest_any = ring.latest_any()
            payload[f"{label}_closed_count"] = ring.closed_count
            payload[f"{label}_gap_count"] = ring.gap_count
            payload[f"{label}_out_of_order_count"] = ring.out_of_order_count
            payload[f"{label}_current_open_time_ms"] = None if ring.current is None else ring.current.open_time_ms
            payload[f"{label}_latest_closed_open_time_ms"] = None if latest_closed is None else latest_closed.open_time_ms
            if latest_any is not None:
                payload.update(latest_any.to_summary_dict(prefix=label))
        return payload

    def total_gap_count(self) -> int:
        return sum(ring.gap_count for ring in self.rings.values())

    def total_out_of_order_count(self) -> int:
        return sum(ring.out_of_order_count for ring in self.rings.values())


def _timeframe_label(timeframe_ms: int) -> str:
    if timeframe_ms % 1000 != 0:
        return f"tf_{timeframe_ms}ms"
    seconds = timeframe_ms // 1000
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"tf_{minutes}m"
    return f"tf_{seconds}s"
