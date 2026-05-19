"""Runner for anomaly live2 v0."""

from __future__ import annotations

import time

from .artifacts import Live2ArtifactWriter
from .clock import utc_now_iso
from .config import AnomalyLive2Config
from .contracts import Live2Component, Live2Event, Live2Readiness, Live2Severity
from .state import SymbolStateStore


class AnomalyLive2Runner:
    """Generation-0 live2 runner.

    This command is intentionally named as a real live runtime, not a shadow or
    dry-run mode. V0 only installs the process/artifact/readiness skeleton; real
    market-data, signal evaluation, and order placement are explicit TODO gates.
    """

    def __init__(self, config: AnomalyLive2Config) -> None:
        self.config = config
        self.started_at_utc = utc_now_iso()
        self.state_store = SymbolStateStore(config.symbols)
        self.readiness = Live2Readiness(
            artifact_writer_ready=True,
            decision_latency_ready=True,
            market_data_ready=False,
            exchange_boundary_ready=False,
            position_supervisor_ready=False,
            execution_ready=False,
        )
        self._shutdown_requested = False

    def run(self) -> int:
        writer = Live2ArtifactWriter(self.config.output_dir)
        try:
            self._write_startup_events(writer)
            writer.write_symbol_state(self.state_store)
            writer.write_status(
                runtime_generation=self.config.runtime_generation,
                started_at_utc=self.started_at_utc,
                readiness=self.readiness,
                state_store=self.state_store,
                status="running",
                reason="generation_0_skeleton_started",
            )
            print(
                f"live2 · старт · артефакты {self.config.output_dir} · "
                "market-data/signal/execution TODO · новые входы запрещены",
                flush=True,
            )
            while not self._shutdown_requested:
                time.sleep(self.config.heartbeat_interval_seconds)
                writer.write_event(
                    Live2Event(
                        event_type="live2_heartbeat",
                        component=Live2Component.RUNNER,
                        message="generation_0_skeleton_alive",
                        data={
                            "symbols_total": len(self.state_store),
                            "new_entries_allowed": self.readiness.new_entries_allowed,
                            "execution_status": "todo_not_implemented",
                        },
                    )
                )
                writer.write_status(
                    runtime_generation=self.config.runtime_generation,
                    started_at_utc=self.started_at_utc,
                    readiness=self.readiness,
                    state_store=self.state_store,
                    status="running",
                    reason="generation_0_skeleton_alive",
                )
        except KeyboardInterrupt:
            self.shutdown(reason="keyboard_interrupt")
            writer.write_event(
                Live2Event(
                    event_type="live2_stopping",
                    component=Live2Component.RUNNER,
                    severity=Live2Severity.WARNING,
                    message="keyboard_interrupt",
                )
            )
            writer.write_status(
                runtime_generation=self.config.runtime_generation,
                started_at_utc=self.started_at_utc,
                readiness=self.readiness,
                state_store=self.state_store,
                status="stopped",
                reason="keyboard_interrupt",
            )
            print("live2 · остановлено пользователем", flush=True)
            return 130
        finally:
            writer.close()
        return 0

    def shutdown(self, *, reason: str) -> None:
        self._shutdown_requested = True

    def _write_startup_events(self, writer: Live2ArtifactWriter) -> None:
        writer.write_event(
            Live2Event(
                event_type="live2_started",
                component=Live2Component.RUNNER,
                message="deadline-driven live2 runtime skeleton started",
                data={
                    "runtime_generation": self.config.runtime_generation,
                    "symbols_total": len(self.state_store),
                    "new_entries_allowed": self.readiness.new_entries_allowed,
                },
            )
        )
        writer.write_event(
            Live2Event(
                event_type="market_data_plane_todo",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.WARNING,
                message="WS ticker/aggTrade ingestion is not implemented in generation 0",
            )
        )
        writer.write_event(
            Live2Event(
                event_type="signal_engine_todo",
                component=Live2Component.SIGNAL,
                severity=Live2Severity.WARNING,
                message="deadline signal evaluation is not implemented in generation 0",
            )
        )
        writer.write_event(
            Live2Event(
                event_type="execution_engine_todo",
                component=Live2Component.EXECUTION,
                severity=Live2Severity.WARNING,
                message="real order placement is intentionally not implemented in generation 0",
            )
        )
