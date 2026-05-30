"""Configuration for anomaly live2 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class AnomalyLive2Config:
    """Minimal v0 configuration for the real-live v2 runtime skeleton."""

    output_dir: Path
    symbols: tuple[str, ...] = ()
    heartbeat_interval_seconds: float = 5.0
    decision_loop_interval_seconds: float = 0.05
    decision_latency_degraded_windows: int = 2
    decision_latency_recovery_windows: int = 3
    market_data_recovery_windows: int = 3
    ticker_stale_ms: int = 5_000
    ticker_startup_wait_seconds: float = 10.0
    aggtrade_stale_ms: int = 5_000
    aggtrade_startup_wait_seconds: float = 60.0
    aggtrade_max_streams_per_connection: int = 150
    mark_price_stale_ms: int = 5_000
    mark_price_startup_wait_seconds: float = 10.0
    oi_stale_ms: int = 720_000
    oi_poll_interval_seconds: float = 5.0
    oi_symbol_cooldown_seconds: float = 60.0
    oi_lookback_minutes: int = 20
    oi_max_symbols_per_cycle: int = 20
    oi_radar_symbol_ttl_ms: int = 60_000
    prior_context_stale_ms: int = 1_200_000
    prior_context_poll_interval_seconds: float = 10.0
    prior_context_symbol_cooldown_seconds: float = 600.0
    prior_context_lookback_hours: int = 24
    prior_context_max_symbols_per_cycle: int = 10
    prior_context_radar_symbol_ttl_ms: int = 60_000
    prior_context_spike_return_pct: float = 0.03
    prior_context_fast_fade_retrace_fraction: float = 0.55
    top_growth_enabled: bool = True
    top_growth_min_return_pct: float = 0.10
    top_growth_limit: int = 5
    top_growth_symbols_per_cycle: int = 1
    top_growth_max_cycle_seconds: float = 0.75
    top_growth_fetch_spacing_seconds: float = 0.02
    startup_context_prewarm_request_sleep_seconds: float = 0.0
    startup_context_prewarm_error_limit: int = 50
    ws_reconnect_initial_delay_seconds: float = 1.0
    ws_reconnect_max_delay_seconds: float = 60.0
    ws_connection_max_age_seconds: float = 84_600.0
    user_data_stream_startup_wait_seconds: float = 10.0
    user_data_stream_keepalive_interval_seconds: float = 1_800.0
    startup_warmup_lookback_minutes: int = 15
    startup_warmup_max_trades_per_symbol: int = 1000
    startup_warmup_max_pages_per_symbol: int = 1
    startup_warmup_request_sleep_seconds: float = 0.03
    startup_warmup_error_limit: int = 20
    startup_htf_baseline_lookback_minutes: int = 2880
    startup_htf_baseline_request_sleep_seconds: float = 0.02
    startup_htf_baseline_error_limit: int = 20
    rolling_context_maintenance_enabled: bool = True
    rolling_context_maintenance_poll_interval_seconds: float = 10.0
    rolling_context_maintenance_symbol_cooldown_seconds: float = 60.0
    rolling_context_maintenance_lookback_minutes: int = 720
    rolling_context_maintenance_max_symbols_per_cycle: int = 8
    rolling_context_maintenance_request_sleep_seconds: float = 0.02
    rolling_context_maintenance_active_symbol_ttl_ms: int = 60_000
    rolling_context_maintenance_closed_candle_lag_ms: int = 5_000
    max_closed_candles_per_timeframe: int = 3000
    universe_max_symbols: int = 600
    universe_min_quote_volume_24h: float = 30_000.0
    universe_min_trade_count_24h: int = 0
    universe_min_auto_symbols: int = 300
    decision_timeframe_ms: int = 30_000
    decision_deadline_ms: int = 1_500
    decision_backlog_expire_ms: int = 10_000
    actionable_min_quote_volume: float = 2_500.0
    actionable_min_trade_count: int = 20
    actionable_min_abs_return_pct: float = 0.003
    entry_guard_max_signal_age_ms: int = 5_000
    entry_guard_max_price_drift_pct: float = 0.004
    entry_guard_min_rr_to_tp1: float = 0.70
    artifact_writer_queue_max_size: int = 8192
    execution_order_notional_usdt: float = 12.0
    execution_risk_per_trade_pct: float = 0.02
    execution_max_total_open_risk_pct: float = 0.08
    execution_max_open_positions: int = 0
    execution_stop_visibility_attempts: int = 5
    execution_stop_visibility_sleep_seconds: float = 0.5
    execution_max_position_amount_slippage_ratio: float = 0.05
    position_supervisor_monitor_interval_ms: int = 1_000
    position_supervisor_tp1_close_fraction: float = 0.5
    position_supervisor_breakeven_stop_offset_pct: float = 0.0
    position_supervisor_flat_position_abs_epsilon: float = 1e-12
    position_supervisor_early_exit_enabled: bool = False
    position_supervisor_early_exit_min_hold_candles: int = 6
    position_supervisor_early_exit_stall_candles: int = 12
    position_supervisor_early_exit_min_mfe_r: float = 0.25
    runtime_generation: str = "live2_v0"

    def __post_init__(self) -> None:
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be > 0")
        if self.decision_loop_interval_seconds <= 0:
            raise ValueError("decision_loop_interval_seconds must be > 0")
        if self.decision_latency_degraded_windows <= 0:
            raise ValueError("decision_latency_degraded_windows must be > 0")
        if self.decision_latency_recovery_windows <= 0:
            raise ValueError("decision_latency_recovery_windows must be > 0")
        if self.market_data_recovery_windows <= 0:
            raise ValueError("market_data_recovery_windows must be > 0")
        if self.ticker_stale_ms <= 0:
            raise ValueError("ticker_stale_ms must be > 0")
        if self.ticker_startup_wait_seconds < 0:
            raise ValueError("ticker_startup_wait_seconds must be >= 0")
        if self.aggtrade_stale_ms <= 0:
            raise ValueError("aggtrade_stale_ms must be > 0")
        if self.aggtrade_startup_wait_seconds < 0:
            raise ValueError("aggtrade_startup_wait_seconds must be >= 0")
        if self.aggtrade_max_streams_per_connection <= 0:
            raise ValueError("aggtrade_max_streams_per_connection must be > 0")
        if self.mark_price_stale_ms <= 0:
            raise ValueError("mark_price_stale_ms must be > 0")
        if self.mark_price_startup_wait_seconds < 0:
            raise ValueError("mark_price_startup_wait_seconds must be >= 0")
        if self.oi_stale_ms <= 0:
            raise ValueError("oi_stale_ms must be > 0")
        if self.oi_poll_interval_seconds <= 0:
            raise ValueError("oi_poll_interval_seconds must be > 0")
        if self.oi_symbol_cooldown_seconds <= 0:
            raise ValueError("oi_symbol_cooldown_seconds must be > 0")
        if self.oi_lookback_minutes < 15:
            raise ValueError("oi_lookback_minutes must be >= 15")
        if self.oi_max_symbols_per_cycle <= 0:
            raise ValueError("oi_max_symbols_per_cycle must be > 0")
        if self.oi_radar_symbol_ttl_ms <= 0:
            raise ValueError("oi_radar_symbol_ttl_ms must be > 0")
        if self.prior_context_stale_ms <= 0:
            raise ValueError("prior_context_stale_ms must be > 0")
        if self.prior_context_poll_interval_seconds <= 0:
            raise ValueError("prior_context_poll_interval_seconds must be > 0")
        if self.prior_context_symbol_cooldown_seconds <= 0:
            raise ValueError("prior_context_symbol_cooldown_seconds must be > 0")
        if self.prior_context_lookback_hours != 24:
            raise ValueError("prior_context_lookback_hours must be exactly 24")
        if self.prior_context_max_symbols_per_cycle <= 0:
            raise ValueError("prior_context_max_symbols_per_cycle must be > 0")
        if self.prior_context_radar_symbol_ttl_ms <= 0:
            raise ValueError("prior_context_radar_symbol_ttl_ms must be > 0")
        if self.prior_context_spike_return_pct <= 0:
            raise ValueError("prior_context_spike_return_pct must be > 0")
        if not 0.0 <= self.prior_context_fast_fade_retrace_fraction <= 1.0:
            raise ValueError("prior_context_fast_fade_retrace_fraction must be in [0, 1]")
        if self.top_growth_min_return_pct <= 0:
            raise ValueError("top_growth_min_return_pct must be > 0")
        if self.top_growth_limit <= 0:
            raise ValueError("top_growth_limit must be > 0")
        if self.top_growth_symbols_per_cycle <= 0:
            raise ValueError("top_growth_symbols_per_cycle must be > 0")
        if self.top_growth_max_cycle_seconds <= 0:
            raise ValueError("top_growth_max_cycle_seconds must be > 0")
        if self.top_growth_fetch_spacing_seconds < 0:
            raise ValueError("top_growth_fetch_spacing_seconds must be >= 0")
        if self.startup_context_prewarm_request_sleep_seconds < 0:
            raise ValueError("startup_context_prewarm_request_sleep_seconds must be >= 0")
        if self.startup_context_prewarm_error_limit <= 0:
            raise ValueError("startup_context_prewarm_error_limit must be > 0")
        if self.ws_reconnect_initial_delay_seconds <= 0:
            raise ValueError("ws_reconnect_initial_delay_seconds must be > 0")
        if self.ws_reconnect_max_delay_seconds < self.ws_reconnect_initial_delay_seconds:
            raise ValueError("ws_reconnect_max_delay_seconds must be >= ws_reconnect_initial_delay_seconds")
        if self.ws_connection_max_age_seconds <= 0:
            raise ValueError("ws_connection_max_age_seconds must be > 0")
        if self.user_data_stream_startup_wait_seconds < 0:
            raise ValueError("user_data_stream_startup_wait_seconds must be >= 0")
        if self.user_data_stream_keepalive_interval_seconds <= 0:
            raise ValueError("user_data_stream_keepalive_interval_seconds must be > 0")
        if self.startup_warmup_lookback_minutes <= 0:
            raise ValueError("startup_warmup_lookback_minutes must be > 0")
        if not 1 <= self.startup_warmup_max_trades_per_symbol <= 1000:
            raise ValueError("startup_warmup_max_trades_per_symbol must be in [1, 1000]")
        if self.startup_warmup_max_pages_per_symbol <= 0:
            raise ValueError("startup_warmup_max_pages_per_symbol must be > 0")
        if self.startup_warmup_request_sleep_seconds < 0:
            raise ValueError("startup_warmup_request_sleep_seconds must be >= 0")
        if self.startup_warmup_error_limit <= 0:
            raise ValueError("startup_warmup_error_limit must be > 0")
        if self.startup_htf_baseline_lookback_minutes <= 0:
            raise ValueError("startup_htf_baseline_lookback_minutes must be > 0")
        if self.startup_htf_baseline_request_sleep_seconds < 0:
            raise ValueError("startup_htf_baseline_request_sleep_seconds must be >= 0")
        if self.startup_htf_baseline_error_limit <= 0:
            raise ValueError("startup_htf_baseline_error_limit must be > 0")
        if self.rolling_context_maintenance_poll_interval_seconds <= 0:
            raise ValueError("rolling_context_maintenance_poll_interval_seconds must be > 0")
        if self.rolling_context_maintenance_symbol_cooldown_seconds <= 0:
            raise ValueError("rolling_context_maintenance_symbol_cooldown_seconds must be > 0")
        if self.rolling_context_maintenance_lookback_minutes <= 0:
            raise ValueError("rolling_context_maintenance_lookback_minutes must be > 0")
        if self.rolling_context_maintenance_max_symbols_per_cycle <= 0:
            raise ValueError("rolling_context_maintenance_max_symbols_per_cycle must be > 0")
        if self.rolling_context_maintenance_request_sleep_seconds < 0:
            raise ValueError("rolling_context_maintenance_request_sleep_seconds must be >= 0")
        if self.rolling_context_maintenance_active_symbol_ttl_ms <= 0:
            raise ValueError("rolling_context_maintenance_active_symbol_ttl_ms must be > 0")
        if self.rolling_context_maintenance_closed_candle_lag_ms < 0:
            raise ValueError("rolling_context_maintenance_closed_candle_lag_ms must be >= 0")
        if self.max_closed_candles_per_timeframe <= 0:
            raise ValueError("max_closed_candles_per_timeframe must be > 0")
        if self.universe_max_symbols <= 0:
            raise ValueError("universe_max_symbols must be > 0")
        if self.universe_min_quote_volume_24h < 0:
            raise ValueError("universe_min_quote_volume_24h must be >= 0")
        if self.universe_min_trade_count_24h < 0:
            raise ValueError("universe_min_trade_count_24h must be >= 0")
        if self.universe_min_auto_symbols < 0:
            raise ValueError("universe_min_auto_symbols must be >= 0")
        if self.universe_min_auto_symbols > self.universe_max_symbols:
            raise ValueError("universe_min_auto_symbols must be <= universe_max_symbols")
        if self.decision_timeframe_ms <= 0:
            raise ValueError("decision_timeframe_ms must be > 0")
        if self.decision_deadline_ms <= 0:
            raise ValueError("decision_deadline_ms must be > 0")
        if self.decision_backlog_expire_ms <= 0:
            raise ValueError("decision_backlog_expire_ms must be > 0")
        if self.actionable_min_quote_volume < 0:
            raise ValueError("actionable_min_quote_volume must be >= 0")
        if self.actionable_min_trade_count < 0:
            raise ValueError("actionable_min_trade_count must be >= 0")
        if self.actionable_min_abs_return_pct < 0:
            raise ValueError("actionable_min_abs_return_pct must be >= 0")
        if self.entry_guard_max_signal_age_ms <= 0:
            raise ValueError("entry_guard_max_signal_age_ms must be > 0")
        if self.entry_guard_max_price_drift_pct < 0:
            raise ValueError("entry_guard_max_price_drift_pct must be >= 0")
        if self.entry_guard_min_rr_to_tp1 <= 0:
            raise ValueError("entry_guard_min_rr_to_tp1 must be > 0")
        if self.artifact_writer_queue_max_size <= 0:
            raise ValueError("artifact_writer_queue_max_size must be > 0")
        if self.execution_order_notional_usdt <= 0:
            raise ValueError("execution_order_notional_usdt must be > 0")
        if self.execution_risk_per_trade_pct <= 0.0:
            raise ValueError("execution_risk_per_trade_pct must be > 0")
        if self.execution_max_total_open_risk_pct < self.execution_risk_per_trade_pct:
            raise ValueError("execution_max_total_open_risk_pct must be >= execution_risk_per_trade_pct")
        if self.execution_max_open_positions < 0:
            raise ValueError("execution_max_open_positions must be >= 0; 0 means unlimited")
        if self.execution_stop_visibility_attempts <= 0:
            raise ValueError("execution_stop_visibility_attempts must be > 0")
        if self.execution_stop_visibility_sleep_seconds < 0:
            raise ValueError("execution_stop_visibility_sleep_seconds must be >= 0")
        if self.execution_max_position_amount_slippage_ratio < 0:
            raise ValueError("execution_max_position_amount_slippage_ratio must be >= 0")
        if self.position_supervisor_monitor_interval_ms <= 0:
            raise ValueError("position_supervisor_monitor_interval_ms must be > 0")
        if not 0.0 < self.position_supervisor_tp1_close_fraction <= 1.0:
            raise ValueError("position_supervisor_tp1_close_fraction must be in (0, 1]")
        if self.position_supervisor_breakeven_stop_offset_pct < 0:
            raise ValueError("position_supervisor_breakeven_stop_offset_pct must be >= 0")
        if self.position_supervisor_flat_position_abs_epsilon < 0:
            raise ValueError("position_supervisor_flat_position_abs_epsilon must be >= 0")
        if self.position_supervisor_early_exit_min_hold_candles <= 0:
            raise ValueError("position_supervisor_early_exit_min_hold_candles must be > 0")
        if self.position_supervisor_early_exit_stall_candles < self.position_supervisor_early_exit_min_hold_candles:
            raise ValueError("position_supervisor_early_exit_stall_candles must be >= min hold")
        if self.position_supervisor_early_exit_min_mfe_r < 0:
            raise ValueError("position_supervisor_early_exit_min_mfe_r must be >= 0")
