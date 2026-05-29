"""In-memory symbol state for anomaly live2."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from math import isfinite
from enum import StrEnum

from .clock import utc_now_ms
from .market_data.candles import Live2AggTradeEvent, Live2CandleBook


LIVE2_DEFAULT_CANDLE_TIMEFRAMES_MS = (5_000, 15_000, 30_000, 60_000, 300_000)
LIVE2_DEFAULT_MAX_CLOSED_CANDLES = 360
LIVE2_STARTUP_AGGTRADE_REST_SOURCE = "binance_futures_aggTrades_startup_rest"
LIVE2_AGGTRADE_WS_SOURCE = "binance_futures_aggtrade_ws"
LIVE2_OPEN_INTEREST_SOURCE = "binance_futures_open_interest_hist_5m_poll"
LIVE2_CURRENT_OPEN_INTEREST_SOURCE = "binance_futures_current_open_interest_poll"
LIVE2_PRIOR_CONTEXT_SOURCE = "binance_futures_ohlcv_5m_prior_context_24h_poll"
LIVE2_STAGE_LABELS: tuple[str, ...] = (
    "stage0",
    "stage1",
    "stage2",
    "stage3",
    "stage4",
    "stage5",
)


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
    decision_dirty_since_ms: int | None = None
    actionable_since_ms: int | None = None
    first_actionable_ms: int | None = None
    last_actionable_ms: int | None = None
    stage0_passed_ms: int | None = None
    stage1_passed_ms: int | None = None
    stage2_passed_ms: int | None = None
    stage3_passed_ms: int | None = None
    stage4_passed_ms: int | None = None
    stage5_passed_ms: int | None = None
    decision_deadline_ms: int | None = None
    last_decision_bucket_ms: int | None = None
    last_verdict: str = "not_evaluated"
    last_verdict_reason: str = ""
    last_decision_latency_ms: int | None = None
    decision_count: int = 0
    rejected_decision_count: int = 0
    data_not_ready_decision_count: int = 0
    data_dependency_not_ready_decision_count: int = 0
    flow_freshness_reject_decision_count: int = 0
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
    mark_market_id: str = ""
    mark_first_seen_ms: int | None = None
    mark_last_seen_ms: int | None = None
    mark_event_time_ms: int | None = None
    mark_update_count: int = 0
    mark_price: float | None = None
    mark_index_price: float | None = None
    mark_estimated_settle_price: float | None = None
    mark_funding_rate: float | None = None
    mark_next_funding_time_ms: int | None = None
    mark_source: str = ""
    mark_status: str = "not_seen"
    mark_reason: str = ""
    oi_first_seen_ms: int | None = None
    oi_last_seen_ms: int | None = None
    oi_latest_timestamp_ms: int | None = None
    oi_previous_timestamp_ms: int | None = None
    oi_update_count: int = 0
    oi_open_interest: float | None = None
    oi_previous_open_interest: float | None = None
    oi_change_pct_3x5m: float | None = None
    oi_rows_received: int = 0
    oi_source: str = ""
    oi_status: str = "not_seen"
    oi_reason: str = ""
    current_oi_first_seen_ms: int | None = None
    current_oi_last_seen_ms: int | None = None
    current_oi_timestamp_ms: int | None = None
    current_oi_update_count: int = 0
    current_oi_open_interest: float | None = None
    current_oi_source: str = ""
    current_oi_status: str = "not_seen"
    current_oi_reason: str = ""
    current_oi_first_ok_seen_ms: int | None = None
    current_oi_first_ok_timestamp_ms: int | None = None
    current_oi_first_ok_open_interest: float | None = None
    current_oi_first_ok_source: str = ""
    current_oi_first_ok_status: str = "not_seen"
    current_oi_first_ok_reason: str = ""
    prior_context_first_seen_ms: int | None = None
    prior_context_last_seen_ms: int | None = None
    prior_context_start_ms: int | None = None
    prior_context_end_ms: int | None = None
    prior_context_update_count: int = 0
    prior_context_rows_received: int = 0
    prior_context_rows_used: int = 0
    prior_spike_count_24h: int | None = None
    prior_fast_fade_count_24h: int | None = None
    prior_high_24h: float | None = None
    prior_low_before_high_24h: float | None = None
    prior_low_after_high_24h: float | None = None
    prior_context_spike_return_pct: float | None = None
    prior_context_fast_fade_retrace_fraction: float | None = None
    prior_context_source: str = ""
    prior_context_status: str = "not_seen"
    prior_context_reason: str = ""
    prior_context_maintenance_source: str = ""
    prior_context_last_live_5m_open_time_ms: int | None = None
    prior_context_last_live_5m_close_time_ms: int | None = None
    prior_context_live_5m_appended_count: int = 0
    prior_context_live_5m_gap_tolerated_count: int = 0
    prior_context_live_5m_gap_rejected_count: int = 0
    prior_context_last_live_5m_missing_aggtrade_ids: int = 0
    prior_context_last_live_5m_gap_tolerance: int = 0
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
        current_fetched_at_ms: int | None = None,
        current_timestamp_ms: int | None = None,
        current_open_interest: float | None = None,
        current_source: str = "",
        current_status: str = "",
        current_reason: str = "",
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
        if current_status:
            effective_current_seen_ms = current_fetched_at_ms if current_fetched_at_ms is not None else fetched_at_ms
            self.current_oi_last_seen_ms = effective_current_seen_ms
            self.current_oi_timestamp_ms = current_timestamp_ms
            self.current_oi_open_interest = current_open_interest
            self.current_oi_source = current_source
            self.current_oi_status = current_status
            self.current_oi_reason = current_reason
            if self.current_oi_first_ok_seen_ms is None and current_status == "ok":
                try:
                    current_open_interest_float = float(current_open_interest)
                except (TypeError, ValueError):
                    current_open_interest_float = float("nan")
                if isfinite(current_open_interest_float) and current_open_interest_float > 0.0:
                    self.current_oi_first_ok_seen_ms = effective_current_seen_ms
                    self.current_oi_first_ok_timestamp_ms = current_timestamp_ms
                    self.current_oi_first_ok_open_interest = current_open_interest_float
                    self.current_oi_first_ok_source = current_source
                    self.current_oi_first_ok_status = current_status
                    self.current_oi_first_ok_reason = current_reason
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
        source_status, source_reason = self._aggtrade_result_status(
            result_gap_count=result.gap_count,
            result_out_of_order=result.out_of_order,
        )
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
        self.decision_dirty_since_ms = received_at_ms
        self.mark_dirty(now_ms=received_at_ms)

    def _aggtrade_result_status(self, *, result_gap_count: int, result_out_of_order: bool) -> tuple[str, str]:
        if result_out_of_order:
            return "out_of_order_trade_ignored", "aggtrade_trade_time_older_than_current_bucket"
        if result_gap_count > 0:
            return "gap_missing_expected_bucket", "aggtrade_update_skipped_empty_buckets_no_synthetic_fill"
        return "ok_active", "live_aggtrade_trade_seen_without_bucket_gap"

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

    def effective_live_aggtrade_status(
        self,
        *,
        now_ms: int,
        stale_ms: int,
    ) -> tuple[str, str, int | None]:
        if self.live_aggtrade_update_count <= 0 or self.live_aggtrade_last_seen_ms is None:
            if self.universe_selected and self.ticker_status == "ok":
                return "ok_idle_no_trades", "selected_symbol_has_no_live_aggtrade_trades_yet", None
            return self.live_aggtrade_status or "not_seen", self.live_aggtrade_reason or "live_aggtrade_not_seen", None
        age_ms = max(0, int(now_ms) - int(self.live_aggtrade_last_seen_ms))
        if self.live_aggtrade_status == "gap_missing_expected_bucket":
            return "gap_missing_expected_bucket", self.live_aggtrade_reason, age_ms
        if age_ms <= int(stale_ms):
            return "ok_active", "live_aggtrade_trade_seen_within_stale_window", age_ms
        if self.status == SymbolLive2Status.IN_POSITION or self.status == SymbolLive2Status.ACTIONABLE or self.last_signal_category_id:
            return "stale", "actionable_or_position_symbol_live_aggtrade_stale", age_ms
        return "ok_idle_no_trades", "no_recent_live_aggtrade_for_passive_selected_symbol", age_ms

    def update_mark_price(
        self,
        *,
        market_id: str,
        received_at_ms: int,
        event_time_ms: int | None,
        mark_price: float,
        index_price: float | None,
        estimated_settle_price: float | None,
        funding_rate: float | None,
        next_funding_time_ms: int | None,
        source: str,
        status: str,
        reason: str,
    ) -> None:
        self.updated_ms = received_at_ms
        self.mark_market_id = market_id
        if self.mark_first_seen_ms is None:
            self.mark_first_seen_ms = received_at_ms
        self.mark_last_seen_ms = received_at_ms
        self.mark_event_time_ms = event_time_ms
        self.mark_update_count += 1
        self.mark_price = mark_price
        self.mark_index_price = index_price
        self.mark_estimated_settle_price = estimated_settle_price
        self.mark_funding_rate = funding_rate
        self.mark_next_funding_time_ms = next_funding_time_ms
        self.mark_source = source
        self.mark_status = status
        self.mark_reason = reason
        self.mark_dirty(now_ms=received_at_ms)

    def update_open_interest(
        self,
        *,
        fetched_at_ms: int,
        latest_timestamp_ms: int | None,
        previous_timestamp_ms: int | None,
        open_interest: float | None,
        previous_open_interest: float | None,
        open_interest_change_pct_3x5m: float | None,
        rows_received: int,
        source: str,
        status: str,
        reason: str,
        current_fetched_at_ms: int | None = None,
        current_timestamp_ms: int | None = None,
        current_open_interest: float | None = None,
        current_source: str = "",
        current_status: str = "",
        current_reason: str = "",
    ) -> None:
        self.updated_ms = fetched_at_ms
        if self.oi_first_seen_ms is None:
            self.oi_first_seen_ms = fetched_at_ms
        self.oi_last_seen_ms = fetched_at_ms
        self.oi_latest_timestamp_ms = latest_timestamp_ms
        self.oi_previous_timestamp_ms = previous_timestamp_ms
        self.oi_update_count += 1
        self.oi_open_interest = open_interest
        self.oi_previous_open_interest = previous_open_interest
        self.oi_change_pct_3x5m = open_interest_change_pct_3x5m
        self.oi_rows_received = rows_received
        self.oi_source = source
        self.oi_status = status
        self.oi_reason = reason
        if current_status:
            effective_current_seen_ms = current_fetched_at_ms if current_fetched_at_ms is not None else fetched_at_ms
            if self.current_oi_first_seen_ms is None:
                self.current_oi_first_seen_ms = effective_current_seen_ms
            self.current_oi_last_seen_ms = effective_current_seen_ms
            self.current_oi_timestamp_ms = current_timestamp_ms
            self.current_oi_update_count += 1
            self.current_oi_open_interest = current_open_interest
            self.current_oi_source = current_source
            self.current_oi_status = current_status
            self.current_oi_reason = current_reason
            if self.current_oi_first_ok_seen_ms is None and current_status == "ok":
                try:
                    current_open_interest_float = float(current_open_interest)
                except (TypeError, ValueError):
                    current_open_interest_float = float("nan")
                if isfinite(current_open_interest_float) and current_open_interest_float > 0.0:
                    self.current_oi_first_ok_seen_ms = effective_current_seen_ms
                    self.current_oi_first_ok_timestamp_ms = current_timestamp_ms
                    self.current_oi_first_ok_open_interest = current_open_interest_float
                    self.current_oi_first_ok_source = current_source
                    self.current_oi_first_ok_status = current_status
                    self.current_oi_first_ok_reason = current_reason
        self.mark_dirty(now_ms=fetched_at_ms)

    def update_prior_context(
        self,
        *,
        fetched_at_ms: int,
        context_start_ms: int | None,
        context_end_ms: int | None,
        rows_received: int,
        rows_used: int,
        prior_spike_count_24h: int | None,
        prior_fast_fade_count_24h: int | None,
        prior_high_24h: float | None,
        prior_low_before_high_24h: float | None,
        prior_low_after_high_24h: float | None,
        spike_return_pct: float | None,
        fast_fade_retrace_fraction: float | None,
        source: str,
        status: str,
        reason: str,
        maintenance_source: str = "",
        live_5m_open_time_ms: int | None = None,
        live_5m_close_time_ms: int | None = None,
        live_5m_gap_tolerated: bool = False,
        live_5m_gap_rejected: bool = False,
        live_5m_missing_aggtrade_ids: int = 0,
        live_5m_gap_tolerance: int = 0,
    ) -> None:
        self.updated_ms = fetched_at_ms
        if self.prior_context_first_seen_ms is None:
            self.prior_context_first_seen_ms = fetched_at_ms
        self.prior_context_last_seen_ms = fetched_at_ms
        self.prior_context_start_ms = context_start_ms
        self.prior_context_end_ms = context_end_ms
        self.prior_context_update_count += 1
        self.prior_context_rows_received = rows_received
        self.prior_context_rows_used = rows_used
        self.prior_spike_count_24h = prior_spike_count_24h
        self.prior_fast_fade_count_24h = prior_fast_fade_count_24h
        self.prior_high_24h = prior_high_24h
        self.prior_low_before_high_24h = prior_low_before_high_24h
        self.prior_low_after_high_24h = prior_low_after_high_24h
        self.prior_context_spike_return_pct = spike_return_pct
        self.prior_context_fast_fade_retrace_fraction = fast_fade_retrace_fraction
        self.prior_context_source = source
        self.prior_context_status = status
        self.prior_context_reason = reason
        if maintenance_source:
            self.prior_context_maintenance_source = maintenance_source
        if live_5m_open_time_ms is not None:
            self.prior_context_last_live_5m_open_time_ms = live_5m_open_time_ms
            self.prior_context_last_live_5m_close_time_ms = live_5m_close_time_ms
            if not live_5m_gap_rejected:
                self.prior_context_live_5m_appended_count += 1
            if live_5m_gap_tolerated:
                self.prior_context_live_5m_gap_tolerated_count += 1
            if live_5m_gap_rejected:
                self.prior_context_live_5m_gap_rejected_count += 1
            self.prior_context_last_live_5m_missing_aggtrade_ids = int(live_5m_missing_aggtrade_ids)
            self.prior_context_last_live_5m_gap_tolerance = int(live_5m_gap_tolerance)
        self.mark_dirty(now_ms=fetched_at_ms)

    def to_artifact_row(self, *, now_ms: int | None = None, aggtrade_stale_ms: int | None = None) -> dict[str, object]:
        effective_live_status = self.live_aggtrade_status
        effective_live_reason = self.live_aggtrade_reason
        effective_live_age_ms: int | None = None
        if now_ms is not None and aggtrade_stale_ms is not None:
            effective_live_status, effective_live_reason, effective_live_age_ms = self.effective_live_aggtrade_status(
                now_ms=int(now_ms),
                stale_ms=int(aggtrade_stale_ms),
            )
        row: dict[str, object] = {
            "symbol": self.symbol,
            "status": self.status.value,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "dirty_since_ms": self.dirty_since_ms,
            "decision_dirty_since_ms": self.decision_dirty_since_ms,
            "actionable_since_ms": self.actionable_since_ms,
            "first_actionable_ms": self.first_actionable_ms,
            "last_actionable_ms": self.last_actionable_ms,
            "stage0_passed_ms": self.stage0_passed_ms,
            "stage1_passed_ms": self.stage1_passed_ms,
            "stage2_passed_ms": self.stage2_passed_ms,
            "stage3_passed_ms": self.stage3_passed_ms,
            "stage4_passed_ms": self.stage4_passed_ms,
            "stage5_passed_ms": self.stage5_passed_ms,
            "decision_deadline_ms": self.decision_deadline_ms,
            "last_decision_bucket_ms": self.last_decision_bucket_ms,
            "last_verdict": self.last_verdict,
            "last_verdict_reason": self.last_verdict_reason,
            "last_decision_latency_ms": self.last_decision_latency_ms,
            "decision_count": self.decision_count,
            "rejected_decision_count": self.rejected_decision_count,
            "data_not_ready_decision_count": self.data_not_ready_decision_count,
            "data_dependency_not_ready_decision_count": self.data_dependency_not_ready_decision_count,
            "flow_freshness_reject_decision_count": self.flow_freshness_reject_decision_count,
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
            "live_aggtrade_effective_status": effective_live_status,
            "live_aggtrade_effective_reason": effective_live_reason,
            "live_aggtrade_age_ms": effective_live_age_ms,
            "mark_market_id": self.mark_market_id,
            "mark_first_seen_ms": self.mark_first_seen_ms,
            "mark_last_seen_ms": self.mark_last_seen_ms,
            "mark_event_time_ms": self.mark_event_time_ms,
            "mark_update_count": self.mark_update_count,
            "mark_price": self.mark_price,
            "mark_index_price": self.mark_index_price,
            "mark_estimated_settle_price": self.mark_estimated_settle_price,
            "mark_funding_rate": self.mark_funding_rate,
            "mark_next_funding_time_ms": self.mark_next_funding_time_ms,
            "mark_source": self.mark_source,
            "mark_status": self.mark_status,
            "mark_reason": self.mark_reason,
            "oi_first_seen_ms": self.oi_first_seen_ms,
            "oi_last_seen_ms": self.oi_last_seen_ms,
            "oi_latest_timestamp_ms": self.oi_latest_timestamp_ms,
            "oi_previous_timestamp_ms": self.oi_previous_timestamp_ms,
            "oi_update_count": self.oi_update_count,
            "oi_open_interest": self.oi_open_interest,
            "oi_previous_open_interest": self.oi_previous_open_interest,
            "oi_change_pct_3x5m": self.oi_change_pct_3x5m,
            "oi_rows_received": self.oi_rows_received,
            "oi_source": self.oi_source,
            "oi_status": self.oi_status,
            "oi_reason": self.oi_reason,
            "current_oi_first_seen_ms": self.current_oi_first_seen_ms,
            "current_oi_last_seen_ms": self.current_oi_last_seen_ms,
            "current_oi_timestamp_ms": self.current_oi_timestamp_ms,
            "current_oi_update_count": self.current_oi_update_count,
            "current_oi_open_interest": self.current_oi_open_interest,
            "current_oi_source": self.current_oi_source,
            "current_oi_status": self.current_oi_status,
            "current_oi_reason": self.current_oi_reason,
            "current_oi_first_ok_seen_ms": self.current_oi_first_ok_seen_ms,
            "current_oi_first_ok_timestamp_ms": self.current_oi_first_ok_timestamp_ms,
            "current_oi_first_ok_open_interest": self.current_oi_first_ok_open_interest,
            "current_oi_first_ok_source": self.current_oi_first_ok_source,
            "current_oi_first_ok_status": self.current_oi_first_ok_status,
            "current_oi_first_ok_reason": self.current_oi_first_ok_reason,
            "prior_context_first_seen_ms": self.prior_context_first_seen_ms,
            "prior_context_last_seen_ms": self.prior_context_last_seen_ms,
            "prior_context_start_ms": self.prior_context_start_ms,
            "prior_context_end_ms": self.prior_context_end_ms,
            "prior_context_update_count": self.prior_context_update_count,
            "prior_context_rows_received": self.prior_context_rows_received,
            "prior_context_rows_used": self.prior_context_rows_used,
            "prior_spike_count_24h": self.prior_spike_count_24h,
            "prior_fast_fade_count_24h": self.prior_fast_fade_count_24h,
            "prior_high_24h": self.prior_high_24h,
            "prior_low_before_high_24h": self.prior_low_before_high_24h,
            "prior_low_after_high_24h": self.prior_low_after_high_24h,
            "prior_context_spike_return_pct": self.prior_context_spike_return_pct,
            "prior_context_fast_fade_retrace_fraction": self.prior_context_fast_fade_retrace_fraction,
            "prior_context_source": self.prior_context_source,
            "prior_context_status": self.prior_context_status,
            "prior_context_reason": self.prior_context_reason,
            "prior_context_maintenance_source": self.prior_context_maintenance_source,
            "prior_context_last_live_5m_open_time_ms": self.prior_context_last_live_5m_open_time_ms,
            "prior_context_last_live_5m_close_time_ms": self.prior_context_last_live_5m_close_time_ms,
            "prior_context_live_5m_appended_count": self.prior_context_live_5m_appended_count,
            "prior_context_live_5m_gap_tolerated_count": self.prior_context_live_5m_gap_tolerated_count,
            "prior_context_live_5m_gap_rejected_count": self.prior_context_live_5m_gap_rejected_count,
            "prior_context_last_live_5m_missing_aggtrade_ids": self.prior_context_last_live_5m_missing_aggtrade_ids,
            "prior_context_last_live_5m_gap_tolerance": self.prior_context_last_live_5m_gap_tolerance,
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
        current_fetched_at_ms: int | None = None,
        current_timestamp_ms: int | None = None,
        current_open_interest: float | None = None,
        current_source: str = "",
        current_status: str = "",
        current_reason: str = "",
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
                current_fetched_at_ms=current_fetched_at_ms,
                current_timestamp_ms=current_timestamp_ms,
                current_open_interest=current_open_interest,
                current_source=current_source,
                current_status=current_status,
                current_reason=current_reason,
            )

    def update_aggtrade(self, trade: Live2AggTradeEvent, *, received_at_ms: int) -> None:
        with self._lock:
            state = self.get_or_create(trade.symbol)
            state.update_aggtrade(trade, received_at_ms=received_at_ms)

    def update_mark_price(
        self,
        *,
        symbol: str,
        market_id: str,
        received_at_ms: int,
        event_time_ms: int | None,
        mark_price: float,
        index_price: float | None,
        estimated_settle_price: float | None,
        funding_rate: float | None,
        next_funding_time_ms: int | None,
        source: str,
        status: str,
        reason: str,
    ) -> None:
        with self._lock:
            state = self.get_or_create(symbol)
            state.update_mark_price(
                market_id=market_id,
                received_at_ms=received_at_ms,
                event_time_ms=event_time_ms,
                mark_price=mark_price,
                index_price=index_price,
                estimated_settle_price=estimated_settle_price,
                funding_rate=funding_rate,
                next_funding_time_ms=next_funding_time_ms,
                source=source,
                status=status,
                reason=reason,
            )

    def update_open_interest(
        self,
        *,
        symbol: str,
        fetched_at_ms: int,
        latest_timestamp_ms: int | None,
        previous_timestamp_ms: int | None,
        open_interest: float | None,
        previous_open_interest: float | None,
        open_interest_change_pct_3x5m: float | None,
        rows_received: int,
        source: str,
        status: str,
        reason: str,
        current_fetched_at_ms: int | None = None,
        current_timestamp_ms: int | None = None,
        current_open_interest: float | None = None,
        current_source: str = "",
        current_status: str = "",
        current_reason: str = "",
    ) -> None:
        with self._lock:
            state = self.get_or_create(symbol)
            state.update_open_interest(
                fetched_at_ms=fetched_at_ms,
                latest_timestamp_ms=latest_timestamp_ms,
                previous_timestamp_ms=previous_timestamp_ms,
                open_interest=open_interest,
                previous_open_interest=previous_open_interest,
                open_interest_change_pct_3x5m=open_interest_change_pct_3x5m,
                rows_received=rows_received,
                source=source,
                status=status,
                reason=reason,
                current_fetched_at_ms=current_fetched_at_ms,
                current_timestamp_ms=current_timestamp_ms,
                current_open_interest=current_open_interest,
                current_source=current_source,
                current_status=current_status,
                current_reason=current_reason,
            )


    def update_prior_context(
        self,
        *,
        symbol: str,
        fetched_at_ms: int,
        context_start_ms: int | None,
        context_end_ms: int | None,
        rows_received: int,
        rows_used: int,
        prior_spike_count_24h: int | None,
        prior_fast_fade_count_24h: int | None,
        prior_high_24h: float | None,
        prior_low_before_high_24h: float | None,
        prior_low_after_high_24h: float | None,
        spike_return_pct: float | None,
        fast_fade_retrace_fraction: float | None,
        source: str,
        status: str,
        reason: str,
        maintenance_source: str = "",
        live_5m_open_time_ms: int | None = None,
        live_5m_close_time_ms: int | None = None,
        live_5m_gap_tolerated: bool = False,
        live_5m_gap_rejected: bool = False,
        live_5m_missing_aggtrade_ids: int = 0,
        live_5m_gap_tolerance: int = 0,
    ) -> None:
        with self._lock:
            state = self.get_or_create(symbol)
            state.update_prior_context(
                fetched_at_ms=fetched_at_ms,
                context_start_ms=context_start_ms,
                context_end_ms=context_end_ms,
                rows_received=rows_received,
                rows_used=rows_used,
                prior_spike_count_24h=prior_spike_count_24h,
                prior_fast_fade_count_24h=prior_fast_fade_count_24h,
                prior_high_24h=prior_high_24h,
                prior_low_before_high_24h=prior_low_before_high_24h,
                prior_low_after_high_24h=prior_low_after_high_24h,
                spike_return_pct=spike_return_pct,
                fast_fade_retrace_fraction=fast_fade_retrace_fraction,
                source=source,
                status=status,
                reason=reason,
                maintenance_source=maintenance_source,
                live_5m_open_time_ms=live_5m_open_time_ms,
                live_5m_close_time_ms=live_5m_close_time_ms,
                live_5m_gap_tolerated=live_5m_gap_tolerated,
                live_5m_gap_rejected=live_5m_gap_rejected,
                live_5m_missing_aggtrade_ids=live_5m_missing_aggtrade_ids,
                live_5m_gap_tolerance=live_5m_gap_tolerance,
            )


    def update_aggtrade_many(self, trades: tuple[Live2AggTradeEvent, ...], *, received_at_ms: int) -> None:
        if not trades:
            return
        with self._lock:
            for trade in trades:
                state = self.get_or_create(trade.symbol)
                state.update_aggtrade(trade, received_at_ms=received_at_ms)

    def append_closed_candles(self, *, symbol: str, candles: tuple[Live2Candle, ...]) -> None:
        if not candles:
            return
        with self._lock:
            state = self.get_or_create(symbol)
            by_timeframe: dict[int, list[Live2Candle]] = {}
            for candle in candles:
                by_timeframe.setdefault(int(candle.timeframe_ms), []).append(candle)
            for timeframe_ms, timeframe_candles in by_timeframe.items():
                ring = state.candle_book.rings.get(timeframe_ms)
                if ring is None:
                    continue
                merged_by_open_time = {int(item.open_time_ms): item for item in ring.closed}
                for candle in timeframe_candles:
                    # Official REST klines may repair or replace a partial live-built
                    # 1m context candle.  Keep one candle per open time and preserve
                    # chronological order; never append old repair candles to the tail.
                    merged_by_open_time[int(candle.open_time_ms)] = candle
                ordered = [merged_by_open_time[key] for key in sorted(merged_by_open_time)]
                ring.closed.clear()
                ring.closed.extend(ordered[-int(ring.max_closed_candles):])
                ring.last_closed_open_time_ms = None if not ring.closed else int(ring.closed[-1].open_time_ms)
                ring.closed_count = len(ring.closed)
            state.mark_dirty()

    def close_due_candles(self, *, now_ms: int) -> int:
        """Finalize ended real-trade candles without synthetic gap filling."""

        closed_symbols = 0
        with self._lock:
            for state in self._states.values():
                if not state.universe_selected:
                    continue
                result = state.candle_book.close_due(now_ms=int(now_ms))
                if result.closed_count <= 0:
                    continue
                state.candle_gap_count = state.candle_book.total_gap_count()
                state.candle_out_of_order_count = state.candle_book.total_out_of_order_count()
                if state.live_aggtrade_update_count > 0:
                    state.candle_coverage_status = "live_ready"
                elif state.startup_aggtrade_update_count > 0:
                    state.candle_coverage_status = "startup_warmup_only"
                else:
                    state.candle_coverage_status = "not_ready"
                state.decision_dirty_since_ms = int(now_ms)
                state.mark_dirty(now_ms=int(now_ms))
                closed_symbols += 1
        return closed_symbols

    def snapshot(self) -> tuple[SymbolState, ...]:
        with self._lock:
            return tuple(self._states.values())

    def decision_snapshot(self) -> tuple[SymbolState, ...]:
        with self._lock:
            return tuple(
                state
                for state in self._states.values()
                if state.universe_selected and state.decision_dirty_since_ms is not None
            )

    def counts_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in SymbolLive2Status}
        with self._lock:
            for state in self._states.values():
                counts[state.status.value] = counts.get(state.status.value, 0) + 1
        return counts

    def actionable_symbol_counts(self, *, now_ms: int, ttl_ms: int, session_start_ms: int | None = None) -> dict[str, object]:
        current = 0
        session_seen = 0
        cutoff_ms = int(now_ms) - int(ttl_ms)
        session_cutoff_ms = None if session_start_ms is None else int(session_start_ms)
        with self._lock:
            for state in self._states.values():
                last_actionable_ms = state.last_actionable_ms
                if last_actionable_ms is None:
                    continue
                if int(last_actionable_ms) >= cutoff_ms:
                    current += 1
                if session_cutoff_ms is None or int(last_actionable_ms) >= session_cutoff_ms:
                    session_seen += 1
        return {
            "current": current,
            "seen": session_seen,
            "session_seen": session_seen,
            "session_start_ms": session_cutoff_ms,
            "source": "last_actionable_ms",
        }

    def stage_symbol_counts(self, *, now_ms: int, ttl_ms: int, session_start_ms: int | None = None) -> dict[str, object]:
        current_by_stage: dict[str, int] = {label: 0 for label in LIVE2_STAGE_LABELS}
        session_seen_by_stage: dict[str, int] = {label: 0 for label in LIVE2_STAGE_LABELS}
        cutoff_ms = int(now_ms) - int(ttl_ms)
        session_cutoff_ms = None if session_start_ms is None else int(session_start_ms)
        stage_fields = (
            ("stage0", "stage0_passed_ms"),
            ("stage1", "stage1_passed_ms"),
            ("stage2", "stage2_passed_ms"),
            ("stage3", "stage3_passed_ms"),
            ("stage4", "stage4_passed_ms"),
            ("stage5", "stage5_passed_ms"),
        )
        with self._lock:
            for state in self._states.values():
                for label, field_name in stage_fields:
                    passed_ms = getattr(state, field_name)
                    if passed_ms is None:
                        continue
                    if int(passed_ms) >= cutoff_ms:
                        current_by_stage[label] += 1
                    if session_cutoff_ms is None or int(passed_ms) >= session_cutoff_ms:
                        session_seen_by_stage[label] += 1
        stages = {
            label: {
                "current": current_by_stage[label],
                "session_seen": session_seen_by_stage[label],
            }
            for label in LIVE2_STAGE_LABELS
        }
        return {
            "ttl_ms": int(ttl_ms),
            "session_start_ms": session_cutoff_ms,
            "stages": stages,
        }

    def ticker_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.ticker_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def aggtrade_counts(self, *, now_ms: int | None = None, stale_ms: int | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        with self._lock:
            for state in self._states.values():
                if stale_ms is not None and state.aggtrade_source == LIVE2_AGGTRADE_WS_SOURCE:
                    key, _, _ = state.effective_live_aggtrade_status(now_ms=effective_now_ms, stale_ms=int(stale_ms))
                else:
                    key = state.aggtrade_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def mark_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.mark_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def open_interest_counts(self, *, now_ms: int | None = None, stale_ms: int | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        with self._lock:
            for state in self._states.values():
                key = state.oi_status or "unknown"
                if (
                    key == "ok"
                    and stale_ms is not None
                    and state.oi_last_seen_ms is not None
                    and effective_now_ms - state.oi_last_seen_ms > stale_ms
                ):
                    key = "stale"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def prior_context_counts(self, *, now_ms: int | None = None, stale_ms: int | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        with self._lock:
            for state in self._states.values():
                key = state.prior_context_status or "unknown"
                if (
                    key == "ok"
                    and stale_ms is not None
                    and state.prior_context_last_seen_ms is not None
                    and effective_now_ms - state.prior_context_last_seen_ms > stale_ms
                ):
                    key = "stale"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def startup_aggtrade_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.startup_aggtrade_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts

    def live_aggtrade_counts(self, *, now_ms: int | None = None, stale_ms: int | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        with self._lock:
            for state in self._states.values():
                if stale_ms is None:
                    key = state.live_aggtrade_status or "unknown"
                else:
                    key, _, _ = state.effective_live_aggtrade_status(now_ms=effective_now_ms, stale_ms=int(stale_ms))
                counts[key] = counts.get(key, 0) + 1
        return counts

    def candle_coverage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.candle_coverage_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts
