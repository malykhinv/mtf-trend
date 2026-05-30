"""Deadline-driven decision layer for anomaly live2.

This module is intentionally small and hot-path safe: it reads already-built
in-memory candle rings and never performs network or disk IO. Generation 0 does
not run the real signal strategy yet, so actionable buckets end in an explicit
`rejected_signal_engine_todo` verdict instead of pretending to be tradable.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from .clock import utc_now_ms
from .contracts import Live2Component, Live2Event, Live2Severity
from .market_data.candles import Live2Candle
from .entry_guard import Live2EntryGuardEngine, Live2EntryGuardResult
from .execution import Live2ExecutionEngine, Live2ExecutionResult
from .signal import Live2SignalDecision, Live2SignalEngine
from .state import SymbolLive2Status, SymbolState, SymbolStateStore


def _monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)


@dataclass(frozen=True, slots=True)
class Live2DeadlineEngineConfig:
    """Tunable deadline contract for generation-0 live2 decisions."""

    timeframe_ms: int = 5_000
    decision_deadline_ms: int = 750
    backlog_expire_ms: int = 5_000
    actionable_min_quote_volume: float = 2_500.0
    actionable_min_trade_count: int = 20
    actionable_min_abs_return_pct: float = 0.003
    stale_trade_ms: int = 5_000
    cycle_budget_ms: int = 1_000

    def __post_init__(self) -> None:
        if self.timeframe_ms <= 0:
            raise ValueError("timeframe_ms must be > 0")
        if self.decision_deadline_ms <= 0:
            raise ValueError("decision_deadline_ms must be > 0")
        if self.backlog_expire_ms <= 0:
            raise ValueError("backlog_expire_ms must be > 0")
        if self.actionable_min_quote_volume < 0:
            raise ValueError("actionable_min_quote_volume must be >= 0")
        if self.actionable_min_trade_count < 0:
            raise ValueError("actionable_min_trade_count must be >= 0")
        if self.actionable_min_abs_return_pct < 0:
            raise ValueError("actionable_min_abs_return_pct must be >= 0")
        if self.stale_trade_ms <= 0:
            raise ValueError("stale_trade_ms must be > 0")
        if self.cycle_budget_ms <= 0:
            raise ValueError("cycle_budget_ms must be > 0")


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
    entry_attempt_timing: dict[str, object] = field(default_factory=dict)
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
    execution_started_at_ms: int | None = None
    execution_finished_at_ms: int | None = None
    execution_duration_ms: int | None = None
    execution_timing: dict[str, object] = field(default_factory=dict)
    signal_features: dict[str, object] = field(default_factory=dict)
    signal_dependency_reasons: tuple[str, ...] = ()
    signal_reject_reasons: tuple[str, ...] = ()

    def as_event(self) -> Live2Event:
        if self.verdict == "position_integrity_error" or self.execution_integrity_error:
            severity = Live2Severity.ERROR
        elif self.verdict in {"deadline_missed", "deadline_expired_backlog"}:
            severity = Live2Severity.WARNING
        else:
            # Routine readiness/dependency decisions are high-volume audit data, not operator warnings.
            # The artifact writer preserves them in summary CSVs and only keeps raw rows within budget.
            severity = Live2Severity.INFO
        return Live2Event(
            event_type="deadline_decision",
            component=Live2Component.SIGNAL,
            severity=severity,
            symbol=self.symbol,
            message=self.verdict,
            data=_decision_event_data(self),
        )

    def as_near_miss_row(self) -> dict[str, object] | None:
        """Return a compact CSV row for post-actionable non-selected decisions."""

        if self.verdict not in {"rejected_signal_contract", "data_dependency_not_ready"}:
            return None
        if not self.signal_features:
            return None
        blockers = tuple(dict.fromkeys([*self.signal_reject_reasons, *self.signal_dependency_reasons]))
        if self.verdict == "data_dependency_not_ready":
            near_miss_stage = "data_dependency_not_ready_after_actionable"
        elif any(item == "stream_candle_is_not_upward_price_confirmation" for item in blockers):
            near_miss_stage = "actionable_without_upward_price_confirmation"
        else:
            near_miss_stage = "category_contract_rejected_after_actionable"
        return {
            "timestamp_utc": datetime.fromtimestamp(self.decision_timestamp_ms / 1000, UTC).isoformat(timespec="milliseconds"),
            "timestamp_ms": self.decision_timestamp_ms,
            "symbol": self.symbol,
            "verdict": self.verdict,
            "near_miss_stage": near_miss_stage,
            "reason": self.reason,
            "bucket_open_ms": self.bucket_open_ms,
            "bucket_close_ms": self.bucket_close_ms,
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "deadline_ms": self.deadline_ms,
            "latency_ms": self.latency_ms,
            "return_pct": self.return_pct,
            "quote_volume": self.quote_volume,
            "number_of_trades": self.number_of_trades,
            "candle_first_source": self.candle_first_source,
            "candle_last_source": self.candle_last_source,
            "candle_live_ws_trade_count": self.candle_live_ws_trade_count,
            "actionable_reason": self.signal_features.get("actionable_reason", ""),
            "signal_reject_reasons": json.dumps(self.signal_reject_reasons, ensure_ascii=False),
            "signal_dependency_reasons": json.dumps(self.signal_dependency_reasons, ensure_ascii=False),
            "unique_blocker_count": len(blockers),
            "prior_context_status": self.signal_features.get("prior_context_status", ""),
            "prior_context_reason": self.signal_features.get("prior_context_reason", ""),
            "prior_up_down_whipsaw_to_impulse_range": self.signal_features.get("prior_up_down_whipsaw_to_impulse_range", ""),
            "prior_spike_count_24h": self.signal_features.get("prior_spike_count_24h", ""),
            "prior_fast_fade_count_24h": self.signal_features.get("prior_fast_fade_count_24h", ""),
            "oi_status": self.signal_features.get("oi_status", ""),
            "oi_change_pct_3x5m": self.signal_features.get("oi_change_pct_3x5m", ""),
            "current_oi_status": self.signal_features.get("current_oi_status", ""),
            "current_oi_open_interest": self.signal_features.get("current_oi_open_interest", ""),
            "current_oi_timestamp_ms": self.signal_features.get("current_oi_timestamp_ms", ""),
            "current_oi_last_seen_ms": self.signal_features.get("current_oi_last_seen_ms", ""),
            "current_oi_source": self.signal_features.get("current_oi_source", ""),
            "mark_basis_status": self.signal_features.get("mark_basis_status", ""),
            "mark_close_vs_decision_close_basis": self.signal_features.get("mark_close_vs_decision_close_basis", ""),
            "start_quote_ratio": self.signal_features.get("start_quote_ratio", ""),
            "baseline_quote_daily_proxy": self.signal_features.get("baseline_quote_daily_proxy", ""),
            "start_trade_ratio": self.signal_features.get("start_trade_ratio", ""),
            "start_range_pct_ratio_to_baseline": self.signal_features.get("start_range_pct_ratio_to_baseline", ""),
            "start_quote_ratio_per_abs_return": self.signal_features.get("start_quote_ratio_per_abs_return", ""),
            "start_trade_ratio_per_abs_return": self.signal_features.get("start_trade_ratio_per_abs_return", ""),
            "start_taker_buy_quote_share": self.signal_features.get("start_taker_buy_quote_share", ""),
            "start_taker_buy_quote_share_delta": self.signal_features.get("start_taker_buy_quote_share_delta", ""),
            "live_confirmed_taker_buy_quote_share": self.signal_features.get("live_confirmed_taker_buy_quote_share", ""),
            "live_confirmed_taker_buy_quote_share_delta": self.signal_features.get("live_confirmed_taker_buy_quote_share_delta", ""),
            "flow_hold_status": self.signal_features.get("flow_hold_status", ""),
            "flow_hold_count": self.signal_features.get("flow_hold_count", ""),
            "initial_risk_pct_at_decision": self.signal_features.get("initial_risk_pct_at_decision", self.initial_risk_pct_at_decision),
            "feature_initial_risk_pct_at_decision": self.signal_features.get("initial_risk_pct_at_decision", ""),
            "live_setup_status": self.signal_features.get("live_setup_status", ""),
            "live_setup_reason": self.signal_features.get("live_setup_reason", ""),
            "live_setup_timeframe": self.signal_features.get("live_setup_timeframe", ""),
            "live_setup_entry_timeframe": self.signal_features.get("live_setup_entry_timeframe", ""),
            "live_setup_alignment": self.signal_features.get("live_setup_alignment", ""),
            "live_setup_calendar_aligned": self.signal_features.get("live_setup_calendar_aligned", ""),
            "live_setup_setup_open_ms": self.signal_features.get("live_setup_setup_open_ms", ""),
            "live_setup_setup_close_ms": self.signal_features.get("live_setup_setup_close_ms", ""),
            "live_setup_flow_window_ms": self.signal_features.get("live_setup_flow_window_ms", ""),
            "live_setup_flow_quote_per_second": self.signal_features.get("live_setup_flow_quote_per_second", ""),
            "live_setup_flow_trades_per_second": self.signal_features.get("live_setup_flow_trades_per_second", ""),
            "selected_source_flow_window_ms": self.signal_features.get("selected_source_flow_window_ms", ""),
            "selected_source_flow_quote_per_second": self.signal_features.get("selected_source_flow_quote_per_second", ""),
            "selected_source_flow_trades_per_second": self.signal_features.get("selected_source_flow_trades_per_second", ""),
            "selected_source_flow_quote_ratio": self.signal_features.get("selected_source_flow_quote_ratio", ""),
            "selected_source_flow_trade_ratio": self.signal_features.get("selected_source_flow_trade_ratio", ""),
            "live_setup_closed_entry_candles": self.signal_features.get("live_setup_closed_entry_candles", ""),
            "live_setup_elapsed_fraction": self.signal_features.get("live_setup_elapsed_fraction", ""),
            "live_setup_raw_quote_ratio": self.signal_features.get("live_setup_raw_quote_ratio", ""),
            "live_setup_raw_trade_ratio": self.signal_features.get("live_setup_raw_trade_ratio", ""),
            "live_setup_min_raw_quote_ratio": self.signal_features.get("live_setup_min_raw_quote_ratio", ""),
            "live_setup_min_raw_trade_ratio": self.signal_features.get("live_setup_min_raw_trade_ratio", ""),
            "live_setup_quote_ratio": self.signal_features.get("live_setup_quote_ratio", ""),
            "live_setup_trade_ratio": self.signal_features.get("live_setup_trade_ratio", ""),
            "live_setup_min_quote_ratio": self.signal_features.get("live_setup_min_quote_ratio", ""),
            "live_setup_min_trade_ratio": self.signal_features.get("live_setup_min_trade_ratio", ""),
            "live_setup_price_retention": self.signal_features.get("live_setup_price_retention", ""),
            "live_setup_min_price_retention": self.signal_features.get("live_setup_min_price_retention", ""),
            "live_setup_hold_count": self.signal_features.get("live_setup_hold_count", ""),
            "live_setup_min_hold_count": self.signal_features.get("live_setup_min_hold_count", ""),
            "live_setup_verticality_score": self.signal_features.get("live_setup_verticality_score", ""),
            "live_setup_min_verticality_score": self.signal_features.get("live_setup_min_verticality_score", ""),
            "live_setup_range": self.signal_features.get("live_setup_range", ""),
            "live_setup_range_pct": self.signal_features.get("live_setup_range_pct", ""),
            "live_setup_range_pct_ratio_to_baseline": self.signal_features.get("live_setup_range_pct_ratio_to_baseline", ""),
            "live_setup_runner_shape_first_half_quote_volume": self.signal_features.get("live_setup_runner_shape_first_half_quote_volume", ""),
            "live_setup_runner_shape_second_half_quote_volume": self.signal_features.get("live_setup_runner_shape_second_half_quote_volume", ""),
            "live_setup_runner_shape_first_half_number_of_trades": self.signal_features.get("live_setup_runner_shape_first_half_number_of_trades", ""),
            "live_setup_runner_shape_second_half_number_of_trades": self.signal_features.get("live_setup_runner_shape_second_half_number_of_trades", ""),
            "live_setup_runner_shape_first_half_range_pct": self.signal_features.get("live_setup_runner_shape_first_half_range_pct", ""),
            "live_setup_runner_shape_second_half_range_pct": self.signal_features.get("live_setup_runner_shape_second_half_range_pct", ""),
            "live_setup_runner_shape_quote_acceleration": self.signal_features.get("live_setup_runner_shape_quote_acceleration", ""),
            "live_setup_runner_shape_trade_acceleration": self.signal_features.get("live_setup_runner_shape_trade_acceleration", ""),
            "live_setup_runner_shape_range_acceleration": self.signal_features.get("live_setup_runner_shape_range_acceleration", ""),
            "live_setup_runner_shape_second_half_return_pct": self.signal_features.get("live_setup_runner_shape_second_half_return_pct", ""),
            "live_setup_runner_shape_top1_quote_share": self.signal_features.get("live_setup_runner_shape_top1_quote_share", ""),
            "live_setup_prior_up_down_whipsaw_to_impulse_range": self.signal_features.get("live_setup_prior_up_down_whipsaw_to_impulse_range", ""),
            "live_setup_flow_hold_count": self.signal_features.get("live_setup_flow_hold_count", ""),
            "live_setup_start_quote_ratio_per_abs_return": self.signal_features.get("live_setup_start_quote_ratio_per_abs_return", ""),
            "live_setup_start_trade_ratio_per_abs_return": self.signal_features.get("live_setup_start_trade_ratio_per_abs_return", ""),
            "live_setup_start_taker_buy_quote_share": self.signal_features.get("live_setup_start_taker_buy_quote_share", ""),
            "live_setup_start_taker_buy_quote_share_delta": self.signal_features.get("live_setup_start_taker_buy_quote_share_delta", ""),
            "live_setup_next_taker_buy_quote_share_mean": self.signal_features.get("live_setup_next_taker_buy_quote_share_mean", ""),
            "live_setup_next_taker_buy_quote_share_delta": self.signal_features.get("live_setup_next_taker_buy_quote_share_delta", ""),
            "live_setup_decision_ema20": self.signal_features.get("live_setup_decision_ema20", ""),
            "live_setup_stop_buffer_range_fraction": self.signal_features.get("live_setup_stop_buffer_range_fraction", ""),
            "live_setup_tp1_r": self.signal_features.get("live_setup_tp1_r", ""),
            "live_setup_baseline_1m_count": self.signal_features.get("live_setup_baseline_1m_count", ""),
            "live_setup_baseline_quote_1m": self.signal_features.get("live_setup_baseline_quote_1m", ""),
            "live_setup_baseline_trade_count_1m": self.signal_features.get("live_setup_baseline_trade_count_1m", ""),
            "live_setup_baseline_range_pct_1m": self.signal_features.get("live_setup_baseline_range_pct_1m", ""),
            "live_setup_baseline_source": self.signal_features.get("live_setup_baseline_source", ""),
            "post_htf_acceptance_contract": self.signal_features.get("post_htf_acceptance_contract", ""),
            "post_htf_acceptance_status": self.signal_features.get("post_htf_acceptance_status", ""),
            "post_htf_acceptance_reason": self.signal_features.get("post_htf_acceptance_reason", ""),
            "post_htf_acceptance_htf_alignment": self.signal_features.get("post_htf_acceptance_htf_alignment", ""),
            "post_htf_acceptance_htf_calendar_aligned": self.signal_features.get("post_htf_acceptance_htf_calendar_aligned", ""),
            "post_htf_acceptance_confirmation_candles": self.signal_features.get("post_htf_acceptance_confirmation_candles", ""),
            "post_htf_acceptance_htf_open_ms": self.signal_features.get("post_htf_acceptance_htf_open_ms", ""),
            "post_htf_acceptance_htf_close_ms": self.signal_features.get("post_htf_acceptance_htf_close_ms", ""),
            "post_htf_acceptance_htf_return_pct": self.signal_features.get("post_htf_acceptance_htf_return_pct", ""),
            "post_htf_acceptance_htf_quote_ratio": self.signal_features.get("post_htf_acceptance_htf_quote_ratio", ""),
            "post_htf_acceptance_htf_trade_ratio": self.signal_features.get("post_htf_acceptance_htf_trade_ratio", ""),
            "post_htf_acceptance_htf_flow_window_ms": self.signal_features.get("post_htf_acceptance_htf_flow_window_ms", ""),
            "post_htf_acceptance_htf_quote_per_second": self.signal_features.get("post_htf_acceptance_htf_quote_per_second", ""),
            "post_htf_acceptance_htf_trades_per_second": self.signal_features.get("post_htf_acceptance_htf_trades_per_second", ""),
            "post_htf_acceptance_ltf6_flow_window_ms": self.signal_features.get("post_htf_acceptance_ltf6_flow_window_ms", ""),
            "post_htf_acceptance_ltf6_quote_per_second": self.signal_features.get("post_htf_acceptance_ltf6_quote_per_second", ""),
            "post_htf_acceptance_ltf6_trades_per_second": self.signal_features.get("post_htf_acceptance_ltf6_trades_per_second", ""),
            "post_htf_acceptance_ltf6_return_pct": self.signal_features.get("post_htf_acceptance_ltf6_return_pct", ""),
            "post_htf_acceptance_ltf6_taker_buy_quote_share": self.signal_features.get("post_htf_acceptance_ltf6_taker_buy_quote_share", ""),
            "post_htf_acceptance_ltf6_top1_quote_share": self.signal_features.get("post_htf_acceptance_ltf6_top1_quote_share", ""),
            "post_htf_acceptance_ltf6_last3_quote_share": self.signal_features.get("post_htf_acceptance_ltf6_last3_quote_share", ""),
            "post_htf_acceptance_ltf6_low_vs_htf_close": self.signal_features.get("post_htf_acceptance_ltf6_low_vs_htf_close", ""),
            "post_htf_acceptance_structural_stop_source": self.signal_features.get("post_htf_acceptance_structural_stop_source", ""),
            "post_htf_acceptance_initial_risk_pct_at_decision": self.signal_features.get("post_htf_acceptance_initial_risk_pct_at_decision", ""),
            "post_htf_acceptance_target_r": self.signal_features.get("post_htf_acceptance_target_r", ""),
            "post_htf_acceptance_oi_divergence_status": self.signal_features.get("post_htf_acceptance_oi_divergence_status", ""),
            "post_htf_acceptance_oi_divergence_reason": self.signal_features.get("post_htf_acceptance_oi_divergence_reason", ""),
            "post_htf_acceptance_oi_divergence_rejected": self.signal_features.get("post_htf_acceptance_oi_divergence_rejected", ""),
            "post_htf_acceptance_oi_divergence_price_return_pct": self.signal_features.get("post_htf_acceptance_oi_divergence_price_return_pct", ""),
            "post_htf_acceptance_oi_divergence_oi_change_pct_3x5m": self.signal_features.get("post_htf_acceptance_oi_divergence_oi_change_pct_3x5m", ""),
            "post_htf_acceptance_artifact_mode": self.signal_features.get("post_htf_acceptance_artifact_mode", ""),
            "decision_box_low": self.signal_features.get("decision_box_low", ""),
            "decision_box_high": self.signal_features.get("decision_box_high", ""),
            "decision_box_range": self.signal_features.get("decision_box_range", ""),
            "signal_entry_price": self.signal_entry_price,
            "initial_stop_at_decision": self.initial_stop_at_decision,
            "tp1_at_decision": self.tp1_at_decision,
            "event_data_json": json.dumps(self.as_event().data, ensure_ascii=False, sort_keys=True),
        }


def _decision_event_data(record: Live2DecisionRecord) -> dict[str, object]:
    if _decision_requires_full_event_payload(record):
        payload = asdict(record)
        payload["event_payload_mode"] = "full_critical"
        return payload
    return _decision_compact_event_payload(record)


def _decision_requires_full_event_payload(record: Live2DecisionRecord) -> bool:
    if record.verdict in {"selected", "position_integrity_error"}:
        return True
    if record.execution_integrity_error:
        return True
    if record.verdict.startswith("rejected_entry_guard") or record.verdict.startswith("rejected_execution"):
        return True
    if record.verdict in {"rejected_runtime_gates_not_ready", "rejected_existing_exchange_position"}:
        return True
    if record.entry_guard_verdict or record.execution_verdict:
        return True
    if record.execution_position_id or record.execution_entry_order_id or record.execution_stop_order_id:
        return True
    return any(
        value is not None
        for value in (
            record.signal_entry_price,
            record.initial_stop_at_decision,
            record.initial_risk_pct_at_decision,
            record.tp1_at_decision,
        )
    )


def _decision_compact_event_payload(record: Live2DecisionRecord) -> dict[str, object]:
    features = record.signal_features if isinstance(record.signal_features, dict) else {}
    compact_feature_keys = (
        "actionable_reason",
        "rolling_1m_repair_reason",
        "rolling_1m_repair_before_ms",
        "rolling_1m_latest_close_ms",
        "rolling_1m_context_gap_ms",
        "rolling_1m_rest_repair_status",
        "rolling_1m_rest_repair_reason",
        "rolling_1m_rest_repair_candles_loaded",
        "rolling_1m_rest_repair_recent_contiguous_count",
        "rolling_1m_rest_repair_duration_ms",
        "rolling_runner_category_id",
        "rolling_runner_tf_set",
        "rolling_runner_htf_timeframe_ms",
        "rolling_runner_dependency_reasons",
        "rolling_runner_reject_reasons",
        "live_setup_status",
        "live_setup_reason",
        "post_htf_acceptance_status",
        "post_htf_acceptance_reason",
        "prior_context_status",
        "prior_context_reason",
        "oi_status",
        "oi_reason",
        "current_oi_status",
        "current_oi_reason",
        "mark_basis_status",
        "start_quote_ratio",
        "start_trade_ratio",
        "start_range_pct_ratio_to_baseline",
        "start_taker_buy_quote_share",
        "selected_source_flow_quote_ratio",
        "selected_source_flow_trade_ratio",
    )
    payload: dict[str, object] = {
        "event_payload_mode": "compact_routine",
        "verdict": record.verdict,
        "reason": record.reason,
        "bucket_open_ms": record.bucket_open_ms,
        "bucket_close_ms": record.bucket_close_ms,
        "decision_timestamp_ms": record.decision_timestamp_ms,
        "deadline_ms": record.deadline_ms,
        "latency_ms": record.latency_ms,
        "quote_volume": record.quote_volume,
        "number_of_trades": record.number_of_trades,
        "return_pct": record.return_pct,
        "candle_first_source": record.candle_first_source,
        "candle_last_source": record.candle_last_source,
        "candle_startup_rest_trade_count": record.candle_startup_rest_trade_count,
        "candle_live_ws_trade_count": record.candle_live_ws_trade_count,
        "signal_dependency_reasons": record.signal_dependency_reasons,
        "signal_reject_reasons": record.signal_reject_reasons,
    }
    for key in compact_feature_keys:
        value = features.get(key)
        if value not in (None, "", (), []):
            payload[key] = value
    return payload


@dataclass(slots=True)
class Live2DeadlineCycleResult:
    """Summary for one deadline engine pass."""

    checked_symbols: int = 0
    skipped_symbols: int = 0
    decisions: list[Live2DecisionRecord] = field(default_factory=list)
    selected_count: int = 0
    rejected_count: int = 0
    data_not_ready_count: int = 0
    data_dependency_not_ready_count: int = 0
    flow_freshness_reject_count: int = 0
    deadline_missed_count: int = 0
    deadline_expired_backlog_count: int = 0
    pre_live_bucket_skipped_count: int = 0
    max_latency_ms: int = 0
    cycle_status: str = "ok"
    cycle_reason: str = ""
    state_store_lock_timeout_count: int = 0
    stale_hot_path_skip_count: int = 0
    symbols_total: int = 0
    close_due_candles_closed_count: int = 0
    close_due_candles_ms: int = 0
    decision_snapshot_ms: int = 0
    deadline_engine_ms: int = 0
    cycle_budget_ms: int = 0
    cycle_elapsed_ms: int = 0
    budget_exhausted_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "checked_symbols": self.checked_symbols,
            "skipped_symbols": self.skipped_symbols,
            "decisions_total": len(self.decisions),
            "selected_count": self.selected_count,
            "rejected_count": self.rejected_count,
            "data_not_ready_count": self.data_not_ready_count,
            "data_dependency_not_ready_count": self.data_dependency_not_ready_count,
            "flow_freshness_reject_count": self.flow_freshness_reject_count,
            "deadline_missed_count": self.deadline_missed_count,
            "deadline_expired_backlog_count": self.deadline_expired_backlog_count,
            "pre_live_bucket_skipped_count": self.pre_live_bucket_skipped_count,
            "max_latency_ms": self.max_latency_ms,
            "cycle_status": self.cycle_status,
            "cycle_reason": self.cycle_reason,
            "state_store_lock_timeout_count": self.state_store_lock_timeout_count,
            "stale_hot_path_skip_count": self.stale_hot_path_skip_count,
            "symbols_total": self.symbols_total,
            "close_due_candles_closed_count": self.close_due_candles_closed_count,
            "close_due_candles_ms": self.close_due_candles_ms,
            "decision_snapshot_ms": self.decision_snapshot_ms,
            "deadline_engine_ms": self.deadline_engine_ms,
            "cycle_budget_ms": self.cycle_budget_ms,
            "cycle_elapsed_ms": self.cycle_elapsed_ms,
            "budget_exhausted_count": self.budget_exhausted_count,
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
        self._total_deadline_expired_backlog = 0
        self._total_pre_live_bucket_skipped = 0
        self._total_data_not_ready = 0
        self._total_data_dependency_not_ready = 0
        self._total_flow_freshness_reject = 0
        self._total_rejected = 0
        self._total_selected = 0

    def run_cycle(
        self,
        *,
        now_ms: int | None = None,
        candidates: tuple[SymbolState, ...] | None = None,
        symbols_total: int | None = None,
        skip_signal_evaluation_reason: str = "",
    ) -> Live2DeadlineCycleResult:
        sort_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        cycle_started_monotonic = _monotonic_ms()
        result = Live2DeadlineCycleResult(cycle_budget_ms=int(self.config.cycle_budget_ms))
        if candidates is None:
            candidates = self.state_store.decision_snapshot()
        candidates = self._fresh_first_candidates(candidates, now_ms=sort_now_ms)
        total_symbols = len(candidates) if symbols_total is None else max(0, int(symbols_total))
        result.symbols_total = total_symbols
        result.skipped_symbols = max(0, total_symbols - len(candidates))
        if skip_signal_evaluation_reason:
            result.cycle_status = "skipped"
            result.cycle_reason = skip_signal_evaluation_reason
            result.stale_hot_path_skip_count = len(candidates)
            result.cycle_elapsed_ms = max(0, _monotonic_ms() - cycle_started_monotonic)
            self._last_cycle = result
            return result
        live_watermark_ms = self.live_decision_watermark_ms()
        for index, state in enumerate(candidates):
            elapsed_ms = max(0, _monotonic_ms() - cycle_started_monotonic)
            if elapsed_ms > int(self.config.cycle_budget_ms):
                result.cycle_status = "budget_exhausted"
                result.cycle_reason = "deadline_engine_cycle_budget_exhausted_before_all_candidates"
                result.budget_exhausted_count += 1
                result.skipped_symbols += len(candidates) - index
                break
            result.checked_symbols += 1
            previous_bucket_ms = state.last_decision_bucket_ms
            decision = self._evaluate_state(state, now_ms=utc_now_ms(), live_watermark_ms=live_watermark_ms)
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
            elif decision.verdict == "data_dependency_not_ready":
                result.data_dependency_not_ready_count += 1
            elif decision.verdict == "flow_freshness_reject":
                result.flow_freshness_reject_count += 1
            elif decision.verdict == "deadline_missed":
                result.deadline_missed_count += 1
            elif decision.verdict == "deadline_expired_backlog":
                result.deadline_expired_backlog_count += 1
            else:
                result.rejected_count += 1
        result.cycle_elapsed_ms = max(0, _monotonic_ms() - cycle_started_monotonic)
        self._last_cycle = result
        self._total_decisions += len(result.decisions)
        self._total_deadline_missed += result.deadline_missed_count
        self._total_deadline_expired_backlog += result.deadline_expired_backlog_count
        self._total_pre_live_bucket_skipped += result.pre_live_bucket_skipped_count
        self._total_data_not_ready += result.data_not_ready_count
        self._total_data_dependency_not_ready += result.data_dependency_not_ready_count
        self._total_flow_freshness_reject += result.flow_freshness_reject_count
        self._total_rejected += result.rejected_count + result.flow_freshness_reject_count
        self._total_selected += result.selected_count
        return result

    def status(self) -> dict[str, object]:
        return {
            "status": "running_stream_signal_adapter",
            "reason": "deadline_engine_active_with_live2_stream_signal_adapter",
            "timeframe_ms": self.config.timeframe_ms,
            "decision_deadline_ms": self.config.decision_deadline_ms,
            "cycle_budget_ms": self.config.cycle_budget_ms,
            "actionable_min_quote_volume": self.config.actionable_min_quote_volume,
            "actionable_min_trade_count": self.config.actionable_min_trade_count,
            "actionable_min_abs_return_pct": self.config.actionable_min_abs_return_pct,
            "last_cycle": self._last_cycle.as_dict(),
            "total_decisions": self._total_decisions,
            "total_rejected": self._total_rejected,
            "total_data_not_ready": self._total_data_not_ready,
            "total_data_dependency_not_ready": self._total_data_dependency_not_ready,
            "total_flow_freshness_reject": self._total_flow_freshness_reject,
            "total_deadline_missed": self._total_deadline_missed,
            "total_deadline_expired_backlog": self._total_deadline_expired_backlog,
            "total_pre_live_bucket_skipped": self._total_pre_live_bucket_skipped,
            "live_decision_watermark_ms": self.live_decision_watermark_ms(),
            "selected_count": self._total_selected,
            "signal_engine": self.signal_engine.status(),
            "entry_guard": self.entry_guard.status(),
            "execution_engine": None if self.execution_engine is None else self.execution_engine.status(),
        }

    def _fresh_first_candidates(self, candidates: tuple[SymbolState, ...], *, now_ms: int) -> tuple[SymbolState, ...]:
        """Evaluate still-enterable buckets before old reconnect/backlog buckets."""

        def key(state: SymbolState) -> tuple[int, int, int, str]:
            ring = state.candle_book.rings.get(self.config.timeframe_ms)
            candle = None if ring is None else ring.latest_closed()
            if candle is None:
                return (3, 0, 0, state.symbol)
            latency_ms = now_ms - candle.close_time_ms
            if state.status == SymbolLive2Status.IN_POSITION:
                priority = 0
            elif latency_ms <= self.config.decision_deadline_ms:
                priority = 1
            elif latency_ms <= self.config.backlog_expire_ms:
                priority = 2
            else:
                priority = 3
            return (priority, max(0, latency_ms), -int(candle.close_time_ms), state.symbol)

        return tuple(sorted(candidates, key=key))

    def _evaluate_state(
        self,
        state: SymbolState,
        *,
        now_ms: int,
        live_watermark_ms: int | None,
    ) -> Live2DecisionRecord | None:
        candidate_started_ms = utc_now_ms()
        ring = state.candle_book.rings.get(self.config.timeframe_ms)
        if ring is None:
            state.decision_dirty_since_ms = None
            return None
        candle = ring.latest_closed()
        if candle is None:
            state.decision_dirty_since_ms = None
            return None
        if state.last_decision_bucket_ms == candle.open_time_ms:
            state.decision_dirty_since_ms = None
            return None
        if live_watermark_ms is None:
            self._apply_pre_live_bucket(
                state,
                candle=candle,
                now_ms=candidate_started_ms,
                verdict="pre_live_ws_not_ready",
                reason="live_aggtrade_ws_has_no_valid_payload_yet",
            )
            return None
        if candle.close_time_ms < int(live_watermark_ms):
            self._apply_pre_live_bucket(
                state,
                candle=candle,
                now_ms=candidate_started_ms,
                verdict="pre_live_warmup_bucket_ignored",
                reason="closed_bucket_before_live_aggtrade_ws_watermark",
            )
            return None
        return_pct = _candle_return_pct(candle)
        actionable_reason = self._actionable_reason(candle=candle, return_pct=return_pct)
        if actionable_reason is None:
            self._apply_non_actionable(state, candle=candle, now_ms=candidate_started_ms)
            return None
        deadline_ms = candle.close_time_ms + self.config.decision_deadline_ms
        pre_signal_latency_ms = candidate_started_ms - candle.close_time_ms
        signal_decision: Live2SignalDecision | None = None
        entry_guard_result: Live2EntryGuardResult | None = None
        execution_result: Live2ExecutionResult | None = None
        entry_attempt_timing: dict[str, object] = {
            "bucket_close_ms": candle.close_time_ms,
            "candidate_started_at_ms": candidate_started_ms,
            "cycle_now_ms_argument": now_ms,
            "bucket_close_to_candidate_start_ms": max(0, pre_signal_latency_ms),
            "decision_deadline_ms": deadline_ms,
        }
        verdict: str
        reason: str
        if pre_signal_latency_ms > self.config.backlog_expire_ms:
            verdict = "deadline_expired_backlog"
            reason = "closed_bucket_expired_before_hot_path_reconnect_or_backlog"
        elif candidate_started_ms > deadline_ms:
            verdict = "deadline_missed"
            reason = "closed_bucket_was_not_evaluated_before_deadline"
        elif _is_trade_stale(candle=candle, now_ms=candidate_started_ms, stale_trade_ms=self.config.stale_trade_ms):
            verdict = "flow_freshness_reject"
            reason = "latest_closed_bucket_trade_flow_did_not_hold_into_decision_deadline"
        else:
            signal_started_at_ms = utc_now_ms()
            entry_attempt_timing["signal_evaluate_started_at_ms"] = signal_started_at_ms
            signal_decision = self.signal_engine.evaluate(
                state=state,
                candle=candle,
                actionable_reason=actionable_reason,
            )
            signal_finished_at_ms = utc_now_ms()
            entry_attempt_timing["signal_evaluate_finished_at_ms"] = signal_finished_at_ms
            entry_attempt_timing["signal_evaluate_duration_ms"] = max(0, signal_finished_at_ms - signal_started_at_ms)
            entry_attempt_timing["bucket_close_to_signal_done_ms"] = max(0, signal_finished_at_ms - int(candle.close_time_ms))
            verdict = signal_decision.verdict
            reason = signal_decision.reason
            if signal_finished_at_ms > deadline_ms:
                # The signal engine may be CPU-heavy.  If it finishes after the live
                # decision deadline, the result is audit-only: do not pass it to
                # entry guard/execution as if it were a fresh live signal.
                verdict = "deadline_missed"
                reason = "signal_evaluation_finished_after_decision_deadline"
                entry_attempt_timing["deadline_missed_stage"] = "after_signal_evaluation"
                entry_attempt_timing["signal_finished_after_deadline_ms"] = max(0, signal_finished_at_ms - deadline_ms)
            elif signal_decision.verdict == "selected":
                guard_started_at_ms = utc_now_ms()
                entry_attempt_timing["entry_guard_started_at_ms"] = guard_started_at_ms
                entry_guard_result = self.entry_guard.evaluate(
                    state=state,
                    signal_decision=signal_decision,
                    signal_timestamp_ms=candle.close_time_ms,
                    now_ms=guard_started_at_ms,
                )
                guard_finished_at_ms = utc_now_ms()
                entry_attempt_timing["entry_guard_finished_at_ms"] = guard_finished_at_ms
                entry_attempt_timing["entry_guard_duration_ms"] = max(0, guard_finished_at_ms - guard_started_at_ms)
                entry_attempt_timing["entry_guard_signal_age_ms"] = entry_guard_result.signal_age_ms
                if guard_finished_at_ms > deadline_ms:
                    verdict = "deadline_missed"
                    reason = "entry_guard_finished_after_decision_deadline"
                    entry_attempt_timing["deadline_missed_stage"] = "after_entry_guard"
                    entry_attempt_timing["entry_guard_finished_after_deadline_ms"] = max(0, guard_finished_at_ms - deadline_ms)
                elif entry_guard_result.verdict != "accepted":
                    verdict = entry_guard_result.verdict
                    reason = entry_guard_result.reason
                elif self.execution_engine is None:
                    verdict = "rejected_execution_engine_not_configured"
                    reason = "live2_execution_engine_missing"
                else:
                    runtime_gate_started_at_ms = utc_now_ms()
                    entry_attempt_timing["runtime_gate_check_started_at_ms"] = runtime_gate_started_at_ms
                    runtime_gate_allowed = self.entries_allowed()
                    runtime_gate_finished_at_ms = utc_now_ms()
                    entry_attempt_timing["runtime_gate_check_finished_at_ms"] = runtime_gate_finished_at_ms
                    entry_attempt_timing["runtime_gate_check_duration_ms"] = max(0, runtime_gate_finished_at_ms - runtime_gate_started_at_ms)
                    entry_attempt_timing["runtime_gate_allowed"] = runtime_gate_allowed
                    pre_execution_age_ms = max(0, runtime_gate_finished_at_ms - int(candle.close_time_ms))
                    entry_attempt_timing["bucket_close_to_pre_execution_ms"] = pre_execution_age_ms
                    if runtime_gate_finished_at_ms > deadline_ms:
                        verdict = "deadline_missed"
                        reason = "runtime_gate_check_finished_after_decision_deadline"
                        entry_attempt_timing["deadline_missed_stage"] = "before_execution_call"
                        entry_attempt_timing["runtime_gate_finished_after_deadline_ms"] = max(0, runtime_gate_finished_at_ms - deadline_ms)
                    elif pre_execution_age_ms > int(self.entry_guard.config.max_signal_age_ms):
                        entry_guard_result = Live2EntryGuardResult(
                            verdict="rejected_entry_guard",
                            reason="stale_signal_before_execution_call",
                            live_price=entry_guard_result.live_price,
                            signal_age_ms=pre_execution_age_ms,
                            entry_price_drift_pct=entry_guard_result.entry_price_drift_pct,
                            rr_to_tp1_at_live_price=entry_guard_result.rr_to_tp1_at_live_price,
                            features={
                                **entry_guard_result.features,
                                "signal_age_ms": pre_execution_age_ms,
                                "decision_timestamp_ms": runtime_gate_finished_at_ms,
                                "max_signal_age_ms": self.entry_guard.config.max_signal_age_ms,
                                "stale_stage": "after_signal_evaluation_before_execution",
                            },
                        )
                        verdict = entry_guard_result.verdict
                        reason = entry_guard_result.reason
                    elif not runtime_gate_allowed:
                        verdict = "rejected_runtime_gates_not_ready"
                        reason = "live2_runtime_gates_do_not_allow_new_entries"
                    else:
                        execution_started_at_ms = utc_now_ms()
                        entry_attempt_timing["execution_call_started_at_ms"] = execution_started_at_ms
                        execution_result = self.execution_engine.execute_selected(
                            state=state,
                            signal_decision=signal_decision,
                            entry_guard_result=entry_guard_result,
                        )
                        execution_finished_at_ms = utc_now_ms()
                        entry_attempt_timing["execution_call_finished_at_ms"] = execution_finished_at_ms
                        entry_attempt_timing["execution_call_duration_ms"] = max(0, execution_finished_at_ms - execution_started_at_ms)
                        verdict = execution_result.verdict
                        reason = execution_result.reason
        if "entry_guard_started_at_ms" in entry_attempt_timing:
            last_stage_finished = entry_attempt_timing.get("execution_call_finished_at_ms") or entry_attempt_timing.get("runtime_gate_check_finished_at_ms") or entry_attempt_timing.get("entry_guard_finished_at_ms")
            try:
                entry_attempt_timing["selected_signal_to_attempt_done_ms"] = max(0, int(last_stage_finished) - candidate_started_ms) if last_stage_finished is not None else None
            except (TypeError, ValueError):
                entry_attempt_timing["selected_signal_to_attempt_done_ms"] = None
        if "execution_call_finished_at_ms" in entry_attempt_timing:
            try:
                entry_attempt_timing["bucket_close_to_execution_done_ms"] = max(0, int(entry_attempt_timing["execution_call_finished_at_ms"]) - int(candle.close_time_ms))
            except (TypeError, ValueError):
                entry_attempt_timing["bucket_close_to_execution_done_ms"] = None
        decision_finished_ms = utc_now_ms()
        final_latency_ms = max(0, decision_finished_ms - int(candle.close_time_ms))
        entry_attempt_timing["final_decision_timestamp_ms"] = decision_finished_ms
        entry_attempt_timing["bucket_close_to_final_decision_ms"] = final_latency_ms
        self._apply_verdict(
            state,
            candle=candle,
            now_ms=decision_finished_ms,
            deadline_ms=deadline_ms,
            latency_ms=final_latency_ms,
            verdict=verdict,
            reason=reason,
            signal_decision=signal_decision,
            entry_guard_result=entry_guard_result,
            execution_result=execution_result,
        )

        execution_timing = {} if execution_result is None else dict(execution_result.timing)
        return Live2DecisionRecord(
            symbol=state.symbol,
            verdict=verdict,
            reason=reason,
            bucket_open_ms=candle.open_time_ms,
            bucket_close_ms=candle.close_time_ms,
            decision_timestamp_ms=decision_finished_ms,
            deadline_ms=deadline_ms,
            latency_ms=final_latency_ms,
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
            entry_attempt_timing=entry_attempt_timing,
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
            execution_started_at_ms=None if execution_result is None else execution_result.started_at_ms,
            execution_finished_at_ms=None if execution_result is None else execution_result.finished_at_ms,
            execution_duration_ms=None if execution_result is None else execution_result.duration_ms,
            execution_timing=execution_timing,
            signal_features={} if signal_decision is None else dict(signal_decision.features),
            signal_dependency_reasons=() if signal_decision is None else tuple(signal_decision.dependency_reasons),
            signal_reject_reasons=() if signal_decision is None else tuple(signal_decision.reject_reasons),
        )

    def _actionable_reason(self, *, candle: Live2Candle, return_pct: float) -> str | None:
        if candle.quote_volume <= 0 or candle.number_of_trades <= 0:
            return None
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
        state.decision_dirty_since_ms = None
        state.updated_ms = now_ms
        state.decision_deadline_ms = None
        state.actionable_since_ms = None

    def _apply_non_actionable(self, state: SymbolState, *, candle: Live2Candle, now_ms: int) -> None:
        state.status = SymbolLive2Status.WATCHING
        state.last_decision_bucket_ms = candle.open_time_ms
        state.last_verdict = "market_quiet_non_actionable"
        state.last_verdict_reason = "no_actionable_quote_trade_or_return_threshold_crossed"
        state.decision_dirty_since_ms = None
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
        if state.first_actionable_ms is None:
            state.first_actionable_ms = candle.close_time_ms
        state.last_actionable_ms = candle.close_time_ms
        state.stage0_passed_ms = candle.close_time_ms
        if signal_decision is not None:
            state.stage1_passed_ms = candle.close_time_ms
            if signal_decision.verdict != "data_dependency_not_ready":
                state.stage2_passed_ms = candle.close_time_ms
            if signal_decision.verdict == "selected":
                state.stage3_passed_ms = candle.close_time_ms
        if entry_guard_result is not None:
            state.stage4_passed_ms = candle.close_time_ms
            if entry_guard_result.verdict == "accepted":
                state.stage5_passed_ms = candle.close_time_ms
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
        state.decision_dirty_since_ms = None
        state.decision_count += 1
        if verdict in {"deadline_missed", "deadline_expired_backlog"}:
            state.deadline_missed_count += 1
        elif verdict == "data_not_ready":
            state.data_not_ready_decision_count += 1
        elif verdict == "data_dependency_not_ready":
            state.data_dependency_not_ready_decision_count += 1
        elif verdict == "flow_freshness_reject":
            state.flow_freshness_reject_decision_count += 1
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
