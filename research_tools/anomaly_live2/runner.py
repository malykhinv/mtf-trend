"""Runner for anomaly live2 v0."""

from __future__ import annotations

import time

from .artifacts import Live2ArtifactWriter
from .clock import utc_now_iso
from .config import AnomalyLive2Config
from .contracts import Live2Component, Live2Event, Live2Readiness, Live2Severity
from .market_data.aggtrade_ws import Live2AggTradeWsSource
from .market_data.ticker_ws import Live2TickerWsSource
from .state import SymbolStateStore


class AnomalyLive2Runner:
    """Generation-0 live2 runner.

    This command is intentionally named as a real live runtime, not a shadow or
    dry-run mode. V0 installs the process/artifact/readiness skeleton and starts
    real ticker + explicit-symbol aggTrade WS ingestion. Signal evaluation and
    order placement remain explicit TODO gates, so new entries stay forbidden.
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
        self.ticker_source = Live2TickerWsSource(
            state_store=self.state_store,
            symbols=config.symbols,
            stale_ms=config.ticker_stale_ms,
            startup_wait_seconds=config.ticker_startup_wait_seconds,
        )
        self.aggtrade_source = Live2AggTradeWsSource(
            state_store=self.state_store,
            symbols=config.symbols,
            stale_ms=config.aggtrade_stale_ms,
            startup_wait_seconds=config.aggtrade_startup_wait_seconds,
            max_streams_per_connection=config.aggtrade_max_streams_per_connection,
        )
        self._shutdown_requested = False

    def run(self) -> int:
        writer = Live2ArtifactWriter(self.config.output_dir)
        try:
            self._write_startup_events(writer)
            self.ticker_source.start()
            self.aggtrade_source.start()
            ticker_ready = self.ticker_source.wait_until_ready()
            aggtrade_ready = self.aggtrade_source.wait_until_ready()
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
                reason="generation_0_ticker_and_aggtrade_ws_started",
                market_data_status=market_data_status,
            )
            print(
                f"live2 · старт · артефакты {self.config.output_dir} · "
                "ticker+aggTrade WS включены · signal/execution TODO · новые входы запрещены",
                flush=True,
            )
            while not self._shutdown_requested:
                time.sleep(self.config.heartbeat_interval_seconds)
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
                            "new_entries_allowed": self.readiness.new_entries_allowed,
                            "execution_status": "todo_not_implemented",
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
            )
            print("live2 · остановлено пользователем", flush=True)
            return 130
        finally:
            self.aggtrade_source.close()
            self.ticker_source.close()
            writer.close()
        return 0

    def shutdown(self, *, reason: str) -> None:
        self._shutdown_requested = True

    def _market_data_status(self) -> dict[str, object]:
        ticker_status = self.ticker_source.status().as_dict(stale_ms=self.config.ticker_stale_ms)
        aggtrade_status = self.aggtrade_source.status().as_dict(stale_ms=self.config.aggtrade_stale_ms)
        stream_coverage_ready = bool(ticker_status.get("ready")) and bool(aggtrade_status.get("ready"))
        if not self.config.symbols:
            status = "partial_ticker_only"
            reason = "aggtrade_requires_explicit_symbols_until_live2_universe_manager_exists"
        elif stream_coverage_ready:
            status = "stream_coverage_ready_signal_todo"
            reason = "ticker_and_aggtrade_ws_ready_but_signal_execution_not_implemented"
        else:
            status = "stream_coverage_not_ready"
            reason = "ticker_or_aggtrade_ws_not_ready"
        return {
            "status": status,
            "reason": reason,
            "ticker_ws": ticker_status,
            "aggtrade_ws": aggtrade_status,
            "stream_coverage_ready": stream_coverage_ready,
            "market_data_ready_for_entries": False,
        }

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
                },
            )
        )
        writer.write_event(
            Live2Event(
                event_type="aggtrade_ws_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if self.config.symbols else Live2Severity.WARNING,
                message=(
                    "starting Binance futures aggTrade WS shards"
                    if self.config.symbols
                    else "aggTrade WS needs explicit symbols until live2 universe manager exists"
                ),
                data={
                    "source": Live2AggTradeWsSource.source_id,
                    "symbols_filter_count": len(self.config.symbols),
                    "max_streams_per_connection": self.config.aggtrade_max_streams_per_connection,
                    "stale_ms": self.config.aggtrade_stale_ms,
                    "startup_wait_seconds": self.config.aggtrade_startup_wait_seconds,
                    "hot_rest_backfill": False,
                },
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
