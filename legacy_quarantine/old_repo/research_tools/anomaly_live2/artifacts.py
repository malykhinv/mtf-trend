"""Artifact writer for anomaly live2."""

from __future__ import annotations

import csv
import json
import os
import queue
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .clock import utc_now_iso, utc_now_ms
from .contracts import Live2Event, Live2Readiness
from .state import SymbolStateStore

ArtifactJobKind = Literal["event", "near_miss", "decision_ledger", "status", "diagnostics_summary", "symbol_state", "stop"]


@dataclass(slots=True)
class _ArtifactJob:
    kind: ArtifactJobKind
    payload: Any = None


@dataclass(slots=True)
class Live2ArtifactWriterStatus:
    """Health snapshot for the bounded live2 artifact writer."""

    ready: bool
    queue_size: int
    queue_max_size: int
    enqueued_count: int
    written_count: int
    rejected_count: int
    events_enqueued_by_type: dict[str, int]
    error_count: int
    last_error: str
    critical_error_count: int
    last_critical_error: str
    noncritical_error_count: int
    last_noncritical_error: str
    dropped_count: int
    dropped_by_kind: dict[str, int]
    dropped_events_by_type: dict[str, int]
    deadline_summary_groups: int
    near_miss_summary_groups: int
    near_miss_example_rows: int
    output_file_budget: dict[str, dict[str, int | bool]]
    backpressure_active: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "queue_size": self.queue_size,
            "queue_max_size": self.queue_max_size,
            "enqueued_count": self.enqueued_count,
            "written_count": self.written_count,
            "rejected_count": self.rejected_count,
            "events_enqueued_by_type": dict(getattr(self, "events_enqueued_by_type", {})),
            "error_count": self.error_count,
            "last_error": self.last_error,
            "critical_error_count": self.critical_error_count,
            "last_critical_error": self.last_critical_error,
            "noncritical_error_count": self.noncritical_error_count,
            "last_noncritical_error": self.last_noncritical_error,
            "dropped_count": self.dropped_count,
            "dropped_by_kind": dict(self.dropped_by_kind),
            "dropped_events_by_type": dict(self.dropped_events_by_type),
            "deadline_summary_groups": self.deadline_summary_groups,
            "near_miss_summary_groups": self.near_miss_summary_groups,
            "near_miss_example_rows": self.near_miss_example_rows,
            "output_file_budget": self.output_file_budget,
            "backpressure_active": self.backpressure_active,
        }


def _dict_or_else(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance(value, dict) else fallback


def _float_or_zero(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result and abs(result) != float("inf") else 0.0


class Live2ArtifactWriter:
    """Bounded asynchronous append-only audit writer for live2 artifacts.

    Signal/deadline code calls this writer synchronously, but disk IO is done on
    a single background thread. The public methods never perform blocking CSV or
    JSON writes on the caller path. Critical append-only audit failures
    (events/near-miss) make the writer not-ready and block new entries.
    Non-critical snapshot/status failures remain visible in writer status but do
    not stop trading by themselves, because those files can be transiently locked
    by Windows tools while live_events.csv remains intact.
    """

    _EVENT_FIELDS = (
        "timestamp_utc",
        "timestamp_ms",
        "component",
        "event_type",
        "severity",
        "symbol",
        "message",
        "data_json",
    )

    _DECISION_LEDGER_FIELDS = (
        "source",
        "contract_id",
        "core_version",
        "decision_model",
        "snapshot_hash",
        "snapshot_match_key",
        "symbol",
        "tf_set",
        "signal_verdict",
        "signal_reason",
        "core_signal_verdict",
        "core_signal_reason",
        "trade_policy_id",
        "trade_policy_version",
        "trade_policy_verdict",
        "trade_policy_reason",
        "trade_policy_rule_id",
        "trade_policy_matched_rule_ids",
        "trade_policy_watchlist_rule_ids",
        "exit_policy_id",
        "exit_tp1_r",
        "exit_tp1_close_fraction",
        "exit_runner_fraction",
        "exit_trail_model",
        "portfolio_verdict",
        "portfolio_reason",
        "entry_guard_verdict",
        "entry_guard_reason",
        "execution_verdict",
        "execution_reason",
        "category_id",
        "rolling_seed_open_ms",
        "rolling_seed_close_ms",
        "confirm_start_ms",
        "confirm_end_ms",
        "decision_time_ms",
        "deadline_decision_timestamp_ms",
        "bucket_open_ms",
        "bucket_close_ms",
        "latency_ms",
        "signal_entry_price",
        "initial_stop_price",
        "tp1_price",
        "entry_guard_live_price",
        "entry_guard_signal_age_ms",
        "entry_guard_price_drift_pct",
        "execution_entry_fill_price",
        "execution_position_id",
        "execution_entry_order_id",
        "signal_reject_reasons",
        "signal_dependency_reasons",
        "confirmation_candles",
        "htf_return_pct",
        "htf_quote_ratio",
        "htf_trade_ratio",
        "dormancy_to_anomaly_quote_ratio",
        "dormancy_to_anomaly_trade_ratio",
        "htf_ltf_sustained_flow_ok",
        "htf_ltf_quote_top1_share",
        "htf_ltf_trade_top1_share",
        "htf_ltf_quote_acceleration",
        "htf_ltf_trade_acceleration",
        "htf_ltf_tail_quote_share",
        "htf_ltf_tail_trade_share",
        "htf_ltf_tail_green_share",
        "current_vs_prior_spike_median_quote",
        "current_vs_prior_spike_max_quote",
        "pregrowth_return_pct",
        "pregrowth_max_single_return_pct",
        "pregrowth_min_single_return_pct",
        "pregrowth_min_path_return_pct",
        "pregrowth_range_pct",
        "pregrowth_positive_step_share",
        "pre_seed_dump_rebound_ok",
        "ltf_confirm_return_pct",
        "ltf_quote_pace_ratio",
        "ltf_trade_pace_ratio",
        "ltf_second_half_return_pct",
        "ltf_quote_acceleration",
        "ltf_trade_acceleration",
        "ltf_taker_buy_quote_share",
        "initial_risk_pct_at_decision",
        "rolling_runner_matched_categories",
        "rolling_runner_category_priority_rank",
    )

    _NEAR_MISS_FIELDS = (
        "timestamp_utc",
        "timestamp_ms",
        "symbol",
        "verdict",
        "near_miss_stage",
        "reason",
        "bucket_open_ms",
        "bucket_close_ms",
        "decision_timestamp_ms",
        "deadline_ms",
        "latency_ms",
        "return_pct",
        "quote_volume",
        "number_of_trades",
        "candle_first_source",
        "candle_last_source",
        "candle_live_ws_trade_count",
        "actionable_reason",
        "signal_reject_reasons",
        "signal_dependency_reasons",
        "unique_blocker_count",
        "prior_context_status",
        "prior_context_reason",
        "prior_up_down_whipsaw_to_impulse_range",
        "prior_spike_count_24h",
        "prior_fast_fade_count_24h",
        "oi_status",
        "oi_change_pct_3x5m",
        "current_oi_status",
        "current_oi_open_interest",
        "current_oi_timestamp_ms",
        "current_oi_last_seen_ms",
        "current_oi_source",
        "mark_basis_status",
        "mark_close_vs_decision_close_basis",
        "start_quote_ratio",
        "baseline_quote_daily_proxy",
        "start_trade_ratio",
        "start_range_pct_ratio_to_baseline",
        "start_quote_ratio_per_abs_return",
        "start_trade_ratio_per_abs_return",
        "start_taker_buy_quote_share",
        "start_taker_buy_quote_share_delta",
        "live_confirmed_taker_buy_quote_share",
        "live_confirmed_taker_buy_quote_share_delta",
        "flow_hold_status",
        "flow_hold_count",
        "initial_risk_pct_at_decision",
        "feature_initial_risk_pct_at_decision",
        "live_setup_status",
        "live_setup_reason",
        "live_setup_timeframe",
        "live_setup_entry_timeframe",
        "live_setup_alignment",
        "live_setup_calendar_aligned",
        "live_setup_setup_open_ms",
        "live_setup_setup_close_ms",
        "live_setup_flow_window_ms",
        "live_setup_flow_quote_per_second",
        "live_setup_flow_trades_per_second",
        "selected_source_flow_window_ms",
        "selected_source_flow_quote_per_second",
        "selected_source_flow_trades_per_second",
        "selected_source_flow_quote_ratio",
        "selected_source_flow_trade_ratio",
        "live_setup_closed_entry_candles",
        "live_setup_elapsed_fraction",
        "live_setup_raw_quote_ratio",
        "live_setup_raw_trade_ratio",
        "live_setup_min_raw_quote_ratio",
        "live_setup_min_raw_trade_ratio",
        "live_setup_quote_ratio",
        "live_setup_trade_ratio",
        "live_setup_min_quote_ratio",
        "live_setup_min_trade_ratio",
        "live_setup_price_retention",
        "live_setup_min_price_retention",
        "live_setup_hold_count",
        "live_setup_min_hold_count",
        "live_setup_verticality_score",
        "live_setup_min_verticality_score",
        "live_setup_range",
        "live_setup_range_pct",
        "live_setup_range_pct_ratio_to_baseline",
        "live_setup_runner_shape_first_half_quote_volume",
        "live_setup_runner_shape_second_half_quote_volume",
        "live_setup_runner_shape_first_half_number_of_trades",
        "live_setup_runner_shape_second_half_number_of_trades",
        "live_setup_runner_shape_first_half_range_pct",
        "live_setup_runner_shape_second_half_range_pct",
        "live_setup_runner_shape_quote_acceleration",
        "live_setup_runner_shape_trade_acceleration",
        "live_setup_runner_shape_range_acceleration",
        "live_setup_runner_shape_second_half_return_pct",
        "live_setup_runner_shape_top1_quote_share",
        "live_setup_prior_up_down_whipsaw_to_impulse_range",
        "live_setup_flow_hold_count",
        "live_setup_start_quote_ratio_per_abs_return",
        "live_setup_start_trade_ratio_per_abs_return",
        "live_setup_start_taker_buy_quote_share",
        "live_setup_start_taker_buy_quote_share_delta",
        "live_setup_next_taker_buy_quote_share_mean",
        "live_setup_next_taker_buy_quote_share_delta",
        "live_setup_decision_ema20",
        "live_setup_stop_buffer_range_fraction",
        "live_setup_tp1_r",
        "live_setup_baseline_1m_count",
        "live_setup_baseline_quote_1m",
        "live_setup_baseline_trade_count_1m",
        "live_setup_baseline_range_pct_1m",
        "live_setup_baseline_source",
        "post_htf_acceptance_contract",
        "post_htf_acceptance_status",
        "post_htf_acceptance_reason",
        "post_htf_acceptance_htf_alignment",
        "post_htf_acceptance_htf_calendar_aligned",
        "post_htf_acceptance_confirmation_candles",
        "post_htf_acceptance_htf_open_ms",
        "post_htf_acceptance_htf_close_ms",
        "post_htf_acceptance_htf_return_pct",
        "post_htf_acceptance_htf_quote_ratio",
        "post_htf_acceptance_htf_trade_ratio",
        "post_htf_acceptance_htf_flow_window_ms",
        "post_htf_acceptance_htf_quote_per_second",
        "post_htf_acceptance_htf_trades_per_second",
        "post_htf_acceptance_ltf6_flow_window_ms",
        "post_htf_acceptance_ltf6_quote_per_second",
        "post_htf_acceptance_ltf6_trades_per_second",
        "post_htf_acceptance_ltf6_return_pct",
        "post_htf_acceptance_ltf6_taker_buy_quote_share",
        "post_htf_acceptance_ltf6_top1_quote_share",
        "post_htf_acceptance_ltf6_last3_quote_share",
        "post_htf_acceptance_ltf6_low_vs_htf_close",
        "post_htf_acceptance_structural_stop_source",
        "post_htf_acceptance_initial_risk_pct_at_decision",
        "post_htf_acceptance_target_r",
        "post_htf_acceptance_oi_divergence_status",
        "post_htf_acceptance_oi_divergence_reason",
        "post_htf_acceptance_oi_divergence_rejected",
        "post_htf_acceptance_oi_divergence_price_return_pct",
        "post_htf_acceptance_oi_divergence_oi_change_pct_3x5m",
        "post_htf_acceptance_artifact_mode",
        "decision_box_low",
        "decision_box_high",
        "decision_box_range",
        "signal_entry_price",
        "initial_stop_at_decision",
        "tp1_at_decision",
        "event_data_json",
    )

    _DEADLINE_SUMMARY_FIELDS = (
        "first_timestamp_utc",
        "last_timestamp_utc",
        "event_type",
        "verdict",
        "reason",
        "signal_verdict",
        "signal_reason",
        "portfolio_verdict",
        "portfolio_reason",
        "entry_guard_verdict",
        "entry_guard_reason",
        "execution_verdict",
        "execution_reason",
        "symbol_count",
        "decision_count",
        "examples_json",
    )
    _NEAR_MISS_SUMMARY_FIELDS = (
        "first_timestamp_utc",
        "last_timestamp_utc",
        "near_miss_stage",
        "verdict",
        "reason",
        "unique_blocker_count",
        "prior_context_status",
        "live_setup_status",
        "post_htf_acceptance_status",
        "symbol_count",
        "row_count",
        "examples_json",
    )

    def __init__(
        self,
        output_dir: Path,
        *,
        queue_max_size: int = 8192,
        event_max_bytes: int = 128 * 1024 * 1024,
        near_miss_max_bytes: int = 64 * 1024 * 1024,
        flush_every_rows: int = 256,
        flush_interval_seconds: float = 2.0,
    ) -> None:
        if queue_max_size <= 0:
            raise ValueError("queue_max_size must be > 0")
        if event_max_bytes <= 0:
            raise ValueError("event_max_bytes must be > 0")
        if near_miss_max_bytes <= 0:
            raise ValueError("near_miss_max_bytes must be > 0")
        if flush_every_rows <= 0:
            raise ValueError("flush_every_rows must be > 0")
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be > 0")
        self.output_dir = output_dir
        self.events_path = output_dir / "live2_events.csv"
        self.near_misses_path = output_dir / "live2_near_misses.csv"
        self.decision_ledger_path = output_dir / "live2_decision_ledger.csv"
        self.status_path = output_dir / "live2_status.json"
        self.symbol_state_path = output_dir / "live2_symbol_state.csv"
        self.diagnostics_summary_path = output_dir / "live2_diagnostics_summary.json"
        self.deadline_summary_path = output_dir / "live2_deadline_summary.csv"
        self.near_miss_summary_path = output_dir / "live2_near_miss_summary.csv"
        self.near_miss_examples_path = output_dir / "live2_near_miss_examples.csv"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._queue: queue.Queue[_ArtifactJob] = queue.Queue(maxsize=queue_max_size)
        self._queue_max_size = queue_max_size
        self._event_max_bytes = int(event_max_bytes)
        self._near_miss_max_bytes = int(near_miss_max_bytes)
        self._flush_every_rows = int(flush_every_rows)
        self._flush_interval_seconds = float(flush_interval_seconds)
        self._lock = threading.Lock()
        self._closed = False
        self._ready = True
        self._enqueued_count = 0
        self._written_count = 0
        self._rejected_count = 0
        self._events_enqueued_by_type: dict[str, int] = {}
        self._error_count = 0
        self._last_error = ""
        self._critical_error_count = 0
        self._last_critical_error = ""
        self._noncritical_error_count = 0
        self._last_noncritical_error = ""
        self._dropped_count = 0
        self._dropped_by_kind: dict[str, int] = {}
        self._dropped_events_by_type: dict[str, int] = {}
        self._deadline_summary: dict[tuple[str, ...], dict[str, Any]] = {}
        self._near_miss_summary: dict[tuple[str, ...], dict[str, Any]] = {}
        self._near_miss_examples: dict[tuple[str, ...], list[tuple[float, dict[str, Any]]]] = {}
        self._events_file = self.events_path.open("w", encoding="utf-8-sig", newline="")
        self._events_writer = csv.DictWriter(self._events_file, fieldnames=self._EVENT_FIELDS)
        self._events_writer.writeheader()
        self._events_file.flush()
        self._near_misses_file = self.near_misses_path.open("w", encoding="utf-8-sig", newline="")
        self._near_misses_writer = csv.DictWriter(self._near_misses_file, fieldnames=self._NEAR_MISS_FIELDS)
        self._near_misses_writer.writeheader()
        self._near_misses_file.flush()
        self._decision_ledger_file = self.decision_ledger_path.open("w", encoding="utf-8-sig", newline="")
        self._decision_ledger_writer = csv.DictWriter(self._decision_ledger_file, fieldnames=self._DECISION_LEDGER_FIELDS)
        self._decision_ledger_writer.writeheader()
        self._decision_ledger_file.flush()
        self._event_rows_since_flush = 0
        self._near_miss_rows_since_flush = 0
        self._decision_ledger_rows_since_flush = 0
        self._last_event_flush_monotonic = time.monotonic()
        self._last_near_miss_flush_monotonic = self._last_event_flush_monotonic
        self._last_decision_ledger_flush_monotonic = self._last_event_flush_monotonic
        self._worker = threading.Thread(
            target=self._run_worker,
            name="live2-artifact-writer",
            daemon=True,
        )
        self._worker.start()

    def write_event(self, event: Live2Event) -> None:
        self._record_event_summary(event)
        row = {
            "timestamp_utc": event.timestamp_utc,
            "timestamp_ms": event.timestamp_ms,
            "component": event.component.value,
            "event_type": event.event_type,
            "severity": event.severity.value,
            "symbol": event.symbol,
            "message": event.message,
            "data_json": json.dumps(event.data, ensure_ascii=False, sort_keys=True),
        }
        if self._should_drop_append_row(kind="event", row=row):
            return
        with self._lock:
            self._events_enqueued_by_type[event.event_type] = self._events_enqueued_by_type.get(event.event_type, 0) + 1
        self._enqueue(_ArtifactJob(kind="event", payload=row))

    def write_near_miss(self, row: dict[str, Any]) -> None:
        payload = {field: row.get(field, "") for field in self._NEAR_MISS_FIELDS}
        self._record_near_miss_summary(payload)
        if self._should_drop_append_row(kind="near_miss", row=payload):
            return
        self._enqueue(_ArtifactJob(kind="near_miss", payload=payload))

    def write_decision_ledger(self, row: dict[str, Any]) -> None:
        payload = {field: row.get(field, "") for field in self._DECISION_LEDGER_FIELDS}
        self._enqueue(_ArtifactJob(kind="decision_ledger", payload=payload))

    def write_status(
        self,
        *,
        runtime_generation: str,
        started_at_utc: str,
        readiness: Live2Readiness,
        state_store: SymbolStateStore,
        status: str,
        reason: str = "",
        market_data_status: dict[str, Any] | None = None,
        decision_status: dict[str, Any] | None = None,
        execution_status: dict[str, Any] | None = None,
        runtime_gate_status: dict[str, Any] | None = None,
        diagnostics_summary: dict[str, Any] | None = None,
    ) -> None:
        artifact_writer_status = self.status().as_dict()
        payload: dict[str, Any] = {
            "runtime_generation": runtime_generation,
            "status": status,
            "reason": reason,
            "started_at_utc": started_at_utc,
            "updated_at_utc": utc_now_iso(),
            "updated_at_ms": utc_now_ms(),
            "symbols_total": len(state_store),
            "symbol_status_counts": state_store.counts_by_status(),
            "ticker_status_counts": state_store.ticker_counts(),
            "aggtrade_status_counts": _dict_or_else(
                (market_data_status or {}).get("aggtrade_status_counts"),
                state_store.aggtrade_counts(),
            ),
            "startup_aggtrade_status_counts": _dict_or_else(
                (market_data_status or {}).get("startup_aggtrade_status_counts"),
                state_store.startup_aggtrade_counts(),
            ),
            "live_aggtrade_status_counts": _dict_or_else(
                (market_data_status or {}).get("live_aggtrade_status_counts"),
                state_store.live_aggtrade_counts(),
            ),
            "prior_context_status_counts": _dict_or_else(
                (market_data_status or {}).get("prior_context_status_counts"),
                state_store.prior_context_counts(),
            ),
            "candle_coverage_counts": _dict_or_else(
                (market_data_status or {}).get("candle_coverage_counts"),
                state_store.candle_coverage_counts(),
            ),
            "readiness": readiness.as_dict(),
            "execution_status": execution_status or {"status": "todo_not_implemented"},
            "market_data_status": market_data_status or {"status": "todo_not_implemented"},
            "signal_status": "deadline_engine_active_stream_signal_adapter",
            "decision_status": decision_status or {"status": "todo_not_implemented"},
            "artifact_writer_status": artifact_writer_status,
            "runtime_gate_status": runtime_gate_status or {"status": "not_evaluated"},
            "diagnostics_summary": diagnostics_summary or {"status": "not_available"},
        }
        self._enqueue(_ArtifactJob(kind="status", payload=payload))

    def write_diagnostics_summary(self, summary: dict[str, Any]) -> None:
        self._enqueue(_ArtifactJob(kind="diagnostics_summary", payload=summary))

    def write_symbol_state(self, state_store: SymbolStateStore, *, aggtrade_stale_ms: int | None = None) -> None:
        now_ms = utc_now_ms()
        rows_snapshot, complete, reason = state_store.artifact_rows_snapshot_bounded(
            now_ms=now_ms,
            aggtrade_stale_ms=aggtrade_stale_ms,
            lock_timeout_ms=0,
            max_lock_ms=250,
        )
        if not complete:
            # Symbol-state CSV is operator UI, not the trading contract.  Keeping the
            # previous complete file is safer than blocking the realtime state lock
            # or writing a misleading partial grid during a hot path contention spike.
            return
        rows = list(rows_snapshot)
        base_fieldnames = [
            "symbol",
            "status",
            "created_ms",
            "updated_ms",
            "dirty_since_ms",
            "decision_dirty_since_ms",
            "actionable_since_ms",
            "decision_deadline_ms",
            "last_decision_bucket_ms",
            "last_verdict",
            "last_verdict_reason",
            "last_decision_latency_ms",
            "decision_count",
            "rejected_decision_count",
            "data_not_ready_decision_count",
            "data_dependency_not_ready_decision_count",
            "deadline_missed_count",
            "ticker_market_id",
            "ticker_first_seen_ms",
            "ticker_last_seen_ms",
            "ticker_update_count",
            "ticker_last_price",
            "ticker_quote_volume_24h",
            "ticker_trade_count_24h",
            "ticker_price_change_pct_24h",
            "ticker_source",
            "ticker_status",
            "ticker_reason",
            "aggtrade_market_id",
            "aggtrade_first_seen_ms",
            "aggtrade_last_seen_ms",
            "aggtrade_last_trade_time_ms",
            "aggtrade_update_count",
            "aggtrade_last_trade_id",
            "aggtrade_last_price",
            "aggtrade_last_quantity",
            "aggtrade_quote_volume_total",
            "aggtrade_taker_buy_quote_volume_total",
            "aggtrade_trade_count_total",
            "aggtrade_source",
            "aggtrade_status",
            "aggtrade_reason",
            "candle_coverage_status",
            "candle_gap_count",
            "candle_out_of_order_count",
        ]
        extra_fieldnames = sorted({key for row in rows for key in row if key not in base_fieldnames})
        payload = {
            "fieldnames": [*base_fieldnames, *extra_fieldnames],
            "rows": rows,
        }
        self._enqueue(_ArtifactJob(kind="symbol_state", payload=payload))

    def status(self) -> Live2ArtifactWriterStatus:
        with self._lock:
            output_file_budget = self._output_file_budget_locked()
            ready = self._ready and not self._closed
            return Live2ArtifactWriterStatus(
                ready=ready,
                queue_size=self._queue.qsize(),
                queue_max_size=self._queue_max_size,
                enqueued_count=self._enqueued_count,
                written_count=self._written_count,
                rejected_count=self._rejected_count,
                events_enqueued_by_type=dict(self._events_enqueued_by_type),
                error_count=self._error_count,
                last_error=self._last_error,
                critical_error_count=self._critical_error_count,
                last_critical_error=self._last_critical_error,
                noncritical_error_count=self._noncritical_error_count,
                last_noncritical_error=self._last_noncritical_error,
                dropped_count=self._dropped_count,
                dropped_by_kind=dict(self._dropped_by_kind),
                dropped_events_by_type=dict(self._dropped_events_by_type),
                deadline_summary_groups=len(self._deadline_summary),
                near_miss_summary_groups=len(self._near_miss_summary),
                near_miss_example_rows=sum(len(rows) for rows in self._near_miss_examples.values()),
                output_file_budget=output_file_budget,
                backpressure_active=self._rejected_count > 0 or self._dropped_count > 0,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._queue.put(_ArtifactJob(kind="stop"), timeout=5.0)
        except queue.Full:
            self._mark_error("artifact writer close timed out because queue is full")
        self._worker.join(timeout=10.0)
        try:
            self._write_audit_summary_snapshots()
            self._events_file.flush()
            self._near_misses_file.flush()
            self._decision_ledger_file.flush()
            self._events_file.close()
            self._near_misses_file.close()
            self._decision_ledger_file.close()
        except OSError as exc:
            self._mark_error(f"artifact writer close failed: {type(exc).__name__}: {exc}")

    def _enqueue(self, job: _ArtifactJob) -> None:
        with self._lock:
            if self._closed:
                self._ready = False
                self._rejected_count += 1
                self._last_error = "artifact writer is closed"
                return
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            if self._is_critical_job(job):
                with self._lock:
                    self._ready = False
                    self._rejected_count += 1
                    self._error_count += 1
                    self._critical_error_count += 1
                    self._last_error = "artifact writer critical queue full"
                    self._last_critical_error = "artifact writer critical queue full"
                return
            with self._lock:
                self._dropped_count += 1
                self._dropped_by_kind[job.kind] = self._dropped_by_kind.get(job.kind, 0) + 1
                self._noncritical_error_count += 1
                self._last_noncritical_error = f"artifact writer noncritical queue full:{job.kind}"
            return
        with self._lock:
            self._enqueued_count += 1

    def _run_worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job.kind == "stop":
                    return
                self._write_job(job)
                with self._lock:
                    self._written_count += 1
            except Exception as exc:  # noqa: BLE001 - artifact writer must surface any IO/serialization failure.
                message = f"{type(exc).__name__}: {exc}"
                if self._is_critical_job(job):
                    self._mark_error(message)
                else:
                    self._mark_noncritical_error(kind=job.kind, message=message)
            finally:
                self._queue.task_done()

    @staticmethod
    def _is_critical_job(job: _ArtifactJob) -> bool:
        return job.kind in {"event", "near_miss", "decision_ledger", "stop"}

    def _mark_noncritical_error(self, *, kind: str, message: str) -> None:
        with self._lock:
            self._error_count += 1
            self._noncritical_error_count += 1
            self._last_error = f"noncritical:{kind}:{message}"[:1000]
            self._last_noncritical_error = f"{kind}:{message}"[:1000]

    def _write_job(self, job: _ArtifactJob) -> None:
        if job.kind == "event":
            self._events_writer.writerow(job.payload)
            self._event_rows_since_flush += 1
            self._maybe_flush_append_file(
                kind="event",
                force=self._is_critical_event_row(job.payload),
            )
            return
        if job.kind == "near_miss":
            self._near_misses_writer.writerow(job.payload)
            self._near_miss_rows_since_flush += 1
            self._maybe_flush_append_file(kind="near_miss")
            return
        if job.kind == "decision_ledger":
            self._decision_ledger_writer.writerow(job.payload)
            self._decision_ledger_rows_since_flush += 1
            self._maybe_flush_append_file(kind="decision_ledger")
            return
        if job.kind == "status":
            self._atomic_write_text(
                self.status_path,
                json.dumps(job.payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            self._write_audit_summary_snapshots()
            return
        if job.kind == "diagnostics_summary":
            self._atomic_write_text(
                self.diagnostics_summary_path,
                json.dumps(job.payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            return
        if job.kind == "symbol_state":
            payload = job.payload
            tmp_path = self._tmp_path_for(self.symbol_state_path)
            with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
                writer = csv.DictWriter(file_obj, fieldnames=payload["fieldnames"])
                writer.writeheader()
                writer.writerows(payload["rows"])
                file_obj.flush()
                os.fsync(file_obj.fileno())
            tmp_path.replace(self.symbol_state_path)
            return
        raise RuntimeError(f"unknown artifact writer job kind: {job.kind}")

    def _atomic_write_text(self, path: Path, text: str, *, encoding: str) -> None:
        tmp_path = self._tmp_path_for(path)
        with tmp_path.open("w", encoding=encoding, newline="") as file_obj:
            file_obj.write(text)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        tmp_path.replace(path)


    def _record_event_summary(self, event: Live2Event) -> None:
        if event.event_type != "deadline_decision":
            return
        data = event.data if isinstance(event.data, dict) else {}
        key = (
            event.event_type,
            str(data.get("verdict") or event.message or ""),
            str(data.get("reason") or ""),
            str(data.get("signal_verdict") or ""),
            str(data.get("signal_reason") or ""),
            str(data.get("portfolio_verdict") or ""),
            str(data.get("portfolio_reason") or ""),
            str(data.get("entry_guard_verdict") or ""),
            str(data.get("entry_guard_reason") or ""),
            str(data.get("execution_verdict") or ""),
            str(data.get("execution_reason") or ""),
        )
        with self._lock:
            item = self._deadline_summary.get(key)
            if item is None:
                item = {
                    "first_timestamp_utc": event.timestamp_utc,
                    "last_timestamp_utc": event.timestamp_utc,
                    "event_type": event.event_type,
                    "verdict": key[1],
                    "reason": key[2],
                    "signal_verdict": key[3],
                    "signal_reason": key[4],
                    "portfolio_verdict": key[5],
                    "portfolio_reason": key[6],
                    "entry_guard_verdict": key[7],
                    "entry_guard_reason": key[8],
                    "execution_verdict": key[9],
                    "execution_reason": key[10],
                    "decision_count": 0,
                    "symbols": set(),
                    "examples": [],
                }
                self._deadline_summary[key] = item
            item["last_timestamp_utc"] = event.timestamp_utc
            item["decision_count"] = int(item.get("decision_count") or 0) + 1
            if event.symbol:
                item["symbols"].add(event.symbol)
                examples = item["examples"]
                if isinstance(examples, list) and len(examples) < 8 and event.symbol not in examples:
                    examples.append(event.symbol)

    def _record_near_miss_summary(self, row: dict[str, Any]) -> None:
        key = (
            str(row.get("near_miss_stage") or ""),
            str(row.get("verdict") or ""),
            str(row.get("reason") or ""),
            str(row.get("unique_blocker_count") or ""),
            str(row.get("prior_context_status") or ""),
            str(row.get("live_setup_status") or ""),
            str(row.get("post_htf_acceptance_status") or ""),
        )
        timestamp_utc = str(row.get("timestamp_utc") or "")
        symbol = str(row.get("symbol") or "")
        with self._lock:
            item = self._near_miss_summary.get(key)
            if item is None:
                item = {
                    "first_timestamp_utc": timestamp_utc,
                    "last_timestamp_utc": timestamp_utc,
                    "near_miss_stage": key[0],
                    "verdict": key[1],
                    "reason": key[2],
                    "unique_blocker_count": key[3],
                    "prior_context_status": key[4],
                    "live_setup_status": key[5],
                    "post_htf_acceptance_status": key[6],
                    "row_count": 0,
                    "symbols": set(),
                    "examples": [],
                }
                self._near_miss_summary[key] = item
            item["last_timestamp_utc"] = timestamp_utc
            item["row_count"] = int(item.get("row_count") or 0) + 1
            if symbol:
                item["symbols"].add(symbol)
                examples = item["examples"]
                if isinstance(examples, list) and len(examples) < 8 and symbol not in examples:
                    examples.append(symbol)
            priority = self._near_miss_priority(row)
            examples_by_group = self._near_miss_examples.setdefault(key, [])
            examples_by_group.append((priority, dict(row)))
            examples_by_group.sort(key=lambda pair: pair[0], reverse=True)
            del examples_by_group[5:]

    def _write_audit_summary_snapshots(self) -> None:
        deadline_rows: list[dict[str, Any]] = []
        near_miss_rows: list[dict[str, Any]] = []
        near_miss_examples: list[dict[str, Any]] = []
        with self._lock:
            for item in self._deadline_summary.values():
                symbols = item.get("symbols") if isinstance(item.get("symbols"), set) else set()
                deadline_rows.append(
                    {
                        "first_timestamp_utc": item.get("first_timestamp_utc", ""),
                        "last_timestamp_utc": item.get("last_timestamp_utc", ""),
                        "event_type": item.get("event_type", ""),
                        "verdict": item.get("verdict", ""),
                        "reason": item.get("reason", ""),
                        "signal_verdict": item.get("signal_verdict", ""),
                        "signal_reason": item.get("signal_reason", ""),
                        "portfolio_verdict": item.get("portfolio_verdict", ""),
                        "portfolio_reason": item.get("portfolio_reason", ""),
                        "entry_guard_verdict": item.get("entry_guard_verdict", ""),
                        "entry_guard_reason": item.get("entry_guard_reason", ""),
                        "execution_verdict": item.get("execution_verdict", ""),
                        "execution_reason": item.get("execution_reason", ""),
                        "symbol_count": len(symbols),
                        "decision_count": item.get("decision_count", 0),
                        "examples_json": json.dumps(item.get("examples", []), ensure_ascii=False),
                    }
                )
            for item in self._near_miss_summary.values():
                symbols = item.get("symbols") if isinstance(item.get("symbols"), set) else set()
                near_miss_rows.append(
                    {
                        "first_timestamp_utc": item.get("first_timestamp_utc", ""),
                        "last_timestamp_utc": item.get("last_timestamp_utc", ""),
                        "near_miss_stage": item.get("near_miss_stage", ""),
                        "verdict": item.get("verdict", ""),
                        "reason": item.get("reason", ""),
                        "unique_blocker_count": item.get("unique_blocker_count", ""),
                        "prior_context_status": item.get("prior_context_status", ""),
                        "live_setup_status": item.get("live_setup_status", ""),
                        "post_htf_acceptance_status": item.get("post_htf_acceptance_status", ""),
                        "symbol_count": len(symbols),
                        "row_count": item.get("row_count", 0),
                        "examples_json": json.dumps(item.get("examples", []), ensure_ascii=False),
                    }
                )
            for rows in self._near_miss_examples.values():
                near_miss_examples.extend(dict(row) for _, row in rows)
        deadline_rows.sort(key=lambda row: int(row.get("decision_count") or 0), reverse=True)
        near_miss_rows.sort(key=lambda row: int(row.get("row_count") or 0), reverse=True)
        near_miss_examples.sort(key=self._near_miss_priority, reverse=True)
        self._atomic_write_csv(self.deadline_summary_path, self._DEADLINE_SUMMARY_FIELDS, deadline_rows)
        self._atomic_write_csv(self.near_miss_summary_path, self._NEAR_MISS_SUMMARY_FIELDS, near_miss_rows)
        self._atomic_write_csv(self.near_miss_examples_path, self._NEAR_MISS_FIELDS, near_miss_examples)

    def _atomic_write_csv(self, path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
        tmp_path = self._tmp_path_for(path)
        with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        tmp_path.replace(path)

    def _near_miss_priority(self, row: dict[str, Any]) -> float:
        return_pct = _float_or_zero(row.get("return_pct"))
        quote_volume = _float_or_zero(row.get("quote_volume"))
        number_of_trades = _float_or_zero(row.get("number_of_trades"))
        return abs(return_pct) * max(1.0, quote_volume) + number_of_trades

    def _event_payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("data_json")
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _should_drop_append_row(self, *, kind: str, row: dict[str, Any]) -> bool:
        if kind == "event":
            event_type = str(row.get("event_type") or "")
            if self._append_file_tell(self._events_file) < self._event_max_bytes:
                return False
            if self._is_critical_event_row(row):
                return False
            self._record_drop(kind="event", event_type=event_type or "unknown")
            return True
        if kind == "near_miss":
            if self._append_file_tell(self._near_misses_file) < self._near_miss_max_bytes:
                return False
            self._record_drop(kind="near_miss", event_type="")
            return True
        return False

    def _record_drop(self, *, kind: str, event_type: str) -> None:
        with self._lock:
            self._dropped_count += 1
            self._dropped_by_kind[kind] = self._dropped_by_kind.get(kind, 0) + 1
            if event_type:
                self._dropped_events_by_type[event_type] = self._dropped_events_by_type.get(event_type, 0) + 1

    def _append_file_tell(self, file_obj: Any) -> int:
        try:
            position = int(file_obj.tell())
        except (OSError, ValueError):
            return 0
        return max(0, position)

    def _output_file_budget_locked(self) -> dict[str, dict[str, int | bool]]:
        event_bytes = self._append_file_tell(self._events_file)
        near_miss_bytes = self._append_file_tell(self._near_misses_file)
        decision_ledger_bytes = self._append_file_tell(self._decision_ledger_file)
        return {
            "live2_events.csv": {
                "bytes": event_bytes,
                "max_bytes": self._event_max_bytes,
                "budget_reached": event_bytes >= self._event_max_bytes,
            },
            "live2_near_misses.csv": {
                "bytes": near_miss_bytes,
                "max_bytes": self._near_miss_max_bytes,
                "budget_reached": near_miss_bytes >= self._near_miss_max_bytes,
            },
            "live2_decision_ledger.csv": {
                "bytes": decision_ledger_bytes,
                "max_bytes": 0,
                "budget_reached": False,
            },
        }

    def _is_critical_event_row(self, row: dict[str, Any]) -> bool:
        severity = str(row.get("severity") or "").lower()
        event_type = str(row.get("event_type") or "").lower()
        if severity in {"error", "critical"}:
            return True
        critical_tokens = (
            "selected",
            "execution",
            "entry",
            "order",
            "fill",
            "stop",
            "position",
            "integrity",
            "halt",
            "failed",
            "error",
            "telegram",
            "preflight",
        )
        if any(token in event_type for token in critical_tokens):
            return True
        if event_type != "deadline_decision":
            return False
        data = self._event_payload_from_row(row)
        verdict = str(data.get("verdict") or "")
        entry_guard_verdict = str(data.get("entry_guard_verdict") or "")
        portfolio_verdict = str(data.get("portfolio_verdict") or "")
        execution_verdict = str(data.get("execution_verdict") or "")
        has_signal_prices = any(
            data.get(field) not in (None, "")
            for field in ("signal_entry_price", "initial_stop_at_decision", "tp1_at_decision")
        )
        if verdict == "selected" or verdict == "position_integrity_error":
            return True
        if verdict.startswith("rejected_entry_guard") or verdict.startswith("rejected_execution"):
            return True
        if verdict in {"rejected_runtime_gates_not_ready", "rejected_existing_exchange_position"}:
            return True
        if entry_guard_verdict or portfolio_verdict or execution_verdict or has_signal_prices:
            return True
        return False

    def _maybe_flush_append_file(self, *, kind: str, force: bool = False) -> None:
        now = time.monotonic()
        if kind == "event":
            if (
                not force
                and self._event_rows_since_flush < self._flush_every_rows
                and now - self._last_event_flush_monotonic < self._flush_interval_seconds
            ):
                return
            self._events_file.flush()
            self._event_rows_since_flush = 0
            self._last_event_flush_monotonic = now
            return
        if kind == "decision_ledger":
            if (
                not force
                and self._decision_ledger_rows_since_flush < self._flush_every_rows
                and now - self._last_decision_ledger_flush_monotonic < self._flush_interval_seconds
            ):
                return
            self._decision_ledger_file.flush()
            self._decision_ledger_rows_since_flush = 0
            self._last_decision_ledger_flush_monotonic = now
            return
        if (
            not force
            and self._near_miss_rows_since_flush < self._flush_every_rows
            and now - self._last_near_miss_flush_monotonic < self._flush_interval_seconds
        ):
            return
        self._near_misses_file.flush()
        self._near_miss_rows_since_flush = 0
        self._last_near_miss_flush_monotonic = now

    def _tmp_path_for(self, path: Path) -> Path:
        return path.with_name(f"{path.name}.{threading.get_ident()}.tmp")

    def _mark_error(self, message: str) -> None:
        with self._lock:
            self._ready = False
            self._error_count += 1
            self._critical_error_count += 1
            self._last_error = message[:1000]
            self._last_critical_error = message[:1000]
