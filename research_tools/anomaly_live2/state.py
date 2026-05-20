"""In-memory symbol state for anomaly live2."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum

from .clock import utc_now_ms
from .market_data.candles import Live2AggTradeEvent, Live2CandleBook


LIVE2_DEFAULT_CANDLE_TIMEFRAMES_MS = (5_000, 15_000, 30_000, 60_000)
LIVE2_DEFAULT_MAX_CLOSED_CANDLES = 360
LIVE2_STARTUP_AGGTRADE_REST_SOURCE = "binance_futures_aggTrades_startup_rest"
LIVE2_AGGTRADE_WS_SOURCE = "binance_futures_aggtrade_ws"


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
    selected_decision_count: int = 0
    last_signal_category_id: str = ""
    last_signal_category_rank: int | None = None
    last_signal_entry_price: float | None = None
    last_signal_initial_stop: float | None = None
    last_signal_initial_risk_pct: float | None = None
    last_signal_tp1: float | None = None
    last_entry_guard_verdict: str = ""
    last_entry_guard_reason: str = ""
    last_entry_guard_live_price: float | None = None
    last_entry_guard_price_drift_pct: float | None = None
    last_entry_guard_rr_to_tp1: float | None = None
    last_execution_verdict: str = ""
    last_execution_reason: str = ""
    last_execution_pre_position_amount: float | None = None
    last_execution_order_placement_status: str = ""
    last_execution_position_id: str = ""
    last_execution_entry_order_id: str = ""
    last_execution_entry_fill_price: float | None = None
    last_execution_entry_filled_amount: float | None = None
    last_execution_stop_order_id: str = ""
    last_execution_stop_price: float | None = None
    last_execution_integrity_error: bool = False
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
    startup_aggtrade_first_seen_ms: int | None = None
    startup_aggtrade_last_seen_ms: int | None = None
    startup_aggtrade_update_count: int = 0
    startup_aggtrade_trade_count_total: int = 0
    startup_aggtrade_quote_volume_total: float = 0.0
    startup_aggtrade_taker_buy_quote_volume_total: float = 0.0
    startup_aggtrade_source: str = ""
    startup_aggtrade_status: str = "not_seen"
    startup_aggtrade_reason: str = ""
    live_aggtrade_first_seen_ms: int | None = None
    live_aggtrade_last_seen_ms: int | None = None
    live_aggtrade_last_trade_time_ms: int | None = None
    live_aggtrade_update_count: int = 0
    live_aggtrade_last_trade_id: int | None = None
    live_aggtrade_last_price: float | None = None
    live_aggtrade_last_quantity: float | None = None
    live_aggtrade_quote_volume_total: float = 0.0
    live_aggtrade_taker_buy_quote_volume_total: float = 0.0
    live_aggtrade_trade_count_total: int = 0
    live_aggtrade_source: str = ""
    live_aggtrade_status: str = "not_seen"
    live_aggtrade_reason: str = ""
    candle_coverage_status: str = "not_ready"
    candle_gap_count: int = 0
    candle_out_of_order_count: int = 0
    candle_timeframes_ms: tuple[int, ...] = LIVE2_DEFAULT_CANDLE_TIMEFRAMES_MS
    max_closed_candles: int = LIVE2_DEFAULT_MAX_CLOSED_CANDLES
    candle_book: Live2CandleBook = field(init=False, repr=False)

    def __post_init__(self) -> None:
        now_ms = utc_now_ms()
        if self.created_ms <= 0:
            self.created_ms = now_ms
        if self.updated_ms <= 0:
            self.updated_ms = now_ms
        if self.max_closed_candles <= 0:
            raise ValueError("max_closed_candles must be > 0")
        self.candle_book = Live2CandleBook(
            timeframes_ms=self.candle_timeframes_ms,
            max_closed_candles=self.max_closed_candles,
        )

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
        source_status, source_reason = self._aggtrade_result_status(result_out_of_order=result.out_of_order)
        self.aggtrade_status = source_status
        self.aggtrade_reason = source_reason
        if trade.source == LIVE2_STARTUP_AGGTRADE_REST_SOURCE:
            self._update_startup_aggtrade_source(trade, received_at_ms=received_at_ms, status=source_status, reason=source_reason)
        elif trade.source == LIVE2_AGGTRADE_WS_SOURCE:
            self._update_live_aggtrade_source(trade, received_at_ms=received_at_ms, status=source_status, reason=source_reason)
        else:
            self.aggtrade_status = "unknown_source"
            self.aggtrade_reason = f"unknown_aggtrade_source:{trade.source}"
        if self.live_aggtrade_update_count > 0:
            self.candle_coverage_status = "live_ready"
        elif self.startup_aggtrade_update_count > 0:
            self.candle_coverage_status = "startup_warmup_only"
        else:
            self.candle_coverage_status = "not_ready"
        self.mark_dirty(now_ms=received_at_ms)

    def _aggtrade_result_status(self, *, result_out_of_order: bool) -> tuple[str, str]:
        if result_out_of_order:
            return "out_of_order_trade_ignored", "aggtrade_trade_time_older_than_current_bucket"
        if self.candle_gap_count > 0:
            return "ok_with_gaps", "aggtrade_bucket_gap_detected_no_synthetic_fill"
        return "ok", ""

    def _update_startup_aggtrade_source(
        self,
        trade: Live2AggTradeEvent,
        *,
        received_at_ms: int,
        status: str,
        reason: str,
    ) -> None:
        if self.startup_aggtrade_first_seen_ms is None:
            self.startup_aggtrade_first_seen_ms = received_at_ms
        self.startup_aggtrade_last_seen_ms = received_at_ms
        self.startup_aggtrade_update_count += 1
        self.startup_aggtrade_trade_count_total += 1
        self.startup_aggtrade_quote_volume_total += trade.quote_quantity
        self.startup_aggtrade_taker_buy_quote_volume_total += trade.taker_buy_quote_quantity
        self.startup_aggtrade_source = trade.source
        self.startup_aggtrade_status = status
        self.startup_aggtrade_reason = reason

    def _update_live_aggtrade_source(
        self,
        trade: Live2AggTradeEvent,
        *,
        received_at_ms: int,
        status: str,
        reason: str,
    ) -> None:
        if self.live_aggtrade_first_seen_ms is None:
            self.live_aggtrade_first_seen_ms = received_at_ms
        self.live_aggtrade_last_seen_ms = received_at_ms
        self.live_aggtrade_last_trade_time_ms = trade.trade_time_ms
        self.live_aggtrade_update_count += 1
        self.live_aggtrade_last_trade_id = trade.aggregate_trade_id
        self.live_aggtrade_last_price = trade.price
        self.live_aggtrade_last_quantity = trade.quantity
        self.live_aggtrade_quote_volume_total += trade.quote_quantity
        self.live_aggtrade_taker_buy_quote_volume_total += trade.taker_buy_quote_quantity
        self.live_aggtrade_trade_count_total += 1
        self.live_aggtrade_source = trade.source
        self.live_aggtrade_status = status
        self.live_aggtrade_reason = reason

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
            "selected_decision_count": self.selected_decision_count,
            "last_signal_category_id": self.last_signal_category_id,
            "last_signal_category_rank": self.last_signal_category_rank,
            "last_signal_entry_price": self.last_signal_entry_price,
            "last_signal_initial_stop": self.last_signal_initial_stop,
            "last_signal_initial_risk_pct": self.last_signal_initial_risk_pct,
            "last_signal_tp1": self.last_signal_tp1,
            "last_entry_guard_verdict": self.last_entry_guard_verdict,
            "last_entry_guard_reason": self.last_entry_guard_reason,
            "last_entry_guard_live_price": self.last_entry_guard_live_price,
            "last_entry_guard_price_drift_pct": self.last_entry_guard_price_drift_pct,
            "last_entry_guard_rr_to_tp1": self.last_entry_guard_rr_to_tp1,
            "last_execution_verdict": self.last_execution_verdict,
            "last_execution_reason": self.last_execution_reason,
            "last_execution_pre_position_amount": self.last_execution_pre_position_amount,
            "last_execution_order_placement_status": self.last_execution_order_placement_status,
            "last_execution_position_id": self.last_execution_position_id,
            "last_execution_entry_order_id": self.last_execution_entry_order_id,
            "last_execution_entry_fill_price": self.last_execution_entry_fill_price,
            "last_execution_entry_filled_amount": self.last_execution_entry_filled_amount,
            "last_execution_stop_order_id": self.last_execution_stop_order_id,
            "last_execution_stop_price": self.last_execution_stop_price,
            "last_execution_integrity_error": self.last_execution_integrity_error,
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
            "startup_aggtrade_first_seen_ms": self.startup_aggtrade_first_seen_ms,
            "startup_aggtrade_last_seen_ms": self.startup_aggtrade_last_seen_ms,
            "startup_aggtrade_update_count": self.startup_aggtrade_update_count,
            "startup_aggtrade_trade_count_total": self.startup_aggtrade_trade_count_total,
            "startup_aggtrade_quote_volume_total": self.startup_aggtrade_quote_volume_total,
            "startup_aggtrade_taker_buy_quote_volume_total": self.startup_aggtrade_taker_buy_quote_volume_total,
            "startup_aggtrade_source": self.startup_aggtrade_source,
            "startup_aggtrade_status": self.startup_aggtrade_status,
            "startup_aggtrade_reason": self.startup_aggtrade_reason,
            "live_aggtrade_first_seen_ms": self.live_aggtrade_first_seen_ms,
            "live_aggtrade_last_seen_ms": self.live_aggtrade_last_seen_ms,
            "live_aggtrade_last_trade_time_ms": self.live_aggtrade_last_trade_time_ms,
            "live_aggtrade_update_count": self.live_aggtrade_update_count,
            "live_aggtrade_last_trade_id": self.live_aggtrade_last_trade_id,
            "live_aggtrade_last_price": self.live_aggtrade_last_price,
            "live_aggtrade_last_quantity": self.live_aggtrade_last_quantity,
            "live_aggtrade_quote_volume_total": self.live_aggtrade_quote_volume_total,
            "live_aggtrade_taker_buy_quote_volume_total": self.live_aggtrade_taker_buy_quote_volume_total,
            "live_aggtrade_trade_count_total": self.live_aggtrade_trade_count_total,
            "live_aggtrade_source": self.live_aggtrade_source,
            "live_aggtrade_status": self.live_aggtrade_status,
            "live_aggtrade_reason": self.live_aggtrade_reason,
            "candle_coverage_status": self.candle_coverage_status,
            "candle_gap_count": self.candle_gap_count,
            "candle_out_of_order_count": self.candle_out_of_order_count,
            "max_closed_candles": self.max_closed_candles,
        }
        row.update(self.candle_book.coverage_summary())
        return row


class SymbolStateStore:
    """Container with exactly one state record per symbol."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        candle_timeframes_ms: tuple[int, ...] = LIVE2_DEFAULT_CANDLE_TIMEFRAMES_MS,
        max_closed_candles: int = LIVE2_DEFAULT_MAX_CLOSED_CANDLES,
    ) -> None:
        unique_symbols = tuple(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        self.candle_timeframes_ms = tuple(int(value) for value in candle_timeframes_ms)
        self.max_closed_candles = int(max_closed_candles)
        self._lock = threading.RLock()
        self._states: dict[str, SymbolState] = {
            symbol: SymbolState(
                symbol=symbol,
                candle_timeframes_ms=self.candle_timeframes_ms,
                max_closed_candles=self.max_closed_candles,
            )
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
                state = SymbolState(
                    symbol=normalized,
                    candle_timeframes_ms=self.candle_timeframes_ms,
                    max_closed_candles=self.max_closed_candles,
                )
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


    def update_aggtrade_many(self, trades: tuple[Live2AggTradeEvent, ...], *, received_at_ms: int) -> None:
        if not trades:
            return
        with self._lock:
            for trade in trades:
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

    def startup_aggtrade_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.startup_aggtrade_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def live_aggtrade_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.live_aggtrade_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def candle_coverage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.candle_coverage_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts
