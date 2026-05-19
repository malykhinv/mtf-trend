"""In-memory symbol state for anomaly live2."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum

from .clock import utc_now_ms
from .market_data.candles import Live2AggTradeEvent, Live2CandleBook


LIVE2_DEFAULT_CANDLE_TIMEFRAMES_MS = (5_000, 15_000, 30_000, 60_000)
LIVE2_DEFAULT_MAX_CLOSED_CANDLES = 360


class SymbolLive2Status(StrEnum):
    INACTIVE = "inactive"
    WATCHING = "watching"
    ACTIONABLE = "actionable"
    IN_POSITION = "in_position"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass(slots=True)
class SymbolState:
    """Single mutable state record per symbol.

    Live2 deliberately keeps one state object per symbol instead of warm/radar
    queues. Ticker and aggTrade updates mutate this record in place. Candle
    buckets are built only from real aggTrade events; missing buckets are gap
    diagnostics, not synthetic flat candles.
    """

    symbol: str
    status: SymbolLive2Status = SymbolLive2Status.INACTIVE
    created_ms: int = 0
    updated_ms: int = 0
    dirty_since_ms: int | None = None
    actionable_since_ms: int | None = None
    decision_deadline_ms: int | None = None
    last_decision_bucket_ms: int | None = None
    last_verdict: str = "not_evaluated"
    last_verdict_reason: str = ""
    last_decision_latency_ms: int | None = None
    decision_count: int = 0
    rejected_decision_count: int = 0
    data_not_ready_decision_count: int = 0
    deadline_missed_count: int = 0
    universe_selected: bool = False
    universe_rank: int | None = None
    universe_reason: str = "not_selected"
    ticker_market_id: str = ""
    ticker_first_seen_ms: int | None = None
    ticker_last_seen_ms: int | None = None
    ticker_update_count: int = 0
    ticker_last_price: float | None = None
    ticker_quote_volume_24h: float | None = None
    ticker_trade_count_24h: int | None = None
    ticker_price_change_pct_24h: float | None = None
    ticker_source: str = ""
    ticker_status: str = "not_seen"
    ticker_reason: str = ""
    aggtrade_market_id: str = ""
    aggtrade_first_seen_ms: int | None = None
    aggtrade_last_seen_ms: int | None = None
    aggtrade_last_trade_time_ms: int | None = None
    aggtrade_update_count: int = 0
    aggtrade_last_trade_id: int | None = None
    aggtrade_last_price: float | None = None
    aggtrade_last_quantity: float | None = None
    aggtrade_quote_volume_total: float = 0.0
    aggtrade_taker_buy_quote_volume_total: float = 0.0
    aggtrade_trade_count_total: int = 0
    aggtrade_source: str = ""
    aggtrade_status: str = "not_seen"
    aggtrade_reason: str = ""
    candle_coverage_status: str = "not_ready"
    candle_gap_count: int = 0
    candle_out_of_order_count: int = 0
    candle_book: Live2CandleBook = field(
        default_factory=lambda: Live2CandleBook(
            timeframes_ms=LIVE2_DEFAULT_CANDLE_TIMEFRAMES_MS,
            max_closed_candles=LIVE2_DEFAULT_MAX_CLOSED_CANDLES,
        ),
        repr=False,
    )

    def __post_init__(self) -> None:
        now_ms = utc_now_ms()
        if self.created_ms <= 0:
            self.created_ms = now_ms
        if self.updated_ms <= 0:
            self.updated_ms = now_ms

    def mark_dirty(self, *, now_ms: int | None = None) -> None:
        effective_now = utc_now_ms() if now_ms is None else now_ms
        self.updated_ms = effective_now
        if self.dirty_since_ms is None:
            self.dirty_since_ms = effective_now

    def set_universe_selection(
        self,
        *,
        selected: bool,
        rank: int | None,
        reason: str,
        selected_at_ms: int,
    ) -> None:
        self.universe_selected = selected
        self.universe_rank = rank
        self.universe_reason = reason
        self.updated_ms = max(self.updated_ms, selected_at_ms)

    def update_ticker(
        self,
        *,
        market_id: str,
        fetched_at_ms: int,
        last_price: float | None,
        quote_volume_24h: float | None,
        trade_count_24h: int | None,
        price_change_pct_24h: float | None,
        source: str,
        status: str,
        reason: str,
    ) -> None:
        self.updated_ms = fetched_at_ms
        self.ticker_market_id = market_id
        if self.ticker_first_seen_ms is None:
            self.ticker_first_seen_ms = fetched_at_ms
        self.ticker_last_seen_ms = fetched_at_ms
        self.ticker_update_count += 1
        self.ticker_last_price = last_price
        self.ticker_quote_volume_24h = quote_volume_24h
        self.ticker_trade_count_24h = trade_count_24h
        self.ticker_price_change_pct_24h = price_change_pct_24h
        self.ticker_source = source
        self.ticker_status = status
        self.ticker_reason = reason
        self.mark_dirty(now_ms=fetched_at_ms)

    def update_aggtrade(self, trade: Live2AggTradeEvent, *, received_at_ms: int) -> None:
        self.updated_ms = received_at_ms
        self.aggtrade_market_id = trade.market_id
        if self.aggtrade_first_seen_ms is None:
            self.aggtrade_first_seen_ms = received_at_ms
        self.aggtrade_last_seen_ms = received_at_ms
        self.aggtrade_last_trade_time_ms = trade.trade_time_ms
        self.aggtrade_update_count += 1
        self.aggtrade_last_trade_id = trade.aggregate_trade_id
        self.aggtrade_last_price = trade.price
        self.aggtrade_last_quantity = trade.quantity
        self.aggtrade_quote_volume_total += trade.quote_quantity
        self.aggtrade_taker_buy_quote_volume_total += trade.taker_buy_quote_quantity
        self.aggtrade_trade_count_total += 1
        self.aggtrade_source = trade.source
        result = self.candle_book.add_trade(trade)
        self.candle_gap_count = self.candle_book.total_gap_count()
        self.candle_out_of_order_count = self.candle_book.total_out_of_order_count()
        if result.out_of_order:
            self.aggtrade_status = "out_of_order_trade_ignored"
            self.aggtrade_reason = "aggtrade_trade_time_older_than_current_bucket"
        elif self.candle_gap_count > 0:
            self.aggtrade_status = "ok_with_gaps"
            self.aggtrade_reason = "aggtrade_bucket_gap_detected_no_synthetic_fill"
        else:
            self.aggtrade_status = "ok"
            self.aggtrade_reason = ""
        self.candle_coverage_status = "ready" if self.aggtrade_update_count > 0 else "not_ready"
        self.mark_dirty(now_ms=received_at_ms)

    def to_artifact_row(self) -> dict[str, object]:
        row: dict[str, object] = {
            "symbol": self.symbol,
            "status": self.status.value,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "dirty_since_ms": self.dirty_since_ms,
            "actionable_since_ms": self.actionable_since_ms,
            "decision_deadline_ms": self.decision_deadline_ms,
            "last_decision_bucket_ms": self.last_decision_bucket_ms,
            "last_verdict": self.last_verdict,
            "last_verdict_reason": self.last_verdict_reason,
            "last_decision_latency_ms": self.last_decision_latency_ms,
            "decision_count": self.decision_count,
            "rejected_decision_count": self.rejected_decision_count,
            "data_not_ready_decision_count": self.data_not_ready_decision_count,
            "deadline_missed_count": self.deadline_missed_count,
            "universe_selected": self.universe_selected,
            "universe_rank": self.universe_rank,
            "universe_reason": self.universe_reason,
            "ticker_market_id": self.ticker_market_id,
            "ticker_first_seen_ms": self.ticker_first_seen_ms,
            "ticker_last_seen_ms": self.ticker_last_seen_ms,
            "ticker_update_count": self.ticker_update_count,
            "ticker_last_price": self.ticker_last_price,
            "ticker_quote_volume_24h": self.ticker_quote_volume_24h,
            "ticker_trade_count_24h": self.ticker_trade_count_24h,
            "ticker_price_change_pct_24h": self.ticker_price_change_pct_24h,
            "ticker_source": self.ticker_source,
            "ticker_status": self.ticker_status,
            "ticker_reason": self.ticker_reason,
            "aggtrade_market_id": self.aggtrade_market_id,
            "aggtrade_first_seen_ms": self.aggtrade_first_seen_ms,
            "aggtrade_last_seen_ms": self.aggtrade_last_seen_ms,
            "aggtrade_last_trade_time_ms": self.aggtrade_last_trade_time_ms,
            "aggtrade_update_count": self.aggtrade_update_count,
            "aggtrade_last_trade_id": self.aggtrade_last_trade_id,
            "aggtrade_last_price": self.aggtrade_last_price,
            "aggtrade_last_quantity": self.aggtrade_last_quantity,
            "aggtrade_quote_volume_total": self.aggtrade_quote_volume_total,
            "aggtrade_taker_buy_quote_volume_total": self.aggtrade_taker_buy_quote_volume_total,
            "aggtrade_trade_count_total": self.aggtrade_trade_count_total,
            "aggtrade_source": self.aggtrade_source,
            "aggtrade_status": self.aggtrade_status,
            "aggtrade_reason": self.aggtrade_reason,
            "candle_coverage_status": self.candle_coverage_status,
            "candle_gap_count": self.candle_gap_count,
            "candle_out_of_order_count": self.candle_out_of_order_count,
        }
        row.update(self.candle_book.coverage_summary())
        return row


class SymbolStateStore:
    """Container with exactly one state record per symbol."""

    def __init__(self, symbols: tuple[str, ...]) -> None:
        unique_symbols = tuple(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        self._lock = threading.RLock()
        self._states: dict[str, SymbolState] = {
            symbol: SymbolState(symbol=symbol)
            for symbol in unique_symbols
        }

    @property
    def symbols(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._states)

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)

    def get_or_create(self, symbol: str) -> SymbolState:
        normalized = symbol.strip()
        if not normalized:
            raise ValueError("symbol must not be empty")
        with self._lock:
            state = self._states.get(normalized)
            if state is None:
                state = SymbolState(symbol=normalized)
                self._states[normalized] = state
            return state

    def apply_universe_selection(
        self,
        *,
        selected_rank_by_symbol: dict[str, int],
        selected_at_ms: int,
        mode: str,
    ) -> None:
        with self._lock:
            for state in self._states.values():
                rank = selected_rank_by_symbol.get(state.symbol)
                if rank is None:
                    state.set_universe_selection(
                        selected=False,
                        rank=None,
                        reason=f"not_selected_by_{mode}",
                        selected_at_ms=selected_at_ms,
                    )
                else:
                    state.set_universe_selection(
                        selected=True,
                        rank=rank,
                        reason=f"selected_by_{mode}",
                        selected_at_ms=selected_at_ms,
                    )

    def update_ticker(
        self,
        *,
        symbol: str,
        market_id: str,
        fetched_at_ms: int,
        last_price: float | None,
        quote_volume_24h: float | None,
        trade_count_24h: int | None,
        price_change_pct_24h: float | None,
        source: str,
        status: str,
        reason: str,
    ) -> None:
        with self._lock:
            state = self.get_or_create(symbol)
            state.update_ticker(
                market_id=market_id,
                fetched_at_ms=fetched_at_ms,
                last_price=last_price,
                quote_volume_24h=quote_volume_24h,
                trade_count_24h=trade_count_24h,
                price_change_pct_24h=price_change_pct_24h,
                source=source,
                status=status,
                reason=reason,
            )

    def update_aggtrade(self, trade: Live2AggTradeEvent, *, received_at_ms: int) -> None:
        with self._lock:
            state = self.get_or_create(trade.symbol)
            state.update_aggtrade(trade, received_at_ms=received_at_ms)

    def snapshot(self) -> tuple[SymbolState, ...]:
        with self._lock:
            return tuple(self._states.values())

    def counts_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in SymbolLive2Status}
        with self._lock:
            for state in self._states.values():
                counts[state.status.value] = counts.get(state.status.value, 0) + 1
        return counts

    def ticker_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.ticker_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def aggtrade_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.aggtrade_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def candle_coverage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.candle_coverage_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts
