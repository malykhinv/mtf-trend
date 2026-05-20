"""Deadline-driven decision layer for anomaly live2.

This module is intentionally small and hot-path safe: it reads already-built
in-memory candle rings and never performs network or disk IO. Generation 0 does
not run the real signal strategy yet, so actionable buckets end in an explicit
`rejected_signal_engine_todo` verdict instead of pretending to be tradable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .clock import utc_now_ms
from .contracts import Live2Component, Live2Event, Live2Severity
from .market_data.candles import Live2Candle
from .entry_guard import Live2EntryGuardEngine, Live2EntryGuardResult
from .execution import Live2ExecutionEngine, Live2ExecutionResult
from .signal import Live2SignalDecision, Live2SignalEngine
from .state import SymbolLive2Status, SymbolState, SymbolStateStore


@dataclass(frozen=True, slots=True)
class Live2DeadlineEngineConfig:
    """Tunable deadline contract for generation-0 live2 decisions."""

    timeframe_ms: int = 5_000
    decision_deadline_ms: int = 750
    actionable_min_quote_volume: float = 2_500.0
    actionable_min_trade_count: int = 20
    actionable_min_abs_return_pct: float = 0.003
    stale_trade_ms: int = 5_000

    def __post_init__(self) -> None:
        if self.timeframe_ms <= 0:
            raise ValueError("timeframe_ms must be > 0")
        if self.decision_deadline_ms <= 0:
            raise ValueError("decision_deadline_ms must be > 0")
        if self.actionable_min_quote_volume < 0:
            raise ValueError("actionable_min_quote_volume must be >= 0")
        if self.actionable_min_trade_count < 0:
            raise ValueError("actionable_min_trade_count must be >= 0")
        if self.actionable_min_abs_return_pct < 0:
            raise ValueError("actionable_min_abs_return_pct must be >= 0")
        if self.stale_trade_ms <= 0:
            raise ValueError("stale_trade_ms must be > 0")


@dataclass(frozen=True, slots=True)
class Live2DecisionRecord:
    """Single deadline verdict emitted by the deadline engine."""

    symbol: str
    verdict: str
    reason: str
    bucket_open_ms: int
    bucket_close_ms: int
    decision_timestamp_ms: int
    deadline_ms: int
    latency_ms: int
    quote_volume: float
    number_of_trades: int
    return_pct: float
    candle_first_source: str = ""
    candle_last_source: str = ""
    candle_startup_rest_trade_count: int = 0
    candle_live_ws_trade_count: int = 0
    category_id: str = ""
    category_rank: int | None = None
    signal_entry_price: float | None = None
    initial_stop_at_decision: float | None = None
    initial_risk_pct_at_decision: float | None = None
    tp1_at_decision: float | None = None
    entry_guard_verdict: str = ""
    entry_guard_reason: str = ""
    entry_guard_live_price: float | None = None
    entry_guard_signal_age_ms: int | None = None
    entry_guard_price_drift_pct: float | None = None
    entry_guard_rr_to_tp1: float | None = None
    execution_verdict: str = ""
    execution_reason: str = ""
    execution_pre_position_amount: float | None = None
    execution_order_placement_status: str = ""
    execution_position_id: str = ""
    execution_entry_order_id: str = ""
    execution_entry_fill_price: float | None = None
    execution_entry_filled_amount: float | None = None
    execution_stop_order_id: str = ""
    execution_stop_price: float | None = None
    execution_integrity_error: bool = False
    execution_emergency_close_status: str = ""
    signal_features: dict[str, object] = field(default_factory=dict)

    def as_event(self) -> Live2Event:
        if self.verdict == "position_integrity_error" or self.execution_integrity_error:
            severity = Live2Severity.ERROR
        elif self.verdict in {"deadline_missed", "data_not_ready"}:
            severity = Live2Severity.WARNING
        else:
            severity = Live2Severity.INFO
        return Live2Event(
            event_type="deadline_decision",
            component=Live2Component.SIGNAL,
            severity=severity,
            symbol=self.symbol,
            message=self.verdict,
            data={
                "verdict": self.verdict,
                "reason": self.reason,
                "bucket_open_ms": self.bucket_open_ms,
                "bucket_close_ms": self.bucket_close_ms,
                "decision_timestamp_ms": self.decision_timestamp_ms,
                "deadline_ms": self.deadline_ms,
                "latency_ms": self.latency_ms,
                "quote_volume": self.quote_volume,
                "number_of_trades": self.number_of_trades,
                "return_pct": self.return_pct,
                "candle_first_source": self.candle_first_source,
                "candle_last_source": self.candle_last_source,
                "candle_startup_rest_trade_count": self.candle_startup_rest_trade_count,
                "candle_live_ws_trade_count": self.candle_live_ws_trade_count,
                "category_id": self.category_id,
                "category_rank": self.category_rank,
                "signal_entry_price": self.signal_entry_price,
                "initial_stop_at_decision": self.initial_stop_at_decision,
                "initial_risk_pct_at_decision": self.initial_risk_pct_at_decision,
                "tp1_at_decision": self.tp1_at_decision,
                "entry_guard_verdict": self.entry_guard_verdict,
                "entry_guard_reason": self.entry_guard_reason,
                "entry_guard_live_price": self.entry_guard_live_price,
                "entry_guard_signal_age_ms": self.entry_guard_signal_age_ms,
                "entry_guard_price_drift_pct": self.entry_guard_price_drift_pct,
                "entry_guard_rr_to_tp1": self.entry_guard_rr_to_tp1,
                "execution_verdict": self.execution_verdict,
                "execution_reason": self.execution_reason,
                "execution_pre_position_amount": self.execution_pre_position_amount,
                "execution_order_placement_status": self.execution_order_placement_status,
                "execution_position_id": self.execution_position_id,
                "execution_entry_order_id": self.execution_entry_order_id,
                "execution_entry_fill_price": self.execution_entry_fill_price,
                "execution_entry_filled_amount": self.execution_entry_filled_amount,
                "execution_stop_order_id": self.execution_stop_order_id,
                "execution_stop_price": self.execution_stop_price,
                "execution_integrity_error": self.execution_integrity_error,
                "execution_emergency_close_status": self.execution_emergency_close_status,
                "signal_features": self.signal_features,
            },
        )


@dataclass(slots=True)
class Live2DeadlineCycleResult:
    """Summary for one deadline engine pass."""

    checked_symbols: int = 0
    skipped_symbols: int = 0
    decisions: list[Live2DecisionRecord] = field(default_factory=list)
    selected_count: int = 0
    rejected_count: int = 0
    data_not_ready_count: int = 0
    deadline_missed_count: int = 0
    pre_live_bucket_skipped_count: int = 0
    max_latency_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "checked_symbols": self.checked_symbols,
            "skipped_symbols": self.skipped_symbols,
            "decisions_total": len(self.decisions),
            "selected_count": self.selected_count,
            "rejected_count": self.rejected_count,
            "data_not_ready_count": self.data_not_ready_count,
            "deadline_missed_count": self.deadline_missed_count,
            "pre_live_bucket_skipped_count": self.pre_live_bucket_skipped_count,
            "max_latency_ms": self.max_latency_ms,
        }


class Live2DeadlineEngine:
    """Turns closed candle buckets into bounded-time verdicts.

    The engine has no candidate queue. Each selected symbol exposes its latest
    closed candle from the in-memory ring. A bucket is processed at most once,
    and every actionable processed bucket receives a verdict immediately.
    """

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        config: Live2DeadlineEngineConfig,
        signal_engine: Live2SignalEngine | None = None,
        entry_guard: Live2EntryGuardEngine | None = None,
        execution_engine: Live2ExecutionEngine | None = None,
        entries_allowed: Callable[[], bool] | None = None,
        live_decision_watermark_ms: Callable[[], int | None] | None = None,
    ) -> None:
        self.state_store = state_store
        self.config = config
        self.signal_engine = signal_engine or Live2SignalEngine()
        self.entry_guard = entry_guard or Live2EntryGuardEngine()
        self.execution_engine = execution_engine
        self.entries_allowed = entries_allowed or (lambda: False)
        self.live_decision_watermark_ms = live_decision_watermark_ms or (lambda: None)
        self._last_cycle: Live2DeadlineCycleResult = Live2DeadlineCycleResult()
        self._total_decisions = 0
        self._total_deadline_missed = 0
        self._total_pre_live_bucket_skipped = 0
        self._total_data_not_ready = 0
        self._total_rejected = 0
        self._total_selected = 0

    def run_cycle(self, *, now_ms: int | None = None) -> Live2DeadlineCycleResult:
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        result = Live2DeadlineCycleResult()
        for state in self.state_store.snapshot():
            if not state.universe_selected:
                result.skipped_symbols += 1
                continue
            result.checked_symbols += 1
            previous_bucket_ms = state.last_decision_bucket_ms
            decision = self._evaluate_state(state, now_ms=effective_now_ms)
            if decision is None:
                if (
                    state.last_decision_bucket_ms is not None
                    and state.last_decision_bucket_ms != previous_bucket_ms
                    and state.last_verdict in {
                        "pre_live_ws_not_ready",
                        "pre_live_warmup_bucket_ignored",
                    }
                ):
                    result.pre_live_bucket_skipped_count += 1
                continue
            result.decisions.append(decision)
            result.max_latency_ms = max(result.max_latency_ms, decision.latency_ms)
            if decision.verdict == "selected":
                result.selected_count += 1
            elif decision.verdict == "data_not_ready":
                result.data_not_ready_count += 1
            elif decision.verdict == "deadline_missed":
                result.deadline_missed_count += 1
            else:
                result.rejected_count += 1
        self._last_cycle = result
        self._total_decisions += len(result.decisions)
        self._total_deadline_missed += result.deadline_missed_count
        self._total_pre_live_bucket_skipped += result.pre_live_bucket_skipped_count
        self._total_data_not_ready += result.data_not_ready_count
        self._total_rejected += result.rejected_count
        self._total_selected += result.selected_count
        return result

    def status(self) -> dict[str, object]:
        return {
            "status": "running_stream_signal_adapter",
            "reason": "deadline_engine_active_with_live2_stream_signal_adapter",
            "timeframe_ms": self.config.timeframe_ms,
            "decision_deadline_ms": self.config.decision_deadline_ms,
            "actionable_min_quote_volume": self.config.actionable_min_quote_volume,
            "actionable_min_trade_count": self.config.actionable_min_trade_count,
            "actionable_min_abs_return_pct": self.config.actionable_min_abs_return_pct,
            "last_cycle": self._last_cycle.as_dict(),
            "total_decisions": self._total_decisions,
            "total_rejected": self._total_rejected,
            "total_data_not_ready": self._total_data_not_ready,
            "total_deadline_missed": self._total_deadline_missed,
            "total_pre_live_bucket_skipped": self._total_pre_live_bucket_skipped,
            "live_decision_watermark_ms": self.live_decision_watermark_ms(),
            "selected_count": self._total_selected,
            "signal_engine": self.signal_engine.status(),
            "entry_guard": self.entry_guard.status(),
            "execution_engine": None if self.execution_engine is None else self.execution_engine.status(),
        }

    def _evaluate_state(self, state: SymbolState, *, now_ms: int) -> Live2DecisionRecord | None:
        ring = state.candle_book.rings.get(self.config.timeframe_ms)
        if ring is None:
            return None
        candle = ring.latest_closed()
        if candle is None:
            return None
        if state.last_decision_bucket_ms == candle.open_time_ms:
            return None
        live_watermark_ms = self.live_decision_watermark_ms()
        if live_watermark_ms is None:
            self._apply_pre_live_bucket(
                state,
                candle=candle,
                now_ms=now_ms,
                verdict="pre_live_ws_not_ready",
                reason="live_aggtrade_ws_has_no_valid_payload_yet",
            )
            return None
        if candle.close_time_ms < int(live_watermark_ms):
            self._apply_pre_live_bucket(
                state,
                candle=candle,
                now_ms=now_ms,
                verdict="pre_live_warmup_bucket_ignored",
                reason="closed_bucket_before_live_aggtrade_ws_watermark",
            )
            return None
        return_pct = _candle_return_pct(candle)
        actionable_reason = self._actionable_reason(candle=candle, return_pct=return_pct)
        if actionable_reason is None:
            self._apply_non_actionable(state, candle=candle, now_ms=now_ms)
            return None
        deadline_ms = candle.close_time_ms + self.config.decision_deadline_ms
        latency_ms = now_ms - candle.close_time_ms
        signal_decision: Live2SignalDecision | None = None
        entry_guard_result: Live2EntryGuardResult | None = None
        execution_result: Live2ExecutionResult | None = None
        if now_ms > deadline_ms:
            verdict = "deadline_missed"
            reason = "closed_bucket_was_not_evaluated_before_deadline"
        elif _is_trade_stale(candle=candle, now_ms=now_ms, stale_trade_ms=self.config.stale_trade_ms):
            verdict = "data_not_ready"
            reason = "latest_closed_bucket_trade_flow_is_stale"
        else:
            # Cumulative gap/out-of-order counters are diagnostics, not a permanent
            # hard rejection. Dormant symbols naturally have no-trade gaps between
            # real aggTrade buckets; banning all future buckets after the first gap
            # would make live2 blind to exactly the wake-up pattern it is meant to see.
            signal_decision = self.signal_engine.evaluate(
                state=state,
                candle=candle,
                actionable_reason=actionable_reason,
            )
            verdict = signal_decision.verdict
            reason = signal_decision.reason
            if signal_decision.verdict == "selected":
                entry_guard_result = self.entry_guard.evaluate(
                    state=state,
                    signal_decision=signal_decision,
                    signal_timestamp_ms=candle.close_time_ms,
                    now_ms=now_ms,
                )
                if entry_guard_result.verdict != "accepted":
                    verdict = entry_guard_result.verdict
                    reason = entry_guard_result.reason
                elif self.execution_engine is None:
                    verdict = "rejected_execution_engine_not_configured"
                    reason = "live2_execution_engine_missing"
                elif not self.entries_allowed():
                    verdict = "rejected_runtime_gates_not_ready"
                    reason = "live2_runtime_gates_do_not_allow_new_entries"
                else:
                    execution_result = self.execution_engine.execute_selected(
                        state=state,
                        signal_decision=signal_decision,
                        entry_guard_result=entry_guard_result,
                    )
                    verdict = execution_result.verdict
                    reason = execution_result.reason
        self._apply_verdict(
            state,
            candle=candle,
            now_ms=now_ms,
            deadline_ms=deadline_ms,
            latency_ms=latency_ms,
            verdict=verdict,
            reason=reason,
            signal_decision=signal_decision,
            entry_guard_result=entry_guard_result,
            execution_result=execution_result,
        )
        return Live2DecisionRecord(
            symbol=state.symbol,
            verdict=verdict,
            reason=reason,
            bucket_open_ms=candle.open_time_ms,
            bucket_close_ms=candle.close_time_ms,
            decision_timestamp_ms=now_ms,
            deadline_ms=deadline_ms,
            latency_ms=latency_ms,
            quote_volume=candle.quote_volume,
            number_of_trades=candle.number_of_trades,
            return_pct=return_pct,
            candle_first_source=candle.first_source,
            candle_last_source=candle.last_source,
            candle_startup_rest_trade_count=candle.startup_rest_trade_count,
            candle_live_ws_trade_count=candle.live_ws_trade_count,
            category_id="" if signal_decision is None else signal_decision.category_id,
            category_rank=None if signal_decision is None else signal_decision.category_rank,
            signal_entry_price=None if signal_decision is None else signal_decision.signal_entry_price,
            initial_stop_at_decision=None if signal_decision is None else signal_decision.initial_stop_at_decision,
            initial_risk_pct_at_decision=None if signal_decision is None else signal_decision.initial_risk_pct_at_decision,
            tp1_at_decision=None if signal_decision is None else signal_decision.tp1_at_decision,
            entry_guard_verdict="" if entry_guard_result is None else entry_guard_result.verdict,
            entry_guard_reason="" if entry_guard_result is None else entry_guard_result.reason,
            entry_guard_live_price=None if entry_guard_result is None else entry_guard_result.live_price,
            entry_guard_signal_age_ms=None if entry_guard_result is None else entry_guard_result.signal_age_ms,
            entry_guard_price_drift_pct=None if entry_guard_result is None else entry_guard_result.entry_price_drift_pct,
            entry_guard_rr_to_tp1=None if entry_guard_result is None else entry_guard_result.rr_to_tp1_at_live_price,
            execution_verdict="" if execution_result is None else execution_result.verdict,
            execution_reason="" if execution_result is None else execution_result.reason,
            execution_pre_position_amount=None if execution_result is None else execution_result.pre_position_amount,
            execution_order_placement_status="" if execution_result is None else execution_result.order_placement_status,
            execution_position_id="" if execution_result is None else execution_result.position_id,
            execution_entry_order_id="" if execution_result is None else execution_result.entry_order_id,
            execution_entry_fill_price=None if execution_result is None else execution_result.entry_fill_price,
            execution_entry_filled_amount=None if execution_result is None else execution_result.entry_filled_amount,
            execution_stop_order_id="" if execution_result is None else execution_result.stop_order_id,
            execution_stop_price=None if execution_result is None else execution_result.stop_price,
            execution_integrity_error=False if execution_result is None else execution_result.integrity_error,
            execution_emergency_close_status="" if execution_result is None else execution_result.emergency_close_status,
            signal_features={} if signal_decision is None else dict(signal_decision.features),
        )

    def _actionable_reason(self, *, candle: Live2Candle, return_pct: float) -> str | None:
        reasons: list[str] = []
        if candle.quote_volume >= self.config.actionable_min_quote_volume:
            reasons.append("quote_volume_threshold_crossed")
        if candle.number_of_trades >= self.config.actionable_min_trade_count:
            reasons.append("trade_count_threshold_crossed")
        if abs(return_pct) >= self.config.actionable_min_abs_return_pct:
            reasons.append("abs_return_threshold_crossed")
        if not reasons:
            return None
        return "+".join(reasons)

    def _apply_pre_live_bucket(
        self,
        state: SymbolState,
        *,
        candle: Live2Candle,
        now_ms: int,
        verdict: str,
        reason: str,
    ) -> None:
        state.status = SymbolLive2Status.WATCHING
        state.last_decision_bucket_ms = candle.open_time_ms
        state.last_verdict = verdict
        state.last_verdict_reason = reason
        state.last_decision_latency_ms = max(0, now_ms - candle.close_time_ms)
        state.updated_ms = now_ms
        state.decision_deadline_ms = None
        state.actionable_since_ms = None

    def _apply_non_actionable(self, state: SymbolState, *, candle: Live2Candle, now_ms: int) -> None:
        state.status = SymbolLive2Status.WATCHING
        state.last_decision_bucket_ms = candle.open_time_ms
        state.last_verdict = "rejected_not_actionable"
        state.updated_ms = now_ms
        state.decision_deadline_ms = None
        state.actionable_since_ms = None

    def _apply_verdict(
        self,
        state: SymbolState,
        *,
        candle: Live2Candle,
        now_ms: int,
        deadline_ms: int,
        latency_ms: int,
        verdict: str,
        reason: str,
        signal_decision: Live2SignalDecision | None = None,
        entry_guard_result: Live2EntryGuardResult | None = None,
        execution_result: Live2ExecutionResult | None = None,
    ) -> None:
        if execution_result is not None and execution_result.verdict == "selected":
            state.status = SymbolLive2Status.IN_POSITION
        else:
            state.status = SymbolLive2Status.WATCHING
        state.actionable_since_ms = candle.close_time_ms
        state.decision_deadline_ms = deadline_ms
        state.last_decision_bucket_ms = candle.open_time_ms
        state.last_verdict = verdict
        state.last_verdict_reason = reason
        state.last_decision_latency_ms = latency_ms
        if signal_decision is not None:
            state.last_signal_category_id = signal_decision.category_id
            state.last_signal_category_rank = signal_decision.category_rank
            state.last_signal_entry_price = signal_decision.signal_entry_price
            state.last_signal_initial_stop = signal_decision.initial_stop_at_decision
            state.last_signal_initial_risk_pct = signal_decision.initial_risk_pct_at_decision
            state.last_signal_tp1 = signal_decision.tp1_at_decision
        else:
            state.last_signal_category_id = ""
            state.last_signal_category_rank = None
            state.last_signal_entry_price = None
            state.last_signal_initial_stop = None
            state.last_signal_initial_risk_pct = None
            state.last_signal_tp1 = None
        if entry_guard_result is not None:
            state.last_entry_guard_verdict = entry_guard_result.verdict
            state.last_entry_guard_reason = entry_guard_result.reason
            state.last_entry_guard_live_price = entry_guard_result.live_price
            state.last_entry_guard_price_drift_pct = entry_guard_result.entry_price_drift_pct
            state.last_entry_guard_rr_to_tp1 = entry_guard_result.rr_to_tp1_at_live_price
        else:
            state.last_entry_guard_verdict = ""
            state.last_entry_guard_reason = ""
            state.last_entry_guard_live_price = None
            state.last_entry_guard_price_drift_pct = None
            state.last_entry_guard_rr_to_tp1 = None
        if execution_result is not None:
            state.last_execution_verdict = execution_result.verdict
            state.last_execution_reason = execution_result.reason
            state.last_execution_pre_position_amount = execution_result.pre_position_amount
            state.last_execution_order_placement_status = execution_result.order_placement_status
            state.last_execution_position_id = execution_result.position_id
            state.last_execution_entry_order_id = execution_result.entry_order_id
            state.last_execution_entry_fill_price = execution_result.entry_fill_price
            state.last_execution_entry_filled_amount = execution_result.entry_filled_amount
            state.last_execution_stop_order_id = execution_result.stop_order_id
            state.last_execution_stop_price = execution_result.stop_price
            state.last_execution_integrity_error = execution_result.integrity_error
        else:
            state.last_execution_verdict = ""
            state.last_execution_reason = ""
            state.last_execution_pre_position_amount = None
            state.last_execution_order_placement_status = ""
            state.last_execution_position_id = ""
            state.last_execution_entry_order_id = ""
            state.last_execution_entry_fill_price = None
            state.last_execution_entry_filled_amount = None
            state.last_execution_stop_order_id = ""
            state.last_execution_stop_price = None
            state.last_execution_integrity_error = False
        state.decision_count += 1
        if verdict == "deadline_missed":
            state.deadline_missed_count += 1
        elif verdict == "data_not_ready":
            state.data_not_ready_decision_count += 1
        elif verdict.startswith("rejected"):
            state.rejected_decision_count += 1
        elif verdict == "selected":
            state.selected_decision_count += 1
        state.updated_ms = now_ms


def _candle_return_pct(candle: Live2Candle) -> float:
    if candle.open <= 0:
        return 0.0
    return (candle.close / candle.open) - 1.0


def _is_trade_stale(*, candle: Live2Candle, now_ms: int, stale_trade_ms: int) -> bool:
    return now_ms - candle.last_trade_time_ms > stale_trade_ms
