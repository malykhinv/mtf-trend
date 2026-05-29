"""Strict verified execution boundary for anomaly live2.

Live2 execution is intentionally exchange-first. A selected signal may proceed
only through this contract:

1. startup account preflight;
2. pre-entry exchange position check;
3. deterministic client id market order;
4. actual fill recovered from exchange order/trades;
5. post-entry exchange position delta check;
6. reduce-only stop-market order;
7. stop visibility verification through the stop/algo-order boundary.

No candle/ticker price is used as a fill substitute. If exposure is created and
protection cannot be verified, the engine attempts an emergency reduce-only
market close and marks execution as unsafe for any further entries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from math import isfinite
from typing import Protocol, runtime_checkable

from data.exchanges.ccxt_types import ExchangeLiveAccountPreflight, ExchangeOpenInterestSnapshot, ExchangeOrderFill

from .clock import utc_now_ms
from .entry_guard import Live2EntryGuardResult
from .signal import Live2SignalDecision
from .state import SymbolState


@runtime_checkable
class Live2ExecutionExchange(Protocol):
    """Typed exchange boundary required by live2 execution."""

    def fetch_live_account_preflight(self) -> ExchangeLiveAccountPreflight:
        """Return explicit account-mode snapshot for real-order live trading."""
        ...

    def fetch_symbol_position_amount(self, symbol: str) -> float:
        """Return signed exchange position amount for one symbol."""
        ...

    def create_market_order_with_fill(
        self,
        symbol: str,
        side: str,
        amount: float,
        *,
        reduce_only: bool,
        client_order_id: str,
    ) -> ExchangeOrderFill:
        """Submit a market order and return verified actual fill fields."""
        ...

    def create_stop_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        *,
        client_order_id: str,
    ) -> dict[str, object]:
        """Create a reduce-only stop-market order with deterministic client id."""
        ...

    def fetch_stop_order_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, object]:
        """Return a visible open conditional stop order by client id."""
        ...

    def cancel_stop_order(self, symbol: str, order_id: str) -> dict[str, object]:
        """Cancel a conditional stop order by exchange order id."""
        ...

    def fetch_usdt_free_balance(self) -> float:
        """Return current free USDT balance for risk-based position sizing."""
        ...


@runtime_checkable
class Live2CurrentOpenInterestExchange(Protocol):
    """Optional typed exchange boundary for post-fill current OI snapshots."""

    def fetch_current_open_interest(self, symbol: str) -> ExchangeOpenInterestSnapshot:
        """Return current open interest for one symbol."""
        ...


@dataclass(frozen=True, slots=True)
class Live2ExecutionConfig:
    """Strict execution contract for live2 real-order entries."""

    max_existing_position_abs_amount: float = 0.0
    order_notional_usdt: float = 12.0
    risk_per_trade_pct: float = 0.02
    max_total_open_risk_pct: float = 0.08
    max_open_positions: int = 0
    max_position_amount_slippage_ratio: float = 0.05
    stop_visibility_attempts: int = 5
    stop_visibility_sleep_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.max_existing_position_abs_amount < 0:
            raise ValueError("max_existing_position_abs_amount must be >= 0")
        if self.order_notional_usdt <= 0:
            raise ValueError("order_notional_usdt must be > 0")
        if self.risk_per_trade_pct <= 0.0:
            raise ValueError("risk_per_trade_pct must be > 0")
        if self.max_total_open_risk_pct < self.risk_per_trade_pct:
            raise ValueError("max_total_open_risk_pct must be >= risk_per_trade_pct")
        if self.max_open_positions < 0:
            raise ValueError("max_open_positions must be >= 0; 0 means unlimited")
        if self.max_position_amount_slippage_ratio < 0:
            raise ValueError("max_position_amount_slippage_ratio must be >= 0")
        if self.stop_visibility_attempts <= 0:
            raise ValueError("stop_visibility_attempts must be > 0")
        if self.stop_visibility_sleep_seconds < 0:
            raise ValueError("stop_visibility_sleep_seconds must be >= 0")


@dataclass(frozen=True, slots=True)
class Live2ExecutionPreflightResult:
    status: str
    reason: str
    exchange: str = ""
    position_mode: str = ""
    hedge_mode_enabled: bool | None = None
    checked_at_ms: int = 0

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "exchange": self.exchange,
            "position_mode": self.position_mode,
            "hedge_mode_enabled": self.hedge_mode_enabled,
            "checked_at_ms": self.checked_at_ms,
        }


@dataclass(frozen=True, slots=True)
class Live2ProtectedPosition:
    """Local registry row for a position whose current stop is verified."""

    position_id: str
    symbol: str
    opened_at_ms: int
    entry_order_id: str
    entry_client_order_id: str
    entry_fill_price: float
    amount: float
    stop_price: float
    stop_order_id: str
    stop_client_order_id: str
    pre_position_amount: float
    post_position_amount: float
    category_id: str
    signal_entry_price: float
    initial_risk_pct: float
    tp1_price: float
    initial_amount: float
    remaining_amount: float
    entry_5m_oi_open_interest: float | None = None
    entry_5m_oi_previous_open_interest: float | None = None
    entry_5m_oi_change_pct_3x5m: float | None = None
    entry_5m_oi_latest_timestamp_ms: int | None = None
    pump_start_current_oi_open_interest: float | None = None
    pump_start_current_oi_timestamp_ms: int | None = None
    pump_start_current_oi_last_seen_ms: int | None = None
    pump_start_current_oi_source: str = ""
    pump_start_current_oi_status: str = "not_seen"
    pump_start_current_oi_reason: str = ""
    signal_current_oi_open_interest: float | None = None
    signal_current_oi_timestamp_ms: int | None = None
    signal_current_oi_last_seen_ms: int | None = None
    signal_current_oi_source: str = ""
    signal_current_oi_status: str = "not_seen"
    signal_current_oi_reason: str = ""
    entry_current_oi_open_interest: float | None = None
    entry_current_oi_timestamp_ms: int | None = None
    entry_current_oi_last_seen_ms: int | None = None
    entry_current_oi_source: str = ""
    entry_current_oi_status: str = "not_seen"
    entry_current_oi_reason: str = ""
    source_flow_window_ms: int | None = None
    source_flow_quote_per_second: float | None = None
    source_flow_trades_per_second: float | None = None
    source_flow_quote_ratio: float | None = None
    source_flow_trade_ratio: float | None = None
    selected_rolling_htf_timeframe_ms: int | None = None
    tp1_close_fraction: float = 1.0
    tp1_closed_amount: float = 0.0
    tp1_fill_price: float | None = None
    tp1_order_id: str = ""
    tp1_client_order_id: str = ""
    tp1_closed_at_ms: int | None = None
    realized_pnl_usdt: float = 0.0
    status: str = "protected_initial_stop_verified"
    closed_at_ms: int | None = None
    close_reason: str = ""
    early_exit_observed_count: int = 0
    early_exit_last_reason: str = ""
    early_exit_last_observed_ms: int | None = None
    last_supervised_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "opened_at_ms": self.opened_at_ms,
            "entry_order_id": self.entry_order_id,
            "entry_client_order_id": self.entry_client_order_id,
            "entry_fill_price": self.entry_fill_price,
            "amount": self.amount,
            "stop_price": self.stop_price,
            "stop_order_id": self.stop_order_id,
            "stop_client_order_id": self.stop_client_order_id,
            "pre_position_amount": self.pre_position_amount,
            "post_position_amount": self.post_position_amount,
            "category_id": self.category_id,
            "signal_entry_price": self.signal_entry_price,
            "initial_risk_pct": self.initial_risk_pct,
            "tp1_price": self.tp1_price,
            "initial_amount": self.initial_amount,
            "remaining_amount": self.remaining_amount,
            "entry_5m_oi_open_interest": self.entry_5m_oi_open_interest,
            "entry_5m_oi_previous_open_interest": self.entry_5m_oi_previous_open_interest,
            "entry_5m_oi_change_pct_3x5m": self.entry_5m_oi_change_pct_3x5m,
            "entry_5m_oi_latest_timestamp_ms": self.entry_5m_oi_latest_timestamp_ms,
            "pump_start_current_oi_open_interest": self.pump_start_current_oi_open_interest,
            "pump_start_current_oi_timestamp_ms": self.pump_start_current_oi_timestamp_ms,
            "pump_start_current_oi_last_seen_ms": self.pump_start_current_oi_last_seen_ms,
            "pump_start_current_oi_source": self.pump_start_current_oi_source,
            "pump_start_current_oi_status": self.pump_start_current_oi_status,
            "pump_start_current_oi_reason": self.pump_start_current_oi_reason,
            "signal_current_oi_open_interest": self.signal_current_oi_open_interest,
            "signal_current_oi_timestamp_ms": self.signal_current_oi_timestamp_ms,
            "signal_current_oi_last_seen_ms": self.signal_current_oi_last_seen_ms,
            "signal_current_oi_source": self.signal_current_oi_source,
            "signal_current_oi_status": self.signal_current_oi_status,
            "signal_current_oi_reason": self.signal_current_oi_reason,
            "entry_current_oi_open_interest": self.entry_current_oi_open_interest,
            "entry_current_oi_timestamp_ms": self.entry_current_oi_timestamp_ms,
            "entry_current_oi_last_seen_ms": self.entry_current_oi_last_seen_ms,
            "entry_current_oi_source": self.entry_current_oi_source,
            "entry_current_oi_status": self.entry_current_oi_status,
            "entry_current_oi_reason": self.entry_current_oi_reason,
            "source_flow_window_ms": self.source_flow_window_ms,
            "source_flow_quote_per_second": self.source_flow_quote_per_second,
            "source_flow_trades_per_second": self.source_flow_trades_per_second,
            "source_flow_quote_ratio": self.source_flow_quote_ratio,
            "source_flow_trade_ratio": self.source_flow_trade_ratio,
            "selected_rolling_htf_timeframe_ms": self.selected_rolling_htf_timeframe_ms,
            "tp1_close_fraction": self.tp1_close_fraction,
            "tp1_closed_amount": self.tp1_closed_amount,
            "tp1_fill_price": self.tp1_fill_price,
            "tp1_order_id": self.tp1_order_id,
            "tp1_client_order_id": self.tp1_client_order_id,
            "tp1_closed_at_ms": self.tp1_closed_at_ms,
            "realized_pnl_usdt": self.realized_pnl_usdt,
            "status": self.status,
            "closed_at_ms": self.closed_at_ms,
            "close_reason": self.close_reason,
            "early_exit_observed_count": self.early_exit_observed_count,
            "early_exit_last_reason": self.early_exit_last_reason,
            "early_exit_last_observed_ms": self.early_exit_last_observed_ms,
            "last_supervised_ms": self.last_supervised_ms,
        }


@dataclass(frozen=True, slots=True)
class Live2ExecutionResult:
    verdict: str
    reason: str
    checked_at_ms: int
    pre_position_amount: float | None = None
    post_position_amount: float | None = None
    position_delta_amount: float | None = None
    exchange_boundary_status: str = ""
    order_placement_status: str = "not_attempted"
    entry_order_id: str = ""
    entry_client_order_id: str = ""
    entry_fill_price: float | None = None
    entry_filled_amount: float | None = None
    entry_fill_timestamp_ms: int | None = None
    stop_order_id: str = ""
    stop_client_order_id: str = ""
    stop_price: float | None = None
    position_id: str = ""
    emergency_close_status: str = "not_attempted"
    integrity_error: bool = False
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    duration_ms: int | None = None
    timing: dict[str, object] = field(default_factory=dict)
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "checked_at_ms": self.checked_at_ms,
            "pre_position_amount": self.pre_position_amount,
            "post_position_amount": self.post_position_amount,
            "position_delta_amount": self.position_delta_amount,
            "exchange_boundary_status": self.exchange_boundary_status,
            "order_placement_status": self.order_placement_status,
            "entry_order_id": self.entry_order_id,
            "entry_client_order_id": self.entry_client_order_id,
            "entry_fill_price": self.entry_fill_price,
            "entry_filled_amount": self.entry_filled_amount,
            "entry_fill_timestamp_ms": self.entry_fill_timestamp_ms,
            "stop_order_id": self.stop_order_id,
            "stop_client_order_id": self.stop_client_order_id,
            "stop_price": self.stop_price,
            "position_id": self.position_id,
            "emergency_close_status": self.emergency_close_status,
            "integrity_error": self.integrity_error,
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "duration_ms": self.duration_ms,
            "timing": self.timing,
            "details": self.details,
        }


class Live2ExecutionEngine:
    """Strict verified real-order execution engine for live2 entries."""

    def __init__(
        self,
        *,
        exchange_client: Live2ExecutionExchange | None,
        config: Live2ExecutionConfig | None = None,
    ) -> None:
        self.exchange_client = exchange_client
        self.config = config or Live2ExecutionConfig()
        self.preflight_result: Live2ExecutionPreflightResult = Live2ExecutionPreflightResult(
            status="not_checked",
            reason="execution_preflight_not_checked",
            checked_at_ms=0,
        )
        self._protected_positions: dict[str, Live2ProtectedPosition] = {}
        self._symbol_cooldown_until_ms: dict[str, int] = {}
        self._trading_halted_reason = ""
        self._total_execute_calls = 0
        self._total_rejected_existing_position = 0
        self._total_rejected_capacity = 0
        self._total_orders_submitted = 0
        self._total_positions_protected = 0
        self._total_integrity_errors = 0
        self._total_exchange_errors = 0

    @property
    def ready(self) -> bool:
        return self.preflight_result.ready and not self._trading_halted_reason

    def preflight(self) -> Live2ExecutionPreflightResult:
        checked_at_ms = utc_now_ms()
        if self.exchange_client is None:
            self.preflight_result = Live2ExecutionPreflightResult(
                status="not_ready",
                reason="exchange_client_not_provided",
                checked_at_ms=checked_at_ms,
            )
            return self.preflight_result
        try:
            snapshot = self.exchange_client.fetch_live_account_preflight()
        except Exception as exc:
            self._total_exchange_errors += 1
            self.preflight_result = Live2ExecutionPreflightResult(
                status="not_ready",
                reason=f"exchange_preflight_failed:{type(exc).__name__}:{exc}",
                checked_at_ms=checked_at_ms,
            )
            return self.preflight_result
        if snapshot.hedge_mode_enabled:
            self.preflight_result = Live2ExecutionPreflightResult(
                status="not_ready",
                reason="hedge_mode_not_supported_by_live2_execution_contract",
                exchange=snapshot.exchange,
                position_mode=snapshot.position_mode,
                hedge_mode_enabled=snapshot.hedge_mode_enabled,
                checked_at_ms=checked_at_ms,
            )
            return self.preflight_result
        self.preflight_result = Live2ExecutionPreflightResult(
            status="ready",
            reason="exchange_account_preflight_ok",
            exchange=snapshot.exchange,
            position_mode=snapshot.position_mode,
            hedge_mode_enabled=snapshot.hedge_mode_enabled,
            checked_at_ms=checked_at_ms,
        )
        return self.preflight_result

    def execute_selected(
        self,
        *,
        state: SymbolState,
        signal_decision: Live2SignalDecision,
        entry_guard_result: Live2EntryGuardResult,
    ) -> Live2ExecutionResult:
        self._total_execute_calls += 1
        started_at_ms = utc_now_ms()
        checked_at_ms = started_at_ms
        timing: dict[str, object] = {"started_at_ms": started_at_ms}
        if self._trading_halted_reason:
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_halted",
                reason=self._trading_halted_reason,
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="halted",
                ),
                timing=timing,
            )
        if not self.preflight_result.ready:
            self._total_exchange_errors += 1
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_boundary_not_ready",
                reason=self.preflight_result.reason or "execution_preflight_not_ready",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status=self.preflight_result.status,
                ),
                timing=timing,
            )
        if self.exchange_client is None:
            self._total_exchange_errors += 1
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_boundary_not_ready",
                reason="exchange_client_not_provided",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="not_ready",
                ),
                timing=timing,
            )
        if state.symbol in self._protected_positions:
            self._total_rejected_existing_position += 1
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_symbol_already_has_live2_position",
                reason="symbol_already_has_open_live2_position",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="ready",
                ),
                timing=timing,
            )
        cooldown_until_ms = int(self._symbol_cooldown_until_ms.get(state.symbol, 0) or 0)
        if cooldown_until_ms > checked_at_ms:
            self._total_rejected_capacity += 1
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_symbol_cooldown_active",
                reason="symbol_cooldown_until_rolling_htf_window_expires",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="ready",
                details={"cooldown_until_ms": cooldown_until_ms, "cooldown_remaining_ms": cooldown_until_ms - checked_at_ms},
                ),
                timing=timing,
            )
        if self.config.max_open_positions > 0 and len(self._protected_positions) >= self.config.max_open_positions:
            self._total_rejected_capacity += 1
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_capacity_full",
                reason="live2_max_open_positions_reached",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="ready",
                details={"max_open_positions": self.config.max_open_positions},
                ),
                timing=timing,
            )

        precheck_started_at_ms = utc_now_ms()
        timing["pre_position_fetch_started_at_ms"] = precheck_started_at_ms
        precheck = self._fetch_pre_position(state.symbol, checked_at_ms=checked_at_ms)
        precheck_finished_at_ms = utc_now_ms()
        timing["pre_position_fetch_finished_at_ms"] = precheck_finished_at_ms
        timing["pre_position_fetch_duration_ms"] = max(0, precheck_finished_at_ms - precheck_started_at_ms)
        if precheck.verdict != "pre_position_flat":
            return self._finish_result(precheck, timing=timing)
        pre_position_amount = float(precheck.pre_position_amount or 0.0)

        live_price = entry_guard_result.live_price
        stop_price = signal_decision.initial_stop_at_decision
        signal_entry_price = signal_decision.signal_entry_price
        if not _positive_finite(live_price) or not _positive_finite(stop_price) or not _positive_finite(signal_entry_price):
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_invalid_signal_prices",
                reason="live_price_stop_or_signal_entry_is_missing_or_invalid",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
                ),
                timing=timing,
            )
        planned_initial_risk_pct = (float(live_price) - float(stop_price)) / float(live_price) if live_price else 0.0
        if not isfinite(planned_initial_risk_pct) or planned_initial_risk_pct <= 0.0:
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_invalid_planned_risk",
                reason="planned_stop_is_not_below_live_entry_price",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
                details={"live_price": live_price, "stop_price": stop_price},
                ),
                timing=timing,
            )
        balance_started_at_ms = utc_now_ms()
        timing["balance_fetch_started_at_ms"] = balance_started_at_ms
        try:
            account_balance_usdt = float(self.exchange_client.fetch_usdt_free_balance())
        except Exception as exc:
            self._total_exchange_errors += 1
            balance_finished_at_ms = utc_now_ms()
            timing["balance_fetch_finished_at_ms"] = balance_finished_at_ms
            timing["balance_fetch_duration_ms"] = max(0, balance_finished_at_ms - balance_started_at_ms)
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_balance_fetch_failed",
                reason=f"fetch_usdt_free_balance_failed:{type(exc).__name__}:{exc}",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
                ),
                timing=timing,
            )
        balance_finished_at_ms = utc_now_ms()
        timing["balance_fetch_finished_at_ms"] = balance_finished_at_ms
        timing["balance_fetch_duration_ms"] = max(0, balance_finished_at_ms - balance_started_at_ms)
        if not isfinite(account_balance_usdt) or account_balance_usdt <= 0.0:
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_invalid_account_balance",
                reason="free_usdt_balance_is_not_positive_finite",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
                details={"account_balance_usdt": account_balance_usdt},
                ),
                timing=timing,
            )
        current_open_risk_usdt = self._open_risk_usdt()
        max_total_open_risk_usdt = account_balance_usdt * self.config.max_total_open_risk_pct
        planned_risk_usdt = account_balance_usdt * self.config.risk_per_trade_pct
        if current_open_risk_usdt + planned_risk_usdt > max_total_open_risk_usdt + 1e-9:
            self._total_rejected_capacity += 1
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_risk_capacity_full",
                reason="live2_max_total_open_risk_reached",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
                details={
                    "account_balance_usdt": account_balance_usdt,
                    "risk_per_trade_pct": self.config.risk_per_trade_pct,
                    "max_total_open_risk_pct": self.config.max_total_open_risk_pct,
                    "current_open_risk_usdt": current_open_risk_usdt,
                    "planned_risk_usdt": planned_risk_usdt,
                    "max_total_open_risk_usdt": max_total_open_risk_usdt,
                },
                ),
                timing=timing,
            )
        order_notional_usdt = max(self.config.order_notional_usdt, planned_risk_usdt / planned_initial_risk_pct)
        amount = order_notional_usdt / float(live_price)
        if not _positive_finite(amount):
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_execution_invalid_order_amount",
                reason="computed_risk_based_order_amount_is_not_positive_finite",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
                details={"order_notional_usdt": order_notional_usdt, "live_price": live_price},
                ),
                timing=timing,
            )

        entry_client_order_id = self._client_order_id(prefix="l2e", symbol=state.symbol, timestamp_ms=checked_at_ms)
        order_submit_started_at_ms = utc_now_ms()
        timing["entry_order_submit_started_at_ms"] = order_submit_started_at_ms
        try:
            self._total_orders_submitted += 1
            fill = self.exchange_client.create_market_order_with_fill(
                state.symbol,
                "buy",
                amount,
                reduce_only=False,
                client_order_id=entry_client_order_id,
            )
        except Exception as exc:
            self._total_exchange_errors += 1
            order_submit_finished_at_ms = utc_now_ms()
            timing["entry_order_submit_finished_at_ms"] = order_submit_finished_at_ms
            timing["entry_order_submit_duration_ms"] = max(0, order_submit_finished_at_ms - order_submit_started_at_ms)
            return self._finish_result(
                Live2ExecutionResult(
                verdict="rejected_entry_order_failed",
                reason=f"create_market_order_with_fill_failed:{type(exc).__name__}:{exc}",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
                order_placement_status="entry_order_failed",
                entry_client_order_id=entry_client_order_id,
                ),
                timing=timing,
            )
        order_submit_finished_at_ms = utc_now_ms()
        timing["entry_order_submit_finished_at_ms"] = order_submit_finished_at_ms
        timing["entry_order_submit_duration_ms"] = max(0, order_submit_finished_at_ms - order_submit_started_at_ms)
        timing["entry_fill_timestamp_ms"] = int(fill.timestamp_ms)
        timing["entry_fill_exchange_lag_ms"] = max(0, int(fill.timestamp_ms) - order_submit_started_at_ms)

        if not _positive_finite(fill.average_price) or not _positive_finite(fill.filled_amount):
            return self._integrity_error_after_fill(
                state=state,
                reason="entry_fill_missing_positive_price_or_amount",
                fill=fill,
                pre_position_amount=pre_position_amount,
                position_delta_amount=None,
                timing=timing,
            )

        post_position_started_at_ms = utc_now_ms()
        timing["post_position_fetch_started_at_ms"] = post_position_started_at_ms
        try:
            post_position_amount = float(self.exchange_client.fetch_symbol_position_amount(state.symbol))
        except Exception as exc:
            post_position_finished_at_ms = utc_now_ms()
            timing["post_position_fetch_finished_at_ms"] = post_position_finished_at_ms
            timing["post_position_fetch_duration_ms"] = max(0, post_position_finished_at_ms - post_position_started_at_ms)
            return self._integrity_error_after_fill(
                state=state,
                reason=f"post_entry_position_fetch_failed:{type(exc).__name__}:{exc}",
                fill=fill,
                pre_position_amount=pre_position_amount,
                position_delta_amount=None,
                timing=timing,
            )
        post_position_finished_at_ms = utc_now_ms()
        timing["post_position_fetch_finished_at_ms"] = post_position_finished_at_ms
        timing["post_position_fetch_duration_ms"] = max(0, post_position_finished_at_ms - post_position_started_at_ms)
        position_delta_amount = post_position_amount - pre_position_amount
        if not isfinite(post_position_amount) or not isfinite(position_delta_amount) or position_delta_amount <= 0.0:
            return self._integrity_error_after_fill(
                state=state,
                reason="entry_fill_without_positive_exchange_position_delta",
                fill=fill,
                pre_position_amount=pre_position_amount,
                position_delta_amount=position_delta_amount if isfinite(position_delta_amount) else None,
                post_position_amount=post_position_amount if isfinite(post_position_amount) else None,
                timing=timing,
            )
        fill_delta_slippage = abs(position_delta_amount - fill.filled_amount) / max(fill.filled_amount, 1e-12)
        if fill_delta_slippage > self.config.max_position_amount_slippage_ratio:
            return self._integrity_error_after_fill(
                state=state,
                reason="entry_fill_position_amount_mismatch",
                fill=fill,
                pre_position_amount=pre_position_amount,
                position_delta_amount=position_delta_amount,
                post_position_amount=post_position_amount,
                details={"fill_delta_slippage": fill_delta_slippage},
                timing=timing,
            )

        actual_initial_risk = float(fill.average_price) - float(stop_price)
        actual_initial_risk_pct = actual_initial_risk / float(fill.average_price) if fill.average_price else 0.0
        if not isfinite(actual_initial_risk) or actual_initial_risk <= 0.0 or not isfinite(actual_initial_risk_pct):
            return self._integrity_error_after_fill(
                state=state,
                reason="invalid_actual_initial_risk_after_fill",
                fill=fill,
                pre_position_amount=pre_position_amount,
                position_delta_amount=position_delta_amount,
                post_position_amount=post_position_amount,
                details={"stop_price": stop_price, "actual_initial_risk_pct": actual_initial_risk_pct},
                timing=timing,
            )

        position_id = self._position_id(state.symbol, checked_at_ms, fill.order_id)
        stop_client_order_id = self._client_order_id(prefix="l2s", symbol=state.symbol, timestamp_ms=checked_at_ms)
        stop_submit_started_at_ms = utc_now_ms()
        timing["stop_order_submit_started_at_ms"] = stop_submit_started_at_ms
        try:
            stop_payload = self.exchange_client.create_stop_market_order(
                state.symbol,
                "sell",
                position_delta_amount,
                float(stop_price),
                client_order_id=stop_client_order_id,
            )
        except Exception as exc:
            stop_submit_finished_at_ms = utc_now_ms()
            timing["stop_order_submit_finished_at_ms"] = stop_submit_finished_at_ms
            timing["stop_order_submit_duration_ms"] = max(0, stop_submit_finished_at_ms - stop_submit_started_at_ms)
            return self._integrity_error_after_fill(
                state=state,
                reason=f"initial_stop_submit_failed:{type(exc).__name__}:{exc}",
                fill=fill,
                pre_position_amount=pre_position_amount,
                position_delta_amount=position_delta_amount,
                post_position_amount=post_position_amount,
                position_id=position_id,
                timing=timing,
            )
        stop_submit_finished_at_ms = utc_now_ms()
        timing["stop_order_submit_finished_at_ms"] = stop_submit_finished_at_ms
        timing["stop_order_submit_duration_ms"] = max(0, stop_submit_finished_at_ms - stop_submit_started_at_ms)
        stop_order_id = _extract_order_id(stop_payload) or stop_client_order_id
        stop_verify_started_at_ms = utc_now_ms()
        timing["stop_verify_started_at_ms"] = stop_verify_started_at_ms
        verified_stop = self._verify_stop_visible(state.symbol, stop_client_order_id)
        stop_verify_finished_at_ms = utc_now_ms()
        timing["stop_verify_finished_at_ms"] = stop_verify_finished_at_ms
        timing["stop_verify_duration_ms"] = max(0, stop_verify_finished_at_ms - stop_verify_started_at_ms)
        if verified_stop is None:
            return self._integrity_error_after_fill(
                state=state,
                reason="initial_stop_not_visible_after_submit",
                fill=fill,
                pre_position_amount=pre_position_amount,
                position_delta_amount=position_delta_amount,
                post_position_amount=post_position_amount,
                position_id=position_id,
                stop_order_id=stop_order_id,
                stop_client_order_id=stop_client_order_id,
                stop_price=float(stop_price),
                timing=timing,
            )
        stop_order_id = _extract_order_id(verified_stop) or stop_order_id
        entry_current_oi_started_at_ms = utc_now_ms()
        timing["entry_current_oi_fetch_started_at_ms"] = entry_current_oi_started_at_ms
        entry_current_oi = self._fetch_entry_current_open_interest(state.symbol, now_ms=entry_current_oi_started_at_ms)
        entry_current_oi_finished_at_ms = utc_now_ms()
        timing["entry_current_oi_fetch_finished_at_ms"] = entry_current_oi_finished_at_ms
        timing["entry_current_oi_fetch_duration_ms"] = max(0, entry_current_oi_finished_at_ms - entry_current_oi_started_at_ms)
        protected_position = Live2ProtectedPosition(
            position_id=position_id,
            symbol=state.symbol,
            opened_at_ms=int(fill.timestamp_ms),
            entry_order_id=fill.order_id,
            entry_client_order_id=entry_client_order_id,
            entry_fill_price=float(fill.average_price),
            amount=position_delta_amount,
            stop_price=float(stop_price),
            stop_order_id=stop_order_id,
            stop_client_order_id=stop_client_order_id,
            pre_position_amount=pre_position_amount,
            post_position_amount=post_position_amount,
            category_id=str(signal_decision.category_id or ""),
            signal_entry_price=float(signal_entry_price),
            initial_risk_pct=actual_initial_risk_pct,
            tp1_price=float(signal_decision.tp1_at_decision or 0.0),
            initial_amount=position_delta_amount,
            remaining_amount=position_delta_amount,
            entry_5m_oi_open_interest=_finite_float_or_none(signal_decision.features.get("oi_open_interest")),
            entry_5m_oi_previous_open_interest=_finite_float_or_none(signal_decision.features.get("oi_previous_open_interest")),
            entry_5m_oi_change_pct_3x5m=_finite_float_or_none(signal_decision.features.get("oi_change_pct_3x5m")),
            entry_5m_oi_latest_timestamp_ms=_int_or_none(signal_decision.features.get("oi_latest_timestamp_ms")),
            pump_start_current_oi_open_interest=_finite_float_or_none(signal_decision.features.get("pump_start_current_oi_open_interest")),
            pump_start_current_oi_timestamp_ms=_int_or_none(signal_decision.features.get("pump_start_current_oi_timestamp_ms")),
            pump_start_current_oi_last_seen_ms=_int_or_none(signal_decision.features.get("pump_start_current_oi_last_seen_ms")),
            pump_start_current_oi_source=str(signal_decision.features.get("pump_start_current_oi_source") or ""),
            pump_start_current_oi_status=str(signal_decision.features.get("pump_start_current_oi_status") or "not_seen"),
            pump_start_current_oi_reason=str(signal_decision.features.get("pump_start_current_oi_reason") or ""),
            signal_current_oi_open_interest=_finite_float_or_none(signal_decision.features.get("signal_current_oi_open_interest")),
            signal_current_oi_timestamp_ms=_int_or_none(signal_decision.features.get("signal_current_oi_timestamp_ms")),
            signal_current_oi_last_seen_ms=_int_or_none(signal_decision.features.get("signal_current_oi_last_seen_ms")),
            signal_current_oi_source=str(signal_decision.features.get("signal_current_oi_source") or ""),
            signal_current_oi_status=str(signal_decision.features.get("signal_current_oi_status") or "not_seen"),
            signal_current_oi_reason=str(signal_decision.features.get("signal_current_oi_reason") or ""),
            entry_current_oi_open_interest=_finite_float_or_none(entry_current_oi.open_interest),
            entry_current_oi_timestamp_ms=_int_or_none(entry_current_oi.timestamp_ms),
            entry_current_oi_last_seen_ms=_int_or_none(entry_current_oi.fetched_at_ms),
            entry_current_oi_source=str(entry_current_oi.source or ""),
            entry_current_oi_status=str(entry_current_oi.status or ""),
            entry_current_oi_reason=str(entry_current_oi.reason or entry_current_oi.status or ""),
            source_flow_window_ms=_int_or_none(signal_decision.features.get("selected_source_flow_window_ms")),
            source_flow_quote_per_second=_finite_float_or_none(signal_decision.features.get("selected_source_flow_quote_per_second")),
            source_flow_trades_per_second=_finite_float_or_none(signal_decision.features.get("selected_source_flow_trades_per_second")),
            source_flow_quote_ratio=_finite_float_or_none(signal_decision.features.get("selected_source_flow_quote_ratio")),
            source_flow_trade_ratio=_finite_float_or_none(signal_decision.features.get("selected_source_flow_trade_ratio")),
            selected_rolling_htf_timeframe_ms=_int_or_none(signal_decision.features.get("rolling_runner_htf_timeframe_ms")),
        )
        self._protected_positions[state.symbol] = protected_position
        self._total_positions_protected += 1
        return self._finish_result(
            Live2ExecutionResult(
            verdict="selected",
            reason="entry_fill_and_initial_stop_verified",
            checked_at_ms=checked_at_ms,
            pre_position_amount=pre_position_amount,
            post_position_amount=post_position_amount,
            position_delta_amount=position_delta_amount,
            exchange_boundary_status="ready",
            order_placement_status="entry_filled_stop_verified",
            entry_order_id=fill.order_id,
            entry_client_order_id=entry_client_order_id,
            entry_fill_price=float(fill.average_price),
            entry_filled_amount=float(fill.filled_amount),
            entry_fill_timestamp_ms=int(fill.timestamp_ms),
            stop_order_id=stop_order_id,
            stop_client_order_id=stop_client_order_id,
            stop_price=float(stop_price),
            position_id=position_id,
            details={
                "order_notional_usdt": order_notional_usdt,
                "account_balance_usdt": account_balance_usdt,
                "risk_per_trade_pct": self.config.risk_per_trade_pct,
                "max_total_open_risk_pct": self.config.max_total_open_risk_pct,
                "planned_risk_usdt": planned_risk_usdt,
                "planned_initial_risk_pct": planned_initial_risk_pct,
                "current_open_risk_usdt_before_entry": current_open_risk_usdt,
                "entry_current_open_interest": {
                    "symbol": entry_current_oi.symbol,
                    "exchange_symbol": entry_current_oi.exchange_symbol,
                    "fetched_at_ms": entry_current_oi.fetched_at_ms,
                    "timestamp_ms": entry_current_oi.timestamp_ms,
                    "open_interest": entry_current_oi.open_interest,
                    "source": entry_current_oi.source,
                    "status": entry_current_oi.status,
                    "reason": entry_current_oi.reason,
                },
                "protected_position": protected_position.as_dict(),
            },
            ),
            timing=timing,
        )


    def _fetch_entry_current_open_interest(self, symbol: str, *, now_ms: int) -> ExchangeOpenInterestSnapshot:
        if self.exchange_client is None or not isinstance(self.exchange_client, Live2CurrentOpenInterestExchange):
            return ExchangeOpenInterestSnapshot(
                symbol=symbol,
                exchange_symbol=symbol,
                fetched_at_ms=now_ms,
                timestamp_ms=None,
                open_interest=None,
                source="",
                status="disabled",
                reason="exchange_client_has_no_fetch_current_open_interest_boundary",
            )
        try:
            return self.exchange_client.fetch_current_open_interest(symbol)
        except Exception as exc:
            self._total_exchange_errors += 1
            return ExchangeOpenInterestSnapshot(
                symbol=symbol,
                exchange_symbol=symbol,
                fetched_at_ms=utc_now_ms(),
                timestamp_ms=None,
                open_interest=None,
                source="",
                status="error",
                reason=f"fetch_current_open_interest_failed:{type(exc).__name__}:{str(exc)[:240]}",
            )


    def protected_positions_snapshot(self) -> tuple[Live2ProtectedPosition, ...]:
        return tuple(self._protected_positions.values())

    def replace_protected_position(self, position: Live2ProtectedPosition) -> None:
        if position.symbol not in self._protected_positions:
            raise KeyError(f"protected position is not registered: {position.symbol}")
        self._protected_positions[position.symbol] = position

    def remove_protected_position(self, symbol: str) -> Live2ProtectedPosition | None:
        removed = self._protected_positions.pop(symbol, None)
        if removed is not None and removed.selected_rolling_htf_timeframe_ms is not None:
            self._symbol_cooldown_until_ms[symbol] = utc_now_ms() + max(0, int(removed.selected_rolling_htf_timeframe_ms))
        return removed

    def _open_risk_usdt(self) -> float:
        total = 0.0
        for position in self._protected_positions.values():
            notional = float(position.entry_fill_price) * max(0.0, float(position.remaining_amount))
            risk = notional * max(0.0, float(position.initial_risk_pct))
            if isfinite(risk):
                total += risk
        return total

    def halt_due_to_position_integrity(self, reason: str) -> None:
        self._total_integrity_errors += 1
        self._trading_halted_reason = f"position_integrity_error:{reason}"

    def mark_exchange_error(self) -> None:
        self._total_exchange_errors += 1

    def attempt_emergency_close(self, symbol: str, amount: float | None) -> str:
        return self._attempt_emergency_close(symbol, amount)

    def verify_stop_visible(self, symbol: str, client_order_id: str) -> dict[str, object] | None:
        return self._verify_stop_visible(symbol, client_order_id)

    def make_client_order_id(self, *, prefix: str, symbol: str, timestamp_ms: int) -> str:
        return self._client_order_id(prefix=prefix, symbol=symbol, timestamp_ms=timestamp_ms)

    def _fetch_pre_position(self, symbol: str, *, checked_at_ms: int) -> Live2ExecutionResult:
        assert self.exchange_client is not None
        try:
            pre_position_amount = float(self.exchange_client.fetch_symbol_position_amount(symbol))
        except Exception as exc:
            self._total_exchange_errors += 1
            return Live2ExecutionResult(
                verdict="rejected_execution_position_precheck_failed",
                reason=f"fetch_symbol_position_amount_failed:{type(exc).__name__}:{exc}",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="error",
            )
        if not isfinite(pre_position_amount):
            self._total_exchange_errors += 1
            return Live2ExecutionResult(
                verdict="rejected_execution_invalid_position_amount",
                reason="exchange_position_amount_is_not_finite",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="error",
            )
        if abs(pre_position_amount) > self.config.max_existing_position_abs_amount:
            self._total_rejected_existing_position += 1
            return Live2ExecutionResult(
                verdict="rejected_existing_exchange_position",
                reason="pre_entry_exchange_position_amount_is_not_flat",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
            )
        return Live2ExecutionResult(
            verdict="pre_position_flat",
            reason="pre_entry_exchange_position_amount_is_flat",
            checked_at_ms=checked_at_ms,
            pre_position_amount=pre_position_amount,
            exchange_boundary_status="ready",
        )

    def _verify_stop_visible(self, symbol: str, client_order_id: str) -> dict[str, object] | None:
        assert self.exchange_client is not None
        for attempt in range(1, self.config.stop_visibility_attempts + 1):
            try:
                return self.exchange_client.fetch_stop_order_by_client_order_id(symbol, client_order_id)
            except Exception:
                if attempt >= self.config.stop_visibility_attempts:
                    return None
                if self.config.stop_visibility_sleep_seconds > 0:
                    time.sleep(self.config.stop_visibility_sleep_seconds)
        return None

    def _integrity_error_after_fill(
        self,
        *,
        state: SymbolState,
        reason: str,
        fill: ExchangeOrderFill,
        pre_position_amount: float,
        position_delta_amount: float | None,
        post_position_amount: float | None = None,
        position_id: str = "",
        stop_order_id: str = "",
        stop_client_order_id: str = "",
        stop_price: float | None = None,
        details: dict[str, object] | None = None,
        timing: dict[str, object] | None = None,
    ) -> Live2ExecutionResult:
        self._total_integrity_errors += 1
        self._trading_halted_reason = f"position_integrity_error:{reason}"
        close_amount = position_delta_amount if _positive_finite(position_delta_amount) else fill.filled_amount
        emergency_close_started_at_ms = utc_now_ms()
        if timing is not None:
            timing["emergency_close_started_at_ms"] = emergency_close_started_at_ms
        emergency_close_status = self._attempt_emergency_close(state.symbol, close_amount)
        emergency_close_finished_at_ms = utc_now_ms()
        if timing is not None:
            timing["emergency_close_finished_at_ms"] = emergency_close_finished_at_ms
            timing["emergency_close_duration_ms"] = max(0, emergency_close_finished_at_ms - emergency_close_started_at_ms)
        return self._finish_result(
            Live2ExecutionResult(
                verdict="position_integrity_error",
            reason=reason,
            checked_at_ms=utc_now_ms(),
            pre_position_amount=pre_position_amount,
            post_position_amount=post_position_amount,
            position_delta_amount=position_delta_amount,
            exchange_boundary_status="halted",
            order_placement_status="entry_filled_unprotected",
            entry_order_id=fill.order_id,
            entry_fill_price=_finite_float_or_none(fill.average_price),
            entry_filled_amount=_finite_float_or_none(fill.filled_amount),
            entry_fill_timestamp_ms=int(fill.timestamp_ms),
            stop_order_id=stop_order_id,
            stop_client_order_id=stop_client_order_id,
            stop_price=stop_price,
            position_id=position_id,
            emergency_close_status=emergency_close_status,
            integrity_error=True,
            details=details or {},
            ),
            timing=timing or {},
        )

    def _finish_result(self, result: Live2ExecutionResult, *, timing: dict[str, object]) -> Live2ExecutionResult:
        finished_at_ms = utc_now_ms()
        started_value = timing.get("started_at_ms")
        try:
            started_at_ms = int(started_value) if started_value is not None else finished_at_ms
        except (TypeError, ValueError):
            started_at_ms = finished_at_ms
        timing["finished_at_ms"] = finished_at_ms
        timing["duration_ms"] = max(0, finished_at_ms - started_at_ms)
        details = dict(result.details)
        details["execution_timing"] = dict(timing)
        return Live2ExecutionResult(
            verdict=result.verdict,
            reason=result.reason,
            checked_at_ms=result.checked_at_ms,
            pre_position_amount=result.pre_position_amount,
            post_position_amount=result.post_position_amount,
            position_delta_amount=result.position_delta_amount,
            exchange_boundary_status=result.exchange_boundary_status,
            order_placement_status=result.order_placement_status,
            entry_order_id=result.entry_order_id,
            entry_client_order_id=result.entry_client_order_id,
            entry_fill_price=result.entry_fill_price,
            entry_filled_amount=result.entry_filled_amount,
            entry_fill_timestamp_ms=result.entry_fill_timestamp_ms,
            stop_order_id=result.stop_order_id,
            stop_client_order_id=result.stop_client_order_id,
            stop_price=result.stop_price,
            position_id=result.position_id,
            emergency_close_status=result.emergency_close_status,
            integrity_error=result.integrity_error,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
            duration_ms=max(0, finished_at_ms - started_at_ms),
            timing=dict(timing),
            details=details,
        )

    def _attempt_emergency_close(self, symbol: str, amount: float | None) -> str:
        if self.exchange_client is None or not _positive_finite(amount):
            return "not_attempted_invalid_amount_or_exchange"
        try:
            client_order_id = self._client_order_id(prefix="l2x", symbol=symbol, timestamp_ms=utc_now_ms())
            self.exchange_client.create_market_order_with_fill(
                symbol,
                "sell",
                float(amount),
                reduce_only=True,
                client_order_id=client_order_id,
            )
        except Exception as exc:
            self._total_exchange_errors += 1
            return f"failed:{type(exc).__name__}:{exc}"
        return "submitted_and_fill_verified"

    @staticmethod
    def _client_order_id(*, prefix: str, symbol: str, timestamp_ms: int) -> str:
        normalized_symbol = "".join(ch if ch.isalnum() else "_" for ch in symbol)
        return f"{prefix}_{normalized_symbol}_{timestamp_ms}"

    @staticmethod
    def _position_id(symbol: str, timestamp_ms: int, order_id: str) -> str:
        normalized_symbol = symbol.replace("/", "_").replace(":", "_")
        return f"{normalized_symbol}_{timestamp_ms}_{order_id}"

    def status(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "ready": self.ready,
            "preflight": self.preflight_result.as_dict(),
            "order_placement_status": "verified_entry_and_initial_stop_enabled",
            "trading_halted_reason": self._trading_halted_reason,
            "max_open_positions": self.config.max_open_positions,
            "max_open_positions_unlimited": self.config.max_open_positions == 0,
            "open_protected_positions": len(self._protected_positions),
            "protected_positions": [position.as_dict() for position in self._protected_positions.values()],
            "open_risk_usdt": self._open_risk_usdt(),
            "symbol_cooldowns": dict(self._symbol_cooldown_until_ms),
            "total_execute_calls": self._total_execute_calls,
            "total_rejected_existing_position": self._total_rejected_existing_position,
            "total_rejected_capacity": self._total_rejected_capacity,
            "total_orders_submitted": self._total_orders_submitted,
            "total_positions_protected": self._total_positions_protected,
            "total_integrity_errors": self._total_integrity_errors,
            "total_exchange_errors": self._total_exchange_errors,
        }


def _positive_finite(value: object) -> bool:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return isfinite(parsed) and parsed > 0.0


def _finite_float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extract_order_id(payload: dict[str, object]) -> str | None:
    value = payload.get("id") or payload.get("orderId")
    info = payload.get("info")
    if value is None and isinstance(info, dict):
        value = info.get("algoId") or info.get("orderId") or info.get("clientAlgoId")
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
