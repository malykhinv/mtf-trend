"""Runner for anomaly live2 v0."""

from __future__ import annotations

import time
import threading
from collections import Counter
from typing import Any

from .artifacts import Live2ArtifactWriter
from .clock import utc_now_iso
from .config import AnomalyLive2Config
from .console import Live2StatusLogger
from .contracts import Live2Component, Live2Event, Live2Readiness, Live2Severity
from .deadline import Live2DeadlineCycleResult, Live2DeadlineEngine, Live2DeadlineEngineConfig
from .entry_guard import Live2EntryGuardConfig, Live2EntryGuardEngine
from .execution import Live2ExecutionConfig, Live2ExecutionEngine, Live2ExecutionExchange
from .market_data.aggtrade_ws import Live2AggTradeWsSource
from .market_data.mark_price_ws import Live2MarkPriceWsSource
from .market_data.open_interest import Live2OpenInterestPollConfig, Live2OpenInterestPoller
from .market_data.prior_context import (
    LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_ID_RATIO,
    LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_IDS_PER_CANDLE,
    Live2PriorContextPollConfig,
    Live2PriorContextPoller,
)
from .market_data.ticker_ws import Live2TickerWsSource
from .market_data.startup_tickers import Live2StartupTickerSnapshot, Live2StartupTickerSnapshotResult
from .market_data.universe import Live2UniverseSelection, Live2UniverseSelector
from .market_data.warmup import (
    Live2StartupAggTradeWarmup,
    Live2StartupHtfBaselineConfig,
    Live2StartupHtfBaselineResult,
    Live2StartupHtfBaselineWarmup,
    Live2StartupWarmupConfig,
    Live2StartupWarmupProgress,
    Live2StartupWarmupResult,
)
from .position_supervisor import Live2PositionSupervisor, Live2PositionSupervisorConfig
from .session_top import Live2SessionTopTracker, live2_session_metric_window_ms
from .signal import Live2SignalEngine
from .state import SymbolStateStore
from .status_grid import format_live2_status_grid
from .telegram import Live2TelegramConfig, Live2TelegramDispatcher
from .top_growth import Live2TopGrowthAudit, Live2TopGrowthAuditConfig, Live2TopGrowthAuditStats
from .user_data_stream import Live2UserDataStreamSource


class AnomalyLive2Runner:
    """Generation-0 live2 runner.

    This command is intentionally named as a real live runtime, not a shadow or
    dry-run mode. V0 installs the process/artifact/readiness skeleton and starts
    real ticker + auto-selected aggTrade WS ingestion plus a stream-only signal
    adapter. P321 enables real entry execution only after runtime gates, actual
    fill recovery, and strict initial-stop visibility verification.
    """

    def __init__(
        self,
        config: AnomalyLive2Config,
        *,
        exchange_client: Live2ExecutionExchange | None = None,
        telegram_config: Live2TelegramConfig | None = None,
    ) -> None:
        self.config = config
        self.status_logger = Live2StatusLogger(print)
        self.started_at_utc = utc_now_iso()
        self.state_store = SymbolStateStore(
            config.symbols,
            max_closed_candles=config.max_closed_candles_per_timeframe,
        )
        if telegram_config is None:
            raise ValueError("telegram_config is required for run-anomaly-live2")
        self.telegram = Live2TelegramDispatcher(
            telegram_config,
            logger=self.status_logger,
        )
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
            reconnect_initial_delay_seconds=config.ws_reconnect_initial_delay_seconds,
            reconnect_max_delay_seconds=config.ws_reconnect_max_delay_seconds,
            connection_max_age_seconds=config.ws_connection_max_age_seconds,
        )
        self.aggtrade_source: Live2AggTradeWsSource | None = None
        self.mark_price_source: Live2MarkPriceWsSource | None = None
        self.open_interest_source: Live2OpenInterestPoller | None = None
        self.prior_context_source: Live2PriorContextPoller | None = None
        self.user_data_source: Live2UserDataStreamSource | None = None
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
                early_exit_enabled=config.position_supervisor_early_exit_enabled,
                early_exit_min_hold_candles=config.position_supervisor_early_exit_min_hold_candles,
                early_exit_stall_candles=config.position_supervisor_early_exit_stall_candles,
                early_exit_min_mfe_r=config.position_supervisor_early_exit_min_mfe_r,
            ),
        )
        self.universe_selection: Live2UniverseSelection | None = None
        self.startup_ticker_snapshot_result: Live2StartupTickerSnapshotResult | None = None
        self.startup_warmup_result: Live2StartupWarmupResult | None = None
        self.startup_htf_baseline_result: Live2StartupHtfBaselineResult | None = None
        self.startup_context_prewarm_result: dict[str, object] | None = None
        self.session_top_tracker = Live2SessionTopTracker()
        self._last_session_top_snapshot: dict[str, object] | None = None
        self.top_growth_audit = Live2TopGrowthAudit(
            output_dir=config.output_dir,
            exchange_client=exchange_client,
            config=Live2TopGrowthAuditConfig(
                enabled=config.top_growth_enabled,
                min_return_pct=config.top_growth_min_return_pct,
                limit=config.top_growth_limit,
                symbols_per_cycle=config.top_growth_symbols_per_cycle,
                max_cycle_seconds=config.top_growth_max_cycle_seconds,
                fetch_spacing_seconds=config.top_growth_fetch_spacing_seconds,
            ),
        )
        self._last_top_growth_audit_stats = Live2TopGrowthAuditStats(
            enabled=config.top_growth_enabled,
            status="not_started",
            reason="not_started",
        )
        self._top_growth_audit_lock = threading.Lock()
        self._top_growth_audit_worker: threading.Thread | None = None
        self._top_growth_completed_events: list[dict[str, object]] = []
        self.deadline_engine = Live2DeadlineEngine(
            state_store=self.state_store,
            config=Live2DeadlineEngineConfig(
                timeframe_ms=config.decision_timeframe_ms,
                decision_deadline_ms=config.decision_deadline_ms,
                backlog_expire_ms=config.decision_backlog_expire_ms,
                actionable_min_quote_volume=config.actionable_min_quote_volume,
                actionable_min_trade_count=config.actionable_min_trade_count,
                actionable_min_abs_return_pct=config.actionable_min_abs_return_pct,
                stale_trade_ms=config.aggtrade_stale_ms,
            ),
            signal_engine=Live2SignalEngine(
                mark_stale_ms=config.mark_price_stale_ms,
                oi_stale_ms=config.oi_stale_ms,
                prior_context_stale_ms=config.prior_context_stale_ms,
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
            live_decision_watermark_ms=self._live_decision_watermark_ms,
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
        self._market_data_transition_counts: Counter[str] = Counter()
        self._runtime_gate_transition_counts: Counter[str] = Counter()
        self._runtime_gate_allowed_seconds = 0.0
        self._runtime_gate_blocked_seconds = 0.0
        self._runtime_gate_current_allowed = False
        self._runtime_gate_current_since_monotonic = time.perf_counter()
        self._session_gate_metric_start_ms: int | None = None
        self._session_gate_allowed_seconds = 0.0
        self._session_gate_blocked_seconds = 0.0
        self._session_gate_current_allowed = False
        self._session_gate_current_since_monotonic = self._runtime_gate_current_since_monotonic
        self._session_counter_metric_start_ms: int | None = None
        self._session_decision_baseline: dict[str, int] = {}
        self._session_execution_baseline: dict[str, int] = {}
        self._session_runtime_baseline: dict[str, int] = {}
        self._last_runtime_gate_reason = "startup"
        self._last_decision_cycle_elapsed_ms = 0
        self._started_monotonic = time.perf_counter()
        self._market_started_monotonic: float | None = None

    def run(self) -> int:
        writer = Live2ArtifactWriter(
            self.config.output_dir,
            queue_max_size=self.config.artifact_writer_queue_max_size,
        )
        try:
            self._set_startup_status("инициализация", "готовлю артефакты и preflight")
            self.telegram.set_event_writer(writer.write_event)
            self._write_startup_events(writer)
            self._set_startup_status("preflight", "проверяю exchange/account")
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
                    data={
                        **execution_preflight.as_dict(),
                        "live2_trading_mode": "real_orders_always_enabled",
                        "dry_run_supported": False,
                    },
                )
            )
            if not execution_preflight.ready:
                raise RuntimeError(f"live2 execution preflight failed: {execution_preflight.reason}")
            user_data_ready = False
            if execution_preflight.ready:
                self.user_data_source = Live2UserDataStreamSource(
                    exchange_client=self.execution_engine.exchange_client,
                    startup_wait_seconds=self.config.user_data_stream_startup_wait_seconds,
                    keepalive_interval_seconds=self.config.user_data_stream_keepalive_interval_seconds,
                    reconnect_initial_delay_seconds=self.config.ws_reconnect_initial_delay_seconds,
                    reconnect_max_delay_seconds=self.config.ws_reconnect_max_delay_seconds,
                    connection_max_age_seconds=self.config.ws_connection_max_age_seconds,
                    event_callback=writer.write_event,
                )
                self.position_supervisor.set_user_data_status_provider(self._user_data_stream_status)
                self._write_user_data_stream_starting_event(writer)
                self._set_startup_status("user stream", "создаю listenKey и подключаю private WS")
                self.user_data_source.start()
                user_data_ready = self.user_data_source.wait_until_ready()
                self._set_startup_status("user stream", "private WS готов" if user_data_ready else "private WS пока не готов")
            else:
                writer.write_event(
                    Live2Event(
                        event_type="user_data_stream_not_started",
                        component=Live2Component.EXECUTION,
                        severity=Live2Severity.ERROR,
                        message="user-data stream not started because execution preflight is not ready",
                        data=execution_preflight.as_dict(),
                    )
                )
            self.readiness.user_data_stream_ready = user_data_ready
            if not user_data_ready:
                writer.write_event(
                    Live2Event(
                        event_type="user_data_stream_startup_failed",
                        component=Live2Component.EXECUTION,
                        severity=Live2Severity.ERROR,
                        message="live2 real-order runtime requires a ready private user-data stream",
                        data={
                            "user_data_stream_status": self._user_data_stream_status(),
                            "live2_trading_mode": "real_orders_always_enabled",
                            "dry_run_supported": False,
                        },
                    )
                )
                raise RuntimeError("live2 user-data stream is not ready")
            if not self.config.symbols:
                self._set_startup_status("вселенная", "загружаю startup ticker snapshot")
                self.startup_ticker_snapshot_result = self._run_startup_ticker_snapshot(writer)
                self._set_startup_status(
                    "вселенная",
                    f"snapshot {self.startup_ticker_snapshot_result.rows_applied}/{self.startup_ticker_snapshot_result.rows_received}",
                )
            self._set_startup_status("ticker", "подключаю !ticker@arr")
            self.ticker_source.start()
            ticker_ready = self.ticker_source.wait_until_ready()
            self._set_startup_status("ticker", "ticker готов" if ticker_ready else "ticker пока не готов")
            if not ticker_ready:
                ticker_status = self.ticker_source.status().as_dict(stale_ms=self.config.ticker_stale_ms)
                writer.write_event(
                    Live2Event(
                        event_type="ticker_ws_startup_failed",
                        component=Live2Component.MARKET_DATA,
                        severity=Live2Severity.ERROR,
                        message="live2 requires a ready ticker WS before entering the main loop",
                        data={
                            "ticker_ws": ticker_status,
                            "startup_failure": True,
                            "live2_trading_mode": "real_orders_always_enabled",
                        },
                    )
                )
                raise RuntimeError(f"live2 ticker WS startup failed: {ticker_status.get('reason') or ticker_status.get('connection_status')}")
            self._set_startup_status("вселенная", "выбираю universe из startup snapshot + ticker state")
            self.universe_selection = self._select_universe()
            if (
                not self.config.symbols
                and self.config.universe_min_auto_symbols > 0
                and len(self.universe_selection.selected_symbols) < self.config.universe_min_auto_symbols
            ):
                writer.write_event(
                    Live2Event(
                        event_type="startup_universe_too_small",
                        component=Live2Component.MARKET_DATA,
                        severity=Live2Severity.ERROR,
                        message="live2 startup universe is below the configured minimum auto coverage",
                        data={
                            "selected_symbols": len(self.universe_selection.selected_symbols),
                            "min_auto_symbols": self.config.universe_min_auto_symbols,
                            "universe": self.universe_selection.as_dict(),
                            "startup_ticker_snapshot": None
                            if self.startup_ticker_snapshot_result is None
                            else self.startup_ticker_snapshot_result.as_dict(),
                        },
                    )
                )
                raise RuntimeError(
                    "live2 startup universe below minimum auto coverage: "
                    f"selected={len(self.universe_selection.selected_symbols)} "
                    f"minimum={self.config.universe_min_auto_symbols}"
                )
            if not self.universe_selection.selected_symbols:
                writer.write_event(
                    Live2Event(
                        event_type="startup_universe_empty",
                        component=Live2Component.MARKET_DATA,
                        severity=Live2Severity.ERROR,
                        message="live2 startup universe is empty",
                        data={
                            "universe": self.universe_selection.as_dict(),
                            "startup_ticker_snapshot": None
                            if self.startup_ticker_snapshot_result is None
                            else self.startup_ticker_snapshot_result.as_dict(),
                            "startup_failure": True,
                        },
                    )
                )
                raise RuntimeError("live2 startup universe is empty")
            self.state_store.apply_universe_selection(
                selected_rank_by_symbol=self.universe_selection.rank_by_symbol(),
                selected_at_ms=self.universe_selection.selected_at_ms,
                mode=self.universe_selection.mode,
            )
            self._set_startup_status(
                "вселенная",
                f"выбрано {len(self.universe_selection.selected_symbols)}/{self.universe_selection.eligible_symbols} · "
                f"cap {self.universe_selection.max_symbols}",
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
                self.startup_htf_baseline_result = self._run_startup_htf_baseline_warmup(writer)
                self._set_startup_status(
                    "HTF baseline",
                    f"готово {self.startup_htf_baseline_result.symbols_warmed}/{self.startup_htf_baseline_result.symbols_requested} · "
                    f"1m candles {self.startup_htf_baseline_result.candles_loaded}",
                )
                self.startup_warmup_result = self._run_startup_aggtrade_warmup(writer)
                self._set_startup_status(
                    "прогрев",
                    f"готово {self.startup_warmup_result.symbols_warmed}/{self.startup_warmup_result.symbols_requested} · "
                    f"trades {self.startup_warmup_result.trades_loaded}",
                )
                self.aggtrade_source = Live2AggTradeWsSource(
                    state_store=self.state_store,
                    symbols=self.universe_selection.selected_symbols,
                    stale_ms=self.config.aggtrade_stale_ms,
                    startup_wait_seconds=self.config.aggtrade_startup_wait_seconds,
                    max_streams_per_connection=self.config.aggtrade_max_streams_per_connection,
                    reconnect_initial_delay_seconds=self.config.ws_reconnect_initial_delay_seconds,
                    reconnect_max_delay_seconds=self.config.ws_reconnect_max_delay_seconds,
                    connection_max_age_seconds=self.config.ws_connection_max_age_seconds,
                )
                self._write_aggtrade_starting_event(writer)
                self._set_startup_status("aggTrade", "запускаю WS shards")
                self.aggtrade_source.start()
                aggtrade_ready = self.aggtrade_source.wait_until_ready()
                self._set_startup_status("aggTrade", "поток готов" if aggtrade_ready else "поток пока не готов")
                if not aggtrade_ready:
                    aggtrade_status = self.aggtrade_source.status().as_dict(stale_ms=self.config.aggtrade_stale_ms)
                    writer.write_event(
                        Live2Event(
                            event_type="aggtrade_ws_startup_failed",
                            component=Live2Component.MARKET_DATA,
                            severity=Live2Severity.ERROR,
                            message="live2 requires all aggTrade shards to receive live WS payload before entering the main loop",
                            data={
                                "aggtrade_ws": aggtrade_status,
                                "startup_failure": True,
                                "live2_trading_mode": "real_orders_always_enabled",
                            },
                        )
                    )
                    raise RuntimeError(f"live2 aggTrade WS startup failed: {aggtrade_status.get('reason') or aggtrade_status.get('source_status')}")
                self.mark_price_source = Live2MarkPriceWsSource(
                    state_store=self.state_store,
                    symbols=self.universe_selection.selected_symbols,
                    stale_ms=self.config.mark_price_stale_ms,
                    startup_wait_seconds=self.config.mark_price_startup_wait_seconds,
                    reconnect_initial_delay_seconds=self.config.ws_reconnect_initial_delay_seconds,
                    reconnect_max_delay_seconds=self.config.ws_reconnect_max_delay_seconds,
                    connection_max_age_seconds=self.config.ws_connection_max_age_seconds,
                )
                self._write_mark_price_starting_event(writer)
                self._set_startup_status("markPrice", "запускаю WS")
                self.mark_price_source.start()
                mark_ready = self.mark_price_source.wait_until_ready()
                self._set_startup_status("markPrice", "поток готов" if mark_ready else "поток пока не готов")
                if not mark_ready:
                    mark_status = self.mark_price_source.status().as_dict(stale_ms=self.config.mark_price_stale_ms)
                    writer.write_event(
                        Live2Event(
                            event_type="mark_price_ws_startup_failed",
                            component=Live2Component.MARKET_DATA,
                            severity=Live2Severity.ERROR,
                            message="live2 requires markPrice WS context before entering the main loop",
                            data={
                                "mark_price_ws": mark_status,
                                "startup_failure": True,
                                "live2_trading_mode": "real_orders_always_enabled",
                            },
                        )
                    )
                    raise RuntimeError(f"live2 markPrice WS startup failed: {mark_status.get('reason') or mark_status.get('connection_status')}")
                self.open_interest_source = Live2OpenInterestPoller(
                    state_store=self.state_store,
                    exchange_client=self.execution_engine.exchange_client,
                    config=Live2OpenInterestPollConfig(
                        poll_interval_seconds=self.config.oi_poll_interval_seconds,
                        symbol_cooldown_seconds=self.config.oi_symbol_cooldown_seconds,
                        stale_ms=self.config.oi_stale_ms,
                        lookback_minutes=self.config.oi_lookback_minutes,
                        max_symbols_per_cycle=self.config.oi_max_symbols_per_cycle,
                        radar_symbol_ttl_ms=self.config.oi_radar_symbol_ttl_ms,
                    ),
                )
                self._write_open_interest_starting_event(writer)
                self.prior_context_source = Live2PriorContextPoller(
                    state_store=self.state_store,
                    exchange_client=self.execution_engine.exchange_client,
                    config=Live2PriorContextPollConfig(
                        poll_interval_seconds=self.config.prior_context_poll_interval_seconds,
                        symbol_cooldown_seconds=self.config.prior_context_symbol_cooldown_seconds,
                        stale_ms=self.config.prior_context_stale_ms,
                        lookback_hours=self.config.prior_context_lookback_hours,
                        max_symbols_per_cycle=self.config.prior_context_max_symbols_per_cycle,
                        radar_symbol_ttl_ms=self.config.prior_context_radar_symbol_ttl_ms,
                        spike_return_pct=self.config.prior_context_spike_return_pct,
                        fast_fade_retrace_fraction=self.config.prior_context_fast_fade_retrace_fraction,
                    ),
                )
                self._write_prior_context_starting_event(writer)
                self.startup_context_prewarm_result = self._run_startup_context_prewarm(writer)
                self._set_startup_status("OI", "запускаю runtime poller для universe")
                self.open_interest_source.start()
                self._set_startup_status("24h context", "запускаю runtime poller для active/radar")
                self.prior_context_source.start()
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
            writer.write_symbol_state(self.state_store, aggtrade_stale_ms=self.config.aggtrade_stale_ms)
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
                diagnostics_summary=self._diagnostics_summary(
                    writer=writer,
                    market_data_status=market_data_status,
                    decision_status=self.deadline_engine.status(),
                    execution_status=self._execution_status(),
                    runtime_gate_status=runtime_gate_status,
                ),
            )
            writer.write_diagnostics_summary(
                self._diagnostics_summary(
                    writer=writer,
                    market_data_status=market_data_status,
                    decision_status=self.deadline_engine.status(),
                    execution_status=self._execution_status(),
                    runtime_gate_status=runtime_gate_status,
                )
            )
            self.telegram.send_startup(
                output_dir=self.config.output_dir,
                universe_size=len(self.universe_selection.selected_symbols),
                runtime_generation=self.config.runtime_generation,
            )
            self._market_started_monotonic = time.perf_counter()
            self.status_logger(
                f"live2 · старт · артефакты {self.config.output_dir} · "
                f"universe {len(self.universe_selection.selected_symbols)} · "
                "ticker+aggTrade+mark WS включены · private user stream включен · verified entry+stop execution включен"
            )
            last_heartbeat_at = 0.0
            while not self._shutdown_requested:
                cycle_started = time.perf_counter()
                decision_now_ms = int(time.time() * 1000)
                self.state_store.close_due_candles(now_ms=decision_now_ms)
                supervisor_result = self.position_supervisor.run_cycle(self.state_store)
                for action in supervisor_result.actions:
                    writer.write_event(action.as_event())
                    self.telegram.notify_supervisor_action(action)
                deadline_result = self.deadline_engine.run_cycle(now_ms=decision_now_ms)
                self._last_decision_cycle_elapsed_ms = int((time.perf_counter() - cycle_started) * 1000)
                self._decision_loop_max_elapsed_ms = max(
                    self._decision_loop_max_elapsed_ms,
                    self._last_decision_cycle_elapsed_ms,
                )
                for decision in deadline_result.decisions:
                    writer.write_event(decision.as_event())
                    near_miss_row = decision.as_near_miss_row()
                    if near_miss_row is not None:
                        writer.write_near_miss(near_miss_row)
                    self.telegram.notify_decision(decision)

                market_data_status = self._market_data_status(include_symbol_counts=False)
                self._refresh_runtime_gates(
                    writer=writer,
                    market_data_status=market_data_status,
                    deadline_result=deadline_result,
                    decision_cycle_elapsed_ms=self._last_decision_cycle_elapsed_ms,
                )

                now_monotonic = time.monotonic()
                if now_monotonic - last_heartbeat_at >= self.config.heartbeat_interval_seconds:
                    last_heartbeat_at = now_monotonic
                    self.session_top_tracker.update_from_state_snapshot(self.state_store.snapshot())
                    self._last_session_top_snapshot = self.session_top_tracker.snapshot()
                    self._kick_top_growth_audit(
                        symbols=self._selected_symbols_tuple(),
                        now_ms=int(time.time() * 1000),
                    )
                    for top_growth_event_data in self._drain_top_growth_completed_events():
                        writer.write_event(
                            Live2Event(
                                event_type="live2_top_growth_audit_completed",
                                component=Live2Component.RUNNER,
                                severity=Live2Severity.INFO,
                                symbol="__top_growth__",
                                message=str(top_growth_event_data.get("reason") or "closed_hour_top_growth_artifacts_written"),
                                data=top_growth_event_data,
                            )
                        )
                    market_data_status = self._market_data_status(include_symbol_counts=True)
                    runtime_gate_status = self._runtime_gate_status()
                    writer.write_event(
                        Live2Event(
                            event_type="live2_heartbeat",
                            component=Live2Component.RUNNER,
                            message="generation_0_fast_decision_loop_alive",
                            data={
                                "symbols_total": len(self.state_store),
                                "ticker_status_counts": self.state_store.ticker_counts(),
                                "aggtrade_status_counts": market_data_status.get("aggtrade_status_counts", {}),
                                "startup_aggtrade_status_counts": market_data_status.get("startup_aggtrade_status_counts", {}),
                                "live_aggtrade_status_counts": market_data_status.get("live_aggtrade_status_counts", {}),
                                "candle_coverage_counts": market_data_status.get("candle_coverage_counts", {}),
                                "market_data_status": market_data_status,
                                "decision_status": self.deadline_engine.status(),
                                "deadline_cycle": deadline_result.as_dict(),
                                "decision_cycle_elapsed_ms": self._last_decision_cycle_elapsed_ms,
                                "runtime_gate_status": runtime_gate_status,
                                "new_entries_allowed": self.readiness.new_entries_allowed,
                                "execution_status": self._execution_status(),
                                "user_data_stream_status": self._user_data_stream_status(),
                                "position_supervisor_cycle": supervisor_result.as_dict(),
                                "session_top": self._last_session_top_snapshot or {},
                                "top_growth_audit": self._last_top_growth_audit_stats.as_dict(),
                                "artifact_writer_status": writer.status().as_dict(),
                            },
                        )
                    )
                    decision_status = self.deadline_engine.status()
                    execution_status = self._execution_status()
                    artifact_writer_status = writer.status().as_dict()
                    grid_decision_status, grid_execution_status, grid_runtime_gate_status = self._session_scoped_grid_status(
                        decision_status=decision_status,
                        execution_status=execution_status,
                        runtime_gate_status=runtime_gate_status,
                    )
                    diagnostics_summary = self._diagnostics_summary(
                        writer=writer,
                        market_data_status=market_data_status,
                        decision_status=decision_status,
                        execution_status=execution_status,
                        runtime_gate_status=runtime_gate_status,
                    )
                    writer.write_symbol_state(self.state_store, aggtrade_stale_ms=self.config.aggtrade_stale_ms)
                    writer.write_status(
                        runtime_generation=self.config.runtime_generation,
                        started_at_utc=self.started_at_utc,
                        readiness=self.readiness,
                        state_store=self.state_store,
                        status="running",
                        reason="generation_0_fast_decision_loop_alive",
                        market_data_status=market_data_status,
                        decision_status=decision_status,
                        execution_status=execution_status,
                        runtime_gate_status=runtime_gate_status,
                        diagnostics_summary=diagnostics_summary,
                    )
                    writer.write_diagnostics_summary(diagnostics_summary)
                    self.status_logger.status(
                        format_live2_status_grid(
                            runtime_seconds=self._market_runtime_seconds(),
                            cycle_seconds=max(0.0, self._last_decision_cycle_elapsed_ms / 1000.0),
                            state_counts=self.state_store.counts_by_status(),
                            ticker_counts=self.state_store.ticker_counts(),
                            aggtrade_counts=market_data_status.get("aggtrade_status_counts", {}),
                            mark_counts=market_data_status.get("mark_price_status_counts", {}),
                            open_interest_counts=market_data_status.get("open_interest_status_counts", {}),
                            prior_context_counts=market_data_status.get("prior_context_status_counts", {}),
                            candle_counts=market_data_status.get("candle_coverage_counts", {}),
                            market_data_status=market_data_status,
                            decision_status=grid_decision_status,
                            execution_status=grid_execution_status,
                            user_data_stream_status=self._user_data_stream_status(),
                            runtime_gate_status=grid_runtime_gate_status,
                            artifact_writer_status=artifact_writer_status,
                            session_top_snapshot=self._last_session_top_snapshot,
                        ),
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
            market_data_status = self._market_data_status()
            decision_status = self.deadline_engine.status()
            execution_status = self._execution_status()
            runtime_gate_status = self._runtime_gate_status()
            diagnostics_summary = self._diagnostics_summary(
                writer=writer,
                market_data_status=market_data_status,
                decision_status=decision_status,
                execution_status=execution_status,
                runtime_gate_status=runtime_gate_status,
            )
            writer.write_status(
                runtime_generation=self.config.runtime_generation,
                started_at_utc=self.started_at_utc,
                readiness=self.readiness,
                state_store=self.state_store,
                status="stopped",
                reason="keyboard_interrupt",
                market_data_status=market_data_status,
                decision_status=decision_status,
                execution_status=execution_status,
                runtime_gate_status=runtime_gate_status,
                diagnostics_summary=diagnostics_summary,
            )
            writer.write_diagnostics_summary(diagnostics_summary)
            self.telegram.send(
                channel="events",
                key="live2_stopped_keyboard_interrupt",
                text="⚠️ <b>Live2 остановлен</b>\n\n<code>keyboard_interrupt</code>",
                symbol="__telegram__",
            )
            self.status_logger.finish_status()
            self.status_logger("live2 · остановлено пользователем")
            return 130
        except Exception as exc:
            self.shutdown(reason="runner_exception")
            self.telegram.send_critical_sync(
                channel="events",
                key="live2_runner_exception",
                text=f"⚠️ <b>Live2 остановлен</b>\n\n<code>{type(exc).__name__}: {str(exc)[:500]}</code>",
                symbol="__telegram__",
            )
            writer.write_event(
                Live2Event(
                    event_type="live2_internal_error",
                    component=Live2Component.RUNNER,
                    severity=Live2Severity.ERROR,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            raise
        finally:
            self.status_logger.finish_status()
            if self.user_data_source is not None:
                self.user_data_source.close()
            if self.prior_context_source is not None:
                self.prior_context_source.close()
            if self.open_interest_source is not None:
                self.open_interest_source.close()
            if self.mark_price_source is not None:
                self.mark_price_source.close()
            if self.aggtrade_source is not None:
                self.aggtrade_source.close()
            self.ticker_source.close()
            self.telegram.close()
            writer.close()
        return 0

    def shutdown(self, *, reason: str) -> None:
        self._shutdown_requested = True


    def _set_startup_status(self, stage: str, message: str) -> None:
        self.status_logger.status(
            "\n".join(
                (
                    "Live2",
                    f"Этап {stage}",
                    str(message),
                    f"Артефакты {self.config.output_dir}",
                )
            )
        )

    def _set_warmup_progress_status(self, progress: Live2StartupWarmupProgress) -> None:
        self.status_logger.status(
            "\n".join(
                (
                    "Live2",
                    "Этап прогрев",
                    (
                        f"{progress.current_index}/{progress.symbols_total} · "
                        f"готово {progress.symbols_warmed} · ошибок {progress.symbols_failed} · "
                        f"trades {progress.trades_loaded}"
                    ),
                    f"Символ {progress.symbol}",
                )
            )
        )


    def _refresh_artifact_writer_readiness(self, writer: Live2ArtifactWriter) -> None:
        status = writer.status()
        self.readiness.artifact_writer_ready = status.ready

    def _market_runtime_seconds(self) -> float:
        if self._market_started_monotonic is None:
            return 0.0
        return max(0.0, time.perf_counter() - self._market_started_monotonic)

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
        self._write_market_data_coverage_transition_if_changed(writer, market_data_status)
        self.readiness.exchange_boundary_ready = self.execution_engine.preflight_result.ready
        self.readiness.execution_ready = self.execution_engine.ready
        self.readiness.user_data_stream_ready = bool(self._user_data_stream_status().get("ready"))
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
            "user_data_stream_ready": self.readiness.user_data_stream_ready,
            "new_entries_allowed": self.readiness.new_entries_allowed,
            "reason": runtime_gate_status["reason"],
            "market_data_source_ready": bool(market_data_status.get("entry_stream_ready")),
            "market_data_status": str(market_data_status.get("status", "")),
        }
        if snapshot != self._last_runtime_gate_snapshot:
            previous_allowed = bool(self._last_runtime_gate_snapshot.get("new_entries_allowed")) if self._last_runtime_gate_snapshot else False
            current_allowed = self.readiness.new_entries_allowed
            self._record_runtime_gate_transition(snapshot)
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
            self.telegram.notify_runtime_gate_update(
                runtime_gate_status,
                previous_allowed=previous_allowed,
                current_allowed=current_allowed,
            )

    def _market_data_gate_ready(self, market_data_status: dict[str, object]) -> bool:
        source_ready = bool(market_data_status.get("entry_stream_ready"))
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

    def _write_market_data_coverage_transition_if_changed(
        self,
        writer: Live2ArtifactWriter,
        market_data_status: dict[str, object],
    ) -> None:
        snapshot = self._market_data_transition_snapshot(market_data_status)
        if snapshot == self._last_market_data_coverage_snapshot:
            return
        self._last_market_data_coverage_snapshot = dict(snapshot)
        transition_key = self._transition_key(snapshot, (
            "status",
            "reason",
            "stream_coverage_ready",
            "entry_stream_ready",
            "market_data_gate_ready",
            "ticker_ready",
            "aggtrade_ready",
            "mark_price_ready",
            "shards_connected",
            "shards_total",
            "shards_stale",
        ))
        self._market_data_transition_counts[transition_key] += 1
        writer.write_event(
            Live2Event(
                event_type="market_data_coverage_transition",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if self._market_data_ready_gate else Live2Severity.WARNING,
                message=str(market_data_status.get("reason", "")),
                data={
                    "transition": snapshot,
                    "transition_count": self._market_data_transition_counts[transition_key],
                    "event_policy": "transition_only_no_counter_window_spam",
                    "full_market_data_status_at_transition": market_data_status,
                },
            )
        )

    def _market_data_transition_snapshot(self, market_data_status: dict[str, object]) -> dict[str, object]:
        ws_health = market_data_status.get("ws_health")
        ws_health_dict = ws_health if isinstance(ws_health, dict) else {}
        return {
            "status": str(market_data_status.get("status", "")),
            "reason": str(market_data_status.get("reason", "")),
            "stream_coverage_ready": bool(market_data_status.get("stream_coverage_ready")),
            "entry_stream_ready": bool(market_data_status.get("entry_stream_ready")),
            "market_data_gate_ready": bool(self._market_data_ready_gate),
            "ticker_ready": bool(ws_health_dict.get("ticker_ready")),
            "aggtrade_ready": bool(ws_health_dict.get("aggtrade_ready")),
            "mark_price_ready": bool(ws_health_dict.get("mark_price_ready")),
            "ticker_connection_status": str(ws_health_dict.get("ticker_connection_status", "unknown")),
            "aggtrade_source_status": str(ws_health_dict.get("aggtrade_source_status", "unknown")),
            "aggtrade_endpoint_category": str(ws_health_dict.get("aggtrade_endpoint_category", "unknown")),
            "mark_price_connection_status": str(ws_health_dict.get("mark_price_connection_status", "unknown")),
            "shards_total": int(ws_health_dict.get("shards_total") or 0),
            "shards_connected": int(ws_health_dict.get("shards_connected") or 0),
            "shards_disconnected": int(ws_health_dict.get("shards_disconnected") or 0),
            "shards_stale": int(ws_health_dict.get("shards_stale") or 0),
            "live_decision_watermark_ready": market_data_status.get("live_decision_watermark_ms") is not None,
        }

    def _record_runtime_gate_transition(self, snapshot: dict[str, object]) -> None:
        now_monotonic = time.perf_counter()
        now_ms = int(time.time() * 1000)
        elapsed = max(0.0, now_monotonic - self._runtime_gate_current_since_monotonic)
        if self._runtime_gate_current_allowed:
            self._runtime_gate_allowed_seconds += elapsed
        else:
            self._runtime_gate_blocked_seconds += elapsed
        self._record_session_gate_transition(
            snapshot=snapshot,
            now_monotonic=now_monotonic,
            now_ms=now_ms,
        )
        self._runtime_gate_current_since_monotonic = now_monotonic
        self._runtime_gate_current_allowed = bool(snapshot.get("new_entries_allowed"))
        transition_key = self._transition_key(snapshot, (
            "reason",
            "new_entries_allowed",
            "market_data_ready",
            "decision_latency_ready",
            "artifact_writer_ready",
            "exchange_boundary_ready",
            "position_supervisor_ready",
            "execution_ready",
            "user_data_stream_ready",
        ))
        self._runtime_gate_transition_counts[transition_key] += 1

    def _record_session_gate_transition(
        self,
        *,
        snapshot: dict[str, object],
        now_monotonic: float,
        now_ms: int,
    ) -> None:
        self._ensure_session_gate_window(now_monotonic=now_monotonic, now_ms=now_ms)
        elapsed = max(0.0, now_monotonic - self._session_gate_current_since_monotonic)
        if self._session_gate_current_allowed:
            self._session_gate_allowed_seconds += elapsed
        else:
            self._session_gate_blocked_seconds += elapsed
        self._session_gate_current_since_monotonic = now_monotonic
        self._session_gate_current_allowed = bool(snapshot.get("new_entries_allowed"))

    def _ensure_session_gate_window(self, *, now_monotonic: float, now_ms: int) -> None:
        session = live2_session_metric_window_ms(int(now_ms))
        metric_start_ms = int(session["metric_start_ms"])
        if self._session_gate_metric_start_ms == metric_start_ms:
            return
        self._session_gate_metric_start_ms = metric_start_ms
        self._session_gate_allowed_seconds = 0.0
        self._session_gate_blocked_seconds = 0.0
        self._session_gate_current_allowed = self._runtime_gate_current_allowed
        self._session_gate_current_since_monotonic = now_monotonic

    def _runtime_gate_seconds_snapshot(self) -> dict[str, object]:
        now_monotonic = time.perf_counter()
        current_elapsed = max(0.0, now_monotonic - self._runtime_gate_current_since_monotonic)
        allowed = self._runtime_gate_allowed_seconds
        blocked = self._runtime_gate_blocked_seconds
        if self._runtime_gate_current_allowed:
            allowed += current_elapsed
        else:
            blocked += current_elapsed
        return {
            "allowed_seconds": round(allowed, 3),
            "blocked_seconds": round(blocked, 3),
            "current_allowed": self._runtime_gate_current_allowed,
            "current_state_seconds": round(current_elapsed, 3),
        }

    def _session_gate_seconds_snapshot(self) -> dict[str, object]:
        now_monotonic = time.perf_counter()
        now_ms = int(time.time() * 1000)
        self._ensure_session_gate_window(now_monotonic=now_monotonic, now_ms=now_ms)
        current_elapsed = max(0.0, now_monotonic - self._session_gate_current_since_monotonic)
        allowed = self._session_gate_allowed_seconds
        blocked = self._session_gate_blocked_seconds
        if self._session_gate_current_allowed:
            allowed += current_elapsed
        else:
            blocked += current_elapsed
        return {
            "metric_start_ms": self._session_gate_metric_start_ms,
            "allowed_seconds": round(allowed, 3),
            "blocked_seconds": round(blocked, 3),
            "current_allowed": self._session_gate_current_allowed,
            "current_state_seconds": round(current_elapsed, 3),
        }

    def _session_scoped_grid_status(
        self,
        *,
        decision_status: dict[str, object],
        execution_status: dict[str, object],
        runtime_gate_status: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        now_ms = int(time.time() * 1000)
        session = live2_session_metric_window_ms(now_ms)
        metric_start_ms = int(session["metric_start_ms"])
        if self._session_counter_metric_start_ms != metric_start_ms:
            first_session_window = self._session_counter_metric_start_ms is None
            self._session_counter_metric_start_ms = metric_start_ms
            self._session_decision_baseline = {} if first_session_window else _counter_baseline(
                decision_status,
                (
                    "total_decisions",
                    "selected_count",
                    "total_rejected",
                    "total_data_not_ready",
                    "total_data_dependency_not_ready",
                    "total_deadline_missed",
                    "total_deadline_expired_backlog",
                    "total_pre_live_bucket_skipped",
                ),
            )
            self._session_execution_baseline = {} if first_session_window else _counter_baseline(
                execution_status,
                (
                    "total_orders_submitted",
                    "total_positions_protected",
                    "total_integrity_errors",
                    "total_exchange_errors",
                    "total_rejected_capacity",
                    "total_rejected_existing_position",
                ),
            )
            self._session_runtime_baseline = {} if first_session_window else _counter_baseline(
                runtime_gate_status,
                (
                    "decision_loop_overrun_count",
                    "decision_loop_max_elapsed_ms",
                    "market_data_clean_windows",
                    "market_data_degraded_windows",
                ),
            )
        session_decision_status = _subtract_counter_baseline(
            decision_status,
            self._session_decision_baseline,
        )
        session_execution_status = _subtract_counter_baseline(
            execution_status,
            self._session_execution_baseline,
        )
        session_runtime_gate_status = _subtract_counter_baseline(
            runtime_gate_status,
            self._session_runtime_baseline,
            max_keys={"decision_loop_max_elapsed_ms"},
        )
        session_decision_status["counter_scope"] = "session"
        session_decision_status["session_metric_start_ms"] = metric_start_ms
        session_execution_status["counter_scope"] = "session"
        session_execution_status["session_metric_start_ms"] = metric_start_ms
        session_runtime_gate_status["counter_scope"] = "session"
        session_runtime_gate_status["session_metric_start_ms"] = metric_start_ms
        return session_decision_status, session_execution_status, session_runtime_gate_status

    def _diagnostics_summary(
        self,
        *,
        writer: Live2ArtifactWriter,
        market_data_status: dict[str, object],
        decision_status: dict[str, object],
        execution_status: dict[str, object],
        runtime_gate_status: dict[str, object],
    ) -> dict[str, object]:
        ws_health = market_data_status.get("ws_health")
        ws_health_dict = ws_health if isinstance(ws_health, dict) else {}
        return {
            "updated_at_utc": utc_now_iso(),
            "updated_at_ms": int(time.time() * 1000),
            "event_policy": {
                "market_data_coverage": "transition_only",
                "market_data_counter_windows": "status_only_not_event_dedup_keys",
            },
            "market_data": {
                "current_transition": self._last_market_data_coverage_snapshot or {},
                "transition_counts": dict(self._market_data_transition_counts),
                "status": market_data_status.get("status"),
                "reason": market_data_status.get("reason"),
                "stream_coverage_ready": bool(market_data_status.get("stream_coverage_ready")),
                "entry_stream_ready": bool(market_data_status.get("entry_stream_ready")),
                "market_data_ready_for_entries": bool(market_data_status.get("market_data_ready_for_entries")),
                "clean_windows": int(market_data_status.get("market_data_clean_windows") or 0),
                "degraded_windows": int(market_data_status.get("market_data_degraded_windows") or 0),
                "open_interest": market_data_status.get("open_interest"),
                "open_interest_status_counts": market_data_status.get("open_interest_status_counts"),
                "prior_context": market_data_status.get("prior_context"),
                "prior_context_status_counts": market_data_status.get("prior_context_status_counts"),
                "startup_context_prewarm": market_data_status.get("startup_context_prewarm"),
                "ws_reconnect_summary": {
                    "reconnect_attempts": int(ws_health_dict.get("reconnect_attempts") or 0),
                    "disconnect_count": int(ws_health_dict.get("disconnect_count") or 0),
                    "payload_errors": int(ws_health_dict.get("payload_errors") or 0),
                    "planned_rotation_count": int(ws_health_dict.get("planned_rotation_count") or 0),
                    "ticker_reconnect_attempts": int(ws_health_dict.get("ticker_reconnect_attempts") or 0),
                    "ticker_disconnect_count": int(ws_health_dict.get("ticker_disconnect_count") or 0),
                    "ticker_last_message_age_ms": ws_health_dict.get("ticker_last_message_age_ms"),
                    "aggtrade_reconnect_attempts": int(ws_health_dict.get("aggtrade_reconnect_attempts") or 0),
                    "aggtrade_disconnect_count": int(ws_health_dict.get("aggtrade_disconnect_count") or 0),
                    "aggtrade_last_message_age_ms": ws_health_dict.get("aggtrade_last_message_age_ms"),
                    "mark_price_reconnect_attempts": int(ws_health_dict.get("mark_price_reconnect_attempts") or 0),
                    "mark_price_disconnect_count": int(ws_health_dict.get("mark_price_disconnect_count") or 0),
                    "mark_price_last_message_age_ms": ws_health_dict.get("mark_price_last_message_age_ms"),
                    "mark_price_ready": bool(ws_health_dict.get("mark_price_ready")),
                    "mark_price_connection_status": str(ws_health_dict.get("mark_price_connection_status", "unknown")),
                    "aggtrade_pre_first_payload_failures": int(ws_health_dict.get("aggtrade_pre_first_payload_failures") or 0),
                    "shards_total": int(ws_health_dict.get("shards_total") or 0),
                    "shards_connected": int(ws_health_dict.get("shards_connected") or 0),
                    "shards_stale": int(ws_health_dict.get("shards_stale") or 0),
                },
            },
            "market_runtime_seconds": round(self._market_runtime_seconds(), 3),
            "session_top": self._last_session_top_snapshot or {},
            "top_growth_audit": self._last_top_growth_audit_stats.as_dict(),
            "runtime_gate": {
                "status": runtime_gate_status.get("status"),
                "reason": runtime_gate_status.get("reason"),
                "transition_counts": dict(self._runtime_gate_transition_counts),
                "seconds": self._runtime_gate_seconds_snapshot(),
                "session_seconds": self._session_gate_seconds_snapshot(),
            },
            "decision_funnel": {
                "total_decisions": int(decision_status.get("total_decisions") or 0),
                "selected_count": int(decision_status.get("selected_count") or 0),
                "total_rejected": int(decision_status.get("total_rejected") or 0),
                "total_data_not_ready": int(decision_status.get("total_data_not_ready") or 0),
                "total_data_dependency_not_ready": int(decision_status.get("total_data_dependency_not_ready") or 0),
                "total_deadline_missed": int(decision_status.get("total_deadline_missed") or 0),
                "total_deadline_expired_backlog": int(decision_status.get("total_deadline_expired_backlog") or 0),
                "total_pre_live_bucket_skipped": int(decision_status.get("total_pre_live_bucket_skipped") or 0),
                "signal_dependency_funnel": _dict_or_empty(decision_status.get("signal_engine")).get("dependency_reason_counts", {}),
                "signal_reject_funnel": _dict_or_empty(decision_status.get("signal_engine")).get("reject_reason_counts", {}),
            },
            "execution_funnel": {
                "open_protected_positions": int(execution_status.get("open_protected_positions") or 0),
                "total_orders_submitted": int(execution_status.get("total_orders_submitted") or 0),
                "total_positions_protected": int(execution_status.get("total_positions_protected") or 0),
                "total_integrity_errors": int(execution_status.get("total_integrity_errors") or 0),
            },
            "private_user_data_stream": self._user_data_stream_status(),
            "artifact_writer": writer.status().as_dict(),
        }

    @staticmethod
    def _transition_key(snapshot: dict[str, object], fields: tuple[str, ...]) -> str:
        return "|".join(f"{field}={snapshot.get(field)}" for field in fields)

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
            reasons.append("entry_stream_not_ready")
        if not self.readiness.decision_latency_ready:
            reasons.append("decision_latency_degraded")
        if not self.readiness.exchange_boundary_ready:
            reasons.append("exchange_boundary_not_ready")
        if not self.readiness.position_supervisor_ready:
            reasons.append("position_supervisor_not_ready")
        if not self.readiness.execution_ready:
            reasons.append("execution_not_ready")
        if not self.readiness.user_data_stream_ready:
            reasons.append("user_data_stream_not_ready")
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
            "session_seconds": self._session_gate_seconds_snapshot(),
            "position_supervisor_status": self.position_supervisor.status(),
            "user_data_stream_status": self._user_data_stream_status(),
            "startup_ticker_snapshot": None
            if self.startup_ticker_snapshot_result is None
            else self.startup_ticker_snapshot_result.as_dict(),
            "startup_warmup": None if self.startup_warmup_result is None else self.startup_warmup_result.as_dict(),
            "startup_htf_baseline": None if self.startup_htf_baseline_result is None else self.startup_htf_baseline_result.as_dict(),
            "startup_context_prewarm": self.startup_context_prewarm_result,
        }

    def _execution_status(self) -> dict[str, object]:
        return {
            **self.execution_engine.status(),
            "position_supervisor": self.position_supervisor.status(),
            "user_data_stream": self._user_data_stream_status(),
        }

    def _user_data_stream_status(self) -> dict[str, object]:
        if self.user_data_source is None:
            return {
                "source_id": Live2UserDataStreamSource.source_id,
                "status": "not_started",
                "ready": False,
                "reason": "execution_preflight_not_ready_or_user_data_source_not_started",
                "endpoint_category": Live2UserDataStreamSource.endpoint_category,
                "websocket_url_redacted": Live2UserDataStreamSource.websocket_url_redacted,
            }
        return self.user_data_source.status().as_dict()

    def _write_user_data_stream_starting_event(self, writer: Live2ArtifactWriter) -> None:
        writer.write_event(
            Live2Event(
                event_type="user_data_stream_starting",
                component=Live2Component.EXECUTION,
                severity=Live2Severity.INFO,
                message="starting Binance futures private user-data WS",
                data={
                    "source": Live2UserDataStreamSource.source_id,
                    "endpoint_category": Live2UserDataStreamSource.endpoint_category,
                    "websocket_url_redacted": Live2UserDataStreamSource.websocket_url_redacted,
                    "startup_wait_seconds": self.config.user_data_stream_startup_wait_seconds,
                    "keepalive_interval_seconds": self.config.user_data_stream_keepalive_interval_seconds,
                    "reconnect_initial_delay_seconds": self.config.ws_reconnect_initial_delay_seconds,
                    "reconnect_max_delay_seconds": self.config.ws_reconnect_max_delay_seconds,
                    "connection_max_age_seconds": self.config.ws_connection_max_age_seconds,
                    "raw_listen_key_in_artifacts": False,
                    "trading_mode": "real_orders_always_enabled",
                },
            )
        )

    def _select_universe(self) -> Live2UniverseSelection:
        selector = Live2UniverseSelector(
            state_store=self.state_store,
            explicit_symbols=self.config.symbols,
            max_symbols=self.config.universe_max_symbols,
            min_quote_volume_24h=self.config.universe_min_quote_volume_24h,
            min_trade_count_24h=self.config.universe_min_trade_count_24h,
        )
        return selector.select()

    def _selected_symbols_tuple(self) -> tuple[str, ...]:
        if self.universe_selection is None:
            return ()
        return tuple(self.universe_selection.selected_symbols)

    def _kick_top_growth_audit(self, *, symbols: tuple[str, ...], now_ms: int) -> None:
        with self._top_growth_audit_lock:
            if self._top_growth_audit_worker is not None and self._top_growth_audit_worker.is_alive():
                return

            def run_worker() -> None:
                stats = self.top_growth_audit.process_due(symbols=symbols, now_ms=now_ms)
                with self._top_growth_audit_lock:
                    self._last_top_growth_audit_stats = stats
                    if stats.status == "completed":
                        self._top_growth_completed_events.append(stats.as_dict())

            self._last_top_growth_audit_stats = Live2TopGrowthAuditStats(
                enabled=self.config.top_growth_enabled,
                status="scheduled",
                reason="background_worker_started",
                period_start_ms=self._last_top_growth_audit_stats.period_start_ms,
                period_end_ms=self._last_top_growth_audit_stats.period_end_ms,
                processed_count=self._last_top_growth_audit_stats.processed_count,
                remaining_count=self._last_top_growth_audit_stats.remaining_count,
                symbols_total=self._last_top_growth_audit_stats.symbols_total,
                top_count=self._last_top_growth_audit_stats.top_count,
            )
            self._top_growth_audit_worker = threading.Thread(
                target=run_worker,
                name="live2-top-growth-audit",
                daemon=True,
            )
            self._top_growth_audit_worker.start()

    def _drain_top_growth_completed_events(self) -> list[dict[str, object]]:
        with self._top_growth_audit_lock:
            events = list(self._top_growth_completed_events)
            self._top_growth_completed_events.clear()
            return events

    def _market_data_status(self, *, include_symbol_counts: bool = True) -> dict[str, object]:
        ticker_status = self.ticker_source.status().as_dict(stale_ms=self.config.ticker_stale_ms)
        aggtrade_status = self._aggtrade_status()
        mark_price_status = self._mark_price_status()
        open_interest_status = (
            self._open_interest_status()
            if include_symbol_counts
            else {
                "source_id": Live2OpenInterestPoller.source_id,
                "status": "omitted_on_fast_runtime_gate_path",
                "ready": True,
                "reason": "not_required_for_stream_coverage_gate",
            }
        )
        prior_context_status = (
            self._prior_context_status()
            if include_symbol_counts
            else {
                "source_id": Live2PriorContextPoller.source_id,
                "status": "omitted_on_fast_runtime_gate_path",
                "ready": True,
                "reason": "not_required_for_stream_coverage_gate",
            }
        )
        ticker_ready = bool(ticker_status.get("ready"))
        aggtrade_ready = bool(aggtrade_status.get("ready"))
        mark_price_ready = bool(mark_price_status.get("ready"))
        stream_coverage_ready = ticker_ready and aggtrade_ready and mark_price_ready
        selected_symbols = 0 if self.universe_selection is None else len(self.universe_selection.selected_symbols)
        live_decision_watermark_ms = self._live_decision_watermark_ms_from_status(aggtrade_status)
        entry_stream_ready = selected_symbols > 0 and aggtrade_ready and live_decision_watermark_ms is not None
        ws_health = self._ws_health_status(
            ticker_status=ticker_status,
            aggtrade_status=aggtrade_status,
            mark_price_status=mark_price_status,
        )
        reasons: list[str] = []
        if selected_symbols <= 0:
            reasons.append("startup_ticker_universe_selection_empty")
        if not ticker_ready:
            reasons.append("ticker_ws_not_ready")
        if not aggtrade_ready:
            reasons.append("aggtrade_ws_not_ready")
        if not mark_price_ready:
            reasons.append("mark_price_ws_not_ready")
        if stream_coverage_ready:
            status = "stream_coverage_ready_signal_adapter_active"
            reason = "ticker_aggtrade_and_mark_price_ws_ready_signal_adapter_active_execution_boundary_active"
        elif entry_stream_ready:
            status = "entry_stream_ready_symbol_dependencies_checked_per_candidate"
            reason = (
                "aggtrade_ws_ready_for_fresh_buckets; "
                "ticker_and_mark_price_global_stream_health_is_diagnostic; "
                "per_symbol_mark_oi_prior_context_dependencies_still_gate_entries"
            )
        elif selected_symbols <= 0:
            status = "no_startup_universe"
            reason = "+".join(reasons)
        else:
            status = "stream_coverage_not_ready"
            reason = "+".join(reasons) if reasons else "stream_coverage_not_ready"
        symbol_counts: dict[str, object] = {"included": False, "reason": "omitted_on_fast_runtime_gate_path"}
        if include_symbol_counts:
            now_ms = int(time.time() * 1000)
            session = live2_session_metric_window_ms(now_ms)
            metric_start_ms = int(session["metric_start_ms"])
            symbol_counts = {
                "included": True,
                "actionable_symbol_counts": self.state_store.actionable_symbol_counts(
                    now_ms=now_ms,
                    ttl_ms=self.config.oi_radar_symbol_ttl_ms,
                    session_start_ms=metric_start_ms,
                ),
                "stage_symbol_counts": self.state_store.stage_symbol_counts(
                    now_ms=now_ms,
                    ttl_ms=self.config.oi_radar_symbol_ttl_ms,
                    session_start_ms=metric_start_ms,
                ),
                "aggtrade_status_counts": self.state_store.aggtrade_counts(now_ms=now_ms, stale_ms=self.config.aggtrade_stale_ms),
                "startup_aggtrade_status_counts": self.state_store.startup_aggtrade_counts(),
                "live_aggtrade_status_counts": self.state_store.live_aggtrade_counts(now_ms=now_ms, stale_ms=self.config.aggtrade_stale_ms),
                "mark_price_status_counts": self.state_store.mark_counts(),
                "open_interest_status_counts": self.state_store.open_interest_counts(now_ms=now_ms, stale_ms=self.config.oi_stale_ms),
                "prior_context_status_counts": self.state_store.prior_context_counts(now_ms=now_ms, stale_ms=self.config.prior_context_stale_ms),
                "candle_coverage_counts": self.state_store.candle_coverage_counts(),
            }
        return {
            "status": status,
            "reason": reason,
            "ticker_ws": ticker_status,
            "aggtrade_ws": aggtrade_status,
            "mark_price_ws": mark_price_status,
            "open_interest": open_interest_status,
            "prior_context": prior_context_status,
            "live_decision_watermark_ms": live_decision_watermark_ms,
            "symbol_counts_included": bool(symbol_counts.get("included")),
            "actionable_symbol_counts": symbol_counts.get("actionable_symbol_counts", {}),
            "stage_symbol_counts": symbol_counts.get("stage_symbol_counts", {}),
            "aggtrade_status_counts": symbol_counts.get("aggtrade_status_counts", {}),
            "startup_aggtrade_status_counts": symbol_counts.get("startup_aggtrade_status_counts", {}),
            "live_aggtrade_status_counts": symbol_counts.get("live_aggtrade_status_counts", {}),
            "mark_price_status_counts": symbol_counts.get("mark_price_status_counts", {}),
            "open_interest_status_counts": symbol_counts.get("open_interest_status_counts", {}),
            "prior_context_status_counts": symbol_counts.get("prior_context_status_counts", {}),
            "candle_coverage_counts": symbol_counts.get("candle_coverage_counts", {}),
            "universe": self._universe_status(),
            "ws_health": ws_health,
            "stream_coverage_ready": stream_coverage_ready,
            "entry_stream_ready": entry_stream_ready,
            "readiness_policy": (
                "global_aggtrade_watermark_required; "
                "ticker_mark_global_health_diagnostic_only; "
                "per_symbol_mark_oi_prior_context_stale_checks_gate_signal_categories"
            ),
            "market_data_ready_for_entries": self._market_data_ready_gate,
            "market_data_clean_windows": self._market_data_clean_windows,
            "market_data_degraded_windows": self._market_data_degraded_windows,
            "market_data_recovery_windows": self.config.market_data_recovery_windows,
            "startup_ticker_snapshot": None
            if self.startup_ticker_snapshot_result is None
            else self.startup_ticker_snapshot_result.as_dict(),
            "startup_warmup": None if self.startup_warmup_result is None else self.startup_warmup_result.as_dict(),
            "startup_htf_baseline": None if self.startup_htf_baseline_result is None else self.startup_htf_baseline_result.as_dict(),
            "startup_context_prewarm": self.startup_context_prewarm_result,
        }

    def _ws_health_status(
        self,
        *,
        ticker_status: dict[str, object],
        aggtrade_status: dict[str, object],
        mark_price_status: dict[str, object],
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
        mark_reconnects = int(mark_price_status.get("reconnect_attempts") or 0)
        mark_disconnects = int(mark_price_status.get("disconnect_count") or 0)
        payload_errors = (
            int(ticker_status.get("payload_errors") or 0)
            + int(aggtrade_status.get("payload_errors") or 0)
            + int(mark_price_status.get("payload_errors") or 0)
        )
        planned_rotation_count = (
            int(ticker_status.get("planned_rotation_count") or 0)
            + int(aggtrade_status.get("planned_rotation_count") or 0)
            + int(mark_price_status.get("planned_rotation_count") or 0)
        )
        pre_first_payload_failures = 0
        if isinstance(agg_shards, list):
            for shard in agg_shards:
                if isinstance(shard, dict):
                    pre_first_payload_failures += int(shard.get("consecutive_pre_first_payload_failures") or 0)
        return {
            "ticker_ready": bool(ticker_status.get("ready")),
            "aggtrade_ready": bool(aggtrade_status.get("ready")),
            "mark_price_ready": bool(mark_price_status.get("ready")),
            "ticker_last_message_age_ms": ticker_status.get("last_message_age_ms"),
            "aggtrade_last_message_age_ms": aggtrade_status.get("last_message_age_ms"),
            "mark_price_last_message_age_ms": mark_price_status.get("last_message_age_ms"),
            "ticker_reconnect_attempts": ticker_reconnects,
            "ticker_disconnect_count": ticker_disconnects,
            "aggtrade_reconnect_attempts": agg_reconnects,
            "aggtrade_disconnect_count": agg_disconnects,
            "mark_price_reconnect_attempts": mark_reconnects,
            "mark_price_disconnect_count": mark_disconnects,
            "ticker_connection_status": str(ticker_status.get("connection_status", "unknown")),
            "aggtrade_source_status": str(aggtrade_status.get("source_status", "unknown")),
            "aggtrade_endpoint_category": str(aggtrade_status.get("endpoint_category", "unknown")),
            "mark_price_connection_status": str(mark_price_status.get("connection_status", "unknown")),
            "mark_price_endpoint_category": str(mark_price_status.get("endpoint_category", "unknown")),
            "aggtrade_pre_first_payload_failures": pre_first_payload_failures,
            "live_decision_watermark_ms": self._live_decision_watermark_ms_from_status(aggtrade_status),
            "shards_total": int(aggtrade_status.get("shards_total") or 0),
            "shards_connected": int(aggtrade_status.get("shards_connected") or 0),
            "shards_disconnected": disconnected_shards,
            "shards_stale": stale_shards,
            "reconnect_attempts": ticker_reconnects + agg_reconnects + mark_reconnects,
            "disconnect_count": ticker_disconnects + agg_disconnects + mark_disconnects,
            "planned_rotation_count": planned_rotation_count,
            "payload_errors": payload_errors,
        }

    def _live_decision_watermark_ms(self) -> int | None:
        if self.aggtrade_source is None:
            return None
        return self._live_decision_watermark_ms_from_status(
            self.aggtrade_source.status().as_dict(stale_ms=self.config.aggtrade_stale_ms)
        )

    def _live_decision_watermark_ms_from_status(self, aggtrade_status: dict[str, object]) -> int | None:
        if not bool(aggtrade_status.get("ready")):
            return None
        shards = aggtrade_status.get("shards")
        if not isinstance(shards, list) or not shards:
            return None
        first_payload_times: list[int] = []
        for shard in shards:
            if not isinstance(shard, dict):
                return None
            first_message_at_ms = shard.get("first_message_at_ms")
            if not isinstance(first_message_at_ms, int):
                return None
            first_payload_times.append(first_message_at_ms)
        if len(first_payload_times) != len(shards):
            return None
        return max(first_payload_times)

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

    def _mark_price_status(self) -> dict[str, object]:
        if self.mark_price_source is None:
            return {
                "connection_status": "not_started",
                "ready": False,
                "endpoint_category": Live2MarkPriceWsSource.endpoint_category,
                "websocket_url": Live2MarkPriceWsSource.websocket_url,
                "tracked_symbols": 0,
                "reason": "startup_universe_not_selected_or_empty",
            }
        return self.mark_price_source.status().as_dict(stale_ms=self.config.mark_price_stale_ms)

    def _open_interest_status(self) -> dict[str, object]:
        if self.open_interest_source is None:
            return {
                "source_id": Live2OpenInterestPoller.source_id,
                "status": "not_started",
                "ready": False,
                "reason": "startup_universe_not_selected_or_empty",
                "active_target_symbols": 0,
                "ready_symbols": 0,
                "tracked_symbols": 0,
            }
        return self.open_interest_source.status()

    def _prior_context_status(self) -> dict[str, object]:
        if self.prior_context_source is None:
            return {
                "source_id": Live2PriorContextPoller.source_id,
                "status": "not_started",
                "ready": False,
                "reason": "startup_universe_not_selected_or_empty",
                "active_target_symbols": 0,
                "ready_symbols": 0,
                "tracked_symbols": 0,
                "lookback_hours": self.config.prior_context_lookback_hours,
            }
        return self.prior_context_source.status()

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
                    "universe_min_auto_symbols": self.config.universe_min_auto_symbols,
                    "decision_timeframe_ms": self.config.decision_timeframe_ms,
                    "decision_deadline_ms": self.config.decision_deadline_ms,
                    "decision_backlog_expire_ms": self.config.decision_backlog_expire_ms,
                    "top_growth_enabled": self.config.top_growth_enabled,
                    "top_growth_min_return_pct": self.config.top_growth_min_return_pct,
                    "top_growth_limit": self.config.top_growth_limit,
                    "top_growth_symbols_per_cycle": self.config.top_growth_symbols_per_cycle,
                    "top_growth_max_cycle_seconds": self.config.top_growth_max_cycle_seconds,
                    "market_data_recovery_windows": self.config.market_data_recovery_windows,
                    "startup_warmup_lookback_minutes": self.config.startup_warmup_lookback_minutes,
                    "startup_warmup_max_trades_per_symbol": self.config.startup_warmup_max_trades_per_symbol,
                    "startup_warmup_max_pages_per_symbol": self.config.startup_warmup_max_pages_per_symbol,
                    "startup_htf_baseline_lookback_minutes": self.config.startup_htf_baseline_lookback_minutes,
                    "max_closed_candles_per_timeframe": self.config.max_closed_candles_per_timeframe,
                    "ws_reconnect_initial_delay_seconds": self.config.ws_reconnect_initial_delay_seconds,
                    "ws_reconnect_max_delay_seconds": self.config.ws_reconnect_max_delay_seconds,
                    "ws_connection_max_age_seconds": self.config.ws_connection_max_age_seconds,
                    "user_data_stream_startup_wait_seconds": self.config.user_data_stream_startup_wait_seconds,
                    "user_data_stream_keepalive_interval_seconds": self.config.user_data_stream_keepalive_interval_seconds,
                    "mark_price_stale_ms": self.config.mark_price_stale_ms,
                    "mark_price_startup_wait_seconds": self.config.mark_price_startup_wait_seconds,
                    "oi_stale_ms": self.config.oi_stale_ms,
                    "oi_poll_interval_seconds": self.config.oi_poll_interval_seconds,
                    "oi_symbol_cooldown_seconds": self.config.oi_symbol_cooldown_seconds,
                    "oi_lookback_minutes": self.config.oi_lookback_minutes,
                    "oi_max_symbols_per_cycle": self.config.oi_max_symbols_per_cycle,
                    "oi_radar_symbol_ttl_ms": self.config.oi_radar_symbol_ttl_ms,
                    "prior_context_stale_ms": self.config.prior_context_stale_ms,
                    "prior_context_poll_interval_seconds": self.config.prior_context_poll_interval_seconds,
                    "prior_context_symbol_cooldown_seconds": self.config.prior_context_symbol_cooldown_seconds,
                    "prior_context_lookback_hours": self.config.prior_context_lookback_hours,
                    "prior_context_max_symbols_per_cycle": self.config.prior_context_max_symbols_per_cycle,
                    "prior_context_radar_symbol_ttl_ms": self.config.prior_context_radar_symbol_ttl_ms,
                    "prior_context_spike_return_pct": self.config.prior_context_spike_return_pct,
                    "prior_context_fast_fade_retrace_fraction": self.config.prior_context_fast_fade_retrace_fraction,
                    "startup_context_prewarm_request_sleep_seconds": self.config.startup_context_prewarm_request_sleep_seconds,
                    "startup_context_prewarm_error_limit": self.config.startup_context_prewarm_error_limit,
                    "startup_context_prewarm_scope": "selected_universe_before_runtime_entries",
                    "execution_order_placement": "verified_fill_and_initial_stop_lifecycle_enabled",
                    "live2_trading_mode": "real_orders_always_enabled",
                    "dry_run_supported": False,
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


    def _run_startup_ticker_snapshot(self, writer: Live2ArtifactWriter) -> Live2StartupTickerSnapshotResult:
        writer.write_event(
            Live2Event(
                event_type="startup_ticker_snapshot_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO,
                message="hydrating live2 startup universe from one exchange ticker/liquidity snapshot",
                data={
                    "source": "exchange_startup_fetch_tickers",
                    "hot_path_available": False,
                    "universe_max_symbols": self.config.universe_max_symbols,
                    "universe_min_quote_volume_24h": self.config.universe_min_quote_volume_24h,
                    "universe_min_auto_symbols": self.config.universe_min_auto_symbols,
                },
            )
        )
        result = Live2StartupTickerSnapshot(
            state_store=self.state_store,
            exchange_client=self.execution_engine.exchange_client,
        ).run()
        writer.write_event(result.as_event())
        return result


    def _run_startup_aggtrade_warmup(self, writer: Live2ArtifactWriter) -> Live2StartupWarmupResult:
        selected_symbols = () if self.universe_selection is None else self.universe_selection.selected_symbols
        writer.write_event(
            Live2Event(
                event_type="startup_aggtrade_warmup_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO,
                message="hydrating live2 in-memory candle rings from startup-only Binance aggTrades REST",
                data={
                    "symbols_requested": len(selected_symbols),
                    "lookback_minutes": self.config.startup_warmup_lookback_minutes,
                    "max_trades_per_symbol": self.config.startup_warmup_max_trades_per_symbol,
                    "max_pages_per_symbol": self.config.startup_warmup_max_pages_per_symbol,
                    "hot_path_available": False,
                    "source": "binance_futures_aggTrades_startup_rest",
                },
            )
        )
        result = Live2StartupAggTradeWarmup(
            state_store=self.state_store,
            exchange_client=self.execution_engine.exchange_client,
            config=Live2StartupWarmupConfig(
                enabled=True,
                lookback_minutes=self.config.startup_warmup_lookback_minutes,
                max_symbols=min(self.config.universe_max_symbols, len(selected_symbols)) if selected_symbols else 1,
                max_trades_per_symbol=self.config.startup_warmup_max_trades_per_symbol,
                max_pages_per_symbol=self.config.startup_warmup_max_pages_per_symbol,
                request_sleep_seconds=self.config.startup_warmup_request_sleep_seconds,
                error_limit=self.config.startup_warmup_error_limit,
            ),
        ).run(selected_symbols, progress=self._set_warmup_progress_status)
        writer.write_event(result.as_event())
        return result

    def _run_startup_htf_baseline_warmup(self, writer: Live2ArtifactWriter) -> Live2StartupHtfBaselineResult:
        selected_symbols = () if self.universe_selection is None else self.universe_selection.selected_symbols
        writer.write_event(
            Live2Event(
                event_type="startup_htf_baseline_warmup_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO,
                message="hydrating live2 rolling HTF baseline from startup-only Binance 1m klines",
                data={
                    "symbols_requested": len(selected_symbols),
                    "lookback_minutes": self.config.startup_htf_baseline_lookback_minutes,
                    "hot_path_available": False,
                    "source": "binance_futures_klines_startup_rest_1m_htf_baseline",
                },
            )
        )
        result = Live2StartupHtfBaselineWarmup(
            state_store=self.state_store,
            exchange_client=self.execution_engine.exchange_client,
            config=Live2StartupHtfBaselineConfig(
                enabled=True,
                lookback_minutes=self.config.startup_htf_baseline_lookback_minutes,
                max_symbols=min(self.config.universe_max_symbols, len(selected_symbols)) if selected_symbols else 1,
                request_sleep_seconds=self.config.startup_htf_baseline_request_sleep_seconds,
                error_limit=self.config.startup_htf_baseline_error_limit,
            ),
        ).run(selected_symbols)
        writer.write_event(result.as_event())
        return result

    def _run_startup_context_prewarm(self, writer: Live2ArtifactWriter) -> dict[str, object]:
        selected_symbols = () if self.universe_selection is None else self.universe_selection.selected_symbols
        if self.open_interest_source is None or self.prior_context_source is None:
            return {
                "status": "not_started",
                "reason": "context_sources_not_initialized",
                "symbols_requested": len(selected_symbols),
            }
        writer.write_event(
            Live2Event(
                event_type="startup_context_prewarm_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO,
                message="prewarming OI and 24h prior context for the selected universe before runtime entries",
                data={
                    "symbols_requested": len(selected_symbols),
                    "scope": "selected_universe",
                    "hot_path_available": False,
                    "silent_zero_fallback": False,
                    "request_sleep_seconds": self.config.startup_context_prewarm_request_sleep_seconds,
                    "error_limit": self.config.startup_context_prewarm_error_limit,
                },
            )
        )
        self._set_startup_status("OI prewarm", f"0/{len(selected_symbols)}")
        oi_summary = self.open_interest_source.poll_symbols_once(
            selected_symbols,
            request_sleep_seconds=self.config.startup_context_prewarm_request_sleep_seconds,
            error_limit=self.config.startup_context_prewarm_error_limit,
            progress=self._set_oi_prewarm_progress_status,
        )
        writer.write_event(
            Live2Event(
                event_type="startup_open_interest_prewarm_completed",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if bool(oi_summary.get("ready")) else Live2Severity.WARNING,
                message="startup open-interest prewarm completed",
                data=oi_summary,
            )
        )
        self._set_startup_status("24h prewarm", f"0/{len(selected_symbols)}")
        prior_summary = self.prior_context_source.poll_symbols_once(
            selected_symbols,
            request_sleep_seconds=self.config.startup_context_prewarm_request_sleep_seconds,
            error_limit=self.config.startup_context_prewarm_error_limit,
            progress=self._set_prior_prewarm_progress_status,
        )
        writer.write_event(
            Live2Event(
                event_type="startup_prior_context_prewarm_completed",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if bool(prior_summary.get("ready")) else Live2Severity.WARNING,
                message="startup 24h prior-context prewarm completed",
                data=prior_summary,
            )
        )
        result = {
            "status": "completed",
            "scope": "selected_universe",
            "symbols_requested": len(selected_symbols),
            "open_interest": oi_summary,
            "prior_context": prior_summary,
            "ready": bool(oi_summary.get("ready")) and bool(prior_summary.get("ready")),
            "reason": "startup_context_prewarm_completed",
        }
        writer.write_event(
            Live2Event(
                event_type="startup_context_prewarm_completed",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if bool(result["ready"]) else Live2Severity.WARNING,
                message="startup OI and 24h prior-context prewarm completed",
                data=result,
            )
        )
        return result

    def _set_oi_prewarm_progress_status(self, progress: dict[str, object]) -> None:
        self.status_logger.status(
            "\n".join(
                (
                    "Live2",
                    "Этап OI prewarm",
                    (
                        f"{progress.get('current_index')}/{progress.get('symbols_requested')} · "
                        f"ok {progress.get('ok')} · empty {progress.get('empty')} · errors {progress.get('error')}"
                    ),
                    f"Символ {progress.get('symbol')}",
                )
            )
        )

    def _set_prior_prewarm_progress_status(self, progress: dict[str, object]) -> None:
        self.status_logger.status(
            "\n".join(
                (
                    "Live2",
                    "Этап 24h prewarm",
                    (
                        f"{progress.get('current_index')}/{progress.get('symbols_requested')} · "
                        f"ok {progress.get('ok')} · empty {progress.get('empty')} · errors {progress.get('error')}"
                    ),
                    f"Символ {progress.get('symbol')}",
                )
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
                    "endpoint_category": Live2AggTradeWsSource.endpoint_category,
                    "combined_stream_base_url": Live2AggTradeWsSource.combined_stream_base_url,
                    "symbols_filter_count": selected_symbols,
                    "max_streams_per_connection": self.config.aggtrade_max_streams_per_connection,
                    "stale_ms": self.config.aggtrade_stale_ms,
                    "startup_wait_seconds": self.config.aggtrade_startup_wait_seconds,
                    "hot_rest_backfill": False,
                    "startup_rest_warmup": None if self.startup_warmup_result is None else self.startup_warmup_result.as_dict(),
                    "universe": self._universe_status(),
                },
            )
        )

    def _write_mark_price_starting_event(self, writer: Live2ArtifactWriter) -> None:
        selected_symbols = 0 if self.universe_selection is None else len(self.universe_selection.selected_symbols)
        writer.write_event(
            Live2Event(
                event_type="mark_price_ws_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if selected_symbols else Live2Severity.ERROR,
                message="starting Binance futures all-market markPrice WS ingestion",
                data={
                    "source": Live2MarkPriceWsSource.source_id,
                    "endpoint_category": Live2MarkPriceWsSource.endpoint_category,
                    "websocket_url": Live2MarkPriceWsSource.websocket_url,
                    "symbols_filter_count": selected_symbols,
                    "stale_ms": self.config.mark_price_stale_ms,
                    "startup_wait_seconds": self.config.mark_price_startup_wait_seconds,
                    "hot_rest_backfill": False,
                    "universe": self._universe_status(),
                },
            )
        )

    def _write_open_interest_starting_event(self, writer: Live2ArtifactWriter) -> None:
        selected_symbols = 0 if self.universe_selection is None else len(self.universe_selection.selected_symbols)
        writer.write_event(
            Live2Event(
                event_type="open_interest_poller_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if selected_symbols else Live2Severity.ERROR,
                message="starting selected-universe open-interest poller with active priority",
                data={
                    "source": Live2OpenInterestPoller.source_id,
                    "symbols_filter_count": selected_symbols,
                    "poll_scope": "selected_universe_active_priority",
                    "timeframe": "5m",
                    "change_window": "3x5m",
                    "stale_ms": self.config.oi_stale_ms,
                    "poll_interval_seconds": self.config.oi_poll_interval_seconds,
                    "symbol_cooldown_seconds": self.config.oi_symbol_cooldown_seconds,
                    "lookback_minutes": self.config.oi_lookback_minutes,
                    "max_symbols_per_cycle": self.config.oi_max_symbols_per_cycle,
                    "radar_symbol_ttl_ms": self.config.oi_radar_symbol_ttl_ms,
                    "selected_universe_refresh": True,
                    "startup_full_universe_prewarm": True,
                    "runtime_full_universe_polling": True,
                    "silent_zero_fallback": False,
                    "universe": self._universe_status(),
                },
            )
        )

    def _write_prior_context_starting_event(self, writer: Live2ArtifactWriter) -> None:
        selected_symbols = 0 if self.universe_selection is None else len(self.universe_selection.selected_symbols)
        writer.write_event(
            Live2Event(
                event_type="prior_context_poller_starting",
                component=Live2Component.MARKET_DATA,
                severity=Live2Severity.INFO if selected_symbols else Live2Severity.ERROR,
                message="starting selected-universe 24h prior-context bootstrap plus live WS 5m rolling maintenance",
                data={
                    "source": Live2PriorContextPoller.source_id,
                    "symbols_filter_count": selected_symbols,
                    "poll_scope": "selected_universe_active_priority",
                    "timeframe": "5m",
                    "lookback_hours": self.config.prior_context_lookback_hours,
                    "stale_ms": self.config.prior_context_stale_ms,
                    "poll_interval_seconds": self.config.prior_context_poll_interval_seconds,
                    "symbol_cooldown_seconds": self.config.prior_context_symbol_cooldown_seconds,
                    "max_symbols_per_cycle": self.config.prior_context_max_symbols_per_cycle,
                    "radar_symbol_ttl_ms": self.config.prior_context_radar_symbol_ttl_ms,
                    "spike_return_pct": self.config.prior_context_spike_return_pct,
                    "fast_fade_retrace_fraction": self.config.prior_context_fast_fade_retrace_fraction,
                    "startup_full_universe_prewarm": True,
                    "runtime_full_universe_polling": False,
                    "runtime_maintenance": "live_ws_closed_5m_roll_forward_after_startup_rest_bootstrap",
                    "ws_5m_gap_tolerance": {
                        "max_missing_aggtrade_ids_per_candle": LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_IDS_PER_CANDLE,
                        "max_missing_aggtrade_id_ratio": LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_ID_RATIO,
                    },
                    "silent_zero_fallback": False,
                    "legacy_category_72h_names_use_24h_live_context": True,
                    "universe": self._universe_status(),
                },
            )
        )


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _counter_baseline(source: dict[str, object], keys: tuple[str, ...]) -> dict[str, int]:
    return {key: int(source.get(key) or 0) for key in keys}


def _subtract_counter_baseline(
    source: dict[str, object],
    baseline: dict[str, int],
    *,
    max_keys: set[str] | None = None,
) -> dict[str, object]:
    result = dict(source)
    keep_as_total = max_keys or set()
    for key, base_value in baseline.items():
        if key not in result:
            continue
        value = int(result.get(key) or 0)
        if key in keep_as_total:
            continue
        result[key] = max(0, value - int(base_value))
    return result
