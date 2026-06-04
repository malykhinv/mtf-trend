"""Research backtest for early anomaly-continuation long entries."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import re
import threading
import time
from dataclasses import asdict
from dataclasses import replace
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Iterable
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import quote

import numpy as np
import pandas as pd

from constants import (
    DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS,
    DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS,
    DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT,
    DEFAULT_SLIPPAGE,
)
from research_tools.anomaly_category_contract import (
    CATEGORY_CONTRACT_ID as PUMP_CATEGORY_CONTRACT,
    DEFAULT_PUMP_CATEGORY_IDS as PUMP_CATEGORY_PROFILE_ORDER,
    PUMP_CATEGORY_DISCOVERY,
    PUMP_CATEGORY_FAMILY_DISCOVERY,
    PUMP_CATEGORY_FAMILY_LIVE,
    backtest_profile_overrides,
    priority_for_timeframe,
)

from research_tools.charting import (
    CHART_ANOMALY,
    CHART_DOWN,
    CHART_EMA9,
    CHART_EMA20,
    CHART_ENTRY,
    CHART_EXIT,
    CHART_FIGURE_FACE,
    CHART_LEVEL,
    CHART_MUTED,
    CHART_PROFIT_EDGE,
    CHART_PROFIT_FACE,
    CHART_RISK_EDGE,
    CHART_RISK_FACE,
    CHART_SAVEFIG_KWARGS,
    CHART_TEXT,
    CHART_UP,
    TRADE_CHART_FIGSIZE,
    annotate_axis_price_tag,
    build_tick_labels_from_timestamps,
    build_tick_positions_from_timestamps,
    build_tick_timestamps,
    configure_plot_axes,
    draw_candles,
    draw_price_zone,
    format_chart_symbol,
    infer_frame_step_ms,
    resolve_axis_tag_positions,
    resolve_candle_width,
    resolve_timestamp_plot_idx,
)
from research_tools.anomaly_continuation_lab import (
    AnomalyLabConfig,
    DERIVATIVES_CONTEXT_SPECS,
    build_derivatives_context_status,
    build_oi_context_status,
    collect_anomaly_lab_rows,
    compute_start_verticality_metrics,
    enrich_candidates_with_open_interest,
    enrich_candidates_with_derivatives_context,
    _empty_context_columns,
    _emit_progress,
)

from utils.symbols import normalize_symbol

from research_tools.runner_fader_prepump_context import (
    DEFAULT_PREPUMP_CONTEXT_TIMEFRAME,
    DEFAULT_PREPUMP_CONTEXT_WINDOWS,
    PrepumpContextConfig,
    parse_prepump_windows,
    write_runner_fader_prepump_context,
)

_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_FIVE_MINUTE_MS = 5 * 60 * 1000
_PRIOR_CONTEXT_LIVE_LOOKBACK_HOURS = 24
_PRIOR_CONTEXT_LIVE_LOOKBACK_MS = _PRIOR_CONTEXT_LIVE_LOOKBACK_HOURS * _HOUR_MS
_PRIOR_CONTEXT_MIN_COVERAGE_RATIO = 0.80
_TRADE_CHART_CONTEXT_DAYS = 4
_MATERIALIZED_SUBMINUTE_CACHE_VERSION = "p378_1s_ohlcv_to_subminute_full_buckets_v1"
AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION = "p378_aggtrades_to_1s_full_buckets_v1"
DEFAULT_LATENCY_EXTRA_MS = 5_000
DEFAULT_LATENCY_GRID_MS = (0, 5_000)
DEFAULT_BACKTEST_SYMBOL_WORKERS = 1


def _effective_symbol_workers(value: object, *, total_items: int) -> int:
    """Return a bounded symbol-level worker count for cache-only backtest work."""

    if total_items <= 1:
        return 1
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = DEFAULT_BACKTEST_SYMBOL_WORKERS
    if requested <= 1:
        return 1
    cpu_count = os.cpu_count() or 1
    # Keep this bounded: each worker may hold large subminute parquet frames.
    return max(1, min(int(requested), int(total_items), max(1, int(cpu_count)), 8))



def append_speed_diagnostic(
    rows: list[dict[str, object]] | None,
    *,
    stage: str,
    seconds: float,
    scope: str = "",
    symbol: str = "",
    status: str = "ok",
    output_rows: int | None = None,
    item_count: int | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Append one low-overhead timing row for backtest speed forensics."""

    if rows is None:
        return
    safe_seconds = max(0.0, float(seconds))
    record: dict[str, object] = {
        "stage": str(stage),
        "scope": str(scope),
        "symbol": str(symbol),
        "status": str(status),
        "seconds": round(safe_seconds, 6),
    }
    if output_rows is not None:
        output_rows_int = int(output_rows)
        record["output_rows"] = output_rows_int
        record["rows_per_second"] = (
            round(float(output_rows_int) / safe_seconds, 3) if safe_seconds > 0.0 else float("nan")
        )
    if item_count is not None:
        item_count_int = int(item_count)
        record["item_count"] = item_count_int
        record["items_per_second"] = (
            round(float(item_count_int) / safe_seconds, 3) if safe_seconds > 0.0 else float("nan")
        )
    if extra:
        record.update(extra)
    rows.append(record)


def speed_diagnostics_frame(rows: list[dict[str, object]] | None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=[
                "stage",
                "scope",
                "symbol",
                "status",
                "seconds",
                "output_rows",
                "item_count",
                "rows_per_second",
                "items_per_second",
            ]
        )
    return pd.DataFrame(rows)


def speed_diagnostics_summary_frame(
    rows: list[dict[str, object]] | None,
    *,
    total_seconds: float | None = None,
) -> pd.DataFrame:
    frame = speed_diagnostics_frame(rows)
    if frame.empty or "stage" not in frame.columns:
        return pd.DataFrame()
    work = frame.copy()
    for column in ("scope", "status"):
        if column not in work.columns:
            work[column] = ""
    seconds = pd.to_numeric(work.get("seconds", 0.0), errors="coerce").fillna(0.0)
    work["_seconds"] = seconds
    if "output_rows" in work.columns:
        work["_output_rows"] = pd.to_numeric(work["output_rows"], errors="coerce").fillna(0.0)
    else:
        work["_output_rows"] = 0.0
    if "item_count" in work.columns:
        work["_item_count"] = pd.to_numeric(work["item_count"], errors="coerce").fillna(0.0)
    else:
        work["_item_count"] = 0.0
    grouped = (
        work.groupby(["stage", "scope", "status"], dropna=False)
        .agg(
            observations=("stage", "size"),
            seconds_sum=("_seconds", "sum"),
            seconds_mean=("_seconds", "mean"),
            seconds_max=("_seconds", "max"),
            output_rows_sum=("_output_rows", "sum"),
            item_count_sum=("_item_count", "sum"),
        )
        .reset_index()
    )
    if total_seconds is None or total_seconds <= 0.0:
        total_seconds = float(grouped["seconds_sum"].sum())
    grouped["share_of_total_seconds"] = grouped["seconds_sum"].apply(
        lambda value: _safe_divide_value(float(value), float(total_seconds)) if total_seconds else float("nan")
    )
    for column in ("seconds_sum", "seconds_mean", "seconds_max", "share_of_total_seconds"):
        grouped[column] = pd.to_numeric(grouped[column], errors="coerce").round(6)
    for column in ("output_rows_sum", "item_count_sum"):
        grouped[column] = pd.to_numeric(grouped[column], errors="coerce").round(0).astype("int64")
    grouped.sort_values(["seconds_sum", "seconds_max"], ascending=[False, False], inplace=True)
    grouped.reset_index(drop=True, inplace=True)
    return grouped


def speed_diagnostics_slowest_symbols_frame(
    rows: list[dict[str, object]] | None,
    *,
    limit: int = 200,
) -> pd.DataFrame:
    frame = speed_diagnostics_frame(rows)
    if frame.empty or "symbol" not in frame.columns:
        return pd.DataFrame()
    work = frame.loc[frame["symbol"].astype(str).ne("")].copy()
    if work.empty:
        return pd.DataFrame()
    work["_seconds"] = pd.to_numeric(work.get("seconds", 0.0), errors="coerce").fillna(0.0)
    if "output_rows" in work.columns:
        work["_output_rows"] = pd.to_numeric(work["output_rows"], errors="coerce").fillna(0.0)
    else:
        work["_output_rows"] = 0.0
    if "item_count" in work.columns:
        work["_item_count"] = pd.to_numeric(work["item_count"], errors="coerce").fillna(0.0)
    else:
        work["_item_count"] = 0.0
    result = (
        work.groupby(["stage", "scope", "symbol"], dropna=False)
        .agg(
            observations=("stage", "size"),
            seconds_sum=("_seconds", "sum"),
            seconds_max=("_seconds", "max"),
            output_rows_sum=("_output_rows", "sum"),
            item_count_sum=("_item_count", "sum"),
        )
        .reset_index()
    )
    for column in ("seconds_sum", "seconds_max"):
        result[column] = pd.to_numeric(result[column], errors="coerce").round(6)
    for column in ("output_rows_sum", "item_count_sum"):
        result[column] = pd.to_numeric(result[column], errors="coerce").round(0).astype("int64")
    result.sort_values(["seconds_sum", "seconds_max"], ascending=[False, False], inplace=True)
    result.reset_index(drop=True, inplace=True)
    return result.head(int(limit)).copy()


def _record_stage_timing(
    timings: dict[str, float],
    diagnostics: list[dict[str, object]] | None,
    key: str,
    seconds: float,
    *,
    output_rows: int | None = None,
    item_count: int | None = None,
    status: str = "ok",
    extra: Mapping[str, object] | None = None,
) -> None:
    timings[key] = float(seconds)
    append_speed_diagnostic(
        diagnostics,
        stage="stage",
        scope=key.removesuffix("_seconds"),
        seconds=float(seconds),
        status=status,
        output_rows=output_rows,
        item_count=item_count,
        extra=extra,
    )

LATENCY_1S_BACKFILL_VERSION = AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION
DIRECT_TARGET_AGGTRADE_CACHE_VERSION = "aggtrade_direct_target_ltf_v1"
DIRECT_TARGET_AGGTRADE_COVERAGE_INDEX_FILE = "aggtrade_direct_target_ltf_coverage.parquet"
TARGETED_FLOW_BACKFILL_DEFAULT_BEFORE_MS = 120_000
TARGETED_FLOW_BACKFILL_DEFAULT_AFTER_MS = 120_000
TARGETED_FLOW_MERGE_GAP_MS = 60_000
TARGETED_FLOW_MAX_MERGED_SPAN_MS = 10 * 60_000
BINANCE_AGGTRADES_REQUEST_TIMEOUT_SECONDS = 10.0
BINANCE_AGGTRADES_MIN_REQUEST_INTERVAL_SECONDS = 0.35
BINANCE_AGGTRADES_MAX_HTTP_ATTEMPTS = 4
BINANCE_AGGTRADES_HTTP_BACKOFF_SECONDS = {429: 20.0, 418: 120.0}
BINANCE_AGGTRADES_MAX_BACKOFF_SECONDS = 180.0
_BINANCE_AGGTRADES_RATE_LIMIT_LOCK = threading.Lock()
_BINANCE_AGGTRADES_NEXT_REQUEST_AT = 0.0


def _wait_for_binance_aggtrades_request_slot() -> None:
    global _BINANCE_AGGTRADES_NEXT_REQUEST_AT
    while True:
        with _BINANCE_AGGTRADES_RATE_LIMIT_LOCK:
            now = time.monotonic()
            wait_seconds = _BINANCE_AGGTRADES_NEXT_REQUEST_AT - now
            if wait_seconds <= 0:
                _BINANCE_AGGTRADES_NEXT_REQUEST_AT = now + float(BINANCE_AGGTRADES_MIN_REQUEST_INTERVAL_SECONDS)
                return
        time.sleep(min(float(wait_seconds), 5.0))


def _register_binance_aggtrades_backoff(seconds: float) -> None:
    global _BINANCE_AGGTRADES_NEXT_REQUEST_AT
    delay = max(0.0, min(float(seconds), float(BINANCE_AGGTRADES_MAX_BACKOFF_SECONDS)))
    if delay <= 0.0:
        return
    with _BINANCE_AGGTRADES_RATE_LIMIT_LOCK:
        _BINANCE_AGGTRADES_NEXT_REQUEST_AT = max(_BINANCE_AGGTRADES_NEXT_REQUEST_AT, time.monotonic() + delay)


def _binance_aggtrades_retry_after_seconds(exc: urllib.error.HTTPError, fallback_seconds: float) -> float:
    retry_after = ""
    headers = getattr(exc, "headers", None)
    if headers is not None:
        retry_after = str(headers.get("Retry-After", "") or "").strip()
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return float(fallback_seconds)


def _fetch_binance_aggtrades_json(url: str) -> list[dict[str, object]]:
    last_exc: BaseException | None = None
    for attempt in range(1, int(BINANCE_AGGTRADES_MAX_HTTP_ATTEMPTS) + 1):
        try:
            _wait_for_binance_aggtrades_request_slot()
            with urllib.request.urlopen(url, timeout=float(BINANCE_AGGTRADES_REQUEST_TIMEOUT_SECONDS)) as response:
                batch = json.loads(response.read().decode("utf-8"))
            if isinstance(batch, list):
                return batch
            raise RuntimeError(f"Binance aggTrades returned non-list payload: {type(batch).__name__}")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if int(getattr(exc, "code", 0)) in BINANCE_AGGTRADES_HTTP_BACKOFF_SECONDS and attempt < int(BINANCE_AGGTRADES_MAX_HTTP_ATTEMPTS):
                base = float(BINANCE_AGGTRADES_HTTP_BACKOFF_SECONDS[int(exc.code)])
                delay = _binance_aggtrades_retry_after_seconds(exc, base * attempt)
                _register_binance_aggtrades_backoff(delay)
                continue
            raise RuntimeError(f"Binance aggTrades HTTP {getattr(exc, 'code', '?')} after {attempt} attempts") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_exc = exc
            if attempt < int(BINANCE_AGGTRADES_MAX_HTTP_ATTEMPTS):
                _register_binance_aggtrades_backoff(min(2.0 * attempt, 15.0))
                continue
            raise RuntimeError(f"Binance aggTrades request failed after {attempt} attempts: {exc}") from exc
    raise RuntimeError(f"Binance aggTrades request failed: {last_exc}")
PAIR_COLLECTION_MODE_FORMING = "forming"
PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION = "post_htf_close_ltf_confirmation"
PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION = "post_htf_close_ltf_forward_confirmation"
PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER = "bare_htf_short_fader"
POST_HTF_CLOSE_LTF_CONFIRMATION_CONTRACT = "post_htf_close_ltf_confirmation_v1"
POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION_CONTRACT = "post_htf_close_ltf_forward_confirmation_v1"
BARE_HTF_SHORT_FADER_CONTRACT = "bare_htf_short_fader_v1"
SHORT_FADER_DEFAULT_TRIGGERS = (
    "failed_new_high,taker_fade_red,close_below_htf_close,close_below_post_mid,"
    "lower_high_close_down,effort_no_progress,pullback_without_recovery"
)
_POST_HTF_CLOSE_PAIR_MODES = {
    PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION,
    PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION,
}
_BAD_CONTEXT_STATUSES = {"error", "missing_columns", "missing_column", "missing_timestamp", "missing_frame", "empty_oi", "stale_asof"}
_TRADE_CHART_FLOW_PROVENANCE = {
    "trade_count_proxy_used": False,
    "levels_trade_count_source": "cached_ohlcv.number_of_trades",
    "entry_trade_count_source": "cached_ohlcv.number_of_trades",
    "levels_quote_volume_source": "cached_ohlcv.quote_volume",
    "entry_quote_volume_source": "cached_ohlcv.quote_volume",
}

TRADE_SIGNAL_CONTEXT_COLUMNS = (
    "setup_available_timestamp_ms",
    "setup_available_timestamp_utc",
    "setup_full_available_timestamp_ms",
    "setup_full_available_timestamp_utc",
    "decision_available_timestamp_ms",
    "decision_available_timestamp_utc",
    "timestamp_semantics",
    "trade_count_proxy_used",
    "levels_trade_count_source",
    "entry_trade_count_source",
    "levels_quote_volume_source",
    "entry_quote_volume_source",
    "pump_category_id",
    "pump_category_rank",
    "pump_category_matches",
    "pump_category_family",
    "pump_category_is_live_rule",
    "pump_category_contract",
    "pump_category_source",
    "feature_contract",
    "setup_timeframe",
    "entry_timeframe",
    "setup_source",
    "setup_elapsed_fraction",
    "setup_closed_entry_candles",
    "candidate_collection_policy",
    "post_htf_close_ltf_confirmation",
    "post_htf_close_entry_not_before_ms",
    "post_htf_close_entry_not_before_utc",
    "post_htf_close_ltf_left_context_ms",
    "post_htf_close_ltf_left_context_candles",
    "post_htf_close_ltf_left_context_status",
    "post_htf_close_ltf_left_context_source",
    "post_htf_close_ltf_left_context_start_ms",
    "post_htf_close_ltf_left_context_end_ms",
    "post_htf_close_ltf_forward_confirmation",
    "post_htf_close_ltf_forward_confirmation_start_ms",
    "post_htf_close_ltf_forward_confirmation_end_ms",
    "post_htf_close_ltf_forward_confirmation_candles",
    "entry_activation_price",
    "start_trade_count",
    "baseline_trade_count_median",
    "baseline_quote_volume_median",
    "start_trade_ratio",
    "start_quote_ratio",
    "start_quote_ratio_raw",
    "start_trade_ratio_raw",
    "start_quote_pace_ratio",
    "start_trade_pace_ratio",
    "next_n_trade_count_mean",
    "next_n_quote_volume_mean",
    "hold_count_next_n_candles",
    "hold_ratio_next_n_candles",
    "hold_count_model",
    "flow_hold_count_next_n_candles",
    "flow_hold_ratio_next_n_candles",
    "price_retention_model",
    "next_n_trade_decay",
    "next_n_quote_decay",
    "price_retention_next_n",
    "new_high_count_next_n",
    "start_verticality_score",
    "start_verticality_path_efficiency",
    "start_verticality_range_efficiency",
    "start_verticality_slope_pct_per_candle",
    "start_verticality_max_retrace_fraction",
    "start_verticality_green_share",
    "flow_taker_buy_status",
    "start_taker_buy_quote_share",
    "baseline_taker_buy_quote_share_median",
    "start_taker_buy_quote_share_delta",
    "next_n_taker_buy_quote_share_mean",
    "next_n_taker_buy_quote_share_delta",
    "next_n_taker_buy_quote_share_decay",
    "start_avg_trade_quote_size",
    "baseline_avg_trade_quote_size_median",
    "start_avg_trade_quote_size_ratio",
    "next_n_avg_trade_quote_size_mean",
    "next_n_avg_trade_quote_size_decay",
    "decision_return_from_start_open",
    "start_close_position_in_range",
    "start_body_to_range",
    "start_upper_wick_to_range",
    "start_lower_wick_to_range",
    "baseline_range_median",
    "baseline_range_pct_median",
    "start_range_ratio_to_baseline",
    "start_range_pct",
    "start_range_pct_ratio_to_baseline",
    "baseline_zero_range_share",
    "baseline_return_range_pct",
    "baseline_close_return_range_pct",
    "baseline_return_from_first_close_pct",
    "prior_up_leg_to_impulse_range",
    "prior_down_leg_to_impulse_range",
    "prior_up_down_whipsaw_to_impulse_range",
    "prior_up_down_whipsaw_source",
    "start_quote_per_abs_return",
    "start_trades_per_abs_return",
    "start_quote_ratio_per_abs_return",
    "start_trade_ratio_per_abs_return",
    "prior_context_status",
    "prior_context_reason",
    "prior_context_source",
    "prior_context_rows_used",
    "prior_context_start_ms",
    "prior_context_end_ms",
    "prior_spike_count_24h",
    "prior_spike_count_72h",
    "prior_fast_fade_count_24h",
    "prior_fast_fade_count_72h",
    "prior_big_move_count_24h",
    "prior_big_move_count_72h",
    "prior_spike_density_72h",
    "time_since_prior_spike_ms",
    "time_since_prior_spike_hours",
    "post_start_pullback_fraction_of_box",
    "oi_timeframe",
    "oi_status",
    "oi_cache_status",
    "oi_fetch_or_load_status",
    "oi_cache_min_timestamp_ms",
    "oi_cache_min_timestamp_utc",
    "oi_cache_max_timestamp_ms",
    "oi_cache_max_timestamp_utc",
    "oi_timestamp_ms",
    "oi_timestamp_utc",
    "oi_asof_timestamp_ms",
    "oi_asof_timestamp_utc",
    "oi_age_ms",
    "oi_open_interest",
    "oi_change_1x5m",
    "oi_change_pct_1x5m",
    "oi_change_3x5m",
    "oi_change_pct_3x5m",
    "oi_change_6x5m",
    "oi_change_pct_6x5m",
    "oi_price_interaction_3x5m",
    "oi_change_pct_3x5m_per_decision_return",
    "mark_close_vs_decision_close_basis",
    "taker_ls_buy_share",
)

for _context_spec in DERIVATIVES_CONTEXT_SPECS:
    _context_prefix = str(_context_spec["prefix"])
    TRADE_SIGNAL_CONTEXT_COLUMNS += (
        f"{_context_prefix}_status",
        f"{_context_prefix}_timestamp_ms",
        f"{_context_prefix}_timestamp_utc",
        f"{_context_prefix}_asof_timestamp_ms",
        f"{_context_prefix}_asof_timestamp_utc",
        f"{_context_prefix}_age_ms",
    )
    for _context_column in _context_spec["value_columns"]:
        _context_column_name = str(_context_column)
        TRADE_SIGNAL_CONTEXT_COLUMNS += (f"{_context_prefix}_{_context_column_name}",)
        for _context_bars in _context_spec["lookback_bars"]:
            TRADE_SIGNAL_CONTEXT_COLUMNS += (
                f"{_context_prefix}_{_context_column_name}_change_{int(_context_bars)}",
                f"{_context_prefix}_{_context_column_name}_change_pct_{int(_context_bars)}",
            )


@dataclass(frozen=True, slots=True)
class AnomalyBacktestConfig:
    lab_config: AnomalyLabConfig
    setup_timeframe: str | None = None
    entry_timeframe: str | None = None
    feature_contract: str = "closed_setup_tf_v1"
    pair_collection_mode: str = PAIR_COLLECTION_MODE_FORMING
    min_price_retention: float = 0.70
    max_price_retention: float | None = None
    min_verticality_score: float = 0.25
    min_hold_count: int = 2
    min_oi_change_pct_3x5m: float | None = None
    require_oi_status_ok: bool = False
    exhaustion_profile: str = "none"
    max_start_quote_ratio: float | None = None
    max_start_trade_ratio: float | None = None
    min_baseline_quote_daily_proxy: float | None = None
    max_start_avg_trade_quote_size_ratio: float | None = None
    max_start_quote_ratio_per_abs_return: float | None = None
    min_start_range_pct_ratio_to_baseline: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = None
    max_prior_up_down_whipsaw_to_impulse_range: float | None = 0.60
    min_flow_hold_count: int | None = None
    max_prior_spike_count_72h: int | None = None
    max_prior_fast_fade_count_72h: int | None = None
    min_start_lower_wick_to_range: float | None = None
    max_start_upper_wick_to_range: float | None = None
    min_next_taker_buy_quote_share: float | None = None
    red_flag_profile: str = "none"
    min_mark_close_vs_decision_close_basis: float | None = None
    reject_oi_down_mark_discount: bool = False
    reject_stale_derivatives_context: bool = False
    max_start_taker_buy_quote_share_delta: float | None = None
    max_next_taker_buy_quote_share_delta: float | None = None
    max_start_trade_ratio_per_abs_return: float | None = None
    min_initial_risk_pct: float | None = None
    max_initial_risk_pct: float = 0.16
    min_runner_shape_quote_ratio: float | None = None
    min_runner_shape_trade_ratio: float | None = None
    min_runner_shape_range_ratio: float | None = None
    min_runner_shape_quote_acceleration: float | None = None
    min_runner_shape_trade_acceleration: float | None = None
    min_runner_shape_range_acceleration: float | None = None
    min_runner_shape_second_half_return_pct: float | None = None
    max_runner_shape_top1_quote_share: float | None = None
    entry_method: str = "market"
    pullback_box_fraction: float = 0.75
    entry_timeout_candles: int = 60
    market_entry_latency_candles: int = 1
    latency_enabled: bool = False
    latency_extra_ms: int = DEFAULT_LATENCY_EXTRA_MS
    max_market_entry_drift_pct: float = DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT
    min_market_rr_to_signal_tp1: float = 0.70
    stop_buffer_range_fraction: float = 0.05
    tp1_r: float = 0.75
    tp1_fraction: float = 1.0
    move_stop_to_breakeven_after_tp1: bool = True
    trail_lookback_candles: int = 5
    trail_buffer_r: float = 0.10
    exit_rule: str = "structural_trail"
    max_hold_candles: int = 240
    max_open_positions: int = DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS
    fee_rate: float = 0.0004
    entry_slippage_pct: float = DEFAULT_SLIPPAGE
    exit_slippage_pct: float = DEFAULT_SLIPPAGE
    short_fader_analysis_minutes: int = 60
    short_fader_target_r: float = 2.5
    short_fader_min_prior_spike_count_72h: int = 10
    short_fader_min_prior_fast_fade_count_72h: int = 3
    short_fader_triggers: str = SHORT_FADER_DEFAULT_TRIGGERS
    short_fader_require_prior_context: bool = False
    short_fader_run_exit_grid: bool = False
    short_fader_prefilter_min_quote_ratio: float = 10.0
    short_fader_prefilter_min_trade_ratio: float = 8.0
    short_fader_prefilter_min_htf_return: float = 0.015
    short_fader_clean_adverse_threshold_pct: float = 0.015
    short_fader_mfe_threshold_pct: float = 0.02
    write_prepump_context: bool = True
    prepump_context_timeframe: str = DEFAULT_PREPUMP_CONTEXT_TIMEFRAME
    prepump_context_windows: str = DEFAULT_PREPUMP_CONTEXT_WINDOWS
    prepump_context_min_coverage_ratio: float = 0.80
    symbol_workers: int = DEFAULT_BACKTEST_SYMBOL_WORKERS


EXECUTION_GUARD_SKIP_REASONS = {
    "no_market_execution_candle",
    "no_latency_execution_frame",
    "no_latency_execution_candle",
    "invalid_market_execution_price",
    "tp1_already_reached_before_market_entry",
    "invalid_actual_market_risk",
    "market_entry_price_drift",
    "market_entry_rr_collapsed",
}
TP1_TARGET_BASIS = "pump_leg_bottom"


def _tp1_pump_leg_risk_from_values(*, entry_price: float, box_low: float) -> tuple[float, float]:
    basis_price = float(box_low)
    return basis_price, float(entry_price) - basis_price

def _category_priority_for_timeframe(setup_timeframe: object, entry_timeframe: object) -> tuple[str, ...]:
    return priority_for_timeframe(setup_timeframe, entry_timeframe)


def _execution_model_label(config: AnomalyBacktestConfig) -> str:
    slippage_suffix = ""
    if float(config.entry_slippage_pct) or float(config.exit_slippage_pct):
        slippage_suffix = (
            f"_slip_entry_{float(config.entry_slippage_pct):g}"
            f"_exit_{float(config.exit_slippage_pct):g}"
        )
    pair_mode_suffix = ""
    pair_collection_mode = str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
    if pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION:
        pair_mode_suffix = "_post_htf_close_ltf_confirmation"
    elif pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION:
        pair_mode_suffix = "_post_htf_close_ltf_forward_confirmation"
    elif pair_collection_mode == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER:
        pair_mode_suffix = "_bare_htf_short_fader"
    if config.entry_method == "market":
        if config.latency_enabled:
            return (
                f"next_bar_open_proxy_latency_{config.market_entry_latency_candles}"
                f"_plus_1s_delay_{int(config.latency_extra_ms)}ms"
                f"{slippage_suffix}{pair_mode_suffix}"
            )
        return f"next_bar_open_proxy_latency_{config.market_entry_latency_candles}{slippage_suffix}{pair_mode_suffix}"
    return f"{config.entry_method}{slippage_suffix}{pair_mode_suffix}"


def _nonnegative_ratio(value: object, *, field_name: str) -> float:
    resolved = float(value)
    if not np.isfinite(resolved) or resolved < 0.0:
        raise ValueError(f"{field_name} must be a finite non-negative ratio")
    return resolved


def _long_entry_fill_price(raw_price: float, *, config: AnomalyBacktestConfig) -> float:
    slippage = _nonnegative_ratio(config.entry_slippage_pct, field_name="entry_slippage_pct")
    return float(raw_price) * (1.0 + slippage)


def _long_exit_fill_price(raw_price: float, *, config: AnomalyBacktestConfig) -> float:
    slippage = _nonnegative_ratio(config.exit_slippage_pct, field_name="exit_slippage_pct")
    return float(raw_price) * (1.0 - slippage)


def _short_entry_fill_price(raw_price: float, *, config: AnomalyBacktestConfig) -> float:
    slippage = _nonnegative_ratio(config.entry_slippage_pct, field_name="entry_slippage_pct")
    return float(raw_price) * (1.0 - slippage)


def _short_exit_fill_price(raw_price: float, *, config: AnomalyBacktestConfig) -> float:
    slippage = _nonnegative_ratio(config.exit_slippage_pct, field_name="exit_slippage_pct")
    return float(raw_price) * (1.0 + slippage)


def _entry_fill_audit(
    *,
    raw_entry_price: float,
    entry_price: float,
    config: AnomalyBacktestConfig,
    model: str,
) -> dict[str, object]:
    return {
        "entry_raw_price": float(raw_entry_price),
        "entry_fill_price_model": str(model),
        "entry_slippage_pct": float(config.entry_slippage_pct),
        "exit_slippage_pct": float(config.exit_slippage_pct),
        "slippage_model": "adverse_long_entry_and_exit",
        "max_open_positions": int(config.max_open_positions),
    }


def _strip_derivative_context_requirements(config: AnomalyBacktestConfig) -> AnomalyBacktestConfig:
    """Return a wider pre-context config used only to decide which rows need market context.

    The real category decision is still made after enrichment. This helper avoids
    a circular dependency where runner rows need mark/OI context to qualify, but
    are excluded from the fetch universe before that context exists.
    """

    return replace(
        config,
        red_flag_profile="none",
        min_oi_change_pct_3x5m=None,
        require_oi_status_ok=False,
        min_mark_close_vs_decision_close_basis=None,
        reject_oi_down_mark_discount=False,
        reject_stale_derivatives_context=False,
    )


def _signal_key_frame(frame: pd.DataFrame) -> pd.DataFrame:
    key_columns = ["symbol", "setup_timeframe", "entry_timeframe", "decision_timestamp_ms"]
    if frame.empty or not set(key_columns).issubset(frame.columns):
        return pd.DataFrame(columns=key_columns)
    keys = frame.loc[:, key_columns].copy()
    keys.dropna(subset=["symbol", "decision_timestamp_ms"], inplace=True)
    keys["symbol"] = keys["symbol"].astype(str)
    keys["setup_timeframe"] = keys["setup_timeframe"].astype(str)
    keys["entry_timeframe"] = keys["entry_timeframe"].astype(str)
    keys["decision_timestamp_ms"] = keys["decision_timestamp_ms"].astype("int64")
    keys.drop_duplicates(key_columns, inplace=True)
    return keys.reset_index(drop=True)


def _attach_signal_context(result: dict[str, object], signal: pd.Series) -> dict[str, object]:
    for column in TRADE_SIGNAL_CONTEXT_COLUMNS:
        result[column] = signal.get(column, "")
    return result


def _skipped_signal_result(
    signal: pd.Series,
    *,
    skip_reason: str,
    config: AnomalyBacktestConfig,
    **extra: object,
) -> dict[str, object]:
    decision_ts = int(signal["decision_timestamp_ms"])
    result: dict[str, object] = {
        "symbol": str(signal["symbol"]),
        "decision_timestamp_ms": decision_ts,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
        "status": "skipped",
        "skip_reason": str(skip_reason),
        "entry_method": config.entry_method,
        "execution_model": _execution_model_label(config),
    }
    result.update(extra)
    return _attach_signal_context(result, signal)


def _apply_red_flag_profile(config: AnomalyBacktestConfig) -> AnomalyBacktestConfig:
    profile = str(config.red_flag_profile or "none").strip().lower()
    if profile == "none":
        return config
    if profile == "cautious":
        return replace(
            config,
            min_mark_close_vs_decision_close_basis=(
                0.0
                if config.min_mark_close_vs_decision_close_basis is None
                else config.min_mark_close_vs_decision_close_basis
            ),
            reject_oi_down_mark_discount=True if not config.reject_oi_down_mark_discount else config.reject_oi_down_mark_discount,
            reject_stale_derivatives_context=True
            if not config.reject_stale_derivatives_context
            else config.reject_stale_derivatives_context,
            max_start_taker_buy_quote_share_delta=(
                0.50
                if config.max_start_taker_buy_quote_share_delta is None
                else config.max_start_taker_buy_quote_share_delta
            ),
            max_next_taker_buy_quote_share_delta=(
                0.40
                if config.max_next_taker_buy_quote_share_delta is None
                else config.max_next_taker_buy_quote_share_delta
            ),
            max_start_quote_ratio_per_abs_return=(
                50_000.0
                if config.max_start_quote_ratio_per_abs_return is None
                else config.max_start_quote_ratio_per_abs_return
            ),
            max_start_trade_ratio_per_abs_return=(
                5_000.0
                if config.max_start_trade_ratio_per_abs_return is None
                else config.max_start_trade_ratio_per_abs_return
            ),
        )
    if profile == "strict":
        return replace(
            config,
            min_mark_close_vs_decision_close_basis=(
                0.001
                if config.min_mark_close_vs_decision_close_basis is None
                else config.min_mark_close_vs_decision_close_basis
            ),
            reject_oi_down_mark_discount=True if not config.reject_oi_down_mark_discount else config.reject_oi_down_mark_discount,
            reject_stale_derivatives_context=True
            if not config.reject_stale_derivatives_context
            else config.reject_stale_derivatives_context,
            max_start_taker_buy_quote_share_delta=(
                0.30
                if config.max_start_taker_buy_quote_share_delta is None
                else config.max_start_taker_buy_quote_share_delta
            ),
            max_next_taker_buy_quote_share_delta=(
                0.30
                if config.max_next_taker_buy_quote_share_delta is None
                else config.max_next_taker_buy_quote_share_delta
            ),
            max_start_quote_ratio_per_abs_return=(
                25_000.0
                if config.max_start_quote_ratio_per_abs_return is None
                else config.max_start_quote_ratio_per_abs_return
            ),
            max_start_trade_ratio_per_abs_return=(
                5_000.0
                if config.max_start_trade_ratio_per_abs_return is None
                else config.max_start_trade_ratio_per_abs_return
            ),
        )
    if profile in PUMP_CATEGORY_PROFILE_ORDER:
        return replace(config, **backtest_profile_overrides(profile))
    raise ValueError(f"unsupported red_flag_profile: {config.red_flag_profile}")


def _context_status_columns(frame: pd.DataFrame) -> list[str]:
    """Return only OI/derivatives market-context status columns.

    Do not include unrelated artifact status columns such as
    future_label_status or prior_context_status. Category filtering must
    not accidentally depend on research labels or non-market diagnostics.
    """

    expected = {"oi_status", "oi_cache_status", "oi_fetch_or_load_status"}
    expected.update(f"{str(spec['prefix'])}_status" for spec in DERIVATIVES_CONTEXT_SPECS)
    return [column for column in frame.columns if column in expected]


def _market_context_status_is_bad(value: object) -> bool:
    status = str(value or "").strip().lower()
    if not status:
        return False
    return status != "ok"


def _candidate_availability_mask(candidates: pd.DataFrame) -> pd.Series:
    """Return rows whose signal fields have explicit closed-candle availability semantics."""

    required_columns = (
        "timestamp_ms",
        "setup_available_timestamp_ms",
        "decision_timestamp_ms",
        "decision_available_timestamp_ms",
        "timestamp_semantics",
    )
    for column in required_columns:
        if column not in candidates.columns:
            return pd.Series(False, index=candidates.index)
    timestamp = pd.to_numeric(candidates["timestamp_ms"], errors="coerce")
    setup_available = pd.to_numeric(candidates["setup_available_timestamp_ms"], errors="coerce")
    decision_timestamp = pd.to_numeric(candidates["decision_timestamp_ms"], errors="coerce")
    decision_available = pd.to_numeric(candidates["decision_available_timestamp_ms"], errors="coerce")
    semantics = candidates["timestamp_semantics"].astype(str)
    return (
        timestamp.notna()
        & setup_available.notna()
        & decision_timestamp.notna()
        & decision_available.notna()
        & setup_available.ge(timestamp)
        & decision_available.ge(decision_timestamp)
        & decision_available.ge(setup_available)
        & semantics.str.contains("ohlcv_timestamp_is_candle_open", regex=False)
        & semantics.str.contains("available_timestamp_is_candle_close", regex=False)
    )


def _red_flag_violation_masks(signals: pd.DataFrame, *, config: AnomalyBacktestConfig) -> dict[str, pd.Series]:
    if signals.empty:
        return {}
    masks: dict[str, pd.Series] = {}
    index = signals.index
    if config.min_mark_close_vs_decision_close_basis is not None:
        mark_basis = pd.to_numeric(signals["mark_close_vs_decision_close_basis"], errors="coerce")
        masks["mark_basis_below_min"] = mark_basis.lt(config.min_mark_close_vs_decision_close_basis) | mark_basis.isna()
    if config.reject_oi_down_mark_discount:
        mark_basis = pd.to_numeric(signals["mark_close_vs_decision_close_basis"], errors="coerce")
        masks["oi_down_with_mark_discount"] = (
            signals["oi_price_interaction_3x5m"].astype(str).eq("oi_down_price_up")
            & mark_basis.le(0.0)
        ) | mark_basis.isna()
    if config.reject_stale_derivatives_context:
        status_columns = _context_status_columns(signals)
        if status_columns:
            bad_status = pd.Series(False, index=index)
            for column in status_columns:
                bad_status |= signals[column].map(_market_context_status_is_bad)
            masks["stale_or_missing_market_context"] = bad_status
        else:
            masks["stale_or_missing_market_context"] = pd.Series(True, index=index)
    if config.max_start_taker_buy_quote_share_delta is not None:
        start_delta = pd.to_numeric(signals["start_taker_buy_quote_share_delta"], errors="coerce")
        masks["start_taker_buy_delta_above_max"] = (
            start_delta.gt(config.max_start_taker_buy_quote_share_delta) | start_delta.isna()
        )
    if config.max_next_taker_buy_quote_share_delta is not None:
        next_delta = pd.to_numeric(signals["next_n_taker_buy_quote_share_delta"], errors="coerce")
        masks["confirmation_taker_buy_delta_above_max"] = (
            next_delta.gt(config.max_next_taker_buy_quote_share_delta) | next_delta.isna()
        )
    if config.max_start_trade_ratio_per_abs_return is not None:
        trade_effort = pd.to_numeric(signals["start_trade_ratio_per_abs_return"], errors="coerce")
        masks["trade_effort_per_return_above_max"] = (
            trade_effort.gt(config.max_start_trade_ratio_per_abs_return) | trade_effort.isna()
        )
    if config.min_start_range_pct_ratio_to_baseline is not None:
        range_ratio = pd.to_numeric(signals["start_range_pct_ratio_to_baseline"], errors="coerce")
        masks["start_range_ratio_below_min"] = (
            range_ratio.lt(config.min_start_range_pct_ratio_to_baseline) | range_ratio.isna()
        )
    if config.max_start_quote_ratio_per_abs_return is not None:
        quote_effort = pd.to_numeric(signals["start_quote_ratio_per_abs_return"], errors="coerce")
        masks["quote_effort_per_return_above_max"] = (
            quote_effort.gt(config.max_start_quote_ratio_per_abs_return) | quote_effort.isna()
        )
    if config.max_start_quote_ratio is not None:
        quote_ratio = pd.to_numeric(signals["start_quote_ratio"], errors="coerce")
        masks["start_quote_ratio_above_max"] = quote_ratio.gt(config.max_start_quote_ratio) | quote_ratio.isna()
    if config.max_start_trade_ratio is not None:
        trade_ratio = pd.to_numeric(signals["start_trade_ratio"], errors="coerce")
        masks["start_trade_ratio_above_max"] = trade_ratio.gt(config.max_start_trade_ratio) | trade_ratio.isna()
    if config.min_runner_shape_quote_ratio is not None:
        ratio = pd.to_numeric(signals["start_quote_ratio"], errors="coerce")
        masks["runner_shape_quote_ratio_below_min"] = ratio.lt(config.min_runner_shape_quote_ratio) | ratio.isna()
    if config.min_runner_shape_trade_ratio is not None:
        ratio = pd.to_numeric(signals["start_trade_ratio"], errors="coerce")
        masks["runner_shape_trade_ratio_below_min"] = ratio.lt(config.min_runner_shape_trade_ratio) | ratio.isna()
    if config.min_runner_shape_range_ratio is not None:
        ratio = pd.to_numeric(signals["start_range_pct_ratio_to_baseline"], errors="coerce")
        masks["runner_shape_range_ratio_below_min"] = ratio.lt(config.min_runner_shape_range_ratio) | ratio.isna()
    if config.min_runner_shape_quote_acceleration is not None:
        accel = pd.to_numeric(signals["runner_shape_quote_acceleration"], errors="coerce")
        masks["runner_shape_quote_acceleration_below_min"] = accel.lt(config.min_runner_shape_quote_acceleration) | accel.isna()
    if config.min_runner_shape_trade_acceleration is not None:
        accel = pd.to_numeric(signals["runner_shape_trade_acceleration"], errors="coerce")
        masks["runner_shape_trade_acceleration_below_min"] = accel.lt(config.min_runner_shape_trade_acceleration) | accel.isna()
    if config.min_runner_shape_range_acceleration is not None:
        accel = pd.to_numeric(signals["runner_shape_range_acceleration"], errors="coerce")
        masks["runner_shape_range_acceleration_below_min"] = accel.lt(config.min_runner_shape_range_acceleration) | accel.isna()
    if config.min_runner_shape_second_half_return_pct is not None:
        ret = pd.to_numeric(signals["runner_shape_second_half_return_pct"], errors="coerce")
        masks["runner_shape_second_half_return_below_min"] = ret.lt(config.min_runner_shape_second_half_return_pct) | ret.isna()
    if config.max_runner_shape_top1_quote_share is not None:
        top1 = pd.to_numeric(signals["runner_shape_top1_quote_share"], errors="coerce")
        masks["runner_shape_top1_quote_share_above_max"] = top1.gt(config.max_runner_shape_top1_quote_share) | top1.isna()
    if config.min_baseline_quote_daily_proxy is not None:
        setup_minutes = signals["setup_timeframe"].astype(str).map({"1m": 1.0, "5m": 5.0})
        baseline_quote = pd.to_numeric(signals["baseline_quote_volume_median"], errors="coerce")
        daily_proxy = baseline_quote * (1440.0 / setup_minutes)
        masks["baseline_quote_daily_proxy_below_min"] = (
            daily_proxy.lt(config.min_baseline_quote_daily_proxy) | daily_proxy.isna()
        )
    if config.min_flow_hold_count is not None:
        flow_hold = pd.to_numeric(signals["flow_hold_count_next_n_candles"], errors="coerce")
        masks["flow_hold_count_below_min"] = flow_hold.lt(config.min_flow_hold_count) | flow_hold.isna()
    if config.max_prior_spike_count_72h is not None:
        prior_spikes = pd.to_numeric(signals["prior_spike_count_72h"], errors="coerce")
        prior_status = signals.get("prior_context_status", pd.Series("", index=index)).astype(str)
        masks["prior_spike_count_72h_above_max"] = (
            prior_status.ne("ok") | prior_spikes.gt(config.max_prior_spike_count_72h) | prior_spikes.isna()
        )
    if config.max_prior_fast_fade_count_72h is not None:
        prior_fast_fades = pd.to_numeric(signals["prior_fast_fade_count_72h"], errors="coerce")
        prior_status = signals.get("prior_context_status", pd.Series("", index=index)).astype(str)
        masks["prior_fast_fade_count_72h_above_max"] = (
            prior_status.ne("ok")
            | prior_fast_fades.gt(config.max_prior_fast_fade_count_72h)
            | prior_fast_fades.isna()
        )
    if config.min_start_lower_wick_to_range is not None:
        lower_wick = pd.to_numeric(signals["start_lower_wick_to_range"], errors="coerce")
        masks["start_lower_wick_below_min"] = lower_wick.le(config.min_start_lower_wick_to_range) | lower_wick.isna()
    if config.max_start_upper_wick_to_range is not None:
        upper_wick = pd.to_numeric(signals["start_upper_wick_to_range"], errors="coerce")
        masks["start_upper_wick_above_max"] = upper_wick.gt(config.max_start_upper_wick_to_range) | upper_wick.isna()
    return masks


def build_red_flag_summary(candidates: pd.DataFrame, *, config: AnomalyBacktestConfig) -> pd.DataFrame:
    config = _apply_red_flag_profile(config)
    if candidates.empty:
        return pd.DataFrame(
            [{"red_flag": "__total_candidates__", "rows": 0, "share": 0.0, "profile": config.red_flag_profile}]
        )
    masks = _red_flag_violation_masks(candidates, config=config)
    rows: list[dict[str, object]] = [
        {
            "red_flag": "__total_candidates__",
            "rows": int(len(candidates)),
            "share": 1.0,
            "profile": config.red_flag_profile,
        }
    ]
    for name, mask in masks.items():
        count = int(mask.fillna(False).sum())
        rows.append(
            {
                "red_flag": name,
                "rows": count,
                "share": _safe_divide_value(count, len(candidates)),
                "profile": config.red_flag_profile,
            }
        )
    if masks:
        any_mask = pd.Series(False, index=candidates.index)
        for mask in masks.values():
            any_mask |= mask.fillna(False)
        count = int(any_mask.sum())
        rows.append(
            {
                "red_flag": "__any_red_flag__",
                "rows": count,
                "share": _safe_divide_value(count, len(candidates)),
                "profile": config.red_flag_profile,
            }
        )
    return pd.DataFrame(rows)


def _emit_progress_1pct(
    *,
    label: str,
    done: int,
    total: int,
    started_at: float,
    next_progress_pct: int,
) -> int:
    if total <= 0:
        return next_progress_pct
    current_pct = min(100, max(0, int((100.0 * done / total) + 0.5)))
    if current_pct >= next_progress_pct or done == total:
        _emit_progress(label=label, done=done, total=total, started_at=started_at)
        return current_pct + 1
    return next_progress_pct


def _write_artifact_frames(
    frames: Iterable[tuple[Path, pd.DataFrame]],
    *,
    progress_label: str,
    timing_rows: list[dict[str, object]] | None = None,
    timing_scope: str | None = None,
) -> None:
    frame_list = list(frames)
    started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, (path, frame) in enumerate(frame_list, start=1):
        write_started_at = time.monotonic()
        status = "ok"
        error = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
        except Exception as exc:
            status = f"error:{type(exc).__name__}"
            error = str(exc)
            raise
        finally:
            file_size_bytes = path.stat().st_size if path.exists() else 0
            append_speed_diagnostic(
                timing_rows,
                stage="artifact_write",
                scope=timing_scope or progress_label,
                seconds=time.monotonic() - write_started_at,
                status=status,
                output_rows=len(frame),
                item_count=1,
                extra={
                    "path": str(path),
                    "file_name": path.name,
                    "columns": int(len(frame.columns)),
                    "file_size_bytes": int(file_size_bytes),
                    "error": error,
                },
            )
        next_progress_pct = _emit_progress_1pct(
            label=progress_label,
            done=processed_count,
            total=len(frame_list),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )



def _write_prepump_context_artifacts(
    *,
    config: AnomalyBacktestConfig,
    output_dir: Path,
) -> None:
    status_path = output_dir / "runner_fader_prepump_run_status.csv"
    if not config.write_prepump_context:
        _write_artifact_frames(
            [
                (
                    status_path,
                    pd.DataFrame(
                        [
                            {
                                "status": "disabled",
                                "reason": "write_prepump_context_false",
                                "output_dir": str(output_dir),
                            }
                        ]
                    ),
                )
            ],
            progress_label="anomaly artifacts: prepump context status",
        )
        return
    try:
        write_runner_fader_prepump_context(
            PrepumpContextConfig(
                cache_dir=config.lab_config.cache_dir,
                output_dir=output_dir,
                artifact_dirs=(output_dir,),
                context_timeframe=str(config.prepump_context_timeframe),
                windows=parse_prepump_windows(str(config.prepump_context_windows)),
                min_coverage_ratio=float(config.prepump_context_min_coverage_ratio),
            )
        )
    except Exception as exc:
        _write_artifact_frames(
            [
                (
                    status_path,
                    pd.DataFrame(
                        [
                            {
                                "status": "error",
                                "reason": type(exc).__name__,
                                "message": str(exc),
                                "output_dir": str(output_dir),
                                "cache_dir": str(config.lab_config.cache_dir),
                                "context_timeframe": str(config.prepump_context_timeframe),
                                "windows": str(config.prepump_context_windows),
                            }
                        ]
                    ),
                )
            ],
            progress_label="anomaly artifacts: prepump context status",
        )
        return
    _write_artifact_frames(
        [
            (
                status_path,
                pd.DataFrame(
                    [
                        {
                            "status": "ok",
                            "reason": "written",
                            "output_dir": str(output_dir),
                            "cache_dir": str(config.lab_config.cache_dir),
                            "context_timeframe": str(config.prepump_context_timeframe),
                            "windows": str(config.prepump_context_windows),
                        }
                    ]
                ),
            )
        ],
        progress_label="anomaly artifacts: prepump context status",
    )

def _sanitize_file_part(value: object) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))
    return cleaned.strip("._") or "item"


def _timestamp_to_utc(timestamp_ms: int | float) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _market_entry_reject_audit(
    *,
    entry_ts: int,
    raw_entry_price: float,
    entry_price: float,
    drift_pct: float,
    abs_drift_pct: float,
    actual_risk_at_signal_stop: float,
    rr_to_signal_tp1: float,
    signal_tp1_price: float,
    config: AnomalyBacktestConfig,
) -> dict[str, object]:
    return {
        "rejected_market_entry_timestamp_ms": int(entry_ts),
        "rejected_market_entry_timestamp_utc": _timestamp_to_utc(entry_ts),
        "rejected_market_entry_raw_price": float(raw_entry_price),
        "rejected_market_entry_price": float(entry_price),
        "entry_slippage_pct": float(config.entry_slippage_pct),
        "market_entry_drift_pct": float(drift_pct),
        "market_entry_abs_drift_pct": float(abs_drift_pct),
        "max_market_entry_drift_pct": float(config.max_market_entry_drift_pct),
        "market_entry_rr_after_latency": float(rr_to_signal_tp1),
        "min_market_rr_to_signal_tp1": float(config.min_market_rr_to_signal_tp1),
        "signal_tp1_price": float(signal_tp1_price),
        "actual_market_risk_at_signal_stop": float(actual_risk_at_signal_stop),
    }


def _safe_float(value: object) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(resolved):
        return None
    return resolved


def _safe_int(value: object) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _timeframe_to_milliseconds(timeframe: str) -> int:
    match = re.fullmatch(r"(\d+)([smhdw])", str(timeframe))
    if match is None:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    amount = int(match.group(1))
    unit = match.group(2)
    seconds_by_unit = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return amount * seconds_by_unit[unit] * 1000


def _with_ohlcv_availability_columns(frame: pd.DataFrame, *, timeframe: str) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns:
        return frame
    result = frame.copy()
    timestamps = pd.to_numeric(result["timestamp"], errors="coerce")
    timeframe_ms = _timeframe_to_milliseconds(timeframe)
    result["candle_open_timestamp_ms"] = timestamps
    result["candle_close_timestamp_ms"] = timestamps + timeframe_ms
    result["available_timestamp_ms"] = result["candle_close_timestamp_ms"]
    return result


def _slice_ohlcv_asof_window(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    """Slice OHLCV by open start and close/availability end.

    Exchange OHLCV timestamps are candle opens.  The candle's high/low/close and
    flow are not available until the candle closes, so the end boundary must be
    checked against `available_timestamp_ms`, not `timestamp`.
    """
    if frame.empty or "timestamp" not in frame.columns:
        return frame.copy()
    if "available_timestamp_ms" in frame.columns:
        prepared = frame
    else:
        prepared = _with_ohlcv_availability_columns(frame, timeframe=timeframe)
    timestamps = pd.to_numeric(prepared["timestamp"], errors="coerce")
    available = pd.to_numeric(prepared["available_timestamp_ms"], errors="coerce")
    return prepared.loc[
        timestamps.ge(int(start_timestamp_ms))
        & available.le(int(end_timestamp_ms))
    ].copy()


def _row_available_timestamp_ms(row: pd.Series, *, timeframe: str) -> int:
    value = _safe_int(row.get("available_timestamp_ms"))
    if value is not None:
        return value
    return int(row["timestamp"]) + _timeframe_to_milliseconds(timeframe)


def _effective_setup_timeframe(config: AnomalyBacktestConfig) -> str:
    return str(config.setup_timeframe or config.lab_config.timeframe)


def _effective_entry_timeframe(config: AnomalyBacktestConfig) -> str:
    return str(config.entry_timeframe or config.lab_config.timeframe)


def _symbol_from_cache_symbol_dir(symbol_dir: Path) -> str:
    from urllib.parse import unquote

    return unquote(symbol_dir.name)


def _normalized_symbol_tuple(symbols: Iterable[str] | None) -> tuple[str, ...]:
    if symbols is None:
        return ()
    normalized = {
        normalize_symbol(str(symbol))
        for symbol in symbols
        if str(symbol).strip()
    }
    return tuple(sorted(symbol for symbol in normalized if symbol))


def _universe_symbol_scope(symbols: Iterable[str] | None) -> str:
    return "explicit_symbols" if _normalized_symbol_tuple(symbols) else "cache_snapshot_scan"


def _universe_contract_frame(*, symbols: Iterable[str] | None) -> pd.DataFrame:
    requested_symbols = _normalized_symbol_tuple(symbols)
    scope = "explicit_symbols" if requested_symbols else "cache_snapshot_scan"
    cache_snapshot = scope == "cache_snapshot_scan"
    return pd.DataFrame([
        {
            "universe_symbol_scope": scope,
            "universe_requested_symbols_count": int(len(requested_symbols)),
            "universe_requested_symbols_normalized": requested_symbols,
            "historical_listing_snapshot_available": False,
            "survivorship_bias_risk": bool(cache_snapshot),
            "universe_contract_note": (
                "explicit caller-provided symbol scope"
                if requested_symbols
                else "current local cache snapshot; not an as-of historical listing universe"
            ),
        }
    ])


def _safe_divide_value(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return float(numerator / denominator)


def _nice_market_round_step(*, reference_price: float, movement: float) -> float:
    if not np.isfinite(reference_price) or reference_price <= 0.0:
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
    if not np.isfinite(base_tp1_price) or base_tp1_price <= 0.0:
        return base_tp1_price, float("nan")
    step = _nice_market_round_step(reference_price=reference_price, movement=movement)
    if not np.isfinite(step) or step <= 0.0:
        return base_tp1_price, float("nan")
    rounded = math.ceil((base_tp1_price - step * 1e-9) / step) * step
    tolerance = max(abs(float(base_tp1_price)) * 1e-12, step * 1e-9)
    if rounded <= base_tp1_price + tolerance:
        rounded += step
    decimals = max(0, int(math.ceil(-math.log10(step))) + 2) if step < 1.0 else 8
    return round(float(rounded), min(decimals, 12)), float(step)

def _prior_up_down_whipsaw_to_impulse_range(baseline: pd.DataFrame, *, impulse_range: float) -> float:
    if baseline.empty or not np.isfinite(impulse_range) or impulse_range <= 0.0:
        return float("nan")
    highs = baseline["high"].astype(float)
    lows = baseline["low"].astype(float)
    high_pos = int(highs.to_numpy().argmax()) if not highs.empty else -1
    if high_pos < 0:
        return float("nan")
    high_value = float(highs.iloc[high_pos])
    low_before_high = float(lows.iloc[: high_pos + 1].min())
    low_after_high = float(lows.iloc[high_pos:].min())
    prior_up_leg = high_value - low_before_high
    prior_down_leg = high_value - low_after_high
    up_ratio = _safe_divide_value(prior_up_leg, impulse_range)
    down_ratio = _safe_divide_value(prior_down_leg, impulse_range)
    if not np.isfinite(up_ratio) or not np.isfinite(down_ratio):
        return float("nan")
    return float(min(up_ratio, down_ratio))


def _post_htf_ltf_left_context_ms(config: AnomalyBacktestConfig) -> int:
    if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING)) not in _POST_HTF_CLOSE_PAIR_MODES:
        return 0
    setup_ms = _timeframe_to_milliseconds(_effective_setup_timeframe(config))
    entry_ms = _timeframe_to_milliseconds(_effective_entry_timeframe(config))
    if setup_ms <= 0 or entry_ms <= 0 or entry_ms >= setup_ms:
        return 0
    return int(max(0, int(config.lab_config.baseline_candles)) * setup_ms)


def _entry_segment_has_full_range_coverage(
    entry_segment: pd.DataFrame,
    *,
    range_start_ms: int,
    range_end_exclusive_ms: int,
    entry_ms: int,
) -> bool:
    if entry_ms <= 0 or int(range_end_exclusive_ms) <= int(range_start_ms):
        return False
    expected_count = int((int(range_end_exclusive_ms) - int(range_start_ms)) // int(entry_ms))
    if expected_count <= 0:
        return False
    if len(entry_segment) != expected_count:
        return False
    if "timestamp" not in entry_segment.columns:
        return False
    timestamps = pd.to_numeric(entry_segment["timestamp"], errors="coerce")
    if timestamps.isna().any():
        return False
    expected = np.arange(int(range_start_ms), int(range_end_exclusive_ms), int(entry_ms), dtype=np.int64)
    actual = timestamps.astype("int64").to_numpy()
    return bool(len(actual) == len(expected) and np.array_equal(actual, expected))


def _aggregate_ohlcv_to_candle(frame: pd.DataFrame, *, timestamp_ms: int) -> pd.Series | None:
    if frame.empty:
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


def _aggregate_entry_frame_to_setup_frame(entry_frame: pd.DataFrame, *, setup_ms: int) -> pd.DataFrame:
    if entry_frame.empty or setup_ms <= 0 or "timestamp" not in entry_frame.columns:
        return pd.DataFrame()
    required = ("open", "high", "low", "close", "quote_volume", "number_of_trades")
    if any(column not in entry_frame.columns for column in required):
        return pd.DataFrame()
    frame = entry_frame.copy().sort_values("timestamp")
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame.dropna(subset=["timestamp"], inplace=True)
    if frame.empty:
        return pd.DataFrame()
    frame["setup_timestamp"] = (frame["timestamp"].astype("int64") // int(setup_ms)) * int(setup_ms)
    aggregations: dict[str, tuple[str, str]] = {
        "timestamp": ("setup_timestamp", "first"),
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "quote_volume": ("quote_volume", "sum"),
        "number_of_trades": ("number_of_trades", "sum"),
    }
    if "volume" in frame.columns:
        aggregations["volume"] = ("volume", "sum")
    if "taker_buy_quote_volume" in frame.columns:
        aggregations["taker_buy_quote_volume"] = ("taker_buy_quote_volume", "sum")
    result = frame.groupby("setup_timestamp", as_index=False).agg(**aggregations)
    result.sort_values("timestamp", inplace=True)
    result.drop_duplicates("timestamp", keep="last", inplace=True)
    result.reset_index(drop=True, inplace=True)
    if "ema20" not in result.columns and "close" in result.columns:
        result["ema20"] = result["close"].astype(float).ewm(span=20, adjust=False).mean()
    return result


def _cache_symbol_dir_name(symbol: str) -> str:
    return quote(symbol, safe="")


def _read_symbol_frame(cache_dir: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    path = cache_dir / _cache_symbol_dir_name(symbol) / timeframe / "data.parquet"
    try:
        from data.storage.parquet_storage import ParquetStorage
        from domain.enums.timeframe import Timeframe

        load_result = ParquetStorage(cache_dir).load_result(symbol, Timeframe(str(timeframe)))
        if not load_result.ok:
            raise FileNotFoundError(path)
        frame = load_result.frame
    except ValueError:
        if not path.exists():
            raise FileNotFoundError(path)
        frame = pd.read_parquet(path)
    frame.sort_values("timestamp", inplace=True)
    frame.drop_duplicates("timestamp", keep="last", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    frame = _with_ohlcv_availability_columns(frame, timeframe=timeframe)
    if "ema20" not in frame.columns and "close" in frame.columns:
        frame["ema20"] = frame["close"].astype(float).ewm(span=20, adjust=False).mean()
    return frame


def _binance_futures_market_id(symbol: str) -> str:
    compact = str(symbol).split(":")[0].replace("/", "")
    return compact.upper()


def _read_symbol_frame_optional(cache_dir: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    try:
        return _read_symbol_frame(cache_dir, symbol, timeframe)
    except FileNotFoundError:
        return pd.DataFrame()



def _trusted_aggtrade_window_covered(frame: pd.DataFrame, *, start_timestamp_ms: int, end_timestamp_ms: int) -> bool:
    """Return True only when trusted 1s aggTrade metadata covers the whole requested window.

    Missing trade seconds are valid for quiet markets, so coverage must be checked
    through P378 aggTrade coverage intervals, not by requiring one OHLCV row per
    second.  Old 1s caches without full-bucket coverage metadata must be treated
    as untrusted and refetched.
    """
    if frame.empty:
        return False
    version = _first_non_empty_string(frame, "aggregation_version")
    if version != AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION:
        return False
    required = {"aggtrade_coverage_start_timestamp_ms", "aggtrade_coverage_end_timestamp_ms"}
    if not required.issubset(frame.columns):
        return False
    intervals_frame = frame.loc[
        frame["aggtrade_coverage_start_timestamp_ms"].notna()
        & frame["aggtrade_coverage_end_timestamp_ms"].notna(),
        ["aggtrade_coverage_start_timestamp_ms", "aggtrade_coverage_end_timestamp_ms"],
    ].drop_duplicates()
    if intervals_frame.empty:
        return False
    intervals = [
        (int(row["aggtrade_coverage_start_timestamp_ms"]), int(row["aggtrade_coverage_end_timestamp_ms"]))
        for _, row in intervals_frame.iterrows()
    ]
    return _coverage_intervals_cover_bucket(
        intervals,
        bucket_start_ms=int(start_timestamp_ms),
        bucket_end_ms=int(end_timestamp_ms),
    )


def _ensure_latency_1s_cache(
    cache_dir: Path,
    symbol: str,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    frame = _read_symbol_frame_optional(cache_dir, symbol, "1s")
    if _trusted_aggtrade_window_covered(
        frame,
        start_timestamp_ms=int(start_timestamp_ms),
        end_timestamp_ms=int(end_timestamp_ms),
    ):
        return frame

    market_id = _binance_futures_market_id(symbol)
    all_rows: list[dict[str, object]] = []
    chunk_start = int(start_timestamp_ms)
    endpoint = "https://fapi.binance.com/fapi/v1/aggTrades"
    while chunk_start <= int(end_timestamp_ms):
        chunk_end = min(int(end_timestamp_ms), chunk_start + 3_600_000 - 1)
        cursor = int(chunk_start)
        while cursor <= chunk_end:
            params = urllib.parse.urlencode(
                {
                    "symbol": market_id,
                    "startTime": int(cursor),
                    "endTime": int(chunk_end),
                    "limit": 1000,
                }
            )
            batch = _fetch_binance_aggtrades_json(f"{endpoint}?{params}")
            if not batch:
                break
            all_rows.extend(dict(row) for row in batch)
            last_ts = int(batch[-1].get("T") or batch[-1].get("time") or cursor)
            if last_ts < cursor or len(batch) < 1000:
                break
            cursor = last_ts + 1
            time.sleep(0.02)
        chunk_start = chunk_end + 1

    if not all_rows:
        return frame

    from data.storage.parquet_storage import ParquetStorage
    from domain.enums.timeframe import Timeframe
    from research_tools.anomaly_aggtrade_cache import aggregate_aggtrades_to_ohlcv_frame

    fetched = aggregate_aggtrades_to_ohlcv_frame(
        pd.DataFrame(all_rows),
        timeframe_ms=1000,
        start_timestamp_ms=int(start_timestamp_ms),
        end_timestamp_ms=int(end_timestamp_ms),
    )
    if fetched.empty:
        return frame
    fetched = fetched.copy()
    fetched["aggregation_source"] = "binance_futures_aggTrades"
    fetched["aggregation_target_timeframe"] = "1s"
    fetched["aggregation_version"] = LATENCY_1S_BACKFILL_VERSION
    ParquetStorage(cache_dir).save_incremental_delta(symbol, Timeframe.S1, fetched)
    merged = pd.concat([frame, fetched], ignore_index=True, sort=False) if not frame.empty else fetched
    merged.sort_values("timestamp", inplace=True)
    merged.drop_duplicates("timestamp", keep="last", inplace=True)
    merged.reset_index(drop=True, inplace=True)
    merged = _with_ohlcv_availability_columns(merged, timeframe="1s")
    if "ema20" not in merged.columns and "close" in merged.columns:
        merged["ema20"] = merged["close"].astype(float).ewm(span=20, adjust=False).mean()
    return merged

def _coverage_intervals_cover_bucket(intervals: list[tuple[int, int]], *, bucket_start_ms: int, bucket_end_ms: int) -> bool:
    if not intervals:
        return False
    covered_until = int(bucket_start_ms) - 1
    for start_ms, end_ms in sorted(intervals):
        start_ms = int(start_ms)
        end_ms = int(end_ms)
        if end_ms < int(bucket_start_ms) or start_ms > int(bucket_end_ms):
            continue
        if start_ms > covered_until + 1:
            return False
        covered_until = max(covered_until, end_ms)
        if covered_until >= int(bucket_end_ms):
            return True
    return False


def _aggregate_frame_to_timeframe(frame: pd.DataFrame, *, timeframe_ms: int) -> pd.DataFrame:
    if frame.empty or timeframe_ms <= 0:
        return pd.DataFrame()
    required = {"timestamp", "open", "high", "low", "close"}
    if required.difference(frame.columns):
        return pd.DataFrame()
    prepared = frame.copy()
    numeric_columns = (
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
        "aggtrade_coverage_start_timestamp_ms",
        "aggtrade_coverage_end_timestamp_ms",
    )
    for column in numeric_columns:
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"])
    if prepared.empty:
        return pd.DataFrame()
    prepared["timestamp"] = prepared["timestamp"].astype("int64")
    prepared.sort_values("timestamp", inplace=True)
    prepared.drop_duplicates("timestamp", keep="last", inplace=True)
    prepared["bucket"] = (prepared["timestamp"] // int(timeframe_ms)) * int(timeframe_ms)
    coverage_by_bucket: dict[int, bool] = {}
    coverage_columns = {"aggtrade_coverage_start_timestamp_ms", "aggtrade_coverage_end_timestamp_ms"}
    if coverage_columns.issubset(prepared.columns):
        intervals_frame = prepared.loc[
            prepared["aggtrade_coverage_start_timestamp_ms"].notna()
            & prepared["aggtrade_coverage_end_timestamp_ms"].notna(),
            ["aggtrade_coverage_start_timestamp_ms", "aggtrade_coverage_end_timestamp_ms"],
        ].drop_duplicates()
        intervals = [
            (int(row["aggtrade_coverage_start_timestamp_ms"]), int(row["aggtrade_coverage_end_timestamp_ms"]))
            for _, row in intervals_frame.iterrows()
        ]
        for start_ms, end_ms in intervals:
            for bucket_start in _contained_bucket_starts(int(start_ms), int(end_ms), timeframe_ms=int(timeframe_ms)):
                bucket_end = int(bucket_start) + int(timeframe_ms) - 1
                coverage_by_bucket[int(bucket_start)] = _coverage_intervals_cover_bucket(
                    intervals,
                    bucket_start_ms=int(bucket_start),
                    bucket_end_ms=bucket_end,
                )
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_volume", "taker_buy_quote_volume"):
        if column in prepared.columns:
            aggregation[column] = "sum"
    aggregated = prepared.groupby("bucket", as_index=False).agg(aggregation)
    aggregated["bucket"] = pd.to_numeric(aggregated["bucket"], errors="coerce").astype("int64")
    if coverage_by_bucket:
        rows_by_bucket = {int(row["bucket"]): row.to_dict() for _, row in aggregated.iterrows()}
        completed_rows: list[dict[str, object]] = []
        last_close = float("nan")
        for bucket in sorted(bucket for bucket, covered in coverage_by_bucket.items() if covered):
            if bucket in rows_by_bucket:
                row = dict(rows_by_bucket[bucket])
                row["aggtrade_zero_trade_bucket"] = False
                close = float(row.get("close", float("nan")))
                if np.isfinite(close):
                    last_close = close
                completed_rows.append(row)
                continue
            if not np.isfinite(last_close):
                continue
            row = {
                "bucket": int(bucket),
                "timestamp": int(bucket),
                "open": float(last_close),
                "high": float(last_close),
                "low": float(last_close),
                "close": float(last_close),
                "aggtrade_zero_trade_bucket": True,
            }
            for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_volume", "taker_buy_quote_volume"):
                if column in aggregation:
                    row[column] = 0.0
            completed_rows.append(row)
        if not completed_rows:
            return pd.DataFrame()
        aggregated = pd.DataFrame(completed_rows)
        aggregated["aggtrade_coverage_verified"] = True
        aggregated["aggtrade_materialization_model"] = "full_covered_buckets_with_zero_trade_candles"
    else:
        aggregated["aggtrade_zero_trade_bucket"] = False
        aggregated["aggtrade_materialization_model"] = "trade_buckets_only_no_coverage_metadata"
    aggregated["timestamp"] = pd.to_numeric(aggregated["bucket"], errors="coerce").astype("int64")
    aggregated.drop(columns=["bucket"], inplace=True)
    aggregated.sort_values("timestamp", inplace=True)
    aggregated.reset_index(drop=True, inplace=True)
    timestamps = pd.to_numeric(aggregated["timestamp"], errors="coerce")
    aggregated["candle_open_timestamp_ms"] = timestamps
    aggregated["candle_close_timestamp_ms"] = timestamps + int(timeframe_ms)
    aggregated["available_timestamp_ms"] = aggregated["candle_close_timestamp_ms"]
    if "ema20" not in aggregated.columns and "close" in aggregated.columns:
        aggregated["ema20"] = aggregated["close"].astype(float).ewm(span=20, adjust=False).mean()
    return aggregated


def _first_non_empty_string(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    values = frame[column].dropna().astype(str).str.strip()
    values = values.loc[values.ne("")]
    return str(values.iloc[0]) if not values.empty else ""


def _materialized_entry_flow_source(frame: pd.DataFrame, *, entry_timeframe: str) -> str:
    if "aggregation_source_timeframe" not in frame.columns:
        return "cached_ohlcv_missing_aggregation_metadata"
    source = _first_non_empty_string(frame, "aggregation_source_timeframe")
    version = _first_non_empty_string(frame, "aggregation_version")
    if not source:
        return "cached_ohlcv_missing_aggregation_metadata"
    if source == "1s" and version == _MATERIALIZED_SUBMINUTE_CACHE_VERSION:
        return f"cached_1s_aggregated_to_{entry_timeframe}"
    if source == "aggTrades" and version == DIRECT_TARGET_AGGTRADE_CACHE_VERSION:
        return f"cached_aggTrades_direct_to_{entry_timeframe}"
    if source == "1s" and not version:
        return f"cached_1s_aggregated_to_{entry_timeframe}_missing_version"
    return f"cached_{source}_materialized_to_{entry_timeframe}_unknown_version"


def _flow_source_is_untrusted(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    return any(marker in text for marker in ("missing", "unknown", "proxy"))


def _flow_cache_validation_error(
    frame: pd.DataFrame,
    *,
    cache_timeframe: str,
    entry_timeframe: str,
) -> str | None:
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if entry_ms >= 60_000:
        return None
    cache_tf = str(cache_timeframe)
    if cache_tf == "1s":
        version = _first_non_empty_string(frame, "aggregation_version")
        if version != AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION:
            return f"untrusted_1s_entry_flow_cache_version:{version or 'missing'}"
        return None
    version = _first_non_empty_string(frame, "aggregation_version")
    source = _first_non_empty_string(frame, "aggregation_source_timeframe")
    if source == "1s" and version == _MATERIALIZED_SUBMINUTE_CACHE_VERSION:
        return None
    if source == "aggTrades" and version == DIRECT_TARGET_AGGTRADE_CACHE_VERSION:
        return None
    return (
        "untrusted_materialized_entry_flow_cache:"
        f"source={source or 'missing'}:version={version or 'missing'}"
    )



def _cache_data_path(cache_dir: Path, symbol: str, timeframe: str) -> Path:
    return cache_dir / _cache_symbol_dir_name(symbol) / str(timeframe) / "data.parquet"


def _target_ltf_coverage_index_path(cache_dir: Path, symbol: str, timeframe: str) -> Path:
    return _cache_data_path(cache_dir, symbol, timeframe).parent / DIRECT_TARGET_AGGTRADE_COVERAGE_INDEX_FILE


def _read_parquet_columns(path: Path, columns: Iterable[str]) -> pd.DataFrame:
    """Read a narrow parquet projection for cache coverage checks."""

    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, columns=list(dict.fromkeys(str(column) for column in columns)))
    except Exception:
        return pd.DataFrame()


def _read_entry_cache_metadata_with_delta(
    cache_dir: Path,
    symbol: str,
    *,
    target_timeframe: str,
    columns: Iterable[str],
) -> pd.DataFrame:
    path = _cache_data_path(cache_dir, symbol, target_timeframe)
    frames: list[pd.DataFrame] = []
    base = _read_parquet_columns(path, columns)
    if not base.empty:
        frames.append(base)
    index = _read_parquet_columns(_target_ltf_coverage_index_path(cache_dir, symbol, target_timeframe), columns)
    if not index.empty:
        frames.append(index)
        delta_dir = None
    else:
        delta_dir = path.parent / "delta"
    if delta_dir is not None and delta_dir.exists():
        delta_files = [delta_path for delta_path in sorted(delta_dir.glob("*.parquet")) if delta_path.is_file()]
        if delta_files:
            try:
                delta_dataset = pd.read_parquet(delta_dir, columns=list(dict.fromkeys(str(column) for column in columns)))
            except Exception:
                delta_dataset = pd.DataFrame()
                for delta_path in delta_files:
                    delta = _read_parquet_columns(delta_path, columns)
                    if not delta.empty:
                        frames.append(delta)
            else:
                if not delta_dataset.empty:
                    frames.append(delta_dataset)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True, sort=False)
    if "timestamp" not in merged.columns:
        return merged
    timestamps = pd.to_numeric(merged["timestamp"], errors="coerce")
    merged = merged.loc[timestamps.notna()].copy()
    if merged.empty:
        return merged
    merged["timestamp"] = pd.to_numeric(merged["timestamp"], errors="coerce").astype("int64")
    return merged.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _write_direct_target_ltf_coverage_index(
    cache_dir: Path,
    symbol: str,
    target_timeframe: str,
    frame: pd.DataFrame,
) -> None:
    if frame.empty or "timestamp" not in frame.columns:
        return
    columns = ["timestamp", "aggregation_source_timeframe", "aggregation_version", "aggtrade_coverage_verified"]
    available = [column for column in columns if column in frame.columns]
    if "timestamp" not in available:
        return
    incoming = frame.loc[:, available].copy()
    incoming["timestamp"] = pd.to_numeric(incoming["timestamp"], errors="coerce")
    incoming = incoming.loc[incoming["timestamp"].notna()].copy()
    if incoming.empty:
        return
    incoming["timestamp"] = incoming["timestamp"].astype("int64")
    index_path = _target_ltf_coverage_index_path(cache_dir, symbol, target_timeframe)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_parquet_columns(index_path, columns)
    frames = [existing, incoming] if not existing.empty else [incoming]
    merged = pd.concat(frames, ignore_index=True, sort=False)
    merged["timestamp"] = pd.to_numeric(merged["timestamp"], errors="coerce")
    merged = (
        merged.loc[merged["timestamp"].notna()]
        .copy()
        .astype({"timestamp": "int64"})
        .drop_duplicates("timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    tmp_path = index_path.with_suffix(index_path.suffix + ".tmp")
    merged.to_parquet(tmp_path, index=False)
    tmp_path.replace(index_path)


def _write_empty_direct_target_ltf_coverage_index(
    cache_dir: Path,
    symbol: str,
    target_timeframe: str,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> int:
    target_ms = _timeframe_to_milliseconds(target_timeframe)
    buckets = list(_contained_bucket_starts(int(start_timestamp_ms), int(end_timestamp_ms), timeframe_ms=target_ms))
    if not buckets:
        return 0
    frame = pd.DataFrame(
        {
            "timestamp": buckets,
            "aggregation_source_timeframe": "aggTrades",
            "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
            "aggtrade_coverage_verified": True,
        }
    )
    _write_direct_target_ltf_coverage_index(cache_dir, symbol, target_timeframe, frame)
    return int(len(frame))


def _cached_timestamp_row_count(path: Path) -> int:
    frame = _read_parquet_columns(path, ["timestamp"])
    return int(len(frame)) if not frame.empty else 0


def _trusted_aggtrade_window_covered_from_cache(
    cache_dir: Path,
    symbol: str,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> bool:
    metadata = _read_parquet_columns(
        _cache_data_path(cache_dir, symbol, "1s"),
        [
            "aggregation_version",
            "aggtrade_coverage_start_timestamp_ms",
            "aggtrade_coverage_end_timestamp_ms",
        ],
    )
    if metadata.empty:
        return False
    if _first_non_empty_string(metadata, "aggregation_version") != AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION:
        return False
    required = {"aggtrade_coverage_start_timestamp_ms", "aggtrade_coverage_end_timestamp_ms"}
    if not required.issubset(metadata.columns):
        return False
    intervals_frame = metadata.loc[
        metadata["aggtrade_coverage_start_timestamp_ms"].notna()
        & metadata["aggtrade_coverage_end_timestamp_ms"].notna(),
        ["aggtrade_coverage_start_timestamp_ms", "aggtrade_coverage_end_timestamp_ms"],
    ].drop_duplicates()
    if intervals_frame.empty:
        return False
    intervals = [
        (int(row["aggtrade_coverage_start_timestamp_ms"]), int(row["aggtrade_coverage_end_timestamp_ms"]))
        for _, row in intervals_frame.iterrows()
    ]
    return _coverage_intervals_cover_bucket(
        intervals,
        bucket_start_ms=int(start_timestamp_ms),
        bucket_end_ms=int(end_timestamp_ms),
    )


def _contained_bucket_starts(start_ms: int, end_ms: int, *, timeframe_ms: int) -> range:
    if timeframe_ms <= 0 or int(end_ms) < int(start_ms):
        return range(0)
    first = ((int(start_ms) + int(timeframe_ms) - 1) // int(timeframe_ms)) * int(timeframe_ms)
    last = ((int(end_ms) - int(timeframe_ms) + 1) // int(timeframe_ms)) * int(timeframe_ms)
    if last < first:
        return range(0)
    return range(first, last + int(timeframe_ms), int(timeframe_ms))


def _trusted_materialized_entry_cache_covers_windows(
    cache_dir: Path,
    symbol: str,
    *,
    target_timeframe: str,
    windows: Iterable[tuple[int, int]],
    metadata: pd.DataFrame | None = None,
) -> bool:
    target_ms = _timeframe_to_milliseconds(target_timeframe)
    if target_ms <= 0:
        return False
    expected: set[int] = set()
    for start_ms, end_ms in _merge_targeted_timestamp_windows(windows):
        expected.update(_contained_bucket_starts(int(start_ms), int(end_ms), timeframe_ms=target_ms))
    if not expected:
        return False
    if metadata is None:
        metadata = _read_entry_cache_metadata_with_delta(
            cache_dir,
            symbol,
            target_timeframe=target_timeframe,
            columns=[
                "timestamp",
                "aggregation_source_timeframe",
                "aggregation_version",
                "aggtrade_coverage_verified",
            ],
        )
    if metadata.empty:
        return False
    validation_error = _flow_cache_validation_error(
        metadata,
        cache_timeframe=target_timeframe,
        entry_timeframe=target_timeframe,
    )
    if validation_error is not None:
        return False
    timestamps = pd.to_numeric(metadata.get("timestamp"), errors="coerce").dropna().astype("int64")
    if timestamps.empty:
        return False
    present = set(int(value) for value in timestamps.to_numpy())
    if not expected.issubset(present):
        return False
    if "aggtrade_coverage_verified" in metadata.columns:
        verified_rows = metadata.loc[timestamps.index, ["timestamp", "aggtrade_coverage_verified"]].copy()
        verified_rows["timestamp"] = pd.to_numeric(verified_rows["timestamp"], errors="coerce")
        verified_rows = verified_rows.loc[verified_rows["timestamp"].isin(expected)]
        if verified_rows.empty:
            return False
        if not _truthy_mask(verified_rows["aggtrade_coverage_verified"]).all():
            return False
    return True


def _trusted_materialized_entry_cache_missing_intervals(
    cache_dir: Path,
    symbol: str,
    *,
    target_timeframe: str,
    window_start_ms: int,
    window_end_ms: int,
    metadata: pd.DataFrame | None = None,
) -> list[tuple[int, int]]:
    target_ms = _timeframe_to_milliseconds(target_timeframe)
    if target_ms <= 0:
        return [(int(window_start_ms), int(window_end_ms))]
    expected = list(_contained_bucket_starts(int(window_start_ms), int(window_end_ms), timeframe_ms=target_ms))
    if not expected:
        return []
    if metadata is None:
        metadata = _read_entry_cache_metadata_with_delta(
            cache_dir,
            symbol,
            target_timeframe=target_timeframe,
            columns=[
                "timestamp",
                "aggregation_source_timeframe",
                "aggregation_version",
                "aggtrade_coverage_verified",
            ],
        )
    if metadata.empty:
        return _bucket_starts_to_intervals(expected, target_ms=target_ms)
    validation_error = _flow_cache_validation_error(
        metadata,
        cache_timeframe=target_timeframe,
        entry_timeframe=target_timeframe,
    )
    if validation_error is not None:
        return _bucket_starts_to_intervals(expected, target_ms=target_ms)
    timestamps = pd.to_numeric(metadata.get("timestamp"), errors="coerce")
    trusted = metadata.loc[timestamps.notna()].copy()
    if trusted.empty:
        return _bucket_starts_to_intervals(expected, target_ms=target_ms)
    trusted["timestamp"] = pd.to_numeric(trusted["timestamp"], errors="coerce").astype("int64")
    if "aggtrade_coverage_verified" in trusted.columns:
        trusted = trusted.loc[_truthy_mask(trusted["aggtrade_coverage_verified"])]
    present = set(int(value) for value in trusted["timestamp"].to_numpy())
    missing = [int(value) for value in expected if int(value) not in present]
    return _bucket_starts_to_intervals(missing, target_ms=target_ms)


def _bucket_starts_to_intervals(bucket_starts: Iterable[int], *, target_ms: int) -> list[tuple[int, int]]:
    ordered = sorted({int(value) for value in bucket_starts})
    if not ordered:
        return []
    intervals: list[tuple[int, int]] = []
    start = ordered[0]
    prev = ordered[0]
    for value in ordered[1:]:
        if int(value) == int(prev) + int(target_ms):
            prev = int(value)
            continue
        intervals.append((int(start), int(prev) + int(target_ms) - 1))
        start = int(value)
        prev = int(value)
    intervals.append((int(start), int(prev) + int(target_ms) - 1))
    return intervals


def _overlapping_intervals(
    intervals: Iterable[tuple[int, int]],
    cover: tuple[int, int],
) -> list[tuple[int, int]]:
    cover_start, cover_end = int(cover[0]), int(cover[1])
    result: list[tuple[int, int]] = []
    for start, end in intervals:
        overlap_start = max(int(start), cover_start)
        overlap_end = min(int(end), cover_end)
        if overlap_start <= overlap_end:
            result.append((overlap_start, overlap_end))
    return result


def _truthy_mask(values: pd.Series) -> pd.Series:
    def _truthy(value: object) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            pass
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if isinstance(value, (int, float, np.integer, np.floating)):
            return bool(float(value) != 0.0)
        return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "ok"}

    return pd.Series([_truthy(value) for value in values], index=values.index, dtype=bool)


def _candidate_flow_source_mask(candidates: pd.DataFrame) -> pd.Series:
    mask = pd.Series(True, index=candidates.index)
    for column in (
        "levels_trade_count_source",
        "entry_trade_count_source",
        "levels_quote_volume_source",
        "entry_quote_volume_source",
    ):
        if column not in candidates.columns:
            return pd.Series(False, index=candidates.index)
        mask &= ~candidates[column].map(_flow_source_is_untrusted)
    if "entry_timeframe" in candidates.columns:
        entry_ms = candidates["entry_timeframe"].map(lambda value: _timeframe_to_milliseconds(str(value)))
        subminute = entry_ms.lt(60_000)
        entry_sources = candidates["entry_trade_count_source"].astype(str)
        trusted_subminute = (
            entry_sources.str.contains("cached_1s_aggregated_to_", regex=False)
            | entry_sources.str.contains("cached_aggTrades_direct_to_", regex=False)
        )
        mask &= ~subminute | trusted_subminute
    return mask


def _slice_frame_to_timestamp_windows(frame: pd.DataFrame, windows: Iterable[tuple[int, int]]) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns:
        return frame.iloc[0:0].copy()
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce")
    mask = pd.Series(False, index=frame.index)
    for start_ms, end_ms in _merge_targeted_timestamp_windows(windows):
        mask |= timestamps.ge(int(start_ms)) & timestamps.le(int(end_ms))
    return frame.loc[mask].copy()


def _merge_existing_and_materialized_subminute(
    *,
    existing_frame: pd.DataFrame,
    new_frame: pd.DataFrame,
    keep_existing: bool,
) -> pd.DataFrame:
    if new_frame.empty:
        return new_frame.copy()
    if keep_existing and not existing_frame.empty:
        merged = pd.concat([existing_frame, new_frame], ignore_index=True, sort=False)
    else:
        merged = new_frame.copy()
    merged.sort_values("timestamp", inplace=True)
    merged.drop_duplicates("timestamp", keep="last", inplace=True)
    merged.reset_index(drop=True, inplace=True)
    return merged


def materialize_subminute_entry_caches(
    *,
    cache_dir: Path,
    target_timeframes: Iterable[str],
    symbols: Iterable[str] | None = None,
    overwrite: bool = False,
    progress_label: str | None = None,
    intervals_by_symbol: Mapping[str, Iterable[tuple[int, int]]] | None = None,
) -> pd.DataFrame:
    wanted_symbols = set(_normalized_symbol_tuple(symbols))
    targets = tuple(dict.fromkeys(str(timeframe) for timeframe in target_timeframes))
    for target in targets:
        target_ms = _timeframe_to_milliseconds(target)
        if target == "1s" or target_ms <= 0 or target_ms >= 60_000 or target_ms % 1000 != 0:
            raise ValueError(f"target subminute timeframe must be derived from 1s: {target}")
    interval_map: dict[str, list[tuple[int, int]]] = {}
    if intervals_by_symbol is not None:
        for raw_symbol, raw_windows in intervals_by_symbol.items():
            symbol = str(raw_symbol)
            windows = _merge_targeted_timestamp_windows(raw_windows)
            if windows:
                interval_map[symbol] = windows
        if wanted_symbols:
            interval_map = {
                symbol: windows
                for symbol, windows in interval_map.items()
                if normalize_symbol(symbol) in wanted_symbols
            }
        paths = [_cache_data_path(cache_dir, symbol, "1s") for symbol in sorted(interval_map)]
    else:
        paths = sorted(cache_dir.glob("*%2FUSDT%3AUSDT/1s/data.parquet"))
        if wanted_symbols:
            paths = [
                path
                for path in paths
                if normalize_symbol(_symbol_from_cache_symbol_dir(path.parent.parent)) in wanted_symbols
            ]
    rows: list[dict[str, object]] = []
    started_at = time.monotonic()
    next_progress_pct = 0
    total = max(1, len(paths) * max(1, len(targets)))
    done = 0

    def _maybe_emit_progress() -> None:
        nonlocal next_progress_pct
        if progress_label is None:
            return
        current_pct = int(100 * done / total)
        if current_pct >= next_progress_pct or done == total:
            _emit_progress(label=progress_label, done=done, total=total, started_at=started_at)
            next_progress_pct = current_pct + 5

    for path in paths:
        symbol = _symbol_from_cache_symbol_dir(path.parent.parent)
        symbol_windows = interval_map.get(symbol) if intervals_by_symbol is not None else None
        pending_targets: list[str] = []
        for target in targets:
            output_path = _cache_data_path(cache_dir, symbol, target)
            if (
                symbol_windows is not None
                and output_path.exists()
                and not overwrite
                and _trusted_materialized_entry_cache_covers_windows(
                    cache_dir,
                    symbol,
                    target_timeframe=target,
                    windows=symbol_windows,
                )
            ):
                rows.append(
                    {
                        "symbol": symbol,
                        "target_timeframe": target,
                        "source_timeframe": "1s",
                        "status": "exists_covered_requested_intervals",
                        "path": str(output_path),
                    }
                )
                done += 1
                _maybe_emit_progress()
            else:
                pending_targets.append(target)
        if not pending_targets:
            continue
        try:
            source_frame = _read_symbol_frame(cache_dir, symbol, "1s")
            validation_error = _flow_cache_validation_error(
                source_frame,
                cache_timeframe="1s",
                entry_timeframe="1s",
            )
            if validation_error is not None:
                raise ValueError(validation_error)
            if symbol_windows is not None:
                source_frame = _slice_frame_to_timestamp_windows(source_frame, symbol_windows)
                if source_frame.empty:
                    raise ValueError("empty_1s_source_in_requested_intervals")
        except Exception as exc:
            for target in pending_targets:
                rows.append(
                    {
                        "symbol": symbol,
                        "target_timeframe": target,
                        "source_timeframe": "1s",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                done += 1
                _maybe_emit_progress()
            continue
        for target in pending_targets:
            done += 1
            output_path = _cache_data_path(cache_dir, symbol, target)
            existing_error: str | None = None
            existing_frame = pd.DataFrame()
            if output_path.exists() and not overwrite:
                try:
                    existing_frame = _read_symbol_frame(cache_dir, symbol, target)
                    existing_error = _flow_cache_validation_error(
                        existing_frame,
                        cache_timeframe=target,
                        entry_timeframe=target,
                    )
                except Exception as exc:
                    existing_error = f"{type(exc).__name__}: {exc}"
                if intervals_by_symbol is None and existing_error is None:
                    rows.append(
                        {
                            "symbol": symbol,
                            "target_timeframe": target,
                            "source_timeframe": "1s",
                            "status": "exists",
                            "path": str(output_path),
                        }
                    )
                    _maybe_emit_progress()
                    continue
            aggregated = _aggregate_frame_to_timeframe(
                source_frame,
                timeframe_ms=_timeframe_to_milliseconds(target),
            )
            if aggregated.empty:
                rows.append(
                    {
                        "symbol": symbol,
                        "target_timeframe": target,
                        "source_timeframe": "1s",
                        "status": "empty",
                        "path": str(output_path),
                    }
                )
            else:
                aggregated = aggregated.copy()
                aggregated["aggregation_source_timeframe"] = "1s"
                aggregated["aggregation_target_timeframe"] = target
                aggregated["aggregation_version"] = _MATERIALIZED_SUBMINUTE_CACHE_VERSION
                keep_existing = bool(existing_error is None and not overwrite)
                written = _merge_existing_and_materialized_subminute(
                    existing_frame=existing_frame,
                    new_frame=aggregated,
                    keep_existing=keep_existing,
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                written.to_parquet(output_path, index=False)
                rows.append(
                    {
                        "symbol": symbol,
                        "target_timeframe": target,
                        "source_timeframe": "1s",
                        "status": "written_interval" if intervals_by_symbol is not None else "written",
                        "path": str(output_path),
                        "rows": int(len(written)),
                        "new_rows": int(len(aggregated)),
                        "kept_existing_rows": int(len(existing_frame)) if keep_existing else 0,
                        "discarded_existing_reason": "" if keep_existing or existing_frame.empty else str(existing_error or "overwrite_requested"),
                        "min_timestamp_ms": int(written["timestamp"].min()),
                        "max_timestamp_ms": int(written["timestamp"].max()),
                        "min_timestamp_utc": _timestamp_to_utc(int(written["timestamp"].min())),
                        "max_timestamp_utc": _timestamp_to_utc(int(written["timestamp"].max())),
                    }
                )
            _maybe_emit_progress()

    return pd.DataFrame(rows)


def ensure_targeted_aggtrade_subminute_cache(
    *,
    cache_dir: Path,
    windows_by_symbol: Mapping[str, Iterable[tuple[int, int]]],
    target_timeframes: Iterable[str],
    progress_label: str | None = None,
    max_merged_span_ms: int | None = TARGETED_FLOW_MAX_MERGED_SPAN_MS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch true aggTrade 1s only for explicit windows and materialize requested LTF caches.

    This is a shared targeted-cache boundary for research jobs that already know
    the suspicious HTF windows. It does not broaden the universe, does not fetch
    whole-history subminute data, and does not synthesize flow from OHLCV proxies.
    """

    target_timeframes_tuple = tuple(str(tf) for tf in target_timeframes)
    normalized_windows: dict[str, list[tuple[int, int]]] = {}
    raw_windows_by_symbol: dict[str, list[tuple[int, int]]] = {}
    for raw_symbol, raw_windows in windows_by_symbol.items():
        symbol = str(raw_symbol).strip()
        if not symbol or symbol == "__all__":
            continue
        valid_raw = sorted((int(start), int(end)) for start, end in raw_windows if int(start) <= int(end))
        if not valid_raw:
            continue
        raw_windows_by_symbol[symbol] = valid_raw
        merged = _merge_targeted_timestamp_windows(
            valid_raw,
            max_merged_span_ms=max_merged_span_ms,
        )
        if merged:
            normalized_windows[symbol] = merged
    plan_rows: list[dict[str, object]] = []
    raw_windows_total = sum(len(windows) for windows in raw_windows_by_symbol.values())
    merged_windows_total = sum(len(windows) for windows in normalized_windows.values())
    raw_window_ms_total = sum(
        int(end) - int(start) + 1
        for windows in raw_windows_by_symbol.values()
        for start, end in windows
    )
    merged_window_ms_total = sum(
        int(end) - int(start) + 1
        for windows in normalized_windows.values()
        for start, end in windows
    )
    plan_rows.append(
        {
            "symbol": "__all__",
            "status": "window_plan",
            "target_timeframes": ",".join(target_timeframes_tuple),
            "symbols_with_windows": int(len(normalized_windows)),
            "raw_targeted_windows": int(raw_windows_total),
            "merged_targeted_windows": int(merged_windows_total),
            "raw_targeted_window_ms": int(raw_window_ms_total),
            "merged_targeted_window_ms": int(merged_window_ms_total),
            "merge_gap_ms": int(TARGETED_FLOW_MERGE_GAP_MS),
            "max_merged_span_ms": (
                "unbounded" if max_merged_span_ms is None else int(max_merged_span_ms)
            ),
            "merge_policy": (
                "gap_only_unbounded_span"
                if max_merged_span_ms is None
                else "gap_and_max_span"
            ),
            "data_source": "binance_futures_aggTrades_1s_then_materialized_ltf",
        }
    )
    if not normalized_windows:
        return pd.DataFrame(plan_rows), pd.DataFrame([{"status": "not_run", "reason": "no_targeted_windows"}])

    fully_materialized_symbols: set[str] = set()
    for symbol, windows in normalized_windows.items():
        if all(
            _trusted_materialized_entry_cache_covers_windows(
                cache_dir,
                symbol,
                target_timeframe=target_timeframe,
                windows=windows,
            )
            for target_timeframe in target_timeframes_tuple
        ):
            fully_materialized_symbols.add(symbol)
    if fully_materialized_symbols:
        plan_rows.append(
            {
                "symbol": "__all__",
                "status": "target_ltf_cache_reuse_plan",
                "target_timeframes": ",".join(target_timeframes_tuple),
                "symbols_skipping_1s_fetch_due_to_existing_target_ltf": int(len(fully_materialized_symbols)),
                "data_source": "trusted_materialized_target_ltf_cache_reused_before_1s_fetch",
            }
        )

    fetch_rows: list[dict[str, object]] = []
    total_windows = max(1, merged_windows_total)
    done_windows = 0
    started_at = time.monotonic()
    next_progress_pct = 0
    for symbol in sorted(normalized_windows):
        if symbol in fully_materialized_symbols:
            for window_start, window_end in normalized_windows[symbol]:
                fetch_rows.append(
                    {
                        "symbol": symbol,
                        "status": "target_ltf_exists_covered_requested_windows",
                        "error": "",
                        "start_timestamp_ms": int(window_start),
                        "end_timestamp_ms": int(window_end),
                        "start_timestamp_utc": _timestamp_to_utc(int(window_start)),
                        "end_timestamp_utc": _timestamp_to_utc(int(window_end)),
                        "rows_before": 0,
                        "rows_after": 0,
                        "rows_delta": 0,
                        "aggregation_version": AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION,
                    }
                )
                done_windows += 1
                if progress_label is not None and total_windows:
                    next_progress_pct = _emit_progress_1pct(
                        label=f"{progress_label}: targeted 1s aggTrades",
                        done=done_windows,
                        total=total_windows,
                        started_at=started_at,
                        next_progress_pct=next_progress_pct,
                    )
            continue
        for window_start, window_end in normalized_windows[symbol]:
            status = "ok"
            error = ""
            rows_before = 0
            rows_after = 0
            try:
                cache_path = _cache_data_path(cache_dir, symbol, "1s")
                rows_before = _cached_timestamp_row_count(cache_path)
                if _trusted_aggtrade_window_covered_from_cache(
                    cache_dir,
                    symbol,
                    start_timestamp_ms=int(window_start),
                    end_timestamp_ms=int(window_end),
                ):
                    rows_after = rows_before
                    status = "exists_covered_requested_window"
                else:
                    frame = _ensure_latency_1s_cache(
                        cache_dir,
                        symbol,
                        start_timestamp_ms=int(window_start),
                        end_timestamp_ms=int(window_end),
                    )
                    rows_after = int(len(frame))
                    if not _trusted_aggtrade_window_covered(
                        frame,
                        start_timestamp_ms=int(window_start),
                        end_timestamp_ms=int(window_end),
                    ):
                        status = "partial_or_empty"
            except Exception as exc:
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            fetch_rows.append(
                {
                    "symbol": symbol,
                    "status": status,
                    "error": error,
                    "start_timestamp_ms": int(window_start),
                    "end_timestamp_ms": int(window_end),
                    "start_timestamp_utc": _timestamp_to_utc(int(window_start)),
                    "end_timestamp_utc": _timestamp_to_utc(int(window_end)),
                    "rows_before": int(rows_before),
                    "rows_after": int(rows_after),
                    "rows_delta": int(rows_after - rows_before),
                    "aggregation_version": AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION,
                }
            )
            done_windows += 1
            if progress_label is not None and total_windows:
                next_progress_pct = _emit_progress_1pct(
                    label=f"{progress_label}: targeted 1s aggTrades",
                    done=done_windows,
                    total=total_windows,
                    started_at=started_at,
                    next_progress_pct=next_progress_pct,
                )

    materialize = materialize_subminute_entry_caches(
        cache_dir=cache_dir,
        target_timeframes=target_timeframes_tuple,
        symbols=tuple(sorted(normalized_windows)),
        overwrite=False,
        progress_label=f"{progress_label}: materialize LTF" if progress_label else None,
        intervals_by_symbol=normalized_windows,
    )
    fetch = pd.concat([pd.DataFrame(plan_rows).assign(row_type="plan"), pd.DataFrame(fetch_rows).assign(row_type="fetch")], ignore_index=True, sort=False)
    return fetch, materialize


def _fetch_binance_futures_aggtrades_rows(
    symbol: str,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    market_id = _binance_futures_market_id(symbol)
    rows: list[dict[str, object]] = []
    endpoint = "https://fapi.binance.com/fapi/v1/aggTrades"
    chunk_start = int(start_timestamp_ms)
    while chunk_start <= int(end_timestamp_ms):
        chunk_end = min(int(end_timestamp_ms), chunk_start + 3_600_000 - 1)
        cursor = int(chunk_start)
        while cursor <= chunk_end:
            params = urllib.parse.urlencode(
                {
                    "symbol": market_id,
                    "startTime": int(cursor),
                    "endTime": int(chunk_end),
                    "limit": 1000,
                }
            )
            batch = _fetch_binance_aggtrades_json(f"{endpoint}?{params}")
            if not batch:
                break
            rows.extend(dict(row) for row in batch)
            last_ts = int(batch[-1].get("T") or batch[-1].get("time") or cursor)
            if last_ts < cursor or len(batch) < 1000:
                break
            cursor = last_ts + 1
            time.sleep(0.02)
        chunk_start = chunk_end + 1
    return pd.DataFrame(rows)


def _write_direct_aggtrade_target_ltf_delta(
    *,
    cache_dir: Path,
    symbol: str,
    target_timeframe: str,
    trades: pd.DataFrame,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> tuple[str, int, str]:
    if trades.empty:
        coverage_rows = _write_empty_direct_target_ltf_coverage_index(
            cache_dir,
            symbol,
            target_timeframe,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )
        return "empty_aggtrades_coverage_index_written", int(coverage_rows), str(_target_ltf_coverage_index_path(cache_dir, symbol, str(target_timeframe)))
    from data.storage.parquet_storage import ParquetStorage
    from domain.enums.timeframe import Timeframe
    from research_tools.anomaly_aggtrade_cache import aggregate_aggtrades_to_ohlcv_frame

    target_ms = _timeframe_to_milliseconds(target_timeframe)
    aggregated = aggregate_aggtrades_to_ohlcv_frame(
        trades,
        timeframe_ms=int(target_ms),
        start_timestamp_ms=int(start_timestamp_ms),
        end_timestamp_ms=int(end_timestamp_ms),
    )
    if aggregated.empty:
        return "empty_materialized_target_ltf", 0, ""
    aggregated = aggregated.copy()
    aggregated["aggregation_source_timeframe"] = "aggTrades"
    aggregated["aggregation_source"] = "binance_futures_aggTrades"
    aggregated["aggregation_target_timeframe"] = str(target_timeframe)
    aggregated["aggregation_version"] = DIRECT_TARGET_AGGTRADE_CACHE_VERSION
    aggregated["aggtrade_coverage_verified"] = True
    aggregated["aggtrade_materialization_model"] = "direct_aggtrades_to_target_ltf_no_1s_cache"
    ParquetStorage(cache_dir).save_incremental_delta(symbol, Timeframe(str(target_timeframe)), aggregated)
    _write_direct_target_ltf_coverage_index(cache_dir, symbol, str(target_timeframe), aggregated)
    _write_empty_direct_target_ltf_coverage_index(
        cache_dir,
        symbol,
        str(target_timeframe),
        start_timestamp_ms=int(start_timestamp_ms),
        end_timestamp_ms=int(end_timestamp_ms),
    )
    return "written_interval", int(len(aggregated)), str(_cache_data_path(cache_dir, symbol, str(target_timeframe)))


def ensure_targeted_aggtrade_direct_ltf_cache(
    *,
    cache_dir: Path,
    windows_by_symbol: Mapping[str, Iterable[tuple[int, int]]],
    target_timeframes: Iterable[str],
    progress_label: str | None = None,
    max_merged_span_ms: int | None = 60 * 60_000,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backward-compatible facade for the unified targeted LTF accelerator."""

    from research_tools.targeted_ltf_accelerator import ensure_targeted_ltf_accelerated_cache

    return ensure_targeted_ltf_accelerated_cache(
        cache_dir=cache_dir,
        windows_by_symbol=windows_by_symbol,
        target_timeframes=target_timeframes,
        progress_label=progress_label,
        max_merged_span_ms=max_merged_span_ms,
    )


def _merge_targeted_timestamp_windows(
    windows: Iterable[tuple[int, int]],
    *,
    merge_gap_ms: int = TARGETED_FLOW_MERGE_GAP_MS,
    max_merged_span_ms: int | None = TARGETED_FLOW_MAX_MERGED_SPAN_MS,
) -> list[tuple[int, int]]:
    valid = sorted((int(start), int(end)) for start, end in windows if int(start) <= int(end))
    if not valid:
        return []
    merged: list[tuple[int, int]] = [valid[0]]
    for start, end in valid[1:]:
        prev_start, prev_end = merged[-1]
        candidate_start = prev_start
        candidate_end = max(prev_end, end)
        candidate_span = candidate_end - candidate_start + 1
        gap_ok = start <= prev_end + max(0, int(merge_gap_ms)) + 1
        span_ok = (
            True
            if max_merged_span_ms is None
            else candidate_span <= max(1, int(max_merged_span_ms))
        )
        if gap_ok and span_ok:
            merged[-1] = (candidate_start, candidate_end)
        else:
            merged.append((start, end))
    return merged


def _targeted_flow_window_for_row(row: pd.Series, *, config: AnomalyBacktestConfig) -> tuple[int, int] | None:
    """Return the minimal 1s flow window needed to build pair/forming candidates.

    The subminute pre-signal cache is only a flow source for the forming setup
    candle plus the tiny immediate-entry guard tail.  Exit simulation is
    intentionally not included here; fetching a full post-entry horizon before
    we even know which subminute rows become signals turns targeted backfill into
    a full 1s data-lake rebuild.
    """

    setup_start = _safe_int(row.get("timestamp_ms"))
    if setup_start is None:
        setup_start = _safe_int(row.get("timestamp"))
    if setup_start is None:
        return None
    setup_ms = _timeframe_to_milliseconds(_effective_setup_timeframe(config))
    entry_ms = _timeframe_to_milliseconds(_effective_entry_timeframe(config))
    immediate_entry_tail_ms = max(1, int(config.market_entry_latency_candles) + 2) * int(entry_ms)
    left_context_ms = _post_htf_ltf_left_context_ms(config)
    right_confirmation_ms = (
        int(max(1, int(getattr(config, "short_fader_analysis_minutes", 60))) * 60_000)
        if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
        == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER
        else (
            int(setup_ms)
            if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
            == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION
            else 0
        )
    )
    return (
        int(setup_start) - int(left_context_ms),
        int(setup_start) + int(setup_ms) - 1 + int(right_confirmation_ms) + int(immediate_entry_tail_ms),
    )


def _targeted_flow_needs_oi_context(config: AnomalyBacktestConfig) -> bool:
    return (
        config.min_oi_change_pct_3x5m is not None
        or bool(config.require_oi_status_ok)
        or bool(config.reject_oi_down_mark_discount)
    )


def _targeted_flow_needs_derivatives_context(config: AnomalyBacktestConfig) -> bool:
    return (
        config.min_mark_close_vs_decision_close_basis is not None
        or bool(config.reject_stale_derivatives_context)
        or bool(config.reject_oi_down_mark_discount)
        or config.max_start_taker_buy_quote_share_delta is not None
        or config.max_next_taker_buy_quote_share_delta is not None
    )


def _apply_subminute_flow_upper_bound_filter(
    coarse: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> tuple[pd.DataFrame, int]:
    """Drop rows whose full setup candle cannot satisfy any subminute flow gate.

    This is a safe upper-bound prune: every forming 5s/15s/30s prefix inside a
    setup candle has quote/trade flow <= the already-closed setup candle.  If the
    full setup candle is below the minimum raw ratio required for the earliest
    allowed subminute decision, no subminute segment inside it can pass.
    """

    required = {
        "start_quote_volume",
        "start_trade_count",
        "baseline_quote_volume_median",
        "baseline_trade_count_median",
    }
    if coarse.empty or required.difference(coarse.columns):
        return coarse, 0
    quote = pd.to_numeric(coarse["start_quote_volume"], errors="coerce")
    trades = pd.to_numeric(coarse["start_trade_count"], errors="coerce")
    baseline_quote = pd.to_numeric(coarse["baseline_quote_volume_median"], errors="coerce")
    baseline_trades = pd.to_numeric(coarse["baseline_trade_count_median"], errors="coerce")
    min_fraction = 0.35
    quote_required = baseline_quote * float(config.lab_config.min_quote_ratio_start) * min_fraction
    trade_required = baseline_trades * float(config.lab_config.min_trade_ratio_start) * min_fraction
    mask = (
        quote.notna()
        & trades.notna()
        & baseline_quote.gt(0.0)
        & baseline_trades.gt(0.0)
        & quote.ge(quote_required)
        & trades.ge(trade_required)
    )
    filtered = coarse.loc[mask].copy()
    return filtered, int(len(coarse) - len(filtered))


def _short_fader_prefilter_thresholds(config: AnomalyBacktestConfig) -> tuple[float, float, float]:
    min_quote_ratio = max(
        float(config.lab_config.min_quote_ratio_start),
        float(getattr(config, "short_fader_prefilter_min_quote_ratio", 10.0)),
    )
    min_trade_ratio = max(
        float(config.lab_config.min_trade_ratio_start),
        float(getattr(config, "short_fader_prefilter_min_trade_ratio", 8.0)),
    )
    min_htf_return = float(getattr(config, "short_fader_prefilter_min_htf_return", 0.015))
    return min_quote_ratio, min_trade_ratio, min_htf_return


def _short_fader_htf_return_from_row(row: pd.Series) -> float:
    open_value = row.get("htf_open", row.get("start_open", row.get("open", np.nan)))
    close_value = row.get("htf_close", row.get("start_close", row.get("close", np.nan)))
    try:
        return _safe_divide_value(float(close_value) - float(open_value), float(open_value))
    except (TypeError, ValueError):
        return float("nan")


def _apply_short_fader_htf_anomaly_prefilter(
    coarse: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> tuple[pd.DataFrame, int]:
    if coarse.empty:
        return coarse.copy(), 0
    required = {"start_quote_ratio", "start_trade_ratio"}
    if required.difference(coarse.columns):
        return coarse.iloc[0:0].copy(), int(len(coarse))
    min_quote_ratio, min_trade_ratio, min_htf_return = _short_fader_prefilter_thresholds(config)
    quote_ratio = pd.to_numeric(coarse["start_quote_ratio"], errors="coerce")
    trade_ratio = pd.to_numeric(coarse["start_trade_ratio"], errors="coerce")
    htf_return = coarse.apply(_short_fader_htf_return_from_row, axis=1)
    mask = (
        quote_ratio.ge(min_quote_ratio)
        & trade_ratio.ge(min_trade_ratio)
        & pd.to_numeric(htf_return, errors="coerce").ge(min_htf_return)
    )
    filtered = coarse.loc[mask].copy()
    return filtered, int(len(coarse) - len(filtered))


def _candidate_decision_center_ms(row: pd.Series) -> int | None:
    for column in (
        "decision_available_timestamp_ms",
        "decision_timestamp_ms",
        "timestamp_ms",
        "timestamp",
    ):
        if column in row.index:
            value = _safe_int(row.get(column))
            if value is not None:
                return value
    return None


def _prepare_coarse_candidates_for_targeted_flow(
    coarse: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    setup_timeframe: str,
) -> pd.DataFrame:
    if coarse.empty:
        return coarse.copy()
    result = coarse.copy()
    if "status" in result.columns:
        result = result.loc[~result["status"].astype(str).eq("error")].copy()
    if result.empty:
        return result
    result["feature_contract"] = "closed_setup_tf_v1"
    result["setup_timeframe"] = setup_timeframe
    result["entry_timeframe"] = setup_timeframe
    result["setup_source"] = "closed_setup_tf_targeted_flow_prefilter"
    if "setup_elapsed_fraction" not in result.columns:
        result["setup_elapsed_fraction"] = 1.0
    if "setup_closed_entry_candles" not in result.columns:
        result["setup_closed_entry_candles"] = int(config.lab_config.confirmation_candles)
    result, _dropped_by_upper_bound = _apply_subminute_flow_upper_bound_filter(result, config=config)
    return result


def _select_coarse_signal_rows_for_targeted_flow(
    coarse: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    setup_timeframe: str,
) -> tuple[pd.DataFrame, str, str]:
    prepared = _prepare_coarse_candidates_for_targeted_flow(
        coarse,
        config=config,
        setup_timeframe=setup_timeframe,
    )
    if prepared.empty:
        return prepared, "no_usable_coarse_candidates", ""
    if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING)) == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER:
        return prepared, "ok_bare_htf_short_fader_prefilter", ""
    signal_config = _strip_derivative_context_requirements(
        replace(
            config,
            setup_timeframe=setup_timeframe,
            entry_timeframe=setup_timeframe,
            feature_contract="closed_setup_tf_v1",
        )
    )
    try:
        signals = build_anomaly_signals(prepared, config=signal_config)
    except Exception as exc:
        return prepared.iloc[0:0].copy(), "coarse_signal_filter_error", f"{type(exc).__name__}: {exc}"
    if signals.empty:
        return signals, "no_coarse_signals", ""
    return signals, "ok", ""


def ensure_targeted_subminute_flow_cache_for_configs(
    configs: Iterable[AnomalyBacktestConfig],
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backfill only the 1s aggTrade windows needed by subminute pair backtests.

    The coarse scan uses closed setup-timeframe OHLCV to find interesting anomaly
    windows cheaply.  It then fetches true aggTrades only around those windows and
    materializes the requested 5s/15s/30s entry caches.  This restores the intended
    backtest flow without requiring a full 1s cache for the whole test range.
    """

    resolved_configs = [_apply_red_flag_profile(config) for config in configs]
    subminute_configs = [
        config
        for config in resolved_configs
        if _effective_entry_timeframe(config) != _effective_setup_timeframe(config)
        and 0 < _timeframe_to_milliseconds(_effective_entry_timeframe(config)) < 60_000
    ]
    if not subminute_configs:
        return pd.DataFrame(), pd.DataFrame()

    windows_by_symbol: dict[str, list[tuple[int, int]]] = {}
    target_timeframes: set[str] = set()
    discovery_rows: list[dict[str, object]] = []
    for config in subminute_configs:
        setup_timeframe = _effective_setup_timeframe(config)
        entry_timeframe = _effective_entry_timeframe(config)
        target_timeframes.add(entry_timeframe)
        coarse_lab_config = replace(config.lab_config, timeframe=setup_timeframe)
        label = f"{progress_label}: coarse {setup_timeframe}/{entry_timeframe}" if progress_label else None
        coarse = collect_anomaly_lab_rows(
            coarse_lab_config,
            symbols=symbols,
            progress_label=label,
            include_oi_context=_targeted_flow_needs_oi_context(config),
            include_derivatives_context=_targeted_flow_needs_derivatives_context(config),
        )
        if not coarse.empty:
            coarse = enrich_candidates_with_recent_spike_context(coarse, config=config)
        if coarse.empty:
            discovery_rows.append(
                {
                    "setup_timeframe": setup_timeframe,
                    "entry_timeframe": entry_timeframe,
                    "status": "no_coarse_candidates",
                    "coarse_candidates": 0,
                    "coarse_signals": 0,
                    "targeted_windows": 0,
                }
            )
            continue
        usable_coarse_count = int(len(coarse.loc[~coarse["status"].astype(str).eq("error")])) if "status" in coarse.columns else int(len(coarse))
        bounded_coarse, upper_bound_dropped = _apply_subminute_flow_upper_bound_filter(coarse, config=config)
        short_fader_prefilter_dropped = 0
        if (
            str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
            == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER
        ):
            bounded_coarse, short_fader_prefilter_dropped = _apply_short_fader_htf_anomaly_prefilter(
                bounded_coarse,
                config=config,
            )
        valid, selection_status, selection_error = _select_coarse_signal_rows_for_targeted_flow(
            bounded_coarse,
            config=config,
            setup_timeframe=setup_timeframe,
        )
        windows_added = 0
        raw_window_ms = 0
        left_context_ms = _post_htf_ltf_left_context_ms(config)
        right_context_ms = (
            int(max(1, int(getattr(config, "short_fader_analysis_minutes", 60))) * 60_000)
            if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
            == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER
            else (
                _timeframe_to_milliseconds(setup_timeframe)
                if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
                == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION
                else 0
            )
        )
        for _, row in valid.iterrows():
            symbol = str(row.get("symbol") or "").strip()
            if not symbol or symbol == "__all__":
                continue
            window = _targeted_flow_window_for_row(row, config=config)
            if window is None:
                continue
            window_start, window_end = window
            windows_by_symbol.setdefault(symbol, []).append((window_start, window_end))
            raw_window_ms += int(window_end) - int(window_start) + 1
            windows_added += 1
        discovery_rows.append(
            {
                "setup_timeframe": setup_timeframe,
                "entry_timeframe": entry_timeframe,
                "status": selection_status if not windows_added else "ok",
                "error": selection_error,
                "coarse_candidates": int(usable_coarse_count),
                "coarse_upper_bound_dropped": int(upper_bound_dropped),
                "short_fader_prefilter_dropped": int(short_fader_prefilter_dropped),
                "short_fader_prefilter_min_quote_ratio": float(_short_fader_prefilter_thresholds(config)[0]),
                "short_fader_prefilter_min_trade_ratio": float(_short_fader_prefilter_thresholds(config)[1]),
                "short_fader_prefilter_min_htf_return": float(_short_fader_prefilter_thresholds(config)[2]),
                "coarse_prefilter_candidates": int(len(bounded_coarse)),
                "coarse_signals": int(len(valid)),
                "targeted_windows": int(windows_added),
                "raw_targeted_window_ms": int(raw_window_ms),
                "window_before_ms": int(left_context_ms),
                "window_after_ms": int(right_context_ms),
                "window_selection": (
                    "bare_htf_anomaly_prefilter_post_close_short_fader_window"
                    if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
                    == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER
                    else (
                        "coarse_signal_prefilter_plus_flow_upper_bound_setup_window_with_post_htf_ltf_left_context"
                        if left_context_ms > 0
                        else "coarse_signal_prefilter_plus_flow_upper_bound_exact_setup_window"
                    )
                ),
            }
        )

    backfill_rows: list[dict[str, object]] = []
    merged_windows_by_symbol = {
        symbol: _merge_targeted_timestamp_windows(windows)
        for symbol, windows in windows_by_symbol.items()
        if windows
    }
    raw_windows_total = sum(len(windows) for windows in windows_by_symbol.values())
    raw_window_ms_total = sum(
        int(end) - int(start) + 1
        for windows in windows_by_symbol.values()
        for start, end in windows
    )
    total_windows = sum(len(windows) for windows in merged_windows_by_symbol.values())
    merged_window_ms_total = sum(
        int(end) - int(start) + 1
        for windows in merged_windows_by_symbol.values()
        for start, end in windows
    )
    discovery_rows.append(
        {
            "setup_timeframe": "__all__",
            "entry_timeframe": ",".join(sorted(target_timeframes, key=_timeframe_to_milliseconds)),
            "status": "window_plan",
            "raw_targeted_windows": int(raw_windows_total),
            "merged_targeted_windows": int(total_windows),
            "raw_targeted_window_ms": int(raw_window_ms_total),
            "merged_targeted_window_ms": int(merged_window_ms_total),
            "merge_gap_ms": int(TARGETED_FLOW_MERGE_GAP_MS),
            "max_merged_span_ms": int(TARGETED_FLOW_MAX_MERGED_SPAN_MS),
        }
    )
    done_windows = 0
    started_at = time.monotonic()
    next_progress_pct = 0
    for symbol in sorted(merged_windows_by_symbol):
        for window_start, window_end in merged_windows_by_symbol[symbol]:
            status = "ok"
            error = ""
            rows_before = 0
            rows_after = 0
            try:
                cache_dir = subminute_configs[0].lab_config.cache_dir
                cache_path = _cache_data_path(cache_dir, symbol, "1s")
                rows_before = _cached_timestamp_row_count(cache_path)
                if _trusted_aggtrade_window_covered_from_cache(
                    cache_dir,
                    symbol,
                    start_timestamp_ms=int(window_start),
                    end_timestamp_ms=int(window_end),
                ):
                    rows_after = rows_before
                    status = "exists_covered_requested_window"
                else:
                    frame = _ensure_latency_1s_cache(
                        cache_dir,
                        symbol,
                        start_timestamp_ms=int(window_start),
                        end_timestamp_ms=int(window_end),
                    )
                    rows_after = int(len(frame))
                    if not _trusted_aggtrade_window_covered(
                        frame,
                        start_timestamp_ms=int(window_start),
                        end_timestamp_ms=int(window_end),
                    ):
                        status = "partial_or_empty"
            except Exception as exc:
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            backfill_rows.append(
                {
                    "symbol": symbol,
                    "status": status,
                    "error": error,
                    "start_timestamp_ms": int(window_start),
                    "end_timestamp_ms": int(window_end),
                    "start_timestamp_utc": _timestamp_to_utc(int(window_start)),
                    "end_timestamp_utc": _timestamp_to_utc(int(window_end)),
                    "rows_before": int(rows_before),
                    "rows_after": int(rows_after),
                    "rows_delta": int(rows_after - rows_before),
                    "aggregation_version": AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION,
                }
            )
            done_windows += 1
            if progress_label is not None and total_windows:
                next_progress_pct = _emit_progress_1pct(
                    label=f"{progress_label}: targeted 1s flow",
                    done=done_windows,
                    total=total_windows,
                    started_at=started_at,
                    next_progress_pct=next_progress_pct,
                )

    symbols_to_materialize = sorted(merged_windows_by_symbol)
    if symbols_to_materialize and target_timeframes:
        materialize = materialize_subminute_entry_caches(
            cache_dir=subminute_configs[0].lab_config.cache_dir,
            target_timeframes=sorted(target_timeframes, key=_timeframe_to_milliseconds),
            symbols=symbols_to_materialize,
            overwrite=False,
            progress_label=f"{progress_label}: materialize subminute flow" if progress_label else None,
            intervals_by_symbol=merged_windows_by_symbol,
        )
    else:
        materialize = pd.DataFrame()
    discovery = pd.DataFrame(discovery_rows)
    backfill = pd.DataFrame(backfill_rows)
    if not discovery.empty:
        backfill = pd.concat([discovery.assign(symbol="__coarse_scan__"), backfill], ignore_index=True, sort=False)
    return backfill, materialize


def split_targeted_flow_backfill_artifacts(backfill: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split the combined targeted-flow artifact into plan and fetch tables.

    `ensure_targeted_subminute_flow_cache_for_configs` keeps returning the legacy
    combined backfill frame for compatibility, but the audit trail needs two
    separate, operator-readable files: what was planned before network fetch and
    what each actual fetch window returned.
    """

    if backfill.empty:
        empty_plan = pd.DataFrame([{"status": "no_targeted_flow_rows", "reason": "targeted_flow_planner_returned_empty"}])
        empty_fetch = pd.DataFrame([{"status": "no_fetch_windows", "reason": "targeted_flow_planner_returned_empty"}])
        return empty_plan, empty_fetch
    if "symbol" not in backfill.columns:
        return backfill.copy(), pd.DataFrame([{"status": "invalid_backfill_artifact", "reason": "missing_symbol_column"}])
    symbols = backfill["symbol"].astype(str)
    plan = backfill.loc[symbols.eq("__coarse_scan__")].copy()
    fetch = backfill.loc[~symbols.eq("__coarse_scan__")].copy()
    if plan.empty:
        plan = pd.DataFrame([{"status": "missing_window_plan", "reason": "combined_backfill_has_no_coarse_scan_rows"}])
    if fetch.empty:
        fetch = pd.DataFrame([{"status": "no_fetch_windows", "reason": "targeted_flow_plan_selected_zero_windows"}])
    return plan.reset_index(drop=True), fetch.reset_index(drop=True)


def _targeted_flow_planned_window_count(plan: pd.DataFrame) -> int:
    if plan.empty:
        return 0
    for column in ("merged_targeted_windows", "targeted_windows", "raw_targeted_windows"):
        if column in plan.columns:
            values = pd.to_numeric(plan[column], errors="coerce").fillna(0)
            count = int(values.sum())
            if count > 0:
                return count
    return 0


def _targeted_flow_entry_timeframes(configs: Iterable[AnomalyBacktestConfig]) -> tuple[str, ...]:
    timeframes = {
        _effective_entry_timeframe(config)
        for config in configs
        if _effective_entry_timeframe(config) != _effective_setup_timeframe(config)
        and 0 < _timeframe_to_milliseconds(_effective_entry_timeframe(config)) < 60_000
    }
    return tuple(sorted(timeframes, key=_timeframe_to_milliseconds))


def _materialized_entry_window_status(
    *,
    cache_dir: Path,
    symbol: str,
    entry_timeframe: str,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    frame_cache: dict[tuple[str, str], pd.DataFrame],
) -> dict[str, object]:
    key = (symbol, entry_timeframe)
    if key not in frame_cache:
        frame_cache[key] = _read_symbol_frame_optional(cache_dir, symbol, entry_timeframe)
    frame = frame_cache[key]
    if frame.empty:
        return {"coverage_status": "missing_materialized_entry_flow_cache", "coverage_rows": 0}
    validation_error = _flow_cache_validation_error(
        frame,
        cache_timeframe=entry_timeframe,
        entry_timeframe=entry_timeframe,
    )
    if validation_error is not None:
        return {
            "coverage_status": "untrusted_materialized_entry_flow_cache",
            "coverage_error": validation_error,
            "coverage_rows": 0,
        }
    if "timestamp" not in frame.columns:
        return {"coverage_status": "missing_timestamp", "coverage_rows": 0}
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna().astype("int64")
    if timestamps.empty:
        return {"coverage_status": "empty_materialized_entry_flow_cache", "coverage_rows": 0}
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    first_bucket = int(start_timestamp_ms // entry_ms) * entry_ms
    last_bucket = int(end_timestamp_ms // entry_ms) * entry_ms
    in_window = timestamps.loc[timestamps.ge(first_bucket) & timestamps.le(last_bucket)]
    if in_window.empty:
        return {
            "coverage_status": "missing_window_candles",
            "coverage_rows": 0,
            "required_first_bucket_ms": int(first_bucket),
            "required_last_bucket_ms": int(last_bucket),
        }
    return {
        "coverage_status": "ready",
        "coverage_rows": int(len(in_window)),
        "coverage_min_timestamp_ms": int(in_window.min()),
        "coverage_max_timestamp_ms": int(in_window.max()),
        "coverage_min_timestamp_utc": _timestamp_to_utc(int(in_window.min())),
        "coverage_max_timestamp_utc": _timestamp_to_utc(int(in_window.max())),
        "required_first_bucket_ms": int(first_bucket),
        "required_last_bucket_ms": int(last_bucket),
    }


def build_targeted_flow_coverage(
    *,
    backfill: pd.DataFrame,
    materialize: pd.DataFrame,
    configs: Iterable[AnomalyBacktestConfig],
) -> pd.DataFrame:
    """Audit whether targeted 1s fetches produced trusted subminute flow windows."""

    resolved_configs = [_apply_red_flag_profile(config) for config in configs]
    entry_timeframes = _targeted_flow_entry_timeframes(resolved_configs)
    if not entry_timeframes:
        return pd.DataFrame([{"coverage_status": "not_required", "reason": "no_subminute_entry_timeframes"}])
    _, fetch = split_targeted_flow_backfill_artifacts(backfill)
    if fetch.empty or "symbol" not in fetch.columns:
        fetch_rows = pd.DataFrame()
    else:
        fetch_rows = fetch.loc[fetch["symbol"].astype(str).ne("__coarse_scan__")].copy()
    if fetch_rows.empty or "symbol" not in fetch_rows.columns:
        return pd.DataFrame([{"coverage_status": "no_targeted_flow_windows", "reason": "no_fetch_windows"}])
    cache_dir = resolved_configs[0].lab_config.cache_dir
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    materialize_status_counts: dict[str, int] = {}
    if not materialize.empty and "status" in materialize.columns:
        for status, count in materialize["status"].astype(str).value_counts().items():
            materialize_status_counts[str(status)] = int(count)
    for _, row in fetch_rows.iterrows():
        symbol = str(row.get("symbol") or "").strip()
        start_ms = _safe_int(row.get("start_timestamp_ms"))
        end_ms = _safe_int(row.get("end_timestamp_ms"))
        fetch_status = str(row.get("status") or "").strip() or "unknown"
        if not symbol or symbol == "__all__" or start_ms is None or end_ms is None:
            rows.append({
                "symbol": symbol or "__missing__",
                "coverage_status": "invalid_fetch_window",
                "fetch_status": fetch_status,
            })
            continue
        for entry_timeframe in entry_timeframes:
            base = {
                "symbol": symbol,
                "entry_timeframe": entry_timeframe,
                "start_timestamp_ms": int(start_ms),
                "end_timestamp_ms": int(end_ms),
                "start_timestamp_utc": _timestamp_to_utc(int(start_ms)),
                "end_timestamp_utc": _timestamp_to_utc(int(end_ms)),
                "fetch_status": fetch_status,
                "fetch_error": row.get("error", ""),
                "materialize_status_counts": json.dumps(materialize_status_counts, sort_keys=True),
            }
            if fetch_status != "ok":
                rows.append({**base, "coverage_status": "fetch_not_ok", "coverage_rows": 0})
                continue
            rows.append({
                **base,
                **_materialized_entry_window_status(
                    cache_dir=cache_dir,
                    symbol=symbol,
                    entry_timeframe=entry_timeframe,
                    start_timestamp_ms=int(start_ms),
                    end_timestamp_ms=int(end_ms),
                    frame_cache=frame_cache,
                ),
            })
    if not rows:
        return pd.DataFrame([{"coverage_status": "no_targeted_flow_windows", "reason": "no_fetch_windows"}])
    return pd.DataFrame(rows)


def ready_symbols_from_targeted_flow_coverage(
    coverage: pd.DataFrame,
    *,
    entry_timeframe: str | None = None,
) -> tuple[str, ...]:
    if coverage.empty or "coverage_status" not in coverage.columns or "symbol" not in coverage.columns:
        return ()
    frame = coverage.loc[coverage["coverage_status"].astype(str).eq("ready")].copy()
    if entry_timeframe is not None and "entry_timeframe" in frame.columns:
        frame = frame.loc[frame["entry_timeframe"].astype(str).eq(str(entry_timeframe))]
    return tuple(sorted({str(symbol) for symbol in frame["symbol"].dropna().astype(str) if symbol and symbol != "__all__"}))


def _status_counts_json(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return "{}"
    return json.dumps({str(key): int(value) for key, value in frame[column].astype(str).value_counts().items()}, sort_keys=True)


def _valid_candidate_count(candidates: pd.DataFrame) -> int:
    if candidates.empty:
        return 0
    if "status" not in candidates.columns:
        return int(len(candidates))
    return int((~candidates["status"].astype(str).eq("error")).sum())


def _candidate_error_count(candidates: pd.DataFrame) -> int:
    if candidates.empty or "status" not in candidates.columns:
        return 0
    return int(candidates["status"].astype(str).eq("error").sum())


def _flow_data_error_count(candidates: pd.DataFrame) -> int:
    if candidates.empty or "error" not in candidates.columns:
        return 0
    errors = candidates["error"].fillna("").astype(str).str.lower()
    markers = ("flow_cache", "subminute", "trusted_flow", "materialized_entry_flow")
    return int(errors.map(lambda value: any(marker in value for marker in markers)).sum())


def build_anomaly_funnel(
    *,
    candidates: pd.DataFrame,
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    targeted_flow_plan: pd.DataFrame,
    targeted_flow_fetch: pd.DataFrame,
    targeted_flow_materialize: pd.DataFrame,
    targeted_flow_coverage: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(stage: str, count: int, **extra: object) -> None:
        rows.append({"stage": stage, "count": int(count), **extra})

    if not targeted_flow_plan.empty and "setup_timeframe" in targeted_flow_plan.columns:
        coarse_rows = targeted_flow_plan.loc[targeted_flow_plan["setup_timeframe"].astype(str).ne("__all__")].copy()
    elif not targeted_flow_plan.empty:
        coarse_rows = targeted_flow_plan.copy()
    else:
        coarse_rows = pd.DataFrame()
    add("coarse_candidates", int(pd.to_numeric(coarse_rows.get("coarse_candidates", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()))
    add("coarse_prefilter_candidates", int(pd.to_numeric(coarse_rows.get("coarse_prefilter_candidates", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()))
    add("coarse_signals", int(pd.to_numeric(coarse_rows.get("coarse_signals", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()))
    add("targeted_raw_windows", int(pd.to_numeric(targeted_flow_plan.get("raw_targeted_windows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()))
    add("targeted_merged_windows", int(pd.to_numeric(targeted_flow_plan.get("merged_targeted_windows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()))
    fetch_window_count = (
        int(len(targeted_flow_fetch.loc[targeted_flow_fetch["symbol"].astype(str).ne("__coarse_scan__")]))
        if not targeted_flow_fetch.empty and "symbol" in targeted_flow_fetch.columns
        else 0
    )
    add("targeted_fetch_windows", fetch_window_count, status_counts=_status_counts_json(targeted_flow_fetch, "status"))
    add("targeted_materialize_rows", int(len(targeted_flow_materialize)), status_counts=_status_counts_json(targeted_flow_materialize, "status"))
    add("targeted_coverage_ready", int((targeted_flow_coverage.get("coverage_status", pd.Series(dtype=str)).astype(str) == "ready").sum()) if not targeted_flow_coverage.empty else 0, status_counts=_status_counts_json(targeted_flow_coverage, "coverage_status"))
    add("final_candidates", int(len(candidates)), error_count=_candidate_error_count(candidates), valid_count=_valid_candidate_count(candidates))
    add("signals", int(len(signals)))
    add("trade_rows", int(len(trades)))
    if not trades.empty and "trade_status" in trades.columns:
        closed = int(trades["trade_status"].astype(str).eq("closed").sum())
        skipped = int(trades["trade_status"].astype(str).eq("skipped").sum())
    else:
        closed = 0
        skipped = 0
    add("closed_trades", closed)
    add("skipped_trades", skipped)
    return pd.DataFrame(rows)


def build_backtest_run_verdict(
    *,
    config: AnomalyBacktestConfig,
    candidates: pd.DataFrame,
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    targeted_flow_plan: pd.DataFrame,
    targeted_flow_coverage: pd.DataFrame,
) -> pd.DataFrame:
    entry_timeframe = _effective_entry_timeframe(config)
    setup_timeframe = _effective_setup_timeframe(config)
    subminute_pair = entry_timeframe != setup_timeframe and 0 < _timeframe_to_milliseconds(entry_timeframe) < 60_000
    planned_windows = _targeted_flow_planned_window_count(targeted_flow_plan)
    ready_windows = int((targeted_flow_coverage.get("coverage_status", pd.Series(dtype=str)).astype(str) == "ready").sum()) if not targeted_flow_coverage.empty else 0
    valid_candidates = _valid_candidate_count(candidates)
    candidate_errors = _candidate_error_count(candidates)
    flow_data_errors = _flow_data_error_count(candidates)
    valid_backtest = True
    verdict = "valid_no_trades_yet"
    reason = "signals_or_trades_may_still_be_zero_after_filters"
    if subminute_pair and planned_windows > 0 and ready_windows == 0:
        valid_backtest = False
        verdict = "invalid_data_pipeline"
        reason = "no_trusted_subminute_flow_coverage_after_targeted_fetch"
    elif not candidates.empty and valid_candidates == 0 and flow_data_errors > 0:
        valid_backtest = False
        verdict = "invalid_data_pipeline"
        reason = "no_valid_candidates_due_to_flow_data_errors"
    elif not candidates.empty and valid_candidates == 0 and candidate_errors > 0:
        valid_backtest = False
        verdict = "invalid_candidate_collection"
        reason = "all_candidates_are_error_rows"
    elif candidates.empty:
        verdict = "valid_empty_candidate_set"
        reason = "no_candidates_after_asof_collection"
    elif signals.empty:
        verdict = "valid_no_signals"
        reason = "valid_candidates_exist_but_signal_filters_selected_zero"
    elif trades.empty:
        verdict = "valid_no_trade_rows"
        reason = "signals_exist_but_execution_simulation_returned_zero_rows"
    else:
        verdict = "valid_edge_evaluable"
        reason = "trades_artifact_available"
    return pd.DataFrame([
        {
            "valid_backtest": bool(valid_backtest),
            "verdict": verdict,
            "reason": reason,
            "setup_timeframe": setup_timeframe,
            "entry_timeframe": entry_timeframe,
            "planned_targeted_flow_windows": int(planned_windows),
            "ready_targeted_flow_windows": int(ready_windows),
            "candidate_rows": int(len(candidates)),
            "valid_candidate_rows": int(valid_candidates),
            "candidate_error_rows": int(candidate_errors),
            "flow_data_error_rows": int(flow_data_errors),
            "signal_rows": int(len(signals)),
            "trade_rows": int(len(trades)),
            "coverage_status_counts": _status_counts_json(targeted_flow_coverage, "coverage_status"),
        }
    ])


def _disabled_artifact_frame(*, artifact: str, reason: str) -> pd.DataFrame:
    return pd.DataFrame([{"status": "disabled", "artifact": artifact, "reason": reason}])


def targeted_flow_collection_symbols(
    coverage: pd.DataFrame,
    *,
    requested_symbols: Iterable[str] | None = None,
) -> tuple[str, ...] | None:
    ready = set(ready_symbols_from_targeted_flow_coverage(coverage))
    requested = tuple(symbol for symbol in (requested_symbols or ()) if str(symbol).strip())
    if requested:
        if not ready:
            return requested
        normalized_ready = {normalize_symbol(symbol) for symbol in ready}
        return tuple(symbol for symbol in requested if normalize_symbol(symbol) in normalized_ready)
    if ready:
        return tuple(sorted(ready))
    return None


def build_entry_cache_coverage(
    *,
    cache_dir: Path,
    setup_timeframe: str,
    entry_timeframe: str,
    start_ms: int,
    end_ms: int,
    symbols: Iterable[str] | None = None,
) -> pd.DataFrame:
    wanted_symbols = set(_normalized_symbol_tuple(symbols))
    rows: list[dict[str, object]] = []
    for requested, cache_timeframe, role in (
        (setup_timeframe, setup_timeframe, "setup"),
        (entry_timeframe, _resolve_entry_cache_timeframe(cache_dir, entry_timeframe), "entry"),
    ):
        symbol_rows: list[tuple[str, int, int, int]] = []
        for path in cache_dir.glob(f"*%2FUSDT%3AUSDT/{cache_timeframe}/data.parquet"):
            symbol = _symbol_from_cache_symbol_dir(path.parent.parent)
            if wanted_symbols and normalize_symbol(symbol) not in wanted_symbols:
                continue
            try:
                timestamps = pd.read_parquet(path, columns=["timestamp"])["timestamp"]
            except Exception:
                continue
            if timestamps.empty:
                continue
            symbol_rows.append((symbol, int(timestamps.min()), int(timestamps.max()), int(len(timestamps))))
        if symbol_rows:
            min_ts = min(row[1] for row in symbol_rows)
            max_ts = max(row[2] for row in symbol_rows)
            rows.append(
                {
                    "role": role,
                    "requested_timeframe": requested,
                    "cache_timeframe": cache_timeframe,
                    "symbols": len(symbol_rows),
                    "rows": sum(row[3] for row in symbol_rows),
                    "global_min_timestamp_ms": min_ts,
                    "global_max_timestamp_ms": max_ts,
                    "global_min_timestamp_utc": _timestamp_to_utc(min_ts),
                    "global_max_timestamp_utc": _timestamp_to_utc(max_ts),
                    "requested_start_timestamp_ms": int(start_ms),
                    "requested_end_timestamp_ms": int(end_ms),
                    "requested_start_timestamp_utc": _timestamp_to_utc(int(start_ms)),
                    "requested_end_timestamp_utc": _timestamp_to_utc(int(end_ms)),
                    "symbols_covering_start": sum(1 for _, min_symbol_ts, _, _ in symbol_rows if min_symbol_ts <= start_ms),
                    "symbols_covering_end": sum(1 for _, _, max_symbol_ts, _ in symbol_rows if max_symbol_ts >= end_ms),
                }
            )
        else:
            rows.append(
                {
                    "role": role,
                    "requested_timeframe": requested,
                    "cache_timeframe": cache_timeframe,
                    "symbols": 0,
                    "rows": 0,
                    "requested_start_timestamp_ms": int(start_ms),
                    "requested_end_timestamp_ms": int(end_ms),
                    "requested_start_timestamp_utc": _timestamp_to_utc(int(start_ms)),
                    "requested_end_timestamp_utc": _timestamp_to_utc(int(end_ms)),
                }
            )
    return pd.DataFrame(rows)


def enrich_candidates_with_recent_spike_context(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> pd.DataFrame:
    if candidates.empty or "symbol" not in candidates.columns or "decision_timestamp_ms" not in candidates.columns:
        return candidates
    result = candidates.copy()
    for column in (
        "prior_context_status",
        "prior_context_reason",
        "prior_context_source",
        "prior_context_rows_used",
        "prior_context_start_ms",
        "prior_context_end_ms",
        "prior_spike_count_24h",
        "prior_spike_count_72h",
        "prior_fast_fade_count_24h",
        "prior_fast_fade_count_72h",
        "prior_big_move_count_24h",
        "prior_big_move_count_72h",
        "prior_spike_density_72h",
        "time_since_prior_spike_ms",
        "time_since_prior_spike_hours",
    ):
        result[column] = np.nan
    for column in ("prior_context_status", "prior_context_reason", "prior_context_source"):
        result[column] = ""
    for _, group in result.groupby("symbol", sort=False):
        symbol = str(group.iloc[0]["symbol"])
        prior_frame = _read_symbol_frame_optional(config.lab_config.cache_dir, symbol, "5m")
        if prior_frame.empty or not {"timestamp", "open", "high", "low", "close"}.issubset(prior_frame.columns):
            for row_index in group.index:
                result.at[row_index, "prior_context_status"] = "missing_5m_prior_context"
                result.at[row_index, "prior_context_reason"] = "missing_or_invalid_5m_ohlcv"
                result.at[row_index, "prior_context_source"] = "cached_5m_ohlcv_for_live_prior_context"
            continue
        prior_frame = prior_frame.loc[:, ["timestamp", "open", "high", "low", "close"]].copy()
        for column in prior_frame.columns:
            prior_frame[column] = pd.to_numeric(prior_frame[column], errors="coerce")
        prior_frame.dropna(subset=["timestamp", "open", "high", "low", "close"], inplace=True)
        prior_frame = prior_frame.loc[
            prior_frame["open"].gt(0.0)
            & prior_frame["high"].gt(0.0)
            & prior_frame["low"].gt(0.0)
            & prior_frame["close"].gt(0.0)
        ].copy()
        prior_frame.sort_values("timestamp", inplace=True)
        prior_frame.drop_duplicates("timestamp", keep="last", inplace=True)
        prior_timestamps = prior_frame["timestamp"].astype("int64").to_numpy()

        ordered = group.copy()
        ordered["_decision_ts_numeric"] = pd.to_numeric(ordered["decision_timestamp_ms"], errors="coerce")
        ordered = ordered.dropna(subset=["_decision_ts_numeric"]).sort_values("_decision_ts_numeric")
        if ordered.empty:
            continue
        timestamps = ordered["_decision_ts_numeric"].astype("int64").to_numpy()
        expected_rows = max(1, int((_PRIOR_CONTEXT_LIVE_LOOKBACK_HOURS * 60) / 5))
        min_expected_rows = max(1, int(expected_rows * _PRIOR_CONTEXT_MIN_COVERAGE_RATIO))
        for pos, row_index in enumerate(ordered.index):
            decision_ts = int(timestamps[pos])
            context_end_exclusive_ms = int(decision_ts // _FIVE_MINUTE_MS) * _FIVE_MINUTE_MS
            context_start_ms = context_end_exclusive_ms - _PRIOR_CONTEXT_LIVE_LOOKBACK_MS
            left = int(np.searchsorted(prior_timestamps, context_start_ms, side="left"))
            right = int(np.searchsorted(prior_timestamps, context_end_exclusive_ms, side="left"))
            window = prior_frame.iloc[left:right]
            rows_used = int(len(window))
            result.at[row_index, "prior_context_source"] = "cached_5m_ohlcv_for_live_prior_context"
            result.at[row_index, "prior_context_rows_used"] = rows_used
            result.at[row_index, "prior_context_start_ms"] = context_start_ms
            result.at[row_index, "prior_context_end_ms"] = context_end_exclusive_ms - 1
            if rows_used < min_expected_rows:
                result.at[row_index, "prior_context_status"] = "insufficient_coverage"
                result.at[row_index, "prior_context_reason"] = (
                    f"prior_context_24h_coverage_below_80pct:{rows_used}/{min_expected_rows}"
                )
                continue
            result.at[row_index, "prior_context_status"] = "ok"
            result.at[row_index, "prior_context_reason"] = "cached_5m_prior_context_ready"
            open_values = window["open"].astype(float)
            high_values = window["high"].astype(float)
            close_values = window["close"].astype(float)
            spike_return = (high_values / open_values) - 1.0
            spike_mask = spike_return.ge(0.03)
            spike_count = int(spike_mask.sum())
            spike_leg = high_values - open_values
            retrace_fraction = (high_values - close_values) / spike_leg.replace(0.0, np.nan)
            fast_fade_count = int((spike_mask & retrace_fraction.ge(0.55)).sum())
            result.at[row_index, "prior_spike_count_24h"] = spike_count
            # Keep legacy *_72h column names for artifact compatibility, but match
            # live2 category gating, which uses 24h closed 5m prior context.
            result.at[row_index, "prior_spike_count_72h"] = spike_count
            result.at[row_index, "prior_fast_fade_count_24h"] = fast_fade_count
            result.at[row_index, "prior_fast_fade_count_72h"] = fast_fade_count
            result.at[row_index, "prior_big_move_count_24h"] = spike_count
            result.at[row_index, "prior_big_move_count_72h"] = spike_count
            result.at[row_index, "prior_spike_density_72h"] = _safe_divide_value(
                spike_count,
                max(1.0, float(_PRIOR_CONTEXT_LIVE_LOOKBACK_HOURS) / 24.0),
            )
            spike_timestamps = window.loc[spike_mask, "timestamp"]
            if not spike_timestamps.empty:
                elapsed_ms = int(decision_ts - int(spike_timestamps.iloc[-1]))
                result.at[row_index, "time_since_prior_spike_ms"] = elapsed_ms
                result.at[row_index, "time_since_prior_spike_hours"] = _safe_divide_value(elapsed_ms, _HOUR_MS)
    return result


def _resolve_entry_cache_timeframe(cache_dir: Path, entry_timeframe: str) -> str:
    if any(cache_dir.glob(f"*%2FUSDT%3AUSDT/{entry_timeframe}/data.parquet")):
        return entry_timeframe
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if 0 < entry_ms < 60_000 and entry_ms % 1000 == 0 and any(cache_dir.glob("*%2FUSDT%3AUSDT/1s/data.parquet")):
        return "1s"
    return entry_timeframe


def _read_entry_simulation_frame(cache_dir: Path, symbol: str, entry_timeframe: str) -> pd.DataFrame:
    entry_cache_timeframe = _resolve_entry_cache_timeframe(cache_dir, entry_timeframe)
    frame = _read_symbol_frame(cache_dir, symbol, entry_cache_timeframe)
    validation_error = _flow_cache_validation_error(
        frame,
        cache_timeframe=entry_cache_timeframe,
        entry_timeframe=entry_timeframe,
    )
    if validation_error is not None:
        raise ValueError(validation_error)
    if entry_cache_timeframe == entry_timeframe:
        return frame
    aggregated = _aggregate_frame_to_timeframe(
        frame,
        timeframe_ms=_timeframe_to_milliseconds(entry_timeframe),
    )
    if aggregated.empty:
        raise ValueError(f"empty_1s_aggregation_for_entry_timeframe:{entry_timeframe}")
    return aggregated


def build_anomaly_signals(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> pd.DataFrame:
    config = _apply_red_flag_profile(config)
    if candidates.empty:
        return pd.DataFrame()
    required = {
        "price_retention_next_n",
        "start_verticality_score",
        "hold_count_next_n_candles",
        "decision_close",
        "decision_box_low",
        "decision_box_high",
        "decision_box_range",
        "decision_ema20",
        "timestamp_ms",
        "setup_available_timestamp_ms",
        "decision_timestamp_ms",
        "decision_available_timestamp_ms",
        "timestamp_semantics",
    }
    if config.min_oi_change_pct_3x5m is not None:
        required.add("oi_change_pct_3x5m")
    if config.require_oi_status_ok:
        required.add("oi_status")
    optional_filter_columns = {
        "max_start_quote_ratio": "start_quote_ratio",
        "max_start_trade_ratio": "start_trade_ratio",
        "min_baseline_quote_daily_proxy": "baseline_quote_volume_median",
        "max_start_avg_trade_quote_size_ratio": "start_avg_trade_quote_size_ratio",
        "max_start_quote_ratio_per_abs_return": "start_quote_ratio_per_abs_return",
        "min_start_range_pct_ratio_to_baseline": "start_range_pct_ratio_to_baseline",
        "max_start_range_pct_ratio_to_baseline": "start_range_pct_ratio_to_baseline",
        "max_prior_up_down_whipsaw_to_impulse_range": "prior_up_down_whipsaw_to_impulse_range",
        "min_flow_hold_count": "flow_hold_count_next_n_candles",
        "max_prior_spike_count_72h": "prior_spike_count_72h",
        "max_prior_fast_fade_count_72h": "prior_fast_fade_count_72h",
        "min_start_lower_wick_to_range": "start_lower_wick_to_range",
        "max_start_upper_wick_to_range": "start_upper_wick_to_range",
        "min_next_taker_buy_quote_share": "next_n_taker_buy_quote_share_mean",
        "max_price_retention": "price_retention_next_n",
        "min_mark_close_vs_decision_close_basis": "mark_close_vs_decision_close_basis",
        "max_start_taker_buy_quote_share_delta": "start_taker_buy_quote_share_delta",
        "max_next_taker_buy_quote_share_delta": "next_n_taker_buy_quote_share_delta",
        "max_start_trade_ratio_per_abs_return": "start_trade_ratio_per_abs_return",
        "min_runner_shape_quote_ratio": "start_quote_ratio",
        "min_runner_shape_trade_ratio": "start_trade_ratio",
        "min_runner_shape_range_ratio": "start_range_pct_ratio_to_baseline",
        "min_runner_shape_quote_acceleration": "runner_shape_quote_acceleration",
        "min_runner_shape_trade_acceleration": "runner_shape_trade_acceleration",
        "min_runner_shape_range_acceleration": "runner_shape_range_acceleration",
        "min_runner_shape_second_half_return_pct": "runner_shape_second_half_return_pct",
        "max_runner_shape_top1_quote_share": "runner_shape_top1_quote_share",
    }
    for config_field, column in optional_filter_columns.items():
        if getattr(config, config_field) is not None:
            required.add(column)
    if config.reject_oi_down_mark_discount:
        required.add("mark_close_vs_decision_close_basis")
        required.add("oi_price_interaction_3x5m")
    for flow_source_column in (
        "levels_trade_count_source",
        "entry_trade_count_source",
        "levels_quote_volume_source",
        "entry_quote_volume_source",
    ):
        required.add(flow_source_column)
    if config.reject_stale_derivatives_context:
        context_columns = _context_status_columns(candidates)
        if not context_columns:
            required.add("mark_status")
    missing = required.difference(candidates.columns)
    if missing:
        if "status" in candidates.columns and candidates["status"].astype(str).eq("error").any():
            return pd.DataFrame()
        raise ValueError(f"candidates missing required columns: {sorted(missing)}")

    signals = candidates.copy()
    signals["entry_price"] = signals["decision_close"].astype(float)
    box_low = signals["decision_box_low"].astype(float)
    box_range = signals["decision_box_range"].astype(float).clip(lower=0.0)
    decision_ema20 = signals["decision_ema20"].astype(float)
    previous_stop = box_low - config.stop_buffer_range_fraction * box_range
    signals["initial_stop_at_decision"] = pd.concat([previous_stop, decision_ema20], axis=1).max(axis=1)
    signals["initial_risk_pct_at_decision"] = (
        (signals["entry_price"] - signals["initial_stop_at_decision"]) / signals["entry_price"]
    )
    mask = (
        signals["price_retention_next_n"].astype(float).ge(config.min_price_retention)
        & signals["start_verticality_score"].astype(float).ge(config.min_verticality_score)
        & signals["hold_count_next_n_candles"].astype(float).ge(config.min_hold_count)
        & signals["initial_risk_pct_at_decision"].gt(0.0)
        & signals["initial_risk_pct_at_decision"].le(config.max_initial_risk_pct)
        & _candidate_availability_mask(signals)
        & _candidate_flow_source_mask(signals)
    )
    if config.min_initial_risk_pct is not None:
        mask &= signals["initial_risk_pct_at_decision"].ge(config.min_initial_risk_pct)
    if config.min_oi_change_pct_3x5m is not None:
        mask &= signals["oi_change_pct_3x5m"].astype(float).gt(config.min_oi_change_pct_3x5m)
    if config.require_oi_status_ok:
        mask &= signals["oi_status"].eq("ok")
    if config.max_price_retention is not None:
        mask &= signals["price_retention_next_n"].astype(float).le(config.max_price_retention)
    if config.max_start_quote_ratio is not None:
        mask &= signals["start_quote_ratio"].astype(float).le(config.max_start_quote_ratio)
    if config.max_start_trade_ratio is not None:
        mask &= signals["start_trade_ratio"].astype(float).le(config.max_start_trade_ratio)
    if config.min_baseline_quote_daily_proxy is not None:
        setup_minutes = signals["setup_timeframe"].astype(str).map({"1m": 1.0, "5m": 5.0})
        baseline_quote_daily_proxy = signals["baseline_quote_volume_median"].astype(float) * (1440.0 / setup_minutes)
        mask &= baseline_quote_daily_proxy.ge(config.min_baseline_quote_daily_proxy).fillna(False)
    if config.max_start_avg_trade_quote_size_ratio is not None:
        mask &= signals["start_avg_trade_quote_size_ratio"].astype(float).le(config.max_start_avg_trade_quote_size_ratio)
    if config.max_start_quote_ratio_per_abs_return is not None:
        mask &= signals["start_quote_ratio_per_abs_return"].astype(float).le(
            config.max_start_quote_ratio_per_abs_return
        )
    if config.min_start_range_pct_ratio_to_baseline is not None:
        mask &= signals["start_range_pct_ratio_to_baseline"].astype(float).ge(
            config.min_start_range_pct_ratio_to_baseline
        )
    if config.max_start_range_pct_ratio_to_baseline is not None:
        mask &= signals["start_range_pct_ratio_to_baseline"].astype(float).le(
            config.max_start_range_pct_ratio_to_baseline
        )
    if config.min_runner_shape_quote_ratio is not None:
        mask &= signals["start_quote_ratio"].astype(float).ge(config.min_runner_shape_quote_ratio)
    if config.min_runner_shape_trade_ratio is not None:
        mask &= signals["start_trade_ratio"].astype(float).ge(config.min_runner_shape_trade_ratio)
    if config.min_runner_shape_range_ratio is not None:
        mask &= signals["start_range_pct_ratio_to_baseline"].astype(float).ge(config.min_runner_shape_range_ratio)
    if config.min_runner_shape_quote_acceleration is not None:
        mask &= signals["runner_shape_quote_acceleration"].astype(float).ge(config.min_runner_shape_quote_acceleration)
    if config.min_runner_shape_trade_acceleration is not None:
        mask &= signals["runner_shape_trade_acceleration"].astype(float).ge(config.min_runner_shape_trade_acceleration)
    if config.min_runner_shape_range_acceleration is not None:
        mask &= signals["runner_shape_range_acceleration"].astype(float).ge(config.min_runner_shape_range_acceleration)
    if config.min_runner_shape_second_half_return_pct is not None:
        mask &= signals["runner_shape_second_half_return_pct"].astype(float).ge(config.min_runner_shape_second_half_return_pct)
    if config.max_runner_shape_top1_quote_share is not None:
        mask &= signals["runner_shape_top1_quote_share"].astype(float).le(config.max_runner_shape_top1_quote_share)
    if config.max_prior_up_down_whipsaw_to_impulse_range is not None:
        mask &= signals["prior_up_down_whipsaw_to_impulse_range"].astype(float).le(
            config.max_prior_up_down_whipsaw_to_impulse_range
        )
    if config.min_next_taker_buy_quote_share is not None:
        mask &= signals["next_n_taker_buy_quote_share_mean"].astype(float).ge(
            config.min_next_taker_buy_quote_share
        )
    if config.min_flow_hold_count is not None:
        mask &= signals["flow_hold_count_next_n_candles"].astype(float).ge(config.min_flow_hold_count)
    if config.max_prior_spike_count_72h is not None:
        mask &= signals.get("prior_context_status", pd.Series("", index=signals.index)).astype(str).eq("ok")
        mask &= signals["prior_spike_count_72h"].astype(float).le(config.max_prior_spike_count_72h)
    if config.max_prior_fast_fade_count_72h is not None:
        mask &= signals.get("prior_context_status", pd.Series("", index=signals.index)).astype(str).eq("ok")
        mask &= (
            signals["prior_fast_fade_count_72h"]
            .astype(float)
            .le(config.max_prior_fast_fade_count_72h)
        )
    if config.min_start_lower_wick_to_range is not None:
        mask &= signals["start_lower_wick_to_range"].astype(float).gt(config.min_start_lower_wick_to_range)
    if config.max_start_upper_wick_to_range is not None:
        mask &= signals["start_upper_wick_to_range"].astype(float).le(config.max_start_upper_wick_to_range)
    for red_flag_mask in _red_flag_violation_masks(signals, config=config).values():
        mask &= ~red_flag_mask.fillna(True)
    signals = signals.loc[mask].copy()
    if signals.empty:
        return signals
    signals.sort_values(["symbol", "decision_timestamp_ms"], inplace=True)
    signals.reset_index(drop=True, inplace=True)
    return signals


def annotate_pump_categories(
    signals: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> pd.DataFrame:
    category_columns = (
        "pump_category_id",
        "pump_category_rank",
        "pump_category_matches",
        "pump_category_family",
        "pump_category_is_live_rule",
        "pump_category_contract",
        "pump_category_source",
    )
    if signals.empty:
        result = signals.copy()
        for column in category_columns:
            if column not in result.columns:
                result[column] = pd.Series(dtype="object")
        return result
    key_columns = ["symbol", "setup_timeframe", "entry_timeframe", "decision_timestamp_ms"]
    if not set(key_columns).issubset(signals.columns) or not set(key_columns).issubset(candidates.columns):
        raise ValueError(f"pump category annotation requires columns: {key_columns}")
    result = signals.copy()
    category_keys: dict[str, set[tuple[str, str, str, int]]] = {}
    for category_id in PUMP_CATEGORY_PROFILE_ORDER:
        category_config = replace(config, red_flag_profile=category_id)
        category_signals = build_anomaly_signals(candidates, config=category_config)
        keys: set[tuple[str, str, str, int]] = set()
        if not category_signals.empty:
            for row in category_signals.loc[:, key_columns].itertuples(index=False, name=None):
                symbol, setup_tf, entry_tf, decision_ts = row
                keys.add((str(symbol), str(setup_tf), str(entry_tf), int(decision_ts)))
        category_keys[category_id] = keys

    selected_categories: list[str] = []
    selected_ranks: list[int] = []
    category_matches: list[str] = []
    category_families: list[str] = []
    category_is_live_rule: list[bool] = []
    category_sources: list[str] = []
    for row in result.loc[:, key_columns].itertuples(index=False, name=None):
        setup_tf = str(row[1])
        entry_tf = str(row[2])
        priority = _category_priority_for_timeframe(setup_tf, entry_tf)
        category_ranks = {category_id: rank for rank, category_id in enumerate(priority, start=1)}
        discovery_rank = len(priority) + 1
        key = (str(row[0]), setup_tf, entry_tf, int(row[3]))
        matches = [category_id for category_id in priority if key in category_keys[category_id]]
        if matches:
            selected = matches[0]
            selected_categories.append(selected)
            selected_ranks.append(category_ranks[selected])
            category_matches.append(",".join(matches))
            category_families.append(PUMP_CATEGORY_FAMILY_LIVE)
            category_is_live_rule.append(True)
            category_sources.append(f"{PUMP_CATEGORY_CONTRACT}:live_priority")
        else:
            selected_categories.append(PUMP_CATEGORY_DISCOVERY)
            selected_ranks.append(discovery_rank)
            category_matches.append(PUMP_CATEGORY_DISCOVERY)
            category_families.append(PUMP_CATEGORY_FAMILY_DISCOVERY)
            category_is_live_rule.append(False)
            category_sources.append(f"{PUMP_CATEGORY_CONTRACT}:discovery_fallback")
    result["pump_category_id"] = selected_categories
    result["pump_category_rank"] = selected_ranks
    result["pump_category_matches"] = category_matches
    result["pump_category_family"] = category_families
    result["pump_category_is_live_rule"] = category_is_live_rule
    result["pump_category_contract"] = PUMP_CATEGORY_CONTRACT
    result["pump_category_source"] = category_sources
    return result


def _resolve_signal_entry(
    frame: pd.DataFrame,
    *,
    anomaly_timestamp_ms: int,
    decision_timestamp_ms: int,
    decision_close: float,
    config: AnomalyBacktestConfig,
    execution_frame: pd.DataFrame | None = None,
) -> tuple[int, float, float, float, float, float, str, dict[str, object]]:
    box = frame.loc[
        (frame["timestamp"] >= anomaly_timestamp_ms)
        & (frame["timestamp"] <= decision_timestamp_ms)
    ]
    if box.empty:
        return decision_timestamp_ms, float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), "empty_decision_box", {}
    box_low = float(box["low"].min())
    box_high = float(box["high"].max())
    box_range = max(box_high - box_low, 0.0)
    previous_stop = box_low - config.stop_buffer_range_fraction * box_range
    decision_row = frame.loc[frame["timestamp"].eq(decision_timestamp_ms)]
    decision_ema20 = (
        _safe_float(decision_row["ema20"].iloc[0])
        if not decision_row.empty and "ema20" in decision_row.columns
        else None
    )
    stop_at_decision = max(previous_stop, decision_ema20) if decision_ema20 is not None else previous_stop
    signal_risk = decision_close - stop_at_decision
    signal_tp1_basis_price, signal_tp1_risk = _tp1_pump_leg_risk_from_values(
        entry_price=decision_close,
        box_low=box_low,
    )
    base_signal_tp1_price = (
        decision_close + config.tp1_r * signal_tp1_risk if np.isfinite(signal_tp1_risk) else float("nan")
    )
    signal_tp1_price, _signal_tp1_round_step = _round_up_tp1_to_market_number(
        base_signal_tp1_price,
        reference_price=decision_close,
        movement=max(signal_tp1_risk, box_range),
    )

    if config.entry_method == "market":
        if config.market_entry_latency_candles < 1:
            raise ValueError("market_entry_latency_candles must be >= 1")
        future = frame.loc[frame["timestamp"] > decision_timestamp_ms].head(config.market_entry_latency_candles)
        if len(future) < config.market_entry_latency_candles:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "no_market_execution_candle", {}
        pre_entry_start_ts = int(future.iloc[0]["timestamp"])
        entry_row_source = future.iloc[-1]
        entry_ts = int(entry_row_source["timestamp"])
        raw_entry_price = float(entry_row_source["open"])
        if config.latency_enabled:
            latency_frame = execution_frame if execution_frame is not None else pd.DataFrame()
            if latency_frame.empty or "timestamp" not in latency_frame.columns:
                return entry_ts, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "no_latency_execution_frame", {}
            target_ts = int(entry_ts) + max(0, int(config.latency_extra_ms))
            latency_ts = pd.to_numeric(latency_frame["timestamp"], errors="coerce")
            target_bucket_end_ts = int(target_ts) + 1000
            latency_rows = latency_frame.loc[latency_ts.ge(target_ts) & latency_ts.lt(target_bucket_end_ts)]
            if latency_rows.empty:
                return target_ts, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "no_latency_execution_candle", {}
            entry_row_source = latency_rows.iloc[0]
            entry_ts = int(entry_row_source["timestamp"])
            raw_entry_price = float(entry_row_source["open"])
        if not np.isfinite(raw_entry_price) or raw_entry_price <= 0.0:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "invalid_market_execution_price", {}
        entry_price = _long_entry_fill_price(raw_entry_price, config=config)
        drift_pct = _safe_divide_value(entry_price - decision_close, decision_close)
        abs_drift_pct = abs(drift_pct) if np.isfinite(drift_pct) else float("nan")
        actual_risk_at_signal_stop = entry_price - stop_at_decision
        rr_to_signal_tp1 = _safe_divide_value(signal_tp1_price - entry_price, actual_risk_at_signal_stop)
        reject_audit = _market_entry_reject_audit(
            entry_ts=entry_ts,
            raw_entry_price=raw_entry_price,
            entry_price=entry_price,
            drift_pct=drift_pct,
            abs_drift_pct=abs_drift_pct,
            actual_risk_at_signal_stop=actual_risk_at_signal_stop,
            rr_to_signal_tp1=rr_to_signal_tp1,
            signal_tp1_price=signal_tp1_price,
            config=config,
        )
        pre_entry_frame = execution_frame if config.latency_enabled and execution_frame is not None and not execution_frame.empty else frame
        if "timestamp" in pre_entry_frame.columns and "high" in pre_entry_frame.columns:
            pre_entry_timestamps = pd.to_numeric(pre_entry_frame["timestamp"], errors="coerce")
            pre_entry_rows = pre_entry_frame.loc[
                pre_entry_timestamps.ge(int(pre_entry_start_ts))
                & pre_entry_timestamps.lt(int(entry_ts))
            ].copy()
            if not pre_entry_rows.empty:
                pre_entry_high = pd.to_numeric(pre_entry_rows["high"], errors="coerce")
                tp1_reached = pre_entry_rows.loc[pre_entry_high.ge(float(signal_tp1_price))]
                if not tp1_reached.empty:
                    reject_audit = dict(reject_audit)
                    reject_audit["pre_entry_tp1_reached_timestamp_ms"] = int(tp1_reached.iloc[0]["timestamp"])
                    reject_audit["pre_entry_tp1_reached_timestamp_utc"] = _timestamp_to_utc(int(tp1_reached.iloc[0]["timestamp"]))
                    reject_audit["pre_entry_tp1_reached_high"] = float(pre_entry_high.loc[tp1_reached.index[0]])
                    return (
                        entry_ts,
                        float("nan"),
                        stop_at_decision,
                        float("nan"),
                        box_range,
                        box_high,
                        "tp1_already_reached_before_market_entry",
                        reject_audit,
                    )
        if entry_price >= signal_tp1_price:
            return (
                entry_ts,
                float("nan"),
                stop_at_decision,
                float("nan"),
                box_range,
                box_high,
                "tp1_already_reached_before_market_entry",
                reject_audit,
            )
        if not np.isfinite(actual_risk_at_signal_stop) or actual_risk_at_signal_stop <= 0.0:
            return (
                entry_ts,
                float("nan"),
                stop_at_decision,
                float("nan"),
                box_range,
                box_high,
                "invalid_actual_market_risk",
                reject_audit,
            )
        if not np.isfinite(abs_drift_pct) or abs_drift_pct > config.max_market_entry_drift_pct:
            return (
                entry_ts,
                float("nan"),
                stop_at_decision,
                float("nan"),
                box_range,
                box_high,
                "market_entry_price_drift",
                reject_audit,
            )
        if not np.isfinite(rr_to_signal_tp1) or rr_to_signal_tp1 < config.min_market_rr_to_signal_tp1:
            return (
                entry_ts,
                float("nan"),
                stop_at_decision,
                float("nan"),
                box_range,
                box_high,
                "market_entry_rr_collapsed",
                reject_audit,
            )
        initial_stop = stop_at_decision
        initial_risk = entry_price - initial_stop
        entry_audit = _entry_fill_audit(
            raw_entry_price=raw_entry_price,
            entry_price=entry_price,
            config=config,
            model="next_bar_open_proxy_plus_adverse_slippage",
        )
        return entry_ts, entry_price, initial_stop, initial_risk, box_range, box_high, "", entry_audit
    else:
        future = frame.loc[frame["timestamp"] > decision_timestamp_ms].head(config.entry_timeout_candles)
        if future.empty:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "entry_timeout_no_future_candles", {}
        if config.entry_method == "break_box_high":
            trigger = box_high
            hit = future.loc[future["high"].astype(float).ge(trigger)]
            if hit.empty:
                return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "entry_trigger_not_reached", {}
            entry_ts = int(hit["timestamp"].iloc[0])
            raw_entry_price = trigger
            entry_price = _long_entry_fill_price(raw_entry_price, config=config)
        elif config.entry_method == "pullback_box_fraction":
            trigger = box_low + config.pullback_box_fraction * box_range
            entry_ts = decision_timestamp_ms
            entry_price = float("nan")
            raw_entry_price = float("nan")
            for _, row in future.iterrows():
                low = float(row["low"])
                high = float(row["high"])
                if low <= stop_at_decision:
                    return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "stop_touched_before_entry", {}
                if low <= trigger <= high:
                    entry_ts = int(row["timestamp"])
                    raw_entry_price = trigger
                    entry_price = _long_entry_fill_price(raw_entry_price, config=config)
                    break
            if not np.isfinite(entry_price):
                return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "entry_trigger_not_reached", {}
        else:
            raise ValueError(f"unsupported entry_method: {config.entry_method}")

    entry_row = frame.loc[frame["timestamp"].eq(entry_ts)]
    entry_ema20 = _safe_float(entry_row["ema20"].iloc[0]) if not entry_row.empty and "ema20" in entry_row.columns else None
    initial_stop = max(previous_stop, entry_ema20) if entry_ema20 is not None else previous_stop
    initial_risk = entry_price - initial_stop
    entry_audit = _entry_fill_audit(
        raw_entry_price=raw_entry_price,
        entry_price=entry_price,
        config=config,
        model=f"{config.entry_method}_trigger_plus_adverse_slippage",
    )
    return entry_ts, entry_price, initial_stop, initial_risk, box_range, box_high, "", entry_audit



def collect_pair_anomaly_rows(
    config: AnomalyBacktestConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str | None = None,
    include_derivatives_context: bool = True,
    auto_targeted_flow_backfill: bool = True,
    speed_diagnostics: list[dict[str, object]] | None = None,
) -> pd.DataFrame:
    setup_timeframe = _effective_setup_timeframe(config)
    entry_timeframe = _effective_entry_timeframe(config)
    if (
        auto_targeted_flow_backfill
        and entry_timeframe != setup_timeframe
        and 0 < _timeframe_to_milliseconds(entry_timeframe) < 60_000
    ):
        ensure_targeted_subminute_flow_cache_for_configs(
            [config],
            symbols=symbols,
            progress_label=progress_label or "anomaly targeted flow",
        )
    entry_cache_timeframe = _resolve_entry_cache_timeframe(config.lab_config.cache_dir, entry_timeframe)
    lab_config = config.lab_config
    end_ms = lab_config.end_timestamp_ms
    if end_ms is None:
        # Use entry timeframe as the source of truth for executable decisions.
        max_timestamp: int | None = None
        for path in lab_config.cache_dir.glob(f"*/{entry_cache_timeframe}/data.parquet"):
            try:
                timestamps = pd.read_parquet(path, columns=["timestamp"])
            except Exception:
                continue
            if timestamps.empty:
                continue
            current = int(timestamps["timestamp"].max())
            max_timestamp = current if max_timestamp is None else max(max_timestamp, current)
        if max_timestamp is None:
            end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        else:
            end_ms = max_timestamp
    start_ms = int((datetime.fromtimestamp(int(end_ms) / 1000, UTC) - pd.Timedelta(days=lab_config.days)).timestamp() * 1000)
    wanted_symbols = set(_normalized_symbol_tuple(symbols))
    paths = sorted(lab_config.cache_dir.glob(f"*%2FUSDT%3AUSDT/{setup_timeframe}/data.parquet"))
    if wanted_symbols:
        paths = [
            path
            for path in paths
            if normalize_symbol(_symbol_from_cache_symbol_dir(path.parent.parent)) in wanted_symbols
        ]
    if entry_timeframe != setup_timeframe and _timeframe_to_milliseconds(entry_timeframe) < 60_000:
        # File-presence prefilter only. The exact trusted-flow cache validation
        # remains in the per-symbol path below, after the frame is loaded once.
        symbols_with_entry_cache = _cache_symbols_for_timeframe(
            lab_config.cache_dir,
            entry_cache_timeframe,
        )
        if not symbols_with_entry_cache:
            return pd.DataFrame([
                {
                    "symbol": "__all__",
                    "timeframe": setup_timeframe,
                    "setup_timeframe": setup_timeframe,
                    "entry_timeframe": entry_timeframe,
                    "feature_contract": "htf_setup_ltf_entry_v1",
                    "status": "error",
                    "error": f"missing_subminute_entry_cache:{entry_timeframe}",
                    "execution_model": "requires_historical_aggtrades_cache",
                }
            ])
        paths = [path for path in paths if _symbol_from_cache_symbol_dir(path.parent.parent) in symbols_with_entry_cache]
    rows: list[dict[str, object]] = []
    progress_started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, path in enumerate(paths, start=1):
        symbol = _symbol_from_cache_symbol_dir(path.parent.parent)
        symbol_started_at = time.monotonic()
        setup_read_seconds = 0.0
        entry_read_seconds = 0.0
        validation_seconds = 0.0
        slice_aggregate_seconds = 0.0
        collect_seconds = 0.0
        output_row_count = 0
        status = "ok"
        error = ""
        try:
            read_started_at = time.monotonic()
            setup_frame = _read_symbol_frame(lab_config.cache_dir, symbol, setup_timeframe)
            setup_read_seconds = time.monotonic() - read_started_at
            read_started_at = time.monotonic()
            entry_frame = _read_symbol_frame(lab_config.cache_dir, symbol, entry_cache_timeframe)
            entry_read_seconds = time.monotonic() - read_started_at
            validation_started_at = time.monotonic()
            validation_error = _flow_cache_validation_error(
                entry_frame,
                cache_timeframe=entry_cache_timeframe,
                entry_timeframe=entry_timeframe,
            )
            validation_seconds = time.monotonic() - validation_started_at
            if validation_error is not None:
                raise ValueError(validation_error)
            slice_started_at = time.monotonic()
            setup_frame = _slice_ohlcv_asof_window(
                setup_frame,
                timeframe=setup_timeframe,
                start_timestamp_ms=start_ms - lab_config.baseline_candles * _timeframe_to_milliseconds(setup_timeframe),
                end_timestamp_ms=int(end_ms),
            )
            entry_left_context_ms = _post_htf_ltf_left_context_ms(config)
            entry_frame = _slice_ohlcv_asof_window(
                entry_frame,
                timeframe=entry_cache_timeframe,
                start_timestamp_ms=start_ms - entry_left_context_ms,
                end_timestamp_ms=int(end_ms),
            )
            if entry_cache_timeframe != entry_timeframe:
                entry_frame = _aggregate_frame_to_timeframe(
                    entry_frame,
                    timeframe_ms=_timeframe_to_milliseconds(entry_timeframe),
                )
                entry_frame = _slice_ohlcv_asof_window(
                    entry_frame,
                    timeframe=entry_timeframe,
                    start_timestamp_ms=start_ms - entry_left_context_ms,
                    end_timestamp_ms=int(end_ms),
                )
                entry_flow_source = f"cached_{entry_cache_timeframe}_aggregated_to_{entry_timeframe}"
            else:
                entry_flow_source = _materialized_entry_flow_source(entry_frame, entry_timeframe=entry_timeframe)
            slice_aggregate_seconds = time.monotonic() - slice_started_at
            pair_collection_mode = str(config.pair_collection_mode)
            if pair_collection_mode == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER:
                collector = _collect_symbol_bare_htf_short_fader_rows
            elif pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION:
                collector = _collect_symbol_post_htf_close_ltf_forward_rows
            elif pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION:
                collector = _collect_symbol_post_htf_close_ltf_rows
            else:
                collector = _collect_symbol_pair_rows
            collect_started_at = time.monotonic()
            symbol_rows = collector(
                symbol=symbol,
                setup_frame=setup_frame,
                entry_frame=entry_frame,
                config=config,
                entry_flow_source=entry_flow_source,
            )
            collect_seconds = time.monotonic() - collect_started_at
            output_row_count = len(symbol_rows)
            rows.extend(symbol_rows)
        except Exception as exc:
            status = f"error:{type(exc).__name__}"
            error = str(exc)
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": setup_timeframe,
                    "setup_timeframe": setup_timeframe,
                    "entry_timeframe": entry_timeframe,
                    "feature_contract": "htf_setup_ltf_entry_v1",
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            output_row_count = 1
        finally:
            append_speed_diagnostic(
                speed_diagnostics,
                stage="candidate_collect_symbol",
                scope=f"{setup_timeframe}/{entry_timeframe}",
                symbol=symbol,
                status=status,
                seconds=time.monotonic() - symbol_started_at,
                output_rows=output_row_count,
                item_count=1,
                extra={
                    "setup_timeframe": setup_timeframe,
                    "entry_timeframe": entry_timeframe,
                    "entry_cache_timeframe": entry_cache_timeframe,
                    "setup_read_seconds": round(setup_read_seconds, 6),
                    "entry_read_seconds": round(entry_read_seconds, 6),
                    "flow_validation_seconds": round(validation_seconds, 6),
                    "slice_aggregate_seconds": round(slice_aggregate_seconds, 6),
                    "collector_seconds": round(collect_seconds, 6),
                    "error": error,
                },
            )
        if progress_label is not None and paths:
            current_pct = int(100 * processed_count / len(paths))
            if current_pct >= next_progress_pct or processed_count == len(paths):
                _emit_progress(label=progress_label, done=processed_count, total=len(paths), started_at=progress_started_at)
                next_progress_pct = current_pct + 5
    result = pd.DataFrame(rows)
    if not result.empty and "timestamp_ms" in result.columns:
        result.sort_values(["timestamp_ms", "symbol", "decision_timestamp_ms"], inplace=True)
        result.reset_index(drop=True, inplace=True)
    result = enrich_candidates_with_recent_spike_context(result, config=config)
    result = enrich_candidates_with_open_interest(result, cache_dir=lab_config.cache_dir, progress_label=f"{progress_label}: oi context" if progress_label is not None else None)
    if include_derivatives_context:
        result = enrich_candidates_with_derivatives_context(result, cache_dir=lab_config.cache_dir, progress_label=f"{progress_label}: derivatives context" if progress_label is not None else None)
    return result


def _pair_key(config: AnomalyBacktestConfig) -> tuple[str, str]:
    return (_effective_setup_timeframe(config), _effective_entry_timeframe(config))


def _resolve_pair_collection_window(config: AnomalyBacktestConfig) -> tuple[int, int, str]:
    lab_config = config.lab_config
    entry_timeframe = _effective_entry_timeframe(config)
    entry_cache_timeframe = _resolve_entry_cache_timeframe(lab_config.cache_dir, entry_timeframe)
    end_ms = lab_config.end_timestamp_ms
    if end_ms is None:
        max_timestamp: int | None = None
        for path in lab_config.cache_dir.glob(f"*/{entry_cache_timeframe}/data.parquet"):
            try:
                timestamps = pd.read_parquet(path, columns=["timestamp"])
            except Exception:
                continue
            if timestamps.empty:
                continue
            current = int(timestamps["timestamp"].max())
            max_timestamp = current if max_timestamp is None else max(max_timestamp, current)
        end_ms = int(datetime.now(tz=UTC).timestamp() * 1000) if max_timestamp is None else max_timestamp
    start_ms = int(
        (
            datetime.fromtimestamp(int(end_ms) / 1000, UTC)
            - pd.Timedelta(days=lab_config.days)
        ).timestamp()
        * 1000
    )
    return start_ms, int(end_ms), entry_cache_timeframe


def _cache_symbols_for_timeframe(cache_dir: Path, timeframe: str) -> set[str]:
    return {
        _symbol_from_cache_symbol_dir(path.parent.parent)
        for path in cache_dir.glob(f"*%2FUSDT%3AUSDT/{timeframe}/data.parquet")
    }


def _trusted_flow_cache_symbols_for_timeframe(cache_dir: Path, *, cache_timeframe: str, entry_timeframe: str) -> set[str]:
    """Return symbols whose entry-flow cache is trusted using metadata-only reads.

    The full frame is still validated again before collection.  This prefilter is
    only an early reject gate, so it must be exact on the version/source contract
    without paying the cost of loading every full subminute parquet file.
    """

    symbols: set[str] = set()
    metadata_columns = ["aggregation_version"]
    if str(cache_timeframe) != "1s":
        metadata_columns.append("aggregation_source_timeframe")
    for path in cache_dir.glob(f"*%2FUSDT%3AUSDT/{cache_timeframe}/data.parquet"):
        symbol = _symbol_from_cache_symbol_dir(path.parent.parent)
        metadata = _read_parquet_columns(path, metadata_columns)
        if metadata.empty:
            continue
        if _flow_cache_validation_error(
            metadata,
            cache_timeframe=cache_timeframe,
            entry_timeframe=entry_timeframe,
        ) is None:
            symbols.add(symbol)
    return symbols


def collect_pair_anomaly_rows_for_configs(
    configs: Iterable[AnomalyBacktestConfig],
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str | None = None,
    include_derivatives_context: bool = True,
    auto_targeted_flow_backfill: bool = True,
    speed_diagnostics: list[dict[str, object]] | None = None,
) -> dict[tuple[str, str], pd.DataFrame]:
    """Collect pair candidates in one symbol-major pass across multiple TF sets."""

    resolved_configs = [_apply_red_flag_profile(config) for config in configs]
    if not resolved_configs:
        return {}
    if auto_targeted_flow_backfill:
        ensure_targeted_subminute_flow_cache_for_configs(
            resolved_configs,
            symbols=symbols,
            progress_label=progress_label or "anomaly targeted flow",
        )

    wanted_symbols = set(_normalized_symbol_tuple(symbols))
    trusted_entry_symbol_cache: dict[tuple[str, str], set[str]] = {}
    states: list[dict[str, object]] = []
    rows_by_key: dict[tuple[str, str], list[dict[str, object]]] = {}
    for config in resolved_configs:
        setup_timeframe, entry_timeframe = _pair_key(config)
        key = (setup_timeframe, entry_timeframe)
        rows_by_key.setdefault(key, [])
        start_ms, end_ms, entry_cache_timeframe = _resolve_pair_collection_window(config)
        setup_symbols = _cache_symbols_for_timeframe(config.lab_config.cache_dir, setup_timeframe)
        if wanted_symbols:
            setup_symbols = {symbol for symbol in setup_symbols if normalize_symbol(symbol) in wanted_symbols}
        entry_ms = _timeframe_to_milliseconds(entry_timeframe)
        eligible_symbols = set(setup_symbols)
        if entry_timeframe != setup_timeframe and entry_ms < 60_000:
            # Keep the early trusted-cache gate.  P419's file-presence-only gate
            # was safe but could push invalid/partial subminute caches into the
            # expensive main symbol pass.  The prefilter now uses narrow metadata
            # reads and is cached per entry-cache/entry-timeframe pair.
            trusted_key = (entry_cache_timeframe, entry_timeframe)
            prefilter_started_at = time.monotonic()
            cached_entry_symbols = trusted_entry_symbol_cache.get(trusted_key)
            if cached_entry_symbols is None:
                cached_entry_symbols = _trusted_flow_cache_symbols_for_timeframe(
                    config.lab_config.cache_dir,
                    cache_timeframe=entry_cache_timeframe,
                    entry_timeframe=entry_timeframe,
                )
                trusted_entry_symbol_cache[trusted_key] = cached_entry_symbols
                status = "computed"
            else:
                status = "cached"
            append_speed_diagnostic(
                speed_diagnostics,
                stage="trusted_entry_cache_prefilter",
                scope=f"{entry_cache_timeframe}->{entry_timeframe}",
                status=status,
                seconds=time.monotonic() - prefilter_started_at,
                item_count=len(cached_entry_symbols),
            )
            entry_symbols = set(cached_entry_symbols)
            if wanted_symbols:
                entry_symbols = {symbol for symbol in entry_symbols if normalize_symbol(symbol) in wanted_symbols}
            if not entry_symbols:
                rows_by_key[key].append(
                    {
                        "symbol": "__all__",
                        "timeframe": setup_timeframe,
                        "setup_timeframe": setup_timeframe,
                        "entry_timeframe": entry_timeframe,
                        "feature_contract": "htf_setup_ltf_entry_v1",
                        "status": "error",
                        "error": f"missing_subminute_entry_cache:{entry_timeframe}",
                        "execution_model": "requires_historical_aggtrades_cache",
                    }
                )
                eligible_symbols = set()
            else:
                eligible_symbols &= entry_symbols
        states.append(
            {
                "config": config,
                "key": key,
                "setup_timeframe": setup_timeframe,
                "entry_timeframe": entry_timeframe,
                "entry_cache_timeframe": entry_cache_timeframe,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "eligible_symbols": eligible_symbols,
            }
        )

    all_symbols = sorted(
        {
            symbol
            for state in states
            for symbol in state["eligible_symbols"]  # type: ignore[union-attr]
        }
    )
    cache_dir = resolved_configs[0].lab_config.cache_dir

    def _collect_rows_for_symbol(
        symbol: str,
    ) -> tuple[dict[tuple[str, str], list[dict[str, object]]], list[dict[str, object]]]:
        local_rows_by_key: dict[tuple[str, str], list[dict[str, object]]] = {}
        local_diagnostics: list[dict[str, object]] = []
        symbol_total_started_at = time.monotonic()
        symbol_states = [
            state
            for state in states
            if symbol in state["eligible_symbols"]  # type: ignore[operator]
        ]
        frame_cache: dict[str, pd.DataFrame] = {}
        frame_errors: dict[str, Exception] = {}
        required_timeframes = sorted(
            {
                str(state["setup_timeframe"])
                for state in symbol_states
            }
            | {
                str(state["entry_cache_timeframe"])
                for state in symbol_states
            }
        )
        read_seconds_by_timeframe: dict[str, float] = {}
        for timeframe in required_timeframes:
            read_started_at = time.monotonic()
            try:
                frame_cache[timeframe] = _read_symbol_frame(cache_dir, symbol, timeframe)
            except Exception as exc:
                frame_errors[timeframe] = exc
            finally:
                read_seconds_by_timeframe[timeframe] = time.monotonic() - read_started_at

        for state in symbol_states:
            config = state["config"]
            assert isinstance(config, AnomalyBacktestConfig)
            setup_timeframe = str(state["setup_timeframe"])
            entry_timeframe = str(state["entry_timeframe"])
            entry_cache_timeframe = str(state["entry_cache_timeframe"])
            key = state["key"]
            assert isinstance(key, tuple)
            local_rows = local_rows_by_key.setdefault(key, [])
            state_started_at = time.monotonic()
            validation_seconds = 0.0
            slice_aggregate_seconds = 0.0
            collect_seconds = 0.0
            output_row_count = 0
            status = "ok"
            error = ""
            try:
                if setup_timeframe in frame_errors:
                    raise frame_errors[setup_timeframe]
                if entry_cache_timeframe in frame_errors:
                    raise frame_errors[entry_cache_timeframe]
                validation_started_at = time.monotonic()
                validation_error = _flow_cache_validation_error(
                    frame_cache[entry_cache_timeframe],
                    cache_timeframe=entry_cache_timeframe,
                    entry_timeframe=entry_timeframe,
                )
                validation_seconds = time.monotonic() - validation_started_at
                if validation_error is not None:
                    raise ValueError(validation_error)
                start_ms = int(state["start_ms"])
                end_ms = int(state["end_ms"])
                setup_ms = _timeframe_to_milliseconds(setup_timeframe)
                slice_started_at = time.monotonic()
                setup_frame = _slice_ohlcv_asof_window(
                    frame_cache[setup_timeframe],
                    timeframe=setup_timeframe,
                    start_timestamp_ms=start_ms - config.lab_config.baseline_candles * setup_ms,
                    end_timestamp_ms=end_ms,
                )
                entry_left_context_ms = _post_htf_ltf_left_context_ms(config)
                entry_frame = _slice_ohlcv_asof_window(
                    frame_cache[entry_cache_timeframe],
                    timeframe=entry_cache_timeframe,
                    start_timestamp_ms=start_ms - entry_left_context_ms,
                    end_timestamp_ms=end_ms,
                )
                if entry_cache_timeframe != entry_timeframe:
                    entry_frame = _aggregate_frame_to_timeframe(
                        entry_frame,
                        timeframe_ms=_timeframe_to_milliseconds(entry_timeframe),
                    )
                    entry_frame = _slice_ohlcv_asof_window(
                        entry_frame,
                        timeframe=entry_timeframe,
                        start_timestamp_ms=start_ms - entry_left_context_ms,
                        end_timestamp_ms=end_ms,
                    )
                    entry_flow_source = f"cached_{entry_cache_timeframe}_aggregated_to_{entry_timeframe}"
                else:
                    entry_flow_source = _materialized_entry_flow_source(
                        entry_frame,
                        entry_timeframe=entry_timeframe,
                    )
                slice_aggregate_seconds = time.monotonic() - slice_started_at
                pair_collection_mode = str(config.pair_collection_mode)
                if pair_collection_mode == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER:
                    collector = _collect_symbol_bare_htf_short_fader_rows
                elif pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION:
                    collector = _collect_symbol_post_htf_close_ltf_forward_rows
                elif pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION:
                    collector = _collect_symbol_post_htf_close_ltf_rows
                else:
                    collector = _collect_symbol_pair_rows
                collect_started_at = time.monotonic()
                symbol_rows = collector(
                    symbol=symbol,
                    setup_frame=setup_frame,
                    entry_frame=entry_frame,
                    config=config,
                    entry_flow_source=entry_flow_source,
                )
                collect_seconds = time.monotonic() - collect_started_at
                output_row_count = len(symbol_rows)
                local_rows.extend(symbol_rows)
            except Exception as exc:
                status = f"error:{type(exc).__name__}"
                error = str(exc)
                local_rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": setup_timeframe,
                        "setup_timeframe": setup_timeframe,
                        "entry_timeframe": entry_timeframe,
                        "feature_contract": "htf_setup_ltf_entry_v1",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                output_row_count = 1
            finally:
                append_speed_diagnostic(
                    local_diagnostics,
                    stage="candidate_collect_symbol",
                    scope=f"{setup_timeframe}/{entry_timeframe}",
                    symbol=symbol,
                    status=status,
                    seconds=time.monotonic() - state_started_at,
                    output_rows=output_row_count,
                    item_count=1,
                    extra={
                        "setup_timeframe": setup_timeframe,
                        "entry_timeframe": entry_timeframe,
                        "entry_cache_timeframe": entry_cache_timeframe,
                        "required_timeframes": ",".join(required_timeframes),
                        "read_seconds_total": round(sum(read_seconds_by_timeframe.values()), 6),
                        "setup_read_seconds": round(read_seconds_by_timeframe.get(setup_timeframe, 0.0), 6),
                        "entry_read_seconds": round(read_seconds_by_timeframe.get(entry_cache_timeframe, 0.0), 6),
                        "flow_validation_seconds": round(validation_seconds, 6),
                        "slice_aggregate_seconds": round(slice_aggregate_seconds, 6),
                        "collector_seconds": round(collect_seconds, 6),
                        "error": error,
                    },
                )
        append_speed_diagnostic(
            local_diagnostics,
            stage="candidate_collect_symbol_total",
            scope="multi_tf_precollection",
            symbol=symbol,
            status="ok" if not frame_errors else "frame_errors_present",
            seconds=time.monotonic() - symbol_total_started_at,
            output_rows=sum(len(rows) for rows in local_rows_by_key.values()),
            item_count=len(symbol_states),
            extra={
                "required_timeframes": ",".join(required_timeframes),
                "read_seconds_total": round(sum(read_seconds_by_timeframe.values()), 6),
                "frame_error_count": int(len(frame_errors)),
            },
        )
        return local_rows_by_key, local_diagnostics

    progress_started_at = time.monotonic()
    next_progress_pct = 0
    symbol_results: dict[str, dict[tuple[str, str], list[dict[str, object]]]] = {}
    workers = _effective_symbol_workers(
        getattr(resolved_configs[0], "symbol_workers", DEFAULT_BACKTEST_SYMBOL_WORKERS),
        total_items=len(all_symbols),
    )
    if workers <= 1:
        for processed_count, symbol in enumerate(all_symbols, start=1):
            symbol_result, local_diagnostics = _collect_rows_for_symbol(symbol)
            symbol_results[symbol] = symbol_result
            if speed_diagnostics is not None:
                speed_diagnostics.extend(local_diagnostics)
            if progress_label is not None and all_symbols:
                current_pct = int(100 * processed_count / len(all_symbols))
                if current_pct >= next_progress_pct or processed_count == len(all_symbols):
                    _emit_progress(
                        label=progress_label,
                        done=processed_count,
                        total=len(all_symbols),
                        started_at=progress_started_at,
                    )
                    next_progress_pct = current_pct + 5
    else:
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="anomaly-pair")
        futures = {executor.submit(_collect_rows_for_symbol, symbol): symbol for symbol in all_symbols}
        try:
            for processed_count, future in enumerate(as_completed(futures), start=1):
                symbol = futures[future]
                symbol_result, local_diagnostics = future.result()
                symbol_results[symbol] = symbol_result
                if speed_diagnostics is not None:
                    speed_diagnostics.extend(local_diagnostics)
                if progress_label is not None and all_symbols:
                    current_pct = int(100 * processed_count / len(all_symbols))
                    if current_pct >= next_progress_pct or processed_count == len(all_symbols):
                        _emit_progress(
                            label=f"{progress_label} ({workers} workers)",
                            done=processed_count,
                            total=len(all_symbols),
                            started_at=progress_started_at,
                        )
                        next_progress_pct = current_pct + 5
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    for symbol in all_symbols:
        for key, rows in symbol_results.get(symbol, {}).items():
            rows_by_key.setdefault(key, []).extend(rows)

    result_by_key: dict[tuple[str, str], pd.DataFrame] = {}
    for state in states:
        config = state["config"]
        assert isinstance(config, AnomalyBacktestConfig)
        key = state["key"]
        assert isinstance(key, tuple)
        result = pd.DataFrame(rows_by_key.get(key, []))
        if not result.empty and "timestamp_ms" in result.columns:
            result.sort_values(["timestamp_ms", "symbol", "decision_timestamp_ms"], inplace=True)
            result.reset_index(drop=True, inplace=True)
        result = enrich_candidates_with_recent_spike_context(result, config=config)
        context_label = f"{progress_label} {key[0]}/{key[1]}" if progress_label is not None else None
        result = enrich_candidates_with_open_interest(
            result,
            cache_dir=config.lab_config.cache_dir,
            progress_label=f"{context_label}: oi context" if context_label is not None else None,
        )
        if include_derivatives_context:
            result = enrich_candidates_with_derivatives_context(
                result,
                cache_dir=config.lab_config.cache_dir,
                progress_label=f"{context_label}: derivatives context" if context_label is not None else None,
            )
        result_by_key[key] = result
    return result_by_key


def _entry_segment_has_full_setup_coverage(
    entry_segment: pd.DataFrame,
    *,
    setup_start_ms: int,
    setup_ms: int,
    entry_ms: int,
) -> bool:
    if entry_ms <= 0 or setup_ms <= 0 or setup_ms % entry_ms != 0:
        return False
    return _entry_segment_has_full_range_coverage(
        entry_segment,
        range_start_ms=int(setup_start_ms),
        range_end_exclusive_ms=int(setup_start_ms + setup_ms),
        entry_ms=int(entry_ms),
    )


def _collect_symbol_post_htf_close_ltf_rows(
    *,
    symbol: str,
    setup_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    config: AnomalyBacktestConfig,
    entry_flow_source: str = "cached_ohlcv",
) -> list[dict[str, object]]:
    lab_config = config.lab_config
    setup_timeframe = _effective_setup_timeframe(config)
    entry_timeframe = _effective_entry_timeframe(config)
    setup_ms = _timeframe_to_milliseconds(setup_timeframe)
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if setup_ms <= 0 or entry_ms <= 0 or entry_ms > setup_ms:
        raise ValueError(f"invalid setup/entry timeframe pair: {setup_timeframe}/{entry_timeframe}")
    if entry_ms == setup_ms:
        raise ValueError("post_htf_close_ltf_confirmation requires entry_timeframe < setup_timeframe")
    if setup_ms % entry_ms != 0:
        raise ValueError(f"entry_timeframe must evenly divide setup_timeframe: {setup_timeframe}/{entry_timeframe}")
    if setup_frame.empty or entry_frame.empty:
        return []

    setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    rows: list[dict[str, object]] = []
    setup_timestamps = setup_frame["timestamp"].astype("int64").to_numpy()
    entry_timestamps = entry_frame["timestamp"].astype("int64").to_numpy()
    baseline_quote_medians = (
        pd.to_numeric(setup_frame["quote_volume"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )
    baseline_trade_medians = (
        pd.to_numeric(setup_frame["number_of_trades"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )

    for setup_idx, setup_start in enumerate(setup_timestamps):
        setup_start = int(setup_start)
        if setup_idx < lab_config.baseline_candles:
            continue
        baseline_quote_value = float(baseline_quote_medians[setup_idx])
        baseline_trade_value = float(baseline_trade_medians[setup_idx])
        if not np.isfinite(baseline_quote_value) or not np.isfinite(baseline_trade_value):
            continue
        if baseline_quote_value <= 0.0 or baseline_trade_value <= 0.0:
            continue
        setup_row = setup_frame.iloc[setup_idx]
        start_quote = float(setup_row["quote_volume"])
        start_trades = float(setup_row["number_of_trades"])
        start_quote_ratio = _safe_divide_value(start_quote, baseline_quote_value)
        start_trade_ratio = _safe_divide_value(start_trades, baseline_trade_value)
        if (
            not np.isfinite(start_quote_ratio)
            or not np.isfinite(start_trade_ratio)
            or start_quote_ratio < lab_config.min_quote_ratio_start
            or start_trade_ratio < lab_config.min_trade_ratio_start
        ):
            continue

        entry_start_pos = int(np.searchsorted(entry_timestamps, setup_start, side="left"))
        entry_end_pos_exclusive = int(np.searchsorted(entry_timestamps, setup_start + setup_ms, side="left"))
        entry_segment = entry_frame.iloc[entry_start_pos:entry_end_pos_exclusive].copy()
        if not _entry_segment_has_full_setup_coverage(
            entry_segment,
            setup_start_ms=setup_start,
            setup_ms=setup_ms,
            entry_ms=entry_ms,
        ):
            continue
        if len(entry_segment) < lab_config.confirmation_candles:
            continue
        left_context_ms = _post_htf_ltf_left_context_ms(config)
        left_context_start = int(setup_start - left_context_ms)
        left_context = pd.DataFrame()
        left_context_status = "disabled"
        if left_context_ms > 0:
            left_start_pos = int(np.searchsorted(entry_timestamps, left_context_start, side="left"))
            left_end_pos = int(np.searchsorted(entry_timestamps, setup_start, side="left"))
            left_context = entry_frame.iloc[left_start_pos:left_end_pos].copy()
            left_context_status = (
                "ok"
                if _entry_segment_has_full_range_coverage(
                    left_context,
                    range_start_ms=left_context_start,
                    range_end_exclusive_ms=setup_start,
                    entry_ms=entry_ms,
                )
                else "missing_ltf_left_context"
            )
            if left_context_status != "ok":
                continue

        baseline = setup_frame.iloc[setup_idx - lab_config.baseline_candles : setup_idx].copy()
        if baseline.empty:
            continue
        row = _build_pair_candidate_row(
            symbol=symbol,
            setup_timeframe=setup_timeframe,
            entry_timeframe=entry_timeframe,
            setup_ms=setup_ms,
            entry_ms=entry_ms,
            setup_idx=setup_idx,
            baseline=baseline,
            setup_row=setup_row,
            entry_segment=entry_segment,
            entry_frame=entry_frame,
            config=config,
            entry_flow_source=entry_flow_source,
            setup_flow_source="cached_ohlcv",
            ltf_left_context=left_context,
            feature_contract=POST_HTF_CLOSE_LTF_CONFIRMATION_CONTRACT,
            setup_source="closed_htf_ltf_confirmation_after_close_backtest",
            timestamp_semantics_note=(
                "closed_htf_setup_available_at_full_close;"
                "ltf_confirmation_read_after_htf_close;"
                "entry_not_retroactively_inside_htf"
            ),
        )
        if row is None:
            continue
        row["setup_decision_index"] = int(len(entry_segment) - 1)
        row["candidate_collection_policy"] = "single_post_htf_close_ltf_confirmation_per_setup"
        row["post_htf_close_ltf_confirmation"] = True
        row["post_htf_close_entry_not_before_ms"] = int(setup_start + setup_ms)
        row["post_htf_close_entry_not_before_utc"] = _timestamp_to_utc(int(setup_start + setup_ms))
        row["post_htf_close_ltf_left_context_ms"] = int(left_context_ms)
        row["post_htf_close_ltf_left_context_candles"] = int(len(left_context))
        row["post_htf_close_ltf_left_context_status"] = left_context_status
        row["post_htf_close_ltf_left_context_source"] = str(entry_flow_source)
        row["post_htf_close_ltf_left_context_start_ms"] = int(left_context_start) if left_context_ms > 0 else None
        row["post_htf_close_ltf_left_context_end_ms"] = int(setup_start - entry_ms) if left_context_ms > 0 else None
        rows.append(row)
    return rows


def _collect_symbol_post_htf_close_ltf_forward_rows(
    *,
    symbol: str,
    setup_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    config: AnomalyBacktestConfig,
    entry_flow_source: str = "cached_ohlcv",
) -> list[dict[str, object]]:
    lab_config = config.lab_config
    setup_timeframe = _effective_setup_timeframe(config)
    entry_timeframe = _effective_entry_timeframe(config)
    setup_ms = _timeframe_to_milliseconds(setup_timeframe)
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if setup_ms <= 0 or entry_ms <= 0 or entry_ms >= setup_ms:
        raise ValueError("post_htf_close_ltf_forward_confirmation requires entry_timeframe below setup_timeframe")
    if setup_ms % entry_ms != 0:
        raise ValueError(f"entry_timeframe must evenly divide setup_timeframe: {setup_timeframe}/{entry_timeframe}")
    if setup_frame.empty or entry_frame.empty:
        return []

    setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    rows: list[dict[str, object]] = []
    setup_timestamps = setup_frame["timestamp"].astype("int64").to_numpy()
    entry_timestamps = entry_frame["timestamp"].astype("int64").to_numpy()
    baseline_quote_medians = (
        pd.to_numeric(setup_frame["quote_volume"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )
    baseline_trade_medians = (
        pd.to_numeric(setup_frame["number_of_trades"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )

    for setup_idx, setup_start in enumerate(setup_timestamps):
        setup_start = int(setup_start)
        if setup_idx < lab_config.baseline_candles:
            continue
        baseline_quote_value = float(baseline_quote_medians[setup_idx])
        baseline_trade_value = float(baseline_trade_medians[setup_idx])
        if not np.isfinite(baseline_quote_value) or not np.isfinite(baseline_trade_value):
            continue
        if baseline_quote_value <= 0.0 or baseline_trade_value <= 0.0:
            continue
        setup_row = setup_frame.iloc[setup_idx]
        start_quote = float(setup_row["quote_volume"])
        start_trades = float(setup_row["number_of_trades"])
        start_quote_ratio = _safe_divide_value(start_quote, baseline_quote_value)
        start_trade_ratio = _safe_divide_value(start_trades, baseline_trade_value)
        if (
            not np.isfinite(start_quote_ratio)
            or not np.isfinite(start_trade_ratio)
            or start_quote_ratio < lab_config.min_quote_ratio_start
            or start_trade_ratio < lab_config.min_trade_ratio_start
        ):
            continue

        left_context_ms = _post_htf_ltf_left_context_ms(config)
        left_context_start = int(setup_start - left_context_ms)
        left_context = pd.DataFrame()
        left_context_status = "disabled"
        if left_context_ms > 0:
            left_start_pos = int(np.searchsorted(entry_timestamps, left_context_start, side="left"))
            left_end_pos = int(np.searchsorted(entry_timestamps, setup_start, side="left"))
            left_context = entry_frame.iloc[left_start_pos:left_end_pos].copy()
            left_context_status = (
                "ok"
                if _entry_segment_has_full_range_coverage(
                    left_context,
                    range_start_ms=left_context_start,
                    range_end_exclusive_ms=setup_start,
                    entry_ms=entry_ms,
                )
                else "missing_ltf_left_context"
            )
            if left_context_status != "ok":
                continue

        forward_start = int(setup_start + setup_ms)
        forward_end = int(forward_start + setup_ms)
        forward_start_pos = int(np.searchsorted(entry_timestamps, forward_start, side="left"))
        forward_end_pos_exclusive = int(np.searchsorted(entry_timestamps, forward_end, side="left"))
        forward_segment_full = entry_frame.iloc[forward_start_pos:forward_end_pos_exclusive]
        if len(forward_segment_full) < lab_config.confirmation_candles:
            continue

        baseline = setup_frame.iloc[setup_idx - lab_config.baseline_candles : setup_idx].copy()
        if baseline.empty:
            continue
        for entry_end_pos in range(lab_config.confirmation_candles - 1, len(forward_segment_full)):
            entry_segment = forward_segment_full.iloc[: entry_end_pos + 1].copy()
            decision = entry_segment.iloc[-1]
            decision_ts = int(decision["timestamp"])
            if not _entry_segment_has_full_range_coverage(
                entry_segment,
                range_start_ms=forward_start,
                range_end_exclusive_ms=decision_ts + entry_ms,
                entry_ms=entry_ms,
            ):
                continue
            row = _build_pair_candidate_row(
                symbol=symbol,
                setup_timeframe=setup_timeframe,
                entry_timeframe=entry_timeframe,
                setup_ms=setup_ms,
                entry_ms=entry_ms,
                setup_idx=setup_idx,
                baseline=baseline,
                setup_row=setup_row,
                entry_segment=entry_segment,
                entry_frame=entry_frame,
                config=config,
                entry_flow_source=entry_flow_source,
                setup_flow_source="cached_ohlcv",
                ltf_left_context=left_context,
                setup_elapsed_fraction_override=1.0,
                feature_contract=POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION_CONTRACT,
                setup_source="closed_htf_ltf_forward_confirmation_after_close_backtest",
                timestamp_semantics_note=(
                    "closed_htf_setup_available_at_full_close;"
                    "ltf_left_context_read_after_htf_close;"
                    "ltf_forward_confirmation_after_htf_close;"
                    "entry_not_retroactively_inside_htf"
                ),
            )
            if row is None:
                continue
            row["setup_decision_index"] = int(entry_end_pos)
            row["candidate_collection_policy"] = "all_ltf_forward_confirmations_after_post_htf_close"
            row["post_htf_close_ltf_confirmation"] = False
            row["post_htf_close_ltf_forward_confirmation"] = True
            row["post_htf_close_entry_not_before_ms"] = int(forward_start)
            row["post_htf_close_entry_not_before_utc"] = _timestamp_to_utc(int(forward_start))
            row["post_htf_close_ltf_left_context_ms"] = int(left_context_ms)
            row["post_htf_close_ltf_left_context_candles"] = int(len(left_context))
            row["post_htf_close_ltf_left_context_status"] = left_context_status
            row["post_htf_close_ltf_left_context_source"] = str(entry_flow_source)
            row["post_htf_close_ltf_left_context_start_ms"] = int(left_context_start) if left_context_ms > 0 else None
            row["post_htf_close_ltf_left_context_end_ms"] = int(setup_start - entry_ms) if left_context_ms > 0 else None
            row["post_htf_close_ltf_forward_confirmation_start_ms"] = int(forward_start)
            row["post_htf_close_ltf_forward_confirmation_end_ms"] = int(decision_ts)
            row["post_htf_close_ltf_forward_confirmation_candles"] = int(len(entry_segment))
            rows.append(row)
    return rows


def _short_fader_trigger_names(config: AnomalyBacktestConfig) -> set[str]:
    raw = str(getattr(config, "short_fader_triggers", SHORT_FADER_DEFAULT_TRIGGERS) or "")
    names = {item.strip() for item in raw.split(",") if item.strip()}
    return names or {"failed_new_high", "taker_fade_red"}


def _window_feature_row(window: pd.DataFrame, *, prefix: str) -> dict[str, object]:
    if window.empty:
        return {
            f"{prefix}_candles": 0,
            f"{prefix}_ret": float("nan"),
            f"{prefix}_red_share": float("nan"),
            f"{prefix}_taker_buy_quote_share": float("nan"),
            f"{prefix}_quote_volume": float("nan"),
            f"{prefix}_number_of_trades": float("nan"),
        }
    first_open = float(window["open"].iloc[0])
    last_close = float(window["close"].iloc[-1])
    red_share = float((pd.to_numeric(window["close"], errors="coerce") < pd.to_numeric(window["open"], errors="coerce")).mean())
    quote_sum = float(pd.to_numeric(window.get("quote_volume", pd.Series(dtype=float)), errors="coerce").sum())
    trades_sum = float(pd.to_numeric(window.get("number_of_trades", pd.Series(dtype=float)), errors="coerce").sum())
    taker_sum = float(pd.to_numeric(window.get("taker_buy_quote_volume", pd.Series(dtype=float)), errors="coerce").sum())
    return {
        f"{prefix}_candles": int(len(window)),
        f"{prefix}_ret": _safe_divide_value(last_close - first_open, first_open),
        f"{prefix}_red_share": red_share,
        f"{prefix}_taker_buy_quote_share": _safe_divide_value(taker_sum, quote_sum),
        f"{prefix}_quote_volume": quote_sum,
        f"{prefix}_number_of_trades": trades_sum,
    }


def _post_close_short_labels(
    post_window: pd.DataFrame,
    *,
    anchor_close: float,
    config: AnomalyBacktestConfig,
) -> dict[str, object]:
    if post_window.empty or not np.isfinite(anchor_close) or anchor_close <= 0.0:
        return {
            "short_label_status": "missing_post_close_window",
            "short_mfe_from_post_close": float("nan"),
            "long_mfe_from_post_close": float("nan"),
            "adverse_up_before_short_low": float("nan"),
            "short2": False,
            "clean_short2": False,
        }
    highs = pd.to_numeric(post_window["high"], errors="coerce")
    lows = pd.to_numeric(post_window["low"], errors="coerce")
    low_idx = lows.idxmin()
    high_idx = highs.idxmax()
    min_low = float(lows.min())
    max_high = float(highs.max())
    before_low = post_window.loc[:low_idx]
    max_high_before_low = float(pd.to_numeric(before_low["high"], errors="coerce").max()) if not before_low.empty else max_high
    short_mfe = _safe_divide_value(anchor_close - min_low, anchor_close)
    long_mfe = _safe_divide_value(max_high - anchor_close, anchor_close)
    adverse = _safe_divide_value(max_high_before_low - anchor_close, anchor_close)
    threshold = float(config.short_fader_mfe_threshold_pct)
    clean_threshold = float(config.short_fader_clean_adverse_threshold_pct)
    first_low_ts = int(post_window.loc[low_idx, "timestamp"]) if low_idx in post_window.index else None
    first_high_ts = int(post_window.loc[high_idx, "timestamp"]) if high_idx in post_window.index else None
    return {
        "short_label_status": "ok",
        "short_mfe_from_post_close": float(short_mfe),
        "long_mfe_from_post_close": float(long_mfe),
        "adverse_up_before_short_low": float(adverse),
        "short2_threshold": threshold,
        "clean_short_adverse_threshold": clean_threshold,
        "short2": bool(np.isfinite(short_mfe) and short_mfe >= threshold),
        "clean_short2": bool(
            np.isfinite(short_mfe)
            and short_mfe >= threshold
            and np.isfinite(adverse)
            and adverse <= clean_threshold
        ),
        "post_close_low_timestamp_ms": first_low_ts,
        "post_close_low_timestamp_utc": _timestamp_to_utc(first_low_ts) if first_low_ts is not None else "",
        "post_close_high_timestamp_ms": first_high_ts,
        "post_close_high_timestamp_utc": _timestamp_to_utc(first_high_ts) if first_high_ts is not None else "",
    }


def _first_short_trigger_rows(
    post_window: pd.DataFrame,
    *,
    htf_high: float,
    htf_low: float,
    htf_close: float,
    entry_ms: int,
    enabled_triggers: set[str],
) -> list[dict[str, object]]:
    if post_window.empty:
        return []
    rows: list[dict[str, object]] = []
    found: set[str] = set()
    local_high = float("-inf")
    local_low = float("inf")
    htf_range = max(float(htf_high) - float(htf_low), 0.0)
    post_mid = float(htf_low) + 0.5 * htf_range
    normalized = post_window.reset_index(drop=True)

    def emit(
        *,
        trigger_type: str,
        idx: int,
        row: pd.Series,
        reference_high: float,
        reference_low: float,
        detail: str,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        found.add(trigger_type)
        ts = int(row["timestamp"])
        decision_available_ts = int(ts + entry_ms)
        payload = {
            "short_trigger_type": trigger_type,
            "short_trigger_status": "triggered",
            "decision_timestamp_ms": ts,
            "decision_timestamp_utc": _timestamp_to_utc(ts),
            "decision_available_timestamp_ms": decision_available_ts,
            "decision_available_timestamp_utc": _timestamp_to_utc(decision_available_ts),
            "short_trigger_rejection_high": float(reference_high),
            "short_trigger_reference_high": max(float(reference_high), float(htf_high)),
            "short_trigger_reference_low": min(float(reference_low), float(htf_low)),
            "short_trigger_close": float(row["close"]),
            "short_trigger_delay_candles": int(idx + 1),
            "short_trigger_delay_ms": int((idx + 1) * entry_ms),
            "short_trigger_detail": detail,
        }
        if extra:
            payload.update(extra)
        rows.append(payload)

    for idx, row in normalized.iterrows():
        ts = int(row["timestamp"])
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        local_high = max(local_high, high)
        local_low = min(local_low, low)
        if "failed_new_high" in enabled_triggers and "failed_new_high" not in found:
            if high >= float(htf_high) and close < float(htf_high) and close < open_:
                emit(
                    trigger_type="failed_new_high",
                    idx=int(idx),
                    row=row,
                    reference_high=high,
                    reference_low=float(htf_low),
                    detail="probed_htf_high_and_closed_back_below_red",
                )
        if "taker_fade_red" in enabled_triggers and "taker_fade_red" not in found and idx >= 3:
            recent = normalized.iloc[idx - 3 : idx + 1]
            recent_features = _window_feature_row(recent, prefix="short_trigger_recent4")
            red_share = float(recent_features["short_trigger_recent4_red_share"])
            taker_share = float(recent_features["short_trigger_recent4_taker_buy_quote_share"])
            if red_share >= 0.50 and np.isfinite(taker_share) and taker_share < 0.48 and close < float(htf_close):
                emit(
                    trigger_type="taker_fade_red",
                    idx=int(idx),
                    row=row,
                    reference_high=float(pd.to_numeric(recent["high"], errors="coerce").max()),
                    reference_low=min(float(htf_low), float(pd.to_numeric(recent["low"], errors="coerce").min())),
                    detail="recent_red_pressure_weak_taker_buy_below_htf_close",
                    extra=recent_features,
                )
        if "close_below_htf_close" in enabled_triggers and "close_below_htf_close" not in found:
            if close < float(htf_close) and close < open_:
                emit(
                    trigger_type="close_below_htf_close",
                    idx=int(idx),
                    row=row,
                    reference_high=max(local_high, float(htf_high)),
                    reference_low=min(local_low, float(htf_low)),
                    detail="red_close_below_htf_close",
                )
        if "close_below_post_mid" in enabled_triggers and "close_below_post_mid" not in found:
            if close < post_mid:
                emit(
                    trigger_type="close_below_post_mid",
                    idx=int(idx),
                    row=row,
                    reference_high=max(local_high, float(htf_high)),
                    reference_low=min(local_low, float(htf_low)),
                    detail="close_below_htf_range_midline",
                    extra={"short_trigger_post_mid": float(post_mid)},
                )
        if "lower_high_close_down" in enabled_triggers and "lower_high_close_down" not in found and idx >= 2:
            previous = normalized.iloc[max(0, idx - 3) : idx]
            previous_high = float(pd.to_numeric(previous["high"], errors="coerce").max())
            previous_close = float(previous["close"].iloc[-1])
            if high < previous_high and close < previous_close and close < open_:
                emit(
                    trigger_type="lower_high_close_down",
                    idx=int(idx),
                    row=row,
                    reference_high=previous_high,
                    reference_low=min(local_low, float(htf_low)),
                    detail="lower_high_and_close_down_after_post_close_attempt",
                    extra={"short_trigger_previous_high": previous_high, "short_trigger_previous_close": previous_close},
                )
        if "effort_no_progress" in enabled_triggers and "effort_no_progress" not in found and idx >= 3:
            recent = normalized.iloc[idx - 3 : idx + 1]
            recent_features = _window_feature_row(recent, prefix="short_trigger_recent4")
            recent_high = float(pd.to_numeric(recent["high"], errors="coerce").max())
            prior_high = float(pd.to_numeric(normalized.iloc[:idx]["high"], errors="coerce").max()) if idx > 0 else recent_high
            quote_sum = float(recent_features["short_trigger_recent4_quote_volume"])
            red_share = float(recent_features["short_trigger_recent4_red_share"])
            if quote_sum > 0.0 and recent_high <= prior_high * 1.001 and red_share >= 0.50 and close <= float(recent["open"].iloc[0]):
                emit(
                    trigger_type="effort_no_progress",
                    idx=int(idx),
                    row=row,
                    reference_high=max(recent_high, float(htf_high)),
                    reference_low=min(float(pd.to_numeric(recent["low"], errors="coerce").min()), float(htf_low)),
                    detail="recent_flow_effort_without_new_high_progress",
                    extra={**recent_features, "short_trigger_prior_high": prior_high},
                )
        if "pullback_without_recovery" in enabled_triggers and "pullback_without_recovery" not in found:
            if np.isfinite(local_high) and local_high > 0.0:
                pullback_from_local_high = _safe_divide_value(local_high - close, local_high)
                local_range_mid = local_low + 0.5 * max(local_high - local_low, 0.0)
                if pullback_from_local_high >= 0.005 and close < local_range_mid:
                    emit(
                        trigger_type="pullback_without_recovery",
                        idx=int(idx),
                        row=row,
                        reference_high=max(local_high, float(htf_high)),
                        reference_low=min(local_low, float(htf_low)),
                        detail="pullback_from_post_close_high_without_recovery",
                        extra={
                            "short_trigger_pullback_from_local_high": float(pullback_from_local_high),
                            "short_trigger_local_range_mid": float(local_range_mid),
                        },
                    )
        if found.issuperset(enabled_triggers):
            break
    return sorted(rows, key=lambda item: (int(item["decision_timestamp_ms"]), str(item["short_trigger_type"])))


def _collect_symbol_bare_htf_short_fader_rows(
    *,
    symbol: str,
    setup_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    config: AnomalyBacktestConfig,
    entry_flow_source: str = "cached_ohlcv",
) -> list[dict[str, object]]:
    lab_config = config.lab_config
    setup_timeframe = _effective_setup_timeframe(config)
    entry_timeframe = _effective_entry_timeframe(config)
    setup_ms = _timeframe_to_milliseconds(setup_timeframe)
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if setup_ms <= 0 or entry_ms <= 0 or entry_ms >= setup_ms:
        raise ValueError("bare_htf_short_fader requires entry_timeframe below setup_timeframe")
    if setup_frame.empty or entry_frame.empty:
        return []

    setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    setup_timestamps = setup_frame["timestamp"].astype("int64").to_numpy()
    entry_timestamps = entry_frame["timestamp"].astype("int64").to_numpy()
    baseline_quote_medians = (
        pd.to_numeric(setup_frame["quote_volume"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )
    baseline_trade_medians = (
        pd.to_numeric(setup_frame["number_of_trades"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )
    analysis_ms = int(max(1, int(config.short_fader_analysis_minutes)) * 60_000)
    enabled_triggers = _short_fader_trigger_names(config)
    rows: list[dict[str, object]] = []
    for setup_idx, setup_start_value in enumerate(setup_timestamps):
        setup_start = int(setup_start_value)
        if setup_idx < lab_config.baseline_candles:
            continue
        baseline_quote_value = float(baseline_quote_medians[setup_idx])
        baseline_trade_value = float(baseline_trade_medians[setup_idx])
        if not np.isfinite(baseline_quote_value) or not np.isfinite(baseline_trade_value):
            continue
        if baseline_quote_value <= 0.0 or baseline_trade_value <= 0.0:
            continue
        setup_row = setup_frame.iloc[setup_idx]
        start_quote = float(setup_row["quote_volume"])
        start_trades = float(setup_row["number_of_trades"])
        start_quote_ratio = _safe_divide_value(start_quote, baseline_quote_value)
        start_trade_ratio = _safe_divide_value(start_trades, baseline_trade_value)
        htf_open = float(setup_row["open"])
        htf_high = float(setup_row["high"])
        htf_low = float(setup_row["low"])
        htf_close = float(setup_row["close"])
        htf_return = _safe_divide_value(htf_close - htf_open, htf_open)
        min_prefilter_quote_ratio, min_prefilter_trade_ratio, min_prefilter_htf_return = _short_fader_prefilter_thresholds(config)
        if (
            not np.isfinite(start_quote_ratio)
            or not np.isfinite(start_trade_ratio)
            or not np.isfinite(htf_return)
            or start_quote_ratio < min_prefilter_quote_ratio
            or start_trade_ratio < min_prefilter_trade_ratio
            or htf_return < min_prefilter_htf_return
        ):
            continue

        htf_close_ts = int(setup_start + setup_ms)
        post_end = int(htf_close_ts + analysis_ms)
        post_start_pos = int(np.searchsorted(entry_timestamps, htf_close_ts, side="left"))
        post_end_pos = int(np.searchsorted(entry_timestamps, post_end, side="left"))
        post_window = entry_frame.iloc[post_start_pos:post_end_pos].copy()
        post_status = (
            "ok"
            if _entry_segment_has_full_range_coverage(
                post_window,
                range_start_ms=htf_close_ts,
                range_end_exclusive_ms=post_end,
                entry_ms=entry_ms,
            )
            else "missing_post_close_ltf_window"
        )
        if post_window.empty:
            post_status = "missing_post_close_ltf_window"

        base_row: dict[str, object] = {
            "symbol": symbol,
            "timeframe": setup_timeframe,
            "setup_timeframe": setup_timeframe,
            "entry_timeframe": entry_timeframe,
            "feature_contract": BARE_HTF_SHORT_FADER_CONTRACT,
            "candidate_collection_policy": "closed_htf_anomaly_post_close_ltf_window",
            "setup_source": "closed_htf_bare_anomaly",
            "setup_elapsed_fraction": 1.0,
            "setup_closed_entry_candles": int(setup_ms / entry_ms),
            "timestamp_ms": setup_start,
            "timestamp_utc": _timestamp_to_utc(setup_start),
            "anomaly_timestamp_ms": setup_start,
            "anomaly_timestamp_utc": _timestamp_to_utc(setup_start),
            "setup_available_timestamp_ms": htf_close_ts,
            "setup_available_timestamp_utc": _timestamp_to_utc(htf_close_ts),
            "decision_available_timestamp_ms": htf_close_ts,
            "decision_available_timestamp_utc": _timestamp_to_utc(htf_close_ts),
            "setup_full_available_timestamp_ms": htf_close_ts,
            "setup_full_available_timestamp_utc": _timestamp_to_utc(htf_close_ts),
            "timestamp_semantics": (
                "ohlcv_timestamp_is_candle_open;available_timestamp_is_candle_close;"
                "closed_htf_anomaly_available_at_htf_close;short_trigger_after_htf_close_only"
            ),
            "trade_count_proxy_used": False,
            "levels_trade_count_source": "cached_ohlcv.number_of_trades",
            "entry_trade_count_source": f"{entry_flow_source}.number_of_trades",
            "levels_quote_volume_source": "cached_ohlcv.quote_volume",
            "entry_quote_volume_source": f"{entry_flow_source}.quote_volume",
            "htf_open": htf_open,
            "htf_high": htf_high,
            "htf_low": htf_low,
            "htf_close": htf_close,
            "htf_return": htf_return,
            "short_fader_prefilter_min_quote_ratio": float(min_prefilter_quote_ratio),
            "short_fader_prefilter_min_trade_ratio": float(min_prefilter_trade_ratio),
            "short_fader_prefilter_min_htf_return": float(min_prefilter_htf_return),
            "start_quote_volume": start_quote,
            "start_trade_count": start_trades,
            "baseline_quote_volume_median": baseline_quote_value,
            "baseline_trade_count_median": baseline_trade_value,
            "start_quote_ratio": start_quote_ratio,
            "start_trade_ratio": start_trade_ratio,
            "short_post_close_start_ms": htf_close_ts,
            "short_post_close_start_utc": _timestamp_to_utc(htf_close_ts),
            "short_post_close_end_ms": post_end,
            "short_post_close_end_utc": _timestamp_to_utc(post_end),
            "short_post_close_ltf_candles": int(len(post_window)),
            "short_post_close_ltf_status": post_status,
            "short_fader_analysis_minutes": int(config.short_fader_analysis_minutes),
            "short_fader_trigger_contract": "failed_new_high_or_taker_fade_red_v1",
        }
        if not post_window.empty:
            for size in (4, 6, 12, 24):
                base_row.update(_window_feature_row(post_window.head(size), prefix=f"ltf{size}"))
            base_row.update(_post_close_short_labels(post_window, anchor_close=htf_close, config=config))
        else:
            base_row.update(_post_close_short_labels(post_window, anchor_close=htf_close, config=config))

        triggers = (
            _first_short_trigger_rows(
                post_window,
                htf_high=htf_high,
                htf_low=htf_low,
                htf_close=htf_close,
                entry_ms=entry_ms,
                enabled_triggers=enabled_triggers,
            )
            if post_status == "ok"
            else []
        )
        if not triggers:
            rows.append(
                {
                    **base_row,
                    "short_trigger_type": "",
                    "short_trigger_status": "no_trigger" if post_status == "ok" else post_status,
                    "decision_timestamp_ms": htf_close_ts,
                    "decision_timestamp_utc": _timestamp_to_utc(htf_close_ts),
                }
            )
            continue
        for trigger in triggers:
            rows.append({**base_row, **trigger})
    return rows


def _collect_symbol_pair_rows(
    *,
    symbol: str,
    setup_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    config: AnomalyBacktestConfig,
    entry_flow_source: str = "cached_ohlcv",
) -> list[dict[str, object]]:
    lab_config = config.lab_config
    setup_timeframe = _effective_setup_timeframe(config)
    entry_timeframe = _effective_entry_timeframe(config)
    setup_ms = _timeframe_to_milliseconds(setup_timeframe)
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if setup_ms <= 0 or entry_ms <= 0 or entry_ms > setup_ms:
        raise ValueError(f"invalid setup/entry timeframe pair: {setup_timeframe}/{entry_timeframe}")
    if setup_frame.empty or entry_frame.empty:
        return []
    setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    # Historical baseline belongs to the setup timeframe. Entry/subminute data
    # is only needed for the currently forming setup candle and execution path.
    # Using entry_frame to build the whole setup baseline forces targeted
    # backfill to fetch baseline_candles * setup_ms of 1s data per event, which
    # turns a targeted run into a near full-cache rebuild.
    setup_metric_frame = setup_frame.copy()
    if setup_metric_frame.empty:
        return []
    rows: list[dict[str, object]] = []
    setup_timestamps = setup_metric_frame["timestamp"].astype("int64").to_numpy()
    entry_timestamps = entry_frame["timestamp"].astype("int64").to_numpy()
    baseline_quote_medians = (
        pd.to_numeric(setup_metric_frame["quote_volume"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )
    baseline_trade_medians = (
        pd.to_numeric(setup_metric_frame["number_of_trades"], errors="coerce")
        .rolling(window=lab_config.baseline_candles, min_periods=lab_config.baseline_candles)
        .median()
        .shift(1)
        .to_numpy()
    )
    for setup_idx, setup_start in enumerate(setup_timestamps):
        setup_start = int(setup_start)
        if setup_idx < lab_config.baseline_candles:
            continue
        baseline_quote_value = float(baseline_quote_medians[setup_idx])
        baseline_trade_value = float(baseline_trade_medians[setup_idx])
        if not np.isfinite(baseline_quote_value) or not np.isfinite(baseline_trade_value):
            continue
        if baseline_quote_value <= 0.0 or baseline_trade_value <= 0.0:
            continue
        entry_start_pos = int(np.searchsorted(entry_timestamps, setup_start, side="left"))
        entry_end_pos_exclusive = int(np.searchsorted(entry_timestamps, setup_start + setup_ms, side="left"))
        entry_segment_full = entry_frame.iloc[entry_start_pos:entry_end_pos_exclusive]
        if len(entry_segment_full) < lab_config.confirmation_candles:
            continue
        entry_quote_cumsum = pd.to_numeric(entry_segment_full["quote_volume"], errors="coerce").fillna(0.0).cumsum().to_numpy()
        entry_trade_cumsum = pd.to_numeric(entry_segment_full["number_of_trades"], errors="coerce").fillna(0.0).cumsum().to_numpy()
        baseline_for_row: pd.DataFrame | None = None
        for entry_end_pos in range(lab_config.confirmation_candles - 1, len(entry_segment_full)):
            start_quote = float(entry_quote_cumsum[entry_end_pos])
            start_trades = float(entry_trade_cumsum[entry_end_pos])
            setup_elapsed_fraction = min(1.0, (entry_end_pos + 1) * entry_ms / setup_ms)
            elapsed_for_ratio = max(1e-9, setup_elapsed_fraction)
            raw_start_quote_ratio = _safe_divide_value(start_quote, baseline_quote_value)
            raw_start_trade_ratio = _safe_divide_value(start_trades, baseline_trade_value)
            start_quote_ratio = _safe_divide_value(raw_start_quote_ratio, elapsed_for_ratio)
            start_trade_ratio = _safe_divide_value(raw_start_trade_ratio, elapsed_for_ratio)
            min_raw_quote_ratio = lab_config.min_quote_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
            min_raw_trade_ratio = lab_config.min_trade_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
            if (
                not np.isfinite(start_quote_ratio)
                or not np.isfinite(start_trade_ratio)
                or not np.isfinite(raw_start_quote_ratio)
                or not np.isfinite(raw_start_trade_ratio)
                or start_quote_ratio < lab_config.min_quote_ratio_start
                or start_trade_ratio < lab_config.min_trade_ratio_start
                or raw_start_quote_ratio < min_raw_quote_ratio
                or raw_start_trade_ratio < min_raw_trade_ratio
            ):
                continue
            entry_segment = entry_segment_full.iloc[: entry_end_pos + 1].copy()
            decision = entry_segment.iloc[-1]
            decision_ts = int(decision["timestamp"])
            forming_setup = _aggregate_ohlcv_to_candle(entry_segment, timestamp_ms=setup_start)
            if forming_setup is None:
                continue
            if baseline_for_row is None:
                baseline_for_row = setup_metric_frame.iloc[setup_idx - lab_config.baseline_candles : setup_idx].copy()
                if baseline_for_row.empty:
                    break
            row = _build_pair_candidate_row(
                symbol=symbol,
                setup_timeframe=setup_timeframe,
                entry_timeframe=entry_timeframe,
                setup_ms=setup_ms,
                entry_ms=entry_ms,
                setup_idx=setup_idx,
                baseline=baseline_for_row,
                setup_row=forming_setup,
                entry_segment=entry_segment,
                entry_frame=entry_frame,
                config=config,
                entry_flow_source=entry_flow_source,
                setup_flow_source=entry_flow_source if entry_ms < setup_ms else "cached_ohlcv",
            )
            if row is None:
                continue
            row["setup_decision_index"] = int(entry_end_pos)
            row["candidate_collection_policy"] = "all_ltf_decisions_per_setup"
            rows.append(row)
    return rows


def _build_pair_candidate_row(
    *,
    symbol: str,
    setup_timeframe: str,
    entry_timeframe: str,
    setup_ms: int,
    entry_ms: int,
    setup_idx: int,
    baseline: pd.DataFrame,
    setup_row: pd.Series,
    entry_segment: pd.DataFrame,
    entry_frame: pd.DataFrame,
    config: AnomalyBacktestConfig,
    entry_flow_source: str = "cached_ohlcv",
    setup_flow_source: str = "cached_ohlcv",
    ltf_left_context: pd.DataFrame | None = None,
    setup_elapsed_fraction_override: float | None = None,
    feature_contract: str = "htf_setup_ltf_entry_v1",
    setup_source: str = "forming_htf_from_entry_tf_backtest",
    timestamp_semantics_note: str = "forming_htf_setup_available_at_entry_decision_close",
) -> dict[str, object] | None:
    lab_config = config.lab_config
    quote_volume = pd.to_numeric(baseline["quote_volume"], errors="coerce")
    trade_count = pd.to_numeric(baseline["number_of_trades"], errors="coerce")
    baseline_quote_value = float(quote_volume.median())
    baseline_trade_value = float(trade_count.median())
    start_quote = float(setup_row["quote_volume"])
    start_trades = float(setup_row["number_of_trades"])
    raw_start_quote_ratio = _safe_divide_value(start_quote, baseline_quote_value)
    raw_start_trade_ratio = _safe_divide_value(start_trades, baseline_trade_value)
    setup_elapsed_fraction = (
        float(setup_elapsed_fraction_override)
        if setup_elapsed_fraction_override is not None
        else min(1.0, len(entry_segment) * entry_ms / setup_ms)
    )
    setup_elapsed_fraction = min(1.0, max(0.0, setup_elapsed_fraction))
    elapsed_for_ratio = max(1e-9, setup_elapsed_fraction)
    start_quote_ratio = _safe_divide_value(raw_start_quote_ratio, elapsed_for_ratio)
    start_trade_ratio = _safe_divide_value(raw_start_trade_ratio, elapsed_for_ratio)
    min_raw_quote_ratio = lab_config.min_quote_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
    min_raw_trade_ratio = lab_config.min_trade_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
    if not np.isfinite(start_quote_ratio) or not np.isfinite(start_trade_ratio) or not np.isfinite(raw_start_quote_ratio) or not np.isfinite(raw_start_trade_ratio):
        return None
    if (
        start_quote_ratio < lab_config.min_quote_ratio_start
        or start_trade_ratio < lab_config.min_trade_ratio_start
        or raw_start_quote_ratio < min_raw_quote_ratio
        or raw_start_trade_ratio < min_raw_trade_ratio
    ):
        return None
    start_open = float(setup_row["open"])
    start_high = float(setup_row["high"])
    start_low = float(setup_row["low"])
    start_close = float(setup_row["close"])
    decision = entry_segment.iloc[-1]
    decision_ts = int(decision["timestamp"])
    decision_available_ts = _row_available_timestamp_ms(decision, timeframe=entry_timeframe)
    setup_full_available_ts = int(setup_row["timestamp"]) + int(setup_ms)
    setup_available_ts = decision_available_ts
    decision_close = float(decision["close"])
    impulse_low = float(min(start_low, pd.to_numeric(entry_segment["low"], errors="coerce").min()))
    impulse_high = float(max(start_high, pd.to_numeric(entry_segment["high"], errors="coerce").max()))
    impulse_range = impulse_high - impulse_low
    if not np.isfinite(impulse_range) or impulse_range <= 0.0:
        return None
    activation_price = start_open + max(0.0, start_close - start_open) * 0.50
    hold_count = int(pd.to_numeric(entry_segment["close"], errors="coerce").ge(activation_price).sum())
    price_retention = _safe_divide_value(decision_close - start_open, impulse_high - start_open)
    verticality = compute_start_verticality_metrics(entry_segment)
    future = entry_frame.loc[entry_frame["timestamp"].astype("int64") > decision_ts]
    future_high_window = future.head(lab_config.forward_high_candles)
    future_low_window = future.head(lab_config.forward_low_candles)
    future_high_observed_candles = int(len(future_high_window))
    future_low_observed_candles = int(len(future_low_window))
    future_label_status = (
        "ok"
        if (
            future_high_observed_candles >= int(lab_config.forward_high_candles)
            and future_low_observed_candles >= int(lab_config.forward_low_candles)
        )
        else "insufficient_future_window"
    )
    future_high = (
        float(pd.to_numeric(future_high_window["high"], errors="coerce").max())
        if future_high_observed_candles
        else float("nan")
    )
    future_low = (
        float(pd.to_numeric(future_low_window["low"], errors="coerce").min())
        if future_low_observed_candles
        else float("nan")
    )
    future_ret_high = _safe_divide_value(future_high - decision_close, decision_close)
    future_dd_low = _safe_divide_value(future_low - decision_close, decision_close)
    outcome_label = (
        _classify_pair_outcome(future_ret_high=future_ret_high, future_dd_low=future_dd_low, config=lab_config)
        if future_label_status == "ok"
        else "unlabeled_insufficient_future"
    )
    setup_with_current = pd.concat([baseline, pd.DataFrame([setup_row.to_dict()])], ignore_index=True)
    decision_ema20 = float(setup_with_current["close"].astype(float).ewm(span=20, adjust=False).mean().iloc[-1])
    range_series = baseline["high"].astype(float) - baseline["low"].astype(float)
    baseline_range = float(range_series.median())
    baseline_range_pct = float((range_series / baseline["close"].astype(float).replace(0.0, np.nan)).median())
    start_range = start_high - start_low
    start_ret = _safe_divide_value(start_close - start_open, start_open)
    abs_start_ret = abs(start_ret) if np.isfinite(start_ret) else float("nan")
    baseline_avg_trade_quote = _safe_divide_value(baseline_quote_value, baseline_trade_value)
    start_avg_trade_quote = _safe_divide_value(start_quote, start_trades)
    prior_whipsaw_source = "setup_timeframe_baseline"
    prior_whipsaw_frame = baseline
    if ltf_left_context is not None and not ltf_left_context.empty:
        prior_whipsaw_source = "entry_timeframe_left_context"
        prior_whipsaw_frame = ltf_left_context
    prior_whipsaw = _prior_up_down_whipsaw_to_impulse_range(prior_whipsaw_frame, impulse_range=impulse_range)
    flow_hold_count = int(
        (
            pd.to_numeric(entry_segment["quote_volume"], errors="coerce").ge(max(0.35 * start_quote, 3.0 * baseline_quote_value))
            & pd.to_numeric(entry_segment["number_of_trades"], errors="coerce").ge(max(0.35 * start_trades, 3.0 * baseline_trade_value))
        ).sum()
    )
    next_quote_mean = float(pd.to_numeric(entry_segment["quote_volume"], errors="coerce").mean())
    next_trade_mean = float(pd.to_numeric(entry_segment["number_of_trades"], errors="coerce").mean())
    taker_metrics = _pair_taker_metrics(entry_segment, baseline, baseline_quote_value)
    runner_shape_segment = entry_segment.tail(12).copy()
    runner_shape_first_half = runner_shape_segment.head(6)
    runner_shape_second_half = runner_shape_segment.iloc[6:12]
    runner_shape_ready = len(runner_shape_first_half) == 6 and len(runner_shape_second_half) == 6
    runner_shape_first_half_quote = (
        float(pd.to_numeric(runner_shape_first_half["quote_volume"], errors="coerce").sum())
        if runner_shape_ready
        else float("nan")
    )
    runner_shape_second_half_quote = (
        float(pd.to_numeric(runner_shape_second_half["quote_volume"], errors="coerce").sum())
        if runner_shape_ready
        else float("nan")
    )
    runner_shape_first_half_trades = (
        float(pd.to_numeric(runner_shape_first_half["number_of_trades"], errors="coerce").sum())
        if runner_shape_ready
        else float("nan")
    )
    runner_shape_second_half_trades = (
        float(pd.to_numeric(runner_shape_second_half["number_of_trades"], errors="coerce").sum())
        if runner_shape_ready
        else float("nan")
    )
    runner_shape_first_half_range_pct = _entry_segment_range_pct(runner_shape_first_half) if runner_shape_ready else float("nan")
    runner_shape_second_half_range_pct = _entry_segment_range_pct(runner_shape_second_half) if runner_shape_ready else float("nan")
    return {
        "symbol": symbol,
        "timeframe": setup_timeframe,
        "feature_contract": str(feature_contract),
        "setup_timeframe": setup_timeframe,
        "entry_timeframe": entry_timeframe,
        "setup_source": str(setup_source),
        "setup_elapsed_fraction": setup_elapsed_fraction,
        "setup_closed_entry_candles": int(len(entry_segment)),
        "timestamp_ms": int(setup_row["timestamp"]),
        "timestamp_utc": _timestamp_to_utc(int(setup_row["timestamp"])),
        "setup_available_timestamp_ms": setup_available_ts,
        "setup_available_timestamp_utc": _timestamp_to_utc(setup_available_ts),
        "setup_full_available_timestamp_ms": setup_full_available_ts,
        "setup_full_available_timestamp_utc": _timestamp_to_utc(setup_full_available_ts),
        "decision_timestamp_ms": decision_ts,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
        "decision_available_timestamp_ms": decision_available_ts,
        "decision_available_timestamp_utc": _timestamp_to_utc(decision_available_ts),
        "timestamp_semantics": (
            "ohlcv_timestamp_is_candle_open;available_timestamp_is_candle_close;"
            f"{timestamp_semantics_note}"
        ),
        "confirmation_candles": int(lab_config.confirmation_candles),
        "baseline_candles": int(lab_config.baseline_candles),
        "start_open": start_open,
        "start_high": start_high,
        "start_low": start_low,
        "start_close": start_close,
        "decision_close": decision_close,
        "decision_ema20": decision_ema20,
        "start_quote_volume": start_quote,
        "start_trade_count": start_trades,
        "baseline_quote_volume_median": baseline_quote_value,
        "baseline_trade_count_median": baseline_trade_value,
        "start_quote_ratio": start_quote_ratio,
        "start_trade_ratio": start_trade_ratio,
        "start_quote_ratio_raw": raw_start_quote_ratio,
        "start_trade_ratio_raw": raw_start_trade_ratio,
        "start_quote_pace_ratio": start_quote_ratio,
        "start_trade_pace_ratio": start_trade_ratio,
        "next_n_quote_volume_mean": next_quote_mean,
        "next_n_trade_count_mean": next_trade_mean,
        "next_n_quote_decay": _safe_divide_value(next_quote_mean, start_quote),
        "next_n_trade_decay": _safe_divide_value(next_trade_mean, start_trades),
        "hold_count_next_n_candles": hold_count,
        "hold_ratio_next_n_candles": _safe_divide_value(hold_count, len(entry_segment)),
        "hold_count_model": "entry_price_activation_hold",
        "flow_hold_count_next_n_candles": flow_hold_count,
        "flow_hold_ratio_next_n_candles": _safe_divide_value(flow_hold_count, len(entry_segment)),
        "runner_shape_first_half_quote_volume": runner_shape_first_half_quote,
        "runner_shape_second_half_quote_volume": runner_shape_second_half_quote,
        "runner_shape_first_half_number_of_trades": runner_shape_first_half_trades,
        "runner_shape_second_half_number_of_trades": runner_shape_second_half_trades,
        "runner_shape_first_half_range_pct": runner_shape_first_half_range_pct,
        "runner_shape_second_half_range_pct": runner_shape_second_half_range_pct,
        "runner_shape_quote_acceleration": _safe_divide_value(runner_shape_second_half_quote, runner_shape_first_half_quote),
        "runner_shape_trade_acceleration": _safe_divide_value(runner_shape_second_half_trades, runner_shape_first_half_trades),
        "runner_shape_range_acceleration": _safe_divide_value(runner_shape_second_half_range_pct, runner_shape_first_half_range_pct),
        "runner_shape_second_half_return_pct": (
            _safe_divide_value(float(runner_shape_second_half.iloc[-1]["close"]) - float(runner_shape_second_half.iloc[0]["open"]), float(runner_shape_second_half.iloc[0]["open"]))
            if runner_shape_ready
            else float("nan")
        ),
        "runner_shape_top1_quote_share": (
            _safe_divide_value(float(pd.to_numeric(runner_shape_segment["quote_volume"], errors="coerce").max()), float(pd.to_numeric(runner_shape_segment["quote_volume"], errors="coerce").sum()))
            if runner_shape_ready
            else float("nan")
        ),
        "price_retention_next_n": price_retention,
        "price_retention_model": "decision_close_vs_setup_open_to_high",
        "entry_activation_price": activation_price,
        "midpoint_lost_next_n": bool(decision_close < activation_price),
        "new_high_count_next_n": int(pd.to_numeric(entry_segment["high"], errors="coerce").gt(float(entry_segment.iloc[0]["high"])).sum()),
        "decision_box_low": impulse_low,
        "decision_box_high": impulse_high,
        "decision_box_range": impulse_range,
        "future_label_status": future_label_status,
        "future_high_observed_candles": future_high_observed_candles,
        "future_low_observed_candles": future_low_observed_candles,
        "future_high": future_high,
        "future_low": future_low,
        "future_ret_high_after_decision": future_ret_high,
        "future_dd_low_after_decision": future_dd_low,
        "outcome_label": outcome_label,
        **{
            **_TRADE_CHART_FLOW_PROVENANCE,
            "levels_trade_count_source": f"{setup_flow_source}.number_of_trades",
            "levels_quote_volume_source": f"{setup_flow_source}.quote_volume",
            "entry_trade_count_source": f"{entry_flow_source}.number_of_trades",
            "entry_quote_volume_source": f"{entry_flow_source}.quote_volume",
        },
        **verticality,
        **taker_metrics,
        "start_avg_trade_quote_size": start_avg_trade_quote,
        "baseline_avg_trade_quote_size_median": baseline_avg_trade_quote,
        "start_avg_trade_quote_size_ratio": _safe_divide_value(start_avg_trade_quote, baseline_avg_trade_quote),
        "next_n_avg_trade_quote_size_mean": _safe_divide_value(next_quote_mean, next_trade_mean),
        "next_n_avg_trade_quote_size_decay": _safe_divide_value(_safe_divide_value(next_quote_mean, next_trade_mean), start_avg_trade_quote),
        "decision_return_from_start_open": _safe_divide_value(decision_close - start_open, start_open),
        "start_close_position_in_range": _safe_divide_value(start_close - start_low, start_range),
        "start_body_to_range": _safe_divide_value(abs(start_close - start_open), start_range),
        "start_upper_wick_to_range": _safe_divide_value(start_high - max(start_open, start_close), start_range),
        "start_lower_wick_to_range": _safe_divide_value(min(start_open, start_close) - start_low, start_range),
        "baseline_range_median": baseline_range,
        "baseline_range_pct_median": baseline_range_pct,
        "baseline_zero_range_share": float(range_series.le(0.0).mean()),
        "baseline_return_range_pct": _safe_divide_value(float(baseline["high"].max()) - float(baseline["low"].min()), start_open),
        "baseline_close_return_range_pct": _safe_divide_value(float(baseline["close"].max()) - float(baseline["close"].min()), start_open),
        "baseline_return_from_first_close_pct": _safe_divide_value(float(baseline["close"].iloc[-1]) - float(baseline["close"].iloc[0]), float(baseline["close"].iloc[0])),
        "prior_up_leg_to_impulse_range": float("nan"),
        "prior_down_leg_to_impulse_range": float("nan"),
        "prior_up_down_whipsaw_to_impulse_range": prior_whipsaw,
        "prior_up_down_whipsaw_source": prior_whipsaw_source,
        "start_range_ratio_to_baseline": _safe_divide_value(start_range, baseline_range),
        "start_range_pct": _safe_divide_value(start_range, start_open),
        "start_range_pct_ratio_to_baseline": _safe_divide_value(_safe_divide_value(start_range, start_open), baseline_range_pct),
        "start_quote_per_abs_return": _safe_divide_value(start_quote, abs_start_ret),
        "start_trades_per_abs_return": _safe_divide_value(start_trades, abs_start_ret),
        "start_quote_ratio_per_abs_return": _safe_divide_value(start_quote_ratio, abs_start_ret),
        "start_trade_ratio_per_abs_return": _safe_divide_value(start_trade_ratio, abs_start_ret),
        "post_start_pullback_fraction_of_box": _safe_divide_value(impulse_high - float(pd.to_numeric(entry_segment["low"], errors="coerce").min()), impulse_range),
    }


def _pair_taker_metrics(entry_segment: pd.DataFrame, baseline: pd.DataFrame, baseline_quote_value: float) -> dict[str, object]:
    default = {
        "flow_taker_buy_status": "missing_columns",
        "start_taker_buy_quote_share": float("nan"),
        "baseline_taker_buy_quote_share_median": float("nan"),
        "start_taker_buy_quote_share_delta": float("nan"),
        "next_n_taker_buy_quote_share_mean": float("nan"),
        "next_n_taker_buy_quote_share_delta": float("nan"),
        "next_n_taker_buy_quote_share_decay": float("nan"),
    }
    if "taker_buy_quote_volume" not in entry_segment.columns:
        return default
    quote_volume = pd.to_numeric(entry_segment["quote_volume"], errors="coerce")
    taker_quote = pd.to_numeric(entry_segment["taker_buy_quote_volume"], errors="coerce")
    share = taker_quote / quote_volume.replace(0.0, np.nan)
    start_share = float(share.iloc[0])
    next_share = float(share.mean())
    baseline_share = float("nan")
    if "taker_buy_quote_volume" in baseline.columns:
        baseline_quote = pd.to_numeric(baseline["quote_volume"], errors="coerce")
        baseline_taker = pd.to_numeric(baseline["taker_buy_quote_volume"], errors="coerce")
        baseline_share = float((baseline_taker / baseline_quote.replace(0.0, np.nan)).median())
    return {
        "flow_taker_buy_status": "ok" if np.isfinite(start_share) else "missing_values",
        "start_taker_buy_quote_share": start_share,
        "baseline_taker_buy_quote_share_median": baseline_share,
        "start_taker_buy_quote_share_delta": start_share - baseline_share,
        "next_n_taker_buy_quote_share_mean": next_share,
        "next_n_taker_buy_quote_share_delta": next_share - baseline_share,
        "next_n_taker_buy_quote_share_decay": _safe_divide_value(next_share, start_share),
    }


def _entry_segment_range_pct(segment: pd.DataFrame) -> float:
    if segment.empty:
        return float("nan")
    open_price = float(segment.iloc[0]["open"])
    if not np.isfinite(open_price) or open_price <= 0:
        return float("nan")
    high = float(pd.to_numeric(segment["high"], errors="coerce").max())
    low = float(pd.to_numeric(segment["low"], errors="coerce").min())
    return _safe_divide_value(high - low, open_price)


def _classify_pair_outcome(*, future_ret_high: float, future_dd_low: float, config: AnomalyLabConfig) -> str:
    if np.isfinite(future_ret_high) and future_ret_high >= config.big_move_threshold:
        return "big_25p"
    if np.isfinite(future_ret_high) and np.isfinite(future_dd_low) and future_ret_high < config.fade_max_upside and future_dd_low <= config.fade_drawdown_threshold:
        return "fast_fade"
    return "other"


def _entry_delay_candles(
    frame: pd.DataFrame,
    *,
    entry_timestamp_ms: int,
    decision_timestamp_ms: int,
) -> int:
    frame_step_ms = int(infer_frame_step_ms(frame) or 60_000)
    if frame_step_ms <= 0:
        frame_step_ms = 60_000
    return max(0, int(round((int(entry_timestamp_ms) - int(decision_timestamp_ms)) / frame_step_ms)))


def simulate_long_signal(
    frame: pd.DataFrame,
    signal: pd.Series,
    *,
    config: AnomalyBacktestConfig,
    execution_frame: pd.DataFrame | None = None,
) -> dict[str, object]:
    symbol = str(signal["symbol"])
    execution_model = _execution_model_label(config)
    anomaly_ts = int(signal["timestamp_ms"])
    decision_ts = int(signal["decision_timestamp_ms"])
    decision_close = float(signal["decision_close"])
    (
        entry_ts,
        entry_price,
        initial_stop,
        initial_risk,
        box_range,
        box_high,
        entry_skip_reason,
        entry_audit,
    ) = _resolve_signal_entry(
        frame,
        anomaly_timestamp_ms=anomaly_ts,
        decision_timestamp_ms=decision_ts,
        decision_close=decision_close,
        config=config,
        execution_frame=execution_frame,
    )
    if not np.isfinite(entry_price):
        return _skipped_signal_result(
            signal,
            skip_reason=entry_skip_reason or "entry_trigger_not_reached",
            config=config,
            entry_timestamp_ms=entry_ts,
            entry_timestamp_utc=_timestamp_to_utc(entry_ts),
            initial_stop=initial_stop,
            box_range=box_range,
            box_high=box_high,
            **entry_audit,
        )
    if not np.isfinite(initial_risk) or initial_risk <= 0.0:
        return _skipped_signal_result(
            signal,
            skip_reason="invalid_initial_risk",
            config=config,
            entry_timestamp_ms=entry_ts,
            entry_timestamp_utc=_timestamp_to_utc(entry_ts),
            entry_price=entry_price,
            initial_stop=initial_stop,
            initial_risk=initial_risk,
            box_range=box_range,
            box_high=box_high,
            **entry_audit,
        )
    initial_risk_pct = initial_risk / entry_price
    if initial_risk_pct > config.max_initial_risk_pct:
        return _skipped_signal_result(
            signal,
            skip_reason="initial_risk_too_wide",
            config=config,
            entry_timestamp_ms=entry_ts,
            entry_timestamp_utc=_timestamp_to_utc(entry_ts),
            entry_price=entry_price,
            initial_stop=initial_stop,
            initial_risk=initial_risk,
            initial_risk_pct=initial_risk_pct,
            box_range=box_range,
            box_high=box_high,
            **entry_audit,
        )

    simulation_frame = execution_frame if config.latency_enabled and execution_frame is not None and not execution_frame.empty else frame
    base_step_ms = int(infer_frame_step_ms(frame) or _timeframe_to_milliseconds(_effective_entry_timeframe(config)))
    simulation_step_ms = int(infer_frame_step_ms(simulation_frame) or base_step_ms)
    max_hold_rows = int(config.max_hold_candles)
    if simulation_step_ms > 0 and base_step_ms > 0:
        max_hold_rows = max(1, int(math.ceil(config.max_hold_candles * base_step_ms / simulation_step_ms)))
    future = simulation_frame.loc[simulation_frame["timestamp"] >= entry_ts].head(max_hold_rows).copy()
    if future.empty:
        return _skipped_signal_result(
            signal,
            skip_reason="no_post_entry_execution_candles",
            config=config,
            entry_timestamp_ms=entry_ts,
            entry_timestamp_utc=_timestamp_to_utc(entry_ts),
            entry_price=entry_price,
            initial_stop=initial_stop,
            initial_risk=initial_risk,
            initial_risk_pct=initial_risk_pct,
            box_range=box_range,
            box_high=box_high,
            **entry_audit,
        )
    post_entry_simulation_start_ts = int(future["timestamp"].iloc[0])

    box_low = _safe_float(signal.get("decision_box_low"))
    if box_low is None:
        box_low = initial_stop
    tp1_basis_price, tp1_risk = _tp1_pump_leg_risk_from_values(
        entry_price=entry_price,
        box_low=box_low,
    )
    tp1_risk_pct = _safe_divide_value(tp1_risk, entry_price)
    if not np.isfinite(tp1_risk) or tp1_risk <= 0.0:
        return _skipped_signal_result(
            signal,
            skip_reason="invalid_tp1_target_basis_risk",
            config=config,
            entry_timestamp_ms=entry_ts,
            entry_timestamp_utc=_timestamp_to_utc(entry_ts),
            entry_price=entry_price,
            initial_stop=initial_stop,
            initial_risk=initial_risk,
            initial_risk_pct=initial_risk_pct,
            box_range=box_range,
            box_high=box_high,
            **entry_audit,
        )
    base_tp1_price = entry_price + config.tp1_r * tp1_risk
    tp1_price, tp1_round_step = _round_up_tp1_to_market_number(
        base_tp1_price,
        reference_price=entry_price,
        movement=max(tp1_risk, box_range),
    )
    active_stop = initial_stop
    tp1_hit = False
    tp1_fill_model = "conservative_limit_proxy"
    tp1_fill_status = "not_hit"
    tp1_fill_timestamp_ms = float("nan")
    tp1_fill_price = float("nan")
    intrabar_path_assumption = "stop_first_on_same_candle_conflict"
    remaining_fraction = 1.0
    realized_r = 0.0
    max_high = entry_price
    min_low = entry_price
    exit_reason = "time_exit"
    exit_ts = int(future["timestamp"].iloc[-1])
    raw_exit_price = float(future["close"].iloc[-1])
    exit_price = _long_exit_fill_price(raw_exit_price, config=config)
    exit_fill_model = "time_exit_close_minus_adverse_slippage"
    trail_stop = float("nan")
    ema20_exit_armed = False
    ema20_exit_armed_ts = float("nan")
    ema20_exit_armed_price = float("nan")
    ema20_exit_triggered = False
    ema20_exit_was_better_than_final = False

    prior_trailing_lows: list[float] = []
    trailing_lookback = max(1, int(config.trail_lookback_candles))
    for idx, row in future.iterrows():
        candle_ts = int(row["timestamp"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        ema20 = _safe_float(row.get("ema20"))
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        stop_hit = low <= active_stop
        tp1_trade_through = high > tp1_price
        tp1_touched = high >= tp1_price
        if stop_hit:
            exit_reason = "stop_loss" if not tp1_hit else "trailing_stop"
            exit_ts = candle_ts
            raw_exit_price = active_stop
            exit_price = _long_exit_fill_price(raw_exit_price, config=config)
            exit_fill_model = "stop_price_minus_adverse_slippage"
            realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
            remaining_fraction = 0.0
            if not tp1_hit and tp1_touched:
                tp1_fill_status = "ambiguous_intrabar_stop_first"
            break

        if (
            config.exit_rule == "ema20_negative_pnl_be_escape"
            and ema20_exit_armed
            and high >= entry_price
        ):
            exit_reason = "ema20_negative_pnl_be_escape"
            exit_ts = candle_ts
            raw_exit_price = entry_price
            exit_price = _long_exit_fill_price(raw_exit_price, config=config)
            exit_fill_model = "breakeven_guard_minus_adverse_slippage"
            realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
            remaining_fraction = 0.0
            ema20_exit_triggered = True
            break

        if not tp1_hit and tp1_trade_through:
            tp1_hit = True
            tp1_fill_status = "filled_conservative_trade_through"
            tp1_fill_timestamp_ms = candle_ts
            tp1_fill_price = _long_exit_fill_price(tp1_price, config=config)
            realized_r += config.tp1_fraction * ((tp1_fill_price - entry_price) / initial_risk)
            remaining_fraction = 1.0 - config.tp1_fraction
            if config.move_stop_to_breakeven_after_tp1:
                active_stop = max(active_stop, entry_price)
            if remaining_fraction <= 0.0:
                exit_reason = "tp1_full_exit"
                exit_ts = candle_ts
                raw_exit_price = tp1_price
                exit_price = tp1_fill_price
                exit_fill_model = "tp1_limit_proxy_minus_adverse_slippage"
                break
        elif not tp1_hit and tp1_touched:
            tp1_fill_status = "touched_not_filled_conservative"

        if (
            config.exit_rule == "ema20_close"
            and ema20 is not None
            and close < ema20
        ):
            exit_reason = "ema20_close"
            exit_ts = candle_ts
            raw_exit_price = close
            exit_price = _long_exit_fill_price(raw_exit_price, config=config)
            exit_fill_model = "ema20_close_minus_adverse_slippage"
            realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
            remaining_fraction = 0.0
            ema20_exit_triggered = True
            break

        if (
            config.exit_rule == "ema20_negative_pnl_be_escape"
            and not ema20_exit_armed
            and ema20 is not None
            and close < ema20
            and close < entry_price
        ):
            ema20_exit_armed = True
            ema20_exit_armed_ts = candle_ts
            ema20_exit_armed_price = close

        if tp1_hit and prior_trailing_lows:
            structural_stop = float(min(prior_trailing_lows[-trailing_lookback:])) - config.trail_buffer_r * initial_risk
            if structural_stop > active_stop and structural_stop < close:
                active_stop = structural_stop
                trail_stop = active_stop

        if np.isfinite(low):
            prior_trailing_lows.append(low)
            if len(prior_trailing_lows) > trailing_lookback:
                prior_trailing_lows.pop(0)

    if remaining_fraction > 0.0:
        realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
    if np.isfinite(ema20_exit_armed_price):
        ema20_exit_was_better_than_final = ema20_exit_armed_price > exit_price

    gross_return = realized_r * initial_risk / entry_price
    round_trip_fee = config.fee_rate * 2.0
    net_return = gross_return - round_trip_fee
    result = {
        "symbol": symbol,
        "status": "closed",
        "anomaly_timestamp_ms": anomaly_ts,
        "anomaly_timestamp_utc": _timestamp_to_utc(anomaly_ts),
        "decision_timestamp_ms": decision_ts,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
        "entry_timestamp_ms": entry_ts,
        "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
        "entry_method": config.entry_method,
        "execution_model": execution_model,
        "pullback_box_fraction": config.pullback_box_fraction if config.entry_method == "pullback_box_fraction" else np.nan,
        "entry_delay_ms": int(entry_ts - decision_ts),
        "entry_delay_candles": _entry_delay_candles(frame, entry_timestamp_ms=entry_ts, decision_timestamp_ms=decision_ts),
        "latency_enabled": bool(config.latency_enabled),
        "latency_extra_ms": int(config.latency_extra_ms) if config.latency_enabled else 0,
        "entry_price": entry_price,
        **entry_audit,
        "initial_stop": initial_stop,
        "initial_risk": initial_risk,
        "initial_risk_pct": initial_risk_pct,
        "box_range": box_range,
        "box_high": box_high,
        "base_tp1_price": base_tp1_price,
        "tp1_price": tp1_price,
        "tp1_round_step": tp1_round_step,
        "tp1_target_basis": TP1_TARGET_BASIS,
        "tp1_basis_price": tp1_basis_price,
        "tp1_basis_risk": tp1_risk,
        "tp1_basis_risk_pct": tp1_risk_pct,
        "tp1_r": config.tp1_r,
        "tp1_target_model": "next_round_number_above_0p75r_pump_leg_bottom",
        "tp1_hit": tp1_hit,
        "tp1_fill_model": tp1_fill_model,
        "tp1_fill_status": tp1_fill_status,
        "tp1_fill_timestamp_ms": tp1_fill_timestamp_ms,
        "tp1_fill_timestamp_utc": _timestamp_to_utc(tp1_fill_timestamp_ms) if np.isfinite(tp1_fill_timestamp_ms) else "",
        "tp1_raw_price": tp1_price,
        "tp1_fill_price": tp1_fill_price,
        "intrabar_path_assumption": intrabar_path_assumption,
        "entry_candle_path_model": "entry_candle_included_stop_first",
        "post_entry_simulation_start_timestamp_ms": post_entry_simulation_start_ts,
        "post_entry_simulation_start_timestamp_utc": _timestamp_to_utc(post_entry_simulation_start_ts),
        "post_entry_simulation_includes_entry_candle": bool(post_entry_simulation_start_ts == int(entry_ts)),
        "tp1_fraction": config.tp1_fraction,
        "trail_stop_final": trail_stop,
        "exit_rule": config.exit_rule,
        "ema20_exit_armed": ema20_exit_armed,
        "ema20_exit_armed_timestamp_ms": ema20_exit_armed_ts,
        "ema20_exit_armed_timestamp_utc": _timestamp_to_utc(ema20_exit_armed_ts) if np.isfinite(ema20_exit_armed_ts) else "",
        "ema20_exit_armed_price": ema20_exit_armed_price,
        "ema20_exit_triggered": ema20_exit_triggered,
        "ema20_exit_was_better_than_final": ema20_exit_was_better_than_final,
        "exit_timestamp_ms": exit_ts,
        "exit_timestamp_utc": _timestamp_to_utc(exit_ts),
        "exit_raw_price": raw_exit_price,
        "exit_price": exit_price,
        "exit_fill_price_model": exit_fill_model,
        "exit_reason": exit_reason,
        "holding_candles": int((future["timestamp"] <= exit_ts).sum()),
        "mfe_pct": (max_high - entry_price) / entry_price,
        "mae_pct": (min_low - entry_price) / entry_price,
        "gross_r": realized_r,
        "gross_return": gross_return,
        "fee_rate": config.fee_rate,
        "net_return": net_return,
        "outcome_label": signal.get("outcome_label", ""),
    }
    for column in TRADE_SIGNAL_CONTEXT_COLUMNS:
        result[column] = signal.get(column, "")
    return result


def _portfolio_skip_after_resolved_entry(
    signal: pd.Series,
    *,
    resolved_trade: dict[str, object],
    skip_reason: str,
    config: AnomalyBacktestConfig,
    open_positions: list[tuple[str, int]],
) -> dict[str, object]:
    entry_ts = _safe_int(resolved_trade.get("entry_timestamp_ms")) or int(signal["decision_timestamp_ms"])
    extras: dict[str, object] = {
        "entry_timestamp_ms": entry_ts,
        "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
        "entry_price": resolved_trade.get("entry_price", float("nan")),
        "entry_raw_price": resolved_trade.get("entry_raw_price", float("nan")),
        "entry_fill_price_model": resolved_trade.get("entry_fill_price_model", ""),
        "entry_slippage_pct": resolved_trade.get("entry_slippage_pct", float(config.entry_slippage_pct)),
        "exit_slippage_pct": resolved_trade.get("exit_slippage_pct", float(config.exit_slippage_pct)),
        "initial_stop": resolved_trade.get("initial_stop", float("nan")),
        "initial_risk": resolved_trade.get("initial_risk", float("nan")),
        "initial_risk_pct": resolved_trade.get("initial_risk_pct", float("nan")),
        "box_range": resolved_trade.get("box_range", float("nan")),
        "box_high": resolved_trade.get("box_high", float("nan")),
        "portfolio_open_positions_at_entry": int(len(open_positions)),
        "portfolio_open_symbols_at_entry": ",".join(symbol for symbol, _exit_ts in open_positions),
        "max_open_positions": int(config.max_open_positions),
        "would_have_exit_timestamp_ms": resolved_trade.get("exit_timestamp_ms", float("nan")),
        "would_have_exit_timestamp_utc": resolved_trade.get("exit_timestamp_utc", ""),
        "would_have_exit_reason": resolved_trade.get("exit_reason", ""),
        "would_have_net_return": resolved_trade.get("net_return", float("nan")),
    }
    return _skipped_signal_result(
        signal,
        skip_reason=skip_reason,
        config=config,
        **extras,
    )


def simulate_anomaly_trades(
    signals: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    progress_label: str | None = None,
    frame_cache: dict[str, pd.DataFrame] | None = None,
    speed_diagnostics: list[dict[str, object]] | None = None,
) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    max_open_positions = int(config.max_open_positions)
    if max_open_positions <= 0:
        raise ValueError("max_open_positions must be > 0")
    rows: list[dict[str, object]] = []
    open_positions: list[tuple[str, int]] = []
    if frame_cache is None:
        frame_cache = {}
    execution_frame_cache: dict[str, pd.DataFrame] = {}
    ordered_signals = signals.sort_values(["decision_timestamp_ms", "symbol"]).reset_index(drop=True)
    latency_windows: dict[str, tuple[int, int]] = {}
    if config.latency_enabled and not ordered_signals.empty:
        entry_tf_ms = _timeframe_to_milliseconds(_effective_entry_timeframe(config))
        tail_ms = (
            int(config.market_entry_latency_candles) * entry_tf_ms
            + int(config.latency_extra_ms)
            + int(config.max_hold_candles) * entry_tf_ms
            + 60_000
        )
        for symbol, group in ordered_signals.groupby("symbol", sort=False):
            timestamps = pd.to_numeric(group["decision_timestamp_ms"], errors="coerce").dropna().astype("int64")
            if timestamps.empty:
                continue
            latency_windows[str(symbol)] = (int(timestamps.min()), int(timestamps.max()) + int(tail_ms))

    def _resolve_symbol_group(
        symbol: str,
        group: pd.DataFrame,
    ) -> tuple[list[tuple[int, pd.Series, dict[str, object]]], dict[str, pd.DataFrame], dict[str, object]]:
        symbol_started_at = time.monotonic()
        read_seconds = 0.0
        latency_cache_seconds = 0.0
        simulate_seconds = 0.0
        loaded_frames: dict[str, pd.DataFrame] = {}
        frame = frame_cache.get(symbol)
        if frame is None:
            read_started_at = time.monotonic()
            frame = _read_entry_simulation_frame(
                config.lab_config.cache_dir,
                symbol,
                _effective_entry_timeframe(config),
            )
            read_seconds = time.monotonic() - read_started_at
            loaded_frames[symbol] = frame
        execution_frame = None
        execution_cache_key = f"{symbol}::__latency_1s"
        if config.latency_enabled:
            execution_frame = frame_cache.get(execution_cache_key)
            if execution_frame is None:
                execution_frame = execution_frame_cache.get(symbol)
            if execution_frame is None:
                latency_started_at = time.monotonic()
                try:
                    first_decision_ts = int(pd.to_numeric(group["decision_timestamp_ms"], errors="coerce").dropna().min())
                    window_start, window_end = latency_windows.get(symbol, (first_decision_ts, first_decision_ts))
                    execution_frame = _ensure_latency_1s_cache(
                        config.lab_config.cache_dir,
                        symbol,
                        start_timestamp_ms=int(window_start),
                        end_timestamp_ms=int(window_end),
                    )
                except Exception:
                    execution_frame = pd.DataFrame()
                latency_cache_seconds = time.monotonic() - latency_started_at
                loaded_frames[execution_cache_key] = execution_frame
        resolved: list[tuple[int, pd.Series, dict[str, object]]] = []
        simulate_started_at = time.monotonic()
        for order_idx, signal in group.iterrows():
            resolved.append(
                (
                    int(order_idx),
                    signal,
                    simulate_long_signal(frame, signal, config=config, execution_frame=execution_frame),
                )
            )
        simulate_seconds = time.monotonic() - simulate_started_at
        closed_count = sum(1 for _order_idx, _signal, result in resolved if result.get("status") == "closed")
        timing_row: dict[str, object] = {
            "stage": "trade_resolve_symbol",
            "scope": _effective_entry_timeframe(config),
            "symbol": symbol,
            "status": "ok",
            "seconds": round(time.monotonic() - symbol_started_at, 6),
            "output_rows": int(len(resolved)),
            "item_count": int(len(group)),
            "read_seconds": round(read_seconds, 6),
            "latency_cache_seconds": round(latency_cache_seconds, 6),
            "simulate_seconds": round(simulate_seconds, 6),
            "closed_count": int(closed_count),
            "skipped_count": int(len(resolved) - closed_count),
        }
        return resolved, loaded_frames, timing_row

    progress_started_at = time.monotonic()
    next_progress_pct = 0
    resolved_by_order: dict[int, tuple[pd.Series, dict[str, object]]] = {}
    symbol_groups = [(str(symbol), group.copy()) for symbol, group in ordered_signals.groupby("symbol", sort=False)]
    workers = _effective_symbol_workers(
        getattr(config, "symbol_workers", DEFAULT_BACKTEST_SYMBOL_WORKERS),
        total_items=len(symbol_groups),
    )
    if workers <= 1:
        for processed_count, (symbol, group) in enumerate(symbol_groups, start=1):
            resolved, loaded_frames, timing_row = _resolve_symbol_group(symbol, group)
            if speed_diagnostics is not None:
                speed_diagnostics.append(timing_row)
            frame_cache.update(loaded_frames)
            execution_frame_cache.update(
                {
                    key.removesuffix("::__latency_1s"): frame
                    for key, frame in loaded_frames.items()
                    if key.endswith("::__latency_1s")
                }
            )
            for order_idx, signal, result in resolved:
                resolved_by_order[int(order_idx)] = (signal, result)
            if progress_label is not None:
                current_pct = int(100 * processed_count / len(symbol_groups))
                if current_pct >= next_progress_pct or processed_count == len(symbol_groups):
                    _emit_progress(
                        label=f"{progress_label}: resolve",
                        done=processed_count,
                        total=len(symbol_groups),
                        started_at=progress_started_at,
                    )
                    next_progress_pct = current_pct + 5
    else:
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="anomaly-trade")
        futures = {
            executor.submit(_resolve_symbol_group, symbol, group): symbol
            for symbol, group in symbol_groups
        }
        try:
            for processed_count, future in enumerate(as_completed(futures), start=1):
                resolved, loaded_frames, timing_row = future.result()
                if speed_diagnostics is not None:
                    speed_diagnostics.append(timing_row)
                frame_cache.update(loaded_frames)
                execution_frame_cache.update(
                    {
                        key.removesuffix("::__latency_1s"): frame
                        for key, frame in loaded_frames.items()
                        if key.endswith("::__latency_1s")
                    }
                )
                for order_idx, signal, result in resolved:
                    resolved_by_order[int(order_idx)] = (signal, result)
                if progress_label is not None:
                    current_pct = int(100 * processed_count / len(symbol_groups))
                    if current_pct >= next_progress_pct or processed_count == len(symbol_groups):
                        _emit_progress(
                            label=f"{progress_label}: resolve ({workers} workers)",
                            done=processed_count,
                            total=len(symbol_groups),
                            started_at=progress_started_at,
                        )
                        next_progress_pct = current_pct + 5
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    for order_idx in range(len(ordered_signals)):
        signal, result = resolved_by_order[int(order_idx)]
        symbol = str(signal["symbol"])
        decision_ts = int(signal["decision_timestamp_ms"])
        if result.get("status") == "closed":
            entry_ts = _safe_int(result.get("entry_timestamp_ms")) or decision_ts
            open_positions = [(open_symbol, exit_ts) for open_symbol, exit_ts in open_positions if int(exit_ts) >= entry_ts]
            if any(open_symbol == symbol for open_symbol, _exit_ts in open_positions):
                rows.append(
                    _portfolio_skip_after_resolved_entry(
                        signal,
                        resolved_trade=result,
                        skip_reason="overlapping_symbol_position_at_entry",
                        config=config,
                        open_positions=open_positions,
                    )
                )
                continue
            if len(open_positions) >= max_open_positions:
                rows.append(
                    _portfolio_skip_after_resolved_entry(
                        signal,
                        resolved_trade=result,
                        skip_reason="max_open_positions_at_entry",
                        config=config,
                        open_positions=open_positions,
                    )
                )
                continue
            exit_ts = _safe_int(result.get("exit_timestamp_ms"))
            if exit_ts is not None:
                result["portfolio_open_positions_at_entry"] = int(len(open_positions))
                result["max_open_positions"] = int(max_open_positions)
                open_positions.append((symbol, int(exit_ts)))
        rows.append(result)
    return pd.DataFrame(rows)

def build_bare_htf_short_fader_signals(candidates: pd.DataFrame, *, config: AnomalyBacktestConfig) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    required = {"short_trigger_status", "short_trigger_type", "decision_timestamp_ms", "symbol"}
    if required.difference(candidates.columns):
        return candidates.iloc[0:0].copy()
    enabled_triggers = _short_fader_trigger_names(config)
    result = candidates.loc[
        candidates["short_trigger_status"].astype(str).eq("triggered")
        & candidates["short_trigger_type"].astype(str).isin(enabled_triggers)
    ].copy()
    if result.empty:
        return result
    prior_spike = pd.to_numeric(result.get("prior_spike_count_72h", 0), errors="coerce").fillna(0)
    prior_fade = pd.to_numeric(result.get("prior_fast_fade_count_72h", 0), errors="coerce").fillna(0)
    result["short_fader_prior_spike_pass"] = prior_spike.ge(int(config.short_fader_min_prior_spike_count_72h))
    result["short_fader_prior_fast_fade_pass"] = prior_fade.ge(int(config.short_fader_min_prior_fast_fade_count_72h))
    result["short_fader_context_pass"] = (
        result["short_fader_prior_spike_pass"].astype(bool)
        | result["short_fader_prior_fast_fade_pass"].astype(bool)
    )
    result["short_fader_signal_contract"] = (
        "bare_htf_anomaly_wide_post_close_ltf_short_pressure_discovery_v1"
    )
    result["short_fader_context_gate_required"] = bool(config.short_fader_require_prior_context)
    if bool(config.short_fader_require_prior_context):
        result["short_fader_signal_contract"] = (
            "bare_htf_anomaly_plus_prior_crowding_fade_plus_post_close_ltf_short_pressure_v1"
        )
        result = result.loc[result["short_fader_context_pass"].astype(bool)].copy()
        if result.empty:
            return result
    priority = {
        "failed_new_high": 0,
        "taker_fade_red": 1,
        "lower_high_close_down": 2,
        "effort_no_progress": 3,
        "pullback_without_recovery": 4,
        "close_below_htf_close": 5,
        "close_below_post_mid": 6,
    }
    result["_short_trigger_priority"] = result["short_trigger_type"].map(priority).fillna(9).astype(int)
    for column in ("decision_timestamp_ms", "timestamp_ms"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    result.sort_values(
        ["decision_timestamp_ms", "_short_trigger_priority", "symbol"],
        inplace=True,
        kind="stable",
    )
    if {"symbol", "timestamp_ms"}.issubset(result.columns):
        result.drop_duplicates(["symbol", "timestamp_ms"], keep="first", inplace=True)
    result.drop(columns=["_short_trigger_priority"], errors="ignore", inplace=True)
    result.reset_index(drop=True, inplace=True)
    return result


def _attach_all_signal_columns(result: dict[str, object], signal: pd.Series) -> dict[str, object]:
    for key, value in signal.items():
        result.setdefault(str(key), value)
    return result


def simulate_short_fader_signal(
    frame: pd.DataFrame,
    signal: pd.Series,
    *,
    config: AnomalyBacktestConfig,
) -> dict[str, object]:
    symbol = str(signal["symbol"])
    decision_ts = int(signal["decision_timestamp_ms"])
    anomaly_ts = _safe_int(signal.get("anomaly_timestamp_ms")) or _safe_int(signal.get("timestamp_ms")) or decision_ts
    if frame.empty or "timestamp" not in frame.columns:
        return _attach_all_signal_columns(
            _skipped_signal_result(signal, skip_reason="missing_entry_frame", config=config),
            signal,
        )
    frame = frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    if config.market_entry_latency_candles < 1:
        raise ValueError("market_entry_latency_candles must be >= 1")
    future_entry = frame.loc[pd.to_numeric(frame["timestamp"], errors="coerce").gt(decision_ts)].head(config.market_entry_latency_candles)
    if len(future_entry) < config.market_entry_latency_candles:
        return _attach_all_signal_columns(
            _skipped_signal_result(signal, skip_reason="no_market_execution_candle", config=config),
            signal,
        )
    entry_row = future_entry.iloc[-1]
    entry_ts = int(entry_row["timestamp"])
    raw_entry_price = float(entry_row["open"])
    if not np.isfinite(raw_entry_price) or raw_entry_price <= 0.0:
        return _attach_all_signal_columns(
            _skipped_signal_result(signal, skip_reason="invalid_market_execution_price", config=config),
            signal,
        )
    entry_price = _short_entry_fill_price(raw_entry_price, config=config)
    htf_high = _safe_float(signal.get("htf_high"))
    htf_low = _safe_float(signal.get("htf_low"))
    reference_high = _safe_float(signal.get("short_trigger_reference_high"))
    reference_low = _safe_float(signal.get("short_trigger_reference_low"))
    if htf_high is None:
        htf_high = reference_high
    if htf_low is None:
        htf_low = reference_low
    if reference_high is None:
        reference_high = htf_high
    if reference_low is None:
        reference_low = htf_low
    if htf_high is None or htf_low is None or reference_high is None or reference_low is None:
        return _attach_all_signal_columns(
            _skipped_signal_result(signal, skip_reason="missing_short_stop_context", config=config),
            signal,
        )
    structure_range = max(float(htf_high) - float(htf_low), float(reference_high) - float(reference_low), 0.0)
    initial_stop = max(float(htf_high), float(reference_high)) + float(config.stop_buffer_range_fraction) * structure_range
    initial_risk = initial_stop - entry_price
    if not np.isfinite(initial_risk) or initial_risk <= 0.0:
        return _attach_all_signal_columns(
            _skipped_signal_result(
                signal,
                skip_reason="invalid_actual_market_risk",
                config=config,
                entry_timestamp_ms=entry_ts,
                entry_timestamp_utc=_timestamp_to_utc(entry_ts),
                entry_price=entry_price,
                initial_stop=initial_stop,
                initial_risk=initial_risk,
            ),
            signal,
        )
    target_r = float(config.short_fader_target_r)
    target_price = entry_price - target_r * initial_risk
    if not np.isfinite(target_price) or target_price <= 0.0:
        return _attach_all_signal_columns(
            _skipped_signal_result(
                signal,
                skip_reason="invalid_short_target_price",
                config=config,
                entry_timestamp_ms=entry_ts,
                entry_timestamp_utc=_timestamp_to_utc(entry_ts),
                entry_price=entry_price,
                initial_stop=initial_stop,
                initial_risk=initial_risk,
            ),
            signal,
        )
    simulation_frame = frame.loc[pd.to_numeric(frame["timestamp"], errors="coerce").ge(entry_ts)].head(int(config.max_hold_candles)).copy()
    if simulation_frame.empty:
        return _attach_all_signal_columns(
            _skipped_signal_result(signal, skip_reason="no_post_entry_simulation_candles", config=config),
            signal,
        )
    exit_reason = "time_exit"
    exit_ts = int(simulation_frame.iloc[-1]["timestamp"])
    raw_exit_price = float(simulation_frame.iloc[-1]["close"])
    exit_price = _short_exit_fill_price(raw_exit_price, config=config)
    exit_fill_model = "time_exit_close_plus_adverse_slippage"
    target_hit = False
    target_fill_status = "not_hit"
    min_low = float("inf")
    max_high = float("-inf")
    for _, row in simulation_frame.iterrows():
        candle_ts = int(row["timestamp"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        min_low = min(min_low, low)
        max_high = max(max_high, high)
        if high >= initial_stop:
            exit_reason = "stop_loss"
            exit_ts = candle_ts
            raw_exit_price = initial_stop
            exit_price = _short_exit_fill_price(raw_exit_price, config=config)
            exit_fill_model = "stop_price_plus_adverse_slippage"
            if low <= target_price:
                target_fill_status = "ambiguous_intrabar_stop_first"
            break
        if low < target_price:
            target_hit = True
            target_fill_status = "filled_conservative_trade_through"
            exit_reason = "target_full_exit"
            exit_ts = candle_ts
            raw_exit_price = target_price
            exit_price = _short_exit_fill_price(raw_exit_price, config=config)
            exit_fill_model = "target_limit_proxy_plus_adverse_slippage"
            break
        if low <= target_price:
            target_fill_status = "touched_not_filled_conservative"
    gross_return = (entry_price - exit_price) / entry_price
    gross_r = (entry_price - exit_price) / initial_risk
    net_return = gross_return - float(config.fee_rate) * 2.0
    result = {
        "symbol": symbol,
        "status": "closed",
        "side": "short",
        "anomaly_timestamp_ms": anomaly_ts,
        "anomaly_timestamp_utc": _timestamp_to_utc(anomaly_ts),
        "decision_timestamp_ms": decision_ts,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
        "entry_timestamp_ms": entry_ts,
        "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
        "entry_method": "short_next_ltf_open_after_trigger",
        "execution_model": _execution_model_label(config),
        "entry_delay_ms": int(entry_ts - decision_ts),
        "entry_delay_candles": _entry_delay_candles(frame, entry_timestamp_ms=entry_ts, decision_timestamp_ms=decision_ts),
        "entry_price": entry_price,
        "entry_raw_price": raw_entry_price,
        "entry_fill_price_model": "short_next_bar_open_minus_adverse_slippage",
        "slippage_model": "adverse_short_entry_and_exit",
        "entry_slippage_pct": float(config.entry_slippage_pct),
        "exit_slippage_pct": float(config.exit_slippage_pct),
        "initial_stop": initial_stop,
        "initial_risk": initial_risk,
        "initial_risk_pct": initial_risk / entry_price,
        "short_target_r": target_r,
        "tp1_r": target_r,
        "tp1_hit": bool(target_hit),
        "tp1_price": target_price,
        "tp1_fill_status": target_fill_status,
        "tp1_fraction": 1.0,
        "exit_rule": "short_full_fixed_rr",
        "exit_timestamp_ms": exit_ts,
        "exit_timestamp_utc": _timestamp_to_utc(exit_ts),
        "exit_raw_price": raw_exit_price,
        "exit_price": exit_price,
        "exit_fill_price_model": exit_fill_model,
        "exit_reason": exit_reason,
        "holding_candles": int((simulation_frame["timestamp"] <= exit_ts).sum()),
        "post_entry_simulation_start_timestamp_ms": entry_ts,
        "post_entry_simulation_start_timestamp_utc": _timestamp_to_utc(entry_ts),
        "post_entry_simulation_includes_entry_candle": True,
        "mfe_pct": (entry_price - min_low) / entry_price,
        "mae_pct": (max_high - entry_price) / entry_price,
        "gross_r": gross_r,
        "gross_return": gross_return,
        "fee_rate": float(config.fee_rate),
        "net_return": net_return,
    }
    return _attach_all_signal_columns(result, signal)


def simulate_short_fader_trades(
    signals: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    progress_label: str | None = None,
    frame_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    max_open_positions = int(config.max_open_positions)
    if max_open_positions <= 0:
        raise ValueError("max_open_positions must be > 0")
    rows: list[dict[str, object]] = []
    open_positions: list[tuple[str, int]] = []
    if frame_cache is None:
        frame_cache = {}
    ordered = signals.sort_values(["decision_timestamp_ms", "symbol"])
    started_at = time.monotonic()
    next_progress_pct = 0
    total = len(ordered)
    for processed_count, (_, signal) in enumerate(ordered.iterrows(), start=1):
        symbol = str(signal["symbol"])
        frame = frame_cache.get(symbol)
        if frame is None:
            frame = _read_entry_simulation_frame(config.lab_config.cache_dir, symbol, _effective_entry_timeframe(config))
            frame_cache[symbol] = frame
        trade = simulate_short_fader_signal(frame, signal, config=config)
        if trade.get("status") == "closed":
            entry_ts = _safe_int(trade.get("entry_timestamp_ms")) or int(signal["decision_timestamp_ms"])
            open_positions = [(open_symbol, exit_ts) for open_symbol, exit_ts in open_positions if int(exit_ts) >= entry_ts]
            skip_reason = ""
            if any(open_symbol == symbol for open_symbol, _exit_ts in open_positions):
                skip_reason = "overlapping_symbol_position_at_entry"
            elif len(open_positions) >= max_open_positions:
                skip_reason = "max_open_positions_at_entry"
            if skip_reason:
                skipped = _portfolio_skip_after_resolved_entry(
                    signal,
                    resolved_trade=trade,
                    skip_reason=skip_reason,
                    config=config,
                    open_positions=open_positions,
                )
                skipped["side"] = "short"
                rows.append(_attach_all_signal_columns(skipped, signal))
            else:
                exit_ts = _safe_int(trade.get("exit_timestamp_ms"))
                trade["portfolio_open_positions_at_entry"] = int(len(open_positions))
                trade["portfolio_open_symbols_at_entry"] = ",".join(open_symbol for open_symbol, _exit_ts in open_positions)
                trade["max_open_positions"] = int(max_open_positions)
                rows.append(trade)
                if exit_ts is not None:
                    open_positions.append((symbol, int(exit_ts)))
        else:
            rows.append(_attach_all_signal_columns(trade, signal))
        if progress_label is not None:
            next_progress_pct = _emit_progress_1pct(
                label=progress_label,
                done=processed_count,
                total=total,
                started_at=started_at,
                next_progress_pct=next_progress_pct,
            )
    return pd.DataFrame(rows)


def _metric_map(summary: pd.DataFrame) -> dict[str, object]:
    if summary.empty or not {"metric", "value"}.issubset(summary.columns):
        return {}
    return {str(row["metric"]): row["value"] for _, row in summary.iterrows()}


def summarize_short_fader_by_column(trades: pd.DataFrame, *, column: str) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns or column not in trades.columns:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for value, group in trades.groupby(column, dropna=False):
        summary = _metric_map(summarize_trades(group))
        rows.append({"group_column": column, "group_value": value, **summary})
    result = pd.DataFrame(rows)
    if not result.empty and "sum_net_return" in result.columns:
        result.sort_values(["sum_net_return", "closed_trades"], ascending=[False, False], inplace=True)
    return result


def build_short_fader_top_dependency(trades: pd.DataFrame) -> pd.DataFrame:
    columns = ["scope", "closed_trades", "sum_net_return", "top1_share", "top5_share", "top15_share"]
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=columns)
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=columns)
    net = pd.to_numeric(closed["net_return"], errors="coerce").dropna().sort_values(ascending=False)
    total = float(net.sum())
    def share(n: int) -> float:
        return float(net.head(n).sum() / total) if total > 0 else float("inf")
    return pd.DataFrame(
        [
            {
                "scope": "all_closed",
                "closed_trades": int(len(net)),
                "sum_net_return": total,
                "top1_share": share(1),
                "top5_share": share(5),
                "top15_share": share(15),
            }
        ],
        columns=columns,
    )


def build_short_fader_factor_separation(candidates: pd.DataFrame, *, config: AnomalyBacktestConfig) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    frame = candidates.copy()
    if {"symbol", "timestamp_ms"}.issubset(frame.columns):
        event_frame = frame.drop_duplicates(["symbol", "timestamp_ms"], keep="first").copy()
    else:
        event_frame = frame.copy()
    rules: list[tuple[str, pd.Series]] = []
    def numeric_series(column: str, default: float = np.nan) -> pd.Series:
        values = event_frame[column] if column in event_frame.columns else pd.Series(default, index=event_frame.index)
        return pd.to_numeric(values, errors="coerce")

    spike = numeric_series("prior_spike_count_72h", 0.0).fillna(0)
    fade = numeric_series("prior_fast_fade_count_72h", 0.0).fillna(0)
    ltf12_ret = numeric_series("ltf12_ret")
    rules.append(("all_bare_htf_anomalies", pd.Series(True, index=event_frame.index)))
    rules.append((f"prior_spike_ge_{int(config.short_fader_min_prior_spike_count_72h)}", spike.ge(int(config.short_fader_min_prior_spike_count_72h))))
    rules.append((f"prior_fast_fade_ge_{int(config.short_fader_min_prior_fast_fade_count_72h)}", fade.ge(int(config.short_fader_min_prior_fast_fade_count_72h))))
    rules.append(("ltf12_ret_lt_0", ltf12_ret.lt(0.0)))
    rules.append((f"prior_spike_ge_{int(config.short_fader_min_prior_spike_count_72h)}_and_ltf12_ret_lt_0", spike.ge(int(config.short_fader_min_prior_spike_count_72h)) & ltf12_ret.lt(0.0)))
    rules.append((f"prior_fast_fade_ge_{int(config.short_fader_min_prior_fast_fade_count_72h)}_and_ltf12_ret_lt_0", fade.ge(int(config.short_fader_min_prior_fast_fade_count_72h)) & ltf12_ret.lt(0.0)))
    rows: list[dict[str, object]] = []
    for name, mask in rules:
        subset = event_frame.loc[mask.fillna(False)].copy()
        short2 = subset.get("short2", pd.Series(dtype=bool)).astype(bool) if not subset.empty else pd.Series(dtype=bool)
        clean = subset.get("clean_short2", pd.Series(dtype=bool)).astype(bool) if not subset.empty else pd.Series(dtype=bool)
        long_mfe = pd.to_numeric(subset.get("long_mfe_from_post_close", pd.Series(dtype=float)), errors="coerce")
        short_mfe = pd.to_numeric(subset.get("short_mfe_from_post_close", pd.Series(dtype=float)), errors="coerce")
        adverse = pd.to_numeric(subset.get("adverse_up_before_short_low", pd.Series(dtype=float)), errors="coerce")
        rows.append(
            {
                "rule": name,
                "events": int(len(subset)),
                "short2_events": int(short2.sum()) if len(short2) else 0,
                "short2_rate": float(short2.mean()) if len(short2) else 0.0,
                "clean_short2_events": int(clean.sum()) if len(clean) else 0,
                "clean_short2_rate": float(clean.mean()) if len(clean) else 0.0,
                "long_up2_rate": float((long_mfe >= 0.02).mean()) if len(long_mfe) else 0.0,
                "median_short_mfe": float(short_mfe.median()) if len(short_mfe) else float("nan"),
                "median_adverse_before_low": float(adverse.median()) if len(adverse) else float("nan"),
            }
        )
    return pd.DataFrame(rows).sort_values(["short2_rate", "events"], ascending=[False, False]).reset_index(drop=True)


def build_short_fader_label_distribution(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    events = candidates.drop_duplicates(["symbol", "timestamp_ms"], keep="first").copy() if {"symbol", "timestamp_ms"}.issubset(candidates.columns) else candidates.copy()
    rows = [{"metric": "events", "value": int(len(events))}]
    for column in ("short2", "clean_short2"):
        if column in events.columns:
            values = events[column].astype(bool)
            rows.append({"metric": f"{column}_events", "value": int(values.sum())})
            rows.append({"metric": f"{column}_rate", "value": float(values.mean()) if len(values) else 0.0})
    if "long_mfe_from_post_close" in events.columns:
        long_mfe = pd.to_numeric(events["long_mfe_from_post_close"], errors="coerce")
        rows.append({"metric": "long_up2_events", "value": int(long_mfe.ge(0.02).sum())})
        rows.append({"metric": "long_up2_rate", "value": float(long_mfe.ge(0.02).mean()) if len(long_mfe) else 0.0})
    for column in ("short_post_close_ltf_status", "short_trigger_status"):
        if column in candidates.columns:
            for value, count in candidates[column].astype(str).value_counts(dropna=False).items():
                rows.append({"metric": f"{column}:{value}", "value": int(count)})
    return pd.DataFrame(rows)


def build_short_fader_data_quality_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame([{"metric": "candidate_rows", "value": 0}])
    rows: list[dict[str, object]] = [{"metric": "candidate_rows", "value": int(len(candidates))}]
    if "symbol" in candidates.columns and "timestamp_ms" in candidates.columns:
        rows.append({"metric": "unique_events", "value": int(candidates.drop_duplicates(["symbol", "timestamp_ms"]).shape[0])})
    if "trade_count_proxy_used" in candidates.columns:
        proxy = candidates["trade_count_proxy_used"].astype(str).str.lower().isin({"true", "1", "yes"})
        rows.append({"metric": "trade_count_proxy_rows", "value": int(proxy.sum())})
    for column in (
        "levels_trade_count_source",
        "entry_trade_count_source",
        "levels_quote_volume_source",
        "entry_quote_volume_source",
        "short_post_close_ltf_status",
    ):
        if column in candidates.columns:
            for value, count in candidates[column].astype(str).value_counts(dropna=False).items():
                rows.append({"metric": f"{column}:{value}", "value": int(count)})
    return pd.DataFrame(rows)


def build_short_fader_funnel(
    *,
    candidates: pd.DataFrame,
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    live_filtered: pd.DataFrame,
) -> pd.DataFrame:
    def event_count(frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        if {"symbol", "timestamp_ms"}.issubset(frame.columns):
            return int(frame.drop_duplicates(["symbol", "timestamp_ms"]).shape[0])
        return int(len(frame))

    rows = [
        {"stage": "bare_htf_anomaly_events", "rows": int(len(candidates)), "events": event_count(candidates)},
    ]
    if not candidates.empty and "short_post_close_ltf_status" in candidates.columns:
        ok = candidates.loc[candidates["short_post_close_ltf_status"].astype(str).eq("ok")]
        rows.append({"stage": "post_close_ltf_window_ok", "rows": int(len(ok)), "events": event_count(ok)})
    if not candidates.empty and "short_trigger_status" in candidates.columns:
        triggered = candidates.loc[candidates["short_trigger_status"].astype(str).eq("triggered")]
        rows.append({"stage": "short_pressure_triggered", "rows": int(len(triggered)), "events": event_count(triggered)})
    rows.append({"stage": "discovery_signals", "rows": int(len(signals)), "events": event_count(signals)})
    closed = trades.loc[trades["status"].eq("closed")] if not trades.empty and "status" in trades.columns else pd.DataFrame()
    rows.append({"stage": "raw_closed_trades", "rows": int(len(closed)), "events": event_count(closed)})
    live_closed = live_filtered.loc[live_filtered["status"].eq("closed")] if not live_filtered.empty and "status" in live_filtered.columns else pd.DataFrame()
    rows.append({"stage": "live_filtered_closed_trades", "rows": int(len(live_closed)), "events": event_count(live_closed)})
    return pd.DataFrame(rows)


def build_short_fader_top_trades(trades: pd.DataFrame, *, limit: int = 50) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns or "net_return" not in trades.columns:
        return pd.DataFrame()
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame()
    closed["net_return"] = pd.to_numeric(closed["net_return"], errors="coerce")
    columns = [
        column
        for column in (
            "symbol",
            "anomaly_timestamp_utc",
            "decision_timestamp_utc",
            "entry_timestamp_utc",
            "short_trigger_type",
            "exit_reason",
            "net_return",
            "gross_r",
            "mfe_pct",
            "mae_pct",
            "prior_spike_count_72h",
            "prior_fast_fade_count_72h",
            "ltf12_ret",
            "short_mfe_from_post_close",
            "long_mfe_from_post_close",
            "adverse_up_before_short_low",
        )
        if column in closed.columns
    ]
    return closed.sort_values("net_return", ascending=False).head(int(limit)).loc[:, columns].reset_index(drop=True)


def build_short_fader_post_close_path_slices(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    max_events: int = 5_000,
) -> pd.DataFrame:
    if candidates.empty or not {"symbol", "timestamp_ms", "short_post_close_start_ms", "short_post_close_end_ms"}.issubset(candidates.columns):
        return pd.DataFrame()
    events = candidates.drop_duplicates(["symbol", "timestamp_ms"], keep="first").copy()
    if len(events) > int(max_events):
        events = events.head(int(max_events)).copy()
    cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    entry_ms = _timeframe_to_milliseconds(_effective_entry_timeframe(config))
    for _, event in events.iterrows():
        symbol = str(event["symbol"])
        start_ts = _safe_int(event.get("short_post_close_start_ms"))
        end_ts = _safe_int(event.get("short_post_close_end_ms"))
        anchor_close = _safe_float(event.get("htf_close"))
        if start_ts is None or end_ts is None:
            continue
        frame = cache.get(symbol)
        if frame is None:
            try:
                frame = _read_entry_simulation_frame(config.lab_config.cache_dir, symbol, _effective_entry_timeframe(config))
            except Exception:
                frame = pd.DataFrame()
            cache[symbol] = frame
        if frame.empty:
            continue
        path = frame.loc[
            pd.to_numeric(frame["timestamp"], errors="coerce").ge(start_ts)
            & pd.to_numeric(frame["timestamp"], errors="coerce").lt(end_ts)
        ].copy()
        if path.empty:
            continue
        for idx, candle in enumerate(path.itertuples(index=False), start=0):
            timestamp = int(getattr(candle, "timestamp"))
            open_ = float(getattr(candle, "open"))
            high = float(getattr(candle, "high"))
            low = float(getattr(candle, "low"))
            close = float(getattr(candle, "close"))
            row = {
                "symbol": symbol,
                "anomaly_timestamp_ms": int(event["timestamp_ms"]),
                "anomaly_timestamp_utc": _timestamp_to_utc(int(event["timestamp_ms"])),
                "relative_candle": int(idx),
                "relative_ms": int(timestamp - start_ts),
                "timestamp_ms": timestamp,
                "timestamp_utc": _timestamp_to_utc(timestamp),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "ret_from_htf_close": _safe_divide_value(close - anchor_close, anchor_close) if anchor_close else float("nan"),
                "high_from_htf_close": _safe_divide_value(high - anchor_close, anchor_close) if anchor_close else float("nan"),
                "low_from_htf_close": _safe_divide_value(low - anchor_close, anchor_close) if anchor_close else float("nan"),
                "entry_timeframe_ms": int(entry_ms),
            }
            for column in ("quote_volume", "number_of_trades", "taker_buy_quote_volume"):
                if hasattr(candle, column):
                    row[column] = float(getattr(candle, column))
            rows.append(row)
    return pd.DataFrame(rows)


def build_short_fader_decay_category_artifacts(candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
    events = candidates.drop_duplicates(["symbol", "timestamp_ms"], keep="first").copy() if {"symbol", "timestamp_ms"}.issubset(candidates.columns) else candidates.copy()
    if events.empty:
        return events, pd.DataFrame()
    def numeric_series(column: str) -> pd.Series:
        values = events[column] if column in events.columns else pd.Series(np.nan, index=events.index)
        return pd.to_numeric(values, errors="coerce")

    short_mfe = numeric_series("short_mfe_from_post_close")
    long_mfe = numeric_series("long_mfe_from_post_close")
    adverse = numeric_series("adverse_up_before_short_low")
    ltf12_ret = numeric_series("ltf12_ret")
    ltf12_taker = numeric_series("ltf12_taker_buy_quote_share")
    clean_short = events.get("clean_short2", pd.Series(False, index=events.index)).astype(bool)
    short2 = events.get("short2", pd.Series(False, index=events.index)).astype(bool)
    categories: list[str] = []
    reasons: list[str] = []
    for idx in events.index:
        reason_parts: list[str] = []
        category = "unclassified_decay_research"
        if bool(clean_short.loc[idx]):
            category = "clean_fader"
            reason_parts.append("short2_clean_adverse")
        elif bool(short2.loc[idx]) and np.isfinite(adverse.loc[idx]) and adverse.loc[idx] > 0.015:
            category = "dirty_fader_after_adverse_pop"
            reason_parts.append("short2_after_adverse")
        elif np.isfinite(long_mfe.loc[idx]) and long_mfe.loc[idx] >= 0.02 and (not np.isfinite(short_mfe.loc[idx]) or short_mfe.loc[idx] < 0.02):
            category = "continuation_risk"
            reason_parts.append("long_up2_without_short2")
        elif np.isfinite(ltf12_ret.loc[idx]) and ltf12_ret.loc[idx] < 0.0 and np.isfinite(ltf12_taker.loc[idx]) and ltf12_taker.loc[idx] < 0.48:
            category = "early_ltf_flow_decay"
            reason_parts.append("ltf12_negative_weak_taker")
        elif np.isfinite(short_mfe.loc[idx]) and short_mfe.loc[idx] >= 0.01:
            category = "shallow_decay"
            reason_parts.append("short_mfe_ge_1pct")
        else:
            reason_parts.append("no_clear_decay_edge")
        categories.append(category)
        reasons.append(",".join(reason_parts))
    events["short_decay_category_candidate"] = categories
    events["short_decay_category_reason"] = reasons

    rows: list[dict[str, object]] = []
    for category, group in events.groupby("short_decay_category_candidate", dropna=False):
        group_short2 = group.get("short2", pd.Series(dtype=bool)).astype(bool)
        group_clean = group.get("clean_short2", pd.Series(dtype=bool)).astype(bool)
        group_long_mfe = pd.to_numeric(group.get("long_mfe_from_post_close", pd.Series(dtype=float)), errors="coerce")
        group_short_mfe = pd.to_numeric(group.get("short_mfe_from_post_close", pd.Series(dtype=float)), errors="coerce")
        rows.append(
            {
                "short_decay_category_candidate": category,
                "events": int(len(group)),
                "short2_rate": float(group_short2.mean()) if len(group_short2) else 0.0,
                "clean_short2_rate": float(group_clean.mean()) if len(group_clean) else 0.0,
                "long_up2_rate": float(group_long_mfe.ge(0.02).mean()) if len(group_long_mfe) else 0.0,
                "median_short_mfe": float(group_short_mfe.median()) if len(group_short_mfe) else float("nan"),
                "symbols": int(group["symbol"].nunique()) if "symbol" in group.columns else 0,
            }
        )
    summary = pd.DataFrame(rows).sort_values(["short2_rate", "events"], ascending=[False, False]).reset_index(drop=True)
    return events, summary


def run_short_fader_exit_grid(
    signals: pd.DataFrame,
    config: AnomalyBacktestConfig,
    *,
    frame_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    rr_values = (1.5, 2.0, 2.5, 3.0)
    trigger_sets: list[tuple[str, pd.DataFrame]] = [("all", signals)]
    if "short_trigger_type" in signals.columns:
        for trigger_type, group in signals.groupby("short_trigger_type", dropna=False):
            trigger_sets.append((str(trigger_type), group.copy()))
    rows: list[dict[str, object]] = []
    cache = frame_cache if frame_cache is not None else {}
    for rr in rr_values:
        variant = replace(config, short_fader_target_r=float(rr))
        for trigger_name, signal_set in trigger_sets:
            trades = simulate_short_fader_trades(signal_set, config=variant, frame_cache=cache)
            live_filtered = apply_live_portfolio_filter(
                trades,
                max_open_positions=DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS,
            )
            row = {
                "target_r": float(rr),
                "trigger_scope": trigger_name,
                "signals": int(len(signal_set)),
            }
            row.update({f"raw_{key}": value for key, value in _metric_map(summarize_trades(trades)).items()})
            row.update({f"live_filtered_{key}": value for key, value in _metric_map(summarize_trades(live_filtered)).items()})
            top = build_short_fader_top_dependency(live_filtered)
            if not top.empty:
                row["live_filtered_top5_share"] = float(top.iloc[0]["top5_share"])
                row["live_filtered_top15_share"] = float(top.iloc[0]["top15_share"])
            rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        sort_cols = [column for column in ("live_filtered_sum_net_return", "live_filtered_closed_trades") if column in result.columns]
        if sort_cols:
            result.sort_values(sort_cols, ascending=[False] * len(sort_cols), inplace=True)
    return result.reset_index(drop=True)


def run_bare_htf_short_fader_backtest(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    output_dir: Path,
    symbols: Iterable[str] | None,
    targeted_flow_plan: pd.DataFrame,
    targeted_flow_fetch: pd.DataFrame,
    targeted_flow_materialize: pd.DataFrame,
    targeted_flow_coverage: pd.DataFrame,
    timings: dict[str, float],
    total_started_at: float,
    speed_diagnostics: list[dict[str, object]] | None = None,
) -> Path:
    print("bare HTF short/fader: filtering signals", flush=True)
    stage_started_at = time.monotonic()
    signals = build_bare_htf_short_fader_signals(candidates, config=config)
    _record_stage_timing(timings, speed_diagnostics, "short_signals_seconds", time.monotonic() - stage_started_at, output_rows=len(signals), item_count=len(candidates))

    stage_started_at = time.monotonic()
    decay_events, decay_summary = build_short_fader_decay_category_artifacts(candidates)
    path_slices = build_short_fader_post_close_path_slices(candidates, config=config)
    _write_artifact_frames(
        [
            (output_dir / "bare_htf_short_candidates.csv", candidates),
            (output_dir / "bare_htf_short_features.csv", candidates),
            (output_dir / "bare_htf_short_labels.csv", candidates),
            (output_dir / "bare_htf_short_triggers.csv", candidates.loc[candidates.get("short_trigger_status", pd.Series(dtype=str)).astype(str).eq("triggered")].copy() if not candidates.empty and "short_trigger_status" in candidates.columns else pd.DataFrame()),
            (output_dir / "bare_htf_short_signals.csv", signals),
            (output_dir / "bare_htf_short_factor_separation.csv", build_short_fader_factor_separation(candidates, config=config)),
            (output_dir / "bare_htf_short_label_distribution.csv", build_short_fader_label_distribution(candidates)),
            (output_dir / "bare_htf_short_data_quality_summary.csv", build_short_fader_data_quality_summary(candidates)),
            (output_dir / "bare_htf_short_post_close_path_slices.csv", path_slices),
            (output_dir / "bare_htf_short_decay_category_events.csv", decay_events),
            (output_dir / "bare_htf_short_decay_category_summary.csv", decay_summary),
            (output_dir / "anomaly_universe_contract.csv", _universe_contract_frame(symbols=symbols)),
        ],
        progress_label="short/fader artifacts: base files",
        timing_rows=speed_diagnostics,
        timing_scope="short/fader artifacts: base files",
    )
    _record_stage_timing(timings, speed_diagnostics, "short_base_artifacts_seconds", time.monotonic() - stage_started_at)

    print(f"bare HTF short/fader: simulating {len(signals)} signals", flush=True)
    stage_started_at = time.monotonic()
    frame_cache: dict[str, pd.DataFrame] = {}
    trades = simulate_short_fader_trades(signals, config=config, progress_label="short/fader trades", frame_cache=frame_cache)
    _record_stage_timing(timings, speed_diagnostics, "short_trades_seconds", time.monotonic() - stage_started_at, output_rows=len(trades), item_count=len(signals))
    live_filtered = apply_live_portfolio_filter(
        trades,
        max_open_positions=DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS,
    )
    summary = summarize_trades(trades)
    live_summary = summarize_trades(live_filtered)
    stage_started_at = time.monotonic()
    exit_grid = (
        run_short_fader_exit_grid(signals, config, frame_cache=frame_cache)
        if bool(config.short_fader_run_exit_grid)
        else _disabled_artifact_frame(
            artifact="bare_htf_short_exit_grid.csv",
            reason="short_fader_run_exit_grid_false",
        )
    )
    _record_stage_timing(timings, speed_diagnostics, "short_exit_grid_seconds", time.monotonic() - stage_started_at, output_rows=len(exit_grid))

    context_parity_report = pd.DataFrame(
        [
            {
                "context_parity_status": "ok",
                "detail": "short/fader contract does not use long pump categories as entry classes",
                "signals": int(len(signals)),
            }
        ]
    )
    honesty_report = build_backtest_honesty_report(
        config=config,
        candidates=candidates,
        signals=signals,
        trades=trades,
        symbols=symbols,
        context_parity_report=context_parity_report,
        run_entry_grid=False,
        run_latency_grid=False,
    )
    run_verdict = pd.DataFrame(
        [
            {
                "verdict": "research_only",
                "valid_backtest": True,
                "reason": "bare_htf_short_fader_contract_written;not_live_ready_without_larger_validation",
                "feature_contract": BARE_HTF_SHORT_FADER_CONTRACT,
                "signals": int(len(signals)),
                "closed_trades": int(trades["status"].eq("closed").sum()) if "status" in trades.columns else 0,
            }
        ]
    )
    stage_started_at = time.monotonic()
    _write_artifact_frames(
        [
            (output_dir / "bare_htf_short_trades_raw.csv", trades),
            (output_dir / "bare_htf_short_trades_live_filtered.csv", live_filtered),
            (output_dir / "bare_htf_short_profitability_summary.csv", summary),
            (output_dir / "bare_htf_short_profitability_summary_live_filtered.csv", live_summary),
            (output_dir / "bare_htf_short_skip_reasons.csv", summarize_trade_skip_reasons(trades)),
            (output_dir / "bare_htf_short_skip_reasons_live_filtered.csv", summarize_trade_skip_reasons(live_filtered)),
            (output_dir / "bare_htf_short_by_symbol.csv", summarize_trades_by_symbol(trades)),
            (output_dir / "bare_htf_short_by_symbol_live_filtered.csv", summarize_trades_by_symbol(live_filtered)),
            (output_dir / "bare_htf_short_by_trigger.csv", summarize_short_fader_by_column(trades, column="short_trigger_type")),
            (output_dir / "bare_htf_short_by_trigger_live_filtered.csv", summarize_short_fader_by_column(live_filtered, column="short_trigger_type")),
            (output_dir / "bare_htf_short_top_dependency.csv", build_short_fader_top_dependency(trades)),
            (output_dir / "bare_htf_short_top_dependency_live_filtered.csv", build_short_fader_top_dependency(live_filtered)),
            (output_dir / "bare_htf_short_top_trades.csv", build_short_fader_top_trades(trades)),
            (output_dir / "bare_htf_short_top_trades_live_filtered.csv", build_short_fader_top_trades(live_filtered)),
            (output_dir / "bare_htf_short_funnel.csv", build_short_fader_funnel(candidates=candidates, signals=signals, trades=trades, live_filtered=live_filtered)),
            (output_dir / "bare_htf_short_exit_grid.csv", exit_grid),
            (output_dir / "bare_htf_short_edge_health.csv", build_edge_health_table(trades, label="short_fader_primary")),
            (output_dir / "bare_htf_short_edge_health_live_filtered.csv", build_edge_health_table(live_filtered, label="short_fader_live_filtered")),
            (output_dir / "anomaly_context_parity_report.csv", context_parity_report),
            (output_dir / "anomaly_backtest_honesty_report.csv", honesty_report),
            (output_dir / "anomaly_run_verdict.csv", run_verdict),
        ],
        progress_label="short/fader artifacts: trade files",
        timing_rows=speed_diagnostics,
        timing_scope="short/fader artifacts: trade files",
    )
    _record_stage_timing(timings, speed_diagnostics, "short_trade_artifacts_seconds", time.monotonic() - stage_started_at)

    requested_symbols_normalized = _normalized_symbol_tuple(symbols)
    _record_stage_timing(timings, speed_diagnostics, "total_seconds", time.monotonic() - total_started_at)
    run_config = {
        **asdict(config),
        "feature_contract": BARE_HTF_SHORT_FADER_CONTRACT,
        "universe_symbol_scope": _universe_symbol_scope(symbols),
        "universe_requested_symbols_count": int(len(requested_symbols_normalized)),
        "universe_requested_symbols_normalized": requested_symbols_normalized,
        "historical_listing_snapshot_available": False,
        "survivorship_bias_risk": _universe_symbol_scope(symbols) == "cache_snapshot_scan",
        "setup_timeframe": _effective_setup_timeframe(config),
        "entry_timeframe": _effective_entry_timeframe(config),
        "execution_model": _execution_model_label(config),
        "portfolio_model": f"global_max_open_positions_{int(config.max_open_positions)}_at_actual_entry",
        "live_filtered_portfolio_model": (
            f"post_simulation_global_max_open_positions_{int(DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS)}"
        ),
        "live_filtered_max_open_positions": int(DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS),
        "slippage_model": "adverse_short_entry_and_exit",
        "entry_slippage_pct": float(config.entry_slippage_pct),
        "exit_slippage_pct": float(config.exit_slippage_pct),
        "prior_context_lookback_hours": int(_PRIOR_CONTEXT_LIVE_LOOKBACK_HOURS),
        "valid_backtest": True,
        "backtest_verdict": "research_only",
        "backtest_invalid_reason": "",
        "targeted_flow_planned_windows": int(_targeted_flow_planned_window_count(targeted_flow_plan)),
        "targeted_flow_ready_windows": int((targeted_flow_coverage.get("coverage_status", pd.Series(dtype=str)).astype(str) == "ready").sum()) if not targeted_flow_coverage.empty else 0,
        "lab_config": asdict(config.lab_config),
    }
    timing_frame = pd.DataFrame(
        [{"stage": key, "seconds": round(float(value), 3)} for key, value in timings.items()]
    )
    speed_detail_frame = speed_diagnostics_frame(speed_diagnostics)
    speed_summary_frame = speed_diagnostics_summary_frame(
        speed_diagnostics,
        total_seconds=float(timings.get("total_seconds", 0.0)),
    )
    slowest_symbols_frame = speed_diagnostics_slowest_symbols_frame(speed_diagnostics)
    _write_artifact_frames(
        [(output_dir / "anomaly_timing_summary.csv", timing_frame)],
        progress_label="short/fader artifacts: timing summary",
        timing_rows=speed_diagnostics,
        timing_scope="short/fader artifacts: timing summary",
    )
    _write_artifact_frames(
        [
            (output_dir / "anomaly_speed_diagnostics.csv", speed_detail_frame),
            (output_dir / "anomaly_speed_summary.csv", speed_summary_frame),
            (output_dir / "anomaly_slowest_symbols.csv", slowest_symbols_frame),
        ],
        progress_label="short/fader artifacts: speed diagnostics",
    )
    _write_artifact_frames(
        [(output_dir / "run_config.csv", pd.DataFrame([run_config]))],
        progress_label="short/fader artifacts: run config",
        timing_rows=speed_diagnostics,
        timing_scope="short/fader artifacts: run config",
    )
    print(
        "bare HTF short/fader result: "
        f"signals={len(signals)} "
        f"closed={_summary_metric(summary, 'closed_trades')} "
        f"skipped={_summary_metric(summary, 'skipped_trades')} "
        f"avg_net={float(_summary_metric(summary, 'avg_net_return', 0.0)):.4%} "
        f"sum_net={float(_summary_metric(summary, 'sum_net_return', 0.0)):.4%} "
        f"win_rate={float(_summary_metric(summary, 'win_rate', 0.0)):.2%}",
        flush=True,
    )
    return output_dir


def apply_live_portfolio_filter(
    trades: pd.DataFrame,
    *,
    max_open_positions: int = DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS,
) -> pd.DataFrame:
    """Apply a live-like portfolio cap after the wide research simulation."""

    if trades.empty or "status" not in trades.columns:
        return trades.copy()
    if max_open_positions <= 0:
        raise ValueError("max_open_positions must be > 0")
    if "entry_timestamp_ms" not in trades.columns or "exit_timestamp_ms" not in trades.columns:
        return trades.copy()

    original = trades.copy()
    original["_original_order"] = np.arange(len(original))
    closed = original.loc[original["status"].eq("closed")].copy()
    non_closed = original.loc[~original["status"].eq("closed")].copy()
    if closed.empty:
        return original.drop(columns=["_original_order"], errors="ignore")

    for column in ("entry_timestamp_ms", "exit_timestamp_ms", "decision_timestamp_ms", "pump_category_rank"):
        if column in closed.columns:
            closed[column] = pd.to_numeric(closed[column], errors="coerce")
    sort_columns = [
        column
        for column in ("entry_timestamp_ms", "pump_category_rank", "decision_timestamp_ms", "symbol", "_original_order")
        if column in closed.columns
    ]
    closed.sort_values(sort_columns, inplace=True)

    open_positions: list[tuple[str, int]] = []
    filtered_rows: list[pd.Series] = []
    for _, row in closed.iterrows():
        entry_ts = _safe_int(row.get("entry_timestamp_ms"))
        exit_ts = _safe_int(row.get("exit_timestamp_ms"))
        symbol = str(row.get("symbol", ""))
        if entry_ts is None or exit_ts is None:
            skipped = row.copy()
            skipped["status"] = "skipped"
            skipped["skip_reason"] = "live_portfolio_filter_invalid_entry_or_exit_timestamp"
            skipped["execution_guard"] = False
            filtered_rows.append(skipped)
            continue
        open_positions = [
            (open_symbol, open_exit_ts)
            for open_symbol, open_exit_ts in open_positions
            if int(open_exit_ts) >= entry_ts
        ]
        skip_reason = ""
        if any(open_symbol == symbol for open_symbol, _open_exit_ts in open_positions):
            skip_reason = "live_portfolio_filter_overlapping_symbol_position_at_entry"
        elif len(open_positions) >= int(max_open_positions):
            skip_reason = "live_portfolio_filter_max_open_positions_at_entry"

        if skip_reason:
            skipped = row.copy()
            skipped["status"] = "skipped"
            skipped["skip_reason"] = skip_reason
            skipped["execution_guard"] = False
            skipped["would_have_exit_timestamp_ms"] = row.get("exit_timestamp_ms", float("nan"))
            skipped["would_have_exit_timestamp_utc"] = row.get("exit_timestamp_utc", "")
            skipped["would_have_exit_reason"] = row.get("exit_reason", "")
            skipped["would_have_net_return"] = row.get("net_return", float("nan"))
            skipped["portfolio_open_positions_at_entry"] = int(len(open_positions))
            skipped["portfolio_open_symbols_at_entry"] = ",".join(open_symbol for open_symbol, _exit_ts in open_positions)
            filtered_rows.append(skipped)
            continue

        kept = row.copy()
        kept["portfolio_open_positions_at_entry"] = int(len(open_positions))
        kept["portfolio_open_symbols_at_entry"] = ",".join(open_symbol for open_symbol, _exit_ts in open_positions)
        kept["max_open_positions"] = int(max_open_positions)
        filtered_rows.append(kept)
        open_positions.append((symbol, int(exit_ts)))

    filtered = pd.concat([pd.DataFrame(filtered_rows), non_closed], ignore_index=True, sort=False)
    filtered.sort_values("_original_order", inplace=True)
    return filtered.drop(columns=["_original_order"], errors="ignore").reset_index(drop=True)


def summarize_live_portfolio_filter(raw_trades: pd.DataFrame, filtered_trades: pd.DataFrame) -> pd.DataFrame:
    raw_closed = int(raw_trades["status"].eq("closed").sum()) if "status" in raw_trades.columns else 0
    filtered_closed = int(filtered_trades["status"].eq("closed").sum()) if "status" in filtered_trades.columns else 0
    skipped = (
        filtered_trades.loc[
            filtered_trades["skip_reason"].astype(str).str.startswith("live_portfolio_filter_")
        ].copy()
        if "skip_reason" in filtered_trades.columns
        else pd.DataFrame()
    )
    rows = [
        {"metric": "raw_closed_trades", "value": raw_closed},
        {"metric": "live_filtered_closed_trades", "value": filtered_closed},
        {"metric": "live_filter_skipped_trades", "value": int(len(skipped))},
        {
            "metric": "live_filter_kept_share_of_raw_closed",
            "value": float(filtered_closed / raw_closed) if raw_closed else 0.0,
        },
    ]
    if not skipped.empty:
        for reason, count in skipped["skip_reason"].astype(str).value_counts().sort_values(ascending=False).items():
            rows.append({"metric": f"live_filter_reason:{reason}", "value": int(count)})
    return pd.DataFrame(rows)


def summarize_trade_skip_reasons(trades: pd.DataFrame) -> pd.DataFrame:
    columns = ["status", "skip_reason", "count", "share_of_all", "execution_guard"]
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=columns)
    skipped = trades.loc[trades["status"].eq("skipped")].copy()
    if skipped.empty:
        return pd.DataFrame(columns=columns)
    if "skip_reason" not in skipped.columns:
        raise ValueError("skipped anomaly trades missing skip_reason column")
    grouped = skipped.groupby("skip_reason", dropna=False).size().reset_index(name="count")
    grouped["status"] = "skipped"
    grouped["share_of_all"] = grouped["count"].astype(float) / float(len(trades))
    grouped["execution_guard"] = grouped["skip_reason"].astype(str).isin(EXECUTION_GUARD_SKIP_REASONS)
    return grouped[columns].sort_values(["count", "skip_reason"], ascending=[False, True]).reset_index(drop=True)


def summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}, {"metric": "skipped_trades", "value": 0}])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    skipped = trades.loc[trades["status"].eq("skipped")].copy()
    rows: list[dict[str, object]] = [
        {"metric": "closed_trades", "value": int(len(closed))},
        {"metric": "skipped_trades", "value": int(len(skipped))},
    ]
    if not skipped.empty:
        skip_counts = summarize_trade_skip_reasons(trades)
        execution_guard_skips = int(skip_counts.loc[skip_counts["execution_guard"].astype(bool), "count"].sum())
        rows.append({"metric": "execution_guard_skips", "value": execution_guard_skips})
        for _, row in skip_counts.iterrows():
            rows.append({"metric": f"skip_reason:{row['skip_reason']}", "value": int(row["count"])})
    if closed.empty:
        return pd.DataFrame(rows)
    net = closed["net_return"].astype(float)
    gross_r = closed["gross_r"].astype(float)
    wins = net > 0
    rows.extend(
        [
            {"metric": "symbols", "value": int(closed["symbol"].nunique())},
            {"metric": "win_rate", "value": float(wins.mean())},
            {"metric": "avg_net_return", "value": float(net.mean())},
            {"metric": "median_net_return", "value": float(net.median())},
            {"metric": "sum_net_return", "value": float(net.sum())},
            {"metric": "avg_gross_r", "value": float(gross_r.mean())},
            {"metric": "median_gross_r", "value": float(gross_r.median())},
            {"metric": "tp1_hit_rate", "value": float(closed["tp1_hit"].astype(bool).mean())},
            {"metric": "avg_mfe_pct", "value": float(closed["mfe_pct"].astype(float).mean())},
            {"metric": "avg_mae_pct", "value": float(closed["mae_pct"].astype(float).mean())},
        ]
    )
    by_reason = closed.groupby("exit_reason").size().reset_index(name="count")
    for _, row in by_reason.iterrows():
        rows.append({"metric": f"exit_reason:{row['exit_reason']}", "value": int(row["count"])})
    return pd.DataFrame(rows)


def summarize_trades_by_symbol(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame()
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame()
    grouped = (
        closed.assign(win=closed["net_return"].astype(float) > 0)
        .groupby("symbol")
        .agg(
            closed_trades=("symbol", "size"),
            win_rate=("win", "mean"),
            avg_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            sum_net_return=("net_return", "sum"),
            avg_gross_r=("gross_r", "mean"),
            tp1_hit_rate=("tp1_hit", "mean"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
        )
        .reset_index()
    )
    grouped.sort_values("sum_net_return", ascending=False, inplace=True)
    return grouped


def summarize_trades_by_pump_category(trades: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "pump_category_id",
        "closed_trades",
        "win_rate",
        "avg_net_return",
        "median_net_return",
        "sum_net_return",
        "avg_gross_r",
        "tp1_hit_rate",
        "avg_mfe_pct",
        "avg_mae_pct",
    ]
    if trades.empty or "status" not in trades.columns or "pump_category_id" not in trades.columns:
        return pd.DataFrame(columns=columns)
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=columns)
    closed["pump_category_id"] = closed["pump_category_id"].replace("", PUMP_CATEGORY_DISCOVERY).fillna(PUMP_CATEGORY_DISCOVERY)
    grouped = (
        closed.assign(win=closed["net_return"].astype(float) > 0)
        .groupby("pump_category_id")
        .agg(
            closed_trades=("pump_category_id", "size"),
            win_rate=("win", "mean"),
            avg_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            sum_net_return=("net_return", "sum"),
            avg_gross_r=("gross_r", "mean"),
            tp1_hit_rate=("tp1_hit", "mean"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
        )
        .reset_index()
    )
    grouped.sort_values(["sum_net_return", "closed_trades"], ascending=[False, False], inplace=True)
    return grouped[columns]



def summarize_trades_by_pump_category_family(trades: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "pump_category_family",
        "closed_trades",
        "win_rate",
        "avg_net_return",
        "median_net_return",
        "sum_net_return",
        "avg_gross_r",
        "tp1_hit_rate",
        "avg_mfe_pct",
        "avg_mae_pct",
    ]
    if trades.empty or "status" not in trades.columns or "pump_category_family" not in trades.columns:
        return pd.DataFrame(columns=columns)
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=columns)
    closed["pump_category_family"] = (
        closed["pump_category_family"].replace("", PUMP_CATEGORY_FAMILY_DISCOVERY).fillna(PUMP_CATEGORY_FAMILY_DISCOVERY)
    )
    grouped = (
        closed.assign(win=closed["net_return"].astype(float) > 0)
        .groupby("pump_category_family")
        .agg(
            closed_trades=("pump_category_family", "size"),
            win_rate=("win", "mean"),
            avg_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            sum_net_return=("net_return", "sum"),
            avg_gross_r=("gross_r", "mean"),
            tp1_hit_rate=("tp1_hit", "mean"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
        )
        .reset_index()
    )
    grouped.sort_values(["sum_net_return", "closed_trades"], ascending=[False, False], inplace=True)
    return grouped[columns]

def _parse_grid_values(raw: str, *, cast: type[float] | type[int]) -> list[float] | list[int]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return [cast(item) for item in values]


EXHAUSTION_PROFILES: dict[str, dict[str, float | None]] = {
    "none": {
        "max_start_quote_ratio": None,
        "max_start_trade_ratio": None,
        "max_start_avg_trade_quote_size_ratio": None,
        "max_start_quote_ratio_per_abs_return": None,
        "max_start_range_pct_ratio_to_baseline": None,
        "max_prior_up_down_whipsaw_to_impulse_range": None,
        "min_next_taker_buy_quote_share": None,
        "max_price_retention": None,
    },
    "mild": {
        "max_start_quote_ratio": 120.0,
        "max_start_trade_ratio": 60.0,
        "max_start_avg_trade_quote_size_ratio": 10.0,
        "max_start_quote_ratio_per_abs_return": 30_000.0,
        "max_start_range_pct_ratio_to_baseline": 35.0,
        "max_prior_up_down_whipsaw_to_impulse_range": 0.60,
        "min_next_taker_buy_quote_share": 0.46,
        "max_price_retention": 0.98,
    },
    "balanced": {
        "max_start_quote_ratio": 80.0,
        "max_start_trade_ratio": 40.0,
        "max_start_avg_trade_quote_size_ratio": 7.0,
        "max_start_quote_ratio_per_abs_return": 15_000.0,
        "max_start_range_pct_ratio_to_baseline": 25.0,
        "max_prior_up_down_whipsaw_to_impulse_range": 0.60,
        "min_next_taker_buy_quote_share": 0.48,
        "max_price_retention": 0.96,
    },
    "strict": {
        "max_start_quote_ratio": 50.0,
        "max_start_trade_ratio": 25.0,
        "max_start_avg_trade_quote_size_ratio": 5.0,
        "max_start_quote_ratio_per_abs_return": 8_000.0,
        "max_start_range_pct_ratio_to_baseline": 18.0,
        "max_prior_up_down_whipsaw_to_impulse_range": 0.50,
        "min_next_taker_buy_quote_share": 0.50,
        "max_price_retention": 0.94,
    },
}


def _parse_grid_profile_values(raw: str) -> list[str]:
    profiles = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(profiles).difference(EXHAUSTION_PROFILES))
    if unknown:
        raise ValueError(f"unknown exhaustion profiles: {unknown}")
    return profiles


EXIT_RULES = {"structural_trail", "ema20_close", "ema20_negative_pnl_be_escape"}


def _parse_grid_exit_rules(raw: str) -> list[str]:
    rules = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(rules).difference(EXIT_RULES))
    if unknown:
        raise ValueError(f"unknown exit rules: {unknown}")
    return rules


def _iter_entry_grid_configs(
    base_config: AnomalyBacktestConfig,
    *,
    oi3_values: Iterable[float],
    hold_values: Iterable[int],
    pullback_fractions: Iterable[float],
    exhaustion_profiles: Iterable[str],
    exit_rules: Iterable[str],
) -> list[AnomalyBacktestConfig]:
    variants: list[AnomalyBacktestConfig] = []
    for profile_name in exhaustion_profiles:
        profile = EXHAUSTION_PROFILES[profile_name]
        for oi3 in oi3_values:
            for hold in hold_values:
                for exit_rule in exit_rules:
                    common = {
                        "min_hold_count": int(hold),
                        "min_oi_change_pct_3x5m": float(oi3),
                        "require_oi_status_ok": True,
                        "exhaustion_profile": profile_name,
                        "exit_rule": str(exit_rule),
                        **profile,
                    }
                    variants.append(
                        replace(
                            base_config,
                            entry_method="market",
                            **common,
                        )
                    )
                    variants.append(
                        replace(
                            base_config,
                            entry_method="break_box_high",
                            **common,
                        )
                    )
                    for fraction in pullback_fractions:
                        variants.append(
                            replace(
                                base_config,
                                entry_method="pullback_box_fraction",
                                pullback_box_fraction=float(fraction),
                                **common,
                            )
                        )
    return variants


def _build_entry_grid_signal_sets(
    candidates: pd.DataFrame,
    variants: Iterable[AnomalyBacktestConfig],
) -> list[tuple[AnomalyBacktestConfig, pd.DataFrame]]:
    variant_list = list(variants)
    started_at = time.monotonic()
    next_progress_pct = 0
    signal_sets: list[tuple[AnomalyBacktestConfig, pd.DataFrame]] = []
    for processed_count, variant in enumerate(variant_list, start=1):
        signals = build_anomaly_signals(candidates, config=variant)
        signal_sets.append((variant, signals))
        next_progress_pct = _emit_progress_1pct(
            label="anomaly grid signals",
            done=processed_count,
            total=len(variant_list),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )
    return signal_sets


def _signal_universe_from_signal_sets(
    signal_sets: Iterable[tuple[AnomalyBacktestConfig, pd.DataFrame]],
) -> pd.DataFrame:
    signal_frames: list[pd.DataFrame] = []
    for _, signals in signal_sets:
        if not signals.empty:
            signal_frames.append(signals.loc[:, ["symbol", "decision_timestamp_ms"]].copy())
    if not signal_frames:
        return pd.DataFrame(columns=["symbol", "decision_timestamp_ms"])
    universe = pd.concat(signal_frames, ignore_index=True)
    universe.dropna(subset=["symbol", "decision_timestamp_ms"], inplace=True)
    universe.drop_duplicates(["symbol", "decision_timestamp_ms"], inplace=True)
    universe.sort_values(["symbol", "decision_timestamp_ms"], inplace=True)
    universe.reset_index(drop=True, inplace=True)
    return universe




def _build_pre_context_signal_universe(candidates: pd.DataFrame, config: AnomalyBacktestConfig) -> pd.DataFrame:
    """Build the widest honest universe that needs derivatives context before final category checks."""

    frames: list[pd.DataFrame] = []
    base_pre_context_config = _strip_derivative_context_requirements(config)
    base_signals = build_anomaly_signals(candidates, config=base_pre_context_config)
    if not base_signals.empty:
        base_keys = _signal_key_frame(base_signals)
        if not base_keys.empty:
            base_keys["pre_context_intent"] = PUMP_CATEGORY_DISCOVERY
            frames.append(base_keys)
    for category_id in PUMP_CATEGORY_PROFILE_ORDER:
        category_config = _apply_red_flag_profile(replace(config, red_flag_profile=category_id))
        category_pre_context_config = _strip_derivative_context_requirements(category_config)
        category_signals = build_anomaly_signals(candidates, config=category_pre_context_config)
        if category_signals.empty:
            continue
        keys = _signal_key_frame(category_signals)
        if keys.empty:
            continue
        keys["pre_context_intent"] = str(category_id)
        frames.append(keys)
    if not frames:
        return pd.DataFrame(
            columns=[
                "symbol",
                "setup_timeframe",
                "entry_timeframe",
                "decision_timestamp_ms",
                "pre_context_intent",
            ]
        )
    union = pd.concat(frames, ignore_index=True)
    key_columns = ["symbol", "setup_timeframe", "entry_timeframe", "decision_timestamp_ms"]
    union = (
        union.groupby(key_columns, as_index=False)["pre_context_intent"]
        .agg(lambda values: ",".join(dict.fromkeys(str(value) for value in values if str(value))))
    )
    union.sort_values(key_columns, inplace=True)
    union.reset_index(drop=True, inplace=True)
    return union


def build_context_parity_report(
    candidates: pd.DataFrame,
    *,
    pre_context_universe: pd.DataFrame,
    signals: pd.DataFrame,
    trades: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "symbol",
        "setup_timeframe",
        "entry_timeframe",
        "decision_timestamp_ms",
        "decision_timestamp_utc",
        "in_pre_context_universe",
        "pre_context_intent",
        "is_final_signal",
        "final_pump_category_id",
        "final_pump_category_family",
        "category_parity_class",
        "live_priority_pass",
        "discovery_only",
        "live_priority_reject_reason",
        "strict_live_replay_enter",
        "strict_live_replay_enter_reason",
        "trade_status",
        "trade_skip_reason",
        "trade_execution_guard",
        "context_fetch_status",
        "mark_status",
        "mark_timestamp_ms",
        "mark_timestamp_utc",
        "mark_asof_timestamp_ms",
        "mark_asof_timestamp_utc",
        "mark_age_ms",
        "mark_close_vs_decision_close_basis",
        "oi_status",
        "oi_cache_status",
        "oi_fetch_or_load_status",
        "oi_cache_min_timestamp_ms",
        "oi_cache_min_timestamp_utc",
        "oi_cache_max_timestamp_ms",
        "oi_cache_max_timestamp_utc",
        "oi_timestamp_ms",
        "oi_timestamp_utc",
        "oi_asof_timestamp_ms",
        "oi_asof_timestamp_utc",
        "oi_age_ms",
        "oi_change_pct_3x5m",
        "context_parity_status",
    ]
    key_columns = ["symbol", "setup_timeframe", "entry_timeframe", "decision_timestamp_ms"]
    if candidates.empty or not set(key_columns).issubset(candidates.columns):
        return pd.DataFrame(columns=columns)

    report = candidates.copy()
    report["symbol"] = report["symbol"].astype(str)
    report["setup_timeframe"] = report["setup_timeframe"].astype(str)
    report["entry_timeframe"] = report["entry_timeframe"].astype(str)
    report["decision_timestamp_ms"] = pd.to_numeric(report["decision_timestamp_ms"], errors="coerce").astype("Int64")
    report.dropna(subset=["symbol", "decision_timestamp_ms"], inplace=True)
    report["decision_timestamp_ms"] = report["decision_timestamp_ms"].astype("int64")

    if not pre_context_universe.empty:
        context_keys = pre_context_universe.loc[:, key_columns + ["pre_context_intent"]].drop_duplicates(key_columns).copy()
        context_keys["in_pre_context_universe"] = True
        report = report.merge(context_keys, on=key_columns, how="left")
    else:
        report["pre_context_intent"] = ""
        report["in_pre_context_universe"] = False
    report["in_pre_context_universe"] = _truthy_mask(report["in_pre_context_universe"])
    report["pre_context_intent"] = report["pre_context_intent"].fillna("")

    if not signals.empty and set(key_columns).issubset(signals.columns):
        signal_columns = [
            *key_columns,
            "pump_category_id",
            "pump_category_family",
        ]
        available_signal_columns = [column for column in signal_columns if column in signals.columns]
        signal_keys = signals.loc[:, available_signal_columns].drop_duplicates(key_columns).copy()
        signal_keys.rename(
            columns={
                "pump_category_id": "final_pump_category_id",
                "pump_category_family": "final_pump_category_family",
            },
            inplace=True,
        )
        signal_keys["is_final_signal"] = True
        report = report.merge(signal_keys, on=key_columns, how="left")
    else:
        report["is_final_signal"] = False
        report["final_pump_category_id"] = ""
        report["final_pump_category_family"] = ""
    report["is_final_signal"] = _truthy_mask(report["is_final_signal"])
    for column in ("final_pump_category_id", "final_pump_category_family"):
        if column not in report.columns:
            report[column] = ""
        report[column] = report[column].fillna("")

    if not trades.empty and set(key_columns).issubset(trades.columns):
        trade_columns = [*key_columns, "status", "skip_reason"]
        available_trade_columns = [column for column in trade_columns if column in trades.columns]
        trade_keys = trades.loc[:, available_trade_columns].copy()
        trade_keys.sort_values(key_columns, inplace=True)
        trade_keys.drop_duplicates(key_columns, keep="last", inplace=True)
        trade_keys.rename(columns={"status": "trade_status", "skip_reason": "trade_skip_reason"}, inplace=True)
        report = report.merge(trade_keys, on=key_columns, how="left")
    else:
        report["trade_status"] = ""
        report["trade_skip_reason"] = ""
    for column in ("trade_status", "trade_skip_reason"):
        if column not in report.columns:
            report[column] = ""
        report[column] = report[column].fillna("")
    report["trade_execution_guard"] = report["trade_skip_reason"].astype(str).isin(EXECUTION_GUARD_SKIP_REASONS)

    mark_status = report.get("mark_status", pd.Series("", index=report.index)).astype(str)
    oi_status = report.get("oi_status", pd.Series("", index=report.index)).astype(str)
    oi_cache_status = report.get("oi_cache_status", pd.Series("", index=report.index)).astype(str)
    oi_fetch_or_load_status = report.get("oi_fetch_or_load_status", pd.Series("", index=report.index)).astype(str)
    report["context_fetch_status"] = np.where(
        report["in_pre_context_universe"],
        "requested",
        "not_requested_pre_context_filtered",
    )
    report["context_parity_status"] = "ok"
    report.loc[~report["in_pre_context_universe"], "context_parity_status"] = "not_in_pre_context_universe"
    report.loc[
        report["in_pre_context_universe"]
        & (
            mark_status.map(_market_context_status_is_bad)
            | oi_status.map(_market_context_status_is_bad)
            | oi_cache_status.map(_market_context_status_is_bad)
            | oi_fetch_or_load_status.map(_market_context_status_is_bad)
        ),
        "context_parity_status",
    ] = "requested_context_missing_or_bad"
    report.loc[
        report["in_pre_context_universe"] & oi_cache_status.ne("") & oi_cache_status.ne("ok"),
        "context_parity_status",
    ] = "oi_cache_unavailable"
    report.loc[
        report["in_pre_context_universe"] & oi_status.eq("stale_asof"),
        "context_parity_status",
    ] = "oi_context_stale_asof"
    report.loc[
        report["is_final_signal"] & ~report["in_pre_context_universe"],
        "context_parity_status",
    ] = "final_signal_missing_pre_context_request"
    report.loc[
        report["trade_execution_guard"] & report["final_pump_category_family"].eq(""),
        "context_parity_status",
    ] = "execution_guard_missing_category_metadata"

    report["live_priority_pass"] = report["final_pump_category_family"].eq(PUMP_CATEGORY_FAMILY_LIVE)
    report["discovery_only"] = (
        report["is_final_signal"]
        & report["final_pump_category_id"].replace("", PUMP_CATEGORY_DISCOVERY).eq(PUMP_CATEGORY_DISCOVERY)
        & ~report["live_priority_pass"]
    )
    report["category_parity_class"] = "other_final_signal"
    report.loc[~report["in_pre_context_universe"], "category_parity_class"] = "not_in_pre_context_universe"
    report.loc[
        report["in_pre_context_universe"] & ~report["is_final_signal"],
        "category_parity_class",
    ] = "pre_context_only_rejected"
    report.loc[report["discovery_only"], "category_parity_class"] = "discovery_only"
    report.loc[report["live_priority_pass"], "category_parity_class"] = "live_priority_pass"

    report["live_priority_reject_reason"] = ""
    report.loc[report["discovery_only"], "live_priority_reject_reason"] = (
        "discovery_only_no_live_priority_category_match"
    )
    report.loc[
        ~report["in_pre_context_universe"],
        "live_priority_reject_reason",
    ] = "not_in_pre_context_universe"
    context_not_ok = report["context_parity_status"].astype(str).ne("ok")
    report.loc[
        report["in_pre_context_universe"] & context_not_ok & ~report["live_priority_pass"],
        "live_priority_reject_reason",
    ] = report.loc[
        report["in_pre_context_universe"] & context_not_ok & ~report["live_priority_pass"],
        "context_parity_status",
    ].astype(str)
    report.loc[
        report["in_pre_context_universe"]
        & ~context_not_ok
        & ~report["is_final_signal"]
        & ~report["live_priority_pass"],
        "live_priority_reject_reason",
    ] = "post_context_or_final_filter_reject"
    report.loc[
        report["in_pre_context_universe"]
        & ~context_not_ok
        & report["is_final_signal"]
        & ~report["live_priority_pass"]
        & report["live_priority_reject_reason"].eq(""),
        "live_priority_reject_reason",
    ] = "final_signal_not_live_priority_category"
    report.loc[report["live_priority_pass"], "live_priority_reject_reason"] = ""

    entered_trade_status = report["trade_status"].astype(str).isin({"open", "closed", "tp1_hit", "stopped", "breakeven"})
    report["strict_live_replay_enter"] = (
        report["live_priority_pass"]
        & entered_trade_status
        & report["trade_skip_reason"].astype(str).eq("")
        & ~report["trade_execution_guard"]
    )
    report["strict_live_replay_enter_reason"] = ""
    report.loc[report["strict_live_replay_enter"], "strict_live_replay_enter_reason"] = "entered"
    report.loc[
        report["live_priority_pass"] & ~report["strict_live_replay_enter"] & report["trade_skip_reason"].astype(str).ne(""),
        "strict_live_replay_enter_reason",
    ] = report.loc[
        report["live_priority_pass"] & ~report["strict_live_replay_enter"] & report["trade_skip_reason"].astype(str).ne(""),
        "trade_skip_reason",
    ].astype(str)
    report.loc[
        report["live_priority_pass"]
        & ~report["strict_live_replay_enter"]
        & report["trade_skip_reason"].astype(str).eq("")
        & report["strict_live_replay_enter_reason"].eq(""),
        "strict_live_replay_enter_reason",
    ] = "live_priority_signal_without_entered_trade"
    report.loc[
        ~report["live_priority_pass"],
        "strict_live_replay_enter_reason",
    ] = report.loc[~report["live_priority_pass"], "live_priority_reject_reason"].astype(str)

    for column in columns:
        if column not in report.columns:
            report[column] = ""
    result = report.loc[:, columns].copy()
    result.drop_duplicates(key_columns, inplace=True)
    result.sort_values(key_columns, inplace=True)
    result.reset_index(drop=True, inplace=True)
    return result


def _honesty_status(*, failures: int, warning: bool = False) -> tuple[str, str]:
    if failures > 0:
        return "fail", "error"
    if warning:
        return "warning", "warning"
    return "ok", "info"


def _honesty_row(
    *,
    node_id: int,
    node: str,
    status: str,
    severity: str,
    rows_checked: int,
    failures: int,
    detail: str,
) -> dict[str, object]:
    return {
        "node_id": int(node_id),
        "node": str(node),
        "status": str(status),
        "severity": str(severity),
        "rows_checked": int(rows_checked),
        "failures": int(failures),
        "detail": str(detail),
    }


def _context_asof_failures(frame: pd.DataFrame, *, prefixes: Iterable[str]) -> int:
    if frame.empty or "decision_available_timestamp_ms" not in frame.columns:
        return 0
    decision_available = pd.to_numeric(frame["decision_available_timestamp_ms"], errors="coerce")
    failures = 0
    for prefix in prefixes:
        asof_column = f"{prefix}_asof_timestamp_ms"
        if asof_column not in frame.columns:
            continue
        asof = pd.to_numeric(frame[asof_column], errors="coerce")
        failures += int((asof.notna() & decision_available.notna() & asof.gt(decision_available)).sum())
    return failures


def build_backtest_honesty_report(
    *,
    config: AnomalyBacktestConfig,
    candidates: pd.DataFrame,
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    symbols: Iterable[str] | None,
    context_parity_report: pd.DataFrame,
    run_entry_grid: bool,
    run_latency_grid: bool,
) -> pd.DataFrame:
    """Summarize the non-negotiable anti-lookahead and live-parity checks."""

    rows: list[dict[str, object]] = []
    requested_symbols = _normalized_symbol_tuple(symbols)
    universe_warning = not bool(requested_symbols)
    status, severity = _honesty_status(failures=0, warning=universe_warning)
    rows.append(
        _honesty_row(
            node_id=1,
            node="CLI/config",
            status="ok",
            severity="info",
            rows_checked=1,
            failures=0,
            detail=(
                f"execution_model={_execution_model_label(config)}; "
                f"max_open_positions={int(config.max_open_positions)}; "
                f"fee_rate={float(config.fee_rate):g}; "
                f"entry_slippage_pct={float(config.entry_slippage_pct):g}; "
                f"exit_slippage_pct={float(config.exit_slippage_pct):g}; "
                f"latency_grid={'on' if run_latency_grid else 'off'}"
            ),
        )
    )
    rows.append(
        _honesty_row(
            node_id=2,
            node="Universe / symbols",
            status=status,
            severity=severity,
            rows_checked=max(1, len(requested_symbols)),
            failures=0,
            detail=(
                "explicit symbol universe"
                if requested_symbols
                else "cache snapshot universe; historical survivorship/listing bias is still a research limitation"
            ),
        )
    )

    if candidates.empty:
        candidate_availability_failures = 0
        flow_source_failures = 0
    else:
        availability_mask = _candidate_availability_mask(candidates)
        candidate_availability_failures = int((~availability_mask).sum())
        flow_source_mask = _candidate_flow_source_mask(candidates)
        flow_source_failures = int((~flow_source_mask).sum())
    status, severity = _honesty_status(failures=candidate_availability_failures)
    rows.append(
        _honesty_row(
            node_id=3,
            node="OHLCV cache loader availability",
            status=status,
            severity=severity,
            rows_checked=int(len(candidates)),
            failures=candidate_availability_failures,
            detail="candidate rows must carry candle-open timestamp and explicit candle-close availability timestamps",
        )
    )
    status, severity = _honesty_status(failures=flow_source_failures)
    rows.append(
        _honesty_row(
            node_id=4,
            node="OHLCV flow provenance",
            status=status,
            severity=severity,
            rows_checked=int(len(candidates)),
            failures=flow_source_failures,
            detail="quote_volume and number_of_trades source labels must not be missing/unknown/proxy",
        )
    )
    subminute_signals = 0
    bad_subminute_sources = 0
    if not signals.empty and "entry_timeframe" in signals.columns and "entry_trade_count_source" in signals.columns:
        entry_ms = signals["entry_timeframe"].map(lambda value: _timeframe_to_milliseconds(str(value)))
        subminute = entry_ms.lt(60_000)
        subminute_signals = int(subminute.sum())
        bad_subminute_sources = int(
            (
                subminute
                & ~(
                    signals["entry_trade_count_source"].astype(str).str.contains("cached_1s_aggregated_to_", regex=False)
                    | signals["entry_trade_count_source"].astype(str).str.contains("cached_aggTrades_direct_to_", regex=False)
                )
            ).sum()
        )
    status, severity = _honesty_status(failures=bad_subminute_sources)
    rows.append(
        _honesty_row(
            node_id=5,
            node="AggTrade/event cache",
            status=status,
            severity=severity,
            rows_checked=subminute_signals,
            failures=bad_subminute_sources,
            detail="sub-minute entry signals must be built from full-bucket 1s aggTrade materialization",
        )
    )

    closed_rows = 0
    pair_rows = 0
    post_htf_rows = 0
    post_htf_forward_rows = 0
    short_fader_rows = 0
    if not candidates.empty and "feature_contract" in candidates.columns:
        contracts = candidates["feature_contract"].astype(str)
        closed_rows = int(contracts.eq("closed_setup_tf_v1").sum())
        pair_rows = int(contracts.eq("htf_setup_ltf_entry_v1").sum())
        post_htf_rows = int(contracts.eq(POST_HTF_CLOSE_LTF_CONFIRMATION_CONTRACT).sum())
        post_htf_forward_rows = int(contracts.eq(POST_HTF_CLOSE_LTF_FORWARD_CONFIRMATION_CONTRACT).sum())
        short_fader_rows = int(contracts.eq(BARE_HTF_SHORT_FADER_CONTRACT).sum())
    rows.append(
        _honesty_row(
            node_id=6,
            node="Closed setup candidate collector",
            status="ok",
            severity="info",
            rows_checked=closed_rows,
            failures=0,
            detail="closed setup rows use closed-candle availability; confirmation is decision-time gated, not pre-signal",
        )
    )
    rows.append(
        _honesty_row(
            node_id=7,
            node="Pair/forming setup collector",
            status="ok",
            severity="info",
            rows_checked=pair_rows,
            failures=0,
            detail="forming pair rows use entry candles available by decision_ts; setup_full_available_timestamp_ms is audit-only",
        )
    )
    rows.append(
        _honesty_row(
            node_id=8,
            node="Post-HTF-close LTF confirmation collector",
            status="ok",
            severity="info",
            rows_checked=post_htf_rows + post_htf_forward_rows,
            failures=0,
            detail="post-HTF-close rows may inspect left/closed HTF context after HTF close; forward mode waits for LTF confirmation after that close",
        )
    )
    rows.append(
        _honesty_row(
            node_id=30,
            node="Bare HTF short/fader collector",
            status="ok",
            severity="info",
            rows_checked=short_fader_rows,
            failures=0,
            detail="short/fader rows start from closed HTF anomalies and require executable LTF triggers after HTF close; long categories are not entry classes",
        )
    )
    rows.append(
        _honesty_row(
            node_id=9,
            node="Baseline calculations",
            status="ok" if candidate_availability_failures == 0 else "fail",
            severity="info" if candidate_availability_failures == 0 else "error",
            rows_checked=int(len(candidates)),
            failures=candidate_availability_failures,
            detail="rolling baselines are shifted to prior candles and availability checks reject impossible setup/decision timestamps",
        )
    )
    rows.append(
        _honesty_row(
            node_id=10,
            node="Flow ratios",
            status="ok" if flow_source_failures == 0 else "fail",
            severity="info" if flow_source_failures == 0 else "error",
            rows_checked=int(len(candidates)),
            failures=flow_source_failures,
            detail="flow ratios are accepted only when quote_volume and number_of_trades provenance is trusted",
        )
    )

    future_columns = [
        column
        for column in signals.columns
        if str(column).startswith("future_") or str(column) in {"outcome_label", "future_label_status"}
    ] if not signals.empty else []
    rows.append(
        _honesty_row(
            node_id=11,
            node="Future labels",
            status="ok",
            severity="info",
            rows_checked=int(len(signals)),
            failures=0,
            detail=(
                "future/outcome columns may exist in artifacts only; build_anomaly_signals has a static test forbidding future filters; "
                f"artifact_future_columns={','.join(future_columns[:12])}"
            ),
        )
    )

    prior_context_failures = 0
    if not candidates.empty and "prior_context_status" in candidates.columns:
        prior_status = candidates["prior_context_status"].astype(str)
        prior_context_failures = int(prior_status.isin({"future_leak", "invalid_anchor"}).sum())
    status, severity = _honesty_status(failures=prior_context_failures)
    rows.append(
        _honesty_row(
            node_id=12,
            node="Prior spike context",
            status=status,
            severity=severity,
            rows_checked=int(len(candidates)),
            failures=prior_context_failures,
            detail="recent spike context is anchored to decision availability and uses closed 5m candles before the anchor",
        )
    )

    oi_asof_failures = _context_asof_failures(candidates, prefixes=("oi",))
    status, severity = _honesty_status(failures=oi_asof_failures)
    rows.append(
        _honesty_row(
            node_id=13,
            node="Open interest context as-of",
            status=status,
            severity=severity,
            rows_checked=int(len(candidates)),
            failures=oi_asof_failures,
            detail="OI rows must be selected by available/asof timestamp <= decision availability",
        )
    )
    derivative_prefixes = [str(spec["prefix"]) for spec in DERIVATIVES_CONTEXT_SPECS]
    derivatives_asof_failures = _context_asof_failures(candidates, prefixes=derivative_prefixes)
    status, severity = _honesty_status(failures=derivatives_asof_failures)
    rows.append(
        _honesty_row(
            node_id=14,
            node="Derivatives context as-of",
            status=status,
            severity=severity,
            rows_checked=int(len(candidates)),
            failures=derivatives_asof_failures,
            detail="funding/premium/mark/long-short/taker context must be availability-lagged before decision use",
        )
    )
    stale_context_failures = 0
    if not context_parity_report.empty and "context_parity_status" in context_parity_report.columns:
        stale_context_failures = int(
            context_parity_report["context_parity_status"]
            .astype(str)
            .isin({"final_signal_missing_pre_context_request", "execution_guard_missing_category_metadata"})
            .sum()
        )
    status, severity = _honesty_status(failures=stale_context_failures)
    rows.append(
        _honesty_row(
            node_id=15,
            node="Category contract",
            status=status,
            severity=severity,
            rows_checked=int(len(context_parity_report)),
            failures=stale_context_failures,
            detail="category filtering is pre-context widened, then rebuilt after context; final trades must carry category metadata",
        )
    )
    rows.append(
        _honesty_row(
            node_id=16,
            node="Signal builder",
            status="ok",
            severity="info",
            rows_checked=int(len(signals)),
            failures=0,
            detail="static test forbids future_/future_label_status/outcome_label references inside build_anomaly_signals",
        )
    )
    rows.append(
        _honesty_row(
            node_id=17,
            node="Signal entry price",
            status="ok",
            severity="info",
            rows_checked=int(len(signals)),
            failures=0,
            detail="decision close is retained as signal/audit price; simulated fills use execution entry_price after latency/slippage",
        )
    )
    market_signals = int(len(signals)) if config.entry_method == "market" else 0
    rows.append(
        _honesty_row(
            node_id=18,
            node="Market entry simulation",
            status="ok",
            severity="info",
            rows_checked=market_signals,
            failures=0,
            detail="market mode enters at next entry candle open after decision, then applies adverse entry slippage",
        )
    )
    guard_skips = 0
    if not trades.empty and "skip_reason" in trades.columns:
        guard_skips = int(trades["skip_reason"].astype(str).isin(EXECUTION_GUARD_SKIP_REASONS).sum())
    rows.append(
        _honesty_row(
            node_id=19,
            node="Entry guards",
            status="ok",
            severity="info",
            rows_checked=int(len(trades)),
            failures=0,
            detail=f"drift, TP-before-entry, positive-risk and RR-collapsed skips are artifacted; execution_guard_skips={guard_skips}",
        )
    )
    rows.append(
        _honesty_row(
            node_id=20,
            node="Stop/TP basis",
            status="ok",
            severity="info",
            rows_checked=int(len(trades)),
            failures=0,
            detail="risk, TP and PnL are based on simulated fill entry_price, not decision signal price",
        )
    )

    entry_candle_failures = 0
    closed = trades.loc[trades["status"].eq("closed")].copy() if "status" in trades.columns else pd.DataFrame()
    if not closed.empty and "post_entry_simulation_includes_entry_candle" in closed.columns:
        entry_candle_failures = int((~closed["post_entry_simulation_includes_entry_candle"].astype(bool)).sum())
    status, severity = _honesty_status(failures=entry_candle_failures)
    rows.append(
        _honesty_row(
            node_id=21,
            node="Exit simulation",
            status=status,
            severity=severity,
            rows_checked=int(len(closed)),
            failures=entry_candle_failures,
            detail="post-entry OHLCV path must include the entry candle and use stop-first intrabar ordering",
        )
    )
    rows.append(
        _honesty_row(
            node_id=22,
            node="Intrabar ordering",
            status="ok",
            severity="info",
            rows_checked=int(len(closed)),
            failures=0,
            detail="OHLCV ambiguity is handled conservatively with stop-first conflicts and TP trade-through requirement",
        )
    )
    rows.append(
        _honesty_row(
            node_id=23,
            node="Position overlap",
            status="ok",
            severity="info",
            rows_checked=int(len(trades)),
            failures=0,
            detail="same-symbol overlap is checked against actual simulated entry timestamp, not decision timestamp",
        )
    )
    portfolio_warning = int(config.max_open_positions) != int(DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS)
    status, severity = _honesty_status(failures=0, warning=bool(portfolio_warning))
    rows.append(
        _honesty_row(
            node_id=24,
            node="Portfolio/max positions",
            status=status,
            severity=severity,
            rows_checked=int(len(trades)),
            failures=0,
            detail=(
                f"raw simulation max_open_positions={int(config.max_open_positions)} is enforced at actual simulated entry timestamp; "
                f"default_raw_discovery_max_open_positions={int(DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS)}; "
                f"final live-like portfolio filter max_open_positions={int(DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS)}"
            ),
        )
    )
    slippage_warning = float(config.entry_slippage_pct) <= 0.0 or float(config.exit_slippage_pct) <= 0.0
    status, severity = _honesty_status(failures=0, warning=slippage_warning)
    rows.append(
        _honesty_row(
            node_id=25,
            node="Fees/slippage",
            status=status,
            severity=severity,
            rows_checked=int(len(trades)),
            failures=0,
            detail=(
                f"fee_rate={float(config.fee_rate):g}; "
                f"entry_slippage_pct={float(config.entry_slippage_pct):g}; "
                f"exit_slippage_pct={float(config.exit_slippage_pct):g}; "
                "slippage is applied adversely to fills for the active side"
            ),
        )
    )
    rows.append(
        _honesty_row(
            node_id=26,
            node="Entry/latency grid selection bias",
            status="warning" if run_entry_grid else "ok",
            severity="warning" if run_entry_grid else "info",
            rows_checked=1,
            failures=0,
            detail=(
                "entry grid is in-sample research only; do not treat best-grid artifacts as edge proof"
                if run_entry_grid
                else "no in-sample entry grid selection requested"
            ),
        )
    )
    rows.append(
        _honesty_row(
            node_id=27,
            node="Precollected candidates",
            status="ok",
            severity="info",
            rows_checked=int(len(candidates)),
            failures=0,
            detail="reuse/precollect path validates exact config and re-applies as-of filtering before signal/trade simulation",
        )
    )
    targeted_context_rows = 0
    if not context_parity_report.empty and "context_fetch_status" in context_parity_report.columns:
        targeted_context_rows = int(context_parity_report["context_fetch_status"].astype(str).eq("requested").sum())
    rows.append(
        _honesty_row(
            node_id=28,
            node="Targeted event backfill",
            status="ok",
            severity="info",
            rows_checked=targeted_context_rows,
            failures=0,
            detail="targeted derivative/event windows are allowed only as as-of context and are audited by context parity/status artifacts",
        )
    )
    rows.append(
        _honesty_row(
            node_id=29,
            node="Artifacts / summaries",
            status="ok",
            severity="info",
            rows_checked=int(len(trades)),
            failures=0,
            detail="future outcome fields remain analysis artifacts; trade summaries use simulated trade status/returns only",
        )
    )
    return pd.DataFrame(rows)


def _fetch_derivatives_context_for_signal_universe(
    signal_universe: pd.DataFrame,
    *,
    derivatives_context_fetcher: object,
) -> pd.DataFrame:
    if signal_universe.empty:
        print("anomaly derivatives context: no post-filter signal universe to fetch", flush=True)
        return pd.DataFrame(
            columns=[
                "symbol",
                "signal_rows",
                "start_timestamp_ms",
                "end_timestamp_ms",
                "success",
                "added_rows",
                "message",
            ]
        )
    fetch_symbol = getattr(derivatives_context_fetcher, "fetch_symbol")
    grouped = signal_universe.groupby("symbol", sort=True)
    started_at = time.monotonic()
    total = int(len(grouped))
    next_progress_pct = 0
    status_rows: list[dict[str, object]] = []
    print(
        "anomaly derivatives context: fetching event windows for "
        f"{len(signal_universe)} post-filter signals across {total} instruments",
        flush=True,
    )
    logger = getattr(derivatives_context_fetcher, "_logger", None)
    previous_disabled = getattr(logger, "disabled", None)
    if logger is not None:
        logger.disabled = True
    try:
        for processed_count, (symbol, group) in enumerate(grouped, start=1):
            timestamps = group["decision_timestamp_ms"].astype(float)
            start_ts = int(timestamps.min()) - 6 * 5 * 60 * 1000
            end_ts = int(timestamps.max()) + 5 * 60 * 1000
            try:
                added_rows = int(fetch_symbol(str(symbol), start_ts, end_ts))
                success = True
                message = "ok"
            except Exception as exc:
                added_rows = 0
                success = False
                message = f"{type(exc).__name__}: {exc}"
            status_rows.append(
                {
                    "symbol": str(symbol),
                    "signal_rows": int(len(group)),
                    "start_timestamp_ms": start_ts,
                    "end_timestamp_ms": end_ts,
                    "success": success,
                    "added_rows": added_rows,
                    "message": message,
                }
            )
            next_progress_pct = _emit_progress_1pct(
                label="anomaly derivatives context",
                done=processed_count,
                total=total,
                started_at=started_at,
                next_progress_pct=next_progress_pct,
            )
    finally:
        if logger is not None and previous_disabled is not None:
            logger.disabled = previous_disabled
    return pd.DataFrame(status_rows)


def _context_default_columns(status: str) -> dict[str, object]:
    defaults: dict[str, object] = {}
    for spec in DERIVATIVES_CONTEXT_SPECS:
        values = _empty_context_columns(spec)
        values[f"{spec['prefix']}_status"] = status
        defaults.update(values)
    defaults["mark_close_vs_decision_close_basis"] = np.nan
    defaults["taker_ls_buy_share"] = np.nan
    return defaults


def _attach_default_context_columns(frame: pd.DataFrame, defaults: dict[str, object]) -> pd.DataFrame:
    default_frame = pd.DataFrame(
        {column: [value] * len(frame) for column, value in defaults.items()},
        index=frame.index,
    )
    return pd.concat([frame.drop(columns=list(defaults), errors="ignore"), default_frame], axis=1)


def _enrich_derivatives_context_for_signal_universe(
    candidates: pd.DataFrame,
    signal_universe: pd.DataFrame,
    *,
    cache_dir: Path,
) -> pd.DataFrame:
    if candidates.empty or "symbol" not in candidates.columns:
        return candidates

    result = candidates.copy()
    defaults = _context_default_columns("outside_pre_context_signal_universe")
    if signal_universe.empty:
        defaults = _context_default_columns("no_context_universe")
        return _attach_default_context_columns(result, defaults)

    key_columns = ["symbol", "decision_timestamp_ms"]
    if not set(key_columns).issubset(result.columns):
        return _attach_default_context_columns(result, defaults)

    universe_keys = signal_universe.loc[:, key_columns].dropna().drop_duplicates()
    selected_index = (
        result.reset_index()
        .merge(universe_keys, on=key_columns, how="inner")["index"]
        .astype(int)
        .to_numpy()
    )
    result = _attach_default_context_columns(result, defaults)
    if len(selected_index) == 0:
        return result

    selected = result.loc[selected_index].drop(columns=list(defaults), errors="ignore").copy()
    print(f"anomaly derivatives context: enriching {len(selected)} post-filter candidate rows", flush=True)
    enriched = enrich_candidates_with_derivatives_context(
        selected,
        cache_dir=cache_dir,
        progress_label="anomaly derivatives enrich",
    )
    context_columns = [column for column in enriched.columns if column in defaults]
    for column in context_columns:
        result.loc[enriched.index, column] = enriched[column]
    return result


def summarize_entry_grid_variant(
    trades: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    signal_count: int,
    variant_id: int | None = None,
) -> dict[str, object]:
    closed = trades.loc[trades.get("status", pd.Series(dtype=str)).eq("closed")].copy()
    skip_summary = summarize_trade_skip_reasons(trades)
    top_skip_reason = str(skip_summary.iloc[0]["skip_reason"]) if not skip_summary.empty else ""
    execution_guard_skips = int(skip_summary.loc[skip_summary["execution_guard"].astype(bool), "count"].sum()) if not skip_summary.empty else 0
    row: dict[str, object] = {
        "variant_id": int(variant_id) if variant_id is not None else -1,
        "entry_method": config.entry_method,
        "exit_rule": config.exit_rule,
        "pullback_box_fraction": (
            config.pullback_box_fraction if config.entry_method == "pullback_box_fraction" else np.nan
        ),
        "min_hold_count": config.min_hold_count,
        "min_oi_change_pct_3x5m": config.min_oi_change_pct_3x5m,
        "require_oi_status_ok": config.require_oi_status_ok,
        "exhaustion_profile": config.exhaustion_profile,
        "max_start_quote_ratio": config.max_start_quote_ratio,
        "max_start_trade_ratio": config.max_start_trade_ratio,
        "max_start_avg_trade_quote_size_ratio": config.max_start_avg_trade_quote_size_ratio,
        "max_start_quote_ratio_per_abs_return": config.max_start_quote_ratio_per_abs_return,
        "max_start_range_pct_ratio_to_baseline": config.max_start_range_pct_ratio_to_baseline,
        "prior_context_lookback_hours": int(_PRIOR_CONTEXT_LIVE_LOOKBACK_HOURS),
        "min_flow_hold_count": config.min_flow_hold_count,
        "max_prior_spike_count_72h": config.max_prior_spike_count_72h,
        "max_prior_fast_fade_count_72h": config.max_prior_fast_fade_count_72h,
        "min_start_lower_wick_to_range": config.min_start_lower_wick_to_range,
        "max_start_upper_wick_to_range": config.max_start_upper_wick_to_range,
        "min_next_taker_buy_quote_share": config.min_next_taker_buy_quote_share,
        "max_price_retention": config.max_price_retention,
        "signals": int(signal_count),
        "closed_trades": int(len(closed)),
        "skipped_trades": int(len(trades) - len(closed)),
        "execution_guard_skips": execution_guard_skips,
        "top_skip_reason": top_skip_reason,
    }
    if closed.empty:
        return row
    net = closed["net_return"].astype(float)
    row.update(
        {
            "symbols": int(closed["symbol"].nunique()),
            "days": int(closed["entry_timestamp_utc"].astype(str).str[:10].nunique()),
            "win_rate": float((net > 0).mean()),
            "avg_net_return": float(net.mean()),
            "median_net_return": float(net.median()),
            "sum_net_return": float(net.sum()),
            "tp1_hit_rate": float(closed["tp1_hit"].astype(bool).mean()),
            "avg_mfe_pct": float(closed["mfe_pct"].astype(float).mean()),
            "avg_mae_pct": float(closed["mae_pct"].astype(float).mean()),
        }
    )
    return row


def build_edge_health_table(trades: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(
            [
                {
                    "label": label,
                    "aspect": "closed_trades",
                    "value": 0.0,
                    "score_0_100": 0.0,
                    "status": "fail",
                    "note": "No closed trades.",
                }
            ]
        )
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return build_edge_health_table(pd.DataFrame(), label=label)

    net = closed["net_return"].astype(float)
    total = float(net.sum())
    top = net.sort_values(ascending=False)
    daily = closed.assign(day=closed["entry_timestamp_utc"].astype(str).str[:10]).groupby("day")["net_return"].sum()
    positive_days = int((daily > 0).sum())
    active_days = int(len(daily))
    top5_share = float(top.head(5).sum() / total) if total > 0 else float("inf")

    def score_linear(value: float, bad: float, good: float, *, higher_is_better: bool = True) -> float:
        if not np.isfinite(value):
            return 0.0
        if not higher_is_better:
            value = -value
            bad = -bad
            good = -good
        if good == bad:
            return 100.0 if value >= good else 0.0
        return float(np.clip((value - bad) / (good - bad), 0.0, 1.0) * 100.0)

    rows = [
        {
            "label": label,
            "aspect": "closed_trades",
            "value": int(len(closed)),
            "score_0_100": score_linear(float(len(closed)), 0.0, 50.0),
            "status": "ok" if len(closed) >= 30 else "watch" if len(closed) >= 15 else "fail",
            "note": "Monthly frequency target: 15 watch, 30 ok, 50 strong.",
        },
        {
            "label": label,
            "aspect": "win_rate",
            "value": float((net > 0).mean()),
            "score_0_100": score_linear(float((net > 0).mean()), 0.40, 0.70),
            "status": "ok" if (net > 0).mean() >= 0.55 else "watch" if (net > 0).mean() >= 0.40 else "fail",
            "note": "40% is minimum viable, 55% ok, 70% excellent for this style.",
        },
        {
            "label": label,
            "aspect": "avg_net_return",
            "value": float(net.mean()),
            "score_0_100": score_linear(float(net.mean()), 0.0, 0.02),
            "status": "ok" if net.mean() >= 0.01 else "watch" if net.mean() > 0 else "fail",
            "note": "Target average trade is above +1%; +2% is strong.",
        },
        {
            "label": label,
            "aspect": "median_net_return",
            "value": float(net.median()),
            "score_0_100": score_linear(float(net.median()), 0.0, 0.015),
            "status": "ok" if net.median() >= 0.005 else "watch" if net.median() > 0 else "fail",
            "note": "Positive median reduces top-tail dependency risk.",
        },
        {
            "label": label,
            "aspect": "active_days",
            "value": active_days,
            "score_0_100": score_linear(float(active_days), 0.0, 20.0),
            "status": "ok" if active_days >= 15 else "watch" if active_days >= 8 else "fail",
            "note": "More active days means less single-day dependence.",
        },
        {
            "label": label,
            "aspect": "positive_day_share",
            "value": float(positive_days / active_days) if active_days else 0.0,
            "score_0_100": score_linear(float(positive_days / active_days) if active_days else 0.0, 0.45, 0.70),
            "status": "ok" if active_days and positive_days / active_days >= 0.60 else "watch" if active_days and positive_days / active_days >= 0.45 else "fail",
            "note": "Day-level consistency check.",
        },
        {
            "label": label,
            "aspect": "symbol_count",
            "value": int(closed["symbol"].nunique()),
            "score_0_100": score_linear(float(closed["symbol"].nunique()), 1.0, 30.0),
            "status": "ok" if closed["symbol"].nunique() >= 20 else "watch" if closed["symbol"].nunique() >= 10 else "fail",
            "note": "Avoid dependence on one instrument.",
        },
        {
            "label": label,
            "aspect": "top5_dependency",
            "value": top5_share,
            "score_0_100": score_linear(top5_share, 1.0, 0.35, higher_is_better=False),
            "status": "ok" if top5_share <= 0.50 else "watch" if top5_share <= 0.85 else "fail",
            "note": "Lower is better. Above 0.85 means result is dominated by top winners.",
        },
        {
            "label": label,
            "aspect": "worst_day_return",
            "value": float(daily.min()) if active_days else np.nan,
            "score_0_100": score_linear(float(daily.min()) if active_days else float("nan"), -0.20, -0.03),
            "status": "ok" if active_days and daily.min() >= -0.03 else "watch" if active_days and daily.min() >= -0.10 else "fail",
            "note": "Day-level downside containment.",
        },
    ]
    return pd.DataFrame(rows)


def _prepare_anomaly_plot_frame(frame: pd.DataFrame, *, start_ts: int, end_ts: int) -> pd.DataFrame:
    columns = [
        column
        for column in (
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
        )
        if column in frame.columns
    ]
    plot_frame = frame.loc[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts), columns].copy()
    if plot_frame.empty:
        return plot_frame
    for column in columns:
        plot_frame[column] = pd.to_numeric(plot_frame[column], errors="coerce")
    plot_frame = plot_frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    if plot_frame.empty:
        return plot_frame
    plot_frame.sort_values("timestamp", inplace=True, kind="stable")
    plot_frame.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
    plot_frame["ema9"] = plot_frame["close"].ewm(span=9, adjust=False).mean()
    plot_frame["ema20"] = plot_frame["close"].ewm(span=20, adjust=False).mean()
    return plot_frame.reset_index(drop=True)


def _draw_anomaly_trade_block(
    ax,
    *,
    start_idx: int | None,
    end_idx: int | None,
    lower_price: float | None,
    upper_price: float | None,
    facecolor: str,
    edgecolor: str,
    alpha: float,
    zorder: float,
    frame_length: int,
) -> None:
    if start_idx is None or end_idx is None or lower_price is None or upper_price is None or frame_length <= 0:
        return
    block_start = min(max(int(start_idx), 0), frame_length - 1)
    block_end = min(max(int(end_idx), block_start), frame_length - 1)
    price_low = min(float(lower_price), float(upper_price))
    price_high = max(float(lower_price), float(upper_price))
    from matplotlib.patches import Rectangle

    ax.add_patch(
        Rectangle(
            (block_start - 0.5, price_low),
            max(float(block_end - block_start + 1), 1.0),
            max(price_high - price_low, 1e-9),
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=0.9,
            alpha=alpha,
            zorder=zorder,
        )
    )


def _annotate_anomaly_panel_message(ax, text: str) -> None:
    ax.text(
        0.5,
        0.5,
        text,
        transform=ax.transAxes,
        ha="center",
        va="center",
        color=CHART_MUTED,
        fontsize=7,
        alpha=0.72,
    )


def _normalize_chart_percent(frame: pd.DataFrame, column: str) -> np.ndarray | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if not values.notna().any():
        return None
    array = values.fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    max_value = float(np.nanmax(array)) if array.size else 0.0
    if max_value <= 0.0:
        return None
    return (array / max_value) * 100.0


def _build_trade_chart_hourly_context(frame: pd.DataFrame, *, end_timestamp_ms: int, days: int) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    if frame.empty or required.difference(frame.columns):
        return pd.DataFrame()

    end_exclusive = (int(end_timestamp_ms) // _HOUR_MS) * _HOUR_MS
    start_inclusive = end_exclusive - max(int(days), 1) * _DAY_MS
    source = frame.copy()
    source["timestamp"] = pd.to_numeric(source["timestamp"], errors="coerce")
    source = source.dropna(subset=["timestamp", "open", "high", "low", "close"])
    source["timestamp"] = source["timestamp"].astype("int64")
    source = source.loc[source["timestamp"].ge(start_inclusive) & source["timestamp"].lt(end_exclusive)].copy()
    if source.empty:
        return pd.DataFrame()

    for column in ("open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            source[column] = pd.to_numeric(source[column], errors="coerce")
    source = source.dropna(subset=["open", "high", "low", "close"])
    source = source.loc[(source["open"] > 0.0) & (source["high"] > 0.0) & (source["low"] > 0.0) & (source["close"] > 0.0)].copy()
    if source.empty:
        return pd.DataFrame()

    source.sort_values("timestamp", inplace=True)
    source.drop_duplicates("timestamp", keep="last", inplace=True)
    source.index = pd.to_datetime(source["timestamp"], unit="ms", utc=True)
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            aggregation[column] = "sum"
    hourly = source.resample("1h", label="left", closed="left").agg(aggregation)
    hourly = hourly.dropna(subset=["open", "high", "low", "close"]).copy()
    if hourly.empty:
        return pd.DataFrame()
    hourly["timestamp"] = (hourly.index.view("int64") // 1_000_000).astype("int64")
    hourly.reset_index(drop=True, inplace=True)
    return hourly


def _slice_trade_chart_hourly_level_context(
    hourly_context: pd.DataFrame,
    *,
    end_timestamp_ms: int,
    days: int,
) -> pd.DataFrame:
    """Return closed 1h context available at the chart as-of timestamp."""
    if hourly_context.empty or "timestamp" not in hourly_context.columns:
        return pd.DataFrame()

    end_exclusive = (int(end_timestamp_ms) // _HOUR_MS) * _HOUR_MS
    start_inclusive = end_exclusive - max(int(days), 1) * _DAY_MS
    sliced = hourly_context.copy()
    sliced["timestamp"] = pd.to_numeric(sliced["timestamp"], errors="coerce")
    sliced = sliced.dropna(subset=["timestamp"])
    if sliced.empty:
        return pd.DataFrame()
    sliced["timestamp"] = sliced["timestamp"].astype("int64")
    sliced = sliced.loc[
        sliced["timestamp"].ge(start_inclusive)
        & sliced["timestamp"].lt(end_exclusive)
    ].copy()
    if sliced.empty:
        return pd.DataFrame()
    sliced.sort_values("timestamp", inplace=True)
    sliced.drop_duplicates("timestamp", keep="last", inplace=True)
    sliced.reset_index(drop=True, inplace=True)
    return sliced


def _find_trade_chart_hourly_levels(
    hourly_context: pd.DataFrame,
    *,
    symbol: str,
    end_timestamp_ms: int,
    days: int = _TRADE_CHART_CONTEXT_DAYS,
) -> list[object]:
    level_context = _slice_trade_chart_hourly_level_context(
        hourly_context,
        end_timestamp_ms=end_timestamp_ms,
        days=days,
    )
    if level_context.empty:
        return []

    from research_tools.hourly_levels import HourlyLevelScanConfig, find_hourly_overhead_levels

    config = HourlyLevelScanConfig(
        cache_dir=Path(),
        output_dir=Path(),
        symbols=(symbol,),
        days=days,
        lookback_bars=days * 24,
        chart_bars=days * 24,
    )
    levels, _trend, _reason = find_hourly_overhead_levels(
        level_context,
        symbol=symbol,
        config=config,
    )
    return list(levels)


def _build_trade_chart_timeframe_context(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int | None,
) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    if frame.empty or required.difference(frame.columns):
        return pd.DataFrame()
    if timeframe_ms is None or int(timeframe_ms) <= 0:
        return pd.DataFrame()

    source = frame.copy()
    source["timestamp"] = pd.to_numeric(source["timestamp"], errors="coerce")
    source = source.dropna(subset=["timestamp", "open", "high", "low", "close"])
    source["timestamp"] = source["timestamp"].astype("int64")
    source = source.loc[
        source["timestamp"].ge(int(start_timestamp_ms))
        & source["timestamp"].le(int(end_timestamp_ms))
    ].copy()
    if source.empty:
        return pd.DataFrame()
    for column in ("open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            source[column] = pd.to_numeric(source[column], errors="coerce")
    source = source.dropna(subset=["open", "high", "low", "close"])
    if source.empty:
        return pd.DataFrame()
    source.sort_values("timestamp", inplace=True)
    source.drop_duplicates("timestamp", keep="last", inplace=True)
    source["bucket"] = (source["timestamp"] // int(timeframe_ms)) * int(timeframe_ms)
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            aggregation[column] = "sum"
    grouped = source.groupby("bucket", as_index=False).agg(aggregation)
    grouped["timestamp"] = grouped["bucket"].astype("int64")
    grouped.drop(columns=["bucket"], inplace=True)
    grouped.sort_values("timestamp", inplace=True)
    grouped.reset_index(drop=True, inplace=True)
    return grouped


def _format_ru_timeframe_label(step_ms: int | None) -> str:
    if step_ms is None or step_ms <= 0:
        return "Цена"
    seconds = step_ms / 1000.0
    if seconds < 60:
        return f"{seconds:g}с"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:g}м"
    hours = minutes / 60.0
    return f"{hours:g}ч"


def _annotate_chart_panel_label(ax, text: str) -> None:
    ax.text(
        0.02,
        0.96,
        text,
        transform=ax.transAxes,
        ha="left",
        va="top",
        color="#ffffff",
        fontsize=18,
        fontweight="bold",
        alpha=0.25,
        zorder=0.2,
    )


def _apply_trade_chart_ticks(ax, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    tick_timestamps = build_tick_timestamps(frame)
    tick_positions = build_tick_positions_from_timestamps(tick_timestamps, len(frame))
    tick_labels = build_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)


def _render_anomaly_trade_chart(
    *,
    frame: pd.DataFrame,
    trade: pd.Series,
    output_path: Path,
    pre_candles: int = 30,
    post_candles: int = 90,
    context_timeframe_ms: int | None = 300_000,
    hourly_context_frame: pd.DataFrame | None = None,
    draw_risk_reward_blocks: bool = True,
    draw_exit_marker: bool | None = None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    anomaly_ts = _safe_int(trade.get("anomaly_timestamp_ms")) or _safe_int(trade.get("decision_timestamp_ms"))
    decision_ts = _safe_int(trade.get("decision_timestamp_ms")) or anomaly_ts
    entry_ts = _safe_int(trade.get("entry_timestamp_ms")) or decision_ts
    raw_exit_ts = _safe_int(trade.get("exit_timestamp_ms"))
    exit_ts = raw_exit_ts or entry_ts
    if anomaly_ts is None or decision_ts is None or entry_ts is None or exit_ts is None:
        raise ValueError("missing_trade_timestamps")
    if draw_exit_marker is None:
        status = str(trade.get("status", "")).lower()
        draw_exit_marker = raw_exit_ts is not None and raw_exit_ts != entry_ts and status != "open"
    entry_price = _safe_float(trade.get("entry_price"))
    signal_entry_price = _safe_float(trade.get("signal_entry_price"))
    signal_entry_ts = _safe_int(trade.get("signal_entry_timestamp_ms")) or decision_ts
    initial_stop = _safe_float(trade.get("initial_stop"))
    tp1_price = _safe_float(trade.get("tp1_price"))
    box_high = _safe_float(trade.get("box_high"))
    exit_price = _safe_float(trade.get("exit_price"))
    timeframe_ms = int(infer_frame_step_ms(frame) or 60_000)
    start_ts = anomaly_ts - pre_candles * timeframe_ms
    end_ts = exit_ts + post_candles * timeframe_ms
    plot_frame = _prepare_anomaly_plot_frame(frame, start_ts=start_ts, end_ts=end_ts)
    if plot_frame.empty:
        raise ValueError("empty_plot_window")
    x_values = np.arange(len(plot_frame), dtype=np.float64)
    timestamps = plot_frame["timestamp"].to_numpy(dtype=np.int64)
    htf_frame = _build_trade_chart_timeframe_context(
        frame,
        start_timestamp_ms=start_ts,
        end_timestamp_ms=end_ts,
        timeframe_ms=context_timeframe_ms,
    )
    htf_x_values = np.arange(len(htf_frame), dtype=np.float64)
    context_source_frame = hourly_context_frame if hourly_context_frame is not None else frame
    context_asof_ts = _safe_int(trade.get("decision_available_timestamp_ms")) or decision_ts
    context_frame = _build_trade_chart_hourly_context(
        context_source_frame,
        end_timestamp_ms=context_asof_ts,
        days=_TRADE_CHART_CONTEXT_DAYS,
    )
    context_x_values = np.arange(len(context_frame), dtype=np.float64)
    context_levels = _find_trade_chart_hourly_levels(
        context_frame,
        symbol=str(trade.get("symbol", "")),
        end_timestamp_ms=context_asof_ts,
        days=_TRADE_CHART_CONTEXT_DAYS,
    )
    anomaly_idx = resolve_timestamp_plot_idx(timestamps, anomaly_ts)
    decision_idx = resolve_timestamp_plot_idx(timestamps, decision_ts)
    entry_idx = resolve_timestamp_plot_idx(timestamps, entry_ts)
    signal_entry_idx = resolve_timestamp_plot_idx(timestamps, signal_entry_ts)
    exit_idx = resolve_timestamp_plot_idx(timestamps, exit_ts)
    price_axis_right_x = float(len(plot_frame) - 0.5)

    fig, (ax_price, ax_htf, ax_flow, ax_context) = plt.subplots(
        4,
        1,
        figsize=TRADE_CHART_FIGSIZE,
        sharex=False,
        gridspec_kw={"height_ratios": [2.0, 2.0, 1.0, 1.0], "hspace": 0.08},
        facecolor=CHART_FIGURE_FACE,
    )
    configure_plot_axes(price_ax=ax_price, volume_ax=ax_htf, trades_ax=ax_flow)
    configure_plot_axes(price_ax=ax_context, volume_ax=ax_context)

    draw_candles(ax_price, plot_frame, x_values)
    if not htf_frame.empty:
        draw_candles(ax_htf, htf_frame, htf_x_values, alpha=0.94, zorder=3.0)
    else:
        _annotate_anomaly_panel_message(ax_htf, "HTF context unavailable")
    if not context_frame.empty:
        draw_candles(ax_context, context_frame, context_x_values, alpha=0.92, zorder=3.0)
        for level in context_levels:
            level_price = _safe_float(getattr(level, "level_price", None))
            if level_price is not None:
                ax_context.axhline(
                    level_price,
                    color=CHART_LEVEL,
                    linewidth=0.85,
                    alpha=0.62,
                    linestyle="--",
                    zorder=4.5,
                )
    else:
        _annotate_anomaly_panel_message(ax_context, "1h / 4d context unavailable")
    ax_price.plot(x_values, plot_frame["ema9"].to_numpy(dtype=np.float64), color=CHART_EMA9, linewidth=1.2, alpha=0.28, zorder=2.2)
    ax_price.plot(x_values, plot_frame["ema20"].to_numpy(dtype=np.float64), color=CHART_EMA20, linewidth=1.2, alpha=0.24, zorder=2.1)

    ax_price.axvline(anomaly_idx, color=CHART_ANOMALY, linewidth=0.95, alpha=0.30, zorder=5)
    ax_price.axvline(decision_idx, color=CHART_ENTRY, linewidth=0.9, alpha=0.18, linestyle="--", zorder=4.8)
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        ax_price.axvline(signal_entry_idx, color=CHART_MUTED, linewidth=0.9, alpha=0.30, linestyle=":", zorder=5.0)
    ax_price.axvline(entry_idx, color=CHART_ENTRY, linewidth=1.0, alpha=0.45, zorder=5.2)
    if draw_exit_marker:
        ax_price.axvline(exit_idx, color=CHART_EXIT, linewidth=1.0, alpha=0.52, linestyle="-.", zorder=5.3)

    draw_price_zone(
        ax_price,
        timestamps=timestamps,
        start_timestamp_ms=anomaly_ts,
        end_timestamp_ms=decision_ts,
        low=initial_stop,
        high=box_high,
        facecolor="#1d4ed8",
        edgecolor="#60a5fa",
        alpha=0.10,
        linewidth=0.9,
        zorder=2.0,
    )
    if draw_risk_reward_blocks:
        _draw_anomaly_trade_block(
            ax_price,
            start_idx=entry_idx,
            end_idx=exit_idx,
            lower_price=initial_stop,
            upper_price=entry_price,
            facecolor=CHART_RISK_FACE,
            edgecolor=CHART_RISK_EDGE,
            alpha=0.30,
            zorder=1.05,
            frame_length=len(plot_frame),
        )
        _draw_anomaly_trade_block(
            ax_price,
            start_idx=entry_idx,
            end_idx=exit_idx,
            lower_price=entry_price,
            upper_price=tp1_price,
            facecolor=CHART_PROFIT_FACE,
            edgecolor=CHART_PROFIT_EDGE,
            alpha=0.22,
            zorder=1.08,
            frame_length=len(plot_frame),
        )
    else:
        if tp1_price is not None:
            ax_price.axhline(tp1_price, color=CHART_PROFIT_EDGE, linewidth=1.1, alpha=0.78, linestyle="--", zorder=4.35)
        if initial_stop is not None:
            ax_price.axhline(initial_stop, color=CHART_RISK_EDGE, linewidth=1.1, alpha=0.78, linestyle="--", zorder=4.35)

    tag_input = [
        ("TP1", tp1_price),
        ("Entry", entry_price),
        ("SL", initial_stop),
    ]
    if draw_exit_marker:
        tag_input.append(("Exit", exit_price))
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        tag_input.append(("Signal", signal_entry_price))
    tag_positions = resolve_axis_tag_positions(tag_input)
    tag_specs = [
        ("TP1", tp1_price, CHART_PROFIT_EDGE, entry_idx - 0.5),
        ("Entry", entry_price, CHART_ENTRY, entry_idx),
        ("SL", initial_stop, CHART_RISK_EDGE, entry_idx - 0.5),
    ]
    if draw_exit_marker:
        tag_specs.append(("Exit", exit_price, CHART_EXIT, exit_idx))
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        tag_specs.append(("Signal", signal_entry_price, CHART_MUTED, signal_entry_idx))
    for label, value, color, leader_x in tag_specs:
        annotate_axis_price_tag(
            ax_price,
            y=value,
            label=label,
            color=color,
            leader_start_x=float(leader_x),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get(label),
            alpha=0.92,
        )

    volume_column = "quote_volume" if "quote_volume" in plot_frame.columns else "volume"
    volume_pct = _normalize_chart_percent(plot_frame, volume_column)
    trade_count_pct = _normalize_chart_percent(plot_frame, "number_of_trades")
    if volume_pct is not None:
        ax_flow.plot(
            x_values,
            volume_pct,
            color=CHART_DOWN,
            linewidth=1.15,
            alpha=0.72,
            zorder=3.0,
        )
    if trade_count_pct is not None:
        ax_flow.plot(
            x_values,
            trade_count_pct,
            color=CHART_UP,
            linewidth=1.25,
            alpha=0.88,
            zorder=4.0,
        )
    if volume_pct is None and trade_count_pct is None:
        _annotate_anomaly_panel_message(ax_flow, "volume / number_of_trades missing")

    high_values = plot_frame["high"].to_numpy(dtype=np.float64)
    low_values = plot_frame["low"].to_numpy(dtype=np.float64)
    price_ylim_values = [
        *high_values.tolist(),
        *low_values.tolist(),
        entry_price,
        initial_stop,
        tp1_price,
    ]
    if draw_exit_marker:
        price_ylim_values.append(exit_price)
    finite_price_ylim_values = [float(value) for value in price_ylim_values if value is not None and math.isfinite(float(value))]
    if not finite_price_ylim_values:
        raise ValueError("empty_price_axis_values")
    padding = max((max(finite_price_ylim_values) - min(finite_price_ylim_values)) * 0.05, 1e-9)
    ax_price.set_ylim(min(finite_price_ylim_values) - padding, max(finite_price_ylim_values) + padding)
    if not context_frame.empty:
        context_high = context_frame["high"].to_numpy(dtype=np.float64)
        context_low = context_frame["low"].to_numpy(dtype=np.float64)
        context_padding = max((float(np.nanmax(context_high)) - float(np.nanmin(context_low))) * 0.08, 1e-9)
        ax_context.set_ylim(float(np.nanmin(context_low)) - context_padding, float(np.nanmax(context_high)) + context_padding)
    if not htf_frame.empty:
        htf_high = htf_frame["high"].to_numpy(dtype=np.float64)
        htf_low = htf_frame["low"].to_numpy(dtype=np.float64)
        htf_padding = max((float(np.nanmax(htf_high)) - float(np.nanmin(htf_low))) * 0.06, 1e-9)
        ax_htf.set_ylim(float(np.nanmin(htf_low)) - htf_padding, float(np.nanmax(htf_high)) + htf_padding)
    entry_candle_width = resolve_candle_width(x_values)
    ax_price.set_xlim(-max(0.5, entry_candle_width * 0.65), len(plot_frame) - 1 + max(0.5, entry_candle_width * 0.65))
    if not context_frame.empty:
        ax_context.set_xlim(-0.5, len(context_frame) - 0.5)
    if not htf_frame.empty:
        htf_candle_width = resolve_candle_width(htf_x_values)
        ax_htf.set_xlim(-max(0.5, htf_candle_width * 0.65), len(htf_frame) - 1 + max(0.5, htf_candle_width * 0.65))
    ax_flow.set_ylim(0.0, 100.0)
    ax_flow.set_yticks([0.0, 50.0, 100.0])
    ax_flow.set_yticklabels(["0", "50", "100"], color=CHART_MUTED)
    ax_price.set_ylabel(_format_ru_timeframe_label(infer_frame_step_ms(plot_frame)))
    ax_htf.set_ylabel(_format_ru_timeframe_label(context_timeframe_ms))
    ax_flow.set_ylabel("Объем / сделки %")
    ax_context.set_ylabel("1ч")
    _annotate_chart_panel_label(ax_price, _format_ru_timeframe_label(infer_frame_step_ms(plot_frame)))
    _annotate_chart_panel_label(ax_htf, _format_ru_timeframe_label(context_timeframe_ms))
    _annotate_chart_panel_label(ax_flow, "Объем / сделки")
    _annotate_chart_panel_label(ax_context, "1ч")
    ax_price.set_title(
        (
            f"{format_chart_symbol(str(trade.get('symbol')))}  "
            f"{trade.get('entry_timestamp_utc')}  "
            f"net={float(trade.get('net_return', 0.0)):.2%}  "
            f"{trade.get('exit_reason', '')}"
        ),
        loc="left",
        color=CHART_TEXT,
        fontsize=10,
        pad=10,
        fontweight="semibold",
    )

    _apply_trade_chart_ticks(ax_htf, htf_frame)
    _apply_trade_chart_ticks(ax_context, context_frame)
    _apply_trade_chart_ticks(ax_flow, plot_frame)
    ax_price.tick_params(axis="x", labelbottom=False)
    ax_htf.tick_params(axis="x", labelbottom=True)
    ax_context.tick_params(axis="x", labelbottom=True)
    ax_flow.tick_params(axis="x", labelbottom=True)
    ax_price.margins(x=0.01)
    ax_htf.margins(x=0.01)
    ax_context.margins(x=0.0)
    ax_flow.margins(x=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(left=0.10, right=0.80, top=0.94, bottom=0.06, hspace=0.05)
    fig.savefig(output_path, **CHART_SAVEFIG_KWARGS)
    plt.close(fig)


def render_anomaly_trade_chart(
    *,
    frame: pd.DataFrame,
    trade: pd.Series | dict[str, object],
    output_path: Path,
    pre_candles: int = 30,
    post_candles: int = 90,
    context_timeframe_ms: int | None = 300_000,
    hourly_context_frame: pd.DataFrame | None = None,
    draw_risk_reward_blocks: bool = True,
    draw_exit_marker: bool | None = None,
) -> None:
    """Render one anomaly trade chart through the canonical backtest chart renderer."""

    trade_series = trade if isinstance(trade, pd.Series) else pd.Series(trade)
    _render_anomaly_trade_chart(
        frame=frame,
        trade=trade_series,
        output_path=output_path,
        pre_candles=pre_candles,
        post_candles=post_candles,
        context_timeframe_ms=context_timeframe_ms,
        hourly_context_frame=hourly_context_frame,
        draw_risk_reward_blocks=draw_risk_reward_blocks,
        draw_exit_marker=draw_exit_marker,
    )


def render_anomaly_trade_charts(
    trades: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    output_dir: Path,
    max_charts: int = 80,
) -> pd.DataFrame:
    columns = ["symbol", "entry_timestamp_ms", "status", "chart_status", "chart_reason", "chart_path"]
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=columns)
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=columns)
    closed["_abs_net"] = closed["net_return"].astype(float).abs()
    selected = closed.sort_values(["_abs_net", "entry_timestamp_ms"], ascending=[False, True]).head(max_charts).copy()
    selected.sort_values("entry_timestamp_ms", inplace=True)
    frame_cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, (_, trade) in enumerate(selected.iterrows(), start=1):
        symbol = str(trade["symbol"])
        entry_ts = int(trade["entry_timestamp_ms"])
        file_name = f"{processed_count:04d}_{_sanitize_file_part(symbol)}_{entry_ts}.png"
        chart_path = output_dir / file_name
        try:
            frame = frame_cache.get(symbol)
            if frame is None:
                frame = _read_symbol_frame(config.lab_config.cache_dir, symbol, _effective_entry_timeframe(config))
                frame_cache[symbol] = frame
            render_anomaly_trade_chart(frame=frame, trade=trade, output_path=chart_path)
            chart_status = "rendered"
            chart_reason = "ok"
        except Exception as exc:
            chart_status = "not_rendered"
            chart_reason = f"{type(exc).__name__}: {exc}"
            chart_path = Path("")
        rows.append(
            {
                "symbol": symbol,
                "entry_timestamp_ms": entry_ts,
                "status": str(trade.get("status", "")),
                "chart_status": chart_status,
                "chart_reason": chart_reason,
                "chart_path": str(chart_path),
            }
        )
        next_progress_pct = _emit_progress_1pct(
            label="anomaly charts",
            done=processed_count,
            total=len(selected),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )
    return pd.DataFrame(rows, columns=columns)



def run_anomaly_entry_grid(
    candidates: pd.DataFrame,
    base_config: AnomalyBacktestConfig,
    *,
    oi3_values: Iterable[float],
    hold_values: Iterable[int],
    pullback_fractions: Iterable[float],
    exhaustion_profiles: Iterable[str],
    exit_rules: Iterable[str],
    signal_sets: list[tuple[AnomalyBacktestConfig, pd.DataFrame]] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if signal_sets is None:
        variants = _iter_entry_grid_configs(
            base_config,
            oi3_values=oi3_values,
            hold_values=hold_values,
            pullback_fractions=pullback_fractions,
            exhaustion_profiles=exhaustion_profiles,
            exit_rules=exit_rules,
        )
        signal_sets = _build_entry_grid_signal_sets(candidates, variants)
    started_at = time.monotonic()
    next_progress_pct = 0
    frame_cache: dict[str, pd.DataFrame] = {}
    for idx, (variant, signals) in enumerate(signal_sets, start=1):
        trades = simulate_anomaly_trades(signals, config=variant, frame_cache=frame_cache)
        rows.append(summarize_entry_grid_variant(trades, config=variant, signal_count=len(signals), variant_id=idx - 1))
        next_progress_pct = _emit_progress_1pct(
            label="anomaly entry grid",
            done=idx,
            total=len(signal_sets),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )
    result = pd.DataFrame(rows)
    if not result.empty and "avg_net_return" in result.columns:
        result.sort_values(["avg_net_return", "closed_trades"], ascending=[False, False], inplace=True)
    return result


def run_anomaly_latency_grid(
    signals: pd.DataFrame,
    base_config: AnomalyBacktestConfig,
    *,
    latency_ms_values: Iterable[int],
    primary_trades: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    frame_cache: dict[str, pd.DataFrame] = {}
    values = [max(0, int(value)) for value in latency_ms_values]
    started_at = time.monotonic()
    next_progress_pct = 0
    for idx, latency_ms in enumerate(values, start=1):
        variant = replace(base_config, latency_enabled=True, latency_extra_ms=int(latency_ms))
        if (
            primary_trades is not None
            and bool(base_config.latency_enabled)
            and int(latency_ms) == int(base_config.latency_extra_ms)
        ):
            trades = primary_trades
        else:
            trades = simulate_anomaly_trades(signals, config=variant, frame_cache=frame_cache)
        summary = summarize_trades(trades)
        row = {
            "variant_id": idx - 1,
            "latency_extra_ms": int(latency_ms),
            "execution_model": _execution_model_label(variant),
            "signal_count": int(len(signals)),
        }
        if not summary.empty and {"metric", "value"}.issubset(summary.columns):
            row.update({str(item["metric"]): item["value"] for _, item in summary.iterrows()})
        rows.append(row)
        next_progress_pct = _emit_progress_1pct(
            label="anomaly latency grid",
            done=idx,
            total=len(values),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )
    return pd.DataFrame(rows)


def _summary_metric(summary: pd.DataFrame, metric: str, default: object = 0) -> object:
    if summary.empty or "metric" not in summary.columns or "value" not in summary.columns:
        return default
    rows = summary.loc[summary["metric"].astype(str).eq(str(metric)), "value"]
    if rows.empty:
        return default
    return rows.iloc[0]


def run_anomaly_strategy_backtest(
    config: AnomalyBacktestConfig,
    *,
    symbols: Iterable[str] | None = None,
    precollected_candidates: pd.DataFrame | None = None,
    precollected_candidates_have_context: bool = False,
    run_entry_grid: bool = False,
    grid_oi3_values: Iterable[float] = (0.01, 0.02, 0.03),
    grid_hold_values: Iterable[int] = (1, 2),
    grid_pullback_fractions: Iterable[float] = (0.65, 0.75, 0.85),
    grid_exhaustion_profiles: Iterable[str] = ("none",),
    grid_exit_rules: Iterable[str] = ("structural_trail",),
    run_latency_grid: bool = False,
    latency_grid_ms: Iterable[int] = DEFAULT_LATENCY_GRID_MS,
    derivatives_context_fetcher: object | None = None,
    render_charts: bool = True,
) -> Path:
    total_started_at = time.monotonic()
    timings: dict[str, float] = {}
    speed_diagnostics: list[dict[str, object]] = []
    config = _apply_red_flag_profile(config)
    run_latency_grid = bool(run_latency_grid or config.latency_enabled)
    output_dir = config.lab_config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    targeted_flow_backfill = pd.DataFrame()
    targeted_flow_plan = pd.DataFrame()
    targeted_flow_fetch = pd.DataFrame()
    targeted_flow_materialize = pd.DataFrame()
    targeted_flow_coverage = pd.DataFrame()
    stage_started_at = time.monotonic()
    precollected_context_ready = False
    if precollected_candidates is not None:
        candidates = precollected_candidates.copy()
        precollected_context_ready = bool(precollected_candidates_have_context)
        if not precollected_context_ready:
            candidates = enrich_candidates_with_open_interest(
                candidates,
                cache_dir=config.lab_config.cache_dir,
                progress_label="anomaly candidates: oi context refresh",
            )
    elif _effective_entry_timeframe(config) != _effective_setup_timeframe(config):
        targeted_flow_backfill, targeted_flow_materialize = ensure_targeted_subminute_flow_cache_for_configs(
            [config],
            symbols=symbols,
            progress_label="anomaly targeted flow",
        )
        targeted_flow_plan, targeted_flow_fetch = split_targeted_flow_backfill_artifacts(targeted_flow_backfill)
        targeted_flow_coverage = build_targeted_flow_coverage(
            backfill=targeted_flow_backfill,
            materialize=targeted_flow_materialize,
            configs=[config],
        )
        _write_artifact_frames(
            [
                (output_dir / "targeted_flow_plan.csv", targeted_flow_plan),
                (output_dir / "targeted_flow_fetch.csv", targeted_flow_fetch),
                (output_dir / "targeted_flow_backfill.csv", targeted_flow_backfill),
                (output_dir / "targeted_flow_materialize.csv", targeted_flow_materialize),
                (output_dir / "targeted_flow_coverage.csv", targeted_flow_coverage),
            ],
            progress_label="anomaly artifacts: targeted flow",
            timing_rows=speed_diagnostics,
            timing_scope="anomaly artifacts: targeted flow",
        )
        planned_windows = _targeted_flow_planned_window_count(targeted_flow_plan)
        ready_symbols = ready_symbols_from_targeted_flow_coverage(
            targeted_flow_coverage,
            entry_timeframe=_effective_entry_timeframe(config),
        )
        if planned_windows > 0 and not ready_symbols:
            candidates = pd.DataFrame([
                {
                    "symbol": "__all__",
                    "timeframe": _effective_setup_timeframe(config),
                    "setup_timeframe": _effective_setup_timeframe(config),
                    "entry_timeframe": _effective_entry_timeframe(config),
                    "feature_contract": "htf_setup_ltf_entry_v1",
                    "status": "error",
                    "error": "no_trusted_targeted_flow_coverage_after_fetch",
                    "execution_model": "targeted_flow_required_before_pair_collection",
                }
            ])
        else:
            collection_symbols = targeted_flow_collection_symbols(
                targeted_flow_coverage,
                requested_symbols=symbols,
            )
            candidates = collect_pair_anomaly_rows(
                config,
                symbols=collection_symbols if collection_symbols is not None else symbols,
                progress_label="anomaly candidates",
                include_derivatives_context=derivatives_context_fetcher is None,
                auto_targeted_flow_backfill=False,
                speed_diagnostics=speed_diagnostics,
            )
    else:
        candidates = collect_anomaly_lab_rows(
            config.lab_config,
            symbols=symbols,
            progress_label="anomaly candidates",
            include_derivatives_context=derivatives_context_fetcher is None,
        )
        if not candidates.empty:
            candidates = candidates.copy()
            candidates["feature_contract"] = "closed_setup_tf_v1"
            candidates["setup_timeframe"] = _effective_setup_timeframe(config)
            candidates["entry_timeframe"] = _effective_entry_timeframe(config)
            candidates["setup_source"] = "closed_setup_tf"
            candidates["setup_elapsed_fraction"] = 1.0
            candidates["setup_closed_entry_candles"] = candidates.get("confirmation_candles", config.lab_config.confirmation_candles)
    if not candidates.empty and not precollected_context_ready:
        candidates = enrich_candidates_with_recent_spike_context(candidates, config=config)
    _record_stage_timing(timings, speed_diagnostics, "candidates_seconds", time.monotonic() - stage_started_at, output_rows=len(candidates))
    if str(getattr(config, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING)) == PAIR_COLLECTION_MODE_BARE_HTF_SHORT_FADER:
        return run_bare_htf_short_fader_backtest(
            candidates,
            config=config,
            output_dir=output_dir,
            symbols=symbols,
            targeted_flow_plan=targeted_flow_plan,
            targeted_flow_fetch=targeted_flow_fetch,
            targeted_flow_materialize=targeted_flow_materialize,
            targeted_flow_coverage=targeted_flow_coverage,
            timings=timings,
            total_started_at=total_started_at,
            speed_diagnostics=speed_diagnostics,
        )
    print("anomaly signals: filtering", flush=True)
    stage_started_at = time.monotonic()
    pre_context_config = replace(
        config,
        red_flag_profile="none",
        min_mark_close_vs_decision_close_basis=None,
        reject_oi_down_mark_discount=False,
        reject_stale_derivatives_context=False,
    )
    signals = build_anomaly_signals(
        candidates,
        config=pre_context_config if derivatives_context_fetcher is not None else config,
    )
    grid_variants: list[AnomalyBacktestConfig] | None = None
    grid_signal_sets: list[tuple[AnomalyBacktestConfig, pd.DataFrame]] | None = None
    pre_context_universe = _build_pre_context_signal_universe(candidates, config)
    if derivatives_context_fetcher is not None:
        if run_entry_grid:
            grid_variants = _iter_entry_grid_configs(
                config,
                oi3_values=grid_oi3_values,
                hold_values=grid_hold_values,
                pullback_fractions=grid_pullback_fractions,
                exhaustion_profiles=grid_exhaustion_profiles,
                exit_rules=grid_exit_rules,
            )
            pre_context_grid_signal_sets = _build_entry_grid_signal_sets(
                candidates,
                [_strip_derivative_context_requirements(variant) for variant in grid_variants],
            )
            grid_context_signals = _signal_universe_from_signal_sets(pre_context_grid_signal_sets)
            context_signals = pd.concat([pre_context_universe, grid_context_signals], ignore_index=True, sort=False)
            if not context_signals.empty:
                context_signals.drop_duplicates(["symbol", "decision_timestamp_ms"], inplace=True)
        else:
            context_signals = pre_context_universe.copy()
        context_fetch_status = _fetch_derivatives_context_for_signal_universe(
            context_signals,
            derivatives_context_fetcher=derivatives_context_fetcher,
        )
        _write_artifact_frames(
            [(output_dir / "market_context_fetch_status.csv", context_fetch_status)],
            progress_label="anomaly artifacts: context fetch status",
            timing_rows=speed_diagnostics,
            timing_scope="anomaly artifacts: context fetch status",
        )
        candidates = _enrich_derivatives_context_for_signal_universe(
            candidates,
            context_signals,
            cache_dir=config.lab_config.cache_dir,
        )
        red_flag_universe = build_anomaly_signals(candidates, config=pre_context_config)
        signals = build_anomaly_signals(candidates, config=config)
        if run_entry_grid and grid_variants is not None:
            grid_signal_sets = _build_entry_grid_signal_sets(candidates, grid_variants)
    else:
        red_flag_universe = build_anomaly_signals(candidates, config=pre_context_config)
    signals = annotate_pump_categories(signals, candidates, config=config)
    _record_stage_timing(timings, speed_diagnostics, "signals_context_seconds", time.monotonic() - stage_started_at, output_rows=len(signals), item_count=len(candidates))
    end_ms = config.lab_config.end_timestamp_ms
    if end_ms is None:
        end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    start_ms = int((datetime.fromtimestamp(int(end_ms) / 1000, UTC) - pd.Timedelta(days=config.lab_config.days)).timestamp() * 1000)
    stage_started_at = time.monotonic()
    _write_artifact_frames(
        [
            (output_dir / "anomaly_candidates.csv", candidates),
            (output_dir / "anomaly_universe_contract.csv", _universe_contract_frame(symbols=symbols)),
            (
                output_dir / "entry_cache_coverage.csv",
                build_entry_cache_coverage(
                    cache_dir=config.lab_config.cache_dir,
                    setup_timeframe=_effective_setup_timeframe(config),
                    entry_timeframe=_effective_entry_timeframe(config),
                    start_ms=start_ms,
                    end_ms=int(end_ms),
                    symbols=symbols,
                ),
            ),
            (output_dir / "anomaly_red_flag_summary.csv", build_red_flag_summary(red_flag_universe, config=config)),
            (output_dir / "oi_context_status.csv", build_oi_context_status(candidates)),
            (output_dir / "market_context_status.csv", build_derivatives_context_status(candidates)),
            (output_dir / "anomaly_signals.csv", signals),
        ],
        progress_label="anomaly artifacts: base files",
        timing_rows=speed_diagnostics,
        timing_scope="anomaly artifacts: base files",
    )
    _record_stage_timing(timings, speed_diagnostics, "base_artifacts_seconds", time.monotonic() - stage_started_at)
    print(f"anomaly trades: simulating {len(signals)} signals", flush=True)
    stage_started_at = time.monotonic()
    trades = simulate_anomaly_trades(
        signals,
        config=config,
        progress_label="anomaly trades",
        speed_diagnostics=speed_diagnostics,
    )
    _record_stage_timing(
        timings,
        speed_diagnostics,
        "trades_seconds",
        time.monotonic() - stage_started_at,
        output_rows=len(trades),
        item_count=len(signals),
    )
    live_filtered_trades = apply_live_portfolio_filter(
        trades,
        max_open_positions=DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS,
    )
    live_filter_summary = summarize_live_portfolio_filter(trades, live_filtered_trades)
    summary = summarize_trades(trades)
    skip_reasons = summarize_trade_skip_reasons(trades)
    live_filtered_summary = summarize_trades(live_filtered_trades)
    live_filtered_skip_reasons = summarize_trade_skip_reasons(live_filtered_trades)
    context_parity_report = build_context_parity_report(
        candidates,
        pre_context_universe=pre_context_universe,
        signals=signals,
        trades=trades,
    )
    honesty_report = build_backtest_honesty_report(
        config=config,
        candidates=candidates,
        signals=signals,
        trades=trades,
        symbols=symbols,
        context_parity_report=context_parity_report,
        run_entry_grid=bool(run_entry_grid),
        run_latency_grid=bool(run_latency_grid),
    )
    anomaly_funnel = build_anomaly_funnel(
        candidates=candidates,
        signals=signals,
        trades=trades,
        targeted_flow_plan=targeted_flow_plan,
        targeted_flow_fetch=targeted_flow_fetch,
        targeted_flow_materialize=targeted_flow_materialize,
        targeted_flow_coverage=targeted_flow_coverage,
    )
    run_verdict = build_backtest_run_verdict(
        config=config,
        candidates=candidates,
        signals=signals,
        trades=trades,
        targeted_flow_plan=targeted_flow_plan,
        targeted_flow_coverage=targeted_flow_coverage,
    )
    valid_backtest = bool(run_verdict.iloc[0]["valid_backtest"]) if not run_verdict.empty else False
    invalid_reason = str(run_verdict.iloc[0]["reason"]) if not run_verdict.empty else "missing_run_verdict"
    stage_started_at = time.monotonic()
    _write_artifact_frames(
        [
            (output_dir / "anomaly_trades.csv", trades),
            (output_dir / "anomaly_profitability_summary.csv", summary),
            (output_dir / "anomaly_skip_reasons.csv", skip_reasons),
            (output_dir / "anomaly_profitability_by_symbol.csv", summarize_trades_by_symbol(trades)),
            (output_dir / "anomaly_profitability_by_category.csv", summarize_trades_by_pump_category(trades)),
            (output_dir / "anomaly_profitability_by_category_family.csv", summarize_trades_by_pump_category_family(trades)),
            (output_dir / "anomaly_trades_live_filtered.csv", live_filtered_trades),
            (output_dir / "anomaly_live_portfolio_filter_summary.csv", live_filter_summary),
            (output_dir / "anomaly_profitability_summary_live_filtered.csv", live_filtered_summary),
            (output_dir / "anomaly_skip_reasons_live_filtered.csv", live_filtered_skip_reasons),
            (output_dir / "anomaly_profitability_by_symbol_live_filtered.csv", summarize_trades_by_symbol(live_filtered_trades)),
            (output_dir / "anomaly_profitability_by_category_live_filtered.csv", summarize_trades_by_pump_category(live_filtered_trades)),
            (output_dir / "anomaly_profitability_by_category_family_live_filtered.csv", summarize_trades_by_pump_category_family(live_filtered_trades)),
            (output_dir / "anomaly_context_parity_report.csv", context_parity_report),
            (output_dir / "anomaly_backtest_honesty_report.csv", honesty_report),
            (output_dir / "anomaly_funnel.csv", anomaly_funnel),
            (output_dir / "anomaly_run_verdict.csv", run_verdict),
        ],
        progress_label="anomaly artifacts: trade files",
        timing_rows=speed_diagnostics,
        timing_scope="anomaly artifacts: trade files",
    )
    _record_stage_timing(timings, speed_diagnostics, "trade_artifacts_seconds", time.monotonic() - stage_started_at)
    stage_started_at = time.monotonic()
    _write_prepump_context_artifacts(config=config, output_dir=output_dir)
    _record_stage_timing(timings, speed_diagnostics, "prepump_context_artifacts_seconds", time.monotonic() - stage_started_at)
    if run_latency_grid:
        if valid_backtest:
            print("anomaly latency grid: running variants", flush=True)
            stage_started_at = time.monotonic()
            latency_grid = run_anomaly_latency_grid(
                signals,
                config,
                latency_ms_values=latency_grid_ms,
                primary_trades=trades,
            )
        else:
            stage_started_at = time.monotonic()
            latency_grid = _disabled_artifact_frame(
                artifact="anomaly_latency_grid_summary.csv",
                reason=invalid_reason,
            )
        _write_artifact_frames(
            [(output_dir / "anomaly_latency_grid_summary.csv", latency_grid)],
            progress_label="anomaly artifacts: latency grid files",
            timing_rows=speed_diagnostics,
            timing_scope="anomaly artifacts: latency grid files",
        )
        _record_stage_timing(timings, speed_diagnostics, "latency_grid_seconds", time.monotonic() - stage_started_at, output_rows=len(latency_grid))
    if run_entry_grid:
        if valid_backtest:
            print("anomaly entry grid: running variants", flush=True)
            stage_started_at = time.monotonic()
            grid = run_anomaly_entry_grid(
                candidates,
                config,
                oi3_values=grid_oi3_values,
                hold_values=grid_hold_values,
                pullback_fractions=grid_pullback_fractions,
                exhaustion_profiles=grid_exhaustion_profiles,
                exit_rules=grid_exit_rules,
                signal_sets=grid_signal_sets,
            )
        else:
            stage_started_at = time.monotonic()
            grid = _disabled_artifact_frame(
                artifact="anomaly_entry_grid_summary.csv",
                reason=invalid_reason,
            )
        _write_artifact_frames(
            [(output_dir / "anomaly_entry_grid_summary.csv", grid)],
            progress_label="anomaly artifacts: grid files",
            timing_rows=speed_diagnostics,
            timing_scope="anomaly artifacts: grid files",
        )
        if valid_backtest and grid_signal_sets is not None and not grid.empty and "variant_id" in grid.columns:
            best_variant_id = int(grid.iloc[0]["variant_id"])
            if 0 <= best_variant_id < len(grid_signal_sets):
                best_config, best_signals = grid_signal_sets[best_variant_id]
                best_trades = simulate_anomaly_trades(best_signals, config=best_config)
                best_label = (
                    f"grid_best_{best_config.exhaustion_profile}_"
                    f"oi{best_config.min_oi_change_pct_3x5m}_hold{best_config.min_hold_count}_"
                    f"{best_config.entry_method}_{best_config.exit_rule}"
                )
                best_config_frame = pd.DataFrame([{**asdict(best_config), "lab_config": asdict(best_config.lab_config)}])
                best_health = build_edge_health_table(best_trades, label=best_label)
                best_skip_reasons = summarize_trade_skip_reasons(best_trades)
                _write_artifact_frames(
                    [
                        (output_dir / "anomaly_entry_grid_best_trades.csv", best_trades),
                        (output_dir / "anomaly_entry_grid_best_config.csv", best_config_frame),
                        (output_dir / "anomaly_entry_grid_best_skip_reasons.csv", best_skip_reasons),
                        (output_dir / "anomaly_edge_health.csv", best_health),
                    ],
                    progress_label="anomaly artifacts: best grid files",
                )
                chart_status = (
                    render_anomaly_trade_charts(
                        best_trades,
                        config=best_config,
                        output_dir=output_dir / "charts" / "best_grid_variant",
                    )
                    if render_charts
                    else pd.DataFrame([{"status": "skipped", "reason": "render_charts_false"}])
                )
                _write_artifact_frames(
                    [(output_dir / "anomaly_trade_chart_status.csv", chart_status)],
                    progress_label="anomaly artifacts: chart status",
                )
        _record_stage_timing(timings, speed_diagnostics, "entry_grid_seconds", time.monotonic() - stage_started_at, output_rows=len(grid))
    elif valid_backtest and not trades.empty:
        stage_started_at = time.monotonic()
        health = build_edge_health_table(trades, label="primary")
        live_filtered_health = build_edge_health_table(live_filtered_trades, label="live_filtered")
        chart_status = (
            render_anomaly_trade_charts(
                trades,
                config=config,
                output_dir=output_dir / "charts" / "primary",
            )
            if render_charts
            else pd.DataFrame([{"status": "skipped", "reason": "render_charts_false"}])
        )
        _write_artifact_frames(
            [
                (output_dir / "anomaly_edge_health.csv", health),
                (output_dir / "anomaly_edge_health_live_filtered.csv", live_filtered_health),
                (output_dir / "anomaly_trade_chart_status.csv", chart_status),
            ],
            progress_label="anomaly artifacts: health chart status",
            timing_rows=speed_diagnostics,
            timing_scope="anomaly artifacts: health chart status",
        )
        _record_stage_timing(timings, speed_diagnostics, "health_charts_seconds", time.monotonic() - stage_started_at)
    elif not valid_backtest:
        stage_started_at = time.monotonic()
        _write_artifact_frames(
            [
                (output_dir / "anomaly_edge_health.csv", _disabled_artifact_frame(artifact="anomaly_edge_health.csv", reason=invalid_reason)),
                (output_dir / "anomaly_edge_health_live_filtered.csv", _disabled_artifact_frame(artifact="anomaly_edge_health_live_filtered.csv", reason=invalid_reason)),
                (output_dir / "anomaly_trade_chart_status.csv", _disabled_artifact_frame(artifact="anomaly_trade_chart_status.csv", reason=invalid_reason)),
            ],
            progress_label="anomaly artifacts: disabled health chart status",
            timing_rows=speed_diagnostics,
            timing_scope="anomaly artifacts: disabled health chart status",
        )
        _record_stage_timing(timings, speed_diagnostics, "health_charts_seconds", time.monotonic() - stage_started_at)
    requested_symbols_normalized = _normalized_symbol_tuple(symbols)
    run_config = {
        **asdict(config),
        "feature_contract": config.feature_contract,
        "universe_symbol_scope": _universe_symbol_scope(symbols),
        "universe_requested_symbols_count": int(len(requested_symbols_normalized)),
        "universe_requested_symbols_normalized": requested_symbols_normalized,
        "historical_listing_snapshot_available": False,
        "survivorship_bias_risk": _universe_symbol_scope(symbols) == "cache_snapshot_scan",
        "setup_timeframe": _effective_setup_timeframe(config),
        "entry_timeframe": _effective_entry_timeframe(config),
        "execution_model": _execution_model_label(config),
        "portfolio_model": f"global_max_open_positions_{int(config.max_open_positions)}_at_actual_entry",
        "live_filtered_portfolio_model": (
            f"post_simulation_global_max_open_positions_{int(DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS)}"
        ),
        "live_filtered_max_open_positions": int(DEFAULT_ANOMALY_LIVE_FILTER_MAX_OPEN_POSITIONS),
        "slippage_model": "adverse_long_entry_and_exit",
        "entry_slippage_pct": float(config.entry_slippage_pct),
        "exit_slippage_pct": float(config.exit_slippage_pct),
        "prior_context_lookback_hours": int(_PRIOR_CONTEXT_LIVE_LOOKBACK_HOURS),
        "valid_backtest": bool(valid_backtest),
        "backtest_verdict": str(run_verdict.iloc[0]["verdict"]) if not run_verdict.empty else "missing_run_verdict",
        "backtest_invalid_reason": invalid_reason,
        "targeted_flow_planned_windows": int(_targeted_flow_planned_window_count(targeted_flow_plan)),
        "targeted_flow_ready_windows": int((targeted_flow_coverage.get("coverage_status", pd.Series(dtype=str)).astype(str) == "ready").sum()) if not targeted_flow_coverage.empty else 0,
        "lab_config": asdict(config.lab_config),
    }
    _record_stage_timing(timings, speed_diagnostics, "total_seconds", time.monotonic() - total_started_at)
    timing_frame = pd.DataFrame(
        [{"stage": key, "seconds": round(float(value), 3)} for key, value in timings.items()]
    )
    speed_detail_frame = speed_diagnostics_frame(speed_diagnostics)
    speed_summary_frame = speed_diagnostics_summary_frame(
        speed_diagnostics,
        total_seconds=float(timings.get("total_seconds", 0.0)),
    )
    slowest_symbols_frame = speed_diagnostics_slowest_symbols_frame(speed_diagnostics)
    _write_artifact_frames(
        [(output_dir / "anomaly_timing_summary.csv", timing_frame)],
        progress_label="anomaly artifacts: timing summary",
        timing_rows=speed_diagnostics,
        timing_scope="timing_artifacts",
    )
    _write_artifact_frames(
        [
            (output_dir / "anomaly_speed_diagnostics.csv", speed_detail_frame),
            (output_dir / "anomaly_speed_summary.csv", speed_summary_frame),
            (output_dir / "anomaly_slowest_symbols.csv", slowest_symbols_frame),
        ],
        progress_label="anomaly artifacts: speed diagnostics",
    )
    _write_artifact_frames(
        [(output_dir / "run_config.csv", pd.DataFrame([run_config]))],
        progress_label="anomaly artifacts: run config",
        timing_rows=speed_diagnostics,
        timing_scope="anomaly artifacts: run config",
    )
    print(
        "anomaly result: "
        f"signals={len(signals)} "
        f"closed={_summary_metric(summary, 'closed_trades')} "
        f"skipped={_summary_metric(summary, 'skipped_trades')} "
        f"avg_net={float(_summary_metric(summary, 'avg_net_return', 0.0)):.4%} "
        f"sum_net={float(_summary_metric(summary, 'sum_net_return', 0.0)):.4%} "
        f"win_rate={float(_summary_metric(summary, 'win_rate', 0.0)):.2%} "
        f"execution={_execution_model_label(config)}",
        flush=True,
    )
    print(
        "anomaly timing: "
        + " · ".join(f"{key}={float(value):.1f}s" for key, value in timings.items()),
        flush=True,
    )
    return output_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".output/cache"))
    parser.add_argument("--output-dir", type=Path, default=Path(".output/results/anomaly_lab"))
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--timeframe", default="1m", help="Legacy single-timeframe mode; used as setup timeframe unless --setup-timeframe is set")
    parser.add_argument("--setup-timeframe", default=None, help="HTF setup timeframe, e.g. 5m")
    parser.add_argument("--entry-timeframe", default=None, help="LTF execution timeframe, e.g. 30s. If omitted, equals setup/timeframe")
    parser.add_argument("--end-timestamp-ms", type=int, default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--baseline-candles", type=int, default=60)
    parser.add_argument("--confirmation-candles", type=int, default=4)
    parser.add_argument("--forward-high-candles", type=int, default=240)
    parser.add_argument("--forward-low-candles", type=int, default=60)
    parser.add_argument("--min-quote-ratio-start", type=float, default=5.0)
    parser.add_argument("--min-trade-ratio-start", type=float, default=5.0)
    parser.add_argument("--min-price-retention", type=float, default=0.70)
    parser.add_argument("--max-price-retention", type=float, default=None)
    parser.add_argument("--min-verticality-score", type=float, default=0.25)
    parser.add_argument("--min-hold-count", type=int, default=2)
    parser.add_argument("--min-oi-change-pct-3x5m", type=float, default=None)
    parser.add_argument("--require-oi-status-ok", action="store_true")
    parser.add_argument("--exhaustion-profile", choices=sorted(EXHAUSTION_PROFILES), default="none")
    parser.add_argument("--max-start-quote-ratio", type=float, default=None)
    parser.add_argument("--max-start-trade-ratio", type=float, default=None)
    parser.add_argument("--max-start-avg-trade-quote-size-ratio", type=float, default=None)
    parser.add_argument("--max-start-quote-ratio-per-abs-return", type=float, default=None)
    parser.add_argument("--max-start-trade-ratio-per-abs-return", type=float, default=None)
    parser.add_argument("--max-start-range-pct-ratio-to-baseline", type=float, default=None)
    parser.add_argument("--min-runner-shape-quote-ratio", type=float, default=None)
    parser.add_argument("--min-runner-shape-trade-ratio", type=float, default=None)
    parser.add_argument("--min-runner-shape-range-ratio", type=float, default=None)
    parser.add_argument("--min-runner-shape-quote-acceleration", type=float, default=None)
    parser.add_argument("--min-runner-shape-trade-acceleration", type=float, default=None)
    parser.add_argument("--min-runner-shape-range-acceleration", type=float, default=None)
    parser.add_argument("--min-runner-shape-second-half-return-pct", type=float, default=None)
    parser.add_argument("--max-runner-shape-top1-quote-share", type=float, default=None)
    parser.add_argument("--max-prior-up-down-whipsaw-to-impulse-range", type=float, default=0.60)
    parser.add_argument("--min-next-taker-buy-quote-share", type=float, default=None)
    parser.add_argument(
        "--red-flag-profile",
        choices=["none", "cautious", "strict", "runner_balanced", "runner_reclaim", "runner_flow", "runner_oi_confirmed"],
        default="none",
    )
    parser.add_argument("--min-mark-close-vs-decision-close-basis", type=float, default=None)
    parser.add_argument("--reject-oi-down-mark-discount", action="store_true")
    parser.add_argument("--reject-stale-derivatives-context", action="store_true")
    parser.add_argument("--max-start-taker-buy-quote-share-delta", type=float, default=None)
    parser.add_argument("--max-next-taker-buy-quote-share-delta", type=float, default=None)
    parser.add_argument("--min-flow-hold-count", type=int, default=None)
    parser.add_argument("--max-prior-spike-count-72h", type=int, default=None)
    parser.add_argument("--max-prior-fast-fade-count-72h", type=int, default=None)
    parser.add_argument("--min-start-lower-wick-to-range", type=float, default=None)
    parser.add_argument("--max-start-upper-wick-to-range", type=float, default=None)
    parser.add_argument("--max-initial-risk-pct", type=float, default=0.16)
    parser.add_argument("--entry-method", choices=["market", "break_box_high", "pullback_box_fraction"], default="market")
    parser.add_argument("--pullback-box-fraction", type=float, default=0.75)
    parser.add_argument("--entry-timeout-candles", type=int, default=60)
    parser.add_argument("--market-entry-latency-candles", type=int, default=1)
    parser.add_argument("--latency", choices=["true", "false"], default="false")
    parser.add_argument(
        "--max-market-entry-drift-pct",
        type=float,
        default=DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT,
    )
    parser.add_argument("--min-market-rr-to-signal-tp1", type=float, default=0.70)
    parser.add_argument("--tp1-r", type=float, default=1.0)
    parser.add_argument("--tp1-fraction", type=float, default=0.50)
    parser.add_argument("--trail-lookback-candles", type=int, default=5)
    parser.add_argument("--trail-buffer-r", type=float, default=0.10)
    parser.add_argument("--exit-rule", choices=sorted(EXIT_RULES), default="structural_trail")
    parser.add_argument("--max-hold-candles", type=int, default=240)
    parser.add_argument("--max-open-positions", type=int, default=DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--entry-slippage-pct", type=float, default=DEFAULT_SLIPPAGE)
    parser.add_argument("--exit-slippage-pct", type=float, default=DEFAULT_SLIPPAGE)
    parser.add_argument("--write-prepump-context", choices=["true", "false"], default="true")
    parser.add_argument("--prepump-context-timeframe", default=DEFAULT_PREPUMP_CONTEXT_TIMEFRAME)
    parser.add_argument("--prepump-context-windows", default=DEFAULT_PREPUMP_CONTEXT_WINDOWS)
    parser.add_argument("--prepump-context-min-coverage-ratio", type=float, default=0.80)
    parser.add_argument("--backtest-symbol-workers", type=int, default=DEFAULT_BACKTEST_SYMBOL_WORKERS)
    parser.add_argument("--render-charts", choices=["true", "false"], default="true")
    parser.add_argument("--run-entry-grid", action="store_true")
    parser.add_argument("--grid-oi3-values", default="0.01,0.02,0.03")
    parser.add_argument("--grid-hold-values", default="1,2")
    parser.add_argument("--grid-pullback-fractions", default="0.65,0.75,0.85")
    parser.add_argument("--grid-exhaustion-profiles", default="none")
    parser.add_argument("--grid-exit-rules", default="structural_trail")
    return parser


def config_from_args(args: argparse.Namespace) -> AnomalyBacktestConfig:
    lab_config = AnomalyLabConfig(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        timeframe=args.setup_timeframe or args.timeframe,
        days=args.days,
        end_timestamp_ms=args.end_timestamp_ms,
        baseline_candles=args.baseline_candles,
        confirmation_candles=args.confirmation_candles,
        forward_high_candles=args.forward_high_candles,
        forward_low_candles=args.forward_low_candles,
        min_quote_ratio_start=args.min_quote_ratio_start,
        min_trade_ratio_start=args.min_trade_ratio_start,
    )
    return AnomalyBacktestConfig(
        lab_config=lab_config,
        setup_timeframe=args.setup_timeframe or args.timeframe,
        entry_timeframe=args.entry_timeframe or args.setup_timeframe or args.timeframe,
        feature_contract=(
            "htf_setup_ltf_entry_v1"
            if (args.entry_timeframe or args.setup_timeframe or args.timeframe) != (args.setup_timeframe or args.timeframe)
            else "closed_setup_tf_v1"
        ),
        min_price_retention=args.min_price_retention,
        max_price_retention=args.max_price_retention,
        min_verticality_score=args.min_verticality_score,
        min_hold_count=args.min_hold_count,
        min_oi_change_pct_3x5m=args.min_oi_change_pct_3x5m,
        require_oi_status_ok=bool(args.require_oi_status_ok),
        exhaustion_profile=args.exhaustion_profile,
        max_start_quote_ratio=args.max_start_quote_ratio,
        max_start_trade_ratio=args.max_start_trade_ratio,
        max_start_avg_trade_quote_size_ratio=args.max_start_avg_trade_quote_size_ratio,
        max_start_quote_ratio_per_abs_return=args.max_start_quote_ratio_per_abs_return,
        max_start_trade_ratio_per_abs_return=args.max_start_trade_ratio_per_abs_return,
        max_start_range_pct_ratio_to_baseline=args.max_start_range_pct_ratio_to_baseline,
        min_runner_shape_quote_ratio=args.min_runner_shape_quote_ratio,
        min_runner_shape_trade_ratio=args.min_runner_shape_trade_ratio,
        min_runner_shape_range_ratio=args.min_runner_shape_range_ratio,
        min_runner_shape_quote_acceleration=args.min_runner_shape_quote_acceleration,
        min_runner_shape_trade_acceleration=args.min_runner_shape_trade_acceleration,
        min_runner_shape_range_acceleration=args.min_runner_shape_range_acceleration,
        min_runner_shape_second_half_return_pct=args.min_runner_shape_second_half_return_pct,
        max_runner_shape_top1_quote_share=args.max_runner_shape_top1_quote_share,
        max_prior_up_down_whipsaw_to_impulse_range=args.max_prior_up_down_whipsaw_to_impulse_range,
        min_flow_hold_count=args.min_flow_hold_count,
        max_prior_spike_count_72h=args.max_prior_spike_count_72h,
        max_prior_fast_fade_count_72h=args.max_prior_fast_fade_count_72h,
        min_start_lower_wick_to_range=args.min_start_lower_wick_to_range,
        max_start_upper_wick_to_range=args.max_start_upper_wick_to_range,
        min_next_taker_buy_quote_share=args.min_next_taker_buy_quote_share,
        red_flag_profile=args.red_flag_profile,
        min_mark_close_vs_decision_close_basis=args.min_mark_close_vs_decision_close_basis,
        reject_oi_down_mark_discount=bool(args.reject_oi_down_mark_discount),
        reject_stale_derivatives_context=bool(args.reject_stale_derivatives_context),
        max_start_taker_buy_quote_share_delta=args.max_start_taker_buy_quote_share_delta,
        max_next_taker_buy_quote_share_delta=args.max_next_taker_buy_quote_share_delta,
        max_initial_risk_pct=args.max_initial_risk_pct,
        entry_method=args.entry_method,
        pullback_box_fraction=args.pullback_box_fraction,
        entry_timeout_candles=args.entry_timeout_candles,
        market_entry_latency_candles=args.market_entry_latency_candles,
        latency_enabled=str(args.latency).lower() == "true",
        latency_extra_ms=int(DEFAULT_LATENCY_EXTRA_MS),
        max_market_entry_drift_pct=args.max_market_entry_drift_pct,
        min_market_rr_to_signal_tp1=args.min_market_rr_to_signal_tp1,
        tp1_r=args.tp1_r,
        tp1_fraction=args.tp1_fraction,
        trail_lookback_candles=args.trail_lookback_candles,
        trail_buffer_r=args.trail_buffer_r,
        exit_rule=args.exit_rule,
        max_hold_candles=args.max_hold_candles,
        max_open_positions=args.max_open_positions,
        fee_rate=args.fee_rate,
        entry_slippage_pct=args.entry_slippage_pct,
        exit_slippage_pct=args.exit_slippage_pct,
        write_prepump_context=str(args.write_prepump_context).lower() == "true",
        prepump_context_timeframe=str(args.prepump_context_timeframe),
        prepump_context_windows=str(args.prepump_context_windows),
        prepump_context_min_coverage_ratio=float(args.prepump_context_min_coverage_ratio),
        symbol_workers=int(args.backtest_symbol_workers),
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = run_anomaly_strategy_backtest(
        config_from_args(args),
        symbols=args.symbols,
        run_entry_grid=bool(args.run_entry_grid),
        grid_oi3_values=_parse_grid_values(args.grid_oi3_values, cast=float),
        grid_hold_values=_parse_grid_values(args.grid_hold_values, cast=int),
        grid_pullback_fractions=_parse_grid_values(args.grid_pullback_fractions, cast=float),
        grid_exhaustion_profiles=_parse_grid_profile_values(args.grid_exhaustion_profiles),
        grid_exit_rules=_parse_grid_exit_rules(args.grid_exit_rules),
        run_latency_grid=str(args.latency).lower() == "true",
        latency_grid_ms=DEFAULT_LATENCY_GRID_MS,
        render_charts=str(args.render_charts).lower() == "true",
    )
    print(f"wrote anomaly strategy artifacts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
