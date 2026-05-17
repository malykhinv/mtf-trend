"""Strict REST-only micro-live runner for the anomaly wake-up research strategy."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import json
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

import pandas as pd

from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.exchanges.ccxt_types import ExchangeTickerSnapshot
from data.storage.parquet_storage import ParquetStorage
from domain.exceptions import ExchangeConnectivityError
from domain.enums.timeframe import Timeframe
from research_tools.anomaly_continuation_lab import compute_start_verticality_metrics
from research_tools.anomaly_config import ANOMALY_LIVE_TIMEFRAME_PAIRS
from research_tools.anomaly_category_contract import (
    CATEGORY_CONTRACT_ID as LIVE_CATEGORY_CONTRACT,
    DEFAULT_PUMP_CATEGORY_IDS as LIVE_DEFAULT_PUMP_CATEGORY_IDS,
    PumpCategoryContract as LivePumpCategory,
    SUPPORTED_PUMP_CATEGORIES as SUPPORTED_LIVE_PUMP_CATEGORIES,
    TIMEFRAME_CATEGORY_PRIORITY as LIVE_TIMEFRAME_CATEGORY_PRIORITY,
)
from research_tools.runner_fader_prepump_context import (
    DEFAULT_PREPUMP_CONTEXT_WINDOWS,
    compute_spot_prepump_window_features,
    parse_prepump_windows,
)


REQUIRED_PRICE_COLUMNS = ("timestamp", "open", "high", "low", "close")
REQUIRED_FLOW_COLUMNS = ("quote_volume", "number_of_trades")
OPTIONAL_FLOW_COLUMNS = ("taker_buy_quote_volume",)
CACHED_OHLCV_DTYPES = {
    "timestamp": "int64",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "quote_volume": "float64",
    "number_of_trades": "float64",
    "taker_buy_quote_volume": "float64",
}
HOUR_MS = 60 * 60 * 1000
BINANCE_FUTURES_ALL_TICKER_WS_URL = "wss://fstream.binance.com/market/ws/!ticker@arr"
BINANCE_FUTURES_COMBINED_WS_URL = "wss://fstream.binance.com/market/stream"
DEFAULT_LIVE_WS_AGGTRADE_MAX_BACKFILL_MS = 360_000
DEFAULT_LIVE_WS_AGGTRADE_BUFFER_MINUTES = 60
DEFAULT_LIVE_WS_HEALTH_BOOTSTRAP_SECONDS = 180.0
DEFAULT_LIVE_OHLCV_CACHE_FLUSH_MAX_SYMBOL_TIMEFRAMES = 4
DEFAULT_LIVE_AGGTRADE_REST_CACHE_TTL_MS = 20 * 60_000
DEFAULT_LIVE_AGGTRADE_REST_CACHE_PADDING_MS = 60_000
NETWORK_DEGRADED_TELEGRAM_RETRY_SECONDS = 60.0
DANGER_DEFAULT_INACTIVE_COLD_COVERAGE_SLOTS_PER_CYCLE = 5
DANGER_ADAPTIVE_COLD_COVERAGE_MAX_SLOTS_PER_CYCLE = 10
DANGER_ADAPTIVE_COLD_COVERAGE_MIN_SCORE = 0.30
DANGER_ADAPTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO = 0.95
DANGER_ADAPTIVE_COLD_COVERAGE_FULL_WS_HEALTH_RATIO = 0.995
DANGER_ADAPTIVE_COLD_COVERAGE_FAST_CYCLE_SECONDS = 2.0
DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS = 8.0
DANGER_ADAPTIVE_COLD_COVERAGE_CYCLE_EWMA_ALPHA = 0.25
DANGER_ADAPTIVE_COLD_COVERAGE_PRESSURE_EWMA_ALPHA = 0.25
DANGER_ADAPTIVE_COLD_COVERAGE_ACTIVE_WAITING_SOFT_CAP = 3
DANGER_ADAPTIVE_COLD_COVERAGE_NETWORK_CALLS_HIGH = 4
DANGER_ADAPTIVE_COLD_COVERAGE_REST_FETCHED_MS_HIGH = 120_000
DANGER_ADAPTIVE_COLD_COVERAGE_PENDING_GAPS_HIGH = 3
DEFAULT_LATENCY_SLA_DUE_SCAN_P95_SECONDS = 15.0
DEFAULT_LATENCY_SLA_MIN_DUE_SAMPLES = 1
DEFAULT_SYMBOL_CONTEXT_SNAPSHOT_MAX_CYCLE_SECONDS = 0.75
DEFAULT_SYMBOL_CONTEXT_SNAPSHOT_MIN_COVERAGE_RATIO = 0.995
DEFAULT_SYMBOL_CONTEXT_SNAPSHOT_MAX_GAP_CANDLES = 2
DEFAULT_SYMBOL_CONTEXT_PRIORITY_TTL_MS = 5 * 60_000
STARTUP_SYMBOL_CONTEXT_MIN_READY_SYMBOL_RATIO = 0.95
STARTUP_SYMBOL_CONTEXT_MIN_READY_SNAPSHOT_RATIO = 0.95
STARTUP_CONTEXT_BACKFILL_FLUSH_SYMBOL_TIMEFRAMES = 16
LIVE_CONTEXT_REPREPARE_MIN_INTERVAL_SECONDS = 6 * 60 * 60
LIVE_CONTEXT_REPREPARE_SNAPSHOT_STALE_SECONDS = 90 * 60
LIVE_CONTEXT_REPREPARE_MIN_RUNTIME_SECONDS = 60 * 60
LIVE_CONTEXT_REPREPARE_DEFER_LOG_INTERVAL_SECONDS = 10 * 60
DEFAULT_WARM_WATCH_AGGTRADE_TARGET_CAP = 40
LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON = "latency_sla_due_scan_p95_above_threshold"
DANGER_LOCAL_ENTRY_POSITION_GUARD_SOURCE = "DANGER_local_memory_position_guard_no_pre_entry_exchange_position_fetch"
DANGER_CHEAP_FLOW_RADAR_SOURCE = "DANGER_ws_ticker_trade_count_flow_radar"
DANGER_TICKER_FLOW_RADAR_MIN_QUOTE_VOLUME_DELTA_USDT = 5_000.0
DANGER_TICKER_FLOW_RADAR_MIN_TRADE_COUNT_DELTA = 40
DANGER_TICKER_FLOW_RADAR_MIN_TRADE_COUNT_DELTA_RATIO = 3.0
DANGER_TICKER_FLOW_RADAR_MIN_PRICE_DELTA_PCT = -0.001
DANGER_TICKER_FLOW_RADAR_MAX_PRICE_DELTA_PCT = 0.003
WARM_WATCH_SOURCE = "warm_watch_continuing_flow"
DEFAULT_WARM_WATCH_TTL_MS = 10 * 60_000
DEFAULT_WARM_WATCH_MIN_OBSERVATIONS_FOR_PRECISE = 2
DEFAULT_WARM_WATCH_MIN_PRICE_DELTA_PCT = -0.001
DEFAULT_WARM_WATCH_MAX_PRICE_DELTA_PCT = 0.012
CANDIDATE_QUEUE_PRESSURE_MIN_KEEP = 12
CANDIDATE_QUEUE_PRESSURE_MAX_RADAR = 24
CANDIDATE_QUEUE_PRESSURE_MAX_WARM = 24
CANDIDATE_QUEUE_PRESSURE_BACKLOG_STALE_FACTOR = 2.0
ADAPTIVE_PRECISE_BUDGET_BREACHED_RADAR_SLOTS = 1
ADAPTIVE_PRECISE_BUDGET_PRESSURE_RADAR_SLOTS = 2
ADAPTIVE_PRECISE_BUDGET_ACTIVE_RADAR_SLOTS = 2
ADAPTIVE_PRECISE_BUDGET_SLOW_CYCLE_RADAR_SLOTS = 3
DEPENDENCY_RETRY_MIN_COOLDOWN_MS = 10_000
DEPENDENCY_RETRY_MAX_COOLDOWN_MS = 30_000
PREPUMP_WARM_WATCH_SCORING_CONTRACT = "prepump_warm_watch_scoring_v1_spot_feature_separation_midpoint"
DEFAULT_PREPUMP_WARM_WATCH_MIN_ABS_STANDARDIZED_DIFF = 0.75
DEFAULT_PREPUMP_WARM_WATCH_MIN_RUNNER_ROWS = 10
DEFAULT_PREPUMP_WARM_WATCH_MIN_FADER_ROWS = 10
DEFAULT_PREPUMP_WARM_WATCH_MAX_FEATURES = 8
DEFAULT_PREPUMP_WARM_WATCH_SCORE_WEIGHT = 0.35
DEFAULT_PREPUMP_WARM_WATCH_MIN_COVERAGE_RATIO = 0.80
DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO = DANGER_ADAPTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO
DANGER_INACTIVE_COLD_COVERAGE_SOURCE = "DANGER_default_precise_cold_coverage_subminute"
EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE = "explicit_precise_cold_coverage"
COLD_COVERAGE_GATED_OFF_SOURCE = "precise_cold_coverage_gated_off"
RETRYABLE_CATEGORY_STATUS_PREFIXES = (
    "mark_context_",
    "no_mark_before_decision",
    "oi_",
    "context=",
    "fetch_failed:",
    "fetch_empty",
    "cache_",
    "empty:",
    "missing_",
)
VISIBILITY_RADAR_EVENTS = frozenset(
    {
        "ticker_radar_promoted",
        "warm_watch_marked",
        "warm_watch_updated",
        "warm_watch_precise_promoted",
        "candidate_dropped_latency_pressure",
        "candidate_expired_backlog_stale",
    }
)
VISIBILITY_WARM_WATCH_EVENTS = frozenset(
    {
        "warm_watch_marked",
        "warm_watch_updated",
        "warm_watch_precise_promoted",
        "warm_watch_precise_deferred_latency_sla",
        "warm_watch_rejected",
        "warm_watch_expired",
        "warm_watch_cleared",
        "candidate_dropped_latency_pressure",
        "candidate_expired_backlog_stale",
    }
)
VISIBILITY_PRECISE_SCAN_EVENTS = frozenset(
    {
        "signal_symbol_scan_summary",
        "signal_setup_fetch_failed",
        "signal_entry_fetch_failed",
        "signal_entry_ws_aggtrade_pending",
        "signal_scan_empty_ohlcv",
        "reject_missing_signal_columns",
        "reject_setup_too_early",
        "signal_scan_retryable_dependency_blocked",
        "signal_scan_dependency_retry_scheduled",
        "candidate_expired_dependency_timeout",
        "reject_entry_below_initial_stop",
        "reject_invalid_initial_risk",
        "category_selected",
        "category_rejected",
    }
)
VISIBILITY_EXECUTION_REJECT_EVENTS = frozenset(
    {
        "reject_symbol_position_already_active",
        "reject_stop_cooldown",
        "reject_max_positions",
        "reject_invalid_existing_exchange_position",
        "reject_existing_exchange_position",
        "reject_invalid_free_balance",
        "reject_no_free_balance",
        "reject_invalid_position_notional",
        "reject_insufficient_margin_for_fixed_notional",
        "reject_invalid_order_amount",
        "reject_signal_not_closed_yet",
        "reject_stale_signal",
        "reject_invalid_live_price",
        "reject_tp1_already_reached",
        "reject_invalid_actual_risk_at_live_price",
        "reject_actual_risk_too_wide_at_live_price",
        "reject_entry_price_drift",
        "reject_rr_collapsed",
        "discrete_signal_snapshot_entry_missed",
    }
)
ANIMAL_EMOJIS = (
    "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨", "🐯",
    "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🐦", "🦆", "🦅",
    "🦉", "🦇", "🐺", "🐗", "🐴", "🦄", "🐝", "🐛", "🦋", "🐌",
    "🐞", "🐜", "🦗", "🕷️", "🦂", "🐢", "🐍", "🦎", "🦖", "🦕",
    "🐙", "🦑", "🦐", "🦞", "🦀", "🐡", "🐠", "🐟", "🐬", "🐳",
    "🐋", "🦈", "🐊", "🐅", "🐆", "🦓", "🦍", "🦧", "🐘", "🦛",
    "🦏", "🐪", "🐫", "🦒", "🦘", "🦬", "🐃", "🐂", "🐄", "🐎",
    "🐖", "🐏", "🐑", "🦙", "🐐", "🦌", "🐕", "🐩", "🐈", "🐓",
    "🦃", "🦚", "🦜", "🦢", "🦩", "🕊️", "🐇", "🦝", "🦨", "🦡",
    "🦫", "🦦", "🦥", "🐁", "🐀", "🐿️", "🦔",
)
SERVICE_WARNING_EMOJI = "⚠️"
SERVICE_WORK_EMOJI = "🚧"
LIVE_SESSION_TOP_LIMIT = 3
LIVE_SESSION_TOP_ARTIFACT_INTERVAL_MS = 60_000
DEFAULT_LIVE_TOP_GROWTH_MIN_RETURN_PCT = 0.10
DEFAULT_LIVE_TOP_GROWTH_LIMIT = 5
DEFAULT_LIVE_TOP_GROWTH_IDLE_SYMBOLS_PER_CYCLE = 8
DEFAULT_LIVE_TOP_GROWTH_IDLE_MAX_CYCLE_SECONDS = 1.5
DEFAULT_LIVE_TOP_GROWTH_CONSERVATIVE_SYMBOLS_PER_CYCLE = 2
DEFAULT_LIVE_TOP_GROWTH_CONSERVATIVE_MAX_CYCLE_SECONDS = 0.5
LIVE_SESSION_TOP_ROLLING_WINDOW_MS = 6 * 60 * 60 * 1000
LIVE_CRYPTO_SESSION_WINDOWS_UTC = (
    (0, 7 * 60, "Азия", "core", "Азия", ""),
    (7 * 60, 9 * 60, "Азия → Европа", "transition", "Азия", "Европа"),
    (9 * 60, 13 * 60, "Европа", "core", "Европа", ""),
    (13 * 60, 16 * 60, "Европа + Америка", "overlap", "Европа", "Америка"),
    (16 * 60, 21 * 60, "Америка", "core", "Америка", ""),
    (21 * 60, 24 * 60, "Америка → Азия", "transition", "Америка", "Азия"),
)
LIVE_SESSION_TOP_GROWTH_COLUMNS = (
    "snapshot_utc",
    "session_label",
    "session_phase",
    "session_primary",
    "session_secondary",
    "session_start_utc",
    "session_end_utc",
    "top_window_label",
    "top_window_start_utc",
    "top_window_end_utc",
    "top_window_hours",
    "rank",
    "symbol",
    "growth_pct",
    "growth_fraction",
    "baseline_price",
    "last_price",
    "baseline_timestamp_utc",
    "last_timestamp_utc",
    "last_price_source",
    "ticker_source",
    "ticker_source_status",
    "ticker_source_reason",
    "status",
    "reason",
    "symbols_tracked",
    "symbols_with_positive_growth",
)
TOP_GROWTH_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "rank",
    "symbol",
    "growth_pct",
    "growth_fraction",
    "open",
    "close",
    "high",
    "low",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
    "threshold_pct",
    "timeframe",
    "source",
)
TOP_GROWTH_STATUS_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "symbol",
    "status",
    "reason",
    "growth_pct",
    "growth_fraction",
    "open",
    "close",
    "high",
    "low",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
    "candle_timestamp_ms",
    "timeframe",
)
TOP_GROWTH_INDEX_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "top_count",
    "symbols_total",
    "ok_count",
    "failed_count",
    "threshold_pct",
    "limit",
    "top_file",
    "status_file",
    "visibility_file",
)
MISSED_PUMP_VISIBILITY_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "symbol",
    "rank",
    "growth_pct",
    "growth_fraction",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
    "live_saw_symbol_before_pump",
    "live_saw_symbol_during_pump_hour",
    "radar_promoted",
    "flow_radar_promoted",
    "warm_watch",
    "precise_scanned",
    "category_rejected",
    "execution_rejected",
    "position_opened",
    "first_live_event_time",
    "first_live_event_before_pump_time",
    "first_radar_seen_time",
    "first_flow_radar_seen_time",
    "first_warm_watch_time",
    "first_precise_scan_time",
    "first_category_reject_time",
    "first_execution_reject_time",
    "first_position_opened_time",
    "first_category_reject_reason",
    "first_execution_reject_event",
    "not_scanned_reason",
    "visibility_source",
    "visibility_source_status",
    "visibility_source_reason",
    "visibility_events_total",
    "visibility_events_for_symbol",
    "visibility_events_before_period_end",
    "visibility_event_parse_error_count",
)

DELAYED_REPLAY_RESULTS_COLUMNS = (
    "case_id",
    "queued_at_utc",
    "processed_at_utc",
    "status",
    "symbol",
    "source_event",
    "priority",
    "live_decision_class",
    "live_reason",
    "levels_tf",
    "entry_tf",
    "decision_timestamp_ms",
    "replay_not_before_ms",
    "queued_delay_seconds",
    "recompute_status",
    "recompute_source",
    "decision_snapshot_status",
    "decision_snapshot_source",
    "would_select_signal",
    "would_enter_under_frozen_decision",
    "strict_replay_would_enter",
    "snapshot_signal_available",
    "strict_recompute_signal",
    "frozen_signal_snapshot_used",
    "operator_alert_kind",
    "mismatch_type",
    "recomputed_category_id",
    "recomputed_category_label",
    "recomputed_entry_price",
    "recomputed_stop_price",
    "recomputed_tp1_price",
    "recompute_reject_reasons",
    "recompute_data_status",
    "decision_data_end_timestamp_ms",
    "telegram_notified",
    "outcome_status",
    "outcome_window_start_ms",
    "outcome_window_end_ms",
    "outcome_rows",
    "outcome_high",
    "outcome_low",
    "outcome_close",
    "signal_entry_price",
    "signal_stop_price",
    "signal_tp1_price",
    "tp1_would_hit",
    "stop_would_hit",
    "first_hit",
    "outcome_is_post_decision",
    "source_scan_mode",
    "delayed_replay_contract",
)
DELAYED_REPLAY_SUMMARY_COLUMNS = (
    "timestamp_utc",
    "cycle",
    "enabled",
    "status",
    "reason",
    "pending_count",
    "ready_count",
    "processed_count",
    "skipped_count",
    "max_cases",
    "max_seconds",
    "active_symbol_count",
    "open_positions",
    "opening_symbols",
    "idle_since_ms",
    "duration_seconds",
)
DELAYED_REPLAY_CONTRACT = "delayed_replay_v6_immutable_decision_snapshot_idle_tg"
DELAYED_REPLAY_DECISION_SNAPSHOT_CONTRACT = "delayed_replay_decision_snapshot_v1_live_inputs"
SYMBOL_CONTEXT_SNAPSHOT_CONTRACT = "symbol_context_snapshot_v2_cache_only_prior_fast_fade_prepump_spot"
SYMBOL_CONTEXT_SNAPSHOT_COLUMNS = (
    "snapshot_timestamp_utc",
    "snapshot_timestamp_ms",
    "symbol",
    "levels_tf",
    "entry_tf",
    "status",
    "reason",
    "source",
    "contract",
    "context_timeframe",
    "history_start_timestamp_ms",
    "context_start_timestamp_ms",
    "context_cache_end_timestamp_ms",
    "effective_cache_end_timestamp_ms",
    "ignored_tail_ms",
    "baseline_candles",
    "baseline_status",
    "baseline_quote_volume_median",
    "baseline_trade_count_median",
    "baseline_range_pct_median",
    "latest_context_close",
    "prior_spike_count_available",
    "prior_fast_fade_count_available",
    "prior_spike_timestamps_ms",
    "prior_fast_fade_timestamps_ms",
    "prepump_spot_feature_contract",
    "prepump_spot_windows",
    "prepump_spot_features_json",
    "compute_seconds",
)
LIVE_LEDGER_COLUMNS = (
    "position_id",
    "status",
    "symbol",
    "signal_category_id",
    "signal_category_label",
    "session",
    "opened_at_utc",
    "closed_at_utc",
    "entry_price",
    "signal_entry_price",
    "first_executable_entry_timestamp_ms",
    "entry_lag_ms",
    "entry_lag_ltf_candles",
    "entered_late_vs_first_executable",
    "entry_order_submit_lag_ms",
    "entry_order_submit_lag_ltf_candles",
    "previous_live_scan_closed_timestamp_ms",
    "first_unscanned_decision_timestamp_ms",
    "live_scan_gap_ltf_candles",
    "entry_fill_timestamp_ms",
    "entry_order_submitted_at_ms",
    "entry_order_status",
    "stop_price",
    "source_scan_mode",
    "danger_cold_coverage_source",
    "entry_position_guard_source",
    "tp1_price",
    "tp1_order_id",
    "tp1_client_order_id",
    "tp1_order_amount",
    "amount",
    "entry_filled_amount",
    "entry_cost_usdt",
    "entry_fee_usdt",
    "pre_position_amount",
    "post_position_amount",
    "position_delta_amount",
    "notional_usdt",
    "risk_usdt",
    "realized_pnl_usdt",
    "realized_pnl_pct",
    "signal_json",
    "entry_order_id",
    "stop_order_id",
    "telegram_open_message_id",
    "telegram_stop_message_id",
    "telegram_close_message_id",
    "close_reason",
)


class LiveStartupError(RuntimeError):
    """Expected startup validation error for clean CLI output."""


class LiveDataIntegrityError(RuntimeError):
    """Live artifact/data integrity failure that must not be hidden as a network issue."""

    def __init__(self, message: str, *, symbol: str | None = None) -> None:
        super().__init__(message)
        normalized_symbol = str(symbol).strip() if symbol is not None else ""
        self.symbol = normalized_symbol or None


class LiveOrderPositionIntegrityError(LiveDataIntegrityError):
    """Order/position integrity failure. Strict by default; explicit danger mode may keep live running."""


class LiveWsAggTradeCoveragePending(RuntimeError):
    """Raised when strict WS aggTrade coverage is insufficient for subminute signal evaluation."""

    def __init__(
        self,
        message: str,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        missing_ranges: tuple[tuple[int, int], ...],
        status: str,
        reason: str | None,
    ) -> None:
        super().__init__(message)
        self.symbol = symbol
        self.start_timestamp_ms = int(start_timestamp_ms)
        self.end_timestamp_ms = int(end_timestamp_ms)
        self.missing_ranges = missing_ranges
        self.status = status
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LivePrepumpWarmWatchFeatureRule:
    feature: str
    runner_mean: float
    fader_mean: float
    standardized_diff: float
    runner_count: int
    fader_count: int
    weight: float

    @property
    def direction(self) -> int:
        return 1 if self.runner_mean >= self.fader_mean else -1


@dataclass(frozen=True, slots=True)
class LivePrepumpWarmWatchScoringProfile:
    enabled: bool
    status: str
    source_path: str = ""
    reason: str = ""
    rules: tuple[LivePrepumpWarmWatchFeatureRule, ...] = ()


@dataclass(frozen=True, slots=True)
class LivePrepumpWarmWatchScore:
    status: str
    reason: str
    raw_score: float | None = None
    score_adjustment: float = 0.0
    features_used: int = 0
    features_missing: int = 0
    top_features: tuple[str, ...] = ()

    def event_payload(self) -> dict[str, object]:
        return {
            "prepump_warm_watch_scoring_status": self.status,
            "prepump_warm_watch_scoring_reason": self.reason,
            "prepump_warm_watch_score": (
                round(float(self.raw_score), 6) if self.raw_score is not None else ""
            ),
            "prepump_warm_watch_score_adjustment": round(float(self.score_adjustment), 6),
            "prepump_warm_watch_features_used": int(self.features_used),
            "prepump_warm_watch_features_missing": int(self.features_missing),
            "prepump_warm_watch_top_features": ";".join(self.top_features),
            "prepump_warm_watch_scoring_contract": PREPUMP_WARM_WATCH_SCORING_CONTRACT,
        }


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    events_bot_token: str
    events_chat_id: str
    positions_bot_token: str
    positions_chat_id: str


@dataclass(frozen=True, slots=True)
class AggTradeRawRange:
    start_timestamp_ms: int
    end_timestamp_ms: int
    rows: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class WsAggTradeReadResult:
    rows: tuple[dict[str, object], ...]
    missing_ranges: tuple[tuple[int, int], ...]
    status: str
    reason: str | None
    subscribed: bool
    connection_status: str
    last_error: str | None
    last_trade_timestamp_ms: int | None
    last_receive_at_ms: int | None
    buffer_row_count: int


@dataclass(frozen=True, slots=True)
class LiveOiChangeResult:
    value: float | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LiveMarkBasisResult:
    value: float | None
    reason: str | None = None
    timestamp_ms: int | None = None
    age_ms: int | None = None


class LiveTickerSnapshotSource(Protocol):
    @property
    def source_id(self) -> str:
        """Stable diagnostics id for the ticker snapshot source."""

    def fetch_snapshots(self, symbols: tuple[str, ...]) -> list[ExchangeTickerSnapshot]:
        """Return ticker snapshots for the requested live universe."""


@dataclass(frozen=True, slots=True)
class RestLiveTickerSnapshotSource:
    exchange: CcxtFuturesClient

    @property
    def source_id(self) -> str:
        return "rest_fetch_tickers"

    def fetch_snapshots(self, symbols: tuple[str, ...]) -> list[ExchangeTickerSnapshot]:
        return self.exchange.fetch_ticker_snapshots(symbols)


def _json_safe_payload(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else ""
    return str(value)


def _delayed_replay_case_id(
    *,
    source_event: str,
    symbol: str,
    levels_tf: str,
    entry_tf: str,
    decision_timestamp_ms: int,
    live_reason: str,
    category_id: str,
) -> str:
    raw = json.dumps(
        {
            "source_event": source_event,
            "symbol": _position_symbol_key(symbol),
            "levels_tf": levels_tf,
            "entry_tf": entry_tf,
            "decision_timestamp_ms": int(decision_timestamp_ms),
            "live_reason": live_reason,
            "category_id": category_id,
        },
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _empty_delayed_replay_outcome(
    status: str,
    *,
    outcome_window_start_ms: int | str = "",
    outcome_window_end_ms: int | str = "",
) -> dict[str, object]:
    return {
        "outcome_status": status,
        "outcome_window_start_ms": outcome_window_start_ms,
        "outcome_window_end_ms": outcome_window_end_ms,
        "outcome_rows": 0,
        "outcome_high": "",
        "outcome_low": "",
        "outcome_close": "",
        "tp1_would_hit": "",
        "stop_would_hit": "",
        "first_hit": "",
    }


def _optional_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _delayed_replay_snapshot_columns(frame: pd.DataFrame) -> list[str]:
    preferred = (
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "synthetic_ohlcv_bucket",
    )
    return [column for column in preferred if column in frame.columns]


def _delayed_replay_frame_to_rows(frame: pd.DataFrame, *, max_rows: int) -> list[dict[str, object]]:
    if frame.empty:
        return []
    columns = _delayed_replay_snapshot_columns(frame)
    if not columns:
        return []
    prepared = frame.loc[:, columns].copy()
    if "timestamp" in prepared.columns:
        prepared = prepared.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    prepared = prepared.tail(max(1, int(max_rows)))
    return [dict(row) for row in _json_safe_payload(prepared.to_dict(orient="records"))]


def _delayed_replay_series_to_row(row: pd.Series | dict[str, object]) -> dict[str, object]:
    if isinstance(row, pd.Series):
        raw = row.to_dict()
    elif isinstance(row, dict):
        raw = dict(row)
    else:
        return {}
    columns = (
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "synthetic_ohlcv_bucket",
    )
    return dict(_json_safe_payload({column: raw[column] for column in columns if column in raw}))


def _delayed_replay_rows_to_frame(rows: object) -> pd.DataFrame:
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame()
    records = [dict(row) for row in rows if isinstance(row, dict)]
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce").astype("Int64")
        frame = frame.dropna(subset=["timestamp"]).copy()
        if not frame.empty:
            frame["timestamp"] = frame["timestamp"].astype("int64")
            frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    for column in frame.columns:
        if column == "timestamp":
            continue
        if column == "synthetic_ohlcv_bucket":
            frame[column] = frame[column].map(
                lambda value: bool(value)
                if isinstance(value, bool)
                else str(value).strip().lower() in {"1", "true", "yes", "y"}
            )
            continue
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _delayed_replay_snapshot_from_json(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, str) or not payload.strip():
        return None
    try:
        raw = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("contract") != DELAYED_REPLAY_DECISION_SNAPSHOT_CONTRACT:
        return None
    return raw


def _frozen_mark_basis_from_context(context: object) -> LiveMarkBasisResult | None:
    if not isinstance(context, dict):
        return None
    has_mark = any(key in context for key in ("mark_close_vs_decision_close_basis", "mark_basis_status", "mark_timestamp_ms", "mark_age_ms"))
    if not has_mark:
        return None
    return LiveMarkBasisResult(
        _optional_float(context.get("mark_close_vs_decision_close_basis")),
        str(context.get("mark_basis_status") or "frozen_mark_basis_missing"),
        timestamp_ms=_optional_int(context.get("mark_timestamp_ms")),
        age_ms=_optional_int(context.get("mark_age_ms")),
    )


def _frozen_oi_change_from_context(context: object) -> LiveOiChangeResult | None:
    if not isinstance(context, dict):
        return None
    has_oi = any(key in context for key in ("oi_change_pct_3x5m", "oi_status"))
    if not has_oi:
        return None
    return LiveOiChangeResult(
        _optional_float(context.get("oi_change_pct_3x5m")),
        str(context.get("oi_status") or "frozen_oi_missing"),
    )


def _frozen_prior_fast_fade_from_context(context: object) -> dict[str, object] | None:
    if not isinstance(context, dict):
        return None
    raw = context.get("prior_fast_fade_result")
    if isinstance(raw, dict):
        return dict(raw)
    return None


def _ws_error_short_label(error: str | None) -> str:
    text = str(error or "")
    lowered = text.lower()
    if "clientconnectordnserror" in lowered or "could not contact dns servers" in lowered:
        return "dns"
    if "timeout" in lowered:
        return "timeout"
    if "ssl" in lowered:
        return "ssl"
    if "proxy" in lowered:
        return "proxy"
    if "connection refused" in lowered or "connect call failed" in lowered:
        return "connect"
    return "error" if text else ""


def _is_retryable_category_rejection(row: dict[str, object]) -> bool:
    reason = str(row.get("category_reject_reason") or row.get("reason") or "")
    if reason == "reject_prior_fast_fade_filter_unavailable":
        return True
    if reason in {"reject_mark_basis_unavailable", "reject_oi_unavailable"}:
        return True
    if reason in {
        "reject_missing_taker_buy_share",
        "reject_invalid_taker_buy_share",
        "reject_missing_start_taker_buy_delta",
        "reject_invalid_start_taker_buy_delta",
    }:
        return True
    # Backward-compatible classification for old rows that used final-reject wording for
    # temporarily unavailable exchange context. New code emits *_unavailable without
    # category_rejected side effects.
    if reason == "reject_mark_basis_below_min":
        status = str(row.get("mark_basis_status") or "")
        return bool(status and status != "ok" and status.startswith(("mark_context_", "no_mark_before_decision")))
    if reason == "reject_oi":
        status = str(row.get("oi_status") or "")
        return bool(status and status != "below_threshold" and status.startswith("oi_"))
    return False


def _retryable_category_rejection_rows(category_rejections: list[dict[str, object]]) -> tuple[dict[str, object], ...]:
    return tuple(row for row in category_rejections if _is_retryable_category_rejection(row))


def _retryable_category_reasons(category_rejections: list[dict[str, object]]) -> tuple[str, ...]:
    retryable: list[str] = []
    seen: set[str] = set()
    for row in _retryable_category_rejection_rows(category_rejections):
        reason = str(row.get("category_reject_reason") or row.get("reason") or "")
        if reason and reason not in seen:
            retryable.append(reason)
            seen.add(reason)
    return tuple(retryable)


def _aiohttp_ws_connector() -> object:
    import aiohttp

    # Force aiohttp to use the same OS getaddrinfo resolver path as REST/ccxt.
    # In environments where aiodns/c-ares cannot contact DNS servers, REST can work
    # while aiohttp WebSockets fail with ClientConnectorDNSError. This is not a
    # data fallback: WS still has to connect or report the real transport error.
    return aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), ttl_dns_cache=300)


class BinanceWsAllTickerSnapshotSource:
    source_id = "binance_ws_all_ticker"

    def __init__(
        self,
        *,
        exchange: CcxtFuturesClient,
        stale_ms: int,
        startup_wait_seconds: float,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.exchange = exchange
        self.stale_ms = int(stale_ms)
        self.startup_wait_seconds = float(startup_wait_seconds)
        self.logger = logger
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._ticker_by_market_id: dict[str, ExchangeTickerSnapshot] = {}
        self._last_message_at_ms: int | None = None
        self._last_error: str | None = None
        self._connection_status = "starting"
        self._last_payload_source = "none"
        self._seeded_at_ms: int | None = None
        self._seeded_count = 0
        self._startup_wait_until_monotonic = time.monotonic() + max(0.0, self.startup_wait_seconds)
        self._thread = threading.Thread(target=self._run_thread, name="binance-ws-all-ticker", daemon=True)
        self._thread.start()

    def fetch_snapshots(self, symbols: tuple[str, ...]) -> list[ExchangeTickerSnapshot]:
        if not symbols:
            return []
        wait_seconds = max(0.0, self._startup_wait_until_monotonic - time.monotonic())
        if not self._ready_event.wait(timeout=wait_seconds):
            raise RuntimeError(self._status_reason("ws_ticker_not_ready"))
        now_ms = int(time.time() * 1000)
        with self._lock:
            last_message_at_ms = self._last_message_at_ms
            last_error = self._last_error
            connection_status = self._connection_status
        if last_message_at_ms is None:
            raise RuntimeError(self._status_reason("ws_ticker_no_messages"))
        if now_ms - int(last_message_at_ms) > self.stale_ms:
            raise RuntimeError(self._status_reason("ws_ticker_stale"))
        snapshots: list[ExchangeTickerSnapshot] = []
        with self._lock:
            ticker_by_market_id = dict(self._ticker_by_market_id)
        for symbol in symbols:
            market_id = self.exchange.get_market_id(symbol)
            snapshot = ticker_by_market_id.get(market_id)
            if snapshot is None:
                snapshots.append(
                    ExchangeTickerSnapshot(
                        symbol=symbol,
                        fetched_at_ms=now_ms,
                        last_price=None,
                        quote_volume_24h=None,
                        trade_count_24h=None,
                        last_price_source="binance_ws_all_ticker",
                        quote_volume_source="binance_ws_all_ticker",
                        trade_count_source="binance_ws_all_ticker",
                        status="missing",
                        reason=f"ws_ticker_missing_market_id:{market_id}",
                    )
                )
            else:
                snapshots.append(
                    ExchangeTickerSnapshot(
                        symbol=symbol,
                        fetched_at_ms=snapshot.fetched_at_ms,
                        last_price=snapshot.last_price,
                        quote_volume_24h=snapshot.quote_volume_24h,
                        trade_count_24h=snapshot.trade_count_24h,
                        last_price_source=snapshot.last_price_source,
                        quote_volume_source=snapshot.quote_volume_source,
                        trade_count_source=snapshot.trade_count_source,
                        status=snapshot.status,
                        reason=snapshot.reason,
                    )
                )
        return snapshots

    def seed_from_snapshots(self, snapshots: list[ExchangeTickerSnapshot]) -> dict[str, object]:
        now_ms = int(time.time() * 1000)
        updates: dict[str, ExchangeTickerSnapshot] = {}
        ok_count = 0
        missing_count = 0
        for snapshot in snapshots:
            if snapshot.status != "ok":
                missing_count += 1
                continue
            try:
                market_id = str(self.exchange.get_market_id(snapshot.symbol)).strip().upper()
            except Exception:
                missing_count += 1
                continue
            if not market_id:
                missing_count += 1
                continue
            ok_count += 1
            updates[market_id] = ExchangeTickerSnapshot(
                symbol=snapshot.symbol,
                fetched_at_ms=now_ms,
                last_price=snapshot.last_price,
                quote_volume_24h=snapshot.quote_volume_24h,
                trade_count_24h=snapshot.trade_count_24h,
                last_price_source=f"rest_startup_seed.{snapshot.last_price_source}",
                quote_volume_source=f"rest_startup_seed.{snapshot.quote_volume_source}",
                trade_count_source=f"rest_startup_seed.{snapshot.trade_count_source}",
                status="ok",
                reason=None,
            )
        with self._lock:
            if updates:
                self._ticker_by_market_id.update(updates)
                self._last_message_at_ms = now_ms
                self._last_error = None
                self._last_payload_source = "rest_startup_seed"
                self._seeded_at_ms = now_ms
                self._seeded_count = len(updates)
                self._ready_event.set()
            return {
                "seeded_count": len(updates),
                "ok_count": ok_count,
                "missing_count": missing_count,
                "seeded_at_ms": self._seeded_at_ms if updates else "",
            }

    def source_status(self) -> tuple[str, str]:
        with self._lock:
            if self._last_payload_source == "rest_startup_seed":
                return (
                    "primary_seeded_rest",
                    f"seeded_at_ms={self._seeded_at_ms if self._seeded_at_ms is not None else ''};"
                    f"seeded_count={self._seeded_count}",
                )
        return "primary", ""

    def close(self) -> None:
        self._stop_event.set()

    def _status_reason(self, reason: str) -> str:
        with self._lock:
            parts = [
                reason,
                f"status={self._connection_status}",
                f"last_message_at_ms={self._last_message_at_ms if self._last_message_at_ms is not None else ''}",
            ]
            if self._last_error:
                parts.append(f"last_error={self._last_error[:240]}")
        return ";".join(parts)

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._run_ws_loop())
            except Exception as exc:
                self._set_status("error", f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            if not self._stop_event.is_set():
                time.sleep(2.0)

    async def _run_ws_loop(self) -> None:
        import aiohttp

        self._set_status("connecting", None)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
        connector = _aiohttp_ws_connector()
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.ws_connect(BINANCE_FUTURES_ALL_TICKER_WS_URL, heartbeat=20) as ws:
                self._set_status("connected", None)
                async for message in ws:
                    if self._stop_event.is_set():
                        await ws.close()
                        return
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self._handle_ws_payload(message.data)
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                        self._set_status("closed", "ws_closed")
                        return
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        self._set_status("error", f"ws_error:{ws.exception()}")
                        return

    def _handle_ws_payload(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._set_status("payload_error", f"json:{exc}")
            return
        rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            self._set_status("payload_error", f"unexpected_payload:{type(payload).__name__}")
            return
        now_ms = int(time.time() * 1000)
        updates: dict[str, ExchangeTickerSnapshot] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            market_id = str(row.get("s", "")).strip().upper()
            if not market_id:
                continue
            last_price = _optional_float(row.get("c"))
            quote_volume = _optional_float(row.get("q"))
            trade_count = _optional_int(row.get("n"))
            status = "ok" if last_price is not None and quote_volume is not None else "missing_fields"
            reason = None if status == "ok" else "ws_ticker_missing_last_or_quote_volume"
            updates[market_id] = ExchangeTickerSnapshot(
                symbol=market_id,
                fetched_at_ms=now_ms,
                last_price=last_price,
                quote_volume_24h=quote_volume,
                trade_count_24h=trade_count,
                last_price_source="binance_ws_all_ticker",
                quote_volume_source="binance_ws_all_ticker",
                trade_count_source="binance_ws_all_ticker",
                status=status,
                reason=reason,
            )
        if not updates:
            self._set_status("payload_error", "no_valid_ticker_rows")
            return
        with self._lock:
            self._ticker_by_market_id.update(updates)
            self._last_message_at_ms = now_ms
            self._last_error = None
            self._last_payload_source = "ws"
            self._connection_status = "connected"
            self._ready_event.set()

    def _set_status(self, status: str, error: str | None) -> None:
        with self._lock:
            self._connection_status = status
            self._last_error = error


class BinanceWsAggTradeBuffer:
    source_id = "binance_ws_aggtrade"

    def __init__(
        self,
        *,
        exchange: CcxtFuturesClient,
        buffer_minutes: int,
        stale_ms: int,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.exchange = exchange
        self.buffer_ms = max(60_000, int(buffer_minutes) * 60_000)
        self.stale_ms = int(stale_ms)
        self.logger = logger
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._target_market_ids: set[str] = set()
        self._symbol_by_market_id: dict[str, str] = {}
        self._subscribed_market_ids: set[str] = set()
        self._pending_subscribe_request_ids: dict[int, list[str]] = {}
        self._pending_unsubscribe_request_ids: dict[int, list[str]] = {}
        self._rows_by_market_id: dict[str, deque[dict[str, object]]] = {}
        self._active_since_ms_by_market_id: dict[str, int] = {}
        self._covered_until_ms_by_market_id: dict[str, int] = {}
        self._gap_ranges_by_market_id: dict[str, list[tuple[int, int]]] = {}
        self._last_aggtrade_id_by_market_id: dict[str, int] = {}
        self._last_trade_timestamp_by_market_id: dict[str, int] = {}
        self._last_receive_at_by_market_id: dict[str, int] = {}
        self._connection_status = "starting"
        self._last_error: str | None = None
        self._subscription_request_id = 0
        self._thread = threading.Thread(target=self._run_thread, name="binance-ws-aggtrade", daemon=True)
        self._thread.start()

    def set_symbols(self, symbols: tuple[str, ...] | list[str] | set[str]) -> dict[str, object]:
        target_market_ids: set[str] = set()
        symbol_by_market_id: dict[str, str] = {}
        for symbol in symbols:
            try:
                market_id = self.exchange.get_market_id(symbol)
            except Exception:
                continue
            market_id = str(market_id).strip().upper()
            if not market_id:
                continue
            target_market_ids.add(market_id)
            symbol_by_market_id[market_id] = str(symbol)
        now_ms = int(time.time() * 1000)
        with self._lock:
            removed = self._target_market_ids - target_market_ids
            self._target_market_ids = target_market_ids
            self._symbol_by_market_id = symbol_by_market_id
            for market_id in removed:
                self._rows_by_market_id.pop(market_id, None)
                self._active_since_ms_by_market_id.pop(market_id, None)
                self._covered_until_ms_by_market_id.pop(market_id, None)
                self._gap_ranges_by_market_id.pop(market_id, None)
                self._last_aggtrade_id_by_market_id.pop(market_id, None)
                self._last_trade_timestamp_by_market_id.pop(market_id, None)
                self._last_receive_at_by_market_id.pop(market_id, None)
            return {
                "source": self.source_id,
                "target_count": len(target_market_ids),
                "subscribed_count": len(self._subscribed_market_ids),
                "connection_status": self._connection_status,
                "last_error": self._last_error or "",
                "updated_at_ms": now_ms,
                "target_symbols": [symbol_by_market_id[market_id] for market_id in sorted(target_market_ids)],
            }

    def read_rows(self, symbol: str, *, start_timestamp_ms: int, end_timestamp_ms: int) -> WsAggTradeReadResult:
        market_id = str(self.exchange.get_market_id(symbol)).strip().upper()
        request_start = int(start_timestamp_ms)
        request_end = int(end_timestamp_ms)
        if request_start > request_end:
            return WsAggTradeReadResult((), (), "empty_request", None, False, "empty_request", None, None, None, 0)
        now_ms = int(time.time() * 1000)
        with self._lock:
            rows_deque = self._rows_by_market_id.get(market_id, deque())
            rows = [dict(row) for row in rows_deque]
            subscribed = market_id in self._subscribed_market_ids
            connection_status = self._connection_status
            last_error = self._last_error
            gap_ranges = list(self._gap_ranges_by_market_id.get(market_id, []))
            active_since_ms = self._active_since_ms_by_market_id.get(market_id)
            covered_until_ms = self._covered_until_ms_by_market_id.get(market_id)
            last_trade_timestamp_ms = self._last_trade_timestamp_by_market_id.get(market_id)
            last_receive_at_ms = self._last_receive_at_by_market_id.get(market_id)
            buffer_row_count = len(rows_deque)
        filtered = _filter_aggtrade_rows_by_time(
            rows,
            start_timestamp_ms=request_start,
            end_timestamp_ms=request_end,
        )
        filtered = _dedupe_aggtrade_rows(filtered)
        if not subscribed:
            return WsAggTradeReadResult(
                tuple(filtered),
                ((request_start, request_end),),
                "not_subscribed",
                "symbol_not_in_ws_subscription",
                False,
                connection_status,
                last_error,
                last_trade_timestamp_ms,
                last_receive_at_ms,
                buffer_row_count,
            )
        if connection_status != "connected":
            return WsAggTradeReadResult(
                tuple(filtered),
                ((request_start, request_end),),
                "not_connected",
                f"ws_status={connection_status}",
                True,
                connection_status,
                last_error,
                last_trade_timestamp_ms,
                last_receive_at_ms,
                buffer_row_count,
            )
        if covered_until_ms is not None and now_ms - int(covered_until_ms) > self.stale_ms:
            return WsAggTradeReadResult(
                tuple(filtered),
                ((request_start, request_end),),
                "stale",
                f"coverage_age_ms={now_ms - int(covered_until_ms)}",
                True,
                connection_status,
                last_error,
                last_trade_timestamp_ms,
                last_receive_at_ms,
                buffer_row_count,
            )
        coverage_start_ms = int(active_since_ms) if active_since_ms is not None else request_end + 1
        coverage_end_ms = int(covered_until_ms) if covered_until_ms is not None else now_ms
        missing_ranges = self._missing_ranges_from_coverage(
            request_start=request_start,
            request_end=request_end,
            coverage_start=coverage_start_ms,
            coverage_end=coverage_end_ms,
        )
        missing_ranges.extend(
            (max(request_start, gap_start), min(request_end, gap_end))
            for gap_start, gap_end in gap_ranges
            if max(request_start, gap_start) <= min(request_end, gap_end)
        )
        missing_ranges = self._merge_time_ranges(missing_ranges)
        status = "covered" if not missing_ranges else "partial"
        reason = None if not missing_ranges else "ws_rows_do_not_cover_full_requested_interval"
        return WsAggTradeReadResult(
            tuple(filtered),
            tuple(missing_ranges),
            status,
            reason,
            True,
            connection_status,
            last_error,
            last_trade_timestamp_ms,
            last_receive_at_ms,
            buffer_row_count,
        )

    def add_backfill_rows(
        self,
        symbol: str,
        rows: list[dict[str, object]],
        *,
        start_timestamp_ms: int | None = None,
        end_timestamp_ms: int | None = None,
    ) -> None:
        market_id = str(self.exchange.get_market_id(symbol)).strip().upper()
        now_ms = int(time.time() * 1000)
        with self._lock:
            if start_timestamp_ms is not None and end_timestamp_ms is not None:
                self._active_since_ms_by_market_id[market_id] = min(
                    int(start_timestamp_ms),
                    int(self._active_since_ms_by_market_id.get(market_id, start_timestamp_ms)),
                )
                self._covered_until_ms_by_market_id[market_id] = max(
                    int(end_timestamp_ms),
                    int(self._covered_until_ms_by_market_id.get(market_id, end_timestamp_ms)),
                )
                self._remove_gap_coverage_locked(
                    market_id,
                    start_timestamp_ms=int(start_timestamp_ms),
                    end_timestamp_ms=int(end_timestamp_ms),
                )
            if not rows:
                return
            rows_deque = self._rows_by_market_id.setdefault(market_id, deque())
            for row in rows:
                row_copy = dict(row)
                timestamp_ms = _resolve_aggtrade_timestamp(row_copy)
                if timestamp_ms is None:
                    continue
                rows_deque.append(row_copy)
                self._last_trade_timestamp_by_market_id[market_id] = max(
                    int(timestamp_ms),
                    int(self._last_trade_timestamp_by_market_id.get(market_id, timestamp_ms)),
                )
                self._last_receive_at_by_market_id[market_id] = now_ms
            self._prune_market_locked(market_id, now_ms=now_ms)

    def close(self) -> None:
        self._stop_event.set()

    def wait_for_targets(self, *, timeout_seconds: float) -> dict[str, object]:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            with self._lock:
                target = set(self._target_market_ids)
                subscribed = set(self._subscribed_market_ids)
                status = self._connection_status
                if target.issubset(subscribed) or time.monotonic() >= deadline or status != "connected":
                    return {
                        "target_count": len(target),
                        "subscribed_count": len(subscribed),
                        "connection_status": status,
                        "last_error": self._last_error or "",
                    }
            time.sleep(0.01)

    @staticmethod
    def _missing_ranges_from_rows(
        rows: list[dict[str, object]],
        *,
        request_start: int,
        request_end: int,
    ) -> list[tuple[int, int]]:
        if not rows:
            return [(request_start, request_end)]
        timestamps = [
            int(timestamp_ms)
            for timestamp_ms in (_resolve_aggtrade_timestamp(row) for row in rows)
            if timestamp_ms is not None
        ]
        if not timestamps:
            return [(request_start, request_end)]
        first_ts = min(timestamps)
        last_ts = max(timestamps)
        missing: list[tuple[int, int]] = []
        if request_start < first_ts:
            missing.append((request_start, first_ts - 1))
        if last_ts < request_end:
            missing.append((last_ts + 1, request_end))
        return missing

    @staticmethod
    def _missing_ranges_from_coverage(
        *,
        request_start: int,
        request_end: int,
        coverage_start: int,
        coverage_end: int,
    ) -> list[tuple[int, int]]:
        missing: list[tuple[int, int]] = []
        if coverage_start > request_start:
            missing.append((request_start, min(request_end, coverage_start - 1)))
        if coverage_end < request_end:
            missing.append((max(request_start, coverage_end + 1), request_end))
        return [(start, end) for start, end in missing if start <= end]

    @staticmethod
    def _merge_time_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        normalized = sorted((int(start), int(end)) for start, end in ranges if int(start) <= int(end))
        if not normalized:
            return []
        merged = [normalized[0]]
        for start, end in normalized[1:]:
            if start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _remove_gap_coverage_locked(self, market_id: str, *, start_timestamp_ms: int, end_timestamp_ms: int) -> None:
        remaining: list[tuple[int, int]] = []
        for gap_start, gap_end in self._gap_ranges_by_market_id.get(market_id, []):
            if int(end_timestamp_ms) < gap_start or int(start_timestamp_ms) > gap_end:
                remaining.append((gap_start, gap_end))
                continue
            if int(start_timestamp_ms) > gap_start:
                remaining.append((gap_start, int(start_timestamp_ms) - 1))
            if int(end_timestamp_ms) < gap_end:
                remaining.append((int(end_timestamp_ms) + 1, gap_end))
        if remaining:
            self._gap_ranges_by_market_id[market_id] = remaining
        else:
            self._gap_ranges_by_market_id.pop(market_id, None)

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._run_ws_loop())
            except Exception as exc:
                self._set_status("error", f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            if not self._stop_event.is_set():
                time.sleep(2.0)

    async def _run_ws_loop(self) -> None:
        import aiohttp

        self._set_status("connecting", None)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
        connector = _aiohttp_ws_connector()
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.ws_connect(BINANCE_FUTURES_COMBINED_WS_URL, heartbeat=20) as ws:
                with self._lock:
                    self._subscribed_market_ids = set()
                    self._pending_subscribe_request_ids = {}
                    self._pending_unsubscribe_request_ids = {}
                self._set_status("connected", None)
                while not self._stop_event.is_set():
                    await self._sync_subscriptions(ws)
                    try:
                        message = await asyncio.wait_for(ws.receive(), timeout=0.25)
                    except asyncio.TimeoutError:
                        self._mark_subscribed_coverage_until(int(time.time() * 1000))
                        continue
                    self._mark_subscribed_coverage_until(int(time.time() * 1000))
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self._handle_ws_payload(message.data)
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                        self._set_status("closed", "ws_closed")
                        return
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        self._set_status("error", f"ws_error:{ws.exception()}")
                        return
                await ws.close()

    def _mark_subscribed_coverage_until(self, timestamp_ms: int) -> None:
        with self._lock:
            if self._connection_status != "connected":
                return
            for market_id in self._subscribed_market_ids:
                self._covered_until_ms_by_market_id[market_id] = max(
                    int(timestamp_ms),
                    int(self._covered_until_ms_by_market_id.get(market_id, timestamp_ms)),
                )

    async def _sync_subscriptions(self, ws: object) -> None:
        with self._lock:
            target = set(self._target_market_ids)
            subscribed = set(self._subscribed_market_ids)
            pending_subscribe = {market_id for market_ids in self._pending_subscribe_request_ids.values() for market_id in market_ids}
            pending_unsubscribe = {market_id for market_ids in self._pending_unsubscribe_request_ids.values() for market_id in market_ids}
        to_subscribe = sorted(target - subscribed - pending_subscribe)
        to_unsubscribe = sorted((subscribed - target) - pending_unsubscribe)
        if to_subscribe:
            request_id = await self._send_subscription_message(ws, "SUBSCRIBE", to_subscribe)
            with self._lock:
                self._pending_subscribe_request_ids[request_id] = list(to_subscribe)
        if to_unsubscribe:
            request_id = await self._send_subscription_message(ws, "UNSUBSCRIBE", to_unsubscribe)
            with self._lock:
                self._pending_unsubscribe_request_ids[request_id] = list(to_unsubscribe)

    async def _send_subscription_message(self, ws: object, method: str, market_ids: list[str]) -> int:
        if not market_ids:
            return 0
        with self._lock:
            self._subscription_request_id += 1
            request_id = self._subscription_request_id
        payload = {
            "method": method,
            "params": [f"{market_id.lower()}@aggTrade" for market_id in market_ids],
            "id": request_id,
        }
        await ws.send_json(payload)
        return request_id

    def _handle_ws_payload(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._set_status("payload_error", f"json:{exc}")
            return
        if isinstance(payload, dict) and "result" in payload and "id" in payload:
            self._handle_subscription_ack(payload)
            return
        if isinstance(payload, dict) and ("code" in payload or "msg" in payload):
            request_id = _optional_int(payload.get("id"))
            if request_id is not None:
                with self._lock:
                    self._pending_subscribe_request_ids.pop(request_id, None)
                    self._pending_unsubscribe_request_ids.pop(request_id, None)
            self._set_status("subscription_error", f"{payload.get('code', '')}:{payload.get('msg', '')}"[:500])
            return
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            self._set_status("payload_error", f"unexpected_payload:{type(payload).__name__}")
            return
        if data.get("e") != "aggTrade":
            return
        market_id = str(data.get("s", "")).strip().upper()
        if not market_id:
            return
        timestamp_ms = _optional_int(data.get("T"))
        price = _optional_float(data.get("p"))
        quantity = _optional_float(data.get("q"))
        if timestamp_ms is None or price is None or quantity is None:
            self._set_status("payload_error", f"aggtrade_missing_required_fields:{market_id}")
            return
        aggtrade_id = _optional_int(data.get("a"))
        row = {
            "a": aggtrade_id if aggtrade_id is not None else data.get("a"),
            "p": str(data.get("p")),
            "q": str(data.get("q")),
            "T": int(timestamp_ms),
            "m": bool(data.get("m")),
        }
        now_ms = int(time.time() * 1000)
        with self._lock:
            previous_id = self._last_aggtrade_id_by_market_id.get(market_id)
            previous_ts = self._last_trade_timestamp_by_market_id.get(market_id)
            if aggtrade_id is not None and previous_id is not None and aggtrade_id > previous_id + 1 and previous_ts is not None:
                gap_start = min(int(previous_ts) + 1, int(timestamp_ms))
                gap_end = max(int(previous_ts) + 1, int(timestamp_ms) - 1)
                if gap_start <= gap_end:
                    gaps = self._gap_ranges_by_market_id.setdefault(market_id, [])
                    gaps.append((gap_start, gap_end))
                    self._gap_ranges_by_market_id[market_id] = self._merge_time_ranges(gaps)
            rows_deque = self._rows_by_market_id.setdefault(market_id, deque())
            rows_deque.append(row)
            if aggtrade_id is not None:
                self._last_aggtrade_id_by_market_id[market_id] = int(aggtrade_id)
            self._last_trade_timestamp_by_market_id[market_id] = int(timestamp_ms)
            self._last_receive_at_by_market_id[market_id] = now_ms
            self._covered_until_ms_by_market_id[market_id] = max(
                int(timestamp_ms),
                int(self._covered_until_ms_by_market_id.get(market_id, timestamp_ms)),
            )
            self._connection_status = "connected"
            self._last_error = None
            self._prune_market_locked(market_id, now_ms=now_ms)

    def _handle_subscription_ack(self, payload: dict[str, object]) -> None:
        request_id = _optional_int(payload.get("id"))
        if request_id is None:
            return
        now_ms = int(time.time() * 1000)
        with self._lock:
            subscribed = self._pending_subscribe_request_ids.pop(request_id, None)
            unsubscribed = self._pending_unsubscribe_request_ids.pop(request_id, None)
            if subscribed:
                for market_id in subscribed:
                    self._subscribed_market_ids.add(market_id)
                    self._rows_by_market_id[market_id] = deque()
                    self._active_since_ms_by_market_id[market_id] = now_ms
                    self._covered_until_ms_by_market_id[market_id] = now_ms
                    self._gap_ranges_by_market_id.pop(market_id, None)
                    self._last_aggtrade_id_by_market_id.pop(market_id, None)
                    self._last_trade_timestamp_by_market_id.pop(market_id, None)
                    self._last_receive_at_by_market_id.pop(market_id, None)
            if unsubscribed:
                for market_id in unsubscribed:
                    self._subscribed_market_ids.discard(market_id)
                    self._rows_by_market_id.pop(market_id, None)
                    self._active_since_ms_by_market_id.pop(market_id, None)
                    self._covered_until_ms_by_market_id.pop(market_id, None)
                    self._gap_ranges_by_market_id.pop(market_id, None)
                    self._last_aggtrade_id_by_market_id.pop(market_id, None)
                    self._last_trade_timestamp_by_market_id.pop(market_id, None)
                    self._last_receive_at_by_market_id.pop(market_id, None)
            if subscribed or unsubscribed:
                self._connection_status = "connected"
                self._last_error = None

    def _prune_market_locked(self, market_id: str, *, now_ms: int) -> None:
        rows_deque = self._rows_by_market_id.get(market_id)
        if rows_deque is None:
            return
        min_timestamp_ms = int(now_ms) - self.buffer_ms
        while rows_deque:
            timestamp_ms = _resolve_aggtrade_timestamp(rows_deque[0])
            if timestamp_ms is None or int(timestamp_ms) >= min_timestamp_ms:
                break
            rows_deque.popleft()

    def _set_status(self, status: str, error: str | None) -> None:
        with self._lock:
            self._connection_status = status
            self._last_error = error


LIVE_DEFAULT_EXCLUDED_HIGH_CAP_BASES: frozenset[str] = frozenset(
    {
        "BTC",
        "ETH",
        "BNB",
        "SOL",
        "XRP",
        "DOGE",
        "ADA",
        "TRX",
        "LINK",
        "AVAX",
        "TON",
        "SHIB",
        "SUI",
        "HBAR",
        "XLM",
        "UNI",
        "ETC",
        "NEAR",
        "APT",
        "ICP",
        "ATOM",
        "FIL",
        "ARB",
        "OP",
        "AAVE",
        "INJ",
        "TIA",
        "WIF",
        "SEI",
        "ENA",
        "TAO",
        "WLD",
        "FET",
        "RENDER",
        "ALGO",
        "VET",
        "LTC",
        "BCH",
        "DOT",
    }
)


@dataclass(frozen=True, slots=True)
class LiveAnomalyConfig:
    results_dir: Path
    symbols: tuple[str, ...]
    confirm_real_orders: bool
    cache_dir: Path | None = None
    timeframe_pairs: tuple[tuple[Timeframe, Timeframe], ...] = ANOMALY_LIVE_TIMEFRAME_PAIRS
    pump_categories: tuple[str, ...] = LIVE_DEFAULT_PUMP_CATEGORY_IDS
    baseline_candles: int = 60
    confirmation_candles: int = 4
    min_quote_ratio_start: float = 4.0
    min_trade_ratio_start: float = 4.0
    max_start_quote_ratio: float | None = 100.0
    max_start_trade_ratio: float | None = 55.0
    max_start_avg_trade_quote_size_ratio: float | None = 9.0
    max_start_quote_ratio_per_abs_return: float | None = 15_000.0
    max_start_trade_ratio_per_abs_return: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = 35.0
    min_next_taker_buy_quote_share: float | None = 0.48
    max_start_taker_buy_quote_share_delta: float | None = None
    max_price_retention: float | None = 0.98
    min_price_retention: float = 0.65
    min_verticality_score: float = 0.20
    min_hold_count: int = 2
    min_oi_change_pct_3x5m: float | None = None
    min_mark_close_vs_decision_close_basis: float | None = None
    max_initial_risk_pct: float = 0.16
    stop_buffer_range_fraction: float = 0.05
    max_prior_up_down_whipsaw_to_impulse_range: float | None = 0.75
    position_notional_usdt: float = 12.0
    max_open_positions: int = 3
    exclude_default_high_cap_symbols: bool = True
    symbol_batch_size: int = 20
    inactive_scan_slots_per_cycle: int | None = None
    scan_hot_timeframes_per_symbol: bool = True
    active_symbol_ttl_ms: int = 60_000
    ticker_radar_enabled: bool = True
    live_ws_ticker_enabled: bool = True
    live_ws_ticker_stale_ms: int = 5_000
    live_ws_ticker_startup_wait_seconds: float = 10.0
    live_ws_ticker_startup_seed_enabled: bool = True
    live_ws_aggtrade_enabled: bool = True
    live_ws_aggtrade_stale_ms: int = 5_000
    live_ws_aggtrade_buffer_minutes: int = DEFAULT_LIVE_WS_AGGTRADE_BUFFER_MINUTES
    live_ws_aggtrade_max_backfill_ms: int = DEFAULT_LIVE_WS_AGGTRADE_MAX_BACKFILL_MS
    live_aggtrade_rest_cache_ttl_ms: int = DEFAULT_LIVE_AGGTRADE_REST_CACHE_TTL_MS
    live_aggtrade_rest_cache_padding_ms: int = DEFAULT_LIVE_AGGTRADE_REST_CACHE_PADDING_MS
    ticker_radar_interval_seconds: float = 5.0
    ticker_radar_watch_ttl_ms: int = 120_000
    ticker_radar_watch_batch_size: int = 5
    ticker_radar_max_promotions_per_cycle: int = 20
    max_precise_scan_symbols_per_cycle: int | None = None
    latency_sla_controller_enabled: bool = True
    latency_sla_due_scan_p95_seconds: float = DEFAULT_LATENCY_SLA_DUE_SCAN_P95_SECONDS
    latency_sla_min_due_samples: int = DEFAULT_LATENCY_SLA_MIN_DUE_SAMPLES
    ticker_radar_min_price_delta_pct: float = 0.003
    ticker_radar_min_quote_volume_delta_usdt: float = 10_000.0
    ticker_radar_min_quote_volume_delta_ratio: float = 3.0
    warm_watch_enabled: bool = True
    warm_watch_ttl_ms: int = DEFAULT_WARM_WATCH_TTL_MS
    warm_watch_min_observations_for_precise: int = DEFAULT_WARM_WATCH_MIN_OBSERVATIONS_FOR_PRECISE
    warm_watch_min_price_delta_pct: float = DEFAULT_WARM_WATCH_MIN_PRICE_DELTA_PCT
    warm_watch_max_price_delta_pct: float = DEFAULT_WARM_WATCH_MAX_PRICE_DELTA_PCT
    warm_watch_aggtrade_target_cap: int = DEFAULT_WARM_WATCH_AGGTRADE_TARGET_CAP
    prepump_warm_watch_scoring_enabled: bool = False
    prepump_warm_watch_profile_csv: Path | None = None
    prepump_warm_watch_min_abs_standardized_diff: float = DEFAULT_PREPUMP_WARM_WATCH_MIN_ABS_STANDARDIZED_DIFF
    prepump_warm_watch_min_runner_rows: int = DEFAULT_PREPUMP_WARM_WATCH_MIN_RUNNER_ROWS
    prepump_warm_watch_min_fader_rows: int = DEFAULT_PREPUMP_WARM_WATCH_MIN_FADER_ROWS
    prepump_warm_watch_max_features: int = DEFAULT_PREPUMP_WARM_WATCH_MAX_FEATURES
    prepump_warm_watch_score_weight: float = DEFAULT_PREPUMP_WARM_WATCH_SCORE_WEIGHT
    prepump_warm_watch_windows: str = DEFAULT_PREPUMP_CONTEXT_WINDOWS
    prepump_warm_watch_min_coverage_ratio: float = DEFAULT_PREPUMP_WARM_WATCH_MIN_COVERAGE_RATIO
    symbol_context_snapshot_enabled: bool = True
    symbol_context_snapshot_interval_seconds: float = 60.0
    symbol_context_snapshot_symbols_per_cycle: int = 20
    symbol_context_snapshot_fresh_ms: int = 15 * 60_000
    symbol_context_snapshot_max_cycle_seconds: float = DEFAULT_SYMBOL_CONTEXT_SNAPSHOT_MAX_CYCLE_SECONDS
    symbol_context_snapshot_min_coverage_ratio: float = DEFAULT_SYMBOL_CONTEXT_SNAPSHOT_MIN_COVERAGE_RATIO
    symbol_context_snapshot_max_gap_candles: int = DEFAULT_SYMBOL_CONTEXT_SNAPSHOT_MAX_GAP_CANDLES
    symbol_context_priority_ttl_ms: int = DEFAULT_SYMBOL_CONTEXT_PRIORITY_TTL_MS
    danger_ticker_flow_radar_enabled: bool = True
    danger_ticker_flow_radar_min_quote_volume_delta_usdt: float = DANGER_TICKER_FLOW_RADAR_MIN_QUOTE_VOLUME_DELTA_USDT
    danger_ticker_flow_radar_min_trade_count_delta: int = DANGER_TICKER_FLOW_RADAR_MIN_TRADE_COUNT_DELTA
    danger_ticker_flow_radar_min_trade_count_delta_ratio: float = DANGER_TICKER_FLOW_RADAR_MIN_TRADE_COUNT_DELTA_RATIO
    danger_ticker_flow_radar_min_price_delta_pct: float = DANGER_TICKER_FLOW_RADAR_MIN_PRICE_DELTA_PCT
    danger_ticker_flow_radar_max_price_delta_pct: float = DANGER_TICKER_FLOW_RADAR_MAX_PRICE_DELTA_PCT
    danger_local_entry_position_guard_enabled: bool = True
    signal_scan_backfill_candles: int = 10
    max_signal_age_ms: int = 60_000
    max_entry_price_drift_pct: float = 0.003
    min_executable_rr_to_signal_tp1: float = 0.75
    discrete_signal_missed_telegram_enabled: bool = True
    max_position_amount_slippage_ratio: float = 0.05
    max_monitor_empty_ohlcv_cycles: int = 3
    scan_sleep_seconds: float = 2.0
    network_sleep_seconds: float = 30.0
    danger_continue_after_order_position_errors: bool = False
    live_ohlcv_cache_enabled: bool = True
    live_ohlcv_cache_write_enabled: bool = True
    live_ohlcv_cache_flush_interval_seconds: float = 30.0
    live_ohlcv_cache_max_buffer_rows: int = 50_000
    live_ohlcv_cache_flush_max_symbol_timeframes: int | None = DEFAULT_LIVE_OHLCV_CACHE_FLUSH_MAX_SYMBOL_TIMEFRAMES
    delayed_replay_enabled: bool = False
    delayed_replay_delay_seconds: float = 300.0
    delayed_replay_min_idle_seconds: float = 45.0
    delayed_replay_max_cases_per_cycle: int = 3
    delayed_replay_max_cycle_seconds: float = 1.5
    delayed_replay_max_queue_size: int = 2000
    delayed_replay_outcome_lookahead_seconds: float = 300.0
    max_cycles: int | None = None
    stop_cooldown_hours: float = 12.0
    stop_limit_per_symbol: int = 2
    telegram_cooldown_seconds: float = 900.0
    oi_fresh_ms: int = 5 * 60 * 1000
    trail_lookback_candles: int = 5
    trail_buffer_r: float = 0.10
    order_reconcile_interval_cycles: int = 10
    order_reconcile_batch_size: int = 25


def _safe_profile_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_profile_int(value: object) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number


def _load_prepump_warm_watch_scoring_profile(
    config: LiveAnomalyConfig,
) -> LivePrepumpWarmWatchScoringProfile:
    if not config.prepump_warm_watch_scoring_enabled:
        return LivePrepumpWarmWatchScoringProfile(enabled=False, status="disabled", reason="disabled_by_config")
    if config.prepump_warm_watch_profile_csv is None:
        raise LiveStartupError(
            "Некорректный live config: prepump warm-watch scoring enabled, "
            "but --prepump-warm-watch-profile-csv is not set"
        )
    path = Path(config.prepump_warm_watch_profile_csv)
    if not path.exists() or not path.is_file():
        raise LiveStartupError(f"prepump warm-watch profile csv not found: {path}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise LiveStartupError(f"cannot read prepump warm-watch profile csv {path}: {type(exc).__name__}: {exc}") from exc
    required_columns = {
        "status",
        "feature",
        "runner_count",
        "fader_count",
        "runner_mean",
        "fader_mean",
        "standardized_diff",
    }
    missing = sorted(required_columns.difference(frame.columns))
    if missing:
        raise LiveStartupError(
            f"prepump warm-watch profile csv is missing required columns: {','.join(missing)}"
        )
    rules: list[LivePrepumpWarmWatchFeatureRule] = []
    min_abs_std = float(config.prepump_warm_watch_min_abs_standardized_diff)
    min_runner_rows = int(config.prepump_warm_watch_min_runner_rows)
    min_fader_rows = int(config.prepump_warm_watch_min_fader_rows)
    for _, row in frame.iterrows():
        if str(row.get("status", "")).strip() != "ok":
            continue
        feature = str(row.get("feature", "")).strip()
        if not feature.startswith("pre_"):
            continue
        if feature.endswith("_coverage_ratio") or feature.endswith("_candles") or feature.endswith("_expected_candles"):
            continue
        runner_count = _safe_profile_int(row.get("runner_count"))
        fader_count = _safe_profile_int(row.get("fader_count"))
        runner_mean = _safe_profile_float(row.get("runner_mean"))
        fader_mean = _safe_profile_float(row.get("fader_mean"))
        standardized_diff = _safe_profile_float(row.get("standardized_diff"))
        if runner_count is None or fader_count is None:
            continue
        if runner_count < min_runner_rows or fader_count < min_fader_rows:
            continue
        if runner_mean is None or fader_mean is None or standardized_diff is None:
            continue
        if runner_mean == fader_mean:
            continue
        if abs(standardized_diff) < min_abs_std:
            continue
        rules.append(
            LivePrepumpWarmWatchFeatureRule(
                feature=feature,
                runner_mean=runner_mean,
                fader_mean=fader_mean,
                standardized_diff=standardized_diff,
                runner_count=runner_count,
                fader_count=fader_count,
                weight=min(abs(standardized_diff), 3.0),
            )
        )
    rules.sort(key=lambda item: (abs(item.standardized_diff), item.feature), reverse=True)
    max_features = max(1, int(config.prepump_warm_watch_max_features))
    selected = tuple(rules[:max_features])
    if not selected:
        raise LiveStartupError(
            "prepump warm-watch profile csv has no usable features after stability filters; "
            "do not enable live scoring until the 30d runner/fader separation artifact has enough stable rows"
        )
    return LivePrepumpWarmWatchScoringProfile(
        enabled=True,
        status="ok",
        source_path=str(path),
        reason="ok",
        rules=selected,
    )


@dataclass(slots=True)
class LiveSignal:
    category_id: str
    category_label: str
    category_priority: int
    symbol: str
    levels_timeframe: Timeframe
    entry_timeframe: Timeframe
    setup_source: str
    setup_elapsed_fraction: float
    setup_closed_entry_candles: int
    decision_timestamp_ms: int
    start_timestamp_ms: int
    session: str
    entry_price: float
    stop_price: float
    tp1_price: float
    box_high: float
    initial_risk: float
    initial_risk_pct: float
    quote_ratio_start: float
    trade_ratio_start: float
    price_retention: float
    hold_count: int
    verticality_score: float
    oi_change_pct_3x5m: float | None
    previous_live_scan_closed_timestamp_ms: int | None = None
    first_unscanned_decision_timestamp_ms: int | None = None
    live_scan_gap_ltf_candles: int = 0
    category_rejections: list[dict[str, object]] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, allow_nan=False)


def _live_signal_from_json(payload: object) -> LiveSignal | None:
    if not isinstance(payload, str) or not payload.strip():
        return None
    try:
        raw = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    def as_str_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None]

    def as_dict_list(value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    try:
        levels_timeframe = Timeframe(str(raw.get("levels_timeframe") or ""))
        entry_timeframe = Timeframe(str(raw.get("entry_timeframe") or ""))
    except ValueError:
        return None

    category_priority = _optional_int(raw.get("category_priority"))
    decision_timestamp_ms = _optional_int(raw.get("decision_timestamp_ms"))
    start_timestamp_ms = _optional_int(raw.get("start_timestamp_ms"))
    entry_price = _optional_float(raw.get("entry_price"))
    stop_price = _optional_float(raw.get("stop_price"))
    tp1_price = _optional_float(raw.get("tp1_price"))
    box_high = _optional_float(raw.get("box_high"))
    initial_risk = _optional_float(raw.get("initial_risk"))
    initial_risk_pct = _optional_float(raw.get("initial_risk_pct"))
    quote_ratio_start = _optional_float(raw.get("quote_ratio_start"))
    trade_ratio_start = _optional_float(raw.get("trade_ratio_start"))
    price_retention = _optional_float(raw.get("price_retention"))
    hold_count = _optional_int(raw.get("hold_count"))
    verticality_score = _optional_float(raw.get("verticality_score"))
    if (
        category_priority is None
        or decision_timestamp_ms is None
        or start_timestamp_ms is None
        or entry_price is None
        or stop_price is None
        or tp1_price is None
        or box_high is None
        or initial_risk is None
        or initial_risk_pct is None
        or quote_ratio_start is None
        or trade_ratio_start is None
        or price_retention is None
        or hold_count is None
        or verticality_score is None
    ):
        return None

    return LiveSignal(
        category_id=str(raw.get("category_id") or ""),
        category_label=str(raw.get("category_label") or ""),
        category_priority=int(category_priority),
        symbol=str(raw.get("symbol") or ""),
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        setup_source=str(raw.get("setup_source") or ""),
        setup_elapsed_fraction=float(_optional_float(raw.get("setup_elapsed_fraction")) or 0.0),
        setup_closed_entry_candles=int(_optional_int(raw.get("setup_closed_entry_candles")) or 0),
        decision_timestamp_ms=int(decision_timestamp_ms),
        start_timestamp_ms=int(start_timestamp_ms),
        session=str(raw.get("session") or ""),
        entry_price=float(entry_price),
        stop_price=float(stop_price),
        tp1_price=float(tp1_price),
        box_high=float(box_high),
        initial_risk=float(initial_risk),
        initial_risk_pct=float(initial_risk_pct),
        quote_ratio_start=float(quote_ratio_start),
        trade_ratio_start=float(trade_ratio_start),
        price_retention=float(price_retention),
        hold_count=int(hold_count),
        verticality_score=float(verticality_score),
        oi_change_pct_3x5m=_optional_float(raw.get("oi_change_pct_3x5m")),
        previous_live_scan_closed_timestamp_ms=_optional_int(raw.get("previous_live_scan_closed_timestamp_ms")),
        first_unscanned_decision_timestamp_ms=_optional_int(raw.get("first_unscanned_decision_timestamp_ms")),
        live_scan_gap_ltf_candles=int(_optional_int(raw.get("live_scan_gap_ltf_candles")) or 0),
        category_rejections=as_dict_list(raw.get("category_rejections")),
        strengths=as_str_list(raw.get("strengths")),
        weaknesses=as_str_list(raw.get("weaknesses")),
    )


@dataclass(slots=True)
class LiveDependencyRetryCooldown:
    symbol: str
    symbol_key: str
    levels_timeframe: str
    entry_timeframe: str
    decision_timestamp_ms: int
    blocked_at_ms: int
    next_retry_at_ms: int
    expires_at_ms: int
    retry_reason: str
    retryable_reasons: tuple[str, ...]


@dataclass(slots=True)
class LiveActiveSymbol:
    symbol: str
    reason: str
    expires_at_ms: int
    updated_at_ms: int
    decision_timestamp_ms: int | None = None


@dataclass(slots=True)
class LiveTickerRadarWatch:
    symbol: str
    reason: str
    expires_at_ms: int
    updated_at_ms: int
    score: float
    price_delta_pct: float
    quote_volume_delta: float
    quote_volume_delta_ratio: float | None
    trade_count_delta: int | None = None
    trade_count_delta_ratio: float | None = None
    promotion_source: str = "ticker_price_volume"
    base_score: float | None = None
    prepump_warm_watch_score_status: str = "not_evaluated"
    prepump_warm_watch_score_reason: str = ""
    prepump_warm_watch_score: float | None = None
    prepump_warm_watch_score_adjustment: float = 0.0
    prepump_warm_watch_features_used: int = 0
    prepump_warm_watch_features_missing: int = 0
    prepump_warm_watch_top_features: tuple[str, ...] = ()


@dataclass(slots=True)
class LiveWarmWatch:
    symbol: str
    reason: str
    expires_at_ms: int
    updated_at_ms: int
    first_seen_ms: int
    observations: int
    score: float
    price_delta_pct: float
    quote_volume_delta: float
    quote_volume_delta_ratio: float | None
    trade_count_delta: int | None = None
    trade_count_delta_ratio: float | None = None
    promotion_source: str = "ticker_price_volume"
    base_score: float | None = None
    prepump_warm_watch_score_status: str = "not_evaluated"
    prepump_warm_watch_score_reason: str = ""
    prepump_warm_watch_score: float | None = None
    prepump_warm_watch_score_adjustment: float = 0.0
    prepump_warm_watch_features_used: int = 0
    prepump_warm_watch_features_missing: int = 0
    prepump_warm_watch_top_features: tuple[str, ...] = ()


def _symbol_context_csv_float(value: float | None) -> float | str:
    if value is None:
        return ""
    number = float(value)
    return number if math.isfinite(number) else ""


def _symbol_context_join_timestamps(values: tuple[int, ...]) -> str:
    return ";".join(str(int(value)) for value in values)


@dataclass(slots=True)
class LiveSymbolContextSnapshot:
    symbol: str
    levels_timeframe: Timeframe
    entry_timeframe: Timeframe
    snapshot_timestamp_ms: int
    status: str
    reason: str
    source: str
    context_timeframe: Timeframe
    history_start_timestamp_ms: int
    context_start_timestamp_ms: int
    context_cache_end_timestamp_ms: int | None
    effective_cache_end_timestamp_ms: int | None
    ignored_tail_ms: int | None
    baseline_candles: int
    baseline_status: str
    baseline_quote_volume_median: float | None
    baseline_trade_count_median: float | None
    baseline_range_pct_median: float | None
    latest_context_close: float | None
    prior_spike_timestamps_ms: tuple[int, ...] = ()
    prior_fast_fade_timestamps_ms: tuple[int, ...] = ()
    prepump_spot_features: dict[str, object] = field(default_factory=dict)
    prepump_spot_windows: str = ""
    compute_seconds: float = 0.0

    def key(self) -> tuple[str, str, str]:
        return (
            _position_symbol_key(self.symbol),
            self.levels_timeframe.value,
            self.entry_timeframe.value,
        )

    def to_row(self) -> dict[str, object]:
        return {
            "snapshot_timestamp_utc": datetime.fromtimestamp(
                int(self.snapshot_timestamp_ms) / 1000, UTC
            ).isoformat(),
            "snapshot_timestamp_ms": int(self.snapshot_timestamp_ms),
            "symbol": self.symbol,
            "levels_tf": self.levels_timeframe.value,
            "entry_tf": self.entry_timeframe.value,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
            "context_timeframe": self.context_timeframe.value,
            "history_start_timestamp_ms": int(self.history_start_timestamp_ms),
            "context_start_timestamp_ms": int(self.context_start_timestamp_ms),
            "context_cache_end_timestamp_ms": (
                int(self.context_cache_end_timestamp_ms)
                if self.context_cache_end_timestamp_ms is not None
                else ""
            ),
            "effective_cache_end_timestamp_ms": (
                int(self.effective_cache_end_timestamp_ms)
                if self.effective_cache_end_timestamp_ms is not None
                else ""
            ),
            "ignored_tail_ms": int(self.ignored_tail_ms) if self.ignored_tail_ms is not None else "",
            "baseline_candles": int(self.baseline_candles),
            "baseline_status": self.baseline_status,
            "baseline_quote_volume_median": _symbol_context_csv_float(self.baseline_quote_volume_median),
            "baseline_trade_count_median": _symbol_context_csv_float(self.baseline_trade_count_median),
            "baseline_range_pct_median": _symbol_context_csv_float(self.baseline_range_pct_median),
            "latest_context_close": _symbol_context_csv_float(self.latest_context_close),
            "prior_spike_count_available": int(len(self.prior_spike_timestamps_ms)),
            "prior_fast_fade_count_available": int(len(self.prior_fast_fade_timestamps_ms)),
            "prior_spike_timestamps_ms": _symbol_context_join_timestamps(self.prior_spike_timestamps_ms),
            "prior_fast_fade_timestamps_ms": _symbol_context_join_timestamps(
                self.prior_fast_fade_timestamps_ms
            ),
            "prepump_spot_feature_contract": PREPUMP_WARM_WATCH_SCORING_CONTRACT,
            "prepump_spot_windows": self.prepump_spot_windows,
            "prepump_spot_features_json": json.dumps(
                self.prepump_spot_features, ensure_ascii=False, sort_keys=True, allow_nan=False
            ) if self.prepump_spot_features else "",
            "compute_seconds": round(float(self.compute_seconds), 6),
        }


@dataclass(frozen=True, slots=True)
class LiveSymbolContextPriorityRequest:
    symbol: str
    reason: str
    first_seen_ms: int
    last_seen_ms: int
    expires_at_ms: int
    hit_count: int = 1


@dataclass(frozen=True, slots=True)
class LiveLatencySlaStatus:
    enabled: bool
    status: str
    optional_scans_allowed: bool
    due_scan_p95_seconds: float | None
    due_scan_max_seconds: float | None
    due_scan_samples: int
    threshold_seconds: float
    min_due_samples: int
    reason: str

    def event_payload(self) -> dict[str, object]:
        return {
            "latency_sla_enabled": bool(self.enabled),
            "latency_sla_status": self.status,
            "latency_sla_optional_scans_allowed": bool(self.optional_scans_allowed),
            "latency_sla_due_scan_p95_seconds": (
                round(float(self.due_scan_p95_seconds), 3)
                if self.due_scan_p95_seconds is not None
                else ""
            ),
            "latency_sla_due_scan_max_seconds": (
                round(float(self.due_scan_max_seconds), 3)
                if self.due_scan_max_seconds is not None
                else ""
            ),
            "latency_sla_due_scan_samples": int(self.due_scan_samples),
            "latency_sla_threshold_seconds": float(self.threshold_seconds),
            "latency_sla_min_due_samples": int(self.min_due_samples),
            "latency_sla_reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LiveCandidateQueuePressureStats:
    status: str = "not_evaluated"
    reason: str = ""
    radar_total_before: int = 0
    radar_total_after: int = 0
    warm_total_before: int = 0
    warm_total_after: int = 0
    actionable_sample_count: int = 0
    dropped_pressure_count: int = 0
    expired_backlog_stale_count: int = 0
    top_score: float | None = None


@dataclass(frozen=True, slots=True)
class LiveAdaptivePreciseBudget:
    status: str = "not_evaluated"
    reason: str = ""
    limit: int | None = None
    radar_slots: int | None = None
    active_due_count: int = 0
    active_waiting_count: int = 0
    manual_cap: int | None = None
    cycle_seconds_ewma: float = 0.0
    pressure_ewma: float = 0.0


@dataclass(frozen=True, slots=True)
class LiveSymbolBatchSelection:
    scheduler_source: str
    active_due: tuple[str, ...]
    active_waiting: tuple[str, ...]
    radar_due: tuple[str, ...]
    radar_waiting: tuple[str, ...]
    warm_watch_waiting: tuple[str, ...]
    latency_sla_status: str
    latency_sla_optional_scans_allowed: bool
    latency_sla_due_scan_p95_seconds: float | None
    latency_sla_due_scan_max_seconds: float | None
    latency_sla_due_scan_samples: int
    latency_sla_threshold_seconds: float
    latency_sla_min_due_samples: int
    latency_sla_reason: str
    candidate_queue_status: str
    candidate_queue_reason: str
    candidate_queue_radar_total_before: int
    candidate_queue_radar_total_after: int
    candidate_queue_warm_total_before: int
    candidate_queue_warm_total_after: int
    candidate_queue_actionable_sample_count: int
    candidate_queue_dropped_pressure_count: int
    candidate_queue_expired_backlog_stale_count: int
    candidate_queue_top_score: float | None
    adaptive_precise_budget_status: str
    adaptive_precise_budget_reason: str
    adaptive_precise_budget_limit: int | None
    adaptive_precise_budget_radar_slots: int | None
    adaptive_precise_budget_active_due_count: int
    adaptive_precise_budget_active_waiting_count: int
    adaptive_precise_budget_manual_cap: int | None
    inactive: tuple[str, ...]
    batch: tuple[str, ...]
    scan_modes: dict[str, str]
    inactive_cursor_before: int
    inactive_cursor_after: int
    batch_in_full_cycle: int
    full_symbol_cycle: int
    precise_budget_remaining_after_active: int | None
    precise_budget_remaining_after_radar: int | None
    inactive_scan_slots: int
    inactive_scan_slots_source: str
    inactive_cold_coverage_danger: bool
    inactive_cold_coverage_gate_reason: str
    inactive_cold_coverage_health_pct: float
    inactive_cold_coverage_health_threshold_pct: float
    inactive_cold_coverage_active_blocked: bool
    inactive_cold_coverage_position_blocked: bool
    inactive_cold_coverage_active_due_count: int
    inactive_cold_coverage_active_waiting_count: int
    inactive_cold_coverage_adaptive_score: float
    inactive_cold_coverage_health_factor: float
    inactive_cold_coverage_speed_factor: float
    inactive_cold_coverage_active_factor: float
    inactive_cold_coverage_load_factor: float
    inactive_cold_coverage_pressure_ewma: float
    inactive_cold_coverage_cycle_seconds_ewma: float
    inactive_cold_coverage_base_slots: int
    inactive_cold_coverage_max_slots: int


@dataclass(frozen=True, slots=True)
class LiveSignalScanResult:
    signal: LiveSignal | None
    decision_timestamp_ms: int | None = None
    retryable_dependency: bool = False
    retry_reason: str = ""
    retryable_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveTickerRadarCycleStats:
    enabled: bool
    attempted: bool
    status: str
    source: str
    symbols_total: int = 0
    ok_count: int = 0
    missing_count: int = 0
    promoted_count: int = 0
    promotion_candidates_count: int = 0
    warm_watch_marked_count: int = 0
    warm_watch_promoted_count: int = 0
    warm_watch_rejected_count: int = 0
    warm_watch_deferred_count: int = 0
    danger_flow_radar_promoted_count: int = 0
    danger_flow_radar_candidate_count: int = 0
    reason: str = ""


@dataclass(slots=True)
class LiveSessionTopSymbolState:
    symbol: str
    price_points: deque[tuple[int, float, str]] = field(default_factory=deque)


def _prune_session_top_points(
    points: deque[tuple[int, float, str]],
    *,
    cutoff_ms: int,
) -> None:
    while points and int(points[0][0]) < int(cutoff_ms):
        points.popleft()


class LiveSessionTopTracker:
    def __init__(self, *, limit: int) -> None:
        if limit < 1:
            raise LiveStartupError(f"Некорректный live session top limit: {limit}")
        self.limit = int(limit)
        self._states: dict[str, LiveSessionTopSymbolState] = {}
        self._last_snapshot_ms = 0
        self._last_source = ""
        self._last_source_status = "not_started"
        self._last_source_reason = "ticker_snapshots_not_received_yet"

    def update_from_ticker_snapshots(
        self,
        snapshots: list[ExchangeTickerSnapshot],
        *,
        now_ms: int,
        source: str,
        source_status: str,
        source_reason: str,
    ) -> None:
        now_ms = int(now_ms)
        cutoff_ms = now_ms - int(LIVE_SESSION_TOP_ROLLING_WINDOW_MS)
        self._last_snapshot_ms = now_ms
        self._last_source = str(source or "")
        self._last_source_status = str(source_status or "")
        self._last_source_reason = str(source_reason or "")
        for state in list(self._states.values()):
            _prune_session_top_points(state.price_points, cutoff_ms=cutoff_ms)
        for symbol_key, state in list(self._states.items()):
            if not state.price_points:
                self._states.pop(symbol_key, None)
        for snapshot in snapshots:
            if snapshot.status != "ok":
                continue
            price = _finite_or_none(snapshot.last_price)
            if price is None or price <= 0.0:
                continue
            fetched_at_ms = int(snapshot.fetched_at_ms)
            if fetched_at_ms < cutoff_ms or fetched_at_ms > now_ms + 60_000:
                continue
            symbol_key = _position_symbol_key(snapshot.symbol)
            if not symbol_key:
                continue
            state = self._states.get(symbol_key)
            if state is None:
                state = LiveSessionTopSymbolState(symbol=snapshot.symbol)
                self._states[symbol_key] = state
            if state.price_points and int(state.price_points[-1][0]) == fetched_at_ms:
                state.price_points[-1] = (fetched_at_ms, float(price), str(snapshot.last_price_source or ""))
            elif not state.price_points or fetched_at_ms > int(state.price_points[-1][0]):
                state.price_points.append((fetched_at_ms, float(price), str(snapshot.last_price_source or "")))
            else:
                state.price_points.append((fetched_at_ms, float(price), str(snapshot.last_price_source or "")))
                state.price_points = deque(sorted(state.price_points, key=lambda row: int(row[0])))
            _prune_session_top_points(state.price_points, cutoff_ms=cutoff_ms)

    def snapshot(self, *, now_ms: int) -> dict[str, object]:
        now_ms = int(now_ms)
        session = _live_crypto_session_context_ms(now_ms)
        cutoff_ms = now_ms - int(LIVE_SESSION_TOP_ROLLING_WINDOW_MS)
        items: list[dict[str, object]] = []
        symbols_tracked = 0
        for state in list(self._states.values()):
            _prune_session_top_points(state.price_points, cutoff_ms=cutoff_ms)
            if not state.price_points:
                continue
            symbols_tracked += 1
            baseline_ts, baseline_price, _baseline_source = state.price_points[0]
            last_ts, last_price, last_source = state.price_points[-1]
            if baseline_price <= 0.0:
                continue
            growth_fraction = (last_price - baseline_price) / baseline_price
            if growth_fraction <= 0.0:
                continue
            items.append(
                {
                    "symbol": state.symbol,
                    "growth_pct": growth_fraction * 100.0,
                    "growth_fraction": growth_fraction,
                    "baseline_price": baseline_price,
                    "last_price": last_price,
                    "baseline_timestamp_ms": int(baseline_ts),
                    "last_timestamp_ms": int(last_ts),
                    "last_price_source": last_source,
                }
            )
        items.sort(key=lambda item: float(item["growth_fraction"]), reverse=True)
        ranked_items: list[dict[str, object]] = []
        for rank, item in enumerate(items[: self.limit], start=1):
            ranked_items.append({"rank": rank, **item})
        if ranked_items:
            status = "ok"
            reason = "ok"
        elif symbols_tracked:
            status = "empty"
            reason = "no_positive_growth_in_rolling_6h"
        else:
            status = "empty"
            reason = "no_usable_ticker_price_snapshots_in_rolling_6h"
        return {
            "snapshot_timestamp_ms": now_ms,
            "session_label": session["label"],
            "session_phase": session["phase"],
            "session_primary": session["primary"],
            "session_secondary": session["secondary"],
            "session_start_ms": session["start_ms"],
            "session_end_ms": session["end_ms"],
            "top_window_label": "последние 6ч",
            "top_window_start_ms": cutoff_ms,
            "top_window_end_ms": now_ms,
            "top_window_hours": 6.0,
            "status": status,
            "reason": reason,
            "source": self._last_source,
            "source_status": self._last_source_status,
            "source_reason": self._last_source_reason,
            "symbols_tracked": symbols_tracked,
            "symbols_with_positive_growth": len(items),
            "items": ranked_items,
        }


@dataclass(frozen=True, slots=True)
class LiveSymbolContextSnapshotCycleStats:
    enabled: bool
    attempted: bool
    status: str
    reason: str
    symbols_total: int = 0
    selected_symbols: tuple[str, ...] = ()
    updated_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    cycle_budget_seconds: float = 0.0
    effective_fresh_ms: int = 0
    output_file: str = ""


@dataclass(frozen=True, slots=True)
class LiveWsAggTradeSubscriptionStats:
    enabled: bool
    source: str
    target_count: int = 0
    subscribed_count: int | str = ""
    connection_status: str = ""
    last_error: str = ""


@dataclass(slots=True)
class LivePosition:
    position_id: str
    signal: LiveSignal
    amount: float
    notional_usdt: float
    risk_usdt: float
    entry_order_id: str
    stop_order_id: str
    opened_at_utc: str
    opened_at_ms: int
    entry_price: float
    stop_price: float
    tp1_price: float
    tp1_order_id: str
    tp1_client_order_id: str
    tp1_order_amount: float
    initial_risk: float
    first_executable_entry_timestamp_ms: int
    entry_lag_ms: int
    entry_lag_ltf_candles: int
    entered_late_vs_first_executable: bool
    entry_order_submit_lag_ms: int
    entry_order_submit_lag_ltf_candles: int
    previous_live_scan_closed_timestamp_ms: int | None
    first_unscanned_decision_timestamp_ms: int | None
    live_scan_gap_ltf_candles: int
    entry_fill_timestamp_ms: int
    entry_order_submitted_at_ms: int
    entry_order_status: str
    source_scan_mode: str
    danger_cold_coverage_source: bool
    entry_position_guard_source: str
    entry_filled_amount: float
    entry_cost_usdt: float | None
    entry_fee_usdt: float | None
    pre_position_amount: float
    post_position_amount: float
    position_delta_amount: float
    telegram_open_message_id: int | None = None
    telegram_stop_message_id: int | None = None
    remaining_amount: float = 0.0
    tp1_done: bool = False
    current_stop_price: float = 0.0
    realized_pnl_usdt: float = 0.0
    tp1_recorded_filled_amount: float = 0.0
    tp1_recorded_realized_pnl_usdt: float = 0.0


class _LiveStatusLogger:
    """Console logger for live heartbeat/status messages."""

    _ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

    def __init__(self, logger: Callable[[str], None]) -> None:
        self._logger = logger
        self._inline_status_enabled = logger is print and sys.stdout.isatty()
        self._lock = threading.RLock()
        self._status_line_open = False
        self._status_line_rows = 0

    @property
    def inline_status_enabled(self) -> bool:
        return self._inline_status_enabled

    def __call__(self, message: str) -> None:
        with self._lock:
            if self._inline_status_enabled and self._status_line_open:
                self._clear_status_line_if_needed()
            else:
                self._finish_status_line_if_needed()
            self._logger(message)

    def alert(self, message: str) -> None:
        with self._lock:
            if self._inline_status_enabled and self._status_line_open:
                self._clear_status_line_if_needed()
                rendered = f"\033[1;31m{message}\033[0m"
                self._logger(rendered)
                return
            self._clear_status_line_if_needed()
            rendered = f"\033[1;31m{message}\033[0m" if self._inline_status_enabled else message
            self._logger(rendered)

    def status(self, message: str, *, highlight: bool = False) -> None:
        with self._lock:
            if not self._inline_status_enabled:
                self._logger(message)
                return
            rendered = f"\033[1;33m{message}\033[0m" if highlight else message
            terminal_columns = self._terminal_columns()
            self._clear_status_line_if_needed()
            sys.stdout.write(rendered)
            sys.stdout.flush()
            self._status_line_rows = self._rendered_rows(rendered, terminal_columns)
            self._status_line_open = True

    def finish_status(self) -> None:
        with self._lock:
            self._clear_status_line_if_needed()

    def _finish_status_line_if_needed(self) -> None:
        if not self._inline_status_enabled or not self._status_line_open:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_line_open = False
        self._status_line_rows = 0

    def _clear_status_line_if_needed(self) -> None:
        if not self._status_line_open:
            sys.stdout.write("\r\033[2K")
            return
        rows = max(1, self._status_line_rows)
        sys.stdout.write("\r\033[2K")
        for _ in range(rows - 1):
            sys.stdout.write("\033[1A\r\033[2K")
        sys.stdout.write("\r")
        self._status_line_open = False
        self._status_line_rows = 0

    @staticmethod
    def _terminal_columns() -> int:
        return max(20, shutil.get_terminal_size(fallback=(120, 20)).columns)

    @classmethod
    def _rendered_rows(cls, rendered: str, terminal_columns: int) -> int:
        visible = cls._ANSI_RE.sub("", rendered)
        if not visible:
            return 1
        columns = max(1, terminal_columns)
        rows = 0
        for line in visible.split("\n"):
            rows += max(1, (len(line) - 1) // columns + 1) if line else 1
        return max(1, rows)


class _LiveRetryLogger:
    def __init__(self, status_logger: _LiveStatusLogger) -> None:
        self._status_logger = status_logger

    @staticmethod
    def _format(message: str, args: tuple[object, ...]) -> str:
        if not args:
            return str(message)
        try:
            return str(message) % args
        except Exception:
            rendered_args = " ".join(str(arg) for arg in args)
            return f"{message} {rendered_args}".strip()

    def debug(self, message: str, *args: object) -> None:
        return

    def warning(self, message: str, *args: object) -> None:
        text = self._format(message, args)
        self._status_logger.alert(f"⚠️ {text}")


class TelegramDispatcher:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        logger: Callable[[str], None],
        cooldown_seconds: float,
        event_writer: Callable[[str, str, dict[str, object]], None] | None = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._cooldown_seconds = cooldown_seconds
        self._event_writer = event_writer
        self._queue: queue.Queue[dict[str, object]] = queue.Queue()
        self._last_sent: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, name="telegram-dispatcher", daemon=True)
        self._thread.start()

    def send(
        self,
        *,
        channel: str,
        key: str,
        text: str,
        reply_to_message_id: int | None = None,
        symbol: str = "__telegram__",
    ) -> None:
        self._queue.put(
            {
                "channel": channel,
                "key": key,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "symbol": symbol,
            }
        )

    def send_sync(self, *, channel: str, text: str, reply_to_message_id: int | None = None) -> int | None:
        return self._send_message(channel=channel, text=text, reply_to_message_id=reply_to_message_id)

    def send_critical_sync(
        self,
        *,
        channel: str,
        key: str,
        text: str,
        symbol: str = "__telegram__",
        reply_to_message_id: int | None = None,
    ) -> int | None:
        try:
            return self._send_message(channel=channel, text=text, reply_to_message_id=reply_to_message_id)
        except Exception as exc:
            self._logger(f"telegram: critical message not sent, reason: {exc}")
            self._append_event(
                "telegram_sync_send_failed",
                symbol,
                {
                    "channel": channel,
                    "key": key,
                    "reply_to_message_id": reply_to_message_id or "",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "text_preview": text[:240],
                },
            )
            return None

    def edit_sync(self, *, channel: str, message_id: int, text: str) -> int | None:
        return self._edit_message(channel=channel, message_id=message_id, text=text)

    def send_photo(self, *, channel: str, photo_path: Path, caption: str, reply_to_message_id: int | None = None) -> bool:
        try:
            self._send_photo(channel=channel, photo_path=photo_path, caption=caption, reply_to_message_id=reply_to_message_id)
            return True
        except Exception as exc:
            self._logger(f"telegram: график не отправлен, причина: {exc}")
            self._append_event(
                "telegram_photo_send_failed",
                "__telegram__",
                {
                    "channel": channel,
                    "photo_path": str(photo_path),
                    "reply_to_message_id": reply_to_message_id or "",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                },
            )
            return False

    def send_photo_sync(self, *, channel: str, photo_path: Path, caption: str, reply_to_message_id: int | None = None) -> int | None:
        return self._send_photo(channel=channel, photo_path=photo_path, caption=caption, reply_to_message_id=reply_to_message_id)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                key = str(item["key"])
                now = time.monotonic()
                if now - self._last_sent.get(key, 0.0) < self._cooldown_seconds:
                    continue
                message_id = self._send_message(
                    channel=str(item["channel"]),
                    text=str(item["text"]),
                    reply_to_message_id=item.get("reply_to_message_id")
                    if isinstance(item.get("reply_to_message_id"), int)
                    else None,
                )
                self._last_sent[key] = now
                if message_id is not None:
                    item["message_id"] = message_id
            except Exception as exc:
                self._logger(f"telegram: сообщение не отправлено, причина: {exc}")
                self._append_event(
                    "telegram_async_send_failed",
                    str(item.get("symbol") or "__telegram__"),
                    {
                        "channel": str(item.get("channel") or ""),
                        "key": str(item.get("key") or ""),
                        "reply_to_message_id": item.get("reply_to_message_id")
                        if isinstance(item.get("reply_to_message_id"), int)
                        else "",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                        "text_preview": str(item.get("text") or "")[:240],
                    },
                )
            finally:
                self._queue.task_done()

    def _append_event(self, event: str, symbol: str, details: dict[str, object]) -> None:
        if self._event_writer is None:
            return
        try:
            self._event_writer(event, symbol, details)
        except Exception as exc:
            self._logger(f"telegram: artifact event не записан, причина: {exc}")

    def _send_message(self, *, channel: str, text: str, reply_to_message_id: int | None) -> int | None:
        if channel == "positions":
            token = self._config.positions_bot_token
            chat_id = self._config.positions_chat_id
        else:
            token = self._config.events_bot_token
            chat_id = self._config.events_chat_id
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = json.loads(response.read().decode("utf-8"))
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        return int(message_id) if message_id is not None else None

    def _edit_message(self, *, channel: str, message_id: int, text: str) -> int | None:
        if channel == "positions":
            token = self._config.positions_bot_token
            chat_id = self._config.positions_chat_id
        else:
            token = self._config.events_bot_token
            chat_id = self._config.events_chat_id
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/editMessageText", data=data)
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = json.loads(response.read().decode("utf-8"))
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            return None
        edited_id = result.get("message_id")
        return int(edited_id) if edited_id is not None else None

    def _send_photo(self, *, channel: str, photo_path: Path, caption: str, reply_to_message_id: int | None) -> int | None:
        token = self._config.positions_bot_token if channel == "positions" else self._config.events_bot_token
        chat_id = self._config.positions_chat_id if channel == "positions" else self._config.events_chat_id
        boundary = f"----codex{uuid4().hex}"
        fields: dict[str, object] = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        if reply_to_message_id is not None:
            fields["reply_to_message_id"] = reply_to_message_id
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{photo_path.name}\"\r\nContent-Type: image/png\r\n\r\n".encode())
        body.extend(photo_path.read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = json.loads(response.read().decode("utf-8"))
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        return int(message_id) if message_id is not None else None


class LiveArtifactWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.root / "live_positions.csv"
        self.events_path = self.root / "live_events.csv"
        self.top_growth_dir = self.root / "top_growth"
        self.top_growth_dir.mkdir(parents=True, exist_ok=True)
        self.top_growth_index_path = self.top_growth_dir / "top_growth_index.csv"
        self.missed_pump_visibility_path = self.top_growth_dir / "missed_pump_visibility.csv"
        self.session_top_growth_path = self.top_growth_dir / "session_top_growth.csv"
        self.delayed_replay_dir = self.root / "delayed_replay"
        self.delayed_replay_dir.mkdir(parents=True, exist_ok=True)
        self.delayed_replay_queue_path = self.delayed_replay_dir / "delayed_replay_queue.jsonl"
        self.delayed_replay_results_path = self.delayed_replay_dir / "delayed_replay_results.csv"
        self.delayed_replay_summary_path = self.delayed_replay_dir / "delayed_replay_summary.csv"
        self.live_status_path = self.root / "live_status.json"
        self.symbol_context_snapshot_path = self.root / "symbol_context_snapshot.csv"
        self._lock = threading.Lock()
        self._events_written = self._count_existing_csv_rows(self.events_path)
        self._ensure_csv(self.ledger_path, LIVE_LEDGER_COLUMNS)
        self._ensure_csv(self.events_path, ("timestamp_utc", "event", "symbol", "details_json"))
        self._ensure_csv(self.top_growth_index_path, TOP_GROWTH_INDEX_COLUMNS)
        self._ensure_csv(self.missed_pump_visibility_path, MISSED_PUMP_VISIBILITY_COLUMNS)
        self._ensure_csv(self.session_top_growth_path, LIVE_SESSION_TOP_GROWTH_COLUMNS)
        self._ensure_csv(self.delayed_replay_results_path, DELAYED_REPLAY_RESULTS_COLUMNS)
        self._ensure_csv(self.delayed_replay_summary_path, DELAYED_REPLAY_SUMMARY_COLUMNS)
        self._ensure_csv(self.symbol_context_snapshot_path, SYMBOL_CONTEXT_SNAPSHOT_COLUMNS)

    @staticmethod
    def _count_existing_csv_rows(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", newline="", encoding="utf-8") as handle:
            return max(sum(1 for _ in handle) - 1, 0)

    @staticmethod
    def _ensure_csv(path: Path, columns: tuple[str, ...]) -> None:
        if path.exists():
            return
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=list(columns)).writeheader()

    def write_top_growth_snapshot(
        self,
        *,
        period_start_ms: int,
        period_end_ms: int,
        snapshot_utc: str,
        top_rows: list[dict[str, object]],
        status_rows: list[dict[str, object]],
        visibility_rows: list[dict[str, object]],
        symbols_total: int,
        threshold_pct: float,
        limit: int,
    ) -> tuple[Path, Path, Path]:
        period_start_utc = datetime.fromtimestamp(period_start_ms / 1000, UTC).isoformat()
        period_end_utc = datetime.fromtimestamp(period_end_ms / 1000, UTC).isoformat()
        stamp = datetime.fromtimestamp(period_start_ms / 1000, UTC).strftime("%Y%m%d_%H0000_UTC")
        top_path = self.top_growth_dir / f"top_growth_{stamp}.csv"
        status_path = self.top_growth_dir / f"top_growth_status_{stamp}.csv"
        visibility_path = self.top_growth_dir / f"missed_pump_visibility_{stamp}.csv"
        enriched_top_rows = [
            {
                "snapshot_utc": snapshot_utc,
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                **row,
            }
            for row in top_rows
        ]
        enriched_status_rows = [
            {
                "snapshot_utc": snapshot_utc,
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                **row,
            }
            for row in status_rows
        ]
        enriched_visibility_rows = [
            {
                "snapshot_utc": snapshot_utc,
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                **row,
            }
            for row in visibility_rows
        ]
        ok_count = sum(1 for row in enriched_status_rows if row.get("status") == "ok")
        failed_count = len(enriched_status_rows) - ok_count
        index_row = {
            "snapshot_utc": snapshot_utc,
            "period_start_utc": period_start_utc,
            "period_end_utc": period_end_utc,
            "top_count": len(enriched_top_rows),
            "symbols_total": symbols_total,
            "ok_count": ok_count,
            "failed_count": failed_count,
            "threshold_pct": threshold_pct,
            "limit": limit,
            "top_file": top_path.name,
            "status_file": status_path.name,
            "visibility_file": visibility_path.name,
        }
        with self._lock:
            self._write_csv_atomic(top_path, TOP_GROWTH_COLUMNS, enriched_top_rows)
            self._write_csv_atomic(status_path, TOP_GROWTH_STATUS_COLUMNS, enriched_status_rows)
            self._write_csv_atomic(visibility_path, MISSED_PUMP_VISIBILITY_COLUMNS, enriched_visibility_rows)
            self._append_csv_rows(
                self.missed_pump_visibility_path,
                MISSED_PUMP_VISIBILITY_COLUMNS,
                enriched_visibility_rows,
            )
            with self.top_growth_index_path.open("a", newline="", encoding="utf-8-sig") as handle:
                csv.DictWriter(handle, fieldnames=list(TOP_GROWTH_INDEX_COLUMNS)).writerow(index_row)
        return top_path, status_path, visibility_path

    @staticmethod
    def _append_csv_rows(path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        with path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
            for row in rows:
                writer.writerow(row)

    @staticmethod
    def _write_csv_atomic(path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        tmp_path.replace(path)

    def append_delayed_replay_case(self, case: dict[str, object]) -> None:
        try:
            line = json.dumps(case, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except ValueError as exc:
            raise LiveDataIntegrityError(f"Non-finite delayed replay case: case_id={case.get('case_id')} error={exc}") from exc
        with self._lock, self.delayed_replay_queue_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def append_delayed_replay_result(self, row: dict[str, object]) -> None:
        with self._lock, self.delayed_replay_results_path.open("a", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=list(DELAYED_REPLAY_RESULTS_COLUMNS), extrasaction="ignore").writerow(row)

    def append_delayed_replay_summary(self, row: dict[str, object]) -> None:
        with self._lock, self.delayed_replay_summary_path.open("a", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=list(DELAYED_REPLAY_SUMMARY_COLUMNS), extrasaction="ignore").writerow(row)

    def write_live_status(self, status: dict[str, object]) -> None:
        try:
            payload = json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        except ValueError as exc:
            raise LiveDataIntegrityError(f"Non-finite live status payload: {exc}") from exc
        tmp_path = self.live_status_path.with_suffix(".json.tmp")
        with self._lock:
            tmp_path.write_text(payload + "\n", encoding="utf-8")
            tmp_path.replace(self.live_status_path)

    def append_session_top_growth_snapshot(self, snapshot: dict[str, object]) -> None:
        items_raw = snapshot.get("items")
        items = list(items_raw) if isinstance(items_raw, list) else []
        symbols_tracked = int(snapshot.get("symbols_tracked") or 0)
        positive_count = int(snapshot.get("symbols_with_positive_growth") or 0)
        common = {
            "snapshot_utc": _ms_to_iso_utc(int(snapshot.get("snapshot_timestamp_ms") or int(time.time() * 1000))),
            "session_label": str(snapshot.get("session_label") or ""),
            "session_phase": str(snapshot.get("session_phase") or ""),
            "session_primary": str(snapshot.get("session_primary") or ""),
            "session_secondary": str(snapshot.get("session_secondary") or ""),
            "session_start_utc": _ms_to_iso_utc(int(snapshot.get("session_start_ms") or 0)),
            "session_end_utc": _ms_to_iso_utc(int(snapshot.get("session_end_ms") or 0)),
            "top_window_label": str(snapshot.get("top_window_label") or ""),
            "top_window_start_utc": _ms_to_iso_utc(int(snapshot.get("top_window_start_ms") or 0)),
            "top_window_end_utc": _ms_to_iso_utc(int(snapshot.get("top_window_end_ms") or 0)),
            "top_window_hours": snapshot.get("top_window_hours", ""),
            "ticker_source": str(snapshot.get("source") or ""),
            "ticker_source_status": str(snapshot.get("source_status") or ""),
            "ticker_source_reason": str(snapshot.get("source_reason") or ""),
            "status": str(snapshot.get("status") or ""),
            "reason": str(snapshot.get("reason") or ""),
            "symbols_tracked": symbols_tracked,
            "symbols_with_positive_growth": positive_count,
        }
        rows: list[dict[str, object]] = []
        if items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        **common,
                        "rank": item.get("rank", ""),
                        "symbol": item.get("symbol", ""),
                        "growth_pct": item.get("growth_pct", ""),
                        "growth_fraction": item.get("growth_fraction", ""),
                        "baseline_price": item.get("baseline_price", ""),
                        "last_price": item.get("last_price", ""),
                        "baseline_timestamp_utc": _ms_to_iso_utc(int(item.get("baseline_timestamp_ms") or 0)),
                        "last_timestamp_utc": _ms_to_iso_utc(int(item.get("last_timestamp_ms") or 0)),
                        "last_price_source": item.get("last_price_source", ""),
                    }
                )
        else:
            rows.append(
                {
                    **common,
                    "rank": "",
                    "symbol": "",
                    "growth_pct": "",
                    "growth_fraction": "",
                    "baseline_price": "",
                    "last_price": "",
                    "baseline_timestamp_utc": "",
                    "last_timestamp_utc": "",
                    "last_price_source": "",
                }
            )
        self._append_csv_rows(self.session_top_growth_path, LIVE_SESSION_TOP_GROWTH_COLUMNS, rows)

    def write_symbol_context_snapshot(
        self,
        snapshots: list[LiveSymbolContextSnapshot],
    ) -> Path:
        rows = [
            snapshot.to_row()
            for snapshot in sorted(
                snapshots,
                key=lambda item: (
                    _position_symbol_key(item.symbol),
                    item.levels_timeframe.value,
                    item.entry_timeframe.value,
                ),
            )
        ]
        with self._lock:
            self._write_csv_atomic(self.symbol_context_snapshot_path, SYMBOL_CONTEXT_SNAPSHOT_COLUMNS, rows)
        return self.symbol_context_snapshot_path

    def append_event(self, event: str, symbol: str, details: dict[str, object]) -> None:
        try:
            details_json = json.dumps(details, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except ValueError as exc:
            raise LiveDataIntegrityError(
                f"Non-finite live event details: event={event} symbol={symbol} error={exc}"
            ) from exc
        with self._lock, self.events_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp_utc", "event", "symbol", "details_json"])
            writer.writerow(
                {
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "event": event,
                    "symbol": symbol,
                    "details_json": details_json,
                }
            )
            self._events_written += 1

    @property
    def events_written(self) -> int:
        with self._lock:
            return self._events_written

    def append_position(self, position: LivePosition, *, status: str = "open") -> None:
        signal = position.signal
        with self._lock, self.ledger_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(LIVE_LEDGER_COLUMNS))
            writer.writerow(
                {
                    "position_id": position.position_id,
                    "status": status,
                    "symbol": signal.symbol,
                    "signal_category_id": signal.category_id,
                    "signal_category_label": signal.category_label,
                    "session": signal.session,
                    "opened_at_utc": position.opened_at_utc,
                    "closed_at_utc": "",
                    "entry_price": position.entry_price,
                    "signal_entry_price": signal.entry_price,
                    "first_executable_entry_timestamp_ms": position.first_executable_entry_timestamp_ms,
                    "entry_lag_ms": position.entry_lag_ms,
                    "entry_lag_ltf_candles": position.entry_lag_ltf_candles,
                    "entered_late_vs_first_executable": position.entered_late_vs_first_executable,
                    "entry_order_submit_lag_ms": position.entry_order_submit_lag_ms,
                    "entry_order_submit_lag_ltf_candles": position.entry_order_submit_lag_ltf_candles,
                    "previous_live_scan_closed_timestamp_ms": position.previous_live_scan_closed_timestamp_ms or "",
                    "first_unscanned_decision_timestamp_ms": position.first_unscanned_decision_timestamp_ms or "",
                    "live_scan_gap_ltf_candles": position.live_scan_gap_ltf_candles,
                    "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                    "entry_order_submitted_at_ms": position.entry_order_submitted_at_ms,
                    "entry_order_status": position.entry_order_status,
                    "stop_price": position.stop_price,
                    "source_scan_mode": position.source_scan_mode,
                    "danger_cold_coverage_source": position.danger_cold_coverage_source,
                    "entry_position_guard_source": position.entry_position_guard_source,
                    "tp1_price": position.tp1_price,
                    "tp1_order_id": position.tp1_order_id,
                    "tp1_client_order_id": position.tp1_client_order_id,
                    "tp1_order_amount": position.tp1_order_amount,
                    "amount": position.amount,
                    "entry_filled_amount": position.entry_filled_amount,
                    "entry_cost_usdt": position.entry_cost_usdt if position.entry_cost_usdt is not None else "",
                    "entry_fee_usdt": position.entry_fee_usdt if position.entry_fee_usdt is not None else "",
                    "pre_position_amount": position.pre_position_amount,
                    "post_position_amount": position.post_position_amount,
                    "position_delta_amount": position.position_delta_amount,
                    "notional_usdt": position.notional_usdt,
                    "risk_usdt": position.risk_usdt,
                    "realized_pnl_usdt": "",
                    "realized_pnl_pct": "",
                    "signal_json": signal.to_json(),
                    "entry_order_id": position.entry_order_id,
                    "stop_order_id": position.stop_order_id,
                    "telegram_open_message_id": position.telegram_open_message_id or "",
                    "telegram_stop_message_id": position.telegram_stop_message_id or "",
                    "telegram_close_message_id": "",
                    "close_reason": "",
                }
            )

    def append_position_close(
        self,
        position: LivePosition,
        *,
        reason: str,
        pnl_usdt: float,
        pnl_pct: float,
    ) -> None:
        self._append_position_terminal_row(
            position,
            status="closed",
            reason=reason,
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
        )

    def append_position_exit_unresolved(self, position: LivePosition, *, reason: str) -> None:
        self._append_position_terminal_row(
            position,
            status="exit_unresolved",
            reason=reason,
            pnl_usdt=None,
            pnl_pct=None,
        )

    def _append_position_terminal_row(
        self,
        position: LivePosition,
        *,
        status: str,
        reason: str,
        pnl_usdt: float | None,
        pnl_pct: float | None,
    ) -> None:
        signal = position.signal
        with self._lock, self.ledger_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(LIVE_LEDGER_COLUMNS))
            writer.writerow(
                {
                    "position_id": position.position_id,
                    "status": status,
                    "symbol": signal.symbol,
                    "signal_category_id": signal.category_id,
                    "signal_category_label": signal.category_label,
                    "session": signal.session,
                    "opened_at_utc": position.opened_at_utc,
                    "closed_at_utc": datetime.now(UTC).isoformat(),
                    "entry_price": position.entry_price,
                    "signal_entry_price": signal.entry_price,
                    "first_executable_entry_timestamp_ms": position.first_executable_entry_timestamp_ms,
                    "entry_lag_ms": position.entry_lag_ms,
                    "entry_lag_ltf_candles": position.entry_lag_ltf_candles,
                    "entered_late_vs_first_executable": position.entered_late_vs_first_executable,
                    "entry_order_submit_lag_ms": position.entry_order_submit_lag_ms,
                    "entry_order_submit_lag_ltf_candles": position.entry_order_submit_lag_ltf_candles,
                    "previous_live_scan_closed_timestamp_ms": position.previous_live_scan_closed_timestamp_ms or "",
                    "first_unscanned_decision_timestamp_ms": position.first_unscanned_decision_timestamp_ms or "",
                    "live_scan_gap_ltf_candles": position.live_scan_gap_ltf_candles,
                    "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                    "entry_order_submitted_at_ms": position.entry_order_submitted_at_ms,
                    "entry_order_status": position.entry_order_status,
                    "stop_price": position.stop_price,
                    "source_scan_mode": position.source_scan_mode,
                    "danger_cold_coverage_source": position.danger_cold_coverage_source,
                    "entry_position_guard_source": position.entry_position_guard_source,
                    "tp1_price": position.tp1_price,
                    "tp1_order_id": position.tp1_order_id,
                    "tp1_client_order_id": position.tp1_client_order_id,
                    "tp1_order_amount": position.tp1_order_amount,
                    "amount": position.amount,
                    "entry_filled_amount": position.entry_filled_amount,
                    "entry_cost_usdt": position.entry_cost_usdt if position.entry_cost_usdt is not None else "",
                    "entry_fee_usdt": position.entry_fee_usdt if position.entry_fee_usdt is not None else "",
                    "pre_position_amount": position.pre_position_amount,
                    "post_position_amount": position.post_position_amount,
                    "position_delta_amount": position.position_delta_amount,
                    "notional_usdt": position.notional_usdt,
                    "risk_usdt": position.risk_usdt,
                    "realized_pnl_usdt": pnl_usdt if pnl_usdt is not None else "",
                    "realized_pnl_pct": pnl_pct if pnl_pct is not None else "",
                    "signal_json": signal.to_json(),
                    "entry_order_id": position.entry_order_id,
                    "stop_order_id": position.stop_order_id,
                    "telegram_open_message_id": position.telegram_open_message_id or "",
                    "telegram_stop_message_id": position.telegram_stop_message_id or "",
                    "telegram_close_message_id": "",
                    "close_reason": reason,
                }
            )


@dataclass(frozen=True, slots=True)
class TopGrowthSnapshotConfig:
    results_dir: Path
    symbols: tuple[str, ...]
    period_start_ms: int | None = None
    min_return_pct: float = 0.10
    limit: int = 5
    fetch_spacing_seconds: float = 0.05
    visibility_events_csv: Path | None = None


@dataclass(frozen=True, slots=True)
class LiveVisibilityEvent:
    timestamp_utc: str
    timestamp_ms: int
    event: str
    symbol: str
    details: dict[str, object]


@dataclass(slots=True)
class LiveTopGrowthAuditTask:
    period_start_ms: int
    period_end_ms: int
    snapshot_utc: str
    symbols: tuple[str, ...]
    cursor: int = 0
    status_rows: list[dict[str, object]] = field(default_factory=list)
    candidates: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LiveTopGrowthAuditCycleStats:
    enabled: bool
    status: str
    reason: str
    period_start_ms: int | None = None
    period_end_ms: int | None = None
    processed_count: int = 0
    remaining_count: int = 0
    symbols_total: int = 0
    top_count: int = 0
    cycle_seconds: float = 0.0


class TopGrowthSnapshotRunner:
    def __init__(
        self,
        *,
        config: TopGrowthSnapshotConfig,
        exchange_client: CcxtFuturesClient,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.exchange = exchange_client
        self.logger = logger
        self.artifacts = LiveArtifactWriter(
            config.results_dir / "top_growth_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        )

    def run(self) -> int:
        _validate_top_growth_config_values(self.config)
        symbols = list(self.config.symbols) or self.exchange.list_usdt_swap_symbols()
        if not symbols:
            raise LiveStartupError("Нет символов для top-growth snapshot")
        now_ms = int(time.time() * 1000)
        period_start_ms = self.config.period_start_ms
        if period_start_ms is None:
            period_start_ms = _previous_closed_hour_start_ms(now_ms)
        period_end_ms = period_start_ms + HOUR_MS
        if period_end_ms > now_ms:
            raise LiveStartupError("top-growth period должен быть полностью закрытым 1h интервалом")
        snapshot_utc = datetime.now(UTC).isoformat()
        self.artifacts.append_event(
            "top_growth_snapshot_started",
            "__top_growth__",
            {
                "period_start_ms": period_start_ms,
                "period_end_ms": period_end_ms,
                "symbols_total": len(symbols),
                "threshold_pct": self.config.min_return_pct * 100.0,
                "limit": self.config.limit,
                "source": "standalone_command",
            },
        )
        top_rows, status_rows = _collect_top_growth_snapshot(
            exchange=self.exchange,
            symbols=tuple(symbols),
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            snapshot_utc=snapshot_utc,
            threshold_fraction=self.config.min_return_pct,
            limit=self.config.limit,
            fetch_spacing_seconds=self.config.fetch_spacing_seconds,
        )
        visibility_rows = _build_missed_pump_visibility_rows(
            top_rows=top_rows,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            visibility_events_csv=self.config.visibility_events_csv,
        )
        top_path, status_path, visibility_path = self.artifacts.write_top_growth_snapshot(
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            snapshot_utc=snapshot_utc,
            top_rows=top_rows,
            status_rows=status_rows,
            visibility_rows=visibility_rows,
            symbols_total=len(symbols),
            threshold_pct=self.config.min_return_pct * 100.0,
            limit=self.config.limit,
        )
        self.artifacts.append_event(
            "top_growth_snapshot_saved",
            "__top_growth__",
            {
                "period_start_ms": period_start_ms,
                "period_end_ms": period_end_ms,
                "top_count": len(top_rows),
                "symbols_total": len(symbols),
                "ok_count": sum(1 for row in status_rows if row.get("status") == "ok"),
                "failed_count": sum(1 for row in status_rows if row.get("status") != "ok"),
                "top_file": str(top_path.relative_to(self.artifacts.root)),
                "status_file": str(status_path.relative_to(self.artifacts.root)),
                "visibility_file": str(visibility_path.relative_to(self.artifacts.root)),
                "visibility_events_csv": str(self.config.visibility_events_csv) if self.config.visibility_events_csv is not None else "",
                "source": "standalone_command",
            },
        )
        period_start_utc = datetime.fromtimestamp(period_start_ms / 1000, UTC).isoformat()
        self.logger(
            "top-growth: "
            f"{period_start_utc} · top {len(top_rows)} · "
            f"ok {sum(1 for row in status_rows if row.get('status') == 'ok')}/{len(status_rows)} · "
            f"артефакты {self.artifacts.root}"
        )
        return 0


def _collect_top_growth_snapshot(
    *,
    exchange: CcxtFuturesClient,
    symbols: tuple[str, ...],
    period_start_ms: int,
    period_end_ms: int,
    snapshot_utc: str,
    threshold_fraction: float,
    limit: int,
    fetch_spacing_seconds: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    candidates: list[dict[str, object]] = []
    status_rows: list[dict[str, object]] = []
    for symbol in symbols:
        status_row = _load_top_growth_symbol_row(
            exchange=exchange,
            symbol=symbol,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            snapshot_utc=snapshot_utc,
        )
        status_rows.append(status_row)
        candidate = _top_growth_candidate_from_status_row(status_row, threshold_fraction=threshold_fraction)
        if candidate is not None:
            candidates.append(candidate)
        spacing = float(fetch_spacing_seconds)
        if math.isfinite(spacing) and spacing > 0.0:
            time.sleep(spacing)
    return _rank_top_growth_candidates(candidates, limit=limit), status_rows


def _top_growth_candidate_from_status_row(
    status_row: dict[str, object],
    *,
    threshold_fraction: float,
) -> dict[str, object] | None:
    if status_row.get("status") != "ok":
        return None
    growth_fraction = _row_float_or_none(status_row, "growth_fraction")
    if growth_fraction is None or growth_fraction < threshold_fraction:
        return None
    return {
        "symbol": status_row.get("symbol", ""),
        "growth_pct": status_row.get("growth_pct", ""),
        "growth_fraction": status_row.get("growth_fraction", ""),
        "open": status_row.get("open", ""),
        "close": status_row.get("close", ""),
        "high": status_row.get("high", ""),
        "low": status_row.get("low", ""),
        "quote_volume": status_row.get("quote_volume", ""),
        "number_of_trades": status_row.get("number_of_trades", ""),
        "taker_buy_quote_volume": status_row.get("taker_buy_quote_volume", ""),
        "threshold_pct": float(threshold_fraction) * 100.0,
        "timeframe": Timeframe.H1.value,
        "source": "exchange_1h_closed_candle",
    }


def _rank_top_growth_candidates(candidates: list[dict[str, object]], *, limit: int) -> list[dict[str, object]]:
    candidates.sort(
        key=lambda row: (
            _row_float_or_none(row, "growth_fraction") or -math.inf,
            _row_float_or_none(row, "quote_volume") or -math.inf,
        ),
        reverse=True,
    )
    top_rows: list[dict[str, object]] = []
    for rank, row in enumerate(candidates[: max(1, int(limit))], start=1):
        top_rows.append({"rank": rank, **row})
    return top_rows


def _build_missed_pump_visibility_rows(
    *,
    top_rows: list[dict[str, object]],
    period_start_ms: int,
    period_end_ms: int,
    visibility_events_csv: Path | None,
) -> list[dict[str, object]]:
    events, source_status, source_reason, parse_error_count = _read_visibility_events(visibility_events_csv)
    events_by_symbol: dict[str, list[LiveVisibilityEvent]] = {}
    for event in events:
        events_by_symbol.setdefault(_position_symbol_key(event.symbol), []).append(event)
    for symbol_events in events_by_symbol.values():
        symbol_events.sort(key=lambda item: (item.timestamp_ms, item.event))

    rows: list[dict[str, object]] = []
    visibility_source = str(visibility_events_csv) if visibility_events_csv is not None else ""
    for top_row in top_rows:
        symbol = str(top_row.get("symbol") or "")
        symbol_key = _position_symbol_key(symbol)
        symbol_events_all = events_by_symbol.get(symbol_key, [])
        events_to_period_end = [event for event in symbol_events_all if event.timestamp_ms <= int(period_end_ms)]
        events_before_pump = [event for event in symbol_events_all if event.timestamp_ms < int(period_start_ms)]
        events_during_pump_hour = [
            event
            for event in symbol_events_all
            if int(period_start_ms) <= event.timestamp_ms <= int(period_end_ms)
        ]

        radar_event = _first_visibility_event(events_to_period_end, _is_visibility_radar_event)
        flow_event = _first_visibility_event(events_to_period_end, _is_visibility_flow_radar_event)
        warm_event = _first_visibility_event(events_to_period_end, _is_visibility_warm_watch_event)
        precise_event = _first_visibility_event(events_to_period_end, _is_visibility_precise_scan_event)
        category_reject_event = _first_visibility_event(
            events_to_period_end,
            lambda event: event.event == "category_rejected",
        )
        execution_reject_event = _first_visibility_event(events_to_period_end, _is_visibility_execution_reject_event)
        position_opened_event = _first_visibility_event(
            events_to_period_end,
            lambda event: event.event == "position_opened",
        )
        first_event = events_to_period_end[0] if events_to_period_end else None
        first_before_event = events_before_pump[0] if events_before_pump else None
        row = {
            "symbol": symbol,
            "rank": top_row.get("rank", ""),
            "growth_pct": top_row.get("growth_pct", ""),
            "growth_fraction": top_row.get("growth_fraction", ""),
            "quote_volume": top_row.get("quote_volume", ""),
            "number_of_trades": top_row.get("number_of_trades", ""),
            "taker_buy_quote_volume": top_row.get("taker_buy_quote_volume", ""),
            "live_saw_symbol_before_pump": bool(events_before_pump),
            "live_saw_symbol_during_pump_hour": bool(events_during_pump_hour),
            "radar_promoted": radar_event is not None,
            "flow_radar_promoted": flow_event is not None,
            "warm_watch": warm_event is not None,
            "precise_scanned": precise_event is not None,
            "category_rejected": category_reject_event is not None,
            "execution_rejected": execution_reject_event is not None,
            "position_opened": position_opened_event is not None,
            "first_live_event_time": first_event.timestamp_utc if first_event is not None else "",
            "first_live_event_before_pump_time": (
                first_before_event.timestamp_utc if first_before_event is not None else ""
            ),
            "first_radar_seen_time": radar_event.timestamp_utc if radar_event is not None else "",
            "first_flow_radar_seen_time": flow_event.timestamp_utc if flow_event is not None else "",
            "first_warm_watch_time": warm_event.timestamp_utc if warm_event is not None else "",
            "first_precise_scan_time": precise_event.timestamp_utc if precise_event is not None else "",
            "first_category_reject_time": (
                category_reject_event.timestamp_utc if category_reject_event is not None else ""
            ),
            "first_execution_reject_time": (
                execution_reject_event.timestamp_utc if execution_reject_event is not None else ""
            ),
            "first_position_opened_time": (
                position_opened_event.timestamp_utc if position_opened_event is not None else ""
            ),
            "first_category_reject_reason": (
                str(
                    category_reject_event.details.get("category_reject_reason")
                    or category_reject_event.details.get("reason")
                    or ""
                )
                if category_reject_event is not None
                else ""
            ),
            "first_execution_reject_event": execution_reject_event.event if execution_reject_event is not None else "",
            "not_scanned_reason": _missed_pump_not_scanned_reason(
                source_status=source_status,
                source_reason=source_reason,
                events_to_period_end=events_to_period_end,
                radar_event=radar_event,
                warm_event=warm_event,
                precise_event=precise_event,
                category_reject_event=category_reject_event,
                execution_reject_event=execution_reject_event,
                position_opened_event=position_opened_event,
            ),
            "visibility_source": visibility_source,
            "visibility_source_status": source_status,
            "visibility_source_reason": source_reason,
            "visibility_events_total": len(events),
            "visibility_events_for_symbol": len(symbol_events_all),
            "visibility_events_before_period_end": len(events_to_period_end),
            "visibility_event_parse_error_count": parse_error_count,
        }
        rows.append(row)
    return rows


def _read_visibility_events(path: Path | None) -> tuple[list[LiveVisibilityEvent], str, str, int]:
    if path is None:
        return [], "not_configured", "visibility_events_csv_not_configured", 0
    if not path.exists():
        return [], "missing", f"visibility_events_csv_missing:{path}", 0
    required_columns = {"timestamp_utc", "event", "symbol", "details_json"}
    events: list[LiveVisibilityEvent] = []
    parse_error_count = 0
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required_columns - set(reader.fieldnames or ()))
        if missing:
            return [], "invalid_schema", "missing_columns:" + ",".join(missing), 0
        for row in reader:
            timestamp_utc = str(row.get("timestamp_utc") or "")
            event_name = str(row.get("event") or "")
            symbol = str(row.get("symbol") or "")
            details_json = str(row.get("details_json") or "{}")
            try:
                timestamp_ms = _parse_visibility_event_timestamp_ms(timestamp_utc)
                details_raw = json.loads(details_json)
                if not isinstance(details_raw, dict):
                    raise ValueError("details_json_not_object")
            except (json.JSONDecodeError, ValueError, TypeError):
                parse_error_count += 1
                continue
            events.append(
                LiveVisibilityEvent(
                    timestamp_utc=timestamp_utc,
                    timestamp_ms=timestamp_ms,
                    event=event_name,
                    symbol=symbol,
                    details=details_raw,
                )
            )
    status = "ok" if parse_error_count == 0 else "partial_parse_errors"
    reason = "ok" if parse_error_count == 0 else f"parse_error_count:{parse_error_count}"
    return events, status, reason, parse_error_count


def _parse_visibility_event_timestamp_ms(value: str) -> int:
    if not value:
        raise ValueError("empty_timestamp_utc")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _first_visibility_event(
    events: list[LiveVisibilityEvent],
    predicate: Callable[[LiveVisibilityEvent], bool],
) -> LiveVisibilityEvent | None:
    for event in events:
        if predicate(event):
            return event
    return None


def _is_visibility_radar_event(event: LiveVisibilityEvent) -> bool:
    return event.event in VISIBILITY_RADAR_EVENTS


def _is_visibility_flow_radar_event(event: LiveVisibilityEvent) -> bool:
    if event.event not in {
        "ticker_radar_promoted",
        "warm_watch_marked",
        "warm_watch_updated",
        "warm_watch_precise_promoted",
        "candidate_dropped_latency_pressure",
        "candidate_expired_backlog_stale",
    }:
        return False
    return str(event.details.get("promotion_source") or "") == DANGER_CHEAP_FLOW_RADAR_SOURCE


def _is_visibility_warm_watch_event(event: LiveVisibilityEvent) -> bool:
    return event.event in VISIBILITY_WARM_WATCH_EVENTS


def _is_visibility_precise_scan_event(event: LiveVisibilityEvent) -> bool:
    if event.event == "signal_symbol_scan_summary":
        evaluated_count = _visibility_int(event.details.get("evaluated_timeframe_count"))
        due_count = _visibility_int(event.details.get("due_timeframe_count"))
        return evaluated_count > 0 or due_count > 0
    return event.event in VISIBILITY_PRECISE_SCAN_EVENTS


def _is_visibility_execution_reject_event(event: LiveVisibilityEvent) -> bool:
    if event.event == "reject_stale_signal":
        return str(event.details.get("stage") or "") == "execution_guard"
    return event.event in VISIBILITY_EXECUTION_REJECT_EVENTS


def _visibility_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _missed_pump_not_scanned_reason(
    *,
    source_status: str,
    source_reason: str,
    events_to_period_end: list[LiveVisibilityEvent],
    radar_event: LiveVisibilityEvent | None,
    warm_event: LiveVisibilityEvent | None,
    precise_event: LiveVisibilityEvent | None,
    category_reject_event: LiveVisibilityEvent | None,
    execution_reject_event: LiveVisibilityEvent | None,
    position_opened_event: LiveVisibilityEvent | None,
) -> str:
    if source_status in {"not_configured", "missing", "invalid_schema"}:
        return source_reason
    if not events_to_period_end:
        return "no_symbol_events_before_period_end"
    if radar_event is None:
        return "radar_not_promoted_before_period_end"
    if warm_event is None:
        return "radar_seen_without_warm_watch_artifact"
    if precise_event is None:
        return "warm_watch_not_precise_scanned_before_period_end"
    if category_reject_event is not None:
        reason = str(category_reject_event.details.get("category_reject_reason") or category_reject_event.details.get("reason") or "")
        return f"category_rejected:{reason}" if reason else "category_rejected"
    if execution_reject_event is not None:
        return f"execution_rejected:{execution_reject_event.event}"
    if position_opened_event is not None:
        return "position_opened"
    return "precise_scanned_no_actionable_signal_or_untracked_reject"


def _load_top_growth_symbol_row(
    *,
    exchange: CcxtFuturesClient,
    symbol: str,
    period_start_ms: int,
    period_end_ms: int,
    snapshot_utc: str,
) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": symbol,
        "status": "failed",
        "reason": "unknown",
        "growth_pct": "",
        "growth_fraction": "",
        "open": "",
        "close": "",
        "high": "",
        "low": "",
        "quote_volume": "",
        "number_of_trades": "",
        "taker_buy_quote_volume": "",
        "candle_timestamp_ms": "",
        "timeframe": Timeframe.H1.value,
    }
    try:
        frame = exchange.fetch_ohlcv(symbol, Timeframe.H1, period_start_ms, period_end_ms - 1)
    except Exception as exc:
        return {**base, "reason": f"fetch_ohlcv_failed:{type(exc).__name__}:{str(exc)[:160]}"}
    if frame.empty:
        return {**base, "reason": "empty_ohlcv"}
    missing = [column for column in REQUIRED_PRICE_COLUMNS if column not in frame.columns]
    if missing:
        return {**base, "reason": "missing_price_columns:" + ",".join(missing)}
    work = frame.copy()
    work["timestamp"] = pd.to_numeric(work["timestamp"], errors="coerce")
    exact = work.loc[work["timestamp"].astype("Int64") == int(period_start_ms)]
    if exact.empty:
        timestamps = pd.to_numeric(work["timestamp"], errors="coerce").dropna().astype("int64")
        reason = "no_exact_hour_candle"
        if not timestamps.empty:
            reason += f":first={int(timestamps.min())}:last={int(timestamps.max())}"
        return {**base, "reason": reason}
    row = exact.sort_values("timestamp").iloc[-1]
    open_price = _series_float_or_none(row, "open")
    close_price = _series_float_or_none(row, "close")
    high_price = _series_float_or_none(row, "high")
    low_price = _series_float_or_none(row, "low")
    if open_price is None or close_price is None or high_price is None or low_price is None or open_price <= 0.0:
        return {
            **base,
            "reason": "invalid_price_values",
            "open": _finite_or_blank(open_price),
            "close": _finite_or_blank(close_price),
            "high": _finite_or_blank(high_price),
            "low": _finite_or_blank(low_price),
            "candle_timestamp_ms": int(period_start_ms),
        }
    growth_fraction = (close_price - open_price) / open_price
    return {
        **base,
        "status": "ok",
        "reason": "ok",
        "growth_pct": growth_fraction * 100.0,
        "growth_fraction": growth_fraction,
        "open": open_price,
        "close": close_price,
        "high": high_price,
        "low": low_price,
        "quote_volume": _finite_or_blank(_series_float_or_none(row, "quote_volume")),
        "number_of_trades": _finite_or_blank(_series_float_or_none(row, "number_of_trades")),
        "taker_buy_quote_volume": _finite_or_blank(_series_float_or_none(row, "taker_buy_quote_volume")),
        "candle_timestamp_ms": int(period_start_ms),
    }


def parse_top_growth_period_start_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LiveStartupError("period-start-utc должен быть ISO timestamp, например 2026-05-12T04:00:00Z") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc.minute != 0 or parsed_utc.second != 0 or parsed_utc.microsecond != 0:
        raise LiveStartupError("period-start-utc должен указывать на начало закрытой 1h свечи")
    return int(parsed_utc.timestamp() * 1000)


def _previous_closed_hour_start_ms(now_ms: int) -> int:
    return ((now_ms // HOUR_MS) - 1) * HOUR_MS



def _validate_top_growth_config_values(config: TopGrowthSnapshotConfig) -> None:
    if config.limit < 1:
        raise LiveStartupError(f"Некорректный top-growth config: limit должен быть >= 1, получено {config.limit!r}")
    _require_finite_config_number("top_growth_min_return_pct", config.min_return_pct, min_value=0.0, allow_equal_min=False)
    _require_finite_config_number("top_growth_fetch_spacing_seconds", config.fetch_spacing_seconds, min_value=0.0, allow_equal_min=True)


class AnomalyMicroLiveRunner:
    def __init__(
        self,
        *,
        config: LiveAnomalyConfig,
        telegram: TelegramConfig,
        exchange_client: CcxtFuturesClient,
        ticker_snapshot_source: LiveTickerSnapshotSource | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.exchange = exchange_client
        self._status_logger = _LiveStatusLogger(logger)
        self.logger = self._status_logger
        self.exchange.set_retry_logger(_LiveRetryLogger(self._status_logger))
        if ticker_snapshot_source is not None:
            self.ticker_snapshot_source = ticker_snapshot_source
        elif config.live_ws_ticker_enabled:
            self.ticker_snapshot_source = BinanceWsAllTickerSnapshotSource(
                exchange=exchange_client,
                stale_ms=config.live_ws_ticker_stale_ms,
                startup_wait_seconds=config.live_ws_ticker_startup_wait_seconds,
                logger=self.logger,
            )
        else:
            self.ticker_snapshot_source = RestLiveTickerSnapshotSource(exchange_client)
        self.aggtrade_source = (
            BinanceWsAggTradeBuffer(
                exchange=exchange_client,
                buffer_minutes=config.live_ws_aggtrade_buffer_minutes,
                stale_ms=config.live_ws_aggtrade_stale_ms,
                logger=self.logger,
            )
            if config.live_ws_aggtrade_enabled
            else None
        )
        self._pump_categories = _resolve_live_pump_categories(config.pump_categories)
        self._pump_categories_by_id = {category.category_id: category for category in self._pump_categories}
        self._prepump_warm_watch_profile = _load_prepump_warm_watch_scoring_profile(config)
        self._prior_fast_fade_cache: dict[tuple[str, str, str, int], dict[str, object]] = {}
        self._symbol_context_snapshots: dict[tuple[str, str, str], LiveSymbolContextSnapshot] = {}
        self._symbol_context_snapshot_cursor = 0
        self._symbol_context_snapshot_last_round_robin_symbols: tuple[str, ...] = ()
        self._symbol_context_priority_requests: dict[str, LiveSymbolContextPriorityRequest] = {}
        self._symbol_context_universe_size = 0
        self._last_symbol_context_snapshot_at_ms = 0
        self._last_symbol_context_snapshot_status = "not_started"
        self._last_context_reprepare_at_monotonic = 0.0
        self._last_context_reprepare_deferred_at_monotonic = 0.0
        self._context_reprepare_total = 0
        self.artifacts = LiveArtifactWriter(
            config.results_dir / "live_anomaly_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        )
        self.telegram = TelegramDispatcher(
            telegram,
            logger=logger,
            cooldown_seconds=config.telegram_cooldown_seconds,
            event_writer=self.artifacts.append_event,
        )
        self._open_positions: dict[str, LivePosition] = {}
        self._opening_symbols: set[str] = set()
        self._order_reconcile_symbols_by_key: dict[str, str] = {}
        self._recent_stops: dict[str, list[float]] = {}
        self._active_symbols: dict[str, LiveActiveSymbol] = {}
        self._active_symbols_seen: set[str] = set()
        self._warm_watch: dict[str, LiveWarmWatch] = {}
        self._ticker_radar_watch: dict[str, LiveTickerRadarWatch] = {}
        self._ticker_radar_snapshots: dict[str, ExchangeTickerSnapshot] = {}
        self._ticker_radar_quote_delta_history: dict[str, deque[float]] = {}
        self._ticker_radar_trade_delta_history: dict[str, deque[float]] = {}
        self._last_ticker_radar_at_ms = 0
        self._last_ticker_radar_health_status = "unknown"
        self._session_top_tracker = LiveSessionTopTracker(limit=LIVE_SESSION_TOP_LIMIT)
        self._last_session_top_growth_artifact_at_ms = 0
        self._run_started_wall_ms = int(time.time() * 1000)
        self._live_top_growth_task: LiveTopGrowthAuditTask | None = None
        self._live_top_growth_completed_periods: set[int] = set()
        self._seen_decisions: set[tuple[str, str, str, int]] = set()
        self._last_signal_scan_closed_at: dict[tuple[str, str], int] = {}
        self._dependency_retry_cooldowns: dict[tuple[str, str, str, int], LiveDependencyRetryCooldown] = {}
        self._opened_positions_total = 0
        self._closed_positions_total = 0
        self._closed_pnl_usdt_total = 0.0
        self._closed_notional_usdt_total = 0.0
        self._orphan_orders_cancelled_total = 0
        self._detected_anomalies_total = 0
        self._delayed_replay_pending: dict[str, dict[str, object]] = {}
        self._delayed_replay_completed: set[str] = set()
        self._delayed_replay_enqueued_total = 0
        self._delayed_replay_processed_total = 0
        self._delayed_replay_skipped_total = 0
        self._delayed_replay_decision_snapshots: dict[tuple[str, str, str, int], str] = {}
        self._idle_since_ms: int | None = None
        self._state_lock = threading.RLock()
        self._inactive_cursor = 0
        self._order_reconcile_cursor = 0
        self._network_degraded = False
        self._network_degraded_first_reason = ""
        self._network_degraded_telegram_last_enqueue_at = 0.0
        self._current_cycle_aggtrade_cache: dict[str, list[AggTradeRawRange]] = {}
        self._aggtrade_raw_process_cache: dict[str, list[AggTradeRawRange]] = {}
        self._cycle_aggtrade_requests = 0
        self._cycle_aggtrade_network_calls = 0
        self._cycle_aggtrade_cache_hits = 0
        self._cycle_aggtrade_process_cache_hits = 0
        self._cycle_aggtrade_coalesced_missing_ranges = 0
        self._cycle_aggtrade_rest_fetched_ms = 0
        self._cycle_ws_aggtrade_backfill_reads = 0
        self._cycle_ws_aggtrade_backfilled_rows = 0
        self._cycle_ws_aggtrade_coverage_pending = 0
        self._cycle_ws_aggtrade_not_connected_backfill_reads = 0
        self._cycle_aggtrade_gap_prefetch_symbols = 0
        self._cycle_aggtrade_gap_prefetch_requested_ranges = 0
        self._cycle_aggtrade_gap_prefetch_missing_ranges = 0
        self._cycle_aggtrade_gap_prefetch_backfill_ranges = 0
        self._cycle_aggtrade_gap_prefetch_rows = 0
        self._cycle_aggtrade_gap_prefetch_pending = 0
        self._cycle_ohlcv_cache_filled_reads = 0
        self._cycle_ohlcv_cache_fetched_rows = 0
        self._cycle_ohlcv_cache_gap_reads = 0
        self._cycle_ohlcv_cache_remaining_gap_count = 0
        self._current_batch_symbol_scan_mode: dict[str, str] = {}
        self._current_scheduler_source = "uninitialized"
        self._current_inactive_scan_slots = 0
        self._current_inactive_scan_slots_source = ""
        self._current_inactive_cold_coverage_gate_reason = ""
        self._current_inactive_cold_coverage_health_pct = 0.0
        self._current_inactive_cold_coverage_health_threshold_pct = DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0
        self._current_inactive_cold_coverage_adaptive_score = 0.0
        self._current_inactive_cold_coverage_health_factor = 0.0
        self._current_inactive_cold_coverage_speed_factor = 0.0
        self._current_inactive_cold_coverage_active_factor = 0.0
        self._current_inactive_cold_coverage_load_factor = 0.0
        self._current_inactive_cold_coverage_pressure_ewma = 0.0
        self._current_inactive_cold_coverage_cycle_seconds_ewma = DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS
        self._current_latency_sla_status = "not_started"
        self._current_latency_sla_optional_scans_allowed = True
        self._current_latency_sla_due_scan_p95_seconds: float | None = None
        self._current_latency_sla_due_scan_max_seconds: float | None = None
        self._current_latency_sla_due_scan_samples = 0
        self._current_latency_sla_threshold_seconds = float(config.latency_sla_due_scan_p95_seconds)
        self._current_latency_sla_min_due_samples = int(config.latency_sla_min_due_samples)
        self._current_latency_sla_reason = "not_started"
        self._current_adaptive_precise_budget_status = "not_started"
        self._current_adaptive_precise_budget_reason = "not_started"
        self._current_adaptive_precise_budget_limit: int | None = None
        self._current_adaptive_precise_budget_radar_slots: int | None = None
        self._current_candidate_queue_status = "not_started"
        self._current_candidate_queue_reason = "not_started"
        self._current_candidate_queue_radar_total_before = 0
        self._current_candidate_queue_radar_total_after = 0
        self._current_candidate_queue_warm_total_before = 0
        self._current_candidate_queue_warm_total_after = 0
        self._current_candidate_queue_actionable_sample_count = 0
        self._current_candidate_queue_dropped_pressure_count = 0
        self._current_candidate_queue_expired_backlog_stale_count = 0
        self._current_candidate_queue_top_score: float | None = None
        self._cold_coverage_pressure_ewma = 0.0
        self._scheduler_cycle_seconds_ewma = DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS
        self._cycle_precise_scan_symbols = 0
        self._cycle_cold_scanned_symbols = 0
        self._cycle_cold_due_timeframe_count = 0
        self._cycle_cold_evaluated_timeframe_count = 0
        self._cycle_cold_retryable_dependency_count = 0
        self._cycle_cold_signal_count = 0
        self._cycle_cold_order_attempt_count = 0
        self._cycle_dependency_retry_cooldown_skipped = 0
        self._cycle_dependency_retry_cooldown_expired = 0
        self._cycle_dependency_retry_cooldown_skip_keys: set[tuple[str, str, str, int]] = set()
        self._dependency_retry_cooldown_skipped_total = 0
        self._dependency_retry_cooldown_expired_total = 0
        self._cold_scanned_symbols_total = 0
        self._cold_evaluated_timeframe_total = 0
        self._cold_retryable_dependency_total = 0
        self._cold_signal_total = 0
        self._cold_order_attempt_total = 0
        self._cycle_inactive_visit_symbols = 0
        self._cycle_deferred_inactive_subminute_pairs = 0
        self._ws_health_observed_seconds = 0.0
        self._ws_health_healthy_seconds = 0.0
        self._last_ws_health_sample_at = time.monotonic()
        self._symbol_universe_scan_started_at = time.monotonic()
        self._last_symbol_universe_cycle_seconds: float | None = None
        self._symbol_universe_cycle_index = 1
        self._symbol_universe_batch_index = 0
        self._current_symbol_universe_cycle_index = 1
        self._current_symbol_universe_batch_index = 1
        self._ohlcv_cache_storage = (
            ParquetStorage(base_dir=config.cache_dir)
            if config.live_ohlcv_cache_enabled and config.cache_dir is not None
            else None
        )
        self._live_ohlcv_frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._live_ohlcv_write_buffer: dict[tuple[str, str, str], list[pd.DataFrame]] = {}
        self._live_ohlcv_pending_rows = 0
        self._last_live_ohlcv_cache_flush_at = time.monotonic()
        self._run_started_monotonic = time.monotonic()
        self._live_loop_started_monotonic: float | None = None
        self._live_sources_closed = False

    def shutdown(self, *, reason: str) -> None:
        self._flush_live_ohlcv_cache_if_due(force=True, reason=reason)
        self._close_live_sources()

    def run(self) -> int:
        self._validate_startup()
        explicit_symbols = bool(self.config.symbols)
        symbols = list(self.config.symbols) if explicit_symbols else self.exchange.list_usdt_swap_symbols()
        symbols = self._filter_live_symbol_universe(symbols, explicit_symbols=explicit_symbols)
        if not symbols:
            raise LiveStartupError("Нет символов для live-обхода")
        self._symbol_context_universe_size = len(symbols)
        category_ids = ",".join(category.category_id for category in self._pump_categories)
        timeframe_pairs_label = _format_timeframe_pairs(self.config.timeframe_pairs)
        self.logger(
            f"старт · символов {len(symbols)} · TF {timeframe_pairs_label} · max {self.config.max_open_positions}"
        )
        self.logger(f"артефакты {self.artifacts.root}")
        trading_mode = "с торговлей" if self.config.confirm_real_orders else "без торговли"
        self.artifacts.append_event(
            "live_cache_config",
            "__live__",
            {
                "live_ohlcv_cache_enabled": bool(self.config.live_ohlcv_cache_enabled),
                "live_ohlcv_cache_write_enabled": bool(self.config.live_ohlcv_cache_write_enabled),
                "live_ohlcv_cache_flush_interval_seconds": self.config.live_ohlcv_cache_flush_interval_seconds,
                "live_ohlcv_cache_max_buffer_rows": self.config.live_ohlcv_cache_max_buffer_rows,
                "live_ohlcv_cache_flush_max_symbol_timeframes": (
                    self.config.live_ohlcv_cache_flush_max_symbol_timeframes
                    if self.config.live_ohlcv_cache_flush_max_symbol_timeframes is not None
                    else ""
                ),
                "danger_continue_after_order_position_errors": bool(
                    self.config.danger_continue_after_order_position_errors
                ),
                "order_position_integrity_policy": (
                    "telegram_and_continue"
                    if self.config.danger_continue_after_order_position_errors
                    else "telegram_and_halt"
                ),
                "delayed_replay_enabled": bool(self.config.delayed_replay_enabled),
                "delayed_replay_contract": DELAYED_REPLAY_CONTRACT,
                "delayed_replay_policy": "capture_final_live_decisions_process_only_when_idle_cache_only_no_orders",
                "delayed_replay_delay_seconds": float(self.config.delayed_replay_delay_seconds),
                "delayed_replay_min_idle_seconds": float(self.config.delayed_replay_min_idle_seconds),
                "delayed_replay_max_cases_per_cycle": int(self.config.delayed_replay_max_cases_per_cycle),
                "delayed_replay_max_cycle_seconds": float(self.config.delayed_replay_max_cycle_seconds),
                "delayed_replay_outcome_lookahead_seconds": float(self.config.delayed_replay_outcome_lookahead_seconds),
                "delayed_replay_telegram_policy": "enabled_with_delayed_replay",
                "discrete_signal_missed_telegram_enabled": bool(self.config.discrete_signal_missed_telegram_enabled),
                "discrete_signal_missed_policy": (
                    "reject_current_live_price_but_record_when_signal_snapshot_was_executable"
                ),
                "delayed_replay_queue_file": str(self.artifacts.delayed_replay_queue_path.relative_to(self.artifacts.root)),
                "delayed_replay_results_file": str(self.artifacts.delayed_replay_results_path.relative_to(self.artifacts.root)),
                "cache_dir": str(self.config.cache_dir) if self.config.cache_dir is not None else "",
                "cache_provider": "parquet_tail_fetch_v1" if self._ohlcv_cache_storage is not None else "disabled",
                "inactive_subminute_scan_policy": "DANGER_default_precise_cold_coverage_idle_health_gated",
                "inactive_scan_slots_default_policy": (
                    f"DANGER default {DANGER_DEFAULT_INACTIVE_COLD_COVERAGE_SLOTS_PER_CYCLE} precise cold-coverage "
                    "slots per cycle for subminute ticker-radar live, gated by WS health > "
                    f"{DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0:.1f}% and no active/opening/open positions; "
                    "set inactive_scan_slots_per_cycle=0 to disable cold coverage"
                ),
                "inactive_cold_coverage_default_danger": True,
                "inactive_cold_coverage_health_gate_min_pct": DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0,
                "inactive_cold_coverage_requires_no_active_or_position": True,
                "latency_sla_controller_enabled": bool(self.config.latency_sla_controller_enabled),
                "latency_sla_policy": "protect_active_and_radar_due_scan_latency_by_gating_optional_warm_and_cold",
                "candidate_queue_pressure_policy": "drop_stale_or_weak_radar_warm_tail_under_latency_pressure_no_cli_knobs",
                "adaptive_precise_budget_policy": "limit_radar_precise_slots_under_latency_or_queue_pressure_active_symbols_never_dropped",
                "dependency_retry_cooldown_policy": "retryable_data_dependencies_are_not_rescanned_every_cycle_until_cooldown_or_stale_timeout",
                "dependency_retry_min_cooldown_ms": int(DEPENDENCY_RETRY_MIN_COOLDOWN_MS),
                "dependency_retry_max_cooldown_ms": int(DEPENDENCY_RETRY_MAX_COOLDOWN_MS),
                "adaptive_precise_budget_breached_radar_slots": int(ADAPTIVE_PRECISE_BUDGET_BREACHED_RADAR_SLOTS),
                "adaptive_precise_budget_pressure_radar_slots": int(ADAPTIVE_PRECISE_BUDGET_PRESSURE_RADAR_SLOTS),
                "candidate_queue_pressure_min_keep": int(CANDIDATE_QUEUE_PRESSURE_MIN_KEEP),
                "candidate_queue_pressure_max_radar": int(CANDIDATE_QUEUE_PRESSURE_MAX_RADAR),
                "candidate_queue_pressure_max_warm": int(CANDIDATE_QUEUE_PRESSURE_MAX_WARM),
                "latency_sla_due_scan_p95_seconds": float(self.config.latency_sla_due_scan_p95_seconds),
                "latency_sla_min_due_samples": int(self.config.latency_sla_min_due_samples),
                "danger_local_entry_position_guard_enabled": bool(self.config.danger_local_entry_position_guard_enabled),
                "danger_local_entry_position_guard_source": (
                    DANGER_LOCAL_ENTRY_POSITION_GUARD_SOURCE
                    if self.config.danger_local_entry_position_guard_enabled
                    else "pre_entry_exchange_position_fetch"
                ),
                "danger_ticker_flow_radar_enabled": bool(self.config.danger_ticker_flow_radar_enabled),
                "danger_ticker_flow_radar_source": DANGER_CHEAP_FLOW_RADAR_SOURCE,
                "danger_ticker_flow_radar_min_quote_volume_delta_usdt": float(
                    self.config.danger_ticker_flow_radar_min_quote_volume_delta_usdt
                ),
                "danger_ticker_flow_radar_min_trade_count_delta": int(
                    self.config.danger_ticker_flow_radar_min_trade_count_delta
                ),
                "danger_ticker_flow_radar_min_trade_count_delta_ratio": float(
                    self.config.danger_ticker_flow_radar_min_trade_count_delta_ratio
                ),
                "danger_ticker_flow_radar_price_delta_window_pct": (
                    f"{self.config.danger_ticker_flow_radar_min_price_delta_pct:.6f}:"
                    f"{self.config.danger_ticker_flow_radar_max_price_delta_pct:.6f}"
                ),
                "warm_watch_enabled": bool(self.config.warm_watch_enabled),
                "warm_watch_source": WARM_WATCH_SOURCE,
                "warm_watch_ttl_ms": int(self.config.warm_watch_ttl_ms),
                "warm_watch_min_observations_for_precise": int(
                    self.config.warm_watch_min_observations_for_precise
                ),
                "warm_watch_price_delta_window_pct": (
                    f"{self.config.warm_watch_min_price_delta_pct:.6f}:"
                    f"{self.config.warm_watch_max_price_delta_pct:.6f}"
                ),
                "warm_watch_aggtrade_target_cap": int(self.config.warm_watch_aggtrade_target_cap),
                "prepump_warm_watch_scoring_enabled": bool(self.config.prepump_warm_watch_scoring_enabled),
                "prepump_warm_watch_scoring_contract": PREPUMP_WARM_WATCH_SCORING_CONTRACT,
                "prepump_warm_watch_profile_status": self._prepump_warm_watch_profile.status,
                "prepump_warm_watch_profile_reason": self._prepump_warm_watch_profile.reason,
                "prepump_warm_watch_profile_csv": self._prepump_warm_watch_profile.source_path,
                "prepump_warm_watch_profile_feature_count": len(self._prepump_warm_watch_profile.rules),
                "prepump_warm_watch_score_weight": float(self.config.prepump_warm_watch_score_weight),
                "prepump_warm_watch_windows": str(self.config.prepump_warm_watch_windows),
                "prepump_warm_watch_min_coverage_ratio": float(self.config.prepump_warm_watch_min_coverage_ratio),
                "prepump_warm_watch_policy": "score_only_for_warm_watch_priority_no_entry_filter_no_trade_veto",
                "symbol_context_snapshot_enabled": bool(self.config.symbol_context_snapshot_enabled),
                "symbol_context_snapshot_contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
                "symbol_context_snapshot_policy": "always_startup_backfill_then_cache_only_rolling_table_no_precise_scan_context_fetch",
                "symbol_context_startup_backfill_policy": "always_on_levels_timeframes_only_no_subminute_entry_timeframes",
                "symbol_context_startup_readiness_policy": (
                    "real_orders_refuse_startup_when_ready_symbol_or_snapshot_ratio_below_internal_threshold"
                ),
                "symbol_context_startup_min_ready_symbol_ratio": float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SYMBOL_RATIO),
                "symbol_context_startup_min_ready_snapshot_ratio": float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SNAPSHOT_RATIO),
                "symbol_context_snapshot_interval_seconds": float(
                    self.config.symbol_context_snapshot_interval_seconds
                ),
                "symbol_context_snapshot_symbols_per_cycle": int(
                    self.config.symbol_context_snapshot_symbols_per_cycle
                ),
                "symbol_context_snapshot_fresh_ms": int(self.config.symbol_context_snapshot_fresh_ms),
                "symbol_context_snapshot_effective_fresh_ms": int(
                    self._effective_symbol_context_snapshot_fresh_ms(symbols_total=len(symbols))
                ),
                "symbol_context_snapshot_max_cycle_seconds": float(
                    self.config.symbol_context_snapshot_max_cycle_seconds
                ),
                "symbol_context_snapshot_min_coverage_ratio": float(
                    self.config.symbol_context_snapshot_min_coverage_ratio
                ),
                "symbol_context_snapshot_max_gap_candles": int(
                    self.config.symbol_context_snapshot_max_gap_candles
                ),
                "symbol_context_priority_ttl_ms": int(self.config.symbol_context_priority_ttl_ms),
                "symbol_context_priority_policy": "open_active_retryable_dependency_radar_warm_then_round_robin",
                "symbol_context_snapshot_file": self.artifacts.symbol_context_snapshot_path.name,
                "live_top_growth_policy": "always_on_latency_gated_incremental_closed_1h_exchange_candles_same_run_visibility_no_ticker_fallback",
                "live_top_growth_min_return_pct": float(DEFAULT_LIVE_TOP_GROWTH_MIN_RETURN_PCT),
                "live_top_growth_limit": int(DEFAULT_LIVE_TOP_GROWTH_LIMIT),
                "live_top_growth_idle_symbols_per_cycle": int(DEFAULT_LIVE_TOP_GROWTH_IDLE_SYMBOLS_PER_CYCLE),
                "live_top_growth_idle_max_cycle_seconds": float(DEFAULT_LIVE_TOP_GROWTH_IDLE_MAX_CYCLE_SECONDS),
                "live_top_growth_conservative_symbols_per_cycle": int(DEFAULT_LIVE_TOP_GROWTH_CONSERVATIVE_SYMBOLS_PER_CYCLE),
                "live_top_growth_conservative_max_cycle_seconds": float(DEFAULT_LIVE_TOP_GROWTH_CONSERVATIVE_MAX_CYCLE_SECONDS),
                "live_top_growth_visibility_events_csv": str(self.artifacts.events_path),
                "danger_micro_cache_policy": "wider_ws_aggtrade_buffer_for_active_warm_watch_radar_and_current_cold_only_no_full_universe_subscription",
                "symbol_batch_size_role": "legacy inactive scan cap, not WS market discovery",
                "subminute_entry_pairs_present": bool(_live_config_has_subminute_entry_pairs(self.config)),
                "ticker_radar_required_for_inactive_subminute_gate": bool(
                    _live_config_has_subminute_entry_pairs(self.config)
                ),
                "ticker_snapshot_source": self.ticker_snapshot_source.source_id,
                "ticker_snapshot_degraded_source_policy": "rest_fetch_tickers_after_primary_ws_failure_with_live_events",
                "live_ws_ticker_enabled": bool(self.config.live_ws_ticker_enabled),
                "live_ws_ticker_stale_ms": int(self.config.live_ws_ticker_stale_ms),
                "live_ws_ticker_startup_wait_seconds": float(self.config.live_ws_ticker_startup_wait_seconds),
                "live_ws_ticker_startup_seed_enabled": bool(self.config.live_ws_ticker_startup_seed_enabled),
                "aggtrade_source": self.aggtrade_source.source_id if self.aggtrade_source is not None else "rest_fetch_aggtrades",
                "live_ws_aggtrade_enabled": bool(self.config.live_ws_aggtrade_enabled),
                "live_ws_aggtrade_stale_ms": int(self.config.live_ws_aggtrade_stale_ms),
                "live_ws_aggtrade_buffer_minutes": int(self.config.live_ws_aggtrade_buffer_minutes),
                "live_ws_aggtrade_max_backfill_ms": int(self.config.live_ws_aggtrade_max_backfill_ms),
                "live_aggtrade_rest_cache_ttl_ms": int(self.config.live_aggtrade_rest_cache_ttl_ms),
                "live_aggtrade_rest_cache_padding_ms": int(self.config.live_aggtrade_rest_cache_padding_ms),
                "exclude_default_high_cap_symbols": bool(self.config.exclude_default_high_cap_symbols),
                "excluded_high_cap_bases": sorted(LIVE_DEFAULT_EXCLUDED_HIGH_CAP_BASES),
            },
        )
        if self.config.confirm_real_orders:
            self._validate_live_account_mode()
            self._close_startup_exchange_positions(symbols)
        self._startup_backfill_symbol_context_cache(symbols)
        self._validate_startup_symbol_context_readiness(symbols)
        self._seed_startup_ticker_radar(symbols)
        try:
            self._validate_required_ticker_radar_source(symbols)
        except LiveStartupError:
            self._close_live_sources()
            raise
        self._run_started_monotonic = time.monotonic()
        self._startup_status("live", "запуск циклов")
        self._live_loop_started_monotonic = time.monotonic()
        self._status_logger.finish_status()
        self.telegram.send(
            channel="events",
            key="live_started",
            text=(
                f"{SERVICE_WORK_EMOJI} <b>Запуск {trading_mode}</b>\n\n"
                f"{_telegram_code(timeframe_pairs_label)}"
            ),
        )
        cycle = 0
        while self.config.max_cycles is None or cycle < self.config.max_cycles:
            cycle += 1
            cycle_started = time.monotonic()
            self._reset_cycle_fetch_state()
            try:
                opened_before, _, closed_before, orphan_before = self._live_counts()
                ticker_started = time.monotonic()
                ticker_stats = self._maybe_update_ticker_radar(symbols)
                ticker_seconds = time.monotonic() - ticker_started
                context_snapshot_stats = LiveSymbolContextSnapshotCycleStats(
                    enabled=bool(self.config.symbol_context_snapshot_enabled),
                    attempted=False,
                    status="not_attempted",
                    reason="critical_scan_path_pending",
                    symbols_total=len(symbols),
                    effective_fresh_ms=self._effective_symbol_context_snapshot_fresh_ms(symbols_total=len(symbols)),
                    output_file=self.artifacts.symbol_context_snapshot_path.name,
                )
                context_snapshot_seconds = 0.0
                top_growth_stats = LiveTopGrowthAuditCycleStats(
                    enabled=True,
                    status="not_attempted",
                    reason="critical_scan_path_pending",
                )
                top_growth_seconds = 0.0
                batch_select_started = time.monotonic()
                batch = self._next_symbol_batch(symbols)
                batch_select_seconds = time.monotonic() - batch_select_started
                ws_aggtrade_started = time.monotonic()
                ws_aggtrade_stats = self._update_ws_aggtrade_subscriptions(batch)
                ws_aggtrade_seconds = time.monotonic() - ws_aggtrade_started
                scan_started = time.monotonic()
                signals = self._scan_batch(batch)
                scan_seconds = time.monotonic() - scan_started
                open_started = time.monotonic()
                for signal in signals:
                    self._maybe_open_position(signal)
                open_seconds = time.monotonic() - open_started
                reconcile_started = time.monotonic()
                orphan_cancelled = self._reconcile_orphan_orders(symbols, cycle=cycle)
                reconcile_seconds = time.monotonic() - reconcile_started
                flush_started = time.monotonic()
                self._flush_live_ohlcv_cache_if_due(reason="cycle")
                cache_flush_seconds = time.monotonic() - flush_started
                context_snapshot_started = time.monotonic()
                context_snapshot_stats = self._maybe_update_symbol_context_snapshots(symbols)
                context_snapshot_seconds = time.monotonic() - context_snapshot_started
                top_growth_started = time.monotonic()
                top_growth_stats = self._maybe_process_live_top_growth_audit(symbols)
                top_growth_seconds = time.monotonic() - top_growth_started
                cycle_seconds = time.monotonic() - cycle_started
                opened_total, open_positions, closed_total, orphan_total = self._live_counts()
                active_symbol_count = self._active_live_symbol_count()
                active_symbols_seen_total = self._active_live_symbols_seen_total()
                closed_pnl_pct = self._closed_pnl_pct_total()
                detected_anomalies_total = self._detected_anomalies_count()
                full_cycle_seconds = self._full_symbol_cycle_seconds(
                    batch_seconds=cycle_seconds,
                    batch_size=len(batch),
                    symbols_total=len(symbols),
                )
                ws_healthy, ws_health_reason = self._is_ws_healthy_for_cycle(ticker_stats, ws_aggtrade_stats)
                ws_health_pct = self._record_ws_health_sample(healthy=ws_healthy)
                self._record_scheduler_cycle_seconds(cycle_seconds)
                self._record_cold_coverage_pressure_sample()
                data_status_text = self._live_data_status_text(
                    ticker_stats=ticker_stats,
                    aggtrade_stats=ws_aggtrade_stats,
                    ws_health_reason=ws_health_reason,
                )
                session_top_snapshot = self._session_top_tracker.snapshot(now_ms=int(time.time() * 1000))
                self.artifacts.append_event(
                    "live_cycle_summary",
                    "__live__",
                    {
                        "cycle": cycle,
                        "data_status": data_status_text,
                        "scheduler_cycle_seconds": round(cycle_seconds, 3),
                        "batch_in_full_cycle": self._current_symbol_universe_batch_index,
                        "full_symbol_cycle": self._current_symbol_universe_cycle_index,
                        "legacy_batch_seconds": round(cycle_seconds, 3),
                        "batch_seconds": round(cycle_seconds, 3),
                        "scheduler_source": self._current_scheduler_source,
                        "inactive_scan_slots_per_cycle": self._current_inactive_scan_slots,
                        "inactive_scan_slots_source": self._current_inactive_scan_slots_source,
                        "ticker_radar_seconds": round(ticker_seconds, 3),
                        "ticker_radar_status": ticker_stats.status,
                        "ticker_radar_attempted": bool(ticker_stats.attempted),
                        "ticker_radar_source": ticker_stats.source,
                        "ticker_radar_symbols_total": ticker_stats.symbols_total,
                        "ticker_radar_ok_count": ticker_stats.ok_count,
                        "ticker_radar_missing_count": ticker_stats.missing_count,
                        "ticker_radar_promoted_count": ticker_stats.promoted_count,
                        "ticker_radar_promotion_candidates_count": ticker_stats.promotion_candidates_count,
                        "warm_watch_marked_count": ticker_stats.warm_watch_marked_count,
                        "warm_watch_promoted_count": ticker_stats.warm_watch_promoted_count,
                        "warm_watch_rejected_count": ticker_stats.warm_watch_rejected_count,
                        "warm_watch_deferred_count": ticker_stats.warm_watch_deferred_count,
                        "latency_sla_status": self._current_latency_sla_status,
                        "latency_sla_optional_scans_allowed": bool(self._current_latency_sla_optional_scans_allowed),
                        "latency_sla_due_scan_p95_seconds": (
                            round(float(self._current_latency_sla_due_scan_p95_seconds), 3)
                            if self._current_latency_sla_due_scan_p95_seconds is not None
                            else ""
                        ),
                        "latency_sla_due_scan_max_seconds": (
                            round(float(self._current_latency_sla_due_scan_max_seconds), 3)
                            if self._current_latency_sla_due_scan_max_seconds is not None
                            else ""
                        ),
                        "latency_sla_due_scan_samples": int(self._current_latency_sla_due_scan_samples),
                        "latency_sla_threshold_seconds": float(self._current_latency_sla_threshold_seconds),
                        "latency_sla_min_due_samples": int(self._current_latency_sla_min_due_samples),
                        "latency_sla_reason": self._current_latency_sla_reason,
                        "candidate_queue_status": self._current_candidate_queue_status,
                        "candidate_queue_reason": self._current_candidate_queue_reason,
                        "candidate_queue_radar_total_before": self._current_candidate_queue_radar_total_before,
                        "candidate_queue_radar_total_after": self._current_candidate_queue_radar_total_after,
                        "candidate_queue_warm_total_before": self._current_candidate_queue_warm_total_before,
                        "candidate_queue_warm_total_after": self._current_candidate_queue_warm_total_after,
                        "candidate_queue_actionable_sample_count": self._current_candidate_queue_actionable_sample_count,
                        "candidate_queue_dropped_pressure_count": self._current_candidate_queue_dropped_pressure_count,
                        "candidate_queue_expired_backlog_stale_count": self._current_candidate_queue_expired_backlog_stale_count,
                        "candidate_queue_top_score": (
                            round(float(self._current_candidate_queue_top_score), 6)
                            if self._current_candidate_queue_top_score is not None
                            else ""
                        ),
                        "adaptive_precise_budget_status": self._current_adaptive_precise_budget_status,
                        "adaptive_precise_budget_reason": self._current_adaptive_precise_budget_reason,
                        "adaptive_precise_budget_limit": (
                            self._current_adaptive_precise_budget_limit
                            if self._current_adaptive_precise_budget_limit is not None
                            else ""
                        ),
                        "adaptive_precise_budget_radar_slots": (
                            self._current_adaptive_precise_budget_radar_slots
                            if self._current_adaptive_precise_budget_radar_slots is not None
                            else ""
                        ),
                        "symbol_context_snapshot_seconds": round(context_snapshot_seconds, 3),
                        "symbol_context_snapshot_enabled": bool(context_snapshot_stats.enabled),
                        "symbol_context_snapshot_attempted": bool(context_snapshot_stats.attempted),
                        "symbol_context_snapshot_status": context_snapshot_stats.status,
                        "symbol_context_snapshot_reason": context_snapshot_stats.reason,
                        "symbol_context_snapshot_selected_symbols_count": len(context_snapshot_stats.selected_symbols),
                        "symbol_context_snapshot_updated_count": int(context_snapshot_stats.updated_count),
                        "symbol_context_snapshot_failed_count": int(context_snapshot_stats.failed_count),
                        "symbol_context_snapshot_skipped_count": int(context_snapshot_stats.skipped_count),
                        "symbol_context_snapshot_cycle_budget_seconds": round(
                            float(context_snapshot_stats.cycle_budget_seconds), 3
                        ),
                        "symbol_context_snapshot_effective_fresh_ms": int(context_snapshot_stats.effective_fresh_ms),
                        "symbol_context_snapshot_file": context_snapshot_stats.output_file,
                        "live_top_growth_seconds": round(top_growth_seconds, 3),
                        "live_top_growth_status": top_growth_stats.status,
                        "live_top_growth_reason": top_growth_stats.reason,
                        "live_top_growth_period_start_ms": top_growth_stats.period_start_ms or "",
                        "live_top_growth_period_end_ms": top_growth_stats.period_end_ms or "",
                        "live_top_growth_processed_count": int(top_growth_stats.processed_count),
                        "live_top_growth_remaining_count": int(top_growth_stats.remaining_count),
                        "live_top_growth_symbols_total": int(top_growth_stats.symbols_total),
                        "live_top_growth_top_count": int(top_growth_stats.top_count),
                        "session_top_label": str(session_top_snapshot.get("session_label") or ""),
                        "session_top_phase": str(session_top_snapshot.get("session_phase") or ""),
                        "session_top_primary": str(session_top_snapshot.get("session_primary") or ""),
                        "session_top_secondary": str(session_top_snapshot.get("session_secondary") or ""),
                        "session_top_window_label": str(session_top_snapshot.get("top_window_label") or ""),
                        "session_top_window_hours": session_top_snapshot.get("top_window_hours", ""),
                        "session_top_symbols_tracked": int(session_top_snapshot.get("symbols_tracked") or 0),
                        "session_top_symbols_with_positive_growth": int(session_top_snapshot.get("symbols_with_positive_growth") or 0),
                        "danger_flow_radar_promoted_count": ticker_stats.danger_flow_radar_promoted_count,
                        "danger_flow_radar_candidate_count": ticker_stats.danger_flow_radar_candidate_count,
                        "detected_anomalies_total": detected_anomalies_total,
                        "batch_select_seconds": round(batch_select_seconds, 3),
                        "ws_aggtrade_subscription_seconds": round(ws_aggtrade_seconds, 3),
                        "ws_aggtrade_enabled": bool(ws_aggtrade_stats.enabled),
                        "ws_aggtrade_source": ws_aggtrade_stats.source,
                        "ws_aggtrade_target_count": ws_aggtrade_stats.target_count,
                        "ws_aggtrade_subscribed_count": ws_aggtrade_stats.subscribed_count,
                        "ws_aggtrade_connection_status": ws_aggtrade_stats.connection_status,
                        "ws_aggtrade_last_error": ws_aggtrade_stats.last_error[:500],
                        "ws_aggtrade_error_label": _ws_error_short_label(ws_aggtrade_stats.last_error),
                        "ticker_radar_error_label": (
                            _ws_error_short_label(ticker_stats.reason)
                            if ticker_stats.status in {"failed", "degraded_rest_fallback"}
                            else ""
                        ),
                        "ws_healthy": bool(ws_healthy),
                        "ws_health_reason": ws_health_reason,
                        "ws_health_pct": round(ws_health_pct, 4),
                        "ws_health_observed_seconds": round(self._ws_health_observed_seconds, 3),
                        "ws_health_healthy_seconds": round(self._ws_health_healthy_seconds, 3),
                        "signal_scan_seconds": round(scan_seconds, 3),
                        "open_signal_seconds": round(open_seconds, 3),
                        "order_reconcile_seconds": round(reconcile_seconds, 3),
                        "cache_flush_seconds": round(cache_flush_seconds, 3),
                        "full_symbol_cycle_seconds": round(full_cycle_seconds, 3),
                        "opened_total": opened_total,
                        "open_positions": open_positions,
                        "active_symbol_count": active_symbol_count,
                        "active_symbols_seen_total": active_symbols_seen_total,
                        "cold_scanned_symbols_cycle": self._cycle_cold_scanned_symbols,
                        "cold_due_timeframe_count_cycle": self._cycle_cold_due_timeframe_count,
                        "cold_evaluated_timeframe_count_cycle": self._cycle_cold_evaluated_timeframe_count,
                        "cold_retryable_dependency_count_cycle": self._cycle_cold_retryable_dependency_count,
                        "cold_signal_count_cycle": self._cycle_cold_signal_count,
                        "cold_order_attempt_count_cycle": self._cycle_cold_order_attempt_count,
                        "dependency_retry_cooldown_active_count": len(self._dependency_retry_cooldowns),
                        "dependency_retry_cooldown_skipped_cycle": self._cycle_dependency_retry_cooldown_skipped,
                        "dependency_retry_cooldown_expired_cycle": self._cycle_dependency_retry_cooldown_expired,
                        "dependency_retry_cooldown_skipped_total": self._dependency_retry_cooldown_skipped_total,
                        "dependency_retry_cooldown_expired_total": self._dependency_retry_cooldown_expired_total,
                        "cold_scanned_symbols_total": self._cold_scanned_symbols_total,
                        "cold_evaluated_timeframe_total": self._cold_evaluated_timeframe_total,
                        "cold_retryable_dependency_total": self._cold_retryable_dependency_total,
                        "cold_signal_total": self._cold_signal_total,
                        "cold_order_attempt_total": self._cold_order_attempt_total,
                        "closed_total": closed_total,
                        "closed_pnl_pct": closed_pnl_pct,
                        "aggtrade_requests": self._cycle_aggtrade_requests,
                        "aggtrade_network_calls": self._cycle_aggtrade_network_calls,
                        "aggtrade_cache_hits": self._cycle_aggtrade_cache_hits,
                        "aggtrade_process_cache_hits": self._cycle_aggtrade_process_cache_hits,
                        "aggtrade_coalesced_missing_ranges": self._cycle_aggtrade_coalesced_missing_ranges,
                        "aggtrade_rest_fetched_ms": self._cycle_aggtrade_rest_fetched_ms,
                        "aggtrade_gap_prefetch_symbols": self._cycle_aggtrade_gap_prefetch_symbols,
                        "aggtrade_gap_prefetch_requested_ranges": self._cycle_aggtrade_gap_prefetch_requested_ranges,
                        "aggtrade_gap_prefetch_missing_ranges": self._cycle_aggtrade_gap_prefetch_missing_ranges,
                        "aggtrade_gap_prefetch_backfill_ranges": self._cycle_aggtrade_gap_prefetch_backfill_ranges,
                        "aggtrade_gap_prefetch_rows": self._cycle_aggtrade_gap_prefetch_rows,
                        "aggtrade_gap_prefetch_pending": self._cycle_aggtrade_gap_prefetch_pending,
                        "ohlcv_cache_filled_reads": self._cycle_ohlcv_cache_filled_reads,
                        "ohlcv_cache_fetched_rows": self._cycle_ohlcv_cache_fetched_rows,
                        "ohlcv_cache_gap_reads": self._cycle_ohlcv_cache_gap_reads,
                        "ohlcv_cache_remaining_gap_count": self._cycle_ohlcv_cache_remaining_gap_count,
                        "ws_aggtrade_backfill_reads": self._cycle_ws_aggtrade_backfill_reads,
                        "ws_aggtrade_backfilled_rows": self._cycle_ws_aggtrade_backfilled_rows,
                        "ws_aggtrade_not_connected_backfill_reads": self._cycle_ws_aggtrade_not_connected_backfill_reads,
                        "ws_aggtrade_coverage_pending_count": self._cycle_ws_aggtrade_coverage_pending,
                        "ws_aggtrade_effective_source": self._ws_aggtrade_effective_source_for_cycle(ws_aggtrade_stats),
                        "precise_scan_symbols": self._cycle_precise_scan_symbols,
                        "inactive_visit_symbols": self._cycle_inactive_visit_symbols,
                        "deferred_inactive_subminute_pairs": self._cycle_deferred_inactive_subminute_pairs,
                        "inactive_cold_coverage_danger": (
                            self._current_inactive_scan_slots > 0
                            and self._current_inactive_scan_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE
                        ),
                        "inactive_cold_coverage_gate_reason": self._current_inactive_cold_coverage_gate_reason,
                        "inactive_cold_coverage_health_pct_at_selection": round(
                            self._current_inactive_cold_coverage_health_pct, 3
                        ),
                        "inactive_cold_coverage_health_threshold_pct": round(
                            self._current_inactive_cold_coverage_health_threshold_pct, 3
                        ),
                        "inactive_cold_coverage_adaptive_score": round(
                            self._current_inactive_cold_coverage_adaptive_score, 4
                        ),
                        "inactive_cold_coverage_health_factor": round(
                            self._current_inactive_cold_coverage_health_factor, 4
                        ),
                        "inactive_cold_coverage_speed_factor": round(
                            self._current_inactive_cold_coverage_speed_factor, 4
                        ),
                        "inactive_cold_coverage_active_factor": round(
                            self._current_inactive_cold_coverage_active_factor, 4
                        ),
                        "inactive_cold_coverage_load_factor": round(
                            self._current_inactive_cold_coverage_load_factor, 4
                        ),
                        "inactive_cold_coverage_pressure_ewma": round(
                            self._current_inactive_cold_coverage_pressure_ewma, 4
                        ),
                        "inactive_cold_coverage_cycle_seconds_ewma": round(
                            self._current_inactive_cold_coverage_cycle_seconds_ewma, 3
                        ),
                        "orphan_orders_cancelled": orphan_cancelled,
                    },
                )
                should_log_status = self._status_logger.inline_status_enabled or (
                    cycle == 1
                    or cycle % 10 == 0
                    or opened_total != opened_before
                    or closed_total != closed_before
                    or orphan_total != orphan_before
                )
                if should_log_status:
                    connection_text = self._live_connection_status_text(
                        ticker_stats=ticker_stats,
                        aggtrade_stats=ws_aggtrade_stats,
                        ws_healthy=ws_healthy,
                        ws_health_reason=ws_health_reason,
                    )
                    cold_status_text = "off"
                    cold_age_text = "-"
                    guard_text = "ok"
                    if self._current_inactive_scan_slots > 0 and self._inactive_slots_are_precise_cold_coverage(
                        self._current_inactive_scan_slots_source
                    ):
                        cold_status_text = (
                            f"DANGER{self._current_inactive_scan_slots}"
                            if self._current_inactive_scan_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE
                            else f"on{self._current_inactive_scan_slots}"
                        )
                        cold_age_text = f"~{full_cycle_seconds:.0f}s"
                        guard_text = f"sc{self._current_inactive_cold_coverage_adaptive_score:.2f}"
                    elif self._current_inactive_cold_coverage_gate_reason:
                        cold_status_text = "off"
                        cold_age_text = "-"
                        guard_text = self._current_inactive_cold_coverage_gate_reason
                    self._status_logger.status(
                        _format_live_heartbeat(
                            runtime_seconds=(
                                time.monotonic()
                                - (self._live_loop_started_monotonic or self._run_started_monotonic)
                            ),
                            cycle_seconds=cycle_seconds,
                            connection_health_pct=ws_health_pct,
                            anomalies_total=detected_anomalies_total,
                            active_now=active_symbol_count,
                            active_seen=active_symbols_seen_total,
                            open_positions=open_positions,
                            closed_positions=closed_total,
                            pnl_pct=closed_pnl_pct,
                            connection_text=connection_text,
                            data_status_text=data_status_text,
                            delayed_replay_enabled=bool(self.config.delayed_replay_enabled),
                            delayed_replay_pending=self._delayed_replay_queue_size(),
                            delayed_replay_total=self._delayed_replay_enqueued_total,
                            idle=(
                                active_symbol_count == 0
                                and open_positions == 0
                                and self._opening_symbol_count() == 0
                            ),
                            order_delta=-int(orphan_cancelled) if orphan_cancelled else 0,
                            real_orders=bool(self.config.confirm_real_orders),
                            cold_status=cold_status_text,
                            cold_age=cold_age_text,
                            guard_status=guard_text,
                            session_top_snapshot=session_top_snapshot,
                        ),
                        highlight=open_positions > 0,
                    )
                if self._network_degraded:
                    self._record_network_recovered(cycle=cycle, cycle_seconds=cycle_seconds)
                self._process_delayed_replay_if_idle(
                    cycle=cycle,
                    active_symbol_count=active_symbol_count,
                    open_positions=open_positions,
                )
                self._maybe_reprepare_symbol_context_if_safe(symbols, cycle=cycle)
                time.sleep(self.config.scan_sleep_seconds)
            except KeyboardInterrupt:
                reconcile_symbols = self._start_graceful_shutdown(reason="keyboard_interrupt", cycle=cycle, symbols=symbols)
                try:
                    orphan_cancelled = self._reconcile_orphan_orders(reconcile_symbols, cycle=cycle, force=True)
                    self._flush_live_ohlcv_cache_if_due(force=True, reason="keyboard_interrupt")
                except KeyboardInterrupt:
                    self.artifacts.append_event(
                        "live_shutdown_forced",
                        "__live__",
                        {"reason": "second_keyboard_interrupt", "cycle": cycle},
                    )
                    self.logger("graceful shutdown прерван повторным Ctrl+C")
                    self._close_live_sources()
                    return 130
                suffix = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
                self.logger(f"остановлено пользователем{suffix}")
                self._close_live_sources()
                return 0
            except LiveOrderPositionIntegrityError as exc:
                self._flush_live_ohlcv_cache_if_due(force=True, reason="order_position_integrity_error")
                error_symbol = exc.symbol
                event_symbol = error_symbol or "__live__"
                continue_enabled = bool(self.config.danger_continue_after_order_position_errors)
                self.artifacts.append_event(
                    "live_order_position_integrity_error",
                    event_symbol,
                    {
                        "cycle": cycle,
                        "symbol": error_symbol or "",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:1000],
                        "continue_after_error": continue_enabled,
                    },
                )
                symbol_prefix = f"{error_symbol}: " if error_symbol else ""
                if continue_enabled:
                    self.logger(f"продолжаю после ошибки ордера/позиции: {symbol_prefix}{exc}")
                    self.telegram.send_critical_sync(
                        channel="events",
                        key=f"live_order_position_integrity_error:{cycle}:{event_symbol}",
                        symbol=event_symbol,
                        text=_format_live_order_position_integrity_message(
                            symbol=error_symbol,
                            reason=str(exc),
                            continue_enabled=True,
                        ),
                    )
                    continue
                self.logger(f"остановлено из-за ошибки ордера/позиции: {symbol_prefix}{exc}")
                self.telegram.send_critical_sync(
                    channel="events",
                    key="live_order_position_integrity_error",
                    symbol=event_symbol,
                    text=_format_live_order_position_integrity_message(
                        symbol=error_symbol,
                        reason=str(exc),
                        continue_enabled=False,
                    ),
                )
                self._close_live_sources()
                return 3
            except LiveDataIntegrityError as exc:
                self._flush_live_ohlcv_cache_if_due(force=True, reason="data_integrity_error")
                error_symbol = exc.symbol
                event_symbol = error_symbol or "__live__"
                self.artifacts.append_event(
                    "live_data_integrity_error",
                    event_symbol,
                    {
                        "cycle": cycle,
                        "symbol": error_symbol or "",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:1000],
                    },
                )
                symbol_prefix = f"{error_symbol}: " if error_symbol else ""
                self.logger(f"остановлено из-за ошибки целостности live-данных: {symbol_prefix}{exc}")
                self.telegram.send_critical_sync(
                    channel="events",
                    key="live_data_integrity_error",
                    symbol=event_symbol,
                    text=_format_live_data_integrity_halt_message(symbol=error_symbol, reason=str(exc)),
                )
                self._close_live_sources()
                return 3
            except ExchangeConnectivityError as exc:
                self._record_network_degraded(cycle=cycle, exc=exc)
                time.sleep(self.config.network_sleep_seconds)
                self._record_ws_health_sample(healthy=False)
            except Exception as exc:
                self.logger(f"остановлено из-за внутренней ошибки: {type(exc).__name__}: {exc}")
                self._flush_live_ohlcv_cache_if_due(force=True, reason="internal_error")
                self.artifacts.append_event(
                    "live_internal_error",
                    "__live__",
                    {"exception_type": type(exc).__name__, "exception_message": str(exc)[:1000]},
                )
                self.telegram.send_critical_sync(
                    channel="events",
                    key="live_internal_error",
                    text=f"{SERVICE_WARNING_EMOJI} <b>Ошибка</b>\n\n{_telegram_code(type(exc).__name__ + ': ' + str(exc)[:500])}",
                )
                self._close_live_sources()
                return 4
        reconcile_symbols = self._forced_orphan_reconcile_symbols(symbols)
        orphan_cancelled = self._reconcile_orphan_orders(reconcile_symbols, cycle=cycle, force=True)
        self._flush_live_ohlcv_cache_if_due(force=True, reason="max_cycles")
        suffix = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
        self.logger(f"достигнут лимит циклов{suffix}")
        self._close_live_sources()
        return 0

    def _record_network_degraded(self, *, cycle: int, exc: ExchangeConnectivityError) -> None:
        reason = str(exc)
        if not self._network_degraded:
            self._network_degraded_first_reason = reason
            self._status_logger.alert(f"⚠️ сеть/API недоступны, жду восстановления.\nПричина: {reason}")
            self.artifacts.append_event(
                "network_degraded",
                "__live__",
                {
                    "cycle": cycle,
                    "sleep_seconds": self.config.network_sleep_seconds,
                    "exception_type": type(exc).__name__,
                    "exception_message": reason[:1000],
                },
            )
        self._enqueue_network_degraded_telegram_alert(cycle=cycle, reason=reason)
        self._network_degraded = True

    def _enqueue_network_degraded_telegram_alert(self, *, cycle: int, reason: str) -> None:
        now = time.monotonic()
        if (
            self._network_degraded_telegram_last_enqueue_at > 0.0
            and now - self._network_degraded_telegram_last_enqueue_at < NETWORK_DEGRADED_TELEGRAM_RETRY_SECONDS
        ):
            return
        self._network_degraded_telegram_last_enqueue_at = now
        self.artifacts.append_event(
            "network_degraded_telegram_alert_enqueued",
            "__live__",
            {"cycle": cycle, "retry_after_seconds": NETWORK_DEGRADED_TELEGRAM_RETRY_SECONDS},
        )
        self.telegram.send(
            channel="events",
            key="network_degraded",
            text=f"{SERVICE_WARNING_EMOJI} <b>Сеть/API недоступны</b>\n\n{_telegram_code(reason[:600])}",
        )

    def _record_network_recovered(self, *, cycle: int, cycle_seconds: float) -> None:
        first_reason = self._network_degraded_first_reason
        self.artifacts.append_event(
            "network_recovered",
            "__live__",
            {"cycle": cycle, "cycle_seconds": cycle_seconds, "first_degraded_reason": first_reason[:1000]},
        )
        self.telegram.send(
            channel="events",
            key="network_recovered",
            text=(
                f"✅ <b>Сеть/API восстановлены</b>\n\n"
                f"Предыдущая причина: {_telegram_code(first_reason[:500] or 'unknown')}"
            ),
        )
        self._network_degraded = False
        self._network_degraded_first_reason = ""
        self._network_degraded_telegram_last_enqueue_at = 0.0

    def _track_order_reconcile_symbol(self, symbol: str, *, reason: str) -> None:
        symbol_key = _position_symbol_key(symbol)
        with self._state_lock:
            already_tracked = symbol_key in self._order_reconcile_symbols_by_key
            self._order_reconcile_symbols_by_key[symbol_key] = symbol
        if not already_tracked:
            self.artifacts.append_event(
                "order_reconcile_symbol_tracked",
                symbol,
                {"symbol_key": symbol_key, "reason": reason},
            )

    def _forced_orphan_reconcile_symbols(self, symbols: list[str]) -> list[str]:
        by_key: dict[str, str] = {}
        with self._state_lock:
            by_key.update(self._order_reconcile_symbols_by_key)
            for position in self._open_positions.values():
                by_key.setdefault(_position_symbol_key(position.signal.symbol), position.signal.symbol)
            for active in self._active_symbols.values():
                if active.reason in {"opening_position", "position_already_active"}:
                    by_key.setdefault(_position_symbol_key(active.symbol), active.symbol)
        if not by_key:
            return []
        ordered: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            symbol_key = _position_symbol_key(symbol)
            if symbol_key in by_key and symbol_key not in seen:
                ordered.append(symbol)
                seen.add(symbol_key)
        for symbol_key, symbol in by_key.items():
            if symbol_key not in seen:
                ordered.append(symbol)
                seen.add(symbol_key)
        return ordered

    def _start_graceful_shutdown(self, *, reason: str, cycle: int, symbols: list[str]) -> list[str]:
        reconcile_symbols = self._forced_orphan_reconcile_symbols(symbols)
        self.logger(
            f"получен сигнал остановки · graceful shutdown · "
            f"reason={reason} · reconcile_symbols={len(reconcile_symbols)}"
        )
        self.artifacts.append_event(
            "live_shutdown_started",
            "__live__",
            {
                "reason": reason,
                "cycle": cycle,
                "orphan_reconcile_scope": "run_trade_symbols_only",
                "orphan_reconcile_symbols": len(reconcile_symbols),
                "tracked_trade_symbols": len(self._order_reconcile_symbols_by_key),
                "open_positions": len(self._open_positions),
                "opening_symbols": len(self._opening_symbols),
            },
        )
        return reconcile_symbols

    def _close_live_sources(self) -> None:
        if self._live_sources_closed:
            return
        self._live_sources_closed = True
        for source in (self.ticker_snapshot_source, self.aggtrade_source):
            close = getattr(source, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    self.artifacts.append_event(
                        "live_source_close_failed",
                        "__live__",
                        {"source": getattr(source, "source_id", type(source).__name__), "reason": f"{type(exc).__name__}: {exc}"},
                    )

    def _ticker_radar_required_for_subminute_gate(self) -> bool:
        return bool(self.config.ticker_radar_enabled and _live_config_has_subminute_entry_pairs(self.config))

    def _fetch_ticker_radar_snapshots(
        self,
        symbols: list[str],
        *,
        stage: str,
    ) -> tuple[list[ExchangeTickerSnapshot], str, str, str]:
        primary_source = self.ticker_snapshot_source
        try:
            snapshots = primary_source.fetch_snapshots(tuple(symbols))
            source_status = "primary"
            source_reason = ""
            status_provider = getattr(primary_source, "source_status", None)
            if callable(status_provider):
                source_status, source_reason = status_provider()
            return snapshots, primary_source.source_id, source_status, source_reason
        except Exception as primary_exc:
            if primary_source.source_id == "rest_fetch_tickers":
                raise
            self.artifacts.append_event(
                "ticker_radar_primary_source_failed",
                "__live__",
                {
                    "stage": stage,
                    "primary_source": primary_source.source_id,
                    "exception_type": type(primary_exc).__name__,
                    "exception_message": str(primary_exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "try_explicit_rest_ticker_radar_after_primary_ws_failure",
                },
            )
            fallback_source = RestLiveTickerSnapshotSource(self.exchange)
            try:
                snapshots = fallback_source.fetch_snapshots(tuple(symbols))
            except Exception as fallback_exc:
                self.artifacts.append_event(
                    "ticker_radar_fallback_source_failed",
                    "__live__",
                    {
                        "stage": stage,
                        "primary_source": primary_source.source_id,
                        "fallback_source": fallback_source.source_id,
                        "primary_exception_type": type(primary_exc).__name__,
                        "primary_exception_message": str(primary_exc)[:500],
                        "fallback_exception_type": type(fallback_exc).__name__,
                        "fallback_exception_message": str(fallback_exc)[:500],
                        "symbols_total": len(symbols),
                        "policy": "refuse_without_any_working_ticker_radar_source",
                    },
                )
                raise ExchangeConnectivityError(
                    "ticker radar primary source failed and explicit REST ticker fallback also failed; "
                    f"primary_source={primary_source.source_id}; primary_error={type(primary_exc).__name__}: {primary_exc}; "
                    f"fallback_source={fallback_source.source_id}; fallback_error={type(fallback_exc).__name__}: {fallback_exc}"
                ) from fallback_exc
            missing_count = sum(1 for snapshot in snapshots if snapshot.status != "ok")
            self.artifacts.append_event(
                "ticker_radar_source_degraded",
                "__live__",
                {
                    "stage": stage,
                    "primary_source": primary_source.source_id,
                    "fallback_source": fallback_source.source_id,
                    "primary_exception_type": type(primary_exc).__name__,
                    "primary_exception_message": str(primary_exc)[:500],
                    "symbols_total": len(snapshots),
                    "ok_count": len(snapshots) - missing_count,
                    "missing_count": missing_count,
                    "policy": "explicit_rest_ticker_radar_fallback_no_ohlcv_or_aggtrade_full_scan",
                },
            )
            return (
                snapshots,
                fallback_source.source_id,
                "degraded_rest_fallback",
                f"primary {primary_source.source_id} failed: {type(primary_exc).__name__}: {str(primary_exc)[:240]}",
            )

    def _seed_startup_ticker_radar(self, symbols: list[str]) -> None:
        if not (
            self.config.live_ws_ticker_startup_seed_enabled
            and self._ticker_radar_required_for_subminute_gate()
        ):
            return
        seed_consumer = getattr(self.ticker_snapshot_source, "seed_from_snapshots", None)
        if not callable(seed_consumer):
            return
        seed_source = RestLiveTickerSnapshotSource(self.exchange)
        self._startup_status("тикеры", f"стартовый снимок · {len(symbols)} символов")
        try:
            snapshots = seed_source.fetch_snapshots(tuple(symbols))
            seed_result = seed_consumer(snapshots)
        except Exception as exc:
            self.artifacts.append_event(
                "ticker_radar_startup_seed_failed",
                "__live__",
                {
                    "seed_source": seed_source.source_id,
                    "primary_source": self.ticker_snapshot_source.source_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "continue_with_primary_ws_or_explicit_degraded_fallback",
                },
            )
            self._status_logger.finish_status()
            return
        self._status_logger.finish_status()
        self.artifacts.append_event(
            "ticker_radar_startup_seeded",
            "__live__",
            {
                "seed_source": seed_source.source_id,
                "primary_source": self.ticker_snapshot_source.source_id,
                "symbols_total": len(symbols),
                "seeded_count": seed_result.get("seeded_count", 0),
                "ok_count": seed_result.get("ok_count", 0),
                "missing_count": seed_result.get("missing_count", 0),
                "seeded_at_ms": seed_result.get("seeded_at_ms", ""),
                "policy": "one_rest_snapshot_initializes_ws_ticker_cache_only",
            },
        )

    def _validate_required_ticker_radar_source(self, symbols: list[str]) -> None:
        if not self._ticker_radar_required_for_subminute_gate():
            return
        self._startup_status("тикеры", f"проверка радара · {len(symbols)} символов")
        try:
            snapshots, source, source_status, reason = self._fetch_ticker_radar_snapshots(symbols, stage="startup")
        except ExchangeConnectivityError as exc:
            self.artifacts.append_event(
                "ticker_radar_startup_failed",
                "__live__",
                {
                    "source": self.ticker_snapshot_source.source_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "startup_refuse_without_any_working_ticker_radar_source",
                },
            )
            self._status_logger.finish_status()
            raise LiveStartupError(
                "subminute live discovery requires a working ticker radar source before the first cycle; "
                f"error={type(exc).__name__}: {exc}"
            ) from exc
        except Exception as exc:
            self.artifacts.append_event(
                "ticker_radar_startup_failed",
                "__live__",
                {
                    "source": self.ticker_snapshot_source.source_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "startup_refuse_without_required_ticker_radar",
                },
            )
            self._status_logger.finish_status()
            raise LiveStartupError(
                "subminute live discovery requires a working ticker radar source before the first cycle; "
                f"source={self.ticker_snapshot_source.source_id}; error={type(exc).__name__}: {exc}"
            ) from exc
        now_ms = int(time.time() * 1000)
        missing_count = sum(1 for snapshot in snapshots if snapshot.status != "ok")
        self._session_top_tracker.update_from_ticker_snapshots(
            snapshots,
            now_ms=now_ms,
            source=source,
            source_status=source_status,
            source_reason=reason,
        )
        self._write_session_top_growth_artifact_if_due(now_ms=now_ms)
        if snapshots and missing_count == len(snapshots):
            self.artifacts.append_event(
                "ticker_radar_startup_failed",
                "__live__",
                {
                    "source": source,
                    "source_status": source_status,
                    "symbols_total": len(snapshots),
                    "missing_count": missing_count,
                    "policy": "startup_refuse_without_valid_ticker_radar_payload",
                    "reason": "all_ticker_snapshots_missing",
                },
            )
            self._status_logger.finish_status()
            raise LiveStartupError(
                "subminute live discovery requires ticker radar snapshots with price and quote_volume; "
                f"source={source}; all {len(snapshots)} snapshots are missing"
            )
        self.artifacts.append_event(
            "ticker_radar_startup_ready",
            "__live__",
            {
                "source": source,
                "source_status": source_status,
                "source_reason": reason,
                "symbols_total": len(snapshots),
                "ok_count": len(snapshots) - missing_count,
                "missing_count": missing_count,
                "policy": "required_for_inactive_subminute_gate",
            },
        )
        self._status_logger.finish_status()

    def _filter_live_symbol_universe(self, symbols: list[str], *, explicit_symbols: bool) -> list[str]:
        if explicit_symbols or not self.config.exclude_default_high_cap_symbols:
            self.artifacts.append_event(
                "live_symbol_universe_filter",
                "__live__",
                {
                    "source": "explicit_symbols" if explicit_symbols else "exchange_usdt_swap_symbols",
                    "input_count": len(symbols),
                    "output_count": len(symbols),
                    "excluded_count": 0,
                    "excluded_symbols": [],
                    "excluded_bases": [],
                    "reason": "disabled_for_explicit_symbols" if explicit_symbols else "high_cap_filter_disabled",
                },
            )
            return symbols
        kept: list[str] = []
        excluded: list[str] = []
        for symbol in symbols:
            base = _compact_symbol(symbol)
            if base in LIVE_DEFAULT_EXCLUDED_HIGH_CAP_BASES:
                excluded.append(symbol)
            else:
                kept.append(symbol)
        self.artifacts.append_event(
            "live_symbol_universe_filter",
            "__live__",
            {
                "source": "exchange_usdt_swap_symbols",
                "input_count": len(symbols),
                "output_count": len(kept),
                "excluded_count": len(excluded),
                "excluded_symbols": excluded,
                "excluded_bases": sorted({_compact_symbol(symbol) for symbol in excluded}),
                "reason": "static_high_cap_exclusion",
            },
        )
        return kept

    def _validate_startup(self) -> None:
        if not self.config.confirm_real_orders:
            raise LiveStartupError("Для micro-live нужен явный флаг --confirm-real-orders")
        _validate_live_config_values(self.config)
        missing = []
        if not os.getenv("BINANCE_API_KEY"):
            missing.append("BINANCE_API_KEY")
        if not os.getenv("BINANCE_SECRET_KEY"):
            missing.append("BINANCE_SECRET_KEY")
        if missing:
            raise LiveStartupError(f"Не заполнены env переменные: {', '.join(missing)}")
        if not self._pump_categories:
            raise LiveStartupError("Не заданы live pump categories")
        try:
            balance = float(self.exchange.fetch_usdt_free_balance())
        except (TypeError, ValueError) as exc:
            raise LiveStartupError("Биржа вернула нечисловой free USDT balance") from exc
        if not math.isfinite(balance):
            raise LiveStartupError("Биржа вернула нечисловой free USDT balance")

    def _validate_live_account_mode(self) -> None:
        try:
            preflight = self.exchange.fetch_live_account_preflight()
        except Exception as exc:
            self.artifacts.append_event(
                "live_account_preflight_failed",
                "__live__",
                {"reason": f"{type(exc).__name__}: {exc}"},
            )
            raise LiveStartupError("Live account-mode preflight failed before real-order startup") from exc
        if preflight.hedge_mode_enabled:
            self.artifacts.append_event(
                "live_account_preflight_failed",
                "__live__",
                {
                    "exchange": preflight.exchange,
                    "position_mode": preflight.position_mode,
                    "hedge_mode_enabled": preflight.hedge_mode_enabled,
                    "required_position_mode": "one_way",
                    "reason": "hedge_mode_not_supported_by_live_execution_contract",
                },
            )
            raise LiveStartupError("Binance account is in hedge mode; live runner requires one-way position mode")
        self.artifacts.append_event(
            "live_account_preflight_ok",
            "__live__",
            {
                "exchange": preflight.exchange,
                "position_mode": preflight.position_mode,
                "hedge_mode_enabled": preflight.hedge_mode_enabled,
                "required_position_mode": "one_way",
                "policy": "fail_fast_before_any_real_order",
            },
        )

    def _close_startup_exchange_positions(self, symbols: list[str]) -> None:
        try:
            snapshots = self.exchange.fetch_position_snapshots(tuple(symbols))
        except Exception as exc:
            reason = f"Startup exchange-position cleanup failed before live loop: {type(exc).__name__}: {exc}"
            self.artifacts.append_event(
                "startup_position_cleanup_failed",
                "__live__",
                {"stage": "fetch_positions", "reason": f"{type(exc).__name__}: {exc}"},
            )
            if self.config.danger_continue_after_order_position_errors:
                self.artifacts.append_event(
                    "live_order_position_integrity_error",
                    "__live__",
                    {
                        "stage": "startup_fetch_positions",
                        "symbol": "",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:1000],
                        "continue_after_error": True,
                    },
                )
                self.logger(f"продолжаю после startup ошибки позиций: {reason}")
                self.telegram.send_critical_sync(
                    channel="events",
                    key="startup_order_position_integrity_error:fetch_positions",
                    text=_format_live_order_position_integrity_message(
                        symbol=None,
                        reason=reason,
                        continue_enabled=True,
                    ),
                )
                return
            raise LiveStartupError("Startup exchange-position cleanup failed before live loop") from exc
        nonzero_snapshots = [snapshot for snapshot in snapshots if abs(float(snapshot.signed_amount)) > 0.0]
        self.artifacts.append_event(
            "startup_position_cleanup_started",
            "__live__",
            {
                "symbols_checked": len(symbols),
                "position_rows": len(snapshots),
                "nonzero_positions": len(nonzero_snapshots),
                "policy": "reduce_only_close_all_existing_exchange_positions_before_live_loop",
            },
        )
        closed = 0
        failed = 0
        for snapshot in nonzero_snapshots:
            symbol = snapshot.symbol
            signed_amount = float(snapshot.signed_amount)
            try:
                self._close_startup_exchange_position(
                    symbol,
                    signed_amount=signed_amount,
                    reason="startup_existing_exchange_position",
                )
                closed += 1
            except Exception as exc:
                failed += 1
                self.artifacts.append_event(
                    "startup_position_close_failed",
                    symbol,
                    {
                        "exchange_position_amount": signed_amount,
                        "exchange_position_side": snapshot.side,
                        "source": snapshot.source,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
                if self.config.danger_continue_after_order_position_errors:
                    reason = (
                        f"Startup exchange position could not be closed: symbol={symbol} "
                        f"amount={signed_amount} {type(exc).__name__}: {exc}"
                    )
                    self.artifacts.append_event(
                        "live_order_position_integrity_error",
                        symbol,
                        {
                            "stage": "startup_close_position",
                            "symbol": symbol,
                            "exchange_position_amount": signed_amount,
                            "exchange_position_side": snapshot.side,
                            "source": snapshot.source,
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc)[:1000],
                            "continue_after_error": True,
                        },
                    )
                    self.logger(f"продолжаю после startup ошибки позиции: {symbol}: {reason}")
                    self.telegram.send_critical_sync(
                        channel="events",
                        key=f"startup_order_position_integrity_error:{symbol}",
                        symbol=symbol,
                        text=_format_live_order_position_integrity_message(
                            symbol=symbol,
                            reason=reason,
                            continue_enabled=True,
                        ),
                    )
                    continue
                raise LiveStartupError(
                    f"Startup exchange position could not be closed: symbol={symbol} amount={signed_amount}"
                ) from exc
        self.artifacts.append_event(
            "startup_position_cleanup_finished",
            "__live__",
            {"closed": closed, "failed": failed},
        )

    def _close_startup_exchange_position(
        self,
        symbol: str,
        *,
        signed_amount: float,
        reason: str,
    ) -> None:
        amount = abs(float(signed_amount))
        if not math.isfinite(amount) or amount <= 0.0:
            raise LiveDataIntegrityError(f"invalid startup exchange position amount: symbol={symbol} amount={signed_amount}")
        side = "sell" if signed_amount > 0.0 else "buy"
        client_order_id = _live_client_order_id("startup", symbol, reason, int(time.time() * 1000))
        fill = self.exchange.create_market_order_with_fill(
            symbol,
            side,
            amount,
            reduce_only=True,
            client_order_id=client_order_id,
        )
        self._track_order_reconcile_symbol(symbol, reason=reason)
        post_amount = float(self.exchange.fetch_symbol_position_amount(symbol))
        if not math.isfinite(post_amount) or abs(post_amount) > max(amount * self.config.max_position_amount_slippage_ratio, 1e-12):
            self.artifacts.append_event(
                "startup_position_close_unverified",
                symbol,
                {
                    "reason": reason,
                    "initial_exchange_position_amount": signed_amount,
                    "post_exchange_position_amount": _finite_or_none(post_amount),
                    "client_order_id": client_order_id,
                    "order_id": fill.order_id,
                },
            )
            raise LiveDataIntegrityError(
                f"startup exchange position close not verified: symbol={symbol} post_amount={post_amount}"
            )
        cancelled_orders = self._cancel_orphan_orders_for_symbol(symbol, symbol_key=_position_symbol_key(symbol))
        self.artifacts.append_event(
            "startup_position_closed",
            symbol,
            {
                "reason": reason,
                "initial_exchange_position_amount": signed_amount,
                "close_side": side,
                "close_amount": amount,
                "client_order_id": client_order_id,
                "order_id": fill.order_id,
                "status": fill.status,
                "fill_timestamp_ms": fill.timestamp_ms,
                "fill_price": fill.average_price,
                "filled_amount": fill.filled_amount,
                "post_exchange_position_amount": post_amount,
                "orphan_orders_cancelled": cancelled_orders,
            },
        )

    def _reset_cycle_fetch_state(self) -> None:
        self._current_cycle_aggtrade_cache = {}
        self._cycle_aggtrade_requests = 0
        self._cycle_aggtrade_network_calls = 0
        self._cycle_aggtrade_cache_hits = 0
        self._cycle_aggtrade_process_cache_hits = 0
        self._cycle_aggtrade_coalesced_missing_ranges = 0
        self._cycle_aggtrade_rest_fetched_ms = 0
        self._cycle_ws_aggtrade_backfill_reads = 0
        self._cycle_ws_aggtrade_backfilled_rows = 0
        self._cycle_ws_aggtrade_coverage_pending = 0
        self._cycle_ws_aggtrade_not_connected_backfill_reads = 0
        self._cycle_aggtrade_gap_prefetch_symbols = 0
        self._cycle_aggtrade_gap_prefetch_requested_ranges = 0
        self._cycle_aggtrade_gap_prefetch_missing_ranges = 0
        self._cycle_aggtrade_gap_prefetch_backfill_ranges = 0
        self._cycle_aggtrade_gap_prefetch_rows = 0
        self._cycle_aggtrade_gap_prefetch_pending = 0
        self._cycle_ohlcv_cache_filled_reads = 0
        self._cycle_ohlcv_cache_fetched_rows = 0
        self._cycle_ohlcv_cache_gap_reads = 0
        self._cycle_ohlcv_cache_remaining_gap_count = 0
        self._current_batch_symbol_scan_mode = {}
        self._cycle_precise_scan_symbols = 0
        self._cycle_inactive_visit_symbols = 0
        self._cycle_deferred_inactive_subminute_pairs = 0
        self._cycle_cold_scanned_symbols = 0
        self._cycle_cold_due_timeframe_count = 0
        self._cycle_cold_evaluated_timeframe_count = 0
        self._cycle_cold_retryable_dependency_count = 0
        self._cycle_cold_signal_count = 0
        self._cycle_cold_order_attempt_count = 0
        self._cycle_dependency_retry_cooldown_skipped = 0
        self._cycle_dependency_retry_cooldown_expired = 0
        self._cycle_dependency_retry_cooldown_skip_keys = set()

    def _full_symbol_cycle_seconds(self, *, batch_seconds: float, batch_size: int, symbols_total: int) -> float:
        if self._last_symbol_universe_cycle_seconds is not None:
            return self._last_symbol_universe_cycle_seconds
        if batch_size <= 0 or symbols_total <= 0:
            return float(batch_seconds)
        batches_per_universe = math.ceil(symbols_total / max(1, batch_size))
        return float(batch_seconds) * float(max(1, batches_per_universe))

    def _active_live_symbol_count(self) -> int:
        with self._state_lock:
            active_keys = set(self._active_symbols)
            active_keys.update(self._opening_symbols)
            active_keys.update(_position_symbol_key(position.signal.symbol) for position in self._open_positions.values())
            return len(active_keys)

    def _active_live_symbols_seen_total(self) -> int:
        with self._state_lock:
            return len(self._active_symbols_seen)

    def _closed_pnl_pct_total(self) -> float:
        pnl_total = float(self._closed_pnl_usdt_total)
        notional_total = float(self._closed_notional_usdt_total)
        if not math.isfinite(pnl_total) or not math.isfinite(notional_total):
            raise LiveDataIntegrityError(
                "Non-finite local closed PnL counters: "
                f"pnl_usdt={self._closed_pnl_usdt_total!r} "
                f"notional_usdt={self._closed_notional_usdt_total!r}"
            )
        if abs(notional_total) <= 1e-12:
            return 0.0
        return pnl_total / notional_total

    def _opening_symbol_count(self) -> int:
        with self._state_lock:
            return len(self._opening_symbols)

    def _delayed_replay_queue_size(self) -> int:
        with self._state_lock:
            return len(self._delayed_replay_pending)

    def _delayed_replay_snapshot_key(
        self,
        *,
        symbol: str,
        levels_tf: str,
        entry_tf: str,
        decision_timestamp_ms: int,
    ) -> tuple[str, str, str, int]:
        return (
            _position_symbol_key(symbol),
            str(levels_tf),
            str(entry_tf),
            int(decision_timestamp_ms),
        )

    def _remember_delayed_replay_snapshot(
        self,
        *,
        symbol: str,
        levels_tf: str,
        entry_tf: str,
        decision_timestamp_ms: int,
        decision_snapshot_json: str,
    ) -> None:
        if not decision_snapshot_json:
            return
        key = self._delayed_replay_snapshot_key(
            symbol=symbol,
            levels_tf=levels_tf,
            entry_tf=entry_tf,
            decision_timestamp_ms=decision_timestamp_ms,
        )
        with self._state_lock:
            self._delayed_replay_decision_snapshots[key] = decision_snapshot_json
            if len(self._delayed_replay_decision_snapshots) > max(1000, int(self.config.delayed_replay_max_queue_size) * 2):
                ordered = list(self._delayed_replay_decision_snapshots.items())[-int(self.config.delayed_replay_max_queue_size) :]
                self._delayed_replay_decision_snapshots = dict(ordered)

    def _lookup_delayed_replay_snapshot(
        self,
        *,
        symbol: str,
        levels_tf: str,
        entry_tf: str,
        decision_timestamp_ms: int,
    ) -> str:
        key = self._delayed_replay_snapshot_key(
            symbol=symbol,
            levels_tf=levels_tf,
            entry_tf=entry_tf,
            decision_timestamp_ms=decision_timestamp_ms,
        )
        with self._state_lock:
            value = self._delayed_replay_decision_snapshots.get(key)
        return value if isinstance(value, str) else ""

    def _build_delayed_replay_decision_snapshot(
        self,
        *,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        decision_timestamp_ms: int,
        setup_start_timestamp_ms: int,
        setup_history: pd.DataFrame,
        setup_row: pd.Series,
        entry_segment: pd.DataFrame,
        setup_source: str,
        setup_elapsed_fraction: float,
        setup_closed_entry_candles: int,
        mark_basis: LiveMarkBasisResult | None,
        oi_change: float | None,
        oi_status: str | None,
        prior_fast_fade_result: dict[str, object] | None,
    ) -> str:
        if not self.config.delayed_replay_enabled:
            return ""
        frozen_context = {
            "mark_close_vs_decision_close_basis": _optional_float(mark_basis.value) if mark_basis is not None else None,
            "mark_basis_status": mark_basis.reason if mark_basis is not None else "not_loaded",
            "mark_timestamp_ms": mark_basis.timestamp_ms if mark_basis is not None and mark_basis.timestamp_ms is not None else "",
            "mark_age_ms": mark_basis.age_ms if mark_basis is not None and mark_basis.age_ms is not None else "",
            "oi_change_pct_3x5m": _optional_float(oi_change),
            "oi_status": oi_status or ("ok" if oi_change is not None else "not_loaded"),
            "prior_fast_fade_result": prior_fast_fade_result or {},
        }
        payload = {
            "contract": DELAYED_REPLAY_DECISION_SNAPSHOT_CONTRACT,
            "source": "live_decision_in_memory",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "created_at_ms": int(time.time() * 1000),
            "symbol": symbol,
            "levels_tf": levels_timeframe.value,
            "entry_tf": entry_timeframe.value,
            "decision_timestamp_ms": int(decision_timestamp_ms),
            "setup_start_timestamp_ms": int(setup_start_timestamp_ms),
            "setup_source": setup_source,
            "setup_elapsed_fraction": float(setup_elapsed_fraction),
            "setup_closed_entry_candles": int(setup_closed_entry_candles),
            "baseline_candles": int(self.config.baseline_candles),
            "confirmation_candles": int(self.config.confirmation_candles),
            "baseline_rows": _delayed_replay_frame_to_rows(setup_history, max_rows=max(1, int(self.config.baseline_candles))),
            "setup_row": _delayed_replay_series_to_row(setup_row),
            "entry_rows": _delayed_replay_frame_to_rows(entry_segment, max_rows=max(1, len(entry_segment))),
            "frozen_context": _json_safe_payload(frozen_context),
        }
        try:
            return json.dumps(_json_safe_payload(payload), ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            return ""

    def _capture_delayed_replay_case(
        self,
        *,
        source_event: str,
        symbol: str,
        details: dict[str, object],
        priority: int,
        live_decision_class: str,
        live_reason: str,
    ) -> None:
        if not self.config.delayed_replay_enabled:
            return
        decision_timestamp_ms = _optional_int(details.get("decision_timestamp_ms"))
        if decision_timestamp_ms is None:
            return
        levels_tf = str(details.get("levels_tf") or "")
        entry_tf = str(details.get("entry_tf") or "")
        if not levels_tf or not entry_tf:
            return
        now_ms = int(time.time() * 1000)
        replay_not_before_ms = max(
            now_ms + int(float(self.config.delayed_replay_delay_seconds) * 1000),
            int(decision_timestamp_ms) + int(float(self.config.delayed_replay_delay_seconds) * 1000),
        )
        case_id = _delayed_replay_case_id(
            source_event=source_event,
            symbol=symbol,
            levels_tf=levels_tf,
            entry_tf=entry_tf,
            decision_timestamp_ms=int(decision_timestamp_ms),
            live_reason=live_reason,
            category_id=str(details.get("category_id") or ""),
        )
        signal_json = details.get("signal_json")
        decision_snapshot_json = details.get("decision_snapshot_json")
        if isinstance(decision_snapshot_json, str) and decision_snapshot_json:
            self._remember_delayed_replay_snapshot(
                symbol=symbol,
                levels_tf=levels_tf,
                entry_tf=entry_tf,
                decision_timestamp_ms=int(decision_timestamp_ms),
                decision_snapshot_json=decision_snapshot_json,
            )
        else:
            decision_snapshot_json = self._lookup_delayed_replay_snapshot(
                symbol=symbol,
                levels_tf=levels_tf,
                entry_tf=entry_tf,
                decision_timestamp_ms=int(decision_timestamp_ms),
            )
        details_for_case = dict(details)
        details_for_case.pop("decision_snapshot_json", None)
        case: dict[str, object] = {
            "case_id": case_id,
            "queued_at_utc": datetime.now(UTC).isoformat(),
            "queued_at_ms": now_ms,
            "replay_not_before_ms": replay_not_before_ms,
            "source_event": source_event,
            "symbol": symbol,
            "priority": int(priority),
            "live_decision_class": live_decision_class,
            "live_reason": live_reason,
            "levels_tf": levels_tf,
            "entry_tf": entry_tf,
            "decision_timestamp_ms": int(decision_timestamp_ms),
            "details": _json_safe_payload(details_for_case),
            "signal_json": signal_json if isinstance(signal_json, str) else "",
            "decision_snapshot_json": decision_snapshot_json if isinstance(decision_snapshot_json, str) else "",
            "delayed_replay_contract": DELAYED_REPLAY_CONTRACT,
        }
        with self._state_lock:
            if case_id in self._delayed_replay_pending or case_id in self._delayed_replay_completed:
                return
            if len(self._delayed_replay_pending) >= int(self.config.delayed_replay_max_queue_size):
                self._delayed_replay_skipped_total += 1
                self.artifacts.append_event(
                    "delayed_replay_queue_full",
                    symbol,
                    {
                        "case_id": case_id,
                        "source_event": source_event,
                        "priority": int(priority),
                        "max_queue_size": int(self.config.delayed_replay_max_queue_size),
                        "decision_timestamp_ms": int(decision_timestamp_ms),
                    },
                )
                return
            self._delayed_replay_pending[case_id] = case
            self._delayed_replay_enqueued_total += 1
        self.artifacts.append_delayed_replay_case(case)
        self.artifacts.append_event(
            "delayed_replay_case_queued",
            symbol,
            {
                "case_id": case_id,
                "source_event": source_event,
                "priority": int(priority),
                "live_decision_class": live_decision_class,
                "live_reason": live_reason,
                "levels_tf": levels_tf,
                "entry_tf": entry_tf,
                "decision_timestamp_ms": int(decision_timestamp_ms),
                "replay_not_before_ms": replay_not_before_ms,
                "queue_size": self._delayed_replay_queue_size(),
                "decision_snapshot_available": bool(decision_snapshot_json),
                "contract": DELAYED_REPLAY_CONTRACT,
            },
        )

    def _capture_delayed_replay_signal(
        self,
        signal: LiveSignal,
        *,
        source_scan_mode: str,
        decision_snapshot_json: str = "",
    ) -> None:
        if not self.config.delayed_replay_enabled:
            return
        details = {
            "category_id": signal.category_id,
            "category_label": signal.category_label,
            "category_priority": signal.category_priority,
            "levels_tf": signal.levels_timeframe.value,
            "entry_tf": signal.entry_timeframe.value,
            "decision_timestamp_ms": int(signal.decision_timestamp_ms),
            "start_timestamp_ms": int(signal.start_timestamp_ms),
            "signal_entry_price": _finite_or_none(signal.entry_price),
            "signal_stop_price": _finite_or_none(signal.stop_price),
            "signal_tp1_price": _finite_or_none(signal.tp1_price),
            "quote_ratio_start": _finite_or_none(signal.quote_ratio_start),
            "trade_ratio_start": _finite_or_none(signal.trade_ratio_start),
            "price_retention": _finite_or_none(signal.price_retention),
            "hold_count": int(signal.hold_count),
            "verticality_score": _finite_or_none(signal.verticality_score),
            "source_scan_mode": source_scan_mode,
            "signal_json": signal.to_json(),
            "decision_snapshot_json": decision_snapshot_json,
        }
        self._capture_delayed_replay_case(
            source_event="category_selected",
            symbol=signal.symbol,
            details=details,
            priority=1,
            live_decision_class="signal_selected",
            live_reason="category_selected",
        )

    def _capture_delayed_replay_execution_reject(
        self,
        signal: LiveSignal,
        *,
        event: str,
        details: dict[str, object],
    ) -> None:
        if not self.config.delayed_replay_enabled:
            return
        replay_details = {
            "category_id": signal.category_id,
            "category_label": signal.category_label,
            "category_priority": signal.category_priority,
            "levels_tf": signal.levels_timeframe.value,
            "entry_tf": signal.entry_timeframe.value,
            "decision_timestamp_ms": int(signal.decision_timestamp_ms),
            "start_timestamp_ms": int(signal.start_timestamp_ms),
            "signal_entry_price": _finite_or_none(signal.entry_price),
            "signal_stop_price": _finite_or_none(signal.stop_price),
            "signal_tp1_price": _finite_or_none(signal.tp1_price),
            "source_scan_mode": self._batch_scan_mode_for_symbol(signal.symbol),
            "signal_json": signal.to_json(),
            "decision_snapshot_json": self._lookup_delayed_replay_snapshot(
                symbol=signal.symbol,
                levels_tf=signal.levels_timeframe.value,
                entry_tf=signal.entry_timeframe.value,
                decision_timestamp_ms=int(signal.decision_timestamp_ms),
            ),
            **details,
        }
        self._capture_delayed_replay_case(
            source_event=event,
            symbol=signal.symbol,
            details=replay_details,
            priority=1,
            live_decision_class="execution_rejected",
            live_reason=event,
        )

    def _process_delayed_replay_if_idle(
        self,
        *,
        cycle: int,
        active_symbol_count: int,
        open_positions: int,
    ) -> None:
        if not self.config.delayed_replay_enabled:
            return
        now_ms = int(time.time() * 1000)
        opening_symbols = self._opening_symbol_count()
        idle = active_symbol_count == 0 and open_positions == 0 and opening_symbols == 0
        if idle:
            if self._idle_since_ms is None:
                self._idle_since_ms = now_ms
        else:
            self._idle_since_ms = None
        idle_since_ms = self._idle_since_ms or ""
        self.artifacts.write_live_status(
            {
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "timestamp_ms": now_ms,
                "cycle": int(cycle),
                "active_symbol_count": int(active_symbol_count),
                "open_positions": int(open_positions),
                "opening_symbols": int(opening_symbols),
                "idle": bool(idle),
                "idle_since_ms": idle_since_ms,
                "delayed_replay_enabled": True,
                "delayed_replay_pending_count": self._delayed_replay_queue_size(),
                "delayed_replay_enqueued_total": int(self._delayed_replay_enqueued_total),
                "delayed_replay_processed_total": int(self._delayed_replay_processed_total),
                "delayed_replay_skipped_total": int(self._delayed_replay_skipped_total),
                "contract": DELAYED_REPLAY_CONTRACT,
            }
        )
        min_idle_ms = int(float(self.config.delayed_replay_min_idle_seconds) * 1000)
        if not idle:
            self._append_delayed_replay_summary(
                cycle=cycle,
                status="skipped",
                reason="live_not_idle",
                active_symbol_count=active_symbol_count,
                open_positions=open_positions,
                opening_symbols=opening_symbols,
                idle_since_ms=idle_since_ms,
                processed_count=0,
                skipped_count=0,
                duration_seconds=0.0,
            )
            return
        if self._idle_since_ms is None or now_ms - int(self._idle_since_ms) < min_idle_ms:
            self._append_delayed_replay_summary(
                cycle=cycle,
                status="skipped",
                reason="idle_window_too_short",
                active_symbol_count=active_symbol_count,
                open_positions=open_positions,
                opening_symbols=opening_symbols,
                idle_since_ms=idle_since_ms,
                processed_count=0,
                skipped_count=0,
                duration_seconds=0.0,
            )
            return
        with self._state_lock:
            ready_cases = [
                case
                for case in self._delayed_replay_pending.values()
                if _optional_int(case.get("replay_not_before_ms")) is not None
                and int(case["replay_not_before_ms"]) <= now_ms
            ]
        ready_cases.sort(key=lambda case: (int(case.get("priority") or 99), int(case.get("queued_at_ms") or 0)))
        if not ready_cases:
            self._append_delayed_replay_summary(
                cycle=cycle,
                status="idle_no_ready_cases",
                reason="no_ready_cases",
                active_symbol_count=active_symbol_count,
                open_positions=open_positions,
                opening_symbols=opening_symbols,
                idle_since_ms=idle_since_ms,
                processed_count=0,
                skipped_count=0,
                duration_seconds=0.0,
            )
            return
        started = time.monotonic()
        processed = 0
        skipped = 0
        max_cases = int(self.config.delayed_replay_max_cases_per_cycle)
        max_seconds = float(self.config.delayed_replay_max_cycle_seconds)
        for case in ready_cases[:max_cases]:
            if time.monotonic() - started >= max_seconds:
                break
            case_id = str(case.get("case_id") or "")
            try:
                row = self._evaluate_delayed_replay_case(case, now_ms=now_ms)
            except Exception as exc:
                row = self._delayed_replay_error_row(case, now_ms=now_ms, exc=exc)
            self.artifacts.append_delayed_replay_result(row)
            with self._state_lock:
                self._delayed_replay_pending.pop(case_id, None)
                self._delayed_replay_completed.add(case_id)
                self._delayed_replay_processed_total += 1
            processed += 1
        if len(ready_cases) > processed:
            skipped = len(ready_cases) - processed
        self._append_delayed_replay_summary(
            cycle=cycle,
            status="processed" if processed else "budget_exhausted",
            reason="ok" if processed else "max_cycle_seconds_exhausted",
            active_symbol_count=active_symbol_count,
            open_positions=open_positions,
            opening_symbols=opening_symbols,
            idle_since_ms=idle_since_ms,
            processed_count=processed,
            skipped_count=skipped,
            duration_seconds=time.monotonic() - started,
        )

    def _append_delayed_replay_summary(
        self,
        *,
        cycle: int,
        status: str,
        reason: str,
        active_symbol_count: int,
        open_positions: int,
        opening_symbols: int,
        idle_since_ms: int | str,
        processed_count: int,
        skipped_count: int,
        duration_seconds: float,
    ) -> None:
        with self._state_lock:
            pending_count = len(self._delayed_replay_pending)
            ready_count = sum(
                1
                for case in self._delayed_replay_pending.values()
                if (_optional_int(case.get("replay_not_before_ms")) or 0) <= int(time.time() * 1000)
            )
        self.artifacts.append_delayed_replay_summary(
            {
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "cycle": int(cycle),
                "enabled": bool(self.config.delayed_replay_enabled),
                "status": status,
                "reason": reason,
                "pending_count": int(pending_count),
                "ready_count": int(ready_count),
                "processed_count": int(processed_count),
                "skipped_count": int(skipped_count),
                "max_cases": int(self.config.delayed_replay_max_cases_per_cycle),
                "max_seconds": float(self.config.delayed_replay_max_cycle_seconds),
                "active_symbol_count": int(active_symbol_count),
                "open_positions": int(open_positions),
                "opening_symbols": int(opening_symbols),
                "idle_since_ms": idle_since_ms,
                "duration_seconds": round(float(duration_seconds), 6),
            }
        )

    def _recompute_delayed_replay_signal(self, case: dict[str, object]) -> dict[str, object]:
        details = case.get("details") if isinstance(case.get("details"), dict) else {}
        frozen_signal = _live_signal_from_json(case.get("signal_json") or details.get("signal_json"))
        symbol = str(case.get("symbol") or "")
        levels_tf_value = str(case.get("levels_tf") or "")
        entry_tf_value = str(case.get("entry_tf") or "")
        decision_ts = int(case.get("decision_timestamp_ms") or 0)
        if not symbol or not levels_tf_value or not entry_tf_value or decision_ts <= 0:
            return {
                "status": "invalid_case_identity",
                "source": "invalid_case",
                "decision_snapshot_status": "not_available",
                "decision_snapshot_source": "none",
                "signal": None,
                "reject_reasons": (),
                "data_status": "invalid_case_identity",
                "decision_data_end_timestamp_ms": "",
            }
        try:
            levels_timeframe = Timeframe(levels_tf_value)
            entry_timeframe = Timeframe(entry_tf_value)
        except ValueError:
            return {
                "status": "invalid_timeframe",
                "source": "invalid_case",
                "decision_snapshot_status": "not_available",
                "decision_snapshot_source": "none",
                "signal": None,
                "reject_reasons": (),
                "data_status": "invalid_timeframe",
                "decision_data_end_timestamp_ms": "",
            }

        levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
        entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
        setup_start_ts = (decision_ts // levels_timeframe_ms) * levels_timeframe_ms
        decision_snapshot = _delayed_replay_snapshot_from_json(
            case.get("decision_snapshot_json") or details.get("decision_snapshot_json")
        )
        use_snapshot = False
        decision_snapshot_status = "not_available"
        decision_snapshot_source = "none"
        frozen_context: dict[str, object] | None = None
        if decision_snapshot is not None:
            snapshot_symbol = str(decision_snapshot.get("symbol") or "")
            snapshot_levels_tf = str(decision_snapshot.get("levels_tf") or "")
            snapshot_entry_tf = str(decision_snapshot.get("entry_tf") or "")
            snapshot_decision_ts = _optional_int(decision_snapshot.get("decision_timestamp_ms"))
            if (
                _position_symbol_key(snapshot_symbol) == _position_symbol_key(symbol)
                and snapshot_levels_tf == levels_timeframe.value
                and snapshot_entry_tf == entry_timeframe.value
                and snapshot_decision_ts == int(decision_ts)
            ):
                use_snapshot = True
                decision_snapshot_status = "ok"
                decision_snapshot_source = str(decision_snapshot.get("source") or "live_decision_in_memory")
                setup_frame = _delayed_replay_rows_to_frame(decision_snapshot.get("baseline_rows"))
                setup_row_frame = _delayed_replay_rows_to_frame([decision_snapshot.get("setup_row")])
                entry_frame = _delayed_replay_rows_to_frame(decision_snapshot.get("entry_rows"))
                if setup_row_frame.empty:
                    setup_status = "snapshot_setup_row_missing"
                else:
                    setup_status = "snapshot_baseline_rows"
                entry_status = "snapshot_entry_rows"
                frozen_raw_context = decision_snapshot.get("frozen_context")
                frozen_context = dict(frozen_raw_context) if isinstance(frozen_raw_context, dict) else {}
            else:
                decision_snapshot_status = "identity_mismatch"
                decision_snapshot_source = str(decision_snapshot.get("source") or "unknown")
                use_snapshot = False
        if not use_snapshot:
            setup_lookback_ms = (self.config.baseline_candles + 5) * levels_timeframe_ms
            setup_frame, setup_status = self._load_delayed_replay_cached_window(
                symbol=symbol,
                timeframe=levels_timeframe,
                start_timestamp_ms=setup_start_ts - setup_lookback_ms,
                end_timestamp_ms=setup_start_ts - levels_timeframe_ms,
            )
            setup_row_frame = pd.DataFrame()
            entry_frame, entry_status = self._load_delayed_replay_cached_window(
                symbol=symbol,
                timeframe=entry_timeframe,
                start_timestamp_ms=setup_start_ts,
                end_timestamp_ms=decision_ts,
            )
        recompute_source = (
            "immutable_live_decision_snapshot_recompute" if use_snapshot else "cache_only_frozen_decision_recompute"
        )
        data_status = f"setup={setup_status};entry={entry_status};snapshot={decision_snapshot_status}"
        if setup_frame.empty or entry_frame.empty:
            return {
                "status": "insufficient_cached_ohlcv" if not use_snapshot else "insufficient_decision_snapshot_ohlcv",
                "source": recompute_source,
                "decision_snapshot_status": decision_snapshot_status,
                "decision_snapshot_source": decision_snapshot_source,
                "signal": None,
                "reject_reasons": (),
                "data_status": data_status,
                "decision_data_end_timestamp_ms": int(decision_ts),
            }
        missing_setup_price = [column for column in REQUIRED_PRICE_COLUMNS if column not in setup_frame.columns]
        missing_setup_flow = [column for column in REQUIRED_FLOW_COLUMNS if column not in setup_frame.columns]
        missing_entry_price = [column for column in REQUIRED_PRICE_COLUMNS if column not in entry_frame.columns]
        missing_entry_flow = [column for column in REQUIRED_FLOW_COLUMNS if column not in entry_frame.columns]
        missing_columns = missing_setup_price + missing_setup_flow + missing_entry_price + missing_entry_flow
        if missing_columns:
            return {
                "status": "missing_columns",
                "source": recompute_source,
                "decision_snapshot_status": decision_snapshot_status,
                "decision_snapshot_source": decision_snapshot_source,
                "signal": None,
                "reject_reasons": tuple(f"missing:{column}" for column in missing_columns),
                "data_status": data_status,
                "decision_data_end_timestamp_ms": int(decision_ts),
            }
        setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        setup_history = setup_frame.loc[
            setup_frame["timestamp"].astype("int64") < int(setup_start_ts)
        ].tail(self.config.baseline_candles).copy()
        if use_snapshot:
            if setup_row_frame.empty:
                return {
                    "status": "snapshot_setup_row_missing",
                    "source": recompute_source,
                    "decision_snapshot_status": decision_snapshot_status,
                    "decision_snapshot_source": decision_snapshot_source,
                    "signal": None,
                    "reject_reasons": (),
                    "data_status": data_status,
                    "decision_data_end_timestamp_ms": int(decision_ts),
                }
            forming_setup = setup_row_frame.iloc[-1]
        else:
            forming_setup = None
        entry_segment = entry_frame.loc[
            (entry_frame["timestamp"].astype("int64") >= int(setup_start_ts))
            & (entry_frame["timestamp"].astype("int64") <= int(decision_ts))
        ].copy()
        if len(setup_history) < self.config.baseline_candles or entry_segment.empty:
            return {
                "status": "insufficient_replay_history",
                "source": recompute_source,
                "decision_snapshot_status": decision_snapshot_status,
                "decision_snapshot_source": decision_snapshot_source,
                "signal": None,
                "reject_reasons": (),
                "data_status": data_status,
                "decision_data_end_timestamp_ms": int(decision_ts),
            }
        seed_close = float(setup_history.iloc[-1]["close"])
        entry_segment = _fill_missing_ohlcv_buckets(
            entry_segment,
            start_timestamp_ms=int(setup_start_ts),
            end_timestamp_ms=int(decision_ts),
            timeframe_ms=entry_timeframe_ms,
            seed_close=seed_close,
        )
        real_entry_segment = _real_ohlcv_buckets(entry_segment)
        if len(real_entry_segment) < self.config.confirmation_candles:
            insufficient_real_only = len(entry_segment) >= self.config.confirmation_candles
            return {
                "status": "insufficient_real_entry_buckets" if insufficient_real_only else "setup_too_early",
                "source": recompute_source,
                "decision_snapshot_status": decision_snapshot_status,
                "decision_snapshot_source": decision_snapshot_source,
                "signal": None,
                "reject_reasons": (
                    "reject_insufficient_real_entry_buckets" if insufficient_real_only else "reject_setup_too_early",
                ),
                "data_status": data_status,
                "decision_data_end_timestamp_ms": int(decision_ts),
                "real_entry_segment_bucket_count": int(len(real_entry_segment)),
                "entry_segment_bucket_count": int(len(entry_segment)),
                "min_closed_entry_candles": int(self.config.confirmation_candles),
            }
        setup_elapsed_fraction = min(1.0, len(entry_segment) * entry_timeframe_ms / levels_timeframe_ms)
        if forming_setup is None:
            forming_setup = _aggregate_frame_to_candle(entry_segment, timestamp_ms=int(setup_start_ts))
        if forming_setup is None:
            return {
                "status": "forming_setup_unavailable",
                "source": recompute_source,
                "decision_snapshot_status": decision_snapshot_status,
                "decision_snapshot_source": decision_snapshot_source,
                "signal": None,
                "reject_reasons": (),
                "data_status": data_status,
                "decision_data_end_timestamp_ms": int(decision_ts),
            }
        category_rejections: list[dict[str, object]] = []
        signal = self._build_signal_from_components(
            symbol=symbol,
            baseline=setup_history,
            setup_row=forming_setup,
            entry_segment=entry_segment,
            now_ms=decision_ts + entry_timeframe_ms,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            setup_source=(
                str(decision_snapshot.get("setup_source") or "forming_htf_from_entry_tf")
                if use_snapshot and decision_snapshot is not None
                else "delayed_replay_forming_htf_from_entry_tf"
            ),
            setup_elapsed_fraction=setup_elapsed_fraction,
            setup_closed_entry_candles=len(real_entry_segment),
            emit_diagnostics=False,
            category_rejections_out=category_rejections,
            allow_exchange_context_fetch=False,
            frozen_exchange_context=frozen_context,
        )
        reject_reasons = tuple(
            str(row.get("category_reject_reason") or row.get("reason") or "")
            for row in category_rejections
            if str(row.get("category_reject_reason") or row.get("reason") or "")
        )
        if signal is None and frozen_signal is not None:
            context_disabled = any(
                "replay_exchange_context_fetch_disabled" in str(value)
                for row in category_rejections
                for value in row.values()
            )
            if context_disabled:
                return {
                    "status": "frozen_live_signal_snapshot_exchange_context_unavailable",
                    "source": "frozen_live_signal_snapshot",
                    "decision_snapshot_status": decision_snapshot_status,
                    "decision_snapshot_source": decision_snapshot_source,
                    "signal": frozen_signal,
                    "reject_reasons": reject_reasons,
                    "data_status": data_status,
                    "decision_data_end_timestamp_ms": int(decision_ts),
                }
        return {
            "status": "recomputed_signal" if signal is not None else "recomputed_no_signal",
            "source": recompute_source,
            "decision_snapshot_status": decision_snapshot_status,
            "decision_snapshot_source": decision_snapshot_source,
            "signal": signal,
            "reject_reasons": reject_reasons,
            "data_status": data_status,
            "decision_data_end_timestamp_ms": int(decision_ts),
        }

    def _evaluate_delayed_replay_case(self, case: dict[str, object], *, now_ms: int) -> dict[str, object]:
        details = case.get("details") if isinstance(case.get("details"), dict) else {}
        decision_ts = int(case.get("decision_timestamp_ms") or 0)
        source_event = str(case.get("source_event") or "")
        live_decision_class = str(case.get("live_decision_class") or "")
        recompute = self._recompute_delayed_replay_signal(case)
        replay_signal = recompute.get("signal") if isinstance(recompute.get("signal"), LiveSignal) else None
        recompute_source = str(recompute.get("source") or "")
        strict_recompute_signal = bool(
            replay_signal is not None
            and recompute_source in {"immutable_live_decision_snapshot_recompute", "cache_only_frozen_decision_recompute"}
            and str(recompute.get("status") or "") == "recomputed_signal"
        )
        frozen_signal_snapshot_used = bool(replay_signal is not None and recompute_source == "frozen_live_signal_snapshot")
        would_select_signal = replay_signal is not None
        strict_replay_would_enter = bool(strict_recompute_signal)
        snapshot_signal_available = bool(frozen_signal_snapshot_used)
        would_enter = bool(strict_replay_would_enter)
        if snapshot_signal_available and live_decision_class == "signal_selected":
            mismatch_type = "live_selected_frozen_signal_snapshot"
        elif snapshot_signal_available and live_decision_class == "execution_rejected":
            mismatch_type = "live_execution_rejected_frozen_signal_snapshot"
        elif snapshot_signal_available:
            mismatch_type = "live_rejected_frozen_signal_snapshot"
        elif strict_replay_would_enter and live_decision_class == "signal_selected":
            mismatch_type = "live_selected_recomputed_signal"
        elif strict_replay_would_enter and live_decision_class == "execution_rejected":
            mismatch_type = "live_execution_rejected_replay_would_enter"
        elif strict_replay_would_enter:
            mismatch_type = "live_rejected_replay_would_enter"
        elif live_decision_class == "signal_selected":
            mismatch_type = "live_selected_replay_no_signal"
        else:
            mismatch_type = "live_rejected_replay_rejected"
        operator_alert_kind = ""
        if live_decision_class != "signal_selected":
            if strict_replay_would_enter:
                operator_alert_kind = "strict_replay_ignored_entry"
            elif snapshot_signal_available:
                operator_alert_kind = "frozen_signal_snapshot_only"
        outcome = self._delayed_replay_outcome(case, now_ms=now_ms, signal=replay_signal) if would_select_signal else {
            "outcome_status": "not_applicable_non_selected_case",
            "outcome_window_start_ms": "",
            "outcome_window_end_ms": "",
            "outcome_rows": 0,
            "outcome_high": "",
            "outcome_low": "",
            "outcome_close": "",
            "tp1_would_hit": "",
            "stop_would_hit": "",
            "first_hit": "",
        }
        telegram_notified = False
        if operator_alert_kind and replay_signal is not None:
            telegram_notified = self._notify_delayed_replay_ignored_entry(
                replay_signal,
                case=case,
                mismatch_type=mismatch_type,
                recompute_status=str(recompute.get("status") or ""),
                recompute_source=recompute_source,
                outcome=outcome,
            )
        return {
            "case_id": str(case.get("case_id") or ""),
            "queued_at_utc": str(case.get("queued_at_utc") or ""),
            "processed_at_utc": datetime.now(UTC).isoformat(),
            "status": "ok",
            "symbol": str(case.get("symbol") or ""),
            "source_event": source_event,
            "priority": int(case.get("priority") or 99),
            "live_decision_class": live_decision_class,
            "live_reason": str(case.get("live_reason") or ""),
            "levels_tf": str(case.get("levels_tf") or ""),
            "entry_tf": str(case.get("entry_tf") or ""),
            "decision_timestamp_ms": decision_ts,
            "replay_not_before_ms": int(case.get("replay_not_before_ms") or 0),
            "queued_delay_seconds": round((now_ms - int(case.get("queued_at_ms") or now_ms)) / 1000.0, 3),
            "recompute_status": str(recompute.get("status") or ""),
            "recompute_source": str(recompute.get("source") or ""),
            "decision_snapshot_status": str(recompute.get("decision_snapshot_status") or ""),
            "decision_snapshot_source": str(recompute.get("decision_snapshot_source") or ""),
            "would_select_signal": bool(would_select_signal),
            "would_enter_under_frozen_decision": bool(would_enter),
            "strict_replay_would_enter": bool(strict_replay_would_enter),
            "snapshot_signal_available": bool(snapshot_signal_available),
            "strict_recompute_signal": bool(strict_recompute_signal),
            "frozen_signal_snapshot_used": bool(frozen_signal_snapshot_used),
            "operator_alert_kind": operator_alert_kind,
            "mismatch_type": mismatch_type,
            "recomputed_category_id": replay_signal.category_id if replay_signal is not None else "",
            "recomputed_category_label": replay_signal.category_label if replay_signal is not None else "",
            "recomputed_entry_price": _finite_or_none(replay_signal.entry_price if replay_signal is not None else None),
            "recomputed_stop_price": _finite_or_none(replay_signal.stop_price if replay_signal is not None else None),
            "recomputed_tp1_price": _finite_or_none(replay_signal.tp1_price if replay_signal is not None else None),
            "recompute_reject_reasons": ",".join(str(reason) for reason in recompute.get("reject_reasons", ()) if reason),
            "recompute_data_status": str(recompute.get("data_status") or ""),
            "decision_data_end_timestamp_ms": recompute.get("decision_data_end_timestamp_ms") or "",
            "telegram_notified": bool(telegram_notified),
            "signal_entry_price": _finite_or_none(
                replay_signal.entry_price if replay_signal is not None else details.get("signal_entry_price")
            ),
            "signal_stop_price": _finite_or_none(
                replay_signal.stop_price if replay_signal is not None else details.get("signal_stop_price")
            ),
            "signal_tp1_price": _finite_or_none(
                replay_signal.tp1_price if replay_signal is not None else details.get("signal_tp1_price")
            ),
            "outcome_is_post_decision": bool(outcome.get("outcome_window_start_ms") not in ("", None)),
            "source_scan_mode": str(details.get("source_scan_mode") or "delayed_replay_cache_only"),
            "delayed_replay_contract": DELAYED_REPLAY_CONTRACT,
            **outcome,
        }

    def _delayed_replay_error_row(self, case: dict[str, object], *, now_ms: int, exc: Exception) -> dict[str, object]:
        return {
            "case_id": str(case.get("case_id") or ""),
            "queued_at_utc": str(case.get("queued_at_utc") or ""),
            "processed_at_utc": datetime.now(UTC).isoformat(),
            "status": "error",
            "symbol": str(case.get("symbol") or ""),
            "source_event": str(case.get("source_event") or ""),
            "priority": int(case.get("priority") or 99),
            "live_decision_class": str(case.get("live_decision_class") or ""),
            "live_reason": str(case.get("live_reason") or ""),
            "levels_tf": str(case.get("levels_tf") or ""),
            "entry_tf": str(case.get("entry_tf") or ""),
            "decision_timestamp_ms": int(case.get("decision_timestamp_ms") or 0),
            "replay_not_before_ms": int(case.get("replay_not_before_ms") or 0),
            "queued_delay_seconds": round((now_ms - int(case.get("queued_at_ms") or now_ms)) / 1000.0, 3),
            "recompute_status": f"error:{type(exc).__name__}",
            "recompute_source": "error",
            "decision_snapshot_status": "",
            "decision_snapshot_source": "",
            "would_select_signal": "",
            "would_enter_under_frozen_decision": "",
            "strict_replay_would_enter": "",
            "snapshot_signal_available": "",
            "strict_recompute_signal": "",
            "frozen_signal_snapshot_used": "",
            "operator_alert_kind": "",
            "mismatch_type": "replay_error",
            "decision_data_end_timestamp_ms": "",
            "outcome_status": str(exc)[:500],
            "outcome_is_post_decision": "",
            "delayed_replay_contract": DELAYED_REPLAY_CONTRACT,
        }

    def _notify_delayed_replay_ignored_entry(
        self,
        signal: LiveSignal,
        *,
        case: dict[str, object],
        mismatch_type: str,
        recompute_status: str,
        recompute_source: str,
        outcome: dict[str, object],
    ) -> bool:
        case_id = str(case.get("case_id") or "")
        live_reason = str(case.get("live_reason") or "")
        try:
            self.telegram.send(
                channel="events",
                key=f"delayed_replay_ignored_entry:{case_id}",
                text=_format_delayed_replay_ignored_entry_message(
                    signal,
                    live_reason=live_reason,
                    mismatch_type=mismatch_type,
                    recompute_status=recompute_status,
                    recompute_source=recompute_source,
                    outcome=outcome,
                ),
                symbol=signal.symbol,
            )
            self.artifacts.append_event(
                "delayed_replay_ignored_entry_notified",
                signal.symbol,
                {
                    "case_id": case_id,
                    "live_reason": live_reason,
                    "mismatch_type": mismatch_type,
                    "recompute_status": recompute_status,
                    "recompute_source": recompute_source,
                    "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                },
            )
            return True
        except Exception as exc:
            self.artifacts.append_event(
                "telegram_delayed_replay_ignored_entry_enqueue_failed",
                signal.symbol,
                {
                    "case_id": case_id,
                    "live_reason": live_reason,
                    "mismatch_type": mismatch_type,
                    "reason": str(exc),
                },
            )
            return False

    def _delayed_replay_outcome(
        self,
        case: dict[str, object],
        *,
        now_ms: int,
        signal: LiveSignal | None = None,
    ) -> dict[str, object]:
        details = case.get("details") if isinstance(case.get("details"), dict) else {}
        symbol = str(case.get("symbol") or "")
        entry_tf_value = str(case.get("entry_tf") or "")
        decision_ts = int(case.get("decision_timestamp_ms") or 0)
        if not symbol or not entry_tf_value or decision_ts <= 0:
            return _empty_delayed_replay_outcome("invalid_case_identity")
        try:
            entry_timeframe = Timeframe(entry_tf_value)
        except ValueError:
            return _empty_delayed_replay_outcome("invalid_entry_timeframe")
        timeframe_ms = int(entry_timeframe.to_milliseconds())
        lookahead_ms = int(float(self.config.delayed_replay_outcome_lookahead_seconds) * 1000)
        outcome_start_ms = int(decision_ts) + timeframe_ms
        outcome_end_ms = int(decision_ts) + max(timeframe_ms, lookahead_ms)
        if now_ms < outcome_end_ms + timeframe_ms:
            return _empty_delayed_replay_outcome(
                "outcome_window_not_closed_yet",
                outcome_window_start_ms=outcome_start_ms,
                outcome_window_end_ms=outcome_end_ms,
            )
        frame, status = self._load_delayed_replay_cached_window(
            symbol=symbol,
            timeframe=entry_timeframe,
            start_timestamp_ms=outcome_start_ms,
            end_timestamp_ms=outcome_end_ms,
        )
        if frame.empty:
            return _empty_delayed_replay_outcome(
                status,
                outcome_window_start_ms=outcome_start_ms,
                outcome_window_end_ms=outcome_end_ms,
            )
        highs = pd.to_numeric(frame["high"], errors="coerce") if "high" in frame.columns else pd.Series(dtype="float64")
        lows = pd.to_numeric(frame["low"], errors="coerce") if "low" in frame.columns else pd.Series(dtype="float64")
        closes = pd.to_numeric(frame["close"], errors="coerce") if "close" in frame.columns else pd.Series(dtype="float64")
        high = float(highs.max()) if not highs.empty else float("nan")
        low = float(lows.min()) if not lows.empty else float("nan")
        close = float(closes.iloc[-1]) if not closes.empty else float("nan")
        tp1_price = float(signal.tp1_price) if signal is not None else _optional_float(details.get("signal_tp1_price"))
        stop_price = float(signal.stop_price) if signal is not None else _optional_float(details.get("signal_stop_price"))
        tp1_hit = bool(tp1_price is not None and math.isfinite(high) and high >= tp1_price)
        stop_hit = bool(stop_price is not None and math.isfinite(low) and low <= stop_price)
        first_hit = ""
        if tp1_hit or stop_hit:
            for _, row in frame.sort_values("timestamp").iterrows():
                row_high = _optional_float(row.get("high"))
                row_low = _optional_float(row.get("low"))
                hit_tp = tp1_price is not None and row_high is not None and row_high >= tp1_price
                hit_stop = stop_price is not None and row_low is not None and row_low <= stop_price
                if hit_tp and hit_stop:
                    first_hit = "ambiguous_same_candle"
                    break
                if hit_tp:
                    first_hit = "tp1"
                    break
                if hit_stop:
                    first_hit = "stop"
                    break
        return {
            "outcome_status": status,
            "outcome_window_start_ms": outcome_start_ms,
            "outcome_window_end_ms": outcome_end_ms,
            "outcome_rows": int(len(frame)),
            "outcome_high": _finite_or_none(high),
            "outcome_low": _finite_or_none(low),
            "outcome_close": _finite_or_none(close),
            "tp1_would_hit": tp1_hit,
            "stop_would_hit": stop_hit,
            "first_hit": first_hit,
        }

    def _load_delayed_replay_cached_window(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> tuple[pd.DataFrame, str]:
        timeframe_ms = int(timeframe.to_milliseconds())
        expected_start_ms = (int(start_timestamp_ms) // timeframe_ms) * timeframe_ms
        expected_end_ms = (int(end_timestamp_ms) // timeframe_ms) * timeframe_ms
        symbol_key = _position_symbol_key(symbol)
        memory_key = (symbol_key, timeframe.value)
        frames: list[pd.DataFrame] = []
        if memory_key in self._live_ohlcv_frame_cache:
            frames.append(self._live_ohlcv_frame_cache[memory_key])
        storage = self._ohlcv_cache_storage
        storage_status = "storage_disabled"
        if storage is not None:
            load_result = storage.load_window_result(
                symbol,
                timeframe,
                start_timestamp_ms=expected_start_ms,
                end_timestamp_ms=expected_end_ms,
            )
            storage_status = f"storage_{load_result.status}:{load_result.reason}"
            if load_result.frame is not None and not load_result.frame.empty:
                frames.append(_prepare_cached_ohlcv_frame(load_result.frame))
        if not frames:
            return pd.DataFrame(), f"cache_only_no_data:{storage_status}"
        frame = _concat_cached_ohlcv_frames(frames)
        if frame.empty or "timestamp" not in frame.columns:
            return pd.DataFrame(), f"cache_only_empty:{storage_status}"
        window = frame.loc[
            (frame["timestamp"].astype("int64") >= expected_start_ms)
            & (frame["timestamp"].astype("int64") <= expected_end_ms)
        ].copy()
        if window.empty:
            return pd.DataFrame(), f"cache_only_window_empty:{storage_status}"
        remaining = _missing_ohlcv_ranges(
            window,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
            timeframe_ms=timeframe_ms,
        )
        if remaining:
            return window.sort_values("timestamp").reset_index(drop=True), f"cache_only_partial_gap:{len(remaining)}"
        return window.sort_values("timestamp").reset_index(drop=True), "cache_only_ok"

    def _next_symbol_batch(self, symbols: list[str]) -> list[str]:
        selection = self._select_next_symbol_batch(symbols)
        self._current_batch_symbol_scan_mode = dict(selection.scan_modes)
        self._current_scheduler_source = selection.scheduler_source
        self._current_inactive_scan_slots = selection.inactive_scan_slots
        self._current_inactive_scan_slots_source = selection.inactive_scan_slots_source
        self._current_inactive_cold_coverage_gate_reason = selection.inactive_cold_coverage_gate_reason
        self._current_inactive_cold_coverage_health_pct = selection.inactive_cold_coverage_health_pct
        self._current_inactive_cold_coverage_health_threshold_pct = selection.inactive_cold_coverage_health_threshold_pct
        self._current_inactive_cold_coverage_adaptive_score = selection.inactive_cold_coverage_adaptive_score
        self._current_inactive_cold_coverage_health_factor = selection.inactive_cold_coverage_health_factor
        self._current_inactive_cold_coverage_speed_factor = selection.inactive_cold_coverage_speed_factor
        self._current_inactive_cold_coverage_active_factor = selection.inactive_cold_coverage_active_factor
        self._current_inactive_cold_coverage_load_factor = selection.inactive_cold_coverage_load_factor
        self._current_inactive_cold_coverage_pressure_ewma = selection.inactive_cold_coverage_pressure_ewma
        self._current_inactive_cold_coverage_cycle_seconds_ewma = selection.inactive_cold_coverage_cycle_seconds_ewma
        self._current_latency_sla_status = selection.latency_sla_status
        self._current_latency_sla_optional_scans_allowed = selection.latency_sla_optional_scans_allowed
        self._current_latency_sla_due_scan_p95_seconds = selection.latency_sla_due_scan_p95_seconds
        self._current_latency_sla_due_scan_max_seconds = selection.latency_sla_due_scan_max_seconds
        self._current_latency_sla_due_scan_samples = selection.latency_sla_due_scan_samples
        self._current_latency_sla_threshold_seconds = selection.latency_sla_threshold_seconds
        self._current_latency_sla_min_due_samples = selection.latency_sla_min_due_samples
        self._current_latency_sla_reason = selection.latency_sla_reason
        self._current_adaptive_precise_budget_status = selection.adaptive_precise_budget_status
        self._current_adaptive_precise_budget_reason = selection.adaptive_precise_budget_reason
        self._current_adaptive_precise_budget_limit = selection.adaptive_precise_budget_limit
        self._current_adaptive_precise_budget_radar_slots = selection.adaptive_precise_budget_radar_slots
        self._current_candidate_queue_status = selection.candidate_queue_status
        self._current_candidate_queue_reason = selection.candidate_queue_reason
        self._current_candidate_queue_radar_total_before = selection.candidate_queue_radar_total_before
        self._current_candidate_queue_radar_total_after = selection.candidate_queue_radar_total_after
        self._current_candidate_queue_warm_total_before = selection.candidate_queue_warm_total_before
        self._current_candidate_queue_warm_total_after = selection.candidate_queue_warm_total_after
        self._current_candidate_queue_actionable_sample_count = selection.candidate_queue_actionable_sample_count
        self._current_candidate_queue_dropped_pressure_count = selection.candidate_queue_dropped_pressure_count
        self._current_candidate_queue_expired_backlog_stale_count = selection.candidate_queue_expired_backlog_stale_count
        self._current_candidate_queue_top_score = selection.candidate_queue_top_score
        self.artifacts.append_event(
            "symbol_batch_selected",
            "__live__",
            {
                "scheduler_source": selection.scheduler_source,
                "active_symbols": list(selection.active_due),
                "active_count": len(selection.active_due),
                "active_waiting_count": len(selection.active_waiting),
                "active_waiting_symbols": list(selection.active_waiting),
                "ticker_radar_symbols": list(selection.radar_due),
                "ticker_radar_count": len(selection.radar_due),
                "ticker_radar_waiting_count": len(selection.radar_waiting),
                "ticker_radar_waiting_symbols": list(selection.radar_waiting),
                "warm_watch_waiting_count": len(selection.warm_watch_waiting),
                "warm_watch_waiting_symbols": list(selection.warm_watch_waiting),
                "latency_sla_status": selection.latency_sla_status,
                "latency_sla_optional_scans_allowed": bool(selection.latency_sla_optional_scans_allowed),
                "latency_sla_due_scan_p95_seconds": (
                    round(float(selection.latency_sla_due_scan_p95_seconds), 3)
                    if selection.latency_sla_due_scan_p95_seconds is not None
                    else ""
                ),
                "latency_sla_due_scan_max_seconds": (
                    round(float(selection.latency_sla_due_scan_max_seconds), 3)
                    if selection.latency_sla_due_scan_max_seconds is not None
                    else ""
                ),
                "latency_sla_due_scan_samples": int(selection.latency_sla_due_scan_samples),
                "latency_sla_threshold_seconds": float(selection.latency_sla_threshold_seconds),
                "latency_sla_min_due_samples": int(selection.latency_sla_min_due_samples),
                "latency_sla_reason": selection.latency_sla_reason,
                "candidate_queue_status": selection.candidate_queue_status,
                "candidate_queue_reason": selection.candidate_queue_reason,
                "candidate_queue_radar_total_before": selection.candidate_queue_radar_total_before,
                "candidate_queue_radar_total_after": selection.candidate_queue_radar_total_after,
                "candidate_queue_warm_total_before": selection.candidate_queue_warm_total_before,
                "candidate_queue_warm_total_after": selection.candidate_queue_warm_total_after,
                "candidate_queue_actionable_sample_count": selection.candidate_queue_actionable_sample_count,
                "candidate_queue_dropped_pressure_count": selection.candidate_queue_dropped_pressure_count,
                "candidate_queue_expired_backlog_stale_count": selection.candidate_queue_expired_backlog_stale_count,
                "candidate_queue_top_score": (
                    round(float(selection.candidate_queue_top_score), 6)
                    if selection.candidate_queue_top_score is not None
                    else ""
                ),
                "adaptive_precise_budget_status": selection.adaptive_precise_budget_status,
                "adaptive_precise_budget_reason": selection.adaptive_precise_budget_reason,
                "adaptive_precise_budget_limit": (
                    selection.adaptive_precise_budget_limit
                    if selection.adaptive_precise_budget_limit is not None
                    else ""
                ),
                "adaptive_precise_budget_radar_slots": (
                    selection.adaptive_precise_budget_radar_slots
                    if selection.adaptive_precise_budget_radar_slots is not None
                    else ""
                ),
                "adaptive_precise_budget_manual_cap": (
                    selection.adaptive_precise_budget_manual_cap
                    if selection.adaptive_precise_budget_manual_cap is not None
                    else ""
                ),
                "max_precise_scan_symbols_per_cycle": (
                    self.config.max_precise_scan_symbols_per_cycle
                    if self.config.max_precise_scan_symbols_per_cycle is not None
                    else ""
                ),
                "precise_budget_remaining_after_active": (
                    selection.precise_budget_remaining_after_active
                    if selection.precise_budget_remaining_after_active is not None
                    else ""
                ),
                "precise_budget_remaining_after_radar": (
                    selection.precise_budget_remaining_after_radar
                    if selection.precise_budget_remaining_after_radar is not None
                    else ""
                ),
                "inactive_count": len(selection.inactive),
                "inactive_scan_slots_per_cycle": selection.inactive_scan_slots,
                "inactive_scan_slots_source": selection.inactive_scan_slots_source,
                "configured_inactive_scan_slots_per_cycle": (
                    self.config.inactive_scan_slots_per_cycle
                    if self.config.inactive_scan_slots_per_cycle is not None
                    else ""
                ),
                "inactive_cold_coverage_danger": bool(selection.inactive_cold_coverage_danger),
                "inactive_cold_coverage_gate_reason": selection.inactive_cold_coverage_gate_reason,
                "inactive_cold_coverage_health_pct": round(selection.inactive_cold_coverage_health_pct, 3),
                "inactive_cold_coverage_health_threshold_pct": round(
                    selection.inactive_cold_coverage_health_threshold_pct, 3
                ),
                "inactive_cold_coverage_active_blocked": bool(selection.inactive_cold_coverage_active_blocked),
                "inactive_cold_coverage_position_blocked": bool(selection.inactive_cold_coverage_position_blocked),
                "inactive_cold_coverage_active_due_count": selection.inactive_cold_coverage_active_due_count,
                "inactive_cold_coverage_active_waiting_count": selection.inactive_cold_coverage_active_waiting_count,
                "inactive_cold_coverage_adaptive_score": round(selection.inactive_cold_coverage_adaptive_score, 4),
                "inactive_cold_coverage_health_factor": round(selection.inactive_cold_coverage_health_factor, 4),
                "inactive_cold_coverage_speed_factor": round(selection.inactive_cold_coverage_speed_factor, 4),
                "inactive_cold_coverage_active_factor": round(selection.inactive_cold_coverage_active_factor, 4),
                "inactive_cold_coverage_load_factor": round(selection.inactive_cold_coverage_load_factor, 4),
                "inactive_cold_coverage_pressure_ewma": round(selection.inactive_cold_coverage_pressure_ewma, 4),
                "inactive_cold_coverage_cycle_seconds_ewma": round(selection.inactive_cold_coverage_cycle_seconds_ewma, 3),
                "inactive_cold_coverage_base_slots": selection.inactive_cold_coverage_base_slots,
                "inactive_cold_coverage_max_slots": selection.inactive_cold_coverage_max_slots,
                "inactive_subminute_scan_policy": "DANGER_adaptive_precise_cold_coverage_health_latency_pressure_gated",
                "subminute_entry_pairs_present": bool(_live_config_has_subminute_entry_pairs(self.config)),
                "scan_hot_timeframes_per_symbol": bool(self.config.scan_hot_timeframes_per_symbol),
                "symbol_batch_size": self.config.symbol_batch_size,
                "symbol_batch_size_role": "legacy_inactive_scan_cap_when_no_explicit_inactive_budget",
                "batch_in_full_cycle": selection.batch_in_full_cycle,
                "full_symbol_cycle": selection.full_symbol_cycle,
                "effective_scan_count": len(selection.batch),
                "scan_reason_by_symbol": {
                    symbol: selection.scan_modes.get(_position_symbol_key(symbol), "unknown")
                    for symbol in selection.batch
                },
                "inactive_cursor_before": selection.inactive_cursor_before,
                "inactive_cursor_after": selection.inactive_cursor_after,
                "last_full_symbol_cycle_seconds": (
                    round(self._last_symbol_universe_cycle_seconds, 3)
                    if self._last_symbol_universe_cycle_seconds is not None
                    else ""
                ),
            },
        )
        return list(selection.batch)

    def _update_ws_aggtrade_subscriptions(self, batch: list[str]) -> LiveWsAggTradeSubscriptionStats:
        if self.aggtrade_source is None:
            return LiveWsAggTradeSubscriptionStats(
                enabled=False,
                source="disabled",
                connection_status="disabled",
            )
        warm_watch_selected: list[str] = []
        warm_watch_dropped: list[str] = []
        if not _live_config_has_subminute_entry_pairs(self.config):
            target_symbols: tuple[str, ...] = ()
        else:
            target_by_key = {
                _position_symbol_key(symbol): symbol
                for symbol in batch
                if self._subminute_entry_scan_allowed(symbol)
            }
            with self._state_lock:
                for state in self._active_symbols.values():
                    target_by_key[_position_symbol_key(state.symbol)] = state.symbol
                ranked_warm_watch = sorted(
                    self._warm_watch.values(),
                    key=lambda item: (float(item.score), int(item.updated_at_ms), item.symbol),
                    reverse=True,
                )
                warm_watch_cap = max(1, int(self.config.warm_watch_aggtrade_target_cap))
                for warm_watch in ranked_warm_watch[:warm_watch_cap]:
                    target_by_key[_position_symbol_key(warm_watch.symbol)] = warm_watch.symbol
                    warm_watch_selected.append(warm_watch.symbol)
                warm_watch_dropped = [warm_watch.symbol for warm_watch in ranked_warm_watch[warm_watch_cap:]]
                for watch in self._ticker_radar_watch.values():
                    target_by_key[_position_symbol_key(watch.symbol)] = watch.symbol
            target_symbols = tuple(target_by_key.values())
        status = self.aggtrade_source.set_symbols(target_symbols)
        if int(status.get("target_count", 0) or 0) > int(status.get("subscribed_count", 0) or 0):
            wait_status = self.aggtrade_source.wait_for_targets(timeout_seconds=0.5)
            status = {**status, **wait_status}
        self.artifacts.append_event(
            "ws_aggtrade_subscription_target",
            "__live__",
            {
                "source": status.get("source", self.aggtrade_source.source_id),
                "target_count": status.get("target_count", len(target_symbols)),
                "subscribed_count": status.get("subscribed_count", ""),
                "connection_status": status.get("connection_status", ""),
                "last_error": str(status.get("last_error", ""))[:500],
                "target_symbols": status.get("target_symbols", list(target_symbols)),
                "warm_watch_aggtrade_target_cap": int(self.config.warm_watch_aggtrade_target_cap),
                "warm_watch_aggtrade_target_count": int(len(warm_watch_selected)),
                "warm_watch_aggtrade_dropped_count": int(len(warm_watch_dropped)),
                "warm_watch_aggtrade_dropped_symbols": warm_watch_dropped[:50],
                "warm_watch_aggtrade_policy": "active_and_ticker_radar_uncapped_warm_watch_bounded_by_score",
            },
        )
        return LiveWsAggTradeSubscriptionStats(
            enabled=True,
            source=str(status.get("source", self.aggtrade_source.source_id)),
            target_count=int(status.get("target_count", len(target_symbols)) or 0),
            subscribed_count=status.get("subscribed_count", ""),
            connection_status=str(status.get("connection_status", "")),
            last_error=str(status.get("last_error", ""))[:500],
        )


    def _ws_aggtrade_effective_source_for_cycle(self, stats: LiveWsAggTradeSubscriptionStats) -> str:
        if not stats.enabled:
            return "disabled"
        if self._cycle_ws_aggtrade_coverage_pending > 0:
            return "uncovered_ws_pending"
        connection_status = (stats.connection_status or "").lower()
        if connection_status == "connected":
            if self._cycle_ws_aggtrade_backfill_reads > 0:
                return "ws_with_rest_gap_backfill"
            return "ws"
        if self._cycle_ws_aggtrade_backfill_reads > 0:
            return "rest_backfill_degraded"
        return f"ws_{connection_status or 'unknown'}_no_entry_read"

    def _is_ws_healthy_for_cycle(
        self,
        ticker_stats: LiveTickerRadarCycleStats,
        aggtrade_stats: LiveWsAggTradeSubscriptionStats,
    ) -> tuple[bool, str]:
        ticker_health_status = self._ticker_health_status(status=ticker_stats.status, source=ticker_stats.source)
        if ticker_health_status == "not_due":
            ticker_health_status = self._last_ticker_radar_health_status
        bootstrap = self._ws_health_observed_seconds <= DEFAULT_LIVE_WS_HEALTH_BOOTSTRAP_SECONDS
        if ticker_health_status == "primary_seeded_rest" and bootstrap:
            ticker_health_status = "primary"
        if ticker_health_status != "primary":
            return False, f"ticker_{ticker_health_status or 'unknown'}"
        if not aggtrade_stats.enabled or aggtrade_stats.target_count <= 0:
            return True, "ticker_primary_no_flow_targets"
        connection_status = (aggtrade_stats.connection_status or "").lower()
        if connection_status != "connected":
            return False, f"flow_{connection_status or 'unknown'}"
        subscribed_count = _optional_int(aggtrade_stats.subscribed_count)
        if subscribed_count is None or subscribed_count < aggtrade_stats.target_count:
            return False, "flow_subscription_mismatch"
        if self._cycle_ws_aggtrade_coverage_pending > 0:
            return False, "flow_coverage_pending"
        if self._cycle_ws_aggtrade_backfill_reads > 0:
            if bootstrap:
                return True, "startup_flow_rest_backfill"
            return False, "flow_rest_backfill"
        return True, "primary_ws"

    def _live_connection_status_text(
        self,
        *,
        ticker_stats: LiveTickerRadarCycleStats,
        aggtrade_stats: LiveWsAggTradeSubscriptionStats,
        ws_healthy: bool,
        ws_health_reason: str,
    ) -> str:
        ticker_error_label = _ws_error_short_label(ticker_stats.reason)
        flow_error_label = _ws_error_short_label(aggtrade_stats.last_error)
        if ticker_stats.status == "primary_seeded_rest":
            return "ticker seed"
        if ws_healthy:
            if not aggtrade_stats.enabled or aggtrade_stats.target_count <= 0:
                return "ticker ok"
            return "ticker ok · flow ok"
        if ticker_stats.status == "failed":
            return "ticker нет" + (f"/{ticker_error_label}" if ticker_error_label else "")
        if ticker_stats.status == "degraded_rest_fallback":
            return "ticker REST" + (f"/{ticker_error_label}" if ticker_error_label else "")
        if ws_health_reason.startswith("ticker_"):
            ticker_reason = ws_health_reason.removeprefix("ticker_")
            if ticker_reason == "rest_fetch_tickers_ok":
                return "ticker REST ok"
            return ticker_reason.replace("_", " ")
        if aggtrade_stats.enabled and aggtrade_stats.target_count > 0:
            subscribed_count = _optional_int(aggtrade_stats.subscribed_count)
            connection_status = (aggtrade_stats.connection_status or "").lower()
            if connection_status != "connected":
                if self._cycle_ws_aggtrade_coverage_pending > 0:
                    return "flow pending" + (f"/{flow_error_label}" if flow_error_label else "")
                if self._cycle_ws_aggtrade_backfill_reads > 0:
                    return "flow REST" + (f"/{flow_error_label}" if flow_error_label else "")
                return "flow нет" + (f"/{flow_error_label}" if flow_error_label else "")
            if subscribed_count is None or subscribed_count < aggtrade_stats.target_count:
                return "flow подписка"
            if self._cycle_ws_aggtrade_backfill_reads > 0:
                return "flow gap REST"
            if self._cycle_ws_aggtrade_coverage_pending > 0:
                return "flow pending"
        return ws_health_reason.replace("_", " ")

    def _live_data_status_text(
        self,
        *,
        ticker_stats: LiveTickerRadarCycleStats,
        aggtrade_stats: LiveWsAggTradeSubscriptionStats,
        ws_health_reason: str,
    ) -> str:
        if ticker_stats.status == "failed":
            label = _ws_error_short_label(ticker_stats.reason)
            return "Тикер нет" + (f"/{label}" if label else "")
        if ticker_stats.status in {"degraded_rest_fallback", "primary_seeded_rest"}:
            return "Тикер REST"
        if ws_health_reason.startswith("ticker_") and ws_health_reason != "ticker_primary_no_flow_targets":
            reason = ws_health_reason.removeprefix("ticker_").replace("_", " ")
            return f"Тикер {reason}"[:26]
        if aggtrade_stats.enabled and aggtrade_stats.target_count > 0:
            connection_status = (aggtrade_stats.connection_status or "").lower()
            if connection_status != "connected":
                if self._cycle_ws_aggtrade_not_connected_backfill_reads > 0:
                    return "Поток REST"
                if self._cycle_ws_aggtrade_coverage_pending > 0:
                    return "Поток ждёт"
                label = _ws_error_short_label(aggtrade_stats.last_error)
                return "Поток нет" + (f"/{label}" if label else "")
            subscribed_count = _optional_int(aggtrade_stats.subscribed_count)
            if subscribed_count is None or subscribed_count < aggtrade_stats.target_count:
                return "Поток подписка"
            if self._cycle_ws_aggtrade_coverage_pending > 0:
                return "Поток ждёт"
            if self._cycle_ws_aggtrade_backfill_reads > 0 or self._cycle_aggtrade_gap_prefetch_backfill_ranges > 0:
                return "Поток gapREST"
        if self._cycle_ohlcv_cache_remaining_gap_count > 0:
            return "Кеш gap"
        if self._cycle_ohlcv_cache_filled_reads > 0 or self._cycle_ohlcv_cache_fetched_rows > 0:
            return "Кеш REST"
        return "ok"

    def _ticker_health_status(self, *, status: str, source: str) -> str:
        if status == "ok" and source == "binance_ws_all_ticker":
            return "primary"
        if status == "ok":
            return f"{source}_ok"
        return status

    def _record_ws_health_sample(self, *, healthy: bool) -> float:
        now = time.monotonic()
        sample_seconds = max(0.0, now - self._last_ws_health_sample_at)
        self._last_ws_health_sample_at = now
        self._ws_health_observed_seconds += sample_seconds
        if healthy:
            self._ws_health_healthy_seconds += sample_seconds
        if self._ws_health_observed_seconds <= 0.0:
            return 0.0
        return self._ws_health_healthy_seconds / self._ws_health_observed_seconds

    def _current_ws_health_ratio(self) -> float:
        if self._ws_health_observed_seconds <= 0.0:
            return 0.0
        return self._ws_health_healthy_seconds / self._ws_health_observed_seconds

    def _record_scheduler_cycle_seconds(self, cycle_seconds: float) -> None:
        if not math.isfinite(cycle_seconds) or cycle_seconds < 0.0:
            return
        alpha = DANGER_ADAPTIVE_COLD_COVERAGE_CYCLE_EWMA_ALPHA
        if not math.isfinite(self._scheduler_cycle_seconds_ewma):
            self._scheduler_cycle_seconds_ewma = cycle_seconds
            return
        self._scheduler_cycle_seconds_ewma = (
            alpha * cycle_seconds + (1.0 - alpha) * self._scheduler_cycle_seconds_ewma
        )

    def _record_cold_coverage_pressure_sample(self) -> None:
        network_pressure = self._linear_ramp(
            float(self._cycle_aggtrade_network_calls),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_NETWORK_CALLS_HIGH),
        )
        fetched_ms_pressure = self._linear_ramp(
            float(self._cycle_aggtrade_rest_fetched_ms),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_REST_FETCHED_MS_HIGH),
        )
        pending_count = int(self._cycle_ws_aggtrade_coverage_pending) + int(self._cycle_aggtrade_gap_prefetch_pending)
        pending_pressure = self._linear_ramp(
            float(pending_count),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_PENDING_GAPS_HIGH),
        )
        pressure = max(0.0, min(1.0, max(network_pressure, fetched_ms_pressure, pending_pressure)))
        alpha = DANGER_ADAPTIVE_COLD_COVERAGE_PRESSURE_EWMA_ALPHA
        if not math.isfinite(self._cold_coverage_pressure_ewma):
            self._cold_coverage_pressure_ewma = pressure
            return
        self._cold_coverage_pressure_ewma = (
            alpha * pressure + (1.0 - alpha) * self._cold_coverage_pressure_ewma
        )

    @staticmethod
    def _linear_ramp(value: float, low: float, high: float) -> float:
        if not math.isfinite(value):
            return 0.0
        if high <= low:
            return 1.0 if value >= high else 0.0
        if value <= low:
            return 0.0
        if value >= high:
            return 1.0
        return (value - low) / (high - low)

    def _current_latency_sla_backlog_status(self, *, now_ms: int) -> LiveLatencySlaStatus:
        with self._state_lock:
            active_by_key: dict[str, str] = {}
            for position in self._open_positions.values():
                active_by_key[_position_symbol_key(position.signal.symbol)] = position.signal.symbol
            for state in self._active_symbols.values():
                active_by_key[_position_symbol_key(state.symbol)] = state.symbol
            radar_by_key = {
                _position_symbol_key(watch.symbol): watch.symbol
                for watch in self._ticker_radar_watch.values()
            }
        return self._latency_sla_status(
            now_ms=now_ms,
            active_symbols=tuple(active_by_key.values()),
            radar_symbols=tuple(radar_by_key.values()),
        )

    def _latency_sla_status(
        self,
        *,
        now_ms: int,
        active_symbols: tuple[str, ...] | list[str],
        radar_symbols: tuple[str, ...] | list[str],
    ) -> LiveLatencySlaStatus:
        threshold_seconds = float(self.config.latency_sla_due_scan_p95_seconds)
        min_due_samples = max(1, int(self.config.latency_sla_min_due_samples))
        if not self.config.latency_sla_controller_enabled:
            return LiveLatencySlaStatus(
                enabled=False,
                status="disabled",
                optional_scans_allowed=True,
                due_scan_p95_seconds=None,
                due_scan_max_seconds=None,
                due_scan_samples=0,
                threshold_seconds=threshold_seconds,
                min_due_samples=min_due_samples,
                reason="controller_disabled",
            )
        symbols_by_key: dict[str, str] = {}
        for symbol in [*active_symbols, *radar_symbols]:
            symbols_by_key[_position_symbol_key(symbol)] = symbol
        latencies: list[float] = []
        for symbol in symbols_by_key.values():
            latencies.extend(self._due_signal_scan_latency_seconds(symbol, now_ms=now_ms))
        sample_count = len(latencies)
        if sample_count < min_due_samples:
            return LiveLatencySlaStatus(
                enabled=True,
                status="insufficient_due_samples",
                optional_scans_allowed=True,
                due_scan_p95_seconds=None,
                due_scan_max_seconds=max(latencies) if latencies else None,
                due_scan_samples=sample_count,
                threshold_seconds=threshold_seconds,
                min_due_samples=min_due_samples,
                reason="not_enough_active_or_radar_due_scans",
            )
        p95_seconds = _percentile(latencies, 0.95)
        max_seconds = max(latencies)
        if p95_seconds > threshold_seconds:
            return LiveLatencySlaStatus(
                enabled=True,
                status="breached",
                optional_scans_allowed=False,
                due_scan_p95_seconds=p95_seconds,
                due_scan_max_seconds=max_seconds,
                due_scan_samples=sample_count,
                threshold_seconds=threshold_seconds,
                min_due_samples=min_due_samples,
                reason=LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON,
            )
        return LiveLatencySlaStatus(
            enabled=True,
            status="ok",
            optional_scans_allowed=True,
            due_scan_p95_seconds=p95_seconds,
            due_scan_max_seconds=max_seconds,
            due_scan_samples=sample_count,
            threshold_seconds=threshold_seconds,
            min_due_samples=min_due_samples,
            reason="within_sla",
        )

    def _due_signal_scan_latency_seconds(self, symbol: str, *, now_ms: int) -> list[float]:
        symbol_key = _position_symbol_key(symbol)
        latencies: list[float] = []
        with self._state_lock:
            for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
                closed_timestamp_ms = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
                scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
                dependency_key = (symbol_key, levels_timeframe.value, entry_timeframe.value, int(closed_timestamp_ms))
                if self._last_signal_scan_closed_at.get(scan_key) == closed_timestamp_ms:
                    continue
                if self._dependency_retry_cooldown_active_locked(dependency_key, now_ms=now_ms):
                    continue
                candle_close_ms = closed_timestamp_ms + int(entry_timeframe.to_milliseconds())
                latencies.append(max(0.0, (int(now_ms) - candle_close_ms) / 1000.0))
        return latencies

    def _adaptive_cold_coverage_slots(
        self,
        *,
        base_slots: int,
        inactive_slots_source: str,
        health_ratio: float,
        active_due_count: int,
        active_waiting_count: int,
        position_blocked: bool,
    ) -> tuple[int, str, str, float, float, float, float, float, float, float, int]:
        base_slots = max(0, int(base_slots))
        cycle_seconds_ewma = max(0.0, float(self._scheduler_cycle_seconds_ewma))
        pressure_ewma = max(0.0, min(1.0, float(self._cold_coverage_pressure_ewma)))
        if not self._inactive_slots_are_precise_cold_coverage(inactive_slots_source) or base_slots <= 0:
            return base_slots, inactive_slots_source, "", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, base_slots
        max_slots = max(0, DANGER_ADAPTIVE_COLD_COVERAGE_MAX_SLOTS_PER_CYCLE)
        if self.config.inactive_scan_slots_per_cycle is not None:
            # Explicit operator value remains DANGER cold coverage but is treated as a hard cap.
            max_slots = min(max_slots, base_slots)
        if max_slots <= 0:
            return 0, COLD_COVERAGE_GATED_OFF_SOURCE, "configured_zero", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, max_slots
        if position_blocked:
            return 0, COLD_COVERAGE_GATED_OFF_SOURCE, "open_or_opening_position_present", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, max_slots
        if active_due_count > 0:
            return 0, COLD_COVERAGE_GATED_OFF_SOURCE, "active_due_symbols_present", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, max_slots
        health_factor = self._linear_ramp(
            health_ratio,
            DANGER_ADAPTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO,
            DANGER_ADAPTIVE_COLD_COVERAGE_FULL_WS_HEALTH_RATIO,
        )
        speed_factor = 1.0 - self._linear_ramp(
            cycle_seconds_ewma,
            DANGER_ADAPTIVE_COLD_COVERAGE_FAST_CYCLE_SECONDS,
            DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS,
        )
        active_factor = 1.0 - self._linear_ramp(
            float(active_waiting_count),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_ACTIVE_WAITING_SOFT_CAP),
        )
        load_factor = 1.0 - pressure_ewma
        adaptive_score = max(0.0, min(1.0, health_factor * speed_factor * active_factor * load_factor))
        if adaptive_score < DANGER_ADAPTIVE_COLD_COVERAGE_MIN_SCORE:
            reason = "adaptive_score_below_threshold"
            if health_factor <= 0.0:
                reason = "ws_health_below_adaptive_min"
            elif speed_factor <= 0.0:
                reason = "heartbeat_too_slow"
            elif active_factor <= 0.0:
                reason = "active_waiting_soft_cap_reached"
            elif load_factor <= 0.0:
                reason = "rest_or_cache_pressure_high"
            return (
                0,
                COLD_COVERAGE_GATED_OFF_SOURCE,
                reason,
                adaptive_score,
                health_factor,
                speed_factor,
                active_factor,
                load_factor,
                pressure_ewma,
                cycle_seconds_ewma,
                max_slots,
            )
        slots = max(1, int(math.ceil(float(max_slots) * adaptive_score)))
        slots = min(max_slots, slots)
        return (
            slots,
            inactive_slots_source,
            "",
            adaptive_score,
            health_factor,
            speed_factor,
            active_factor,
            load_factor,
            pressure_ewma,
            cycle_seconds_ewma,
            max_slots,
        )

    def _default_inactive_scan_slots(self) -> tuple[int, str]:
        if self.config.inactive_scan_slots_per_cycle is not None:
            return max(0, int(self.config.inactive_scan_slots_per_cycle)), EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE
        if self.config.ticker_radar_enabled and _live_config_has_subminute_entry_pairs(self.config):
            return DANGER_DEFAULT_INACTIVE_COLD_COVERAGE_SLOTS_PER_CYCLE, DANGER_INACTIVE_COLD_COVERAGE_SOURCE
        return max(0, int(self.config.symbol_batch_size)), "legacy_symbol_batch_size"

    @staticmethod
    def _inactive_slots_are_precise_cold_coverage(source: str) -> bool:
        return source in {DANGER_INACTIVE_COLD_COVERAGE_SOURCE, EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE}

    def _max_due_signal_scan_latency_seconds(self, symbol: str, *, now_ms: int) -> float:
        latencies = self._due_signal_scan_latency_seconds(symbol, now_ms=now_ms)
        return max(latencies) if latencies else 0.0

    def _control_candidate_queue_pressure(
        self,
        *,
        now_ms: int,
        active_keys: set[str],
        latency_sla: LiveLatencySlaStatus,
    ) -> LiveCandidateQueuePressureStats:
        with self._state_lock:
            radar_items = list(self._ticker_radar_watch.values())
            warm_items = list(self._warm_watch.values())
            opening_keys = set(self._opening_symbols)
        radar_before = len(radar_items)
        warm_before = len(warm_items)
        if not radar_items and not warm_items:
            return LiveCandidateQueuePressureStats(status="empty", reason="no_radar_or_warm_candidates")

        ranked: list[tuple[float, int, str, str]] = []
        for item in radar_items:
            ranked.append((float(item.score), int(item.updated_at_ms), "radar", _position_symbol_key(item.symbol)))
        for item in warm_items:
            ranked.append((float(item.score), int(item.updated_at_ms), "warm", _position_symbol_key(item.symbol)))
        ranked.sort(reverse=True)
        top_score = ranked[0][0] if ranked else None
        actionable_sample_count = sum(
            1
            for _, _, _, symbol_key in ranked
            if symbol_key not in active_keys
            and symbol_key not in opening_keys
            and not self._symbol_in_stop_cooldown(symbol_key)
        )

        pressure = (
            not latency_sla.optional_scans_allowed
            or radar_before > CANDIDATE_QUEUE_PRESSURE_MAX_RADAR
            or warm_before > CANDIDATE_QUEUE_PRESSURE_MAX_WARM
        )
        if not pressure:
            return LiveCandidateQueuePressureStats(
                status="ok",
                reason="within_queue_pressure_limits",
                radar_total_before=radar_before,
                radar_total_after=radar_before,
                warm_total_before=warm_before,
                warm_total_after=warm_before,
                actionable_sample_count=actionable_sample_count,
                top_score=top_score,
            )

        keep_keys = {symbol_key for _, _, _, symbol_key in ranked[: max(1, CANDIDATE_QUEUE_PRESSURE_MIN_KEEP)]}
        stale_latency_seconds = max(
            float(self.config.max_signal_age_ms) / 1000.0,
            float(self.config.latency_sla_due_scan_p95_seconds) * CANDIDATE_QUEUE_PRESSURE_BACKLOG_STALE_FACTOR,
        )
        radar_drop: list[tuple[LiveTickerRadarWatch, str, str]] = []
        warm_drop: list[tuple[LiveWarmWatch, str, str]] = []

        def should_drop(kind: str, symbol: str, symbol_key: str, rank_index: int) -> tuple[bool, str, str]:
            if symbol_key in active_keys or symbol_key in opening_keys or self._symbol_in_stop_cooldown(symbol):
                return False, "", ""
            if symbol_key in keep_keys:
                return False, "", ""
            max_due_latency = self._max_due_signal_scan_latency_seconds(symbol, now_ms=now_ms)
            if max_due_latency > stale_latency_seconds:
                return True, "candidate_expired_backlog_stale", (
                    f"due_scan_latency_seconds>{stale_latency_seconds:.3f}"
                )
            if kind == "radar" and rank_index >= CANDIDATE_QUEUE_PRESSURE_MAX_RADAR:
                return True, "candidate_dropped_latency_pressure", "radar_queue_over_pressure_cap"
            if kind == "warm" and rank_index >= CANDIDATE_QUEUE_PRESSURE_MAX_WARM:
                return True, "candidate_dropped_latency_pressure", "warm_queue_over_pressure_cap"
            return False, "", ""

        radar_ranked = sorted(radar_items, key=lambda item: (float(item.score), int(item.updated_at_ms), item.symbol), reverse=True)
        warm_ranked = sorted(warm_items, key=lambda item: (float(item.score), int(item.updated_at_ms), item.symbol), reverse=True)
        for index, item in enumerate(radar_ranked):
            key = _position_symbol_key(item.symbol)
            drop, event_name, reason = should_drop("radar", item.symbol, key, index)
            if drop:
                radar_drop.append((item, event_name, reason))
        for index, item in enumerate(warm_ranked):
            key = _position_symbol_key(item.symbol)
            drop, event_name, reason = should_drop("warm", item.symbol, key, index)
            if drop:
                warm_drop.append((item, event_name, reason))

        with self._state_lock:
            for item, _, _ in radar_drop:
                self._ticker_radar_watch.pop(_position_symbol_key(item.symbol), None)
            for item, _, _ in warm_drop:
                self._warm_watch.pop(_position_symbol_key(item.symbol), None)
            radar_after = len(self._ticker_radar_watch)
            warm_after = len(self._warm_watch)

        dropped_pressure = 0
        expired_backlog_stale = 0
        for item, event_name, reason in radar_drop:
            if event_name == "candidate_expired_backlog_stale":
                expired_backlog_stale += 1
            else:
                dropped_pressure += 1
            self.artifacts.append_event(
                event_name,
                item.symbol,
                {
                    "candidate_source": "ticker_radar",
                    "reason": reason,
                    "score": item.score,
                    "updated_at_ms": item.updated_at_ms,
                    "expires_at_ms": item.expires_at_ms,
                    "price_delta_pct": item.price_delta_pct,
                    "quote_volume_delta": item.quote_volume_delta,
                    "quote_volume_delta_ratio": item.quote_volume_delta_ratio if item.quote_volume_delta_ratio is not None else "",
                    "trade_count_delta": item.trade_count_delta if item.trade_count_delta is not None else "",
                    "trade_count_delta_ratio": item.trade_count_delta_ratio if item.trade_count_delta_ratio is not None else "",
                    "promotion_source": item.promotion_source,
                    **latency_sla.event_payload(),
                },
            )
        for item, event_name, reason in warm_drop:
            if event_name == "candidate_expired_backlog_stale":
                expired_backlog_stale += 1
            else:
                dropped_pressure += 1
            self.artifacts.append_event(
                event_name,
                item.symbol,
                {
                    "candidate_source": "warm_watch",
                    "reason": reason,
                    "score": item.score,
                    "observations": item.observations,
                    "first_seen_ms": item.first_seen_ms,
                    "updated_at_ms": item.updated_at_ms,
                    "expires_at_ms": item.expires_at_ms,
                    "age_ms": max(0, int(now_ms) - int(item.first_seen_ms)),
                    "price_delta_pct": item.price_delta_pct,
                    "quote_volume_delta": item.quote_volume_delta,
                    "quote_volume_delta_ratio": item.quote_volume_delta_ratio if item.quote_volume_delta_ratio is not None else "",
                    "trade_count_delta": item.trade_count_delta if item.trade_count_delta is not None else "",
                    "trade_count_delta_ratio": item.trade_count_delta_ratio if item.trade_count_delta_ratio is not None else "",
                    "promotion_source": item.promotion_source,
                    **latency_sla.event_payload(),
                },
            )

        status = "trimmed" if dropped_pressure or expired_backlog_stale else "pressure_no_drop"
        reason = latency_sla.reason if not latency_sla.optional_scans_allowed else "queue_size_pressure"
        return LiveCandidateQueuePressureStats(
            status=status,
            reason=reason,
            radar_total_before=radar_before,
            radar_total_after=radar_after,
            warm_total_before=warm_before,
            warm_total_after=warm_after,
            actionable_sample_count=actionable_sample_count,
            dropped_pressure_count=dropped_pressure,
            expired_backlog_stale_count=expired_backlog_stale,
            top_score=top_score,
        )

    def _adaptive_precise_scan_budget(
        self,
        *,
        active_due_count: int,
        active_waiting_count: int,
        latency_sla: LiveLatencySlaStatus,
        queue_pressure: LiveCandidateQueuePressureStats,
    ) -> LiveAdaptivePreciseBudget:
        """Return a reviewed precise-scan cap for this cycle.

        Active/opening symbols are never dropped by this cap. The cap only limits radar/cold precise
        scans when backlog or runtime pressure says that scanning every candidate would make signals stale.
        """
        manual_cap = self.config.max_precise_scan_symbols_per_cycle
        manual_limit = int(manual_cap) if manual_cap is not None else None
        cycle_seconds_ewma = max(0.0, float(self._scheduler_cycle_seconds_ewma))
        pressure_ewma = max(0.0, min(1.0, float(self._cold_coverage_pressure_ewma)))
        radar_batch_limit = max(0, int(self.config.ticker_radar_watch_batch_size))
        if radar_batch_limit <= 0:
            limit = manual_limit
            return LiveAdaptivePreciseBudget(
                status="radar_disabled",
                reason="ticker_radar_watch_batch_size_zero",
                limit=limit,
                radar_slots=0,
                active_due_count=int(active_due_count),
                active_waiting_count=int(active_waiting_count),
                manual_cap=manual_limit,
                cycle_seconds_ewma=cycle_seconds_ewma,
                pressure_ewma=pressure_ewma,
            )

        radar_slots: int | None = None
        reason = "normal_no_adaptive_cap"
        status = "uncapped"
        if not latency_sla.optional_scans_allowed:
            radar_slots = min(radar_batch_limit, ADAPTIVE_PRECISE_BUDGET_BREACHED_RADAR_SLOTS)
            reason = latency_sla.reason or LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON
            status = "breached"
        elif queue_pressure.status in {"trimmed", "pressure_no_drop"}:
            radar_slots = min(radar_batch_limit, ADAPTIVE_PRECISE_BUDGET_PRESSURE_RADAR_SLOTS)
            reason = queue_pressure.reason or "candidate_queue_pressure"
            status = "queue_pressure"
        elif active_due_count > 0 or active_waiting_count > 0:
            radar_slots = min(radar_batch_limit, ADAPTIVE_PRECISE_BUDGET_ACTIVE_RADAR_SLOTS)
            reason = "active_symbols_need_priority"
            status = "active_priority"
        elif cycle_seconds_ewma > DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS or pressure_ewma >= 0.75:
            radar_slots = min(radar_batch_limit, ADAPTIVE_PRECISE_BUDGET_SLOW_CYCLE_RADAR_SLOTS)
            reason = "scheduler_or_rest_pressure_high"
            status = "runtime_pressure"

        adaptive_limit = None if radar_slots is None else int(active_due_count) + max(0, int(radar_slots))
        if manual_limit is None:
            limit = adaptive_limit
        elif adaptive_limit is None:
            limit = max(int(active_due_count), int(manual_limit))
            if active_due_count > manual_limit:
                reason = f"active_due_over_manual_cap:{manual_limit}"
                status = "manual_active_priority"
        else:
            # Keep a reviewed operator cap as a hard upper bound, but never use it to drop active symbols.
            limit = min(manual_limit, adaptive_limit)
            limit = max(int(active_due_count), int(limit))
            radar_slots = max(0, int(limit) - int(active_due_count))
            if limit < adaptive_limit:
                reason = f"manual_cap:{manual_limit};{reason}"
                status = f"manual_{status}"
        return LiveAdaptivePreciseBudget(
            status=status,
            reason=reason,
            limit=limit,
            radar_slots=radar_slots,
            active_due_count=int(active_due_count),
            active_waiting_count=int(active_waiting_count),
            manual_cap=manual_limit,
            cycle_seconds_ewma=cycle_seconds_ewma,
            pressure_ewma=pressure_ewma,
        )

    def _select_next_symbol_batch(self, symbols: list[str]) -> LiveSymbolBatchSelection:
        now_ms = int(time.time() * 1000)
        self._prune_dependency_retry_cooldowns(now_ms=now_ms)
        inactive_cursor_before = self._inactive_cursor
        batch_full_cycle = self._symbol_universe_cycle_index
        batch_in_full_cycle = self._symbol_universe_batch_index + 1
        self._current_symbol_universe_cycle_index = batch_full_cycle
        self._current_symbol_universe_batch_index = batch_in_full_cycle
        active_due, active_waiting = self._active_symbol_batch(now_ms=now_ms)
        active_keys = {_position_symbol_key(symbol) for symbol in [*active_due, *active_waiting]}
        pre_pressure_sla = self._current_latency_sla_backlog_status(now_ms=now_ms)
        queue_pressure = self._control_candidate_queue_pressure(
            now_ms=now_ms,
            active_keys=active_keys,
            latency_sla=pre_pressure_sla,
        )
        precise_budget = self._adaptive_precise_scan_budget(
            active_due_count=len(active_due),
            active_waiting_count=len(active_waiting),
            latency_sla=pre_pressure_sla,
            queue_pressure=queue_pressure,
        )
        precise_budget_remaining = None
        if precise_budget.limit is not None:
            precise_budget_remaining = max(0, int(precise_budget.limit) - len(active_due))
        radar_due, radar_waiting = self._ticker_radar_batch(
            now_ms=now_ms,
            excluded_keys=active_keys,
            max_due=precise_budget_remaining,
        )
        radar_due_keys = {_position_symbol_key(symbol) for symbol in radar_due}
        radar_waiting_keys = {_position_symbol_key(symbol) for symbol in radar_waiting}
        warm_watch_waiting = self._warm_watch_waiting_symbols(
            now_ms=now_ms,
            excluded_keys=active_keys | radar_due_keys | radar_waiting_keys,
        )
        latency_sla = self._latency_sla_status(
            now_ms=now_ms,
            active_symbols=tuple(active_due),
            radar_symbols=tuple([*radar_due, *radar_waiting]),
        )
        warm_watch_keys = {_position_symbol_key(symbol) for symbol in warm_watch_waiting}
        base_inactive_slots, inactive_slots_source = self._default_inactive_scan_slots()
        cold_coverage_health_ratio = self._current_ws_health_ratio()
        cold_coverage_gate_reason = ""
        active_blocked = bool(active_due)
        with self._state_lock:
            position_blocked = bool(self._open_positions or self._opening_symbols)
        precise_budget_remaining_after_radar = None
        if precise_budget.limit is not None:
            precise_budget_remaining_after_radar = max(
                0,
                int(precise_budget.limit) - len(active_due) - len(radar_due),
            )
        if self.config.inactive_scan_slots_per_cycle is None and inactive_slots_source == "legacy_symbol_batch_size":
            inactive_slots = max(0, base_inactive_slots - len(active_due))
            cold_coverage_adaptive_score = 0.0
            cold_coverage_health_factor = 0.0
            cold_coverage_speed_factor = 0.0
            cold_coverage_active_factor = 0.0
            cold_coverage_load_factor = 0.0
            cold_coverage_pressure_ewma = self._cold_coverage_pressure_ewma
            cold_coverage_cycle_seconds_ewma = self._scheduler_cycle_seconds_ewma
            cold_coverage_max_slots = max(0, inactive_slots)
        else:
            (
                inactive_slots,
                inactive_slots_source,
                cold_coverage_gate_reason,
                cold_coverage_adaptive_score,
                cold_coverage_health_factor,
                cold_coverage_speed_factor,
                cold_coverage_active_factor,
                cold_coverage_load_factor,
                cold_coverage_pressure_ewma,
                cold_coverage_cycle_seconds_ewma,
                cold_coverage_max_slots,
            ) = self._adaptive_cold_coverage_slots(
                base_slots=base_inactive_slots,
                inactive_slots_source=inactive_slots_source,
                health_ratio=cold_coverage_health_ratio,
                active_due_count=len(active_due),
                active_waiting_count=len(active_waiting),
                position_blocked=position_blocked,
            )
        if self._inactive_slots_are_precise_cold_coverage(inactive_slots_source) and precise_budget_remaining_after_radar is not None:
            inactive_slots_before_budget = inactive_slots
            inactive_slots = min(inactive_slots, precise_budget_remaining_after_radar)
            if inactive_slots_before_budget > 0 and inactive_slots <= 0:
                inactive_slots_source = COLD_COVERAGE_GATED_OFF_SOURCE
                cold_coverage_gate_reason = "precise_scan_budget_exhausted"
        if (
            self._inactive_slots_are_precise_cold_coverage(inactive_slots_source)
            and inactive_slots > 0
            and not latency_sla.optional_scans_allowed
        ):
            inactive_slots = 0
            inactive_slots_source = COLD_COVERAGE_GATED_OFF_SOURCE
            cold_coverage_gate_reason = latency_sla.reason or LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON
        inactive: list[str] = []
        attempts = 0
        while len(inactive) < inactive_slots and attempts < len(symbols):
            symbol = symbols[self._inactive_cursor % len(symbols)]
            self._inactive_cursor += 1
            attempts += 1
            symbol_key = _position_symbol_key(symbol)
            with self._state_lock:
                symbol_is_opening = symbol_key in self._opening_symbols
            if (
                symbol_key in active_keys
                or symbol_key in radar_due_keys
                or symbol_key in radar_waiting_keys
                or symbol_key in warm_watch_keys
                or symbol_is_opening
                or self._symbol_in_stop_cooldown(symbol)
            ):
                continue
            inactive.append(symbol)
        batch = [*active_due, *radar_due, *inactive]
        batch_scan_modes: dict[str, str] = {}
        for symbol in active_due:
            batch_scan_modes[_position_symbol_key(symbol)] = "precise_active"
        for symbol in radar_due:
            batch_scan_modes[_position_symbol_key(symbol)] = "precise_ticker_radar"
        inactive_scan_mode = (
            "precise_DANGER_cold_coverage"
            if inactive_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE
            else (
                "precise_explicit_cold_coverage"
                if inactive_slots_source == EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE
                else "inactive_deferred_subminute"
            )
        )
        for symbol in inactive:
            batch_scan_modes[_position_symbol_key(symbol)] = inactive_scan_mode
        if symbols and self._inactive_cursor // len(symbols) > inactive_cursor_before // len(symbols):
            now_monotonic = time.monotonic()
            self._last_symbol_universe_cycle_seconds = now_monotonic - self._symbol_universe_scan_started_at
            self._symbol_universe_scan_started_at = now_monotonic
            completed_cycles = max(
                1,
                self._inactive_cursor // len(symbols) - inactive_cursor_before // len(symbols),
            )
            self._symbol_universe_cycle_index += completed_cycles
            self._symbol_universe_batch_index = 0
        else:
            self._symbol_universe_batch_index = batch_in_full_cycle
        if inactive_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE:
            scheduler_source = "DANGER_ws_event_driven_plus_precise_cold_coverage"
        elif inactive_slots_source == EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE:
            scheduler_source = "configured_precise_cold_coverage_scheduler"
        elif inactive_slots_source == COLD_COVERAGE_GATED_OFF_SOURCE:
            scheduler_source = "ws_event_driven_scheduler_cold_coverage_gated"
        elif inactive_slots_source == "default_ws_event_driven_subminute":
            scheduler_source = "ws_event_driven_scheduler"
        else:
            scheduler_source = "legacy_rest_round_robin_scheduler"
        return LiveSymbolBatchSelection(
            scheduler_source=scheduler_source,
            active_due=tuple(active_due),
            active_waiting=tuple(active_waiting),
            radar_due=tuple(radar_due),
            radar_waiting=tuple(radar_waiting),
            warm_watch_waiting=tuple(warm_watch_waiting),
            latency_sla_status=latency_sla.status,
            latency_sla_optional_scans_allowed=latency_sla.optional_scans_allowed,
            latency_sla_due_scan_p95_seconds=latency_sla.due_scan_p95_seconds,
            latency_sla_due_scan_max_seconds=latency_sla.due_scan_max_seconds,
            latency_sla_due_scan_samples=latency_sla.due_scan_samples,
            latency_sla_threshold_seconds=latency_sla.threshold_seconds,
            latency_sla_min_due_samples=latency_sla.min_due_samples,
            latency_sla_reason=latency_sla.reason,
            candidate_queue_status=queue_pressure.status,
            candidate_queue_reason=queue_pressure.reason,
            candidate_queue_radar_total_before=queue_pressure.radar_total_before,
            candidate_queue_radar_total_after=queue_pressure.radar_total_after,
            candidate_queue_warm_total_before=queue_pressure.warm_total_before,
            candidate_queue_warm_total_after=queue_pressure.warm_total_after,
            candidate_queue_actionable_sample_count=queue_pressure.actionable_sample_count,
            candidate_queue_dropped_pressure_count=queue_pressure.dropped_pressure_count,
            candidate_queue_expired_backlog_stale_count=queue_pressure.expired_backlog_stale_count,
            candidate_queue_top_score=queue_pressure.top_score,
            adaptive_precise_budget_status=precise_budget.status,
            adaptive_precise_budget_reason=precise_budget.reason,
            adaptive_precise_budget_limit=precise_budget.limit,
            adaptive_precise_budget_radar_slots=precise_budget.radar_slots,
            adaptive_precise_budget_active_due_count=precise_budget.active_due_count,
            adaptive_precise_budget_active_waiting_count=precise_budget.active_waiting_count,
            adaptive_precise_budget_manual_cap=precise_budget.manual_cap,
            inactive=tuple(inactive),
            batch=tuple(batch),
            scan_modes=batch_scan_modes,
            inactive_cursor_before=inactive_cursor_before,
            inactive_cursor_after=self._inactive_cursor,
            batch_in_full_cycle=batch_in_full_cycle,
            full_symbol_cycle=batch_full_cycle,
            precise_budget_remaining_after_active=precise_budget_remaining,
            precise_budget_remaining_after_radar=precise_budget_remaining_after_radar,
            inactive_scan_slots=inactive_slots,
            inactive_scan_slots_source=inactive_slots_source,
            inactive_cold_coverage_danger=(
                inactive_slots > 0 and inactive_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE
            ),
            inactive_cold_coverage_gate_reason=cold_coverage_gate_reason,
            inactive_cold_coverage_health_pct=cold_coverage_health_ratio * 100.0,
            inactive_cold_coverage_health_threshold_pct=DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0,
            inactive_cold_coverage_active_blocked=active_blocked,
            inactive_cold_coverage_position_blocked=position_blocked,
            inactive_cold_coverage_active_due_count=len(active_due),
            inactive_cold_coverage_active_waiting_count=len(active_waiting),
            inactive_cold_coverage_adaptive_score=cold_coverage_adaptive_score,
            inactive_cold_coverage_health_factor=cold_coverage_health_factor,
            inactive_cold_coverage_speed_factor=cold_coverage_speed_factor,
            inactive_cold_coverage_active_factor=cold_coverage_active_factor,
            inactive_cold_coverage_load_factor=cold_coverage_load_factor,
            inactive_cold_coverage_pressure_ewma=cold_coverage_pressure_ewma,
            inactive_cold_coverage_cycle_seconds_ewma=cold_coverage_cycle_seconds_ewma,
            inactive_cold_coverage_base_slots=max(0, int(base_inactive_slots)),
            inactive_cold_coverage_max_slots=max(0, int(cold_coverage_max_slots)),
        )

    def _active_symbol_batch(self, *, now_ms: int) -> tuple[list[str], list[str]]:
        with self._state_lock:
            expired = self._prune_active_symbols_locked(now_ms)
            symbols_by_key: dict[str, str] = {}
            for position in self._open_positions.values():
                symbols_by_key[_position_symbol_key(position.signal.symbol)] = position.signal.symbol
            for state in self._active_symbols.values():
                symbols_by_key[_position_symbol_key(state.symbol)] = state.symbol
            active_symbols = sorted(symbols_by_key.values())
        active_due: list[str] = []
        active_waiting: list[str] = []
        for symbol in active_symbols:
            if self._signal_scan_due_for_symbol(symbol, now_ms=now_ms):
                active_due.append(symbol)
            else:
                active_waiting.append(symbol)
        for state in expired:
            self.artifacts.append_event(
                "active_symbol_expired",
                state.symbol,
                {
                    "reason": state.reason,
                    "expires_at_ms": state.expires_at_ms,
                    "decision_timestamp_ms": state.decision_timestamp_ms if state.decision_timestamp_ms is not None else "",
                },
            )
        return active_due, active_waiting

    def _ticker_radar_batch(
        self,
        *,
        now_ms: int,
        excluded_keys: set[str],
        max_due: int | None = None,
    ) -> tuple[list[str], list[str]]:
        if not self.config.ticker_radar_enabled or self.config.ticker_radar_watch_batch_size <= 0:
            return [], []
        due_limit = self.config.ticker_radar_watch_batch_size if max_due is None else min(
            self.config.ticker_radar_watch_batch_size,
            max(0, int(max_due)),
        )
        with self._state_lock:
            expired = self._prune_ticker_radar_watch_locked(now_ms)
            watch_items = sorted(
                self._ticker_radar_watch.values(),
                key=lambda item: (item.score, item.updated_at_ms, item.symbol),
                reverse=True,
            )
        for item in expired:
            self.artifacts.append_event(
                "ticker_radar_watch_expired",
                item.symbol,
                {
                    "reason": item.reason,
                    "score": item.score,
                    "expires_at_ms": item.expires_at_ms,
                    "price_delta_pct": item.price_delta_pct,
                    "quote_volume_delta": item.quote_volume_delta,
                    "quote_volume_delta_ratio": item.quote_volume_delta_ratio if item.quote_volume_delta_ratio is not None else "",
                    "trade_count_delta": item.trade_count_delta if item.trade_count_delta is not None else "",
                    "trade_count_delta_ratio": item.trade_count_delta_ratio if item.trade_count_delta_ratio is not None else "",
                    "promotion_source": item.promotion_source,
                },
            )
        due: list[str] = []
        waiting: list[str] = []
        for item in watch_items:
            symbol_key = _position_symbol_key(item.symbol)
            with self._state_lock:
                symbol_is_opening = symbol_key in self._opening_symbols
            if symbol_key in excluded_keys or symbol_is_opening or self._symbol_in_stop_cooldown(item.symbol):
                continue
            if self._signal_scan_due_for_symbol(item.symbol, now_ms=now_ms):
                if len(due) < due_limit:
                    due.append(item.symbol)
                else:
                    waiting.append(item.symbol)
            else:
                waiting.append(item.symbol)
        return due, waiting

    def _warm_watch_waiting_symbols(self, *, now_ms: int, excluded_keys: set[str]) -> list[str]:
        with self._state_lock:
            expired = self._prune_warm_watch_locked(now_ms)
            warm_items = sorted(
                self._warm_watch.values(),
                key=lambda item: (item.updated_at_ms, item.score, item.symbol),
                reverse=True,
            )
        for item in expired:
            self.artifacts.append_event(
                "warm_watch_expired",
                item.symbol,
                {
                    "reason": item.reason,
                    "observations": item.observations,
                    "score": item.score,
                    "expires_at_ms": item.expires_at_ms,
                    "first_seen_ms": item.first_seen_ms,
                    "price_delta_pct": item.price_delta_pct,
                    "quote_volume_delta": item.quote_volume_delta,
                    "quote_volume_delta_ratio": item.quote_volume_delta_ratio if item.quote_volume_delta_ratio is not None else "",
                    "trade_count_delta": item.trade_count_delta if item.trade_count_delta is not None else "",
                    "trade_count_delta_ratio": item.trade_count_delta_ratio if item.trade_count_delta_ratio is not None else "",
                    "promotion_source": item.promotion_source,
                },
            )
        waiting: list[str] = []
        for item in warm_items:
            symbol_key = _position_symbol_key(item.symbol)
            with self._state_lock:
                symbol_is_opening = symbol_key in self._opening_symbols
            if symbol_key in excluded_keys or symbol_is_opening or self._symbol_in_stop_cooldown(item.symbol):
                continue
            waiting.append(item.symbol)
        return waiting

    def _maybe_process_live_top_growth_audit(self, symbols: list[str]) -> LiveTopGrowthAuditCycleStats:
        now_ms = int(time.time() * 1000)
        latency_sla = self._current_latency_sla_backlog_status(now_ms=now_ms)
        if not latency_sla.optional_scans_allowed:
            return LiveTopGrowthAuditCycleStats(
                enabled=True,
                status="skipped_latency_sla",
                reason=latency_sla.reason or LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON,
            )
        if latency_sla.status == "ok":
            max_symbols = int(DEFAULT_LIVE_TOP_GROWTH_IDLE_SYMBOLS_PER_CYCLE)
            max_seconds = float(DEFAULT_LIVE_TOP_GROWTH_IDLE_MAX_CYCLE_SECONDS)
        else:
            max_symbols = int(DEFAULT_LIVE_TOP_GROWTH_CONSERVATIVE_SYMBOLS_PER_CYCLE)
            max_seconds = float(DEFAULT_LIVE_TOP_GROWTH_CONSERVATIVE_MAX_CYCLE_SECONDS)
        if self._live_top_growth_task is None:
            period_start_ms = self._next_live_top_growth_period_start_ms(now_ms=now_ms)
            if period_start_ms is None:
                return LiveTopGrowthAuditCycleStats(enabled=True, status="idle", reason="no_closed_period_due")
            period_end_ms = period_start_ms + HOUR_MS
            task_symbols = tuple(symbols)
            if not task_symbols:
                return LiveTopGrowthAuditCycleStats(enabled=True, status="skipped", reason="empty_symbol_universe")
            self._live_top_growth_task = LiveTopGrowthAuditTask(
                period_start_ms=period_start_ms,
                period_end_ms=period_end_ms,
                snapshot_utc=datetime.now(UTC).isoformat(),
                symbols=task_symbols,
            )
            self.artifacts.append_event(
                "live_top_growth_audit_started",
                "__top_growth__",
                {
                    "period_start_ms": int(period_start_ms),
                    "period_end_ms": int(period_end_ms),
                    "symbols_total": len(task_symbols),
                    "threshold_pct": float(DEFAULT_LIVE_TOP_GROWTH_MIN_RETURN_PCT) * 100.0,
                    "limit": int(DEFAULT_LIVE_TOP_GROWTH_LIMIT),
                    "latency_sla_status": latency_sla.status,
                    "latency_sla_reason": latency_sla.reason,
                    "source": "live_incremental_closed_1h_exchange_candle",
                    "visibility_events_csv": str(self.artifacts.events_path),
                },
            )
        task = self._live_top_growth_task
        started = time.monotonic()
        processed = 0
        max_symbols = max(1, int(max_symbols))
        max_seconds = max(0.0, float(max_seconds))
        while task.cursor < len(task.symbols) and processed < max_symbols:
            if processed > 0 and max_seconds > 0.0 and time.monotonic() - started >= max_seconds:
                break
            symbol = task.symbols[task.cursor]
            task.cursor += 1
            status_row = _load_top_growth_symbol_row(
                exchange=self.exchange,
                symbol=symbol,
                period_start_ms=task.period_start_ms,
                period_end_ms=task.period_end_ms,
                snapshot_utc=task.snapshot_utc,
            )
            task.status_rows.append(status_row)
            candidate = _top_growth_candidate_from_status_row(
                status_row,
                threshold_fraction=float(DEFAULT_LIVE_TOP_GROWTH_MIN_RETURN_PCT),
            )
            if candidate is not None:
                task.candidates.append(candidate)
            processed += 1
        remaining = len(task.symbols) - task.cursor
        cycle_seconds = time.monotonic() - started
        if remaining > 0:
            return LiveTopGrowthAuditCycleStats(
                enabled=True,
                status="processing",
                reason="symbols_remaining",
                period_start_ms=task.period_start_ms,
                period_end_ms=task.period_end_ms,
                processed_count=processed,
                remaining_count=remaining,
                symbols_total=len(task.symbols),
                top_count=len(task.candidates),
                cycle_seconds=cycle_seconds,
            )
        top_rows = _rank_top_growth_candidates(task.candidates, limit=int(DEFAULT_LIVE_TOP_GROWTH_LIMIT))
        visibility_rows = _build_missed_pump_visibility_rows(
            top_rows=top_rows,
            period_start_ms=task.period_start_ms,
            period_end_ms=task.period_end_ms,
            visibility_events_csv=self.artifacts.events_path,
        )
        top_path, status_path, visibility_path = self.artifacts.write_top_growth_snapshot(
            period_start_ms=task.period_start_ms,
            period_end_ms=task.period_end_ms,
            snapshot_utc=task.snapshot_utc,
            top_rows=top_rows,
            status_rows=task.status_rows,
            visibility_rows=visibility_rows,
            symbols_total=len(task.symbols),
            threshold_pct=float(DEFAULT_LIVE_TOP_GROWTH_MIN_RETURN_PCT) * 100.0,
            limit=int(DEFAULT_LIVE_TOP_GROWTH_LIMIT),
        )
        ok_count = sum(1 for row in task.status_rows if row.get("status") == "ok")
        failed_count = len(task.status_rows) - ok_count
        self.artifacts.append_event(
            "live_top_growth_audit_saved",
            "__top_growth__",
            {
                "period_start_ms": int(task.period_start_ms),
                "period_end_ms": int(task.period_end_ms),
                "top_count": len(top_rows),
                "symbols_total": len(task.symbols),
                "ok_count": ok_count,
                "failed_count": failed_count,
                "top_file": str(top_path.relative_to(self.artifacts.root)),
                "status_file": str(status_path.relative_to(self.artifacts.root)),
                "visibility_file": str(visibility_path.relative_to(self.artifacts.root)),
                "visibility_events_csv": str(self.artifacts.events_path),
                "source": "live_incremental_closed_1h_exchange_candle",
            },
        )
        completed_start = task.period_start_ms
        self._live_top_growth_completed_periods.add(completed_start)
        self._live_top_growth_task = None
        return LiveTopGrowthAuditCycleStats(
            enabled=True,
            status="completed",
            reason="snapshot_saved",
            period_start_ms=completed_start,
            period_end_ms=completed_start + HOUR_MS,
            processed_count=processed,
            remaining_count=0,
            symbols_total=len(task.symbols),
            top_count=len(top_rows),
            cycle_seconds=cycle_seconds,
        )

    def _next_live_top_growth_period_start_ms(self, *, now_ms: int) -> int | None:
        latest_closed_start_ms = _previous_closed_hour_start_ms(now_ms)
        first_period_start_ms = (int(self._run_started_wall_ms) // HOUR_MS) * HOUR_MS
        period_start_ms = first_period_start_ms
        while period_start_ms <= latest_closed_start_ms:
            period_end_ms = period_start_ms + HOUR_MS
            if (
                period_end_ms > int(self._run_started_wall_ms)
                and period_start_ms not in self._live_top_growth_completed_periods
            ):
                return int(period_start_ms)
            period_start_ms += HOUR_MS
        return None

    def _write_session_top_growth_artifact_if_due(self, *, now_ms: int) -> None:
        if now_ms - self._last_session_top_growth_artifact_at_ms < LIVE_SESSION_TOP_ARTIFACT_INTERVAL_MS:
            return
        snapshot = self._session_top_tracker.snapshot(now_ms=now_ms)
        self.artifacts.append_session_top_growth_snapshot(snapshot)
        self._last_session_top_growth_artifact_at_ms = int(now_ms)

    def _maybe_update_ticker_radar(self, symbols: list[str]) -> LiveTickerRadarCycleStats:
        configured_source = self.ticker_snapshot_source.source_id
        if not self.config.ticker_radar_enabled:
            return LiveTickerRadarCycleStats(
                enabled=False,
                attempted=False,
                status="disabled",
                source=configured_source,
                reason="ticker_radar_disabled",
            )
        now_ms = int(time.time() * 1000)
        interval_ms = int(self.config.ticker_radar_interval_seconds * 1000.0)
        if now_ms - self._last_ticker_radar_at_ms < max(1, interval_ms):
            return LiveTickerRadarCycleStats(
                enabled=True,
                attempted=False,
                status="not_due",
                source=configured_source,
                reason="interval_not_elapsed",
            )
        self._last_ticker_radar_at_ms = now_ms
        try:
            snapshots, source, source_status, source_reason = self._fetch_ticker_radar_snapshots(symbols, stage="cycle")
        except Exception as exc:
            payload = {
                "source": configured_source,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:500],
                "required_for_inactive_subminute_gate": bool(self._ticker_radar_required_for_subminute_gate()),
            }
            self.artifacts.append_event("ticker_radar_failed", "__live__", payload)
            self._last_ticker_radar_health_status = "failed"
            if self._ticker_radar_required_for_subminute_gate():
                raise ExchangeConnectivityError(
                    "ticker radar source is required for inactive subminute discovery and is unavailable; "
                    f"source={configured_source}; error={type(exc).__name__}: {exc}"
                ) from exc
            return LiveTickerRadarCycleStats(
                enabled=True,
                attempted=True,
                status="failed",
                source=configured_source,
                symbols_total=len(symbols),
                reason=f"{type(exc).__name__}: {str(exc)[:240]}",
            )
        missing_count = sum(1 for snapshot in snapshots if snapshot.status != "ok")
        self._session_top_tracker.update_from_ticker_snapshots(
            snapshots,
            now_ms=now_ms,
            source=source,
            source_status=source_status,
            source_reason=source_reason,
        )
        self._write_session_top_growth_artifact_if_due(now_ms=now_ms)
        if snapshots and missing_count == len(snapshots):
            self.artifacts.append_event(
                "ticker_radar_snapshot_all_missing",
                "__live__",
                {
                    "source": source,
                    "source_status": source_status,
                    "source_reason": source_reason,
                    "symbols_total": len(snapshots),
                    "missing_count": missing_count,
                    "required_for_inactive_subminute_gate": bool(self._ticker_radar_required_for_subminute_gate()),
                },
            )
            self._last_ticker_radar_health_status = "all_missing"
            if self._ticker_radar_required_for_subminute_gate():
                raise ExchangeConnectivityError(
                    "ticker radar source returned no usable snapshots for inactive subminute discovery; "
                    f"source={source}; symbols_total={len(snapshots)}"
                )
            return LiveTickerRadarCycleStats(
                enabled=True,
                attempted=True,
                status="all_missing",
                source=source,
                symbols_total=len(snapshots),
                missing_count=missing_count,
                reason="all_ticker_snapshots_missing",
            )
        promotions = self._evaluate_ticker_radar_snapshots(snapshots, now_ms=now_ms)
        radar_candidates = promotions[: self.config.ticker_radar_max_promotions_per_cycle]
        promoted_count = 0
        warm_watch_marked_count = 0
        warm_watch_promoted_count = 0
        warm_watch_rejected_count = 0
        warm_watch_deferred_count = 0
        danger_flow_candidate_count = sum(
            1 for promotion in promotions if promotion.get("promotion_source") == DANGER_CHEAP_FLOW_RADAR_SOURCE
        )
        danger_flow_promoted_count = 0
        for promotion in radar_candidates:
            promotion_source = str(promotion.get("promotion_source") or "ticker_price_volume")
            action = self._mark_or_promote_warm_watch(
                promotion,
                now_ms=now_ms,
                promotion_source=promotion_source,
            )
            if action == "marked":
                warm_watch_marked_count += 1
            elif action == "promoted":
                promoted_count += 1
                warm_watch_promoted_count += 1
                if promotion_source == DANGER_CHEAP_FLOW_RADAR_SOURCE:
                    danger_flow_promoted_count += 1
            elif action == "rejected":
                warm_watch_rejected_count += 1
            elif action == "deferred":
                warm_watch_deferred_count += 1
        ok_count = len(snapshots) - missing_count
        snapshot_status = "ok" if ok_count > 0 else "all_missing"
        self.artifacts.append_event(
            "ticker_radar_snapshot",
            "__live__",
            {
                "symbols_total": len(snapshots),
                "ok_count": ok_count,
                "missing_count": missing_count,
                "promoted_count": promoted_count,
                "promotion_candidates_count": len(promotions),
                "warm_watch_marked_count": warm_watch_marked_count,
                "warm_watch_promoted_count": warm_watch_promoted_count,
                "warm_watch_rejected_count": warm_watch_rejected_count,
                "warm_watch_deferred_count": warm_watch_deferred_count,
                "warm_watch_enabled": bool(self.config.warm_watch_enabled),
                "danger_flow_radar_promoted_count": danger_flow_promoted_count,
                "danger_flow_radar_candidate_count": danger_flow_candidate_count,
                "danger_flow_radar_enabled": bool(self.config.danger_ticker_flow_radar_enabled),
                "interval_seconds": self.config.ticker_radar_interval_seconds,
                "watch_batch_size": self.config.ticker_radar_watch_batch_size,
                "source": source,
                "source_status": source_status,
                "source_reason": source_reason,
                "status": snapshot_status if source_status == "primary" else source_status,
                "required_for_inactive_subminute_gate": bool(self._ticker_radar_required_for_subminute_gate()),
            },
        )
        if snapshots and ok_count == 0 and self._ticker_radar_required_for_subminute_gate():
            self._last_ticker_radar_health_status = "all_missing"
            raise ExchangeConnectivityError(
                "ticker radar source returned no usable snapshots while inactive subminute discovery depends on it; "
                f"source={source}; missing={missing_count}/{len(snapshots)}"
            )
        self._last_ticker_radar_health_status = self._ticker_health_status(
            status=snapshot_status if source_status == "primary" else source_status,
            source=source,
        )
        return LiveTickerRadarCycleStats(
            enabled=True,
            attempted=True,
            status=snapshot_status if source_status == "primary" else source_status,
            source=source,
            symbols_total=len(snapshots),
            ok_count=ok_count,
            missing_count=missing_count,
            promoted_count=promoted_count,
            promotion_candidates_count=len(promotions),
            warm_watch_marked_count=warm_watch_marked_count,
            warm_watch_promoted_count=warm_watch_promoted_count,
            warm_watch_rejected_count=warm_watch_rejected_count,
            warm_watch_deferred_count=warm_watch_deferred_count,
            danger_flow_radar_promoted_count=danger_flow_promoted_count,
            danger_flow_radar_candidate_count=danger_flow_candidate_count,
            reason=source_reason,
        )


    def _prepump_warm_watch_score_for_symbol(self, symbol: str, *, now_ms: int) -> LivePrepumpWarmWatchScore:
        profile = self._prepump_warm_watch_profile
        if not profile.enabled:
            return LivePrepumpWarmWatchScore(status="disabled", reason=profile.reason)
        if not self.config.symbol_context_snapshot_enabled:
            return LivePrepumpWarmWatchScore(status="unavailable", reason="symbol_context_snapshot_disabled")
        if not self.config.timeframe_pairs:
            return LivePrepumpWarmWatchScore(status="unavailable", reason="no_timeframe_pairs")
        levels_timeframe, entry_timeframe = self.config.timeframe_pairs[0]
        snapshot = self._symbol_context_snapshots.get(
            self._symbol_context_snapshot_key(symbol, levels_timeframe, entry_timeframe)
        )
        if snapshot is None:
            return LivePrepumpWarmWatchScore(status="unavailable", reason="symbol_context_snapshot_missing")
        snapshot_age_ms = max(0, int(now_ms) - int(snapshot.snapshot_timestamp_ms))
        effective_fresh_ms = self._effective_symbol_context_snapshot_fresh_ms()
        if snapshot_age_ms > effective_fresh_ms:
            return LivePrepumpWarmWatchScore(status="unavailable", reason="symbol_context_snapshot_stale")
        if snapshot.status != "ok":
            return LivePrepumpWarmWatchScore(
                status="unavailable",
                reason=f"symbol_context_snapshot_{snapshot.status}:{snapshot.reason}",
            )
        if not snapshot.prepump_spot_features:
            return LivePrepumpWarmWatchScore(status="unavailable", reason="prepump_spot_features_missing")

        weighted_sum = 0.0
        weight_total = 0.0
        used = 0
        missing = 0
        contributions: list[tuple[float, str]] = []
        for rule in profile.rules:
            value = _safe_profile_float(snapshot.prepump_spot_features.get(rule.feature))
            if value is None:
                missing += 1
                continue
            denominator = abs(rule.runner_mean - rule.fader_mean)
            if denominator <= 0.0 or not math.isfinite(denominator):
                missing += 1
                continue
            midpoint = (rule.runner_mean + rule.fader_mean) / 2.0
            directional_distance = float(rule.direction) * ((value - midpoint) / denominator)
            bounded = max(-1.0, min(1.0, directional_distance))
            contribution = bounded * rule.weight
            weighted_sum += contribution
            weight_total += rule.weight
            used += 1
            contributions.append((abs(contribution), f"{rule.feature}={value:.6g}:{bounded:.3f}"))
        if used <= 0 or weight_total <= 0.0:
            return LivePrepumpWarmWatchScore(
                status="unavailable",
                reason="no_matching_profile_features_in_snapshot",
                features_missing=missing,
            )
        raw_score = weighted_sum / weight_total
        adjustment = raw_score * float(self.config.prepump_warm_watch_score_weight)
        contributions.sort(reverse=True)
        top_features = tuple(item for _, item in contributions[:3])
        return LivePrepumpWarmWatchScore(
            status="ok",
            reason="ok",
            raw_score=raw_score,
            score_adjustment=adjustment,
            features_used=used,
            features_missing=missing,
            top_features=top_features,
        )

    @staticmethod
    def _prepump_score_payload_from_promotion(promotion: dict[str, object]) -> dict[str, object]:
        top_features = promotion.get("prepump_warm_watch_top_features")
        if isinstance(top_features, (list, tuple)):
            top_features_value = ";".join(str(item) for item in top_features)
        else:
            top_features_value = str(top_features or "")
        return {
            "base_score": promotion.get("base_score", promotion.get("score", "")),
            "prepump_warm_watch_scoring_status": promotion.get("prepump_warm_watch_scoring_status", "not_evaluated"),
            "prepump_warm_watch_scoring_reason": promotion.get("prepump_warm_watch_scoring_reason", ""),
            "prepump_warm_watch_score": promotion.get("prepump_warm_watch_score", ""),
            "prepump_warm_watch_score_adjustment": promotion.get("prepump_warm_watch_score_adjustment", ""),
            "prepump_warm_watch_features_used": promotion.get("prepump_warm_watch_features_used", ""),
            "prepump_warm_watch_features_missing": promotion.get("prepump_warm_watch_features_missing", ""),
            "prepump_warm_watch_top_features": top_features_value,
            "prepump_warm_watch_scoring_contract": PREPUMP_WARM_WATCH_SCORING_CONTRACT,
        }


    def _evaluate_ticker_radar_snapshots(
        self,
        snapshots: list[ExchangeTickerSnapshot],
        *,
        now_ms: int,
    ) -> list[dict[str, object]]:
        promotions: list[dict[str, object]] = []
        for snapshot in snapshots:
            symbol_key = _position_symbol_key(snapshot.symbol)
            previous = self._ticker_radar_snapshots.get(symbol_key)
            if snapshot.status != "ok":
                self.artifacts.append_event(
                    "ticker_radar_missing_fields",
                    snapshot.symbol,
                    {
                        "status": snapshot.status,
                        "reason": snapshot.reason or "",
                        "last_price_source": snapshot.last_price_source,
                        "quote_volume_source": snapshot.quote_volume_source,
                        "trade_count_source": snapshot.trade_count_source,
                    },
                )
                self._ticker_radar_snapshots[symbol_key] = snapshot
                continue
            self._ticker_radar_snapshots[symbol_key] = snapshot
            if previous is None or previous.status != "ok":
                continue
            price_delta_pct = _safe_divide(
                float(snapshot.last_price or float("nan")) - float(previous.last_price or float("nan")),
                float(previous.last_price or float("nan")),
            )
            quote_volume_delta = float(snapshot.quote_volume_24h or 0.0) - float(previous.quote_volume_24h or 0.0)
            if quote_volume_delta > 0.0:
                quote_history = self._ticker_radar_quote_delta_history.setdefault(symbol_key, deque(maxlen=24))
                quote_baseline = _median_positive(list(quote_history))
                quote_volume_delta_ratio = _safe_divide(quote_volume_delta, quote_baseline) if quote_baseline is not None else None
                quote_history.append(quote_volume_delta)
            else:
                quote_volume_delta_ratio = None
            trade_count_delta: int | None = None
            trade_count_delta_ratio: float | None = None
            if snapshot.trade_count_24h is not None and previous.trade_count_24h is not None:
                trade_count_delta = int(snapshot.trade_count_24h) - int(previous.trade_count_24h)
                if trade_count_delta > 0:
                    trade_history = self._ticker_radar_trade_delta_history.setdefault(symbol_key, deque(maxlen=24))
                    trade_baseline = _median_positive(list(trade_history))
                    trade_count_delta_ratio = _safe_divide(float(trade_count_delta), trade_baseline) if trade_baseline is not None else None
                    trade_history.append(float(trade_count_delta))
            price_volume_candidate = True
            if not math.isfinite(price_delta_pct) or price_delta_pct < self.config.ticker_radar_min_price_delta_pct:
                price_volume_candidate = False
            if quote_volume_delta < self.config.ticker_radar_min_quote_volume_delta_usdt:
                price_volume_candidate = False
            if (
                quote_volume_delta_ratio is not None
                and quote_volume_delta_ratio < self.config.ticker_radar_min_quote_volume_delta_ratio
            ):
                price_volume_candidate = False
            flow_candidate = bool(self.config.danger_ticker_flow_radar_enabled)
            if not math.isfinite(price_delta_pct):
                flow_candidate = False
            if price_delta_pct < self.config.danger_ticker_flow_radar_min_price_delta_pct:
                flow_candidate = False
            if price_delta_pct >= self.config.danger_ticker_flow_radar_max_price_delta_pct:
                flow_candidate = False
            if quote_volume_delta < self.config.danger_ticker_flow_radar_min_quote_volume_delta_usdt:
                flow_candidate = False
            if trade_count_delta is None or trade_count_delta < self.config.danger_ticker_flow_radar_min_trade_count_delta:
                flow_candidate = False
            if (
                trade_count_delta_ratio is not None
                and trade_count_delta_ratio < self.config.danger_ticker_flow_radar_min_trade_count_delta_ratio
            ):
                flow_candidate = False
            if not price_volume_candidate and not flow_candidate:
                continue
            if price_volume_candidate:
                score = (
                    price_delta_pct * 100.0
                    + math.log1p(max(0.0, quote_volume_delta / max(1.0, self.config.ticker_radar_min_quote_volume_delta_usdt)))
                    + (math.log1p(quote_volume_delta_ratio) if quote_volume_delta_ratio is not None else 0.0)
                )
                promotion_source = "ticker_price_volume"
            else:
                trade_component = float(trade_count_delta or 0) / max(1.0, float(self.config.danger_ticker_flow_radar_min_trade_count_delta))
                score = (
                    math.log1p(max(0.0, quote_volume_delta / max(1.0, self.config.danger_ticker_flow_radar_min_quote_volume_delta_usdt)))
                    + math.log1p(max(0.0, trade_component))
                    + (math.log1p(trade_count_delta_ratio) if trade_count_delta_ratio is not None else 0.0)
                    + max(0.0, price_delta_pct) * 100.0
                )
                promotion_source = DANGER_CHEAP_FLOW_RADAR_SOURCE
            base_score = float(score)
            prepump_score = self._prepump_warm_watch_score_for_symbol(snapshot.symbol, now_ms=now_ms)
            score = base_score + float(prepump_score.score_adjustment)
            prepump_payload = prepump_score.event_payload()
            promotions.append(
                {
                    "symbol": snapshot.symbol,
                    "score": score,
                    "base_score": base_score,
                    "price_delta_pct": price_delta_pct,
                    "quote_volume_delta": quote_volume_delta,
                    "quote_volume_delta_ratio": quote_volume_delta_ratio,
                    "trade_count_delta": trade_count_delta,
                    "trade_count_delta_ratio": trade_count_delta_ratio,
                    "promotion_source": promotion_source,
                    "last_price_source": snapshot.last_price_source,
                    "quote_volume_source": snapshot.quote_volume_source,
                    "trade_count_source": snapshot.trade_count_source,
                    "now_ms": now_ms,
                    **prepump_payload,
                }
            )
        promotions.sort(key=lambda item: (float(item["score"]), str(item["symbol"])), reverse=True)
        return promotions

    def _mark_or_promote_warm_watch(
        self,
        promotion: dict[str, object],
        *,
        now_ms: int,
        promotion_source: str,
    ) -> str:
        symbol = str(promotion["symbol"])
        score = float(promotion["score"])
        base_score = _safe_profile_float(promotion.get("base_score"))
        if base_score is None:
            base_score = score
        prepump_payload = self._prepump_score_payload_from_promotion(promotion)
        prepump_score_value = _safe_profile_float(prepump_payload["prepump_warm_watch_score"])
        prepump_score_adjustment = float(prepump_payload["prepump_warm_watch_score_adjustment"] or 0.0)
        prepump_features_used = int(prepump_payload["prepump_warm_watch_features_used"] or 0)
        prepump_features_missing = int(prepump_payload["prepump_warm_watch_features_missing"] or 0)
        prepump_top_features = tuple(
            item for item in str(prepump_payload["prepump_warm_watch_top_features"] or "").split(";") if item
        )
        price_delta_pct = float(promotion["price_delta_pct"])
        quote_volume_delta = float(promotion["quote_volume_delta"])
        quote_volume_delta_ratio = (
            None
            if promotion.get("quote_volume_delta_ratio") is None
            else float(promotion["quote_volume_delta_ratio"])
        )
        trade_count_delta = (
            None
            if promotion.get("trade_count_delta") is None
            else int(promotion["trade_count_delta"])
        )
        trade_count_delta_ratio = (
            None
            if promotion.get("trade_count_delta_ratio") is None
            else float(promotion["trade_count_delta_ratio"])
        )
        radar_reason = "ticker_flow_radar" if promotion_source == DANGER_CHEAP_FLOW_RADAR_SOURCE else "ticker_radar"
        if not self.config.warm_watch_enabled:
            self._mark_ticker_radar_watch(
                symbol,
                now_ms=now_ms,
                reason=radar_reason,
                score=score,
                price_delta_pct=price_delta_pct,
                quote_volume_delta=quote_volume_delta,
                quote_volume_delta_ratio=quote_volume_delta_ratio,
                trade_count_delta=trade_count_delta,
                trade_count_delta_ratio=trade_count_delta_ratio,
                promotion_source=promotion_source,
                base_score=base_score,
                prepump_warm_watch_score_status=str(prepump_payload["prepump_warm_watch_scoring_status"]),
                prepump_warm_watch_score_reason=str(prepump_payload["prepump_warm_watch_scoring_reason"]),
                prepump_warm_watch_score=prepump_score_value,
                prepump_warm_watch_score_adjustment=prepump_score_adjustment,
                prepump_warm_watch_features_used=prepump_features_used,
                prepump_warm_watch_features_missing=prepump_features_missing,
                prepump_warm_watch_top_features=prepump_top_features,
            )
            return "promoted"
        reject_reason = self._warm_watch_reject_reason(
            price_delta_pct=price_delta_pct,
            quote_volume_delta=quote_volume_delta,
            trade_count_delta=trade_count_delta,
            promotion_source=promotion_source,
        )
        if reject_reason:
            self._reject_warm_watch(
                symbol,
                now_ms=now_ms,
                reason=reject_reason,
                score=score,
                price_delta_pct=price_delta_pct,
                quote_volume_delta=quote_volume_delta,
                quote_volume_delta_ratio=quote_volume_delta_ratio,
                trade_count_delta=trade_count_delta,
                trade_count_delta_ratio=trade_count_delta_ratio,
                promotion_source=promotion_source,
                prepump_payload=prepump_payload,
            )
            return "rejected"
        symbol_key = _position_symbol_key(symbol)
        ttl_ms = max(1, int(self.config.warm_watch_ttl_ms))
        expires_at_ms = now_ms + ttl_ms
        min_observations = max(1, int(self.config.warm_watch_min_observations_for_precise))
        event_name = "warm_watch_marked"
        event_payload: dict[str, object]
        promote_payload: dict[str, object] | None = None
        with self._state_lock:
            current = self._warm_watch.get(symbol_key)
            if current is None or current.expires_at_ms < now_ms:
                first_seen_ms = now_ms
                observations = 1
                previous_score = ""
            else:
                first_seen_ms = current.first_seen_ms
                observations = current.observations + 1
                previous_score = current.score
                event_name = "warm_watch_updated"
            if observations >= min_observations:
                latency_sla = self._current_latency_sla_backlog_status(now_ms=now_ms)
                if not latency_sla.optional_scans_allowed:
                    self._warm_watch[symbol_key] = LiveWarmWatch(
                        symbol=symbol,
                        reason=radar_reason,
                        expires_at_ms=expires_at_ms,
                        updated_at_ms=now_ms,
                        first_seen_ms=first_seen_ms,
                        observations=observations,
                        score=score,
                        price_delta_pct=price_delta_pct,
                        quote_volume_delta=quote_volume_delta,
                        quote_volume_delta_ratio=quote_volume_delta_ratio,
                        trade_count_delta=trade_count_delta,
                        trade_count_delta_ratio=trade_count_delta_ratio,
                        promotion_source=promotion_source,
                        base_score=base_score,
                        prepump_warm_watch_score_status=str(prepump_payload["prepump_warm_watch_scoring_status"]),
                        prepump_warm_watch_score_reason=str(prepump_payload["prepump_warm_watch_scoring_reason"]),
                        prepump_warm_watch_score=prepump_score_value,
                        prepump_warm_watch_score_adjustment=prepump_score_adjustment,
                        prepump_warm_watch_features_used=prepump_features_used,
                        prepump_warm_watch_features_missing=prepump_features_missing,
                        prepump_warm_watch_top_features=prepump_top_features,
                    )
                    event_name = "warm_watch_precise_deferred_latency_sla"
                    event_payload = {
                        "reason": latency_sla.reason or LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON,
                        "radar_reason": radar_reason,
                        "expires_at_ms": expires_at_ms,
                        "ttl_ms": ttl_ms,
                        "observations": observations,
                        "min_observations_for_precise": min_observations,
                        "first_seen_ms": first_seen_ms,
                        "age_ms": max(0, now_ms - first_seen_ms),
                        "score": score,
                        "previous_score": previous_score,
                        "price_delta_pct": price_delta_pct,
                        "quote_volume_delta": quote_volume_delta,
                        "quote_volume_delta_ratio": quote_volume_delta_ratio if quote_volume_delta_ratio is not None else "",
                        "trade_count_delta": trade_count_delta if trade_count_delta is not None else "",
                        "trade_count_delta_ratio": trade_count_delta_ratio if trade_count_delta_ratio is not None else "",
                        "promotion_source": promotion_source,
                        "source": WARM_WATCH_SOURCE,
                        **prepump_payload,
                        **latency_sla.event_payload(),
                    }
                else:
                    self._warm_watch.pop(symbol_key, None)
                    promote_payload = {
                        "reason": WARM_WATCH_SOURCE,
                        "radar_reason": radar_reason,
                        "observations": observations,
                        "min_observations_for_precise": min_observations,
                        "first_seen_ms": first_seen_ms,
                        "age_ms": max(0, now_ms - first_seen_ms),
                        "score": score,
                        "previous_score": previous_score,
                        "price_delta_pct": price_delta_pct,
                        "quote_volume_delta": quote_volume_delta,
                        "quote_volume_delta_ratio": quote_volume_delta_ratio if quote_volume_delta_ratio is not None else "",
                        "trade_count_delta": trade_count_delta if trade_count_delta is not None else "",
                        "trade_count_delta_ratio": trade_count_delta_ratio if trade_count_delta_ratio is not None else "",
                        "promotion_source": promotion_source,
                        "source": WARM_WATCH_SOURCE,
                        **prepump_payload,
                    }
            else:
                self._warm_watch[symbol_key] = LiveWarmWatch(
                    symbol=symbol,
                    reason=radar_reason,
                    expires_at_ms=expires_at_ms,
                    updated_at_ms=now_ms,
                    first_seen_ms=first_seen_ms,
                    observations=observations,
                    score=score,
                    price_delta_pct=price_delta_pct,
                    quote_volume_delta=quote_volume_delta,
                    quote_volume_delta_ratio=quote_volume_delta_ratio,
                    trade_count_delta=trade_count_delta,
                    trade_count_delta_ratio=trade_count_delta_ratio,
                    promotion_source=promotion_source,
                    base_score=base_score,
                    prepump_warm_watch_score_status=str(prepump_payload["prepump_warm_watch_scoring_status"]),
                    prepump_warm_watch_score_reason=str(prepump_payload["prepump_warm_watch_scoring_reason"]),
                    prepump_warm_watch_score=prepump_score_value,
                    prepump_warm_watch_score_adjustment=prepump_score_adjustment,
                    prepump_warm_watch_features_used=prepump_features_used,
                    prepump_warm_watch_features_missing=prepump_features_missing,
                    prepump_warm_watch_top_features=prepump_top_features,
                )
                event_payload = {
                    "reason": radar_reason,
                    "expires_at_ms": expires_at_ms,
                    "ttl_ms": ttl_ms,
                    "observations": observations,
                    "min_observations_for_precise": min_observations,
                    "first_seen_ms": first_seen_ms,
                    "age_ms": max(0, now_ms - first_seen_ms),
                    "score": score,
                    "previous_score": previous_score,
                    "price_delta_pct": price_delta_pct,
                    "quote_volume_delta": quote_volume_delta,
                    "quote_volume_delta_ratio": quote_volume_delta_ratio if quote_volume_delta_ratio is not None else "",
                    "trade_count_delta": trade_count_delta if trade_count_delta is not None else "",
                    "trade_count_delta_ratio": trade_count_delta_ratio if trade_count_delta_ratio is not None else "",
                    "promotion_source": promotion_source,
                    "source": WARM_WATCH_SOURCE,
                    **prepump_payload,
                }
        if promote_payload is not None:
            self.artifacts.append_event("warm_watch_precise_promoted", symbol, promote_payload)
            self._mark_ticker_radar_watch(
                symbol,
                now_ms=now_ms,
                reason=WARM_WATCH_SOURCE,
                score=score,
                price_delta_pct=price_delta_pct,
                quote_volume_delta=quote_volume_delta,
                quote_volume_delta_ratio=quote_volume_delta_ratio,
                trade_count_delta=trade_count_delta,
                trade_count_delta_ratio=trade_count_delta_ratio,
                promotion_source=promotion_source,
                base_score=base_score,
                prepump_warm_watch_score_status=str(prepump_payload["prepump_warm_watch_scoring_status"]),
                prepump_warm_watch_score_reason=str(prepump_payload["prepump_warm_watch_scoring_reason"]),
                prepump_warm_watch_score=prepump_score_value,
                prepump_warm_watch_score_adjustment=prepump_score_adjustment,
                prepump_warm_watch_features_used=prepump_features_used,
                prepump_warm_watch_features_missing=prepump_features_missing,
                prepump_warm_watch_top_features=prepump_top_features,
            )
            return "promoted"
        self.artifacts.append_event(event_name, symbol, event_payload)
        if event_name == "warm_watch_precise_deferred_latency_sla":
            return "deferred"
        return "marked"

    def _warm_watch_reject_reason(
        self,
        *,
        price_delta_pct: float,
        quote_volume_delta: float,
        trade_count_delta: int | None,
        promotion_source: str,
    ) -> str:
        if not math.isfinite(price_delta_pct):
            return "invalid_price_delta"
        if price_delta_pct < self.config.warm_watch_min_price_delta_pct:
            return "fade_price_delta_below_min"
        if price_delta_pct >= self.config.warm_watch_max_price_delta_pct:
            return "chase_price_delta_above_max"
        if not math.isfinite(quote_volume_delta) or quote_volume_delta <= 0.0:
            return "quote_volume_delta_not_rising"
        if promotion_source == DANGER_CHEAP_FLOW_RADAR_SOURCE and (
            trade_count_delta is None or trade_count_delta <= 0
        ):
            return "trade_count_delta_not_rising"
        return ""

    def _reject_warm_watch(
        self,
        symbol: str,
        *,
        now_ms: int,
        reason: str,
        score: float,
        price_delta_pct: float,
        quote_volume_delta: float,
        quote_volume_delta_ratio: float | None,
        trade_count_delta: int | None,
        trade_count_delta_ratio: float | None,
        promotion_source: str,
        prepump_payload: dict[str, object] | None = None,
    ) -> None:
        symbol_key = _position_symbol_key(symbol)
        previous: LiveWarmWatch | None = None
        prepump_payload = dict(prepump_payload or LivePrepumpWarmWatchScore(status="not_evaluated", reason="").event_payload())
        with self._state_lock:
            previous = self._warm_watch.pop(symbol_key, None)
        self.artifacts.append_event(
            "warm_watch_rejected",
            symbol,
            {
                "reason": reason,
                "had_previous_warm_watch": previous is not None,
                "previous_observations": previous.observations if previous is not None else "",
                "previous_first_seen_ms": previous.first_seen_ms if previous is not None else "",
                "age_ms": max(0, now_ms - previous.first_seen_ms) if previous is not None else "",
                "score": score,
                "price_delta_pct": price_delta_pct,
                "quote_volume_delta": quote_volume_delta,
                "quote_volume_delta_ratio": quote_volume_delta_ratio if quote_volume_delta_ratio is not None else "",
                "trade_count_delta": trade_count_delta if trade_count_delta is not None else "",
                "trade_count_delta_ratio": trade_count_delta_ratio if trade_count_delta_ratio is not None else "",
                "promotion_source": promotion_source,
                "source": WARM_WATCH_SOURCE,
                **prepump_payload,
            },
        )

    def _detected_anomalies_count(self) -> int:
        with self._state_lock:
            return self._detected_anomalies_total

    def _mark_ticker_radar_watch(
        self,
        symbol: str,
        *,
        now_ms: int,
        reason: str,
        score: float,
        price_delta_pct: float,
        quote_volume_delta: float,
        quote_volume_delta_ratio: float | None,
        trade_count_delta: int | None = None,
        trade_count_delta_ratio: float | None = None,
        promotion_source: str = "ticker_price_volume",
        base_score: float | None = None,
        prepump_warm_watch_score_status: str = "not_evaluated",
        prepump_warm_watch_score_reason: str = "",
        prepump_warm_watch_score: float | None = None,
        prepump_warm_watch_score_adjustment: float = 0.0,
        prepump_warm_watch_features_used: int = 0,
        prepump_warm_watch_features_missing: int = 0,
        prepump_warm_watch_top_features: tuple[str, ...] = (),
    ) -> None:
        expires_at_ms = now_ms + max(1, int(self.config.ticker_radar_watch_ttl_ms))
        symbol_key = _position_symbol_key(symbol)
        event_payload: dict[str, object] | None = None
        with self._state_lock:
            current = self._ticker_radar_watch.get(symbol_key)
            should_emit = current is None or current.expires_at_ms < now_ms or score > current.score
            self._ticker_radar_watch[symbol_key] = LiveTickerRadarWatch(
                symbol=symbol,
                reason=reason,
                expires_at_ms=expires_at_ms,
                updated_at_ms=now_ms,
                score=score,
                price_delta_pct=price_delta_pct,
                quote_volume_delta=quote_volume_delta,
                quote_volume_delta_ratio=quote_volume_delta_ratio,
                trade_count_delta=trade_count_delta,
                trade_count_delta_ratio=trade_count_delta_ratio,
                promotion_source=promotion_source,
                base_score=base_score if base_score is not None else score,
                prepump_warm_watch_score_status=prepump_warm_watch_score_status,
                prepump_warm_watch_score_reason=prepump_warm_watch_score_reason,
                prepump_warm_watch_score=prepump_warm_watch_score,
                prepump_warm_watch_score_adjustment=prepump_warm_watch_score_adjustment,
                prepump_warm_watch_features_used=prepump_warm_watch_features_used,
                prepump_warm_watch_features_missing=prepump_warm_watch_features_missing,
                prepump_warm_watch_top_features=prepump_warm_watch_top_features,
            )
            if should_emit:
                self._detected_anomalies_total += 1
                event_payload = {
                    "detected_anomaly_index": self._detected_anomalies_total,
                    "reason": reason,
                    "expires_at_ms": expires_at_ms,
                    "ttl_ms": int(self.config.ticker_radar_watch_ttl_ms),
                    "score": score,
                    "base_score": base_score if base_score is not None else score,
                    "price_delta_pct": price_delta_pct,
                    "quote_volume_delta": quote_volume_delta,
                    "quote_volume_delta_ratio": quote_volume_delta_ratio if quote_volume_delta_ratio is not None else "",
                    "trade_count_delta": trade_count_delta if trade_count_delta is not None else "",
                    "trade_count_delta_ratio": trade_count_delta_ratio if trade_count_delta_ratio is not None else "",
                    "promotion_source": promotion_source,
                    "source": promotion_source,
                    "danger_flow_radar": promotion_source == DANGER_CHEAP_FLOW_RADAR_SOURCE,
                    "prepump_warm_watch_scoring_status": prepump_warm_watch_score_status,
                    "prepump_warm_watch_scoring_reason": prepump_warm_watch_score_reason,
                    "prepump_warm_watch_score": (
                        round(float(prepump_warm_watch_score), 6)
                        if prepump_warm_watch_score is not None
                        else ""
                    ),
                    "prepump_warm_watch_score_adjustment": round(
                        float(prepump_warm_watch_score_adjustment), 6
                    ),
                    "prepump_warm_watch_features_used": int(prepump_warm_watch_features_used),
                    "prepump_warm_watch_features_missing": int(prepump_warm_watch_features_missing),
                    "prepump_warm_watch_top_features": ";".join(prepump_warm_watch_top_features),
                    "prepump_warm_watch_scoring_contract": PREPUMP_WARM_WATCH_SCORING_CONTRACT,
                }
        if event_payload is not None:
            self.artifacts.append_event("ticker_radar_promoted", symbol, event_payload)

    def _prune_ticker_radar_watch_locked(self, now_ms: int) -> list[LiveTickerRadarWatch]:
        expired_keys = [key for key, state in self._ticker_radar_watch.items() if state.expires_at_ms < now_ms]
        expired: list[LiveTickerRadarWatch] = []
        for key in expired_keys:
            state = self._ticker_radar_watch.pop(key, None)
            if state is not None:
                expired.append(state)
        return expired

    def _prune_warm_watch_locked(self, now_ms: int) -> list[LiveWarmWatch]:
        expired_keys = [key for key, state in self._warm_watch.items() if state.expires_at_ms < now_ms]
        expired: list[LiveWarmWatch] = []
        for key in expired_keys:
            state = self._warm_watch.pop(key, None)
            if state is not None:
                expired.append(state)
        return expired

    def _mark_active_symbol(
        self,
        symbol: str,
        *,
        reason: str,
        now_ms: int,
        ttl_ms: int | None = None,
        decision_timestamp_ms: int | None = None,
    ) -> None:
        ttl = self.config.active_symbol_ttl_ms if ttl_ms is None else ttl_ms
        expires_at_ms = now_ms + max(1, int(ttl))
        symbol_key = _position_symbol_key(symbol)
        event_payload: dict[str, object] | None = None
        cleared_radar_watch: LiveTickerRadarWatch | None = None
        cleared_warm_watch: LiveWarmWatch | None = None
        with self._state_lock:
            current = self._active_symbols.get(symbol_key)
            should_emit = (
                current is None
                or current.reason != reason
                or current.decision_timestamp_ms != decision_timestamp_ms
                or current.expires_at_ms < now_ms
            )
            self._active_symbols[symbol_key] = LiveActiveSymbol(
                symbol=symbol,
                reason=reason,
                expires_at_ms=expires_at_ms,
                updated_at_ms=now_ms,
                decision_timestamp_ms=decision_timestamp_ms,
            )
            self._active_symbols_seen.add(symbol_key)
            cleared_warm_watch = self._warm_watch.pop(symbol_key, None)
            cleared_radar_watch = self._ticker_radar_watch.pop(symbol_key, None)
            if should_emit:
                event_payload = {
                    "reason": reason,
                    "expires_at_ms": expires_at_ms,
                    "ttl_ms": int(ttl),
                    "decision_timestamp_ms": decision_timestamp_ms if decision_timestamp_ms is not None else "",
                }
        if cleared_radar_watch is not None:
            self.artifacts.append_event(
                "ticker_radar_watch_cleared",
                symbol,
                {
                    "reason": "promoted_to_active_symbol",
                    "active_reason": reason,
                    "watch_reason": cleared_radar_watch.reason,
                    "score": cleared_radar_watch.score,
                    "expires_at_ms": cleared_radar_watch.expires_at_ms,
                    "price_delta_pct": cleared_radar_watch.price_delta_pct,
                    "quote_volume_delta": cleared_radar_watch.quote_volume_delta,
                    "quote_volume_delta_ratio": (
                        cleared_radar_watch.quote_volume_delta_ratio
                        if cleared_radar_watch.quote_volume_delta_ratio is not None
                        else ""
                    ),
                },
            )
        if cleared_warm_watch is not None:
            self.artifacts.append_event(
                "warm_watch_cleared",
                symbol,
                {
                    "reason": "promoted_to_active_symbol",
                    "active_reason": reason,
                    "watch_reason": cleared_warm_watch.reason,
                    "observations": cleared_warm_watch.observations,
                    "score": cleared_warm_watch.score,
                    "expires_at_ms": cleared_warm_watch.expires_at_ms,
                    "price_delta_pct": cleared_warm_watch.price_delta_pct,
                    "quote_volume_delta": cleared_warm_watch.quote_volume_delta,
                    "quote_volume_delta_ratio": (
                        cleared_warm_watch.quote_volume_delta_ratio
                        if cleared_warm_watch.quote_volume_delta_ratio is not None
                        else ""
                    ),
                    "promotion_source": cleared_warm_watch.promotion_source,
                },
            )
        if event_payload is not None:
            self.artifacts.append_event("active_symbol_marked", symbol, event_payload)

    def _clear_active_symbol(self, symbol: str, *, reason: str) -> None:
        symbol_key = _position_symbol_key(symbol)
        removed: LiveActiveSymbol | None = None
        with self._state_lock:
            removed = self._active_symbols.pop(symbol_key, None)
        if removed is not None:
            self.artifacts.append_event(
                "active_symbol_cleared",
                symbol,
                {
                    "reason": reason,
                    "previous_reason": removed.reason,
                    "previous_expires_at_ms": removed.expires_at_ms,
                    "decision_timestamp_ms": removed.decision_timestamp_ms if removed.decision_timestamp_ms is not None else "",
                },
            )

    def _prune_active_symbols_locked(self, now_ms: int) -> list[LiveActiveSymbol]:
        expired_keys = [key for key, state in self._active_symbols.items() if state.expires_at_ms < now_ms]
        expired: list[LiveActiveSymbol] = []
        for key in expired_keys:
            state = self._active_symbols.pop(key, None)
            if state is not None:
                expired.append(state)
        return expired

    def _mark_signal_decision_consumed(self, signal: LiveSignal, *, reason: str) -> None:
        key = (signal.symbol, signal.levels_timeframe.value, signal.entry_timeframe.value, int(signal.decision_timestamp_ms))
        with self._state_lock:
            already_seen = key in self._seen_decisions
            self._seen_decisions.add(key)
        if not already_seen:
            self.artifacts.append_event(
                "signal_decision_consumed",
                signal.symbol,
                {
                    "reason": reason,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                },
            )

    def _dependency_retry_cooldown_key(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        decision_timestamp_ms: int,
    ) -> tuple[str, str, str, int]:
        return (
            _position_symbol_key(symbol),
            levels_timeframe.value,
            entry_timeframe.value,
            int(decision_timestamp_ms),
        )

    def _dependency_retry_cooldown_delay_ms(self, entry_timeframe: Timeframe) -> int:
        timeframe_ms = max(1, int(entry_timeframe.to_milliseconds()))
        return max(
            int(DEPENDENCY_RETRY_MIN_COOLDOWN_MS),
            min(int(DEPENDENCY_RETRY_MAX_COOLDOWN_MS), timeframe_ms),
        )

    def _dependency_retry_cooldown_active_locked(
        self,
        key: tuple[str, str, str, int],
        *,
        now_ms: int,
    ) -> bool:
        cooldown = self._dependency_retry_cooldowns.get(key)
        return bool(
            cooldown is not None
            and int(now_ms) < int(cooldown.next_retry_at_ms)
            and int(now_ms) <= int(cooldown.expires_at_ms)
        )

    def _note_dependency_retry_cooldown_skipped_locked(self, key: tuple[str, str, str, int]) -> None:
        if key in self._cycle_dependency_retry_cooldown_skip_keys:
            return
        self._cycle_dependency_retry_cooldown_skip_keys.add(key)
        self._cycle_dependency_retry_cooldown_skipped += 1
        self._dependency_retry_cooldown_skipped_total += 1

    def _emit_dependency_retry_expired_event(
        self,
        cooldown: LiveDependencyRetryCooldown,
        *,
        source: str,
    ) -> None:
        self._cycle_dependency_retry_cooldown_expired += 1
        self._dependency_retry_cooldown_expired_total += 1
        self.artifacts.append_event(
            "candidate_expired_dependency_timeout",
            cooldown.symbol,
            {
                "levels_tf": cooldown.levels_timeframe,
                "entry_tf": cooldown.entry_timeframe,
                "decision_timestamp_ms": int(cooldown.decision_timestamp_ms),
                "retry_reason": cooldown.retry_reason,
                "retryable_reasons": list(cooldown.retryable_reasons),
                "blocked_at_ms": int(cooldown.blocked_at_ms),
                "next_retry_at_ms": int(cooldown.next_retry_at_ms),
                "expires_at_ms": int(cooldown.expires_at_ms),
                "reason": "dependency_not_ready_before_signal_stale_timeout",
                "source": source,
            },
        )

    def _prune_dependency_retry_cooldowns(self, *, now_ms: int) -> None:
        expired: list[LiveDependencyRetryCooldown] = []
        with self._state_lock:
            expired_keys = [
                key
                for key, cooldown in self._dependency_retry_cooldowns.items()
                if int(now_ms) > int(cooldown.expires_at_ms)
            ]
            for key in expired_keys:
                cooldown = self._dependency_retry_cooldowns.pop(key, None)
                if cooldown is None:
                    continue
                scan_key = (cooldown.symbol_key, cooldown.levels_timeframe, cooldown.entry_timeframe)
                current = self._last_signal_scan_closed_at.get(scan_key)
                if current is None or int(current) <= int(cooldown.decision_timestamp_ms):
                    self._last_signal_scan_closed_at[scan_key] = int(cooldown.decision_timestamp_ms)
                expired.append(cooldown)
        for cooldown in expired:
            self._emit_dependency_retry_expired_event(cooldown, source="dependency_retry_cooldown_prune")

    def _dependency_retry_cooldown_status(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        *,
        decision_timestamp_ms: int,
        now_ms: int,
    ) -> tuple[str, LiveDependencyRetryCooldown | None]:
        key = self._dependency_retry_cooldown_key(
            symbol,
            levels_timeframe,
            entry_timeframe,
            int(decision_timestamp_ms),
        )
        scan_key = (_position_symbol_key(symbol), levels_timeframe.value, entry_timeframe.value)
        with self._state_lock:
            cooldown = self._dependency_retry_cooldowns.get(key)
            if cooldown is None:
                return "ready", None
            if int(now_ms) > int(cooldown.expires_at_ms):
                removed = self._dependency_retry_cooldowns.pop(key, None)
                self._last_signal_scan_closed_at[scan_key] = int(decision_timestamp_ms)
                return "expired", removed
            if int(now_ms) < int(cooldown.next_retry_at_ms):
                self._note_dependency_retry_cooldown_skipped_locked(key)
                return "cooldown", cooldown
            self._dependency_retry_cooldowns.pop(key, None)
            return "ready", cooldown

    def _register_dependency_retry_cooldown(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        *,
        decision_timestamp_ms: int | None,
        now_ms: int,
        retry_reason: str,
        retryable_reasons: tuple[str, ...],
    ) -> None:
        if decision_timestamp_ms is None:
            return
        delay_ms = self._dependency_retry_cooldown_delay_ms(entry_timeframe)
        key = self._dependency_retry_cooldown_key(
            symbol,
            levels_timeframe,
            entry_timeframe,
            int(decision_timestamp_ms),
        )
        expires_at_ms = int(decision_timestamp_ms) + int(self.config.max_signal_age_ms)
        if int(now_ms) > expires_at_ms:
            return
        cooldown = LiveDependencyRetryCooldown(
            symbol=symbol,
            symbol_key=_position_symbol_key(symbol),
            levels_timeframe=levels_timeframe.value,
            entry_timeframe=entry_timeframe.value,
            decision_timestamp_ms=int(decision_timestamp_ms),
            blocked_at_ms=int(now_ms),
            next_retry_at_ms=min(int(now_ms) + int(delay_ms), expires_at_ms),
            expires_at_ms=expires_at_ms,
            retry_reason=retry_reason,
            retryable_reasons=tuple(retryable_reasons),
        )
        with self._state_lock:
            previous = self._dependency_retry_cooldowns.get(key)
            self._dependency_retry_cooldowns[key] = cooldown
        if previous is None or previous.retryable_reasons != cooldown.retryable_reasons:
            self.artifacts.append_event(
                "signal_scan_dependency_retry_scheduled",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "decision_timestamp_ms": int(decision_timestamp_ms),
                    "retry_reason": retry_reason,
                    "retryable_reasons": list(retryable_reasons),
                    "blocked_at_ms": int(now_ms),
                    "next_retry_at_ms": int(cooldown.next_retry_at_ms),
                    "expires_at_ms": int(cooldown.expires_at_ms),
                    "cooldown_ms": int(delay_ms),
                    "policy": "do_not_rescan_retryable_dependency_every_cycle_wait_for_context_or_stale_timeout",
                },
            )

    def _clear_dependency_retry_cooldown(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        *,
        decision_timestamp_ms: int | None,
    ) -> None:
        if decision_timestamp_ms is None:
            return
        key = self._dependency_retry_cooldown_key(symbol, levels_timeframe, entry_timeframe, int(decision_timestamp_ms))
        with self._state_lock:
            self._dependency_retry_cooldowns.pop(key, None)

    def _signal_scan_due_for_symbol(self, symbol: str, *, now_ms: int) -> bool:
        symbol_key = _position_symbol_key(symbol)
        with self._state_lock:
            for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
                closed_timestamp_ms = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
                scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
                dependency_key = (symbol_key, levels_timeframe.value, entry_timeframe.value, int(closed_timestamp_ms))
                if self._last_signal_scan_closed_at.get(scan_key) == closed_timestamp_ms:
                    continue
                if self._dependency_retry_cooldown_active_locked(dependency_key, now_ms=now_ms):
                    self._note_dependency_retry_cooldown_skipped_locked(dependency_key)
                    continue
                return True
        return False

    def _signal_scan_due_for_timeframe(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        *,
        closed_timestamp_ms: int,
    ) -> bool:
        symbol_key = _position_symbol_key(symbol)
        scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
        dependency_key = (symbol_key, levels_timeframe.value, entry_timeframe.value, int(closed_timestamp_ms))
        now_ms = int(time.time() * 1000)
        with self._state_lock:
            if self._last_signal_scan_closed_at.get(scan_key) == int(closed_timestamp_ms):
                return False
            if self._dependency_retry_cooldown_active_locked(dependency_key, now_ms=now_ms):
                self._note_dependency_retry_cooldown_skipped_locked(dependency_key)
                return False
            return True

    def _mark_signal_scan_closed_at(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        *,
        closed_timestamp_ms: int,
    ) -> None:
        symbol_key = _position_symbol_key(symbol)
        scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
        with self._state_lock:
            self._last_signal_scan_closed_at[scan_key] = int(closed_timestamp_ms)

    def _forget_signal_scan_closed_at(self, signal: LiveSignal, *, reason: str) -> None:
        symbol_key = _position_symbol_key(signal.symbol)
        scan_key = (symbol_key, signal.levels_timeframe.value, signal.entry_timeframe.value)
        removed_timestamp_ms: int | None = None
        with self._state_lock:
            current = self._last_signal_scan_closed_at.get(scan_key)
            if current == int(signal.decision_timestamp_ms):
                removed_timestamp_ms = self._last_signal_scan_closed_at.pop(scan_key)
        if removed_timestamp_ms is not None:
            self.artifacts.append_event(
                "signal_scan_retry_enabled",
                signal.symbol,
                {
                    "reason": reason,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                    "removed_scan_closed_timestamp_ms": int(removed_timestamp_ms),
                },
            )

    def _previous_signal_scan_closed_at(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> int | None:
        symbol_key = _position_symbol_key(symbol)
        scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
        with self._state_lock:
            return self._last_signal_scan_closed_at.get(scan_key)

    def _batch_scan_mode_for_symbol(self, symbol: str) -> str:
        return self._current_batch_symbol_scan_mode.get(_position_symbol_key(symbol), "precise_direct")

    def _is_danger_cold_scan_symbol(self, symbol: str) -> bool:
        return self._batch_scan_mode_for_symbol(symbol) == "precise_DANGER_cold_coverage"

    def _subminute_entry_scan_allowed(self, symbol: str) -> bool:
        return self._batch_scan_mode_for_symbol(symbol).startswith("precise")

    def _should_defer_inactive_subminute_pair(self, symbol: str, entry_timeframe: Timeframe) -> bool:
        if int(entry_timeframe.to_milliseconds()) >= int(Timeframe.M1.to_milliseconds()):
            return False
        return not self._subminute_entry_scan_allowed(symbol)

    def _count_symbol_scan_mode(self, symbol: str) -> None:
        if self._batch_scan_mode_for_symbol(symbol).startswith("precise"):
            self._cycle_precise_scan_symbols += 1
            if self._is_danger_cold_scan_symbol(symbol):
                self._cycle_cold_scanned_symbols += 1
                self._cold_scanned_symbols_total += 1
        else:
            self._cycle_inactive_visit_symbols += 1

    def _categories_for_timeframe(
        self,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> tuple[LivePumpCategory, ...]:
        priority = LIVE_TIMEFRAME_CATEGORY_PRIORITY.get((levels_timeframe.value, entry_timeframe.value))
        if priority is None:
            return self._pump_categories
        ordered: list[LivePumpCategory] = []
        seen: set[str] = set()
        for category_id in priority:
            category = self._pump_categories_by_id.get(category_id)
            if category is not None:
                ordered.append(category)
                seen.add(category_id)
        ordered.extend(category for category in self._pump_categories if category.category_id not in seen)
        return tuple(ordered)



    def _symbol_context_snapshot_timeframes(self) -> tuple[Timeframe, ...]:
        unique: dict[str, Timeframe] = {}
        for levels_timeframe, _entry_timeframe in self.config.timeframe_pairs:
            if int(levels_timeframe.to_milliseconds()) < int(Timeframe.M1.to_milliseconds()):
                continue
            unique.setdefault(levels_timeframe.value, levels_timeframe)
        return tuple(unique.values())

    def _symbol_context_window_bounds(
        self,
        timeframe: Timeframe,
        *,
        now_ms: int,
        symbols_total: int | None = None,
    ) -> tuple[int, int, int]:
        timeframe_ms = int(timeframe.to_milliseconds())
        decision_ts = _latest_closed_candle_start_ms(timeframe, now_ms=int(now_ms))
        history_padding_ms = max(
            int(self._effective_symbol_context_snapshot_fresh_ms(symbols_total=symbols_total)),
            int(float(self.config.symbol_context_snapshot_interval_seconds) * 1000.0),
        )
        history_start_ms = int(decision_ts) - 3 * 86_400_000 - int(history_padding_ms)
        context_start_ms = int(history_start_ms) - int(self.config.baseline_candles) * int(timeframe_ms)
        return int(context_start_ms), int(history_start_ms), int(decision_ts)

    def _startup_status_time(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _startup_status(self, stage: str, message: str) -> None:
        self._status_logger.status(f"{stage} · {self._startup_status_time()} · {message}")

    def _context_preparation_telegram_title(self, *, policy_context: str) -> str:
        if policy_context == "live_reprepare":
            return "Контекст 72ч: переподготовка"
        return "Контекст 72ч: подготовка"

    def _send_context_preparation_started_telegram(
        self,
        *,
        phase: str,
        symbols_total: int,
        context_timeframes: tuple[Timeframe, ...],
    ) -> None:
        timeframe_label = ", ".join(timeframe.value for timeframe in context_timeframes) or "-"
        self.telegram.send(
            channel="events",
            key=f"context_preparation_started:{phase}",
            text=(
                f"{SERVICE_WORK_EMOJI} <b>{self._context_preparation_telegram_title(policy_context=phase)} включена</b>\n\n"
                f"контекст 72ч · {symbols_total} символов · TF {_telegram_code(timeframe_label)}"
            ),
        )

    def _send_context_preparation_finished_telegram(
        self,
        *,
        policy_context: str,
        startup_ready: bool,
        ready_symbols: int,
        symbols_total: int,
        partial_symbols: int,
        unavailable_symbols: int,
        ready_snapshots: int,
        expected_snapshot_count: int,
        refuse_real_orders: bool,
    ) -> None:
        if startup_ready:
            title = "Контекст 72ч готов"
            emoji = "✅"
        else:
            title = "Контекст 72ч не готов"
            emoji = SERVICE_WARNING_EMOJI
        refusal_note = "\nreal-orders не стартует" if (not startup_ready and self.config.confirm_real_orders and refuse_real_orders) else ""
        self.telegram.send(
            channel="events",
            key=f"context_preparation_finished:{policy_context}:{'ready' if startup_ready else 'not_ready'}",
            text=(
                f"{emoji} <b>{title}</b>\n\n"
                f"готово {ready_symbols}/{symbols_total} · partial {partial_symbols} · unavailable {unavailable_symbols}\n"
                f"snapshots {ready_snapshots}/{expected_snapshot_count}{refusal_note}"
            ),
        )

    def _startup_eta_text(self, *, started_at: float, completed: int, total: int) -> str:
        if total <= 0:
            return "0с"
        completed = max(0, min(int(completed), int(total)))
        if completed <= 0:
            return "-"
        elapsed_seconds = max(0.0, float(time.monotonic() - started_at))
        seconds_per_unit = elapsed_seconds / float(completed)
        eta_seconds = max(0.0, seconds_per_unit * float(int(total) - completed))
        return _format_live_runtime(float(eta_seconds))

    def _startup_symbol_context_expected_keys(self, symbol: str) -> tuple[tuple[str, str, str], ...]:
        keys: list[tuple[str, str, str]] = []
        for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
            if int(levels_timeframe.to_milliseconds()) < int(Timeframe.M1.to_milliseconds()):
                continue
            keys.append(self._symbol_context_snapshot_key(symbol, levels_timeframe, entry_timeframe))
        return tuple(keys)

    def _startup_symbol_context_readiness_summary(self, symbols: list[str]) -> dict[str, object]:
        expected_keys_by_symbol = {
            _position_symbol_key(symbol): self._startup_symbol_context_expected_keys(symbol)
            for symbol in symbols
        }
        symbols_total = int(len(symbols))
        expected_snapshot_count = sum(len(keys) for keys in expected_keys_by_symbol.values())
        ready_symbols = 0
        partial_symbols = 0
        unavailable_symbols = 0
        missing_symbols = 0
        ready_snapshots = 0
        tolerated_gap_snapshots = 0
        unavailable_snapshots = 0
        missing_snapshots = 0
        baseline_not_ok_snapshots = 0
        reason_counts: dict[str, int] = {}
        unavailable_symbol_examples: list[str] = []

        for symbol in symbols:
            symbol_key = _position_symbol_key(symbol)
            keys = expected_keys_by_symbol.get(symbol_key, ())
            if not keys:
                missing_symbols += 1
                if len(unavailable_symbol_examples) < 12:
                    unavailable_symbol_examples.append(symbol)
                continue
            symbol_ready = 0
            symbol_missing = 0
            for key in keys:
                snapshot = self._symbol_context_snapshots.get(key)
                if snapshot is None:
                    missing_snapshots += 1
                    symbol_missing += 1
                    reason_counts["missing_snapshot"] = reason_counts.get("missing_snapshot", 0) + 1
                    continue
                status = str(snapshot.status or "")
                baseline_status = str(snapshot.baseline_status or "")
                reason = str(snapshot.reason or "")
                if status == "ok" and baseline_status == "ok":
                    ready_snapshots += 1
                    symbol_ready += 1
                    if reason.startswith("ok_tolerated_gap"):
                        tolerated_gap_snapshots += 1
                    continue
                unavailable_snapshots += 1
                if baseline_status and baseline_status != "ok":
                    baseline_not_ok_snapshots += 1
                    reason_key = f"baseline={baseline_status}"
                elif status != "ok":
                    reason_key = f"status={status}:{reason}"
                else:
                    reason_key = f"reason={reason or 'unknown'}"
                reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1
            if symbol_ready == len(keys):
                ready_symbols += 1
            elif symbol_ready > 0:
                partial_symbols += 1
                if len(unavailable_symbol_examples) < 12:
                    unavailable_symbol_examples.append(symbol)
            else:
                unavailable_symbols += 1
                if symbol_missing == len(keys):
                    missing_symbols += 1
                if len(unavailable_symbol_examples) < 12:
                    unavailable_symbol_examples.append(symbol)

        ready_symbol_ratio = float(ready_symbols) / float(symbols_total) if symbols_total else 0.0
        ready_snapshot_ratio = (
            float(ready_snapshots) / float(expected_snapshot_count) if expected_snapshot_count else 0.0
        )
        top_reasons = dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:12])
        return {
            "symbols_total": symbols_total,
            "expected_snapshot_count": int(expected_snapshot_count),
            "ready_symbols": int(ready_symbols),
            "partial_symbols": int(partial_symbols),
            "unavailable_symbols": int(unavailable_symbols),
            "missing_symbols": int(missing_symbols),
            "ready_snapshots": int(ready_snapshots),
            "tolerated_gap_snapshots": int(tolerated_gap_snapshots),
            "unavailable_snapshots": int(unavailable_snapshots),
            "missing_snapshots": int(missing_snapshots),
            "baseline_not_ok_snapshots": int(baseline_not_ok_snapshots),
            "ready_symbol_ratio": float(ready_symbol_ratio),
            "ready_snapshot_ratio": float(ready_snapshot_ratio),
            "min_ready_symbol_ratio": float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SYMBOL_RATIO),
            "min_ready_snapshot_ratio": float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SNAPSHOT_RATIO),
            "top_unavailable_reasons": top_reasons,
            "unavailable_symbol_examples": unavailable_symbol_examples,
        }

    def _validate_startup_symbol_context_readiness(
        self,
        symbols: list[str],
        *,
        stage: str = "подготовка",
        policy_context: str = "startup",
        refuse_real_orders: bool = True,
    ) -> bool:
        summary = self._startup_symbol_context_readiness_summary(symbols)
        ready_symbols = int(summary["ready_symbols"])
        symbols_total = int(summary["symbols_total"])
        partial_symbols = int(summary["partial_symbols"])
        unavailable_symbols = int(summary["unavailable_symbols"])
        ready_snapshots = int(summary["ready_snapshots"])
        expected_snapshot_count = int(summary["expected_snapshot_count"])
        ready_symbol_ratio = float(summary["ready_symbol_ratio"])
        ready_snapshot_ratio = float(summary["ready_snapshot_ratio"])
        startup_ready = (
            symbols_total > 0
            and expected_snapshot_count > 0
            and ready_symbol_ratio >= float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SYMBOL_RATIO)
            and ready_snapshot_ratio >= float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SNAPSHOT_RATIO)
        )
        status = "ready" if startup_ready else "not_ready"
        self._startup_status(
            stage,
            (
                f"готово {ready_symbols}/{symbols_total} · partial {partial_symbols} · "
                f"unavailable {unavailable_symbols} · snapshots {ready_snapshots}/{expected_snapshot_count} · ETA 0с"
            ),
        )
        payload = {
            "status": status,
            "contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
            "policy": "real_orders_refuse_startup_when_context_readiness_below_internal_threshold_no_pass_by_default",
            "policy_context": policy_context,
            "confirm_real_orders": bool(self.config.confirm_real_orders),
            **summary,
        }
        self.artifacts.append_event("symbol_context_startup_readiness", "__live__", payload)
        self._send_context_preparation_finished_telegram(
            policy_context=policy_context,
            startup_ready=bool(startup_ready),
            ready_symbols=ready_symbols,
            symbols_total=symbols_total,
            partial_symbols=partial_symbols,
            unavailable_symbols=unavailable_symbols,
            ready_snapshots=ready_snapshots,
            expected_snapshot_count=expected_snapshot_count,
            refuse_real_orders=bool(refuse_real_orders),
        )
        if not startup_ready and self.config.confirm_real_orders and refuse_real_orders:
            self._status_logger.finish_status()
            self.artifacts.append_event(
                "live_startup_refused",
                "__live__",
                {
                    "reason": "symbol_context_startup_readiness_below_threshold",
                    **payload,
                },
            )
            raise LiveStartupError(
                "72ч контекст не готов для real-orders live: "
                f"symbols {ready_symbols}/{symbols_total} "
                f"({ready_symbol_ratio:.1%}), snapshots {ready_snapshots}/{expected_snapshot_count} "
                f"({ready_snapshot_ratio:.1%})"
            )
        return bool(startup_ready)

    def _symbol_context_reprepare_need(self, symbols: list[str]) -> tuple[bool, str, dict[str, object]]:
        summary = self._startup_symbol_context_readiness_summary(symbols)
        now_ms = int(time.time() * 1000)
        snapshot_age_seconds: float | None = None
        if self._last_symbol_context_snapshot_at_ms > 0:
            snapshot_age_seconds = max(
                0.0,
                float(now_ms - int(self._last_symbol_context_snapshot_at_ms)) / 1000.0,
            )
        reasons: list[str] = []
        if float(summary["ready_symbol_ratio"]) < float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SYMBOL_RATIO):
            reasons.append("ready_symbol_ratio_below_threshold")
        if float(summary["ready_snapshot_ratio"]) < float(STARTUP_SYMBOL_CONTEXT_MIN_READY_SNAPSHOT_RATIO):
            reasons.append("ready_snapshot_ratio_below_threshold")
        if snapshot_age_seconds is None:
            reasons.append("snapshot_never_completed")
        elif snapshot_age_seconds >= float(LIVE_CONTEXT_REPREPARE_SNAPSHOT_STALE_SECONDS):
            reasons.append("snapshot_stale")
        if self._last_symbol_context_snapshot_status in {"failed", "cache_storage_unavailable", "empty_universe"}:
            reasons.append(f"snapshot_status={self._last_symbol_context_snapshot_status}")
        payload: dict[str, object] = {
            "snapshot_age_seconds": (
                round(float(snapshot_age_seconds), 3) if snapshot_age_seconds is not None else ""
            ),
            "snapshot_stale_threshold_seconds": int(LIVE_CONTEXT_REPREPARE_SNAPSHOT_STALE_SECONDS),
            "last_symbol_context_snapshot_status": self._last_symbol_context_snapshot_status,
            **summary,
        }
        if not reasons:
            return False, "context_healthy", payload
        return True, "+".join(reasons), payload

    def _live_context_reprepare_safety_payload(self) -> tuple[bool, dict[str, object]]:
        _opened_total, open_positions, _closed_total, orphan_cancelled_total = self._live_counts()
        active_symbols = self._active_live_symbol_count()
        opening_symbols = self._opening_symbol_count()
        with self._state_lock:
            tracked_order_symbols = len(self._order_reconcile_symbols_by_key)
        safe = (
            active_symbols == 0
            and open_positions == 0
            and opening_symbols == 0
            and tracked_order_symbols == 0
        )
        payload = {
            "safe": bool(safe),
            "active_symbols": int(active_symbols),
            "open_positions": int(open_positions),
            "opening_symbols": int(opening_symbols),
            "tracked_order_symbols": int(tracked_order_symbols),
            "orphan_orders_cancelled_total": int(orphan_cancelled_total),
        }
        return bool(safe), payload

    def _maybe_reprepare_symbol_context_if_safe(self, symbols: list[str], *, cycle: int) -> None:
        now_monotonic = time.monotonic()
        runtime_seconds = max(0.0, now_monotonic - self._run_started_monotonic)
        if runtime_seconds < float(LIVE_CONTEXT_REPREPARE_MIN_RUNTIME_SECONDS):
            return
        if (
            self._last_context_reprepare_at_monotonic > 0.0
            and now_monotonic - self._last_context_reprepare_at_monotonic
            < float(LIVE_CONTEXT_REPREPARE_MIN_INTERVAL_SECONDS)
        ):
            return
        needed, reason, readiness_payload = self._symbol_context_reprepare_need(symbols)
        if not needed:
            return
        safe, safety_payload = self._live_context_reprepare_safety_payload()
        if not safe:
            if (
                self._last_context_reprepare_deferred_at_monotonic <= 0.0
                or now_monotonic - self._last_context_reprepare_deferred_at_monotonic
                >= float(LIVE_CONTEXT_REPREPARE_DEFER_LOG_INTERVAL_SECONDS)
            ):
                self._last_context_reprepare_deferred_at_monotonic = now_monotonic
                self.artifacts.append_event(
                    "live_context_reprepare_deferred",
                    "__live__",
                    {
                        "cycle": int(cycle),
                        "reason": reason,
                        "policy": "defer_until_zero_active_zero_positions_zero_opening_zero_tracked_orders",
                        **safety_payload,
                        **readiness_payload,
                    },
                )
                self._startup_status(
                    "переподготовка",
                    (
                        f"отложена · {reason} · активные {safety_payload['active_symbols']} · "
                        f"позиции {safety_payload['open_positions']} · ETA -"
                    ),
                )
            return
        self._last_context_reprepare_at_monotonic = now_monotonic
        self._context_reprepare_total += 1
        self.artifacts.append_event(
            "live_context_reprepare_started",
            "__live__",
            {
                "cycle": int(cycle),
                "reason": reason,
                "reprepare_total": int(self._context_reprepare_total),
                "policy": "state_based_only_when_zero_active_zero_positions_zero_opening_zero_tracked_orders_no_scheduled_restart",
                **safety_payload,
                **readiness_payload,
            },
        )
        self._startup_status("переподготовка", f"старт · {reason} · ETA -")
        self._startup_backfill_symbol_context_cache(symbols, phase="live_reprepare")
        ready = self._validate_startup_symbol_context_readiness(
            symbols,
            stage="переподготовка",
            policy_context="live_reprepare",
            refuse_real_orders=False,
        )
        final_payload = self._startup_symbol_context_readiness_summary(symbols)
        status = "ready" if ready else "not_ready"
        self.artifacts.append_event(
            "live_context_reprepare_completed",
            "__live__",
            {
                "cycle": int(cycle),
                "status": status,
                "reason": reason,
                "reprepare_total": int(self._context_reprepare_total),
                "confirm_real_orders": bool(self.config.confirm_real_orders),
                **final_payload,
            },
        )
        self._startup_status("переподготовка", f"{status} · ETA 0с")
        self._status_logger.finish_status()
        if not ready and self.config.confirm_real_orders:
            self.artifacts.append_event(
                "live_context_reprepare_refused_continue",
                "__live__",
                {
                    "cycle": int(cycle),
                    "reason": "symbol_context_readiness_still_below_threshold_after_safe_reprepare",
                    **final_payload,
                },
            )
            raise LiveDataIntegrityError(
                "72ч контекст остался неготовым после безопасной переподготовки; "
                "real-orders live остановлен без активных символов/позиций"
            )

    def _startup_backfill_symbol_context_cache(self, symbols: list[str], *, phase: str = "startup") -> None:
        status_stage = "контекст 72ч" if phase == "startup" else "переподготовка 72ч"
        if not self.config.symbol_context_snapshot_enabled:
            return
        if self._ohlcv_cache_storage is None:
            self.artifacts.append_event(
                "symbol_context_startup_backfill_skipped",
                "__live__",
                {
                    "status": "cache_storage_unavailable",
                    "phase": phase,
                    "reason": "live_ohlcv_cache_required_for_startup_context_backfill",
                    "policy": "no_fallback_to_subminute_or_synthetic_context",
                    "symbols_total": int(len(symbols)),
                },
            )
            return
        context_timeframes = self._symbol_context_snapshot_timeframes()
        if not context_timeframes:
            self.artifacts.append_event(
                "symbol_context_startup_backfill_skipped",
                "__live__",
                {
                    "status": "empty_timeframes",
                    "phase": phase,
                    "reason": "no_minute_or_higher_levels_timeframes",
                    "policy": "no_subminute_context_backfill",
                    "symbols_total": int(len(symbols)),
                },
            )
            return
        now_ms = int(time.time() * 1000)
        windows = {
            timeframe.value: self._symbol_context_window_bounds(
                timeframe,
                now_ms=now_ms,
                symbols_total=len(symbols),
            )
            for timeframe in context_timeframes
        }
        self.artifacts.append_event(
            "symbol_context_startup_backfill_started",
            "__live__",
            {
                "status": "started",
                "phase": phase,
                "contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
                "policy": "default_on_startup_backfill_levels_timeframes_only_no_subminute_entry_tfs",
                "symbols_total": int(len(symbols)),
                "context_timeframes": [timeframe.value for timeframe in context_timeframes],
                "baseline_candles": int(self.config.baseline_candles),
                "history_days": 3,
                "effective_fresh_ms": int(self._effective_symbol_context_snapshot_fresh_ms(symbols_total=len(symbols))),
                "windows": {
                    timeframe_value: {
                        "context_start_timestamp_ms": int(bounds[0]),
                        "history_start_timestamp_ms": int(bounds[1]),
                        "decision_timestamp_ms": int(bounds[2]),
                    }
                    for timeframe_value, bounds in windows.items()
                },
            },
        )
        self._send_context_preparation_started_telegram(
            phase=phase,
            symbols_total=int(len(symbols)),
            context_timeframes=context_timeframes,
        )
        started_at = time.monotonic()
        fetched_symbol_timeframes = 0
        failed_symbol_timeframes = 0
        fetched_rows_total = 0
        incremental_flushed_rows_total = 0
        incremental_flush_count = 0
        failure_reasons: dict[str, int] = {}
        symbols_total = int(len(symbols))
        chunk_flush_started_at = started_at

        def _chunk_cache_flush_progress(symbol: str, timeframe_value: str, index: int, total: int) -> None:
            eta_text = self._startup_eta_text(
                started_at=chunk_flush_started_at,
                completed=int(index - 1),
                total=int(total),
            )
            self._startup_status(
                status_stage,
                f"запись кеша {index}/{total} · {_compact_symbol(symbol)} {timeframe_value} · ETA {eta_text}",
            )

        for index, symbol in enumerate(symbols, start=1):
            eta_text = self._startup_eta_text(started_at=started_at, completed=int(index - 1), total=symbols_total)
            self._startup_status(
                status_stage,
                f"кеш {index}/{symbols_total} · {_compact_symbol(symbol)} · ETA {eta_text}",
            )
            for timeframe in context_timeframes:
                context_start_ms, _history_start_ms, decision_ts = windows[timeframe.value]
                try:
                    frame = self._fetch_chart_frame(
                        symbol,
                        timeframe,
                        start_timestamp_ms=int(context_start_ms),
                        end_timestamp_ms=int(decision_ts),
                    )
                    fetched_rows_total += int(len(frame))
                    fetched_symbol_timeframes += 1
                except Exception as exc:
                    failed_symbol_timeframes += 1
                    reason = f"{type(exc).__name__}:{str(exc)[:160]}"
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                    self.artifacts.append_event(
                        "symbol_context_startup_backfill_failed",
                        symbol,
                        {
                            "phase": phase,
                            "timeframe": timeframe.value,
                            "reason": reason,
                            "context_start_timestamp_ms": int(context_start_ms),
                            "decision_timestamp_ms": int(decision_ts),
                        },
                    )
            if (
                self._live_ohlcv_write_buffer
                and (
                    len(self._live_ohlcv_write_buffer) >= STARTUP_CONTEXT_BACKFILL_FLUSH_SYMBOL_TIMEFRAMES
                    or self._live_ohlcv_pending_rows >= int(self.config.live_ohlcv_cache_max_buffer_rows)
                )
            ):
                chunk_flush_started_at = time.monotonic()
                chunk_flushed_rows = self._flush_live_ohlcv_cache_if_due(
                    force=True,
                    reason=f"symbol_context_{phase}_backfill_chunk",
                    progress_callback=_chunk_cache_flush_progress,
                    max_symbol_timeframes_override=STARTUP_CONTEXT_BACKFILL_FLUSH_SYMBOL_TIMEFRAMES,
                )
                if chunk_flushed_rows > 0:
                    incremental_flush_count += 1
                    incremental_flushed_rows_total += int(chunk_flushed_rows)
        self._status_logger.finish_status()
        flush_items_total = int(len(self._live_ohlcv_write_buffer))
        flush_started_at = time.monotonic()
        if flush_items_total <= 0:
            self._startup_status(status_stage, "запись кеша 0/0 · ETA 0с")

        def _cache_flush_progress(symbol: str, timeframe_value: str, index: int, total: int) -> None:
            eta_text = self._startup_eta_text(started_at=flush_started_at, completed=int(index - 1), total=int(total))
            self._startup_status(
                status_stage,
                f"запись кеша {index}/{total} · {_compact_symbol(symbol)} {timeframe_value} · ETA {eta_text}",
            )

        final_flushed_rows = self._flush_live_ohlcv_cache_if_due(
            force=True,
            reason=f"symbol_context_{phase}_backfill_final",
            progress_callback=_cache_flush_progress,
        )
        flushed_rows = int(incremental_flushed_rows_total) + int(final_flushed_rows)
        if flush_items_total > 0:
            self._startup_status(status_stage, f"запись кеша {flush_items_total}/{flush_items_total} · ETA 0с")
        snapshot_ok = 0
        snapshot_failed = 0
        snapshot_total = int(len(symbols))
        snapshot_started_at = time.monotonic()
        for snapshot_index, symbol in enumerate(symbols, start=1):
            eta_text = self._startup_eta_text(
                started_at=snapshot_started_at,
                completed=int(snapshot_index - 1),
                total=snapshot_total,
            )
            self._startup_status(
                status_stage,
                f"снимок {snapshot_index}/{snapshot_total} · {_compact_symbol(symbol)} · ETA {eta_text}",
            )
            for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
                if int(levels_timeframe.to_milliseconds()) < int(Timeframe.M1.to_milliseconds()):
                    continue
                snapshot = self._compute_symbol_context_snapshot(
                    symbol,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    now_ms=now_ms,
                )
                self._symbol_context_snapshots[snapshot.key()] = snapshot
                if snapshot.status == "ok":
                    snapshot_ok += 1
                else:
                    snapshot_failed += 1
        if snapshot_total > 0:
            self._startup_status(status_stage, f"снимок {snapshot_total}/{snapshot_total} · ETA 0с")
        self._startup_status(status_stage, "запись snapshot · ETA -")
        output_path = self.artifacts.write_symbol_context_snapshot(list(self._symbol_context_snapshots.values()))
        self._startup_status(status_stage, "запись snapshot · ETA 0с")
        self._status_logger.finish_status()
        self._last_symbol_context_snapshot_at_ms = int(time.time() * 1000)
        status = "ok" if snapshot_failed == 0 else "partial" if snapshot_ok else "failed"
        self._last_symbol_context_snapshot_status = status
        elapsed_seconds = time.monotonic() - started_at
        self.artifacts.append_event(
            "symbol_context_startup_backfill_completed",
            "__live__",
            {
                "status": status,
                "phase": phase,
                "contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
                "policy": "default_on_startup_backfill_levels_timeframes_only_no_subminute_entry_tfs",
                "symbols_total": int(len(symbols)),
                "context_timeframes": [timeframe.value for timeframe in context_timeframes],
                "fetched_symbol_timeframes": int(fetched_symbol_timeframes),
                "failed_symbol_timeframes": int(failed_symbol_timeframes),
                "fetched_rows_total": int(fetched_rows_total),
                "flushed_rows": int(flushed_rows),
                "incremental_flushed_rows": int(incremental_flushed_rows_total),
                "incremental_flush_count": int(incremental_flush_count),
                "final_flushed_rows": int(final_flushed_rows),
                "snapshot_ok_count": int(snapshot_ok),
                "snapshot_failed_count": int(snapshot_failed),
                "failure_reasons": failure_reasons,
                "elapsed_seconds": round(float(elapsed_seconds), 3),
                "output_file": output_path.name,
                "gap_tolerance_min_coverage_ratio": float(self.config.symbol_context_snapshot_min_coverage_ratio),
                "gap_tolerance_max_gap_candles": int(self.config.symbol_context_snapshot_max_gap_candles),
            },
        )

    def _load_cached_window_allow_trailing_gap(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        fetch_missing: bool = False,
    ) -> tuple[pd.DataFrame, str, int | None]:
        storage = self._ohlcv_cache_storage
        if storage is None and not fetch_missing:
            return pd.DataFrame(), "cache_storage_unavailable", None
        timeframe_ms = int(timeframe.to_milliseconds())
        if timeframe_ms <= 0:
            return pd.DataFrame(), "invalid_timeframe", None
        expected_start_ms = (int(start_timestamp_ms) // timeframe_ms) * timeframe_ms
        expected_end_ms = (int(end_timestamp_ms) // timeframe_ms) * timeframe_ms
        if expected_end_ms < expected_start_ms:
            return pd.DataFrame(), "invalid_window", None
        if fetch_missing:
            try:
                frame = _prepare_cached_ohlcv_frame(
                    self._fetch_chart_frame(
                        symbol,
                        timeframe,
                        start_timestamp_ms=expected_start_ms,
                        end_timestamp_ms=expected_end_ms,
                    )
                )
            except Exception as exc:
                return pd.DataFrame(), f"fetch_failed:{type(exc).__name__}:{str(exc)[:120]}", None
            if frame.empty:
                return pd.DataFrame(), "fetch_empty", None
        else:
            assert storage is not None
            result = storage.load_window_result(
                symbol,
                timeframe,
                start_timestamp_ms=expected_start_ms,
                end_timestamp_ms=expected_end_ms,
            )
            frame = _prepare_cached_ohlcv_frame(result.frame)
            if frame.empty:
                status = getattr(result, "status", "empty") or "empty"
                reason = getattr(result, "reason", "cache_window_empty") or "cache_window_empty"
                return pd.DataFrame(), f"{status}:{reason}", None
        timestamps = frame["timestamp"].astype("int64")
        window = frame.loc[(timestamps >= expected_start_ms) & (timestamps <= expected_end_ms)].copy()
        if window.empty:
            return pd.DataFrame(), "cache_window_empty", None
        window.sort_values("timestamp", inplace=True)
        window.reset_index(drop=True, inplace=True)
        min_cached_ms = int(window["timestamp"].iloc[0])
        max_cached_ms = int(window["timestamp"].iloc[-1])
        if min_cached_ms > expected_start_ms:
            return pd.DataFrame(), f"cache_start_gap:{min_cached_ms - expected_start_ms}", None
        effective_end_ms = min(max_cached_ms, expected_end_ms)
        if effective_end_ms < expected_start_ms:
            return pd.DataFrame(), "cache_before_window", None
        missing_ranges = _missing_ohlcv_ranges(
            window,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=effective_end_ms,
            timeframe_ms=timeframe_ms,
        )
        if missing_ranges:
            missing_candles = sum(
                ((int(end_ms) - int(start_ms)) // timeframe_ms) + 1
                for start_ms, end_ms in missing_ranges
            )
            expected_candles = ((int(effective_end_ms) - int(expected_start_ms)) // timeframe_ms) + 1
            present_candles = max(0, int(expected_candles) - int(missing_candles))
            coverage_ratio = float(present_candles) / float(expected_candles) if expected_candles > 0 else 0.0
            max_gap_candles = max(
                ((int(end_ms) - int(start_ms)) // timeframe_ms) + 1
                for start_ms, end_ms in missing_ranges
            )
            min_coverage_ratio = float(self.config.symbol_context_snapshot_min_coverage_ratio)
            max_allowed_gap_candles = int(self.config.symbol_context_snapshot_max_gap_candles)
            if coverage_ratio < min_coverage_ratio or max_gap_candles > max_allowed_gap_candles:
                return pd.DataFrame(), (
                    f"cache_gap:{len(missing_ranges)}:missing_candles={missing_candles}:"
                    f"coverage={coverage_ratio:.6f}:max_gap_candles={max_gap_candles}"
                ), None
        effective_window = window.loc[window["timestamp"].astype("int64") <= effective_end_ms].copy()
        if effective_window.empty:
            return pd.DataFrame(), "cache_window_empty", None
        if missing_ranges:
            return (
                effective_window.sort_values("timestamp").reset_index(drop=True),
                (
                    f"ok_tolerated_gap:{len(missing_ranges)}:missing_candles={missing_candles}:"
                    f"coverage={coverage_ratio:.6f}:max_gap_candles={max_gap_candles}"
                ),
                effective_end_ms,
            )
        return effective_window.sort_values("timestamp").reset_index(drop=True), "ok", effective_end_ms

    def _symbol_context_snapshot_key(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> tuple[str, str, str]:
        return (_position_symbol_key(symbol), levels_timeframe.value, entry_timeframe.value)

    def _effective_symbol_context_snapshot_fresh_ms(self, *, symbols_total: int | None = None) -> int:
        configured_ms = max(1, int(self.config.symbol_context_snapshot_fresh_ms))
        total = int(symbols_total if symbols_total is not None else self._symbol_context_universe_size)
        if total <= 0:
            return configured_ms
        per_cycle = max(1, int(self.config.symbol_context_snapshot_symbols_per_cycle))
        interval_ms = max(1, int(float(self.config.symbol_context_snapshot_interval_seconds) * 1000.0))
        full_refresh_ms = int(math.ceil(float(total) / float(per_cycle)) * interval_ms)
        return max(configured_ms, full_refresh_ms * 2)

    def _remember_symbol_context_priority(
        self,
        symbol: str,
        *,
        reason: str,
        now_ms: int,
        ttl_ms: int | None = None,
    ) -> None:
        symbol_key = _position_symbol_key(symbol)
        if not symbol_key:
            return
        ttl = int(ttl_ms if ttl_ms is not None else self.config.symbol_context_priority_ttl_ms)
        ttl = max(1, ttl)
        expires_at_ms = int(now_ms) + ttl
        with self._state_lock:
            previous = self._symbol_context_priority_requests.get(symbol_key)
            if previous is None:
                self._symbol_context_priority_requests[symbol_key] = LiveSymbolContextPriorityRequest(
                    symbol=symbol,
                    reason=reason,
                    first_seen_ms=int(now_ms),
                    last_seen_ms=int(now_ms),
                    expires_at_ms=expires_at_ms,
                )
                return
            self._symbol_context_priority_requests[symbol_key] = LiveSymbolContextPriorityRequest(
                symbol=symbol or previous.symbol,
                reason=reason or previous.reason,
                first_seen_ms=int(previous.first_seen_ms),
                last_seen_ms=int(now_ms),
                expires_at_ms=max(int(previous.expires_at_ms), expires_at_ms),
                hit_count=int(previous.hit_count) + 1,
            )

    def _symbol_context_priority_requests_snapshot(self, *, now_ms: int) -> tuple[LiveSymbolContextPriorityRequest, ...]:
        with self._state_lock:
            expired = [
                symbol_key
                for symbol_key, request in self._symbol_context_priority_requests.items()
                if int(request.expires_at_ms) <= int(now_ms)
            ]
            for symbol_key in expired:
                self._symbol_context_priority_requests.pop(symbol_key, None)
            return tuple(
                sorted(
                    self._symbol_context_priority_requests.values(),
                    key=lambda request: (int(request.last_seen_ms), int(request.hit_count)),
                    reverse=True,
                )
            )

    def _symbol_context_snapshot_priority_symbols(self, *, now_ms: int) -> tuple[str, ...]:
        by_key: dict[str, str] = {}
        with self._state_lock:
            for position in self._open_positions.values():
                by_key[_position_symbol_key(position.signal.symbol)] = position.signal.symbol
            for state in self._active_symbols.values():
                by_key[_position_symbol_key(state.symbol)] = state.symbol
            for cooldown in self._dependency_retry_cooldowns.values():
                by_key[cooldown.symbol_key] = cooldown.symbol
        for request in self._symbol_context_priority_requests_snapshot(now_ms=now_ms):
            by_key.setdefault(_position_symbol_key(request.symbol), request.symbol)
        with self._state_lock:
            for watch in self._ticker_radar_watch.values():
                by_key.setdefault(_position_symbol_key(watch.symbol), watch.symbol)
            for warm_watch in self._warm_watch.values():
                by_key.setdefault(_position_symbol_key(warm_watch.symbol), warm_watch.symbol)
        return tuple(by_key.values())

    def _symbol_context_priority_reason_counts(self) -> dict[str, int]:
        now_ms = int(time.time() * 1000)
        counts: dict[str, int] = {}
        for request in self._symbol_context_priority_requests_snapshot(now_ms=now_ms):
            reason = request.reason or "unknown"
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def _next_symbol_context_snapshot_symbols(self, symbols: list[str], *, limit: int, now_ms: int) -> tuple[str, ...]:
        unique_symbols = list(dict.fromkeys(symbols))
        self._symbol_context_snapshot_last_round_robin_symbols = ()
        if not unique_symbols or limit <= 0:
            return ()
        universe_by_key = {_position_symbol_key(symbol): symbol for symbol in unique_symbols}
        selected: list[str] = []
        selected_keys: set[str] = set()
        for symbol in self._symbol_context_snapshot_priority_symbols(now_ms=now_ms):
            symbol_key = _position_symbol_key(symbol)
            universe_symbol = universe_by_key.get(symbol_key)
            if universe_symbol is None or symbol_key in selected_keys:
                continue
            selected.append(universe_symbol)
            selected_keys.add(symbol_key)
            if len(selected) >= int(limit):
                return tuple(selected)
        start_index = self._symbol_context_snapshot_cursor % len(unique_symbols)
        round_robin: list[str] = []
        attempts = 0
        while len(selected) + len(round_robin) < int(limit) and attempts < len(unique_symbols):
            symbol = unique_symbols[(start_index + attempts) % len(unique_symbols)]
            attempts += 1
            symbol_key = _position_symbol_key(symbol)
            if symbol_key in selected_keys:
                continue
            round_robin.append(symbol)
            selected_keys.add(symbol_key)
        self._symbol_context_snapshot_last_round_robin_symbols = tuple(round_robin)
        return tuple([*selected, *round_robin])

    def _advance_symbol_context_snapshot_cursor(self, symbols: list[str], *, processed_symbols: tuple[str, ...]) -> None:
        unique_symbols = list(dict.fromkeys(symbols))
        if not unique_symbols or not processed_symbols:
            return
        rr_keys = {_position_symbol_key(symbol) for symbol in self._symbol_context_snapshot_last_round_robin_symbols}
        processed_round_robin = sum(1 for symbol in processed_symbols if _position_symbol_key(symbol) in rr_keys)
        if processed_round_robin <= 0:
            return
        self._symbol_context_snapshot_cursor = (
            (self._symbol_context_snapshot_cursor % len(unique_symbols)) + int(processed_round_robin)
        ) % len(unique_symbols)

    def _maybe_update_symbol_context_snapshots(self, symbols: list[str]) -> LiveSymbolContextSnapshotCycleStats:
        effective_fresh_ms = self._effective_symbol_context_snapshot_fresh_ms(symbols_total=len(symbols))
        cycle_budget_seconds = max(0.0, float(self.config.symbol_context_snapshot_max_cycle_seconds))
        if not self.config.symbol_context_snapshot_enabled:
            return LiveSymbolContextSnapshotCycleStats(
                enabled=False,
                attempted=False,
                status="disabled",
                reason="symbol_context_snapshot_disabled",
                symbols_total=len(symbols),
                cycle_budget_seconds=cycle_budget_seconds,
                effective_fresh_ms=effective_fresh_ms,
            )
        if self._ohlcv_cache_storage is None:
            self._last_symbol_context_snapshot_status = "cache_storage_unavailable"
            return LiveSymbolContextSnapshotCycleStats(
                enabled=True,
                attempted=False,
                status="cache_storage_unavailable",
                reason="live_ohlcv_cache_required_for_cache_only_context_snapshot",
                symbols_total=len(symbols),
                cycle_budget_seconds=cycle_budget_seconds,
                effective_fresh_ms=effective_fresh_ms,
                output_file=self.artifacts.symbol_context_snapshot_path.name,
            )
        now_ms = int(time.time() * 1000)
        latency_sla = self._current_latency_sla_backlog_status(now_ms=now_ms)
        if not latency_sla.optional_scans_allowed:
            self._last_symbol_context_snapshot_status = "skipped_latency_sla"
            self.artifacts.append_event(
                "symbol_context_snapshot_skipped",
                "__live__",
                {
                    "status": "skipped_latency_sla",
                    "reason": latency_sla.reason or LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON,
                    "contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
                    "symbols_total": int(len(symbols)),
                    "cycle_budget_seconds": float(cycle_budget_seconds),
                    "effective_fresh_ms": int(effective_fresh_ms),
                    "policy": "context_snapshot_is_optional_and_never_runs_while_active_or_radar_due_latency_sla_is_breached",
                    **latency_sla.event_payload(),
                },
            )
            return LiveSymbolContextSnapshotCycleStats(
                enabled=True,
                attempted=False,
                status="skipped_latency_sla",
                reason=latency_sla.reason or LATENCY_SLA_OPTIONAL_SCANS_GATED_REASON,
                symbols_total=len(symbols),
                skipped_count=len(symbols),
                cycle_budget_seconds=cycle_budget_seconds,
                effective_fresh_ms=effective_fresh_ms,
                output_file=self.artifacts.symbol_context_snapshot_path.name,
            )
        interval_ms = max(1, int(float(self.config.symbol_context_snapshot_interval_seconds) * 1000.0))
        if self._last_symbol_context_snapshot_at_ms and now_ms - self._last_symbol_context_snapshot_at_ms < interval_ms:
            return LiveSymbolContextSnapshotCycleStats(
                enabled=True,
                attempted=False,
                status="skipped_interval",
                reason="interval_not_elapsed",
                symbols_total=len(symbols),
                cycle_budget_seconds=cycle_budget_seconds,
                effective_fresh_ms=effective_fresh_ms,
                output_file=self.artifacts.symbol_context_snapshot_path.name,
            )
        self._last_symbol_context_snapshot_at_ms = now_ms
        selected_symbols = self._next_symbol_context_snapshot_symbols(
            symbols,
            limit=int(self.config.symbol_context_snapshot_symbols_per_cycle),
            now_ms=now_ms,
        )
        if not selected_symbols:
            self._last_symbol_context_snapshot_status = "empty_universe"
            return LiveSymbolContextSnapshotCycleStats(
                enabled=True,
                attempted=True,
                status="empty_universe",
                reason="no_symbols_to_snapshot",
                symbols_total=len(symbols),
                cycle_budget_seconds=cycle_budget_seconds,
                effective_fresh_ms=effective_fresh_ms,
                output_file=self.artifacts.symbol_context_snapshot_path.name,
            )
        updated_count = 0
        failed_count = 0
        processed_symbols: list[str] = []
        started_at = time.monotonic()
        budget_exhausted = False
        for symbol in selected_symbols:
            if processed_symbols and time.monotonic() - started_at >= cycle_budget_seconds:
                budget_exhausted = True
                break
            for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
                snapshot = self._compute_symbol_context_snapshot(
                    symbol,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    now_ms=now_ms,
                )
                self._symbol_context_snapshots[snapshot.key()] = snapshot
                if snapshot.status == "ok":
                    updated_count += 1
                else:
                    failed_count += 1
            processed_symbols.append(symbol)
            if time.monotonic() - started_at >= cycle_budget_seconds:
                budget_exhausted = len(processed_symbols) < len(selected_symbols)
                if budget_exhausted:
                    break
        self._advance_symbol_context_snapshot_cursor(symbols, processed_symbols=tuple(processed_symbols))
        skipped_count = max(0, len(selected_symbols) - len(processed_symbols))
        output_path = self.artifacts.write_symbol_context_snapshot(list(self._symbol_context_snapshots.values()))
        if budget_exhausted:
            status = "partial_budget" if processed_symbols else "skipped_budget"
            reason = "cycle_budget_exhausted"
        else:
            status = "ok" if failed_count == 0 else "partial" if updated_count else "failed"
            reason = "ok" if failed_count == 0 else "some_snapshots_unavailable" if updated_count else "all_snapshots_unavailable"
        self._last_symbol_context_snapshot_status = status
        elapsed_seconds = time.monotonic() - started_at
        self.artifacts.append_event(
            "symbol_context_snapshot_updated",
            "__live__",
            {
                "status": status,
                "reason": reason,
                "contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
                "source": "cache_only_rolling_context_snapshot",
                "symbols_total": int(len(symbols)),
                "selected_symbols": list(processed_symbols),
                "selected_symbols_count": int(len(processed_symbols)),
                "requested_symbols_count": int(len(selected_symbols)),
                "priority_symbols_count": int(
                    len(selected_symbols) - len(self._symbol_context_snapshot_last_round_robin_symbols)
                ),
                "round_robin_symbols_count": int(len(self._symbol_context_snapshot_last_round_robin_symbols)),
                "priority_reason_counts": self._symbol_context_priority_reason_counts(),
                "skipped_symbols_count": int(skipped_count),
                "skipped_symbols": list(selected_symbols[len(processed_symbols):]),
                "timeframe_pairs": [
                    f"{levels.value}/{entry.value}" for levels, entry in self.config.timeframe_pairs
                ],
                "updated_count": int(updated_count),
                "failed_count": int(failed_count),
                "snapshot_count_total": int(len(self._symbol_context_snapshots)),
                "elapsed_seconds": round(float(elapsed_seconds), 3),
                "cycle_budget_seconds": float(cycle_budget_seconds),
                "configured_fresh_ms": int(self.config.symbol_context_snapshot_fresh_ms),
                "effective_fresh_ms": int(effective_fresh_ms),
                "output_file": output_path.name,
                "policy": "optional_budgeted_context_snapshot_prioritizes_open_active_retryable_radar_warm_symbols_no_sync_precise_scan_fetch",
            },
        )
        return LiveSymbolContextSnapshotCycleStats(
            enabled=True,
            attempted=True,
            status=status,
            reason=reason,
            symbols_total=len(symbols),
            selected_symbols=tuple(processed_symbols),
            updated_count=updated_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            cycle_budget_seconds=cycle_budget_seconds,
            effective_fresh_ms=effective_fresh_ms,
            output_file=output_path.name,
        )

    def _compute_symbol_context_snapshot(
        self,
        symbol: str,
        *,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        now_ms: int,
    ) -> LiveSymbolContextSnapshot:
        started_at = time.monotonic()
        context_timeframe = levels_timeframe
        context_ms = int(context_timeframe.to_milliseconds())
        snapshot_ts = int(now_ms)
        decision_ts = _latest_closed_candle_start_ms(context_timeframe, now_ms=snapshot_ts)
        history_padding_ms = max(
            int(self._effective_symbol_context_snapshot_fresh_ms()),
            int(float(self.config.symbol_context_snapshot_interval_seconds) * 1000.0),
        )
        history_start_ms = decision_ts - 3 * 86_400_000 - history_padding_ms
        context_start_ms = history_start_ms - int(self.config.baseline_candles) * context_ms

        def build_snapshot(
            *,
            status: str,
            reason: str,
            context_cache_end_ms: int | None = None,
            effective_cache_end_ms: int | None = None,
            ignored_tail_ms: int | None = None,
            baseline_status: str = "not_computed",
            baseline_quote_volume_median: float | None = None,
            baseline_trade_count_median: float | None = None,
            baseline_range_pct_median: float | None = None,
            latest_context_close: float | None = None,
            prior_spike_timestamps_ms: tuple[int, ...] = (),
            prior_fast_fade_timestamps_ms: tuple[int, ...] = (),
            prepump_spot_features: dict[str, object] | None = None,
            prepump_spot_windows: str = "",
        ) -> LiveSymbolContextSnapshot:
            return LiveSymbolContextSnapshot(
                symbol=symbol,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                snapshot_timestamp_ms=snapshot_ts,
                status=status,
                reason=reason,
                source="cache_only_rolling_context_snapshot",
                context_timeframe=context_timeframe,
                history_start_timestamp_ms=history_start_ms,
                context_start_timestamp_ms=context_start_ms,
                context_cache_end_timestamp_ms=context_cache_end_ms,
                effective_cache_end_timestamp_ms=effective_cache_end_ms,
                ignored_tail_ms=ignored_tail_ms,
                baseline_candles=int(self.config.baseline_candles),
                baseline_status=baseline_status,
                baseline_quote_volume_median=baseline_quote_volume_median,
                baseline_trade_count_median=baseline_trade_count_median,
                baseline_range_pct_median=baseline_range_pct_median,
                latest_context_close=latest_context_close,
                prior_spike_timestamps_ms=prior_spike_timestamps_ms,
                prior_fast_fade_timestamps_ms=prior_fast_fade_timestamps_ms,
                prepump_spot_features=dict(prepump_spot_features or {}),
                prepump_spot_windows=prepump_spot_windows,
                compute_seconds=time.monotonic() - started_at,
            )

        context_frame, context_status, context_cache_end_ms = self._load_cached_window_allow_trailing_gap(
            symbol,
            context_timeframe,
            start_timestamp_ms=context_start_ms,
            end_timestamp_ms=decision_ts,
            fetch_missing=False,
        )
        if not context_status.startswith("ok") or context_cache_end_ms is None:
            return build_snapshot(
                status="unavailable",
                reason=f"context={context_status}",
                context_cache_end_ms=context_cache_end_ms,
            )
        effective_cache_end_ms = min(int(context_cache_end_ms), int(decision_ts))
        ignored_tail_ms = max(0, int(decision_ts) - int(effective_cache_end_ms))
        if effective_cache_end_ms <= history_start_ms:
            return build_snapshot(
                status="unavailable",
                reason="context_cache_effective_end_before_history_start",
                context_cache_end_ms=int(context_cache_end_ms),
                effective_cache_end_ms=effective_cache_end_ms,
                ignored_tail_ms=ignored_tail_ms,
            )
        prepared = context_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        prepared = prepared.loc[pd.to_numeric(prepared["timestamp"], errors="coerce").le(effective_cache_end_ms)]
        latest_context_close: float | None = None
        if not prepared.empty and "close" in prepared.columns:
            latest_close = float(pd.to_numeric(prepared["close"], errors="coerce").iloc[-1])
            latest_context_close = latest_close if math.isfinite(latest_close) else None
        baseline_status = "not_computed"
        baseline_quote_volume_median: float | None = None
        baseline_trade_count_median: float | None = None
        baseline_range_pct_median: float | None = None
        required_baseline_columns = {"timestamp", "high", "low", "close", "quote_volume", "number_of_trades"}
        missing_baseline_columns = sorted(required_baseline_columns.difference(prepared.columns))
        if missing_baseline_columns:
            baseline_status = "missing_columns:" + ",".join(missing_baseline_columns)
        else:
            baseline = prepared.loc[prepared["timestamp"].astype("int64") < effective_cache_end_ms].tail(
                int(self.config.baseline_candles)
            )
            if len(baseline) < int(self.config.baseline_candles):
                baseline_status = f"insufficient_rows:{len(baseline)}"
            else:
                baseline_status = "ok"
                baseline_quote_volume_median = float(pd.to_numeric(baseline["quote_volume"], errors="coerce").median())
                baseline_trade_count_median = float(pd.to_numeric(baseline["number_of_trades"], errors="coerce").median())
                close = pd.to_numeric(baseline["close"], errors="coerce").replace(0.0, pd.NA)
                ranges = (pd.to_numeric(baseline["high"], errors="coerce") - pd.to_numeric(baseline["low"], errors="coerce")) / close
                baseline_range_pct_median = float(ranges.median())
        prepump_spot_features: dict[str, object] = {}
        prepump_spot_windows = ""
        if self.config.prepump_warm_watch_scoring_enabled:
            prepump_spot_windows = str(self.config.prepump_warm_watch_windows)
            try:
                prepump_spot_features = compute_spot_prepump_window_features(
                    prepared,
                    anchor_ms=effective_cache_end_ms,
                    timeframe_ms=context_ms,
                    windows=parse_prepump_windows(prepump_spot_windows),
                    min_coverage_ratio=float(self.config.prepump_warm_watch_min_coverage_ratio),
                )
            except Exception as exc:
                return build_snapshot(
                    status="unavailable",
                    reason=f"prepump_spot_features_error:{type(exc).__name__}:{str(exc)[:160]}",
                    context_cache_end_ms=int(context_cache_end_ms),
                    effective_cache_end_ms=effective_cache_end_ms,
                    ignored_tail_ms=ignored_tail_ms,
                    baseline_status=baseline_status,
                    baseline_quote_volume_median=baseline_quote_volume_median,
                    baseline_trade_count_median=baseline_trade_count_median,
                    baseline_range_pct_median=baseline_range_pct_median,
                    latest_context_close=latest_context_close,
                    prepump_spot_windows=prepump_spot_windows,
                )

        try:
            from research_tools.anomaly_continuation_lab import AnomalyLabConfig
            from research_tools.anomaly_strategy_backtest import AnomalyBacktestConfig, _collect_symbol_pair_rows

            entry_ms = int(entry_timeframe.to_milliseconds())
            forward_high_candles = max(1, math.ceil(60 * entry_ms / context_ms))
            forward_low_candles = max(1, math.ceil(30 * entry_ms / context_ms))
            lab_config = AnomalyLabConfig(
                cache_dir=self.config.cache_dir or Path("."),
                output_dir=self.config.results_dir,
                timeframe=context_timeframe.value,
                days=3,
                end_timestamp_ms=effective_cache_end_ms,
                baseline_candles=int(self.config.baseline_candles),
                confirmation_candles=int(self.config.confirmation_candles),
                forward_high_candles=forward_high_candles,
                forward_low_candles=forward_low_candles,
                min_quote_ratio_start=float(self.config.min_quote_ratio_start),
                min_trade_ratio_start=float(self.config.min_trade_ratio_start),
            )
            backtest_config = AnomalyBacktestConfig(
                lab_config=lab_config,
                setup_timeframe=context_timeframe.value,
                entry_timeframe=context_timeframe.value,
                feature_contract=SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
            )
            rows = _collect_symbol_pair_rows(
                symbol=symbol,
                setup_frame=prepared,
                entry_frame=prepared,
                config=backtest_config,
                entry_flow_source="live_symbol_context_snapshot_cache_only",
            )
        except Exception as exc:
            return build_snapshot(
                status="unavailable",
                reason=f"compute_error:{type(exc).__name__}:{str(exc)[:160]}",
                context_cache_end_ms=int(context_cache_end_ms),
                effective_cache_end_ms=effective_cache_end_ms,
                ignored_tail_ms=ignored_tail_ms,
                baseline_status=baseline_status,
                baseline_quote_volume_median=baseline_quote_volume_median,
                baseline_trade_count_median=baseline_trade_count_median,
                baseline_range_pct_median=baseline_range_pct_median,
                latest_context_close=latest_context_close,
                prepump_spot_features=prepump_spot_features,
                prepump_spot_windows=prepump_spot_windows,
            )
        prior_spike_timestamps: list[int] = []
        prior_fast_fade_timestamps: list[int] = []
        for row in rows:
            try:
                row_ts = int(row.get("decision_timestamp_ms", 0))
            except (TypeError, ValueError):
                continue
            if history_start_ms <= row_ts < effective_cache_end_ms:
                prior_spike_timestamps.append(row_ts)
                if str(row.get("outcome_label", "")) == "fast_fade":
                    prior_fast_fade_timestamps.append(row_ts)
        return build_snapshot(
            status="ok",
            reason=context_status,
            context_cache_end_ms=int(context_cache_end_ms),
            effective_cache_end_ms=effective_cache_end_ms,
            ignored_tail_ms=ignored_tail_ms,
            baseline_status=baseline_status,
            baseline_quote_volume_median=baseline_quote_volume_median,
            baseline_trade_count_median=baseline_trade_count_median,
            baseline_range_pct_median=baseline_range_pct_median,
            latest_context_close=latest_context_close,
            prior_spike_timestamps_ms=tuple(sorted(set(prior_spike_timestamps))),
            prior_fast_fade_timestamps_ms=tuple(sorted(set(prior_fast_fade_timestamps))),
            prepump_spot_features=prepump_spot_features,
            prepump_spot_windows=prepump_spot_windows,
        )

    def _live_prior_fast_fade_72h(
        self,
        symbol: str,
        *,
        decision_timestamp_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> dict[str, object]:
        decision_ts = int(decision_timestamp_ms)
        cache_key = (symbol, levels_timeframe.value, entry_timeframe.value, decision_ts)
        cached = self._prior_fast_fade_cache.get(cache_key)
        if cached is not None:
            return cached
        setup_ms = int(levels_timeframe.to_milliseconds())
        entry_ms = int(entry_timeframe.to_milliseconds())
        history_start_ms = decision_ts - 3 * 86_400_000
        context_start_ms = history_start_ms - int(self.config.baseline_candles) * setup_ms
        base_result: dict[str, object] = {
            "coverage_policy": "startup_backfilled_cache_only_symbol_context_snapshot_no_precise_scan_fetch",
            "snapshot_contract": SYMBOL_CONTEXT_SNAPSHOT_CONTRACT,
            "history_start_timestamp_ms": history_start_ms,
            "context_timeframe": levels_timeframe.value,
            "context_start_timestamp_ms": context_start_ms,
        }
        snapshot = self._symbol_context_snapshots.get(
            self._symbol_context_snapshot_key(symbol, levels_timeframe, entry_timeframe)
        )
        if snapshot is None:
            result = {
                **base_result,
                "status": "unavailable",
                "reason": "symbol_context_snapshot_missing",
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "context_cache_end_timestamp_ms": "",
                "setup_cache_end_timestamp_ms": "",
                "entry_cache_end_timestamp_ms": "",
                "effective_cache_end_timestamp_ms": "",
                "ignored_tail_ms": "",
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        snapshot_age_ms = int(time.time() * 1000) - int(snapshot.snapshot_timestamp_ms)
        snapshot_details = {
            **base_result,
            "snapshot_status": snapshot.status,
            "snapshot_reason": snapshot.reason,
            "snapshot_source": snapshot.source,
            "snapshot_timestamp_ms": int(snapshot.snapshot_timestamp_ms),
            "snapshot_age_ms": int(snapshot_age_ms),
            "snapshot_fresh_ms": int(self._effective_symbol_context_snapshot_fresh_ms()),
            "snapshot_configured_fresh_ms": int(self.config.symbol_context_snapshot_fresh_ms),
            "baseline_status": snapshot.baseline_status,
            "baseline_quote_volume_median": _symbol_context_csv_float(snapshot.baseline_quote_volume_median),
            "baseline_trade_count_median": _symbol_context_csv_float(snapshot.baseline_trade_count_median),
            "baseline_range_pct_median": _symbol_context_csv_float(snapshot.baseline_range_pct_median),
            "latest_context_close": _symbol_context_csv_float(snapshot.latest_context_close),
            "context_cache_end_timestamp_ms": (
                int(snapshot.context_cache_end_timestamp_ms)
                if snapshot.context_cache_end_timestamp_ms is not None
                else ""
            ),
            "setup_cache_end_timestamp_ms": (
                int(snapshot.context_cache_end_timestamp_ms)
                if snapshot.context_cache_end_timestamp_ms is not None
                else ""
            ),
            "entry_cache_end_timestamp_ms": (
                int(snapshot.context_cache_end_timestamp_ms)
                if snapshot.context_cache_end_timestamp_ms is not None
                else ""
            ),
            "effective_cache_end_timestamp_ms": (
                int(snapshot.effective_cache_end_timestamp_ms)
                if snapshot.effective_cache_end_timestamp_ms is not None
                else ""
            ),
        }
        if snapshot_age_ms > self._effective_symbol_context_snapshot_fresh_ms():
            result = {
                **snapshot_details,
                "status": "unavailable",
                "reason": "symbol_context_snapshot_stale",
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "ignored_tail_ms": "",
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        if snapshot.status != "ok" or snapshot.effective_cache_end_timestamp_ms is None:
            result = {
                **snapshot_details,
                "status": "unavailable",
                "reason": snapshot.reason,
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "ignored_tail_ms": snapshot.ignored_tail_ms if snapshot.ignored_tail_ms is not None else "",
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        if int(snapshot.history_start_timestamp_ms) > history_start_ms:
            result = {
                **snapshot_details,
                "status": "unavailable",
                "reason": "symbol_context_snapshot_history_starts_after_decision_window",
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "ignored_tail_ms": snapshot.ignored_tail_ms if snapshot.ignored_tail_ms is not None else "",
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        visible_end_ms = min(int(decision_ts), int(snapshot.effective_cache_end_timestamp_ms))
        if visible_end_ms <= history_start_ms:
            result = {
                **snapshot_details,
                "status": "unavailable",
                "reason": "symbol_context_snapshot_before_history_start",
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "effective_cache_end_timestamp_ms": int(visible_end_ms),
                "ignored_tail_ms": max(0, int(decision_ts) - int(visible_end_ms)),
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        maturity_ms = max(240, 60) * entry_ms
        mature_cutoff = decision_ts - maturity_ms
        prior_spikes = [
            ts
            for ts in snapshot.prior_spike_timestamps_ms
            if history_start_ms <= int(ts) < visible_end_ms and int(ts) <= mature_cutoff
        ]
        prior_fast_fades = [
            ts
            for ts in snapshot.prior_fast_fade_timestamps_ms
            if history_start_ms <= int(ts) < visible_end_ms and int(ts) <= mature_cutoff
        ]
        result = {
            **snapshot_details,
            "status": "ok",
            "reason": "ok",
            "prior_fast_fade_count_72h": int(len(prior_fast_fades)),
            "prior_spike_count_72h": int(len(prior_spikes)),
            "effective_cache_end_timestamp_ms": int(visible_end_ms),
            "ignored_tail_ms": max(0, int(decision_ts) - int(visible_end_ms)),
            "ignored_tail_entry_candles": max(0, (int(decision_ts) - int(visible_end_ms)) // max(1, entry_ms)),
            "prior_fast_fade_context_forward_high_candles": max(1, math.ceil(60 * entry_ms / max(1, setup_ms))),
            "prior_fast_fade_context_forward_low_candles": max(1, math.ceil(30 * entry_ms / max(1, setup_ms))),
        }
        self._prior_fast_fade_cache[cache_key] = result
        return result

    def _due_subminute_entry_raw_ranges(
        self,
        symbol: str,
        *,
        now_ms: int,
    ) -> list[tuple[int, int]]:
        if not self._subminute_entry_scan_allowed(symbol):
            return []
        ranges: list[tuple[int, int]] = []
        for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
            if int(entry_timeframe.to_milliseconds()) >= int(Timeframe.M1.to_milliseconds()):
                continue
            levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
            latest_closed_entry_ts = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
            setup_start_ts = (latest_closed_entry_ts // levels_timeframe_ms) * levels_timeframe_ms
            if not self._signal_scan_due_for_timeframe(
                symbol,
                levels_timeframe,
                entry_timeframe,
                closed_timestamp_ms=latest_closed_entry_ts,
            ):
                continue
            if self._should_defer_inactive_subminute_pair(symbol, entry_timeframe):
                continue
            ranges.append((int(setup_start_ts), int(now_ms)))
        return _merge_time_ranges(ranges)

    def _prefetch_symbol_subminute_entry_gap_debt(
        self,
        symbol: str,
        *,
        now_ms: int,
        reason: str,
    ) -> None:
        source = self.aggtrade_source
        if source is None:
            return
        requested_ranges = self._due_subminute_entry_raw_ranges(symbol, now_ms=now_ms)
        if not requested_ranges:
            return
        missing_ranges: list[tuple[int, int]] = []
        read_status_counts: dict[str, int] = {}
        ws_rows_total = 0
        buffer_rows_total = 0
        connection_statuses: set[str] = set()
        for start_ms, end_ms in requested_ranges:
            read_result = source.read_rows(
                symbol,
                start_timestamp_ms=int(start_ms),
                end_timestamp_ms=int(end_ms),
            )
            ws_rows_total += int(len(read_result.rows))
            buffer_rows_total += int(read_result.buffer_row_count)
            read_status_counts[read_result.status] = read_status_counts.get(read_result.status, 0) + 1
            if read_result.connection_status:
                connection_statuses.add(str(read_result.connection_status))
            missing_ranges.extend((int(start), int(end)) for start, end in read_result.missing_ranges)
        missing_ranges = _merge_time_ranges(missing_ranges)
        if not missing_ranges:
            return
        missing_total_ms = self._time_ranges_duration_ms(missing_ranges)
        max_backfill_ms = int(self.config.live_ws_aggtrade_max_backfill_ms)
        self._cycle_aggtrade_gap_prefetch_symbols += 1
        self._cycle_aggtrade_gap_prefetch_requested_ranges += int(len(requested_ranges))
        self._cycle_aggtrade_gap_prefetch_missing_ranges += int(len(missing_ranges))
        if missing_total_ms > max_backfill_ms:
            self._cycle_aggtrade_gap_prefetch_pending += 1
            self.artifacts.append_event(
                "aggtrade_rest_gap_prefetch",
                symbol,
                {
                    "status": "coverage_pending",
                    "reason": "missing_total_exceeds_backfill_budget",
                    "scan_mode": self._batch_scan_mode_for_symbol(symbol),
                    "prefetch_reason": reason,
                    "source": source.source_id,
                    "requested_ranges": [f"{start}:{end}" for start, end in requested_ranges],
                    "requested_range_count": int(len(requested_ranges)),
                    "missing_ranges": [f"{start}:{end}" for start, end in missing_ranges],
                    "missing_range_count": int(len(missing_ranges)),
                    "missing_total_ms": int(missing_total_ms),
                    "backfill_max_ms": int(max_backfill_ms),
                    "ws_rows": int(ws_rows_total),
                    "buffer_row_count_total": int(buffer_rows_total),
                    "read_status_counts": read_status_counts,
                    "connection_statuses": sorted(connection_statuses),
                    "backfill_ranges": [],
                    "backfilled_rows": 0,
                },
            )
            return
        request_start_ms = min(start for start, _end in requested_ranges)
        request_end_ms = max(end for _start, end in requested_ranges)
        self._cycle_aggtrade_requests += 1
        rows, backfill_ranges, fetched_rows_total, cache_missing_count = self._fetch_aggtrade_raw_ranges_cached(
            symbol,
            missing_ranges,
            fetch_window_start_ms=int(request_start_ms),
            fetch_window_end_ms=int(request_end_ms),
        )
        for missing_start_ms, missing_end_ms in missing_ranges:
            source.add_backfill_rows(
                symbol,
                _filter_aggtrade_rows_by_time(
                    rows,
                    start_timestamp_ms=int(missing_start_ms),
                    end_timestamp_ms=int(missing_end_ms),
                ),
                start_timestamp_ms=int(missing_start_ms),
                end_timestamp_ms=int(missing_end_ms),
            )
        self._cycle_aggtrade_gap_prefetch_backfill_ranges += int(len(backfill_ranges))
        self._cycle_aggtrade_gap_prefetch_rows += int(fetched_rows_total)
        self.artifacts.append_event(
            "aggtrade_rest_gap_prefetch",
            symbol,
            {
                "status": "backfilled",
                "reason": "coalesced_symbol_subminute_entry_debt",
                "scan_mode": self._batch_scan_mode_for_symbol(symbol),
                "prefetch_reason": reason,
                "source": source.source_id,
                "requested_ranges": [f"{start}:{end}" for start, end in requested_ranges],
                "requested_range_count": int(len(requested_ranges)),
                "missing_ranges": [f"{start}:{end}" for start, end in missing_ranges],
                "missing_range_count": int(len(missing_ranges)),
                "missing_total_ms": int(missing_total_ms),
                "backfill_max_ms": int(max_backfill_ms),
                "cache_missing_range_count": int(cache_missing_count),
                "network_backfill_range_count": int(len(backfill_ranges)),
                "backfill_ranges": backfill_ranges,
                "backfilled_rows": int(fetched_rows_total),
                "ws_rows": int(ws_rows_total),
                "buffer_row_count_total": int(buffer_rows_total),
                "read_status_counts": read_status_counts,
                "connection_statuses": sorted(connection_statuses),
            },
        )

    def _scan_batch(self, symbols: list[str]) -> list[LiveSignal]:
        for symbol in symbols:
            self._count_symbol_scan_mode(symbol)
        if self.config.scan_hot_timeframes_per_symbol:
            return self._scan_batch_by_symbol(symbols)
        now_ms = int(time.time() * 1000)
        for symbol in symbols:
            self._prefetch_symbol_subminute_entry_gap_debt(symbol, now_ms=now_ms, reason="batch_by_timeframe")
        return self._scan_batch_by_timeframe(symbols)

    def _scan_batch_by_symbol(self, symbols: list[str]) -> list[LiveSignal]:
        signals: list[LiveSignal] = []
        now_ms = int(time.time() * 1000)
        setup_cache: dict[tuple[str, str, int], pd.DataFrame] = {}
        entry_cache: dict[tuple[str, str, int, int], pd.DataFrame] = {}
        for symbol in symbols:
            self._prefetch_symbol_subminute_entry_gap_debt(symbol, now_ms=now_ms, reason="batch_by_symbol")
            started_at = time.monotonic()
            due_count = 0
            evaluated_count = 0
            setup_fetch_count = 0
            entry_fetch_count = 0
            signal_count = 0
            retryable_dependency_count = 0
            fetch_failures = 0
            entry_ws_coverage_pending = 0
            skipped_not_due = 0
            skipped_inactive_subminute = 0
            dependency_cooldown_skipped = 0
            dependency_cooldown_expired = 0
            for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
                levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
                setup_lookback_ms = (self.config.baseline_candles + 5) * levels_timeframe_ms
                latest_closed_entry_ts = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
                setup_start_ts = (latest_closed_entry_ts // levels_timeframe_ms) * levels_timeframe_ms
                entry_lookback_start_ms = setup_start_ts
                if not self._signal_scan_due_for_timeframe(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    closed_timestamp_ms=latest_closed_entry_ts,
                ):
                    dependency_key = (
                        _position_symbol_key(symbol),
                        levels_timeframe.value,
                        entry_timeframe.value,
                        int(latest_closed_entry_ts),
                    )
                    with self._state_lock:
                        cooldown_active = self._dependency_retry_cooldown_active_locked(
                            dependency_key,
                            now_ms=now_ms,
                        )
                    if cooldown_active:
                        dependency_cooldown_skipped += 1
                    else:
                        skipped_not_due += 1
                    continue
                due_count += 1
                cooldown_status, cooldown = self._dependency_retry_cooldown_status(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    decision_timestamp_ms=latest_closed_entry_ts,
                    now_ms=now_ms,
                )
                if cooldown_status == "cooldown":
                    dependency_cooldown_skipped += 1
                    continue
                if cooldown_status == "expired":
                    dependency_cooldown_expired += 1
                    if cooldown is not None:
                        self._emit_dependency_retry_expired_event(cooldown, source="dependency_retry_cooldown_scan_gate")
                    continue
                if self._should_defer_inactive_subminute_pair(symbol, entry_timeframe):
                    skipped_inactive_subminute += 1
                    self._cycle_deferred_inactive_subminute_pairs += 1
                    continue
                setup_key = (symbol, levels_timeframe.value, setup_start_ts)
                if setup_key not in setup_cache:
                    try:
                        setup_cache[setup_key] = self._fetch_chart_frame(
                            symbol,
                            levels_timeframe,
                            start_timestamp_ms=setup_start_ts - setup_lookback_ms,
                            end_timestamp_ms=now_ms,
                        )
                        setup_fetch_count += 1
                    except Exception as exc:
                        fetch_failures += 1
                        self.artifacts.append_event(
                            "signal_setup_fetch_failed",
                            symbol,
                            {
                                "levels_tf": levels_timeframe.value,
                                "entry_tf": entry_timeframe.value,
                                "reason": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        continue
                entry_key = (symbol, entry_timeframe.value, entry_lookback_start_ms, now_ms)
                if entry_key not in entry_cache:
                    try:
                        entry_cache[entry_key] = self._fetch_chart_frame(
                            symbol,
                            entry_timeframe,
                            start_timestamp_ms=entry_lookback_start_ms,
                            end_timestamp_ms=now_ms,
                        )
                        entry_fetch_count += 1
                    except LiveWsAggTradeCoveragePending as exc:
                        entry_ws_coverage_pending += 1
                        self.artifacts.append_event(
                            "signal_entry_ws_aggtrade_pending",
                            symbol,
                            {
                                "levels_tf": levels_timeframe.value,
                                "entry_tf": entry_timeframe.value,
                                "reason": str(exc),
                                "ws_status": exc.status,
                                "ws_reason": exc.reason or "",
                                "start_timestamp_ms": exc.start_timestamp_ms,
                                "end_timestamp_ms": exc.end_timestamp_ms,
                                "missing_ranges": [f"{start}:{end}" for start, end in exc.missing_ranges],
                                "backfill_max_ms": int(self.config.live_ws_aggtrade_max_backfill_ms),
                            },
                        )
                        continue
                    except Exception as exc:
                        fetch_failures += 1
                        self.artifacts.append_event(
                            "signal_entry_fetch_failed",
                            symbol,
                            {
                                "levels_tf": levels_timeframe.value,
                                "entry_tf": entry_timeframe.value,
                                "reason": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        continue
                scan_result = self._build_forming_setup_signal(
                    symbol,
                    setup_cache[setup_key],
                    entry_cache[entry_key],
                    now_ms=now_ms,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    setup_start_ts=setup_start_ts,
                    latest_closed_entry_ts=latest_closed_entry_ts,
                    previous_scan_closed_ts=self._previous_signal_scan_closed_at(symbol, levels_timeframe, entry_timeframe),
                )
                evaluated_count += 1
                if scan_result.retryable_dependency:
                    retryable_dependency_count += 1
                    self._register_dependency_retry_cooldown(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        decision_timestamp_ms=scan_result.decision_timestamp_ms,
                        now_ms=now_ms,
                        retry_reason=scan_result.retry_reason,
                        retryable_reasons=scan_result.retryable_reasons,
                    )
                else:
                    self._clear_dependency_retry_cooldown(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        decision_timestamp_ms=scan_result.decision_timestamp_ms,
                    )
                    self._mark_signal_scan_closed_at(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        closed_timestamp_ms=latest_closed_entry_ts,
                    )
                if scan_result.signal is not None:
                    signals.append(scan_result.signal)
                    signal_count += 1
            scan_mode = self._batch_scan_mode_for_symbol(symbol)
            if scan_mode == "precise_DANGER_cold_coverage":
                self._cycle_cold_due_timeframe_count += due_count
                self._cycle_cold_evaluated_timeframe_count += evaluated_count
                self._cycle_cold_retryable_dependency_count += retryable_dependency_count
                self._cycle_cold_signal_count += signal_count
                self._cold_evaluated_timeframe_total += evaluated_count
                self._cold_retryable_dependency_total += retryable_dependency_count
                self._cold_signal_total += signal_count
            self.artifacts.append_event(
                "signal_symbol_scan_summary",
                symbol,
                {
                    "mode": "timeframes_per_symbol",
                    "scan_mode": scan_mode,
                    "danger_cold_coverage_scan": scan_mode == "precise_DANGER_cold_coverage",
                    "subminute_entry_scan_allowed": bool(self._subminute_entry_scan_allowed(symbol)),
                    "timeframe_pairs": [f"{levels.value}/{entry.value}" for levels, entry in self.config.timeframe_pairs],
                    "due_timeframe_count": due_count,
                    "evaluated_timeframe_count": evaluated_count,
                    "skipped_not_due_count": skipped_not_due,
                    "skipped_inactive_subminute_count": skipped_inactive_subminute,
                    "dependency_retry_cooldown_skipped_count": dependency_cooldown_skipped,
                    "dependency_retry_cooldown_expired_count": dependency_cooldown_expired,
                    "setup_fetch_count": setup_fetch_count,
                    "entry_fetch_count": entry_fetch_count,
                    "fetch_failure_count": fetch_failures,
                    "entry_ws_aggtrade_pending_count": entry_ws_coverage_pending,
                    "retryable_dependency_count": retryable_dependency_count,
                    "signal_count": signal_count,
                    "duration_ms": round((time.monotonic() - started_at) * 1000.0, 3),
                },
            )
        return signals

    def _scan_batch_by_timeframe(self, symbols: list[str]) -> list[LiveSignal]:
        signals: list[LiveSignal] = []
        now_ms = int(time.time() * 1000)
        for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
            levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
            entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
            setup_lookback_ms = (self.config.baseline_candles + 5) * levels_timeframe_ms
            latest_closed_entry_ts = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
            setup_start_ts = (latest_closed_entry_ts // levels_timeframe_ms) * levels_timeframe_ms
            entry_lookback_start_ms = setup_start_ts
            for symbol in symbols:
                if not self._signal_scan_due_for_timeframe(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    closed_timestamp_ms=latest_closed_entry_ts,
                ):
                    continue
                cooldown_status, cooldown = self._dependency_retry_cooldown_status(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    decision_timestamp_ms=latest_closed_entry_ts,
                    now_ms=now_ms,
                )
                if cooldown_status == "cooldown":
                    continue
                if cooldown_status == "expired":
                    if cooldown is not None:
                        self._emit_dependency_retry_expired_event(cooldown, source="dependency_retry_cooldown_scan_gate")
                    continue
                if self._should_defer_inactive_subminute_pair(symbol, entry_timeframe):
                    self._cycle_deferred_inactive_subminute_pairs += 1
                    continue
                try:
                    setup_frame = self._fetch_chart_frame(
                        symbol,
                        levels_timeframe,
                        start_timestamp_ms=setup_start_ts - setup_lookback_ms,
                        end_timestamp_ms=now_ms,
                    )
                except Exception as exc:
                    self.artifacts.append_event(
                        "signal_setup_fetch_failed",
                        symbol,
                        {
                            "levels_tf": levels_timeframe.value,
                            "entry_tf": entry_timeframe.value,
                            "reason": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    continue
                try:
                    entry_frame = self._fetch_chart_frame(
                        symbol,
                        entry_timeframe,
                        start_timestamp_ms=entry_lookback_start_ms,
                        end_timestamp_ms=now_ms,
                    )
                except LiveWsAggTradeCoveragePending as exc:
                    self.artifacts.append_event(
                        "signal_entry_ws_aggtrade_pending",
                        symbol,
                        {
                            "levels_tf": levels_timeframe.value,
                            "entry_tf": entry_timeframe.value,
                            "reason": str(exc),
                            "ws_status": exc.status,
                            "ws_reason": exc.reason or "",
                            "start_timestamp_ms": exc.start_timestamp_ms,
                            "end_timestamp_ms": exc.end_timestamp_ms,
                            "missing_ranges": [f"{start}:{end}" for start, end in exc.missing_ranges],
                            "backfill_max_ms": int(self.config.live_ws_aggtrade_max_backfill_ms),
                        },
                    )
                    continue
                except Exception as exc:
                    self.artifacts.append_event(
                        "signal_entry_fetch_failed",
                        symbol,
                        {
                            "levels_tf": levels_timeframe.value,
                            "entry_tf": entry_timeframe.value,
                            "reason": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    continue
                scan_result = self._build_forming_setup_signal(
                    symbol,
                    setup_frame,
                    entry_frame,
                    now_ms=now_ms,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    setup_start_ts=setup_start_ts,
                    latest_closed_entry_ts=latest_closed_entry_ts,
                    previous_scan_closed_ts=self._previous_signal_scan_closed_at(symbol, levels_timeframe, entry_timeframe),
                )
                if self._is_danger_cold_scan_symbol(symbol):
                    self._cycle_cold_due_timeframe_count += 1
                    self._cycle_cold_evaluated_timeframe_count += 1
                    self._cold_evaluated_timeframe_total += 1
                    if scan_result.retryable_dependency:
                        self._cycle_cold_retryable_dependency_count += 1
                        self._cold_retryable_dependency_total += 1
                if scan_result.retryable_dependency:
                    self._register_dependency_retry_cooldown(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        decision_timestamp_ms=scan_result.decision_timestamp_ms,
                        now_ms=now_ms,
                        retry_reason=scan_result.retry_reason,
                        retryable_reasons=scan_result.retryable_reasons,
                    )
                else:
                    self._clear_dependency_retry_cooldown(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        decision_timestamp_ms=scan_result.decision_timestamp_ms,
                    )
                    self._mark_signal_scan_closed_at(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        closed_timestamp_ms=latest_closed_entry_ts,
                    )
                if scan_result.signal is not None:
                    if self._is_danger_cold_scan_symbol(symbol):
                        self._cycle_cold_signal_count += 1
                        self._cold_signal_total += 1
                    signals.append(scan_result.signal)
        return signals

    def _build_forming_setup_signal(
        self,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        *,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        setup_start_ts: int,
        latest_closed_entry_ts: int,
        previous_scan_closed_ts: int | None = None,
    ) -> LiveSignalScanResult:
        def no_signal(
            *,
            decision_timestamp_ms: int | None = None,
            retryable_dependency: bool = False,
            retry_reason: str = "",
            retryable_reasons: tuple[str, ...] = (),
        ) -> LiveSignalScanResult:
            return LiveSignalScanResult(
                signal=None,
                decision_timestamp_ms=decision_timestamp_ms,
                retryable_dependency=retryable_dependency,
                retry_reason=retry_reason,
                retryable_reasons=retryable_reasons,
            )

        if setup_frame.empty or entry_frame.empty:
            self.artifacts.append_event(
                "signal_scan_empty_ohlcv",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_start_timestamp_ms": int(setup_start_ts),
                    "reason": "empty_setup_or_entry_frame",
                },
            )
            return no_signal()
        missing_setup_price = [column for column in REQUIRED_PRICE_COLUMNS if column not in setup_frame.columns]
        missing_setup_flow = [column for column in REQUIRED_FLOW_COLUMNS if column not in setup_frame.columns]
        missing_entry_price = [column for column in REQUIRED_PRICE_COLUMNS if column not in entry_frame.columns]
        missing_entry_flow = [column for column in REQUIRED_FLOW_COLUMNS if column not in entry_frame.columns]
        if missing_setup_price or missing_setup_flow or missing_entry_price or missing_entry_flow:
            self.artifacts.append_event(
                "reject_missing_signal_columns",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "missing_setup_price": missing_setup_price,
                    "missing_setup_flow": missing_setup_flow,
                    "missing_entry_price": missing_entry_price,
                    "missing_entry_flow": missing_entry_flow,
                },
            )
            return no_signal()
        levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
        entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
        setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        setup_history = setup_frame.loc[setup_frame["timestamp"].astype(int) < int(setup_start_ts)].tail(self.config.baseline_candles).copy()
        entry_segment = entry_frame.loc[
            (entry_frame["timestamp"].astype(int) >= int(setup_start_ts))
            & (entry_frame["timestamp"].astype(int) <= int(latest_closed_entry_ts))
        ].copy()
        if len(setup_history) < self.config.baseline_candles or entry_segment.empty:
            return no_signal()
        seed_close = float(setup_history.iloc[-1]["close"])
        synthetic_bucket_count = _count_missing_ohlcv_buckets(
            entry_segment,
            start_timestamp_ms=int(setup_start_ts),
            end_timestamp_ms=int(latest_closed_entry_ts),
            timeframe_ms=entry_timeframe_ms,
        )
        entry_segment = _fill_missing_ohlcv_buckets(
            entry_segment,
            start_timestamp_ms=int(setup_start_ts),
            end_timestamp_ms=int(latest_closed_entry_ts),
            timeframe_ms=entry_timeframe_ms,
            seed_close=seed_close,
        )
        if synthetic_bucket_count > 0:
            self.artifacts.append_event(
                "entry_segment_synthetic_ohlcv_buckets",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_start_timestamp_ms": int(setup_start_ts),
                    "latest_closed_entry_timestamp_ms": int(latest_closed_entry_ts),
                    "synthetic_bucket_count": int(synthetic_bucket_count),
                    "entry_segment_bucket_count": int(len(entry_segment)),
                    "real_entry_segment_bucket_count": int(len(_real_ohlcv_buckets(entry_segment))),
                    "source": "fill_missing_ohlcv_buckets",
                },
            )
        real_entry_segment = _real_ohlcv_buckets(entry_segment)
        if len(real_entry_segment) < self.config.confirmation_candles:
            insufficient_real_only = len(entry_segment) >= self.config.confirmation_candles
            self.artifacts.append_event(
                "reject_insufficient_real_entry_buckets" if insufficient_real_only else "reject_setup_too_early",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_start_timestamp_ms": int(setup_start_ts),
                    "closed_entry_candles": int(len(entry_segment)),
                    "real_closed_entry_candles": int(len(real_entry_segment)),
                    "min_closed_entry_candles": int(self.config.confirmation_candles),
                    "synthetic_bucket_count": int(synthetic_bucket_count),
                    "source": "synthetic_buckets_do_not_count_as_confirmation" if insufficient_real_only else "setup_too_early",
                },
            )
            return no_signal()
        setup_elapsed_fraction = min(1.0, len(entry_segment) * entry_timeframe_ms / levels_timeframe_ms)
        forming_setup = _aggregate_frame_to_candle(entry_segment, timestamp_ms=int(setup_start_ts))
        if forming_setup is None:
            return no_signal()
        decision_ts = int(entry_segment.iloc[-1]["timestamp"])
        key = (symbol, levels_timeframe.value, entry_timeframe.value, decision_ts)
        with self._state_lock:
            if key in self._seen_decisions:
                return no_signal(decision_timestamp_ms=decision_ts)
        freshness = _decision_freshness_details(
            decision_timestamp_ms=decision_ts,
            signal_timeframe=entry_timeframe,
            now_ms=now_ms,
            max_signal_age_ms=self.config.max_signal_age_ms,
        )
        if freshness["signal_age_ms"] > self.config.max_signal_age_ms:
            with self._state_lock:
                self._seen_decisions.add(key)
            self.artifacts.append_event(
                "reject_stale_signal",
                symbol,
                {
                    **freshness,
                    "stage": "prescan",
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_source": "forming_htf_from_entry_tf",
                },
            )
            return no_signal(decision_timestamp_ms=decision_ts)
        category_rejections: list[dict[str, object]] = []
        signal = self._build_signal_from_components(
            symbol=symbol,
            baseline=setup_history,
            setup_row=forming_setup,
            entry_segment=entry_segment,
            now_ms=now_ms,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            setup_source="forming_htf_from_entry_tf",
            setup_elapsed_fraction=setup_elapsed_fraction,
            setup_closed_entry_candles=len(real_entry_segment),
            emit_diagnostics=True,
            category_rejections_out=category_rejections,
        )
        if signal is None:
            retryable_rows = _retryable_category_rejection_rows(category_rejections)
            retryable_reasons = _retryable_category_reasons(category_rejections)
            if retryable_reasons:
                blocked_category_ids = [str(row.get("category_id") or "") for row in retryable_rows if row.get("category_id")]
                blocked_category_labels = [str(row.get("category_label") or "") for row in retryable_rows if row.get("category_label")]
                retryable_details = [
                    {
                        "category_id": row.get("category_id", ""),
                        "category_label": row.get("category_label", ""),
                        "reason": row.get("category_reject_reason") or row.get("reason") or "",
                        "status": (
                            row.get("status")
                            or row.get("data_dependency_status")
                            or row.get("mark_basis_status")
                            or row.get("oi_status")
                            or ""
                        ),
                        "detail_reason": row.get("detail_reason") or row.get("coverage_reason") or "",
                        "dependency_type": row.get("dependency_type", ""),
                        "coverage_policy": row.get("coverage_policy", ""),
                    }
                    for row in retryable_rows
                ]
                self._remember_symbol_context_priority(
                    symbol,
                    reason="retryable_dependency_blocked",
                    now_ms=now_ms,
                    ttl_ms=max(self.config.active_symbol_ttl_ms, self.config.max_signal_age_ms),
                )
                self.artifacts.append_event(
                    "signal_scan_retryable_dependency_blocked",
                    symbol,
                    {
                        "levels_tf": levels_timeframe.value,
                        "entry_tf": entry_timeframe.value,
                        "decision_timestamp_ms": decision_ts,
                        "retryable_dependency_count": int(len(retryable_rows)),
                        "retryable_reasons": list(retryable_reasons),
                        "blocked_category_ids": blocked_category_ids,
                        "blocked_category_labels": blocked_category_labels,
                        "retryable_details": retryable_details,
                        "retry_policy": "do_not_consume_decision_until_stale_or_final_reject",
                        "category_contract": LIVE_CATEGORY_CONTRACT,
                    },
                )
                return no_signal(
                    decision_timestamp_ms=decision_ts,
                    retryable_dependency=True,
                    retry_reason="category_dependency_unavailable",
                    retryable_reasons=retryable_reasons,
                )
            with self._state_lock:
                self._seen_decisions.add(key)
            return no_signal(decision_timestamp_ms=decision_ts)
        gap = _live_scan_gap_details(
            decision_timestamp_ms=decision_ts,
            previous_scan_closed_timestamp_ms=previous_scan_closed_ts,
            entry_timeframe=entry_timeframe,
        )
        signal.previous_live_scan_closed_timestamp_ms = gap["previous_live_scan_closed_timestamp_ms"]
        signal.first_unscanned_decision_timestamp_ms = gap["first_unscanned_decision_timestamp_ms"]
        signal.live_scan_gap_ltf_candles = int(gap["live_scan_gap_ltf_candles"])
        if signal.live_scan_gap_ltf_candles > 0:
            self._start_missed_entry_replay_probe(
                symbol=symbol,
                setup_frame=setup_frame,
                entry_frame=entry_frame,
                now_ms=now_ms,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                current_signal=signal,
            )
        return LiveSignalScanResult(signal=signal, decision_timestamp_ms=decision_ts)

    def _start_missed_entry_replay_probe(
        self,
        *,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        current_signal: LiveSignal,
    ) -> None:
        threading.Thread(
            target=self._run_missed_entry_replay_probe,
            kwargs={
                "symbol": symbol,
                "setup_frame": setup_frame.copy(deep=False),
                "entry_frame": entry_frame.copy(deep=False),
                "now_ms": int(now_ms),
                "levels_timeframe": levels_timeframe,
                "entry_timeframe": entry_timeframe,
                "current_signal": current_signal,
            },
            name=f"missed-entry-probe-{_compact_symbol(symbol)}-{entry_timeframe.value}",
            daemon=True,
        ).start()

    def _run_missed_entry_replay_probe(
        self,
        *,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        current_signal: LiveSignal,
    ) -> None:
        try:
            result = self._probe_first_missed_entry_signal(
                symbol=symbol,
                setup_frame=setup_frame,
                entry_frame=entry_frame,
                now_ms=now_ms,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                current_signal=current_signal,
            )
            self.artifacts.append_event("missed_entry_replay_probe", symbol, result)
        except Exception as exc:
            self.artifacts.append_event(
                "missed_entry_replay_probe_failed",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:1000],
                },
            )

    def _probe_first_missed_entry_signal(
        self,
        *,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        current_signal: LiveSignal,
    ) -> dict[str, object]:
        entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
        levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
        previous_ts = current_signal.previous_live_scan_closed_timestamp_ms
        if previous_ts is None or entry_timeframe_ms <= 0 or levels_timeframe_ms <= 0:
            return {
                "status": "no_previous_scan",
                "levels_tf": levels_timeframe.value,
                "entry_tf": entry_timeframe.value,
                "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
            }
        first_candidate_ts = int(previous_ts) + entry_timeframe_ms
        last_candidate_ts = int(current_signal.decision_timestamp_ms) - entry_timeframe_ms
        if first_candidate_ts > last_candidate_ts:
            return {
                "status": "no_missed_closed_candles",
                "levels_tf": levels_timeframe.value,
                "entry_tf": entry_timeframe.value,
                "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
                "previous_live_scan_closed_timestamp_ms": int(previous_ts),
            }
        max_probe = max(1, int(self.config.signal_scan_backfill_candles))
        all_candidate_timestamps = list(range(first_candidate_ts, last_candidate_ts + 1, entry_timeframe_ms))
        probe_truncated = len(all_candidate_timestamps) > max_probe
        candidate_timestamps = all_candidate_timestamps[:max_probe]
        setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        for candidate_ts in candidate_timestamps:
            setup_start_ts = (int(candidate_ts) // levels_timeframe_ms) * levels_timeframe_ms
            setup_history = setup_frame.loc[setup_frame["timestamp"].astype(int) < setup_start_ts].tail(self.config.baseline_candles).copy()
            if len(setup_history) < self.config.baseline_candles:
                continue
            entry_segment = entry_frame.loc[
                (entry_frame["timestamp"].astype(int) >= setup_start_ts)
                & (entry_frame["timestamp"].astype(int) <= int(candidate_ts))
            ].copy()
            if entry_segment.empty:
                continue
            seed_close = float(setup_history.iloc[-1]["close"])
            synthetic_bucket_count = _count_missing_ohlcv_buckets(
                entry_segment,
                start_timestamp_ms=setup_start_ts,
                end_timestamp_ms=int(candidate_ts),
                timeframe_ms=entry_timeframe_ms,
            )
            entry_segment = _fill_missing_ohlcv_buckets(
                entry_segment,
                start_timestamp_ms=setup_start_ts,
                end_timestamp_ms=int(candidate_ts),
                timeframe_ms=entry_timeframe_ms,
                seed_close=seed_close,
            )
            real_entry_segment = _real_ohlcv_buckets(entry_segment)
            if len(real_entry_segment) < self.config.confirmation_candles:
                continue
            forming_setup = _aggregate_frame_to_candle(entry_segment, timestamp_ms=setup_start_ts)
            if forming_setup is None:
                continue
            setup_elapsed_fraction = min(1.0, len(entry_segment) * entry_timeframe_ms / levels_timeframe_ms)
            signal = self._build_signal_from_components(
                symbol=symbol,
                baseline=setup_history,
                setup_row=forming_setup,
                entry_segment=entry_segment,
                now_ms=now_ms,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                setup_source="forming_htf_from_entry_tf_replay_probe",
                setup_elapsed_fraction=setup_elapsed_fraction,
                setup_closed_entry_candles=len(real_entry_segment),
                emit_diagnostics=False,
            )
            if signal is None:
                continue
            missed_lag_ltf = int((int(current_signal.decision_timestamp_ms) - int(signal.decision_timestamp_ms)) // entry_timeframe_ms)
            return {
                "status": "first_prior_signal_found",
                "levels_tf": levels_timeframe.value,
                "entry_tf": entry_timeframe.value,
                "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
                "previous_live_scan_closed_timestamp_ms": int(previous_ts),
                "first_unscanned_decision_timestamp_ms": int(first_candidate_ts),
                "first_prior_signal_decision_timestamp_ms": int(signal.decision_timestamp_ms),
                "missed_signal_lag_ltf_candles": missed_lag_ltf,
                "missed_signal_lag_ms": missed_lag_ltf * entry_timeframe_ms,
                "candidate_decision_count_total": len(all_candidate_timestamps),
                "probed_decision_count": len(candidate_timestamps),
                "probe_max_candles": max_probe,
                "synthetic_ohlcv_bucket_count": int(synthetic_bucket_count),
                "probe_truncated": bool(probe_truncated),
                "unprobed_newer_decision_count": max(0, len(all_candidate_timestamps) - len(candidate_timestamps)),
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "signal_entry_price": _finite_or_none(signal.entry_price),
                "signal_stop_price": _finite_or_none(signal.stop_price),
                "signal_tp1_price": _finite_or_none(signal.tp1_price),
            }
        status = "no_prior_signal_found_in_probed_prefix" if probe_truncated else "no_prior_signal_found"
        return {
            "status": status,
            "levels_tf": levels_timeframe.value,
            "entry_tf": entry_timeframe.value,
            "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
            "previous_live_scan_closed_timestamp_ms": int(previous_ts),
            "first_unscanned_decision_timestamp_ms": int(first_candidate_ts),
            "last_probed_decision_timestamp_ms": int(candidate_timestamps[-1]),
            "candidate_decision_count_total": len(all_candidate_timestamps),
            "probed_decision_count": len(candidate_timestamps),
            "probe_max_candles": max_probe,
            "probe_truncated": bool(probe_truncated),
            "unprobed_newer_decision_count": max(0, len(all_candidate_timestamps) - len(candidate_timestamps)),
        }

    def _build_signal_from_components(
        self,
        *,
        symbol: str,
        baseline: pd.DataFrame,
        setup_row: pd.Series,
        entry_segment: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        setup_source: str,
        setup_elapsed_fraction: float,
        setup_closed_entry_candles: int,
        emit_diagnostics: bool = True,
        category_rejections_out: list[dict[str, object]] | None = None,
        allow_exchange_context_fetch: bool = True,
        frozen_exchange_context: dict[str, object] | None = None,
    ) -> LiveSignal | None:
        if baseline.empty or entry_segment.empty:
            return None
        decision = entry_segment.iloc[-1]
        baseline_quote = float(pd.to_numeric(baseline["quote_volume"], errors="coerce").median())
        baseline_trades = float(pd.to_numeric(baseline["number_of_trades"], errors="coerce").median())
        start_quote = float(setup_row["quote_volume"])
        start_trades = float(setup_row["number_of_trades"])
        start_open = float(setup_row["open"])
        start_close = float(setup_row["close"])
        start_high = float(setup_row["high"])
        start_low = float(setup_row["low"])
        start_ret = _safe_divide(start_close - start_open, start_open)
        abs_start_ret = abs(start_ret) if math.isfinite(start_ret) else float("nan")
        baseline_avg_trade_quote = _safe_divide(baseline_quote, baseline_trades)
        start_avg_trade_quote = _safe_divide(start_quote, start_trades)
        start_avg_trade_ratio = _safe_divide(start_avg_trade_quote, baseline_avg_trade_quote)
        raw_quote_ratio_for_return = _safe_divide(start_quote, baseline_quote)
        start_quote_ratio_per_abs_return = _safe_divide(raw_quote_ratio_for_return, abs_start_ret)
        raw_trade_ratio_for_return = _safe_divide(start_trades, baseline_trades)
        start_trade_ratio_per_abs_return = _safe_divide(raw_trade_ratio_for_return, abs_start_ret)
        baseline_range_pct = float(
            ((baseline["high"].astype(float) - baseline["low"].astype(float)) / baseline["close"].astype(float).replace(0.0, pd.NA)).median()
        )
        start_range_pct_ratio = _safe_divide(_safe_divide(start_high - start_low, start_open), baseline_range_pct)
        raw_quote_ratio = _safe_divide(start_quote, baseline_quote)
        raw_trade_ratio = _safe_divide(start_trades, baseline_trades)
        elapsed_for_ratio = max(1e-9, min(1.0, float(setup_elapsed_fraction)))
        if setup_source.startswith("forming_htf"):
            quote_ratio = _safe_divide(raw_quote_ratio, elapsed_for_ratio)
            trade_ratio = _safe_divide(raw_trade_ratio, elapsed_for_ratio)
            min_raw_quote_ratio = self.config.min_quote_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
            min_raw_trade_ratio = self.config.min_trade_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
        else:
            quote_ratio = raw_quote_ratio
            trade_ratio = raw_trade_ratio
            min_raw_quote_ratio = self.config.min_quote_ratio_start
            min_raw_trade_ratio = self.config.min_trade_ratio_start
        if not math.isfinite(quote_ratio) or not math.isfinite(trade_ratio) or not math.isfinite(raw_quote_ratio) or not math.isfinite(raw_trade_ratio):
            if emit_diagnostics:
                self.artifacts.append_event(
                    "reject_invalid_flow_ratios",
                    symbol,
                    {
                        "baseline_quote": _finite_or_none(baseline_quote),
                        "baseline_trades": _finite_or_none(baseline_trades),
                        "start_quote": _finite_or_none(start_quote),
                        "start_trades": _finite_or_none(start_trades),
                        "raw_quote_ratio": _finite_or_none(raw_quote_ratio),
                        "raw_trade_ratio": _finite_or_none(raw_trade_ratio),
                        "quote_pace_ratio": _finite_or_none(quote_ratio),
                        "trade_pace_ratio": _finite_or_none(trade_ratio),
                        "setup_elapsed_fraction": float(setup_elapsed_fraction),
                        "decision_timestamp_ms": int(decision["timestamp"]),
                        "setup_source": setup_source,
                    },
                )
            return None
        if (
            quote_ratio < self.config.min_quote_ratio_start
            or trade_ratio < self.config.min_trade_ratio_start
            or raw_quote_ratio < min_raw_quote_ratio
            or raw_trade_ratio < min_raw_trade_ratio
        ):
            if emit_diagnostics:
                self.artifacts.append_event(
                    "reject_weak_start_flow",
                    symbol,
                    {
                        "raw_quote_ratio": raw_quote_ratio,
                        "raw_trade_ratio": raw_trade_ratio,
                        "quote_pace_ratio": quote_ratio,
                        "trade_pace_ratio": trade_ratio,
                        "min_quote_pace_ratio": self.config.min_quote_ratio_start,
                        "min_trade_pace_ratio": self.config.min_trade_ratio_start,
                        "min_raw_quote_ratio": min_raw_quote_ratio,
                        "min_raw_trade_ratio": min_raw_trade_ratio,
                        "setup_elapsed_fraction": float(setup_elapsed_fraction),
                        "decision_timestamp_ms": int(decision["timestamp"]),
                        "setup_source": setup_source,
                    },
                )
            return None

        latest_decision_ts = int(decision["timestamp"])
        decision_available_ms = latest_decision_ts + int(entry_timeframe.to_milliseconds())
        if emit_diagnostics and 0 <= now_ms - decision_available_ms <= self.config.max_signal_age_ms:
            self._mark_active_symbol(
                symbol,
                reason="pump_flow_candidate",
                now_ms=now_ms,
                ttl_ms=self.config.active_symbol_ttl_ms,
                decision_timestamp_ms=latest_decision_ts,
            )

        segment_high = float(max(start_high, pd.to_numeric(entry_segment["high"], errors="coerce").max()))
        segment_low = float(min(start_low, pd.to_numeric(entry_segment["low"], errors="coerce").min()))
        impulse_range = segment_high - segment_low
        prior_whipsaw = _prior_up_down_whipsaw_to_impulse_range(baseline, impulse_range=impulse_range)
        decision_close = float(decision["close"])
        price_retention = _safe_divide(decision_close - start_open, segment_high - start_open)
        real_entry_segment = _real_ohlcv_buckets(entry_segment)
        metric_entry_segment = real_entry_segment if not real_entry_segment.empty else entry_segment
        verticality = compute_start_verticality_metrics(metric_entry_segment)
        verticality_score = float(verticality["start_verticality_score"])
        activation_price = start_open + max(0.0, start_close - start_open) * 0.50
        hold_count = int((metric_entry_segment["close"].astype(float) >= activation_price).sum())
        flow_hold_count = int(
            (
                pd.to_numeric(metric_entry_segment["quote_volume"], errors="coerce").ge(max(0.35 * start_quote, 3.0 * baseline_quote))
                & pd.to_numeric(metric_entry_segment["number_of_trades"], errors="coerce").ge(max(0.35 * start_trades, 3.0 * baseline_trades))
            ).sum()
        )
        start_range = start_high - start_low
        start_lower_wick_to_range = _safe_divide(min(start_open, start_close) - start_low, start_range)
        start_upper_wick_to_range = _safe_divide(start_high - max(start_open, start_close), start_range)
        setup_with_current = pd.concat([baseline, pd.DataFrame([setup_row.to_dict()])], ignore_index=True)
        ema20 = setup_with_current["close"].astype(float).ewm(span=20, adjust=False).mean()
        decision_ema20 = float(ema20.iloc[-1])
        previous_stop = segment_low - self.config.stop_buffer_range_fraction * impulse_range
        stop_price = max(previous_stop, decision_ema20)
        entry_price = decision_close
        risk = entry_price - stop_price
        initial_risk_pct = _safe_divide(risk, entry_price)
        if not math.isfinite(risk) or risk <= 0.0:
            if emit_diagnostics:
                if math.isfinite(previous_stop) and math.isfinite(decision_ema20):
                    if abs(previous_stop - decision_ema20) <= max(abs(entry_price) * 1e-12, 1e-12):
                        stop_source = "structural_and_ema20_tie"
                    elif decision_ema20 > previous_stop:
                        stop_source = "ema20"
                    else:
                        stop_source = "structural"
                elif math.isfinite(previous_stop):
                    stop_source = "structural"
                elif math.isfinite(decision_ema20):
                    stop_source = "ema20"
                else:
                    stop_source = "non_finite"
                risk_side = (
                    "non_finite"
                    if not math.isfinite(risk) or not math.isfinite(stop_price) or not math.isfinite(entry_price)
                    else "stop_above_entry"
                    if stop_price > entry_price
                    else "zero_risk"
                    if stop_price == entry_price
                    else "unknown"
                )
                stop_above_entry_pct = (
                    _safe_divide(stop_price - entry_price, entry_price)
                    if math.isfinite(stop_price) and math.isfinite(entry_price) and entry_price != 0.0
                    else float("nan")
                )
                self._clear_active_symbol(symbol, reason="entry_below_initial_stop")
                self.artifacts.append_event(
                    "reject_entry_below_initial_stop",
                    symbol,
                    {
                        "entry_price": _finite_or_none(entry_price),
                        "stop_price": _finite_or_none(stop_price),
                        "risk": _finite_or_none(risk),
                        "risk_side": risk_side,
                        "stop_source": stop_source,
                        "previous_stop": _finite_or_none(previous_stop),
                        "decision_ema20": _finite_or_none(decision_ema20),
                        "stop_above_entry_pct": _finite_or_none(stop_above_entry_pct),
                        "decision_timestamp_ms": int(decision["timestamp"]),
                        "setup_source": setup_source,
                    },
                )
            return None
        if not math.isfinite(initial_risk_pct) or initial_risk_pct > self.config.max_initial_risk_pct:
            if emit_diagnostics:
                self._clear_active_symbol(symbol, reason="initial_risk_too_wide")
                self.artifacts.append_event(
                    "reject_initial_risk_too_wide",
                    symbol,
                    {
                        "initial_risk_pct": _finite_or_none(initial_risk_pct),
                        "max": self.config.max_initial_risk_pct,
                        "decision_timestamp_ms": int(decision["timestamp"]),
                        "setup_source": setup_source,
                    },
                )
            return None
        base_tp1_price = entry_price + risk
        tp1_price, tp1_round_step = _round_up_tp1_to_market_number(
            base_tp1_price,
            reference_price=entry_price,
            movement=max(risk, segment_high - segment_low),
        )

        category_rejections: list[dict[str, object]] = []
        oi_change_loaded = False
        oi_change: float | None = None
        oi_change_reason: str | None = None
        mark_basis_loaded = False
        mark_basis: LiveMarkBasisResult | None = None
        next_taker_share_loaded = False
        next_taker_share = float("nan")
        valid_taker_share_count = 0
        total_taker_share_rows = 0
        start_taker_share_delta_loaded = False
        start_taker_share_delta = float("nan")
        prior_fast_fade_loaded = False
        prior_fast_fade_result: dict[str, object] | None = None

        def record_category_reject(
            category: LivePumpCategory,
            symbol: str,
            reason: str,
            details: dict[str, object],
            *,
            emit: bool = True,
        ) -> dict[str, object]:
            enriched_details = {
                "levels_tf": levels_timeframe.value,
                "entry_tf": entry_timeframe.value,
                "setup_source": setup_source,
                "setup_elapsed_fraction": float(setup_elapsed_fraction),
                "setup_closed_entry_candles": int(setup_closed_entry_candles),
                **details,
            }
            row = self._record_category_reject(
                category,
                symbol,
                reason,
                enriched_details,
                emit=bool(emit_diagnostics and emit),
            )
            if category_rejections_out is not None:
                category_rejections_out.append(row)
            return row

        for category in self._categories_for_timeframe(levels_timeframe, entry_timeframe):
            max_prior_fast_fade = _category_value(category, self.config, "max_prior_fast_fade_count_72h")
            if max_prior_fast_fade is not None:
                if not prior_fast_fade_loaded:
                    frozen_prior_fast_fade = _frozen_prior_fast_fade_from_context(frozen_exchange_context)
                    if frozen_prior_fast_fade is not None:
                        prior_fast_fade_result = frozen_prior_fast_fade
                    else:
                        prior_fast_fade_result = self._live_prior_fast_fade_72h(
                            symbol,
                            decision_timestamp_ms=int(decision["timestamp"]),
                            levels_timeframe=levels_timeframe,
                            entry_timeframe=entry_timeframe,
                        )
                    prior_fast_fade_loaded = True
                assert prior_fast_fade_result is not None
                prior_fast_fade_count = prior_fast_fade_result.get("prior_fast_fade_count_72h")
                prior_fast_fade_details = {
                    **prior_fast_fade_result,
                    "max_prior_fast_fade_count_72h": int(max_prior_fast_fade),
                    "decision_timestamp_ms": int(decision["timestamp"]),
                }
                if prior_fast_fade_result.get("status") != "ok" or prior_fast_fade_count is None:
                    category_rejections.append(
                        record_category_reject(
                            category,
                            symbol,
                            "reject_prior_fast_fade_filter_unavailable",
                            prior_fast_fade_details,
                            emit=False,
                        )
                    )
                    continue
                if int(prior_fast_fade_count) > int(max_prior_fast_fade):
                    category_rejections.append(record_category_reject(category, symbol, "reject_prior_fast_fade_72h", prior_fast_fade_details))
                    continue

            min_mark_basis = _category_value(category, self.config, "min_mark_close_vs_decision_close_basis")
            if min_mark_basis is not None:
                if not mark_basis_loaded:
                    frozen_mark_basis = _frozen_mark_basis_from_context(frozen_exchange_context)
                    if frozen_mark_basis is not None:
                        mark_basis = frozen_mark_basis
                    elif allow_exchange_context_fetch:
                        mark_basis = self._fetch_live_mark_basis(
                            symbol,
                            decision_timestamp_ms=int(decision["timestamp"]),
                            decision_close=decision_close,
                        )
                    else:
                        mark_basis = LiveMarkBasisResult(None, "replay_exchange_context_fetch_disabled")
                    mark_basis_loaded = True
                basis_value = mark_basis.value if mark_basis is not None else None
                mark_details = {
                    "mark_close_vs_decision_close_basis": _finite_or_none(basis_value),
                    "mark_basis_status": mark_basis.reason if mark_basis is not None else "not_loaded",
                    "mark_timestamp_ms": mark_basis.timestamp_ms if mark_basis is not None and mark_basis.timestamp_ms is not None else "",
                    "mark_age_ms": mark_basis.age_ms if mark_basis is not None and mark_basis.age_ms is not None else "",
                    "min": min_mark_basis,
                    "dependency_type": "mark_price_context",
                    "decision_timestamp_ms": int(decision["timestamp"]),
                }
                if basis_value is None or not math.isfinite(basis_value):
                    category_rejections.append(
                        record_category_reject(
                            category,
                            symbol,
                            "reject_mark_basis_unavailable",
                            mark_details,
                            emit=False,
                        )
                    )
                    continue
                if basis_value < min_mark_basis:
                    category_rejections.append(
                        record_category_reject(
                            category,
                            symbol,
                            "reject_mark_basis_below_min",
                            mark_details,
                        )
                    )
                    continue
            max_start_quote_ratio = _category_value(category, self.config, "max_start_quote_ratio")
            if max_start_quote_ratio is not None and quote_ratio > max_start_quote_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_exhausted_quote_ratio", {"quote_ratio": quote_ratio, "max": max_start_quote_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_start_trade_ratio = _category_value(category, self.config, "max_start_trade_ratio")
            if max_start_trade_ratio is not None and trade_ratio > max_start_trade_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_exhausted_trade_ratio", {"trade_ratio": trade_ratio, "max": max_start_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_avg_trade_ratio = _category_value(category, self.config, "max_start_avg_trade_quote_size_ratio")
            if max_avg_trade_ratio is not None and not math.isfinite(start_avg_trade_ratio):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_avg_trade_quote_size_ratio", {"ratio": _finite_or_none(start_avg_trade_ratio), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_avg_trade_ratio is not None and start_avg_trade_ratio > max_avg_trade_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_large_print_signature", {"ratio": start_avg_trade_ratio, "max": max_avg_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_quote_per_return = _category_value(category, self.config, "max_start_quote_ratio_per_abs_return")
            if max_quote_per_return is not None and not math.isfinite(start_quote_ratio_per_abs_return):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_quote_ratio_per_abs_return", {"ratio": _finite_or_none(start_quote_ratio_per_abs_return), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_quote_per_return is not None and start_quote_ratio_per_abs_return > max_quote_per_return:
                category_rejections.append(record_category_reject(category, symbol, "reject_poor_effort_per_return", {"ratio": start_quote_ratio_per_abs_return, "max": max_quote_per_return, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_trade_per_return = _category_value(category, self.config, "max_start_trade_ratio_per_abs_return")
            if max_trade_per_return is not None and not math.isfinite(start_trade_ratio_per_abs_return):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_trade_ratio_per_abs_return", {"ratio": _finite_or_none(start_trade_ratio_per_abs_return), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_trade_per_return is not None and start_trade_ratio_per_abs_return > max_trade_per_return:
                category_rejections.append(record_category_reject(category, symbol, "reject_poor_trade_effort_per_return", {"ratio": start_trade_ratio_per_abs_return, "max": max_trade_per_return, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_range_ratio = _category_value(category, self.config, "max_start_range_pct_ratio_to_baseline")
            if max_range_ratio is not None and not math.isfinite(start_range_pct_ratio):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_range_expansion_ratio", {"ratio": _finite_or_none(start_range_pct_ratio), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_range_ratio is not None and start_range_pct_ratio > max_range_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_extreme_range_expansion", {"ratio": start_range_pct_ratio, "max": max_range_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_prior_whipsaw = self.config.max_prior_up_down_whipsaw_to_impulse_range
            if max_prior_whipsaw is not None and not math.isfinite(prior_whipsaw):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_prior_whipsaw", {"prior_up_down_whipsaw_to_impulse_range": _finite_or_none(prior_whipsaw), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_prior_whipsaw is not None and prior_whipsaw > max_prior_whipsaw:
                category_rejections.append(record_category_reject(category, symbol, "reject_prior_up_down_whipsaw", {"prior_up_down_whipsaw_to_impulse_range": prior_whipsaw, "max": max_prior_whipsaw, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_flow_hold_count = _category_value(category, self.config, "min_flow_hold_count")
            if min_flow_hold_count is not None and flow_hold_count < int(min_flow_hold_count):
                category_rejections.append(record_category_reject(category, symbol, "reject_low_flow_hold_count", {"flow_hold_count": flow_hold_count, "min": int(min_flow_hold_count), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_lower_wick = _category_value(category, self.config, "min_start_lower_wick_to_range")
            if min_lower_wick is not None:
                if not math.isfinite(start_lower_wick_to_range) or start_lower_wick_to_range <= min_lower_wick:
                    category_rejections.append(record_category_reject(category, symbol, "reject_low_start_lower_wick", {"start_lower_wick_to_range": _finite_or_none(start_lower_wick_to_range), "min": min_lower_wick, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            max_upper_wick = _category_value(category, self.config, "max_start_upper_wick_to_range")
            if max_upper_wick is not None:
                if not math.isfinite(start_upper_wick_to_range) or start_upper_wick_to_range > max_upper_wick:
                    category_rejections.append(record_category_reject(category, symbol, "reject_high_start_upper_wick", {"start_upper_wick_to_range": _finite_or_none(start_upper_wick_to_range), "max": max_upper_wick, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            if not math.isfinite(price_retention):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_price_retention", {"price_retention": _finite_or_none(price_retention), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if price_retention < self.config.min_price_retention:
                category_rejections.append(record_category_reject(category, symbol, "reject_low_price_retention", {"price_retention": price_retention, "min": self.config.min_price_retention, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_price_retention = _category_value(category, self.config, "max_price_retention")
            if max_price_retention is not None and price_retention > max_price_retention:
                category_rejections.append(record_category_reject(category, symbol, "reject_overextended_retention", {"price_retention": price_retention, "max": max_price_retention, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_next_taker_share = _category_value(category, self.config, "min_next_taker_buy_quote_share")
            if min_next_taker_share is not None:
                if "taker_buy_quote_volume" not in entry_segment.columns:
                    category_rejections.append(record_category_reject(category, symbol, "reject_missing_taker_buy_share", {"required": min_next_taker_share, "dependency_type": "taker_buy_quote_volume", "data_dependency_status": "missing_column", "decision_timestamp_ms": int(decision["timestamp"])}, emit=False))
                    continue
                if not next_taker_share_loaded:
                    taker_quote = pd.to_numeric(entry_segment["taker_buy_quote_volume"], errors="coerce")
                    quote_volume = pd.to_numeric(entry_segment["quote_volume"], errors="coerce")
                    valid_taker_share_rows = taker_quote.notna() & quote_volume.notna() & quote_volume.gt(0.0)
                    valid_taker_share_count = int(valid_taker_share_rows.sum())
                    total_taker_share_rows = int(len(entry_segment))
                    if valid_taker_share_count <= 0:
                        next_taker_share = float("nan")
                    else:
                        next_taker_share = float((taker_quote[valid_taker_share_rows] / quote_volume[valid_taker_share_rows]).mean())
                    next_taker_share_loaded = True
                if not math.isfinite(next_taker_share):
                    category_rejections.append(record_category_reject(category, symbol, "reject_invalid_taker_buy_share", {"share": _finite_or_none(next_taker_share), "valid_taker_share_rows": valid_taker_share_count, "total_taker_share_rows": total_taker_share_rows, "taker_share_contract": "mean_over_valid_quote_volume_rows", "dependency_type": "taker_buy_quote_volume", "data_dependency_status": "no_valid_quote_volume_rows", "decision_timestamp_ms": int(decision["timestamp"])}, emit=False))
                    continue
                if next_taker_share < min_next_taker_share:
                    category_rejections.append(record_category_reject(category, symbol, "reject_weak_next_taker_buy_share", {"share": next_taker_share, "min": min_next_taker_share, "valid_taker_share_rows": valid_taker_share_count, "total_taker_share_rows": total_taker_share_rows, "taker_share_contract": "mean_over_valid_quote_volume_rows", "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            max_start_taker_delta = _category_value(category, self.config, "max_start_taker_buy_quote_share_delta")
            if max_start_taker_delta is not None:
                if "taker_buy_quote_volume" not in entry_segment.columns or "taker_buy_quote_volume" not in baseline.columns:
                    category_rejections.append(record_category_reject(category, symbol, "reject_missing_start_taker_buy_delta", {"required_max": max_start_taker_delta, "dependency_type": "taker_buy_quote_volume", "data_dependency_status": "missing_column", "decision_timestamp_ms": int(decision["timestamp"])}, emit=False))
                    continue
                if not start_taker_share_delta_loaded:
                    start_quote_volume = float(pd.to_numeric(pd.Series([entry_segment.iloc[0]["quote_volume"]]), errors="coerce").iloc[0])
                    start_taker_quote = float(pd.to_numeric(pd.Series([entry_segment.iloc[0]["taker_buy_quote_volume"]]), errors="coerce").iloc[0])
                    baseline_quote_volume = pd.to_numeric(baseline["quote_volume"], errors="coerce")
                    baseline_taker_quote = pd.to_numeric(baseline["taker_buy_quote_volume"], errors="coerce")
                    baseline_share = float((baseline_taker_quote / baseline_quote_volume.replace(0.0, pd.NA)).median())
                    start_share = _safe_divide(start_taker_quote, start_quote_volume)
                    start_taker_share_delta = start_share - baseline_share
                    start_taker_share_delta_loaded = True
                if not math.isfinite(start_taker_share_delta):
                    category_rejections.append(record_category_reject(category, symbol, "reject_invalid_start_taker_buy_delta", {"delta": _finite_or_none(start_taker_share_delta), "dependency_type": "taker_buy_quote_volume", "data_dependency_status": "invalid_start_or_baseline_share", "decision_timestamp_ms": int(decision["timestamp"])}, emit=False))
                    continue
                if start_taker_share_delta > max_start_taker_delta:
                    category_rejections.append(record_category_reject(category, symbol, "reject_start_taker_buy_delta_above_max", {"delta": start_taker_share_delta, "max": max_start_taker_delta, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            if not math.isfinite(verticality_score):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_verticality", {"verticality_score": _finite_or_none(verticality_score), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if verticality_score < self.config.min_verticality_score:
                category_rejections.append(record_category_reject(category, symbol, "reject_low_verticality", {"verticality_score": verticality_score, "min": self.config.min_verticality_score, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if hold_count < self.config.min_hold_count:
                category_rejections.append(record_category_reject(category, symbol, "reject_low_hold_count", {"hold_count": hold_count, "min": self.config.min_hold_count, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_oi_change = _category_value(category, self.config, "min_oi_change_pct_3x5m")
            if min_oi_change is not None:
                if not oi_change_loaded:
                    frozen_oi_result = _frozen_oi_change_from_context(frozen_exchange_context)
                    if frozen_oi_result is not None:
                        oi_result = frozen_oi_result
                    elif allow_exchange_context_fetch:
                        oi_result = self._fetch_live_oi_change(
                            symbol,
                            decision_timestamp_ms=int(decision["timestamp"]),
                        )
                    else:
                        oi_result = LiveOiChangeResult(None, "replay_exchange_context_fetch_disabled")
                    oi_change = oi_result.value
                    oi_change_reason = oi_result.reason
                    oi_change_loaded = True
                oi_details = {
                    "oi_change_pct_3x5m": _finite_or_none(oi_change),
                    "oi_status": oi_change_reason or ("ok" if oi_change is not None else "not_loaded"),
                    "required_gt": min_oi_change,
                    "dependency_type": "open_interest_context",
                    "decision_timestamp_ms": int(decision["timestamp"]),
                }
                if oi_change is None or not math.isfinite(oi_change):
                    category_rejections.append(
                        record_category_reject(
                            category,
                            symbol,
                            "reject_oi_unavailable",
                            oi_details,
                            emit=False,
                        )
                    )
                    continue
                if oi_change <= min_oi_change:
                    category_rejections.append(
                        record_category_reject(
                            category,
                            symbol,
                            "reject_oi_below_min",
                            oi_details,
                        )
                    )
                    continue

            strengths = [
                f"категория {category.category_id}",
                f"setup flow x{quote_ratio:.1f}/{trade_ratio:.1f}",
                f"entry hold {hold_count}",
                f"удержание {price_retention:.0%}",
                f"вертикальность {verticality_score:.2f}",
            ]
            if oi_change is not None:
                strengths.append(f"OI {oi_change:.1%}")
            weaknesses: list[str] = []
            if "taker_buy_quote_volume" not in entry_segment.columns:
                weaknesses.append("нет taker-buy в entry flow")
            signal = LiveSignal(
                category_id=category.category_id,
                category_label=category.label,
                category_priority=category.priority,
                symbol=symbol,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                setup_source=setup_source,
                setup_elapsed_fraction=float(setup_elapsed_fraction),
                setup_closed_entry_candles=int(setup_closed_entry_candles),
                decision_timestamp_ms=int(decision["timestamp"]),
                start_timestamp_ms=int(setup_row["timestamp"]),
                session=_session_name(int(decision["timestamp"])),
                entry_price=entry_price,
                stop_price=stop_price,
                tp1_price=tp1_price,
                box_high=segment_high,
                initial_risk=risk,
                initial_risk_pct=initial_risk_pct,
                quote_ratio_start=quote_ratio,
                trade_ratio_start=trade_ratio,
                price_retention=price_retention,
                hold_count=hold_count,
                verticality_score=verticality_score,
                oi_change_pct_3x5m=oi_change,
                previous_live_scan_closed_timestamp_ms=None,
                first_unscanned_decision_timestamp_ms=None,
                live_scan_gap_ltf_candles=0,
                category_rejections=list(category_rejections),
                strengths=strengths,
                weaknesses=weaknesses,
            )
            if emit_diagnostics:
                self._mark_active_symbol(
                    symbol,
                    reason="entry_signal_selected",
                    now_ms=now_ms,
                    ttl_ms=self.config.max_signal_age_ms,
                    decision_timestamp_ms=int(decision["timestamp"]),
                )
                self.artifacts.append_event(
                    "category_selected",
                    symbol,
                    {
                        "category_id": category.category_id,
                        "category_label": category.label,
                        "category_contract": LIVE_CATEGORY_CONTRACT,
                        "category_priority": category.priority,
                        "prior_fast_fade_filter_status": prior_fast_fade_result.get("status") if prior_fast_fade_result is not None else "not_required",
                        "prior_fast_fade_filter_reason": prior_fast_fade_result.get("reason") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_count_72h": prior_fast_fade_result.get("prior_fast_fade_count_72h") if prior_fast_fade_result is not None else "",
                        "prior_spike_count_72h": prior_fast_fade_result.get("prior_spike_count_72h") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_filter_coverage_policy": prior_fast_fade_result.get("coverage_policy") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_filter_effective_cache_end_timestamp_ms": prior_fast_fade_result.get("effective_cache_end_timestamp_ms") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_filter_ignored_tail_ms": prior_fast_fade_result.get("ignored_tail_ms") if prior_fast_fade_result is not None else "",
                        "decision_timestamp_ms": int(decision["timestamp"]),
                        "prior_category_rejections": category_rejections,
                        "levels_tf": levels_timeframe.value,
                        "entry_tf": entry_timeframe.value,
                        "setup_source": setup_source,
                        "setup_elapsed_fraction": float(setup_elapsed_fraction),
                        "setup_closed_entry_candles": int(setup_closed_entry_candles),
                        "raw_quote_ratio": _finite_or_none(raw_quote_ratio),
                        "raw_trade_ratio": _finite_or_none(raw_trade_ratio),
                        "quote_pace_ratio": _finite_or_none(quote_ratio),
                        "trade_pace_ratio": _finite_or_none(trade_ratio),
                        "flow_hold_count_next_n_candles": flow_hold_count,
                        "start_lower_wick_to_range": _finite_or_none(start_lower_wick_to_range),
                        "start_upper_wick_to_range": _finite_or_none(start_upper_wick_to_range),
                        "base_tp1_price": _finite_or_none(base_tp1_price),
                        "tp1_price": _finite_or_none(tp1_price),
                        "tp1_round_step": _finite_or_none(tp1_round_step),
                        "mark_close_vs_decision_close_basis": _finite_or_none(
                            mark_basis.value if mark_basis is not None else None
                        ),
                        "mark_basis_status": mark_basis.reason if mark_basis is not None else "",
                        "mark_timestamp_ms": mark_basis.timestamp_ms
                        if mark_basis is not None and mark_basis.timestamp_ms is not None
                        else "",
                        "mark_age_ms": mark_basis.age_ms
                        if mark_basis is not None and mark_basis.age_ms is not None
                        else "",
                        "oi_change_pct_3x5m": _finite_or_none(oi_change),
                        "oi_status": oi_change_reason or ("ok" if oi_change is not None else ""),
                    },
                )
                decision_snapshot_json = self._build_delayed_replay_decision_snapshot(
                    symbol=symbol,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    decision_timestamp_ms=int(decision["timestamp"]),
                    setup_start_timestamp_ms=int(setup_row["timestamp"]),
                    setup_history=baseline,
                    setup_row=setup_row,
                    entry_segment=entry_segment,
                    setup_source=setup_source,
                    setup_elapsed_fraction=float(setup_elapsed_fraction),
                    setup_closed_entry_candles=int(setup_closed_entry_candles),
                    mark_basis=mark_basis,
                    oi_change=oi_change,
                    oi_status=oi_change_reason or ("ok" if oi_change is not None else "not_loaded"),
                    prior_fast_fade_result=prior_fast_fade_result,
                )
                self._capture_delayed_replay_signal(
                    signal,
                    source_scan_mode=self._batch_scan_mode_for_symbol(symbol),
                    decision_snapshot_json=decision_snapshot_json,
                )
            if emit_diagnostics and category_rejections:
                rejected_ids = ",".join(str(row.get("category_id")) for row in category_rejections)
                self.logger(
                    f"{_compact_symbol(symbol)} {levels_timeframe.value}/{entry_timeframe.value} · "
                    f"сигнал {category.category_id} · раньше отвалилось {rejected_ids}"
                )
            return signal
        if emit_diagnostics and category_rejections:
            if _retryable_category_rejection_rows(category_rejections):
                return None
            reject_reasons = [
                str(row.get("category_reject_reason") or row.get("reason") or "")
                for row in category_rejections
                if str(row.get("category_reject_reason") or row.get("reason") or "")
            ]
            unique_reasons = list(dict.fromkeys(reject_reasons))
            rejected_category_ids = ",".join(str(row.get("category_id") or "") for row in category_rejections if row.get("category_id"))
            live_reason = "all_categories_rejected"
            if unique_reasons:
                live_reason = f"all_categories_rejected:{'|'.join(unique_reasons[:3])}"
            decision_snapshot_json = self._build_delayed_replay_decision_snapshot(
                symbol=symbol,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                decision_timestamp_ms=int(decision["timestamp"]),
                setup_start_timestamp_ms=int(setup_row["timestamp"]),
                setup_history=baseline,
                setup_row=setup_row,
                entry_segment=entry_segment,
                setup_source=setup_source,
                setup_elapsed_fraction=float(setup_elapsed_fraction),
                setup_closed_entry_candles=int(setup_closed_entry_candles),
                mark_basis=mark_basis,
                oi_change=oi_change,
                oi_status=oi_change_reason or ("ok" if oi_change is not None else "not_loaded"),
                prior_fast_fade_result=prior_fast_fade_result,
            )
            self._capture_delayed_replay_case(
                source_event="all_categories_rejected",
                symbol=symbol,
                details={
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "decision_timestamp_ms": int(decision["timestamp"]),
                    "start_timestamp_ms": int(setup_row["timestamp"]),
                    "setup_source": setup_source,
                    "setup_elapsed_fraction": float(setup_elapsed_fraction),
                    "setup_closed_entry_candles": int(setup_closed_entry_candles),
                    "rejected_category_count": int(len(category_rejections)),
                    "rejected_category_ids": rejected_category_ids,
                    "category_reject_reasons": unique_reasons,
                    "category_rejections": category_rejections,
                    "source_scan_mode": self._batch_scan_mode_for_symbol(symbol),
                    "decision_snapshot_json": decision_snapshot_json,
                },
                priority=3,
                live_decision_class="all_categories_rejected",
                live_reason=live_reason,
            )
        return None

    def _record_category_reject(
        self,
        category: LivePumpCategory,
        symbol: str,
        reason: str,
        details: dict[str, object],
        emit: bool = True,
    ) -> dict[str, object]:
        detail_payload = dict(details)
        detail_reason = detail_payload.pop("reason", None)
        payload: dict[str, object] = {
            "category_id": category.category_id,
            "category_label": category.label,
            "category_priority": category.priority,
            "reason": reason,
            "category_reject_reason": reason,
            **detail_payload,
        }
        if detail_reason is not None:
            payload.setdefault("coverage_reason", detail_reason)
            payload["detail_reason"] = detail_reason
        if emit:
            self.artifacts.append_event("category_rejected", symbol, payload)
        return payload

    def _fetch_live_oi_change(self, symbol: str, *, decision_timestamp_ms: int) -> LiveOiChangeResult:
        end_ms = int(decision_timestamp_ms)
        start_ms = end_ms - 25 * 60_000
        frame = self.exchange.fetch_open_interest(symbol, Timeframe.M5, start_ms, end_ms)
        if frame.empty:
            return LiveOiChangeResult(None, "oi_frame_empty")
        if "open_interest" not in frame.columns:
            return LiveOiChangeResult(None, "oi_column_missing")
        frame = frame.sort_values("timestamp").dropna(subset=["timestamp", "open_interest"]).reset_index(drop=True)
        if len(frame) < 4:
            return LiveOiChangeResult(None, "oi_history_too_short")
        latest_ts = int(frame.iloc[-1]["timestamp"])
        if latest_ts < end_ms - self.config.oi_fresh_ms:
            return LiveOiChangeResult(None, "oi_stale")
        current = float(frame.iloc[-1]["open_interest"])
        previous = float(frame.iloc[-4]["open_interest"])
        if not math.isfinite(current) or not math.isfinite(previous) or previous <= 0.0:
            return LiveOiChangeResult(None, "oi_invalid_values")
        value = _safe_divide(current - previous, previous)
        if not math.isfinite(value):
            return LiveOiChangeResult(None, "oi_invalid_change")
        return LiveOiChangeResult(value)

    def _fetch_live_mark_basis(
        self,
        symbol: str,
        *,
        decision_timestamp_ms: int,
        decision_close: float,
    ) -> LiveMarkBasisResult:
        if not math.isfinite(decision_close) or decision_close <= 0.0:
            return LiveMarkBasisResult(None, "invalid_decision_close")
        fetcher = getattr(self.exchange, "fetch_binance_derivatives_context", None)
        if not callable(fetcher):
            return LiveMarkBasisResult(None, "mark_context_fetch_unavailable")
        end_ms = int(decision_timestamp_ms)
        start_ms = end_ms - 20 * 60_000
        try:
            frame = fetcher(
                symbol=symbol,
                source="mark",
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
                period="5m",
                limit=20,
            )
        except Exception as exc:
            return LiveMarkBasisResult(None, f"mark_context_error:{type(exc).__name__}")
        if frame.empty:
            return LiveMarkBasisResult(None, "mark_context_empty")
        if "timestamp" not in frame.columns or "close" not in frame.columns:
            return LiveMarkBasisResult(None, "mark_context_missing_columns")
        prepared = frame.sort_values("timestamp").dropna(subset=["timestamp", "close"]).reset_index(drop=True)
        prepared = prepared.loc[pd.to_numeric(prepared["timestamp"], errors="coerce").le(end_ms)]
        if prepared.empty:
            return LiveMarkBasisResult(None, "no_mark_before_decision")
        row = prepared.iloc[-1]
        mark_ts = int(row["timestamp"])
        mark_close = float(row["close"])
        age_ms = int(end_ms - mark_ts)
        if age_ms > 5 * 60_000:
            return LiveMarkBasisResult(None, "mark_context_stale", timestamp_ms=mark_ts, age_ms=age_ms)
        basis = _safe_divide(mark_close - decision_close, decision_close)
        if not math.isfinite(basis):
            return LiveMarkBasisResult(None, "invalid_mark_basis", timestamp_ms=mark_ts, age_ms=age_ms)
        return LiveMarkBasisResult(basis, "ok", timestamp_ms=mark_ts, age_ms=age_ms)

    def _maybe_open_position(self, signal: LiveSignal) -> None:
        symbol_key = _position_symbol_key(signal.symbol)
        source_scan_mode = self._batch_scan_mode_for_symbol(signal.symbol)
        if source_scan_mode == "precise_DANGER_cold_coverage":
            self._cycle_cold_order_attempt_count += 1
            self._cold_order_attempt_total += 1
        reject_max_positions = False
        with self._state_lock:
            if symbol_key in self._open_positions or symbol_key in self._opening_symbols:
                self._mark_active_symbol(
                    signal.symbol,
                    reason="position_already_active",
                    now_ms=int(time.time() * 1000),
                    decision_timestamp_ms=signal.decision_timestamp_ms,
                )
                self._mark_signal_decision_consumed(signal, reason="symbol_position_already_active")
                reject_details = {
                    "symbol_key": symbol_key,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": signal.decision_timestamp_ms,
                }
                self.artifacts.append_event(
                    "reject_symbol_position_already_active",
                    signal.symbol,
                    reject_details,
                )
                self._capture_delayed_replay_execution_reject(
                    signal,
                    event="reject_symbol_position_already_active",
                    details=reject_details,
                )
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_symbol_position_already_active",
                    details=reject_details,
                )
                return
            if len(self._open_positions) + len(self._opening_symbols) >= self.config.max_open_positions:
                reject_max_positions = True
            if self._symbol_in_stop_cooldown(signal.symbol):
                self._clear_active_symbol(signal.symbol, reason="stop_cooldown")
                self._mark_signal_decision_consumed(signal, reason="stop_cooldown")
                reject_details = {
                    "symbol_key": symbol_key,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": signal.decision_timestamp_ms,
                    "stop_cooldown_hours": self.config.stop_cooldown_hours,
                    "stop_limit_per_symbol": self.config.stop_limit_per_symbol,
                }
                self.artifacts.append_event(
                    "reject_stop_cooldown",
                    signal.symbol,
                    reject_details,
                )
                self._capture_delayed_replay_execution_reject(
                    signal,
                    event="reject_stop_cooldown",
                    details=reject_details,
                )
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_stop_cooldown",
                    details=reject_details,
                )
                return
            if not reject_max_positions:
                self._opening_symbols.add(symbol_key)
        if not reject_max_positions:
            self._mark_active_symbol(
                signal.symbol,
                reason="opening_position",
                now_ms=int(time.time() * 1000),
                ttl_ms=self.config.max_signal_age_ms,
                decision_timestamp_ms=signal.decision_timestamp_ms,
            )
        if reject_max_positions:
            self._mark_active_symbol(
                signal.symbol,
                reason="entry_waiting_for_free_slot",
                now_ms=int(time.time() * 1000),
                ttl_ms=self.config.max_signal_age_ms,
                decision_timestamp_ms=signal.decision_timestamp_ms,
            )
            self._forget_signal_scan_closed_at(signal, reason="entry_waiting_for_free_slot")
            self.artifacts.append_event(
                "reject_max_positions",
                signal.symbol,
                {
                    "max": self.config.max_open_positions,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": signal.decision_timestamp_ms,
                    "retry_until_stale": True,
                },
            )
            reject_details = {
                "max": self.config.max_open_positions,
                "levels_tf": signal.levels_timeframe.value,
                "entry_tf": signal.entry_timeframe.value,
                "decision_timestamp_ms": signal.decision_timestamp_ms,
                "retry_until_stale": True,
            }
            self._capture_delayed_replay_execution_reject(
                signal,
                event="reject_max_positions",
                details=reject_details,
            )
            self._append_selected_terminal_outcome(
                signal,
                outcome="waiting_for_capacity",
                terminal_event="reject_max_positions",
                details=reject_details,
            )
            return
        order_flow_started = False
        try:
            if not self._validate_signal_freshness(signal):
                return
            if self.config.danger_local_entry_position_guard_enabled:
                pre_position_amount = 0.0
                self.artifacts.append_event(
                    "danger_local_entry_position_guard_used",
                    signal.symbol,
                    {
                        "source": DANGER_LOCAL_ENTRY_POSITION_GUARD_SOURCE,
                        "symbol_key": symbol_key,
                        "source_scan_mode": source_scan_mode,
                        "assumed_pre_position_amount": pre_position_amount,
                        "startup_position_cleanup_required": bool(self.config.confirm_real_orders),
                    },
                )
            else:
                pre_position_amount = float(self.exchange.fetch_symbol_position_amount(signal.symbol))
                if not math.isfinite(pre_position_amount):
                    self._clear_active_symbol(signal.symbol, reason="invalid_existing_exchange_position")
                    self._mark_signal_decision_consumed(signal, reason="invalid_existing_exchange_position")
                    reject_details = {"exchange_position_amount": _finite_or_none(pre_position_amount)}
                    self.artifacts.append_event(
                        "reject_invalid_existing_exchange_position",
                        signal.symbol,
                        reject_details,
                    )
                    self._append_selected_terminal_outcome(
                        signal,
                        outcome="execution_rejected",
                        terminal_event="reject_invalid_existing_exchange_position",
                        details=reject_details,
                    )
                    return
                if abs(pre_position_amount) > 0.0:
                    self._clear_active_symbol(signal.symbol, reason="existing_exchange_position")
                    self._mark_signal_decision_consumed(signal, reason="existing_exchange_position")
                    reject_details = {"exchange_position_amount": pre_position_amount}
                    self.artifacts.append_event(
                        "reject_existing_exchange_position",
                        signal.symbol,
                        reject_details,
                    )
                    self._append_selected_terminal_outcome(
                        signal,
                        outcome="execution_rejected",
                        terminal_event="reject_existing_exchange_position",
                        details=reject_details,
                    )
                    return
            live_price = float(self.exchange.fetch_last_price(signal.symbol))
            if not self._validate_signal_executable(signal, live_price=live_price):
                return
            try:
                balance = float(self.exchange.fetch_usdt_free_balance())
            except (TypeError, ValueError) as exc:
                reject_details = {"free_usdt": None, "reason": str(exc)}
                self.artifacts.append_event("reject_invalid_free_balance", signal.symbol, reject_details)
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_invalid_free_balance",
                    details=reject_details,
                )
                return
            if not math.isfinite(balance):
                reject_details = {"free_usdt": None}
                self.artifacts.append_event("reject_invalid_free_balance", signal.symbol, reject_details)
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_invalid_free_balance",
                    details=reject_details,
                )
                return
            if balance <= 0.0:
                reject_details = {"free_usdt": balance}
                self.artifacts.append_event("reject_no_free_balance", signal.symbol, reject_details)
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_no_free_balance",
                    details=reject_details,
                )
                return
            notional = self.config.position_notional_usdt
            if not math.isfinite(notional) or notional <= 0.0:
                self._mark_signal_decision_consumed(signal, reason="invalid_position_notional")
                reject_details = {"position_notional": _finite_or_none(notional)}
                self.artifacts.append_event("reject_invalid_position_notional", signal.symbol, reject_details)
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_invalid_position_notional",
                    details=reject_details,
                )
                return
            if balance < notional:
                reject_details = {"free_usdt": balance, "position_notional": notional}
                self.artifacts.append_event(
                    "reject_insufficient_margin_for_fixed_notional",
                    signal.symbol,
                    reject_details,
                )
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_insufficient_margin_for_fixed_notional",
                    details=reject_details,
                )
                return
            amount_requested = notional / live_price
            if not math.isfinite(amount_requested) or amount_requested <= 0.0:
                reject_details = {"live_price": _finite_or_none(live_price), "position_notional": _finite_or_none(notional)}
                self.artifacts.append_event(
                    "reject_invalid_order_amount",
                    signal.symbol,
                    reject_details,
                )
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="execution_rejected",
                    terminal_event="reject_invalid_order_amount",
                    details=reject_details,
                )
                return
            entry_order_submitted_at_ms = int(time.time() * 1000)
            order_flow_started = True
            entry_client_order_id = _live_client_order_id(
                "entry",
                signal.symbol,
                signal.levels_timeframe.value,
                signal.entry_timeframe.value,
                signal.decision_timestamp_ms,
                entry_order_submitted_at_ms,
            )
            self._track_order_reconcile_symbol(signal.symbol, reason="entry_order_submitted")
            try:
                fill = self.exchange.create_market_order_with_fill(
                    signal.symbol,
                    "buy",
                    amount_requested,
                    reduce_only=False,
                    client_order_id=entry_client_order_id,
                )
            except Exception as exc:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=amount_requested,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{entry_client_order_id}",
                    reason=f"entry_order_or_fill_failed:{type(exc).__name__}",
                )
                raise
            self._track_order_reconcile_symbol(signal.symbol, reason="entry_order_filled")
            post_position_amount = float(self.exchange.fetch_symbol_position_amount(signal.symbol))
            if self.config.danger_local_entry_position_guard_enabled:
                position_delta_amount = float(fill.filled_amount)
                if not math.isfinite(post_position_amount) or not math.isfinite(position_delta_amount):
                    self._close_unprotected_entry_exposure(
                        signal.symbol,
                        amount=fill.filled_amount,
                        position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                        reason="invalid_post_entry_position_amount",
                    )
                    raise LiveDataIntegrityError(
                        f"invalid post-entry position amount: symbol={signal.symbol} local_guard=true post={post_position_amount}"
                    )
                post_fill_slippage = abs(post_position_amount - fill.filled_amount) / max(fill.filled_amount, 1e-12)
                if post_position_amount <= 0.0 or post_fill_slippage > self.config.max_position_amount_slippage_ratio:
                    self._close_unprotected_entry_exposure(
                        signal.symbol,
                        amount=fill.filled_amount,
                        position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                        reason="danger_local_guard_post_position_mismatch",
                    )
                    raise LiveDataIntegrityError(
                        f"local guard post-entry position mismatch: symbol={signal.symbol} order_id={fill.order_id} "
                        f"filled={fill.filled_amount} post={post_position_amount} slippage={post_fill_slippage}"
                    )
                fill_position_slippage = post_fill_slippage
            else:
                position_delta_amount = post_position_amount - pre_position_amount
                if not math.isfinite(post_position_amount) or not math.isfinite(position_delta_amount):
                    self._close_unprotected_entry_exposure(
                        signal.symbol,
                        amount=fill.filled_amount,
                        position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                        reason="invalid_post_entry_position_amount",
                    )
                    raise LiveDataIntegrityError(
                        f"invalid post-entry position amount: symbol={signal.symbol} pre={pre_position_amount} post={post_position_amount}"
                    )
                if position_delta_amount <= 0.0:
                    self._close_unprotected_entry_exposure(
                        signal.symbol,
                        amount=fill.filled_amount,
                        position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                        reason="entry_fill_without_position_delta",
                    )
                    raise LiveDataIntegrityError(
                        f"entry order filled but exchange position did not increase: symbol={signal.symbol} order_id={fill.order_id} "
                        f"pre={pre_position_amount} post={post_position_amount}"
                    )
                fill_position_slippage = abs(position_delta_amount - fill.filled_amount) / max(fill.filled_amount, 1e-12)
                if fill_position_slippage > self.config.max_position_amount_slippage_ratio:
                    self._close_unprotected_entry_exposure(
                        signal.symbol,
                        amount=position_delta_amount,
                        position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                        reason="entry_fill_position_amount_mismatch",
                    )
                    raise LiveDataIntegrityError(
                        f"entry fill/position amount mismatch: symbol={signal.symbol} order_id={fill.order_id} "
                        f"filled={fill.filled_amount} delta={position_delta_amount}"
                    )
            actual_entry_price = float(fill.average_price)
            actual_stop_price = float(signal.stop_price)
            actual_initial_risk = actual_entry_price - actual_stop_price
            actual_initial_risk_pct = _safe_divide(actual_initial_risk, actual_entry_price)
            if not math.isfinite(actual_initial_risk) or actual_initial_risk <= 0.0:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                    reason="invalid_actual_initial_risk_after_fill",
                )
                raise LiveDataIntegrityError(
                    f"invalid actual initial risk after fill: symbol={signal.symbol} entry={actual_entry_price} stop={actual_stop_price}"
                )
            if not math.isfinite(actual_initial_risk_pct) or actual_initial_risk_pct > self.config.max_initial_risk_pct:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                    reason="actual_initial_risk_too_wide_after_fill",
                )
                raise LiveDataIntegrityError(
                    f"actual initial risk too wide after fill: symbol={signal.symbol} risk_pct={actual_initial_risk_pct} "
                    f"max={self.config.max_initial_risk_pct}"
                )
            actual_base_tp1_price = actual_entry_price + actual_initial_risk
            actual_tp1_price, actual_tp1_round_step = _round_up_tp1_to_market_number(
                actual_base_tp1_price,
                reference_price=actual_entry_price,
                movement=max(actual_initial_risk, abs(float(signal.box_high) - actual_entry_price)),
            )
            actual_notional = actual_entry_price * position_delta_amount
            actual_risk_usdt = position_delta_amount * actual_initial_risk
            position_id = f"{signal.symbol.replace('/', '_').replace(':', '_')}_{signal.decision_timestamp_ms}_{fill.order_id}"
            entry_fill_lag = _entry_lag_details(signal, observed_timestamp_ms=int(fill.timestamp_ms))
            entry_submit_lag = _entry_lag_details(signal, observed_timestamp_ms=entry_order_submitted_at_ms)
            try:
                stop_order_id = self._create_verified_position_stop_order(
                    signal.symbol,
                    amount=position_delta_amount,
                    stop_price=actual_stop_price,
                    position_id=position_id,
                    reason="initial_stop",
                )
            except Exception as exc:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=position_id,
                    reason=f"initial_stop_failed:{type(exc).__name__}",
                )
                raise
            tp1_order_amount = max(position_delta_amount * 0.5, 0.0)
            try:
                tp1_order_id, tp1_client_order_id, tp1_order_amount = self._create_verified_tp1_limit_order(
                    signal.symbol,
                    amount=tp1_order_amount,
                    tp1_price=actual_tp1_price,
                    position_id=position_id,
                )
            except Exception as exc:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=position_id,
                    reason=f"tp1_limit_order_failed:{type(exc).__name__}",
                )
                raise
            opened_at_ms = int(fill.timestamp_ms)
            position = LivePosition(
                position_id=position_id,
                signal=signal,
                amount=float(position_delta_amount),
                notional_usdt=float(actual_notional),
                risk_usdt=float(actual_risk_usdt),
                entry_order_id=fill.order_id,
                stop_order_id=stop_order_id,
                opened_at_utc=datetime.fromtimestamp(opened_at_ms / 1000, UTC).isoformat(),
                opened_at_ms=opened_at_ms,
                entry_price=actual_entry_price,
                stop_price=actual_stop_price,
                tp1_price=actual_tp1_price,
                tp1_order_id=tp1_order_id,
                tp1_client_order_id=tp1_client_order_id,
                tp1_order_amount=tp1_order_amount,
                initial_risk=actual_initial_risk,
                first_executable_entry_timestamp_ms=int(entry_fill_lag["first_executable_entry_timestamp_ms"]),
                entry_lag_ms=int(entry_fill_lag["entry_lag_ms"]),
                entry_lag_ltf_candles=int(entry_fill_lag["entry_lag_ltf_candles"]),
                entered_late_vs_first_executable=bool(entry_fill_lag["entered_late_vs_first_executable"]),
                entry_order_submit_lag_ms=int(entry_submit_lag["entry_lag_ms"]),
                entry_order_submit_lag_ltf_candles=int(entry_submit_lag["entry_lag_ltf_candles"]),
                previous_live_scan_closed_timestamp_ms=signal.previous_live_scan_closed_timestamp_ms,
                first_unscanned_decision_timestamp_ms=signal.first_unscanned_decision_timestamp_ms,
                live_scan_gap_ltf_candles=signal.live_scan_gap_ltf_candles,
                entry_fill_timestamp_ms=int(fill.timestamp_ms),
                entry_order_submitted_at_ms=entry_order_submitted_at_ms,
                entry_order_status=fill.status,
                source_scan_mode=source_scan_mode,
                danger_cold_coverage_source=source_scan_mode == "precise_DANGER_cold_coverage",
                entry_position_guard_source=(
                    DANGER_LOCAL_ENTRY_POSITION_GUARD_SOURCE
                    if self.config.danger_local_entry_position_guard_enabled
                    else "pre_entry_exchange_position_fetch"
                ),
                entry_filled_amount=float(fill.filled_amount),
                entry_cost_usdt=fill.cost,
                entry_fee_usdt=fill.fee_cost,
                pre_position_amount=pre_position_amount,
                post_position_amount=post_position_amount,
                position_delta_amount=position_delta_amount,
                remaining_amount=float(position_delta_amount),
                current_stop_price=actual_stop_price,
            )
            with self._state_lock:
                self._open_positions[symbol_key] = position
                self._opened_positions_total += 1
                self._active_symbols.pop(symbol_key, None)
            self._mark_signal_decision_consumed(signal, reason="position_opened")
        except LiveOrderPositionIntegrityError as exc:
            with self._state_lock:
                self._opening_symbols.discard(symbol_key)
            self._append_selected_terminal_outcome(
                signal,
                outcome="order_position_integrity_error",
                terminal_event="live_order_position_integrity_error",
                details={"error_type": type(exc).__name__, "reason": str(exc)},
            )
            raise
        except Exception as exc:
            with self._state_lock:
                self._opening_symbols.discard(symbol_key)
            if order_flow_started:
                self._append_selected_terminal_outcome(
                    signal,
                    outcome="order_position_integrity_error",
                    terminal_event="order_position_flow_failed",
                    details={"error_type": type(exc).__name__, "reason": str(exc)},
                )
                raise LiveOrderPositionIntegrityError(
                    f"order/position flow failed: symbol={signal.symbol} {type(exc).__name__}: {exc}",
                    symbol=signal.symbol,
                ) from exc
            raise
        finally:
            with self._state_lock:
                self._opening_symbols.discard(symbol_key)
        self.artifacts.append_position(position)
        self._append_selected_terminal_outcome(
            signal,
            outcome="position_opened",
            terminal_event="position_opened",
            details={"position_id": position.position_id, "entry_order_id": position.entry_order_id, "stop_order_id": position.stop_order_id},
        )
        self.artifacts.append_event(
            "position_opened",
            signal.symbol,
            {
                "position_id": position.position_id,
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "category_contract": LIVE_CATEGORY_CONTRACT,
                "category_priority": signal.category_priority,
                "signal_entry_price": signal.entry_price,
                "actual_entry_price": position.entry_price,
                "first_executable_entry_timestamp_ms": position.first_executable_entry_timestamp_ms,
                "entry_lag_ms": position.entry_lag_ms,
                "entry_lag_ltf_candles": position.entry_lag_ltf_candles,
                "entered_late_vs_first_executable": position.entered_late_vs_first_executable,
                "entry_order_submit_lag_ms": position.entry_order_submit_lag_ms,
                "entry_order_submit_lag_ltf_candles": position.entry_order_submit_lag_ltf_candles,
                "previous_live_scan_closed_timestamp_ms": position.previous_live_scan_closed_timestamp_ms or "",
                "first_unscanned_decision_timestamp_ms": position.first_unscanned_decision_timestamp_ms or "",
                "live_scan_gap_ltf_candles": position.live_scan_gap_ltf_candles,
                "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                "entry_order_submitted_at_ms": position.entry_order_submitted_at_ms,
                "entry_client_order_id": entry_client_order_id,
                "entry_filled_amount": position.entry_filled_amount,
                "position_delta_amount": position.position_delta_amount,
                "base_tp1_price": _finite_or_none(actual_base_tp1_price),
                "tp1_price": position.tp1_price,
                "tp1_round_step": _finite_or_none(actual_tp1_round_step),
                "tp1_order_id": position.tp1_order_id,
                "tp1_client_order_id": position.tp1_client_order_id,
                "tp1_order_amount": position.tp1_order_amount,
                "stop_price": position.stop_price,
            },
        )
        open_text = _format_open_message(position)
        open_chart_path = self._render_open_chart(position)
        try:
            if open_chart_path is not None:
                try:
                    position.telegram_open_message_id = self.telegram.send_photo_sync(
                        channel="positions",
                        photo_path=open_chart_path,
                        caption=open_text,
                    )
                    if position.telegram_open_message_id is None:
                        self.artifacts.append_event(
                            "telegram_open_chart_missing_id",
                            signal.symbol,
                            {"position_id": position.position_id, "chart_path": str(open_chart_path)},
                        )
                    else:
                        self.artifacts.append_event(
                            "telegram_open_photo_sent",
                            signal.symbol,
                            {"position_id": position.position_id, "message_id": position.telegram_open_message_id, "chart_path": str(open_chart_path)},
                        )
                except Exception as exc:
                    self.artifacts.append_event(
                        "telegram_open_chart_failed",
                        signal.symbol,
                        {"position_id": position.position_id, "chart_path": str(open_chart_path), "reason": str(exc)},
                    )
                    self.logger(f"позиция {signal.symbol} открыта, но Telegram-график входа не отправлен: {exc}")
            if position.telegram_open_message_id is None:
                self.artifacts.append_event(
                    "telegram_open_text_fallback",
                    signal.symbol,
                    {
                        "position_id": position.position_id,
                        "reason": "chart_unavailable" if open_chart_path is None else "chart_photo_unavailable",
                    },
                )
                position.telegram_open_message_id = self.telegram.send_sync(
                    channel="positions",
                    text=open_text,
                )
                if position.telegram_open_message_id is None:
                    self.artifacts.append_event(
                        "telegram_open_text_missing_id",
                        signal.symbol,
                        {"position_id": position.position_id},
                    )
                else:
                    self.artifacts.append_event(
                        "telegram_open_text_sent",
                        signal.symbol,
                        {"position_id": position.position_id, "message_id": position.telegram_open_message_id},
                    )
        except Exception as exc:
            self.artifacts.append_event("telegram_open_failed", signal.symbol, {"position_id": position.position_id, "reason": str(exc)})
            self.logger(f"позиция {signal.symbol} открыта, но Telegram-вход не отправлен: {exc}")
        self.logger(
            f"{_compact_symbol(signal.symbol)} открыт · {signal.levels_timeframe.value}/{signal.entry_timeframe.value} · "
            f"entry {position.entry_price:.6g} · риск {position.risk_usdt:.2f} · notional {position.notional_usdt:.2f}"
        )
        threading.Thread(
            target=self._monitor_position,
            args=(position,),
            name=f"position-{_compact_symbol(signal.symbol)}",
            daemon=True,
        ).start()

    def _validate_signal_freshness(self, signal: LiveSignal) -> bool:
        now_ms = int(time.time() * 1000)
        details = _decision_freshness_details(
            decision_timestamp_ms=int(signal.decision_timestamp_ms),
            signal_timeframe=signal.entry_timeframe,
            now_ms=now_ms,
            max_signal_age_ms=self.config.max_signal_age_ms,
        )
        details.update(_entry_lag_details(signal, observed_timestamp_ms=now_ms))
        details.update(
            {
                "previous_live_scan_closed_timestamp_ms": signal.previous_live_scan_closed_timestamp_ms or "",
                "first_unscanned_decision_timestamp_ms": signal.first_unscanned_decision_timestamp_ms or "",
                "live_scan_gap_ltf_candles": signal.live_scan_gap_ltf_candles,
            }
        )
        if details["signal_age_ms"] < 0:
            reject_details = {**details, "stage": "execution_guard"}
            self.artifacts.append_event("reject_signal_not_closed_yet", signal.symbol, reject_details)
            self._capture_delayed_replay_execution_reject(
                signal,
                event="reject_signal_not_closed_yet",
                details=reject_details,
            )
            self._append_selected_terminal_outcome(
                signal,
                outcome="execution_rejected",
                terminal_event="reject_signal_not_closed_yet",
                details=reject_details,
            )
            return False
        if details["signal_age_ms"] > self.config.max_signal_age_ms:
            self._clear_active_symbol(signal.symbol, reason="stale_signal")
            self._mark_signal_decision_consumed(signal, reason="stale_signal")
            reject_details = {**details, "stage": "execution_guard"}
            self.artifacts.append_event("reject_stale_signal", signal.symbol, reject_details)
            self._capture_delayed_replay_execution_reject(
                signal,
                event="reject_stale_signal",
                details=reject_details,
            )
            self._append_selected_terminal_outcome(
                signal,
                outcome="execution_rejected",
                terminal_event="reject_stale_signal",
                details=reject_details,
            )
            self._notify_order_blocked(signal, event="reject_stale_signal", details=reject_details)
            return False
        return True

    def _validate_signal_executable(self, signal: LiveSignal, *, live_price: float) -> bool:
        now_ms = int(time.time() * 1000)
        details = self._entry_execution_guard_details(
            signal,
            executable_price=live_price,
            observed_timestamp_ms=now_ms,
        )
        snapshot_details = self._entry_execution_guard_details(
            signal,
            executable_price=float(signal.entry_price),
            observed_timestamp_ms=int(signal.decision_timestamp_ms),
        )
        snapshot_would_enter = self._entry_execution_guard_reject_event(snapshot_details) is None
        reject_event = self._entry_execution_guard_reject_event(details)
        if reject_event is None:
            return True
        reject_details = details
        if reject_event == "reject_actual_risk_too_wide_at_live_price":
            reject_details = {**details, "max_initial_risk_pct": self.config.max_initial_risk_pct}
        elif reject_event == "reject_entry_price_drift":
            reject_details = {**details, "max_entry_price_drift_pct": self.config.max_entry_price_drift_pct}
        elif reject_event == "reject_rr_collapsed":
            reject_details = {**details, "min_executable_rr_to_signal_tp1": self.config.min_executable_rr_to_signal_tp1}
        if snapshot_would_enter:
            reject_details = {
                **reject_details,
                "discrete_snapshot_would_enter": True,
                "discrete_snapshot_entry_price": _finite_or_none(signal.entry_price),
                "discrete_snapshot_actual_risk_pct": snapshot_details.get("actual_risk_pct"),
                "discrete_snapshot_rr_to_signal_tp1": snapshot_details.get("rr_to_signal_tp1"),
            }
            self._record_discrete_snapshot_entry_missed(
                signal,
                reject_event=reject_event,
                details=reject_details,
            )
        return self._reject_live_order(signal, event=reject_event, details=reject_details)

    def _entry_execution_guard_details(
        self,
        signal: LiveSignal,
        *,
        executable_price: float,
        observed_timestamp_ms: int,
    ) -> dict[str, object]:
        signed_drift_pct = _safe_divide(executable_price - signal.entry_price, signal.entry_price)
        abs_drift_pct = abs(signed_drift_pct) if math.isfinite(signed_drift_pct) else float("nan")
        actual_risk = executable_price - signal.stop_price
        actual_risk_pct = _safe_divide(actual_risk, executable_price)
        rr_to_signal_tp1 = _safe_divide(signal.tp1_price - executable_price, actual_risk)
        return {
            "live_price": _finite_or_none(executable_price),
            "executable_price": _finite_or_none(executable_price),
            "signal_entry_price": _finite_or_none(signal.entry_price),
            "signal_tp1_price": _finite_or_none(signal.tp1_price),
            "stop_price": _finite_or_none(signal.stop_price),
            "drift_pct": _finite_or_none(signed_drift_pct),
            "abs_drift_pct": _finite_or_none(abs_drift_pct),
            "actual_risk_pct": _finite_or_none(actual_risk_pct),
            "rr_to_signal_tp1": _finite_or_none(rr_to_signal_tp1),
            "decision_timestamp_ms": signal.decision_timestamp_ms,
            "observed_timestamp_ms": int(observed_timestamp_ms),
            "previous_live_scan_closed_timestamp_ms": signal.previous_live_scan_closed_timestamp_ms or "",
            "first_unscanned_decision_timestamp_ms": signal.first_unscanned_decision_timestamp_ms or "",
            "live_scan_gap_ltf_candles": signal.live_scan_gap_ltf_candles,
            **_entry_lag_details(signal, observed_timestamp_ms=observed_timestamp_ms),
        }

    def _entry_execution_guard_reject_event(self, details: dict[str, object]) -> str | None:
        live_price = _finite_or_none(details.get("live_price"))
        signal_tp1_price = _finite_or_none(details.get("signal_tp1_price"))
        actual_risk_pct = _finite_or_none(details.get("actual_risk_pct"))
        abs_drift_pct = _finite_or_none(details.get("abs_drift_pct"))
        rr_to_signal_tp1 = _finite_or_none(details.get("rr_to_signal_tp1"))
        if live_price is None or live_price <= 0.0:
            return "reject_invalid_live_price"
        if signal_tp1_price is not None and live_price >= signal_tp1_price:
            return "reject_tp1_already_reached"
        if actual_risk_pct is None or actual_risk_pct <= 0.0:
            return "reject_invalid_actual_risk_at_live_price"
        if actual_risk_pct > self.config.max_initial_risk_pct:
            return "reject_actual_risk_too_wide_at_live_price"
        if abs_drift_pct is None or abs_drift_pct > self.config.max_entry_price_drift_pct:
            return "reject_entry_price_drift"
        if rr_to_signal_tp1 is None or rr_to_signal_tp1 < self.config.min_executable_rr_to_signal_tp1:
            return "reject_rr_collapsed"
        return None

    def _record_discrete_snapshot_entry_missed(
        self,
        signal: LiveSignal,
        *,
        reject_event: str,
        details: dict[str, object],
    ) -> None:
        payload = {
            "reject_event": reject_event,
            "category_id": signal.category_id,
            "category_label": signal.category_label,
            "category_contract": LIVE_CATEGORY_CONTRACT,
            "levels_tf": signal.levels_timeframe.value,
            "entry_tf": signal.entry_timeframe.value,
            "decision_timestamp_ms": int(signal.decision_timestamp_ms),
            "signal_entry_price": _finite_or_none(signal.entry_price),
            "live_price": details.get("live_price"),
            "signal_tp1_price": _finite_or_none(signal.tp1_price),
            "stop_price": _finite_or_none(signal.stop_price),
            "drift_pct": details.get("drift_pct"),
            "abs_drift_pct": details.get("abs_drift_pct"),
            "actual_risk_pct": details.get("actual_risk_pct"),
            "rr_to_signal_tp1": details.get("rr_to_signal_tp1"),
            "entry_lag_ms": details.get("entry_lag_ms", ""),
            "entry_lag_ltf_candles": details.get("entry_lag_ltf_candles", ""),
            "live_scan_gap_ltf_candles": signal.live_scan_gap_ltf_candles,
        }
        self.artifacts.append_event("discrete_signal_snapshot_entry_missed", signal.symbol, payload)

    def _append_selected_terminal_outcome(
        self,
        signal: LiveSignal,
        *,
        outcome: str,
        terminal_event: str,
        details: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "outcome": outcome,
            "terminal_event": terminal_event,
            "category_id": signal.category_id,
            "category_label": signal.category_label,
            "category_contract": LIVE_CATEGORY_CONTRACT,
            "category_priority": signal.category_priority,
            "levels_tf": signal.levels_timeframe.value,
            "entry_tf": signal.entry_timeframe.value,
            "decision_timestamp_ms": int(signal.decision_timestamp_ms),
            "signal_entry_price": _finite_or_none(signal.entry_price),
            "signal_stop_price": _finite_or_none(signal.stop_price),
            "signal_tp1_price": _finite_or_none(signal.tp1_price),
        }
        if details:
            payload["terminal_details"] = _json_safe_payload(details)
        self.artifacts.append_event("selected_terminal_outcome", signal.symbol, payload)

    def _reject_live_order(self, signal: LiveSignal, *, event: str, details: dict[str, object]) -> bool:
        self._clear_active_symbol(signal.symbol, reason=event)
        self._mark_signal_decision_consumed(signal, reason=event)
        self.artifacts.append_event(event, signal.symbol, details)
        self._append_selected_terminal_outcome(
            signal,
            outcome="execution_rejected",
            terminal_event=event,
            details=details,
        )
        self._capture_delayed_replay_execution_reject(signal, event=event, details=details)
        self._notify_order_blocked(signal, event=event, details=details)
        return False

    def _notify_order_blocked(self, signal: LiveSignal, *, event: str, details: dict[str, object]) -> None:
        if bool(details.get("discrete_snapshot_would_enter")) and not self.config.discrete_signal_missed_telegram_enabled:
            return
        try:
            self.telegram.send(
                channel="events",
                key=f"order_blocked:{event}:{_position_symbol_key(signal.symbol)}",
                text=_format_order_blocked_message(signal, event=event, details=details),
                symbol=signal.symbol,
            )
        except Exception as exc:
            self.artifacts.append_event(
                "telegram_order_blocked_enqueue_failed",
                signal.symbol,
                {"blocked_event": event, "reason": str(exc)},
            )

    def _send_or_edit_stop_message(self, position: LivePosition, *, text: str, stop_price: float, reason: str) -> None:
        if position.telegram_open_message_id is None:
            self.artifacts.append_event(
                "telegram_stop_message_skipped_no_parent",
                position.signal.symbol,
                {"position_id": position.position_id, "reason": reason, "stop_price": stop_price},
            )
            return
        if position.telegram_stop_message_id is None:
            try:
                message_id = self.telegram.send_sync(
                    channel="positions",
                    reply_to_message_id=position.telegram_open_message_id,
                    text=text,
                )
            except Exception as exc:
                self.artifacts.append_event(
                    "telegram_stop_message_send_failed",
                    position.signal.symbol,
                    {"position_id": position.position_id, "reason": reason, "stop_price": stop_price, "error": str(exc)},
                )
                self.logger(f"стоп-сообщение {position.signal.symbol} не отправлено: {exc}")
                return
            if message_id is None:
                self.artifacts.append_event(
                    "telegram_stop_message_missing_id",
                    position.signal.symbol,
                    {"position_id": position.position_id, "reason": reason, "stop_price": stop_price},
                )
                return
            position.telegram_stop_message_id = message_id
            self.artifacts.append_event(
                "telegram_stop_message_sent",
                position.signal.symbol,
                {"position_id": position.position_id, "message_id": message_id, "reason": reason, "stop_price": stop_price},
            )
            return
        try:
            edited_message_id = self.telegram.edit_sync(
                channel="positions",
                message_id=position.telegram_stop_message_id,
                text=text,
            )
        except Exception as exc:
            self.artifacts.append_event(
                "telegram_stop_message_edit_failed",
                position.signal.symbol,
                {
                    "position_id": position.position_id,
                    "message_id": position.telegram_stop_message_id,
                    "reason": reason,
                    "stop_price": stop_price,
                    "error": str(exc),
                },
            )
            self.logger(f"стоп-сообщение {position.signal.symbol} не отредактировано: {exc}")
            return
        if edited_message_id is None:
            self.artifacts.append_event(
                "telegram_stop_message_edit_missing_id",
                position.signal.symbol,
                {
                    "position_id": position.position_id,
                    "message_id": position.telegram_stop_message_id,
                    "reason": reason,
                    "stop_price": stop_price,
                },
            )
            return
        self.artifacts.append_event(
            "telegram_stop_message_edited",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "message_id": position.telegram_stop_message_id,
                "reason": reason,
                "stop_price": stop_price,
            },
        )

    def _create_verified_tp1_limit_order(
        self,
        symbol: str,
        *,
        amount: float,
        tp1_price: float,
        position_id: str,
    ) -> tuple[str, str, float]:
        if not math.isfinite(amount) or amount <= 0.0:
            raise LiveDataIntegrityError(f"invalid TP1 limit order amount: position_id={position_id} amount={amount}")
        if not math.isfinite(tp1_price) or tp1_price <= 0.0:
            raise LiveDataIntegrityError(f"invalid TP1 limit order price: position_id={position_id} tp1_price={tp1_price}")
        tp1_client_order_id = _live_client_order_id("tp1", symbol, position_id)
        tp1_order = self.exchange.create_limit_order(
            symbol,
            "sell",
            amount,
            tp1_price,
            reduce_only=True,
            client_order_id=tp1_client_order_id,
        )
        tp1_order_id = _resolve_order_id(tp1_order) or ""
        if not tp1_order_id:
            raise LiveDataIntegrityError(f"TP1 limit order returned no id: position_id={position_id} symbol={symbol}")
        self._verify_open_tp1_limit_order(
            symbol,
            order_id=tp1_order_id,
            expected_client_order_id=tp1_client_order_id,
            expected_side="sell",
            expected_amount=amount,
            expected_price=tp1_price,
            position_id=position_id,
        )
        self.artifacts.append_event(
            "position_tp1_limit_order_verified",
            symbol,
            {
                "position_id": position_id,
                "order_id": tp1_order_id,
                "client_order_id": tp1_client_order_id,
                "tp1_price": tp1_price,
                "amount": amount,
            },
        )
        return tp1_order_id, tp1_client_order_id, float(amount)

    def _verify_open_tp1_limit_order(
        self,
        symbol: str,
        *,
        order_id: str,
        expected_client_order_id: str,
        expected_side: str,
        expected_amount: float,
        expected_price: float,
        position_id: str,
    ) -> None:
        open_orders: list[dict[str, object]] = []
        order: dict[str, object] | None = None
        order_source = ""
        client_lookup_error = ""
        verification_attempts = 5
        for attempt in range(1, verification_attempts + 1):
            open_orders = self.exchange.fetch_open_orders(symbol)
            order = next(
                (row for row in open_orders if isinstance(row, dict) and _order_matches_order_id(row, order_id)),
                None,
            )
            if order is not None:
                order_source = "open_orders_order_id"
            else:
                order = next(
                    (
                        row
                        for row in open_orders
                        if isinstance(row, dict) and _order_matches_client_order_id(row, expected_client_order_id)
                    ),
                    None,
                )
                if order is not None:
                    order_source = "open_orders_client_order_id"
            if order is None:
                try:
                    fetched_order = self.exchange.fetch_order_by_client_order_id(symbol, expected_client_order_id)
                except Exception as exc:
                    client_lookup_error = f"{type(exc).__name__}: {exc}"
                else:
                    if _order_matches_order_id(fetched_order, order_id) or _order_matches_client_order_id(
                        fetched_order,
                        expected_client_order_id,
                    ):
                        order = fetched_order
                        order_source = "client_order_id_lookup"
            if order is not None:
                if attempt > 1:
                    self.artifacts.append_event(
                        "position_tp1_limit_order_visibility_delayed",
                        symbol,
                        {
                            "position_id": position_id,
                            "order_id": order_id,
                            "client_order_id": expected_client_order_id,
                            "verification_attempt": attempt,
                            "open_orders_seen": len(open_orders),
                            "order_source": order_source,
                        },
                    )
                break
            if attempt < verification_attempts:
                self.artifacts.append_event(
                    "position_tp1_limit_order_visibility_retry",
                    symbol,
                    {
                        "position_id": position_id,
                        "order_id": order_id,
                        "client_order_id": expected_client_order_id,
                        "verification_attempt": attempt,
                        "open_orders_seen": len(open_orders),
                        "client_lookup_error": client_lookup_error,
                    },
                )
                time.sleep(0.5)
        if order is None:
            raise LiveDataIntegrityError(
                f"TP1 limit order not visible in open orders after {verification_attempts} checks: "
                f"symbol={symbol} position_id={position_id} order_id={order_id} "
                f"client_order_id={expected_client_order_id} open_orders_seen={len(open_orders)} "
                f"client_lookup_error={client_lookup_error or 'none'}",
                symbol=symbol,
            )
        terminal_status = _order_terminal_status(order)
        if terminal_status is not None:
            raise LiveDataIntegrityError(
                f"TP1 limit order is terminal after verification: symbol={symbol} position_id={position_id} "
                f"order_id={order_id} client_order_id={expected_client_order_id} status={terminal_status!r} source={order_source}",
                symbol=symbol,
            )
        side = _order_text_field(order, "side")
        if side is None or side.lower() != expected_side.lower():
            raise LiveDataIntegrityError(
                f"TP1 limit order side not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"side={side!r} expected={expected_side} source={order_source}",
                symbol=symbol,
            )
        order_type = _order_text_field(order, "type")
        if order_type is None or "limit" not in order_type.lower():
            raise LiveDataIntegrityError(
                f"TP1 limit order type not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"type={order_type!r} source={order_source}",
                symbol=symbol,
            )
        reduce_only = _order_bool_field(order, "reduceOnly")
        if reduce_only is not True:
            raise LiveDataIntegrityError(
                f"TP1 limit order reduceOnly not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"reduceOnly={reduce_only!r} source={order_source}",
                symbol=symbol,
            )
        amount = _order_float_field(order, "amount", "origQty")
        amount_delta = abs(amount - expected_amount) if amount is not None else float("nan")
        amount_delta_ratio = _safe_divide(amount_delta, expected_amount) if amount is not None else float("nan")
        if amount is None or not math.isfinite(amount_delta_ratio) or amount_delta_ratio > self.config.max_position_amount_slippage_ratio:
            raise LiveDataIntegrityError(
                f"TP1 limit order amount not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"amount={amount!r} expected={expected_amount} source={order_source}",
                symbol=symbol,
            )
        price = _order_float_field(order, "price")
        if price is None or not _price_matches_exchange_precision(price, expected_price):
            price_delta = abs(price - expected_price) if price is not None else float("nan")
            price_tolerance = _exchange_price_precision_tolerance(price) if price is not None else float("nan")
            raise LiveDataIntegrityError(
                f"TP1 limit order price not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"price={price!r} expected={expected_price} delta={price_delta} "
                f"exchange_precision_tolerance={price_tolerance} source={order_source}",
                symbol=symbol,
            )

    def _is_open_tp1_limit_order_visible(self, position: LivePosition) -> bool:
        order_id = str(position.tp1_order_id or "").strip()
        client_order_id = str(position.tp1_client_order_id or "").strip()
        if not order_id and not client_order_id:
            return False
        open_orders = self.exchange.fetch_open_orders(position.signal.symbol)
        return any(
            isinstance(row, dict)
            and (
                (order_id and _order_matches_order_id(row, order_id))
                or (client_order_id and _order_matches_client_order_id(row, client_order_id))
            )
            for row in open_orders
        )

    def _sync_position_tp1_limit_order(self, position: LivePosition, *, actual_amount: float) -> float:
        if position.tp1_done or not str(position.tp1_order_id or "").strip():
            return actual_amount
        amount_drop = max(float(position.remaining_amount) - float(actual_amount), 0.0)
        progress_threshold = max(float(position.tp1_order_amount) * self.config.max_position_amount_slippage_ratio, 1e-12)
        try:
            tp1_visible = self._is_open_tp1_limit_order_visible(position)
        except ExchangeConnectivityError:
            raise
        except Exception:
            tp1_visible = False
        if tp1_visible and amount_drop <= progress_threshold:
            return actual_amount
        try:
            tp1_fill = self.exchange.fetch_order_fill(position.signal.symbol, position.tp1_order_id)
        except ExchangeConnectivityError:
            raise
        except Exception as exc:
            if tp1_visible and amount_drop <= progress_threshold:
                return actual_amount
            raise LiveDataIntegrityError(
                f"TP1 limit order fill unresolved: position_id={position.position_id} order_id={position.tp1_order_id} "
                f"visible={tp1_visible} amount_drop={amount_drop} error={type(exc).__name__}: {exc}",
                symbol=position.signal.symbol,
            ) from exc
        filled_amount = float(tp1_fill.filled_amount)
        if not math.isfinite(filled_amount) or filled_amount <= 0.0 or not math.isfinite(float(tp1_fill.average_price)):
            raise LiveDataIntegrityError(
                f"TP1 limit fill invalid: position_id={position.position_id} order_id={tp1_fill.order_id}",
                symbol=position.signal.symbol,
            )
        if filled_amount <= position.tp1_recorded_filled_amount + progress_threshold:
            return actual_amount
        overfill_ratio = _safe_divide(filled_amount - float(position.tp1_order_amount), max(float(position.tp1_order_amount), 1e-12))
        if math.isfinite(overfill_ratio) and overfill_ratio > self.config.max_position_amount_slippage_ratio:
            raise LiveDataIntegrityError(
                f"TP1 limit overfilled: position_id={position.position_id} order_id={tp1_fill.order_id} "
                f"filled={filled_amount} expected={position.tp1_order_amount}",
                symbol=position.signal.symbol,
            )
        cumulative_realized_pnl = filled_amount * (float(tp1_fill.average_price) - float(position.entry_price))
        realized_delta = cumulative_realized_pnl - float(position.tp1_recorded_realized_pnl_usdt)
        position.realized_pnl_usdt += realized_delta
        filled_delta = filled_amount - float(position.tp1_recorded_filled_amount)
        position.tp1_recorded_filled_amount = filled_amount
        position.tp1_recorded_realized_pnl_usdt = cumulative_realized_pnl
        actual_after_tp1 = abs(self.exchange.fetch_symbol_position_amount(position.signal.symbol))
        expected_remaining_amount = max(float(position.amount) - filled_amount, 0.0)
        position.remaining_amount = min(actual_after_tp1, expected_remaining_amount)
        self.artifacts.append_event(
            "tp1_limit_exit_filled",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "order_id": tp1_fill.order_id,
                "client_order_id": position.tp1_client_order_id,
                "status": tp1_fill.status,
                "fill_timestamp_ms": tp1_fill.timestamp_ms,
                "fill_price": tp1_fill.average_price,
                "filled_amount": filled_amount,
                "filled_delta": filled_delta,
                "target_amount": position.tp1_order_amount,
                "exchange_position_amount_after_tp1": actual_after_tp1,
                "expected_remaining_amount": expected_remaining_amount,
                "cost": tp1_fill.cost,
                "fee_cost": tp1_fill.fee_cost,
                "realized_pnl_usdt": position.realized_pnl_usdt,
            },
        )
        completion_threshold = max(float(position.tp1_order_amount) * self.config.max_position_amount_slippage_ratio, 1e-12)
        if filled_amount + completion_threshold >= float(position.tp1_order_amount):
            position.tp1_done = True
            if position.remaining_amount <= 0.0:
                return actual_after_tp1
            self._replace_position_stop_order(
                position,
                amount=position.remaining_amount,
                stop_price=position.entry_price,
                reason="tp1_be",
            )
            self.artifacts.append_event("tp1_and_stop_to_be", position.signal.symbol, {"position_id": position.position_id})
            self._send_or_edit_stop_message(
                position,
                stop_price=position.entry_price,
                reason="tp1_be",
                text=_format_stop_move_message(position, stop_price=position.entry_price, label="BE"),
            )
        return actual_after_tp1

    def _monitor_position(self, position: LivePosition) -> None:
        signal = position.signal
        last_stop_price = position.stop_price
        empty_ohlcv_cycles = 0
        waiting_first_candle_logged = False
        monitor_timeframe = signal.entry_timeframe
        monitor_timeframe_ms = int(monitor_timeframe.to_milliseconds())
        first_monitor_candle_start_ms = _next_candle_start_ms(position.entry_fill_timestamp_ms, monitor_timeframe)
        while True:
            try:
                actual_amount = abs(self.exchange.fetch_symbol_position_amount(signal.symbol))
                if actual_amount <= 0.0:
                    self.artifacts.append_event(
                        "position_closed_externally_unverified_exit_price",
                        signal.symbol,
                        {
                            "position_id": position.position_id,
                            "last_known_stop_price": position.current_stop_price,
                            "reason": "exchange_position_amount_zero_before_monitor_decision",
                        },
                    )
                    self._finalize_unresolved_position_exit(
                        position,
                        reason="позиция закрыта на бирже, но exit fill не восстановлен",
                        source_event="position_closed_externally_unverified_exit_price",
                        details={
                            "exchange_position_amount": actual_amount,
                            "last_known_stop_price": position.current_stop_price,
                        },
                        cancel_stop_order=True,
                        record_stop_cooldown=False,
                    )
                    return
                actual_amount = self._sync_position_tp1_limit_order(position, actual_amount=actual_amount)
                if position.tp1_done and math.isfinite(float(position.current_stop_price)):
                    last_stop_price = max(last_stop_price, float(position.current_stop_price))
                if actual_amount <= 0.0:
                    self._finalize_position(
                        position,
                        reason="TP1 limit закрыл позицию полностью",
                        pnl_price=position.tp1_price,
                        exit_amount=0.0,
                    )
                    return
                managed_amount = min(actual_amount, max(position.remaining_amount, 0.0))
                if managed_amount <= 0.0:
                    raise LiveDataIntegrityError(f"managed position amount is zero while exchange position is open: {position.position_id}")
                now_ms = int(time.time() * 1000)
                latest_closed_candle_start_ms = _latest_closed_candle_start_ms(monitor_timeframe, now_ms=now_ms)
                if latest_closed_candle_start_ms < first_monitor_candle_start_ms:
                    if not waiting_first_candle_logged:
                        self.artifacts.append_event(
                            "position_monitor_waiting_first_candle",
                            signal.symbol,
                            {
                                "position_id": position.position_id,
                                "timeframe": monitor_timeframe.value,
                                "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                                "first_monitor_candle_start_ms": first_monitor_candle_start_ms,
                                "latest_closed_candle_start_ms": latest_closed_candle_start_ms,
                                "time_until_first_candle_close_ms": max(
                                    0,
                                    first_monitor_candle_start_ms + monitor_timeframe_ms - now_ms,
                                ),
                            },
                        )
                        waiting_first_candle_logged = True
                    time.sleep(15.0)
                    continue
                frame = self._fetch_chart_frame(
                    signal.symbol,
                    monitor_timeframe,
                    start_timestamp_ms=first_monitor_candle_start_ms,
                    end_timestamp_ms=now_ms,
                )
                if not frame.empty and "timestamp" in frame.columns:
                    frame = frame.copy()
                    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
                    frame = frame.loc[
                        frame["timestamp"].notna()
                        & (frame["timestamp"].astype("int64") >= int(first_monitor_candle_start_ms))
                        & (frame["timestamp"].astype("int64") <= int(latest_closed_candle_start_ms))
                    ].copy()
                if frame.empty:
                    empty_ohlcv_cycles += 1
                    details = {
                        "position_id": position.position_id,
                        "timeframe": monitor_timeframe.value,
                        "empty_ohlcv_cycles": empty_ohlcv_cycles,
                        "max_empty_ohlcv_cycles": self.config.max_monitor_empty_ohlcv_cycles,
                        "from_timestamp_ms": first_monitor_candle_start_ms,
                        "to_timestamp_ms": now_ms,
                        "latest_closed_candle_start_ms": latest_closed_candle_start_ms,
                    }
                    self.artifacts.append_event("position_monitor_empty_ohlcv", signal.symbol, details)
                    if empty_ohlcv_cycles >= self.config.max_monitor_empty_ohlcv_cycles:
                        raise LiveDataIntegrityError(
                            f"monitor {monitor_timeframe.value} OHLCV empty for {empty_ohlcv_cycles} consecutive cycles: "
                            f"position_id={position.position_id}"
                        )
                    time.sleep(15.0)
                    continue
                empty_ohlcv_cycles = 0
                latest = frame.sort_values("timestamp").iloc[-1]
                latest_low = float(latest["low"])
                latest_close = float(latest["close"])
                if position.tp1_done:
                    latest_ts = int(latest["timestamp"])
                    prior = frame.loc[frame["timestamp"].astype(int) < latest_ts].tail(self.config.trail_lookback_candles)
                    if not prior.empty:
                        trail_low = float(prior["low"].min())
                        structural_stop = trail_low - self.config.trail_buffer_r * position.initial_risk
                    else:
                        structural_stop = last_stop_price
                    new_stop = max(last_stop_price, structural_stop)
                    if new_stop > last_stop_price and new_stop < latest_close:
                        self._replace_position_stop_order(
                            position,
                            amount=min(actual_amount, position.remaining_amount),
                            stop_price=new_stop,
                            reason="structural_trail",
                        )
                        last_stop_price = new_stop
                        self._send_or_edit_stop_message(
                            position,
                            stop_price=new_stop,
                            reason="structural_trail",
                            text=_format_stop_move_message(position, stop_price=new_stop, label="SL"),
                        )
                if latest_low <= last_stop_price:
                    time.sleep(3.0)
                    if abs(self.exchange.fetch_symbol_position_amount(signal.symbol)) <= 0.0:
                        stop_exit_price = last_stop_price
                        stop_exit_reason = "стоп исполнен на бирже"
                        try:
                            stop_fill = self.exchange.fetch_order_fill(signal.symbol, position.stop_order_id)
                        except Exception as exc:
                            unresolved_details = {
                                "position_id": position.position_id,
                                "stop_order_id": position.stop_order_id,
                                "last_known_stop_price": last_stop_price,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                            self.artifacts.append_event("stop_exit_fill_unresolved", signal.symbol, unresolved_details)
                            self._finalize_unresolved_position_exit(
                                position,
                                reason="стоп закрыл позицию на бирже, но exit fill не восстановлен",
                                source_event="stop_exit_fill_unresolved",
                                details=unresolved_details,
                                cancel_stop_order=False,
                                record_stop_cooldown=True,
                            )
                            return
                        else:
                            stop_exit_price = float(stop_fill.average_price)
                            self.artifacts.append_event(
                                "stop_exit_filled",
                                signal.symbol,
                                {
                                    "position_id": position.position_id,
                                    "order_id": stop_fill.order_id,
                                    "status": stop_fill.status,
                                    "fill_timestamp_ms": stop_fill.timestamp_ms,
                                    "fill_price": stop_fill.average_price,
                                    "filled_amount": stop_fill.filled_amount,
                                    "cost": stop_fill.cost,
                                    "fee_cost": stop_fill.fee_cost,
                                },
                            )
                        self._finalize_position(position, reason=stop_exit_reason, pnl_price=stop_exit_price)
                        return
                time.sleep(15.0)
            except LiveDataIntegrityError as exc:
                self._record_position_integrity_error(position, reason=str(exc))
                return
            except ExchangeConnectivityError as exc:
                self.artifacts.append_event(
                    "position_monitor_network_degraded",
                    signal.symbol,
                    {"position_id": position.position_id, "reason": f"{type(exc).__name__}: {exc}"},
                )
                self.logger(f"позиция {signal.symbol}, сеть/API временно недоступны: {exc}")
                time.sleep(30.0)
            except Exception as exc:
                self.artifacts.append_event(
                    "position_monitor_internal_error",
                    signal.symbol,
                    {"position_id": position.position_id, "reason": f"{type(exc).__name__}: {exc}"},
                )
                self._record_position_integrity_error(
                    position,
                    reason=f"position monitor internal error: {type(exc).__name__}: {exc}",
                )
                return

    def _record_position_integrity_error(self, position: LivePosition, *, reason: str) -> None:
        symbol = position.signal.symbol
        details = {
            "position_id": position.position_id,
            "reason": reason,
            "entry_price": position.entry_price,
            "remaining_amount": position.remaining_amount,
            "stop_order_id": position.stop_order_id,
            "current_stop_price": position.current_stop_price,
        }
        self.artifacts.append_event("position_integrity_error", symbol, details)
        self.telegram.send(
            channel="events",
            key=f"position_integrity_error:{position.position_id}",
            text=_format_position_integrity_error_message(position, reason=reason),
            symbol=symbol,
        )
        self.logger(f"{_compact_symbol(symbol)} integrity error · {reason}")

    def _finalize_unresolved_position_exit(
        self,
        position: LivePosition,
        *,
        reason: str,
        source_event: str,
        details: dict[str, object],
        cancel_stop_order: bool,
        record_stop_cooldown: bool,
    ) -> None:
        symbol = position.signal.symbol
        symbol_key = _position_symbol_key(symbol)
        with self._state_lock:
            self._open_positions.pop(symbol_key, None)
            self._closed_positions_total += 1
            if record_stop_cooldown:
                self._recent_stops.setdefault(symbol_key, []).append(time.time())
        if cancel_stop_order:
            self._cancel_position_stop_order(position, reason=reason)
        self._cancel_position_tp1_order(position, reason=reason)
        event_details = {
            "position_id": position.position_id,
            "reason": reason,
            "source_event": source_event,
            "pnl_status": "unresolved",
            **details,
        }
        self.artifacts.append_event("position_exit_unresolved", symbol, event_details)
        self.artifacts.append_position_exit_unresolved(position, reason=reason)
        self.telegram.send(
            channel="events",
            key=f"position_exit_unresolved:{position.position_id}",
            text=_format_position_integrity_error_message(position, reason=reason),
            symbol=symbol,
        )
        self.logger(f"{_compact_symbol(symbol)} exit unresolved · {reason}")

    def _finalize_position(
        self,
        position: LivePosition,
        *,
        reason: str,
        pnl_price: float | None,
        exit_amount: float | None = None,
    ) -> None:
        symbol_key = _position_symbol_key(position.signal.symbol)
        with self._state_lock:
            self._open_positions.pop(symbol_key, None)
            self._closed_positions_total += 1
        exit_price = pnl_price if pnl_price is not None else position.entry_price
        terminal_exit_amount = max(position.remaining_amount, 0.0) if exit_amount is None else exit_amount
        if not math.isfinite(terminal_exit_amount) or terminal_exit_amount < 0.0:
            raise LiveDataIntegrityError(
                f"invalid terminal exit amount: position_id={position.position_id} exit_amount={terminal_exit_amount}"
            )
        pnl_usdt = position.realized_pnl_usdt + terminal_exit_amount * (exit_price - position.entry_price)
        pnl_pct = _safe_divide(pnl_usdt, position.notional_usdt)
        if math.isfinite(pnl_usdt) and math.isfinite(position.notional_usdt) and position.notional_usdt > 0.0:
            with self._state_lock:
                self._closed_pnl_usdt_total += float(pnl_usdt)
                self._closed_notional_usdt_total += float(position.notional_usdt)
        self._cancel_position_tp1_order(position, reason=reason)
        if reason.startswith("стоп"):
            with self._state_lock:
                self._recent_stops.setdefault(symbol_key, []).append(time.time())
        else:
            self._cancel_position_stop_order(position, reason=reason)
        self.artifacts.append_event(
            "position_closed",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "reason": reason,
                "pnl_pct": pnl_pct,
                "pnl_usdt": pnl_usdt,
                "realized_pnl_before_terminal_exit_usdt": position.realized_pnl_usdt,
                "terminal_exit_amount": terminal_exit_amount,
                "terminal_exit_price": exit_price,
            },
        )
        self.artifacts.append_position_close(position, reason=reason, pnl_usdt=pnl_usdt, pnl_pct=pnl_pct)
        exit_timestamp_ms = int(time.time() * 1000)
        chart_path = self._render_close_chart(
            position,
            exit_price=exit_price,
            exit_timestamp_ms=exit_timestamp_ms,
            reason=reason,
            pnl_pct=pnl_pct,
        )
        close_text = _format_close_message(position, pnl_usdt=pnl_usdt, pnl_pct=pnl_pct, exit_price=exit_price)
        if chart_path is not None:
            try:
                close_message_id = self.telegram.send_photo_sync(
                    channel="positions",
                    photo_path=chart_path,
                    caption=close_text,
                    reply_to_message_id=position.telegram_open_message_id,
                )
                if close_message_id is not None:
                    self.artifacts.append_event(
                        "telegram_close_photo_sent",
                        position.signal.symbol,
                        {"position_id": position.position_id, "message_id": close_message_id, "chart_path": str(chart_path)},
                    )
                    return
                self.artifacts.append_event(
                    "telegram_close_photo_missing_id",
                    position.signal.symbol,
                    {"position_id": position.position_id, "chart_path": str(chart_path)},
                )
            except Exception as exc:
                self.artifacts.append_event(
                    "telegram_close_photo_failed",
                    position.signal.symbol,
                    {
                        "position_id": position.position_id,
                        "chart_path": str(chart_path),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                    },
                )
                self.logger(f"позиция {position.signal.symbol} закрыта, но Telegram-график закрытия не отправлен: {exc}")
        self.artifacts.append_event(
            "telegram_close_text_fallback",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "reason": "chart_unavailable" if chart_path is None else "chart_photo_unavailable",
                "reply_to_message_id": position.telegram_open_message_id or "",
            },
        )
        self.telegram.send(
            channel="positions",
            key=f"close:{position.position_id}",
            reply_to_message_id=position.telegram_open_message_id,
            text=close_text,
            symbol=position.signal.symbol,
        )

    def _render_open_chart(self, position: LivePosition) -> Path | None:
        signal = position.signal
        try:
            from research_tools.anomaly_strategy_backtest import render_anomaly_trade_chart

            entry_timeframe_ms = int(signal.entry_timeframe.to_milliseconds())
            opened_at_ms = int(position.opened_at_ms)
            start_ms = int(signal.start_timestamp_ms) - max(35 * 60_000, 70 * entry_timeframe_ms)
            end_ms = max(int(time.time() * 1000), opened_at_ms + max(60_000, 4 * entry_timeframe_ms))
            frame = self._fetch_chart_frame(
                signal.symbol,
                signal.entry_timeframe,
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
            )
            if frame.empty:
                self.artifacts.append_event(
                    "chart_render_failed",
                    signal.symbol,
                    {"position_id": position.position_id, "stage": "open", "reason": "empty_plot_source_frame"},
                )
                return None

            try:
                hourly_context_frame = self._fetch_chart_frame(
                    signal.symbol,
                    Timeframe.H1,
                    start_timestamp_ms=opened_at_ms - 4 * 24 * HOUR_MS - HOUR_MS,
                    end_timestamp_ms=opened_at_ms + HOUR_MS,
                )
            except Exception as exc:
                hourly_context_frame = pd.DataFrame()
                self.artifacts.append_event(
                    "chart_context_fetch_failed",
                    signal.symbol,
                    {
                        "position_id": position.position_id,
                        "stage": "open",
                        "timeframe": Timeframe.H1.value,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )

            chart_dir = self.artifacts.root / "charts"
            chart_dir.mkdir(parents=True, exist_ok=True)
            path = chart_dir / f"{position.position_id}_open.png"
            trade_row = {
                "symbol": signal.symbol,
                "status": "open",
                "anomaly_timestamp_ms": int(signal.start_timestamp_ms),
                "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                "entry_timestamp_ms": int(position.opened_at_ms),
                "entry_timestamp_utc": position.opened_at_utc,
                "entry_price": float(position.entry_price),
                "signal_entry_price": float(signal.entry_price),
                "signal_entry_timestamp_ms": int(signal.decision_timestamp_ms),
                "initial_stop": float(position.stop_price),
                "tp1_price": float(position.tp1_price),
                "box_high": float(signal.box_high),
                "net_return": 0.0,
                "exit_reason": "open",
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "levels_timeframe": signal.levels_timeframe.value,
                "entry_timeframe": signal.entry_timeframe.value,
            }
            render_anomaly_trade_chart(
                frame=frame,
                trade=trade_row,
                output_path=path,
                context_timeframe_ms=int(signal.levels_timeframe.to_milliseconds()),
                hourly_context_frame=hourly_context_frame,
                draw_risk_reward_blocks=False,
                draw_exit_marker=False,
            )
            self.artifacts.append_event(
                "chart_rendered",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "stage": "open",
                    "chart_path": str(path),
                    "renderer": "anomaly_backtest_trade_chart",
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                },
            )
            return path
        except Exception as exc:
            self.artifacts.append_event(
                "chart_render_failed",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "stage": "open",
                    "renderer": "anomaly_backtest_trade_chart",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return None


    def _render_close_chart(
        self,
        position: LivePosition,
        *,
        exit_price: float,
        exit_timestamp_ms: int,
        reason: str,
        pnl_pct: float,
    ) -> Path | None:
        signal = position.signal
        try:
            from research_tools.anomaly_strategy_backtest import render_anomaly_trade_chart

            entry_timeframe_ms = int(signal.entry_timeframe.to_milliseconds())
            start_ms = int(signal.start_timestamp_ms) - max(35 * 60_000, 70 * entry_timeframe_ms)
            end_ms = max(int(time.time() * 1000), int(exit_timestamp_ms) + max(60_000, 4 * entry_timeframe_ms))
            frame = self._fetch_chart_frame(
                signal.symbol,
                signal.entry_timeframe,
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
            )
            if frame.empty:
                self.artifacts.append_event(
                    "chart_render_failed",
                    signal.symbol,
                    {"position_id": position.position_id, "reason": "empty_plot_source_frame"},
                )
                return None
            chart_dir = self.artifacts.root / "charts"
            chart_dir.mkdir(parents=True, exist_ok=True)
            path = chart_dir / f"{position.position_id}.png"
            try:
                hourly_context_frame = self._fetch_chart_frame(
                    signal.symbol,
                    Timeframe.H1,
                    start_timestamp_ms=end_ms - 4 * 24 * HOUR_MS - HOUR_MS,
                    end_timestamp_ms=end_ms + HOUR_MS,
                )
            except Exception as exc:
                hourly_context_frame = pd.DataFrame()
                self.artifacts.append_event(
                    "chart_context_fetch_failed",
                    signal.symbol,
                    {
                        "position_id": position.position_id,
                        "stage": "close",
                        "timeframe": Timeframe.H1.value,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
            trade_row = {
                "symbol": signal.symbol,
                "status": "closed",
                "anomaly_timestamp_ms": int(signal.start_timestamp_ms),
                "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                "entry_timestamp_ms": int(position.opened_at_ms),
                "entry_timestamp_utc": position.opened_at_utc,
                "exit_timestamp_ms": int(exit_timestamp_ms),
                "entry_price": float(position.entry_price),
                "signal_entry_price": float(signal.entry_price),
                "signal_entry_timestamp_ms": int(signal.decision_timestamp_ms),
                "initial_stop": float(position.stop_price),
                "tp1_price": float(position.tp1_price),
                "box_high": float(signal.box_high),
                "exit_price": float(exit_price),
                "net_return": float(pnl_pct),
                "exit_reason": reason,
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "levels_timeframe": signal.levels_timeframe.value,
                "entry_timeframe": signal.entry_timeframe.value,
            }
            render_anomaly_trade_chart(
                frame=frame,
                trade=trade_row,
                output_path=path,
                context_timeframe_ms=int(signal.levels_timeframe.to_milliseconds()),
                hourly_context_frame=hourly_context_frame,
            )
            self.artifacts.append_event(
                "chart_rendered",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "chart_path": str(path),
                    "renderer": "anomaly_backtest_trade_chart",
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                },
            )
            return path
        except Exception as exc:
            self.artifacts.append_event(
                "chart_render_failed",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "renderer": "anomaly_backtest_trade_chart",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return None

    def _buffer_live_ohlcv_cache_write(self, symbol: str, timeframe: Timeframe, frame: pd.DataFrame) -> int:
        if self._ohlcv_cache_storage is None or not self.config.live_ohlcv_cache_write_enabled or frame.empty:
            return 0
        prepared = _prepare_cached_ohlcv_frame(frame)
        if prepared.empty:
            return 0
        symbol_key = _position_symbol_key(symbol)
        buffer_key = (symbol_key, symbol, timeframe.value)
        self._live_ohlcv_write_buffer.setdefault(buffer_key, []).append(prepared)
        buffered_rows = int(len(prepared))
        self._live_ohlcv_pending_rows += buffered_rows
        self.artifacts.append_event(
            "live_ohlcv_cache_buffered",
            symbol,
            {
                "timeframe": timeframe.value,
                "buffered_rows": buffered_rows,
                "pending_rows": int(self._live_ohlcv_pending_rows),
                "min_timestamp_ms": int(prepared["timestamp"].min()),
                "max_timestamp_ms": int(prepared["timestamp"].max()),
            },
        )
        return buffered_rows

    def _flush_live_ohlcv_cache_if_due(
        self,
        *,
        force: bool = False,
        reason: str,
        progress_callback: Callable[[str, str, int, int], None] | None = None,
        max_symbol_timeframes_override: int | None = None,
    ) -> int:
        storage = self._ohlcv_cache_storage
        if storage is None or not self._live_ohlcv_write_buffer:
            return 0
        elapsed = time.monotonic() - self._last_live_ohlcv_cache_flush_at
        if (
            not force
            and elapsed < float(self.config.live_ohlcv_cache_flush_interval_seconds)
            and self._live_ohlcv_pending_rows < int(self.config.live_ohlcv_cache_max_buffer_rows)
        ):
            return 0
        pending_items = self._live_ohlcv_write_buffer
        pending_rows = int(self._live_ohlcv_pending_rows)
        self._live_ohlcv_write_buffer = {}
        self._live_ohlcv_pending_rows = 0
        flushed_rows_total = 0
        remaining: dict[tuple[str, str, str], list[pd.DataFrame]] = {}
        remaining_rows = 0
        failed_symbol_timeframes = 0
        if max_symbol_timeframes_override is not None:
            flush_limit = max(1, int(max_symbol_timeframes_override))
        else:
            flush_limit = None if force else self.config.live_ohlcv_cache_flush_max_symbol_timeframes
        items = list(pending_items.items())
        selected_items = items if flush_limit is None else items[: max(0, int(flush_limit))]
        deferred_items = items[len(selected_items) :]
        for key, frames in deferred_items:
            remaining[key] = frames
            remaining_rows += int(sum(len(frame) for frame in frames))
        selected_total = int(len(selected_items))
        for selected_index, ((_symbol_key, symbol, timeframe_value), frames) in enumerate(selected_items, start=1):
            if progress_callback is not None:
                progress_callback(str(symbol), str(timeframe_value), int(selected_index), selected_total)
            try:
                timeframe = Timeframe(timeframe_value)
                combined = _concat_cached_ohlcv_frames(frames)
                added_rows = int(storage.save_incremental(symbol, timeframe, combined))
                flushed_rows_total += int(len(combined))
                self.artifacts.append_event(
                    "live_ohlcv_cache_flushed",
                    symbol,
                    {
                        "timeframe": timeframe.value,
                        "reason": reason,
                        "input_rows": int(len(combined)),
                        "added_rows": added_rows,
                        "pending_rows_before_flush": pending_rows,
                    },
                )
            except Exception as exc:
                remaining[(_symbol_key, symbol, timeframe_value)] = frames
                failed_symbol_timeframes += 1
                failed_rows = sum(len(frame) for frame in frames)
                remaining_rows += int(failed_rows)
                self.artifacts.append_event(
                    "live_ohlcv_cache_flush_failed",
                    symbol,
                    {
                        "timeframe": timeframe_value,
                        "reason": reason,
                        "input_rows": int(failed_rows),
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:1000],
                    },
                )
        self._live_ohlcv_write_buffer = remaining
        self._live_ohlcv_pending_rows = remaining_rows
        self._last_live_ohlcv_cache_flush_at = time.monotonic()
        self.artifacts.append_event(
            "live_ohlcv_cache_flush_summary",
            "__live__",
            {
                "reason": reason,
                "force": bool(force),
                "pending_rows_before_flush": pending_rows,
                "flushed_rows": int(flushed_rows_total),
                "remaining_rows": int(remaining_rows),
                "failed_symbol_timeframes": int(failed_symbol_timeframes),
                "flushed_symbol_timeframes": int(len(selected_items)),
                "deferred_symbol_timeframes": int(len(deferred_items)),
                "flush_max_symbol_timeframes": flush_limit if flush_limit is not None else "",
                "flush_max_symbol_timeframes_override": (
                    int(max_symbol_timeframes_override) if max_symbol_timeframes_override is not None else ""
                ),
            },
        )
        return flushed_rows_total


    def _fetch_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        if self._ohlcv_cache_storage is not None:
            return self._fetch_cached_chart_frame(
                symbol,
                timeframe,
                start_timestamp_ms=start_timestamp_ms,
                end_timestamp_ms=end_timestamp_ms,
            )
        return self._fetch_uncached_chart_frame(
            symbol,
            timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )

    def _fetch_uncached_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        if int(timeframe.to_milliseconds()) >= int(Timeframe.M1.to_milliseconds()):
            return self.exchange.fetch_ohlcv(symbol, timeframe, start_timestamp_ms, end_timestamp_ms)
        return self._fetch_aggtrade_chart_frame(
            symbol,
            timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )

    def _fetch_cached_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        storage = self._ohlcv_cache_storage
        if storage is None:
            return self._fetch_uncached_chart_frame(
                symbol,
                timeframe,
                start_timestamp_ms=start_timestamp_ms,
                end_timestamp_ms=end_timestamp_ms,
            )
        timeframe_ms = int(timeframe.to_milliseconds())
        cache_end_ms = _latest_closed_candle_start_ms(timeframe, now_ms=int(end_timestamp_ms))
        expected_start_ms = (int(start_timestamp_ms) // timeframe_ms) * timeframe_ms
        expected_end_ms = min(cache_end_ms, (int(end_timestamp_ms) // timeframe_ms) * timeframe_ms)
        symbol_key = _position_symbol_key(symbol)
        memory_key = (symbol_key, timeframe.value)
        if memory_key in self._live_ohlcv_frame_cache and _cached_frame_covers_window(
            self._live_ohlcv_frame_cache[memory_key],
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
        ):
            load_status = "memory_hit"
            load_reason = "process_memory_cache"
            cached_rows_before = int(len(self._live_ohlcv_frame_cache[memory_key]))
            cached = self._live_ohlcv_frame_cache[memory_key]
        else:
            load_result = storage.load_window_result(
                symbol,
                timeframe,
                start_timestamp_ms=expected_start_ms,
                end_timestamp_ms=expected_end_ms,
            )
            load_status = load_result.status
            load_reason = load_result.reason
            cached_rows_before = int(len(load_result.frame)) if load_result.frame is not None else 0
            cached = _prepare_cached_ohlcv_frame(load_result.frame)
            if memory_key in self._live_ohlcv_frame_cache:
                cached = _concat_cached_ohlcv_frames([self._live_ohlcv_frame_cache[memory_key], cached])
            self._live_ohlcv_frame_cache[memory_key] = cached
        fetched_rows = 0
        buffered_rows = 0
        fetched_ranges: list[str] = []
        fetched_frames: list[pd.DataFrame] = []
        missing_ranges = _missing_ohlcv_ranges(
            cached,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
            timeframe_ms=timeframe_ms,
        )
        for missing_start_ms, missing_end_ms in missing_ranges:
            fetch_end_ms = int(missing_end_ms) + timeframe_ms - 1
            fetched = self._fetch_uncached_chart_frame(
                symbol,
                timeframe,
                start_timestamp_ms=missing_start_ms,
                end_timestamp_ms=fetch_end_ms,
            )
            if not fetched.empty:
                fetched = fetched.copy()
                fetched["live_cache_source"] = (
                    "exchange_ohlcv"
                    if timeframe_ms >= int(Timeframe.M1.to_milliseconds())
                    else "binance_futures_ws_aggTrade_explicit_backfill"
                    if self.aggtrade_source is not None
                    else "binance_futures_aggTrades"
                )
                fetched["live_cache_target_timeframe"] = timeframe.value
                fetched["live_cache_version"] = "p168_live_ohlcv_cache_v1"
                fetched_rows += int(len(fetched))
                fetched_ranges.append(f"{missing_start_ms}:{fetch_end_ms}")
                fetched_frames.append(fetched)
        if fetched_frames:
            fetched_combined = _concat_cached_ohlcv_frames(fetched_frames)
            if self.config.live_ohlcv_cache_write_enabled:
                buffered_rows = self._buffer_live_ohlcv_cache_write(symbol, timeframe, fetched_combined)
            cached = _concat_cached_ohlcv_frames([cached, fetched_combined])
            self._live_ohlcv_frame_cache[memory_key] = cached
        window_end_ms = min(int(end_timestamp_ms), int(expected_end_ms))
        window = cached.loc[
            (cached["timestamp"].astype("int64") >= int(start_timestamp_ms))
            & (cached["timestamp"].astype("int64") <= int(window_end_ms))
        ].copy()
        remaining_ranges = _missing_ohlcv_ranges(
            window,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
            timeframe_ms=timeframe_ms,
        )
        cache_status = "hit" if not missing_ranges else "filled"
        if missing_ranges and fetched_rows > 0:
            self._cycle_ohlcv_cache_filled_reads += 1
            self._cycle_ohlcv_cache_fetched_rows += int(fetched_rows)
        if remaining_ranges:
            cache_status = "gap"
            self._cycle_ohlcv_cache_gap_reads += 1
            self._cycle_ohlcv_cache_remaining_gap_count += int(len(remaining_ranges))
            self.artifacts.append_event(
                "live_ohlcv_cache_gap",
                symbol,
                {
                    "timeframe": timeframe.value,
                    "cache_status": load_status,
                    "cache_reason": load_reason,
                    "start_timestamp_ms": int(start_timestamp_ms),
                    "end_timestamp_ms": int(end_timestamp_ms),
                    "expected_start_ms": int(expected_start_ms),
                    "expected_end_ms": int(expected_end_ms),
                    "missing_ranges": [f"{start}:{end}" for start, end in remaining_ranges],
                    "fetched_ranges": fetched_ranges,
                    "fetched_rows": fetched_rows,
                    "buffered_rows": buffered_rows,
                    "write_enabled": bool(self.config.live_ohlcv_cache_write_enabled),
                },
            )
        self.artifacts.append_event(
            "live_ohlcv_cache_read",
            symbol,
            {
                "timeframe": timeframe.value,
                "status": cache_status,
                "load_status": load_status,
                "load_reason": load_reason,
                "cached_rows_before": cached_rows_before,
                "window_rows": int(len(window)),
                "expected_start_ms": int(expected_start_ms),
                "expected_end_ms": int(expected_end_ms),
                "window_end_ms": int(window_end_ms),
                "missing_range_count": int(len(missing_ranges)),
                "remaining_gap_count": int(len(remaining_ranges)),
                "fetched_rows": fetched_rows,
                "buffered_rows": buffered_rows,
                "pending_rows": int(self._live_ohlcv_pending_rows),
                "write_enabled": bool(self.config.live_ohlcv_cache_write_enabled),
            },
        )
        return window.sort_values("timestamp").reset_index(drop=True)

    def _fetch_aggtrade_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        timeframe_ms = int(timeframe.to_milliseconds())
        if timeframe_ms <= 0 or timeframe_ms >= int(Timeframe.M1.to_milliseconds()):
            raise ValueError(f"invalid_seconds_chart_timeframe:{timeframe.value}")
        if self.aggtrade_source is not None:
            all_rows = self._fetch_ws_aggtrade_raw_rows(
                symbol,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
        else:
            all_rows = self._fetch_aggtrade_raw_rows_cached(
                symbol,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
        if not all_rows:
            return pd.DataFrame(columns=list(REQUIRED_PRICE_COLUMNS) + ["volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"])
        return _aggregate_aggtrades_to_ohlcv_frame(
            pd.DataFrame(all_rows),
            timeframe_ms=timeframe_ms,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )

    @staticmethod
    def _time_ranges_duration_ms(ranges: tuple[tuple[int, int], ...] | list[tuple[int, int]]) -> int:
        return int(sum(max(0, int(end) - int(start) + 1) for start, end in ranges))

    def _prune_aggtrade_process_cache(self, symbol_key: str, *, now_ms: int) -> None:
        ttl_ms = max(0, int(self.config.live_aggtrade_rest_cache_ttl_ms))
        if ttl_ms <= 0:
            self._aggtrade_raw_process_cache.pop(symbol_key, None)
            return
        cutoff_ms = int(now_ms) - ttl_ms
        ranges = [
            cached_range
            for cached_range in self._aggtrade_raw_process_cache.get(symbol_key, [])
            if int(cached_range.end_timestamp_ms) >= cutoff_ms
        ]
        if ranges:
            self._aggtrade_raw_process_cache[symbol_key] = ranges
        else:
            self._aggtrade_raw_process_cache.pop(symbol_key, None)

    def _aggtrade_cached_ranges(self, symbol_key: str, *, now_ms: int | None = None) -> list[AggTradeRawRange]:
        if now_ms is not None:
            self._prune_aggtrade_process_cache(symbol_key, now_ms=now_ms)
        ranges = list(self._aggtrade_raw_process_cache.get(symbol_key, []))
        ranges.extend(self._current_cycle_aggtrade_cache.get(symbol_key, []))
        return ranges

    def _remember_aggtrade_raw_range(
        self,
        symbol_key: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        rows: list[dict[str, object]],
    ) -> None:
        raw_range = AggTradeRawRange(
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
            rows=tuple(dict(row) for row in rows),
        )
        self._current_cycle_aggtrade_cache.setdefault(symbol_key, []).append(raw_range)
        if int(self.config.live_aggtrade_rest_cache_ttl_ms) > 0:
            self._aggtrade_raw_process_cache.setdefault(symbol_key, []).append(raw_range)
            self._prune_aggtrade_process_cache(symbol_key, now_ms=int(time.time() * 1000))

    def _coalesce_aggtrade_missing_ranges(
        self,
        ranges: list[tuple[int, int]],
        *,
        request_start_ms: int,
        request_end_ms: int,
    ) -> list[tuple[int, int]]:
        padding_ms = max(0, int(self.config.live_aggtrade_rest_cache_padding_ms))
        padded = [
            (max(int(request_start_ms), int(start) - padding_ms), min(int(request_end_ms), int(end) + padding_ms))
            for start, end in ranges
            if int(start) <= int(end)
        ]
        return _merge_time_ranges(padded)

    def _fetch_aggtrade_raw_ranges_cached(
        self,
        symbol: str,
        requested_ranges: list[tuple[int, int]],
        *,
        fetch_window_start_ms: int | None = None,
        fetch_window_end_ms: int | None = None,
    ) -> tuple[list[dict[str, object]], list[str], int, int]:
        requested_ranges = _merge_time_ranges(
            [(int(start), int(end)) for start, end in requested_ranges if int(start) <= int(end)]
        )
        if not requested_ranges:
            return [], [], 0, 0
        symbol_key = _position_symbol_key(symbol)
        request_start_ms = min(start for start, _end in requested_ranges)
        request_end_ms = max(end for _start, end in requested_ranges)
        fetch_window_start = int(fetch_window_start_ms) if fetch_window_start_ms is not None else request_start_ms
        fetch_window_end = int(fetch_window_end_ms) if fetch_window_end_ms is not None else request_end_ms
        cached_ranges = self._aggtrade_cached_ranges(symbol_key, now_ms=int(time.time() * 1000))
        missing_ranges: list[tuple[int, int]] = []
        for requested_start, requested_end in requested_ranges:
            missing_ranges.extend(
                _missing_aggtrade_raw_ranges(
                    cached_ranges,
                    start_timestamp_ms=requested_start,
                    end_timestamp_ms=requested_end,
                )
            )
        missing_ranges = _merge_time_ranges(missing_ranges)
        if not missing_ranges:
            self._cycle_aggtrade_cache_hits += 1
            self._cycle_aggtrade_process_cache_hits += 1
        elif len(missing_ranges) < len(requested_ranges):
            self._cycle_aggtrade_cache_hits += 1
        original_missing_count = len(missing_ranges)
        fetch_ranges = self._coalesce_aggtrade_missing_ranges(
            missing_ranges,
            request_start_ms=fetch_window_start,
            request_end_ms=fetch_window_end,
        )
        if original_missing_count > len(fetch_ranges):
            self._cycle_aggtrade_coalesced_missing_ranges += original_missing_count - len(fetch_ranges)
        backfill_ranges: list[str] = []
        fetched_rows_total = 0
        for fetch_start_ms, fetch_end_ms in fetch_ranges:
            rows = self._fetch_aggtrade_raw_rows(
                symbol,
                start_timestamp_ms=int(fetch_start_ms),
                end_timestamp_ms=int(fetch_end_ms),
            )
            fetched_rows_total += int(len(rows))
            self._cycle_aggtrade_rest_fetched_ms += max(0, int(fetch_end_ms) - int(fetch_start_ms) + 1)
            backfill_ranges.append(f"{fetch_start_ms}:{fetch_end_ms}")
            self._remember_aggtrade_raw_range(
                symbol_key,
                start_timestamp_ms=int(fetch_start_ms),
                end_timestamp_ms=int(fetch_end_ms),
                rows=rows,
            )
        all_ranges = self._aggtrade_cached_ranges(symbol_key)
        all_rows: list[dict[str, object]] = []
        for requested_start, requested_end in requested_ranges:
            for cached_range in all_ranges:
                if cached_range.end_timestamp_ms < requested_start or cached_range.start_timestamp_ms > requested_end:
                    continue
                all_rows.extend(
                    _filter_aggtrade_rows_by_time(
                        cached_range.rows,
                        start_timestamp_ms=requested_start,
                        end_timestamp_ms=requested_end,
                    )
                )
        return _dedupe_aggtrade_rows(all_rows), backfill_ranges, fetched_rows_total, original_missing_count

    def _fetch_ws_aggtrade_raw_rows(
        self,
        symbol: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> list[dict[str, object]]:
        source = self.aggtrade_source
        if source is None:
            raise LiveDataIntegrityError("ws aggTrade source is disabled on WS fetch path")
        self._cycle_aggtrade_requests += 1
        request_start_ms = int(start_timestamp_ms)
        request_end_ms = int(end_timestamp_ms)
        read_result = source.read_rows(
            symbol,
            start_timestamp_ms=request_start_ms,
            end_timestamp_ms=request_end_ms,
        )
        all_rows = [dict(row) for row in read_result.rows]
        backfilled_rows = 0
        backfill_ranges: list[str] = []
        missing_total_ms = self._time_ranges_duration_ms(read_result.missing_ranges)
        max_backfill_ms = int(self.config.live_ws_aggtrade_max_backfill_ms)
        if read_result.missing_ranges and missing_total_ms > max_backfill_ms:
            self._cycle_ws_aggtrade_coverage_pending += 1
            self.artifacts.append_event(
                "ws_aggtrade_frame_read",
                symbol,
                {
                    "source": source.source_id,
                    "status": "coverage_pending",
                    "read_status": read_result.status,
                    "reason": read_result.reason or "",
                    "subscribed": bool(read_result.subscribed),
                    "connection_status": read_result.connection_status,
                    "last_error": (read_result.last_error or "")[:500],
                    "start_timestamp_ms": request_start_ms,
                    "end_timestamp_ms": request_end_ms,
                    "ws_rows": int(len(read_result.rows)),
                    "buffer_row_count": int(read_result.buffer_row_count),
                    "missing_range_count": int(len(read_result.missing_ranges)),
                    "missing_ranges": [f"{start}:{end}" for start, end in read_result.missing_ranges],
                    "missing_total_ms": int(missing_total_ms),
                    "backfill_max_ms": int(max_backfill_ms),
                    "backfill_skipped": True,
                    "backfill_ranges": [],
                    "backfilled_rows": 0,
                    "result_rows": 0,
                    "last_trade_timestamp_ms": read_result.last_trade_timestamp_ms if read_result.last_trade_timestamp_ms is not None else "",
                    "last_receive_at_ms": read_result.last_receive_at_ms if read_result.last_receive_at_ms is not None else "",
                },
            )
            raise LiveWsAggTradeCoveragePending(
                "ws_aggtrade_coverage_pending;"
                f"status={read_result.status};"
                f"missing_total_ms={missing_total_ms};"
                f"backfill_max_ms={max_backfill_ms}",
                symbol=symbol,
                start_timestamp_ms=request_start_ms,
                end_timestamp_ms=request_end_ms,
                missing_ranges=tuple(read_result.missing_ranges),
                status=read_result.status,
                reason=read_result.reason,
            )
        if read_result.missing_ranges:
            cached_rows, backfill_ranges, backfilled_rows, original_missing_count = self._fetch_aggtrade_raw_ranges_cached(
                symbol,
                [(int(start), int(end)) for start, end in read_result.missing_ranges],
                fetch_window_start_ms=request_start_ms,
                fetch_window_end_ms=request_end_ms,
            )
            all_rows.extend(cached_rows)
            for missing_start_ms, missing_end_ms in read_result.missing_ranges:
                source.add_backfill_rows(
                    symbol,
                    _filter_aggtrade_rows_by_time(
                        cached_rows,
                        start_timestamp_ms=int(missing_start_ms),
                        end_timestamp_ms=int(missing_end_ms),
                    ),
                    start_timestamp_ms=int(missing_start_ms),
                    end_timestamp_ms=int(missing_end_ms),
                )
        all_rows = _dedupe_aggtrade_rows(all_rows)
        if read_result.missing_ranges:
            self._cycle_ws_aggtrade_backfill_reads += 1
            self._cycle_ws_aggtrade_backfilled_rows += int(backfilled_rows)
            if (read_result.connection_status or "").lower() != "connected":
                self._cycle_ws_aggtrade_not_connected_backfill_reads += 1
        else:
            self._cycle_aggtrade_cache_hits += 1
        self.artifacts.append_event(
            "ws_aggtrade_frame_read",
            symbol,
            {
                "source": source.source_id,
                "status": read_result.status,
                "reason": read_result.reason or "",
                "subscribed": bool(read_result.subscribed),
                "connection_status": read_result.connection_status,
                "last_error": (read_result.last_error or "")[:500],
                "start_timestamp_ms": request_start_ms,
                "end_timestamp_ms": request_end_ms,
                "ws_rows": int(len(read_result.rows)),
                "buffer_row_count": int(read_result.buffer_row_count),
                "missing_range_count": int(len(read_result.missing_ranges)),
                "cache_missing_range_count": int(original_missing_count) if read_result.missing_ranges else 0,
                "network_backfill_range_count": int(len(backfill_ranges)),
                "missing_ranges": [f"{start}:{end}" for start, end in read_result.missing_ranges],
                "missing_total_ms": int(missing_total_ms),
                "backfill_max_ms": int(max_backfill_ms),
                "backfill_skipped": False,
                "backfill_ranges": backfill_ranges,
                "backfilled_rows": int(backfilled_rows),
                "result_rows": int(len(all_rows)),
                "last_trade_timestamp_ms": read_result.last_trade_timestamp_ms if read_result.last_trade_timestamp_ms is not None else "",
                "last_receive_at_ms": read_result.last_receive_at_ms if read_result.last_receive_at_ms is not None else "",
            },
        )
        return all_rows

    def _fetch_aggtrade_raw_rows_cached(
        self,
        symbol: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> list[dict[str, object]]:
        self._cycle_aggtrade_requests += 1
        request_start_ms = int(start_timestamp_ms)
        request_end_ms = int(end_timestamp_ms)
        rows, _backfill_ranges, _fetched_rows_total, _missing_count = self._fetch_aggtrade_raw_ranges_cached(
            symbol,
            [(request_start_ms, request_end_ms)],
            fetch_window_start_ms=request_start_ms,
            fetch_window_end_ms=request_end_ms,
        )
        return rows

    def _fetch_aggtrade_raw_rows(
        self,
        symbol: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> list[dict[str, object]]:
        market_id = self.exchange.get_market_id(symbol)
        all_rows: list[dict[str, object]] = []
        next_from_id: int | None = None
        previous_last_id: int | None = None
        while True:
            params: dict[str, object] = {"symbol": market_id, "limit": 1000}
            if next_from_id is None:
                params["startTime"] = int(start_timestamp_ms)
                params["endTime"] = int(end_timestamp_ms)
            else:
                params["fromId"] = int(next_from_id)
                params["endTime"] = int(end_timestamp_ms)
            self._cycle_aggtrade_network_calls += 1
            rows = self.exchange.fetch_binance_agg_trades(symbol=symbol, params=params)
            if not rows:
                break
            all_rows.extend(dict(row) for row in rows)
            last_row = rows[-1]
            last_id = _resolve_aggtrade_id(last_row)
            last_ts = _resolve_aggtrade_timestamp(last_row)
            if last_id is None or (previous_last_id is not None and last_id <= previous_last_id):
                break
            previous_last_id = last_id
            next_from_id = last_id + 1
            if last_ts is not None and last_ts >= int(end_timestamp_ms):
                break
            if len(rows) < 1000:
                break
        return all_rows

    def _reconcile_orphan_orders(self, symbols: list[str], *, cycle: int, force: bool = False) -> int:
        if not force and cycle != 1 and cycle % self.config.order_reconcile_interval_cycles != 0:
            return 0
        max_checks = len(symbols) if force else min(self.config.order_reconcile_batch_size, len(symbols))
        if force:
            self.artifacts.append_event(
                "orphan_order_reconcile_started",
                "__live__",
                {"scope": "run_trade_symbols_only", "symbols_to_check": max_checks, "cycle": cycle},
            )
        if not symbols:
            return 0
        checked_symbols: set[str] = set()
        cancelled_total = 0
        for _ in range(max_checks):
            symbol = symbols[self._order_reconcile_cursor % len(symbols)]
            self._order_reconcile_cursor += 1
            symbol_key = _position_symbol_key(symbol)
            if symbol_key in checked_symbols:
                continue
            checked_symbols.add(symbol_key)
            with self._state_lock:
                if symbol_key in self._open_positions or symbol_key in self._opening_symbols:
                    continue
            try:
                cancelled_total += self._cancel_orphan_orders_for_symbol(symbol, symbol_key=symbol_key)
            except Exception as exc:
                self.artifacts.append_event(
                    "orphan_order_reconcile_failed",
                    symbol,
                    {"symbol_key": symbol_key, "reason": f"{type(exc).__name__}: {exc}"},
                )
        if cancelled_total:
            with self._state_lock:
                self._orphan_orders_cancelled_total += cancelled_total
        return cancelled_total

    def _cancel_orphan_orders_for_symbol(self, symbol: str, *, symbol_key: str) -> int:
        ordinary_orders = self.exchange.fetch_open_orders(symbol)
        stop_orders = self.exchange.fetch_open_stop_orders(symbol)
        ordinary_order_ids = [_resolve_order_id(order) for order in ordinary_orders]
        ordinary_order_ids = [order_id for order_id in ordinary_order_ids if order_id]
        stop_order_ids = [_resolve_order_id(order) for order in stop_orders]
        stop_order_ids = [order_id for order_id in stop_order_ids if order_id]
        if not ordinary_order_ids and not stop_order_ids:
            return 0
        actual_amount = abs(self.exchange.fetch_symbol_position_amount(symbol))
        if actual_amount > 0.0:
            self.artifacts.append_event(
                "orphan_order_reconcile_kept_with_position",
                symbol,
                {
                    "symbol_key": symbol_key,
                    "ordinary_open_orders": len(ordinary_order_ids),
                    "conditional_stop_orders": len(stop_order_ids),
                    "exchange_position_amount": actual_amount,
                },
            )
            return 0
        cancelled = 0
        failed = 0
        for order_id in ordinary_order_ids:
            try:
                self.exchange.cancel_order(symbol, order_id)
                cancelled += 1
            except Exception as exc:
                failed += 1
                self.artifacts.append_event(
                    "orphan_order_cancel_failed",
                    symbol,
                    {
                        "symbol_key": symbol_key,
                        "order_id": order_id,
                        "order_source": "ordinary_open_orders",
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
        for order_id in stop_order_ids:
            try:
                self.exchange.cancel_stop_order(symbol, order_id)
                cancelled += 1
            except Exception as exc:
                failed += 1
                self.artifacts.append_event(
                    "orphan_order_cancel_failed",
                    symbol,
                    {
                        "symbol_key": symbol_key,
                        "order_id": order_id,
                        "order_source": "conditional_stop_orders",
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
        self.artifacts.append_event(
            "orphan_orders_reconciled",
            symbol,
            {
                "symbol_key": symbol_key,
                "ordinary_seen": len(ordinary_order_ids),
                "conditional_stop_seen": len(stop_order_ids),
                "cancelled": cancelled,
                "failed": failed,
            },
        )
        return cancelled

    def _create_verified_position_stop_order(
        self,
        symbol: str,
        *,
        amount: float,
        stop_price: float,
        position_id: str,
        reason: str,
    ) -> str:
        if not math.isfinite(amount) or amount <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop order amount: position_id={position_id} amount={amount}")
        if not math.isfinite(stop_price) or stop_price <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop order price: position_id={position_id} stop_price={stop_price}")
        stop_client_order_id = _live_client_order_id("stop", symbol, position_id, reason)
        stop_order = self.exchange.create_stop_market_order(
            symbol,
            "sell",
            amount,
            stop_price,
            client_order_id=stop_client_order_id,
        )
        stop_order_id = _resolve_order_id(stop_order) or ""
        if not stop_order_id:
            raise LiveDataIntegrityError(f"stop order returned no id: position_id={position_id} symbol={symbol}")
        self._verify_open_stop_order(
            symbol,
            order_id=stop_order_id,
            expected_client_order_id=stop_client_order_id,
            expected_side="sell",
            expected_amount=amount,
            expected_stop_price=stop_price,
            position_id=position_id,
            reason=reason,
        )
        self.artifacts.append_event(
            "position_stop_order_verified",
            symbol,
            {
                "position_id": position_id,
                "order_id": stop_order_id,
                "client_order_id": stop_client_order_id,
                "stop_price": stop_price,
                "amount": amount,
                "reason": reason,
            },
        )
        return stop_order_id

    def _verify_open_stop_order(
        self,
        symbol: str,
        *,
        order_id: str,
        expected_client_order_id: str,
        expected_side: str,
        expected_amount: float,
        expected_stop_price: float,
        position_id: str,
        reason: str,
    ) -> None:
        ordinary_open_orders: list[dict[str, object]] = []
        conditional_stop_orders: list[dict[str, object]] = []
        order: dict[str, object] | None = None
        order_source = ""
        client_lookup_error = ""
        verification_attempts = 5
        for attempt in range(1, verification_attempts + 1):
            conditional_stop_orders = self.exchange.fetch_open_stop_orders(symbol)
            order = next(
                (row for row in conditional_stop_orders if isinstance(row, dict) and _order_matches_order_id(row, order_id)),
                None,
            )
            if order is not None:
                order_source = "open_algo_orders_order_id"
            else:
                order = next(
                    (
                        row
                        for row in conditional_stop_orders
                        if isinstance(row, dict) and _order_matches_client_order_id(row, expected_client_order_id)
                    ),
                    None,
                )
                if order is not None:
                    order_source = "open_algo_orders_client_order_id"
            if order is None:
                try:
                    fetched_stop_order = self.exchange.fetch_stop_order_by_client_order_id(symbol, expected_client_order_id)
                except Exception as exc:
                    client_lookup_error = f"{type(exc).__name__}: {exc}"
                else:
                    if _order_matches_order_id(fetched_stop_order, order_id) or _order_matches_client_order_id(
                        fetched_stop_order,
                        expected_client_order_id,
                    ):
                        order = fetched_stop_order
                        order_source = "algo_client_order_id_lookup"
                        self.artifacts.append_event(
                            "position_stop_order_client_lookup_confirmed",
                            symbol,
                            {
                                "position_id": position_id,
                                "order_id": order_id,
                                "client_order_id": expected_client_order_id,
                                "reason": reason,
                                "verification_attempt": attempt,
                                "conditional_stop_orders_seen": len(conditional_stop_orders),
                                "order_status": _order_text_field(order, "status") or "",
                                "order_source": order_source,
                            },
                        )
            if order is None:
                ordinary_open_orders = self.exchange.fetch_open_orders(symbol)
                order = next(
                    (row for row in ordinary_open_orders if isinstance(row, dict) and _order_matches_order_id(row, order_id)),
                    None,
                )
                if order is not None:
                    order_source = "legacy_open_orders_order_id"
                else:
                    order = next(
                        (
                            row
                            for row in ordinary_open_orders
                            if isinstance(row, dict) and _order_matches_client_order_id(row, expected_client_order_id)
                        ),
                        None,
                    )
                    if order is not None:
                        order_source = "legacy_open_orders_client_order_id"
                if order is None:
                    try:
                        fetched_order = self.exchange.fetch_order_by_client_order_id(symbol, expected_client_order_id)
                    except Exception as exc:
                        if not client_lookup_error:
                            client_lookup_error = f"{type(exc).__name__}: {exc}"
                    else:
                        if _order_matches_order_id(fetched_order, order_id) or _order_matches_client_order_id(
                            fetched_order,
                            expected_client_order_id,
                        ):
                            order = fetched_order
                            order_source = "legacy_client_order_id_lookup"
            if order is not None:
                if attempt > 1:
                    self.artifacts.append_event(
                        "position_stop_order_visibility_delayed",
                        symbol,
                        {
                            "position_id": position_id,
                            "order_id": order_id,
                            "client_order_id": expected_client_order_id,
                            "reason": reason,
                            "verification_attempt": attempt,
                            "ordinary_open_orders_seen": len(ordinary_open_orders),
                            "conditional_stop_orders_seen": len(conditional_stop_orders),
                            "order_source": order_source,
                        },
                    )
                break
            if attempt < verification_attempts:
                self.artifacts.append_event(
                    "position_stop_order_visibility_retry",
                    symbol,
                    {
                        "position_id": position_id,
                        "order_id": order_id,
                        "client_order_id": expected_client_order_id,
                        "reason": reason,
                        "verification_attempt": attempt,
                        "ordinary_open_orders_seen": len(ordinary_open_orders),
                        "conditional_stop_orders_seen": len(conditional_stop_orders),
                        "client_lookup_error": client_lookup_error,
                    },
                )
                time.sleep(0.5)
        if order is None:
            raise LiveDataIntegrityError(
                f"stop order not visible in conditional/open orders after {verification_attempts} checks: "
                f"symbol={symbol} position_id={position_id} order_id={order_id} "
                f"client_order_id={expected_client_order_id} reason={reason} "
                f"ordinary_open_orders_seen={len(ordinary_open_orders)} "
                f"conditional_stop_orders_seen={len(conditional_stop_orders)} "
                f"client_lookup_error={client_lookup_error or 'none'}",
                symbol=symbol,
            )
        terminal_status = _order_terminal_status(order)
        if terminal_status is not None:
            raise LiveDataIntegrityError(
                f"stop order is terminal after verification: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"client_order_id={expected_client_order_id} status={terminal_status!r} source={order_source}",
                symbol=symbol,
            )
        side = _order_text_field(order, "side")
        if side is None or side.lower() != expected_side.lower():
            raise LiveDataIntegrityError(
                f"stop order side not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"side={side!r} expected={expected_side} source={order_source}",
                symbol=symbol,
            )
        order_type = _order_text_field(order, "type")
        if order_type is None or "stop" not in order_type.lower():
            raise LiveDataIntegrityError(
                f"stop order type not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"type={order_type!r} source={order_source}",
                symbol=symbol,
            )
        reduce_only = _order_bool_field(order, "reduceOnly")
        if reduce_only is not True:
            raise LiveDataIntegrityError(
                f"stop order reduceOnly not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"reduceOnly={reduce_only!r} source={order_source}",
                symbol=symbol,
            )
        amount = _order_float_field(order, "amount", "origQty")
        amount_delta = abs(amount - expected_amount) if amount is not None else float("nan")
        amount_delta_ratio = _safe_divide(amount_delta, expected_amount) if amount is not None else float("nan")
        if amount is None or not math.isfinite(amount_delta_ratio) or amount_delta_ratio > self.config.max_position_amount_slippage_ratio:
            raise LiveDataIntegrityError(
                f"stop order amount not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"amount={amount!r} expected={expected_amount} source={order_source}",
                symbol=symbol,
            )
        stop_price = _order_float_field(order, "stopPrice")
        if stop_price is None or not _price_matches_exchange_precision(stop_price, expected_stop_price):
            price_delta = abs(stop_price - expected_stop_price) if stop_price is not None else float("nan")
            price_tolerance = _exchange_price_precision_tolerance(stop_price) if stop_price is not None else float("nan")
            raise LiveDataIntegrityError(
                f"stop order stopPrice not verified: symbol={symbol} position_id={position_id} order_id={order_id} "
                f"stopPrice={stop_price!r} expected={expected_stop_price} delta={price_delta} "
                f"exchange_precision_tolerance={price_tolerance} source={order_source}",
                symbol=symbol,
            )

    def _close_unprotected_entry_exposure(
        self,
        symbol: str,
        *,
        amount: float,
        position_id: str,
        reason: str,
    ) -> None:
        try:
            actual_amount = abs(float(self.exchange.fetch_symbol_position_amount(symbol)))
        except Exception as exc:
            self.artifacts.append_event(
                "unprotected_entry_position_read_failed",
                symbol,
                {
                    "position_id": position_id,
                    "amount_requested": amount,
                    "reason": reason,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise LiveDataIntegrityError(
                f"unprotected entry exposure amount could not be read: position_id={position_id} symbol={symbol} reason={reason}"
            ) from exc
        if not math.isfinite(actual_amount):
            self.artifacts.append_event(
                "unprotected_entry_position_amount_invalid",
                symbol,
                {"position_id": position_id, "amount_requested": amount, "actual_amount": actual_amount, "reason": reason},
            )
            raise LiveDataIntegrityError(
                f"unprotected entry exposure amount is invalid: position_id={position_id} symbol={symbol} reason={reason}"
            )
        if actual_amount <= 0.0:
            self.artifacts.append_event(
                "unprotected_entry_no_exchange_exposure",
                symbol,
                {"position_id": position_id, "amount_requested": amount, "reason": reason},
            )
            return
        close_amount = min(float(amount), actual_amount) if math.isfinite(amount) and amount > 0.0 else actual_amount
        try:
            client_order_id = _live_client_order_id("protect", symbol, position_id, reason, int(time.time() * 1000))
            fill = self.exchange.create_market_order_with_fill(
                symbol,
                "sell",
                close_amount,
                reduce_only=True,
                client_order_id=client_order_id,
            )
            self._track_order_reconcile_symbol(symbol, reason="unprotected_entry_reduce_only_exit")
        except Exception as exc:
            self.artifacts.append_event(
                "unprotected_entry_reduce_only_exit_failed",
                symbol,
                {
                    "position_id": position_id,
                    "amount_requested": amount,
                    "actual_amount": actual_amount,
                    "close_amount": close_amount,
                    "reason": reason,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise LiveDataIntegrityError(
                f"unprotected entry exposure could not be closed: position_id={position_id} symbol={symbol} reason={reason}"
            ) from exc
        self.artifacts.append_event(
            "unprotected_entry_reduce_only_exit_filled",
            symbol,
            {
                "position_id": position_id,
                "amount_requested": amount,
                "actual_amount": actual_amount,
                "close_amount": close_amount,
                "client_order_id": client_order_id,
                "order_id": fill.order_id,
                "status": fill.status,
                "fill_timestamp_ms": fill.timestamp_ms,
                "fill_price": fill.average_price,
                "filled_amount": fill.filled_amount,
                "cost": fill.cost,
                "fee_cost": fill.fee_cost,
                "reason": reason,
            },
        )

    def _replace_position_stop_order(
        self,
        position: LivePosition,
        *,
        amount: float,
        stop_price: float,
        reason: str,
    ) -> None:
        old_stop_order_id = str(position.stop_order_id or "").strip()
        if not math.isfinite(amount) or amount <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop replacement amount: position_id={position.position_id} amount={amount}")
        if not math.isfinite(stop_price) or stop_price <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop replacement price: position_id={position.position_id} stop_price={stop_price}")
        new_stop_order_id = self._create_verified_position_stop_order(
            position.signal.symbol,
            amount=amount,
            stop_price=stop_price,
            position_id=position.position_id,
            reason=reason,
        )
        cancel_error: str | None = None
        if old_stop_order_id:
            try:
                self.exchange.cancel_stop_order(position.signal.symbol, old_stop_order_id)
            except Exception as exc:
                cancel_error = f"{type(exc).__name__}: {exc}"
        open_stop_orders = self.exchange.fetch_open_stop_orders(position.signal.symbol)
        open_order_ids = {str(order.get("id") or "").strip() for order in open_stop_orders if isinstance(order, dict)}
        if cancel_error is not None and old_stop_order_id in open_order_ids:
            raise LiveDataIntegrityError(
                f"old stop cancel failed and old order remains open: position_id={position.position_id} "
                f"old_order_id={old_stop_order_id} error={cancel_error}"
            )
        position.stop_order_id = new_stop_order_id
        position.current_stop_price = stop_price
        self.artifacts.append_event(
            "position_stop_order_replaced",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "old_order_id": old_stop_order_id,
                "new_order_id": new_stop_order_id,
                "stop_price": stop_price,
                "amount": amount,
                "reason": reason,
                "old_cancel_error": cancel_error or "",
            },
        )

    def _cancel_position_tp1_order(self, position: LivePosition, *, reason: str) -> None:
        if position.tp1_done:
            return
        order_id = str(position.tp1_order_id or "").strip()
        if not order_id:
            return
        try:
            self.exchange.cancel_order(position.signal.symbol, order_id)
        except Exception as exc:
            self.artifacts.append_event(
                "position_tp1_limit_order_cancel_failed",
                position.signal.symbol,
                {"position_id": position.position_id, "order_id": order_id, "reason": reason, "error": f"{type(exc).__name__}: {exc}"},
            )
            return
        self.artifacts.append_event(
            "position_tp1_limit_order_cancelled",
            position.signal.symbol,
            {"position_id": position.position_id, "order_id": order_id, "reason": reason},
        )

    def _cancel_position_stop_order(self, position: LivePosition, *, reason: str) -> None:
        order_id = str(position.stop_order_id or "").strip()
        if not order_id:
            return
        try:
            self.exchange.cancel_stop_order(position.signal.symbol, order_id)
        except Exception as exc:
            self.artifacts.append_event(
                "position_stop_order_cancel_failed",
                position.signal.symbol,
                {"position_id": position.position_id, "order_id": order_id, "reason": reason, "error": f"{type(exc).__name__}: {exc}"},
            )
            return
        self.artifacts.append_event(
            "position_stop_order_cancelled",
            position.signal.symbol,
            {"position_id": position.position_id, "order_id": order_id, "reason": reason},
        )

    def _symbol_in_stop_cooldown(self, symbol: str) -> bool:
        symbol_key = _position_symbol_key(symbol)
        cutoff = time.time() - self.config.stop_cooldown_hours * 3600.0
        with self._state_lock:
            stops = [ts for ts in self._recent_stops.get(symbol_key, []) if ts >= cutoff]
            self._recent_stops[symbol_key] = stops
        return len(stops) >= self.config.stop_limit_per_symbol

    def _live_counts(self) -> tuple[int, int, int, int]:
        with self._state_lock:
            return (
                self._opened_positions_total,
                len(self._open_positions),
                self._closed_positions_total,
                self._orphan_orders_cancelled_total,
            )



def _live_config_has_subminute_entry_pairs(config: LiveAnomalyConfig) -> bool:
    return any(
        int(entry_timeframe.to_milliseconds()) < int(Timeframe.M1.to_milliseconds())
        for _, entry_timeframe in config.timeframe_pairs
    )


def _validate_live_config_values(config: LiveAnomalyConfig) -> None:
    integer_minimums = {
        "baseline_candles": (config.baseline_candles, 1),
        "confirmation_candles": (config.confirmation_candles, 1),
        "min_hold_count": (config.min_hold_count, 1),
        "max_open_positions": (config.max_open_positions, 1),
        "symbol_batch_size": (config.symbol_batch_size, 1),
        "active_symbol_ttl_ms": (config.active_symbol_ttl_ms, 1),
        "ticker_radar_watch_ttl_ms": (config.ticker_radar_watch_ttl_ms, 1),
        "ticker_radar_max_promotions_per_cycle": (config.ticker_radar_max_promotions_per_cycle, 1),
        "warm_watch_ttl_ms": (config.warm_watch_ttl_ms, 1),
        "warm_watch_min_observations_for_precise": (config.warm_watch_min_observations_for_precise, 1),
        "warm_watch_aggtrade_target_cap": (config.warm_watch_aggtrade_target_cap, 1),
        "prepump_warm_watch_min_runner_rows": (config.prepump_warm_watch_min_runner_rows, 1),
        "prepump_warm_watch_min_fader_rows": (config.prepump_warm_watch_min_fader_rows, 1),
        "prepump_warm_watch_max_features": (config.prepump_warm_watch_max_features, 1),
        "symbol_context_snapshot_symbols_per_cycle": (config.symbol_context_snapshot_symbols_per_cycle, 1),
        "symbol_context_snapshot_fresh_ms": (config.symbol_context_snapshot_fresh_ms, 1),
        "symbol_context_snapshot_max_gap_candles": (config.symbol_context_snapshot_max_gap_candles, 0),
        "latency_sla_min_due_samples": (config.latency_sla_min_due_samples, 1),
        "live_ws_ticker_stale_ms": (config.live_ws_ticker_stale_ms, 1),
        "live_ws_aggtrade_stale_ms": (config.live_ws_aggtrade_stale_ms, 1),
        "live_ws_aggtrade_buffer_minutes": (config.live_ws_aggtrade_buffer_minutes, 1),
        "signal_scan_backfill_candles": (config.signal_scan_backfill_candles, 1),
        "max_signal_age_ms": (config.max_signal_age_ms, 1),
        "stop_limit_per_symbol": (config.stop_limit_per_symbol, 1),
        "oi_fresh_ms": (config.oi_fresh_ms, 1),
        "trail_lookback_candles": (config.trail_lookback_candles, 1),
        "order_reconcile_interval_cycles": (config.order_reconcile_interval_cycles, 1),
        "order_reconcile_batch_size": (config.order_reconcile_batch_size, 1),
        "max_monitor_empty_ohlcv_cycles": (config.max_monitor_empty_ohlcv_cycles, 1),
        "live_ohlcv_cache_max_buffer_rows": (config.live_ohlcv_cache_max_buffer_rows, 1),
        "live_aggtrade_rest_cache_ttl_ms": (config.live_aggtrade_rest_cache_ttl_ms, 1),
        "danger_ticker_flow_radar_min_trade_count_delta": (config.danger_ticker_flow_radar_min_trade_count_delta, 1),
        "delayed_replay_max_cases_per_cycle": (config.delayed_replay_max_cases_per_cycle, 1),
        "delayed_replay_max_queue_size": (config.delayed_replay_max_queue_size, 1),
    }
    for name, (value, minimum) in integer_minimums.items():
        if not isinstance(value, int) or value < minimum:
            raise LiveStartupError(f"Некорректный live config: {name} должен быть целым >= {minimum}, получено {value!r}")
    if not isinstance(config.ticker_radar_watch_batch_size, int) or config.ticker_radar_watch_batch_size < 0:
        raise LiveStartupError(
            "Некорректный live config: ticker_radar_watch_batch_size должен быть целым >= 0, "
            f"получено {config.ticker_radar_watch_batch_size!r}"
        )
    if config.max_precise_scan_symbols_per_cycle is not None and (
        not isinstance(config.max_precise_scan_symbols_per_cycle, int)
        or config.max_precise_scan_symbols_per_cycle < 1
    ):
        raise LiveStartupError(
            "Некорректный live config: max_precise_scan_symbols_per_cycle должен быть целым >= 1 или None, "
            f"получено {config.max_precise_scan_symbols_per_cycle!r}"
        )
    if (
        not isinstance(config.live_ws_aggtrade_max_backfill_ms, int)
        or config.live_ws_aggtrade_max_backfill_ms < 0
    ):
        raise LiveStartupError(
            "Некорректный live config: live_ws_aggtrade_max_backfill_ms должен быть целым >= 0, "
            f"получено {config.live_ws_aggtrade_max_backfill_ms!r}"
        )
    if (
        not isinstance(config.live_aggtrade_rest_cache_padding_ms, int)
        or config.live_aggtrade_rest_cache_padding_ms < 0
    ):
        raise LiveStartupError(
            "Некорректный live config: live_aggtrade_rest_cache_padding_ms должен быть целым >= 0, "
            f"получено {config.live_aggtrade_rest_cache_padding_ms!r}"
        )
    if config.live_ohlcv_cache_flush_max_symbol_timeframes is not None and (
        not isinstance(config.live_ohlcv_cache_flush_max_symbol_timeframes, int)
        or config.live_ohlcv_cache_flush_max_symbol_timeframes < 1
    ):
        raise LiveStartupError(
            "Некорректный live config: live_ohlcv_cache_flush_max_symbol_timeframes должен быть целым >= 1 или None, "
            f"получено {config.live_ohlcv_cache_flush_max_symbol_timeframes!r}"
        )
    if _live_config_has_subminute_entry_pairs(config):
        if not config.ticker_radar_enabled:
            raise LiveStartupError(
                "Некорректный live config: subminute entry TF требует ticker_radar_enabled=true. "
                "Live uses ticker-radar/active symbols plus explicitly labeled DANGER cold coverage; "
                "there is no silent fallback to full inactive aggTrades scans."
            )
        if config.ticker_radar_watch_batch_size < 1:
            raise LiveStartupError(
                "Некорректный live config: subminute entry TF требует ticker_radar_watch_batch_size >= 1, "
                "иначе ticker-radar symbols не смогут попасть в precise scan; cold coverage remains bounded and DANGER-labeled."
            )

    if config.inactive_scan_slots_per_cycle is not None and (
        not isinstance(config.inactive_scan_slots_per_cycle, int) or config.inactive_scan_slots_per_cycle < 0
    ):
        raise LiveStartupError(
            "invalid live config: inactive_scan_slots_per_cycle must be an integer >= 0 or None, "
            f"got {config.inactive_scan_slots_per_cycle!r}"
        )

    required_positive = {
        "min_quote_ratio_start": config.min_quote_ratio_start,
        "min_trade_ratio_start": config.min_trade_ratio_start,
        "max_initial_risk_pct": config.max_initial_risk_pct,
        "position_notional_usdt": config.position_notional_usdt,
        "network_sleep_seconds": config.network_sleep_seconds,
        "min_executable_rr_to_signal_tp1": config.min_executable_rr_to_signal_tp1,
        "max_position_amount_slippage_ratio": config.max_position_amount_slippage_ratio,
        "ticker_radar_interval_seconds": config.ticker_radar_interval_seconds,
        "live_ws_ticker_startup_wait_seconds": config.live_ws_ticker_startup_wait_seconds,
        "ticker_radar_min_quote_volume_delta_ratio": config.ticker_radar_min_quote_volume_delta_ratio,
        "danger_ticker_flow_radar_min_quote_volume_delta_usdt": config.danger_ticker_flow_radar_min_quote_volume_delta_usdt,
        "danger_ticker_flow_radar_min_trade_count_delta_ratio": config.danger_ticker_flow_radar_min_trade_count_delta_ratio,
        "symbol_context_snapshot_interval_seconds": config.symbol_context_snapshot_interval_seconds,
        "symbol_context_snapshot_max_cycle_seconds": config.symbol_context_snapshot_max_cycle_seconds,
        "latency_sla_due_scan_p95_seconds": config.latency_sla_due_scan_p95_seconds,
        "prepump_warm_watch_min_abs_standardized_diff": config.prepump_warm_watch_min_abs_standardized_diff,
        "delayed_replay_delay_seconds": config.delayed_replay_delay_seconds,
        "delayed_replay_min_idle_seconds": config.delayed_replay_min_idle_seconds,
        "delayed_replay_max_cycle_seconds": config.delayed_replay_max_cycle_seconds,
        "delayed_replay_outcome_lookahead_seconds": config.delayed_replay_outcome_lookahead_seconds,
    }
    for name, value in required_positive.items():
        _require_finite_config_number(name, value, min_value=0.0, allow_equal_min=False)

    required_non_negative = {
        "min_price_retention": config.min_price_retention,
        "min_verticality_score": config.min_verticality_score,
        "stop_buffer_range_fraction": config.stop_buffer_range_fraction,
        "scan_sleep_seconds": config.scan_sleep_seconds,
        "stop_cooldown_hours": config.stop_cooldown_hours,
        "telegram_cooldown_seconds": config.telegram_cooldown_seconds,
        "trail_buffer_r": config.trail_buffer_r,
        "max_entry_price_drift_pct": config.max_entry_price_drift_pct,
        "ticker_radar_min_price_delta_pct": config.ticker_radar_min_price_delta_pct,
        "ticker_radar_min_quote_volume_delta_usdt": config.ticker_radar_min_quote_volume_delta_usdt,
        "live_ohlcv_cache_flush_interval_seconds": config.live_ohlcv_cache_flush_interval_seconds,
        "danger_ticker_flow_radar_max_price_delta_pct": config.danger_ticker_flow_radar_max_price_delta_pct,
        "warm_watch_max_price_delta_pct": config.warm_watch_max_price_delta_pct,
        "prepump_warm_watch_score_weight": config.prepump_warm_watch_score_weight,
        "prepump_warm_watch_min_coverage_ratio": config.prepump_warm_watch_min_coverage_ratio,
    }
    for name, value in required_non_negative.items():
        _require_finite_config_number(name, value, min_value=0.0, allow_equal_min=True)

    _require_finite_config_number(
        "symbol_context_snapshot_min_coverage_ratio",
        config.symbol_context_snapshot_min_coverage_ratio,
        min_value=0.0,
        allow_equal_min=False,
    )
    if float(config.symbol_context_snapshot_min_coverage_ratio) > 1.0:
        raise LiveStartupError(
            "Некорректный live config: symbol_context_snapshot_min_coverage_ratio must be <= 1.0"
        )

    optional_positive = {
        "max_start_quote_ratio": config.max_start_quote_ratio,
        "max_start_trade_ratio": config.max_start_trade_ratio,
        "max_start_avg_trade_quote_size_ratio": config.max_start_avg_trade_quote_size_ratio,
        "max_start_quote_ratio_per_abs_return": config.max_start_quote_ratio_per_abs_return,
        "max_start_range_pct_ratio_to_baseline": config.max_start_range_pct_ratio_to_baseline,
        "min_next_taker_buy_quote_share": config.min_next_taker_buy_quote_share,
        "max_price_retention": config.max_price_retention,
        "min_oi_change_pct_3x5m": config.min_oi_change_pct_3x5m,
        "max_prior_up_down_whipsaw_to_impulse_range": config.max_prior_up_down_whipsaw_to_impulse_range,
    }
    for name, value in optional_positive.items():
        if value is not None:
            _require_finite_config_number(name, value, min_value=0.0, allow_equal_min=False)

    _require_finite_config_number(
        "danger_ticker_flow_radar_min_price_delta_pct",
        config.danger_ticker_flow_radar_min_price_delta_pct,
        min_value=-1.0,
        allow_equal_min=True,
    )
    _require_finite_config_number(
        "warm_watch_min_price_delta_pct",
        config.warm_watch_min_price_delta_pct,
        min_value=-1.0,
        allow_equal_min=True,
    )
    if config.warm_watch_enabled and config.warm_watch_max_price_delta_pct <= config.warm_watch_min_price_delta_pct:
        raise LiveStartupError(
            "Некорректный live config: warm_watch_max_price_delta_pct must be greater than "
            "warm_watch_min_price_delta_pct"
        )
    if config.prepump_warm_watch_scoring_enabled:
        try:
            parse_prepump_windows(str(config.prepump_warm_watch_windows))
        except ValueError as exc:
            raise LiveStartupError(
                f"Некорректный live config: prepump_warm_watch_windows: {exc}"
            ) from exc
        if float(config.prepump_warm_watch_min_coverage_ratio) > 1.0:
            raise LiveStartupError(
                "Некорректный live config: prepump_warm_watch_min_coverage_ratio must be <= 1.0"
            )
    if config.danger_ticker_flow_radar_enabled and (
        config.danger_ticker_flow_radar_max_price_delta_pct
        <= config.danger_ticker_flow_radar_min_price_delta_pct
    ):
        raise LiveStartupError(
            "Некорректный live config: danger_ticker_flow_radar_max_price_delta_pct must be greater than "
            "danger_ticker_flow_radar_min_price_delta_pct"
        )
    if config.max_cycles is not None and (not isinstance(config.max_cycles, int) or config.max_cycles < 0):
        raise LiveStartupError(f"Некорректный live config: max_cycles должен быть целым >= 0, получено {config.max_cycles!r}")


def _require_finite_config_number(name: str, value: float, *, min_value: float, allow_equal_min: bool) -> None:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LiveStartupError(f"Некорректный live config: {name} должен быть числом, получено {value!r}") from exc
    if not math.isfinite(number):
        raise LiveStartupError(f"Некорректный live config: {name} должен быть finite, получено {value!r}")
    if allow_equal_min:
        valid = number >= min_value
        relation = ">="
    else:
        valid = number > min_value
        relation = ">"
    if not valid:
        raise LiveStartupError(f"Некорректный live config: {name} должен быть {relation} {min_value}, получено {number!r}")


def build_telegram_config_from_env() -> TelegramConfig:
    values = {
        "TELEGRAM_EVENTS_BOT_TOKEN": os.getenv("TELEGRAM_EVENTS_BOT_TOKEN", "").strip(),
        "TELEGRAM_EVENTS_CHAT_ID": os.getenv("TELEGRAM_EVENTS_CHAT_ID", "").strip(),
        "TELEGRAM_POSITIONS_BOT_TOKEN": os.getenv("TELEGRAM_POSITIONS_BOT_TOKEN", "").strip(),
        "TELEGRAM_POSITIONS_CHAT_ID": os.getenv("TELEGRAM_POSITIONS_CHAT_ID", "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise LiveStartupError(f"Не заполнены Telegram env переменные: {', '.join(missing)}")
    return TelegramConfig(
        events_bot_token=values["TELEGRAM_EVENTS_BOT_TOKEN"],
        events_chat_id=values["TELEGRAM_EVENTS_CHAT_ID"],
        positions_bot_token=values["TELEGRAM_POSITIONS_BOT_TOKEN"],
        positions_chat_id=values["TELEGRAM_POSITIONS_CHAT_ID"],
    )


def _resolve_live_pump_categories(category_ids: tuple[str, ...]) -> tuple[LivePumpCategory, ...]:
    resolved: list[LivePumpCategory] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw_category_id in category_ids:
        category_id = raw_category_id.strip()
        if not category_id or category_id in seen:
            continue
        category = SUPPORTED_LIVE_PUMP_CATEGORIES.get(category_id)
        if category is None:
            unknown.append(category_id)
            continue
        resolved.append(category)
        seen.add(category_id)
    if unknown:
        raise LiveStartupError(
            "Неизвестные live pump categories: "
            f"{', '.join(sorted(unknown))}. Доступны: {', '.join(sorted(SUPPORTED_LIVE_PUMP_CATEGORIES))}"
        )
    return tuple(sorted(resolved, key=lambda item: item.priority))


def _category_value(category: LivePumpCategory, config: LiveAnomalyConfig, field_name: str) -> float | None:
    value = getattr(category, field_name)
    if value is not None:
        return value
    return getattr(config, field_name, None)


def _latest_closed_candle_start_ms(timeframe: Timeframe, *, now_ms: int) -> int:
    timeframe_ms = int(timeframe.to_milliseconds())
    if timeframe_ms <= 0:
        raise ValueError(f"invalid timeframe milliseconds: {timeframe.value}")
    return ((int(now_ms) - timeframe_ms) // timeframe_ms) * timeframe_ms


def _next_candle_start_ms(timestamp_ms: int, timeframe: Timeframe) -> int:
    timeframe_ms = int(timeframe.to_milliseconds())
    if timeframe_ms <= 0:
        raise ValueError(f"invalid timeframe milliseconds: {timeframe.value}")
    return ((int(timestamp_ms) + timeframe_ms - 1) // timeframe_ms) * timeframe_ms


def _empty_cached_ohlcv_frame() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype=dtype) for column, dtype in CACHED_OHLCV_DTYPES.items()})


def _prepare_cached_ohlcv_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return _empty_cached_ohlcv_frame()
    prepared = frame.copy()
    for column in prepared.columns:
        if column == "timestamp" or column in REQUIRED_PRICE_COLUMNS or column in REQUIRED_FLOW_COLUMNS or column in OPTIONAL_FLOW_COLUMNS:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.loc[prepared["timestamp"].notna()].copy()
    if prepared.empty:
        return _empty_cached_ohlcv_frame()
    prepared["timestamp"] = prepared["timestamp"].astype("int64")
    return prepared.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _concat_cached_ohlcv_frames(frames: list[pd.DataFrame | None]) -> pd.DataFrame:
    prepared_frames: list[pd.DataFrame] = []
    for frame in frames:
        prepared = _prepare_cached_ohlcv_frame(frame)
        if not prepared.empty:
            prepared_frames.append(prepared)
    if not prepared_frames:
        return _empty_cached_ohlcv_frame()
    if len(prepared_frames) == 1:
        return prepared_frames[0].copy()
    return _prepare_cached_ohlcv_frame(pd.concat(prepared_frames, ignore_index=True))


def _cached_frame_covers_window(frame: pd.DataFrame, *, start_timestamp_ms: int, end_timestamp_ms: int) -> bool:
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return False
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna()
    if timestamps.empty:
        return False
    return int(timestamps.min()) <= int(start_timestamp_ms) and int(timestamps.max()) >= int(end_timestamp_ms)


def _missing_ohlcv_ranges(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int,
) -> list[tuple[int, int]]:
    if timeframe_ms <= 0 or int(end_timestamp_ms) < int(start_timestamp_ms):
        return []
    expected = range(int(start_timestamp_ms), int(end_timestamp_ms) + 1, int(timeframe_ms))
    present = set()
    if frame is not None and not frame.empty and "timestamp" in frame.columns:
        present = set(pd.to_numeric(frame["timestamp"], errors="coerce").dropna().astype("int64").tolist())
    ranges: list[tuple[int, int]] = []
    range_start: int | None = None
    previous_missing: int | None = None
    for timestamp_ms in expected:
        if int(timestamp_ms) in present:
            if range_start is not None and previous_missing is not None:
                ranges.append((range_start, previous_missing))
                range_start = None
                previous_missing = None
            continue
        if range_start is None:
            range_start = int(timestamp_ms)
        previous_missing = int(timestamp_ms)
    if range_start is not None and previous_missing is not None:
        ranges.append((range_start, previous_missing))
    return ranges


def _decision_freshness_details(
    *,
    decision_timestamp_ms: int,
    now_ms: int,
    max_signal_age_ms: int,
    levels_timeframe: Timeframe | None = None,
    signal_timeframe: Timeframe | None = None,
) -> dict[str, object]:
    resolved_timeframe = signal_timeframe or levels_timeframe
    if resolved_timeframe is None:
        raise ValueError("decision freshness requires a timeframe")
    signal_timeframe_ms = int(resolved_timeframe.to_milliseconds())
    decision_available_ms = int(decision_timestamp_ms) + signal_timeframe_ms
    signal_age_ms = int(now_ms) - decision_available_ms
    return {
        "decision_timestamp_ms": int(decision_timestamp_ms),
        "decision_available_ms": decision_available_ms,
        "now_ms": int(now_ms),
        "signal_age_ms": signal_age_ms,
        "max_signal_age_ms": int(max_signal_age_ms),
    }


def _entry_lag_details(signal: LiveSignal, *, observed_timestamp_ms: int) -> dict[str, object]:
    entry_timeframe_ms = int(signal.entry_timeframe.to_milliseconds())
    first_executable_ms = int(signal.decision_timestamp_ms) + entry_timeframe_ms
    lag_ms = int(observed_timestamp_ms) - first_executable_ms
    lag_candles = int(math.floor(lag_ms / entry_timeframe_ms)) if entry_timeframe_ms > 0 else 0
    return {
        "first_executable_entry_timestamp_ms": first_executable_ms,
        "observed_entry_check_timestamp_ms": int(observed_timestamp_ms),
        "entry_lag_ms": lag_ms,
        "entry_lag_ltf_candles": lag_candles,
        "entered_late_vs_first_executable": bool(lag_ms >= entry_timeframe_ms),
    }


def _live_scan_gap_details(
    *,
    decision_timestamp_ms: int,
    previous_scan_closed_timestamp_ms: int | None,
    entry_timeframe: Timeframe,
) -> dict[str, object]:
    entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
    if previous_scan_closed_timestamp_ms is None or entry_timeframe_ms <= 0:
        return {
            "previous_live_scan_closed_timestamp_ms": None,
            "first_unscanned_decision_timestamp_ms": None,
            "live_scan_gap_ltf_candles": 0,
        }
    previous_ts = int(previous_scan_closed_timestamp_ms)
    decision_ts = int(decision_timestamp_ms)
    skipped = max(0, int((decision_ts - previous_ts) // entry_timeframe_ms) - 1)
    return {
        "previous_live_scan_closed_timestamp_ms": previous_ts,
        "first_unscanned_decision_timestamp_ms": previous_ts + entry_timeframe_ms if skipped > 0 else decision_ts,
        "live_scan_gap_ltf_candles": skipped,
    }


BLOCKED_ORDER_REASON_LABELS = {
    "reject_stale_signal": "сигнал устарел",
    "reject_invalid_live_price": "live-price невалидный",
    "reject_tp1_already_reached": "TP1 уже достигнут",
    "reject_invalid_actual_risk_at_live_price": "риск от live-price невалидный",
    "reject_actual_risk_too_wide_at_live_price": "риск от live-price слишком широкий",
    "reject_entry_price_drift": "live-price слишком далеко от цены сигнала",
    "reject_rr_collapsed": "RR до TP1 развалился",
}


def _format_order_blocked_message(signal: LiveSignal, *, event: str, details: dict[str, object]) -> str:
    reason = BLOCKED_ORDER_REASON_LABELS.get(event, event)
    if bool(details.get("discrete_snapshot_would_enter")):
        live_price = _finite_or_none(details.get("live_price"))
        live_line = f"Текущая цена: {_telegram_code(_format_price(live_price))}\n" if live_price is not None else ""
        return (
            f"{_symbol_emoji(signal.symbol)} "
            f"<b>{_telegram_symbol_link(signal.symbol)} вход пропущен</b>\n\n"
            f"Свечной сигнал был, но сейчас вход уже не исполним.\n"
            f"Причина: {_telegram_code(reason)}\n"
            f"{live_line}\n"
            f"{_telegram_signal_context(signal)}"
        )
    return (
        f"{_symbol_emoji(signal.symbol)} "
        f"<b>{_telegram_symbol_link(signal.symbol)} Позиция не открыта</b>\n\n"
        f"{_telegram_escape(reason)}\n\n"
        f"{_telegram_signal_context(signal)}"
    )


def _format_delayed_replay_ignored_entry_message(
    signal: LiveSignal,
    *,
    live_reason: str,
    mismatch_type: str,
    recompute_status: str,
    recompute_source: str,
    outcome: dict[str, object],
) -> str:
    tp_pct = _safe_divide(float(signal.tp1_price) - float(signal.entry_price), float(signal.entry_price))
    sl_pct = _safe_divide(float(signal.entry_price) - float(signal.stop_price), float(signal.entry_price))
    first_hit = str(outcome.get("first_hit") or "")
    outcome_line = ""
    if first_hit:
        outcome_line = f"\nПосле сигнала: {_telegram_code(first_hit)}\n"
    snapshot_source = recompute_source == "frozen_live_signal_snapshot"
    title = "Replay поднял frozen-сигнал" if snapshot_source else "Replay нашёл вход"
    source_line = f"Source: {_telegram_code(recompute_source or 'unknown')}\n"
    caveat_line = ""
    if snapshot_source:
        caveat_line = "Строгий пересчёт свечей не подтвердил вход: использован live snapshot, потому что replay не ходит за mark/OI.\n"
    return (
        f"{_symbol_emoji(signal.symbol)} "
        f"<b>{_telegram_symbol_link(signal.symbol)} {title}</b>\n\n"
        f"Live: {_telegram_code(live_reason or 'unknown')}\n"
        f"Replay: {_telegram_code(mismatch_type)}\n"
        f"Status: {_telegram_code(recompute_status)}\n"
        f"{source_line}"
        f"{caveat_line}\n"
        f"Вход: {_format_price(signal.entry_price)}\n"
        f"TP1: {_format_price(signal.tp1_price)} {_format_percent(tp_pct)}\n"
        f"SL: {_format_price(signal.stop_price)} {_format_percent(sl_pct)}\n"
        f"{outcome_line}\n"
        f"{_telegram_signal_context(signal)}"
    )


def _format_position_integrity_error_message(position: LivePosition, *, reason: str) -> str:
    return (
        f"{_symbol_emoji(position.signal.symbol)} "
        f"<b>{_telegram_symbol_link(position.signal.symbol)} Ошибка ведения позиции</b>\n\n"
        f"{_telegram_code(reason)}\n\n"
        f"ID: {_telegram_code(position.position_id)}"
    )


def _format_live_data_integrity_halt_message(*, symbol: str | None, reason: str) -> str:
    if symbol:
        title = f"{_symbol_emoji(symbol)} <b>{_telegram_symbol_link(symbol)} Live остановлен</b>"
    else:
        title = f"{SERVICE_WARNING_EMOJI} <b>Live остановлен</b>"
    return f"{title}\n\n{_telegram_code(reason[:600])}"


def _format_live_order_position_integrity_message(
    *,
    symbol: str | None,
    reason: str,
    continue_enabled: bool,
) -> str:
    status = "Live продолжает работу" if continue_enabled else "Live остановлен"
    if symbol:
        title = f"{_symbol_emoji(symbol)} <b>{_telegram_symbol_link(symbol)} {status}</b>"
    else:
        title = f"{SERVICE_WARNING_EMOJI} <b>{status}</b>"
    mode_line = "Режим: DANGER continue-after-order-position-errors" if continue_enabled else "Режим: strict"
    return f"{title}\n\n<b>Проблема ордера/позиции</b>\n{_telegram_code(reason[:600])}\n\n{mode_line}"


def _order_info(order: dict[str, object]) -> dict[str, object]:
    info = order.get("info")
    return info if isinstance(info, dict) else {}


def _order_text_field(order: dict[str, object], key: str) -> str | None:
    value = order.get(key)
    if value is None or value == "":
        value = _order_info(order).get(key)
    if value is None or value == "":
        return None
    return str(value)


def _order_matches_order_id(order: dict[str, object], order_id: str) -> bool:
    return (_resolve_order_id(order) or "") == str(order_id).strip()


def _order_matches_client_order_id(order: dict[str, object], client_order_id: str) -> bool:
    expected = str(client_order_id).strip()
    if not expected:
        return False
    info = _order_info(order)
    for key in ("clientOrderId", "client_order_id", "origClientOrderId", "newClientOrderId", "clientAlgoId"):
        for source in (order, info):
            value = source.get(key)
            if value is not None and str(value).strip() == expected:
                return True
    return False


def _order_terminal_status(order: dict[str, object]) -> str | None:
    status = _order_text_field(order, "status")
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized in {"closed", "canceled", "cancelled", "expired", "rejected", "triggered", "finished"}:
        return status
    return None


def _order_float_field(order: dict[str, object], *keys: str) -> float | None:
    info = _order_info(order)
    for key in keys:
        for source in (order, info):
            value = source.get(key)
            if value is None or value == "":
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed):
                return parsed
    return None



def _exchange_price_precision_tolerance(value: float) -> float:
    """Return one displayed price unit for an exchange-normalized price."""
    try:
        decimal_value = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return float("nan")
    exponent = decimal_value.as_tuple().exponent
    if exponent >= 0:
        return 1.0
    return float(Decimal(1).scaleb(exponent))


def _price_matches_exchange_precision(actual: float, expected: float, *, max_relative_ratio: float = 1e-4) -> bool:
    if not math.isfinite(actual) or not math.isfinite(expected) or expected <= 0.0:
        return False
    delta = abs(actual - expected)
    relative_ratio = _safe_divide(delta, expected)
    if math.isfinite(relative_ratio) and relative_ratio <= max_relative_ratio:
        return True
    precision_tolerance = _exchange_price_precision_tolerance(actual)
    return math.isfinite(precision_tolerance) and delta <= precision_tolerance + 1e-12

def _order_bool_field(order: dict[str, object], key: str) -> bool | None:
    value = order.get(key)
    if value is None or value == "":
        value = _order_info(order).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    return None


def _format_open_message(position: LivePosition) -> str:
    signal = position.signal
    entry_price = float(position.entry_price)
    signal_entry_price = float(signal.entry_price)
    entry_drift_pct = _safe_divide(entry_price - signal_entry_price, signal_entry_price)
    tp_pct = _safe_divide(float(position.tp1_price) - entry_price, entry_price)
    sl_pct = _safe_divide(entry_price - float(position.stop_price), entry_price)
    sl_reference_text = _format_price(position.stop_price)
    price_decimals = _price_decimal_places(sl_reference_text)
    tp_text = _format_price_fixed_decimals(position.tp1_price, price_decimals)
    sl_text = _format_price_fixed_decimals(position.stop_price, price_decimals)
    compact_symbol = _telegram_escape(_compact_symbol(signal.symbol))
    coinglass_url = _telegram_escape(_coinglass_url(signal.symbol))
    return (
        f"{_symbol_emoji(signal.symbol)} <b>{compact_symbol}</b> ({coinglass_url}) <b>LONG</b>\n\n"
        f"Сигнал: {_format_price(signal.entry_price)}\n\n"
        f"Вход: {_format_percent(entry_drift_pct, signed=True)}\n\n"
        f"TP: {tp_text} {_format_percent(tp_pct)}\n"
        f"SL: {sl_text} {_format_percent(sl_pct)}\n\n"
        f"{_telegram_escape(signal.levels_timeframe.value)}/{_telegram_escape(signal.entry_timeframe.value)} · "
        f"{_telegram_escape(signal.category_label)} · {_telegram_escape(signal.session)}"
    )


def _format_close_message(position: LivePosition, *, pnl_usdt: float, pnl_pct: float, exit_price: float) -> str:
    exit_zone = _format_exit_zone(position, exit_price=exit_price)
    return (
        f"{_symbol_emoji(position.signal.symbol)} "
        f"<b>{_telegram_symbol_link(position.signal.symbol)} {_format_usdt(pnl_usdt)} USDT</b>\n\n"
        f"PNL {_format_percent(pnl_pct, signed=False)}\n\n"
        f"{exit_zone}"
    )


def _format_stop_move_message(position: LivePosition, *, stop_price: float, label: str) -> str:
    stop_distance_from_entry = _safe_divide(float(stop_price) - float(position.entry_price), float(position.entry_price))
    display_label = _format_stop_zone(position, stop_price=stop_price, fallback_label=label)
    return (
        f"{_symbol_emoji(position.signal.symbol)} "
        f"<b>{_telegram_symbol_link(position.signal.symbol)} {display_label} "
        f"{_format_percent(stop_distance_from_entry, signed=True, precision=2)}</b>"
    )


def _nice_market_round_step(*, reference_price: float, movement: float) -> float:
    if not math.isfinite(reference_price) or reference_price <= 0.0:
        return float("nan")
    raw_step = max(abs(float(movement)) * 0.25, abs(float(reference_price)) * 0.0002, 1e-12)
    exponent = math.floor(math.log10(raw_step))
    base = 10.0 ** exponent
    normalized = raw_step / base
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        if normalized <= multiplier:
            return multiplier * base
    return 10.0 * base


def _round_up_tp1_to_market_number(base_tp1_price: float, *, reference_price: float, movement: float) -> tuple[float, float]:
    if not math.isfinite(base_tp1_price) or base_tp1_price <= 0.0:
        return base_tp1_price, float("nan")
    step = _nice_market_round_step(reference_price=reference_price, movement=movement)
    if not math.isfinite(step) or step <= 0.0:
        return base_tp1_price, float("nan")
    rounded = math.ceil((base_tp1_price - step * 1e-9) / step) * step
    tolerance = max(abs(float(base_tp1_price)) * 1e-12, step * 1e-9)
    if rounded <= base_tp1_price + tolerance:
        rounded += step
    decimals = max(0, int(math.ceil(-math.log10(step))) + 2) if step < 1.0 else 8
    rounded = round(float(rounded), min(decimals, 12))
    return rounded, float(step)

def _format_timeframe_pairs(timeframe_pairs: tuple[tuple[Timeframe, Timeframe], ...]) -> str:
    return ", ".join(f"{levels.value}/{entry.value}" for levels, entry in timeframe_pairs)


def _position_symbol_key(symbol: str) -> str:
    return _compact_symbol(symbol)


def _live_client_order_id(prefix: str, symbol: str, *parts: object) -> str:
    compact_symbol = re.sub(r"[^A-Za-z0-9]", "", _compact_symbol(symbol).upper()) or "SYM"
    raw = "|".join([prefix, compact_symbol, *(str(part) for part in parts)])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    normalized_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", str(prefix).strip())[:8] or "live"
    return f"pa_{normalized_prefix}_{compact_symbol[:8]}_{digest}"[:36]


def _resolve_order_id(order: dict[str, object]) -> str | None:
    value = order.get("id")
    if value is None:
        info = order.get("info")
        if isinstance(info, dict):
            value = info.get("algoId") or info.get("orderId") or info.get("clientAlgoId") or info.get("clientOrderId")
    if value is None:
        return None
    order_id = str(value).strip()
    return order_id or None


def _resolve_aggtrade_timestamp(row: dict[str, object]) -> int | None:
    for column in ("transact_time", "T"):
        if column not in row:
            continue
        try:
            return int(row[column])
        except (TypeError, ValueError):
            return None
    return None


def _filter_aggtrade_rows_by_time(
    rows: tuple[dict[str, object], ...] | list[dict[str, object]],
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> list[dict[str, object]]:
    filtered: list[dict[str, object]] = []
    for row in rows:
        timestamp_ms = _resolve_aggtrade_timestamp(row)
        if timestamp_ms is None:
            continue
        if int(start_timestamp_ms) <= timestamp_ms <= int(end_timestamp_ms):
            filtered.append(dict(row))
    return filtered


def _missing_aggtrade_raw_ranges(
    cached_ranges: list[AggTradeRawRange],
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> list[tuple[int, int]]:
    request_start = int(start_timestamp_ms)
    request_end = int(end_timestamp_ms)
    if request_start > request_end:
        return []
    coverage: list[tuple[int, int]] = []
    for cached_range in cached_ranges:
        overlap_start = max(request_start, int(cached_range.start_timestamp_ms))
        overlap_end = min(request_end, int(cached_range.end_timestamp_ms))
        if overlap_start <= overlap_end:
            coverage.append((overlap_start, overlap_end))
    if not coverage:
        return [(request_start, request_end)]
    coverage.sort()
    merged: list[tuple[int, int]] = []
    for start, end in coverage:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    missing: list[tuple[int, int]] = []
    cursor = request_start
    for start, end in merged:
        if cursor < start:
            missing.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= request_end:
        missing.append((cursor, request_end))
    return missing


def _merge_time_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    normalized = sorted((int(start), int(end)) for start, end in ranges if int(start) <= int(end))
    if not normalized:
        return []
    merged = [normalized[0]]
    for start, end in normalized[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _dedupe_aggtrade_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: dict[tuple[object, int], dict[str, object]] = {}
    fallback_index = 0
    for row in rows:
        agg_id = _resolve_aggtrade_id(row)
        timestamp_ms = _resolve_aggtrade_timestamp(row)
        if timestamp_ms is None:
            fallback_index += 1
            key = (f"missing_ts:{fallback_index}", fallback_index)
        elif agg_id is None:
            fallback_index += 1
            key = (f"missing_id:{fallback_index}", timestamp_ms)
        else:
            key = (agg_id, timestamp_ms)
        deduped[key] = dict(row)
    return sorted(
        deduped.values(),
        key=lambda row: (
            _resolve_aggtrade_timestamp(row) if _resolve_aggtrade_timestamp(row) is not None else -1,
            _resolve_aggtrade_id(row) if _resolve_aggtrade_id(row) is not None else -1,
        ),
    )


def _fill_missing_ohlcv_buckets(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int,
    seed_close: float,
) -> pd.DataFrame:
    if timeframe_ms <= 0 or end_timestamp_ms < start_timestamp_ms:
        result = frame.copy()
        if "synthetic_ohlcv_bucket" not in result.columns:
            result["synthetic_ohlcv_bucket"] = False
        return result
    source = frame.copy()
    if "synthetic_ohlcv_bucket" not in source.columns:
        source["synthetic_ohlcv_bucket"] = False
    else:
        source["synthetic_ohlcv_bucket"] = source["synthetic_ohlcv_bucket"].fillna(False).astype(bool)
    full_index = pd.DataFrame(
        {"timestamp": list(range(int(start_timestamp_ms), int(end_timestamp_ms) + int(timeframe_ms), int(timeframe_ms)))}
    )
    merged = full_index.merge(source, on="timestamp", how="left")
    merged["synthetic_ohlcv_bucket"] = merged["synthetic_ohlcv_bucket"].fillna(True).astype(bool)
    merged["close"] = pd.to_numeric(merged["close"], errors="coerce").ffill().fillna(float(seed_close))
    for price_column in ("open", "high", "low"):
        merged[price_column] = pd.to_numeric(merged[price_column], errors="coerce").fillna(merged["close"])
    for volume_column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if volume_column not in merged.columns:
            merged[volume_column] = 0.0
        merged[volume_column] = pd.to_numeric(merged[volume_column], errors="coerce").fillna(0.0)
    return merged.reset_index(drop=True)


def _real_ohlcv_buckets(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "synthetic_ohlcv_bucket" not in frame.columns:
        return frame
    synthetic = frame["synthetic_ohlcv_bucket"].fillna(False).astype(bool)
    return frame.loc[~synthetic].copy()


def _count_missing_ohlcv_buckets(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int,
) -> int:
    if timeframe_ms <= 0 or end_timestamp_ms < start_timestamp_ms or "timestamp" not in frame.columns:
        return 0
    expected = set(range(int(start_timestamp_ms), int(end_timestamp_ms) + int(timeframe_ms), int(timeframe_ms)))
    if not expected:
        return 0
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna()
    if timestamps.empty:
        return len(expected)
    present = {
        int(timestamp)
        for timestamp in timestamps.astype("int64")
        if int(start_timestamp_ms) <= int(timestamp) <= int(end_timestamp_ms)
    }
    return len(expected.difference(present))


def _aggregate_frame_to_candle(frame: pd.DataFrame, *, timestamp_ms: int) -> pd.Series | None:
    if frame.empty:
        return None
    required = [column for column in REQUIRED_PRICE_COLUMNS if column in frame.columns]
    if len(required) < len(REQUIRED_PRICE_COLUMNS):
        return None
    ordered = frame.copy().sort_values("timestamp")
    row: dict[str, object] = {
        "timestamp": int(timestamp_ms),
        "open": float(ordered.iloc[0]["open"]),
        "high": float(pd.to_numeric(ordered["high"], errors="coerce").max()),
        "low": float(pd.to_numeric(ordered["low"], errors="coerce").min()),
        "close": float(ordered.iloc[-1]["close"]),
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in ordered.columns:
            row[column] = float(pd.to_numeric(ordered[column], errors="coerce").fillna(0.0).sum())
    return pd.Series(row)

def _resolve_aggtrade_id(row: dict[str, object]) -> int | None:
    for column in ("aggregate_trade_id", "a"):
        if column not in row:
            continue
        try:
            return int(row[column])
        except (TypeError, ValueError):
            return None
    return None


def _aggregate_aggtrades_to_ohlcv_frame(
    trades: pd.DataFrame,
    *,
    timeframe_ms: int,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_quote_volume",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    timestamp_column = "transact_time" if "transact_time" in trades.columns else "T"
    price_column = "price" if "price" in trades.columns else "p"
    quantity_column = "quantity" if "quantity" in trades.columns else "q"
    maker_column = "is_buyer_maker" if "is_buyer_maker" in trades.columns else "m"
    required = (timestamp_column, price_column, quantity_column, maker_column)
    missing = [column for column in required if column not in trades.columns]
    if missing:
        return pd.DataFrame(columns=columns)
    work = trades.copy()
    work["timestamp"] = pd.to_numeric(work[timestamp_column], errors="coerce")
    work["price"] = pd.to_numeric(work[price_column], errors="coerce")
    work["quantity"] = pd.to_numeric(work[quantity_column], errors="coerce")
    work = work.loc[
        work["timestamp"].notna()
        & work["price"].notna()
        & work["quantity"].notna()
        & (work["timestamp"] >= int(start_timestamp_ms))
        & (work["timestamp"] <= int(end_timestamp_ms))
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)
    work["timestamp"] = work["timestamp"].astype("int64")
    work["bucket"] = (work["timestamp"] // int(timeframe_ms)) * int(timeframe_ms)
    work["quote_volume"] = work["price"].astype("float64") * work["quantity"].astype("float64")
    buyer_is_maker = work[maker_column].astype(str).str.lower().isin(("true", "1"))
    work["taker_buy_quote_volume"] = work["quote_volume"].where(~buyer_is_maker, 0.0)
    aggregated = (
        work.groupby("bucket", sort=True)
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
            quote_volume=("quote_volume", "sum"),
            number_of_trades=("quantity", "size"),
            taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
        )
        .reset_index()
        .rename(columns={"bucket": "timestamp"})
    )
    first_bucket = int((int(start_timestamp_ms) // int(timeframe_ms)) * int(timeframe_ms))
    last_bucket = int((int(end_timestamp_ms) // int(timeframe_ms)) * int(timeframe_ms))
    full_index = pd.DataFrame({"timestamp": list(range(first_bucket, last_bucket + int(timeframe_ms), int(timeframe_ms)))})
    aggregated = full_index.merge(aggregated, on="timestamp", how="left")
    aggregated["close"] = aggregated["close"].ffill().bfill()
    for price_column in ("open", "high", "low"):
        aggregated[price_column] = aggregated[price_column].fillna(aggregated["close"])
    for volume_column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        aggregated[volume_column] = aggregated[volume_column].fillna(0.0)
    return aggregated.loc[:, columns].reset_index(drop=True)

def _telegram_signal_context(signal: LiveSignal) -> str:
    return _telegram_code(
        f"{signal.levels_timeframe.value}/{signal.entry_timeframe.value} · "
        f"{signal.category_label} · {signal.session}"
    )


def _symbol_emoji(symbol: str) -> str:
    compact = _compact_symbol(symbol).upper()
    digest = hashlib.sha256(compact.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(ANIMAL_EMOJIS)
    return ANIMAL_EMOJIS[index]


QUOTE_SYMBOL_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD")


def _compact_symbol(symbol: str) -> str:
    raw = str(symbol).upper().strip()
    compact = raw.split(":", 1)[0]
    if "/" in compact:
        compact = compact.split("/", 1)[0]
    else:
        for suffix in QUOTE_SYMBOL_SUFFIXES:
            if compact.endswith(suffix) and len(compact) > len(suffix):
                compact = compact[: -len(suffix)]
                break
    return compact or raw


def _coinglass_url(symbol: str) -> str:
    return f"https://www.coinglass.com/tv/Binance_{_compact_symbol(symbol)}USDT"


def _telegram_symbol_link(symbol: str) -> str:
    compact = _telegram_escape(_compact_symbol(symbol))
    return f'<a href="{_coinglass_url(symbol)}">{compact}</a>'


def _format_weaknesses(weaknesses: list[str]) -> str:
    if not weaknesses:
        return "нет"
    return ", ".join(_telegram_escape(str(item)) for item in weaknesses)


def _format_exit_zone(position: LivePosition, *, exit_price: float) -> str:
    if not position.tp1_done:
        return "SL"
    entry_price = float(position.entry_price)
    if math.isfinite(exit_price) and math.isfinite(entry_price):
        tolerance = max(abs(entry_price), 1.0) * 1e-4
        if abs(float(exit_price) - entry_price) <= tolerance:
            return "BE"
    return _format_stop_zone(position, stop_price=exit_price, fallback_label="SL")


def _format_stop_zone(position: LivePosition, *, stop_price: float, fallback_label: str) -> str:
    if not position.tp1_done:
        return fallback_label
    price = float(stop_price)
    tp1_price = float(position.tp1_price)
    if not math.isfinite(price) or not math.isfinite(tp1_price):
        return fallback_label
    return "TP-" if price < tp1_price else "TP+"


def _format_price(value: float) -> str:
    return f"{float(value):.6g}"


def _price_decimal_places(formatted_price: str) -> int:
    text = str(formatted_price).strip()
    if "." not in text:
        return 0
    return len(text.rsplit(".", 1)[1])


def _format_price_fixed_decimals(value: float, decimals: int) -> str:
    safe_decimals = max(0, min(int(decimals), 12))
    return f"{float(value):.{safe_decimals}f}"


def _format_live_status_value(value: object) -> str:
    text = str(value).strip() or "-"
    lowered = text.lower()
    if lowered == "ok":
        return "Ok"
    if lowered == "yes":
        return "Да"
    if lowered == "no":
        return "Нет"
    if lowered == "off":
        return "Выкл"
    if lowered.startswith("on") and lowered[2:].isdigit():
        return f"Вкл {lowered[2:]}"
    if lowered == "on":
        return "Вкл"
    if lowered.startswith("danger") and lowered[6:].isdigit():
        return f"Опасно {lowered[6:]}"
    if lowered.startswith("sc"):
        return f"Score {text[2:]}"
    if lowered == "rest":
        return "REST"
    if lowered == "seed":
        return "Seed"
    if lowered == "wait":
        return "Wait"
    return text[:1].upper() + text[1:] if text else "-"


def _format_status_cell(label: str, value: object, *, width: int = 26) -> str:
    text = f"{label} {_format_live_status_value(value)}"
    if len(text) > width:
        text = text[: max(0, width - 1)] + "~"
    return f"{text:<{width}}"


def _format_status_line(*cells: str) -> str:
    return "  ".join(cells).rstrip()


def _format_live_runtime(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0.0:
        return "-"
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}ч {minutes:02d}м {secs:02d}с"
    if minutes > 0:
        return f"{minutes}м {secs:02d}с"
    return f"{secs}с"


def _format_live_pulse(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0.0:
        return "-"
    if seconds >= 60.0:
        return _format_live_runtime(seconds)
    return f"{float(seconds):.1f}с"


def _ms_to_iso_utc(timestamp_ms: int) -> str:
    if int(timestamp_ms) <= 0:
        return ""
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _live_crypto_session_context_ms(timestamp_ms: int) -> dict[str, object]:
    moment = datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC)
    minute_of_day = moment.hour * 60 + moment.minute
    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    for start_minute, end_minute, label, phase, primary, secondary in LIVE_CRYPTO_SESSION_WINDOWS_UTC:
        if int(start_minute) <= minute_of_day < int(end_minute):
            start_dt = day_start + timedelta(minutes=int(start_minute))
            end_dt = day_start + timedelta(minutes=int(end_minute))
            return {
                "label": label,
                "phase": phase,
                "primary": primary,
                "secondary": secondary,
                "start_ms": int(start_dt.timestamp() * 1000),
                "end_ms": int(end_dt.timestamp() * 1000),
            }
    raise LiveDataIntegrityError(f"No crypto session window for UTC minute {minute_of_day}")


def _live_session_window_ms(timestamp_ms: int) -> tuple[str, int, int]:
    session = _live_crypto_session_context_ms(timestamp_ms)
    return str(session["label"]), int(session["start_ms"]), int(session["end_ms"])


def _split_live_connection_status(connection_text: str) -> tuple[str, str]:
    text = " ".join(str(connection_text or "").replace("·", " ").split())
    lowered = text.lower()
    ticker_status = "-"
    flow_status = "-"
    if "ticker ok" in lowered:
        ticker_status = "ok"
    elif "ticker seed" in lowered:
        ticker_status = "seed"
    elif "ticker rest" in lowered:
        ticker_status = "REST"
    elif "ticker нет" in lowered:
        ticker_status = "нет"
    elif lowered.startswith("ticker "):
        ticker_status = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else "-"
    elif lowered.startswith("flow "):
        ticker_status = "ok"
    elif text:
        ticker_status = text

    if "flow ok" in lowered:
        flow_status = "ok"
    elif "flow pending" in lowered:
        flow_status = "wait"
    elif "flow rest" in lowered:
        flow_status = "REST"
    elif "flow нет" in lowered:
        flow_status = "нет"
    elif "flow подписка" in lowered:
        flow_status = "sub"
    elif "flow gap rest" in lowered:
        flow_status = "gapREST"
    elif lowered.startswith("flow "):
        flow_status = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else "-"
    return ticker_status, flow_status


def _format_session_top_cell(text: str, *, width: int = 26) -> str:
    cleaned = str(text).strip()
    if len(cleaned) > width:
        cleaned = cleaned[: max(0, width - 1)] + "~"
    return f"{cleaned:<{width}}"


def _format_session_top_block(session_top_snapshot: dict[str, object] | None) -> list[str]:
    if not session_top_snapshot:
        return []
    label_base = str(session_top_snapshot.get("session_label") or "Топы")
    window_label = str(session_top_snapshot.get("top_window_label") or "")
    phase = str(session_top_snapshot.get("session_phase") or "")
    if window_label:
        label = f"{label_base} · {window_label}"
    else:
        label = label_base
    if phase == "overlap" and "+" not in label:
        label = f"{label} · наложение"
    elif phase == "transition" and "→" not in label:
        label = f"{label} · переход"
    items_raw = session_top_snapshot.get("items")
    items = items_raw if isinstance(items_raw, list) else []
    cells: list[str] = []
    for item in items[:LIVE_SESSION_TOP_LIMIT]:
        if not isinstance(item, dict):
            continue
        symbol = _compact_symbol(str(item.get("symbol") or ""))
        growth = _finite_or_none(item.get("growth_fraction"))
        if not symbol or growth is None:
            continue
        cells.append(_format_session_top_cell(f"{symbol} {_format_percent(growth, signed=False, precision=1)}"))
    if cells:
        while len(cells) < LIVE_SESSION_TOP_LIMIT:
            cells.append(_format_session_top_cell(""))
        return ["", label, _format_status_line(*cells[:LIVE_SESSION_TOP_LIMIT])]
    reason = str(session_top_snapshot.get("reason") or "")
    if reason in {"no_positive_growth_since_session_baseline", "no_positive_growth_in_rolling_6h"}:
        value = "нет роста"
    elif reason in {"ticker_snapshot_not_seen_for_current_session", "no_usable_ticker_price_snapshots_in_rolling_6h"}:
        value = "нет данных"
    elif reason == "no_usable_ticker_price_snapshots":
        value = "нет цен"
    else:
        value = "нет данных"
    return ["", label, _format_status_line(_format_session_top_cell(value), _format_session_top_cell(""), _format_session_top_cell(""))]


def _format_live_heartbeat(
    *,
    runtime_seconds: float,
    cycle_seconds: float,
    connection_health_pct: float,
    anomalies_total: int,
    active_now: int,
    active_seen: int,
    open_positions: int,
    closed_positions: int,
    pnl_pct: float,
    connection_text: str,
    data_status_text: str,
    delayed_replay_enabled: bool,
    delayed_replay_pending: int,
    delayed_replay_total: int,
    idle: bool,
    order_delta: int,
    real_orders: bool,
    cold_status: str,
    cold_age: str,
    guard_status: str,
    session_top_snapshot: dict[str, object] | None = None,
) -> str:
    del idle, real_orders, cold_age, connection_text
    if delayed_replay_enabled:
        replay_pending = max(0, int(delayed_replay_pending))
        replay_total = max(replay_pending, int(delayed_replay_total))
        replay_value = f"{replay_pending}/{replay_total}"
    else:
        replay_value = "Выкл"
    order_value = str(int(order_delta)) if order_delta else "0"
    rows = [
        "Соединение",
        _format_status_line(
            _format_status_cell("Стабильность", _format_percent(connection_health_pct, signed=False, precision=1)),
            _format_status_cell("Пульс", _format_live_pulse(cycle_seconds)),
            _format_status_cell("Данные", data_status_text),
        ),
        "",
        "Рынок",
        _format_status_line(
            _format_status_cell("Время", _format_live_runtime(runtime_seconds)),
            _format_status_cell("События", anomalies_total),
            _format_status_cell("Активные", f"{active_now}/{active_seen}"),
        ),
        "",
        "Торговля",
        _format_status_line(
            _format_status_cell("PNL", _format_percent(pnl_pct, signed=False)),
            _format_status_cell("Позиции", f"{open_positions}/{closed_positions}"),
            _format_status_cell("Ордера", order_value),
        ),
        "",
        "Контроль",
        _format_status_line(
            _format_status_cell("Повтор", replay_value),
            _format_status_cell("Покрытие", cold_status),
            _format_status_cell("Защита", guard_status),
        ),
    ]
    rows.extend(_format_session_top_block(session_top_snapshot))
    return "\n" + "\n".join(rows)

def _format_percent(value: float, *, signed: bool = False, precision: int = 1) -> str:
    if not math.isfinite(value):
        return "n/a"
    pct = float(value) * 100.0
    sign = "+" if signed and pct >= 0.0 else ""
    return f"{sign}{pct:.{precision}f}%"


def _format_usdt(value: float) -> str:
    sign = "+" if value >= 0.0 else "-"
    rendered = f"{abs(float(value)):.3f}".rstrip("0").rstrip(".")
    return f"{sign}{rendered.replace('.', ',')}"


def _series_float_or_none(row: pd.Series, column: str) -> float | None:
    if column not in row.index:
        return None
    return _finite_or_none(row.get(column))


def _row_float_or_none(row: dict[str, object], column: str) -> float | None:
    return _finite_or_none(row.get(column))


def _finite_or_blank(value: float | None) -> float | str:
    finite = _finite_or_none(value)
    return finite if finite is not None else ""


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_divide(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return float(numerator / denominator)


def _percentile(values: list[float], fraction: float) -> float:
    finite_values = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite_values:
        return float("nan")
    fraction = max(0.0, min(1.0, float(fraction)))
    if len(finite_values) == 1:
        return finite_values[0]
    position = fraction * float(len(finite_values) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return finite_values[lower_index]
    lower = finite_values[lower_index]
    upper = finite_values[upper_index]
    weight = position - float(lower_index)
    return lower + (upper - lower) * weight

def _median_positive(values: list[float]) -> float | None:
    finite_positive = sorted(float(value) for value in values if math.isfinite(float(value)) and float(value) > 0.0)
    if not finite_positive:
        return None
    mid = len(finite_positive) // 2
    if len(finite_positive) % 2:
        return finite_positive[mid]
    return (finite_positive[mid - 1] + finite_positive[mid]) / 2.0


def _prior_up_down_whipsaw_to_impulse_range(baseline: pd.DataFrame, *, impulse_range: float) -> float:
    if baseline.empty or not math.isfinite(impulse_range) or impulse_range <= 0.0:
        return float("nan")
    highs = baseline["high"].astype(float).to_numpy()
    lows = baseline["low"].astype(float).to_numpy()
    if highs.size == 0 or lows.size == 0:
        return float("nan")
    high_pos = max(range(len(highs)), key=lambda pos: highs[pos] if math.isfinite(highs[pos]) else -math.inf)
    lows_before = [float(value) for value in lows[: high_pos + 1] if math.isfinite(float(value))]
    lows_after = [float(value) for value in lows[high_pos:] if math.isfinite(float(value))]
    if not lows_before or not lows_after or not math.isfinite(float(highs[high_pos])):
        return float("nan")
    low_before_high = min(lows_before)
    low_after_high = min(lows_after)
    high_value = float(highs[high_pos])
    up_leg = high_value - low_before_high
    down_leg = high_value - low_after_high
    return min(_safe_divide(up_leg, impulse_range), _safe_divide(down_leg, impulse_range))

def _session_name(timestamp_ms: int) -> str:
    hour = datetime.fromtimestamp(timestamp_ms / 1000, UTC).hour
    if 0 <= hour < 7:
        return "Азия"
    if 7 <= hour < 13:
        return "Европа"
    if 13 <= hour < 21:
        return "Америка"
    return "поздняя Америка/Азия"


def _telegram_escape(value: str) -> str:
    return html.escape(str(value), quote=False)


def _telegram_code(value: str) -> str:
    return f"<code>{_telegram_escape(str(value))}</code>"
