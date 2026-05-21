"""Artifact writer for anomaly live2."""

from __future__ import annotations

import csv
import json
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .clock import utc_now_iso, utc_now_ms
from .contracts import Live2Event, Live2Readiness
from .state import SymbolStateStore

ArtifactJobKind = Literal["event", "near_miss", "status", "diagnostics_summary", "symbol_state", "stop"]


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
            "backpressure_active": self.backpressure_active,
        }


def _dict_or_else(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance(value, dict) else fallback


class Live2ArtifactWriter:
    """Bounded asynchronous append-only audit writer for live2 artifacts.

    Signal/deadline code calls this writer synchronously, but disk IO is done on
    a single background thread. The public methods never perform blocking CSV or
    JSON writes on the caller path. If the bounded queue fills or the writer hits
    an IO error, the writer becomes not-ready; the runner must then keep new
    entries disabled rather than trade without reliable audit.
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
        "mark_basis_status",
        "mark_close_vs_decision_close_basis",
        "start_quote_ratio",
        "baseline_quote_daily_proxy",
        "start_trade_ratio",
        "start_range_pct_ratio_to_baseline",
        "start_quote_ratio_per_abs_return",
        "start_trade_ratio_per_abs_return",
        "start_taker_buy_quote_share",
        "flow_hold_status",
        "flow_hold_count",
        "initial_risk_pct_at_decision",
        "feature_initial_risk_pct_at_decision",
        "live_setup_status",
        "live_setup_reason",
        "live_setup_timeframe",
        "live_setup_entry_timeframe",
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
        "live_setup_baseline_1m_count",
        "live_setup_baseline_quote_1m",
        "live_setup_baseline_trade_count_1m",
        "live_setup_baseline_range_pct_1m",
        "live_setup_baseline_source",
        "decision_box_low",
        "decision_box_high",
        "decision_box_range",
        "signal_entry_price",
        "initial_stop_at_decision",
        "tp1_at_decision",
        "event_data_json",
    )

    def __init__(self, output_dir: Path, *, queue_max_size: int = 8192) -> None:
        if queue_max_size <= 0:
            raise ValueError("queue_max_size must be > 0")
        self.output_dir = output_dir
        self.events_path = output_dir / "live2_events.csv"
        self.near_misses_path = output_dir / "live2_near_misses.csv"
        self.status_path = output_dir / "live2_status.json"
        self.symbol_state_path = output_dir / "live2_symbol_state.csv"
        self.diagnostics_summary_path = output_dir / "live2_diagnostics_summary.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._queue: queue.Queue[_ArtifactJob] = queue.Queue(maxsize=queue_max_size)
        self._queue_max_size = queue_max_size
        self._lock = threading.Lock()
        self._closed = False
        self._ready = True
        self._enqueued_count = 0
        self._written_count = 0
        self._rejected_count = 0
        self._events_enqueued_by_type: dict[str, int] = {}
        self._error_count = 0
        self._last_error = ""
        self._events_file = self.events_path.open("w", encoding="utf-8-sig", newline="")
        self._events_writer = csv.DictWriter(self._events_file, fieldnames=self._EVENT_FIELDS)
        self._events_writer.writeheader()
        self._events_file.flush()
        self._near_misses_file = self.near_misses_path.open("w", encoding="utf-8-sig", newline="")
        self._near_misses_writer = csv.DictWriter(self._near_misses_file, fieldnames=self._NEAR_MISS_FIELDS)
        self._near_misses_writer.writeheader()
        self._near_misses_file.flush()
        self._worker = threading.Thread(
            target=self._run_worker,
            name="live2-artifact-writer",
            daemon=True,
        )
        self._worker.start()

    def write_event(self, event: Live2Event) -> None:
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
        with self._lock:
            self._events_enqueued_by_type[event.event_type] = self._events_enqueued_by_type.get(event.event_type, 0) + 1
        self._enqueue(_ArtifactJob(kind="event", payload=row))

    def write_near_miss(self, row: dict[str, Any]) -> None:
        payload = {field: row.get(field, "") for field in self._NEAR_MISS_FIELDS}
        self._enqueue(_ArtifactJob(kind="near_miss", payload=payload))

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
        rows = [
            state.to_artifact_row(now_ms=now_ms, aggtrade_stale_ms=aggtrade_stale_ms)
            for state in state_store.snapshot()
        ]
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
                backpressure_active=self._rejected_count > 0,
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
            self._events_file.flush()
            self._events_file.close()
            self._near_misses_file.flush()
            self._near_misses_file.close()
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
            with self._lock:
                self._ready = False
                self._rejected_count += 1
                self._last_error = "artifact writer queue full"
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
                self._mark_error(f"{type(exc).__name__}: {exc}")
            finally:
                self._queue.task_done()

    def _write_job(self, job: _ArtifactJob) -> None:
        if job.kind == "event":
            self._events_writer.writerow(job.payload)
            self._events_file.flush()
            return
        if job.kind == "near_miss":
            self._near_misses_writer.writerow(job.payload)
            self._near_misses_file.flush()
            return
        if job.kind == "status":
            self._atomic_write_text(
                self.status_path,
                json.dumps(job.payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
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

    def _tmp_path_for(self, path: Path) -> Path:
        return path.with_name(f"{path.name}.{threading.get_ident()}.tmp")

    def _mark_error(self, message: str) -> None:
        with self._lock:
            self._ready = False
            self._error_count += 1
            self._last_error = message
