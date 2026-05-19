"""Runner for anomaly live2 v0."""

from __future__ import annotations

import time
from typing import Any

from .artifacts import Live2ArtifactWriter
from .clock import utc_now_iso
from .config import AnomalyLive2Config
from .contracts import Live2Component, Live2Event, Live2Readiness, Live2Severity
from .deadline import Live2DeadlineCycleResult, Live2DeadlineEngine, Live2DeadlineEngineConfig
from .entry_guard import Live2EntryGuardConfig, Live2EntryGuardEngine
from .execution import Live2ExecutionConfig, Live2ExecutionEngine, Live2ExecutionExchange
from .market_data.aggtrade_ws import Live2AggTradeWsSource
from .market_data.ticker_ws import Live2TickerWsSource
from .market_data.universe import Live2UniverseSelection, Live2UniverseSelector
from .position_supervisor import Live2PositionSupervisor, Live2PositionSupervisorConfig
from .state import SymbolStateStore


class AnomalyLive2Runner:
    """Generation-0 live2 runner.

    This command is intentionally named as a real live runtime, not a shadow or
    dry-run mode. V0 installs the process/artifact/readiness skeleton and starts
    real ticker + auto-selected aggTrade WS ingestion plus a stream-only signal
    adapter. P321 enables real entry execution only after runtime gates, actual
    fill recovery, and strict initial-stop visibility verification.
    """

    def __init__(self, config: AnomalyLive2Config, *, exchange_client: Live2ExecutionExchange | None = None) -> None:
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
        self.ticker_source = Live2TickerWsSource(
            state_store=self.state_store,
            symbols=config.symbols,
            stale_ms=config.ticker_stale_ms,
            startup_wait_seconds=config.ticker_startup_wait_seconds,
        )
        self.aggtrade_source: Live2AggTradeWsSource | None = None
        self.execution_engine = Live2ExecutionEngine(
            exchange_client=exchange_client,
            config=Live2ExecutionConfig(
                order_notional_usdt=config.execution_order_notional_usdt,
                max_open_positions=config.execution_max_open_positions,
                max_position_amount_slippage_ratio=config.execution_max_position_amount_slippage_ratio,
                stop_visibility_attempts=config.execution_stop_visibility_attempts,
                stop_visibility_sleep_seconds=config.execution_stop_visibility_sleep_seconds,
            ),
        )
        self.position_supervisor = Live2PositionSupervisor(
            exchange_client=exchange_client,
            execution_engine=self.execution_engine,
            config=Live2PositionSupervisorConfig(
                monitor_interval_ms=config.position_supervisor_monitor_interval_ms,
                tp1_close_fraction=config.position_supervisor_tp1_close_fraction,
                breakeven_stop_offset_pct=config.position_supervisor_breakeven_stop_offset_pct,
                flat_position_abs_epsilon=config.position_supervisor_flat_position_abs_epsilon,
            ),
        )
        self.universe_selection: Live2UniverseSelection | None = None
        self.deadline_engine = Live2DeadlineEngine(
            state_store=self.state_store,
            config=Live2DeadlineEngineConfig(
                timeframe_ms=config.decision_timeframe_ms,
                decision_deadline_ms=config.decision_deadline_ms,
                actionable_min_quote_volume=config.actionable_min_quote_volume,
                actionable_min_trade_count=config.actionable_min_trade_count,
                actionable_min_abs_return_pct=config.actionable_min_abs_return_pct,
                stale_trade_ms=config.aggtrade_stale_ms,
            ),
            entry_guard=Live2EntryGuardEngine(
                config=Live2EntryGuardConfig(
                    max_signal_age_ms=config.entry_guard_max_signal_age_ms,
                    max_entry_price_drift_pct=config.entry_guard_max_price_drift_pct,
                    min_rr_to_tp1=config.entry_guard_min_rr_to_tp1,
                )
            ),
            execution_engine=self.execution_engine,
            entries_allowed=lambda: self.readiness.new_entries_allowed,
        )
        self._shutdown_requested = False
        self._decision_latency_degraded_windows = 0
        self._decision_latency_clean_windows = 0
        self._market_data_degraded_windows = 0
        self._market_data_clean_windows = 0
        self._market_data_ready_gate = False
        self._decision_loop_overrun_count = 0
        self._decision_loop_max_elapsed_ms = 0
        self._last_runtime_gate_snapshot: dict[str, object] | None = None
        self._last_market_data_coverage_snapshot: dict[str, object] | None = None
        self._last_runtime_gate_reason = "startup"
        self._last_decision_cycle_elapsed_ms = 0

    def run(self) -> int:
        writer = Live2ArtifactWriter(
            self.config.output_dir,
            queue_max_size=self.config.artifact_writer_queue_max_size,
        )
        try:
            self._write_startup_events(writer)
            execution_preflight = self.execution_engine.preflight()
            self.readiness.exchange_boundary_ready = execution_preflight.ready
            self.readiness.execution_ready = self.execution_engine.ready
            self.readiness.position_supervisor_ready = self.position_supervisor.ready
            writer.write_event(
                Live2Event(
                    event_type="execution_preflight",
                    component=Live2Component.EXECUTION,
                    severity=Live2Severity.INFO if execution_preflight.ready else Live2Severity.ERROR,
                    message=execution_preflight.reason,
                    data=execution_preflight.as_dict(),
                )
            )
            self.ticker_source.start()
            ticker_ready = self.ticker_source.wait_until_ready()
            self.universe_selection = self._select_universe()
            self.state_store.apply_universe_selection(
                selected_rank_by_symbol=self.universe_selection.rank_by_symbol(),
                selected_at_ms=self.universe_selection.selected_at_ms,
                mode=self.universe_selection.mode,
            )
            writer.write_event(
                Live2Event(
                    event_type="universe_selected",
                    component=Live2Component.MARKET_DATA,
                    severity=Live2Severity.INFO if self.universe_selection.selected_symbols else Live2Severity.ERROR,
                    message="live2 startup universe selected from ticker state",
                    data=self.universe_selection.as_dict(),
                )
            )
            aggtrade_ready = False
            if self.universe_selection.selected_symbols:
                self.aggtrade_source = Live2AggTradeWsSource(
                    state_store=self.state_store,
                    symbols=self.universe_selection.selected_symbols,
                    stale_ms=self.config.aggtrade_stale_ms,
                    startup_wait_seconds=self.config.aggtrade_startup_wait_seconds,
                    max_streams_per_connection=self.config.aggtrade_max_streams_per_connection,
                )
                self._write_aggtrade_starting_event(writer)
                self.aggtrade_source.start()
                aggtrade_ready = self.aggtrade_source.wait_until_ready()
            else:
                writer.write_event(
                    Live2Event(
                        event_type="aggtrade_ws_not_started",
                        component=Live2Component.MARKET_DATA,
                        severity=Live2Severity.ERROR,
                        message="aggTrade WS not started because startup universe is empty",
                        data=self._universe_status(),
                    )
                )
            self._refresh_artifact_writer_readiness(writer)
            market_data_status = self._market_data_status()
            self._refresh_runtime_gates(
                writer=writer,
                market_data_status=market_data_status,
                deadline_result=Live2DeadlineCycleResult(),
                decision_cycle_elapsed_ms=0,
            )
            runtime_gate_status = self._runtime_gate_status()
            writer.write_event(
                Live2Event(
                    event_type="ticker_ws_startup_status",
                    component=Live2Component.MARKET_DATA,
                    severity=Live2Severity.INFO if ticker_ready else Live2Severity.WARNING,
                    message="ticker WS startup completed" if ticker_ready else "ticker WS startup wait ended without ready payload",
                    data=market_data_status,
                )
            )
            writer.write_event(
                Live2Event(
                    event_type="aggtrade_ws_startup_status",
                    component=Live2Component.MARKET_DATA,
                    severity=Live2Severity.INFO if aggtrade_ready else Live2Severity.WARNING,
                    message="aggTrade WS startup completed" if aggtrade_ready else "aggTrade WS startup wait ended without full readiness",
                    data=market_data_status,
                )
            )
            writer.write_symbol_state(self.state_store)
            writer.write_status(
                runtime_generation=self.config.runtime_generation,
                started_at_utc=self.started_at_utc,
                readiness=self.readiness,
                state_store=self.state_store,
                status="running",
                reason="generation_0_ticker_universe_and_aggtrade_ws_started",
                market_data_status=market_data_status,
                decision_status=self.deadline_engine.status(),
                execution_status=self._execution_status(),
                runtime_gate_status=runtime_gate_status,
            )
            print(
                f"live2 · старт · артефакты {self.config.output_dir} · "
                f"universe {len(self.universe_selection.selected_symbols)} · "
                "ticker+aggTrade WS включены · stream signal adapter включен · verified entry+stop execution включен",
                flush=True,
            )
            last_heartbeat_at = 0.0
            while not self._shutdown_requested:
                cycle_started = time.perf_counter()
                supervisor_result = self.position_supervisor.run_cycle(self.state_store)
                for action in supervisor_result.actions:
                    writer.write_event(action.as_event())
                deadline_result = self.deadline_engine.run_cycle()
                self._last_decision_cycle_elapsed_ms = int((time.perf_counter() - cycle_started) * 1000)
                self._decision_loop_max_elapsed_ms = max(
                    self._decision_loop_max_elapsed_ms,
                    self._last_decision_cycle_elapsed_ms,
                )
                for decision in deadline_result.decisions:
                    writer.write_event(decision.as_event())

                market_data_status = self._market_data_status()
                self._refresh_runtime_gates(
                    writer=writer,
                    market_data_status=market_data_status,
                    deadline_result=deadline_result,
                    decision_cycle_elapsed_ms=self._last_decision_cycle_elapsed_ms,
                )

                now_monotonic = time.monotonic()
                if now_monotonic - last_heartbeat_at >= self.config.heartbeat_interval_seconds:
                    last_heartbeat_at = now_monotonic
                    runtime_gate_status = self._runtime_gate_status()
                    writer.write_event(
                        Live2Event(
                            event_type="live2_heartbeat",
                            component=Live2Component.RUNNER,
                            message="generation_0_fast_decision_loop_alive",
                            data={
                                "symbols_total": len(self.state_store),
                                "ticker_status_counts": self.state_store.ticker_counts(),
                                "aggtrade_status_counts": self.state_store.aggtrade_counts(),
                                "candle_coverage_counts": self.state_store.candle_coverage_counts(),
                                "market_data_status": market_data_status,
                                "decision_status": self.deadline_engine.status(),
                                "deadline_cycle": deadline_result.as_dict(),
                                "decision_cycle_elapsed_ms": self._last_decision_cycle_elapsed_ms,
                                "runtime_gate_status": runtime_gate_status,
                                "new_entries_allowed": self.readiness.new_entries_allowed,
                                "execution_status": self._execution_status(),
                                "position_supervisor_cycle": supervisor_result.as_dict(),
                                "artifact_writer_status": writer.status().as_dict(),
                            },
                        )
                    )
                    writer.write_symbol_state(self.state_store)
                    writer.write_status(
                        runtime_generation=self.config.runtime_generation,
                        started_at_utc=self.started_at_utc,
                        readiness=self.readiness,
                        state_store=self.state_store,
                        status="running",
                        reason="generation_0_fast_decision_loop_alive",
                        market_data_status=market_data_status,
                        decision_status=self.deadline_engine.status(),
                        execution_status=self._execution_status(),
                        runtime_gate_status=runtime_gate_status,
                    )

                elapsed = time.perf_counter() - cycle_started
                sleep_seconds = self.config.decision_loop_interval_seconds - elapsed
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            self.shutdown(reason="keyboard_interrupt")
            self._refresh_artifact_writer_readiness(writer)
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
                market_data_status=self._market_data_status(),
                decision_status=self.deadline_engine.status(),
                execution_status=self._execution_status(),
                runtime_gate_status=self._runtime_gate_status(),
            )
            print("live2 · остановлено пользователем", flush=True)
            return 130
        finally:
            if self.aggtrade_source is not None:
                self.aggtrade_source.close()
            self.ticker_source.close()
            writer.close()
        return 0

    def shutdown(self, *, reason: str) -> None:
        self._shutdown_requested = True


    def _refresh_artifact_writer_readiness(self, writer: Live2ArtifactWriter) -> None:
        status = writer.status()
        self.readiness.artifact_writer_ready = status.ready

    def _refresh_runtime_gates(
        self,
        *,
        writer: Live2ArtifactWriter,
        market_data_status: dict[str, object],
        deadline_result: Live2DeadlineCycleResult,
        decision_cycle_elapsed_ms: int,
    ) -> None:
        self._refresh_artifact_writer_readiness(writer)
        self.readiness.market_data_ready = self._market_data_gate_ready(market_data_status)
        self._write_market_data_coverage_update_if_changed(writer, market_data_status)
        self.readiness.exchange_boundary_ready = self.execution_engine.preflight_result.ready
        self.readiness.execution_ready = self.execution_engine.ready
        self.readiness.position_supervisor_ready = self.position_supervisor.ready
        self.readiness.decision_latency_ready = self._decision_latency_gate_ready(
            deadline_result=deadline_result,
            decision_cycle_elapsed_ms=decision_cycle_elapsed_ms,
        )
        runtime_gate_status = self._runtime_gate_status()
        snapshot = {
            "market_data_ready": self.readiness.market_data_ready,
            "decision_latency_ready": self.readiness.decision_latency_ready,
            "artifact_writer_ready": self.readiness.artifact_writer_ready,
            "exchange_boundary_ready": self.readiness.exchange_boundary_ready,
            "position_supervisor_ready": self.readiness.position_supervisor_ready,
            "execution_ready": self.readiness.execution_ready,
            "new_entries_allowed": self.readiness.new_entries_allowed,
            "reason": runtime_gate_status["reason"],
            "market_data_source_ready": bool(market_data_status.get("stream_coverage_ready")),
            "market_data_status": str(market_data_status.get("status", "")),
        }
        if snapshot != self._last_runtime_gate_snapshot:
            self._last_runtime_gate_snapshot = dict(snapshot)
            writer.write_event(
                Live2Event(
                    event_type="runtime_gate_update",
                    component=Live2Component.RUNNER,
                    severity=Live2Severity.INFO if self.readiness.new_entries_allowed else Live2Severity.WARNING,
                    message=str(runtime_gate_status["reason"]),
                    data=runtime_gate_status,
                )
            )

    def _market_data_gate_ready(self, market_data_status: dict[str, object]) -> bool:
        source_ready = bool(market_data_status.get("stream_coverage_ready"))
        if not source_ready:
            self._market_data_degraded_windows += 1
            self._market_data_clean_windows = 0
            self._market_data_ready_gate = False
            return False
        self._market_data_clean_windows += 1
        if self._market_data_clean_windows >= self.config.market_data_recovery_windows:
            self._market_data_degraded_windows = 0
            self._market_data_ready_gate = True
        return self._market_data_ready_gate

    def _write_market_data_coverage_update_if_changed(
        self,
        writer: Live2ArtifactWriter,
        market_data_status: dict[str, object],
    ) -> None:
        ws_health = market_data_status.get("ws_health")
        snapshot = {
            "status": market_data_status.get("status"),
            "reason": market_data_status.get("reason"),
            "stream_coverage_ready": market_data_status.get("stream_coverage_ready"),
            "market_data_gate_ready": self._market_data_ready_gate,
            "market_data_clean_windows": self._market_data_clean_windows,
            "market_data_degraded_windows": self._market_data_degraded_windows,
            "ws_health": ws_health if isinstance(ws_health, dict) else {},
        }
        if snapshot == self._last_market_data_coverage_snapshot:
            return
        self._last_market_data_coverage_snapshot = dict(snapshot)
        writer.write_event(
            Live2Event(
                event_type="market_data_coverage_update",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if self._market_data_ready_gate else Live2Severity.WARNING,
                message=str(market_data_status.get("reason", "")),
                data=snapshot,
            )
        )

    def _decision_latency_gate_ready(
        self,
        *,
        deadline_result: Live2DeadlineCycleResult,
        decision_cycle_elapsed_ms: int,
    ) -> bool:
        loop_budget_ms = max(1, int(self.config.decision_loop_interval_seconds * 1000))
        loop_overrun = decision_cycle_elapsed_ms > loop_budget_ms
        if loop_overrun:
            self._decision_loop_overrun_count += 1
        degraded = (
            deadline_result.deadline_missed_count > 0
            or deadline_result.max_latency_ms > self.config.decision_deadline_ms
            or loop_overrun
        )
        if degraded:
            self._decision_latency_degraded_windows += 1
            self._decision_latency_clean_windows = 0
        else:
            self._decision_latency_clean_windows += 1
            if self._decision_latency_clean_windows >= self.config.decision_latency_recovery_windows:
                self._decision_latency_degraded_windows = 0
        return self._decision_latency_degraded_windows < self.config.decision_latency_degraded_windows

    def _runtime_gate_status(self) -> dict[str, object]:
        reasons: list[str] = []
        if not self.readiness.artifact_writer_ready:
            reasons.append("artifact_writer_not_ready")
        if not self.readiness.market_data_ready:
            reasons.append("stream_coverage_not_ready")
        if not self.readiness.decision_latency_ready:
            reasons.append("decision_latency_degraded")
        if not self.readiness.exchange_boundary_ready:
            reasons.append("exchange_boundary_not_ready")
        if not self.readiness.position_supervisor_ready:
            reasons.append("position_supervisor_not_ready")
        if not self.readiness.execution_ready:
            reasons.append("execution_not_ready")
        reason = "+".join(reasons) if reasons else "all_gates_ready"
        self._last_runtime_gate_reason = reason
        return {
            "status": "ready" if self.readiness.new_entries_allowed else "no_new_entries",
            "reason": reason,
            "decision_loop_interval_seconds": self.config.decision_loop_interval_seconds,
            "decision_cycle_elapsed_ms": self._last_decision_cycle_elapsed_ms,
            "decision_latency_degraded_windows": self._decision_latency_degraded_windows,
            "decision_latency_clean_windows": self._decision_latency_clean_windows,
            "decision_latency_degraded_limit": self.config.decision_latency_degraded_windows,
            "decision_latency_recovery_windows": self.config.decision_latency_recovery_windows,
            "decision_loop_overrun_count": self._decision_loop_overrun_count,
            "decision_loop_max_elapsed_ms": self._decision_loop_max_elapsed_ms,
            "market_data_degraded_windows": self._market_data_degraded_windows,
            "market_data_clean_windows": self._market_data_clean_windows,
            "market_data_recovery_windows": self.config.market_data_recovery_windows,
            "readiness": self.readiness.as_dict(),
            "position_supervisor_status": self.position_supervisor.status(),
        }

    def _execution_status(self) -> dict[str, object]:
        return {
            **self.execution_engine.status(),
            "position_supervisor": self.position_supervisor.status(),
        }

    def _select_universe(self) -> Live2UniverseSelection:
        selector = Live2UniverseSelector(
            state_store=self.state_store,
            explicit_symbols=self.config.symbols,
            max_symbols=self.config.universe_max_symbols,
            min_quote_volume_24h=self.config.universe_min_quote_volume_24h,
            min_trade_count_24h=self.config.universe_min_trade_count_24h,
        )
        return selector.select()

    def _market_data_status(self) -> dict[str, object]:
        ticker_status = self.ticker_source.status().as_dict(stale_ms=self.config.ticker_stale_ms)
        aggtrade_status = self._aggtrade_status()
        ticker_ready = bool(ticker_status.get("ready"))
        aggtrade_ready = bool(aggtrade_status.get("ready"))
        stream_coverage_ready = ticker_ready and aggtrade_ready
        selected_symbols = 0 if self.universe_selection is None else len(self.universe_selection.selected_symbols)
        ws_health = self._ws_health_status(ticker_status=ticker_status, aggtrade_status=aggtrade_status)
        reasons: list[str] = []
        if selected_symbols <= 0:
            reasons.append("startup_ticker_universe_selection_empty")
        if not ticker_ready:
            reasons.append("ticker_ws_not_ready")
        if not aggtrade_ready:
            reasons.append("aggtrade_ws_not_ready")
        if stream_coverage_ready:
            status = "stream_coverage_ready_signal_adapter_active"
            reason = "ticker_and_aggtrade_ws_ready_signal_adapter_active_execution_boundary_active"
        elif selected_symbols <= 0:
            status = "no_startup_universe"
            reason = "+".join(reasons)
        else:
            status = "stream_coverage_not_ready"
            reason = "+".join(reasons) if reasons else "stream_coverage_not_ready"
        return {
            "status": status,
            "reason": reason,
            "ticker_ws": ticker_status,
            "aggtrade_ws": aggtrade_status,
            "universe": self._universe_status(),
            "ws_health": ws_health,
            "stream_coverage_ready": stream_coverage_ready,
            "market_data_ready_for_entries": self._market_data_ready_gate,
            "market_data_clean_windows": self._market_data_clean_windows,
            "market_data_degraded_windows": self._market_data_degraded_windows,
            "market_data_recovery_windows": self.config.market_data_recovery_windows,
        }

    def _ws_health_status(
        self,
        *,
        ticker_status: dict[str, object],
        aggtrade_status: dict[str, object],
    ) -> dict[str, object]:
        agg_shards = aggtrade_status.get("shards", [])
        stale_shards = 0
        disconnected_shards = 0
        if isinstance(agg_shards, list):
            for shard in agg_shards:
                if not isinstance(shard, dict):
                    continue
                if not bool(shard.get("connected")):
                    disconnected_shards += 1
                age_ms = shard.get("last_message_age_ms")
                if isinstance(age_ms, (int, float)) and age_ms > self.config.aggtrade_stale_ms:
                    stale_shards += 1
        ticker_reconnects = int(ticker_status.get("reconnect_attempts") or 0)
        ticker_disconnects = int(ticker_status.get("disconnect_count") or 0)
        agg_reconnects = int(aggtrade_status.get("reconnect_attempts") or 0)
        agg_disconnects = int(aggtrade_status.get("disconnect_count") or 0)
        payload_errors = int(ticker_status.get("payload_errors") or 0) + int(aggtrade_status.get("payload_errors") or 0)
        return {
            "ticker_ready": bool(ticker_status.get("ready")),
            "aggtrade_ready": bool(aggtrade_status.get("ready")),
            "ticker_connection_status": str(ticker_status.get("connection_status", "unknown")),
            "aggtrade_source_status": str(aggtrade_status.get("source_status", "unknown")),
            "shards_total": int(aggtrade_status.get("shards_total") or 0),
            "shards_connected": int(aggtrade_status.get("shards_connected") or 0),
            "shards_disconnected": disconnected_shards,
            "shards_stale": stale_shards,
            "reconnect_attempts": ticker_reconnects + agg_reconnects,
            "disconnect_count": ticker_disconnects + agg_disconnects,
            "payload_errors": payload_errors,
        }

    def _aggtrade_status(self) -> dict[str, object]:
        if self.aggtrade_source is None:
            return {
                "source_status": "not_started",
                "ready": False,
                "shards_total": 0,
                "shards_connected": 0,
                "symbols_total": 0,
                "streams_total": 0,
                "max_streams_per_connection": self.config.aggtrade_max_streams_per_connection,
                "reason": "startup_universe_not_selected_or_empty",
            }
        return self.aggtrade_source.status().as_dict(stale_ms=self.config.aggtrade_stale_ms)

    def _universe_status(self) -> dict[str, Any]:
        if self.universe_selection is None:
            return {
                "mode": "not_selected",
                "selected_symbols": 0,
                "max_symbols": self.config.universe_max_symbols,
                "min_quote_volume_24h": self.config.universe_min_quote_volume_24h,
                "min_trade_count_24h": self.config.universe_min_trade_count_24h,
            }
        return self.universe_selection.as_dict()

    def _write_startup_events(self, writer: Live2ArtifactWriter) -> None:
        writer.write_event(
            Live2Event(
                event_type="live2_started",
                component=Live2Component.RUNNER,
                message="deadline-driven live2 runtime started",
                data={
                    "runtime_generation": self.config.runtime_generation,
                    "symbols_total": len(self.state_store),
                    "new_entries_allowed": self.readiness.new_entries_allowed,
                    "universe_max_symbols": self.config.universe_max_symbols,
                    "universe_min_quote_volume_24h": self.config.universe_min_quote_volume_24h,
                    "universe_min_trade_count_24h": self.config.universe_min_trade_count_24h,
                    "decision_timeframe_ms": self.config.decision_timeframe_ms,
                    "decision_deadline_ms": self.config.decision_deadline_ms,
                    "market_data_recovery_windows": self.config.market_data_recovery_windows,
                    "execution_order_placement": "not_implemented_until_verified_fill_and_stop_path_exists",
                },
            )
        )
        writer.write_event(
            Live2Event(
                event_type="ticker_ws_starting",
                component=Live2Component.MARKET_DATA,
                message="starting Binance futures all-ticker WS ingestion",
                data={
                    "source": Live2TickerWsSource.source_id,
                    "symbols_filter_count": len(self.config.symbols),
                    "accept_all_symbols": not bool(self.config.symbols),
                    "stale_ms": self.config.ticker_stale_ms,
                    "startup_wait_seconds": self.config.ticker_startup_wait_seconds,
                    "universe_source": "explicit_symbols" if self.config.symbols else "ticker_ws_state_top_liquidity",
                },
            )
        )
        writer.write_event(
            Live2Event(
                event_type="signal_engine_started",
                component=Live2Component.SIGNAL,
                severity=Live2Severity.INFO,
                message="deadline engine uses live2 stream signal adapter with shared pump category contract subset",
            )
        )
        writer.write_event(
            Live2Event(
                event_type="execution_engine_started",
                component=Live2Component.EXECUTION,
                severity=Live2Severity.WARNING,
                message="execution boundary is protected by verified fill, verified initial stop, and live2 position supervisor",
            )
        )

    def _write_aggtrade_starting_event(self, writer: Live2ArtifactWriter) -> None:
        selected_symbols = 0 if self.universe_selection is None else len(self.universe_selection.selected_symbols)
        writer.write_event(
            Live2Event(
                event_type="aggtrade_ws_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if selected_symbols else Live2Severity.ERROR,
                message="starting Binance futures aggTrade WS shards",
                data={
                    "source": Live2AggTradeWsSource.source_id,
                    "symbols_filter_count": selected_symbols,
                    "max_streams_per_connection": self.config.aggtrade_max_streams_per_connection,
                    "stale_ms": self.config.aggtrade_stale_ms,
                    "startup_wait_seconds": self.config.aggtrade_startup_wait_seconds,
                    "hot_rest_backfill": False,
                    "universe": self._universe_status(),
                },
            )
        )
