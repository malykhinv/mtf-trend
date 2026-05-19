"""Runner for anomaly live2 v0."""

from __future__ import annotations

import time
from typing import Any

from .artifacts import Live2ArtifactWriter
from .clock import utc_now_iso
from .config import AnomalyLive2Config
from .contracts import Live2Component, Live2Event, Live2Readiness, Live2Severity
from .deadline import Live2DeadlineEngine, Live2DeadlineEngineConfig
from .entry_guard import Live2EntryGuardConfig, Live2EntryGuardEngine
from .execution import Live2ExecutionConfig, Live2ExecutionEngine, Live2ExecutionExchange
from .market_data.aggtrade_ws import Live2AggTradeWsSource
from .market_data.ticker_ws import Live2TickerWsSource
from .market_data.universe import Live2UniverseSelection, Live2UniverseSelector
from .state import SymbolStateStore


class AnomalyLive2Runner:
    """Generation-0 live2 runner.

    This command is intentionally named as a real live runtime, not a shadow or
    dry-run mode. V0 installs the process/artifact/readiness skeleton and starts
    real ticker + auto-selected aggTrade WS ingestion plus a stream-only signal
    adapter. Order placement remains an explicit TODO gate, so new entries stay
    forbidden until execution and safety guards are implemented.
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
            config=Live2ExecutionConfig(),
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
        )
        self._shutdown_requested = False

    def run(self) -> int:
        writer = Live2ArtifactWriter(self.config.output_dir)
        try:
            self._write_startup_events(writer)
            execution_preflight = self.execution_engine.preflight()
            self.readiness.exchange_boundary_ready = execution_preflight.ready
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
            market_data_status = self._market_data_status()
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
                execution_status=self.execution_engine.status(),
            )
            print(
                f"live2 · старт · артефакты {self.config.output_dir} · "
                f"universe {len(self.universe_selection.selected_symbols)} · "
                "ticker+aggTrade WS включены · stream signal adapter включен · execution boundary включен · новые входы запрещены",
                flush=True,
            )
            while not self._shutdown_requested:
                time.sleep(self.config.heartbeat_interval_seconds)
                deadline_result = self.deadline_engine.run_cycle()
                for decision in deadline_result.decisions:
                    writer.write_event(decision.as_event())
                market_data_status = self._market_data_status()
                writer.write_event(
                    Live2Event(
                        event_type="live2_heartbeat",
                        component=Live2Component.RUNNER,
                        message="generation_0_market_data_ws_alive",
                        data={
                            "symbols_total": len(self.state_store),
                            "ticker_status_counts": self.state_store.ticker_counts(),
                            "aggtrade_status_counts": self.state_store.aggtrade_counts(),
                            "candle_coverage_counts": self.state_store.candle_coverage_counts(),
                            "market_data_status": market_data_status,
                            "decision_status": self.deadline_engine.status(),
                            "deadline_cycle": deadline_result.as_dict(),
                            "new_entries_allowed": self.readiness.new_entries_allowed,
                            "execution_status": self.execution_engine.status(),
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
                    reason="generation_0_market_data_ws_alive",
                    market_data_status=market_data_status,
                    decision_status=self.deadline_engine.status(),
                    execution_status=self.execution_engine.status(),
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
                market_data_status=self._market_data_status(),
                decision_status=self.deadline_engine.status(),
                execution_status=self.execution_engine.status(),
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
        stream_coverage_ready = bool(ticker_status.get("ready")) and bool(aggtrade_status.get("ready"))
        selected_symbols = 0 if self.universe_selection is None else len(self.universe_selection.selected_symbols)
        if selected_symbols <= 0:
            status = "no_startup_universe"
            reason = "startup_ticker_universe_selection_empty"
        elif stream_coverage_ready:
            status = "stream_coverage_ready_signal_adapter_active"
            reason = "ticker_and_aggtrade_ws_ready_signal_adapter_active_execution_boundary_active"
        else:
            status = "stream_coverage_not_ready"
            reason = "ticker_or_aggtrade_ws_not_ready"
        return {
            "status": status,
            "reason": reason,
            "ticker_ws": ticker_status,
            "aggtrade_ws": aggtrade_status,
            "universe": self._universe_status(),
            "stream_coverage_ready": stream_coverage_ready,
            "market_data_ready_for_entries": False,
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
                message="execution boundary checks exchange preflight and pre-entry position; order placement waits for verified-fill/stop path",
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
