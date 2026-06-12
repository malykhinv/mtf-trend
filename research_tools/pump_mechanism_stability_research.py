"""Cache-only pump mechanism stability research.

This module is the upstream layer for failed-pump short research.  It builds an
immutable broad-pump event store and a separate future-response outcome store.
No PnL, short entry, final-holdout tuning, or threshold optimization is done in
this layer: later patches add mechanism taxonomy, full negative-space plateau
accounting, and daily prequential OOS replay.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
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
from research_tools.failed_pump_short_research import parse_research_end_timestamp_ms
from utils.symbols import normalize_symbol

__all__ = (
    "PumpMechanismStabilityConfig",
    "parse_research_end_timestamp_ms",
    "run_pump_mechanism_stability_research",
)

MINUTE_MS = 60_000
FIVE_MINUTE_MS = 5 * MINUTE_MS
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS

RESEARCH_ID = "pump_mechanism_stability_research_v1"
DATA_ACCESS_MODEL = "cache_only_no_exchange_fetch"
CACHE_READ_MODE = "read_only"
CACHE_WRITE_MODEL = "no_cache_writes_outputs_only_to_results_dir"
IMPLEMENTATION_STAGE = "mechanism_daily_prequential_oos_with_audits"

# The daily replay contract is fixed here so it is visible before the heavier
# taxonomy/plateau implementation lands.  Do not expose these as CLI optimization
# knobs: changing them must create an explicit config/code diff.
ROLLING_WINDOWS_DAYS = (15, 30, 60)
PRIMARY_EVALUATION_MODEL = "daily_prequential_train_past_test_day"
PLATEAU_ACCOUNTING_MODEL = "full_neighborhood_with_negative_space"
FUTURE_OUTCOME_USAGE_MODEL = "outcomes_for_response_analysis_only_not_entry_features"
OI_MODEL = "closed_5m_oi_asof_feature_cutoff"
FEATURE_SNAPSHOT_MODEL = "closed_1m_15m_acceptance_snapshot_after_broad_5m_seed"
EVENT_MODEL = "broad_5m_pump_awakening_event_store"
OUTCOME_MODEL = "future_response_vectors_after_feature_cutoff_only"
SESSION_BUCKETS = (
    "asia_only",
    "asia_europe_overlap",
    "europe_only",
    "europe_us_overlap",
    "us_only",
    "off_session",
)
OUTCOME_HORIZONS_MINUTES = (5, 10, 15, 30, 60)

# Deterministic mechanism taxonomy constants.  These are broad descriptive
# boundaries, not tuned trading thresholds.  They convert entry-known pump
# features into market-nature axes before any PnL, rule scoring, or OOS
# selection is allowed.
ACCEPTED_HIGH_CLOSE_TO_HIGH_MIN = 0.70
PARTIAL_ACCEPTANCE_CLOSE_TO_HIGH_MIN = 0.35
FAILED_ACCEPTANCE_CLOSE_TO_HIGH_MAX = 0.35
IMMEDIATE_REJECTION_CLOSE_TO_HIGH_MAX = 0.15
WICK_WITHOUT_ACCEPTANCE_MIN = 0.012
BUYER_DOMINANT_TAKER_SHARE_MIN = 0.55
SELLER_ABSORPTION_TAKER_SHARE_MIN = 0.55
LATE_BUYER_LAST2_SHARE_MIN = 0.34
LATE_ACTIVITY_LAST2_SHARE_MIN = 0.34
LATE_ACCELERATION_MIN = 1.80
EARLY_FRONT_LOADED_LAST2_SHARE_MAX = 0.18
HIGH_FLOW_RATIO_MIN = 3.0
HIGH_VOLUME_LOW_PROGRESS_CLOSE_TO_HIGH_MAX = 0.35
SEED_LOW_BREAK_BUFFER_PCT = 0.0005
DEEP_RETRACE_TO_SEED_RETURN_FRACTION = 0.25

# Train-only rule grammar constants.  These generate candidate rule specs from
# entry-known feature distributions inside each rolling train window.  They are
# descriptive rule-construction axes, not optimized performance knobs.
MIN_TRAIN_SCOPE_EVENTS = 20
MIN_RULE_EVENTS = 8
MIN_RULE_SYMBOLS = 3
MIN_RULE_ACTIVE_DAYS = 3
RULE_QUANTILES = (0.25, 0.35, 0.50, 0.65, 0.75)
RULE_THRESHOLD_FEATURES: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("close15_to_high15_ratio", "le", (0.25, 0.35, 0.50)),
    ("wick_ret_15m", "ge", (0.50, 0.65, 0.75)),
    ("pre60_range_pct", "ge", (0.50, 0.65, 0.75)),
    ("seed_high_return_pct", "ge", (0.50, 0.65, 0.75)),
    ("early_quote_ratio_60m_scaled", "ge", (0.50, 0.65, 0.75)),
    ("early_trade_ratio_60m_scaled", "ge", (0.50, 0.65, 0.75)),
    ("early_taker_buy_quote_share", "ge", (0.50, 0.65, 0.75)),
    ("m1_last2_quote_share", "ge", (0.50, 0.65, 0.75)),
    ("m1_quote_accel_last2_vs_first2", "ge", (0.50, 0.65, 0.75)),
    ("oi_change_5m_pct", "abs_ge", (0.50, 0.65, 0.75)),
)
RULE_SCOPE_AXES: tuple[str, ...] = (
    "mechanism_family",
    "acceptance_regime",
    "oi_regime",
    "flow_regime",
    "structure_regime",
    "late_buyer_regime",
    "session_bucket",
)
RULE_PAIR_SCOPES: tuple[tuple[str, str], ...] = (
    ("mechanism_family", "session_bucket"),
    ("acceptance_regime", "session_bucket"),
    ("oi_regime", "acceptance_regime"),
    ("mechanism_family", "oi_regime"),
)
NEGATIVE_SPACE_MODEL = "train_only_full_generated_neighbor_space_including_rejected_v1"
NEGATIVE_SPACE_THRESHOLD_RADIUS_STEPS = 2
MAX_SCOPE_REPLACEMENT_VALUES_PER_AXIS = 12

PLATEAU_BASIN_MODEL = "train_only_basin_score_from_full_negative_space_v1"
PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD = -0.0075
PLATEAU_MIN_NEIGHBORS = 3
PLATEAU_MIN_NEIGHBOR_SURVIVAL_RATE = 0.55
PLATEAU_STRONG_NEIGHBOR_SURVIVAL_RATE = 0.70
PLATEAU_SIGN_CONSISTENCY_MIN = 0.60
PLATEAU_STRONG_SIGN_CONSISTENCY_MIN = 0.70
PLATEAU_P25_SCORE_MIN = 0.0

DAILY_PREQUENTIAL_OOS_MODEL = "daily_prequential_oos_selected_basins_v1"
DAILY_SELECTION_MODEL = "select_train_only_plateau_basins_for_test_day_v1"
WINDOW_HEALTH_MODEL = "daily_window_health_from_train_only_basins_and_oos_v1"
SELECTION_DRIFT_MODEL = "selected_basin_key_drift_over_prequential_days_v1"
PROTOCOL_AUDIT_MODEL = "pump_mechanism_no_lookahead_negative_space_daily_replay_audit_v1"
MAX_SELECTED_BASINS_PER_DAY_WINDOW = 20
DAILY_ALLOWED_BASIN_STATUSES = ("strong_candidate", "tactical", "challenger")


@dataclass(frozen=True, slots=True)
class PumpMechanismStabilityConfig:
    """Configuration for the pump mechanism stability research command.

    Only operational parameters belong here.  Research thresholds and scoring
    gates are constants in code so historical runs are reproducible and cannot
    be tuned from the command line after seeing OOS/final artifacts.
    """

    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    output_dir: Path = Path(DEFAULT_RESULTS_DIR) / "pump_mechanism_stability_research"
    days: int = 365
    end_timestamp_ms: int | None = None
    symbol_workers: int = min(4, max(1, os.cpu_count() or 1))
    fail_on_empty_input: bool = True

    # Broad pump universe.  These are intentionally broad discovery constants,
    # not optimized rule parameters.  They define the event store population.
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

    # Mechanism feature snapshot and future response windows.  Outcomes are
    # always measured after feature_snapshot_minutes, never from the seed close.
    feature_snapshot_minutes: int = 15
    structural_lookback_minutes: int = 30
    pre_context_minutes: int = 60
    max_outcome_minutes: int = 60
    oi_change_threshold_pct: float = 0.0015
    oi_strong_change_threshold_pct: float = 0.0040

    def __post_init__(self) -> None:
        if int(self.days) <= 0:
            raise ValueError("days must be > 0")
        if int(self.symbol_workers) <= 0:
            raise ValueError("symbol_workers must be > 0")
        if int(self.baseline_5m_candles) <= 0:
            raise ValueError("baseline_5m_candles must be > 0")
        if int(self.feature_snapshot_minutes) <= 0:
            raise ValueError("feature_snapshot_minutes must be > 0")
        if int(self.max_outcome_minutes) < max(OUTCOME_HORIZONS_MINUTES):
            raise ValueError("max_outcome_minutes must cover all outcome horizons")
        if not (
            0
            < float(self.strong_pump_min_high_return_pct)
            <= float(self.anomaly_pump_min_high_return_pct)
            <= float(self.extreme_pump_min_high_return_pct)
        ):
            raise ValueError("pump tier high-return thresholds must be positive and ordered")
        if not (
            float(self.broad_min_quote_ratio)
            <= float(self.strong_pump_min_quote_ratio)
            <= float(self.anomaly_pump_min_quote_ratio)
            <= float(self.extreme_pump_min_quote_ratio)
        ):
            raise ValueError("pump tier quote-ratio thresholds must be ordered")
        if not (
            float(self.broad_min_trade_ratio)
            <= float(self.strong_pump_min_trade_ratio)
            <= float(self.anomaly_pump_min_trade_ratio)
            <= float(self.extreme_pump_min_trade_ratio)
        ):
            raise ValueError("pump tier trade-ratio thresholds must be ordered")


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


def run_pump_mechanism_stability_research(
    config: PumpMechanismStabilityConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str = "pump mechanism stability research",
) -> Path:
    """Build broad pump mechanism events and outcome vectors from cache only."""

    started_at = time.monotonic()
    selected_symbols_arg = tuple(str(symbol).strip() for symbol in (symbols or ()) if str(symbol).strip())
    config.output_dir.mkdir(parents=True, exist_ok=True)

    _print_stage(progress_label, "resolving symbols and cache coverage", started_at)
    selected_symbols = tuple(_resolve_symbols(config.cache_dir, selected_symbols_arg))
    if not selected_symbols:
        run_config = _run_config_frame(
            config=config,
            selected_symbols=selected_symbols,
            start_ms=np.nan,
            end_ms=np.nan,
            started_at=started_at,
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            quality=pd.DataFrame(),
            cache_coverage={},
            taxonomy=pd.DataFrame(),
            response_surfaces=pd.DataFrame(),
            rule_universe=pd.DataFrame(),
            negative_space=pd.DataFrame(),
            plateau_basins=pd.DataFrame(),
            daily_selection=pd.DataFrame(),
            daily_oos=pd.DataFrame(),
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        )
        _write_csv(config.output_dir / "pump_mechanism_run_config.csv", run_config)
        _write_csv(config.output_dir / "pump_mechanism_protocol_audit.csv", _build_protocol_audit(
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            taxonomy=pd.DataFrame(),
            rule_universe=pd.DataFrame(),
            negative_space=pd.DataFrame(),
            plateau_basins=pd.DataFrame(),
            daily_selection=pd.DataFrame(),
            daily_oos=pd.DataFrame(),
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        ))
        _write_csv(config.output_dir / "pump_mechanism_artifact_manifest.csv", _artifact_manifest_frame(config=config))
        raise RuntimeError(
            "No symbols with both 1m and 5m cache were found. "
            f"cache_dir={Path(config.cache_dir)}; cache is not modified by this command."
        )

    cache_coverage = _cache_coverage_summary(config.cache_dir, selected_symbols)
    end_ms = _resolve_end_timestamp_ms(config, cache_coverage)
    start_ms = int(end_ms) - int(config.days) * DAY_MS
    warmup_ms = max(2 * DAY_MS, int(config.baseline_5m_candles) * FIVE_MINUTE_MS)
    load_start_ms = int(start_ms) - int(warmup_ms)
    load_end_ms = int(end_ms) + (int(config.feature_snapshot_minutes) + int(config.max_outcome_minutes) + 10) * MINUTE_MS
    print(
        f"{progress_label}: window={_fmt_ts(start_ms)}..{_fmt_ts(end_ms)} "
        f"symbols={len(selected_symbols)} latest_cache_5m={cache_coverage.get('max_5m_time_utc', '')}",
        flush=True,
    )

    storage = ParquetStorage(config.cache_dir)
    all_events: list[dict[str, object]] = []
    all_outcomes: list[dict[str, object]] = []
    all_quality: list[dict[str, object]] = []
    progress = _ProgressLine(progress_label, len(selected_symbols))

    def _consume(index: int, symbol: str, result: dict[str, list[dict[str, object]]]) -> None:
        progress.update(index=index, item=symbol)
        all_events.extend(result["events"])
        all_outcomes.extend(result["outcomes"])
        all_quality.extend(result["quality"])

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
            _consume(index, symbol, result)
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
                _consume(index, symbol, future.result())
    progress.finish()

    _print_stage(progress_label, "building event/outcome dataframes", started_at)
    events = _ensure_columns(pd.DataFrame(all_events), _event_columns())
    outcomes = _ensure_columns(pd.DataFrame(all_outcomes), _outcome_columns())
    quality = _ensure_columns(pd.DataFrame(all_quality), _quality_columns())
    events = _sort_frame(events, ["feature_cutoff_ms", "symbol", "event_id"])
    outcomes = _sort_frame(outcomes, ["feature_cutoff_ms", "symbol", "event_id"])
    quality = _sort_frame(quality, ["symbol"])

    _fail_fast_on_empty_input(config=config, events=events, quality=quality)

    _print_stage(progress_label, "classifying entry-known mechanism taxonomy", started_at)
    taxonomy = _build_mechanism_taxonomy(events)
    taxonomy_by_axis = _taxonomy_by_axis_frame(taxonomy)

    _print_stage(progress_label, "building pre-trade mechanism response surfaces", started_at)
    response_surfaces = _build_response_surfaces(taxonomy=taxonomy, outcomes=outcomes)

    _print_stage(progress_label, "generating train-only mechanism rule universe", started_at)
    rule_universe = _build_train_only_rule_universe(taxonomy=taxonomy, events=events)

    _print_stage(progress_label, "building full negative-space rule neighborhoods", started_at)
    negative_space = _build_negative_space_neighborhoods(rule_universe=rule_universe, taxonomy=taxonomy, events=events)

    _print_stage(progress_label, "scoring train-only plateau basins", started_at)
    plateau_basins = _build_plateau_basins(
        rule_universe=rule_universe,
        negative_space=negative_space,
        taxonomy=taxonomy,
        events=events,
        outcomes=outcomes,
    )

    _print_stage(progress_label, "evaluating daily prequential OOS selected basins", started_at)
    daily_selection, daily_oos, window_health, selection_drift = _build_daily_prequential_oos(
        plateau_basins=plateau_basins,
        rule_universe=rule_universe,
        taxonomy=taxonomy,
        events=events,
        outcomes=outcomes,
    )

    _print_stage(progress_label, "auditing no-lookahead, negative-space, and daily replay contracts", started_at)
    protocol_audit = _build_protocol_audit(
        events=events,
        outcomes=outcomes,
        taxonomy=taxonomy,
        rule_universe=rule_universe,
        negative_space=negative_space,
        plateau_basins=plateau_basins,
        daily_selection=daily_selection,
        daily_oos=daily_oos,
        window_health=window_health,
        selection_drift=selection_drift,
    )

    _print_stage(progress_label, "writing event/outcome/taxonomy/response-surface/rule/negative-space/basin/OOS/audit artifacts", started_at)
    _write_parquet(config.output_dir / "pump_mechanism_events.parquet", events)
    _write_parquet(config.output_dir / "pump_mechanism_outcomes.parquet", outcomes)
    _write_csv(config.output_dir / "pump_mechanism_event_quality.csv", quality)
    _write_csv(config.output_dir / "pump_mechanism_taxonomy.csv", taxonomy)
    _write_csv(config.output_dir / "pump_mechanism_taxonomy_by_axis.csv", taxonomy_by_axis)
    _write_csv(config.output_dir / "pump_mechanism_response_surfaces.csv", response_surfaces)
    _write_csv(config.output_dir / "pump_mechanism_rule_universe.csv", rule_universe)
    _write_csv(config.output_dir / "pump_mechanism_negative_space.csv", negative_space)
    _write_csv(config.output_dir / "pump_mechanism_plateau_basins.csv", plateau_basins)
    _write_csv(config.output_dir / "pump_mechanism_daily_selection.csv", daily_selection)
    _write_csv(config.output_dir / "pump_mechanism_daily_oos.csv", daily_oos)
    _write_csv(config.output_dir / "pump_mechanism_window_health.csv", window_health)
    _write_csv(config.output_dir / "pump_mechanism_selection_drift.csv", selection_drift)
    _write_csv(config.output_dir / "pump_mechanism_protocol_audit.csv", protocol_audit)

    run_config = _run_config_frame(
        config=config,
        selected_symbols=selected_symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        started_at=started_at,
        events=events,
        outcomes=outcomes,
        quality=quality,
        cache_coverage=cache_coverage,
        taxonomy=taxonomy,
        response_surfaces=response_surfaces,
        rule_universe=rule_universe,
        negative_space=negative_space,
        plateau_basins=plateau_basins,
        daily_selection=daily_selection,
        daily_oos=daily_oos,
        window_health=window_health,
        selection_drift=selection_drift,
    )
    _write_csv(config.output_dir / "pump_mechanism_run_config.csv", run_config)
    _write_csv(config.output_dir / "pump_mechanism_artifact_manifest.csv", _artifact_manifest_frame(config=config))

    print(
        f"{progress_label}: artifacts written events={len(events):,} outcomes={len(outcomes):,} "
        f"taxonomy={len(taxonomy):,} response_surfaces={len(response_surfaces):,} "
        f"rule_universe={len(rule_universe):,} negative_space={len(negative_space):,} "
        f"plateau_basins={len(plateau_basins):,} daily_selection={len(daily_selection):,} "
        f"daily_oos={len(daily_oos):,} protocol_audit={len(protocol_audit):,} "
        f"elapsed={_format_duration(time.monotonic() - started_at)} output_dir={config.output_dir}",
        flush=True,
    )
    return config.output_dir


def _process_symbol_parallel_worker(
    symbol: str,
    cache_dir: Path,
    config: PumpMechanismStabilityConfig,
    start_ms: int,
    end_ms: int,
    load_start_ms: int,
    load_end_ms: int,
) -> dict[str, list[dict[str, object]]]:
    # Worker processes only read parquet cache.  They never write artifacts or
    # mutate .output/cache; all output remains in the parent process.
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
    config: PumpMechanismStabilityConfig,
    start_ms: int,
    end_ms: int,
    load_start_ms: int,
    load_end_ms: int,
) -> dict[str, list[dict[str, object]]]:
    frame_5m_result = _load_frame_result(storage, symbol, "5m", start_ms=load_start_ms, end_ms=load_end_ms)
    frame_1m_result = _load_frame_result(storage, symbol, "1m", start_ms=load_start_ms, end_ms=load_end_ms)
    oi_5m_result = frame_5m_result
    frame_5m_raw = frame_5m_result.frame if frame_5m_result.ok else pd.DataFrame()
    frame_1m_raw = frame_1m_result.frame if frame_1m_result.ok else pd.DataFrame()
    oi_5m_raw = oi_5m_result.frame if oi_5m_result.ok else pd.DataFrame()

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
    quality = [qrow]
    if frame_5m_raw.empty or frame_1m_raw.empty:
        qrow["data_rejection"] = "missing_5m_or_1m_cache"
        return {"events": [], "outcomes": [], "quality": quality}

    frame_5m = _prepare_ohlcv(frame_5m_raw)
    frame_1m = _prepare_ohlcv(frame_1m_raw)
    oi_lookup = _prepare_oi_lookup(_prepare_oi(oi_5m_raw))
    missing_required_5m = sorted(set(["timestamp", "open", "high", "low", "close"]) - set(frame_5m.columns))
    missing_required_1m = sorted(set(["timestamp", "open", "high", "low", "close"]) - set(frame_1m.columns))
    if missing_required_5m or missing_required_1m:
        qrow["data_rejection"] = f"missing_ohlcv_columns_5m={missing_required_5m}_1m={missing_required_1m}"
        return {"events": [], "outcomes": [], "quality": quality}
    if not _has_real_flow(frame_5m) or not _has_real_flow(frame_1m):
        qrow["data_warning"] = "missing_real_quote_or_trade_flow_columns; flow features will be NaN"

    raw_events = _collect_broad_pump_events(symbol=symbol, frame_5m=frame_5m, start_ms=start_ms, end_ms=end_ms, config=config)
    events: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []
    for event in raw_events:
        enriched = _build_event_features(event, frame_1m=frame_1m, oi_5m=oi_lookup, config=config)
        events.append(enriched)
        if str(enriched.get("event_status")) == "ok":
            outcomes.append(_build_event_outcomes(enriched, frame_1m=frame_1m, config=config))
    qrow.update(
        {
            "events": int(len(events)),
            "outcomes": int(len(outcomes)),
            "ok_events": int(sum(1 for row in events if str(row.get("event_status")) == "ok")),
            "data_rejection": qrow.get("data_rejection", ""),
        }
    )
    return {"events": events, "outcomes": outcomes, "quality": quality}


def _collect_broad_pump_events(
    *,
    symbol: str,
    frame_5m: pd.DataFrame,
    start_ms: int,
    end_ms: int,
    config: PumpMechanismStabilityConfig,
) -> list[dict[str, object]]:
    if frame_5m.empty:
        return []
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

    quote_baseline = quote.rolling(window=baseline, min_periods=baseline).median().shift(1)
    trade_baseline = trades.rolling(window=baseline, min_periods=baseline).median().shift(1)
    last_seed_ms: int | None = None
    cluster_ms = int(config.setup_cluster_minutes) * MINUTE_MS
    rows: list[dict[str, object]] = []

    for i in range(baseline, len(frame)):
        seed_open_ms = int(ts.iat[i])
        feature_cutoff_ms = seed_open_ms + int(config.feature_snapshot_minutes) * MINUTE_MS
        # The research window is keyed by the time the mechanism features become
        # known, not by the seed candle open.  This lets the daily replay test a
        # day using only events whose feature snapshot is available on that day.
        if feature_cutoff_ms < int(start_ms) or feature_cutoff_ms > int(end_ms):
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
        seed_high_return_pct = h / o - 1.0
        if c <= o:
            continue
        if seed_return_pct < float(config.broad_min_seed_return_pct) and seed_high_return_pct < float(config.broad_min_high_return_pct):
            continue
        q_med = _float(quote_baseline.iat[i])
        t_med = _float(trade_baseline.iat[i])
        q_now = _float(quote.iat[i])
        t_now = _float(trades.iat[i])
        quote_ratio = q_now / q_med if q_med > 0 else 0.0
        trade_ratio = t_now / t_med if t_med > 0 else 0.0
        if quote_ratio < float(config.broad_min_quote_ratio) or trade_ratio < float(config.broad_min_trade_ratio):
            continue
        pump_tier = _pump_tier(
            seed_high_return_pct=seed_high_return_pct,
            quote_ratio=quote_ratio,
            trade_ratio=trade_ratio,
            config=config,
        )
        seed_close_ms = seed_open_ms + FIVE_MINUTE_MS
        event_id = f"{_compact_symbol(symbol)}:{seed_open_ms}:{int(config.feature_snapshot_minutes)}m"
        session = _session_features(feature_cutoff_ms)
        last_seed_ms = seed_open_ms
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "event_id": event_id,
                "symbol": symbol,
                "source_setup_type": EVENT_MODEL,
                "seed_open_ms": seed_open_ms,
                "seed_close_ms": seed_close_ms,
                "feature_cutoff_ms": feature_cutoff_ms,
                "seed_open_time_utc": _fmt_ts(seed_open_ms),
                "seed_close_time_utc": _fmt_ts(seed_close_ms),
                "feature_cutoff_time_utc": _fmt_ts(feature_cutoff_ms),
                "day_ord": int(feature_cutoff_ms // DAY_MS),
                "date": _date_from_ms(feature_cutoff_ms),
                "session_bucket": session["session_bucket"],
                "session_primary": session["session_primary"],
                "session_overlap": session["session_overlap"],
                "hour_utc": session["hour_utc"],
                "weekday": session["weekday"],
                "seed_open": o,
                "seed_high": h,
                "seed_low": l,
                "seed_close": c,
                "seed_return_pct": seed_return_pct,
                "seed_high_return_pct": seed_high_return_pct,
                "seed_range_pct": h / l - 1.0 if l > 0 else np.nan,
                "seed_quote_volume": q_now,
                "seed_number_of_trades": t_now,
                "seed_quote_ratio": quote_ratio,
                "seed_trade_ratio": trade_ratio,
                "seed_taker_buy_share": _candle_taker_buy_share(frame.iloc[i]),
                "pump_tier": pump_tier,
                "is_strong_pump_tier": pump_tier in {"strong_pump", "anomaly_pump", "extreme_pump"},
                "is_anomaly_pump_tier": pump_tier in {"anomaly_pump", "extreme_pump"},
                "is_extreme_pump_tier": pump_tier == "extreme_pump",
                "event_model": EVENT_MODEL,
                "feature_snapshot_model": FEATURE_SNAPSHOT_MODEL,
                "feature_available_timestamp_ms": feature_cutoff_ms,
                "outcome_model": OUTCOME_MODEL,
                "future_label_available_at_entry": False,
                "outcomes_available_in_feature_store": False,
                "data_access_model": DATA_ACCESS_MODEL,
                "oi_model": OI_MODEL,
            }
        )
    return rows


def _build_event_features(
    event: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    oi_5m: _OiLookup,
    config: PumpMechanismStabilityConfig,
) -> dict[str, object]:
    seed_open_ms = int(event["seed_open_ms"])
    feature_cutoff_ms = int(event["feature_cutoff_ms"])
    pre_start_ms = seed_open_ms - int(config.pre_context_minutes) * MINUTE_MS
    struct_start_ms = seed_open_ms - int(config.structural_lookback_minutes) * MINUTE_MS
    early = _time_window(frame_1m, start_ms=seed_open_ms, end_ms=feature_cutoff_ms, include_end=False)
    pre = _time_window(frame_1m, start_ms=pre_start_ms, end_ms=seed_open_ms, include_end=False)
    struct = _time_window(frame_1m, start_ms=struct_start_ms, end_ms=feature_cutoff_ms, include_end=False)
    enriched = dict(event)
    if early.empty:
        enriched.update({"event_status": "missing_1m_feature_window"})
        return enriched
    if len(early) < int(config.feature_snapshot_minutes):
        enriched.update({"event_status": "incomplete_1m_feature_window", "feature_window_1m_rows": int(len(early))})
        return enriched

    seed_open = _float(event.get("seed_open"))
    cutoff_close = _float(early.iloc[-1].get("close"))
    if not math.isfinite(seed_open) or seed_open <= 0 or not math.isfinite(cutoff_close) or cutoff_close <= 0:
        enriched.update({"event_status": "invalid_feature_prices"})
        return enriched

    high_series = _numeric_series(early, "high")
    low_series = _numeric_series(early, "low")
    close_series = _numeric_series(early, "close")
    quote_series = _numeric_series(early, "quote_volume")
    trade_series = _numeric_series(early, "number_of_trades")
    pump_high_idx = high_series.idxmax()
    pump_high = _float(high_series.loc[pump_high_idx])
    pump_high_ts = int(early.loc[pump_high_idx, "timestamp"])

    pre_quote_sum = _safe_sum(_numeric_series(pre, "quote_volume"))
    pre_trade_sum = _safe_sum(_numeric_series(pre, "number_of_trades"))
    early_quote_sum = _safe_sum(quote_series)
    early_trade_sum = _safe_sum(trade_series)
    pre60_scaled_quote = pre_quote_sum * (int(config.feature_snapshot_minutes) / max(float(config.pre_context_minutes), 1.0))
    pre60_scaled_trades = pre_trade_sum * (int(config.feature_snapshot_minutes) / max(float(config.pre_context_minutes), 1.0))

    ret_5 = _close_return_at_minutes(early, seed_open=seed_open, minutes=5)
    ret_10 = _close_return_at_minutes(early, seed_open=seed_open, minutes=10)
    ret_15 = cutoff_close / seed_open - 1.0
    high_ret_15 = pump_high / seed_open - 1.0 if pump_high > 0 else np.nan
    close15_to_high15_ratio = (cutoff_close - seed_open) / (pump_high - seed_open) if pump_high > seed_open else np.nan
    wick_ret_15 = pump_high / cutoff_close - 1.0 if cutoff_close > 0 and pump_high > 0 else np.nan

    first2_quote = _safe_sum(quote_series.iloc[:2])
    last2_quote = _safe_sum(quote_series.iloc[-2:])
    first2_trades = _safe_sum(trade_series.iloc[:2])
    last2_trades = _safe_sum(trade_series.iloc[-2:])
    struct_low = _safe_min(_numeric_series(struct, "low"))
    struct_high = _safe_max(_numeric_series(struct, "high"))
    structural_low_timestamp_ms = _timestamp_at_min(struct, "low")
    structural_high_timestamp_ms = _timestamp_at_max(struct, "high")
    oi_context = _closed_5m_oi_context(
        oi_5m,
        asof_timestamp_ms=feature_cutoff_ms,
        threshold_pct=float(config.oi_change_threshold_pct),
        strong_threshold_pct=float(config.oi_strong_change_threshold_pct),
    )

    enriched.update(
        {
            "event_status": "ok",
            "feature_window_1m_rows": int(len(early)),
            "pre_context_1m_rows": int(len(pre)),
            "structural_window_1m_rows": int(len(struct)),
            "feature_price": cutoff_close,
            "pump_high": pump_high,
            "pump_high_timestamp_ms": pump_high_ts,
            "pump_high_time_utc": _fmt_ts(pump_high_ts),
            "pre60_return_pct": _pre_window_return(pre),
            "pre60_range_pct": _pre_window_range(pre),
            "pre60_quote_volume": pre_quote_sum,
            "pre60_number_of_trades": pre_trade_sum,
            "pre60_taker_buy_share": _safe_median(_taker_buy_share(pre)),
            "early_return_pct": ret_15,
            "early_range_pct": _range_pct(high_series, low_series),
            "early_quote_volume": early_quote_sum,
            "early_number_of_trades": early_trade_sum,
            "early_quote_ratio_60m_scaled": early_quote_sum / pre60_scaled_quote if pre60_scaled_quote > 0 else np.nan,
            "early_trade_ratio_60m_scaled": early_trade_sum / pre60_scaled_trades if pre60_scaled_trades > 0 else np.nan,
            "early_taker_buy_quote_share": _safe_median(_taker_buy_share(early)),
            "close_ret_5m": ret_5,
            "close_ret_10m": ret_10,
            "close_ret_15m": ret_15,
            "high_ret_15m": high_ret_15,
            "close15_to_high15_ratio": close15_to_high15_ratio,
            "wick_ret_15m": wick_ret_15,
            "m1_last2_quote_share": last2_quote / early_quote_sum if early_quote_sum > 0 else np.nan,
            "m1_last2_trade_share": last2_trades / early_trade_sum if early_trade_sum > 0 else np.nan,
            "m1_quote_accel_last2_vs_first2": last2_quote / first2_quote if first2_quote > 0 else np.nan,
            "m1_trade_accel_last2_vs_first2": last2_trades / first2_trades if first2_trades > 0 else np.nan,
            "structural_low": struct_low,
            "structural_low_timestamp_ms": structural_low_timestamp_ms,
            "structural_low_time_utc": _fmt_optional_ts(structural_low_timestamp_ms),
            "structural_high": struct_high,
            "structural_high_timestamp_ms": structural_high_timestamp_ms,
            "structural_high_time_utc": _fmt_optional_ts(structural_high_timestamp_ms),
            **oi_context,
        }
    )
    return enriched


def _build_event_outcomes(
    event: dict[str, object],
    *,
    frame_1m: pd.DataFrame,
    config: PumpMechanismStabilityConfig,
) -> dict[str, object]:
    feature_cutoff_ms = int(event["feature_cutoff_ms"])
    max_end_ms = feature_cutoff_ms + int(config.max_outcome_minutes) * MINUTE_MS
    future = _time_window(frame_1m, start_ms=feature_cutoff_ms, end_ms=max_end_ms, include_end=False)
    feature_price = _float(event.get("feature_price"))
    pump_high = _float(event.get("pump_high"))
    structural_low = _float(event.get("structural_low"))
    row: dict[str, object] = {
        "research_id": RESEARCH_ID,
        "event_id": event["event_id"],
        "symbol": event["symbol"],
        "feature_cutoff_ms": feature_cutoff_ms,
        "feature_cutoff_time_utc": _fmt_ts(feature_cutoff_ms),
        "day_ord": int(feature_cutoff_ms // DAY_MS),
        "date": _date_from_ms(feature_cutoff_ms),
        "session_bucket": event.get("session_bucket", ""),
        "pump_tier": event.get("pump_tier", ""),
        "outcome_model": OUTCOME_MODEL,
        "outcome_start_timestamp_ms": feature_cutoff_ms,
        "outcome_start_time_utc": _fmt_ts(feature_cutoff_ms),
        "max_outcome_minutes": int(config.max_outcome_minutes),
        "future_label_available_at_entry": False,
        "outcomes_available_in_feature_store": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }
    if future.empty or not math.isfinite(feature_price) or feature_price <= 0:
        row.update({"outcome_status": "missing_future_1m_window", "future_1m_rows": int(len(future))})
        return row
    row.update({"outcome_status": "ok", "future_1m_rows": int(len(future))})
    for minutes in OUTCOME_HORIZONS_MINUTES:
        horizon = _time_window(frame_1m, start_ms=feature_cutoff_ms, end_ms=feature_cutoff_ms + int(minutes) * MINUTE_MS, include_end=False)
        if horizon.empty:
            row[f"future_ret_{minutes}m"] = np.nan
            row[f"future_min_ret_{minutes}m"] = np.nan
            row[f"future_max_ret_{minutes}m"] = np.nan
            row[f"down_mfe_{minutes}m"] = np.nan
            row[f"up_mae_{minutes}m"] = np.nan
            continue
        last_close = _float(horizon.iloc[-1].get("close"))
        min_low = _safe_min(_numeric_series(horizon, "low"))
        max_high = _safe_max(_numeric_series(horizon, "high"))
        row[f"future_ret_{minutes}m"] = last_close / feature_price - 1.0 if last_close > 0 else np.nan
        row[f"future_min_ret_{minutes}m"] = min_low / feature_price - 1.0 if min_low > 0 else np.nan
        row[f"future_max_ret_{minutes}m"] = max_high / feature_price - 1.0 if max_high > 0 else np.nan
        row[f"down_mfe_{minutes}m"] = min_low / feature_price - 1.0 if min_low > 0 else np.nan
        row[f"up_mae_{minutes}m"] = max_high / feature_price - 1.0 if max_high > 0 else np.nan
    row["time_to_reclaim_pump_high_minutes"] = _minutes_to_first(future, threshold_price=pump_high, side="high_ge")
    row["time_to_structural_low_break_minutes"] = _minutes_to_first(future, threshold_price=structural_low, side="low_le")
    row["reclaimed_pump_high_60m"] = bool(math.isfinite(float(row["time_to_reclaim_pump_high_minutes"])))
    row["broke_structural_low_60m"] = bool(math.isfinite(float(row["time_to_structural_low_break_minutes"])))
    return row



def _build_mechanism_taxonomy(events: pd.DataFrame) -> pd.DataFrame:
    """Classify ok pump events into deterministic entry-known mechanism axes.

    Taxonomy uses only columns from ``pump_mechanism_events``.  It deliberately
    does not join ``pump_mechanism_outcomes``: future response vectors are labels
    for later diagnostics, not inputs for market-nature classification.
    """

    columns = _taxonomy_columns()
    if events.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    ok_events = events.loc[events.get("event_status", pd.Series(dtype=str)).astype(str) == "ok"]
    for _, event in ok_events.iterrows():
        acceptance_regime = _classify_acceptance_regime(event)
        flow_regime = _classify_flow_regime(event, acceptance_regime=acceptance_regime)
        price_progress_regime = _classify_price_progress_regime(event, acceptance_regime=acceptance_regime)
        structure_regime = _classify_structure_regime(event)
        late_buyer_regime = _classify_late_buyer_regime(event)
        oi_regime = _normalize_oi_regime(event.get("oi_regime"))
        session_bucket = str(event.get("session_bucket", "")) or "unknown_session"
        mechanism_id = "|".join(
            (
                acceptance_regime,
                oi_regime,
                flow_regime,
                price_progress_regime,
                structure_regime,
                late_buyer_regime,
                session_bucket,
            )
        )
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "event_id": event.get("event_id", ""),
                "symbol": event.get("symbol", ""),
                "feature_cutoff_ms": event.get("feature_cutoff_ms", np.nan),
                "feature_cutoff_time_utc": event.get("feature_cutoff_time_utc", ""),
                "day_ord": event.get("day_ord", np.nan),
                "date": event.get("date", ""),
                "session_bucket": session_bucket,
                "pump_tier": event.get("pump_tier", ""),
                "acceptance_regime": acceptance_regime,
                "oi_regime": oi_regime,
                "flow_regime": flow_regime,
                "price_progress_regime": price_progress_regime,
                "structure_regime": structure_regime,
                "late_buyer_regime": late_buyer_regime,
                "mechanism_id": mechanism_id,
                "mechanism_family": _mechanism_family(
                    acceptance_regime=acceptance_regime,
                    oi_regime=oi_regime,
                    flow_regime=flow_regime,
                    price_progress_regime=price_progress_regime,
                    structure_regime=structure_regime,
                    late_buyer_regime=late_buyer_regime,
                ),
                "taxonomy_model": "entry_known_deterministic_mechanism_axes_v1",
                "taxonomy_feature_source_model": "pump_mechanism_events_only_no_outcomes",
                "taxonomy_uses_outcome_columns": False,
                "future_label_available_at_entry": False,
                "outcomes_available_in_feature_store": False,
                "data_access_model": DATA_ACCESS_MODEL,
                "oi_model": OI_MODEL,
                "close_ret_15m": _float(event.get("close_ret_15m")),
                "high_ret_15m": _float(event.get("high_ret_15m")),
                "close15_to_high15_ratio": _float(event.get("close15_to_high15_ratio")),
                "wick_ret_15m": _float(event.get("wick_ret_15m")),
                "early_quote_ratio_60m_scaled": _float(event.get("early_quote_ratio_60m_scaled")),
                "early_trade_ratio_60m_scaled": _float(event.get("early_trade_ratio_60m_scaled")),
                "early_taker_buy_quote_share": _float(event.get("early_taker_buy_quote_share")),
                "m1_last2_quote_share": _float(event.get("m1_last2_quote_share")),
                "m1_last2_trade_share": _float(event.get("m1_last2_trade_share")),
                "m1_quote_accel_last2_vs_first2": _float(event.get("m1_quote_accel_last2_vs_first2")),
                "m1_trade_accel_last2_vs_first2": _float(event.get("m1_trade_accel_last2_vs_first2")),
                "seed_open": _float(event.get("seed_open")),
                "seed_low": _float(event.get("seed_low")),
                "seed_close": _float(event.get("seed_close")),
                "feature_price": _float(event.get("feature_price")),
                "pump_high": _float(event.get("pump_high")),
                "structural_low": _float(event.get("structural_low")),
            }
        )
    return _sort_frame(_ensure_columns(pd.DataFrame(rows), columns), ["feature_cutoff_ms", "symbol", "event_id"])


def _taxonomy_by_axis_frame(taxonomy: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "research_id", "axis", "axis_value", "events", "symbols", "active_days", "sessions", "mechanism_ids",
        "taxonomy_model", "taxonomy_feature_source_model", "taxonomy_uses_outcome_columns", "data_access_model",
    ]
    if taxonomy.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    axis_columns = [
        "acceptance_regime",
        "oi_regime",
        "flow_regime",
        "price_progress_regime",
        "structure_regime",
        "late_buyer_regime",
        "mechanism_family",
        "session_bucket",
        "pump_tier",
    ]
    for axis in axis_columns:
        if axis not in taxonomy.columns:
            continue
        grouped = taxonomy.groupby(axis, dropna=False)
        for value, frame in grouped:
            rows.append(
                {
                    "research_id": RESEARCH_ID,
                    "axis": axis,
                    "axis_value": str(value),
                    "events": int(len(frame)),
                    "symbols": int(frame["symbol"].nunique()) if "symbol" in frame.columns else 0,
                    "active_days": int(frame["date"].nunique()) if "date" in frame.columns else 0,
                    "sessions": int(frame["session_bucket"].nunique()) if "session_bucket" in frame.columns else 0,
                    "mechanism_ids": int(frame["mechanism_id"].nunique()) if "mechanism_id" in frame.columns else 0,
                    "taxonomy_model": "entry_known_deterministic_mechanism_axes_v1",
                    "taxonomy_feature_source_model": "pump_mechanism_events_only_no_outcomes",
                    "taxonomy_uses_outcome_columns": False,
                    "data_access_model": DATA_ACCESS_MODEL,
                }
            )
    return _sort_frame(_ensure_columns(pd.DataFrame(rows), columns), ["axis", "events", "axis_value"])


def _taxonomy_columns() -> list[str]:
    return [
        "research_id", "event_id", "symbol", "feature_cutoff_ms", "feature_cutoff_time_utc", "day_ord", "date",
        "session_bucket", "pump_tier", "acceptance_regime", "oi_regime", "flow_regime", "price_progress_regime",
        "structure_regime", "late_buyer_regime", "mechanism_id", "mechanism_family", "taxonomy_model",
        "taxonomy_feature_source_model", "taxonomy_uses_outcome_columns", "future_label_available_at_entry",
        "outcomes_available_in_feature_store", "data_access_model", "oi_model", "close_ret_15m", "high_ret_15m",
        "close15_to_high15_ratio", "wick_ret_15m", "early_quote_ratio_60m_scaled", "early_trade_ratio_60m_scaled",
        "early_taker_buy_quote_share", "m1_last2_quote_share", "m1_last2_trade_share", "m1_quote_accel_last2_vs_first2",
        "m1_trade_accel_last2_vs_first2", "seed_open", "seed_low", "seed_close", "feature_price", "pump_high", "structural_low",
    ]


def _classify_acceptance_regime(event: pd.Series) -> str:
    close_ret = _float(event.get("close_ret_15m"))
    high_ret = _float(event.get("high_ret_15m"))
    close_to_high = _float(event.get("close15_to_high15_ratio"))
    wick_ret = _float(event.get("wick_ret_15m"))
    if not math.isfinite(close_ret) or not math.isfinite(high_ret) or high_ret <= 0:
        return "missing_acceptance"
    if close_ret <= 0 or (math.isfinite(close_to_high) and close_to_high <= IMMEDIATE_REJECTION_CLOSE_TO_HIGH_MAX):
        return "immediate_rejection"
    if math.isfinite(wick_ret) and wick_ret >= WICK_WITHOUT_ACCEPTANCE_MIN and math.isfinite(close_to_high) and close_to_high <= FAILED_ACCEPTANCE_CLOSE_TO_HIGH_MAX:
        return "failed_acceptance"
    if math.isfinite(close_to_high) and close_to_high >= ACCEPTED_HIGH_CLOSE_TO_HIGH_MIN:
        return "accepted_high"
    if math.isfinite(close_to_high) and close_to_high >= PARTIAL_ACCEPTANCE_CLOSE_TO_HIGH_MIN:
        return "partial_acceptance"
    return "failed_acceptance"


def _classify_flow_regime(event: pd.Series, *, acceptance_regime: str) -> str:
    taker_share = _float(event.get("early_taker_buy_quote_share"))
    quote_ratio = _float(event.get("early_quote_ratio_60m_scaled"))
    trade_ratio = _float(event.get("early_trade_ratio_60m_scaled"))
    close_to_high = _float(event.get("close15_to_high15_ratio"))
    last2_quote_share = _float(event.get("m1_last2_quote_share"))
    quote_accel = _float(event.get("m1_quote_accel_last2_vs_first2"))
    if not math.isfinite(taker_share):
        return "missing_flow"
    high_flow = _is_high_flow(quote_ratio=quote_ratio, trade_ratio=trade_ratio)
    weak_acceptance = acceptance_regime in {"failed_acceptance", "immediate_rejection"} or (
        math.isfinite(close_to_high) and close_to_high <= FAILED_ACCEPTANCE_CLOSE_TO_HIGH_MAX
    )
    late_activity = (
        math.isfinite(last2_quote_share) and last2_quote_share >= LATE_BUYER_LAST2_SHARE_MIN
    ) or (math.isfinite(quote_accel) and quote_accel >= LATE_ACCELERATION_MIN)
    if high_flow and taker_share >= SELLER_ABSORPTION_TAKER_SHARE_MIN and weak_acceptance:
        return "seller_absorption"
    if late_activity and taker_share >= BUYER_DOMINANT_TAKER_SHARE_MIN and weak_acceptance:
        return "late_buyer_exhaustion"
    if taker_share >= BUYER_DOMINANT_TAKER_SHARE_MIN and not weak_acceptance:
        return "buyer_dominant"
    if high_flow and weak_acceptance:
        return "high_flow_weak_acceptance"
    return "neutral_flow"


def _classify_price_progress_regime(event: pd.Series, *, acceptance_regime: str) -> str:
    ret5 = _float(event.get("close_ret_5m"))
    ret10 = _float(event.get("close_ret_10m"))
    ret15 = _float(event.get("close_ret_15m"))
    high_ret = _float(event.get("high_ret_15m"))
    close_to_high = _float(event.get("close15_to_high15_ratio"))
    wick_ret = _float(event.get("wick_ret_15m"))
    quote_ratio = _float(event.get("early_quote_ratio_60m_scaled"))
    trade_ratio = _float(event.get("early_trade_ratio_60m_scaled"))
    if not math.isfinite(ret15) or not math.isfinite(high_ret):
        return "missing_price_progress"
    if math.isfinite(wick_ret) and wick_ret >= WICK_WITHOUT_ACCEPTANCE_MIN and math.isfinite(close_to_high) and close_to_high <= FAILED_ACCEPTANCE_CLOSE_TO_HIGH_MAX:
        return "wick_without_acceptance"
    if _is_high_flow(quote_ratio=quote_ratio, trade_ratio=trade_ratio) and math.isfinite(close_to_high) and close_to_high <= HIGH_VOLUME_LOW_PROGRESS_CLOSE_TO_HIGH_MAX:
        return "high_volume_low_progress"
    if acceptance_regime == "accepted_high" and ret15 > 0:
        return "efficient_markup"
    if all(math.isfinite(value) for value in (ret5, ret10, ret15)) and 0 < ret5 <= ret10 <= ret15:
        return "grind_up"
    if acceptance_regime in {"failed_acceptance", "immediate_rejection"}:
        return "pullback_after_spike"
    return "mixed_progress"


def _classify_structure_regime(event: pd.Series) -> str:
    seed_open = _float(event.get("seed_open"))
    seed_low = _float(event.get("seed_low"))
    seed_close = _float(event.get("seed_close"))
    feature_price = _float(event.get("feature_price"))
    pump_high = _float(event.get("pump_high"))
    close_ret = _float(event.get("close_ret_15m"))
    seed_return = _float(event.get("seed_return_pct"))
    if not all(math.isfinite(value) and value > 0 for value in (seed_open, seed_low, seed_close, feature_price, pump_high)):
        return "missing_structure"
    if feature_price <= seed_low * (1.0 - SEED_LOW_BREAK_BUFFER_PCT):
        return "seed_low_break_after_pump"
    if feature_price <= seed_open:
        return "round_trip_to_seed_open"
    if math.isfinite(seed_return) and seed_return > 0 and math.isfinite(close_ret) and close_ret <= seed_return * DEEP_RETRACE_TO_SEED_RETURN_FRACTION:
        return "deep_retrace_to_seed_body"
    midpoint = seed_open + 0.5 * (pump_high - seed_open)
    if feature_price < midpoint:
        return "upper_half_rejected"
    return "structure_intact_near_high"


def _classify_late_buyer_regime(event: pd.Series) -> str:
    taker_share = _float(event.get("early_taker_buy_quote_share"))
    last2_quote_share = _float(event.get("m1_last2_quote_share"))
    last2_trade_share = _float(event.get("m1_last2_trade_share"))
    quote_accel = _float(event.get("m1_quote_accel_last2_vs_first2"))
    trade_accel = _float(event.get("m1_trade_accel_last2_vs_first2"))
    if not any(math.isfinite(value) for value in (last2_quote_share, last2_trade_share, quote_accel, trade_accel)):
        return "missing_late_activity"
    late_share = max(_finite_or_negative(last2_quote_share), _finite_or_negative(last2_trade_share))
    late_accel = max(_finite_or_negative(quote_accel), _finite_or_negative(trade_accel))
    if late_share >= LATE_BUYER_LAST2_SHARE_MIN and math.isfinite(taker_share) and taker_share >= BUYER_DOMINANT_TAKER_SHARE_MIN:
        return "concentrated_late_buyers"
    if late_share >= LATE_ACTIVITY_LAST2_SHARE_MIN or late_accel >= LATE_ACCELERATION_MIN:
        return "late_activity_spike"
    if math.isfinite(last2_quote_share) and last2_quote_share <= EARLY_FRONT_LOADED_LAST2_SHARE_MAX:
        return "early_front_loaded"
    return "balanced_activity"


def _normalize_oi_regime(value: object) -> str:
    parsed = str(value or "").strip()
    return parsed if parsed else "missing_closed_5m_oi"


def _mechanism_family(
    *,
    acceptance_regime: str,
    oi_regime: str,
    flow_regime: str,
    price_progress_regime: str,
    structure_regime: str,
    late_buyer_regime: str,
) -> str:
    failed = acceptance_regime in {"failed_acceptance", "immediate_rejection"}
    accepted = acceptance_regime == "accepted_high"
    if accepted and structure_regime == "structure_intact_near_high":
        return "accepted_ignition_candidate"
    if failed and oi_regime == "oi_down_squeeze_unwind":
        return "squeeze_unwind_failed_acceptance"
    if failed and oi_regime == "oi_up_fresh_leverage":
        return "fresh_leverage_trap_candidate"
    if failed and flow_regime == "seller_absorption":
        return "absorption_top_candidate"
    if failed and late_buyer_regime in {"concentrated_late_buyers", "late_activity_spike"}:
        return "late_buyer_exhaustion_candidate"
    if price_progress_regime == "wick_without_acceptance":
        return "stop_run_reversal_candidate"
    if failed:
        return "generic_failed_acceptance_candidate"
    return "mixed_or_continuation_candidate"


def _is_high_flow(*, quote_ratio: float, trade_ratio: float) -> bool:
    quote_ok = math.isfinite(quote_ratio) and quote_ratio >= HIGH_FLOW_RATIO_MIN
    trade_ok = math.isfinite(trade_ratio) and trade_ratio >= HIGH_FLOW_RATIO_MIN
    return quote_ok or trade_ok


def _finite_or_negative(value: float) -> float:
    return value if math.isfinite(value) else -1.0



RESPONSE_SURFACE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mechanism_id", ("mechanism_id",)),
    ("mechanism_family", ("mechanism_family",)),
    ("session", ("session_bucket",)),
    ("acceptance", ("acceptance_regime",)),
    ("oi", ("oi_regime",)),
    ("flow", ("flow_regime",)),
    ("price_progress", ("price_progress_regime",)),
    ("structure", ("structure_regime",)),
    ("late_buyer", ("late_buyer_regime",)),
    ("acceptance_session", ("acceptance_regime", "session_bucket")),
    ("oi_acceptance", ("oi_regime", "acceptance_regime")),
    ("family_session", ("mechanism_family", "session_bucket")),
)

DOWNSIDE_HIT_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("minus_0p50pct", -0.005),
    ("minus_1p00pct", -0.010),
    ("minus_2p00pct", -0.020),
)


def _build_response_surfaces(*, taxonomy: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    """Summarize future response distributions by entry-known mechanism groups.

    This is still a pre-trade diagnostics layer: taxonomy/event features define
    the groups, outcomes supply labels after ``feature_cutoff_ms``.  The function
    never ranks candidate entries, builds stops, computes PnL, or changes rule
    thresholds from final/holdout information.
    """

    columns = _response_surface_columns()
    if taxonomy.empty or outcomes.empty:
        return pd.DataFrame(columns=columns)
    required_taxonomy = {"event_id", "symbol", "date", "session_bucket"}
    required_outcomes = {"event_id", "outcome_status"}
    if not required_taxonomy.issubset(taxonomy.columns) or not required_outcomes.issubset(outcomes.columns):
        return pd.DataFrame(columns=columns)

    taxonomy_input_columns = [
        "event_id", "symbol", "feature_cutoff_ms", "feature_cutoff_time_utc", "day_ord", "date", "session_bucket",
        "pump_tier", "acceptance_regime", "oi_regime", "flow_regime", "price_progress_regime", "structure_regime",
        "late_buyer_regime", "mechanism_id", "mechanism_family",
    ]
    available_taxonomy_columns = [column for column in taxonomy_input_columns if column in taxonomy.columns]
    outcome_columns = [
        "event_id", "outcome_status", "future_1m_rows", "future_ret_5m", "future_ret_10m", "future_ret_15m",
        "future_ret_30m", "future_ret_60m", "future_min_ret_15m", "future_min_ret_30m", "future_min_ret_60m",
        "future_max_ret_15m", "future_max_ret_30m", "future_max_ret_60m", "down_mfe_15m", "down_mfe_30m",
        "down_mfe_60m", "up_mae_15m", "up_mae_30m", "up_mae_60m", "reclaimed_pump_high_60m",
        "broke_structural_low_60m", "time_to_reclaim_pump_high_minutes", "time_to_structural_low_break_minutes",
    ]
    available_outcome_columns = [column for column in outcome_columns if column in outcomes.columns]
    joined = taxonomy[available_taxonomy_columns].merge(outcomes[available_outcome_columns], on="event_id", how="inner")
    if joined.empty:
        return pd.DataFrame(columns=columns)
    joined = joined.loc[joined["outcome_status"].astype(str) == "ok"].copy()
    if joined.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    for surface_name, group_columns in RESPONSE_SURFACE_GROUPS:
        if not all(column in joined.columns for column in group_columns):
            continue
        grouped = joined.groupby(list(group_columns), dropna=False)
        for group_key, frame in grouped:
            key_values = group_key if isinstance(group_key, tuple) else (group_key,)
            group_value = "|".join(str(value) for value in key_values)
            rows.append(_response_surface_row(surface_name=surface_name, group_columns=group_columns, group_value=group_value, frame=frame))
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["surface_name", "events", "group_value"])


def _response_surface_row(*, surface_name: str, group_columns: tuple[str, ...], group_value: str, frame: pd.DataFrame) -> dict[str, object]:
    event_count = int(len(frame))
    row: dict[str, object] = {
        "research_id": RESEARCH_ID,
        "surface_name": surface_name,
        "group_columns": ",".join(group_columns),
        "group_value": group_value,
        "events": event_count,
        "symbols": int(frame["symbol"].nunique()) if "symbol" in frame.columns else 0,
        "active_days": int(frame["date"].nunique()) if "date" in frame.columns else 0,
        "sessions": int(frame["session_bucket"].nunique()) if "session_bucket" in frame.columns else 0,
        "mechanism_ids": int(frame["mechanism_id"].nunique()) if "mechanism_id" in frame.columns else 0,
        "families": int(frame["mechanism_family"].nunique()) if "mechanism_family" in frame.columns else 0,
        "response_surface_model": "pre_trade_mechanism_outcome_distribution_v1",
        "taxonomy_source_model": "entry_known_deterministic_mechanism_axes_v1",
        "outcome_source_model": OUTCOME_MODEL,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }
    for minutes in OUTCOME_HORIZONS_MINUTES:
        future_ret = _numeric_series(frame, f"future_ret_{minutes}m")
        future_min = _numeric_series(frame, f"future_min_ret_{minutes}m")
        future_max = _numeric_series(frame, f"future_max_ret_{minutes}m")
        row[f"median_future_ret_{minutes}m"] = _safe_median(future_ret)
        row[f"avg_future_ret_{minutes}m"] = _safe_mean(future_ret)
        row[f"p25_future_ret_{minutes}m"] = _safe_quantile(future_ret, 0.25)
        row[f"p75_future_ret_{minutes}m"] = _safe_quantile(future_ret, 0.75)
        row[f"median_future_min_ret_{minutes}m"] = _safe_median(future_min)
        row[f"median_future_max_ret_{minutes}m"] = _safe_median(future_max)
        row[f"future_ret_positive_rate_{minutes}m"] = _numeric_condition_rate(future_ret, threshold=0.0, side="gt")
        for label, threshold in DOWNSIDE_HIT_THRESHOLDS:
            row[f"downside_hit_rate_{label}_{minutes}m"] = _numeric_condition_rate(future_min, threshold=threshold, side="le")
    row["reclaim_rate_60m"] = _rate(_as_bool_series(frame.get("reclaimed_pump_high_60m", pd.Series(dtype=object))))
    row["structural_low_break_rate_60m"] = _rate(_as_bool_series(frame.get("broke_structural_low_60m", pd.Series(dtype=object))))
    row["median_time_to_reclaim_pump_high_minutes"] = _safe_median(_numeric_series(frame, "time_to_reclaim_pump_high_minutes"))
    row["median_time_to_structural_low_break_minutes"] = _safe_median(_numeric_series(frame, "time_to_structural_low_break_minutes"))
    row["downside_monotonicity_score"] = _downside_monotonicity_score(row)
    row["directional_response_score"] = _directional_response_score(row)
    row.update(_dependency_metrics(frame))
    return row


def _response_surface_columns() -> list[str]:
    columns = [
        "research_id", "surface_name", "group_columns", "group_value", "events", "symbols", "active_days", "sessions",
        "mechanism_ids", "families", "response_surface_model", "taxonomy_source_model", "outcome_source_model",
        "uses_pnl", "uses_short_entry", "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]
    for minutes in OUTCOME_HORIZONS_MINUTES:
        columns.extend(
            [
                f"median_future_ret_{minutes}m", f"avg_future_ret_{minutes}m", f"p25_future_ret_{minutes}m",
                f"p75_future_ret_{minutes}m", f"median_future_min_ret_{minutes}m", f"median_future_max_ret_{minutes}m",
                f"future_ret_positive_rate_{minutes}m",
            ]
        )
        for label, _threshold in DOWNSIDE_HIT_THRESHOLDS:
            columns.append(f"downside_hit_rate_{label}_{minutes}m")
    columns.extend(
        [
            "reclaim_rate_60m", "structural_low_break_rate_60m", "median_time_to_reclaim_pump_high_minutes",
            "median_time_to_structural_low_break_minutes", "downside_monotonicity_score", "directional_response_score",
            "top_event_dependency_pct", "top_symbol_dependency_pct", "largest_symbol_event_share", "largest_day_event_share",
        ]
    )
    return columns


def _downside_monotonicity_score(row: dict[str, object]) -> float:
    values = [_float(row.get(f"median_future_min_ret_{minutes}m")) for minutes in OUTCOME_HORIZONS_MINUTES]
    finite_values = [value for value in values if math.isfinite(value)]
    if len(finite_values) < 2:
        return float("nan")
    non_increasing = 0
    comparisons = 0
    previous = finite_values[0]
    for value in finite_values[1:]:
        comparisons += 1
        if value <= previous + 1e-12:
            non_increasing += 1
        previous = value
    return non_increasing / comparisons if comparisons else float("nan")


def _directional_response_score(row: dict[str, object]) -> float:
    median_ret_30 = _float(row.get("median_future_ret_30m"))
    median_ret_60 = _float(row.get("median_future_ret_60m"))
    median_min_30 = _float(row.get("median_future_min_ret_30m"))
    median_min_60 = _float(row.get("median_future_min_ret_60m"))
    break_rate = _float(row.get("structural_low_break_rate_60m"))
    reclaim_rate = _float(row.get("reclaim_rate_60m"))
    components = [
        -median_ret_30 if math.isfinite(median_ret_30) else np.nan,
        -median_ret_60 if math.isfinite(median_ret_60) else np.nan,
        -median_min_30 if math.isfinite(median_min_30) else np.nan,
        -median_min_60 if math.isfinite(median_min_60) else np.nan,
        break_rate if math.isfinite(break_rate) else np.nan,
        1.0 - reclaim_rate if math.isfinite(reclaim_rate) else np.nan,
    ]
    finite = [float(value) for value in components if math.isfinite(float(value))]
    return float(np.mean(finite)) if finite else float("nan")


def _dependency_metrics(frame: pd.DataFrame) -> dict[str, float]:
    event_count = max(int(len(frame)), 1)
    largest_symbol_share = 0.0
    largest_day_share = 0.0
    if "symbol" in frame.columns and not frame.empty:
        largest_symbol_share = float(frame["symbol"].value_counts(dropna=False).iloc[0]) / event_count
    if "date" in frame.columns and not frame.empty:
        largest_day_share = float(frame["date"].value_counts(dropna=False).iloc[0]) / event_count
    return {
        "top_event_dependency_pct": 1.0 / event_count,
        "top_symbol_dependency_pct": largest_symbol_share,
        "largest_symbol_event_share": largest_symbol_share,
        "largest_day_event_share": largest_day_share,
    }




def _build_train_only_rule_universe(*, taxonomy: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Generate candidate mechanism rule specs with train-window-only thresholds.

    This function creates the grammar layer for later negative-space plateau
    accounting.  It uses only entry-known taxonomy/event columns; it deliberately
    does not join outcomes, PnL, short entries, or final-holdout artifacts.
    Threshold values are recomputed independently inside each train window for
    each test day, so the same 365d cache can be replayed chronologically.
    """

    columns = _rule_universe_columns()
    rule_input = _rule_input_frame(taxonomy=taxonomy, events=events)
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input = rule_input.loc[pd.to_numeric(rule_input.get("day_ord"), errors="coerce").notna()].copy()
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input["day_ord"] = pd.to_numeric(rule_input["day_ord"], errors="coerce").astype("int64")
    rows: list[dict[str, object]] = []
    for test_day_ord in sorted(rule_input["day_ord"].dropna().astype("int64").unique()):
        test_day_ord_int = int(test_day_ord)
        test_date = _date_from_day_ord(test_day_ord_int)
        for train_window_days in ROLLING_WINDOWS_DAYS:
            train_start_day_ord = test_day_ord_int - int(train_window_days)
            train_end_day_ord = test_day_ord_int - 1
            train = rule_input.loc[
                (rule_input["day_ord"] >= train_start_day_ord) & (rule_input["day_ord"] <= train_end_day_ord)
            ].copy()
            if train.empty:
                continue
            for scope in _train_rule_scopes(train):
                scoped = _apply_rule_scope(train, scope["conditions"])
                if len(scoped) < MIN_TRAIN_SCOPE_EVENTS:
                    continue
                rows.append(
                    _rule_universe_row(
                        test_day_ord=test_day_ord_int,
                        test_date=test_date,
                        train_window_days=int(train_window_days),
                        train_start_day_ord=train_start_day_ord,
                        train_end_day_ord=train_end_day_ord,
                        scope=scope,
                        threshold_feature="",
                        threshold_side="none",
                        threshold_quantile=np.nan,
                        threshold_value=np.nan,
                        filtered=scoped,
                        threshold_source_events=0,
                        threshold_valid_values=0,
                        threshold_uses_abs_value=False,
                    )
                )
                for feature, side, quantiles in RULE_THRESHOLD_FEATURES:
                    if feature not in scoped.columns:
                        continue
                    source_values = pd.to_numeric(scoped[feature], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
                    threshold_uses_abs_value = side == "abs_ge"
                    fit_values = source_values.abs() if threshold_uses_abs_value else source_values
                    fit_values = fit_values.dropna()
                    if fit_values.empty:
                        continue
                    for quantile in quantiles:
                        threshold_value = _safe_quantile(fit_values, float(quantile))
                        if not math.isfinite(threshold_value):
                            continue
                        filtered = _apply_threshold(scoped, feature=feature, side=side, threshold_value=threshold_value)
                        rows.append(
                            _rule_universe_row(
                                test_day_ord=test_day_ord_int,
                                test_date=test_date,
                                train_window_days=int(train_window_days),
                                train_start_day_ord=train_start_day_ord,
                                train_end_day_ord=train_end_day_ord,
                                scope=scope,
                                threshold_feature=feature,
                                threshold_side=side,
                                threshold_quantile=float(quantile),
                                threshold_value=float(threshold_value),
                                filtered=filtered,
                                threshold_source_events=int(len(scoped)),
                                threshold_valid_values=int(len(fit_values)),
                                threshold_uses_abs_value=threshold_uses_abs_value,
                            )
                        )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "scope_name", "threshold_feature", "threshold_quantile", "rule_id"])


def _rule_input_frame(*, taxonomy: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if taxonomy.empty:
        return pd.DataFrame()
    taxonomy_columns = [
        "event_id", "symbol", "feature_cutoff_ms", "feature_cutoff_time_utc", "day_ord", "date", "session_bucket",
        "pump_tier", "acceptance_regime", "oi_regime", "flow_regime", "price_progress_regime", "structure_regime",
        "late_buyer_regime", "mechanism_id", "mechanism_family", "taxonomy_model", "taxonomy_feature_source_model",
        "taxonomy_uses_outcome_columns", "future_label_available_at_entry", "outcomes_available_in_feature_store",
        "data_access_model", "oi_model",
    ]
    event_feature_columns = [
        "event_id", "seed_return_pct", "seed_high_return_pct", "seed_quote_ratio", "seed_trade_ratio", "seed_taker_buy_share",
        "pre60_return_pct", "pre60_range_pct", "early_return_pct", "early_range_pct", "early_quote_ratio_60m_scaled",
        "early_trade_ratio_60m_scaled", "early_taker_buy_quote_share", "close_ret_5m", "close_ret_10m", "close_ret_15m",
        "high_ret_15m", "close15_to_high15_ratio", "wick_ret_15m", "m1_last2_quote_share", "m1_last2_trade_share",
        "m1_quote_accel_last2_vs_first2", "m1_trade_accel_last2_vs_first2", "oi_change_5m_pct", "oi_change_10m_pct",
        "oi_change_15m_pct", "oi_available",
    ]
    available_taxonomy_columns = [column for column in taxonomy_columns if column in taxonomy.columns]
    work = taxonomy[available_taxonomy_columns].copy()
    if not events.empty:
        available_event_columns = [column for column in event_feature_columns if column in events.columns]
        if available_event_columns and "event_id" in available_event_columns:
            event_features = events[available_event_columns].drop_duplicates("event_id", keep="last")
            work = work.merge(event_features, on="event_id", how="left", suffixes=("", "_event"))
    for column in _rule_numeric_feature_columns():
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    return work


def _train_rule_scopes(train: pd.DataFrame) -> list[dict[str, object]]:
    scopes: list[dict[str, object]] = [
        {"scope_name": "all", "conditions": {}, "scope_depth": 0},
    ]
    for axis in RULE_SCOPE_AXES:
        if axis not in train.columns:
            continue
        counts = train[axis].fillna("missing").astype(str).value_counts(dropna=False)
        for value, count in counts.items():
            if int(count) >= MIN_TRAIN_SCOPE_EVENTS:
                scopes.append({"scope_name": axis, "conditions": {axis: str(value)}, "scope_depth": 1})
    for left, right in RULE_PAIR_SCOPES:
        if left not in train.columns or right not in train.columns:
            continue
        grouped = train.assign(**{left: train[left].fillna("missing").astype(str), right: train[right].fillna("missing").astype(str)}).groupby([left, right], dropna=False)
        for (left_value, right_value), frame in grouped:
            if len(frame) >= MIN_TRAIN_SCOPE_EVENTS:
                scopes.append(
                    {
                        "scope_name": f"{left}+{right}",
                        "conditions": {left: str(left_value), right: str(right_value)},
                        "scope_depth": 2,
                    }
                )
    # Deterministic order and de-duplication: the same condition set can be
    # reached through a named pair after missing-value normalization.
    unique: dict[str, dict[str, object]] = {}
    for scope in scopes:
        key = _scope_key(scope["conditions"])
        unique.setdefault(f"{scope['scope_name']}|{key}", scope)
    return list(unique.values())


def _apply_rule_scope(frame: pd.DataFrame, conditions: object) -> pd.DataFrame:
    if not isinstance(conditions, dict) or not conditions:
        return frame.copy()
    mask = pd.Series(True, index=frame.index)
    for column, expected in conditions.items():
        if column not in frame.columns:
            return frame.iloc[0:0].copy()
        mask &= frame[column].fillna("missing").astype(str).eq(str(expected))
    return frame.loc[mask].copy()


def _apply_threshold(frame: pd.DataFrame, *, feature: str, side: str, threshold_value: float) -> pd.DataFrame:
    values = pd.to_numeric(frame.get(feature, pd.Series(np.nan, index=frame.index)), errors="coerce")
    if side == "le":
        mask = values <= float(threshold_value)
    elif side == "ge":
        mask = values >= float(threshold_value)
    elif side == "abs_ge":
        mask = values.abs() >= float(threshold_value)
    else:
        raise ValueError(f"unsupported threshold side: {side}")
    return frame.loc[mask.fillna(False)].copy()


def _rule_universe_row(
    *,
    test_day_ord: int,
    test_date: str,
    train_window_days: int,
    train_start_day_ord: int,
    train_end_day_ord: int,
    scope: dict[str, object],
    threshold_feature: str,
    threshold_side: str,
    threshold_quantile: float,
    threshold_value: float,
    filtered: pd.DataFrame,
    threshold_source_events: int,
    threshold_valid_values: int,
    threshold_uses_abs_value: bool,
) -> dict[str, object]:
    conditions = scope.get("conditions") if isinstance(scope.get("conditions"), dict) else {}
    assert isinstance(conditions, dict)
    scope_key = _scope_key(conditions)
    scope_name = str(scope.get("scope_name", ""))
    threshold_family = "axis_only" if not threshold_feature else f"{threshold_feature}_{threshold_side}_q{_quantile_label(threshold_quantile)}"
    rule_key = "|".join(
        (
            str(test_day_ord),
            str(train_window_days),
            scope_name,
            scope_key,
            threshold_family,
            _format_float_for_id(threshold_value),
        )
    )
    train_events = int(len(filtered))
    train_symbols = int(filtered["symbol"].nunique()) if "symbol" in filtered.columns and not filtered.empty else 0
    train_active_days = int(filtered["date"].nunique()) if "date" in filtered.columns and not filtered.empty else 0
    return {
        "research_id": RESEARCH_ID,
        "rule_id": "pmr_" + hashlib.sha1(rule_key.encode("utf-8")).hexdigest()[:16],
        "rule_key": rule_key,
        "test_day_ord": int(test_day_ord),
        "test_date": test_date,
        "train_window_days": int(train_window_days),
        "train_start_day_ord": int(train_start_day_ord),
        "train_end_day_ord": int(train_end_day_ord),
        "train_start_date": _date_from_day_ord(train_start_day_ord),
        "train_end_date": _date_from_day_ord(train_end_day_ord),
        "scope_name": scope_name,
        "scope_depth": int(scope.get("scope_depth", 0) or 0),
        "scope_conditions": scope_key,
        "mechanism_family": _condition_value(conditions, "mechanism_family"),
        "acceptance_regime": _condition_value(conditions, "acceptance_regime"),
        "oi_regime": _condition_value(conditions, "oi_regime"),
        "flow_regime": _condition_value(conditions, "flow_regime"),
        "structure_regime": _condition_value(conditions, "structure_regime"),
        "late_buyer_regime": _condition_value(conditions, "late_buyer_regime"),
        "session_bucket": _condition_value(conditions, "session_bucket"),
        "threshold_family": threshold_family,
        "threshold_feature": threshold_feature,
        "threshold_side": threshold_side,
        "threshold_quantile": threshold_quantile,
        "threshold_value": threshold_value,
        "threshold_source_events": int(threshold_source_events),
        "threshold_valid_values": int(threshold_valid_values),
        "threshold_uses_abs_value": bool(threshold_uses_abs_value),
        "train_events": train_events,
        "train_symbols": train_symbols,
        "train_active_days": train_active_days,
        "rule_passes_min_sample": bool(train_events >= MIN_RULE_EVENTS and train_symbols >= MIN_RULE_SYMBOLS and train_active_days >= MIN_RULE_ACTIVE_DAYS),
        "min_rule_events": int(MIN_RULE_EVENTS),
        "min_rule_symbols": int(MIN_RULE_SYMBOLS),
        "min_rule_active_days": int(MIN_RULE_ACTIVE_DAYS),
        "rule_generation_model": "train_only_entry_known_mechanism_rule_grammar_v1",
        "threshold_fit_model": "quantiles_fit_inside_train_window_only",
        "train_uses_only_days_before_test": True,
        "uses_outcome_columns": False,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _rule_universe_columns() -> list[str]:
    return [
        "research_id", "rule_id", "rule_key", "test_day_ord", "test_date", "train_window_days", "train_start_day_ord",
        "train_end_day_ord", "train_start_date", "train_end_date", "scope_name", "scope_depth", "scope_conditions",
        "mechanism_family", "acceptance_regime", "oi_regime", "flow_regime", "structure_regime", "late_buyer_regime",
        "session_bucket", "threshold_family", "threshold_feature", "threshold_side", "threshold_quantile", "threshold_value",
        "threshold_source_events", "threshold_valid_values", "threshold_uses_abs_value", "train_events", "train_symbols",
        "train_active_days", "rule_passes_min_sample", "min_rule_events", "min_rule_symbols", "min_rule_active_days",
        "rule_generation_model", "threshold_fit_model", "train_uses_only_days_before_test", "uses_outcome_columns", "uses_pnl",
        "uses_short_entry", "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]


def _rule_numeric_feature_columns() -> list[str]:
    return sorted({feature for feature, _side, _quantiles in RULE_THRESHOLD_FEATURES} | {"day_ord"})


def _scope_key(conditions: object) -> str:
    if not isinstance(conditions, dict) or not conditions:
        return "*"
    return ";".join(f"{key}={conditions[key]}" for key in sorted(conditions))


def _condition_value(conditions: dict[str, object], key: str) -> str:
    return str(conditions.get(key, "*"))


def _quantile_label(value: float) -> str:
    if not math.isfinite(_float(value)):
        return "none"
    return str(int(round(float(value) * 100))).zfill(2)


def _format_float_for_id(value: float) -> str:
    parsed = _float(value)
    if not math.isfinite(parsed):
        return "nan"
    return f"{parsed:.8g}"


def _date_from_day_ord(day_ord: int) -> str:
    return datetime.fromtimestamp(int(day_ord) * DAY_MS / 1000, UTC).date().isoformat()



def _build_negative_space_neighborhoods(
    *,
    rule_universe: pd.DataFrame,
    taxonomy: pd.DataFrame,
    events: pd.DataFrame,
) -> pd.DataFrame:
    """Generate full train-only neighbor space around each mechanism rule.

    This is negative-space accounting, not plateau scoring.  It starts from
    train-window sample-valid centers only, then deliberately emits neighbors
    that fail low-sample gates so later plateau scoring can see cliffs and dead
    zones instead of only promoted/surviving rules.
    """

    columns = _negative_space_columns()
    if rule_universe.empty:
        return pd.DataFrame(columns=columns)
    rule_input = _rule_input_frame(taxonomy=taxonomy, events=events)
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input = rule_input.loc[pd.to_numeric(rule_input.get("day_ord"), errors="coerce").notna()].copy()
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input["day_ord"] = pd.to_numeric(rule_input["day_ord"], errors="coerce").astype("int64")

    center_rules = rule_universe.loc[rule_universe.get("rule_passes_min_sample", pd.Series(dtype=bool)).astype(bool)].copy()
    if center_rules.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    for center in center_rules.to_dict("records"):
        test_day_ord = _finite_int_or_none(center.get("test_day_ord"))
        train_start_day_ord = _finite_int_or_none(center.get("train_start_day_ord"))
        train_end_day_ord = _finite_int_or_none(center.get("train_end_day_ord"))
        train_window_days = _finite_int_or_none(center.get("train_window_days"))
        if test_day_ord is None or train_start_day_ord is None or train_end_day_ord is None or train_window_days is None:
            continue
        train = rule_input.loc[
            (rule_input["day_ord"] >= int(train_start_day_ord)) & (rule_input["day_ord"] <= int(train_end_day_ord))
        ].copy()
        if train.empty:
            continue
        basin_id = _basin_id_for_center(center)
        for neighbor in _iter_negative_space_neighbor_specs(center=center, train=train):
            rows.append(
                _negative_space_row(
                    center=center,
                    neighbor=neighbor,
                    train=train,
                    basin_id=basin_id,
                    test_day_ord=int(test_day_ord),
                    train_window_days=int(train_window_days),
                    train_start_day_ord=int(train_start_day_ord),
                    train_end_day_ord=int(train_end_day_ord),
                )
            )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "basin_id", "neighbor_distance", "neighbor_kind", "neighbor_rule_id"])


def _iter_negative_space_neighbor_specs(*, center: dict[str, object], train: pd.DataFrame) -> list[dict[str, object]]:
    conditions = _parse_scope_conditions(center.get("scope_conditions"))
    threshold_feature = str(center.get("threshold_feature") or "")
    threshold_side = str(center.get("threshold_side") or "none")
    threshold_quantile = _float(center.get("threshold_quantile"))
    specs: dict[str, dict[str, object]] = {}

    def add_spec(
        *,
        kind: str,
        distance: int,
        axis_changed: str,
        next_conditions: dict[str, str],
        next_quantile: float,
    ) -> None:
        key = _negative_neighbor_key(
            kind=kind,
            conditions=next_conditions,
            threshold_feature=threshold_feature,
            threshold_side=threshold_side,
            threshold_quantile=next_quantile,
        )
        specs.setdefault(
            key,
            {
                "neighbor_kind": kind,
                "neighbor_distance": int(distance),
                "neighbor_axis_changed": axis_changed,
                "conditions": dict(next_conditions),
                "threshold_feature": threshold_feature,
                "threshold_side": threshold_side,
                "threshold_quantile": next_quantile,
            },
        )

    add_spec(kind="center", distance=0, axis_changed="none", next_conditions=conditions, next_quantile=threshold_quantile)

    if threshold_feature and threshold_side != "none":
        quantiles = _quantiles_for_threshold_feature(threshold_feature, threshold_side)
        if quantiles and math.isfinite(threshold_quantile):
            center_index = min(range(len(quantiles)), key=lambda index: abs(float(quantiles[index]) - float(threshold_quantile)))
            for delta in range(-NEGATIVE_SPACE_THRESHOLD_RADIUS_STEPS, NEGATIVE_SPACE_THRESHOLD_RADIUS_STEPS + 1):
                if delta == 0:
                    continue
                neighbor_index = center_index + delta
                if 0 <= neighbor_index < len(quantiles):
                    add_spec(
                        kind="threshold_perturb",
                        distance=abs(delta),
                        axis_changed=threshold_feature,
                        next_conditions=conditions,
                        next_quantile=float(quantiles[neighbor_index]),
                    )

    for axis in sorted(conditions):
        relaxed = dict(conditions)
        relaxed.pop(axis, None)
        add_spec(
            kind="scope_relax_axis",
            distance=1,
            axis_changed=axis,
            next_conditions=relaxed,
            next_quantile=threshold_quantile,
        )
        for value in _eligible_scope_values(train, axis):
            if value == str(conditions.get(axis)):
                continue
            replaced = dict(conditions)
            replaced[axis] = value
            add_spec(
                kind="scope_replace_axis_value",
                distance=1,
                axis_changed=axis,
                next_conditions=replaced,
                next_quantile=threshold_quantile,
            )

    if not conditions:
        for axis in RULE_SCOPE_AXES:
            for value in _eligible_scope_values(train, axis):
                add_spec(
                    kind="scope_tighten_axis_value",
                    distance=1,
                    axis_changed=axis,
                    next_conditions={axis: value},
                    next_quantile=threshold_quantile,
                )

    return list(specs.values())


def _negative_space_row(
    *,
    center: dict[str, object],
    neighbor: dict[str, object],
    train: pd.DataFrame,
    basin_id: str,
    test_day_ord: int,
    train_window_days: int,
    train_start_day_ord: int,
    train_end_day_ord: int,
) -> dict[str, object]:
    conditions = neighbor.get("conditions") if isinstance(neighbor.get("conditions"), dict) else {}
    assert isinstance(conditions, dict)
    scoped = _apply_rule_scope(train, conditions)
    threshold_feature = str(neighbor.get("threshold_feature") or "")
    threshold_side = str(neighbor.get("threshold_side") or "none")
    threshold_quantile = _float(neighbor.get("threshold_quantile"))
    threshold_uses_abs_value = threshold_side == "abs_ge"
    threshold_value = float("nan")
    threshold_valid_values = 0
    if threshold_feature and threshold_side != "none":
        source_values = pd.to_numeric(scoped.get(threshold_feature, pd.Series(np.nan, index=scoped.index)), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        fit_values = source_values.abs() if threshold_uses_abs_value else source_values
        fit_values = fit_values.dropna()
        threshold_valid_values = int(len(fit_values))
        if math.isfinite(threshold_quantile) and threshold_valid_values > 0:
            threshold_value = _safe_quantile(fit_values, float(threshold_quantile))
        if math.isfinite(threshold_value):
            filtered = _apply_threshold(scoped, feature=threshold_feature, side=threshold_side, threshold_value=threshold_value)
        else:
            filtered = scoped.iloc[0:0].copy()
    else:
        filtered = scoped.copy()

    neighbor_events = int(len(filtered))
    neighbor_symbols = int(filtered["symbol"].nunique()) if "symbol" in filtered.columns and not filtered.empty else 0
    neighbor_active_days = int(filtered["date"].nunique()) if "date" in filtered.columns and not filtered.empty else 0
    passes_min_sample = bool(
        neighbor_events >= MIN_RULE_EVENTS
        and neighbor_symbols >= MIN_RULE_SYMBOLS
        and neighbor_active_days >= MIN_RULE_ACTIVE_DAYS
    )
    fail_reason = _negative_space_fail_reason(
        scoped_events=int(len(scoped)),
        threshold_feature=threshold_feature,
        threshold_valid_values=threshold_valid_values,
        neighbor_events=neighbor_events,
        neighbor_symbols=neighbor_symbols,
        neighbor_active_days=neighbor_active_days,
        passes_min_sample=passes_min_sample,
    )
    scope_conditions = _scope_key(conditions)
    neighbor_key = "|".join(
        (
            str(center.get("rule_id", "")),
            str(neighbor.get("neighbor_kind", "")),
            str(neighbor.get("neighbor_axis_changed", "")),
            scope_conditions,
            threshold_feature,
            threshold_side,
            _quantile_label(threshold_quantile),
            _format_float_for_id(threshold_value),
        )
    )
    return {
        "research_id": RESEARCH_ID,
        "basin_id": basin_id,
        "center_rule_id": str(center.get("rule_id", "")),
        "center_rule_key": str(center.get("rule_key", "")),
        "neighbor_rule_id": "pmn_" + hashlib.sha1(neighbor_key.encode("utf-8")).hexdigest()[:16],
        "neighbor_rule_key": neighbor_key,
        "test_day_ord": int(test_day_ord),
        "test_date": _date_from_day_ord(test_day_ord),
        "train_window_days": int(train_window_days),
        "train_start_day_ord": int(train_start_day_ord),
        "train_end_day_ord": int(train_end_day_ord),
        "train_start_date": _date_from_day_ord(train_start_day_ord),
        "train_end_date": _date_from_day_ord(train_end_day_ord),
        "center_scope_name": str(center.get("scope_name", "")),
        "center_scope_conditions": str(center.get("scope_conditions", "")),
        "center_threshold_feature": str(center.get("threshold_feature") or ""),
        "center_threshold_side": str(center.get("threshold_side") or "none"),
        "center_threshold_quantile": _float(center.get("threshold_quantile")),
        "center_train_events": _finite_int_or_none(center.get("train_events")) or 0,
        "center_train_symbols": _finite_int_or_none(center.get("train_symbols")) or 0,
        "center_train_active_days": _finite_int_or_none(center.get("train_active_days")) or 0,
        "center_passes_min_sample": _to_bool(center.get("rule_passes_min_sample")),
        "neighbor_kind": str(neighbor.get("neighbor_kind", "")),
        "neighbor_distance": int(neighbor.get("neighbor_distance", 0) or 0),
        "neighbor_axis_changed": str(neighbor.get("neighbor_axis_changed", "")),
        "neighbor_scope_conditions": scope_conditions,
        "neighbor_scope_depth": int(len(conditions)),
        "neighbor_threshold_feature": threshold_feature,
        "neighbor_threshold_side": threshold_side,
        "neighbor_threshold_quantile": threshold_quantile,
        "neighbor_threshold_value": threshold_value,
        "neighbor_threshold_source_events": int(len(scoped)),
        "neighbor_threshold_valid_values": int(threshold_valid_values),
        "neighbor_threshold_uses_abs_value": bool(threshold_uses_abs_value),
        "neighbor_events": neighbor_events,
        "neighbor_symbols": neighbor_symbols,
        "neighbor_active_days": neighbor_active_days,
        "neighbor_passes_min_sample": passes_min_sample,
        "neighbor_fail_reason": fail_reason,
        "min_rule_events": int(MIN_RULE_EVENTS),
        "min_rule_symbols": int(MIN_RULE_SYMBOLS),
        "min_rule_active_days": int(MIN_RULE_ACTIVE_DAYS),
        "negative_space_model": NEGATIVE_SPACE_MODEL,
        "train_uses_only_days_before_test": True,
        "uses_outcome_columns": False,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _negative_space_fail_reason(
    *,
    scoped_events: int,
    threshold_feature: str,
    threshold_valid_values: int,
    neighbor_events: int,
    neighbor_symbols: int,
    neighbor_active_days: int,
    passes_min_sample: bool,
) -> str:
    if passes_min_sample:
        return "pass"
    if int(scoped_events) < MIN_TRAIN_SCOPE_EVENTS:
        return "scope_low_sample"
    if threshold_feature and int(threshold_valid_values) <= 0:
        return "threshold_no_valid_values"
    if int(neighbor_events) < MIN_RULE_EVENTS:
        return "low_events"
    if int(neighbor_symbols) < MIN_RULE_SYMBOLS:
        return "low_symbols"
    if int(neighbor_active_days) < MIN_RULE_ACTIVE_DAYS:
        return "low_active_days"
    return "failed_min_sample"


def _negative_space_columns() -> list[str]:
    return [
        "research_id", "basin_id", "center_rule_id", "center_rule_key", "neighbor_rule_id", "neighbor_rule_key",
        "test_day_ord", "test_date", "train_window_days", "train_start_day_ord", "train_end_day_ord",
        "train_start_date", "train_end_date", "center_scope_name", "center_scope_conditions", "center_threshold_feature",
        "center_threshold_side", "center_threshold_quantile", "center_train_events", "center_train_symbols",
        "center_train_active_days", "center_passes_min_sample", "neighbor_kind", "neighbor_distance", "neighbor_axis_changed",
        "neighbor_scope_conditions", "neighbor_scope_depth", "neighbor_threshold_feature", "neighbor_threshold_side",
        "neighbor_threshold_quantile", "neighbor_threshold_value", "neighbor_threshold_source_events", "neighbor_threshold_valid_values",
        "neighbor_threshold_uses_abs_value", "neighbor_events", "neighbor_symbols", "neighbor_active_days",
        "neighbor_passes_min_sample", "neighbor_fail_reason", "min_rule_events", "min_rule_symbols", "min_rule_active_days",
        "negative_space_model", "train_uses_only_days_before_test", "uses_outcome_columns", "uses_pnl", "uses_short_entry",
        "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]


def _parse_scope_conditions(value: object) -> dict[str, str]:
    text = str(value or "").strip()
    if not text or text == "*":
        return {}
    conditions: dict[str, str] = {}
    for part in text.split(";"):
        if not part or "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if key:
            conditions[key] = raw_value
    return conditions


def _quantiles_for_threshold_feature(feature: str, side: str) -> tuple[float, ...]:
    for known_feature, known_side, quantiles in RULE_THRESHOLD_FEATURES:
        if known_feature == feature and known_side == side:
            return tuple(float(value) for value in quantiles)
    return tuple(float(value) for value in RULE_QUANTILES)


def _eligible_scope_values(train: pd.DataFrame, axis: str) -> list[str]:
    if axis not in train.columns:
        return []
    counts = train[axis].fillna("missing").astype(str).value_counts(dropna=False)
    values = [str(value) for value, count in counts.items() if int(count) >= MIN_TRAIN_SCOPE_EVENTS]
    return sorted(values[:MAX_SCOPE_REPLACEMENT_VALUES_PER_AXIS])


def _negative_neighbor_key(
    *,
    kind: str,
    conditions: dict[str, str],
    threshold_feature: str,
    threshold_side: str,
    threshold_quantile: float,
) -> str:
    return "|".join((kind, _scope_key(conditions), threshold_feature, threshold_side, _quantile_label(threshold_quantile)))


def _basin_id_for_center(center: dict[str, object]) -> str:
    key = "|".join(
        (
            str(center.get("test_day_ord", "")),
            str(center.get("train_window_days", "")),
            str(center.get("scope_conditions", "")),
            str(center.get("threshold_feature", "")),
            str(center.get("threshold_side", "")),
            _quantile_label(_float(center.get("threshold_quantile"))),
        )
    )
    return "pmb_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]



def _build_plateau_basins(
    *,
    rule_universe: pd.DataFrame,
    negative_space: pd.DataFrame,
    taxonomy: pd.DataFrame,
    events: pd.DataFrame,
    outcomes: pd.DataFrame,
) -> pd.DataFrame:
    """Score train-only plateau basins from the full generated neighbor space.

    This is still pre-trade mechanism research.  It joins future-response
    outcomes only after the entry-known event/taxonomy rule masks have been
    constructed, and it scores every generated neighbor, including low-sample
    and rejected neighbors.  The output is a basin-level diagnostic for later
    daily prequential selection; it is not a trading system and it does not use
    PnL, short entries, or final-holdout artifacts.
    """

    columns = _plateau_basin_columns()
    if negative_space.empty or outcomes.empty:
        return pd.DataFrame(columns=columns)
    rule_input = _rule_input_frame(taxonomy=taxonomy, events=events)
    if rule_input.empty or "event_id" not in rule_input.columns:
        return pd.DataFrame(columns=columns)
    outcome_columns = [
        "event_id", "outcome_status", "future_ret_30m", "future_ret_60m", "future_min_ret_30m",
        "future_min_ret_60m", "future_max_ret_30m", "future_max_ret_60m", "down_mfe_30m", "down_mfe_60m",
        "up_mae_30m", "up_mae_60m", "reclaimed_pump_high_60m", "broke_structural_low_60m",
        "time_to_reclaim_pump_high_minutes", "time_to_structural_low_break_minutes",
    ]
    available_outcome_columns = [column for column in outcome_columns if column in outcomes.columns]
    if available_outcome_columns:
        joined_outcomes = outcomes[available_outcome_columns].drop_duplicates("event_id", keep="last")
        rule_input = rule_input.merge(joined_outcomes, on="event_id", how="left", suffixes=("", "_outcome"))
    else:
        return pd.DataFrame(columns=columns)
    rule_input = rule_input.loc[pd.to_numeric(rule_input.get("day_ord"), errors="coerce").notna()].copy()
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input["day_ord"] = pd.to_numeric(rule_input["day_ord"], errors="coerce").astype("int64")

    rows: list[dict[str, object]] = []
    grouped = negative_space.groupby("basin_id", dropna=False, sort=True)
    for basin_id, basin_neighbors in grouped:
        first = basin_neighbors.iloc[0]
        train_start_day_ord = _finite_int_or_none(first.get("train_start_day_ord"))
        train_end_day_ord = _finite_int_or_none(first.get("train_end_day_ord"))
        test_day_ord = _finite_int_or_none(first.get("test_day_ord"))
        train_window_days = _finite_int_or_none(first.get("train_window_days"))
        if train_start_day_ord is None or train_end_day_ord is None or test_day_ord is None or train_window_days is None:
            continue
        train = rule_input.loc[
            (rule_input["day_ord"] >= int(train_start_day_ord)) & (rule_input["day_ord"] <= int(train_end_day_ord))
        ].copy()
        if train.empty:
            continue
        scored_neighbors = [_score_negative_space_neighbor(row=neighbor, train=train) for neighbor in basin_neighbors.to_dict("records")]
        rows.append(
            _plateau_basin_row(
                basin_id=str(basin_id),
                first=first.to_dict(),
                scored_neighbors=scored_neighbors,
                test_day_ord=int(test_day_ord),
                train_window_days=int(train_window_days),
                train_start_day_ord=int(train_start_day_ord),
                train_end_day_ord=int(train_end_day_ord),
            )
        )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "basin_status", "basin_score", "basin_id"])


def _score_negative_space_neighbor(*, row: dict[str, object], train: pd.DataFrame) -> dict[str, object]:
    conditions = _parse_scope_conditions(row.get("neighbor_scope_conditions"))
    scoped = _apply_rule_scope(train, conditions)
    threshold_feature = str(row.get("neighbor_threshold_feature") or "")
    threshold_side = str(row.get("neighbor_threshold_side") or "none")
    threshold_value = _float(row.get("neighbor_threshold_value"))
    if threshold_feature and threshold_side != "none" and math.isfinite(threshold_value):
        filtered = _apply_threshold(scoped, feature=threshold_feature, side=threshold_side, threshold_value=threshold_value)
    elif threshold_feature and threshold_side != "none":
        filtered = scoped.iloc[0:0].copy()
    else:
        filtered = scoped.copy()
    metrics = _plateau_response_metrics(filtered)
    min_sample_pass = _to_bool(row.get("neighbor_passes_min_sample"))
    score = _plateau_neighbor_score(metrics)
    expected_downside = _plateau_expected_downside(metrics)
    scored_pass = bool(min_sample_pass and math.isfinite(score) and score > PLATEAU_P25_SCORE_MIN and expected_downside)
    return {
        **metrics,
        "neighbor_rule_id": str(row.get("neighbor_rule_id", "")),
        "neighbor_kind": str(row.get("neighbor_kind", "")),
        "neighbor_distance": int(row.get("neighbor_distance", 0) or 0),
        "neighbor_axis_changed": str(row.get("neighbor_axis_changed", "")),
        "neighbor_passes_min_sample": bool(min_sample_pass),
        "neighbor_passes_score_gate": scored_pass,
        "neighbor_response_score": score,
        "neighbor_expected_downside": bool(expected_downside),
    }


def _plateau_response_metrics(frame: pd.DataFrame) -> dict[str, object]:
    events = int(len(frame))
    symbols = int(frame["symbol"].nunique()) if "symbol" in frame.columns and not frame.empty else 0
    active_days = int(frame["date"].nunique()) if "date" in frame.columns and not frame.empty else 0
    dependency = _dependency_metrics(frame)
    future_min_30 = pd.to_numeric(frame.get("future_min_ret_30m", pd.Series(dtype=float)), errors="coerce")
    future_min_60 = pd.to_numeric(frame.get("future_min_ret_60m", pd.Series(dtype=float)), errors="coerce")
    reclaimed = _as_bool_series(frame.get("reclaimed_pump_high_60m", pd.Series(dtype=bool))) if not frame.empty else pd.Series(dtype=bool)
    broke = _as_bool_series(frame.get("broke_structural_low_60m", pd.Series(dtype=bool))) if not frame.empty else pd.Series(dtype=bool)
    return {
        "events": events,
        "symbols": symbols,
        "active_days": active_days,
        "median_future_ret_30m": _safe_median(frame.get("future_ret_30m", pd.Series(dtype=float))),
        "median_future_ret_60m": _safe_median(frame.get("future_ret_60m", pd.Series(dtype=float))),
        "median_future_min_ret_30m": _safe_median(future_min_30),
        "median_future_min_ret_60m": _safe_median(future_min_60),
        "median_future_max_ret_30m": _safe_median(frame.get("future_max_ret_30m", pd.Series(dtype=float))),
        "median_future_max_ret_60m": _safe_median(frame.get("future_max_ret_60m", pd.Series(dtype=float))),
        "median_down_mfe_30m": _safe_median(frame.get("down_mfe_30m", pd.Series(dtype=float))),
        "median_down_mfe_60m": _safe_median(frame.get("down_mfe_60m", pd.Series(dtype=float))),
        "median_up_mae_30m": _safe_median(frame.get("up_mae_30m", pd.Series(dtype=float))),
        "median_up_mae_60m": _safe_median(frame.get("up_mae_60m", pd.Series(dtype=float))),
        "downside_hit_rate_30m": _rate(future_min_30 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD),
        "downside_hit_rate_60m": _rate(future_min_60 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD),
        "reclaim_rate_60m": _rate(reclaimed) if not reclaimed.empty else float("nan"),
        "structural_low_break_rate_60m": _rate(broke) if not broke.empty else float("nan"),
        "median_time_to_reclaim_pump_high_minutes": _safe_median(frame.get("time_to_reclaim_pump_high_minutes", pd.Series(dtype=float))),
        "median_time_to_structural_low_break_minutes": _safe_median(frame.get("time_to_structural_low_break_minutes", pd.Series(dtype=float))),
        **dependency,
    }


def _plateau_neighbor_score(metrics: dict[str, object]) -> float:
    # A positive score means the train-window response surface points down after
    # the feature cutoff.  The score is intentionally pre-trade: it uses outcome
    # distributions, not entries/exits/R-multiples.
    components = [
        -_float(metrics.get("median_future_ret_30m")),
        -_float(metrics.get("median_future_ret_60m")),
        -_float(metrics.get("median_future_min_ret_30m")),
        -_float(metrics.get("median_future_min_ret_60m")),
        0.02 * (_float(metrics.get("downside_hit_rate_30m")) - 0.50),
        0.02 * (_float(metrics.get("downside_hit_rate_60m")) - 0.50),
        0.02 * (_float(metrics.get("structural_low_break_rate_60m")) - 0.30),
        0.02 * (0.50 - _float(metrics.get("reclaim_rate_60m"))),
    ]
    finite = [float(value) for value in components if math.isfinite(float(value))]
    return float(np.mean(finite)) if finite else float("nan")


def _plateau_expected_downside(metrics: dict[str, object]) -> bool:
    median_min_30 = _float(metrics.get("median_future_min_ret_30m"))
    median_min_60 = _float(metrics.get("median_future_min_ret_60m"))
    downside_30 = _float(metrics.get("downside_hit_rate_30m"))
    downside_60 = _float(metrics.get("downside_hit_rate_60m"))
    median_ret_60 = _float(metrics.get("median_future_ret_60m"))
    has_negative_path = (math.isfinite(median_min_30) and median_min_30 < 0.0) or (math.isfinite(median_min_60) and median_min_60 < 0.0)
    has_downside_frequency = (math.isfinite(downside_30) and downside_30 >= 0.45) or (math.isfinite(downside_60) and downside_60 >= 0.45)
    no_strong_positive_drift = not math.isfinite(median_ret_60) or median_ret_60 <= 0.005
    return bool(has_negative_path and has_downside_frequency and no_strong_positive_drift)


def _plateau_basin_row(
    *,
    basin_id: str,
    first: dict[str, object],
    scored_neighbors: list[dict[str, object]],
    test_day_ord: int,
    train_window_days: int,
    train_start_day_ord: int,
    train_end_day_ord: int,
) -> dict[str, object]:
    all_scores = [_float(row.get("neighbor_response_score")) for row in scored_neighbors]
    finite_scores = [score for score in all_scores if math.isfinite(score)]
    score_pass_flags = [bool(row.get("neighbor_passes_score_gate")) for row in scored_neighbors]
    min_sample_flags = [bool(row.get("neighbor_passes_min_sample")) for row in scored_neighbors]
    expected_downside_flags = [bool(row.get("neighbor_expected_downside")) for row in scored_neighbors]
    center_rows = [row for row in scored_neighbors if str(row.get("neighbor_kind")) == "center"]
    center = center_rows[0] if center_rows else (scored_neighbors[0] if scored_neighbors else {})
    center_score = _float(center.get("neighbor_response_score"))
    neighbor_count = int(len(scored_neighbors))
    scoreable_count = int(len(finite_scores))
    score_pass_count = int(sum(score_pass_flags))
    min_sample_pass_count = int(sum(min_sample_flags))
    expected_downside_count = int(sum(expected_downside_flags))
    neighbor_survival_rate = score_pass_count / neighbor_count if neighbor_count else float("nan")
    min_sample_survival_rate = min_sample_pass_count / neighbor_count if neighbor_count else float("nan")
    sign_consistency = expected_downside_count / neighbor_count if neighbor_count else float("nan")
    median_neighbor_score = float(np.median(finite_scores)) if finite_scores else float("nan")
    p25_neighbor_score = float(np.quantile(finite_scores, 0.25)) if finite_scores else float("nan")
    worst_neighbor_score = float(np.min(finite_scores)) if finite_scores else float("nan")
    best_neighbor_score = float(np.max(finite_scores)) if finite_scores else float("nan")
    score_degradation_pct = _score_degradation_pct(center_score=center_score, median_neighbor_score=median_neighbor_score)
    cliff_penalty = _cliff_penalty(
        center_score=center_score,
        p25_neighbor_score=p25_neighbor_score,
        neighbor_survival_rate=neighbor_survival_rate,
        min_sample_survival_rate=min_sample_survival_rate,
    )
    dependency_penalty = _basin_dependency_penalty(scored_neighbors)
    basin_score = _basin_score(
        median_neighbor_score=median_neighbor_score,
        p25_neighbor_score=p25_neighbor_score,
        sign_consistency=sign_consistency,
        neighbor_survival_rate=neighbor_survival_rate,
        min_sample_survival_rate=min_sample_survival_rate,
        cliff_penalty=cliff_penalty,
        dependency_penalty=dependency_penalty,
    )
    status = _plateau_basin_status(
        neighbor_count=neighbor_count,
        neighbor_survival_rate=neighbor_survival_rate,
        sign_consistency=sign_consistency,
        p25_neighbor_score=p25_neighbor_score,
        basin_score=basin_score,
        cliff_penalty=cliff_penalty,
    )
    passes_gate = status in {"challenger", "tactical", "strong_candidate"}
    return {
        "research_id": RESEARCH_ID,
        "basin_id": basin_id,
        "center_rule_id": str(first.get("center_rule_id", "")),
        "center_rule_key": str(first.get("center_rule_key", "")),
        "test_day_ord": int(test_day_ord),
        "test_date": _date_from_day_ord(test_day_ord),
        "train_window_days": int(train_window_days),
        "train_start_day_ord": int(train_start_day_ord),
        "train_end_day_ord": int(train_end_day_ord),
        "train_start_date": _date_from_day_ord(train_start_day_ord),
        "train_end_date": _date_from_day_ord(train_end_day_ord),
        "center_scope_name": str(first.get("center_scope_name", "")),
        "center_scope_conditions": str(first.get("center_scope_conditions", "")),
        "center_threshold_feature": str(first.get("center_threshold_feature", "")),
        "center_threshold_side": str(first.get("center_threshold_side", "")),
        "center_threshold_quantile": _float(first.get("center_threshold_quantile")),
        "center_train_events": int(first.get("center_train_events", 0) or 0),
        "center_train_symbols": int(first.get("center_train_symbols", 0) or 0),
        "center_train_active_days": int(first.get("center_train_active_days", 0) or 0),
        "center_response_score": center_score,
        "center_expected_downside": bool(center.get("neighbor_expected_downside", False)),
        "center_median_future_ret_30m": _float(center.get("median_future_ret_30m")),
        "center_median_future_ret_60m": _float(center.get("median_future_ret_60m")),
        "center_median_future_min_ret_30m": _float(center.get("median_future_min_ret_30m")),
        "center_median_future_min_ret_60m": _float(center.get("median_future_min_ret_60m")),
        "center_downside_hit_rate_30m": _float(center.get("downside_hit_rate_30m")),
        "center_downside_hit_rate_60m": _float(center.get("downside_hit_rate_60m")),
        "neighbor_count": neighbor_count,
        "neighbor_scoreable_count": scoreable_count,
        "neighbor_min_sample_pass_count": min_sample_pass_count,
        "neighbor_score_pass_count": score_pass_count,
        "neighbor_expected_downside_count": expected_downside_count,
        "neighbor_survival_rate": neighbor_survival_rate,
        "min_sample_survival_rate": min_sample_survival_rate,
        "sign_consistency": sign_consistency,
        "median_neighbor_score": median_neighbor_score,
        "p25_neighbor_score": p25_neighbor_score,
        "worst_neighbor_score": worst_neighbor_score,
        "best_neighbor_score": best_neighbor_score,
        "score_degradation_pct": score_degradation_pct,
        "cliff_penalty": cliff_penalty,
        "dependency_penalty": dependency_penalty,
        "max_neighbor_top_symbol_dependency_pct": _max_neighbor_metric(scored_neighbors, "top_symbol_dependency_pct"),
        "max_neighbor_largest_day_event_share": _max_neighbor_metric(scored_neighbors, "largest_day_event_share"),
        "basin_score": basin_score,
        "basin_status": status,
        "basin_passes_plateau_gate": bool(passes_gate),
        "plateau_basin_model": PLATEAU_BASIN_MODEL,
        "outcome_usage_model": FUTURE_OUTCOME_USAGE_MODEL,
        "train_uses_only_days_before_test": True,
        "uses_outcome_columns": True,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _score_degradation_pct(*, center_score: float, median_neighbor_score: float) -> float:
    if not math.isfinite(center_score) or not math.isfinite(median_neighbor_score):
        return float("nan")
    denominator = max(abs(center_score), 1e-9)
    return float(max(0.0, center_score - median_neighbor_score) / denominator)


def _cliff_penalty(
    *,
    center_score: float,
    p25_neighbor_score: float,
    neighbor_survival_rate: float,
    min_sample_survival_rate: float,
) -> float:
    penalty = 0.0
    if math.isfinite(center_score) and math.isfinite(p25_neighbor_score):
        penalty += max(0.0, center_score - p25_neighbor_score)
    if math.isfinite(neighbor_survival_rate):
        penalty += max(0.0, PLATEAU_MIN_NEIGHBOR_SURVIVAL_RATE - neighbor_survival_rate)
    if math.isfinite(min_sample_survival_rate):
        penalty += 0.5 * max(0.0, PLATEAU_MIN_NEIGHBOR_SURVIVAL_RATE - min_sample_survival_rate)
    return float(penalty)


def _basin_dependency_penalty(scored_neighbors: list[dict[str, object]]) -> float:
    symbol_dependency = _max_neighbor_metric(scored_neighbors, "top_symbol_dependency_pct")
    day_dependency = _max_neighbor_metric(scored_neighbors, "largest_day_event_share")
    components = []
    if math.isfinite(symbol_dependency):
        components.append(max(0.0, symbol_dependency - 0.50))
    if math.isfinite(day_dependency):
        components.append(max(0.0, day_dependency - 0.35))
    return float(sum(components)) if components else 0.0


def _basin_score(
    *,
    median_neighbor_score: float,
    p25_neighbor_score: float,
    sign_consistency: float,
    neighbor_survival_rate: float,
    min_sample_survival_rate: float,
    cliff_penalty: float,
    dependency_penalty: float,
) -> float:
    components = [
        median_neighbor_score,
        p25_neighbor_score,
        0.02 * (sign_consistency - 0.50),
        0.02 * (neighbor_survival_rate - 0.50),
        0.01 * (min_sample_survival_rate - 0.50),
        -cliff_penalty,
        -dependency_penalty,
    ]
    finite = [float(value) for value in components if math.isfinite(float(value))]
    return float(np.sum(finite)) if finite else float("nan")


def _plateau_basin_status(
    *,
    neighbor_count: int,
    neighbor_survival_rate: float,
    sign_consistency: float,
    p25_neighbor_score: float,
    basin_score: float,
    cliff_penalty: float,
) -> str:
    if int(neighbor_count) < PLATEAU_MIN_NEIGHBORS:
        return "rejected"
    if not all(math.isfinite(value) for value in (neighbor_survival_rate, sign_consistency, p25_neighbor_score, basin_score)):
        return "rejected"
    if p25_neighbor_score <= PLATEAU_P25_SCORE_MIN:
        return "rejected"
    if neighbor_survival_rate < PLATEAU_MIN_NEIGHBOR_SURVIVAL_RATE or sign_consistency < PLATEAU_SIGN_CONSISTENCY_MIN:
        return "cooldown"
    if basin_score <= 0.0:
        return "cooldown"
    if (
        neighbor_survival_rate >= PLATEAU_STRONG_NEIGHBOR_SURVIVAL_RATE
        and sign_consistency >= PLATEAU_STRONG_SIGN_CONSISTENCY_MIN
        and cliff_penalty <= 0.02
        and basin_score > 0.02
    ):
        return "strong_candidate"
    if neighbor_survival_rate >= 0.62 and sign_consistency >= 0.65 and basin_score > 0.01:
        return "tactical"
    return "challenger"


def _max_neighbor_metric(scored_neighbors: list[dict[str, object]], key: str) -> float:
    values = [_float(row.get(key)) for row in scored_neighbors]
    finite = [value for value in values if math.isfinite(value)]
    return float(max(finite)) if finite else float("nan")


def _plateau_basin_columns() -> list[str]:
    return [
        "research_id", "basin_id", "center_rule_id", "center_rule_key", "test_day_ord", "test_date",
        "train_window_days", "train_start_day_ord", "train_end_day_ord", "train_start_date", "train_end_date",
        "center_scope_name", "center_scope_conditions", "center_threshold_feature", "center_threshold_side",
        "center_threshold_quantile", "center_train_events", "center_train_symbols", "center_train_active_days",
        "center_response_score", "center_expected_downside", "center_median_future_ret_30m",
        "center_median_future_ret_60m", "center_median_future_min_ret_30m", "center_median_future_min_ret_60m",
        "center_downside_hit_rate_30m", "center_downside_hit_rate_60m", "neighbor_count", "neighbor_scoreable_count",
        "neighbor_min_sample_pass_count", "neighbor_score_pass_count", "neighbor_expected_downside_count",
        "neighbor_survival_rate", "min_sample_survival_rate", "sign_consistency", "median_neighbor_score",
        "p25_neighbor_score", "worst_neighbor_score", "best_neighbor_score", "score_degradation_pct", "cliff_penalty",
        "dependency_penalty", "max_neighbor_top_symbol_dependency_pct", "max_neighbor_largest_day_event_share", "basin_score",
        "basin_status", "basin_passes_plateau_gate", "plateau_basin_model", "outcome_usage_model",
        "train_uses_only_days_before_test", "uses_outcome_columns", "uses_pnl", "uses_short_entry",
        "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]

def _build_daily_prequential_oos(
    *,
    plateau_basins: pd.DataFrame,
    rule_universe: pd.DataFrame,
    taxonomy: pd.DataFrame,
    events: pd.DataFrame,
    outcomes: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate selected train-only basins on their held-out test day.

    The selection side reads only plateau basin rows that were already scored on
    days < D.  The evaluation side then applies the selected center rule to
    events whose feature snapshot day is exactly D and joins outcomes only for
    measuring the held-out response.  This is the first artifact that answers
    the professional question: would a basin selected from the past have kept
    its response sign on the next day?
    """

    selection_columns = _daily_selection_columns()
    oos_columns = _daily_oos_columns()
    window_columns = _window_health_columns()
    drift_columns = _selection_drift_columns()
    if plateau_basins.empty or rule_universe.empty:
        empty_selection = pd.DataFrame(columns=selection_columns)
        empty_oos = pd.DataFrame(columns=oos_columns)
        return empty_selection, empty_oos, pd.DataFrame(columns=window_columns), pd.DataFrame(columns=drift_columns)

    selected = _select_daily_basins(plateau_basins=plateau_basins)
    daily_selection = _ensure_columns(selected, selection_columns)
    if daily_selection.empty:
        empty_oos = pd.DataFrame(columns=oos_columns)
        return (
            daily_selection,
            pd.DataFrame(columns=oos_columns),
            _build_window_health(plateau_basins=plateau_basins, daily_selection=daily_selection, daily_oos=empty_oos),
            pd.DataFrame(columns=drift_columns),
        )

    rule_lookup = rule_universe.drop_duplicates("rule_id", keep="last").set_index("rule_id", drop=False) if "rule_id" in rule_universe.columns else pd.DataFrame()
    oos_input = _joined_rule_outcome_input(taxonomy=taxonomy, events=events, outcomes=outcomes)
    rows: list[dict[str, object]] = []
    for selection_row in daily_selection.to_dict("records"):
        center_rule_id = str(selection_row.get("center_rule_id", ""))
        if rule_lookup.empty or center_rule_id not in rule_lookup.index:
            rows.append(_daily_oos_missing_rule_row(selection_row, reason="center_rule_missing_from_rule_universe"))
            continue
        rule = rule_lookup.loc[center_rule_id]
        if isinstance(rule, pd.DataFrame):
            rule = rule.iloc[-1]
        rows.append(_evaluate_selected_basin_on_test_day(selection=selection_row, rule=rule.to_dict(), oos_input=oos_input))

    daily_oos = _ensure_columns(pd.DataFrame(rows), oos_columns)
    daily_oos = _sort_frame(daily_oos, ["test_day_ord", "train_window_days", "oos_response_score", "selected_rank", "basin_id"])
    window_health = _build_window_health(plateau_basins=plateau_basins, daily_selection=daily_selection, daily_oos=daily_oos)
    selection_drift = _build_selection_drift(daily_selection=daily_selection)
    return (
        _sort_frame(daily_selection, ["test_day_ord", "train_window_days", "selected_rank", "basin_id"]),
        daily_oos,
        window_health,
        selection_drift,
    )


def _select_daily_basins(*, plateau_basins: pd.DataFrame) -> pd.DataFrame:
    if plateau_basins.empty:
        return pd.DataFrame(columns=_daily_selection_columns())
    work = plateau_basins.copy()
    if "basin_passes_plateau_gate" in work.columns:
        pass_mask = work["basin_passes_plateau_gate"].map(_to_bool)
    else:
        pass_mask = work.get("basin_status", pd.Series(dtype=str)).astype(str).isin(DAILY_ALLOWED_BASIN_STATUSES)
    status_mask = work.get("basin_status", pd.Series(dtype=str)).astype(str).isin(DAILY_ALLOWED_BASIN_STATUSES)
    work = work.loc[pass_mask & status_mask].copy()
    if work.empty:
        return pd.DataFrame(columns=_daily_selection_columns())
    work["_status_rank"] = work["basin_status"].astype(str).map({"strong_candidate": 0, "tactical": 1, "challenger": 2}).fillna(99).astype(int)
    work["_basin_score_sort"] = pd.to_numeric(work.get("basin_score", pd.Series(np.nan, index=work.index)), errors="coerce").fillna(-np.inf)
    work["_neighbor_survival_sort"] = pd.to_numeric(work.get("neighbor_survival_rate", pd.Series(np.nan, index=work.index)), errors="coerce").fillna(-np.inf)
    rows: list[dict[str, object]] = []
    grouped = work.groupby(["test_day_ord", "train_window_days"], dropna=False, sort=True)
    for (_test_day_ord, _train_window_days), frame in grouped:
        ordered = frame.sort_values(
            ["_status_rank", "_basin_score_sort", "_neighbor_survival_sort", "basin_id"],
            ascending=[True, False, False, True],
        ).head(MAX_SELECTED_BASINS_PER_DAY_WINDOW)
        for rank, row in enumerate(ordered.to_dict("records"), start=1):
            rows.append(_daily_selection_row(row=row, selected_rank=rank))
    return _ensure_columns(pd.DataFrame(rows), _daily_selection_columns())


def _daily_selection_row(*, row: dict[str, object], selected_rank: int) -> dict[str, object]:
    test_day_ord = int(row.get("test_day_ord", 0) or 0)
    train_start_day_ord = int(row.get("train_start_day_ord", test_day_ord) or test_day_ord)
    train_end_day_ord = int(row.get("train_end_day_ord", test_day_ord - 1) or (test_day_ord - 1))
    selection_key = _selection_key(row)
    return {
        "research_id": RESEARCH_ID,
        "test_day_ord": test_day_ord,
        "test_date": _date_from_day_ord(test_day_ord),
        "train_window_days": int(row.get("train_window_days", 0) or 0),
        "train_start_day_ord": train_start_day_ord,
        "train_end_day_ord": train_end_day_ord,
        "train_start_date": _date_from_day_ord(train_start_day_ord),
        "train_end_date": _date_from_day_ord(train_end_day_ord),
        "selected_rank": int(selected_rank),
        "basin_id": str(row.get("basin_id", "")),
        "center_rule_id": str(row.get("center_rule_id", "")),
        "center_rule_key": str(row.get("center_rule_key", "")),
        "selection_key": selection_key,
        "center_scope_name": str(row.get("center_scope_name", "")),
        "center_scope_conditions": str(row.get("center_scope_conditions", "")),
        "center_threshold_feature": str(row.get("center_threshold_feature", "")),
        "center_threshold_side": str(row.get("center_threshold_side", "")),
        "center_threshold_quantile": _float(row.get("center_threshold_quantile")),
        "basin_status": str(row.get("basin_status", "")),
        "basin_score": _float(row.get("basin_score")),
        "center_response_score": _float(row.get("center_response_score")),
        "median_neighbor_score": _float(row.get("median_neighbor_score")),
        "p25_neighbor_score": _float(row.get("p25_neighbor_score")),
        "neighbor_survival_rate": _float(row.get("neighbor_survival_rate")),
        "min_sample_survival_rate": _float(row.get("min_sample_survival_rate")),
        "sign_consistency": _float(row.get("sign_consistency")),
        "cliff_penalty": _float(row.get("cliff_penalty")),
        "dependency_penalty": _float(row.get("dependency_penalty")),
        "daily_selection_model": DAILY_SELECTION_MODEL,
        "train_uses_only_days_before_test": True,
        "selection_uses_test_day_outcomes": False,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _joined_rule_outcome_input(*, taxonomy: pd.DataFrame, events: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    rule_input = _rule_input_frame(taxonomy=taxonomy, events=events)
    if rule_input.empty or outcomes.empty or "event_id" not in rule_input.columns or "event_id" not in outcomes.columns:
        return pd.DataFrame()
    outcome_columns = [
        "event_id", "outcome_status", "future_ret_30m", "future_ret_60m", "future_min_ret_30m",
        "future_min_ret_60m", "future_max_ret_30m", "future_max_ret_60m", "down_mfe_30m", "down_mfe_60m",
        "up_mae_30m", "up_mae_60m", "reclaimed_pump_high_60m", "broke_structural_low_60m",
        "time_to_reclaim_pump_high_minutes", "time_to_structural_low_break_minutes",
    ]
    available = [column for column in outcome_columns if column in outcomes.columns]
    if not available:
        return pd.DataFrame()
    joined = rule_input.merge(outcomes[available].drop_duplicates("event_id", keep="last"), on="event_id", how="left")
    if "day_ord" in joined.columns:
        joined = joined.loc[pd.to_numeric(joined["day_ord"], errors="coerce").notna()].copy()
        joined["day_ord"] = pd.to_numeric(joined["day_ord"], errors="coerce").astype("int64")
    return joined


def _evaluate_selected_basin_on_test_day(*, selection: dict[str, object], rule: dict[str, object], oos_input: pd.DataFrame) -> dict[str, object]:
    test_day_ord = _finite_int_or_none(selection.get("test_day_ord"))
    if test_day_ord is None or oos_input.empty or "day_ord" not in oos_input.columns:
        return _daily_oos_missing_rule_row(selection, reason="missing_test_day_or_oos_input")
    test = oos_input.loc[oos_input["day_ord"] == int(test_day_ord)].copy()
    if test.empty:
        return _daily_oos_row(selection=selection, metrics=_plateau_response_metrics(test), reason="no_test_day_events")
    conditions = _parse_scope_conditions(rule.get("scope_conditions"))
    filtered = _apply_rule_scope(test, conditions)
    threshold_feature = str(rule.get("threshold_feature") or "")
    threshold_side = str(rule.get("threshold_side") or "none")
    threshold_value = _float(rule.get("threshold_value"))
    if threshold_feature and threshold_side != "none" and math.isfinite(threshold_value):
        filtered = _apply_threshold(filtered, feature=threshold_feature, side=threshold_side, threshold_value=threshold_value)
    elif threshold_feature and threshold_side != "none":
        filtered = filtered.iloc[0:0].copy()
    metrics = _plateau_response_metrics(filtered)
    reason = "pass" if int(metrics.get("events", 0) or 0) > 0 else "selected_rule_no_test_events"
    return _daily_oos_row(selection=selection, metrics=metrics, reason=reason)


def _daily_oos_missing_rule_row(selection: dict[str, object], *, reason: str) -> dict[str, object]:
    return _daily_oos_row(selection=selection, metrics=_plateau_response_metrics(pd.DataFrame()), reason=reason)


def _daily_oos_row(*, selection: dict[str, object], metrics: dict[str, object], reason: str) -> dict[str, object]:
    test_day_ord = int(selection.get("test_day_ord", 0) or 0)
    score = _plateau_neighbor_score(metrics)
    expected_downside = _plateau_expected_downside(metrics)
    events = int(metrics.get("events", 0) or 0)
    return {
        "research_id": RESEARCH_ID,
        "test_day_ord": test_day_ord,
        "test_date": _date_from_day_ord(test_day_ord),
        "train_window_days": int(selection.get("train_window_days", 0) or 0),
        "train_start_day_ord": int(selection.get("train_start_day_ord", 0) or 0),
        "train_end_day_ord": int(selection.get("train_end_day_ord", 0) or 0),
        "selected_rank": int(selection.get("selected_rank", 0) or 0),
        "basin_id": str(selection.get("basin_id", "")),
        "center_rule_id": str(selection.get("center_rule_id", "")),
        "selection_key": str(selection.get("selection_key", "")),
        "basin_status_at_selection": str(selection.get("basin_status", "")),
        "train_basin_score": _float(selection.get("basin_score")),
        "train_neighbor_survival_rate": _float(selection.get("neighbor_survival_rate")),
        "train_sign_consistency": _float(selection.get("sign_consistency")),
        "oos_events": events,
        "oos_symbols": int(metrics.get("symbols", 0) or 0),
        "oos_active_days": int(metrics.get("active_days", 0) or 0),
        "oos_median_future_ret_30m": _float(metrics.get("median_future_ret_30m")),
        "oos_median_future_ret_60m": _float(metrics.get("median_future_ret_60m")),
        "oos_median_future_min_ret_30m": _float(metrics.get("median_future_min_ret_30m")),
        "oos_median_future_min_ret_60m": _float(metrics.get("median_future_min_ret_60m")),
        "oos_downside_hit_rate_30m": _float(metrics.get("downside_hit_rate_30m")),
        "oos_downside_hit_rate_60m": _float(metrics.get("downside_hit_rate_60m")),
        "oos_reclaim_rate_60m": _float(metrics.get("reclaim_rate_60m")),
        "oos_structural_low_break_rate_60m": _float(metrics.get("structural_low_break_rate_60m")),
        "oos_top_symbol_dependency_pct": _float(metrics.get("top_symbol_dependency_pct")),
        "oos_largest_day_event_share": _float(metrics.get("largest_day_event_share")),
        "oos_response_score": score,
        "oos_expected_downside": bool(expected_downside),
        "oos_has_events": bool(events > 0),
        "oos_evaluation_status": reason,
        "daily_prequential_oos_model": DAILY_PREQUENTIAL_OOS_MODEL,
        "selection_model": DAILY_SELECTION_MODEL,
        "test_day_not_in_train_window": True,
        "selection_uses_test_day_outcomes": False,
        "evaluation_uses_test_day_outcomes": True,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _build_window_health(*, plateau_basins: pd.DataFrame, daily_selection: pd.DataFrame, daily_oos: pd.DataFrame) -> pd.DataFrame:
    columns = _window_health_columns()
    if plateau_basins.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    grouped = plateau_basins.groupby(["test_day_ord", "train_window_days"], dropna=False, sort=True)
    selection_grouped = daily_selection.groupby(["test_day_ord", "train_window_days"], dropna=False) if not daily_selection.empty else {}
    oos_grouped = daily_oos.groupby(["test_day_ord", "train_window_days"], dropna=False) if not daily_oos.empty else {}
    for key, frame in grouped:
        test_day_ord, train_window_days = int(key[0]), int(key[1])
        selected_frame = selection_grouped.get_group(key) if hasattr(selection_grouped, "groups") and key in selection_grouped.groups else pd.DataFrame()
        oos_frame = oos_grouped.get_group(key) if hasattr(oos_grouped, "groups") and key in oos_grouped.groups else pd.DataFrame()
        status = frame.get("basin_status", pd.Series(dtype=str)).astype(str)
        pass_mask = frame.get("basin_passes_plateau_gate", pd.Series(False, index=frame.index)).map(_to_bool)
        oos_scores = pd.to_numeric(oos_frame.get("oos_response_score", pd.Series(dtype=float)), errors="coerce") if not oos_frame.empty else pd.Series(dtype=float)
        oos_events = pd.to_numeric(oos_frame.get("oos_events", pd.Series(dtype=float)), errors="coerce") if not oos_frame.empty else pd.Series(dtype=float)
        expected = oos_frame.get("oos_expected_downside", pd.Series(dtype=bool)).map(_to_bool) if not oos_frame.empty else pd.Series(dtype=bool)
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "test_day_ord": test_day_ord,
                "test_date": _date_from_day_ord(test_day_ord),
                "train_window_days": train_window_days,
                "train_basin_rows": int(len(frame)),
                "train_pass_basin_rows": int(pass_mask.sum()),
                "train_strong_candidate_rows": int((status == "strong_candidate").sum()),
                "train_tactical_rows": int((status == "tactical").sum()),
                "train_challenger_rows": int((status == "challenger").sum()),
                "train_cooldown_rows": int((status == "cooldown").sum()),
                "train_rejected_rows": int((status == "rejected").sum()),
                "train_median_basin_score": _safe_median(frame.get("basin_score", pd.Series(dtype=float))),
                "train_p75_basin_score": _safe_quantile(frame.get("basin_score", pd.Series(dtype=float)), 0.75),
                "selected_basin_rows": int(len(selected_frame)),
                "selected_unique_keys": int(selected_frame["selection_key"].nunique()) if not selected_frame.empty and "selection_key" in selected_frame.columns else 0,
                "oos_rows": int(len(oos_frame)),
                "oos_rows_with_events": int((oos_events.fillna(0) > 0).sum()) if not oos_events.empty else 0,
                "oos_total_events": int(oos_events.fillna(0).sum()) if not oos_events.empty else 0,
                "oos_median_response_score": _safe_median(oos_scores),
                "oos_expected_downside_rate": _rate(expected) if not expected.empty else float("nan"),
                "window_health_model": WINDOW_HEALTH_MODEL,
                "train_uses_only_days_before_test": True,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days"])


def _build_selection_drift(*, daily_selection: pd.DataFrame) -> pd.DataFrame:
    columns = _selection_drift_columns()
    if daily_selection.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    ordered = daily_selection.sort_values(["train_window_days", "test_day_ord", "selected_rank", "selection_key"]).copy()
    seen_by_window: dict[int, dict[str, int]] = {}
    last_day_by_window_key: dict[tuple[int, str], int] = {}
    for row in ordered.to_dict("records"):
        window = int(row.get("train_window_days", 0) or 0)
        key = str(row.get("selection_key", ""))
        test_day = int(row.get("test_day_ord", 0) or 0)
        seen = seen_by_window.setdefault(window, {})
        previous_count = int(seen.get(key, 0))
        previous_day = last_day_by_window_key.get((window, key))
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "test_day_ord": test_day,
                "test_date": _date_from_day_ord(test_day),
                "train_window_days": window,
                "selected_rank": int(row.get("selected_rank", 0) or 0),
                "basin_id": str(row.get("basin_id", "")),
                "selection_key": key,
                "center_scope_conditions": str(row.get("center_scope_conditions", "")),
                "center_threshold_feature": str(row.get("center_threshold_feature", "")),
                "center_threshold_side": str(row.get("center_threshold_side", "")),
                "center_threshold_quantile": _float(row.get("center_threshold_quantile")),
                "basin_status": str(row.get("basin_status", "")),
                "basin_score": _float(row.get("basin_score")),
                "previous_selected_count_same_window": previous_count,
                "is_new_selection_key_for_window": bool(previous_count == 0),
                "days_since_previous_same_key": int(test_day - previous_day) if previous_day is not None else np.nan,
                "selection_drift_model": SELECTION_DRIFT_MODEL,
                "selection_uses_test_day_outcomes": False,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
        seen[key] = previous_count + 1
        last_day_by_window_key[(window, key)] = test_day
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["train_window_days", "test_day_ord", "selected_rank", "selection_key"])


def _selection_key(row: dict[str, object]) -> str:
    return "|".join(
        (
            str(row.get("center_scope_conditions", "")),
            str(row.get("center_threshold_feature", "")),
            str(row.get("center_threshold_side", "")),
            _quantile_label(_float(row.get("center_threshold_quantile"))),
        )
    )


def _daily_selection_columns() -> list[str]:
    return [
        "research_id", "test_day_ord", "test_date", "train_window_days", "train_start_day_ord", "train_end_day_ord",
        "train_start_date", "train_end_date", "selected_rank", "basin_id", "center_rule_id", "center_rule_key",
        "selection_key", "center_scope_name", "center_scope_conditions", "center_threshold_feature",
        "center_threshold_side", "center_threshold_quantile", "basin_status", "basin_score", "center_response_score",
        "median_neighbor_score", "p25_neighbor_score", "neighbor_survival_rate", "min_sample_survival_rate",
        "sign_consistency", "cliff_penalty", "dependency_penalty", "daily_selection_model",
        "train_uses_only_days_before_test", "selection_uses_test_day_outcomes", "uses_pnl", "uses_short_entry",
        "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]


def _daily_oos_columns() -> list[str]:
    return [
        "research_id", "test_day_ord", "test_date", "train_window_days", "train_start_day_ord", "train_end_day_ord",
        "selected_rank", "basin_id", "center_rule_id", "selection_key", "basin_status_at_selection",
        "train_basin_score", "train_neighbor_survival_rate", "train_sign_consistency", "oos_events", "oos_symbols",
        "oos_active_days", "oos_median_future_ret_30m", "oos_median_future_ret_60m", "oos_median_future_min_ret_30m",
        "oos_median_future_min_ret_60m", "oos_downside_hit_rate_30m", "oos_downside_hit_rate_60m",
        "oos_reclaim_rate_60m", "oos_structural_low_break_rate_60m", "oos_top_symbol_dependency_pct",
        "oos_largest_day_event_share", "oos_response_score", "oos_expected_downside", "oos_has_events",
        "oos_evaluation_status", "daily_prequential_oos_model", "selection_model", "test_day_not_in_train_window",
        "selection_uses_test_day_outcomes", "evaluation_uses_test_day_outcomes", "uses_pnl", "uses_short_entry",
        "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]


def _window_health_columns() -> list[str]:
    return [
        "research_id", "test_day_ord", "test_date", "train_window_days", "train_basin_rows", "train_pass_basin_rows",
        "train_strong_candidate_rows", "train_tactical_rows", "train_challenger_rows", "train_cooldown_rows",
        "train_rejected_rows", "train_median_basin_score", "train_p75_basin_score", "selected_basin_rows",
        "selected_unique_keys", "oos_rows", "oos_rows_with_events", "oos_total_events", "oos_median_response_score",
        "oos_expected_downside_rate", "window_health_model", "train_uses_only_days_before_test", "uses_pnl",
        "uses_short_entry", "uses_final_holdout_tuning", "data_access_model",
    ]


def _selection_drift_columns() -> list[str]:
    return [
        "research_id", "test_day_ord", "test_date", "train_window_days", "selected_rank", "basin_id", "selection_key",
        "center_scope_conditions", "center_threshold_feature", "center_threshold_side", "center_threshold_quantile",
        "basin_status", "basin_score", "previous_selected_count_same_window", "is_new_selection_key_for_window",
        "days_since_previous_same_key", "selection_drift_model", "selection_uses_test_day_outcomes", "uses_pnl",
        "uses_short_entry", "uses_final_holdout_tuning", "data_access_model",
    ]


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        parsed = float(value)
        return bool(math.isfinite(parsed) and parsed != 0.0)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_protocol_audit(
    *,
    events: pd.DataFrame,
    outcomes: pd.DataFrame,
    taxonomy: pd.DataFrame,
    rule_universe: pd.DataFrame,
    negative_space: pd.DataFrame,
    plateau_basins: pd.DataFrame,
    daily_selection: pd.DataFrame,
    daily_oos: pd.DataFrame,
    window_health: pd.DataFrame,
    selection_drift: pd.DataFrame,
) -> pd.DataFrame:
    """Audit the mechanism-stability protocol without changing selection.

    This is a hard guardrail layer for the focused mechanism-stability plan:
    train/test separation, no outcome columns in rule masks, negative-space
    accounting before plateau selection, and explicit artifact contracts.  It
    only reads already-built artifacts and never post-processes or repairs them.
    """

    rows: list[dict[str, object]] = []

    def add(
        audit_name: str,
        passed: bool,
        *,
        observed_rows: int = 0,
        failing_rows: int = 0,
        expected: str = "",
        observed: str = "",
        details: str = "",
    ) -> None:
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "audit_name": audit_name,
                "audit_status": "pass" if bool(passed) else "fail",
                "passed": bool(passed),
                "observed_rows": int(observed_rows),
                "failing_rows": int(failing_rows),
                "expected": expected,
                "observed": observed,
                "details": details,
                "protocol_audit_model": PROTOCOL_AUDIT_MODEL,
                "primary_evaluation_model": PRIMARY_EVALUATION_MODEL,
                "plateau_accounting_model": PLATEAU_ACCOUNTING_MODEL,
                "future_outcome_usage_model": FUTURE_OUTCOME_USAGE_MODEL,
                "data_access_model": DATA_ACCESS_MODEL,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
            }
        )

    # 1. No selected or OOS-evaluated test day may be inside its train window.
    selection_bad = _rows_where_train_touches_test_day(daily_selection)
    oos_bad = _rows_where_train_touches_test_day(daily_oos)
    add(
        "daily_selection_and_oos_train_windows_end_before_test_day",
        selection_bad == 0 and oos_bad == 0,
        observed_rows=int(len(daily_selection)) + int(len(daily_oos)),
        failing_rows=selection_bad + oos_bad,
        expected="train_end_day_ord < test_day_ord for every selected/evaluated daily row",
        observed=f"selection_bad={selection_bad};oos_bad={oos_bad}",
    )

    # 2. Train-derived rule thresholds must also be fit before the test day.
    rule_bad = _rows_where_train_touches_test_day(rule_universe)
    add(
        "rule_universe_thresholds_fit_only_on_prior_train_days",
        rule_bad == 0,
        observed_rows=int(len(rule_universe)),
        failing_rows=rule_bad,
        expected="rule_universe train_end_day_ord < test_day_ord",
        observed=f"bad_rule_rows={rule_bad}",
    )

    # 3. Rule masks and taxonomy must not use outcome/PnL/short-entry columns.
    rule_leak_bad = _rows_with_truthy_flags(
        rule_universe,
        {
            "uses_outcome_columns": False,
            "uses_pnl": False,
            "uses_short_entry": False,
            "uses_final_holdout_tuning": False,
        },
    )
    taxonomy_leak_bad = _rows_with_truthy_flags(
        taxonomy,
        {
            "taxonomy_uses_outcome_columns": False,
            "outcomes_available_in_feature_store": False,
            "future_label_available_at_entry": False,
        },
    )
    add(
        "entry_known_masks_exclude_outcomes_pnl_short_entry",
        rule_leak_bad == 0 and taxonomy_leak_bad == 0,
        observed_rows=int(len(rule_universe)) + int(len(taxonomy)),
        failing_rows=rule_leak_bad + taxonomy_leak_bad,
        expected="rule/taxonomy masks use entry-known data only",
        observed=f"rule_bad={rule_leak_bad};taxonomy_bad={taxonomy_leak_bad}",
    )

    # 4. Event/outcome stores must be separate and explicitly non-entry labels.
    event_outcome_columns = [column for column in _outcome_columns() if column in set(events.columns)]
    event_forbidden = [column for column in event_outcome_columns if column != "event_id"]
    outcome_contract_bad = _rows_with_truthy_flags(
        outcomes,
        {
            "future_label_available_at_entry": False,
            "outcomes_available_in_feature_store": False,
        },
    )
    add(
        "event_store_does_not_embed_future_outcome_labels",
        not event_forbidden and outcome_contract_bad == 0,
        observed_rows=int(len(events)) + int(len(outcomes)),
        failing_rows=int(bool(event_forbidden)) + outcome_contract_bad,
        expected="events contain no future outcome label columns; outcomes are not available at entry",
        observed="forbidden_event_columns=" + ",".join(event_forbidden),
    )

    # 5. Negative-space accounting must exist for selected basins and must keep rejected/failed neighbors.
    selected_basin_ids = set(daily_selection.get("basin_id", pd.Series(dtype=str)).dropna().astype(str)) if not daily_selection.empty and "basin_id" in daily_selection.columns else set()
    neg_basin_ids = set(negative_space.get("basin_id", pd.Series(dtype=str)).dropna().astype(str)) if not negative_space.empty and "basin_id" in negative_space.columns else set()
    missing_negative_space = sorted(selected_basin_ids - neg_basin_ids)
    failed_neighbor_count = _failed_negative_space_neighbor_count(negative_space, selected_basin_ids)
    selected_with_failed_neighbors = _selected_basin_ids_with_failed_neighbors(negative_space, selected_basin_ids)
    missing_failed_neighbors = sorted(selected_basin_ids - selected_with_failed_neighbors)
    add(
        "selected_basins_have_full_negative_space_rows_including_rejected_neighbors",
        not missing_negative_space and not missing_failed_neighbors,
        observed_rows=int(len(negative_space)),
        failing_rows=len(missing_negative_space) + len(missing_failed_neighbors),
        expected="each selected basin_id has generated neighbor rows and at least one rejected/min-sample-failed neighbor",
        observed=(
            f"selected_basins={len(selected_basin_ids)};negative_space_basins={len(neg_basin_ids)};"
            f"failed_neighbor_rows={failed_neighbor_count}"
        ),
        details=(
            "missing_negative_space=" + ",".join(missing_negative_space[:20])
            + ";missing_failed_neighbors=" + ",".join(missing_failed_neighbors[:20])
        ),
    )

    # 6. Daily OOS rows must explicitly say selection did not use test outcomes.
    daily_oos_flag_bad = _rows_with_truthy_flags(
        daily_oos,
        {
            "selection_uses_test_day_outcomes": False,
            "uses_pnl": False,
            "uses_short_entry": False,
            "uses_final_holdout_tuning": False,
        },
    )
    if not daily_oos.empty and "test_day_not_in_train_window" in daily_oos.columns:
        daily_oos_flag_bad += int((~daily_oos["test_day_not_in_train_window"].map(_to_bool)).sum())
    add(
        "daily_oos_is_evaluation_only_not_selection_or_pnl",
        daily_oos_flag_bad == 0,
        observed_rows=int(len(daily_oos)),
        failing_rows=daily_oos_flag_bad,
        expected="daily OOS uses outcomes only after train-only selection; no PnL/short entry/final tuning",
        observed=f"bad_daily_oos_flags={daily_oos_flag_bad}",
    )

    # 7. OI features must use closed 5m as-of feature cutoff when OI is available.
    oi_bad = 0
    if not events.empty and {"oi_available", "oi_asof_timestamp_ms", "feature_cutoff_ms"}.issubset(events.columns):
        oi_events = events.loc[events["oi_available"].map(_to_bool)].copy()
        if not oi_events.empty:
            oi_bad = int(
                (
                    pd.to_numeric(oi_events["oi_asof_timestamp_ms"], errors="coerce")
                    > pd.to_numeric(oi_events["feature_cutoff_ms"], errors="coerce")
                ).sum()
            )
    add(
        "oi_snapshot_is_closed_5m_asof_feature_cutoff",
        oi_bad == 0,
        observed_rows=int(len(events)),
        failing_rows=oi_bad,
        expected="oi_asof_timestamp_ms <= feature_cutoff_ms for OI-available events",
        observed=f"bad_oi_rows={oi_bad}",
    )

    # 8. Every major artifact row should carry the cache-only data access model.
    data_access_bad = 0
    for frame in (events, outcomes, taxonomy, rule_universe, negative_space, plateau_basins, daily_selection, daily_oos, window_health, selection_drift):
        data_access_bad += _rows_without_expected_data_access_model(frame)
    add(
        "all_major_artifacts_declare_cache_only_data_access_model",
        data_access_bad == 0,
        observed_rows=sum(int(len(frame)) for frame in (events, outcomes, taxonomy, rule_universe, negative_space, plateau_basins, daily_selection, daily_oos, window_health, selection_drift)),
        failing_rows=data_access_bad,
        expected=f"data_access_model == {DATA_ACCESS_MODEL} where the column exists",
        observed=f"bad_data_access_rows={data_access_bad}",
    )

    return _ensure_columns(pd.DataFrame(rows), _protocol_audit_columns())


def _rows_where_train_touches_test_day(frame: pd.DataFrame) -> int:
    if frame is None or frame.empty:
        return 0
    if not {"test_day_ord", "train_end_day_ord"}.issubset(frame.columns):
        return 0
    test_day = pd.to_numeric(frame["test_day_ord"], errors="coerce")
    train_end = pd.to_numeric(frame["train_end_day_ord"], errors="coerce")
    bad = train_end.notna() & test_day.notna() & (train_end >= test_day)
    return int(bad.sum())


def _rows_with_truthy_flags(frame: pd.DataFrame, expected_flags: dict[str, bool]) -> int:
    if frame is None or frame.empty:
        return 0
    bad = pd.Series(False, index=frame.index)
    for column, expected in expected_flags.items():
        if column not in frame.columns:
            continue
        values = frame[column].map(_to_bool)
        bad |= values != bool(expected)
    return int(bad.sum())


def _failed_negative_space_neighbor_count(negative_space: pd.DataFrame, selected_basin_ids: set[str]) -> int:
    if negative_space is None or negative_space.empty or "basin_id" not in negative_space.columns:
        return 0
    frame = negative_space.loc[negative_space["basin_id"].astype(str).isin(selected_basin_ids)].copy()
    if frame.empty:
        return 0
    if "neighbor_passes_min_sample" in frame.columns:
        return int((~frame["neighbor_passes_min_sample"].map(_to_bool)).sum())
    if "neighbor_fail_reason" in frame.columns:
        return int((frame["neighbor_fail_reason"].astype(str) != "pass").sum())
    return 0


def _selected_basin_ids_with_failed_neighbors(negative_space: pd.DataFrame, selected_basin_ids: set[str]) -> set[str]:
    if not selected_basin_ids or negative_space is None or negative_space.empty or "basin_id" not in negative_space.columns:
        return set()
    frame = negative_space.loc[negative_space["basin_id"].astype(str).isin(selected_basin_ids)].copy()
    if frame.empty:
        return set()
    if "neighbor_passes_min_sample" in frame.columns:
        failed = frame.loc[~frame["neighbor_passes_min_sample"].map(_to_bool)]
    elif "neighbor_fail_reason" in frame.columns:
        failed = frame.loc[frame["neighbor_fail_reason"].astype(str) != "pass"]
    else:
        return set()
    return set(failed.get("basin_id", pd.Series(dtype=str)).dropna().astype(str))


def _rows_without_expected_data_access_model(frame: pd.DataFrame) -> int:
    if frame is None or frame.empty or "data_access_model" not in frame.columns:
        return 0
    return int((frame["data_access_model"].astype(str) != DATA_ACCESS_MODEL).sum())


def _protocol_audit_columns() -> list[str]:
    return [
        "research_id", "audit_name", "audit_status", "passed", "observed_rows", "failing_rows", "expected", "observed",
        "details", "protocol_audit_model", "primary_evaluation_model", "plateau_accounting_model", "future_outcome_usage_model",
        "data_access_model", "uses_pnl", "uses_short_entry", "uses_final_holdout_tuning",
    ]


def _run_config_frame(
    *,
    config: PumpMechanismStabilityConfig,
    selected_symbols: Sequence[str],
    start_ms: object,
    end_ms: object,
    started_at: float,
    events: pd.DataFrame,
    outcomes: pd.DataFrame,
    quality: pd.DataFrame,
    cache_coverage: dict[str, object],
    taxonomy: pd.DataFrame,
    response_surfaces: pd.DataFrame,
    rule_universe: pd.DataFrame,
    negative_space: pd.DataFrame,
    plateau_basins: pd.DataFrame,
    daily_selection: pd.DataFrame,
    daily_oos: pd.DataFrame,
    window_health: pd.DataFrame,
    selection_drift: pd.DataFrame,
) -> pd.DataFrame:
    total_5m_rows = int(pd.to_numeric(quality.get("5m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    total_1m_rows = int(pd.to_numeric(quality.get("1m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    total_5m_oi_rows = int(pd.to_numeric(quality.get("5m_oi_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    ok_events = int((events.get("event_status", pd.Series(dtype=str)).astype(str) == "ok").sum()) if not events.empty else 0
    taxonomy_rows = int(len(taxonomy)) if taxonomy is not None else 0
    unique_mechanism_ids = int(taxonomy["mechanism_id"].nunique()) if taxonomy is not None and not taxonomy.empty and "mechanism_id" in taxonomy.columns else 0
    response_surface_rows = int(len(response_surfaces)) if response_surfaces is not None else 0
    rule_universe_rows = int(len(rule_universe)) if rule_universe is not None else 0
    sampled_train_rule_rows = int((rule_universe.get("rule_passes_min_sample", pd.Series(dtype=bool)).astype(bool)).sum()) if rule_universe is not None and not rule_universe.empty else 0
    negative_space_rows = int(len(negative_space)) if negative_space is not None else 0
    negative_space_pass_rows = int((negative_space.get("neighbor_passes_min_sample", pd.Series(dtype=bool)).astype(bool)).sum()) if negative_space is not None and not negative_space.empty else 0
    plateau_basin_rows = int(len(plateau_basins)) if plateau_basins is not None else 0
    plateau_basin_pass_rows = int((plateau_basins.get("basin_passes_plateau_gate", pd.Series(dtype=bool)).map(_to_bool)).sum()) if plateau_basins is not None and not plateau_basins.empty else 0
    daily_selection_rows = int(len(daily_selection)) if daily_selection is not None else 0
    daily_oos_rows = int(len(daily_oos)) if daily_oos is not None else 0
    daily_oos_rows_with_events = int((pd.to_numeric(daily_oos.get("oos_events", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).sum()) if daily_oos is not None and not daily_oos.empty else 0
    window_health_rows = int(len(window_health)) if window_health is not None else 0
    selection_drift_rows = int(len(selection_drift)) if selection_drift is not None else 0
    row = {
        **asdict(config),
        "research_id": RESEARCH_ID,
        "implementation_stage": IMPLEMENTATION_STAGE,
        "data_access_model": DATA_ACCESS_MODEL,
        "cache_read_mode": CACHE_READ_MODE,
        "cache_write_model": CACHE_WRITE_MODEL,
        "cache_dir": str(Path(config.cache_dir)),
        "output_dir": str(Path(config.output_dir)),
        "cache_probe_model": str(cache_coverage.get("cache_probe_model", "")),
        "cache_symbols_with_5m_timestamp": int(cache_coverage.get("symbols_with_5m_timestamp", 0) or 0),
        "cache_symbols_with_1m_timestamp": int(cache_coverage.get("symbols_with_1m_timestamp", 0) or 0),
        "cache_min_5m_timestamp_ms": cache_coverage.get("min_5m_timestamp_ms", np.nan),
        "cache_max_5m_timestamp_ms": cache_coverage.get("max_5m_timestamp_ms", np.nan),
        "cache_min_1m_timestamp_ms": cache_coverage.get("min_1m_timestamp_ms", np.nan),
        "cache_max_1m_timestamp_ms": cache_coverage.get("max_1m_timestamp_ms", np.nan),
        "cache_min_5m_time_utc": str(cache_coverage.get("min_5m_time_utc", "")),
        "cache_max_5m_time_utc": str(cache_coverage.get("max_5m_time_utc", "")),
        "cache_min_1m_time_utc": str(cache_coverage.get("min_1m_time_utc", "")),
        "cache_max_1m_time_utc": str(cache_coverage.get("max_1m_time_utc", "")),
        "rolling_windows_days": ",".join(str(window) for window in ROLLING_WINDOWS_DAYS),
        "primary_evaluation_model": PRIMARY_EVALUATION_MODEL,
        "plateau_accounting_model": PLATEAU_ACCOUNTING_MODEL,
        "future_outcome_usage_model": FUTURE_OUTCOME_USAGE_MODEL,
        "event_model": EVENT_MODEL,
        "feature_snapshot_model": FEATURE_SNAPSHOT_MODEL,
        "outcome_model": OUTCOME_MODEL,
        "oi_model": OI_MODEL,
        "final_holdout_tuning_allowed": False,
        "human_post_final_threshold_tuning_allowed": False,
        "daily_train_uses_only_days_before_test": True,
        "negative_space_required": True,
        "outcomes_written_separately_from_events": True,
        "outcome_columns_allowed_in_event_store": False,
        "symbols": ",".join(selected_symbols),
        "symbol_count": int(len(selected_symbols)),
        "start_timestamp_ms": start_ms,
        "end_timestamp_ms": end_ms,
        "start_time_utc": _fmt_optional_ts(start_ms),
        "end_time_utc": _fmt_optional_ts(end_ms),
        "total_loaded_5m_rows": total_5m_rows,
        "total_loaded_1m_rows": total_1m_rows,
        "total_loaded_5m_oi_rows": total_5m_oi_rows,
        "events": int(len(events)),
        "ok_events": ok_events,
        "outcomes": int(len(outcomes)),
        "taxonomy_rows": taxonomy_rows,
        "unique_mechanism_ids": unique_mechanism_ids,
        "response_surface_rows": response_surface_rows,
        "rule_universe_rows": rule_universe_rows,
        "sampled_train_rule_rows": sampled_train_rule_rows,
        "negative_space_rows": negative_space_rows,
        "negative_space_pass_rows": negative_space_pass_rows,
        "negative_space_model": NEGATIVE_SPACE_MODEL,
        "plateau_basin_rows": plateau_basin_rows,
        "plateau_basin_pass_rows": plateau_basin_pass_rows,
        "plateau_basin_model": PLATEAU_BASIN_MODEL,
        "plateau_expected_downside_ret_threshold": float(PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD),
        "plateau_min_neighbors": int(PLATEAU_MIN_NEIGHBORS),
        "plateau_min_neighbor_survival_rate": float(PLATEAU_MIN_NEIGHBOR_SURVIVAL_RATE),
        "plateau_strong_neighbor_survival_rate": float(PLATEAU_STRONG_NEIGHBOR_SURVIVAL_RATE),
        "plateau_sign_consistency_min": float(PLATEAU_SIGN_CONSISTENCY_MIN),
        "plateau_p25_score_min": float(PLATEAU_P25_SCORE_MIN),
        "plateau_basins_use_pnl": False,
        "plateau_basins_use_short_entry": False,
        "plateau_basins_use_final_holdout_tuning": False,
        "daily_selection_rows": daily_selection_rows,
        "daily_oos_rows": daily_oos_rows,
        "daily_oos_rows_with_events": daily_oos_rows_with_events,
        "window_health_rows": window_health_rows,
        "selection_drift_rows": selection_drift_rows,
        "daily_prequential_oos_model": DAILY_PREQUENTIAL_OOS_MODEL,
        "daily_selection_model": DAILY_SELECTION_MODEL,
        "window_health_model": WINDOW_HEALTH_MODEL,
        "selection_drift_model": SELECTION_DRIFT_MODEL,
        "max_selected_basins_per_day_window": int(MAX_SELECTED_BASINS_PER_DAY_WINDOW),
        "daily_allowed_basin_statuses": ",".join(DAILY_ALLOWED_BASIN_STATUSES),
        "daily_selection_uses_test_day_outcomes": False,
        "daily_oos_uses_pnl": False,
        "daily_oos_uses_short_entry": False,
        "daily_oos_uses_final_holdout_tuning": False,
        "negative_space_threshold_radius_steps": int(NEGATIVE_SPACE_THRESHOLD_RADIUS_STEPS),
        "negative_space_max_scope_replacement_values_per_axis": int(MAX_SCOPE_REPLACEMENT_VALUES_PER_AXIS),
        "negative_space_uses_outcomes": False,
        "negative_space_uses_pnl": False,
        "rule_generation_model": "train_only_entry_known_mechanism_rule_grammar_v1",
        "rule_threshold_fit_model": "quantiles_fit_inside_train_window_only",
        "rule_universe_uses_outcomes": False,
        "rule_universe_uses_pnl": False,
        "response_surface_model": "pre_trade_mechanism_outcome_distribution_v1",
        "response_surfaces_use_pnl": False,
        "response_surfaces_use_short_entry": False,
        "taxonomy_feature_source_model": "entry_known_events_only_no_outcomes",
        "taxonomy_uses_outcome_columns": False,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "end_timestamp_ms_requested": config.end_timestamp_ms,
        "end_time_utc_requested": _format_timestamp_ms(config.end_timestamp_ms),
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
    }
    for key, value in list(row.items()):
        if isinstance(value, Path):
            row[key] = str(value)
    return pd.DataFrame([row])


def _artifact_manifest_frame(*, config: PumpMechanismStabilityConfig) -> pd.DataFrame:
    planned = [
        ("pump_mechanism_run_config.csv", "written", "command/run contract and anti-leakage governance"),
        ("pump_mechanism_artifact_manifest.csv", "written", "planned artifact namespace"),
        ("pump_mechanism_events.parquet", "written", "immutable broad pump event store with entry-known features"),
        ("pump_mechanism_outcomes.parquet", "written", "future response vectors stored only as outcomes"),
        ("pump_mechanism_event_quality.csv", "written", "cache-only data quality and coverage audit"),
        ("pump_mechanism_taxonomy.csv", "written", "entry-known mechanism axes per event"),
        ("pump_mechanism_taxonomy_by_axis.csv", "written", "axis-level taxonomy counts and breadth diagnostics"),
        ("pump_mechanism_response_surfaces.csv", "written", "mechanism response diagnostics before PnL"),
        ("pump_mechanism_rule_universe.csv", "written", "train-only entry-known mechanism rule specs"),
        ("pump_mechanism_negative_space.csv", "written", "all generated train-only neighbors including failed/rejected rules"),
        ("pump_mechanism_plateau_basins.csv", "written", "train-only basin-level plateau scores from full negative space"),
        ("pump_mechanism_daily_selection.csv", "written", "train-only basin selection per test day"),
        ("pump_mechanism_daily_oos.csv", "written", "daily prequential OOS ledger for selected basins"),
        ("pump_mechanism_window_health.csv", "written", "15/30/60d train-window health plus selected-basin OOS summary"),
        ("pump_mechanism_selection_drift.csv", "written", "selected mechanism-key drift over time"),
        ("pump_mechanism_protocol_audit.csv", "written", "no-lookahead, train-only, negative-space, and daily replay guardrail audit"),
    ]
    rows = []
    for artifact_name, status, description in planned:
        rows.append(
            {
                "artifact_name": artifact_name,
                "status": status,
                "description": description,
                "output_dir": str(Path(config.output_dir)),
                "data_access_model": DATA_ACCESS_MODEL,
                "implementation_stage": IMPLEMENTATION_STAGE,
            }
        )
    return pd.DataFrame(rows)


def _load_frame_result(storage: ParquetStorage, symbol: str, timeframe: str, *, start_ms: int, end_ms: int) -> ParquetLoadResult:
    return storage.load_window_result(symbol, Timeframe(timeframe), int(start_ms), int(end_ms))


def _resolve_symbols(cache_dir: Path, symbols: Iterable[str] | None) -> list[str]:
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
        available.setdefault(normalize_symbol(decoded), decoded)
    requested = [normalize_symbol(str(symbol)) for symbol in symbols or [] if str(symbol).strip()]
    if requested:
        return sorted({available[value] for value in requested if value in available})
    return sorted(available.values())


def _resolve_end_timestamp_ms(config: PumpMechanismStabilityConfig, cache_coverage: dict[str, object]) -> int:
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
        "cache_read_mode": CACHE_READ_MODE,
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
        return {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "path": str(base_path),
            "paths_read": int(paths_read),
            "status": ";".join(status_parts[:3]) if status_parts else "missing_or_no_timestamp",
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
        "cache_read_mode": CACHE_READ_MODE,
        "cache_write_model": CACHE_WRITE_MODEL,
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
        "5m_has_taker_buy_quote_volume": bool("taker_buy_quote_volume" in frame_5m.columns),
        "1m_has_quote_volume": bool("quote_volume" in frame_1m.columns),
        "1m_has_number_of_trades": bool("number_of_trades" in frame_1m.columns),
        "1m_has_taker_buy_quote_volume": bool("taker_buy_quote_volume" in frame_1m.columns),
        "oi_load_ok": bool(oi_5m_result.ok),
        "oi_load_status": str(oi_5m_result.status),
        "oi_load_reason": str(oi_5m_result.reason),
        "oi_attempted_path": str(oi_5m_result.path),
        "oi_model": OI_MODEL,
        "data_access_model": DATA_ACCESS_MODEL,
        "data_rejection": "",
        "data_warning": "",
        "events": 0,
        "ok_events": 0,
        "outcomes": 0,
    }


def _prepare_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for col in ("timestamp", "open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_volume", "taker_buy_quote_volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    if "quote_volume" not in work.columns:
        work["quote_volume"] = np.nan
    if "number_of_trades" not in work.columns:
        work["number_of_trades"] = pd.to_numeric(work["trades"], errors="coerce") if "trades" in work.columns else np.nan
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
    available = pd.to_numeric(frame["available_timestamp_ms"], errors="coerce") if "available_timestamp_ms" in frame.columns else ts + FIVE_MINUTE_MS
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


def _closed_5m_oi_context(
    lookup: _OiLookup,
    *,
    asof_timestamp_ms: int,
    threshold_pct: float,
    strong_threshold_pct: float,
) -> dict[str, object]:
    base = {
        "oi_model": OI_MODEL,
        "oi_available": False,
        "oi_asof_timestamp_ms": int(asof_timestamp_ms),
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
        "oi_change_5m_pct": np.nan,
        "oi_change_10m_pct": np.nan,
        "oi_change_15m_pct": np.nan,
        "oi_age_ms": np.nan,
        "oi_status": "missing",
        "oi_strength_bucket": "missing",
        "oi_regime": "missing_closed_5m_oi",
    }
    if lookup.empty:
        return base
    idx = int(np.searchsorted(lookup.available_ms, int(asof_timestamp_ms), side="right")) - 1
    if idx < 1:
        return base
    oi_current = float(lookup.open_interest[idx])
    oi_previous = float(lookup.open_interest[idx - 1])
    if oi_previous <= 0 or not math.isfinite(oi_current) or not math.isfinite(oi_previous):
        return base
    change_5m = oi_current / oi_previous - 1.0
    oi_two_back = float(lookup.open_interest[idx - 2]) if idx >= 2 else float("nan")
    oi_three_back = float(lookup.open_interest[idx - 3]) if idx >= 3 else float("nan")
    change_10m = oi_current / oi_two_back - 1.0 if math.isfinite(oi_two_back) and oi_two_back > 0 else np.nan
    change_15m = oi_current / oi_three_back - 1.0 if math.isfinite(oi_three_back) and oi_three_back > 0 else np.nan
    if change_5m <= -abs(threshold_pct):
        regime = "oi_down_squeeze_unwind"
    elif change_5m >= abs(threshold_pct):
        regime = "oi_up_fresh_leverage"
    else:
        regime = "oi_flat_or_below_threshold"
    oi_age_ms = int(asof_timestamp_ms) - int(lookup.available_ms[idx])
    return {
        **base,
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
        "oi_change_pct": change_5m,
        "oi_change_5m_pct": change_5m,
        "oi_change_10m_pct": change_10m,
        "oi_change_15m_pct": change_15m,
        "oi_age_ms": oi_age_ms,
        "oi_status": "ok" if 0 <= oi_age_ms <= 2 * FIVE_MINUTE_MS else "stale",
        "oi_strength_bucket": _oi_strength_bucket(change_5m, weak_threshold_pct=threshold_pct, strong_threshold_pct=strong_threshold_pct),
        "oi_regime": regime,
    }


def _event_columns() -> list[str]:
    return [
        "research_id", "event_id", "symbol", "source_setup_type", "seed_open_ms", "seed_close_ms", "feature_cutoff_ms",
        "seed_open_time_utc", "seed_close_time_utc", "feature_cutoff_time_utc", "day_ord", "date", "session_bucket",
        "session_primary", "session_overlap", "hour_utc", "weekday", "seed_open", "seed_high", "seed_low", "seed_close",
        "seed_return_pct", "seed_high_return_pct", "seed_range_pct", "seed_quote_volume", "seed_number_of_trades",
        "seed_quote_ratio", "seed_trade_ratio", "seed_taker_buy_share", "pump_tier", "is_strong_pump_tier",
        "is_anomaly_pump_tier", "is_extreme_pump_tier", "event_model", "feature_snapshot_model", "feature_available_timestamp_ms",
        "outcome_model", "future_label_available_at_entry", "outcomes_available_in_feature_store", "data_access_model", "oi_model",
        "event_status", "feature_window_1m_rows", "pre_context_1m_rows", "structural_window_1m_rows", "feature_price",
        "pump_high", "pump_high_timestamp_ms", "pump_high_time_utc", "pre60_return_pct", "pre60_range_pct", "pre60_quote_volume",
        "pre60_number_of_trades", "pre60_taker_buy_share", "early_return_pct", "early_range_pct", "early_quote_volume",
        "early_number_of_trades", "early_quote_ratio_60m_scaled", "early_trade_ratio_60m_scaled", "early_taker_buy_quote_share",
        "close_ret_5m", "close_ret_10m", "close_ret_15m", "high_ret_15m", "close15_to_high15_ratio", "wick_ret_15m",
        "m1_last2_quote_share", "m1_last2_trade_share", "m1_quote_accel_last2_vs_first2", "m1_trade_accel_last2_vs_first2",
        "structural_low", "structural_low_timestamp_ms", "structural_low_time_utc", "structural_high", "structural_high_timestamp_ms",
        "structural_high_time_utc", "oi_available", "oi_asof_timestamp_ms", "oi_current_timestamp_ms", "oi_current_available_timestamp_ms",
        "oi_previous_timestamp_ms", "oi_two_back_timestamp_ms", "oi_three_back_timestamp_ms", "oi_current", "oi_previous", "oi_two_back",
        "oi_three_back", "oi_change_pct", "oi_change_5m_pct", "oi_change_10m_pct", "oi_change_15m_pct", "oi_age_ms", "oi_status",
        "oi_strength_bucket", "oi_regime",
    ]


def _outcome_columns() -> list[str]:
    base = [
        "research_id", "event_id", "symbol", "feature_cutoff_ms", "feature_cutoff_time_utc", "day_ord", "date", "session_bucket", "pump_tier",
        "outcome_model", "outcome_start_timestamp_ms", "outcome_start_time_utc", "max_outcome_minutes", "future_label_available_at_entry",
        "outcomes_available_in_feature_store", "data_access_model", "outcome_status", "future_1m_rows",
    ]
    for minutes in OUTCOME_HORIZONS_MINUTES:
        base.extend([f"future_ret_{minutes}m", f"future_min_ret_{minutes}m", f"future_max_ret_{minutes}m", f"down_mfe_{minutes}m", f"up_mae_{minutes}m"])
    base.extend(["time_to_reclaim_pump_high_minutes", "time_to_structural_low_break_minutes", "reclaimed_pump_high_60m", "broke_structural_low_60m"])
    return base


def _quality_columns() -> list[str]:
    return [
        "research_id", "symbol", "cache_read_mode", "cache_write_model", "load_start_timestamp_ms", "load_end_timestamp_ms",
        "load_start_time_utc", "load_end_time_utc", "5m_rows", "1m_rows", "5m_oi_rows", "5m_load_ok", "1m_load_ok",
        "5m_load_status", "1m_load_status", "5m_load_reason", "1m_load_reason", "5m_attempted_path", "1m_attempted_path",
        "5m_loaded_first_timestamp_ms", "5m_loaded_last_timestamp_ms", "1m_loaded_first_timestamp_ms", "1m_loaded_last_timestamp_ms",
        "5m_loaded_first_time_utc", "5m_loaded_last_time_utc", "1m_loaded_first_time_utc", "1m_loaded_last_time_utc",
        "5m_has_quote_volume", "5m_has_number_of_trades", "5m_has_taker_buy_quote_volume", "1m_has_quote_volume",
        "1m_has_number_of_trades", "1m_has_taker_buy_quote_volume", "oi_load_ok", "oi_load_status", "oi_load_reason", "oi_attempted_path",
        "oi_model", "data_access_model", "data_rejection", "data_warning", "events", "ok_events", "outcomes",
    ]


def _fail_fast_on_empty_input(*, config: PumpMechanismStabilityConfig, events: pd.DataFrame, quality: pd.DataFrame) -> None:
    if not bool(config.fail_on_empty_input):
        return
    total_5m = int(pd.to_numeric(quality.get("5m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    total_1m = int(pd.to_numeric(quality.get("1m_rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not quality.empty else 0
    if total_5m <= 0 or total_1m <= 0:
        raise RuntimeError(
            "Pump mechanism research loaded no usable market cache rows "
            f"for the requested window: total_5m_rows={total_5m}, total_1m_rows={total_1m}. "
            f"Diagnostics were written to {Path(config.output_dir)}. Cache was read-only and was not modified."
        )
    if events.empty:
        raise RuntimeError(
            "Pump mechanism research found no broad pump events for the requested window. "
            f"Diagnostics were written to {Path(config.output_dir)}. Cache was read-only and was not modified."
        )


def _has_real_flow(frame: pd.DataFrame) -> bool:
    return "quote_volume" in frame.columns and "number_of_trades" in frame.columns and "taker_buy_quote_volume" in frame.columns


def _pump_tier(
    *,
    seed_high_return_pct: float,
    quote_ratio: float,
    trade_ratio: float,
    config: PumpMechanismStabilityConfig,
) -> str:
    if (
        seed_high_return_pct >= float(config.extreme_pump_min_high_return_pct)
        and quote_ratio >= float(config.extreme_pump_min_quote_ratio)
        and trade_ratio >= float(config.extreme_pump_min_trade_ratio)
    ):
        return "extreme_pump"
    if (
        seed_high_return_pct >= float(config.anomaly_pump_min_high_return_pct)
        and quote_ratio >= float(config.anomaly_pump_min_quote_ratio)
        and trade_ratio >= float(config.anomaly_pump_min_trade_ratio)
    ):
        return "anomaly_pump"
    if (
        seed_high_return_pct >= float(config.strong_pump_min_high_return_pct)
        and quote_ratio >= float(config.strong_pump_min_quote_ratio)
        and trade_ratio >= float(config.strong_pump_min_trade_ratio)
    ):
        return "strong_pump"
    return "broad_pump"


def _close_return_at_minutes(frame: pd.DataFrame, *, seed_open: float, minutes: int) -> float:
    if frame.empty or not math.isfinite(seed_open) or seed_open <= 0:
        return float("nan")
    if len(frame) < minutes:
        return float("nan")
    close = _float(frame.iloc[int(minutes) - 1].get("close"))
    return close / seed_open - 1.0 if close > 0 else float("nan")


def _pre_window_return(frame: pd.DataFrame) -> float:
    if frame.empty:
        return float("nan")
    first_open = _float(frame.iloc[0].get("open"))
    last_close = _float(frame.iloc[-1].get("close"))
    return last_close / first_open - 1.0 if first_open > 0 and last_close > 0 else float("nan")


def _pre_window_range(frame: pd.DataFrame) -> float:
    if frame.empty:
        return float("nan")
    return _range_pct(_numeric_series(frame, "high"), _numeric_series(frame, "low"))


def _range_pct(highs: pd.Series, lows: pd.Series) -> float:
    high = _safe_max(highs)
    low = _safe_min(lows)
    return high / low - 1.0 if high > 0 and low > 0 else float("nan")


def _timestamp_at_min(frame: pd.DataFrame, column: str) -> int | float:
    if frame.empty or column not in frame.columns or "timestamp" not in frame.columns:
        return np.nan
    values = _numeric_series(frame, column).replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return np.nan
    return int(frame.loc[values.idxmin(), "timestamp"])


def _timestamp_at_max(frame: pd.DataFrame, column: str) -> int | float:
    if frame.empty or column not in frame.columns or "timestamp" not in frame.columns:
        return np.nan
    values = _numeric_series(frame, column).replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return np.nan
    return int(frame.loc[values.idxmax(), "timestamp"])


def _minutes_to_first(frame: pd.DataFrame, *, threshold_price: float, side: str) -> float:
    if frame.empty or not math.isfinite(threshold_price) or threshold_price <= 0:
        return float("nan")
    start = int(frame.iloc[0]["timestamp"])
    if side == "high_ge":
        values = _numeric_series(frame, "high")
        hits = frame.loc[values >= threshold_price]
    elif side == "low_le":
        values = _numeric_series(frame, "low")
        hits = frame.loc[values <= threshold_price]
    else:
        raise ValueError(f"unknown side: {side}")
    if hits.empty:
        return float("nan")
    return float((int(hits.iloc[0]["timestamp"]) - start) / MINUTE_MS)


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


def _oi_strength_bucket(change_pct: float, *, weak_threshold_pct: float, strong_threshold_pct: float) -> str:
    if not math.isfinite(change_pct):
        return "missing"
    if change_pct >= abs(strong_threshold_pct):
        return "strong_oi_up"
    if change_pct >= abs(weak_threshold_pct):
        return "mild_oi_up"
    if change_pct <= -abs(strong_threshold_pct):
        return "strong_oi_down"
    if change_pct <= -abs(weak_threshold_pct):
        return "mild_oi_down"
    return "flat"


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


def _time_window(frame: pd.DataFrame, *, start_ms: int, end_ms: int, include_end: bool) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns:
        return frame.iloc[0:0]
    ts = frame["timestamp"].to_numpy(dtype="int64", copy=False)
    left = int(np.searchsorted(ts, int(start_ms), side="left"))
    right_side = "right" if include_end else "left"
    right = int(np.searchsorted(ts, int(end_ms), side=right_side))
    if right <= left:
        return frame.iloc[0:0]
    return frame.iloc[left:right]


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")



def _safe_mean(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(values.mean()) if not values.empty else float("nan")


def _safe_quantile(series: pd.Series, quantile: float) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(values.quantile(float(quantile))) if not values.empty else float("nan")



def _numeric_condition_rate(series: pd.Series, *, threshold: float, side: str) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return float("nan")
    if side == "gt":
        return float((values > float(threshold)).mean())
    if side == "le":
        return float((values <= float(threshold)).mean())
    raise ValueError(f"unsupported numeric condition side: {side}")

def _rate(mask: pd.Series | np.ndarray | Sequence[object]) -> float:
    if isinstance(mask, pd.Series):
        values = mask.dropna()
    else:
        values = pd.Series(mask).dropna()
    if values.empty:
        return float("nan")
    return float(values.astype(bool).mean())


def _as_bool_series(values: pd.Series) -> pd.Series:
    if values.empty:
        return pd.Series(dtype=bool)
    if values.dtype == bool:
        return values
    normalized = values.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "yes", "y"})

def _safe_median(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(values.median()) if not values.empty else float("nan")


def _safe_sum(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(values.sum()) if not values.empty else 0.0


def _safe_min(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(values.min()) if not values.empty else float("nan")


def _safe_max(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(values.max()) if not values.empty else float("nan")


def _float(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return parsed


def _finite_int_or_none(value: object) -> int | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return int(parsed)


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


def _ensure_columns(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    work = frame.copy()
    for column in columns:
        if column not in work.columns:
            work[column] = pd.Series(dtype="object")
    extra = [column for column in work.columns if column not in columns]
    return work[list(columns) + extra]


def _sort_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame.reset_index(drop=True)
    existing = [col for col in columns if col in frame.columns]
    if not existing:
        return frame.reset_index(drop=True)
    return frame.sort_values(existing).reset_index(drop=True)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _format_timestamp_ms(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "latest-cache"
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _fmt_optional_ts(value: object) -> str:
    parsed = _finite_int_or_none(value)
    return _fmt_ts(parsed) if parsed is not None else ""


def _fmt_ts(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC).isoformat()


def _date_from_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC).strftime("%Y-%m-%d")


def _compact_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "").replace(":", "_").replace("-", "_").replace(" ", "")


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _safe_console_text(value: object) -> str:
    return str(value).encode("ascii", "replace").decode("ascii")


def _print_stage(label: str, stage: str, started_at: float) -> None:
    print(f"{label}: {stage} elapsed={_format_duration(time.monotonic() - started_at)}", flush=True)
