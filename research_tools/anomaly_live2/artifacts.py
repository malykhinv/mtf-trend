"""Artifact writer for anomaly live2."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .clock import utc_now_iso, utc_now_ms
from .contracts import Live2Event, Live2Readiness
from .state import SymbolStateStore


class Live2ArtifactWriter:
    """Append-only audit writer for v0 live2 artifacts.

    V0 writes synchronously because there is no market-data hot path yet. Later
    patches must move this behind a bounded writer queue before any real signal
    or order path is enabled.
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

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.events_path = output_dir / "live2_events.csv"
        self.status_path = output_dir / "live2_status.json"
        self.symbol_state_path = output_dir / "live2_symbol_state.csv"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._events_file = self.events_path.open("w", encoding="utf-8-sig", newline="")
        self._events_writer = csv.DictWriter(self._events_file, fieldnames=self._EVENT_FIELDS)
        self._events_writer.writeheader()
        self._events_file.flush()
        self._closed = False

    def write_event(self, event: Live2Event) -> None:
        if self._closed:
            raise RuntimeError("live2 artifact writer is closed")
        self._events_writer.writerow(
            {
                "timestamp_utc": event.timestamp_utc,
                "timestamp_ms": event.timestamp_ms,
                "component": event.component.value,
                "event_type": event.event_type,
                "severity": event.severity.value,
                "symbol": event.symbol,
                "message": event.message,
                "data_json": json.dumps(event.data, ensure_ascii=False, sort_keys=True),
            }
        )
        self._events_file.flush()

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
    ) -> None:
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
            "aggtrade_status_counts": state_store.aggtrade_counts(),
            "candle_coverage_counts": state_store.candle_coverage_counts(),
            "readiness": readiness.as_dict(),
            "execution_status": "todo_not_implemented",
            "market_data_status": market_data_status or {"status": "todo_not_implemented"},
            "signal_status": "deadline_engine_active_signal_todo",
            "decision_status": decision_status or {"status": "todo_not_implemented"},
        }
        self.status_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def write_symbol_state(self, state_store: SymbolStateStore) -> None:
        rows = [state.to_artifact_row() for state in state_store.snapshot()]
        base_fieldnames = [
            "symbol",
            "status",
            "created_ms",
            "updated_ms",
            "dirty_since_ms",
            "actionable_since_ms",
            "decision_deadline_ms",
            "last_decision_bucket_ms",
            "last_verdict",
            "last_verdict_reason",
            "last_decision_latency_ms",
            "decision_count",
            "rejected_decision_count",
            "data_not_ready_decision_count",
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
        fieldnames = [*base_fieldnames, *extra_fieldnames]
        with self.symbol_state_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def close(self) -> None:
        if self._closed:
            return
        self._events_file.flush()
        self._events_file.close()
        self._closed = True
