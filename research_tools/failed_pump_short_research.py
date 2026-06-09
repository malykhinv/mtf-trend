"""Cache-only failed-pump short research with rolling walk-forward OOS.

The module is intentionally isolated from the long anomaly optimizer.  It builds
short setups from broad 5m pump/awakening candles, confirms failure on closed 1m
candles, uses only closed 5m OI buckets as-of the confirmation close, and writes
research artifacts that are shaped to be reusable for later live/backtest parity.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import math
import os
from pathlib import Path
import time
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from constants import DEFAULT_CACHE_DIR, DEFAULT_RESULTS_DIR
from data.storage.parquet_storage import ParquetLoadResult, ParquetStorage
from domain.enums.timeframe import Timeframe
from utils.symbols import normalize_symbol


MINUTE_MS = 60_000
FIVE_MINUTE_MS = 5 * MINUTE_MS
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS

RESEARCH_ID = "failed_pump_short_research_v1"
ENTRY_MODEL = "next_1m_open_after_confirm_plus_slippage"
OI_MODEL = "closed_5m_oi_asof_confirm_close"
DATA_ACCESS_MODEL = "cache_only_no_exchange_fetch"

SIGNAL_FAMILIES = (
    "oi_drop_position_exit",
    "oi_rise_fresh_shorts",
    "no_oi_confirmation",
)
EXIT_POLICIES = (
    "short_trail_all_lower_highs",
    "short_tp0p75r_close50_trail",
    "short_tp1r_close50_trail",
)
SIGNAL_VARIANTS = (
    "structural_low_break",
    "simple_pump_fade_without_low_break_audit",
)
PUMP_TIERS = (
    "broad",
    "strong_pump",
    "anomaly_pump",
    "extreme_pump",
)
OPPORTUNITY_THRESHOLDS_R = (0.50, 0.75, 1.00, 1.50, 2.00, 3.00, 5.00)
SESSION_BUCKETS = (
    "asia_only",
    "asia_europe_overlap",
    "europe_only",
    "europe_us_overlap",
    "us_only",
    "off_session",
)
ROLLING_WINDOWS = (15, 30, 60)
PARTIAL_FLUSH_SYMBOLS = 25
PARTIAL_FLUSH_SECONDS = 10.0
PARTIAL_ARTIFACT_FILES = {
    "setups": "failed_pump_short_setups.partial.csv",
    "signals": "failed_pump_short_signals.partial.csv",
    "trades": "failed_pump_short_trade_grid.partial.csv",
    "quality": "failed_pump_short_data_quality.partial.csv",
}
PROGRESS_LOG_FILE = "failed_pump_short_progress.csv"


@dataclass(frozen=True, slots=True)
class FailedPumpShortResearchConfig:
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    output_dir: Path = Path(DEFAULT_RESULTS_DIR) / "failed_pump_short_research"
    days: int = 365
    end_timestamp_ms: int | None = None
    symbol_workers: int = min(4, max(1, os.cpu_count() or 1))
    fail_on_empty_input: bool = True
    cache_read_mode: str = "read_only"
    partial_flush_symbols: int = PARTIAL_FLUSH_SYMBOLS
    partial_flush_seconds: float = PARTIAL_FLUSH_SECONDS

    # Setup discovery: deliberately broad.  These constants are not optimized by
    # the command line; changing them should create a new research_id/config row.
    baseline_5m_candles: int = 288
    broad_min_seed_return_pct: float = 0.005
    broad_min_high_return_pct: float = 0.008
    broad_min_quote_ratio: float = 3.0
    broad_min_trade_ratio: float = 2.0
    strong_pump_min_high_return_pct: float = 0.015
    strong_pump_min_quote_ratio: float = 5.0
    strong_pump_min_trade_ratio: float = 3.0
    anomaly_pump_min_high_return_pct: float = 0.025
    anomaly_pump_min_quote_ratio: float = 8.0
    anomaly_pump_min_trade_ratio: float = 5.0
    extreme_pump_min_high_return_pct: float = 0.040
    extreme_pump_min_quote_ratio: float = 12.0
    extreme_pump_min_trade_ratio: float = 8.0
    setup_cluster_minutes: int = 60

    # Confirmation and execution.
    structural_lookback_minutes: int = 30
    post_pump_observation_minutes: int = 60
    flow_baseline_minutes: int = 30
    weak_close_max_position: float = 0.35
    taker_share_norm_mult: float = 0.90
    max_confirm_taker_buy_share_for_research: float = 0.65
    min_1m_quote_ratio: float = 1.20
    min_1m_trade_ratio: float = 1.00
    oi_change_threshold_pct: float = 0.0015
    oi_strong_change_threshold_pct: float = 0.0040
    simple_fade_min_drawdown_from_pump_high_pct: float = 0.0030
    max_initial_risk_pct: float = 0.08
    structural_stop_buffer_pct: float = 0.0005
    entry_slippage_pct: float = 0.0005
    exit_slippage_pct: float = 0.0005
    fee_rate: float = 0.0004
    trail_lookback_1m: int = 5
    max_hold_minutes: int = 60

    # Rolling selection gates.
    min_train_trades: int = 6
    min_train_active_days: int = 2
    tactical_min_avg_r: float = 0.02
    strong_min_avg_r: float = 0.08
    core_min_avg_r: float = 0.12
    min_positive_active_day_share: float = 0.50
    min_top_trade_independence_pct: float = 0.02
    min_top_symbol_independence_pct: float = 0.20
    shadow_oos_max_rules_per_day: int = 10
    shadow_oos_min_train_trades: int = 6
    shadow_oos_min_train_active_days: int = 2
    shadow_oos_min_train_sum_r: float = 0.0

    def __post_init__(self) -> None:
        if int(self.days) <= 0:
            raise ValueError("days must be > 0")
        if int(self.baseline_5m_candles) <= 0:
            raise ValueError("baseline_5m_candles must be > 0")
        if int(self.post_pump_observation_minutes) <= 0:
            raise ValueError("post_pump_observation_minutes must be > 0")
        if not (0 < float(self.strong_pump_min_high_return_pct) <= float(self.anomaly_pump_min_high_return_pct) <= float(self.extreme_pump_min_high_return_pct)):
            raise ValueError("pump tier high-return thresholds must be positive and ordered")
        if not (float(self.broad_min_quote_ratio) <= float(self.strong_pump_min_quote_ratio) <= float(self.anomaly_pump_min_quote_ratio) <= float(self.extreme_pump_min_quote_ratio)):
            raise ValueError("pump tier quote-ratio thresholds must be ordered")
        if not (float(self.broad_min_trade_ratio) <= float(self.strong_pump_min_trade_ratio) <= float(self.anomaly_pump_min_trade_ratio) <= float(self.extreme_pump_min_trade_ratio)):
            raise ValueError("pump tier trade-ratio thresholds must be ordered")
        if int(self.max_hold_minutes) <= 0:
            raise ValueError("max_hold_minutes must be > 0")
        if int(self.trail_lookback_1m) <= 0:
            raise ValueError("trail_lookback_1m must be > 0")
        if int(self.symbol_workers) <= 0:
            raise ValueError("symbol_workers must be > 0")
        if int(self.partial_flush_symbols) <= 0:
            raise ValueError("partial_flush_symbols must be > 0")
        if float(self.partial_flush_seconds) <= 0:
            raise ValueError("partial_flush_seconds must be > 0")
        if int(self.shadow_oos_max_rules_per_day) <= 0:
            raise ValueError("shadow_oos_max_rules_per_day must be > 0")
        if int(self.shadow_oos_min_train_trades) <= 0:
            raise ValueError("shadow_oos_min_train_trades must be > 0")
        if int(self.shadow_oos_min_train_active_days) <= 0:
            raise ValueError("shadow_oos_min_train_active_days must be > 0")


@dataclass(frozen=True, slots=True)
class _OiLookup:
    timestamp_ms: np.ndarray
    available_ms: np.ndarray
    open_interest: np.ndarray

    @property
    def empty(self) -> bool:
        return len(self.open_interest) == 0


class _ProgressLine:
    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = max(0, int(total))
        self.started_at = time.monotonic()
        self.last_emit_at = 0.0

    def update(self, *, index: int, item: str) -> None:
        now = time.monotonic()
        if now - self.last_emit_at < 0.7 and index < self.total:
            return
        self.last_emit_at = now
        pct = (index / self.total * 100.0) if self.total else 100.0
        elapsed = max(0.001, now - self.started_at)
        eta = (elapsed / max(index, 1)) * max(self.total - index, 0) if self.total else 0.0
        print(
            f"{self.label}: scanning {index}/{self.total} ({pct:5.1f}%) "
            f"symbol={_safe_console_text(item)} eta={_format_duration(eta)}",
            flush=True,
        )

    def finish(self) -> None:
        if self.total:
            print("", flush=True)


def run_failed_pump_short_research(
    config: FailedPumpShortResearchConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str = "failed-pump short research",
) -> Path:
    """Run cache-only short research and write all requested artifacts."""

    started_at = time.monotonic()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    _reset_partial_artifacts(config.output_dir)
    _write_progress_event(
        config.output_dir,
        stage="start",
        message="failed-pump short research started",
        elapsed_seconds=0.0,
    )
    print(f"{progress_label}: output_dir={config.output_dir}", flush=True)
    print(f"{progress_label}: cache_dir={config.cache_dir} mode=read_only", flush=True)

    _print_stage(progress_label, "resolving symbols and cache coverage", started_at)
    selected_symbols = tuple(_resolve_symbols(config.cache_dir, symbols))
    if not selected_symbols:
        _write_progress_event(
            config.output_dir,
            stage="failed",
            message="no symbols with both 1m and 5m cache were found",
            elapsed_seconds=time.monotonic() - started_at,
        )
        raise RuntimeError(
            "No symbols with both 1m and 5m cache were found. "
            f"cache_dir={Path(config.cache_dir)}; cache is not modified by this command."
        )
    storage = ParquetStorage(config.cache_dir)
    cache_coverage = _cache_coverage_summary(config.cache_dir, selected_symbols)
    end_ms = _resolve_end_timestamp_ms(config, cache_coverage)
    start_ms = int(end_ms) - int(config.days) * DAY_MS
    warmup_ms = max(2 * DAY_MS, int(config.baseline_5m_candles) * FIVE_MINUTE_MS)
    load_start_ms = int(start_ms) - int(warmup_ms)
    load_end_ms = int(end_ms) + (int(config.max_hold_minutes) + int(config.post_pump_observation_minutes) + 10) * MINUTE_MS
    print(
        f"{progress_label}: window={_fmt_ts(start_ms)}..{_fmt_ts(end_ms)} "
        f"symbols={len(selected_symbols)} latest_cache_5m={cache_coverage.get('max_5m_time_utc', '')}",
        flush=True,
    )
    _write_progress_event(
        config.output_dir,
        stage="scan_started",
        message=f"symbols={len(selected_symbols)} window={_fmt_ts(start_ms)}..{_fmt_ts(end_ms)}",
        elapsed_seconds=time.monotonic() - started_at,
        symbols=len(selected_symbols),
    )

    progress = _ProgressLine(progress_label, len(selected_symbols))
    all_setups: list[dict[str, object]] = []
    all_signals: list[dict[str, object]] = []
    all_trades: list[dict[str, object]] = []
    all_quality: list[dict[str, object]] = []
    pending_setups: list[dict[str, object]] = []
    pending_signals: list[dict[str, object]] = []
    pending_trades: list[dict[str, object]] = []
    pending_quality: list[dict[str, object]] = []
    last_flush_at = time.monotonic()
    last_flush_index = 0

    def _consume_symbol_result(index: int, symbol: str, result: dict[str, list[dict[str, object]]]) -> None:
        nonlocal last_flush_at, last_flush_index
        progress.update(index=index, item=symbol)
        symbol_setups = result["setups"]
        symbol_signals = result["signals"]
        symbol_trades = result["trades"]
        symbol_quality = result["quality"]
        all_setups.extend(symbol_setups)
        all_signals.extend(symbol_signals)
        all_trades.extend(symbol_trades)
        all_quality.extend(symbol_quality)
        pending_setups.extend(symbol_setups)
        pending_signals.extend(symbol_signals)
        pending_trades.extend(symbol_trades)
        pending_quality.extend(symbol_quality)

        now = time.monotonic()
        should_flush = (
            index == len(selected_symbols)
            or index - last_flush_index >= int(config.partial_flush_symbols)
            or now - last_flush_at >= float(config.partial_flush_seconds)
        )
        if should_flush:
            _flush_partial_artifacts(
                output_dir=config.output_dir,
                setups=pending_setups,
                signals=pending_signals,
                trades=pending_trades,
                quality=pending_quality,
            )
            pending_setups.clear()
            pending_signals.clear()
            pending_trades.clear()
            pending_quality.clear()
            last_flush_index = index
            last_flush_at = now
            _write_progress_event(
                config.output_dir,
                stage="scan_flush",
                message="partial raw artifacts flushed",
                elapsed_seconds=now - started_at,
                symbols=index,
                setups=len(all_setups),
                signals=len(all_signals),
                trades=len(all_trades),
            )
            print(
                f"{progress_label}: flushed partial raw artifacts "
                f"symbols={index}/{len(selected_symbols)} setups={len(all_setups):,} "
                f"signals={len(all_signals):,} trades={len(all_trades):,}",
                flush=True,
            )

    workers = max(1, int(config.symbol_workers))
    print(f"{progress_label}: symbol_workers={workers} cache_mode=read_only", flush=True)
    if workers <= 1:
        for index, symbol in enumerate(selected_symbols, start=1):
            result = _process_symbol(
                symbol=symbol,
                storage=storage,
                config=config,
                start_ms=start_ms,
                end_ms=end_ms,
                load_start_ms=load_start_ms,
                load_end_ms=load_end_ms,
            )
            _consume_symbol_result(index, symbol, result)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            future_to_symbol = {
                executor.submit(
                    _process_symbol_parallel_worker,
                    symbol,
                    Path(config.cache_dir),
                    config,
                    int(start_ms),
                    int(end_ms),
                    int(load_start_ms),
                    int(load_end_ms),
                ): symbol
                for symbol in selected_symbols
            }
            for index, future in enumerate(as_completed(future_to_symbol), start=1):
                symbol = future_to_symbol[future]
                _consume_symbol_result(index, symbol, future.result())
    progress.finish()

    _print_stage(progress_label, "building raw dataframes", started_at)
    setups = pd.DataFrame(all_setups)
    signals = pd.DataFrame(all_signals)
    trade_grid = pd.DataFrame(all_trades)
    quality = pd.DataFrame(all_quality)

    if not trade_grid.empty:
        trade_grid = _prepare_trade_grid_for_reporting(trade_grid)
    if not signals.empty:
        signals = _sort_frame(signals, ["confirm_timestamp_ms", "symbol", "signal_family"])
    if not setups.empty:
        setups = _sort_frame(setups, ["seed_open_ms", "symbol"])
    if not quality.empty:
        quality = _sort_frame(quality, ["symbol"])

    _print_stage(progress_label, "writing raw core artifacts before rolling", started_at)
    _write_csv(config.output_dir / "failed_pump_short_setups.csv", setups)
    _write_csv(config.output_dir / "failed_pump_short_signals.csv", signals)
    _write_csv(config.output_dir / "failed_pump_short_trade_grid.csv", trade_grid)
    _write_csv(config.output_dir / "failed_pump_short_data_quality.csv", quality)
    _write_progress_event(
        config.output_dir,
        stage="raw_core_written",
        message="setups/signals/trade_grid/data_quality were written before summaries and rolling",
        elapsed_seconds=time.monotonic() - started_at,
        setups=len(setups),
        signals=len(signals),
        trades=len(trade_grid),
    )
    print(
        f"{progress_label}: raw artifacts on disk setups={len(setups):,} "
        f"signals={len(signals):,} trades={len(trade_grid):,}",
        flush=True,
    )

    _fail_fast_on_empty_input(config=config, quality=quality)

    _print_stage(progress_label, "building summary metrics", started_at)
    by_family = _group_metrics(trade_grid, ["signal_family"])
    by_session = _group_metrics(trade_grid, ["session_bucket"])
    by_session_family = _group_metrics(trade_grid, ["session_bucket", "signal_family"])
    by_exit = _group_metrics(trade_grid, ["exit_policy"])
    by_signal_variant = _group_metrics(trade_grid, ["signal_variant"])
    by_taker_bucket = _group_metrics(trade_grid, ["confirm_taker_buy_bucket"])
    by_oi_strength = _group_metrics(trade_grid, ["oi_strength_bucket"])
    by_distribution = _group_metrics(trade_grid, ["post_pump_preconfirm_distribution_bucket"])
    by_pump_tier = _group_metrics(trade_grid, ["pump_tier"])
    by_pump_tier_session_family = _group_metrics(trade_grid, ["pump_tier", "session_bucket", "signal_family"])
    opportunity_audit = _opportunity_audit(trade_grid)
    by_day = _daily_metrics(trade_grid)
    top_dependency = _top_dependency_report(trade_grid)

    _print_stage(progress_label, "running rolling walk-forward protocol", started_at)
    rolling = _run_rolling_protocol(trade_grid, config=config)
    _write_progress_event(
        config.output_dir,
        stage="rolling_done",
        message="rolling walk-forward protocol finished",
        elapsed_seconds=time.monotonic() - started_at,
        oos_trades=len(rolling.get("oos_trades", pd.DataFrame())),
    )

    _print_stage(progress_label, "building run config and final artifacts", started_at)
    run_config = _run_config_frame(
        config=config,
        selected_symbols=selected_symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        started_at=started_at,
        setups=setups,
        signals=signals,
        trade_grid=trade_grid,
        quality=quality,
        cache_coverage=cache_coverage,
    )
    rolling_run_config = _rolling_run_config_frame(config=config, trade_grid=trade_grid, rolling=rolling)

    # Raw core artifacts were already written before rolling.  Rewriting the large
    # setup/signal/trade CSVs here doubles final IO on 365d without changing
    # content, so finalization writes only derived artifacts.
    _write_csv(config.output_dir / "failed_pump_short_by_signal_family.csv", by_family)
    _write_csv(config.output_dir / "failed_pump_short_by_session.csv", by_session)
    _write_csv(config.output_dir / "failed_pump_short_by_session_family.csv", by_session_family)
    _write_csv(config.output_dir / "failed_pump_short_by_exit_policy.csv", by_exit)
    _write_csv(config.output_dir / "failed_pump_short_by_signal_variant.csv", by_signal_variant)
    _write_csv(config.output_dir / "failed_pump_short_by_taker_bucket.csv", by_taker_bucket)
    _write_csv(config.output_dir / "failed_pump_short_by_oi_strength.csv", by_oi_strength)
    _write_csv(config.output_dir / "failed_pump_short_by_distribution.csv", by_distribution)
    _write_csv(config.output_dir / "failed_pump_short_by_pump_tier.csv", by_pump_tier)
    _write_csv(config.output_dir / "failed_pump_short_by_pump_tier_session_family.csv", by_pump_tier_session_family)
    _write_csv(config.output_dir / "failed_pump_short_opportunity_audit.csv", opportunity_audit)
    _write_csv(config.output_dir / "failed_pump_short_by_day.csv", by_day)
    _write_csv(config.output_dir / "failed_pump_short_top_dependency.csv", top_dependency)
    _write_csv(config.output_dir / "failed_pump_short_data_quality.csv", quality)
    _write_csv(config.output_dir / "failed_pump_short_run_config.csv", run_config)

    _write_csv(config.output_dir / "failed_pump_short_rolling_daily_summary.csv", rolling["daily_summary"])
    _write_csv(config.output_dir / "failed_pump_short_rolling_rule_health.csv", rolling["rule_health"])
    _write_csv(config.output_dir / "failed_pump_short_rolling_oos_trades.csv", rolling["oos_trades"])
    _write_csv(config.output_dir / "failed_pump_short_rolling_window_health.csv", rolling["window_health"])
    _write_csv(config.output_dir / "failed_pump_short_rolling_selection_drift.csv", rolling["selection_drift"])
    _write_csv(config.output_dir / "failed_pump_short_rolling_fluctuation_stress.csv", rolling["fluctuation_stress"])
    _write_csv(config.output_dir / "failed_pump_short_rolling_shadow_oos_audit.csv", rolling["shadow_oos_audit"])
    _write_csv(config.output_dir / "failed_pump_short_rolling_run_config.csv", rolling_run_config)
    _write_progress_event(
        config.output_dir,
        stage="finished",
        message="all final artifacts written",
        elapsed_seconds=time.monotonic() - started_at,
        setups=len(setups),
        signals=len(signals),
        trades=len(trade_grid),
    )
    print(f"{progress_label}: all final artifacts written in {_format_duration(time.monotonic() - started_at)}", flush=True)

    return config.output_dir



def _process_symbol_parallel_worker(
    symbol: str,
    cache_dir: Path,
    config: FailedPumpShortResearchConfig,
    start_ms: int,
    end_ms: int,
    load_start_ms: int,
    load_end_ms: int,
) -> dict[str, list[dict[str, object]]]:
    # Worker processes only read parquet cache.  They never write artifacts or
    # mutate .output/cache; all output flushing remains in the parent process.
    storage = ParquetStorage(Path(cache_dir))
    return _process_symbol(
        symbol=symbol,
        storage=storage,
        config=config,
        start_ms=int(start_ms),
        end_ms=int(end_ms),
        load_start_ms=int(load_start_ms),
        load_end_ms=int(load_end_ms),
    )

def _process_symbol(
    *,
    symbol: str,
    storage: ParquetStorage,
    config: FailedPumpShortResearchConfig,
    start_ms: int,
    end_ms: int,
    load_start_ms: int,
    load_end_ms: int,
) -> dict[str, list[dict[str, object]]]:
    quality: list[dict[str, object]] = []
    frame_5m_result = _load_frame_result(storage, symbol, "5m", start_ms=load_start_ms, end_ms=load_end_ms)
    frame_1m_result = _load_frame_result(storage, symbol, "1m", start_ms=load_start_ms, end_ms=load_end_ms)
    oi_5m_result = frame_5m_result
    frame_5m_raw = frame_5m_result.frame if frame_5m_result.ok else pd.DataFrame()
    frame_1m_raw = frame_1m_result.frame if frame_1m_result.ok else pd.DataFrame()
    oi_5m_raw = oi_5m_result.frame if oi_5m_result.ok else pd.DataFrame()
    # OI is stored in the same 5m cache frame as an extra column when available.
    # These calls are read-only; no exchange clients or cache write APIs are used.

    qrow = _data_quality_row(
        symbol=symbol,
        frame_5m=frame_5m_raw,
        frame_1m=frame_1m_raw,
        oi_5m=oi_5m_raw,
        frame_5m_result=frame_5m_result,
        frame_1m_result=frame_1m_result,
        oi_5m_result=oi_5m_result,
        load_start_ms=load_start_ms,
        load_end_ms=load_end_ms,
    )
    quality.append(qrow)

    if frame_5m_raw.empty or frame_1m_raw.empty:
        qrow["data_rejection"] = "missing_5m_or_1m_cache"
        return {"setups": [], "signals": [], "trades": [], "quality": quality}

    frame_5m = _prepare_ohlcv(frame_5m_raw)
    frame_1m = _prepare_ohlcv(frame_1m_raw)
    oi_5m = _prepare_oi(oi_5m_raw)
    oi_lookup = _prepare_oi_lookup(oi_5m)

    missing_required_5m = sorted(set(["timestamp", "open", "high", "low", "close"]) - set(frame_5m.columns))
    missing_required_1m = sorted(set(["timestamp", "open", "high", "low", "close"]) - set(frame_1m.columns))
    if missing_required_5m or missing_required_1m:
        qrow["data_rejection"] = f"missing_ohlcv_columns_5m={missing_required_5m}_1m={missing_required_1m}"
        return {"setups": [], "signals": [], "trades": [], "quality": quality}

    if not _has_real_flow(frame_5m) or not _has_real_flow(frame_1m):
        qrow["data_warning"] = "missing_real_quote_or_trade_flow_columns; confirmations requiring flow will be unavailable"

    setups = _collect_broad_pump_setups(symbol=symbol, frame_5m=frame_5m, start_ms=start_ms, end_ms=end_ms, config=config)
    setup_rows: list[dict[str, object]] = []
    signal_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    for setup in setups:
        enriched = _enrich_setup_with_1m_structure(setup, frame_1m=frame_1m, config=config)
        setup_rows.append(enriched)
        if str(enriched.get("enrichment_status")) != "ok":
            continue
        signals = [
            signal
            for signal in (
                _find_first_failed_pump_signal(enriched, frame_1m=frame_1m, oi_5m=oi_lookup, config=config),
                _find_first_simple_pump_fade_signal(enriched, frame_1m=frame_1m, oi_5m=oi_lookup, config=config),
            )
            if signal is not None
        ]
        for signal in signals:
            signal_rows.append(signal)
            trade_rows.extend(_simulate_signal_trade_grid(signal, frame_1m=frame_1m, config=config))

    qrow.update(
        {
            "setups": int(len(setup_rows)),
            "signals": int(len(signal_rows)),
            "trades": int(len(trade_rows)),
            "data_rejection": qrow.get("data_rejection", ""),
        }
    )
    return {"setups": setup_rows, "signals": signal_rows, "trades": trade_rows, "quality": quality}


def _collect_broad_pump_setups(
    *,
    symbol: str,
    frame_5m: pd.DataFrame,
    start_ms: int,
    end_ms: int,
    config: FailedPumpShortResearchConfig,
) -> list[dict[str, object]]:
    if frame_5m.empty:
        return []
    rows: list[dict[str, object]] = []
    frame = frame_5m.sort_values("timestamp").reset_index(drop=True)
    quote = _numeric_series(frame, "quote_volume")
    trades = _numeric_series(frame, "number_of_trades")
    close = _numeric_series(frame, "close")
    open_ = _numeric_series(frame, "open")
    high = _numeric_series(frame, "high")
    low = _numeric_series(frame, "low")
    ts = _numeric_series(frame, "timestamp").astype("int64")
    baseline = int(config.baseline_5m_candles)
    if len(frame) <= baseline:
        return []

    # Critical speed path for 365d: compute rolling baselines once instead of
    # taking a fresh median slice for every candidate candle.  This preserves the
    # no-lookahead contract because the median is shifted by one closed 5m bar.
    quote_baseline = quote.rolling(window=baseline, min_periods=baseline).median().shift(1)
    trade_baseline = trades.rolling(window=baseline, min_periods=baseline).median().shift(1)

    last_seed_ms: int | None = None
    cluster_ms = int(config.setup_cluster_minutes) * MINUTE_MS

    for i in range(baseline, len(frame)):
        seed_open_ms = int(ts.iat[i])
        if seed_open_ms < int(start_ms) or seed_open_ms > int(end_ms):
            continue
        if last_seed_ms is not None and seed_open_ms - last_seed_ms < cluster_ms:
            continue
        o = float(open_.iat[i])
        h = float(high.iat[i])
        l = float(low.iat[i])
        c = float(close.iat[i])
        if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
            continue
        seed_return_pct = c / o - 1.0
        high_return_pct = h / o - 1.0
        if c <= o:
            continue
        if seed_return_pct < float(config.broad_min_seed_return_pct) and high_return_pct < float(config.broad_min_high_return_pct):
            continue
        q_med = _float(quote_baseline.iat[i])
        t_med = _float(trade_baseline.iat[i])
        q_now = float(quote.iat[i]) if math.isfinite(float(quote.iat[i])) else 0.0
        t_now = float(trades.iat[i]) if math.isfinite(float(trades.iat[i])) else 0.0
        quote_ratio = q_now / q_med if q_med > 0 else 0.0
        trade_ratio = t_now / t_med if t_med > 0 else 0.0
        if quote_ratio < float(config.broad_min_quote_ratio) or trade_ratio < float(config.broad_min_trade_ratio):
            continue
        pump_tier = _pump_tier(
            high_return_pct=high_return_pct,
            quote_ratio=quote_ratio,
            trade_ratio=trade_ratio,
            config=config,
        )
        setup_id = f"{_compact_symbol(symbol)}:{seed_open_ms}"
        last_seed_ms = seed_open_ms
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "setup_id": setup_id,
                "symbol": symbol,
                "seed_open_ms": seed_open_ms,
                "seed_close_ms": seed_open_ms + FIVE_MINUTE_MS,
                "seed_open_time_utc": _fmt_ts(seed_open_ms),
                "seed_close_time_utc": _fmt_ts(seed_open_ms + FIVE_MINUTE_MS),
                "seed_open": o,
                "seed_high": h,
                "seed_low": l,
                "seed_close": c,
                "pump_high": h,
                "pump_tier": pump_tier,
                "is_strong_pump_tier": pump_tier in {"strong_pump", "anomaly_pump", "extreme_pump"},
                "is_anomaly_pump_tier": pump_tier in {"anomaly_pump", "extreme_pump"},
                "is_extreme_pump_tier": pump_tier == "extreme_pump",
                "seed_return_pct": seed_return_pct,
                "seed_high_return_pct": high_return_pct,
                "seed_range_pct": h / l - 1.0 if l > 0 else np.nan,
                "seed_quote_volume": q_now,
                "seed_number_of_trades": t_now,
                "seed_quote_ratio": quote_ratio,
                "seed_trade_ratio": trade_ratio,
                "setup_model": "broad_5m_pump_awakening_cache_only_with_pump_tiers",
                "future_label_available_at_entry": False,
                "short_confirm_closed_before_entry": True,
                "entry_model": ENTRY_MODEL,
                "oi_model": OI_MODEL,
            }
        )
    return rows

def _enrich_setup_with_1m_structure(
    setup: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    config: FailedPumpShortResearchConfig,
) -> dict[str, object]:
    seed_open_ms = int(setup["seed_open_ms"])
    seed_close_ms = int(setup["seed_close_ms"])
    lookback_start = seed_open_ms - int(config.structural_lookback_minutes) * MINUTE_MS
    struct = _time_window(frame_1m, start_ms=lookback_start, end_ms=seed_close_ms, include_end=False).copy()
    enriched = dict(setup)
    if struct.empty:
        enriched.update({"enrichment_status": "missing_1m_structural_window"})
        return enriched
    lows = _numeric_series(struct, "low")
    highs = _numeric_series(struct, "high")
    if lows.dropna().empty or highs.dropna().empty:
        enriched.update({"enrichment_status": "invalid_1m_structural_prices"})
        return enriched
    low_idx = lows.idxmin()
    high_idx = highs.idxmax()
    structural_low = float(lows.loc[low_idx])
    structural_high = float(highs.loc[high_idx])
    low_ts = int(struct.loc[low_idx, "timestamp"])
    high_ts = int(struct.loc[high_idx, "timestamp"])
    seed_1m = _time_window(frame_1m, start_ms=seed_open_ms, end_ms=seed_close_ms, include_end=False)
    seed_shape = _seed_1m_shape_features(seed_1m, seed_open_ms=seed_open_ms, seed_close_ms=seed_close_ms)
    enriched.update(
        {
            "enrichment_status": "ok",
            "structural_low": structural_low,
            "structural_low_timestamp_ms": low_ts,
            "structural_low_time_utc": _fmt_ts(low_ts),
            "prepump_structural_high": structural_high,
            "prepump_structural_high_timestamp_ms": high_ts,
            "prepump_structural_high_time_utc": _fmt_ts(high_ts),
            "post_pump_observation_start_ms": seed_close_ms,
            "post_pump_observation_end_ms": seed_close_ms + int(config.post_pump_observation_minutes) * MINUTE_MS,
            **seed_shape,
        }
    )
    return enriched


def _find_first_failed_pump_signal(
    setup: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    oi_5m: pd.DataFrame,
    config: FailedPumpShortResearchConfig,
) -> dict[str, object] | None:
    structural_low = float(setup.get("structural_low", np.nan))
    if not math.isfinite(structural_low) or structural_low <= 0:
        return None
    start = int(setup["post_pump_observation_start_ms"])
    end = int(setup["post_pump_observation_end_ms"])
    window = _time_window(frame_1m, start_ms=start, end_ms=end, include_end=False)
    if len(window) < 2:
        return None
    flow_baseline_start = max(int(setup["seed_open_ms"]) - int(config.flow_baseline_minutes) * MINUTE_MS, 0)
    flow_baseline = _time_window(frame_1m, start_ms=flow_baseline_start, end_ms=start, include_end=False)
    baseline_taker_share = _safe_median(_taker_buy_share(flow_baseline))
    baseline_quote = _safe_median(_numeric_series(flow_baseline, "quote_volume"))
    baseline_trades = _safe_median(_numeric_series(flow_baseline, "number_of_trades"))
    if baseline_taker_share <= 0 or baseline_quote <= 0 or baseline_trades <= 0:
        return None

    for pos in range(0, len(window) - 1):
        candle = window.iloc[pos]
        confirm_open_ms = int(candle["timestamp"])
        confirm_close_ms = confirm_open_ms + MINUTE_MS
        o = _float(candle.get("open"))
        h = _float(candle.get("high"))
        l = _float(candle.get("low"))
        c = _float(candle.get("close"))
        if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
            continue
        if c >= structural_low:
            continue
        if c >= o:
            continue
        range_abs = max(h - l, 0.0)
        close_pos = (c - l) / range_abs if range_abs > 0 else 1.0
        if close_pos > float(config.weak_close_max_position):
            continue
        quote_now = _float(candle.get("quote_volume"))
        trades_now = _float(candle.get("number_of_trades"))
        quote_ratio = quote_now / baseline_quote if baseline_quote > 0 else 0.0
        trade_ratio = trades_now / baseline_trades if baseline_trades > 0 else 0.0
        if quote_ratio < float(config.min_1m_quote_ratio) or trade_ratio < float(config.min_1m_trade_ratio):
            continue
        taker_share = _candle_taker_buy_share(candle)
        taker_threshold = float(config.max_confirm_taker_buy_share_for_research)
        if not math.isfinite(taker_share) or taker_share > taker_threshold:
            continue

        oi_context = _closed_5m_oi_context(
            oi_5m,
            asof_timestamp_ms=confirm_close_ms,
            threshold_pct=float(config.oi_change_threshold_pct),
            strong_threshold_pct=float(config.oi_strong_change_threshold_pct),
        )
        family = str(oi_context["signal_family"])
        next_row = window.iloc[pos + 1]
        entry_open_ms = int(next_row["timestamp"])
        if entry_open_ms < confirm_close_ms:
            continue
        entry_open = _float(next_row.get("open"))
        if not math.isfinite(entry_open) or entry_open <= 0:
            continue
        structural_high_window = _time_window(
            frame_1m,
            start_ms=int(setup["seed_open_ms"]),
            end_ms=confirm_open_ms,
            include_end=True,
        )
        last_structural_high = _safe_max(_numeric_series(structural_high_window, "high"))
        if last_structural_high <= 0:
            last_structural_high = max(float(setup.get("pump_high", 0.0)), h)
        stop_price = max(last_structural_high, h, entry_open) * (1.0 + float(config.structural_stop_buffer_pct))
        entry_price = entry_open * (1.0 - float(config.entry_slippage_pct))
        risk_abs = stop_price - entry_price
        if risk_abs <= 0:
            continue
        initial_risk_pct = risk_abs / entry_price
        if initial_risk_pct > float(config.max_initial_risk_pct):
            continue

        session = _session_features(entry_open_ms)
        shape = _post_pump_signal_shape_features(setup, frame_1m=frame_1m, confirm_open_ms=confirm_open_ms, confirm_close=c)
        signal_variant = "structural_low_break"
        signal_id = f"{setup['setup_id']}:{confirm_open_ms}:{signal_variant}:{family}"
        return {
            **_honesty_fields(),
            "research_id": RESEARCH_ID,
            "signal_id": signal_id,
            "setup_id": setup["setup_id"],
            "symbol": setup["symbol"],
            "signal_variant": signal_variant,
            "selection_eligible": True,
            "seed_open_ms": int(setup["seed_open_ms"]),
            "seed_close_ms": int(setup["seed_close_ms"]),
            "confirm_timestamp_ms": confirm_open_ms,
            "confirm_close_timestamp_ms": confirm_close_ms,
            "confirm_time_utc": _fmt_ts(confirm_open_ms),
            "confirm_close_time_utc": _fmt_ts(confirm_close_ms),
            "entry_timestamp_ms": entry_open_ms,
            "entry_time_utc": _fmt_ts(entry_open_ms),
            "signal_family": family,
            "session_bucket": session["session_bucket"],
            "session_primary": session["session_primary"],
            "session_overlap": session["session_overlap"],
            "hour_utc": session["hour_utc"],
            "weekday": session["weekday"],
            "structural_low": structural_low,
            "last_structural_high": last_structural_high,
            "stop_price": stop_price,
            "entry_open": entry_open,
            "entry_price": entry_price,
            "initial_risk_abs": risk_abs,
            "initial_risk_pct": initial_risk_pct,
            "confirm_open": o,
            "confirm_high": h,
            "confirm_low": l,
            "confirm_close": c,
            "confirm_return_pct": c / o - 1.0 if o > 0 else np.nan,
            "confirm_close_position": close_pos,
            "confirm_quote_volume": quote_now,
            "confirm_number_of_trades": trades_now,
            "confirm_quote_ratio_vs_norm": quote_ratio,
            "confirm_trade_ratio_vs_norm": trade_ratio,
            "confirm_taker_buy_share": taker_share,
            "confirm_taker_buy_share_norm": baseline_taker_share,
            "confirm_taker_buy_share_threshold": taker_threshold,
            "confirm_taker_buy_bucket": _taker_buy_bucket(taker_share),
            "confirm_taker_buy_vs_norm_pct": taker_share - baseline_taker_share,
            "confirm_taker_buy_norm_ratio": taker_share / baseline_taker_share if baseline_taker_share > 0 else np.nan,
            "confirm_taker_buy_below_norm_flag": bool(taker_share <= baseline_taker_share * float(config.taker_share_norm_mult)),
            **oi_context,
            **shape,
            **_setup_shape_passthrough(setup),
        }
    return None


def _find_first_simple_pump_fade_signal(
    setup: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    oi_5m: pd.DataFrame,
    config: FailedPumpShortResearchConfig,
) -> dict[str, object] | None:
    """Control-group fade after pump without a structural-low break.

    This is intentionally marked ``selection_eligible=False``.  It is written to
    artifacts and simulated with the same execution model so that the structural
    break edge can be compared against a non-break fade baseline without letting
    the rolling selector promote the audit baseline into a trading candidate.
    """

    structural_low = float(setup.get("structural_low", np.nan))
    pump_high = float(setup.get("pump_high", setup.get("seed_high", np.nan)))
    if not math.isfinite(structural_low) or structural_low <= 0 or not math.isfinite(pump_high) or pump_high <= 0:
        return None
    start = int(setup["post_pump_observation_start_ms"])
    end = int(setup["post_pump_observation_end_ms"])
    window = _time_window(frame_1m, start_ms=start, end_ms=end, include_end=False)
    if len(window) < 2:
        return None
    flow_baseline_start = max(int(setup["seed_open_ms"]) - int(config.flow_baseline_minutes) * MINUTE_MS, 0)
    flow_baseline = _time_window(frame_1m, start_ms=flow_baseline_start, end_ms=start, include_end=False)
    baseline_taker_share = _safe_median(_taker_buy_share(flow_baseline))
    baseline_quote = _safe_median(_numeric_series(flow_baseline, "quote_volume"))
    baseline_trades = _safe_median(_numeric_series(flow_baseline, "number_of_trades"))
    if baseline_taker_share <= 0 or baseline_quote <= 0 or baseline_trades <= 0:
        return None

    for pos in range(0, len(window) - 1):
        candle = window.iloc[pos]
        confirm_open_ms = int(candle["timestamp"])
        confirm_close_ms = confirm_open_ms + MINUTE_MS
        o = _float(candle.get("open"))
        h = _float(candle.get("high"))
        l = _float(candle.get("low"))
        c = _float(candle.get("close"))
        if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
            continue
        if c < structural_low:
            continue
        if c >= o:
            continue
        drawdown_from_pump_high = c / pump_high - 1.0
        if abs(drawdown_from_pump_high) < float(config.simple_fade_min_drawdown_from_pump_high_pct):
            continue
        range_abs = max(h - l, 0.0)
        close_pos = (c - l) / range_abs if range_abs > 0 else 1.0
        if close_pos > float(config.weak_close_max_position):
            continue
        quote_now = _float(candle.get("quote_volume"))
        trades_now = _float(candle.get("number_of_trades"))
        quote_ratio = quote_now / baseline_quote if baseline_quote > 0 else 0.0
        trade_ratio = trades_now / baseline_trades if baseline_trades > 0 else 0.0
        if quote_ratio < float(config.min_1m_quote_ratio) or trade_ratio < float(config.min_1m_trade_ratio):
            continue
        taker_share = _candle_taker_buy_share(candle)
        taker_threshold = float(config.max_confirm_taker_buy_share_for_research)
        if not math.isfinite(taker_share) or taker_share > taker_threshold:
            continue

        oi_context = _closed_5m_oi_context(
            oi_5m,
            asof_timestamp_ms=confirm_close_ms,
            threshold_pct=float(config.oi_change_threshold_pct),
            strong_threshold_pct=float(config.oi_strong_change_threshold_pct),
        )
        family = str(oi_context["signal_family"])
        next_row = window.iloc[pos + 1]
        entry_open_ms = int(next_row["timestamp"])
        if entry_open_ms < confirm_close_ms:
            continue
        entry_open = _float(next_row.get("open"))
        if not math.isfinite(entry_open) or entry_open <= 0:
            continue
        structural_high_window = _time_window(
            frame_1m,
            start_ms=int(setup["seed_open_ms"]),
            end_ms=confirm_open_ms,
            include_end=True,
        )
        last_structural_high = _safe_max(_numeric_series(structural_high_window, "high"))
        if last_structural_high <= 0:
            last_structural_high = max(pump_high, h)
        stop_price = max(last_structural_high, h, entry_open) * (1.0 + float(config.structural_stop_buffer_pct))
        entry_price = entry_open * (1.0 - float(config.entry_slippage_pct))
        risk_abs = stop_price - entry_price
        if risk_abs <= 0:
            continue
        initial_risk_pct = risk_abs / entry_price
        if initial_risk_pct > float(config.max_initial_risk_pct):
            continue

        session = _session_features(entry_open_ms)
        shape = _post_pump_signal_shape_features(setup, frame_1m=frame_1m, confirm_open_ms=confirm_open_ms, confirm_close=c)
        signal_variant = "simple_pump_fade_without_low_break_audit"
        signal_id = f"{setup['setup_id']}:{confirm_open_ms}:{signal_variant}:{family}"
        return {
            **_honesty_fields(),
            "research_id": RESEARCH_ID,
            "signal_id": signal_id,
            "setup_id": setup["setup_id"],
            "symbol": setup["symbol"],
            "signal_variant": signal_variant,
            "selection_eligible": False,
            "seed_open_ms": int(setup["seed_open_ms"]),
            "seed_close_ms": int(setup["seed_close_ms"]),
            "confirm_timestamp_ms": confirm_open_ms,
            "confirm_close_timestamp_ms": confirm_close_ms,
            "confirm_time_utc": _fmt_ts(confirm_open_ms),
            "confirm_close_time_utc": _fmt_ts(confirm_close_ms),
            "entry_timestamp_ms": entry_open_ms,
            "entry_time_utc": _fmt_ts(entry_open_ms),
            "signal_family": family,
            "session_bucket": session["session_bucket"],
            "session_primary": session["session_primary"],
            "session_overlap": session["session_overlap"],
            "hour_utc": session["hour_utc"],
            "weekday": session["weekday"],
            "structural_low": structural_low,
            "last_structural_high": last_structural_high,
            "stop_price": stop_price,
            "entry_open": entry_open,
            "entry_price": entry_price,
            "initial_risk_abs": risk_abs,
            "initial_risk_pct": initial_risk_pct,
            "confirm_open": o,
            "confirm_high": h,
            "confirm_low": l,
            "confirm_close": c,
            "confirm_return_pct": c / o - 1.0 if o > 0 else np.nan,
            "confirm_close_position": close_pos,
            "confirm_quote_volume": quote_now,
            "confirm_number_of_trades": trades_now,
            "confirm_quote_ratio_vs_norm": quote_ratio,
            "confirm_trade_ratio_vs_norm": trade_ratio,
            "confirm_taker_buy_share": taker_share,
            "confirm_taker_buy_share_norm": baseline_taker_share,
            "confirm_taker_buy_share_threshold": taker_threshold,
            "confirm_taker_buy_bucket": _taker_buy_bucket(taker_share),
            "confirm_taker_buy_vs_norm_pct": taker_share - baseline_taker_share,
            "confirm_taker_buy_norm_ratio": taker_share / baseline_taker_share if baseline_taker_share > 0 else np.nan,
            "confirm_taker_buy_below_norm_flag": bool(taker_share <= baseline_taker_share * float(config.taker_share_norm_mult)),
            **oi_context,
            **shape,
            **_setup_shape_passthrough(setup),
        }
    return None


def _closed_5m_oi_context(
    oi_5m: _OiLookup | pd.DataFrame,
    *,
    asof_timestamp_ms: int,
    threshold_pct: float,
    strong_threshold_pct: float,
) -> dict[str, object]:
    base = {
        "oi_model": OI_MODEL,
        "oi_available": False,
        "oi_current_timestamp_ms": np.nan,
        "oi_current_available_timestamp_ms": np.nan,
        "oi_previous_timestamp_ms": np.nan,
        "oi_two_back_timestamp_ms": np.nan,
        "oi_three_back_timestamp_ms": np.nan,
        "oi_current": np.nan,
        "oi_previous": np.nan,
        "oi_two_back": np.nan,
        "oi_three_back": np.nan,
        "oi_change_pct": np.nan,
        "oi_change_pct_1x5m": np.nan,
        "oi_change_pct_2x5m": np.nan,
        "oi_change_pct_3x5m": np.nan,
        "oi_age_ms": np.nan,
        "oi_status": "missing",
        "oi_strength_bucket": "missing",
        "oi_regime": "missing_closed_5m_oi",
        "signal_family": "no_oi_confirmation",
    }
    lookup = oi_5m if isinstance(oi_5m, _OiLookup) else _prepare_oi_lookup(_prepare_oi(oi_5m))
    if lookup.empty:
        return base
    idx = int(np.searchsorted(lookup.available_ms, int(asof_timestamp_ms), side="right")) - 1
    if idx < 1:
        return base
    oi_current = float(lookup.open_interest[idx])
    oi_previous = float(lookup.open_interest[idx - 1])
    if oi_previous <= 0 or not math.isfinite(oi_current) or not math.isfinite(oi_previous):
        return base
    change_pct = oi_current / oi_previous - 1.0
    oi_two_back = float(lookup.open_interest[idx - 2]) if idx >= 2 else float("nan")
    oi_three_back = float(lookup.open_interest[idx - 3]) if idx >= 3 else float("nan")
    change_pct_2x5m = oi_current / oi_two_back - 1.0 if math.isfinite(oi_two_back) and oi_two_back > 0 else np.nan
    change_pct_3x5m = oi_current / oi_three_back - 1.0 if math.isfinite(oi_three_back) and oi_three_back > 0 else np.nan
    if change_pct <= -abs(threshold_pct):
        regime = "oi_drop_position_exit"
        family = "oi_drop_position_exit"
    elif change_pct >= abs(threshold_pct):
        regime = "oi_rise_fresh_shorts"
        family = "oi_rise_fresh_shorts"
    else:
        regime = "oi_flat_or_below_threshold"
        family = "no_oi_confirmation"
    oi_age_ms = int(asof_timestamp_ms) - int(lookup.available_ms[idx])
    oi_status = "ok" if 0 <= oi_age_ms <= 2 * FIVE_MINUTE_MS else "stale"
    return {
        "oi_model": OI_MODEL,
        "oi_available": True,
        "oi_current_timestamp_ms": int(lookup.timestamp_ms[idx]),
        "oi_current_available_timestamp_ms": int(lookup.available_ms[idx]),
        "oi_previous_timestamp_ms": int(lookup.timestamp_ms[idx - 1]),
        "oi_two_back_timestamp_ms": int(lookup.timestamp_ms[idx - 2]) if idx >= 2 else np.nan,
        "oi_three_back_timestamp_ms": int(lookup.timestamp_ms[idx - 3]) if idx >= 3 else np.nan,
        "oi_current": oi_current,
        "oi_previous": oi_previous,
        "oi_two_back": oi_two_back,
        "oi_three_back": oi_three_back,
        "oi_change_pct": change_pct,
        "oi_change_pct_1x5m": change_pct,
        "oi_change_pct_2x5m": change_pct_2x5m,
        "oi_change_pct_3x5m": change_pct_3x5m,
        "oi_age_ms": oi_age_ms,
        "oi_status": oi_status,
        "oi_strength_bucket": _oi_strength_bucket(change_pct, weak_threshold_pct=threshold_pct, strong_threshold_pct=strong_threshold_pct),
        "oi_regime": regime,
        "signal_family": family,
    }


def _simulate_signal_trade_grid(
    signal: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    config: FailedPumpShortResearchConfig,
) -> list[dict[str, object]]:
    entry_ts = int(signal["entry_timestamp_ms"])
    end_ts = entry_ts + int(config.max_hold_minutes) * MINUTE_MS
    path = _time_window(frame_1m, start_ms=entry_ts, end_ms=end_ts, include_end=True)
    if path.empty:
        return []
    rows: list[dict[str, object]] = []
    for policy in EXIT_POLICIES:
        trade = _simulate_short_trade_on_path(signal, path=path, config=config, exit_policy=policy)
        if trade is not None:
            rows.append(trade)
    return rows



def _simulate_short_trade_on_path(
    signal: dict[str, object],
    *,
    path: pd.DataFrame,
    config: FailedPumpShortResearchConfig,
    exit_policy: str,
) -> dict[str, object] | None:
    entry_ts = int(signal["entry_timestamp_ms"])
    if path.empty:
        return None
    entry_price = float(signal["entry_price"])
    initial_stop = float(signal["stop_price"])
    initial_risk_abs = float(signal["initial_risk_abs"])
    if initial_risk_abs <= 0 or initial_stop <= entry_price:
        return None

    stop = initial_stop
    open_fraction = 1.0
    realized_r = 0.0
    partial_taken = False
    if exit_policy == "short_tp0p75r_close50_trail":
        tp_price = entry_price - 0.75 * initial_risk_abs
    elif exit_policy == "short_tp1r_close50_trail":
        tp_price = entry_price - initial_risk_abs
    else:
        tp_price = np.nan
    mfe_r = 0.0
    mae_r = 0.0
    exit_ts = int(path.iloc[-1]["timestamp"])
    exit_reason = "time_exit_max_hold"
    exit_price = _float(path.iloc[-1].get("close")) * (1.0 + float(config.exit_slippage_pct))
    trail_updates = 0
    stop_first_conservative = False

    for i in range(len(path)):
        candle = path.iloc[i]
        candle_ts = int(candle["timestamp"])
        h = _float(candle.get("high"))
        l = _float(candle.get("low"))
        c = _float(candle.get("close"))
        if not all(math.isfinite(v) and v > 0 for v in (h, l, c)):
            continue
        mfe_r = max(mfe_r, (entry_price - l) / initial_risk_abs)
        mae_r = max(mae_r, (h - entry_price) / initial_risk_abs)

        if h >= stop:
            stop_first_conservative = bool(exit_policy in {"short_tp0p75r_close50_trail", "short_tp1r_close50_trail"} and not partial_taken and math.isfinite(tp_price) and l <= tp_price)
            fill = stop * (1.0 + float(config.exit_slippage_pct))
            realized_r += _short_leg_r(entry_price, fill, open_fraction, initial_risk_abs, float(config.fee_rate))
            exit_ts = candle_ts
            exit_price = fill
            exit_reason = "stop_loss" if trail_updates <= 0 else "structural_trailing_stop"
            open_fraction = 0.0
            break

        if exit_policy in {"short_tp0p75r_close50_trail", "short_tp1r_close50_trail"} and not partial_taken and math.isfinite(tp_price) and l <= tp_price:
            fill = tp_price * (1.0 + float(config.exit_slippage_pct))
            close_fraction = 0.50
            realized_r += _short_leg_r(entry_price, fill, close_fraction, initial_risk_abs, float(config.fee_rate))
            open_fraction -= close_fraction
            partial_taken = True

        if i >= int(config.trail_lookback_1m) - 1 and open_fraction > 0:
            recent = path.iloc[max(0, i - int(config.trail_lookback_1m) + 1) : i + 1]
            candidate = _safe_max(_numeric_series(recent, "high")) * (1.0 + float(config.structural_stop_buffer_pct))
            # A new short trail is accepted only after the candle is closed and
            # only if it remains above current close, so it can be executed from
            # the next minute without retroactively stopping the current candle.
            if candidate > c and candidate < stop:
                stop = candidate
                trail_updates += 1

    if open_fraction > 0:
        final_close = _float(path.iloc[-1].get("close"))
        if math.isfinite(final_close) and final_close > 0:
            fill = final_close * (1.0 + float(config.exit_slippage_pct))
            realized_r += _short_leg_r(entry_price, fill, open_fraction, initial_risk_abs, float(config.fee_rate))
            exit_ts = int(path.iloc[-1]["timestamp"])
            exit_price = fill
            exit_reason = "time_exit_max_hold"
            open_fraction = 0.0

    trade_id = f"{signal['signal_id']}:{exit_policy}"
    hold_minutes = max(0.0, (int(exit_ts) - entry_ts) / MINUTE_MS)
    row = {
        **_honesty_fields(),
        "research_id": RESEARCH_ID,
        "trade_id": trade_id,
        "signal_id": signal["signal_id"],
        "setup_id": signal["setup_id"],
        "symbol": signal["symbol"],
        "signal_variant": signal.get("signal_variant", "structural_low_break"),
        "pump_tier": signal.get("pump_tier", "missing"),
        "is_strong_pump_tier": bool(signal.get("is_strong_pump_tier", False)),
        "is_anomaly_pump_tier": bool(signal.get("is_anomaly_pump_tier", False)),
        "is_extreme_pump_tier": bool(signal.get("is_extreme_pump_tier", False)),
        "selection_eligible": bool(signal.get("selection_eligible", True)),
        "signal_family": signal["signal_family"],
        "exit_policy": exit_policy,
        "session_bucket": signal["session_bucket"],
        "session_primary": signal["session_primary"],
        "session_overlap": signal["session_overlap"],
        "hour_utc": signal["hour_utc"],
        "weekday": signal["weekday"],
        "entry_timestamp_ms": entry_ts,
        "entry_time_utc": _fmt_ts(entry_ts),
        "exit_timestamp_ms": int(exit_ts),
        "exit_time_utc": _fmt_ts(int(exit_ts)),
        "entry_price": entry_price,
        "initial_stop_price": initial_stop,
        "final_stop_price": stop,
        "exit_price": exit_price,
        "initial_risk_abs": initial_risk_abs,
        "initial_risk_pct": float(signal["initial_risk_pct"]),
        "r_multiple": realized_r,
        "net_r": realized_r,
        "gross_direction": "short",
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "hold_minutes": hold_minutes,
        "exit_reason": exit_reason,
        "partial_taken": partial_taken,
        "trail_updates": int(trail_updates),
        "stop_first_conservative": stop_first_conservative,
        "oi_regime": signal.get("oi_regime", ""),
        "oi_change_pct": signal.get("oi_change_pct", np.nan),
        "oi_change_pct_1x5m": signal.get("oi_change_pct_1x5m", np.nan),
        "oi_change_pct_2x5m": signal.get("oi_change_pct_2x5m", np.nan),
        "oi_change_pct_3x5m": signal.get("oi_change_pct_3x5m", np.nan),
        "oi_age_ms": signal.get("oi_age_ms", np.nan),
        "oi_status": signal.get("oi_status", ""),
        "oi_strength_bucket": signal.get("oi_strength_bucket", ""),
        "confirm_timestamp_ms": signal["confirm_timestamp_ms"],
        "confirm_close_timestamp_ms": signal["confirm_close_timestamp_ms"],
        "confirm_taker_buy_share": signal.get("confirm_taker_buy_share", np.nan),
        "confirm_taker_buy_share_norm": signal.get("confirm_taker_buy_share_norm", np.nan),
        "confirm_taker_buy_bucket": signal.get("confirm_taker_buy_bucket", ""),
        "confirm_taker_buy_vs_norm_pct": signal.get("confirm_taker_buy_vs_norm_pct", np.nan),
        "confirm_taker_buy_norm_ratio": signal.get("confirm_taker_buy_norm_ratio", np.nan),
        "confirm_taker_buy_below_norm_flag": signal.get("confirm_taker_buy_below_norm_flag", False),
        "confirm_quote_ratio_vs_norm": signal.get("confirm_quote_ratio_vs_norm", np.nan),
        "confirm_trade_ratio_vs_norm": signal.get("confirm_trade_ratio_vs_norm", np.nan),
        "structural_low": signal.get("structural_low", np.nan),
        "last_structural_high": signal.get("last_structural_high", np.nan),
        "has_lower_high_before_break": signal.get("has_lower_high_before_break", False),
        "last_lower_high_price": signal.get("last_lower_high_price", np.nan),
        "failed_retest_distance_pct": signal.get("failed_retest_distance_pct", np.nan),
        "pump_high_to_confirm_drawdown_pct": signal.get("pump_high_to_confirm_drawdown_pct", np.nan),
        "structural_break_depth_pct": signal.get("structural_break_depth_pct", np.nan),
        "minutes_from_seed_to_break": signal.get("minutes_from_seed_to_break", np.nan),
        "pre_confirm_return_from_seed_close_pct": signal.get("pre_confirm_return_from_seed_close_pct", np.nan),
        "post_pump_preconfirm_quote_top1_share": signal.get("post_pump_preconfirm_quote_top1_share", np.nan),
        "post_pump_preconfirm_trade_top1_share": signal.get("post_pump_preconfirm_trade_top1_share", np.nan),
        "post_pump_preconfirm_distribution_bucket": signal.get("post_pump_preconfirm_distribution_bucket", ""),
        "seed_1m_quote_top1_share": signal.get("seed_1m_quote_top1_share", np.nan),
        "seed_1m_trade_top1_share": signal.get("seed_1m_trade_top1_share", np.nan),
        "seed_1m_distribution_bucket": signal.get("seed_1m_distribution_bucket", ""),
        "pump_high_timing_pct": signal.get("pump_high_timing_pct", np.nan),
        "seed_first_half_return_pct": signal.get("seed_first_half_return_pct", np.nan),
        "seed_second_half_return_pct": signal.get("seed_second_half_return_pct", np.nan),
        "seed_last_2m_return_pct": signal.get("seed_last_2m_return_pct", np.nan),
    }
    return row



def _simulate_short_trade(
    signal: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    config: FailedPumpShortResearchConfig,
    exit_policy: str,
) -> dict[str, object] | None:
    entry_ts = int(signal["entry_timestamp_ms"])
    end_ts = entry_ts + int(config.max_hold_minutes) * MINUTE_MS
    path = _time_window(frame_1m, start_ms=entry_ts, end_ms=end_ts, include_end=True)
    return _simulate_short_trade_on_path(signal, path=path, config=config, exit_policy=exit_policy)

def _short_leg_r(entry_price: float, exit_price: float, fraction: float, risk_abs: float, fee_rate: float) -> float:
    if risk_abs <= 0 or fraction <= 0:
        return 0.0
    gross = fraction * (entry_price - exit_price)
    fees = fraction * fee_rate * (entry_price + exit_price)
    return (gross - fees) / risk_abs


def _prepare_trade_grid_for_reporting(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    work["day_ord"] = (pd.to_numeric(work["entry_timestamp_ms"], errors="coerce") // DAY_MS).astype("Int64")
    work["is_win"] = pd.to_numeric(work["r_multiple"], errors="coerce") > 0
    if "selection_eligible" not in work.columns:
        work["selection_eligible"] = True
    for col, default in (
        ("signal_family", ""),
        ("session_bucket", ""),
        ("exit_policy", ""),
        ("confirm_taker_buy_bucket", "unknown"),
        ("oi_strength_bucket", "unknown"),
        ("post_pump_preconfirm_distribution_bucket", "unknown"),
        ("pump_tier", "missing"),
    ):
        if col not in work.columns:
            work[col] = default
    family = work["signal_family"].astype(str)
    session = work["session_bucket"].astype(str)
    exit_policy = work["exit_policy"].astype(str)
    taker = work["confirm_taker_buy_bucket"].astype(str)
    oi = work["oi_strength_bucket"].astype(str)
    distribution = work["post_pump_preconfirm_distribution_bucket"].astype(str)
    lower_high = work.get("has_lower_high_before_break", pd.Series(False, index=work.index)).astype(bool).astype(str)
    pump_tier = work["pump_tier"].astype(str)
    work["rule_id"] = family + "|" + session + "|" + exit_policy
    work["pump_tier_rule_id"] = family + "|" + session + "|" + exit_policy + "|pump_tier=" + pump_tier
    work["extended_rule_id"] = (
        family
        + "|"
        + session
        + "|"
        + exit_policy
        + "|taker="
        + taker
        + "|oi="
        + oi
        + "|distribution="
        + distribution
        + "|lower_high="
        + lower_high
    )
    work["extended_pump_tier_rule_id"] = work["extended_rule_id"] + "|pump_tier=" + pump_tier
    return _sort_frame(work, ["entry_timestamp_ms", "symbol", "signal_family", "exit_policy"])



def _run_rolling_protocol(trade_grid: pd.DataFrame, *, config: FailedPumpShortResearchConfig) -> dict[str, pd.DataFrame]:
    empty = _empty_rolling_outputs(trade_grid)
    if trade_grid.empty:
        return empty
    trades = trade_grid.copy()
    trades["day_ord"] = pd.to_numeric(trades["day_ord"], errors="coerce").astype("Int64")
    trades = trades.loc[trades["day_ord"].notna()].copy()
    trades["day_ord_i"] = trades["day_ord"].astype(int)
    _add_rule_match_columns(trades)
    if "selection_eligible" in trades.columns:
        selection_trades = trades.loc[trades["selection_eligible"].astype(bool)].copy()
    else:
        selection_trades = trades.copy()
    trades = selection_trades
    if trades.empty:
        return _empty_rolling_outputs(trade_grid)
    min_day = int(trades["day_ord_i"].min())
    max_day = int(trades["day_ord_i"].max())
    # Need at least the shortest window behind the first evaluated day.  Longer
    # windows are still reported as incomplete until the full requested history
    # exists; incomplete windows are never allowed to select live/OOS trades.
    oos_days = list(range(min_day + min(ROLLING_WINDOWS), max_day + 1))
    if not oos_days:
        return _empty_rolling_outputs(trade_grid)

    rule_health_rows: list[dict[str, object]] = []
    oos_rows: list[dict[str, object]] = []
    drift_rows: list[dict[str, object]] = []
    window_health_rows: list[dict[str, object]] = []
    shadow_rows: list[dict[str, object]] = []
    all_rules = _candidate_rules(trades)
    # Rule masks are independent of day/window.  Precompute once instead of
    # rebuilding the same pandas boolean masks for every rolling day.
    rule_masks = {str(rule["rule_id"]): _rule_mask(trades, rule) for rule in all_rules}
    rolling_started_at = time.monotonic()
    last_rolling_emit_at = 0.0

    for day_index, test_day in enumerate(oos_days, start=1):
        day_health: list[dict[str, object]] = []
        selected_rules: set[str] = set()
        for rule in all_rules:
            scope_mask = rule_masks[str(rule["rule_id"])]
            for window in ROLLING_WINDOWS:
                window_start = int(test_day) - int(window)
                train_window_days_available = max(0, int(test_day) - max(int(min_day), window_start))
                train_window_complete = bool(train_window_days_available >= int(window))
                train_mask = (trades["day_ord_i"] >= window_start) & (trades["day_ord_i"] < test_day)
                train = trades.loc[train_mask & scope_mask]
                metrics = _metrics_dict(train)
                status = _rule_status(metrics, window=window, config=config)
                selection_allowed = bool(
                    train_window_complete
                    and rule["scope"] == "session_specific"
                    and int(window) == 30
                    and status in {"core", "strong", "tactical"}
                )
                block_reason = _selection_block_reason(
                    status=status,
                    metrics=metrics,
                    train_window_complete=train_window_complete,
                    rule_scope=str(rule["scope"]),
                    window=int(window),
                    config=config,
                )
                health = {
                    "date": _date_from_day_ord(test_day),
                    "test_day_ord": int(test_day),
                    "window_days": int(window),
                    "train_start_day_ord": int(window_start),
                    "train_end_day_ord": int(test_day) - 1,
                    "train_start_date": _date_from_day_ord(window_start),
                    "train_end_date": _date_from_day_ord(int(test_day) - 1),
                    "train_window_days_requested": int(window),
                    "train_window_days_available": int(train_window_days_available),
                    "train_window_complete": train_window_complete,
                    "rule_scope": rule["scope"],
                    "rule_id": rule["rule_id"],
                    "signal_family": rule["signal_family"],
                    "session_bucket": rule["session_bucket"],
                    "exit_policy": rule["exit_policy"],
                    "selection_model_window": bool(int(window) == 30 and rule["scope"] == "session_specific"),
                    "selection_allowed": selection_allowed,
                    "selection_block_reason": block_reason,
                    "status": status,
                    **metrics,
                }
                for extra_col in (
                    "confirm_taker_buy_bucket",
                    "oi_strength_bucket",
                    "post_pump_preconfirm_distribution_bucket",
                    "has_lower_high_before_break",
                    "pump_tier",
                ):
                    if extra_col in rule:
                        health[extra_col] = rule[extra_col]
                rule_health_rows.append(health)
                day_health.append(health)
                window_health_rows.append(
                    {
                        "date": _date_from_day_ord(test_day),
                        "test_day_ord": int(test_day),
                        "window_days": int(window),
                        "train_window_days_requested": int(window),
                        "train_window_days_available": int(train_window_days_available),
                        "train_window_complete": train_window_complete,
                        "rule_scope": rule["scope"],
                        "status": status,
                        "selection_block_reason": block_reason,
                        "rules": 1,
                        "tradeable": int(selection_allowed),
                        "cooldown_or_rejected": int(status in {"cooldown", "rejected"}),
                    }
                )

        main_health = [
            row
            for row in day_health
            if int(row["window_days"]) == 30
            and row["rule_scope"] == "session_specific"
            and bool(row.get("train_window_complete", False))
        ]
        by_rule = {str(row["rule_id"]): row for row in main_health}
        for rule_id, row in by_rule.items():
            if bool(row.get("selection_allowed", False)):
                selected_rules.add(rule_id)

        test_all_trades = trades.loc[trades["day_ord_i"] == test_day].copy()
        test_trades = test_all_trades.copy()
        if selected_rules:
            test_trades = test_trades.loc[test_trades["rule_id"].astype(str).isin(selected_rules)].copy()
        else:
            test_trades = test_trades.iloc[0:0].copy()
        if not test_trades.empty:
            for _, trade in test_trades.iterrows():
                source = {
                    key: value
                    for key, value in trade.to_dict().items()
                    if not str(key).startswith("__rule_") and key != "day_ord_i"
                }
                health = by_rule.get(str(source.get("rule_id")), {})
                source.update(
                    {
                        "test_date": _date_from_day_ord(test_day),
                        "test_day_ord": int(test_day),
                        "selected_rule_status": health.get("status", ""),
                        "selected_rule_train_trades": health.get("trades", 0),
                        "selected_rule_train_avg_r": health.get("avg_r", np.nan),
                        "selected_rule_train_sum_r": health.get("sum_r", np.nan),
                        "selected_rule_train_window_days_requested": health.get("train_window_days_requested", 30),
                        "selected_rule_train_window_days_available": health.get("train_window_days_available", 0),
                        "selected_rule_train_window_complete": health.get("train_window_complete", False),
                        "selected_rule_block_reason": health.get("selection_block_reason", ""),
                    }
                )
                oos_rows.append(source)

        shadow_rows.extend(
            _shadow_oos_audit_rows(
                day_health=day_health,
                test_trades=test_all_trades,
                test_day=int(test_day),
                config=config,
            )
        )

        main_including_incomplete = [row for row in day_health if int(row["window_days"]) == 30 and row["rule_scope"] == "session_specific"]
        status_counts = pd.Series([str(row["status"]) for row in main_including_incomplete]).value_counts().to_dict() if main_including_incomplete else {}
        incomplete_main = [row for row in main_including_incomplete if not bool(row.get("train_window_complete", False))]
        block_counts = pd.Series([str(row.get("selection_block_reason", "")) for row in main_including_incomplete]).value_counts().to_dict() if main_including_incomplete else {}
        drift_rows.append(
            {
                "date": _date_from_day_ord(test_day),
                "test_day_ord": int(test_day),
                "selection_rule_scope": "session_specific",
                "selection_window_days": 30,
                "candidate_rules": int(len(main_including_incomplete)),
                "candidate_rules_complete_train": int(len(main_health)),
                "candidate_rules_incomplete_train": int(len(incomplete_main)),
                "selected_rules": int(len(selected_rules)),
                "core_rules": int(status_counts.get("core", 0)),
                "strong_rules": int(status_counts.get("strong", 0)),
                "tactical_rules": int(status_counts.get("tactical", 0)),
                "challenger_rules": int(status_counts.get("challenger", 0)),
                "cooldown_rules": int(status_counts.get("cooldown", 0)),
                "rejected_rules": int(status_counts.get("rejected", 0)),
                "blocked_incomplete_train_window": int(block_counts.get("incomplete_train_window", 0)),
                "blocked_not_tradeable_status": int(block_counts.get("not_tradeable_status", 0)),
                "blocked_non_selection_scope": int(block_counts.get("non_selection_scope", 0)),
                "blocked_non_selection_window": int(block_counts.get("non_selection_window", 0)),
                "test_trades": int(len(test_trades)),
                "test_all_selection_eligible_trades": int(len(test_all_trades)),
                "test_sum_r": float(pd.to_numeric(test_trades.get("r_multiple", pd.Series(dtype=float)), errors="coerce").sum()) if not test_trades.empty else 0.0,
            }
        )

        now = time.monotonic()
        if day_index == 1 or day_index == len(oos_days) or now - last_rolling_emit_at >= 10.0:
            last_rolling_emit_at = now
            print(
                "failed-pump short research: rolling "
                f"{day_index}/{len(oos_days)} date={_date_from_day_ord(test_day)} "
                f"selected_rules={len(selected_rules)} oos_trades={len(test_trades)} "
                f"elapsed={_format_duration(now - rolling_started_at)}",
                flush=True,
            )
            _write_progress_event(
                config.output_dir,
                stage="rolling_progress",
                message=f"day={day_index}/{len(oos_days)} date={_date_from_day_ord(test_day)}",
                elapsed_seconds=now - rolling_started_at,
                trades=len(test_trades),
                oos_trades=len(oos_rows),
            )

    oos_frame = _ensure_columns(pd.DataFrame(oos_rows), _rolling_oos_columns(trade_grid))
    if not oos_frame.empty:
        oos_frame = _sort_frame(oos_frame, ["entry_timestamp_ms", "symbol", "rule_id"])
    daily_summary = _rolling_daily_summary(oos_frame, oos_days=oos_days)
    rule_health = _ensure_columns(pd.DataFrame(rule_health_rows), _rolling_rule_health_columns())
    window_health = _aggregate_window_health(pd.DataFrame(window_health_rows))
    window_health = _ensure_columns(window_health, _rolling_window_health_columns())
    selection_drift = _ensure_columns(pd.DataFrame(drift_rows), _rolling_selection_drift_columns())
    fluctuation_stress = _rolling_fluctuation_stress(oos_frame, full_trade_grid=trades)
    shadow_oos_audit = _ensure_columns(pd.DataFrame(shadow_rows), _rolling_shadow_oos_audit_columns())
    return {
        "daily_summary": _ensure_columns(daily_summary, _rolling_daily_summary_columns()),
        "rule_health": _sort_frame(rule_health, ["test_day_ord", "window_days", "rule_scope", "rule_id"]),
        "oos_trades": oos_frame,
        "window_health": window_health,
        "selection_drift": _sort_frame(selection_drift, ["test_day_ord"]),
        "fluctuation_stress": fluctuation_stress,
        "shadow_oos_audit": _sort_frame(shadow_oos_audit, ["test_day_ord", "shadow_rank"]),
    }


def _empty_rolling_outputs(trade_grid: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "daily_summary": pd.DataFrame(columns=_rolling_daily_summary_columns()),
        "rule_health": pd.DataFrame(columns=_rolling_rule_health_columns()),
        "oos_trades": pd.DataFrame(columns=_rolling_oos_columns(trade_grid)),
        "window_health": pd.DataFrame(columns=_rolling_window_health_columns()),
        "selection_drift": pd.DataFrame(columns=_rolling_selection_drift_columns()),
        "fluctuation_stress": pd.DataFrame(columns=_rolling_fluctuation_stress_columns()),
        "shadow_oos_audit": pd.DataFrame(columns=_rolling_shadow_oos_audit_columns()),
    }


def _ensure_columns(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=list(columns))
    work = frame.copy()
    for col in columns:
        if col not in work.columns:
            work[col] = np.nan
    ordered = list(columns) + [col for col in work.columns if col not in columns]
    return work.loc[:, ordered]


def _rolling_oos_columns(trade_grid: pd.DataFrame) -> list[str]:
    base = list(trade_grid.columns) if isinstance(trade_grid, pd.DataFrame) else []
    extras = [
        "test_date",
        "test_day_ord",
        "selected_rule_status",
        "selected_rule_train_trades",
        "selected_rule_train_avg_r",
        "selected_rule_train_sum_r",
        "selected_rule_train_window_days_requested",
        "selected_rule_train_window_days_available",
        "selected_rule_train_window_complete",
        "selected_rule_block_reason",
    ]
    return [*base, *[col for col in extras if col not in base]]


def _rolling_rule_health_columns() -> list[str]:
    return [
        "date",
        "test_day_ord",
        "window_days",
        "train_start_day_ord",
        "train_end_day_ord",
        "train_start_date",
        "train_end_date",
        "train_window_days_requested",
        "train_window_days_available",
        "train_window_complete",
        "rule_scope",
        "rule_id",
        "signal_family",
        "session_bucket",
        "exit_policy",
        "pump_tier",
        "confirm_taker_buy_bucket",
        "oi_strength_bucket",
        "post_pump_preconfirm_distribution_bucket",
        "has_lower_high_before_break",
        "selection_model_window",
        "selection_allowed",
        "selection_block_reason",
        "status",
        *_metrics_columns(),
    ]


def _rolling_window_health_columns() -> list[str]:
    return [
        "date",
        "test_day_ord",
        "window_days",
        "train_window_days_requested",
        "train_window_days_available",
        "train_window_complete",
        "rule_scope",
        "status",
        "selection_block_reason",
        "rules",
        "tradeable",
        "cooldown_or_rejected",
    ]


def _rolling_selection_drift_columns() -> list[str]:
    return [
        "date",
        "test_day_ord",
        "selection_rule_scope",
        "selection_window_days",
        "candidate_rules",
        "candidate_rules_complete_train",
        "candidate_rules_incomplete_train",
        "selected_rules",
        "core_rules",
        "strong_rules",
        "tactical_rules",
        "challenger_rules",
        "cooldown_rules",
        "rejected_rules",
        "blocked_incomplete_train_window",
        "blocked_not_tradeable_status",
        "blocked_non_selection_scope",
        "blocked_non_selection_window",
        "test_trades",
        "test_all_selection_eligible_trades",
        "test_sum_r",
    ]


def _rolling_daily_summary_columns() -> list[str]:
    return [
        "date",
        "test_day_ord",
        "trades",
        "active_day",
        "sum_r",
        "avg_r",
        "winrate",
        "cumulative_oos_r",
        "oos_drawdown",
        "positive_day",
        "cumulative_oos_positive_days",
        "cumulative_oos_active_days",
        "cumulative_oos_positive_active_days",
        "cumulative_oos_positive_active_day_share",
    ]


def _rolling_fluctuation_stress_columns() -> list[str]:
    return ["source", "stress", *_metrics_columns()]


def _rolling_shadow_oos_audit_columns() -> list[str]:
    return [
        "date",
        "test_day_ord",
        "shadow_rank",
        "shadow_rule_id",
        "rule_scope",
        "signal_family",
        "session_bucket",
        "exit_policy",
        "pump_tier",
        "confirm_taker_buy_bucket",
        "oi_strength_bucket",
        "post_pump_preconfirm_distribution_bucket",
        "has_lower_high_before_break",
        "train_window_days_requested",
        "train_window_days_available",
        "train_window_complete",
        "train_trades",
        "train_active_days",
        "train_sum_r",
        "train_avg_r",
        "train_median_r",
        "train_winrate",
        "train_positive_active_day_share",
        "train_top_trade_independence_pct",
        "train_top_symbol_independence_pct",
        "test_trades",
        "test_sum_r",
        "test_avg_r",
        "test_median_r",
        "test_winrate",
        "audit_model",
        "selection_eligible",
    ]


def _metrics_columns() -> list[str]:
    return list(_metrics_dict(pd.DataFrame()).keys())


def _selection_block_reason(
    *,
    status: str,
    metrics: dict[str, object],
    train_window_complete: bool,
    rule_scope: str,
    window: int,
    config: FailedPumpShortResearchConfig,
) -> str:
    if not train_window_complete:
        return "incomplete_train_window"
    if rule_scope != "session_specific":
        return "non_selection_scope"
    if int(window) != 30:
        return "non_selection_window"
    if status in {"core", "strong", "tactical"}:
        return "selected"
    return _rule_rejection_reason(metrics=metrics, status=status, config=config)


def _rule_rejection_reason(*, metrics: dict[str, object], status: str, config: FailedPumpShortResearchConfig) -> str:
    trades = int(metrics.get("trades", 0) or 0)
    active_days = int(metrics.get("active_days", 0) or 0)
    avg_r = float(metrics.get("avg_r", 0.0) or 0.0)
    sum_r = float(metrics.get("sum_r", 0.0) or 0.0)
    pos_active_share = float(metrics.get("positive_active_day_share", 0.0) or 0.0)
    top_trade_ind = float(metrics.get("top_trade_independence_pct", 0.0) or 0.0)
    top_symbol_ind = float(metrics.get("top_symbol_independence_pct", 0.0) or 0.0)
    if trades <= 0:
        return "no_train_trades"
    if trades < int(config.min_train_trades):
        return "too_few_train_trades"
    if active_days < int(config.min_train_active_days):
        return "too_few_active_train_days"
    if sum_r <= 0:
        return "non_positive_train_sum_r"
    if avg_r <= 0:
        return "non_positive_train_avg_r"
    if pos_active_share < float(config.min_positive_active_day_share):
        return "weak_positive_active_day_share"
    if top_trade_ind < float(config.min_top_trade_independence_pct):
        return "top_trade_dependency"
    if top_symbol_ind < float(config.min_top_symbol_independence_pct):
        return "top_symbol_dependency"
    if status == "challenger":
        return "below_tactical_avg_r"
    if status == "cooldown":
        return "cooldown_status"
    if status == "rejected":
        return "rejected_status"
    return "not_tradeable_status"


def _shadow_oos_audit_rows(
    *,
    day_health: Sequence[dict[str, object]],
    test_trades: pd.DataFrame,
    test_day: int,
    config: FailedPumpShortResearchConfig,
) -> list[dict[str, object]]:
    candidates = [
        row
        for row in day_health
        if row.get("rule_scope") == "extended_flow_oi_distribution_audit"
        and int(row.get("window_days", 0) or 0) == 30
        and bool(row.get("train_window_complete", False))
        and int(row.get("trades", 0) or 0) >= int(config.shadow_oos_min_train_trades)
        and int(row.get("active_days", 0) or 0) >= int(config.shadow_oos_min_train_active_days)
        and float(row.get("sum_r", 0.0) or 0.0) > float(config.shadow_oos_min_train_sum_r)
    ]
    if not candidates:
        return []
    candidates.sort(
        key=lambda row: (
            float(row.get("avg_r", 0.0) or 0.0),
            float(row.get("sum_r", 0.0) or 0.0),
            int(row.get("trades", 0) or 0),
        ),
        reverse=True,
    )
    rows: list[dict[str, object]] = []
    for rank, candidate in enumerate(candidates[: int(config.shadow_oos_max_rules_per_day)], start=1):
        rule = {
            "signal_family": str(candidate.get("signal_family", "")),
            "session_bucket": str(candidate.get("session_bucket", "")),
            "exit_policy": str(candidate.get("exit_policy", "")),
            "confirm_taker_buy_bucket": str(candidate.get("confirm_taker_buy_bucket", "")),
            "oi_strength_bucket": str(candidate.get("oi_strength_bucket", "")),
            "post_pump_preconfirm_distribution_bucket": str(candidate.get("post_pump_preconfirm_distribution_bucket", "")),
            "has_lower_high_before_break": str(candidate.get("has_lower_high_before_break", "")),
            "pump_tier": str(candidate.get("pump_tier", "")),
        }
        matched = test_trades.loc[_rule_mask(test_trades, rule)].copy() if not test_trades.empty else test_trades
        test_metrics = _metrics_dict(matched)
        rows.append(
            {
                "date": _date_from_day_ord(test_day),
                "test_day_ord": int(test_day),
                "shadow_rank": int(rank),
                "shadow_rule_id": str(candidate.get("rule_id", "")),
                "rule_scope": "extended_flow_oi_distribution_audit",
                "signal_family": candidate.get("signal_family", ""),
                "session_bucket": candidate.get("session_bucket", ""),
                "exit_policy": candidate.get("exit_policy", ""),
                "pump_tier": candidate.get("pump_tier", ""),
                "confirm_taker_buy_bucket": candidate.get("confirm_taker_buy_bucket", ""),
                "oi_strength_bucket": candidate.get("oi_strength_bucket", ""),
                "post_pump_preconfirm_distribution_bucket": candidate.get("post_pump_preconfirm_distribution_bucket", ""),
                "has_lower_high_before_break": candidate.get("has_lower_high_before_break", ""),
                "train_window_days_requested": int(candidate.get("train_window_days_requested", 30) or 30),
                "train_window_days_available": int(candidate.get("train_window_days_available", 0) or 0),
                "train_window_complete": bool(candidate.get("train_window_complete", False)),
                "train_trades": int(candidate.get("trades", 0) or 0),
                "train_active_days": int(candidate.get("active_days", 0) or 0),
                "train_sum_r": float(candidate.get("sum_r", 0.0) or 0.0),
                "train_avg_r": float(candidate.get("avg_r", 0.0) or 0.0),
                "train_median_r": float(candidate.get("median_r", 0.0) or 0.0),
                "train_winrate": float(candidate.get("winrate", 0.0) or 0.0),
                "train_positive_active_day_share": float(candidate.get("positive_active_day_share", 0.0) or 0.0),
                "train_top_trade_independence_pct": float(candidate.get("top_trade_independence_pct", 0.0) or 0.0),
                "train_top_symbol_independence_pct": float(candidate.get("top_symbol_independence_pct", 0.0) or 0.0),
                "test_trades": int(test_metrics.get("trades", 0) or 0),
                "test_sum_r": float(test_metrics.get("sum_r", 0.0) or 0.0),
                "test_avg_r": float(test_metrics.get("avg_r", 0.0) or 0.0),
                "test_median_r": float(test_metrics.get("median_r", 0.0) or 0.0),
                "test_winrate": float(test_metrics.get("winrate", 0.0) or 0.0),
                "audit_model": "shadow_extended_bucket_oos_not_used_for_trading_selection",
                "selection_eligible": False,
            }
        )
    return rows



def _candidate_rules(trades: pd.DataFrame) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for family in SIGNAL_FAMILIES:
        for exit_policy in EXIT_POLICIES:
            rows.append(
                {
                    "scope": "all_sessions",
                    "rule_id": _rule_id(family, "all_sessions", exit_policy),
                    "signal_family": family,
                    "session_bucket": "all_sessions",
                    "exit_policy": exit_policy,
                }
            )
            for session in SESSION_BUCKETS:
                rows.append(
                    {
                        "scope": "session_specific",
                        "rule_id": _rule_id(family, session, exit_policy),
                        "signal_family": family,
                        "session_bucket": session,
                        "exit_policy": exit_policy,
                    }
                )
    if "pump_tier" in trades.columns:
        for (family, session, exit_policy, pump_tier), _group in trades.groupby(
            ["signal_family", "session_bucket", "exit_policy", "pump_tier"],
            dropna=False,
        ):
            rows.append(
                {
                    "scope": "pump_tier_session_audit",
                    "rule_id": _pump_tier_rule_id(str(family), str(session), str(exit_policy), str(pump_tier)),
                    "signal_family": str(family),
                    "session_bucket": str(session),
                    "exit_policy": str(exit_policy),
                    "pump_tier": str(pump_tier),
                }
            )

    # Extended rules are evaluated for health/drift diagnostics only.  The OOS
    # selector below still promotes only the 30d session_specific core scope, so
    # these finer buckets cannot silently overfit the trading decision.
    extended_cols = {
        "confirm_taker_buy_bucket": "all_taker_buckets",
        "oi_strength_bucket": "all_oi_strengths",
        "post_pump_preconfirm_distribution_bucket": "all_distribution_buckets",
        "has_lower_high_before_break": "all_lower_high_states",
    }
    if all(col in trades.columns for col in extended_cols):
        group_cols = [
            "signal_family",
            "session_bucket",
            "exit_policy",
            "confirm_taker_buy_bucket",
            "oi_strength_bucket",
            "post_pump_preconfirm_distribution_bucket",
            "has_lower_high_before_break",
        ]
        include_pump_tier = "pump_tier" in trades.columns
        if include_pump_tier:
            group_cols.append("pump_tier")
        for key, _group in trades.groupby(group_cols, dropna=False):
            if include_pump_tier:
                family, session, exit_policy, taker_bucket, oi_bucket, dist_bucket, lower_high, pump_tier = key
            else:
                family, session, exit_policy, taker_bucket, oi_bucket, dist_bucket, lower_high = key
                pump_tier = "all_pump_tiers"
            rows.append(
                {
                    "scope": "extended_flow_oi_distribution_audit",
                    "rule_id": _extended_rule_id(
                        str(family),
                        str(session),
                        str(exit_policy),
                        str(taker_bucket),
                        str(oi_bucket),
                        str(dist_bucket),
                        str(bool(lower_high)),
                    ) + f"|pump_tier={pump_tier}",
                    "signal_family": str(family),
                    "session_bucket": str(session),
                    "exit_policy": str(exit_policy),
                    "confirm_taker_buy_bucket": str(taker_bucket),
                    "oi_strength_bucket": str(oi_bucket),
                    "post_pump_preconfirm_distribution_bucket": str(dist_bucket),
                    "has_lower_high_before_break": str(bool(lower_high)),
                    "pump_tier": str(pump_tier),
                }
            )
    return rows


def _rule_mask(trades: pd.DataFrame, rule: dict[str, str]) -> pd.Series:
    mask = (_rule_value_series(trades, "signal_family") == rule["signal_family"]) & (_rule_value_series(trades, "exit_policy") == rule["exit_policy"])
    if rule["session_bucket"] != "all_sessions":
        mask &= _rule_value_series(trades, "session_bucket") == rule["session_bucket"]
    for col in ("confirm_taker_buy_bucket", "oi_strength_bucket", "post_pump_preconfirm_distribution_bucket", "has_lower_high_before_break", "pump_tier"):
        if col in rule and col in trades.columns:
            mask &= _rule_value_series(trades, col) == rule[col]
    return mask


def _add_rule_match_columns(frame: pd.DataFrame) -> None:
    for col in (
        "signal_family",
        "exit_policy",
        "session_bucket",
        "confirm_taker_buy_bucket",
        "oi_strength_bucket",
        "post_pump_preconfirm_distribution_bucket",
        "pump_tier",
    ):
        if col in frame.columns:
            frame[f"__rule_{col}"] = frame[col].astype(str)
    if "has_lower_high_before_break" in frame.columns:
        frame["__rule_has_lower_high_before_break"] = frame["has_lower_high_before_break"].astype(bool).astype(str)


def _rule_value_series(frame: pd.DataFrame, column: str) -> pd.Series:
    fast_col = f"__rule_{column}"
    if fast_col in frame.columns:
        return frame[fast_col].astype(str)
    if column not in frame.columns:
        return pd.Series("", index=frame.index, dtype=object)
    if column == "has_lower_high_before_break":
        return frame[column].astype(bool).astype(str)
    return frame[column].astype(str)


def _rule_id(signal_family: str, session_bucket: str, exit_policy: str) -> str:
    return f"{signal_family}|{session_bucket}|{exit_policy}"


def _pump_tier_rule_id(signal_family: str, session_bucket: str, exit_policy: str, pump_tier: str) -> str:
    return f"{signal_family}|{session_bucket}|{exit_policy}|pump_tier={pump_tier}"


def _extended_rule_id(
    signal_family: str,
    session_bucket: str,
    exit_policy: str,
    taker_bucket: str,
    oi_strength_bucket: str,
    distribution_bucket: str,
    lower_high_state: str,
) -> str:
    return "|".join(
        (
            signal_family,
            session_bucket,
            exit_policy,
            f"taker={taker_bucket}",
            f"oi={oi_strength_bucket}",
            f"distribution={distribution_bucket}",
            f"lower_high={lower_high_state}",
        )
    )


def _rule_status(metrics: dict[str, object], *, window: int, config: FailedPumpShortResearchConfig) -> str:
    trades = int(metrics.get("trades", 0) or 0)
    active_days = int(metrics.get("active_days", 0) or 0)
    avg_r = float(metrics.get("avg_r", 0.0) or 0.0)
    sum_r = float(metrics.get("sum_r", 0.0) or 0.0)
    pos_active_share = float(metrics.get("positive_active_day_share", 0.0) or 0.0)
    top_trade_ind = float(metrics.get("top_trade_independence_pct", 0.0) or 0.0)
    top_symbol_ind = float(metrics.get("top_symbol_independence_pct", 0.0) or 0.0)
    max_dd = abs(float(metrics.get("max_drawdown", 0.0) or 0.0))

    if trades <= 0:
        return "challenger"
    if trades < int(config.min_train_trades) or active_days < int(config.min_train_active_days):
        return "challenger" if sum_r >= 0 else "cooldown"
    if sum_r <= 0 or avg_r <= 0:
        return "rejected" if window >= 30 else "cooldown"
    if pos_active_share < float(config.min_positive_active_day_share):
        return "cooldown"
    if top_trade_ind < float(config.min_top_trade_independence_pct):
        return "cooldown"
    if top_symbol_ind < float(config.min_top_symbol_independence_pct):
        return "cooldown"
    if avg_r >= float(config.core_min_avg_r) and max_dd <= max(2.0, abs(sum_r) * 0.75) and trades >= int(config.min_train_trades) * 3:
        return "core"
    if avg_r >= float(config.strong_min_avg_r) and max_dd <= max(2.5, abs(sum_r) * 1.0):
        return "strong"
    if avg_r >= float(config.tactical_min_avg_r):
        return "tactical"
    return "challenger"


def _rolling_daily_summary(oos_frame: pd.DataFrame, *, oos_days: Sequence[int]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    cumulative = 0.0
    peak = 0.0
    by_day: dict[int, pd.DataFrame] = {}
    if not oos_frame.empty and "test_day_ord" in oos_frame.columns:
        for day, group in oos_frame.groupby("test_day_ord", dropna=False):
            by_day[int(day)] = group
    for day in oos_days:
        group = by_day.get(int(day), pd.DataFrame())
        metrics = _metrics_dict(group)
        day_r = float(metrics.get("sum_r", 0.0) or 0.0)
        cumulative += day_r
        peak = max(peak, cumulative)
        drawdown = cumulative - peak
        rows.append(
            {
                "date": _date_from_day_ord(day),
                "test_day_ord": int(day),
                "trades": int(metrics.get("trades", 0)),
                "active_day": bool(int(metrics.get("trades", 0)) > 0),
                "sum_r": day_r,
                "avg_r": float(metrics.get("avg_r", 0.0) or 0.0),
                "winrate": float(metrics.get("winrate", 0.0) or 0.0),
                "cumulative_oos_r": cumulative,
                "oos_drawdown": drawdown,
                "positive_day": bool(day_r > 0),
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        active = frame.loc[frame["active_day"]]
        frame["cumulative_oos_positive_days"] = (frame["sum_r"] > 0).cumsum()
        frame["cumulative_oos_active_days"] = frame["active_day"].cumsum()
        frame["cumulative_oos_positive_active_days"] = ((frame["sum_r"] > 0) & frame["active_day"]).cumsum()
        frame["cumulative_oos_positive_active_day_share"] = (
            frame["cumulative_oos_positive_active_days"] / frame["cumulative_oos_active_days"].replace(0, np.nan)
        ).fillna(0.0)
    return frame


def _aggregate_window_health(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _ensure_columns(frame, _rolling_window_health_columns())
    group_cols = [
        "date",
        "test_day_ord",
        "window_days",
        "train_window_days_requested",
        "train_window_days_available",
        "train_window_complete",
        "rule_scope",
        "status",
        "selection_block_reason",
    ]
    grouped = frame.groupby(group_cols, dropna=False).agg(
        rules=("rules", "sum"),
        tradeable=("tradeable", "sum"),
        cooldown_or_rejected=("cooldown_or_rejected", "sum"),
    ).reset_index()
    return _sort_frame(grouped, ["test_day_ord", "window_days", "rule_scope", "status", "selection_block_reason"])


def _rolling_fluctuation_stress(oos_frame: pd.DataFrame, *, full_trade_grid: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for source_name, source in (("rolling_oos", oos_frame), ("full_trade_grid", full_trade_grid)):
        if source.empty:
            rows.append({"source": source_name, "stress": "empty", **_metrics_dict(source)})
            continue
        work = source.copy()
        if "entry_timestamp_ms" in work.columns:
            work["date"] = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
            work["month"] = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
            work["week"] = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True).dt.strftime("%G-W%V")
            work["day_ord"] = (pd.to_numeric(work["entry_timestamp_ms"], errors="coerce") // DAY_MS).astype("Int64")
        rows.append({"source": source_name, "stress": "all", **_metrics_dict(work)})
        if not work.empty:
            median_day = int(work["day_ord"].dropna().median()) if "day_ord" in work.columns and work["day_ord"].notna().any() else 0
            rows.append({"source": source_name, "stress": "first_half", **_metrics_dict(work.loc[work["day_ord"].astype("Int64") <= median_day])})
            rows.append({"source": source_name, "stress": "second_half", **_metrics_dict(work.loc[work["day_ord"].astype("Int64") > median_day])})
            rows.append({"source": source_name, "stress": "odd_days", **_metrics_dict(work.loc[(work["day_ord"].astype("Int64") % 2) == 1])})
            rows.append({"source": source_name, "stress": "even_days", **_metrics_dict(work.loc[(work["day_ord"].astype("Int64") % 2) == 0])})
            rows.append({"source": source_name, "stress": "remove_top_1_trade", **_metrics_dict(_remove_top_n_trades(work, 1))})
            rows.append({"source": source_name, "stress": "remove_top_3_trades", **_metrics_dict(_remove_top_n_trades(work, 3))})
            rows.append({"source": source_name, "stress": "remove_top_1_symbol", **_metrics_dict(_remove_top_n_symbols(work, 1))})
            for group_col in (
                "month",
                "week",
                "session_bucket",
                "signal_variant",
                "pump_tier",
                "signal_family",
                "exit_policy",
                "confirm_taker_buy_bucket",
                "oi_strength_bucket",
                "post_pump_preconfirm_distribution_bucket",
                "symbol",
            ):
                if group_col not in work.columns:
                    continue
                for value, group in work.groupby(group_col, dropna=False):
                    rows.append({"source": source_name, "stress": f"{group_col}={value}", **_metrics_dict(group)})
    return pd.DataFrame(rows)


def _metrics_dict(frame: pd.DataFrame) -> dict[str, object]:
    if frame is None or frame.empty or "r_multiple" not in frame.columns:
        return {
            "trades": 0,
            "winrate": 0.0,
            "avg_r": 0.0,
            "median_r": 0.0,
            "sum_r": 0.0,
            "active_days": 0,
            "positive_days": 0,
            "positive_active_days": 0,
            "positive_active_day_share": 0.0,
            "max_drawdown": 0.0,
            "top_trade_independence_pct": 0.0,
            "top_symbol_independence_pct": 0.0,
        }
    r = pd.to_numeric(frame["r_multiple"], errors="coerce").dropna()
    if r.empty:
        return _metrics_dict(pd.DataFrame())
    trades = int(len(r))
    sum_r = float(r.sum())
    days = _daily_r(frame)
    active_days = int(len(days))
    positive_days = int((days > 0).sum()) if not days.empty else 0
    winrate = float((r > 0).mean()) if trades else 0.0
    return {
        "trades": trades,
        "winrate": winrate,
        "avg_r": float(r.mean()) if trades else 0.0,
        "median_r": float(r.median()) if trades else 0.0,
        "sum_r": sum_r,
        "active_days": active_days,
        "positive_days": positive_days,
        "positive_active_days": positive_days,
        "positive_active_day_share": float(positive_days / active_days) if active_days else 0.0,
        "max_drawdown": _max_drawdown(r),
        "top_trade_independence_pct": _top_trade_independence_pct(r),
        "top_symbol_independence_pct": _top_symbol_independence_pct(frame),
    }


def _group_metrics(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[*group_cols, *_metrics_dict(pd.DataFrame()).keys()])
    rows: list[dict[str, object]] = []
    for key, group in frame.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        rows.append({**dict(zip(group_cols, key)), **_metrics_dict(group)})
    return pd.DataFrame(rows).sort_values(group_cols + ["sum_r"], ascending=[True] * len(group_cols) + [False]).reset_index(drop=True)


def _opportunity_audit(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "signal_id" not in frame.columns:
        return pd.DataFrame(columns=_opportunity_audit_columns())
    work = frame.copy()
    work["mfe_r"] = pd.to_numeric(work.get("mfe_r", np.nan), errors="coerce")
    work["mae_r"] = pd.to_numeric(work.get("mae_r", np.nan), errors="coerce")
    work["r_multiple"] = pd.to_numeric(work.get("r_multiple", np.nan), errors="coerce")
    first_cols = [
        "signal_variant",
        "pump_tier",
        "signal_family",
        "session_bucket",
        "confirm_taker_buy_bucket",
        "oi_strength_bucket",
        "post_pump_preconfirm_distribution_bucket",
        "has_lower_high_before_break",
        "symbol",
        "date",
    ]
    agg_spec: dict[str, tuple[str, str]] = {
        "mfe_r": ("mfe_r", "max"),
        "mae_r": ("mae_r", "max"),
        "best_exit_r": ("r_multiple", "max"),
        "worst_exit_r": ("r_multiple", "min"),
        "exit_policy_count": ("exit_policy", "nunique"),
    }
    for col in first_cols:
        if col in work.columns:
            agg_spec[col] = (col, "first")
    signals = work.groupby("signal_id", dropna=False).agg(**agg_spec).reset_index()
    if signals.empty:
        return pd.DataFrame(columns=_opportunity_audit_columns())
    rows: list[dict[str, object]] = []
    group_defs = [
        ("all", []),
        ("signal_variant", ["signal_variant"]),
        ("pump_tier", ["pump_tier"]),
        ("signal_family", ["signal_family"]),
        ("session", ["session_bucket"]),
        ("pump_tier_session_family", ["pump_tier", "session_bucket", "signal_family"]),
        ("pump_tier_family_distribution", ["pump_tier", "signal_family", "post_pump_preconfirm_distribution_bucket"]),
        ("pump_tier_family_taker_oi", ["pump_tier", "signal_family", "confirm_taker_buy_bucket", "oi_strength_bucket"]),
    ]
    for scope, cols in group_defs:
        if any(col not in signals.columns for col in cols):
            continue
        groups = [((), signals)] if not cols else signals.groupby(cols, dropna=False)
        for key, group in groups:
            key_values = () if not cols else (key if isinstance(key, tuple) else (key,))
            row = _opportunity_metrics_row(group, audit_scope=scope)
            for col, value in zip(cols, key_values, strict=False):
                row[col] = value
            rows.append(row)
    result = pd.DataFrame(rows)
    return _ensure_columns(_sort_frame(result, ["audit_scope", "pump_tier", "session_bucket", "signal_family"]), _opportunity_audit_columns())


def _opportunity_metrics_row(group: pd.DataFrame, *, audit_scope: str) -> dict[str, object]:
    mfe = pd.to_numeric(group.get("mfe_r", pd.Series(dtype=float)), errors="coerce").dropna()
    mae = pd.to_numeric(group.get("mae_r", pd.Series(dtype=float)), errors="coerce").dropna()
    best = pd.to_numeric(group.get("best_exit_r", pd.Series(dtype=float)), errors="coerce").dropna()
    row: dict[str, object] = {
        "audit_scope": audit_scope,
        "signals": int(len(group)),
        "avg_mfe_r": float(mfe.mean()) if not mfe.empty else 0.0,
        "median_mfe_r": float(mfe.median()) if not mfe.empty else 0.0,
        "avg_mae_r": float(mae.mean()) if not mae.empty else 0.0,
        "median_mae_r": float(mae.median()) if not mae.empty else 0.0,
        "avg_best_exit_r": float(best.mean()) if not best.empty else 0.0,
        "median_best_exit_r": float(best.median()) if not best.empty else 0.0,
        "positive_best_exit_rate": float((best > 0).mean()) if not best.empty else 0.0,
    }
    for threshold in OPPORTUNITY_THRESHOLDS_R:
        name = _threshold_name(threshold)
        hits = int((mfe >= float(threshold)).sum()) if not mfe.empty else 0
        row[f"mfe_ge_{name}_signals"] = hits
        row[f"mfe_ge_{name}_rate"] = hits / len(group) if len(group) else 0.0
    return row


def _opportunity_audit_columns() -> list[str]:
    base = [
        "audit_scope",
        "pump_tier",
        "signal_variant",
        "signal_family",
        "session_bucket",
        "confirm_taker_buy_bucket",
        "oi_strength_bucket",
        "post_pump_preconfirm_distribution_bucket",
        "has_lower_high_before_break",
        "signals",
        "avg_mfe_r",
        "median_mfe_r",
        "avg_mae_r",
        "median_mae_r",
        "avg_best_exit_r",
        "median_best_exit_r",
        "positive_best_exit_rate",
    ]
    threshold_cols: list[str] = []
    for threshold in OPPORTUNITY_THRESHOLDS_R:
        name = _threshold_name(threshold)
        threshold_cols.extend([f"mfe_ge_{name}_signals", f"mfe_ge_{name}_rate"])
    return [*base, *threshold_cols]


def _threshold_name(value: float) -> str:
    text = f"{float(value):g}".replace(".", "p")
    return f"{text}r"


def _daily_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", *_metrics_dict(pd.DataFrame()).keys()])
    work = frame.copy()
    if "date" not in work.columns:
        work["date"] = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    rows = []
    cumulative = 0.0
    peak = 0.0
    for date, group in work.groupby("date", dropna=False):
        metrics = _metrics_dict(group)
        cumulative += float(metrics["sum_r"])
        peak = max(peak, cumulative)
        rows.append({"date": date, **metrics, "cumulative_r": cumulative, "drawdown": cumulative - peak})
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _top_dependency_report(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    scopes = [([], "all")]
    for cols, name in [
        (["signal_variant"], "signal_variant"),
        (["signal_family"], "signal_family"),
        (["session_bucket"], "session"),
        (["session_bucket", "signal_family"], "session_family"),
        (["exit_policy"], "exit_policy"),
        (["confirm_taker_buy_bucket"], "confirm_taker_buy_bucket"),
        (["oi_strength_bucket"], "oi_strength_bucket"),
        (["post_pump_preconfirm_distribution_bucket"], "distribution_bucket"),
    ]:
        scopes.append((cols, name))
    if frame.empty:
        return pd.DataFrame(columns=["scope", "key", "top_trade_independence_pct", "top_symbol_independence_pct", "sum_r", "trades"])
    for cols, scope_name in scopes:
        if not cols:
            groups = [("all", frame)]
        else:
            groups = [(key, group) for key, group in frame.groupby(cols, dropna=False)]
        for key, group in groups:
            metrics = _metrics_dict(group)
            rows.append(
                {
                    "scope": scope_name,
                    "key": key if isinstance(key, str) else "|".join(str(part) for part in (key if isinstance(key, tuple) else (key,))),
                    "trades": metrics["trades"],
                    "sum_r": metrics["sum_r"],
                    "top_trade_independence_pct": metrics["top_trade_independence_pct"],
                    "top_symbol_independence_pct": metrics["top_symbol_independence_pct"],
                }
            )
    return pd.DataFrame(rows)


def _top_trade_independence_pct(r: pd.Series) -> float:
    values = pd.to_numeric(r, errors="coerce").dropna().sort_values(ascending=False).to_numpy(dtype=float)
    if len(values) == 0:
        return 0.0
    total = float(values.sum())
    if total <= 0:
        return 0.0
    remaining = total
    for i, value in enumerate(values, start=1):
        remaining -= float(value)
        if remaining <= 0:
            return float(i / len(values))
    return 1.0


def _top_symbol_independence_pct(frame: pd.DataFrame) -> float:
    if frame.empty or "symbol" not in frame.columns or "r_multiple" not in frame.columns:
        return 0.0
    symbol_r = pd.to_numeric(frame["r_multiple"], errors="coerce").groupby(frame["symbol"].astype(str)).sum().sort_values(ascending=False)
    if symbol_r.empty:
        return 0.0
    total = float(symbol_r.sum())
    if total <= 0:
        return 0.0
    remaining = total
    for i, value in enumerate(symbol_r.to_numpy(dtype=float), start=1):
        remaining -= float(value)
        if remaining <= 0:
            return float(i / len(symbol_r))
    return 1.0


def _daily_r(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or "r_multiple" not in frame.columns:
        return pd.Series(dtype=float)
    work = frame.copy()
    if "date" not in work.columns:
        if "entry_timestamp_ms" not in work.columns:
            return pd.Series(dtype=float)
        work["date"] = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    return pd.to_numeric(work["r_multiple"], errors="coerce").groupby(work["date"].astype(str)).sum()


def _max_drawdown(r: pd.Series) -> float:
    values = pd.to_numeric(r, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) == 0:
        return 0.0
    equity = np.cumsum(values)
    peak = np.maximum.accumulate(np.insert(equity, 0, 0.0))[1:]
    dd = equity - peak
    return float(dd.min()) if len(dd) else 0.0


def _remove_top_n_trades(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    if frame.empty or "r_multiple" not in frame.columns:
        return frame
    work = frame.copy()
    r = pd.to_numeric(work["r_multiple"], errors="coerce")
    drop_idx = r.sort_values(ascending=False).head(max(0, int(n))).index
    return work.drop(index=drop_idx)


def _remove_top_n_symbols(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    if frame.empty or "symbol" not in frame.columns or "r_multiple" not in frame.columns:
        return frame
    symbol_r = pd.to_numeric(frame["r_multiple"], errors="coerce").groupby(frame["symbol"].astype(str)).sum().sort_values(ascending=False)
    drop_symbols = set(symbol_r.head(max(0, int(n))).index.astype(str))
    return frame.loc[~frame["symbol"].astype(str).isin(drop_symbols)].copy()


def _seed_1m_shape_features(seed_1m: pd.DataFrame, *, seed_open_ms: int, seed_close_ms: int) -> dict[str, object]:
    base = {
        "seed_1m_quote_top1_share": np.nan,
        "seed_1m_trade_top1_share": np.nan,
        "seed_1m_distribution_bucket": "missing",
        "pump_high_timing_pct": np.nan,
        "seed_first_half_return_pct": np.nan,
        "seed_second_half_return_pct": np.nan,
        "seed_last_2m_return_pct": np.nan,
    }
    if seed_1m.empty:
        return base
    work = seed_1m.sort_values("timestamp").reset_index(drop=True)
    quote_top1 = _top1_share(_numeric_series(work, "quote_volume"))
    trade_top1 = _top1_share(_numeric_series(work, "number_of_trades"))
    high = _numeric_series(work, "high")
    if high.dropna().empty:
        high_timing = np.nan
    else:
        high_ts = int(work.loc[high.idxmax(), "timestamp"])
        high_timing = (high_ts - int(seed_open_ms)) / max(1, int(seed_close_ms) - int(seed_open_ms))
    first = work.iloc[: max(1, len(work) // 2)]
    second = work.iloc[max(0, len(work) // 2) :]
    last2 = work.iloc[-2:]
    return {
        "seed_1m_quote_top1_share": quote_top1,
        "seed_1m_trade_top1_share": trade_top1,
        "seed_1m_distribution_bucket": _distribution_bucket(quote_top1, trade_top1),
        "pump_high_timing_pct": high_timing,
        "seed_first_half_return_pct": _window_return_pct(first),
        "seed_second_half_return_pct": _window_return_pct(second),
        "seed_last_2m_return_pct": _window_return_pct(last2),
    }


def _post_pump_signal_shape_features(
    setup: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    confirm_open_ms: int,
    confirm_close: float,
) -> dict[str, object]:
    seed_open_ms = int(setup["seed_open_ms"])
    seed_close_ms = int(setup["seed_close_ms"])
    structural_low = float(setup.get("structural_low", np.nan))
    seed_close = float(setup.get("seed_close", np.nan))
    seed_pump_high = float(setup.get("pump_high", setup.get("seed_high", np.nan)))
    preconfirm = _time_window(frame_1m, start_ms=seed_close_ms, end_ms=int(confirm_open_ms), include_end=True)
    quote_top1 = _top1_share(_numeric_series(preconfirm, "quote_volume"))
    trade_top1 = _top1_share(_numeric_series(preconfirm, "number_of_trades"))
    high_series = _numeric_series(preconfirm, "high")
    post_high = _safe_max(high_series)
    pump_high = max(seed_pump_high if math.isfinite(seed_pump_high) else 0.0, post_high)
    has_lower_high = bool(post_high > 0 and pump_high > 0 and post_high < pump_high * 0.999)
    failed_retest_distance = (pump_high - post_high) / pump_high if has_lower_high and pump_high > 0 else np.nan
    structural_break_depth = (structural_low - float(confirm_close)) / structural_low if math.isfinite(structural_low) and structural_low > 0 else np.nan
    return {
        "has_lower_high_before_break": has_lower_high,
        "last_lower_high_price": post_high if has_lower_high else np.nan,
        "failed_retest_distance_pct": failed_retest_distance,
        "pump_high_to_confirm_drawdown_pct": float(confirm_close) / pump_high - 1.0 if pump_high > 0 else np.nan,
        "structural_break_depth_pct": structural_break_depth,
        "minutes_from_seed_to_break": (int(confirm_open_ms) - seed_open_ms) / MINUTE_MS,
        "pre_confirm_return_from_seed_close_pct": float(confirm_close) / seed_close - 1.0 if math.isfinite(seed_close) and seed_close > 0 else np.nan,
        "post_pump_preconfirm_quote_top1_share": quote_top1,
        "post_pump_preconfirm_trade_top1_share": trade_top1,
        "post_pump_preconfirm_distribution_bucket": _distribution_bucket(quote_top1, trade_top1),
    }


def _setup_shape_passthrough(setup: dict[str, object]) -> dict[str, object]:
    keys = (
        "pump_tier",
        "is_strong_pump_tier",
        "is_anomaly_pump_tier",
        "is_extreme_pump_tier",
        "seed_1m_quote_top1_share",
        "seed_1m_trade_top1_share",
        "seed_1m_distribution_bucket",
        "pump_high_timing_pct",
        "seed_first_half_return_pct",
        "seed_second_half_return_pct",
        "seed_last_2m_return_pct",
    )
    string_defaults = {"seed_1m_distribution_bucket", "pump_tier"}
    bool_defaults = {"is_strong_pump_tier", "is_anomaly_pump_tier", "is_extreme_pump_tier"}
    return {
        key: setup.get(key, False if key in bool_defaults else ("missing" if key in string_defaults else np.nan))
        for key in keys
    }


def _top1_share(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    total = float(values.sum()) if not values.empty else 0.0
    if total <= 0:
        return float("nan")
    return float(values.max() / total)


def _distribution_bucket(quote_top1_share: float, trade_top1_share: float) -> str:
    values = [value for value in (quote_top1_share, trade_top1_share) if math.isfinite(float(value))]
    if not values:
        return "missing"
    top1 = max(float(value) for value in values)
    if top1 <= 0.25:
        return "distributed_top1_le25"
    if top1 >= 0.60:
        return "one_print_like_top1_ge60"
    return "mixed_top1_25_60"


def _pump_tier(
    *,
    high_return_pct: float,
    quote_ratio: float,
    trade_ratio: float,
    config: FailedPumpShortResearchConfig,
) -> str:
    if (
        high_return_pct >= float(config.extreme_pump_min_high_return_pct)
        and quote_ratio >= float(config.extreme_pump_min_quote_ratio)
        and trade_ratio >= float(config.extreme_pump_min_trade_ratio)
    ):
        return "extreme_pump"
    if (
        high_return_pct >= float(config.anomaly_pump_min_high_return_pct)
        and quote_ratio >= float(config.anomaly_pump_min_quote_ratio)
        and trade_ratio >= float(config.anomaly_pump_min_trade_ratio)
    ):
        return "anomaly_pump"
    if (
        high_return_pct >= float(config.strong_pump_min_high_return_pct)
        and quote_ratio >= float(config.strong_pump_min_quote_ratio)
        and trade_ratio >= float(config.strong_pump_min_trade_ratio)
    ):
        return "strong_pump"
    return "broad"


def _window_return_pct(frame: pd.DataFrame) -> float:
    if frame.empty:
        return float("nan")
    open_price = _float(frame.iloc[0].get("open"))
    close_price = _float(frame.iloc[-1].get("close"))
    if not math.isfinite(open_price) or not math.isfinite(close_price) or open_price <= 0:
        return float("nan")
    return close_price / open_price - 1.0


def _taker_buy_bucket(value: float) -> str:
    if not math.isfinite(float(value)):
        return "missing"
    pct = float(value)
    if pct <= 0.45:
        return "le45"
    if pct <= 0.50:
        return "45_50"
    if pct <= 0.55:
        return "50_55"
    if pct <= 0.60:
        return "55_60"
    if pct <= 0.65:
        return "60_65"
    return "gt65_danger"


def _oi_strength_bucket(value: float, *, weak_threshold_pct: float, strong_threshold_pct: float) -> str:
    if not math.isfinite(float(value)):
        return "missing"
    change = float(value)
    weak = abs(float(weak_threshold_pct))
    strong = abs(float(strong_threshold_pct))
    if change <= -strong:
        return "strong_drop"
    if change <= -weak:
        return "drop"
    if change >= strong:
        return "strong_rise"
    if change >= weak:
        return "rise"
    return "flat"


def _prepare_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for col in ("timestamp", "open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_volume", "taker_buy_quote_volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    if "quote_volume" not in work.columns:
        if "volume" in work.columns and "close" in work.columns:
            work["quote_volume"] = pd.to_numeric(work["volume"], errors="coerce") * pd.to_numeric(work["close"], errors="coerce")
        else:
            work["quote_volume"] = np.nan
    if "number_of_trades" not in work.columns:
        if "trades" in work.columns:
            work["number_of_trades"] = pd.to_numeric(work["trades"], errors="coerce")
        else:
            work["number_of_trades"] = np.nan
    return work.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def _prepare_oi(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for col in ("timestamp", "open_interest", "available_timestamp_ms"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    if "open_interest" not in work.columns:
        return pd.DataFrame()
    return work.loc[work["open_interest"].notna()].sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def _prepare_oi_lookup(frame: pd.DataFrame) -> _OiLookup:
    if frame.empty or "timestamp" not in frame.columns or "open_interest" not in frame.columns:
        empty = np.array([], dtype=np.float64)
        return _OiLookup(timestamp_ms=empty.astype(np.int64), available_ms=empty.astype(np.int64), open_interest=empty)
    ts = pd.to_numeric(frame["timestamp"], errors="coerce")
    oi = pd.to_numeric(frame["open_interest"], errors="coerce")
    if "available_timestamp_ms" in frame.columns:
        available = pd.to_numeric(frame["available_timestamp_ms"], errors="coerce")
    else:
        available = ts + FIVE_MINUTE_MS
    work = pd.DataFrame({"timestamp": ts, "available": available, "open_interest": oi})
    work = work.loc[work["timestamp"].notna() & work["available"].notna() & work["open_interest"].notna()]
    if work.empty:
        empty = np.array([], dtype=np.float64)
        return _OiLookup(timestamp_ms=empty.astype(np.int64), available_ms=empty.astype(np.int64), open_interest=empty)
    work = work.sort_values("available").drop_duplicates("available", keep="last")
    return _OiLookup(
        timestamp_ms=work["timestamp"].to_numpy(dtype=np.int64, copy=True),
        available_ms=work["available"].to_numpy(dtype=np.int64, copy=True),
        open_interest=work["open_interest"].to_numpy(dtype=np.float64, copy=True),
    )


def _has_real_flow(frame: pd.DataFrame) -> bool:
    return "quote_volume" in frame.columns and "number_of_trades" in frame.columns and "taker_buy_quote_volume" in frame.columns


def _taker_buy_share(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or "taker_buy_quote_volume" not in frame.columns or "quote_volume" not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    quote = pd.to_numeric(frame["quote_volume"], errors="coerce")
    taker = pd.to_numeric(frame["taker_buy_quote_volume"], errors="coerce")
    return taker / quote.replace(0, np.nan)


def _candle_taker_buy_share(candle: pd.Series) -> float:
    quote = _float(candle.get("quote_volume"))
    taker = _float(candle.get("taker_buy_quote_volume"))
    if not math.isfinite(quote) or quote <= 0 or not math.isfinite(taker):
        return float("nan")
    return taker / quote


def parse_research_end_timestamp_ms(value: object) -> int | None:
    """Parse CLI end timestamp.

    ``latest-cache``/``latest``/empty means: choose the latest closed 5m cache
    timestamp by reading parquet metadata/timestamp columns only. Date-only input
    means the end of that UTC day.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"latest", "latest-cache", "cache-latest"}:
        return None
    if text.isdigit():
        raw = int(text)
        return raw * 1000 if raw < 10_000_000_000 else raw
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        dt = datetime.fromisoformat(text).replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000) + DAY_MS - 1
    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _load_frame_result(storage: ParquetStorage, symbol: str, timeframe: str, *, start_ms: int, end_ms: int) -> ParquetLoadResult:
    tf = Timeframe(timeframe)
    return storage.load_window_result(symbol, tf, int(start_ms), int(end_ms))


def _resolve_symbols(cache_dir: Path, symbols: Iterable[str] | None) -> list[str]:
    """Resolve symbols without losing the exact cache path identity.

    The futures cache is commonly stored as encoded CCXT symbols such as
    ``BTC%2FUSDT%3AUSDT`` (``BTC/USDT:USDT``).  ``normalize_symbol`` strips the
    settlement suffix for comparison, but using the normalized value for storage
    lookup changes the path to ``BTC%2FUSDT`` and makes an existing cache look
    empty.  Return the exact decoded cache symbol for reads, while allowing
    explicit user symbols to match by normalized form.
    """

    base = Path(cache_dir)
    if not base.exists():
        return []

    available: dict[str, str] = {}
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        has_5m = (path / "5m" / "data.parquet").exists() or (path / "5m" / "delta").exists()
        has_1m = (path / "1m" / "data.parquet").exists() or (path / "1m" / "delta").exists()
        if not (has_5m and has_1m):
            continue
        decoded = ParquetStorage.decode_symbol_from_path(path.name)
        normalized = normalize_symbol(decoded)
        # Keep the exact cache symbol value as the read key.  If duplicate cache
        # dirs normalize to the same market, prefer the lexicographically first
        # decoded path for deterministic runs.
        available.setdefault(normalized, decoded)

    requested = [normalize_symbol(str(symbol)) for symbol in symbols or [] if str(symbol).strip()]
    if requested:
        return sorted({available[value] for value in requested if value in available})
    return sorted(available.values())


def _resolve_end_timestamp_ms(config: FailedPumpShortResearchConfig, cache_coverage: dict[str, object]) -> int:
    if config.end_timestamp_ms is not None:
        return int(config.end_timestamp_ms)
    latest = _finite_int_or_none(cache_coverage.get("max_5m_timestamp_ms"))
    if latest is None:
        raise RuntimeError(
            "Cannot resolve research end time from cache: no readable 5m timestamp coverage. "
            f"cache_dir={Path(config.cache_dir)}; cache is not modified by this command."
        )
    return int(latest)


def _cache_coverage_summary(cache_dir: Path, symbols: Sequence[str]) -> dict[str, object]:
    probes_5m = [_cache_timeframe_probe(cache_dir, symbol, Timeframe.M5) for symbol in symbols]
    probes_1m = [_cache_timeframe_probe(cache_dir, symbol, Timeframe.M1) for symbol in symbols]
    first_5m = [value for value in (_finite_int_or_none(row.get("first_timestamp_ms")) for row in probes_5m) if value is not None]
    last_5m = [value for value in (_finite_int_or_none(row.get("last_timestamp_ms")) for row in probes_5m) if value is not None]
    first_1m = [value for value in (_finite_int_or_none(row.get("first_timestamp_ms")) for row in probes_1m) if value is not None]
    last_1m = [value for value in (_finite_int_or_none(row.get("last_timestamp_ms")) for row in probes_1m) if value is not None]
    return {
        "cache_dir": str(Path(cache_dir)),
        "cache_read_mode": "read_only",
        "probed_symbols": int(len(symbols)),
        "symbols_with_5m_timestamp": int(len(last_5m)),
        "symbols_with_1m_timestamp": int(len(last_1m)),
        "min_5m_timestamp_ms": min(first_5m) if first_5m else np.nan,
        "max_5m_timestamp_ms": max(last_5m) if last_5m else np.nan,
        "min_1m_timestamp_ms": min(first_1m) if first_1m else np.nan,
        "max_1m_timestamp_ms": max(last_1m) if last_1m else np.nan,
        "min_5m_time_utc": _fmt_ts(min(first_5m)) if first_5m else "",
        "max_5m_time_utc": _fmt_ts(max(last_5m)) if last_5m else "",
        "min_1m_time_utc": _fmt_ts(min(first_1m)) if first_1m else "",
        "max_1m_time_utc": _fmt_ts(max(last_1m)) if last_1m else "",
        "cache_probe_model": "read_parquet_timestamp_column_only_no_writes",
    }


def _cache_timeframe_probe(cache_dir: Path, symbol: str, timeframe: Timeframe) -> dict[str, object]:
    encoded = ParquetStorage.encode_symbol_for_path(symbol)
    tf_dir = Path(cache_dir) / encoded / timeframe.value
    base_path = tf_dir / "data.parquet"
    delta_dir = tf_dir / "delta"
    timestamp_frames: list[pd.Series] = []
    status_parts: list[str] = []
    paths_read = 0

    paths = [base_path]
    if delta_dir.exists():
        paths.extend(sorted(delta_dir.glob("*.parquet")))
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            ts = pd.read_parquet(path, columns=["timestamp"])["timestamp"]
        except Exception as exc:
            status_parts.append(f"{path.name}:read_failed:{type(exc).__name__}")
            continue
        paths_read += 1
        numeric = pd.to_numeric(ts, errors="coerce").dropna()
        if not numeric.empty:
            timestamp_frames.append(numeric.astype("int64"))

    if not timestamp_frames:
        status = "missing_or_no_timestamp"
        if status_parts:
            status = ";".join(status_parts[:3])
        return {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "path": str(base_path),
            "paths_read": int(paths_read),
            "status": status,
            "first_timestamp_ms": np.nan,
            "last_timestamp_ms": np.nan,
        }
    merged = pd.concat(timestamp_frames, ignore_index=True)
    return {
        "symbol": symbol,
        "timeframe": timeframe.value,
        "path": str(base_path),
        "paths_read": int(paths_read),
        "status": "ok",
        "first_timestamp_ms": int(merged.min()),
        "last_timestamp_ms": int(merged.max()),
    }


def _data_quality_row(
    *,
    symbol: str,
    frame_5m: pd.DataFrame,
    frame_1m: pd.DataFrame,
    oi_5m: pd.DataFrame,
    frame_5m_result: ParquetLoadResult,
    frame_1m_result: ParquetLoadResult,
    oi_5m_result: ParquetLoadResult,
    load_start_ms: int,
    load_end_ms: int,
) -> dict[str, object]:
    return {
        "research_id": RESEARCH_ID,
        "symbol": symbol,
        "cache_read_mode": "read_only",
        "cache_write_model": "no_cache_writes_outputs_only_to_results_dir",
        "load_start_timestamp_ms": int(load_start_ms),
        "load_end_timestamp_ms": int(load_end_ms),
        "load_start_time_utc": _fmt_ts(load_start_ms),
        "load_end_time_utc": _fmt_ts(load_end_ms),
        "5m_rows": int(len(frame_5m)),
        "1m_rows": int(len(frame_1m)),
        "5m_oi_rows": int(len(oi_5m.loc[oi_5m.get("open_interest", pd.Series(dtype=float)).notna()])) if not oi_5m.empty and "open_interest" in oi_5m.columns else 0,
        "5m_load_ok": bool(frame_5m_result.ok),
        "1m_load_ok": bool(frame_1m_result.ok),
        "5m_load_status": str(frame_5m_result.status),
        "1m_load_status": str(frame_1m_result.status),
        "5m_load_reason": str(frame_5m_result.reason),
        "1m_load_reason": str(frame_1m_result.reason),
        "5m_attempted_path": str(frame_5m_result.path),
        "1m_attempted_path": str(frame_1m_result.path),
        "5m_loaded_first_timestamp_ms": _frame_min_timestamp(frame_5m),
        "5m_loaded_last_timestamp_ms": _frame_max_timestamp(frame_5m),
        "1m_loaded_first_timestamp_ms": _frame_min_timestamp(frame_1m),
        "1m_loaded_last_timestamp_ms": _frame_max_timestamp(frame_1m),
        "5m_loaded_first_time_utc": _fmt_optional_ts(_frame_min_timestamp(frame_5m)),
        "5m_loaded_last_time_utc": _fmt_optional_ts(_frame_max_timestamp(frame_5m)),
        "1m_loaded_first_time_utc": _fmt_optional_ts(_frame_min_timestamp(frame_1m)),
        "1m_loaded_last_time_utc": _fmt_optional_ts(_frame_max_timestamp(frame_1m)),
        "5m_has_quote_volume": bool("quote_volume" in frame_5m.columns),
        "5m_has_number_of_trades": bool("number_of_trades" in frame_5m.columns),
        "1m_has_quote_volume": bool("quote_volume" in frame_1m.columns),
        "1m_has_number_of_trades": bool("number_of_trades" in frame_1m.columns),
        "1m_has_taker_buy_quote_volume": bool("taker_buy_quote_volume" in frame_1m.columns),
        "oi_load_ok": bool(oi_5m_result.ok),
        "oi_load_status": str(oi_5m_result.status),
        "oi_load_reason": str(oi_5m_result.reason),
        "oi_attempted_path": str(oi_5m_result.path),
        "oi_model": OI_MODEL,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _frame_min_timestamp(frame: pd.DataFrame) -> int | float:
    if frame.empty or "timestamp" not in frame.columns:
        return np.nan
    values = pd.to_numeric(frame["timestamp"], errors="coerce").dropna()
    return int(values.min()) if not values.empty else np.nan


def _frame_max_timestamp(frame: pd.DataFrame) -> int | float:
    if frame.empty or "timestamp" not in frame.columns:
        return np.nan
    values = pd.to_numeric(frame["timestamp"], errors="coerce").dropna()
    return int(values.max()) if not values.empty else np.nan


def _fmt_optional_ts(value: object) -> str:
    parsed = _finite_int_or_none(value)
    return _fmt_ts(parsed) if parsed is not None else ""


def _finite_int_or_none(value: object) -> int | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return int(parsed)


def _fail_fast_on_empty_input(*, config: FailedPumpShortResearchConfig, quality: pd.DataFrame) -> None:
    if not bool(config.fail_on_empty_input):
        return
    total_5m = int(pd.to_numeric(quality.get("5m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    total_1m = int(pd.to_numeric(quality.get("1m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    if total_5m <= 0 or total_1m <= 0:
        raise RuntimeError(
            "Failed-pump short research loaded no usable market cache rows "
            f"for the requested window: total_5m_rows={total_5m}, total_1m_rows={total_1m}. "
            f"Diagnostics were written to {Path(config.output_dir)}. Cache was read-only and was not modified."
        )


def _run_config_frame(
    *,
    config: FailedPumpShortResearchConfig,
    selected_symbols: Sequence[str],
    start_ms: int,
    end_ms: int,
    started_at: float,
    setups: pd.DataFrame,
    signals: pd.DataFrame,
    trade_grid: pd.DataFrame,
    quality: pd.DataFrame,
    cache_coverage: dict[str, object],
) -> pd.DataFrame:
    total_5m_rows = int(pd.to_numeric(quality.get("5m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    total_1m_rows = int(pd.to_numeric(quality.get("1m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    total_5m_oi_rows = int(pd.to_numeric(quality.get("5m_oi_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    row = {
        **asdict(config),
        "research_id": RESEARCH_ID,
        "data_access_model": DATA_ACCESS_MODEL,
        "cache_read_mode": "read_only",
        "cache_write_model": "no_cache_writes_outputs_only_to_results_dir",
        "cache_dir": str(Path(config.cache_dir)),
        "cache_probe_model": str(cache_coverage.get("cache_probe_model", "")),
        "cache_symbols_with_5m_timestamp": int(cache_coverage.get("symbols_with_5m_timestamp", 0)),
        "cache_symbols_with_1m_timestamp": int(cache_coverage.get("symbols_with_1m_timestamp", 0)),
        "cache_min_5m_timestamp_ms": cache_coverage.get("min_5m_timestamp_ms", np.nan),
        "cache_max_5m_timestamp_ms": cache_coverage.get("max_5m_timestamp_ms", np.nan),
        "cache_min_1m_timestamp_ms": cache_coverage.get("min_1m_timestamp_ms", np.nan),
        "cache_max_1m_timestamp_ms": cache_coverage.get("max_1m_timestamp_ms", np.nan),
        "cache_min_5m_time_utc": str(cache_coverage.get("min_5m_time_utc", "")),
        "cache_max_5m_time_utc": str(cache_coverage.get("max_5m_time_utc", "")),
        "cache_min_1m_time_utc": str(cache_coverage.get("min_1m_time_utc", "")),
        "cache_max_1m_time_utc": str(cache_coverage.get("max_1m_time_utc", "")),
        "total_loaded_5m_rows": total_5m_rows,
        "total_loaded_1m_rows": total_1m_rows,
        "total_loaded_5m_oi_rows": total_5m_oi_rows,
        "entry_model": ENTRY_MODEL,
        "oi_model": OI_MODEL,
        "future_label_available_at_entry": False,
        "short_confirm_closed_before_entry": True,
        "symbols": ",".join(selected_symbols),
        "symbol_count": int(len(selected_symbols)),
        "start_timestamp_ms": int(start_ms),
        "end_timestamp_ms": int(end_ms),
        "start_time_utc": _fmt_ts(start_ms),
        "end_time_utc": _fmt_ts(end_ms),
        "setups": int(len(setups)),
        "signals": int(len(signals)),
        "trades": int(len(trade_grid)),
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "signal_families": ",".join(SIGNAL_FAMILIES),
        "signal_variants": ",".join(SIGNAL_VARIANTS),
        "exit_policies": ",".join(EXIT_POLICIES),
        "session_buckets": ",".join(SESSION_BUCKETS),
        "rolling_windows": ",".join(str(v) for v in ROLLING_WINDOWS),
        "simple_fade_baseline_selection_eligible": False,
    }
    for key, value in list(row.items()):
        if isinstance(value, Path):
            row[key] = str(value)
    return pd.DataFrame([row])


def _rolling_run_config_frame(*, config: FailedPumpShortResearchConfig, trade_grid: pd.DataFrame, rolling: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "research_id": RESEARCH_ID,
                "rolling_model": "daily_walk_forward_train_15_30_60_test_one_day_no_day_overlap",
                "selection_model": "session_specific_rule_selection_by_past_30d_status_core_strong_tactical",
                "selection_eligible_only": True,
                "all_session_rules_evaluated_for_health_only": True,
                "extended_flow_oi_distribution_rules_evaluated_for_health_only": True,
                "shadow_oos_audit_model": "best_extended_30d_rules_tested_oos_not_used_for_trading_selection",
                "shadow_oos_max_rules_per_day": int(config.shadow_oos_max_rules_per_day),
                "shadow_oos_min_train_trades": int(config.shadow_oos_min_train_trades),
                "shadow_oos_min_train_active_days": int(config.shadow_oos_min_train_active_days),
                "shadow_oos_min_train_sum_r": float(config.shadow_oos_min_train_sum_r),
                "simple_fade_baseline_selection_eligible": False,
                "train_windows": ",".join(str(v) for v in ROLLING_WINDOWS),
                "entry_model": ENTRY_MODEL,
                "oi_model": OI_MODEL,
                "future_label_available_at_entry": False,
                "short_confirm_closed_before_entry": True,
                "source_trades": int(len(trade_grid)),
                "oos_trades": int(len(rolling.get("oos_trades", pd.DataFrame()))),
                "shadow_oos_audit_rows": int(len(rolling.get("shadow_oos_audit", pd.DataFrame()))),
                "empty_oos_trades_csv_has_headers": True,
                "incomplete_train_windows_never_select_trades": True,
                "min_train_trades": int(config.min_train_trades),
                "min_train_active_days": int(config.min_train_active_days),
                "statuses": "core,strong,tactical,challenger,cooldown,rejected",
            }
        ]
    )


def _honesty_fields() -> dict[str, object]:
    return {
        "future_label_available_at_entry": False,
        "short_confirm_closed_before_entry": True,
        "entry_model": ENTRY_MODEL,
        "oi_model": OI_MODEL,
    }


def _session_features(timestamp_ms: int) -> dict[str, object]:
    dt = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC)
    hour = int(dt.hour)
    asia = 0 <= hour < 8
    europe = 7 <= hour < 16
    us = 13 <= hour < 22
    if asia and europe and not us:
        bucket = "asia_europe_overlap"
    elif europe and us and not asia:
        bucket = "europe_us_overlap"
    elif asia and not europe and not us:
        bucket = "asia_only"
    elif europe and not asia and not us:
        bucket = "europe_only"
    elif us and not asia and not europe:
        bucket = "us_only"
    else:
        bucket = "off_session"
    active = [name for name, flag in (("asia", asia), ("europe", europe), ("us", us)) if flag]
    return {
        "session_bucket": bucket,
        "session_primary": active[-1] if active else "off_session",
        "session_overlap": bool(len(active) >= 2),
        "hour_utc": hour,
        "weekday": int(dt.weekday()),
    }


def _safe_median(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return 0.0
    return float(values.median())


def _safe_max(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return 0.0
    return float(values.max())


def _time_window(frame: pd.DataFrame, *, start_ms: int, end_ms: int, include_end: bool) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns:
        return frame.iloc[0:0]
    ts = frame["timestamp"].to_numpy(dtype="int64", copy=False)
    left = int(np.searchsorted(ts, int(start_ms), side="left"))
    right_side = "right" if include_end else "left"
    right = int(np.searchsorted(ts, int(end_ms), side=right_side))
    if right <= left:
        return frame.iloc[0:0]
    # Keep original index and avoid reset_index/copy here.  This function is on
    # the hottest path; callers use iloc or label-aware loc after idxmin/idxmax,
    # so preserving the parent index is safe and substantially faster.
    return frame.iloc[left:right]


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _float(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return parsed


def _sort_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame
    existing = [col for col in columns if col in frame.columns]
    if not existing:
        return frame.reset_index(drop=True)
    return frame.sort_values(existing).reset_index(drop=True)


def _print_stage(label: str, stage: str, started_at: float) -> None:
    print(f"{label}: {stage} elapsed={_format_duration(time.monotonic() - started_at)}", flush=True)


def _reset_partial_artifacts(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in [*PARTIAL_ARTIFACT_FILES.values(), PROGRESS_LOG_FILE]:
        path = output_dir / filename
        if path.exists() and path.is_file():
            path.unlink()


def _flush_partial_artifacts(
    *,
    output_dir: Path,
    setups: list[dict[str, object]],
    signals: list[dict[str, object]],
    trades: list[dict[str, object]],
    quality: list[dict[str, object]],
) -> None:
    _append_rows_csv(output_dir / PARTIAL_ARTIFACT_FILES["setups"], setups)
    _append_rows_csv(output_dir / PARTIAL_ARTIFACT_FILES["signals"], signals)
    _append_rows_csv(output_dir / PARTIAL_ARTIFACT_FILES["trades"], trades)
    _append_rows_csv(output_dir / PARTIAL_ARTIFACT_FILES["quality"], quality)


def _append_rows_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(
        path,
        mode="a",
        header=not path.exists(),
        index=False,
        encoding="utf-8-sig",
    )


def _write_progress_event(
    output_dir: Path,
    *,
    stage: str,
    message: str,
    elapsed_seconds: float,
    symbols: int | None = None,
    setups: int | None = None,
    signals: int | None = None,
    trades: int | None = None,
    oos_trades: int | None = None,
) -> None:
    row = {
        "time_utc": _fmt_ts(int(datetime.now(tz=UTC).timestamp() * 1000)),
        "stage": stage,
        "message": message,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "symbols": "" if symbols is None else int(symbols),
        "setups": "" if setups is None else int(setups),
        "signals": "" if signals is None else int(signals),
        "trades": "" if trades is None else int(trades),
        "oos_trades": "" if oos_trades is None else int(oos_trades),
    }
    _append_rows_csv(output_dir / PROGRESS_LOG_FILE, [row])


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig", chunksize=100_000)


def _fmt_ts(timestamp_ms: int | float | object) -> str:
    try:
        if pd.isna(timestamp_ms):
            return ""
        value = int(timestamp_ms)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_from_day_ord(day_ord: int) -> str:
    return datetime.fromtimestamp(int(day_ord) * DAY_MS / 1000, tz=UTC).strftime("%Y-%m-%d")


def _compact_symbol(symbol: str) -> str:
    return normalize_symbol(symbol).replace("/", "").replace(":", "_")


def _safe_console_text(value: object) -> str:
    return str(value).encode("ascii", errors="replace").decode("ascii")


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"
