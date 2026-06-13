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
IMPLEMENTATION_STAGE = "mechanism_short_portfolio_aggregation"

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
DAILY_OOS_EVENT_LEDGER_MODEL = "daily_prequential_selected_event_ledger_v1"
OOS_DIAGNOSTICS_MODEL = "daily_prequential_oos_split_diagnostics_v1"
OOS_TOP_REMOVAL_MODEL = "daily_prequential_oos_top_removal_stress_v1"
OOS_SELECTION_SUMMARY_MODEL = "daily_prequential_selection_key_summary_v1"
OOS_VERDICT_MODEL = "daily_prequential_oos_mechanism_verdict_v1"
OOS_VERDICT_MIN_ACTIVE_DAYS = 20
OOS_VERDICT_MIN_EVENTS = 40
OOS_VERDICT_MIN_SYMBOLS = 5
OOS_VERDICT_MIN_SESSIONS = 2
OOS_VERDICT_MIN_POSITIVE_ACTIVE_SCORE_RATE = 0.55
OOS_VERDICT_MIN_MEDIAN_RESPONSE_SCORE = 0.0
OOS_VERDICT_MAX_TOP_SYMBOL_EVENT_SHARE = 0.40
OOS_VERDICT_MAX_TOP_DAY_EVENT_SHARE = 0.25
OOS_VERDICT_MAX_MONTH_POSITIVE_SCORE_SHARE = 0.50
OOS_VERDICT_TOP_REMOVAL_FRACTION = 0.10
MAX_SELECTED_BASINS_PER_DAY_WINDOW = 20
DAILY_ALLOWED_BASIN_STATUSES = ("strong_candidate", "tactical", "challenger")

SHORT_MAPPING_MODEL = "accepted_mechanism_to_short_confirm_candidates_v1"
SHORT_CONFIRM_MODEL = "closed_1m_structural_low_break_after_feature_cutoff_v1"
SHORT_ENTRY_MODEL = "next_1m_open_after_confirm_plus_slippage"
SHORT_STOP_MODEL = "structural_high_plus_buffer_no_future_high"
SHORT_OI_MODEL = "closed_5m_oi_asof_confirm_close"
SHORT_CONFIRM_WINDOW_MINUTES = 60
SHORT_CONFIRM_CLOSE_BELOW_STRUCTURAL_LOW_BUFFER_PCT = 0.0005
SHORT_CONFIRM_NEAR_LOW_MAX = 0.35
SHORT_CONFIRM_MIN_BODY_PCT = 0.0002
SHORT_CONFIRM_MIN_ENTRY_RISK_PCT = 0.0025
SHORT_CONFIRM_MAX_ENTRY_RISK_PCT = 0.08
SHORT_STOP_BUFFER_PCT = 0.0010
SHORT_ENTRY_ADVERSE_SLIPPAGE_BPS = 2.0
SHORT_ALLOWED_VERDICT = "accepted_mechanism"

SHORT_TRADE_GRID_MODEL = "accepted_mechanism_short_exit_policy_grid_v1"
SHORT_EXIT_MAX_HOLD_MINUTES = 60
SHORT_TRAIL_PIVOT_LEFT_BARS = 1
SHORT_TRAIL_PIVOT_RIGHT_BARS = 1
SHORT_TRAIL_BUFFER_PCT = 0.0005
SHORT_PARTIAL_TP_R_MULTIPLE = 1.0
SHORT_PARTIAL_TP_FRACTION = 0.50
SHORT_EXIT_POLICIES = ("short_trail_all_lower_highs", "short_tp1r_close50_trail")

SHORT_TRADE_DIAGNOSTICS_MODEL = "accepted_mechanism_short_trade_oos_diagnostics_v1"
SHORT_TRADE_TOP_REMOVAL_MODEL = "accepted_mechanism_short_trade_top_removal_v1"
SHORT_TRADE_VERDICT_MODEL = "accepted_mechanism_short_trade_verdict_v1"
SHORT_TRADE_GUARD_MODEL = "short_trade_grid_source_guard_v1"
SHORT_TRADE_VERDICT_MIN_TRADES = 20
SHORT_TRADE_VERDICT_MIN_ACTIVE_DAYS = 8
SHORT_TRADE_VERDICT_MIN_SYMBOLS = 4
SHORT_TRADE_VERDICT_MIN_SESSIONS = 1
SHORT_TRADE_VERDICT_MIN_SUM_R = 0.0
SHORT_TRADE_VERDICT_MIN_AVG_R = 0.0
SHORT_TRADE_VERDICT_MIN_MEDIAN_R = 0.0
SHORT_TRADE_VERDICT_MIN_POSITIVE_ACTIVE_DAY_RATE = 0.50
SHORT_TRADE_VERDICT_MAX_TOP_SYMBOL_TRADE_SHARE = 0.45
SHORT_TRADE_VERDICT_MAX_TOP_DAY_TRADE_SHARE = 0.35
SHORT_TRADE_VERDICT_MAX_MONTH_POSITIVE_R_SHARE = 0.55
SHORT_TRADE_TOP_REMOVAL_FRACTION = 0.10

SHORT_PORTFOLIO_CANDIDATE_MODEL = "accepted_trading_sleeve_portfolio_candidates_v1"
SHORT_PORTFOLIO_OOS_MODEL = "accepted_trading_sleeve_constrained_portfolio_oos_v1"
SHORT_PORTFOLIO_VERDICT_MODEL = "accepted_trading_sleeve_portfolio_verdict_v1"
SHORT_PORTFOLIO_GUARD_MODEL = "short_portfolio_source_and_overlap_guard_v1"
SHORT_PORTFOLIO_MAX_CONCURRENT_TRADES = 3
SHORT_PORTFOLIO_MAX_CONCURRENT_PER_SYMBOL = 1
SHORT_PORTFOLIO_SYMBOL_COOLDOWN_MINUTES = 30
SHORT_PORTFOLIO_MIN_TRADES = 20
SHORT_PORTFOLIO_MIN_ACTIVE_DAYS = 8
SHORT_PORTFOLIO_MIN_SYMBOLS = 4
SHORT_PORTFOLIO_MIN_SUM_R = 0.0
SHORT_PORTFOLIO_MIN_AVG_R = 0.0
SHORT_PORTFOLIO_MIN_MEDIAN_R = 0.0
SHORT_PORTFOLIO_MIN_POSITIVE_ACTIVE_DAY_RATE = 0.50
SHORT_PORTFOLIO_MAX_TOP_SYMBOL_TRADE_SHARE = 0.45
SHORT_PORTFOLIO_MAX_TOP_DAY_TRADE_SHARE = 0.35
SHORT_PORTFOLIO_MAX_DRAWDOWN_TO_PROFIT = 1.50


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


class _EtaProgress:
    def __init__(self, label: str, total: int, *, verb: str = "progress") -> None:
        self.label = label
        self.total = max(0, int(total))
        self.verb = verb
        self.started_at = time.monotonic()
        self.last_emit_at = 0.0

    def update(self, *, index: int, item: str = "", force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_emit_at < 2.0 and index < self.total:
            return
        self.last_emit_at = now
        pct = (index / self.total * 100.0) if self.total else 100.0
        elapsed = max(0.001, now - self.started_at)
        eta = (elapsed / max(index, 1)) * max(self.total - index, 0) if self.total else 0.0
        suffix = f" {item}" if item else ""
        print(
            f"{self.label}: {self.verb} {index}/{self.total} ({pct:5.1f}%){suffix} "
            f"elapsed={_format_duration(elapsed)} eta={_format_duration(eta)}",
            flush=True,
        )

    def finish(self) -> None:
        if self.total:
            self.update(index=self.total, force=True)


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
            daily_oos_events=pd.DataFrame(),
            oos_diagnostics=pd.DataFrame(),
            oos_top_removal=pd.DataFrame(),
            oos_selection_summary=pd.DataFrame(),
            oos_verdict=pd.DataFrame(),
            short_trade_candidates=pd.DataFrame(),
            short_trade_grid=pd.DataFrame(),
            short_trade_diagnostics=pd.DataFrame(),
            short_trade_top_removal=pd.DataFrame(),
            short_trade_verdict=pd.DataFrame(),
            short_trade_guard=pd.DataFrame(),
            short_portfolio_candidates=pd.DataFrame(),
            short_portfolio_oos=pd.DataFrame(),
            short_portfolio_verdict=pd.DataFrame(),
            short_portfolio_guard=pd.DataFrame(),
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
    rule_universe = _build_train_only_rule_universe(taxonomy=taxonomy, events=events, progress_label=progress_label)

    _print_stage(progress_label, "building full negative-space rule neighborhoods", started_at)
    negative_space = _build_negative_space_neighborhoods(
        rule_universe=rule_universe,
        taxonomy=taxonomy,
        events=events,
        progress_label=progress_label,
    )

    _print_stage(progress_label, "scoring train-only plateau basins", started_at)
    plateau_basins = _build_plateau_basins(
        rule_universe=rule_universe,
        negative_space=negative_space,
        taxonomy=taxonomy,
        events=events,
        outcomes=outcomes,
        progress_label=progress_label,
    )

    _print_stage(progress_label, "evaluating daily prequential OOS selected basins", started_at)
    daily_selection, daily_oos, window_health, selection_drift = _build_daily_prequential_oos(
        plateau_basins=plateau_basins,
        rule_universe=rule_universe,
        taxonomy=taxonomy,
        events=events,
        outcomes=outcomes,
        progress_label=progress_label,
    )

    _print_stage(progress_label, "building OOS event ledger and stability diagnostics", started_at)
    daily_oos_events = _build_daily_oos_event_ledger(
        daily_selection=daily_selection,
        rule_universe=rule_universe,
        taxonomy=taxonomy,
        events=events,
        outcomes=outcomes,
    )
    oos_diagnostics = _build_oos_diagnostics(daily_oos=daily_oos, daily_oos_events=daily_oos_events)
    oos_top_removal = _build_oos_top_removal(daily_oos_events=daily_oos_events)
    oos_selection_summary = _build_oos_selection_summary(
        daily_selection=daily_selection,
        daily_oos=daily_oos,
        daily_oos_events=daily_oos_events,
    )

    _print_stage(progress_label, "building OOS mechanism verdict and pass/fail reasons", started_at)
    oos_verdict = _build_oos_verdict(
        daily_oos=daily_oos,
        daily_oos_events=daily_oos_events,
        oos_top_removal=oos_top_removal,
        oos_selection_summary=oos_selection_summary,
    )

    _print_stage(progress_label, "mapping accepted mechanisms to honest short confirm candidates", started_at)
    short_trade_candidates = _build_short_trade_candidates(
        config=config,
        storage=storage,
        oos_verdict=oos_verdict,
        daily_oos_events=daily_oos_events,
        events=events,
    )

    _print_stage(progress_label, "simulating honest short exit policy grid", started_at)
    short_trade_grid = _build_short_trade_grid(
        storage=storage,
        short_trade_candidates=short_trade_candidates,
    )

    _print_stage(progress_label, "building short trade diagnostics and verdict gates", started_at)
    short_trade_diagnostics = _build_short_trade_diagnostics(short_trade_grid=short_trade_grid)
    short_trade_top_removal = _build_short_trade_top_removal(short_trade_grid=short_trade_grid)
    short_trade_verdict = _build_short_trade_verdict(
        short_trade_grid=short_trade_grid,
        short_trade_top_removal=short_trade_top_removal,
    )
    short_trade_guard = _build_short_trade_guard(
        short_trade_candidates=short_trade_candidates,
        short_trade_grid=short_trade_grid,
        oos_verdict=oos_verdict,
    )

    _print_stage(progress_label, "aggregating accepted trading sleeves into constrained portfolio OOS", started_at)
    short_portfolio_candidates = _build_short_portfolio_candidates(
        short_trade_grid=short_trade_grid,
        short_trade_verdict=short_trade_verdict,
    )
    short_portfolio_oos = _build_short_portfolio_oos(short_portfolio_candidates=short_portfolio_candidates)
    short_portfolio_verdict = _build_short_portfolio_verdict(short_portfolio_oos=short_portfolio_oos)
    short_portfolio_guard = _build_short_portfolio_guard(
        short_trade_verdict=short_trade_verdict,
        short_portfolio_candidates=short_portfolio_candidates,
        short_portfolio_oos=short_portfolio_oos,
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

    _print_stage(progress_label, "writing event/outcome/taxonomy/response-surface/rule/negative-space/basin/OOS/short-mapping/trade-grid/verdict/portfolio/audit artifacts", started_at)
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
    _write_csv(config.output_dir / "pump_mechanism_daily_oos_events.csv", daily_oos_events)
    _write_csv(config.output_dir / "pump_mechanism_oos_diagnostics.csv", oos_diagnostics)
    _write_csv(config.output_dir / "pump_mechanism_oos_top_removal.csv", oos_top_removal)
    _write_csv(config.output_dir / "pump_mechanism_oos_selection_summary.csv", oos_selection_summary)
    _write_csv(config.output_dir / "pump_mechanism_oos_verdict.csv", oos_verdict)
    _write_csv(config.output_dir / "pump_mechanism_short_trade_candidates.csv", short_trade_candidates)
    _write_csv(config.output_dir / "pump_mechanism_short_trade_grid.csv", short_trade_grid)
    _write_csv(config.output_dir / "pump_mechanism_short_trade_diagnostics.csv", short_trade_diagnostics)
    _write_csv(config.output_dir / "pump_mechanism_short_trade_top_removal.csv", short_trade_top_removal)
    _write_csv(config.output_dir / "pump_mechanism_short_trade_verdict.csv", short_trade_verdict)
    _write_csv(config.output_dir / "pump_mechanism_short_trade_guard.csv", short_trade_guard)
    _write_csv(config.output_dir / "pump_mechanism_short_portfolio_candidates.csv", short_portfolio_candidates)
    _write_csv(config.output_dir / "pump_mechanism_short_portfolio_oos.csv", short_portfolio_oos)
    _write_csv(config.output_dir / "pump_mechanism_short_portfolio_verdict.csv", short_portfolio_verdict)
    _write_csv(config.output_dir / "pump_mechanism_short_portfolio_guard.csv", short_portfolio_guard)
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
        daily_oos_events=daily_oos_events,
        oos_diagnostics=oos_diagnostics,
        oos_top_removal=oos_top_removal,
        oos_selection_summary=oos_selection_summary,
        oos_verdict=oos_verdict,
        short_trade_candidates=short_trade_candidates,
        short_trade_grid=short_trade_grid,
        short_trade_diagnostics=short_trade_diagnostics,
        short_trade_top_removal=short_trade_top_removal,
        short_trade_verdict=short_trade_verdict,
        short_trade_guard=short_trade_guard,
        short_portfolio_candidates=short_portfolio_candidates,
        short_portfolio_oos=short_portfolio_oos,
        short_portfolio_verdict=short_portfolio_verdict,
        short_portfolio_guard=short_portfolio_guard,
    )
    _write_csv(config.output_dir / "pump_mechanism_run_config.csv", run_config)
    _write_csv(config.output_dir / "pump_mechanism_artifact_manifest.csv", _artifact_manifest_frame(config=config))

    print(
        f"{progress_label}: artifacts written events={len(events):,} outcomes={len(outcomes):,} "
        f"taxonomy={len(taxonomy):,} response_surfaces={len(response_surfaces):,} "
        f"rule_universe={len(rule_universe):,} negative_space={len(negative_space):,} "
        f"plateau_basins={len(plateau_basins):,} daily_selection={len(daily_selection):,} "
        f"daily_oos={len(daily_oos):,} daily_oos_events={len(daily_oos_events):,} "
        f"oos_diagnostics={len(oos_diagnostics):,} oos_top_removal={len(oos_top_removal):,} "
        f"oos_verdict={len(oos_verdict):,} short_trade_candidates={len(short_trade_candidates):,} "
        f"short_trade_grid={len(short_trade_grid):,} short_trade_verdict={len(short_trade_verdict):,} "
        f"short_portfolio_oos={len(short_portfolio_oos):,} short_portfolio_verdict={len(short_portfolio_verdict):,} "
        f"protocol_audit={len(protocol_audit):,} "
        f"elapsed={_format_duration(time.monotonic() - started_at)} "
        f"output_dir={config.output_dir}",
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




def _build_train_only_rule_universe(
    *,
    taxonomy: pd.DataFrame,
    events: pd.DataFrame,
    progress_label: str | None = None,
) -> pd.DataFrame:
    """Generate candidate mechanism rule specs with train-window-only thresholds.

    The first implementation used repeated DataFrame copies for every
    day/window/scope/threshold combination.  On a 365d broad-pump universe this
    can become dominated by pandas allocation rather than research work.  This
    version keeps the exact train-only contract, but evaluates scopes and
    thresholds with precomputed numpy arrays and row positions.
    """

    columns = _rule_universe_columns()
    rule_input = _rule_input_frame(taxonomy=taxonomy, events=events)
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input = rule_input.loc[pd.to_numeric(rule_input.get("day_ord"), errors="coerce").notna()].copy()
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input["day_ord"] = pd.to_numeric(rule_input["day_ord"], errors="coerce").astype("int64")
    rule_input = _sort_frame(rule_input, ["day_ord", "symbol", "event_id"]).reset_index(drop=True)

    day_values = rule_input["day_ord"].to_numpy(dtype="int64", copy=False)
    unique_test_days = np.asarray(sorted(pd.unique(day_values)), dtype="int64")
    if unique_test_days.size == 0:
        return pd.DataFrame(columns=columns)

    axis_arrays = _normalized_axis_arrays(rule_input)
    numeric_arrays = _numeric_feature_arrays(rule_input)
    symbol_codes = _factor_codes(rule_input.get("symbol", pd.Series("", index=rule_input.index)))
    date_codes = _factor_codes(rule_input.get("date", pd.Series("", index=rule_input.index)))

    rows: list[dict[str, object]] = []
    total_windows = int(len(unique_test_days) * len(ROLLING_WINDOWS_DAYS))
    progress = (
        _EtaProgress(f"{progress_label}: rule universe", total_windows, verb="windows")
        if progress_label and total_windows
        else None
    )
    progress_index = 0

    for test_day_ord in unique_test_days:
        test_day_ord_int = int(test_day_ord)
        test_date = _date_from_day_ord(test_day_ord_int)
        for train_window_days in ROLLING_WINDOWS_DAYS:
            progress_index += 1
            train_start_day_ord = test_day_ord_int - int(train_window_days)
            train_end_day_ord = test_day_ord_int - 1
            train_start_pos = int(np.searchsorted(day_values, train_start_day_ord, side="left"))
            train_end_pos = int(np.searchsorted(day_values, train_end_day_ord, side="right"))
            if train_end_pos <= train_start_pos:
                if progress:
                    progress.update(
                        index=progress_index,
                        item=f"day={test_date} window={train_window_days}d rows={len(rows)} train_events=0",
                    )
                continue
            train_pos = np.arange(train_start_pos, train_end_pos, dtype=np.int64)
            for scope in _train_rule_scopes_from_arrays(train_pos=train_pos, axis_arrays=axis_arrays):
                scope_pos = _apply_scope_to_positions(train_pos=train_pos, axis_arrays=axis_arrays, conditions=scope["conditions"])
                if int(scope_pos.size) < MIN_TRAIN_SCOPE_EVENTS:
                    continue
                train_events, train_symbols, train_active_days = _position_sample_stats(
                    row_pos=scope_pos,
                    symbol_codes=symbol_codes,
                    date_codes=date_codes,
                )
                rows.append(
                    _rule_universe_row_from_stats(
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
                        train_events=train_events,
                        train_symbols=train_symbols,
                        train_active_days=train_active_days,
                        threshold_source_events=0,
                        threshold_valid_values=0,
                        threshold_uses_abs_value=False,
                    )
                )
                for feature, side, quantiles in RULE_THRESHOLD_FEATURES:
                    values = numeric_arrays.get(feature)
                    if values is None:
                        continue
                    source_values = values[scope_pos]
                    finite_mask = np.isfinite(source_values)
                    if not bool(finite_mask.any()):
                        continue
                    fit_values = np.abs(source_values[finite_mask]) if side == "abs_ge" else source_values[finite_mask]
                    if fit_values.size == 0:
                        continue
                    for quantile in quantiles:
                        threshold_value = _safe_quantile_np(fit_values, float(quantile))
                        if not math.isfinite(threshold_value):
                            continue
                        threshold_pos = _threshold_positions(
                            scope_pos=scope_pos,
                            source_values=source_values,
                            finite_mask=finite_mask,
                            side=side,
                            threshold_value=float(threshold_value),
                        )
                        train_events, train_symbols, train_active_days = _position_sample_stats(
                            row_pos=threshold_pos,
                            symbol_codes=symbol_codes,
                            date_codes=date_codes,
                        )
                        rows.append(
                            _rule_universe_row_from_stats(
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
                                train_events=train_events,
                                train_symbols=train_symbols,
                                train_active_days=train_active_days,
                                threshold_source_events=int(scope_pos.size),
                                threshold_valid_values=int(fit_values.size),
                                threshold_uses_abs_value=side == "abs_ge",
                            )
                        )
            if progress:
                progress.update(
                    index=progress_index,
                    item=(
                        f"day={test_date} window={train_window_days}d rows={len(rows)} "
                        f"train_events={train_end_pos - train_start_pos}"
                    ),
                )
    if progress:
        progress.finish()
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "scope_name", "threshold_feature", "threshold_quantile", "rule_id"])



def _normalized_axis_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    for axis in set(RULE_SCOPE_AXES) | {axis for pair in RULE_PAIR_SCOPES for axis in pair}:
        if axis in frame.columns:
            arrays[axis] = frame[axis].fillna("missing").astype(str).to_numpy(dtype=object, copy=False)
    return arrays


def _numeric_feature_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    for feature, _side, _quantiles in RULE_THRESHOLD_FEATURES:
        if feature in frame.columns:
            arrays[feature] = pd.to_numeric(frame[feature], errors="coerce").to_numpy(dtype="float64", copy=False)
    return arrays


def _factor_codes(values: pd.Series) -> np.ndarray:
    series = values.fillna("missing").astype(str)
    codes, _uniques = pd.factorize(series, sort=False)
    return codes.astype("int64", copy=False)


def _train_rule_scopes_from_arrays(*, train_pos: np.ndarray, axis_arrays: dict[str, np.ndarray]) -> list[dict[str, object]]:
    scopes: list[dict[str, object]] = [
        {"scope_name": "all", "conditions": {}, "scope_depth": 0},
    ]
    for axis in RULE_SCOPE_AXES:
        values = axis_arrays.get(axis)
        if values is None:
            continue
        counts = pd.Series(values[train_pos], copy=False).value_counts(dropna=False)
        for value, count in counts.items():
            if int(count) >= MIN_TRAIN_SCOPE_EVENTS:
                scopes.append({"scope_name": axis, "conditions": {axis: str(value)}, "scope_depth": 1})
    for left, right in RULE_PAIR_SCOPES:
        left_values = axis_arrays.get(left)
        right_values = axis_arrays.get(right)
        if left_values is None or right_values is None:
            continue
        pair_frame = pd.DataFrame({left: left_values[train_pos], right: right_values[train_pos]})
        counts = pair_frame.value_counts(sort=False, dropna=False)
        for pair_values, count in counts.items():
            if int(count) < MIN_TRAIN_SCOPE_EVENTS:
                continue
            if not isinstance(pair_values, tuple):
                continue
            left_value, right_value = pair_values
            scopes.append(
                {
                    "scope_name": f"{left}+{right}",
                    "conditions": {left: str(left_value), right: str(right_value)},
                    "scope_depth": 2,
                }
            )
    unique: dict[str, dict[str, object]] = {}
    for scope in scopes:
        key = f"{scope['scope_name']}|{_scope_key(scope['conditions'])}"
        unique.setdefault(key, scope)
    return list(unique.values())


def _apply_scope_to_positions(
    *,
    train_pos: np.ndarray,
    axis_arrays: dict[str, np.ndarray],
    conditions: object,
) -> np.ndarray:
    if not isinstance(conditions, dict) or not conditions:
        return train_pos
    mask = np.ones(train_pos.size, dtype=bool)
    for axis, expected in conditions.items():
        values = axis_arrays.get(str(axis))
        if values is None:
            return train_pos[:0]
        mask &= values[train_pos] == str(expected)
        if not bool(mask.any()):
            return train_pos[:0]
    return train_pos[mask]


def _position_sample_stats(
    *,
    row_pos: np.ndarray,
    symbol_codes: np.ndarray,
    date_codes: np.ndarray,
) -> tuple[int, int, int]:
    if row_pos.size == 0:
        return 0, 0, 0
    train_events = int(row_pos.size)
    train_symbols = int(np.unique(symbol_codes[row_pos]).size)
    train_active_days = int(np.unique(date_codes[row_pos]).size)
    return train_events, train_symbols, train_active_days


def _safe_quantile_np(values: np.ndarray, quantile: float) -> float:
    if values.size == 0:
        return float("nan")
    try:
        return float(np.quantile(values, float(quantile)))
    except (TypeError, ValueError, FloatingPointError):
        return float("nan")


def _threshold_positions(
    *,
    scope_pos: np.ndarray,
    source_values: np.ndarray,
    finite_mask: np.ndarray,
    side: str,
    threshold_value: float,
) -> np.ndarray:
    if side == "le":
        pass_mask = finite_mask & (source_values <= float(threshold_value))
    elif side == "ge":
        pass_mask = finite_mask & (source_values >= float(threshold_value))
    elif side == "abs_ge":
        pass_mask = finite_mask & (np.abs(source_values) >= float(threshold_value))
    else:
        raise ValueError(f"unsupported threshold side: {side}")
    return scope_pos[pass_mask]


def _rule_universe_row_from_stats(
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
    train_events: int,
    train_symbols: int,
    train_active_days: int,
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
        "train_events": int(train_events),
        "train_symbols": int(train_symbols),
        "train_active_days": int(train_active_days),
        "rule_passes_min_sample": bool(
            int(train_events) >= MIN_RULE_EVENTS
            and int(train_symbols) >= MIN_RULE_SYMBOLS
            and int(train_active_days) >= MIN_RULE_ACTIVE_DAYS
        ),
        "min_rule_events": int(MIN_RULE_EVENTS),
        "min_rule_symbols": int(MIN_RULE_SYMBOLS),
        "min_rule_active_days": int(MIN_RULE_ACTIVE_DAYS),
        "rule_generation_model": "train_only_entry_known_mechanism_rule_grammar_v2_vectorized",
        "threshold_fit_model": "quantiles_fit_inside_train_window_only",
        "train_uses_only_days_before_test": True,
        "uses_outcome_columns": False,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }

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
    progress_label: str | None = None,
) -> pd.DataFrame:
    """Generate full train-only neighbor space around each mechanism rule.

    The original negative-space pass re-sliced and copied the same train
    dataframe for every center rule and every neighbor.  That is the next
    large bottleneck after rule-universe generation.  This implementation keeps
    the same research contract, but evaluates scopes, threshold perturbations,
    sample counts, and failed neighbors with precomputed row-position arrays.
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
    rule_input = _sort_frame(rule_input, ["day_ord", "symbol", "event_id"]).reset_index(drop=True)

    pass_mask = rule_universe.get("rule_passes_min_sample", pd.Series(False, index=rule_universe.index)).map(_to_bool)
    center_rules = rule_universe.loc[pass_mask].copy()
    if center_rules.empty:
        return pd.DataFrame(columns=columns)

    day_values = rule_input["day_ord"].to_numpy(dtype="int64", copy=False)
    axis_arrays = _normalized_axis_arrays(rule_input)
    numeric_arrays = _numeric_feature_arrays(rule_input)
    symbol_codes = _factor_codes(rule_input.get("symbol", pd.Series("", index=rule_input.index)))
    date_codes = _factor_codes(rule_input.get("date", pd.Series("", index=rule_input.index)))

    rows: list[dict[str, object]] = []
    center_records = center_rules.to_dict("records")
    progress = (
        _EtaProgress(f"{progress_label}: negative space", len(center_records), verb="centers")
        if progress_label and center_records
        else None
    )
    processed = 0

    grouped: dict[tuple[int, int, int, int], list[dict[str, object]]] = {}
    for center in center_records:
        test_day_ord = _finite_int_or_none(center.get("test_day_ord"))
        train_start_day_ord = _finite_int_or_none(center.get("train_start_day_ord"))
        train_end_day_ord = _finite_int_or_none(center.get("train_end_day_ord"))
        train_window_days = _finite_int_or_none(center.get("train_window_days"))
        if test_day_ord is None or train_start_day_ord is None or train_end_day_ord is None or train_window_days is None:
            continue
        grouped.setdefault(
            (int(test_day_ord), int(train_window_days), int(train_start_day_ord), int(train_end_day_ord)), []
        ).append(center)

    for (test_day_ord, train_window_days, train_start_day_ord, train_end_day_ord), centers in sorted(grouped.items()):
        train_start_pos = int(np.searchsorted(day_values, train_start_day_ord, side="left"))
        train_end_pos = int(np.searchsorted(day_values, train_end_day_ord, side="right"))
        if train_end_pos <= train_start_pos:
            processed += len(centers)
            if progress:
                progress.update(
                    index=processed,
                    item=f"day={_date_from_day_ord(test_day_ord)} window={train_window_days}d rows={len(rows)} train_events=0",
                )
            continue
        train_pos = np.arange(train_start_pos, train_end_pos, dtype=np.int64)
        eligible_values_by_axis = {
            axis: _eligible_scope_values_from_arrays(train_pos=train_pos, axis_arrays=axis_arrays, axis=axis)
            for axis in RULE_SCOPE_AXES
        }
        for center in centers:
            basin_id = _basin_id_for_center(center)
            for neighbor in _iter_negative_space_neighbor_specs_from_values(
                center=center,
                eligible_values_by_axis=eligible_values_by_axis,
            ):
                rows.append(
                    _negative_space_row_from_positions(
                        center=center,
                        neighbor=neighbor,
                        train_pos=train_pos,
                        axis_arrays=axis_arrays,
                        numeric_arrays=numeric_arrays,
                        symbol_codes=symbol_codes,
                        date_codes=date_codes,
                        basin_id=basin_id,
                        test_day_ord=int(test_day_ord),
                        train_window_days=int(train_window_days),
                        train_start_day_ord=int(train_start_day_ord),
                        train_end_day_ord=int(train_end_day_ord),
                    )
                )
            processed += 1
            if progress and (processed == len(center_records) or processed % 500 == 0):
                progress.update(
                    index=processed,
                    item=(
                        f"day={_date_from_day_ord(test_day_ord)} window={train_window_days}d "
                        f"rows={len(rows)} centers={processed}/{len(center_records)}"
                    ),
                )
    if progress:
        progress.finish()
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "basin_id", "neighbor_distance", "neighbor_kind", "neighbor_rule_id"])


def _iter_negative_space_neighbor_specs(*, center: dict[str, object], train: pd.DataFrame) -> list[dict[str, object]]:
    # Compatibility wrapper for tests or older call sites.  Production code uses
    # _iter_negative_space_neighbor_specs_from_values() so eligible axis values
    # are counted once per train window instead of once per center rule.
    eligible_values_by_axis = {axis: _eligible_scope_values(train, axis) for axis in RULE_SCOPE_AXES}
    return _iter_negative_space_neighbor_specs_from_values(center=center, eligible_values_by_axis=eligible_values_by_axis)


def _iter_negative_space_neighbor_specs_from_values(
    *,
    center: dict[str, object],
    eligible_values_by_axis: dict[str, list[str]],
) -> list[dict[str, object]]:
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
        for value in eligible_values_by_axis.get(axis, []):
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
            for value in eligible_values_by_axis.get(axis, []):
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
    # Compatibility wrapper for tests or older call sites.  Production code uses
    # _negative_space_row_from_positions() to avoid pandas copies per neighbor.
    train = train.reset_index(drop=True)
    axis_arrays = _normalized_axis_arrays(train)
    numeric_arrays = _numeric_feature_arrays(train)
    symbol_codes = _factor_codes(train.get("symbol", pd.Series("", index=train.index)))
    date_codes = _factor_codes(train.get("date", pd.Series("", index=train.index)))
    train_pos = np.arange(len(train), dtype=np.int64)
    return _negative_space_row_from_positions(
        center=center,
        neighbor=neighbor,
        train_pos=train_pos,
        axis_arrays=axis_arrays,
        numeric_arrays=numeric_arrays,
        symbol_codes=symbol_codes,
        date_codes=date_codes,
        basin_id=basin_id,
        test_day_ord=test_day_ord,
        train_window_days=train_window_days,
        train_start_day_ord=train_start_day_ord,
        train_end_day_ord=train_end_day_ord,
    )


def _negative_space_row_from_positions(
    *,
    center: dict[str, object],
    neighbor: dict[str, object],
    train_pos: np.ndarray,
    axis_arrays: dict[str, np.ndarray],
    numeric_arrays: dict[str, np.ndarray],
    symbol_codes: np.ndarray,
    date_codes: np.ndarray,
    basin_id: str,
    test_day_ord: int,
    train_window_days: int,
    train_start_day_ord: int,
    train_end_day_ord: int,
) -> dict[str, object]:
    conditions = neighbor.get("conditions") if isinstance(neighbor.get("conditions"), dict) else {}
    assert isinstance(conditions, dict)
    scoped_pos = _apply_scope_to_positions(train_pos=train_pos, axis_arrays=axis_arrays, conditions=conditions)
    threshold_feature = str(neighbor.get("threshold_feature") or "")
    threshold_side = str(neighbor.get("threshold_side") or "none")
    threshold_quantile = _float(neighbor.get("threshold_quantile"))
    threshold_uses_abs_value = threshold_side == "abs_ge"
    threshold_value = float("nan")
    threshold_valid_values = 0
    if threshold_feature and threshold_side != "none":
        values = numeric_arrays.get(threshold_feature)
        if values is not None and scoped_pos.size:
            source_values = values[scoped_pos]
            finite_mask = np.isfinite(source_values)
            fit_values = np.abs(source_values[finite_mask]) if threshold_uses_abs_value else source_values[finite_mask]
            threshold_valid_values = int(fit_values.size)
            if math.isfinite(threshold_quantile) and threshold_valid_values > 0:
                threshold_value = _safe_quantile_np(fit_values, float(threshold_quantile))
            filtered_pos = (
                _threshold_positions(
                    scope_pos=scoped_pos,
                    source_values=source_values,
                    finite_mask=finite_mask,
                    side=threshold_side,
                    threshold_value=float(threshold_value),
                )
                if math.isfinite(threshold_value)
                else scoped_pos[:0]
            )
        else:
            filtered_pos = scoped_pos[:0]
    else:
        filtered_pos = scoped_pos

    neighbor_events, neighbor_symbols, neighbor_active_days = _position_sample_stats(
        row_pos=filtered_pos,
        symbol_codes=symbol_codes,
        date_codes=date_codes,
    )
    passes_min_sample = bool(
        neighbor_events >= MIN_RULE_EVENTS
        and neighbor_symbols >= MIN_RULE_SYMBOLS
        and neighbor_active_days >= MIN_RULE_ACTIVE_DAYS
    )
    fail_reason = _negative_space_fail_reason(
        scoped_events=int(scoped_pos.size),
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
        "neighbor_threshold_source_events": int(scoped_pos.size),
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


def _eligible_scope_values_from_arrays(
    *,
    train_pos: np.ndarray,
    axis_arrays: dict[str, np.ndarray],
    axis: str,
) -> list[str]:
    values = axis_arrays.get(axis)
    if values is None or train_pos.size == 0:
        return []
    counts = pd.Series(values[train_pos], copy=False).value_counts(dropna=False)
    eligible = [str(value) for value, count in counts.items() if int(count) >= MIN_TRAIN_SCOPE_EVENTS]
    return sorted(eligible[:MAX_SCOPE_REPLACEMENT_VALUES_PER_AXIS])


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
    progress_label: str | None = None,
) -> pd.DataFrame:
    """Score train-only plateau basins from the full generated neighbor space.

    The expensive part is applying each neighbor rule back to its train window.
    The first implementation did that with pandas dataframe copies for every
    basin/neighbor.  This version precomputes entry-known arrays and outcome
    arrays once, then applies scopes/thresholds through row positions.  Outcome
    columns still enter only in this response-scoring phase, never in rule masks
    or train-only selection.
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
    if not available_outcome_columns:
        return pd.DataFrame(columns=columns)
    joined_outcomes = outcomes[available_outcome_columns].drop_duplicates("event_id", keep="last")
    rule_input = rule_input.merge(joined_outcomes, on="event_id", how="left", suffixes=("", "_outcome"))
    rule_input = rule_input.loc[pd.to_numeric(rule_input.get("day_ord"), errors="coerce").notna()].copy()
    if rule_input.empty:
        return pd.DataFrame(columns=columns)
    rule_input["day_ord"] = pd.to_numeric(rule_input["day_ord"], errors="coerce").astype("int64")
    rule_input = _sort_frame(rule_input, ["day_ord", "symbol", "event_id"]).reset_index(drop=True)

    day_values = rule_input["day_ord"].to_numpy(dtype="int64", copy=False)
    axis_arrays = _normalized_axis_arrays(rule_input)
    numeric_arrays = _numeric_feature_arrays(rule_input)
    symbol_codes = _factor_codes(rule_input.get("symbol", pd.Series("", index=rule_input.index)))
    date_codes = _factor_codes(rule_input.get("date", pd.Series("", index=rule_input.index)))
    outcome_arrays = _plateau_outcome_arrays(rule_input)

    rows: list[dict[str, object]] = []
    grouped = list(negative_space.groupby("basin_id", dropna=False, sort=True))
    progress = (
        _EtaProgress(f"{progress_label}: plateau basins", len(grouped), verb="basins")
        if progress_label and grouped
        else None
    )
    for index, (basin_id, basin_neighbors) in enumerate(grouped, start=1):
        first = basin_neighbors.iloc[0]
        train_start_day_ord = _finite_int_or_none(first.get("train_start_day_ord"))
        train_end_day_ord = _finite_int_or_none(first.get("train_end_day_ord"))
        test_day_ord = _finite_int_or_none(first.get("test_day_ord"))
        train_window_days = _finite_int_or_none(first.get("train_window_days"))
        if train_start_day_ord is None or train_end_day_ord is None or test_day_ord is None or train_window_days is None:
            continue
        train_start_pos = int(np.searchsorted(day_values, int(train_start_day_ord), side="left"))
        train_end_pos = int(np.searchsorted(day_values, int(train_end_day_ord), side="right"))
        if train_end_pos <= train_start_pos:
            continue
        train_pos = np.arange(train_start_pos, train_end_pos, dtype=np.int64)
        scored_neighbors = [
            _score_negative_space_neighbor_positions(
                row=neighbor,
                train_pos=train_pos,
                axis_arrays=axis_arrays,
                numeric_arrays=numeric_arrays,
                symbol_codes=symbol_codes,
                date_codes=date_codes,
                outcome_arrays=outcome_arrays,
            )
            for neighbor in basin_neighbors.to_dict("records")
        ]
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
        if progress and (index == len(grouped) or index % 500 == 0):
            progress.update(
                index=index,
                item=(
                    f"day={_date_from_day_ord(int(test_day_ord))} window={int(train_window_days)}d "
                    f"rows={len(rows)} basins={index}/{len(grouped)}"
                ),
            )
    if progress:
        progress.finish()
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "basin_status", "basin_score", "basin_id"])


def _score_negative_space_neighbor(*, row: dict[str, object], train: pd.DataFrame) -> dict[str, object]:
    # Compatibility wrapper for tests or older call sites.  Production plateau
    # scoring uses _score_negative_space_neighbor_positions().
    train = train.reset_index(drop=True)
    axis_arrays = _normalized_axis_arrays(train)
    numeric_arrays = _numeric_feature_arrays(train)
    symbol_codes = _factor_codes(train.get("symbol", pd.Series("", index=train.index)))
    date_codes = _factor_codes(train.get("date", pd.Series("", index=train.index)))
    outcome_arrays = _plateau_outcome_arrays(train)
    train_pos = np.arange(len(train), dtype=np.int64)
    return _score_negative_space_neighbor_positions(
        row=row,
        train_pos=train_pos,
        axis_arrays=axis_arrays,
        numeric_arrays=numeric_arrays,
        symbol_codes=symbol_codes,
        date_codes=date_codes,
        outcome_arrays=outcome_arrays,
    )


def _score_negative_space_neighbor_positions(
    *,
    row: dict[str, object],
    train_pos: np.ndarray,
    axis_arrays: dict[str, np.ndarray],
    numeric_arrays: dict[str, np.ndarray],
    symbol_codes: np.ndarray,
    date_codes: np.ndarray,
    outcome_arrays: dict[str, np.ndarray],
) -> dict[str, object]:
    conditions = _parse_scope_conditions(row.get("neighbor_scope_conditions"))
    scoped_pos = _apply_scope_to_positions(train_pos=train_pos, axis_arrays=axis_arrays, conditions=conditions)
    threshold_feature = str(row.get("neighbor_threshold_feature") or "")
    threshold_side = str(row.get("neighbor_threshold_side") or "none")
    threshold_value = _float(row.get("neighbor_threshold_value"))
    if threshold_feature and threshold_side != "none" and math.isfinite(threshold_value):
        values = numeric_arrays.get(threshold_feature)
        if values is None or scoped_pos.size == 0:
            filtered_pos = scoped_pos[:0]
        else:
            source_values = values[scoped_pos]
            finite_mask = np.isfinite(source_values)
            filtered_pos = _threshold_positions(
                scope_pos=scoped_pos,
                source_values=source_values,
                finite_mask=finite_mask,
                side=threshold_side,
                threshold_value=threshold_value,
            )
    elif threshold_feature and threshold_side != "none":
        filtered_pos = scoped_pos[:0]
    else:
        filtered_pos = scoped_pos
    metrics = _plateau_response_metrics_from_positions(
        row_pos=filtered_pos,
        symbol_codes=symbol_codes,
        date_codes=date_codes,
        outcome_arrays=outcome_arrays,
    )
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


def _plateau_outcome_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    numeric_columns = (
        "future_ret_30m", "future_ret_60m", "future_min_ret_30m", "future_min_ret_60m",
        "future_max_ret_30m", "future_max_ret_60m", "down_mfe_30m", "down_mfe_60m",
        "up_mae_30m", "up_mae_60m", "time_to_reclaim_pump_high_minutes",
        "time_to_structural_low_break_minutes",
    )
    for column in numeric_columns:
        if column in frame.columns:
            arrays[column] = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64", copy=False)
        else:
            arrays[column] = np.full(len(frame), np.nan, dtype="float64")
    bool_columns = ("reclaimed_pump_high_60m", "broke_structural_low_60m")
    for column in bool_columns:
        if column in frame.columns:
            arrays[column] = frame[column].map(_to_bool).to_numpy(dtype=bool, copy=False)
        else:
            arrays[column] = np.zeros(len(frame), dtype=bool)
    return arrays


def _plateau_response_metrics(frame: pd.DataFrame) -> dict[str, object]:
    # Compatibility wrapper.  Hot paths use _plateau_response_metrics_from_positions().
    frame = frame.reset_index(drop=True)
    symbol_codes = _factor_codes(frame.get("symbol", pd.Series("", index=frame.index)))
    date_codes = _factor_codes(frame.get("date", pd.Series("", index=frame.index)))
    outcome_arrays = _plateau_outcome_arrays(frame)
    row_pos = np.arange(len(frame), dtype=np.int64)
    return _plateau_response_metrics_from_positions(
        row_pos=row_pos,
        symbol_codes=symbol_codes,
        date_codes=date_codes,
        outcome_arrays=outcome_arrays,
    )


def _plateau_response_metrics_from_positions(
    *,
    row_pos: np.ndarray,
    symbol_codes: np.ndarray,
    date_codes: np.ndarray,
    outcome_arrays: dict[str, np.ndarray],
) -> dict[str, object]:
    events = int(row_pos.size)
    symbols = int(np.unique(symbol_codes[row_pos]).size) if events else 0
    active_days = int(np.unique(date_codes[row_pos]).size) if events else 0
    dependency = _dependency_metrics_from_codes(row_pos=row_pos, symbol_codes=symbol_codes, date_codes=date_codes)
    future_min_30 = _array_take(outcome_arrays, "future_min_ret_30m", row_pos)
    future_min_60 = _array_take(outcome_arrays, "future_min_ret_60m", row_pos)
    reclaimed = _array_take_bool(outcome_arrays, "reclaimed_pump_high_60m", row_pos)
    broke = _array_take_bool(outcome_arrays, "broke_structural_low_60m", row_pos)
    return {
        "events": events,
        "symbols": symbols,
        "active_days": active_days,
        "median_future_ret_30m": _array_median(_array_take(outcome_arrays, "future_ret_30m", row_pos)),
        "median_future_ret_60m": _array_median(_array_take(outcome_arrays, "future_ret_60m", row_pos)),
        "median_future_min_ret_30m": _array_median(future_min_30),
        "median_future_min_ret_60m": _array_median(future_min_60),
        "median_future_max_ret_30m": _array_median(_array_take(outcome_arrays, "future_max_ret_30m", row_pos)),
        "median_future_max_ret_60m": _array_median(_array_take(outcome_arrays, "future_max_ret_60m", row_pos)),
        "median_down_mfe_30m": _array_median(_array_take(outcome_arrays, "down_mfe_30m", row_pos)),
        "median_down_mfe_60m": _array_median(_array_take(outcome_arrays, "down_mfe_60m", row_pos)),
        "median_up_mae_30m": _array_median(_array_take(outcome_arrays, "up_mae_30m", row_pos)),
        "median_up_mae_60m": _array_median(_array_take(outcome_arrays, "up_mae_60m", row_pos)),
        "downside_hit_rate_30m": _array_rate(future_min_30 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD),
        "downside_hit_rate_60m": _array_rate(future_min_60 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD),
        "reclaim_rate_60m": _array_rate(reclaimed),
        "structural_low_break_rate_60m": _array_rate(broke),
        "median_time_to_reclaim_pump_high_minutes": _array_median(_array_take(outcome_arrays, "time_to_reclaim_pump_high_minutes", row_pos)),
        "median_time_to_structural_low_break_minutes": _array_median(_array_take(outcome_arrays, "time_to_structural_low_break_minutes", row_pos)),
        **dependency,
    }


def _array_take(arrays: dict[str, np.ndarray], key: str, row_pos: np.ndarray) -> np.ndarray:
    values = arrays.get(key)
    if values is None or row_pos.size == 0:
        return np.asarray([], dtype="float64")
    return np.asarray(values[row_pos], dtype="float64")


def _array_take_bool(arrays: dict[str, np.ndarray], key: str, row_pos: np.ndarray) -> np.ndarray:
    values = arrays.get(key)
    if values is None or row_pos.size == 0:
        return np.asarray([], dtype=bool)
    return np.asarray(values[row_pos], dtype=bool)


def _array_median(values: np.ndarray) -> float:
    if values.size == 0:
        return float("nan")
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return float("nan")
    return float(np.median(finite))


def _array_rate(mask: np.ndarray) -> float:
    if mask.size == 0:
        return float("nan")
    return float(np.mean(mask.astype(float)))


def _dependency_metrics_from_codes(
    *,
    row_pos: np.ndarray,
    symbol_codes: np.ndarray,
    date_codes: np.ndarray,
) -> dict[str, float]:
    event_count = max(int(row_pos.size), 1)
    largest_symbol_share = 0.0
    largest_day_share = 0.0
    if row_pos.size:
        symbol_counts = np.bincount(symbol_codes[row_pos][symbol_codes[row_pos] >= 0])
        date_counts = np.bincount(date_codes[row_pos][date_codes[row_pos] >= 0])
        if symbol_counts.size:
            largest_symbol_share = float(symbol_counts.max()) / event_count
        if date_counts.size:
            largest_day_share = float(date_counts.max()) / event_count
    return {
        "top_event_dependency_pct": 1.0 / event_count,
        "top_symbol_dependency_pct": largest_symbol_share,
        "largest_symbol_event_share": largest_symbol_share,
        "largest_day_event_share": largest_day_share,
    }


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
    progress_label: str | None = None,
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
    selection_records = daily_selection.to_dict("records")
    progress = (
        _EtaProgress(f"{progress_label}: daily OOS", len(selection_records), verb="selected basins")
        if progress_label and selection_records
        else None
    )
    for index, selection_row in enumerate(selection_records, start=1):
        center_rule_id = str(selection_row.get("center_rule_id", ""))
        if rule_lookup.empty or center_rule_id not in rule_lookup.index:
            rows.append(_daily_oos_missing_rule_row(selection_row, reason="center_rule_missing_from_rule_universe"))
        else:
            rule = rule_lookup.loc[center_rule_id]
            if isinstance(rule, pd.DataFrame):
                rule = rule.iloc[-1]
            rows.append(_evaluate_selected_basin_on_test_day(selection=selection_row, rule=rule.to_dict(), oos_input=oos_input))
        if progress and (index == len(selection_records) or index % 500 == 0):
            progress.update(
                index=index,
                item=f"day={selection_row.get('test_date', '')} rows={len(rows)}",
            )
    if progress:
        progress.finish()

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



def _build_daily_oos_event_ledger(
    *,
    daily_selection: pd.DataFrame,
    rule_universe: pd.DataFrame,
    taxonomy: pd.DataFrame,
    events: pd.DataFrame,
    outcomes: pd.DataFrame,
) -> pd.DataFrame:
    """Create event-level rows for selected daily OOS basins.

    This ledger is intentionally downstream of daily selection.  It reconstructs
    the selected center rule on the held-out test day and writes every matching
    event so OOS stability can be stressed by symbol, event, calendar bucket,
    and selection key.  Selection still comes only from train-scored basins; the
    outcome fields here are diagnostic labels for the already-selected OOS rows.
    """

    columns = _daily_oos_event_columns()
    if daily_selection.empty or rule_universe.empty:
        return pd.DataFrame(columns=columns)
    rule_lookup = rule_universe.drop_duplicates("rule_id", keep="last").set_index("rule_id", drop=False) if "rule_id" in rule_universe.columns else pd.DataFrame()
    if rule_lookup.empty:
        return pd.DataFrame(columns=columns)
    oos_input = _joined_rule_outcome_input(taxonomy=taxonomy, events=events, outcomes=outcomes)
    if oos_input.empty or "day_ord" not in oos_input.columns:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for selection in daily_selection.to_dict("records"):
        center_rule_id = str(selection.get("center_rule_id", ""))
        if center_rule_id not in rule_lookup.index:
            continue
        rule = rule_lookup.loc[center_rule_id]
        if isinstance(rule, pd.DataFrame):
            rule = rule.iloc[-1]
        filtered = _selected_oos_events(selection=selection, rule=rule.to_dict(), oos_input=oos_input)
        if filtered.empty:
            continue
        for event_rank, event in enumerate(filtered.to_dict("records"), start=1):
            rows.append(_daily_oos_event_row(selection=selection, event=event, event_rank=event_rank))
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "selected_rank", "event_rank", "event_id"])


def _selected_oos_events(*, selection: dict[str, object], rule: dict[str, object], oos_input: pd.DataFrame) -> pd.DataFrame:
    test_day_ord = _finite_int_or_none(selection.get("test_day_ord"))
    if test_day_ord is None or oos_input.empty or "day_ord" not in oos_input.columns:
        return pd.DataFrame()
    test = oos_input.loc[oos_input["day_ord"] == int(test_day_ord)].copy()
    if test.empty:
        return pd.DataFrame()
    filtered = _apply_rule_scope(test, _parse_scope_conditions(rule.get("scope_conditions")))
    threshold_feature = str(rule.get("threshold_feature") or "")
    threshold_side = str(rule.get("threshold_side") or "none")
    threshold_value = _float(rule.get("threshold_value"))
    if threshold_feature and threshold_side != "none" and math.isfinite(threshold_value):
        filtered = _apply_threshold(filtered, feature=threshold_feature, side=threshold_side, threshold_value=threshold_value)
    elif threshold_feature and threshold_side != "none":
        filtered = filtered.iloc[0:0].copy()
    if filtered.empty:
        return pd.DataFrame()
    return filtered.sort_values([column for column in ["symbol", "event_id"] if column in filtered.columns]).copy()


def _daily_oos_event_row(*, selection: dict[str, object], event: dict[str, object], event_rank: int) -> dict[str, object]:
    test_day_ord = int(selection.get("test_day_ord", 0) or 0)
    score = _event_response_score(event)
    future_min_30 = _float(event.get("future_min_ret_30m"))
    future_min_60 = _float(event.get("future_min_ret_60m"))
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
        "event_rank": int(event_rank),
        "event_id": str(event.get("event_id", "")),
        "symbol": str(event.get("symbol", "")),
        "session_bucket": str(event.get("session_bucket", "")),
        "mechanism_family": str(event.get("mechanism_family", "")),
        "mechanism_id": str(event.get("mechanism_id", "")),
        "acceptance_regime": str(event.get("acceptance_regime", "")),
        "oi_regime": str(event.get("oi_regime", "")),
        "flow_regime": str(event.get("flow_regime", "")),
        "price_progress_regime": str(event.get("price_progress_regime", "")),
        "structure_regime": str(event.get("structure_regime", "")),
        "late_buyer_regime": str(event.get("late_buyer_regime", "")),
        "future_ret_30m": _float(event.get("future_ret_30m")),
        "future_ret_60m": _float(event.get("future_ret_60m")),
        "future_min_ret_30m": future_min_30,
        "future_min_ret_60m": future_min_60,
        "future_max_ret_30m": _float(event.get("future_max_ret_30m")),
        "future_max_ret_60m": _float(event.get("future_max_ret_60m")),
        "down_mfe_30m": _float(event.get("down_mfe_30m")),
        "down_mfe_60m": _float(event.get("down_mfe_60m")),
        "up_mae_30m": _float(event.get("up_mae_30m")),
        "up_mae_60m": _float(event.get("up_mae_60m")),
        "downside_hit_30m": bool(math.isfinite(future_min_30) and future_min_30 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD),
        "downside_hit_60m": bool(math.isfinite(future_min_60) and future_min_60 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD),
        "reclaimed_pump_high_60m": _to_bool(event.get("reclaimed_pump_high_60m")),
        "broke_structural_low_60m": _to_bool(event.get("broke_structural_low_60m")),
        "event_response_score": score,
        "event_expected_downside": bool(math.isfinite(score) and score > 0.0),
        "daily_oos_event_ledger_model": DAILY_OOS_EVENT_LEDGER_MODEL,
        "selection_uses_test_day_outcomes": False,
        "evaluation_uses_test_day_outcomes": True,
        "uses_pnl": False,
        "uses_short_entry": False,
        "uses_final_holdout_tuning": False,
        "future_label_available_at_entry": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _event_response_score(row: dict[str, object]) -> float:
    future_min_30 = _float(row.get("future_min_ret_30m"))
    future_min_60 = _float(row.get("future_min_ret_60m"))
    broke = 1.0 if _to_bool(row.get("broke_structural_low_60m")) else 0.0
    reclaimed = 1.0 if _to_bool(row.get("reclaimed_pump_high_60m")) else 0.0
    downside_30 = 1.0 if math.isfinite(future_min_30) and future_min_30 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD else 0.0
    downside_60 = 1.0 if math.isfinite(future_min_60) and future_min_60 <= PLATEAU_EXPECTED_DOWNSIDE_RET_THRESHOLD else 0.0
    components = [
        -_float(row.get("future_ret_30m")),
        -_float(row.get("future_ret_60m")),
        -future_min_30,
        -future_min_60,
        0.02 * (downside_30 - 0.50),
        0.02 * (downside_60 - 0.50),
        0.02 * (broke - 0.30),
        0.02 * (0.50 - reclaimed),
    ]
    finite = [float(value) for value in components if math.isfinite(float(value))]
    return float(np.mean(finite)) if finite else float("nan")


def _build_oos_diagnostics(*, daily_oos: pd.DataFrame, daily_oos_events: pd.DataFrame) -> pd.DataFrame:
    columns = _oos_diagnostics_columns()
    rows: list[dict[str, object]] = []
    if not daily_oos.empty:
        work = daily_oos.copy()
        work["month"] = work["test_date"].astype(str).str.slice(0, 7) if "test_date" in work.columns else ""
        day_ord = pd.to_numeric(work.get("test_day_ord", pd.Series(dtype=float)), errors="coerce")
        work["odd_even_day"] = np.where((day_ord.fillna(0).astype("int64") % 2) == 0, "even", "odd")
        finite_day_ord = day_ord.dropna()
        midpoint = float(finite_day_ord.median()) if not finite_day_ord.empty else float("nan")
        work["year_half"] = np.where(day_ord <= midpoint, "first_half", "second_half") if math.isfinite(midpoint) else "unknown"
        for axis in ["all", "train_window_days", "basin_status_at_selection", "month", "odd_even_day", "year_half"]:
            rows.extend(_oos_daily_split_rows(work, axis=axis))
    if not daily_oos_events.empty:
        event_work = daily_oos_events.copy()
        event_work["month"] = event_work["test_date"].astype(str).str.slice(0, 7) if "test_date" in event_work.columns else ""
        for axis in ["session_bucket", "mechanism_family", "acceptance_regime", "oi_regime", "flow_regime", "structure_regime", "symbol", "month"]:
            rows.extend(_oos_event_split_rows(event_work, axis=axis))
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["diagnostic_source", "split_axis", "split_value"])


def _oos_daily_split_rows(frame: pd.DataFrame, *, axis: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if frame.empty:
        return rows
    if axis == "all":
        groups = [("all", frame)]
    elif axis not in frame.columns:
        return rows
    else:
        groups = [(str(value), group) for value, group in frame.groupby(axis, dropna=False, sort=True)]
    for value, group in groups:
        score = pd.to_numeric(group.get("oos_response_score", pd.Series(dtype=float)), errors="coerce")
        events = pd.to_numeric(group.get("oos_events", pd.Series(dtype=float)), errors="coerce").fillna(0)
        active = events > 0
        expected = group.get("oos_expected_downside", pd.Series(dtype=bool)).map(_to_bool)
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "diagnostic_source": "daily_oos_rows",
                "split_axis": axis,
                "split_value": value,
                "rows": int(len(group)),
                "active_rows": int(active.sum()),
                "unique_test_days": int(group["test_day_ord"].nunique()) if "test_day_ord" in group.columns else 0,
                "unique_selection_keys": int(group["selection_key"].nunique()) if "selection_key" in group.columns else 0,
                "unique_symbols": np.nan,
                "total_events": int(events.sum()),
                "median_response_score": _safe_median(score),
                "avg_response_score": _safe_mean(score),
                "positive_score_rate": _rate(score > 0.0),
                "positive_active_score_rate": _rate(score.loc[active] > 0.0) if bool(active.any()) else float("nan"),
                "expected_downside_rate": _rate(expected),
                "median_future_ret_30m": _safe_median(group.get("oos_median_future_ret_30m", pd.Series(dtype=float))),
                "median_future_ret_60m": _safe_median(group.get("oos_median_future_ret_60m", pd.Series(dtype=float))),
                "median_future_min_ret_30m": _safe_median(group.get("oos_median_future_min_ret_30m", pd.Series(dtype=float))),
                "median_future_min_ret_60m": _safe_median(group.get("oos_median_future_min_ret_60m", pd.Series(dtype=float))),
                "downside_hit_rate_30m": _safe_mean(group.get("oos_downside_hit_rate_30m", pd.Series(dtype=float))),
                "downside_hit_rate_60m": _safe_mean(group.get("oos_downside_hit_rate_60m", pd.Series(dtype=float))),
                "reclaim_rate_60m": _safe_mean(group.get("oos_reclaim_rate_60m", pd.Series(dtype=float))),
                "diagnostics_model": OOS_DIAGNOSTICS_MODEL,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    return rows


def _oos_event_split_rows(frame: pd.DataFrame, *, axis: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if frame.empty or axis not in frame.columns:
        return rows
    for value, group in frame.groupby(axis, dropna=False, sort=True):
        score = pd.to_numeric(group.get("event_response_score", pd.Series(dtype=float)), errors="coerce")
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "diagnostic_source": "selected_oos_events",
                "split_axis": axis,
                "split_value": str(value),
                "rows": int(len(group)),
                "active_rows": int(len(group)),
                "unique_test_days": int(group["test_day_ord"].nunique()) if "test_day_ord" in group.columns else 0,
                "unique_selection_keys": int(group["selection_key"].nunique()) if "selection_key" in group.columns else 0,
                "unique_symbols": int(group["symbol"].nunique()) if "symbol" in group.columns else 0,
                "total_events": int(len(group)),
                "median_response_score": _safe_median(score),
                "avg_response_score": _safe_mean(score),
                "positive_score_rate": _rate(score > 0.0),
                "positive_active_score_rate": _rate(score > 0.0),
                "expected_downside_rate": _rate(group.get("event_expected_downside", pd.Series(dtype=bool)).map(_to_bool)),
                "median_future_ret_30m": _safe_median(group.get("future_ret_30m", pd.Series(dtype=float))),
                "median_future_ret_60m": _safe_median(group.get("future_ret_60m", pd.Series(dtype=float))),
                "median_future_min_ret_30m": _safe_median(group.get("future_min_ret_30m", pd.Series(dtype=float))),
                "median_future_min_ret_60m": _safe_median(group.get("future_min_ret_60m", pd.Series(dtype=float))),
                "downside_hit_rate_30m": _rate(group.get("downside_hit_30m", pd.Series(dtype=bool)).map(_to_bool)),
                "downside_hit_rate_60m": _rate(group.get("downside_hit_60m", pd.Series(dtype=bool)).map(_to_bool)),
                "reclaim_rate_60m": _rate(group.get("reclaimed_pump_high_60m", pd.Series(dtype=bool)).map(_to_bool)),
                "diagnostics_model": OOS_DIAGNOSTICS_MODEL,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    return rows


def _build_oos_top_removal(*, daily_oos_events: pd.DataFrame) -> pd.DataFrame:
    columns = _oos_top_removal_columns()
    if daily_oos_events.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    groups: list[tuple[str, str, pd.DataFrame]] = [("all", "all", daily_oos_events)]
    if "train_window_days" in daily_oos_events.columns:
        groups.extend(("train_window_days", str(value), group) for value, group in daily_oos_events.groupby("train_window_days", dropna=False, sort=True))
    if "selection_key" in daily_oos_events.columns:
        groups.extend(("selection_key", str(value), group) for value, group in daily_oos_events.groupby("selection_key", dropna=False, sort=True))
    for group_axis, group_value, group in groups:
        rows.extend(_top_removal_rows_for_group(group_axis=group_axis, group_value=group_value, group=group, removal_kind="top_events"))
        rows.extend(_top_removal_rows_for_group(group_axis=group_axis, group_value=group_value, group=group, removal_kind="top_symbols"))
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["group_axis", "group_value", "removal_kind", "remove_fraction"])


def _top_removal_rows_for_group(*, group_axis: str, group_value: str, group: pd.DataFrame, removal_kind: str) -> list[dict[str, object]]:
    score = pd.to_numeric(group.get("event_response_score", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    base_score_sum = float(score.sum())
    base_events = int(len(group))
    if removal_kind == "top_symbols" and "symbol" in group.columns:
        ranked = group.assign(_score=score).groupby("symbol", dropna=False)["_score"].sum().sort_values(ascending=False)
        unit_count = int(len(ranked))
    else:
        ranked = score.sort_values(ascending=False)
        unit_count = int(len(ranked))
    rows: list[dict[str, object]] = []
    for fraction in (0.0, 0.01, 0.05, 0.10, 0.20):
        remove_count = _removal_count(unit_count, float(fraction))
        if removal_kind == "top_symbols" and "symbol" in group.columns:
            removed_symbols = set(ranked.head(remove_count).index.astype(str)) if remove_count > 0 else set()
            remaining = group.loc[~group["symbol"].astype(str).isin(removed_symbols)].copy()
        else:
            removed_index = set(ranked.head(remove_count).index) if remove_count > 0 else set()
            remaining = group.loc[~group.index.isin(removed_index)].copy()
        remaining_score = pd.to_numeric(remaining.get("event_response_score", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        remaining_score_sum = float(remaining_score.sum())
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "group_axis": group_axis,
                "group_value": group_value,
                "removal_kind": removal_kind,
                "remove_fraction": float(fraction),
                "base_events": base_events,
                "base_symbols": int(group["symbol"].nunique()) if "symbol" in group.columns else 0,
                "removed_units": int(remove_count),
                "remaining_events": int(len(remaining)),
                "remaining_symbols": int(remaining["symbol"].nunique()) if "symbol" in remaining.columns and not remaining.empty else 0,
                "base_score_sum": base_score_sum,
                "remaining_score_sum": remaining_score_sum,
                "score_retention_rate": float(remaining_score_sum / base_score_sum) if base_score_sum != 0 else float("nan"),
                "remaining_score_positive": bool(remaining_score_sum > 0.0),
                "top_removal_model": OOS_TOP_REMOVAL_MODEL,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    return rows


def _removal_count(unit_count: int, fraction: float) -> int:
    if int(unit_count) <= 0 or float(fraction) <= 0.0:
        return 0
    return min(int(unit_count), max(1, int(math.ceil(float(unit_count) * float(fraction)))))


def _build_oos_selection_summary(
    *,
    daily_selection: pd.DataFrame,
    daily_oos: pd.DataFrame,
    daily_oos_events: pd.DataFrame,
) -> pd.DataFrame:
    columns = _oos_selection_summary_columns()
    if daily_selection.empty:
        return pd.DataFrame(columns=columns)
    oos_grouped = daily_oos.groupby("selection_key", dropna=False) if not daily_oos.empty and "selection_key" in daily_oos.columns else None
    event_grouped = daily_oos_events.groupby("selection_key", dropna=False) if not daily_oos_events.empty and "selection_key" in daily_oos_events.columns else None
    rows: list[dict[str, object]] = []
    for selection_key, selected in daily_selection.groupby("selection_key", dropna=False, sort=True):
        key = str(selection_key)
        oos = oos_grouped.get_group(selection_key) if oos_grouped is not None and selection_key in oos_grouped.groups else pd.DataFrame()
        events = event_grouped.get_group(selection_key) if event_grouped is not None and selection_key in event_grouped.groups else pd.DataFrame()
        score = pd.to_numeric(oos.get("oos_response_score", pd.Series(dtype=float)), errors="coerce") if not oos.empty else pd.Series(dtype=float)
        active = pd.to_numeric(oos.get("oos_events", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0 if not oos.empty else pd.Series(dtype=bool)
        event_score = pd.to_numeric(events.get("event_response_score", pd.Series(dtype=float)), errors="coerce") if not events.empty else pd.Series(dtype=float)
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "selection_key": key,
                "selected_rows": int(len(selected)),
                "selected_days": int(selected["test_day_ord"].nunique()) if "test_day_ord" in selected.columns else 0,
                "train_windows_seen": int(selected["train_window_days"].nunique()) if "train_window_days" in selected.columns else 0,
                "first_selected_date": str(selected["test_date"].min()) if "test_date" in selected.columns else "",
                "last_selected_date": str(selected["test_date"].max()) if "test_date" in selected.columns else "",
                "basin_statuses_seen": ",".join(sorted(set(selected.get("basin_status", pd.Series(dtype=str)).astype(str)))) if not selected.empty else "",
                "oos_rows": int(len(oos)),
                "oos_active_rows": int(active.sum()) if not active.empty else 0,
                "oos_total_events": int(pd.to_numeric(oos.get("oos_events", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not oos.empty else 0,
                "oos_median_response_score": _safe_median(score),
                "oos_avg_response_score": _safe_mean(score),
                "oos_positive_active_score_rate": _rate(score.loc[active] > 0.0) if not score.empty and bool(active.any()) else float("nan"),
                "event_rows": int(len(events)),
                "event_symbols": int(events["symbol"].nunique()) if "symbol" in events.columns and not events.empty else 0,
                "event_sessions": int(events["session_bucket"].nunique()) if "session_bucket" in events.columns and not events.empty else 0,
                "event_median_response_score": _safe_median(event_score),
                "event_score_sum": _safe_sum(event_score),
                "event_positive_score_rate": _rate(event_score > 0.0) if not event_score.empty else float("nan"),
                "top_symbol_event_share": _top_share(events, column="symbol"),
                "top_day_event_share": _top_share(events, column="test_day_ord"),
                "selection_summary_model": OOS_SELECTION_SUMMARY_MODEL,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["oos_median_response_score", "event_score_sum", "selection_key"])


def _build_oos_verdict(
    *,
    daily_oos: pd.DataFrame,
    daily_oos_events: pd.DataFrame,
    oos_top_removal: pd.DataFrame,
    oos_selection_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Produce explicit OOS pass/fail verdicts for selected mechanism keys.

    This is a downstream diagnostic artifact.  It may read OOS outcomes because
    it never feeds daily selection or threshold fitting.  Its only job is to say
    which prequentially selected mechanism keys survived the OOS stability gates
    and which reasons block them from the later short-mapping layer.
    """

    columns = _oos_verdict_columns()
    key_values: set[str] = set()
    for frame in (oos_selection_summary, daily_oos, daily_oos_events):
        if frame is not None and not frame.empty and "selection_key" in frame.columns:
            key_values.update(frame["selection_key"].astype(str).dropna().tolist())
    if not key_values:
        return pd.DataFrame(columns=columns)

    summary_lookup = (
        oos_selection_summary.drop_duplicates("selection_key", keep="last").set_index("selection_key", drop=False)
        if oos_selection_summary is not None and not oos_selection_summary.empty and "selection_key" in oos_selection_summary.columns
        else pd.DataFrame()
    )
    oos_grouped = daily_oos.groupby("selection_key", dropna=False) if daily_oos is not None and not daily_oos.empty and "selection_key" in daily_oos.columns else None
    event_grouped = daily_oos_events.groupby("selection_key", dropna=False) if daily_oos_events is not None and not daily_oos_events.empty and "selection_key" in daily_oos_events.columns else None
    top_removal_lookup = _oos_top_removal_lookup(oos_top_removal)

    rows: list[dict[str, object]] = []
    for selection_key in sorted(key_values):
        summary = summary_lookup.loc[selection_key].to_dict() if not summary_lookup.empty and selection_key in summary_lookup.index else {}
        oos = oos_grouped.get_group(selection_key) if oos_grouped is not None and selection_key in oos_grouped.groups else pd.DataFrame()
        events = event_grouped.get_group(selection_key) if event_grouped is not None and selection_key in event_grouped.groups else pd.DataFrame()
        active = pd.to_numeric(oos.get("oos_events", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0 if not oos.empty else pd.Series(dtype=bool)
        active_oos = oos.loc[active].copy() if not oos.empty and len(active) == len(oos) else pd.DataFrame()
        active_score = pd.to_numeric(active_oos.get("oos_response_score", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan)
        event_score = pd.to_numeric(events.get("event_response_score", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan) if not events.empty else pd.Series(dtype=float)

        selected_days = int(summary.get("selected_days", 0) or 0)
        train_windows_seen = int(summary.get("train_windows_seen", 0) or 0)
        oos_active_days = int(active.sum()) if not active.empty else int(summary.get("oos_active_rows", 0) or 0)
        total_events = int(len(events)) if not events.empty else int(summary.get("event_rows", 0) or 0)
        event_symbols = int(events["symbol"].nunique()) if "symbol" in events.columns and not events.empty else int(summary.get("event_symbols", 0) or 0)
        event_sessions = int(events["session_bucket"].nunique()) if "session_bucket" in events.columns and not events.empty else int(summary.get("event_sessions", 0) or 0)
        event_months = _event_month_count(events)
        median_response_score = _safe_median(active_score)
        avg_response_score = _safe_mean(active_score)
        positive_active_score_rate = _rate(active_score > 0.0) if not active_score.empty else float("nan")
        event_median_response_score = _safe_median(event_score)
        event_score_sum = _safe_sum(event_score)
        event_positive_score_rate = _rate(event_score > 0.0) if not event_score.empty else float("nan")
        expected_downside_rate = _rate(events.get("event_expected_downside", pd.Series(dtype=bool)).map(_to_bool)) if not events.empty else float("nan")
        top_symbol_event_share = _top_share(events, column="symbol")
        top_day_event_share = _top_share(events, column="test_day_ord")
        month_positive_score_share = _month_positive_score_share(events)
        first_half_score, second_half_score = _half_event_score_sums(events)
        odd_score, even_score = _odd_even_event_score_sums(events)
        top_event_retention, top_event_positive = top_removal_lookup.get((selection_key, "top_events"), (float("nan"), False))
        top_symbol_retention, top_symbol_positive = top_removal_lookup.get((selection_key, "top_symbols"), (float("nan"), False))

        fail_reasons: list[str] = []
        warning_reasons: list[str] = []
        if oos_active_days < OOS_VERDICT_MIN_ACTIVE_DAYS:
            fail_reasons.append("low_oos_active_days")
        if total_events < OOS_VERDICT_MIN_EVENTS:
            fail_reasons.append("low_oos_event_count")
        if event_symbols < OOS_VERDICT_MIN_SYMBOLS:
            fail_reasons.append("low_symbol_breadth")
        if event_sessions < OOS_VERDICT_MIN_SESSIONS:
            fail_reasons.append("low_session_breadth")
        if not math.isfinite(median_response_score) or median_response_score <= OOS_VERDICT_MIN_MEDIAN_RESPONSE_SCORE:
            fail_reasons.append("non_positive_oos_median_response_score")
        if not math.isfinite(positive_active_score_rate) or positive_active_score_rate < OOS_VERDICT_MIN_POSITIVE_ACTIVE_SCORE_RATE:
            fail_reasons.append("low_positive_active_score_rate")
        if not math.isfinite(top_symbol_event_share) or top_symbol_event_share > OOS_VERDICT_MAX_TOP_SYMBOL_EVENT_SHARE:
            fail_reasons.append("top_symbol_dependency")
        if not math.isfinite(top_day_event_share) or top_day_event_share > OOS_VERDICT_MAX_TOP_DAY_EVENT_SHARE:
            fail_reasons.append("top_day_dependency")
        if not math.isfinite(month_positive_score_share) or month_positive_score_share > OOS_VERDICT_MAX_MONTH_POSITIVE_SCORE_SHARE:
            fail_reasons.append("month_positive_score_concentration")
        if not bool(top_event_positive):
            fail_reasons.append("top_event_removal_not_positive")
        if not bool(top_symbol_positive):
            fail_reasons.append("top_symbol_removal_not_positive")
        if math.isfinite(first_half_score) and math.isfinite(second_half_score) and first_half_score > 0.0 and second_half_score <= 0.0:
            fail_reasons.append("second_half_degraded_to_non_positive")
        if math.isfinite(odd_score) and math.isfinite(even_score) and (odd_score <= 0.0 or even_score <= 0.0):
            warning_reasons.append("odd_even_split_not_both_positive")
        if selected_days > 0 and oos_active_days / max(selected_days, 1) < 0.35:
            warning_reasons.append("selected_oos_sparse_after_rule_application")

        if not fail_reasons:
            verdict = "accepted_mechanism"
            next_stage = "short_mapping_candidate"
            allowed = True
        elif (
            total_events >= OOS_VERDICT_MIN_EVENTS
            and math.isfinite(median_response_score)
            and median_response_score > OOS_VERDICT_MIN_MEDIAN_RESPONSE_SCORE
            and math.isfinite(positive_active_score_rate)
            and positive_active_score_rate >= OOS_VERDICT_MIN_POSITIVE_ACTIVE_SCORE_RATE
        ):
            verdict = "watchlist_mechanism"
            next_stage = "mechanism_research_only"
            allowed = False
        else:
            verdict = "rejected_mechanism"
            next_stage = "mechanism_research_only"
            allowed = False

        rows.append(
            {
                "research_id": RESEARCH_ID,
                "selection_key": selection_key,
                "oos_verdict": verdict,
                "allowed_for_short_mapping": allowed,
                "next_allowed_stage": next_stage,
                "fail_reasons": ";".join(fail_reasons) if fail_reasons else "pass",
                "warning_reasons": ";".join(warning_reasons),
                "selected_days": selected_days,
                "train_windows_seen": train_windows_seen,
                "first_selected_date": str(summary.get("first_selected_date", "")),
                "last_selected_date": str(summary.get("last_selected_date", "")),
                "basin_statuses_seen": str(summary.get("basin_statuses_seen", "")),
                "oos_rows": int(len(oos)),
                "oos_active_days": oos_active_days,
                "oos_total_events": total_events,
                "event_rows": int(len(events)),
                "event_symbols": event_symbols,
                "event_sessions": event_sessions,
                "event_months": event_months,
                "dominant_mechanism_family": _mode_value(events, "mechanism_family"),
                "dominant_acceptance_regime": _mode_value(events, "acceptance_regime"),
                "dominant_oi_regime": _mode_value(events, "oi_regime"),
                "dominant_flow_regime": _mode_value(events, "flow_regime"),
                "dominant_structure_regime": _mode_value(events, "structure_regime"),
                "oos_median_response_score": median_response_score,
                "oos_avg_response_score": avg_response_score,
                "oos_positive_active_score_rate": positive_active_score_rate,
                "event_median_response_score": event_median_response_score,
                "event_score_sum": event_score_sum,
                "event_positive_score_rate": event_positive_score_rate,
                "expected_downside_rate": expected_downside_rate,
                "median_future_ret_30m": _safe_median(events.get("future_ret_30m", pd.Series(dtype=float))),
                "median_future_ret_60m": _safe_median(events.get("future_ret_60m", pd.Series(dtype=float))),
                "median_future_min_ret_30m": _safe_median(events.get("future_min_ret_30m", pd.Series(dtype=float))),
                "median_future_min_ret_60m": _safe_median(events.get("future_min_ret_60m", pd.Series(dtype=float))),
                "downside_hit_rate_30m": _rate(events.get("downside_hit_30m", pd.Series(dtype=bool)).map(_to_bool)) if not events.empty else float("nan"),
                "downside_hit_rate_60m": _rate(events.get("downside_hit_60m", pd.Series(dtype=bool)).map(_to_bool)) if not events.empty else float("nan"),
                "reclaim_rate_60m": _rate(events.get("reclaimed_pump_high_60m", pd.Series(dtype=bool)).map(_to_bool)) if not events.empty else float("nan"),
                "top_symbol_event_share": top_symbol_event_share,
                "top_day_event_share": top_day_event_share,
                "month_positive_score_share": month_positive_score_share,
                "first_half_event_score_sum": first_half_score,
                "second_half_event_score_sum": second_half_score,
                "odd_day_event_score_sum": odd_score,
                "even_day_event_score_sum": even_score,
                "top_event_removal_10pct_score_retention_rate": top_event_retention,
                "top_event_removal_10pct_remaining_positive": bool(top_event_positive),
                "top_symbol_removal_10pct_score_retention_rate": top_symbol_retention,
                "top_symbol_removal_10pct_remaining_positive": bool(top_symbol_positive),
                "min_active_days_gate": int(OOS_VERDICT_MIN_ACTIVE_DAYS),
                "min_events_gate": int(OOS_VERDICT_MIN_EVENTS),
                "min_symbols_gate": int(OOS_VERDICT_MIN_SYMBOLS),
                "min_sessions_gate": int(OOS_VERDICT_MIN_SESSIONS),
                "min_positive_active_score_rate_gate": float(OOS_VERDICT_MIN_POSITIVE_ACTIVE_SCORE_RATE),
                "min_median_response_score_gate": float(OOS_VERDICT_MIN_MEDIAN_RESPONSE_SCORE),
                "max_top_symbol_event_share_gate": float(OOS_VERDICT_MAX_TOP_SYMBOL_EVENT_SHARE),
                "max_top_day_event_share_gate": float(OOS_VERDICT_MAX_TOP_DAY_EVENT_SHARE),
                "max_month_positive_score_share_gate": float(OOS_VERDICT_MAX_MONTH_POSITIVE_SCORE_SHARE),
                "top_removal_fraction_gate": float(OOS_VERDICT_TOP_REMOVAL_FRACTION),
                "oos_verdict_model": OOS_VERDICT_MODEL,
                "selection_uses_test_day_outcomes": False,
                "verdict_uses_oos_outcomes": True,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "future_label_available_at_entry": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["oos_verdict", "oos_median_response_score", "event_score_sum", "selection_key"])



def _build_short_trade_candidates(
    *,
    config: PumpMechanismStabilityConfig,
    storage: ParquetStorage,
    oos_verdict: pd.DataFrame,
    daily_oos_events: pd.DataFrame,
    events: pd.DataFrame,
) -> pd.DataFrame:
    """Map accepted mechanism OOS rows to honest short confirm candidates.

    This is the first execution-adjacent layer, but it still does not simulate
    exits or PnL.  It only asks whether an OOS event selected by an accepted
    mechanism later produced a closed 1m structural-low-break confirmation that
    can be entered on the next 1m open.  Rejected/watchlist mechanisms are not
    eligible, and no outcome columns are used for the mapping decision.
    """

    columns = _short_trade_candidate_columns()
    accepted = _accepted_oos_events_for_short_mapping(oos_verdict=oos_verdict, daily_oos_events=daily_oos_events, events=events)
    if accepted.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    for symbol, symbol_events in accepted.groupby("symbol", dropna=False, sort=True):
        symbol_str = str(symbol)
        if not symbol_str:
            continue
        min_cutoff = _finite_int_or_none(pd.to_numeric(symbol_events.get("feature_cutoff_ms", pd.Series(dtype=float)), errors="coerce").min())
        max_cutoff = _finite_int_or_none(pd.to_numeric(symbol_events.get("feature_cutoff_ms", pd.Series(dtype=float)), errors="coerce").max())
        if min_cutoff is None or max_cutoff is None:
            for selected in symbol_events.to_dict("records"):
                rows.append(_short_mapping_rejection_row(selected, status="missing_feature_cutoff", reason="feature_cutoff_ms_missing"))
            continue
        load_start_ms = int(min_cutoff) - FIVE_MINUTE_MS
        load_end_ms = int(max_cutoff) + (int(SHORT_CONFIRM_WINDOW_MINUTES) + 5) * MINUTE_MS
        frame_1m_result = _load_frame_result(storage, symbol_str, "1m", start_ms=load_start_ms, end_ms=load_end_ms)
        frame_5m_result = _load_frame_result(storage, symbol_str, "5m", start_ms=load_start_ms - 15 * MINUTE_MS, end_ms=load_end_ms)
        frame_1m = _prepare_ohlcv(frame_1m_result.frame) if frame_1m_result.ok and not frame_1m_result.frame.empty else pd.DataFrame()
        oi_lookup = _prepare_oi_lookup(_prepare_oi(frame_5m_result.frame)) if frame_5m_result.ok and not frame_5m_result.frame.empty else _prepare_oi_lookup(pd.DataFrame())
        if frame_1m.empty:
            for selected in symbol_events.to_dict("records"):
                rows.append(
                    _short_mapping_rejection_row(
                        selected,
                        status="missing_1m_cache_for_mapping",
                        reason=str(frame_1m_result.reason or "missing_1m_cache"),
                    )
                )
            continue
        for selected in symbol_events.to_dict("records"):
            rows.append(_find_short_confirm_candidate(selected=selected, frame_1m=frame_1m, oi_lookup=oi_lookup, config=config))

    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "selected_rank", "event_rank", "event_id"])


def _accepted_oos_events_for_short_mapping(*, oos_verdict: pd.DataFrame, daily_oos_events: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "selection_key", "test_day_ord", "test_date", "train_window_days", "train_start_day_ord", "train_end_day_ord", "selected_rank",
        "basin_id", "center_rule_id", "basin_status_at_selection", "event_rank", "event_id", "symbol", "session_bucket",
        "mechanism_family", "mechanism_id", "acceptance_regime", "oi_regime", "flow_regime", "price_progress_regime", "structure_regime",
        "late_buyer_regime", "feature_cutoff_ms", "feature_cutoff_time_utc", "pump_high", "pump_high_timestamp_ms", "structural_low",
        "structural_low_timestamp_ms", "structural_high", "structural_high_timestamp_ms", "feature_price", "seed_open_ms", "seed_close_ms",
    ]
    if oos_verdict.empty or daily_oos_events.empty:
        return pd.DataFrame(columns=columns)
    verdict = oos_verdict.copy()
    verdict = verdict.loc[
        verdict.get("allowed_for_short_mapping", pd.Series(dtype=bool)).map(_to_bool)
        & verdict.get("oos_verdict", pd.Series(dtype=str)).astype(str).eq(SHORT_ALLOWED_VERDICT)
    ].copy()
    if verdict.empty or "selection_key" not in verdict.columns:
        return pd.DataFrame(columns=columns)
    accepted_keys = set(verdict["selection_key"].astype(str))
    selected = daily_oos_events.loc[daily_oos_events.get("selection_key", pd.Series(dtype=str)).astype(str).isin(accepted_keys)].copy()
    if selected.empty:
        return pd.DataFrame(columns=columns)
    selected = selected[[column for column in selected.columns if not column.startswith("future_") and column not in {"down_mfe_30m", "down_mfe_60m", "up_mae_30m", "up_mae_60m"}]].copy()
    if events.empty or "event_id" not in events.columns:
        return _ensure_columns(selected, columns)
    event_columns = [
        "event_id", "feature_cutoff_ms", "feature_cutoff_time_utc", "pump_high", "pump_high_timestamp_ms", "structural_low",
        "structural_low_timestamp_ms", "structural_high", "structural_high_timestamp_ms", "feature_price", "seed_open_ms", "seed_close_ms",
    ]
    event_features = events[[column for column in event_columns if column in events.columns]].drop_duplicates("event_id", keep="last")
    work = selected.merge(event_features, on="event_id", how="left", suffixes=("", "_event"))
    result = _ensure_columns(work, columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "selected_rank", "event_rank", "event_id"])


def _find_short_confirm_candidate(
    *,
    selected: dict[str, object],
    frame_1m: pd.DataFrame,
    oi_lookup: _OiLookup,
    config: PumpMechanismStabilityConfig,
) -> dict[str, object]:
    feature_cutoff_ms = _finite_int_or_none(selected.get("feature_cutoff_ms"))
    structural_low = _float(selected.get("structural_low"))
    structural_high = _float(selected.get("structural_high"))
    if feature_cutoff_ms is None:
        return _short_mapping_rejection_row(selected, status="missing_feature_cutoff", reason="feature_cutoff_ms_missing")
    if not math.isfinite(structural_low) or structural_low <= 0.0:
        return _short_mapping_rejection_row(selected, status="missing_structural_low", reason="structural_low_missing_or_invalid")
    if not math.isfinite(structural_high) or structural_high <= 0.0:
        return _short_mapping_rejection_row(selected, status="missing_structural_high", reason="structural_high_missing_or_invalid")
    if not {"timestamp", "open", "high", "low", "close"}.issubset(frame_1m.columns):
        return _short_mapping_rejection_row(selected, status="missing_1m_ohlc_columns", reason="required_1m_ohlc_columns_missing")
    if "quote_volume" not in frame_1m.columns or "number_of_trades" not in frame_1m.columns:
        return _short_mapping_rejection_row(selected, status="missing_1m_flow_columns", reason="quote_volume_or_number_of_trades_missing")

    search_end_ms = int(feature_cutoff_ms) + int(SHORT_CONFIRM_WINDOW_MINUTES) * MINUTE_MS
    window = _time_window(frame_1m, start_ms=int(feature_cutoff_ms), end_ms=search_end_ms, include_end=False)
    if window.empty:
        return _short_mapping_rejection_row(selected, status="no_confirm_window_1m_rows", reason="no_1m_rows_after_feature_cutoff")

    ts_to_row = {int(row["timestamp"]): row for row in frame_1m.to_dict("records") if math.isfinite(_float(row.get("timestamp")))}
    break_level = float(structural_low) * (1.0 - float(SHORT_CONFIRM_CLOSE_BELOW_STRUCTURAL_LOW_BUFFER_PCT))
    stop_price = float(structural_high) * (1.0 + float(SHORT_STOP_BUFFER_PCT))

    for candle in window.to_dict("records"):
        confirm_open = _float(candle.get("open"))
        confirm_high = _float(candle.get("high"))
        confirm_low = _float(candle.get("low"))
        confirm_close = _float(candle.get("close"))
        confirm_ts = _finite_int_or_none(candle.get("timestamp"))
        quote_volume = _float(candle.get("quote_volume"))
        trades = _float(candle.get("number_of_trades"))
        if confirm_ts is None or not all(math.isfinite(value) and value > 0.0 for value in (confirm_open, confirm_high, confirm_low, confirm_close)):
            continue
        if confirm_close > break_level:
            continue
        if confirm_close >= confirm_open:
            continue
        body_pct = (confirm_open - confirm_close) / confirm_open if confirm_open > 0.0 else float("nan")
        if not math.isfinite(body_pct) or body_pct < float(SHORT_CONFIRM_MIN_BODY_PCT):
            continue
        close_location = _candle_close_location(candle)
        if math.isfinite(close_location) and close_location > float(SHORT_CONFIRM_NEAR_LOW_MAX):
            continue
        if not math.isfinite(quote_volume) or quote_volume <= 0.0 or not math.isfinite(trades) or trades <= 0.0:
            continue
        confirm_close_ms = int(confirm_ts) + MINUTE_MS
        next_row = ts_to_row.get(confirm_close_ms)
        if next_row is None:
            return _short_mapping_rejection_row(
                selected,
                status="confirm_found_but_missing_next_1m_open",
                reason="next_1m_open_not_available_after_confirm_close",
                confirm_candle=candle,
            )
        next_open = _float(next_row.get("open"))
        if not math.isfinite(next_open) or next_open <= 0.0:
            return _short_mapping_rejection_row(
                selected,
                status="confirm_found_but_invalid_next_1m_open",
                reason="next_1m_open_invalid",
                confirm_candle=candle,
            )
        entry_price = next_open * (1.0 - float(SHORT_ENTRY_ADVERSE_SLIPPAGE_BPS) / 10_000.0)
        # For a short, adverse slippage means selling slightly lower than the next open.
        risk_pct = stop_price / entry_price - 1.0 if entry_price > 0.0 else float("nan")
        if not math.isfinite(risk_pct) or risk_pct < float(SHORT_CONFIRM_MIN_ENTRY_RISK_PCT):
            return _short_mapping_rejection_row(
                selected,
                status="confirm_found_but_invalid_stop_risk",
                reason="entry_stop_risk_too_small_or_invalid",
                confirm_candle=candle,
                entry_timestamp_ms=confirm_close_ms,
                entry_price=entry_price,
                stop_price=stop_price,
            )
        if risk_pct > float(SHORT_CONFIRM_MAX_ENTRY_RISK_PCT):
            return _short_mapping_rejection_row(
                selected,
                status="confirm_found_but_invalid_stop_risk",
                reason="entry_stop_risk_too_large",
                confirm_candle=candle,
                entry_timestamp_ms=confirm_close_ms,
                entry_price=entry_price,
                stop_price=stop_price,
            )
        oi_context = _closed_5m_oi_context(
            oi_lookup,
            asof_timestamp_ms=confirm_close_ms,
            threshold_pct=float(config.oi_change_threshold_pct),
            strong_threshold_pct=float(config.oi_strong_change_threshold_pct),
        )
        signal_family = _short_signal_family(oi_context)
        if signal_family == "no_oi_confirmation":
            status = "confirmed_audit_only_no_oi_candidate"
            allowed_for_exit_grid = False
        else:
            status = "confirmed_short_candidate"
            allowed_for_exit_grid = True
        return _short_trade_candidate_row(
            selected=selected,
            status=status,
            rejection_reason="pass" if status == "confirmed_short_candidate" else "no_oi_confirmation_audit_only",
            signal_family=signal_family,
            allowed_for_exit_grid=allowed_for_exit_grid,
            confirm_candle=candle,
            entry_timestamp_ms=confirm_close_ms,
            entry_raw_next_open=next_open,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_pct=risk_pct,
            oi_context=oi_context,
        )
    return _short_mapping_rejection_row(selected, status="no_closed_1m_confirm", reason="no_bearish_structural_low_break_in_confirm_window")


def _short_signal_family(oi_context: dict[str, object]) -> str:
    regime = str(oi_context.get("oi_regime", ""))
    if regime == "oi_down_squeeze_unwind":
        return "oi_drop_position_exit"
    if regime == "oi_up_fresh_leverage":
        return "oi_rise_fresh_shorts"
    return "no_oi_confirmation"


def _candle_close_location(candle: dict[str, object]) -> float:
    high = _float(candle.get("high"))
    low = _float(candle.get("low"))
    close = _float(candle.get("close"))
    if not all(math.isfinite(value) for value in (high, low, close)) or high <= low:
        return float("nan")
    return (close - low) / (high - low)


def _short_mapping_rejection_row(
    selected: dict[str, object],
    *,
    status: str,
    reason: str,
    confirm_candle: dict[str, object] | None = None,
    entry_timestamp_ms: int | None = None,
    entry_price: float = float("nan"),
    stop_price: float = float("nan"),
) -> dict[str, object]:
    return _short_trade_candidate_row(
        selected=selected,
        status=status,
        rejection_reason=reason,
        signal_family="not_mapped",
        allowed_for_exit_grid=False,
        confirm_candle=confirm_candle or {},
        entry_timestamp_ms=entry_timestamp_ms,
        entry_raw_next_open=float("nan"),
        entry_price=entry_price,
        stop_price=stop_price,
        risk_pct=(stop_price / entry_price - 1.0) if math.isfinite(entry_price) and entry_price > 0.0 and math.isfinite(stop_price) else float("nan"),
        oi_context={},
    )


def _short_trade_candidate_row(
    *,
    selected: dict[str, object],
    status: str,
    rejection_reason: str,
    signal_family: str,
    allowed_for_exit_grid: bool,
    confirm_candle: dict[str, object],
    entry_timestamp_ms: int | None,
    entry_raw_next_open: float,
    entry_price: float,
    stop_price: float,
    risk_pct: float,
    oi_context: dict[str, object],
) -> dict[str, object]:
    confirm_ts = _finite_int_or_none(confirm_candle.get("timestamp")) if confirm_candle else None
    confirm_close_ms = int(confirm_ts) + MINUTE_MS if confirm_ts is not None else np.nan
    confirm_close = _float(confirm_candle.get("close")) if confirm_candle else float("nan")
    structural_low = _float(selected.get("structural_low"))
    structural_high = _float(selected.get("structural_high"))
    entry_ts = int(entry_timestamp_ms) if entry_timestamp_ms is not None else np.nan
    candidate_key = "|".join(
        (
            str(selected.get("selection_key", "")),
            str(selected.get("event_id", "")),
            str(confirm_ts or "no_confirm"),
            str(status),
        )
    )
    return {
        "research_id": RESEARCH_ID,
        "short_candidate_id": "pms_" + hashlib.sha1(candidate_key.encode("utf-8")).hexdigest()[:16],
        "selection_key": str(selected.get("selection_key", "")),
        "test_day_ord": int(selected.get("test_day_ord", 0) or 0),
        "test_date": str(selected.get("test_date", "")),
        "train_window_days": int(selected.get("train_window_days", 0) or 0),
        "train_start_day_ord": int(selected.get("train_start_day_ord", 0) or 0),
        "train_end_day_ord": int(selected.get("train_end_day_ord", 0) or 0),
        "selected_rank": int(selected.get("selected_rank", 0) or 0),
        "basin_id": str(selected.get("basin_id", "")),
        "center_rule_id": str(selected.get("center_rule_id", "")),
        "basin_status_at_selection": str(selected.get("basin_status_at_selection", "")),
        "event_rank": int(selected.get("event_rank", 0) or 0),
        "event_id": str(selected.get("event_id", "")),
        "symbol": str(selected.get("symbol", "")),
        "session_bucket": str(selected.get("session_bucket", "")),
        "mechanism_family": str(selected.get("mechanism_family", "")),
        "mechanism_id": str(selected.get("mechanism_id", "")),
        "acceptance_regime": str(selected.get("acceptance_regime", "")),
        "oi_regime_at_feature_cutoff": str(selected.get("oi_regime", "")),
        "flow_regime": str(selected.get("flow_regime", "")),
        "price_progress_regime": str(selected.get("price_progress_regime", "")),
        "structure_regime": str(selected.get("structure_regime", "")),
        "late_buyer_regime": str(selected.get("late_buyer_regime", "")),
        "feature_cutoff_ms": _finite_int_or_none(selected.get("feature_cutoff_ms")) or np.nan,
        "feature_cutoff_time_utc": str(selected.get("feature_cutoff_time_utc", "")),
        "confirm_search_window_minutes": int(SHORT_CONFIRM_WINDOW_MINUTES),
        "short_mapping_status": status,
        "short_mapping_rejection_reason": rejection_reason,
        "allowed_for_exit_grid": bool(allowed_for_exit_grid),
        "signal_family": signal_family,
        "confirm_candle_timestamp_ms": confirm_ts if confirm_ts is not None else np.nan,
        "confirm_candle_close_timestamp_ms": confirm_close_ms,
        "confirm_candle_time_utc": _fmt_optional_ts(confirm_ts),
        "confirm_candle_close_time_utc": _fmt_optional_ts(confirm_close_ms),
        "confirm_open": _float(confirm_candle.get("open")) if confirm_candle else np.nan,
        "confirm_high": _float(confirm_candle.get("high")) if confirm_candle else np.nan,
        "confirm_low": _float(confirm_candle.get("low")) if confirm_candle else np.nan,
        "confirm_close": confirm_close,
        "confirm_quote_volume": _float(confirm_candle.get("quote_volume")) if confirm_candle else np.nan,
        "confirm_number_of_trades": _float(confirm_candle.get("number_of_trades")) if confirm_candle else np.nan,
        "confirm_taker_buy_share": _candle_taker_buy_share(pd.Series(confirm_candle)) if confirm_candle else np.nan,
        "confirm_bearish_body": bool(confirm_candle and _float(confirm_candle.get("close")) < _float(confirm_candle.get("open"))),
        "confirm_close_location": _candle_close_location(confirm_candle) if confirm_candle else np.nan,
        "confirm_breaks_structural_low": bool(math.isfinite(confirm_close) and math.isfinite(structural_low) and confirm_close <= structural_low * (1.0 - float(SHORT_CONFIRM_CLOSE_BELOW_STRUCTURAL_LOW_BUFFER_PCT))),
        "structural_low": structural_low,
        "structural_low_timestamp_ms": _finite_int_or_none(selected.get("structural_low_timestamp_ms")) or np.nan,
        "structural_high": structural_high,
        "structural_high_timestamp_ms": _finite_int_or_none(selected.get("structural_high_timestamp_ms")) or np.nan,
        "pump_high": _float(selected.get("pump_high")),
        "pump_high_timestamp_ms": _finite_int_or_none(selected.get("pump_high_timestamp_ms")) or np.nan,
        "entry_timestamp_ms": entry_ts,
        "entry_time_utc": _fmt_optional_ts(entry_ts),
        "entry_raw_next_1m_open": entry_raw_next_open,
        "entry_price_after_slippage": entry_price,
        "entry_adverse_slippage_bps": float(SHORT_ENTRY_ADVERSE_SLIPPAGE_BPS),
        "stop_price": stop_price,
        "stop_buffer_pct": float(SHORT_STOP_BUFFER_PCT),
        "entry_to_stop_risk_pct": risk_pct,
        "oi_available_at_confirm": _to_bool(oi_context.get("oi_available")),
        "oi_regime_at_confirm": str(oi_context.get("oi_regime", "")),
        "oi_confirm_asof_timestamp_ms": oi_context.get("oi_asof_timestamp_ms", np.nan),
        "oi_current_timestamp_ms": oi_context.get("oi_current_timestamp_ms", np.nan),
        "oi_current_available_timestamp_ms": oi_context.get("oi_current_available_timestamp_ms", np.nan),
        "oi_change_5m_pct_at_confirm": oi_context.get("oi_change_5m_pct", np.nan),
        "oi_change_10m_pct_at_confirm": oi_context.get("oi_change_10m_pct", np.nan),
        "oi_change_15m_pct_at_confirm": oi_context.get("oi_change_15m_pct", np.nan),
        "accepted_mechanism_required": True,
        "accepted_mechanism_verdict": SHORT_ALLOWED_VERDICT,
        "short_mapping_model": SHORT_MAPPING_MODEL,
        "short_confirm_model": SHORT_CONFIRM_MODEL,
        "entry_model": SHORT_ENTRY_MODEL,
        "stop_model": SHORT_STOP_MODEL,
        "oi_model": SHORT_OI_MODEL,
        "data_access_model": DATA_ACCESS_MODEL,
        "future_label_available_at_entry": False,
        "short_confirm_closed_before_entry": bool(confirm_ts is not None and entry_ts == int(confirm_ts) + MINUTE_MS),
        "mapping_uses_oos_outcome_columns": False,
        "uses_pnl": False,
        "uses_future_exit": False,
        "uses_final_holdout_tuning": False,
    }


def _short_trade_candidate_columns() -> list[str]:
    return [
        "research_id", "short_candidate_id", "selection_key", "test_day_ord", "test_date", "train_window_days", "train_start_day_ord",
        "train_end_day_ord", "selected_rank", "basin_id", "center_rule_id", "basin_status_at_selection", "event_rank", "event_id",
        "symbol", "session_bucket", "mechanism_family", "mechanism_id", "acceptance_regime", "oi_regime_at_feature_cutoff",
        "flow_regime", "price_progress_regime", "structure_regime", "late_buyer_regime", "feature_cutoff_ms", "feature_cutoff_time_utc",
        "confirm_search_window_minutes", "short_mapping_status", "short_mapping_rejection_reason", "allowed_for_exit_grid", "signal_family",
        "confirm_candle_timestamp_ms", "confirm_candle_close_timestamp_ms", "confirm_candle_time_utc", "confirm_candle_close_time_utc",
        "confirm_open", "confirm_high", "confirm_low", "confirm_close", "confirm_quote_volume", "confirm_number_of_trades",
        "confirm_taker_buy_share", "confirm_bearish_body", "confirm_close_location", "confirm_breaks_structural_low", "structural_low",
        "structural_low_timestamp_ms", "structural_high", "structural_high_timestamp_ms", "pump_high", "pump_high_timestamp_ms", "entry_timestamp_ms",
        "entry_time_utc", "entry_raw_next_1m_open", "entry_price_after_slippage", "entry_adverse_slippage_bps", "stop_price",
        "stop_buffer_pct", "entry_to_stop_risk_pct", "oi_available_at_confirm", "oi_regime_at_confirm", "oi_confirm_asof_timestamp_ms",
        "oi_current_timestamp_ms", "oi_current_available_timestamp_ms", "oi_change_5m_pct_at_confirm", "oi_change_10m_pct_at_confirm",
        "oi_change_15m_pct_at_confirm", "accepted_mechanism_required", "accepted_mechanism_verdict", "short_mapping_model", "short_confirm_model",
        "entry_model", "stop_model", "oi_model", "data_access_model", "future_label_available_at_entry", "short_confirm_closed_before_entry",
        "mapping_uses_oos_outcome_columns", "uses_pnl", "uses_future_exit", "uses_final_holdout_tuning",
    ]


def _build_short_trade_grid(*, storage: ParquetStorage, short_trade_candidates: pd.DataFrame) -> pd.DataFrame:
    """Simulate fixed short exit policies for confirmed tradable candidates.

    This layer is intentionally downstream of the accepted-mechanism verdict and
    the closed-confirm/next-open mapping.  It does not create new signals and it
    excludes audit-only no-OI rows.  Exit policies are deterministic and run
    forward minute by minute from the known entry timestamp; there is no
    future-optimal stop/target selection.
    """

    columns = _short_trade_grid_columns()
    tradable = _tradable_short_candidates(short_trade_candidates)
    if tradable.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    for symbol, symbol_candidates in tradable.groupby("symbol", dropna=False, sort=True):
        symbol_str = str(symbol)
        entry_times = pd.to_numeric(symbol_candidates.get("entry_timestamp_ms", pd.Series(dtype=float)), errors="coerce").dropna()
        if not symbol_str or entry_times.empty:
            for candidate in symbol_candidates.to_dict("records"):
                for exit_policy in SHORT_EXIT_POLICIES:
                    rows.append(
                        _short_trade_grid_rejection_row(
                            candidate,
                            exit_policy=exit_policy,
                            status="missing_symbol_or_entry_time",
                            reason="symbol_or_entry_timestamp_missing",
                        )
                    )
            continue
        load_start_ms = int(entry_times.min())
        load_end_ms = int(entry_times.max()) + (int(SHORT_EXIT_MAX_HOLD_MINUTES) + 5) * MINUTE_MS
        frame_1m_result = _load_frame_result(storage, symbol_str, "1m", start_ms=load_start_ms, end_ms=load_end_ms)
        frame_1m = _prepare_ohlcv(frame_1m_result.frame) if frame_1m_result.ok and not frame_1m_result.frame.empty else pd.DataFrame()
        if frame_1m.empty:
            for candidate in symbol_candidates.to_dict("records"):
                for exit_policy in SHORT_EXIT_POLICIES:
                    rows.append(
                        _short_trade_grid_rejection_row(
                            candidate,
                            exit_policy=exit_policy,
                            status="missing_1m_cache_for_exit_grid",
                            reason=str(frame_1m_result.reason or "missing_1m_cache"),
                        )
                    )
            continue
        for candidate in symbol_candidates.to_dict("records"):
            for exit_policy in SHORT_EXIT_POLICIES:
                rows.append(_simulate_short_exit_policy(candidate=candidate, frame_1m=frame_1m, exit_policy=exit_policy))

    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["test_day_ord", "train_window_days", "selected_rank", "event_rank", "exit_policy", "short_candidate_id"])


def _tradable_short_candidates(short_trade_candidates: pd.DataFrame) -> pd.DataFrame:
    columns = _short_trade_candidate_columns()
    if short_trade_candidates is None or short_trade_candidates.empty:
        return pd.DataFrame(columns=columns)
    frame = short_trade_candidates.copy()
    allowed = frame.get("allowed_for_exit_grid", pd.Series(False, index=frame.index)).map(_to_bool)
    confirmed = frame.get("short_mapping_status", pd.Series("", index=frame.index)).astype(str).eq("confirmed_short_candidate")
    signal = frame.get("signal_family", pd.Series("", index=frame.index)).astype(str).isin({"oi_drop_position_exit", "oi_rise_fresh_shorts"})
    required = frame.get("accepted_mechanism_verdict", pd.Series("", index=frame.index)).astype(str).eq(SHORT_ALLOWED_VERDICT)
    return frame.loc[allowed & confirmed & signal & required].copy()


def _simulate_short_exit_policy(*, candidate: dict[str, object], frame_1m: pd.DataFrame, exit_policy: str) -> dict[str, object]:
    entry_ts = _finite_int_or_none(candidate.get("entry_timestamp_ms"))
    entry_price = _float(candidate.get("entry_price_after_slippage"))
    initial_stop = _float(candidate.get("stop_price"))
    if entry_ts is None:
        return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="missing_entry_timestamp", reason="entry_timestamp_ms_missing")
    if not math.isfinite(entry_price) or entry_price <= 0.0:
        return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="invalid_entry_price", reason="entry_price_missing_or_invalid")
    if not math.isfinite(initial_stop) or initial_stop <= entry_price:
        return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="invalid_stop_price", reason="stop_price_missing_or_not_above_entry")
    risk_abs = initial_stop - entry_price
    if risk_abs <= 0.0:
        return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="invalid_risk", reason="non_positive_entry_stop_risk")
    if exit_policy not in SHORT_EXIT_POLICIES:
        return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="unknown_exit_policy", reason="exit_policy_not_registered")
    if not {"timestamp", "open", "high", "low", "close"}.issubset(frame_1m.columns):
        return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="missing_1m_ohlc_columns", reason="required_1m_ohlc_columns_missing")

    hold_end_ms = int(entry_ts) + int(SHORT_EXIT_MAX_HOLD_MINUTES) * MINUTE_MS
    window = _time_window(frame_1m, start_ms=int(entry_ts), end_ms=hold_end_ms, include_end=False)
    if window.empty:
        return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="missing_exit_window", reason="no_1m_rows_from_entry_to_max_hold")

    candles = window.to_dict("records")
    active_stop = float(initial_stop)
    remaining_size = 1.0
    realized_r = 0.0
    partial_tp_hit = False
    partial_tp_timestamp_ms: int | None = None
    partial_tp_price = float("nan")
    partial_tp_r = 0.0
    trailing_updates = 0
    first_trail_update_ms: int | None = None
    last_trail_update_ms: int | None = None
    min_active_stop = float(active_stop)
    max_high = float("nan")
    min_low = float("nan")
    final_exit_price = float("nan")
    final_exit_timestamp_ms: int | None = None
    final_exit_reason = "max_hold_close"

    tp_price = entry_price - float(SHORT_PARTIAL_TP_R_MULTIPLE) * risk_abs
    for index, candle in enumerate(candles):
        candle_ts = _finite_int_or_none(candle.get("timestamp"))
        high = _float(candle.get("high"))
        low = _float(candle.get("low"))
        close = _float(candle.get("close"))
        if candle_ts is None or not all(math.isfinite(value) and value > 0.0 for value in (high, low, close)):
            continue
        max_high = high if not math.isfinite(max_high) else max(max_high, high)
        min_low = low if not math.isfinite(min_low) else min(min_low, low)

        # Conservative intrabar assumption: when stop and target are both inside
        # one candle, the stop is considered first for a short.  This avoids
        # optimistic path assumptions from OHLC-only data.
        if high >= active_stop:
            realized_r += remaining_size * ((entry_price - active_stop) / risk_abs)
            final_exit_price = active_stop
            final_exit_timestamp_ms = candle_ts
            final_exit_reason = "stop_or_trailing_stop_hit"
            remaining_size = 0.0
            break

        if exit_policy == "short_tp1r_close50_trail" and not partial_tp_hit and low <= tp_price:
            partial_size = float(SHORT_PARTIAL_TP_FRACTION)
            realized_r += partial_size * ((entry_price - tp_price) / risk_abs)
            remaining_size = max(0.0, 1.0 - partial_size)
            partial_tp_hit = True
            partial_tp_timestamp_ms = candle_ts
            partial_tp_price = tp_price
            partial_tp_r = partial_size * float(SHORT_PARTIAL_TP_R_MULTIPLE)

        new_stop = _candidate_lower_high_trailing_stop(candles=candles, current_index=index, active_stop=active_stop, latest_close=close)
        if math.isfinite(new_stop) and new_stop < active_stop:
            active_stop = new_stop
            min_active_stop = min(min_active_stop, active_stop)
            trailing_updates += 1
            update_ms = candle_ts + MINUTE_MS
            first_trail_update_ms = first_trail_update_ms or update_ms
            last_trail_update_ms = update_ms

    if remaining_size > 0.0:
        last_valid = _last_valid_candle(candles)
        if last_valid is None:
            return _short_trade_grid_rejection_row(candidate, exit_policy=exit_policy, status="missing_valid_exit_candle", reason="no_valid_ohlc_in_exit_window")
        final_exit_price = _float(last_valid.get("close"))
        final_exit_timestamp_ms = _finite_int_or_none(last_valid.get("timestamp"))
        final_exit_reason = "max_hold_close"
        realized_r += remaining_size * ((entry_price - final_exit_price) / risk_abs)
        remaining_size = 0.0

    mae_r = ((max_high - entry_price) / risk_abs) if math.isfinite(max_high) else float("nan")
    mfe_r = ((entry_price - min_low) / risk_abs) if math.isfinite(min_low) else float("nan")
    hold_minutes = ((int(final_exit_timestamp_ms) - int(entry_ts)) / MINUTE_MS) if final_exit_timestamp_ms is not None else float("nan")
    return _short_trade_grid_row(
        candidate=candidate,
        exit_policy=exit_policy,
        status="simulated_trade",
        rejection_reason="pass",
        entry_price=entry_price,
        initial_stop=initial_stop,
        final_stop=active_stop,
        risk_abs=risk_abs,
        final_exit_timestamp_ms=final_exit_timestamp_ms,
        final_exit_price=final_exit_price,
        final_exit_reason=final_exit_reason,
        hold_minutes=hold_minutes,
        realized_r=realized_r,
        mae_r=mae_r,
        mfe_r=mfe_r,
        partial_tp_hit=partial_tp_hit,
        partial_tp_timestamp_ms=partial_tp_timestamp_ms,
        partial_tp_price=partial_tp_price,
        partial_tp_realized_r=partial_tp_r,
        trailing_updates=trailing_updates,
        first_trail_update_ms=first_trail_update_ms,
        last_trail_update_ms=last_trail_update_ms,
        min_active_stop=min_active_stop,
    )


def _candidate_lower_high_trailing_stop(*, candles: list[dict[str, object]], current_index: int, active_stop: float, latest_close: float) -> float:
    if current_index < int(SHORT_TRAIL_PIVOT_LEFT_BARS) + int(SHORT_TRAIL_PIVOT_RIGHT_BARS):
        return float("nan")
    pivot_index = current_index - int(SHORT_TRAIL_PIVOT_RIGHT_BARS)
    left_index = pivot_index - int(SHORT_TRAIL_PIVOT_LEFT_BARS)
    right_index = pivot_index + int(SHORT_TRAIL_PIVOT_RIGHT_BARS)
    if left_index < 0 or right_index >= len(candles):
        return float("nan")
    pivot_high = _float(candles[pivot_index].get("high"))
    left_high = _float(candles[left_index].get("high"))
    right_high = _float(candles[right_index].get("high"))
    if not all(math.isfinite(value) and value > 0.0 for value in (pivot_high, left_high, right_high, active_stop, latest_close)):
        return float("nan")
    if not (pivot_high >= left_high and pivot_high >= right_high):
        return float("nan")
    proposed_stop = pivot_high * (1.0 + float(SHORT_TRAIL_BUFFER_PCT))
    if proposed_stop >= active_stop:
        return float("nan")
    if proposed_stop <= latest_close:
        return float("nan")
    return float(proposed_stop)


def _last_valid_candle(candles: list[dict[str, object]]) -> dict[str, object] | None:
    for candle in reversed(candles):
        close = _float(candle.get("close"))
        ts = _finite_int_or_none(candle.get("timestamp"))
        if ts is not None and math.isfinite(close) and close > 0.0:
            return candle
    return None


def _short_trade_grid_rejection_row(candidate: dict[str, object], *, exit_policy: str, status: str, reason: str) -> dict[str, object]:
    return _short_trade_grid_row(
        candidate=candidate,
        exit_policy=exit_policy,
        status=status,
        rejection_reason=reason,
        entry_price=_float(candidate.get("entry_price_after_slippage")),
        initial_stop=_float(candidate.get("stop_price")),
        final_stop=float("nan"),
        risk_abs=float("nan"),
        final_exit_timestamp_ms=None,
        final_exit_price=float("nan"),
        final_exit_reason="",
        hold_minutes=float("nan"),
        realized_r=float("nan"),
        mae_r=float("nan"),
        mfe_r=float("nan"),
        partial_tp_hit=False,
        partial_tp_timestamp_ms=None,
        partial_tp_price=float("nan"),
        partial_tp_realized_r=0.0,
        trailing_updates=0,
        first_trail_update_ms=None,
        last_trail_update_ms=None,
        min_active_stop=float("nan"),
    )


def _short_trade_grid_row(
    *,
    candidate: dict[str, object],
    exit_policy: str,
    status: str,
    rejection_reason: str,
    entry_price: float,
    initial_stop: float,
    final_stop: float,
    risk_abs: float,
    final_exit_timestamp_ms: int | None,
    final_exit_price: float,
    final_exit_reason: str,
    hold_minutes: float,
    realized_r: float,
    mae_r: float,
    mfe_r: float,
    partial_tp_hit: bool,
    partial_tp_timestamp_ms: int | None,
    partial_tp_price: float,
    partial_tp_realized_r: float,
    trailing_updates: int,
    first_trail_update_ms: int | None,
    last_trail_update_ms: int | None,
    min_active_stop: float,
) -> dict[str, object]:
    trade_key = "|".join((str(candidate.get("short_candidate_id", "")), str(exit_policy)))
    final_exit_ts = int(final_exit_timestamp_ms) if final_exit_timestamp_ms is not None else np.nan
    partial_ts = int(partial_tp_timestamp_ms) if partial_tp_timestamp_ms is not None else np.nan
    first_trail_ts = int(first_trail_update_ms) if first_trail_update_ms is not None else np.nan
    last_trail_ts = int(last_trail_update_ms) if last_trail_update_ms is not None else np.nan
    return {
        "research_id": RESEARCH_ID,
        "short_trade_id": "pmst_" + hashlib.sha1(trade_key.encode("utf-8")).hexdigest()[:16],
        "short_candidate_id": str(candidate.get("short_candidate_id", "")),
        "selection_key": str(candidate.get("selection_key", "")),
        "test_day_ord": int(candidate.get("test_day_ord", 0) or 0),
        "test_date": str(candidate.get("test_date", "")),
        "train_window_days": int(candidate.get("train_window_days", 0) or 0),
        "train_start_day_ord": int(candidate.get("train_start_day_ord", 0) or 0),
        "train_end_day_ord": int(candidate.get("train_end_day_ord", 0) or 0),
        "selected_rank": int(candidate.get("selected_rank", 0) or 0),
        "basin_id": str(candidate.get("basin_id", "")),
        "center_rule_id": str(candidate.get("center_rule_id", "")),
        "basin_status_at_selection": str(candidate.get("basin_status_at_selection", "")),
        "accepted_mechanism_verdict": str(candidate.get("accepted_mechanism_verdict", "")),
        "event_rank": int(candidate.get("event_rank", 0) or 0),
        "event_id": str(candidate.get("event_id", "")),
        "symbol": str(candidate.get("symbol", "")),
        "session_bucket": str(candidate.get("session_bucket", "")),
        "mechanism_family": str(candidate.get("mechanism_family", "")),
        "mechanism_id": str(candidate.get("mechanism_id", "")),
        "acceptance_regime": str(candidate.get("acceptance_regime", "")),
        "oi_regime_at_feature_cutoff": str(candidate.get("oi_regime_at_feature_cutoff", "")),
        "oi_regime_at_confirm": str(candidate.get("oi_regime_at_confirm", "")),
        "signal_family": str(candidate.get("signal_family", "")),
        "exit_policy": str(exit_policy),
        "trade_grid_status": status,
        "trade_grid_rejection_reason": rejection_reason,
        "entry_timestamp_ms": _finite_int_or_none(candidate.get("entry_timestamp_ms")) or np.nan,
        "entry_time_utc": str(candidate.get("entry_time_utc", "")),
        "entry_price_after_slippage": entry_price,
        "entry_adverse_slippage_bps": _float(candidate.get("entry_adverse_slippage_bps")),
        "initial_stop_price": initial_stop,
        "final_stop_price": final_stop,
        "risk_abs": risk_abs,
        "entry_to_stop_risk_pct": _float(candidate.get("entry_to_stop_risk_pct")),
        "final_exit_timestamp_ms": final_exit_ts,
        "final_exit_time_utc": _fmt_optional_ts(final_exit_ts),
        "final_exit_price": final_exit_price,
        "final_exit_reason": final_exit_reason,
        "hold_minutes": hold_minutes,
        "realized_r": realized_r,
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "win": bool(math.isfinite(realized_r) and realized_r > 0.0),
        "loss": bool(math.isfinite(realized_r) and realized_r < 0.0),
        "partial_tp_hit": bool(partial_tp_hit),
        "partial_tp_r_multiple": float(SHORT_PARTIAL_TP_R_MULTIPLE) if exit_policy == "short_tp1r_close50_trail" else np.nan,
        "partial_tp_fraction": float(SHORT_PARTIAL_TP_FRACTION) if exit_policy == "short_tp1r_close50_trail" else np.nan,
        "partial_tp_timestamp_ms": partial_ts,
        "partial_tp_time_utc": _fmt_optional_ts(partial_ts),
        "partial_tp_price": partial_tp_price,
        "partial_tp_realized_r": partial_tp_realized_r,
        "trailing_updates": int(trailing_updates),
        "first_trail_update_timestamp_ms": first_trail_ts,
        "first_trail_update_time_utc": _fmt_optional_ts(first_trail_ts),
        "last_trail_update_timestamp_ms": last_trail_ts,
        "last_trail_update_time_utc": _fmt_optional_ts(last_trail_ts),
        "min_active_stop_price": min_active_stop,
        "max_hold_minutes": int(SHORT_EXIT_MAX_HOLD_MINUTES),
        "trail_pivot_left_bars": int(SHORT_TRAIL_PIVOT_LEFT_BARS),
        "trail_pivot_right_bars": int(SHORT_TRAIL_PIVOT_RIGHT_BARS),
        "trail_buffer_pct": float(SHORT_TRAIL_BUFFER_PCT),
        "exit_policy_model": SHORT_TRADE_GRID_MODEL,
        "short_mapping_model": str(candidate.get("short_mapping_model", SHORT_MAPPING_MODEL)),
        "short_confirm_model": str(candidate.get("short_confirm_model", SHORT_CONFIRM_MODEL)),
        "entry_model": SHORT_ENTRY_MODEL,
        "stop_model": SHORT_STOP_MODEL,
        "oi_model": SHORT_OI_MODEL,
        "data_access_model": DATA_ACCESS_MODEL,
        "future_label_available_at_entry": False,
        "short_confirm_closed_before_entry": _to_bool(candidate.get("short_confirm_closed_before_entry")),
        "allowed_for_exit_grid_required": True,
        "audit_only_no_oi_excluded": True,
        "uses_future_optimal_exit": False,
        "uses_final_holdout_tuning": False,
    }


def _short_trade_grid_columns() -> list[str]:
    return [
        "research_id", "short_trade_id", "short_candidate_id", "selection_key", "test_day_ord", "test_date", "train_window_days",
        "train_start_day_ord", "train_end_day_ord", "selected_rank", "basin_id", "center_rule_id", "basin_status_at_selection",
        "accepted_mechanism_verdict", "event_rank", "event_id", "symbol", "session_bucket", "mechanism_family", "mechanism_id", "acceptance_regime",
        "oi_regime_at_feature_cutoff", "oi_regime_at_confirm", "signal_family", "exit_policy", "trade_grid_status",
        "trade_grid_rejection_reason", "entry_timestamp_ms", "entry_time_utc", "entry_price_after_slippage", "entry_adverse_slippage_bps",
        "initial_stop_price", "final_stop_price", "risk_abs", "entry_to_stop_risk_pct", "final_exit_timestamp_ms", "final_exit_time_utc",
        "final_exit_price", "final_exit_reason", "hold_minutes", "realized_r", "mae_r", "mfe_r", "win", "loss", "partial_tp_hit",
        "partial_tp_r_multiple", "partial_tp_fraction", "partial_tp_timestamp_ms", "partial_tp_time_utc", "partial_tp_price",
        "partial_tp_realized_r", "trailing_updates", "first_trail_update_timestamp_ms", "first_trail_update_time_utc",
        "last_trail_update_timestamp_ms", "last_trail_update_time_utc", "min_active_stop_price", "max_hold_minutes", "trail_pivot_left_bars",
        "trail_pivot_right_bars", "trail_buffer_pct", "exit_policy_model", "short_mapping_model", "short_confirm_model", "entry_model",
        "stop_model", "oi_model", "data_access_model", "future_label_available_at_entry", "short_confirm_closed_before_entry",
        "allowed_for_exit_grid_required", "audit_only_no_oi_excluded", "uses_future_optimal_exit", "uses_final_holdout_tuning",
    ]



def _build_short_trade_diagnostics(*, short_trade_grid: pd.DataFrame) -> pd.DataFrame:
    """Aggregate simulated short trades by stability slices.

    This is a downstream diagnostic layer.  It never changes signals, exits, or
    daily mechanism selection.  It reads realized R from the already-fixed trade
    grid only to answer whether the accepted mechanism sleeves survived the
    honest execution layer by session/family/exit/time splits.
    """

    columns = _short_trade_diagnostics_columns()
    trades = _simulated_short_trades(short_trade_grid)
    if trades.empty:
        return pd.DataFrame(columns=columns)
    work = _add_trade_split_columns(trades)
    rows: list[dict[str, object]] = []
    for axis in (
        "all",
        "trading_sleeve_key",
        "selection_key",
        "exit_policy",
        "signal_family",
        "session_bucket",
        "mechanism_family",
        "mechanism_id",
        "acceptance_regime",
        "oi_regime_at_confirm",
        "train_window_days",
        "month",
        "week",
        "odd_even_day",
        "year_half",
        "symbol",
    ):
        rows.extend(_short_trade_split_rows(work, axis=axis))
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["split_axis", "split_value"])


def _build_short_trade_top_removal(*, short_trade_grid: pd.DataFrame) -> pd.DataFrame:
    columns = _short_trade_top_removal_columns()
    trades = _simulated_short_trades(short_trade_grid)
    if trades.empty:
        return pd.DataFrame(columns=columns)
    work = _add_trade_split_columns(trades)
    groups: list[tuple[str, str, pd.DataFrame]] = [("all", "all", work)]
    for axis in ("trading_sleeve_key", "selection_key", "exit_policy", "signal_family", "session_bucket", "mechanism_family"):
        if axis in work.columns:
            groups.extend((axis, str(value), group) for value, group in work.groupby(axis, dropna=False, sort=True))
    rows: list[dict[str, object]] = []
    for group_axis, group_value, group in groups:
        rows.extend(_short_trade_top_removal_rows(group_axis=group_axis, group_value=group_value, group=group, removal_kind="top_trades"))
        rows.extend(_short_trade_top_removal_rows(group_axis=group_axis, group_value=group_value, group=group, removal_kind="top_symbols"))
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["group_axis", "group_value", "removal_kind", "remove_fraction"])


def _build_short_trade_verdict(*, short_trade_grid: pd.DataFrame, short_trade_top_removal: pd.DataFrame) -> pd.DataFrame:
    """Produce explicit pass/fail verdicts for executed trading sleeves."""

    columns = _short_trade_verdict_columns()
    trades = _simulated_short_trades(short_trade_grid)
    if trades.empty:
        return pd.DataFrame(columns=columns)
    work = _add_trade_split_columns(trades)
    if "trading_sleeve_key" not in work.columns:
        return pd.DataFrame(columns=columns)
    top_lookup = _short_trade_top_removal_lookup(short_trade_top_removal)
    rows: list[dict[str, object]] = []
    for sleeve_key, group in work.groupby("trading_sleeve_key", dropna=False, sort=True):
        stats = _short_trade_stats(group)
        top_trade_retention, top_trade_positive = top_lookup.get((str(sleeve_key), "top_trades"), (float("nan"), False))
        top_symbol_retention, top_symbol_positive = top_lookup.get((str(sleeve_key), "top_symbols"), (float("nan"), False))
        first_half_r, second_half_r = _trade_half_r_sums(group)
        odd_r, even_r = _trade_odd_even_r_sums(group)
        month_positive_share = _trade_month_positive_r_share(group)
        fail_reasons: list[str] = []
        warning_reasons: list[str] = []
        if stats["trades"] < SHORT_TRADE_VERDICT_MIN_TRADES:
            fail_reasons.append("low_trade_count")
        if stats["active_days"] < SHORT_TRADE_VERDICT_MIN_ACTIVE_DAYS:
            fail_reasons.append("low_active_days")
        if stats["symbols"] < SHORT_TRADE_VERDICT_MIN_SYMBOLS:
            fail_reasons.append("low_symbol_breadth")
        if stats["sessions"] < SHORT_TRADE_VERDICT_MIN_SESSIONS:
            fail_reasons.append("low_session_breadth")
        if not math.isfinite(stats["sum_r"]) or stats["sum_r"] <= SHORT_TRADE_VERDICT_MIN_SUM_R:
            fail_reasons.append("non_positive_sum_r")
        if not math.isfinite(stats["avg_r"]) or stats["avg_r"] <= SHORT_TRADE_VERDICT_MIN_AVG_R:
            fail_reasons.append("non_positive_avg_r")
        if not math.isfinite(stats["median_r"]) or stats["median_r"] <= SHORT_TRADE_VERDICT_MIN_MEDIAN_R:
            fail_reasons.append("non_positive_median_r")
        if not math.isfinite(stats["positive_active_day_rate"]) or stats["positive_active_day_rate"] < SHORT_TRADE_VERDICT_MIN_POSITIVE_ACTIVE_DAY_RATE:
            fail_reasons.append("low_positive_active_day_rate")
        if not math.isfinite(stats["top_symbol_trade_share"]) or stats["top_symbol_trade_share"] > SHORT_TRADE_VERDICT_MAX_TOP_SYMBOL_TRADE_SHARE:
            fail_reasons.append("top_symbol_trade_dependency")
        if not math.isfinite(stats["top_day_trade_share"]) or stats["top_day_trade_share"] > SHORT_TRADE_VERDICT_MAX_TOP_DAY_TRADE_SHARE:
            fail_reasons.append("top_day_trade_dependency")
        if not math.isfinite(month_positive_share) or month_positive_share > SHORT_TRADE_VERDICT_MAX_MONTH_POSITIVE_R_SHARE:
            fail_reasons.append("month_positive_r_concentration")
        if not bool(top_trade_positive):
            fail_reasons.append("top_trade_removal_not_positive")
        if not bool(top_symbol_positive):
            fail_reasons.append("top_symbol_removal_not_positive")
        if math.isfinite(first_half_r) and math.isfinite(second_half_r) and first_half_r > 0.0 and second_half_r <= 0.0:
            fail_reasons.append("second_half_degraded_to_non_positive")
        if math.isfinite(odd_r) and math.isfinite(even_r) and (odd_r <= 0.0 or even_r <= 0.0):
            warning_reasons.append("odd_even_split_not_both_positive")
        if math.isfinite(stats["max_drawdown_r"]) and math.isfinite(stats["sum_r"]) and stats["sum_r"] > 0.0:
            dd_to_profit = stats["max_drawdown_r"] / max(stats["sum_r"], 1e-12)
            if dd_to_profit > 1.50:
                warning_reasons.append("drawdown_large_vs_profit")
        else:
            dd_to_profit = float("nan")

        if not fail_reasons:
            verdict = "accepted_trading_sleeve"
            allowed = True
            next_stage = "portfolio_aggregation_candidate"
        elif stats["trades"] >= SHORT_TRADE_VERDICT_MIN_TRADES and math.isfinite(stats["sum_r"]) and stats["sum_r"] > 0.0:
            verdict = "watchlist_trading_sleeve"
            allowed = False
            next_stage = "trade_research_only"
        else:
            verdict = "rejected_trading_sleeve"
            allowed = False
            next_stage = "trade_research_only"

        rows.append(
            {
                "research_id": RESEARCH_ID,
                "trading_sleeve_key": str(sleeve_key),
                "trading_sleeve_verdict": verdict,
                "allowed_for_portfolio_aggregation": allowed,
                "next_allowed_stage": next_stage,
                "fail_reasons": ";".join(fail_reasons) if fail_reasons else "pass",
                "warning_reasons": ";".join(warning_reasons),
                **stats,
                "first_trade_date": _safe_min_str(group.get("test_date", pd.Series(dtype=str))),
                "last_trade_date": _safe_max_str(group.get("test_date", pd.Series(dtype=str))),
                "selection_keys_seen": _joined_unique(group, "selection_key"),
                "exit_policies_seen": _joined_unique(group, "exit_policy"),
                "signal_families_seen": _joined_unique(group, "signal_family"),
                "mechanism_families_seen": _joined_unique(group, "mechanism_family"),
                "sessions_seen": _joined_unique(group, "session_bucket"),
                "month_positive_r_share": month_positive_share,
                "first_half_sum_r": first_half_r,
                "second_half_sum_r": second_half_r,
                "odd_day_sum_r": odd_r,
                "even_day_sum_r": even_r,
                "drawdown_to_profit_ratio": dd_to_profit,
                "top_trade_removal_10pct_r_retention_rate": top_trade_retention,
                "top_trade_removal_10pct_remaining_positive": bool(top_trade_positive),
                "top_symbol_removal_10pct_r_retention_rate": top_symbol_retention,
                "top_symbol_removal_10pct_remaining_positive": bool(top_symbol_positive),
                "min_trades_gate": int(SHORT_TRADE_VERDICT_MIN_TRADES),
                "min_active_days_gate": int(SHORT_TRADE_VERDICT_MIN_ACTIVE_DAYS),
                "min_symbols_gate": int(SHORT_TRADE_VERDICT_MIN_SYMBOLS),
                "min_sessions_gate": int(SHORT_TRADE_VERDICT_MIN_SESSIONS),
                "min_sum_r_gate": float(SHORT_TRADE_VERDICT_MIN_SUM_R),
                "min_avg_r_gate": float(SHORT_TRADE_VERDICT_MIN_AVG_R),
                "min_median_r_gate": float(SHORT_TRADE_VERDICT_MIN_MEDIAN_R),
                "min_positive_active_day_rate_gate": float(SHORT_TRADE_VERDICT_MIN_POSITIVE_ACTIVE_DAY_RATE),
                "max_top_symbol_trade_share_gate": float(SHORT_TRADE_VERDICT_MAX_TOP_SYMBOL_TRADE_SHARE),
                "max_top_day_trade_share_gate": float(SHORT_TRADE_VERDICT_MAX_TOP_DAY_TRADE_SHARE),
                "max_month_positive_r_share_gate": float(SHORT_TRADE_VERDICT_MAX_MONTH_POSITIVE_R_SHARE),
                "top_removal_fraction_gate": float(SHORT_TRADE_TOP_REMOVAL_FRACTION),
                "short_trade_verdict_model": SHORT_TRADE_VERDICT_MODEL,
                "uses_final_holdout_tuning": False,
                "uses_future_optimal_exit": False,
                "source_mechanism_verdict_required": SHORT_ALLOWED_VERDICT,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["trading_sleeve_verdict", "sum_r", "trading_sleeve_key"])


def _build_short_trade_guard(
    *,
    short_trade_candidates: pd.DataFrame,
    short_trade_grid: pd.DataFrame,
    oos_verdict: pd.DataFrame,
) -> pd.DataFrame:
    """Audit that the trade grid is sourced only from accepted non-audit candidates."""

    columns = _short_trade_guard_columns()
    grid = short_trade_grid.copy() if short_trade_grid is not None else pd.DataFrame()
    candidates = short_trade_candidates.copy() if short_trade_candidates is not None else pd.DataFrame()
    verdict = oos_verdict.copy() if oos_verdict is not None else pd.DataFrame()
    simulated = grid.loc[grid.get("trade_grid_status", pd.Series(dtype=str)).astype(str) == "simulated_trade"].copy() if not grid.empty else pd.DataFrame()
    rows: list[dict[str, object]] = []

    def add_guard(name: str, failures: int, checked: int, description: str) -> None:
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "guard_name": name,
                "guard_status": "pass" if int(failures) == 0 else "fail",
                "checked_rows": int(checked),
                "failure_rows": int(failures),
                "description": description,
                "short_trade_guard_model": SHORT_TRADE_GUARD_MODEL,
                "uses_final_holdout_tuning": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )

    add_guard(
        "no_audit_only_no_oi_in_trade_grid",
        int((simulated.get("signal_family", pd.Series(dtype=str)).astype(str) == "no_oi_confirmation").sum()) if not simulated.empty else 0,
        int(len(simulated)),
        "simulated trades must exclude audit-only no_oi_confirmation candidates",
    )
    add_guard(
        "all_trade_grid_rows_from_accepted_mechanisms",
        int((simulated.get("accepted_mechanism_verdict", simulated.get("source_mechanism_verdict_required", pd.Series(dtype=str))).astype(str) != SHORT_ALLOWED_VERDICT).sum()) if not simulated.empty else 0,
        int(len(simulated)),
        "simulated trades must come from accepted_mechanism rows only",
    )
    add_guard(
        "all_trade_grid_rows_have_no_future_optimal_exit",
        int(simulated.get("uses_future_optimal_exit", pd.Series(False, index=simulated.index)).map(_to_bool).sum()) if not simulated.empty else 0,
        int(len(simulated)),
        "exit grid must never use future-optimal exits",
    )
    add_guard(
        "all_trade_grid_rows_enter_after_closed_confirm",
        int((~simulated.get("short_confirm_closed_before_entry", pd.Series(False, index=simulated.index)).map(_to_bool)).sum()) if not simulated.empty else 0,
        int(len(simulated)),
        "entry must occur only after a closed 1m confirm candle",
    )
    add_guard(
        "all_trade_grid_rows_use_next_open_entry_model",
        int((simulated.get("entry_model", pd.Series(dtype=str)).astype(str) != SHORT_ENTRY_MODEL).sum()) if not simulated.empty else 0,
        int(len(simulated)),
        "entry model must be next 1m open after confirm plus adverse slippage",
    )
    add_guard(
        "all_trade_grid_rows_use_closed_5m_oi_model",
        int((simulated.get("oi_model", pd.Series(dtype=str)).astype(str) != SHORT_OI_MODEL).sum()) if not simulated.empty else 0,
        int(len(simulated)),
        "OI model must be closed 5m OI as-of confirm close",
    )
    if not candidates.empty:
        allowed = candidates.get("allowed_for_exit_grid", pd.Series(False, index=candidates.index)).map(_to_bool)
        confirmed = candidates.get("short_mapping_status", pd.Series(dtype=str)).astype(str).eq("confirmed_short_candidate")
        bad_candidates = candidates.loc[allowed & confirmed & candidates.get("signal_family", pd.Series(dtype=str)).astype(str).eq("no_oi_confirmation")]
        add_guard(
            "no_audit_only_candidate_marked_for_exit_grid",
            int(len(bad_candidates)),
            int(len(candidates)),
            "candidate layer must not mark no_oi_confirmation as allowed_for_exit_grid",
        )
    else:
        add_guard("no_audit_only_candidate_marked_for_exit_grid", 0, 0, "candidate layer absent or empty")
    if not verdict.empty and "allowed_for_short_mapping" in verdict.columns:
        accepted = verdict.loc[verdict["allowed_for_short_mapping"].map(_to_bool)].copy()
        bad_verdict = accepted.loc[accepted.get("oos_verdict", pd.Series(dtype=str)).astype(str) != SHORT_ALLOWED_VERDICT]
        add_guard(
            "short_mapping_allowed_only_for_accepted_mechanisms",
            int(len(bad_verdict)),
            int(len(accepted)),
            "OOS verdict must allow short mapping only for accepted mechanisms",
        )
    else:
        add_guard("short_mapping_allowed_only_for_accepted_mechanisms", 0, 0, "OOS verdict absent or empty")
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["guard_status", "guard_name"])


def _simulated_short_trades(short_trade_grid: pd.DataFrame) -> pd.DataFrame:
    columns = _short_trade_grid_columns()
    if short_trade_grid is None or short_trade_grid.empty:
        return pd.DataFrame(columns=columns)
    frame = short_trade_grid.copy()
    if "trade_grid_status" not in frame.columns:
        return pd.DataFrame(columns=columns)
    frame = frame.loc[frame["trade_grid_status"].astype(str) == "simulated_trade"].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["realized_r"] = pd.to_numeric(frame.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan)
    frame = frame.loc[frame["realized_r"].notna()].copy()
    return frame


def _add_trade_split_columns(trades: pd.DataFrame) -> pd.DataFrame:
    work = trades.copy()
    work["month"] = work.get("test_date", pd.Series(dtype=str)).astype(str).str.slice(0, 7)
    parsed_date = pd.to_datetime(work.get("test_date", pd.Series(dtype=str)), errors="coerce", utc=True)
    iso = parsed_date.dt.isocalendar()
    work["week"] = np.where(parsed_date.notna(), iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2), "")
    day_ord = pd.to_numeric(work.get("test_day_ord", pd.Series(dtype=float)), errors="coerce")
    work["odd_even_day"] = np.where((day_ord.fillna(0).astype("int64") % 2) == 0, "even", "odd")
    finite_day_ord = day_ord.dropna()
    midpoint = float(finite_day_ord.median()) if not finite_day_ord.empty else float("nan")
    work["year_half"] = np.where(day_ord <= midpoint, "first_half", "second_half") if math.isfinite(midpoint) else "unknown"
    work["trading_sleeve_key"] = (
        work.get("selection_key", pd.Series(dtype=str)).astype(str)
        + "|"
        + work.get("signal_family", pd.Series(dtype=str)).astype(str)
        + "|"
        + work.get("exit_policy", pd.Series(dtype=str)).astype(str)
    )
    return work


def _short_trade_split_rows(frame: pd.DataFrame, *, axis: str) -> list[dict[str, object]]:
    if frame.empty:
        return []
    if axis == "all":
        groups = [("all", frame)]
    elif axis not in frame.columns:
        return []
    else:
        groups = [(str(value), group) for value, group in frame.groupby(axis, dropna=False, sort=True)]
    rows: list[dict[str, object]] = []
    for value, group in groups:
        stats = _short_trade_stats(group)
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "split_axis": axis,
                "split_value": value,
                **stats,
                "short_trade_diagnostics_model": SHORT_TRADE_DIAGNOSTICS_MODEL,
                "uses_final_holdout_tuning": False,
                "uses_future_optimal_exit": False,
                "source_mechanism_verdict_required": SHORT_ALLOWED_VERDICT,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    return rows


def _short_trade_stats(group: pd.DataFrame) -> dict[str, object]:
    r = pd.to_numeric(group.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    day_r = _trade_day_r(group)
    return {
        "trades": int(len(r)),
        "active_days": int(day_r.shape[0]),
        "symbols": int(group["symbol"].nunique()) if "symbol" in group.columns else 0,
        "sessions": int(group["session_bucket"].nunique()) if "session_bucket" in group.columns else 0,
        "selection_keys": int(group["selection_key"].nunique()) if "selection_key" in group.columns else 0,
        "sum_r": _safe_sum(r),
        "avg_r": _safe_mean(r),
        "median_r": _safe_median(r),
        "win_rate": _rate(r > 0.0) if not r.empty else float("nan"),
        "loss_rate": _rate(r < 0.0) if not r.empty else float("nan"),
        "positive_active_days": int((day_r > 0.0).sum()) if not day_r.empty else 0,
        "positive_active_day_rate": _rate(day_r > 0.0) if not day_r.empty else float("nan"),
        "max_drawdown_r": _max_drawdown_from_returns(day_r),
        "avg_hold_minutes": _safe_mean(group.get("hold_minutes", pd.Series(dtype=float))),
        "median_hold_minutes": _safe_median(group.get("hold_minutes", pd.Series(dtype=float))),
        "median_mae_r": _safe_median(group.get("mae_r", pd.Series(dtype=float))),
        "median_mfe_r": _safe_median(group.get("mfe_r", pd.Series(dtype=float))),
        "partial_tp_hit_rate": _rate(group.get("partial_tp_hit", pd.Series(dtype=bool)).map(_to_bool)) if "partial_tp_hit" in group.columns else float("nan"),
        "stop_exit_rate": _rate(group.get("final_exit_reason", pd.Series(dtype=str)).astype(str).eq("stop_or_trailing_stop_hit")) if "final_exit_reason" in group.columns else float("nan"),
        "max_hold_exit_rate": _rate(group.get("final_exit_reason", pd.Series(dtype=str)).astype(str).eq("max_hold_close")) if "final_exit_reason" in group.columns else float("nan"),
        "top_symbol_trade_share": _top_share(group, column="symbol"),
        "top_day_trade_share": _top_share(group, column="test_day_ord"),
    }


def _trade_day_r(group: pd.DataFrame) -> pd.Series:
    if group.empty or "test_day_ord" not in group.columns:
        return pd.Series(dtype=float)
    work = group.copy()
    work["_r"] = pd.to_numeric(work.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    day = pd.to_numeric(work.get("test_day_ord", pd.Series(dtype=float)), errors="coerce")
    work = work.loc[day.notna()].copy()
    if work.empty:
        return pd.Series(dtype=float)
    work["_day"] = day.loc[work.index].astype("int64")
    return work.groupby("_day", dropna=False)["_r"].sum().sort_index()


def _max_drawdown_from_returns(returns: pd.Series) -> float:
    values = pd.to_numeric(returns, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if values.empty:
        return float("nan")
    cumulative = values.cumsum()
    drawdown = cumulative - cumulative.cummax()
    return float(abs(drawdown.min())) if not drawdown.empty else float("nan")


def _short_trade_top_removal_rows(*, group_axis: str, group_value: str, group: pd.DataFrame, removal_kind: str) -> list[dict[str, object]]:
    r = pd.to_numeric(group.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    base_sum_r = float(r.sum())
    if removal_kind == "top_symbols" and "symbol" in group.columns:
        ranked = group.assign(_r=r).groupby("symbol", dropna=False)["_r"].sum().sort_values(ascending=False)
        unit_count = int(len(ranked))
    else:
        ranked = r.sort_values(ascending=False)
        unit_count = int(len(ranked))
    rows: list[dict[str, object]] = []
    for fraction in (0.0, 0.01, 0.05, 0.10, 0.20):
        remove_count = _removal_count(unit_count, float(fraction))
        if removal_kind == "top_symbols" and "symbol" in group.columns:
            removed_symbols = set(ranked.head(remove_count).index.astype(str)) if remove_count > 0 else set()
            remaining = group.loc[~group["symbol"].astype(str).isin(removed_symbols)].copy()
        else:
            removed_index = set(ranked.head(remove_count).index) if remove_count > 0 else set()
            remaining = group.loc[~group.index.isin(removed_index)].copy()
        remaining_r = pd.to_numeric(remaining.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        remaining_sum_r = float(remaining_r.sum())
        rows.append(
            {
                "research_id": RESEARCH_ID,
                "group_axis": group_axis,
                "group_value": group_value,
                "removal_kind": removal_kind,
                "remove_fraction": float(fraction),
                "base_trades": int(len(group)),
                "base_symbols": int(group["symbol"].nunique()) if "symbol" in group.columns else 0,
                "base_active_days": int(_trade_day_r(group).shape[0]),
                "base_sum_r": base_sum_r,
                "removed_units": int(remove_count),
                "remaining_trades": int(len(remaining)),
                "remaining_symbols": int(remaining["symbol"].nunique()) if "symbol" in remaining.columns and not remaining.empty else 0,
                "remaining_active_days": int(_trade_day_r(remaining).shape[0]) if not remaining.empty else 0,
                "remaining_sum_r": remaining_sum_r,
                "r_retention_rate": float(remaining_sum_r / base_sum_r) if abs(base_sum_r) > 1e-12 else float("nan"),
                "remaining_sum_r_positive": bool(remaining_sum_r > 0.0),
                "short_trade_top_removal_model": SHORT_TRADE_TOP_REMOVAL_MODEL,
                "uses_final_holdout_tuning": False,
                "uses_future_optimal_exit": False,
                "data_access_model": DATA_ACCESS_MODEL,
            }
        )
    return rows


def _short_trade_top_removal_lookup(top_removal: pd.DataFrame) -> dict[tuple[str, str], tuple[float, bool]]:
    if top_removal is None or top_removal.empty:
        return {}
    frame = top_removal.loc[top_removal.get("group_axis", pd.Series(dtype=str)).astype(str) == "trading_sleeve_key"].copy()
    if frame.empty:
        return {}
    fraction = pd.to_numeric(frame.get("remove_fraction", pd.Series(dtype=float)), errors="coerce")
    frame = frame.loc[(fraction - float(SHORT_TRADE_TOP_REMOVAL_FRACTION)).abs() < 1e-12].copy()
    lookup: dict[tuple[str, str], tuple[float, bool]] = {}
    for row in frame.to_dict("records"):
        lookup[(str(row.get("group_value", "")), str(row.get("removal_kind", "")))] = (
            _float(row.get("r_retention_rate")),
            _to_bool(row.get("remaining_sum_r_positive")),
        )
    return lookup


def _trade_month_positive_r_share(trades: pd.DataFrame) -> float:
    if trades.empty or "month" not in trades.columns:
        return float("nan")
    r = pd.to_numeric(trades.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    grouped = r.groupby(trades["month"].astype(str)).sum()
    positive = grouped.loc[grouped > 0.0]
    total_positive = float(positive.sum())
    if total_positive <= 0.0 or positive.empty:
        return float("nan")
    return float(positive.max() / total_positive)


def _trade_half_r_sums(trades: pd.DataFrame) -> tuple[float, float]:
    if trades.empty or "year_half" not in trades.columns:
        return (float("nan"), float("nan"))
    r = pd.to_numeric(trades.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    grouped = r.groupby(trades["year_half"].astype(str)).sum()
    return (float(grouped.get("first_half", np.nan)), float(grouped.get("second_half", np.nan)))


def _trade_odd_even_r_sums(trades: pd.DataFrame) -> tuple[float, float]:
    if trades.empty or "odd_even_day" not in trades.columns:
        return (float("nan"), float("nan"))
    r = pd.to_numeric(trades.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    grouped = r.groupby(trades["odd_even_day"].astype(str)).sum()
    return (float(grouped.get("odd", np.nan)), float(grouped.get("even", np.nan)))


def _joined_unique(frame: pd.DataFrame, column: str, *, limit: int = 20) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    values = [str(value) for value in frame[column].dropna().astype(str).unique().tolist() if str(value)]
    values = sorted(values)
    if len(values) > limit:
        return ",".join(values[:limit]) + f",...(+{len(values) - limit})"
    return ",".join(values)


def _safe_min_str(series: pd.Series) -> str:
    values = series.dropna().astype(str) if series is not None else pd.Series(dtype=str)
    values = values.loc[values != ""]
    return str(values.min()) if not values.empty else ""


def _safe_max_str(series: pd.Series) -> str:
    values = series.dropna().astype(str) if series is not None else pd.Series(dtype=str)
    values = values.loc[values != ""]
    return str(values.max()) if not values.empty else ""


def _short_trade_diagnostics_columns() -> list[str]:
    return [
        "research_id", "split_axis", "split_value", "trades", "active_days", "symbols", "sessions", "selection_keys",
        "sum_r", "avg_r", "median_r", "win_rate", "loss_rate", "positive_active_days", "positive_active_day_rate",
        "max_drawdown_r", "avg_hold_minutes", "median_hold_minutes", "median_mae_r", "median_mfe_r", "partial_tp_hit_rate",
        "stop_exit_rate", "max_hold_exit_rate", "top_symbol_trade_share", "top_day_trade_share", "short_trade_diagnostics_model",
        "uses_final_holdout_tuning", "uses_future_optimal_exit", "source_mechanism_verdict_required", "data_access_model",
    ]


def _short_trade_top_removal_columns() -> list[str]:
    return [
        "research_id", "group_axis", "group_value", "removal_kind", "remove_fraction", "base_trades", "base_symbols",
        "base_active_days", "base_sum_r", "removed_units", "remaining_trades", "remaining_symbols", "remaining_active_days",
        "remaining_sum_r", "r_retention_rate", "remaining_sum_r_positive", "short_trade_top_removal_model",
        "uses_final_holdout_tuning", "uses_future_optimal_exit", "data_access_model",
    ]


def _short_trade_verdict_columns() -> list[str]:
    return [
        "research_id", "trading_sleeve_key", "trading_sleeve_verdict", "allowed_for_portfolio_aggregation", "next_allowed_stage",
        "fail_reasons", "warning_reasons", "trades", "active_days", "symbols", "sessions", "selection_keys", "sum_r", "avg_r",
        "median_r", "win_rate", "loss_rate", "positive_active_days", "positive_active_day_rate", "max_drawdown_r",
        "avg_hold_minutes", "median_hold_minutes", "median_mae_r", "median_mfe_r", "partial_tp_hit_rate", "stop_exit_rate",
        "max_hold_exit_rate", "top_symbol_trade_share", "top_day_trade_share", "first_trade_date", "last_trade_date",
        "selection_keys_seen", "exit_policies_seen", "signal_families_seen", "mechanism_families_seen", "sessions_seen",
        "month_positive_r_share", "first_half_sum_r", "second_half_sum_r", "odd_day_sum_r", "even_day_sum_r",
        "drawdown_to_profit_ratio", "top_trade_removal_10pct_r_retention_rate", "top_trade_removal_10pct_remaining_positive",
        "top_symbol_removal_10pct_r_retention_rate", "top_symbol_removal_10pct_remaining_positive", "min_trades_gate",
        "min_active_days_gate", "min_symbols_gate", "min_sessions_gate", "min_sum_r_gate", "min_avg_r_gate", "min_median_r_gate",
        "min_positive_active_day_rate_gate", "max_top_symbol_trade_share_gate", "max_top_day_trade_share_gate",
        "max_month_positive_r_share_gate", "top_removal_fraction_gate", "short_trade_verdict_model", "uses_final_holdout_tuning",
        "uses_future_optimal_exit", "source_mechanism_verdict_required", "data_access_model",
    ]


def _short_trade_guard_columns() -> list[str]:
    return [
        "research_id", "guard_name", "guard_status", "checked_rows", "failure_rows", "description",
        "short_trade_guard_model", "uses_final_holdout_tuning", "data_access_model",
    ]



def _build_short_portfolio_candidates(*, short_trade_grid: pd.DataFrame, short_trade_verdict: pd.DataFrame) -> pd.DataFrame:
    """Create portfolio candidates only from accepted trading sleeves.

    This layer does not rediscover signals or exits.  It filters the already
    simulated short trade grid through the explicit trading-sleeve verdict and
    prepares deterministic ranks for a chronological portfolio replay.
    """

    columns = _short_portfolio_candidate_columns()
    trades = _simulated_short_trades(short_trade_grid)
    if trades.empty or short_trade_verdict is None or short_trade_verdict.empty:
        return pd.DataFrame(columns=columns)

    verdict = short_trade_verdict.copy()
    allowed = verdict.loc[
        verdict.get("trading_sleeve_verdict", pd.Series(dtype=str)).astype(str).eq("accepted_trading_sleeve")
        & verdict.get("allowed_for_portfolio_aggregation", pd.Series(dtype=bool)).map(_to_bool)
    ].copy()
    if allowed.empty or "trading_sleeve_key" not in allowed.columns:
        return pd.DataFrame(columns=columns)

    work = _add_trade_split_columns(trades)
    work = work.loc[work["trading_sleeve_key"].astype(str).isin(set(allowed["trading_sleeve_key"].astype(str)))].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    verdict_cols = [
        "trading_sleeve_key", "trading_sleeve_verdict", "allowed_for_portfolio_aggregation", "sum_r", "avg_r",
        "median_r", "positive_active_day_rate", "max_drawdown_r", "top_symbol_trade_share", "top_day_trade_share",
    ]
    available_verdict_cols = [column for column in verdict_cols if column in allowed.columns]
    allowed_small = allowed[available_verdict_cols].copy().rename(
        columns={
            "sum_r": "sleeve_sum_r",
            "avg_r": "sleeve_avg_r",
            "median_r": "sleeve_median_r",
            "positive_active_day_rate": "sleeve_positive_active_day_rate",
            "max_drawdown_r": "sleeve_max_drawdown_r",
            "top_symbol_trade_share": "sleeve_top_symbol_trade_share",
            "top_day_trade_share": "sleeve_top_day_trade_share",
        }
    )
    work = work.merge(allowed_small, on="trading_sleeve_key", how="left", validate="many_to_one")
    for column in ("sleeve_sum_r", "sleeve_avg_r", "sleeve_median_r", "sleeve_positive_active_day_rate", "sleeve_max_drawdown_r"):
        work[column] = pd.to_numeric(work.get(column, pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan)
    work["portfolio_rank_score"] = (
        work["sleeve_median_r"].fillna(0.0)
        + 0.25 * work["sleeve_avg_r"].fillna(0.0)
        + 0.10 * work["sleeve_positive_active_day_rate"].fillna(0.0)
        - 0.10 * work["sleeve_max_drawdown_r"].fillna(0.0)
    )
    work["portfolio_candidate_status"] = "eligible_portfolio_candidate"
    work["portfolio_candidate_rejection_reason"] = ""
    work["portfolio_candidate_model"] = SHORT_PORTFOLIO_CANDIDATE_MODEL
    work["portfolio_uses_only_accepted_trading_sleeves"] = True
    work["uses_final_holdout_tuning"] = False
    work["uses_future_optimal_exit"] = False
    work["data_access_model"] = DATA_ACCESS_MODEL
    work["portfolio_candidate_id"] = [
        "pmspc_" + hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]
        for value in work.get("short_trade_id", pd.Series(dtype=str)).astype(str)
    ]
    result = _ensure_columns(work, columns)
    return _sort_frame(result, ["entry_timestamp_ms", "portfolio_rank_score", "short_trade_id"])


def _build_short_portfolio_oos(*, short_portfolio_candidates: pd.DataFrame) -> pd.DataFrame:
    """Chronologically replay accepted trading sleeves with overlap controls."""

    columns = _short_portfolio_oos_columns()
    if short_portfolio_candidates is None or short_portfolio_candidates.empty:
        return pd.DataFrame(columns=columns)

    candidates = short_portfolio_candidates.copy()
    if "portfolio_candidate_status" not in candidates.columns:
        return pd.DataFrame(columns=columns)
    candidates = candidates.loc[candidates["portfolio_candidate_status"].astype(str).eq("eligible_portfolio_candidate")].copy()
    if candidates.empty:
        return pd.DataFrame(columns=columns)

    candidates["_entry_ms"] = pd.to_numeric(candidates.get("entry_timestamp_ms", pd.Series(dtype=float)), errors="coerce")
    candidates["_exit_ms"] = pd.to_numeric(candidates.get("final_exit_timestamp_ms", pd.Series(dtype=float)), errors="coerce")
    candidates["_rank_score"] = pd.to_numeric(candidates.get("portfolio_rank_score", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    candidates["_selected_rank"] = pd.to_numeric(candidates.get("selected_rank", pd.Series(dtype=float)), errors="coerce").fillna(999999.0)
    candidates = candidates.loc[candidates["_entry_ms"].notna() & candidates["_exit_ms"].notna()].copy()
    if candidates.empty:
        return pd.DataFrame(columns=columns)
    candidates = candidates.sort_values(["_entry_ms", "_rank_score", "_selected_rank", "short_trade_id"], ascending=[True, False, True, True])

    open_positions: list[dict[str, object]] = []
    selected_events: set[str] = set()
    last_symbol_entry_ms: dict[str, int] = {}
    cumulative_r = 0.0
    selected_sequence = 0
    rows: list[dict[str, object]] = []
    cooldown_ms = int(SHORT_PORTFOLIO_SYMBOL_COOLDOWN_MINUTES) * MINUTE_MS

    for raw in candidates.to_dict("records"):
        entry_ms = int(float(raw.get("_entry_ms", 0)))
        exit_ms = int(float(raw.get("_exit_ms", entry_ms)))
        symbol = str(raw.get("symbol", ""))
        event_id = str(raw.get("event_id", ""))
        open_positions = [position for position in open_positions if int(position.get("exit_ms", 0)) > entry_ms]
        concurrent_before = len(open_positions)
        symbol_concurrent_before = sum(1 for position in open_positions if str(position.get("symbol", "")) == symbol)
        reason = ""
        action = "selected"
        if event_id and event_id in selected_events:
            action = "rejected"
            reason = "duplicate_event_already_selected"
        elif concurrent_before >= int(SHORT_PORTFOLIO_MAX_CONCURRENT_TRADES):
            action = "rejected"
            reason = "max_concurrent_portfolio_trades_reached"
        elif symbol_concurrent_before >= int(SHORT_PORTFOLIO_MAX_CONCURRENT_PER_SYMBOL):
            action = "rejected"
            reason = "max_concurrent_symbol_trades_reached"
        elif symbol in last_symbol_entry_ms and entry_ms - int(last_symbol_entry_ms[symbol]) < cooldown_ms:
            action = "rejected"
            reason = "symbol_cooldown_active"

        realized_r = _float(raw.get("realized_r"))
        if action == "selected":
            selected_sequence += 1
            cumulative_r += realized_r if math.isfinite(realized_r) else 0.0
            open_positions.append({"symbol": symbol, "exit_ms": exit_ms, "event_id": event_id})
            if event_id:
                selected_events.add(event_id)
            if symbol:
                last_symbol_entry_ms[symbol] = entry_ms
        else:
            realized_r = 0.0

        rows.append(_short_portfolio_oos_row(
            raw,
            portfolio_action=action,
            rejection_reason=reason,
            selected_sequence=selected_sequence if action == "selected" else np.nan,
            concurrent_before_entry=concurrent_before,
            symbol_concurrent_before_entry=symbol_concurrent_before,
            cumulative_portfolio_r=cumulative_r,
            realized_r=realized_r,
        ))

    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["entry_timestamp_ms", "portfolio_action", "short_trade_id"])


def _build_short_portfolio_verdict(*, short_portfolio_oos: pd.DataFrame) -> pd.DataFrame:
    columns = _short_portfolio_verdict_columns()
    if short_portfolio_oos is None or short_portfolio_oos.empty:
        return pd.DataFrame(columns=columns)
    selected = short_portfolio_oos.loc[short_portfolio_oos.get("portfolio_action", pd.Series(dtype=str)).astype(str) == "selected"].copy()
    if selected.empty:
        return pd.DataFrame(columns=columns)
    work = _add_trade_split_columns(selected)
    stats = _short_trade_stats(work)
    first_half_r, second_half_r = _trade_half_r_sums(work)
    odd_r, even_r = _trade_odd_even_r_sums(work)
    month_positive_share = _trade_month_positive_r_share(work)
    dd_to_profit = float("nan")
    if math.isfinite(stats["max_drawdown_r"]) and math.isfinite(stats["sum_r"]) and stats["sum_r"] > 0.0:
        dd_to_profit = stats["max_drawdown_r"] / max(stats["sum_r"], 1e-12)

    fail_reasons: list[str] = []
    warning_reasons: list[str] = []
    if stats["trades"] < SHORT_PORTFOLIO_MIN_TRADES:
        fail_reasons.append("low_portfolio_trade_count")
    if stats["active_days"] < SHORT_PORTFOLIO_MIN_ACTIVE_DAYS:
        fail_reasons.append("low_portfolio_active_days")
    if stats["symbols"] < SHORT_PORTFOLIO_MIN_SYMBOLS:
        fail_reasons.append("low_portfolio_symbol_breadth")
    if not math.isfinite(stats["sum_r"]) or stats["sum_r"] <= SHORT_PORTFOLIO_MIN_SUM_R:
        fail_reasons.append("non_positive_portfolio_sum_r")
    if not math.isfinite(stats["avg_r"]) or stats["avg_r"] <= SHORT_PORTFOLIO_MIN_AVG_R:
        fail_reasons.append("non_positive_portfolio_avg_r")
    if not math.isfinite(stats["median_r"]) or stats["median_r"] <= SHORT_PORTFOLIO_MIN_MEDIAN_R:
        fail_reasons.append("non_positive_portfolio_median_r")
    if not math.isfinite(stats["positive_active_day_rate"]) or stats["positive_active_day_rate"] < SHORT_PORTFOLIO_MIN_POSITIVE_ACTIVE_DAY_RATE:
        fail_reasons.append("low_portfolio_positive_active_day_rate")
    if not math.isfinite(stats["top_symbol_trade_share"]) or stats["top_symbol_trade_share"] > SHORT_PORTFOLIO_MAX_TOP_SYMBOL_TRADE_SHARE:
        fail_reasons.append("portfolio_top_symbol_dependency")
    if not math.isfinite(stats["top_day_trade_share"]) or stats["top_day_trade_share"] > SHORT_PORTFOLIO_MAX_TOP_DAY_TRADE_SHARE:
        fail_reasons.append("portfolio_top_day_dependency")
    if math.isfinite(dd_to_profit) and dd_to_profit > SHORT_PORTFOLIO_MAX_DRAWDOWN_TO_PROFIT:
        fail_reasons.append("portfolio_drawdown_large_vs_profit")
    if math.isfinite(first_half_r) and math.isfinite(second_half_r) and first_half_r > 0.0 and second_half_r <= 0.0:
        fail_reasons.append("portfolio_second_half_degraded_to_non_positive")
    if math.isfinite(odd_r) and math.isfinite(even_r) and (odd_r <= 0.0 or even_r <= 0.0):
        warning_reasons.append("portfolio_odd_even_split_not_both_positive")

    if not fail_reasons:
        verdict = "accepted_short_portfolio"
        allowed_next_stage = "paper_forward_candidate"
    elif stats["trades"] >= SHORT_PORTFOLIO_MIN_TRADES and math.isfinite(stats["sum_r"]) and stats["sum_r"] > 0.0:
        verdict = "watchlist_short_portfolio"
        allowed_next_stage = "portfolio_research_only"
    else:
        verdict = "rejected_short_portfolio"
        allowed_next_stage = "portfolio_research_only"

    selected_ratio = float(len(selected) / len(short_portfolio_oos)) if len(short_portfolio_oos) else float("nan")
    row = {
        "research_id": RESEARCH_ID,
        "portfolio_key": "accepted_short_sleeves_constrained_portfolio",
        "portfolio_verdict": verdict,
        "next_allowed_stage": allowed_next_stage,
        "fail_reasons": ";".join(fail_reasons) if fail_reasons else "pass",
        "warning_reasons": ";".join(warning_reasons),
        **stats,
        "candidate_rows": int(len(short_portfolio_oos)),
        "selected_rows": int(len(selected)),
        "selected_candidate_ratio": selected_ratio,
        "first_trade_date": _safe_min_str(selected.get("test_date", pd.Series(dtype=str))),
        "last_trade_date": _safe_max_str(selected.get("test_date", pd.Series(dtype=str))),
        "trading_sleeves_seen": _joined_unique(selected, "trading_sleeve_key"),
        "exit_policies_seen": _joined_unique(selected, "exit_policy"),
        "signal_families_seen": _joined_unique(selected, "signal_family"),
        "mechanism_families_seen": _joined_unique(selected, "mechanism_family"),
        "sessions_seen": _joined_unique(selected, "session_bucket"),
        "month_positive_r_share": month_positive_share,
        "first_half_sum_r": first_half_r,
        "second_half_sum_r": second_half_r,
        "odd_day_sum_r": odd_r,
        "even_day_sum_r": even_r,
        "drawdown_to_profit_ratio": dd_to_profit,
        "max_concurrent_trades_gate": int(SHORT_PORTFOLIO_MAX_CONCURRENT_TRADES),
        "max_concurrent_per_symbol_gate": int(SHORT_PORTFOLIO_MAX_CONCURRENT_PER_SYMBOL),
        "symbol_cooldown_minutes_gate": int(SHORT_PORTFOLIO_SYMBOL_COOLDOWN_MINUTES),
        "short_portfolio_verdict_model": SHORT_PORTFOLIO_VERDICT_MODEL,
        "source_trading_sleeve_verdict_required": "accepted_trading_sleeve",
        "uses_final_holdout_tuning": False,
        "uses_future_optimal_exit": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }
    return _ensure_columns(pd.DataFrame([row]), columns)


def _build_short_portfolio_guard(
    *,
    short_trade_verdict: pd.DataFrame,
    short_portfolio_candidates: pd.DataFrame,
    short_portfolio_oos: pd.DataFrame,
) -> pd.DataFrame:
    columns = _short_portfolio_guard_columns()
    verdict = short_trade_verdict.copy() if short_trade_verdict is not None else pd.DataFrame()
    candidates = short_portfolio_candidates.copy() if short_portfolio_candidates is not None else pd.DataFrame()
    oos = short_portfolio_oos.copy() if short_portfolio_oos is not None else pd.DataFrame()
    selected = oos.loc[oos.get("portfolio_action", pd.Series(dtype=str)).astype(str) == "selected"].copy() if not oos.empty else pd.DataFrame()
    rows: list[dict[str, object]] = []

    def add_guard(name: str, failures: int, checked: int, description: str) -> None:
        rows.append({
            "research_id": RESEARCH_ID,
            "guard_name": name,
            "guard_status": "pass" if int(failures) == 0 else "fail",
            "checked_rows": int(checked),
            "failure_rows": int(failures),
            "description": description,
            "short_portfolio_guard_model": SHORT_PORTFOLIO_GUARD_MODEL,
            "uses_final_holdout_tuning": False,
            "uses_future_optimal_exit": False,
            "data_access_model": DATA_ACCESS_MODEL,
        })

    accepted_sleeves = set()
    if verdict is not None and not verdict.empty and "trading_sleeve_key" in verdict.columns:
        accepted = verdict.loc[
            verdict.get("trading_sleeve_verdict", pd.Series(dtype=str)).astype(str).eq("accepted_trading_sleeve")
            & verdict.get("allowed_for_portfolio_aggregation", pd.Series(dtype=bool)).map(_to_bool)
        ]
        accepted_sleeves = set(accepted["trading_sleeve_key"].astype(str))
    candidate_sleeves = set(candidates.get("trading_sleeve_key", pd.Series(dtype=str)).astype(str)) if not candidates.empty else set()
    selected_sleeves = set(selected.get("trading_sleeve_key", pd.Series(dtype=str)).astype(str)) if not selected.empty else set()

    add_guard(
        "portfolio_candidates_from_accepted_trading_sleeves_only",
        len(candidate_sleeves - accepted_sleeves),
        len(candidate_sleeves),
        "portfolio candidate sleeves must be accepted_trading_sleeve rows only",
    )
    add_guard(
        "portfolio_selected_from_accepted_trading_sleeves_only",
        len(selected_sleeves - accepted_sleeves),
        len(selected_sleeves),
        "selected portfolio trades must come from accepted_trading_sleeve rows only",
    )
    add_guard(
        "portfolio_excludes_no_oi_confirmation",
        int((selected.get("signal_family", pd.Series(dtype=str)).astype(str) == "no_oi_confirmation").sum()) if not selected.empty else 0,
        int(len(selected)),
        "audit-only no_oi_confirmation trades must not enter the portfolio",
    )
    if not selected.empty and "event_id" in selected.columns:
        selected_event_ids = selected["event_id"].dropna().astype(str)
        selected_event_ids = selected_event_ids.loc[selected_event_ids != ""]
        duplicate_event_failures = int(selected_event_ids.duplicated().sum())
    else:
        duplicate_event_failures = 0
    add_guard(
        "portfolio_no_duplicate_selected_events",
        duplicate_event_failures,
        int(len(selected)),
        "a single event_id may not be selected more than once in the constrained portfolio",
    )
    add_guard(
        "portfolio_concurrency_gate_respected",
        int((pd.to_numeric(selected.get("concurrent_before_entry", pd.Series(dtype=float)), errors="coerce").fillna(0) >= int(SHORT_PORTFOLIO_MAX_CONCURRENT_TRADES)).sum()) if not selected.empty else 0,
        int(len(selected)),
        "selected rows must have free portfolio concurrency slot before entry",
    )
    add_guard(
        "portfolio_symbol_concurrency_gate_respected",
        int((pd.to_numeric(selected.get("symbol_concurrent_before_entry", pd.Series(dtype=float)), errors="coerce").fillna(0) >= int(SHORT_PORTFOLIO_MAX_CONCURRENT_PER_SYMBOL)).sum()) if not selected.empty else 0,
        int(len(selected)),
        "selected rows must have free same-symbol concurrency slot before entry",
    )
    result = _ensure_columns(pd.DataFrame(rows), columns)
    return _sort_frame(result, ["guard_status", "guard_name"])


def _short_portfolio_oos_row(
    raw: dict[str, object],
    *,
    portfolio_action: str,
    rejection_reason: str,
    selected_sequence: object,
    concurrent_before_entry: int,
    symbol_concurrent_before_entry: int,
    cumulative_portfolio_r: float,
    realized_r: float,
) -> dict[str, object]:
    entry_ms = _finite_int_or_none(raw.get("entry_timestamp_ms"))
    exit_ms = _finite_int_or_none(raw.get("final_exit_timestamp_ms"))
    return {
        "research_id": RESEARCH_ID,
        "portfolio_trade_id": "pmsp_" + hashlib.sha1((str(raw.get("short_trade_id", "")) + "|portfolio").encode("utf-8")).hexdigest()[:16],
        "portfolio_candidate_id": str(raw.get("portfolio_candidate_id", "")),
        "short_trade_id": str(raw.get("short_trade_id", "")),
        "short_candidate_id": str(raw.get("short_candidate_id", "")),
        "trading_sleeve_key": str(raw.get("trading_sleeve_key", "")),
        "portfolio_action": str(portfolio_action),
        "portfolio_rejection_reason": str(rejection_reason),
        "selected_sequence": selected_sequence,
        "selection_key": str(raw.get("selection_key", "")),
        "test_day_ord": int(raw.get("test_day_ord", 0) or 0),
        "test_date": str(raw.get("test_date", "")),
        "train_window_days": int(raw.get("train_window_days", 0) or 0),
        "event_id": str(raw.get("event_id", "")),
        "symbol": str(raw.get("symbol", "")),
        "session_bucket": str(raw.get("session_bucket", "")),
        "mechanism_family": str(raw.get("mechanism_family", "")),
        "mechanism_id": str(raw.get("mechanism_id", "")),
        "acceptance_regime": str(raw.get("acceptance_regime", "")),
        "signal_family": str(raw.get("signal_family", "")),
        "exit_policy": str(raw.get("exit_policy", "")),
        "entry_timestamp_ms": entry_ms if entry_ms is not None else np.nan,
        "entry_time_utc": str(raw.get("entry_time_utc", "")),
        "final_exit_timestamp_ms": exit_ms if exit_ms is not None else np.nan,
        "final_exit_time_utc": str(raw.get("final_exit_time_utc", "")),
        "final_exit_reason": str(raw.get("final_exit_reason", "")),
        "hold_minutes": _float(raw.get("hold_minutes")),
        "realized_r": float(realized_r),
        "mae_r": _float(raw.get("mae_r")),
        "mfe_r": _float(raw.get("mfe_r")),
        "win": bool(float(realized_r) > 0.0),
        "loss": bool(float(realized_r) < 0.0),
        "partial_tp_hit": _to_bool(raw.get("partial_tp_hit")),
        "portfolio_rank_score": _float(raw.get("portfolio_rank_score")),
        "sleeve_sum_r": _float(raw.get("sleeve_sum_r")),
        "sleeve_avg_r": _float(raw.get("sleeve_avg_r")),
        "sleeve_median_r": _float(raw.get("sleeve_median_r")),
        "concurrent_before_entry": int(concurrent_before_entry),
        "symbol_concurrent_before_entry": int(symbol_concurrent_before_entry),
        "max_concurrent_trades_gate": int(SHORT_PORTFOLIO_MAX_CONCURRENT_TRADES),
        "max_concurrent_per_symbol_gate": int(SHORT_PORTFOLIO_MAX_CONCURRENT_PER_SYMBOL),
        "symbol_cooldown_minutes_gate": int(SHORT_PORTFOLIO_SYMBOL_COOLDOWN_MINUTES),
        "cumulative_portfolio_r": float(cumulative_portfolio_r),
        "portfolio_oos_model": SHORT_PORTFOLIO_OOS_MODEL,
        "source_trading_sleeve_verdict_required": "accepted_trading_sleeve",
        "uses_final_holdout_tuning": False,
        "uses_future_optimal_exit": False,
        "data_access_model": DATA_ACCESS_MODEL,
    }


def _short_portfolio_candidate_columns() -> list[str]:
    return [
        "research_id", "portfolio_candidate_id", "short_trade_id", "short_candidate_id", "trading_sleeve_key", "portfolio_candidate_status",
        "portfolio_candidate_rejection_reason", "trading_sleeve_verdict", "allowed_for_portfolio_aggregation", "selection_key",
        "test_day_ord", "test_date", "train_window_days", "event_id", "symbol", "session_bucket", "mechanism_family", "mechanism_id",
        "acceptance_regime", "oi_regime_at_confirm", "signal_family", "exit_policy", "entry_timestamp_ms", "entry_time_utc",
        "final_exit_timestamp_ms", "final_exit_time_utc", "final_exit_reason", "hold_minutes", "realized_r", "mae_r", "mfe_r", "win",
        "loss", "partial_tp_hit", "portfolio_rank_score", "sleeve_sum_r", "sleeve_avg_r", "sleeve_median_r",
        "sleeve_positive_active_day_rate", "sleeve_max_drawdown_r", "sleeve_top_symbol_trade_share", "sleeve_top_day_trade_share",
        "portfolio_candidate_model", "portfolio_uses_only_accepted_trading_sleeves", "uses_final_holdout_tuning",
        "uses_future_optimal_exit", "data_access_model",
    ]


def _short_portfolio_oos_columns() -> list[str]:
    return [
        "research_id", "portfolio_trade_id", "portfolio_candidate_id", "short_trade_id", "short_candidate_id", "trading_sleeve_key",
        "portfolio_action", "portfolio_rejection_reason", "selected_sequence", "selection_key", "test_day_ord", "test_date", "train_window_days",
        "event_id", "symbol", "session_bucket", "mechanism_family", "mechanism_id", "acceptance_regime", "signal_family", "exit_policy",
        "entry_timestamp_ms", "entry_time_utc", "final_exit_timestamp_ms", "final_exit_time_utc", "final_exit_reason", "hold_minutes",
        "realized_r", "mae_r", "mfe_r", "win", "loss", "partial_tp_hit", "portfolio_rank_score", "sleeve_sum_r", "sleeve_avg_r",
        "sleeve_median_r", "concurrent_before_entry", "symbol_concurrent_before_entry", "max_concurrent_trades_gate",
        "max_concurrent_per_symbol_gate", "symbol_cooldown_minutes_gate", "cumulative_portfolio_r", "portfolio_oos_model",
        "source_trading_sleeve_verdict_required", "uses_final_holdout_tuning", "uses_future_optimal_exit", "data_access_model",
    ]


def _short_portfolio_verdict_columns() -> list[str]:
    return [
        "research_id", "portfolio_key", "portfolio_verdict", "next_allowed_stage", "fail_reasons", "warning_reasons", "trades",
        "active_days", "symbols", "sessions", "selection_keys", "sum_r", "avg_r", "median_r", "win_rate", "loss_rate",
        "positive_active_days", "positive_active_day_rate", "max_drawdown_r", "avg_hold_minutes", "median_hold_minutes",
        "median_mae_r", "median_mfe_r", "partial_tp_hit_rate", "stop_exit_rate", "max_hold_exit_rate", "top_symbol_trade_share",
        "top_day_trade_share", "candidate_rows", "selected_rows", "selected_candidate_ratio", "first_trade_date", "last_trade_date",
        "trading_sleeves_seen", "exit_policies_seen", "signal_families_seen", "mechanism_families_seen", "sessions_seen",
        "month_positive_r_share", "first_half_sum_r", "second_half_sum_r", "odd_day_sum_r", "even_day_sum_r",
        "drawdown_to_profit_ratio", "max_concurrent_trades_gate", "max_concurrent_per_symbol_gate", "symbol_cooldown_minutes_gate",
        "short_portfolio_verdict_model", "source_trading_sleeve_verdict_required", "uses_final_holdout_tuning",
        "uses_future_optimal_exit", "data_access_model",
    ]


def _short_portfolio_guard_columns() -> list[str]:
    return [
        "research_id", "guard_name", "guard_status", "checked_rows", "failure_rows", "description",
        "short_portfolio_guard_model", "uses_final_holdout_tuning", "uses_future_optimal_exit", "data_access_model",
    ]

def _oos_top_removal_lookup(top_removal: pd.DataFrame) -> dict[tuple[str, str], tuple[float, bool]]:
    if top_removal is None or top_removal.empty:
        return {}
    frame = top_removal.loc[top_removal.get("group_axis", pd.Series(dtype=str)).astype(str) == "selection_key"].copy()
    if frame.empty:
        return {}
    fraction = pd.to_numeric(frame.get("remove_fraction", pd.Series(dtype=float)), errors="coerce")
    frame = frame.loc[(fraction - float(OOS_VERDICT_TOP_REMOVAL_FRACTION)).abs() < 1e-12].copy()
    lookup: dict[tuple[str, str], tuple[float, bool]] = {}
    for row in frame.to_dict("records"):
        key = (str(row.get("group_value", "")), str(row.get("removal_kind", "")))
        lookup[key] = (_float(row.get("score_retention_rate")), _to_bool(row.get("remaining_score_positive")))
    return lookup


def _event_month_count(events: pd.DataFrame) -> int:
    if events.empty or "test_date" not in events.columns:
        return 0
    months = events["test_date"].astype(str).str.slice(0, 7)
    return int(months.nunique())


def _month_positive_score_share(events: pd.DataFrame) -> float:
    if events.empty or "test_date" not in events.columns:
        return float("nan")
    score = pd.to_numeric(events.get("event_response_score", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    months = events["test_date"].astype(str).str.slice(0, 7)
    grouped = score.groupby(months).sum()
    positive = grouped.loc[grouped > 0.0]
    total_positive = float(positive.sum())
    if total_positive <= 0.0 or positive.empty:
        return float("nan")
    return float(positive.max() / total_positive)


def _half_event_score_sums(events: pd.DataFrame) -> tuple[float, float]:
    if events.empty or "test_day_ord" not in events.columns:
        return (float("nan"), float("nan"))
    day = pd.to_numeric(events["test_day_ord"], errors="coerce")
    valid = day.notna()
    if not bool(valid.any()):
        return (float("nan"), float("nan"))
    score = pd.to_numeric(events.get("event_response_score", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    midpoint = float((day.loc[valid].min() + day.loc[valid].max()) / 2.0)
    return (float(score.loc[day <= midpoint].sum()), float(score.loc[day > midpoint].sum()))


def _odd_even_event_score_sums(events: pd.DataFrame) -> tuple[float, float]:
    if events.empty or "test_day_ord" not in events.columns:
        return (float("nan"), float("nan"))
    day = pd.to_numeric(events["test_day_ord"], errors="coerce")
    score = pd.to_numeric(events.get("event_response_score", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    valid = day.notna()
    if not bool(valid.any()):
        return (float("nan"), float("nan"))
    day_int = day.loc[valid].astype("int64")
    score_valid = score.loc[valid]
    return (float(score_valid.loc[(day_int % 2) == 1].sum()), float(score_valid.loc[(day_int % 2) == 0].sum()))


def _mode_value(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    counts = frame[column].astype(str).value_counts(dropna=False)
    return str(counts.index[0]) if not counts.empty else ""


def _top_share(frame: pd.DataFrame, *, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return float("nan")
    counts = frame[column].astype(str).value_counts(dropna=False)
    if counts.empty:
        return float("nan")
    return float(counts.iloc[0] / counts.sum())



def _oos_verdict_columns() -> list[str]:
    return [
        "research_id", "selection_key", "oos_verdict", "allowed_for_short_mapping", "next_allowed_stage",
        "fail_reasons", "warning_reasons", "selected_days", "train_windows_seen", "first_selected_date",
        "last_selected_date", "basin_statuses_seen", "oos_rows", "oos_active_days", "oos_total_events",
        "event_rows", "event_symbols", "event_sessions", "event_months", "dominant_mechanism_family",
        "dominant_acceptance_regime", "dominant_oi_regime", "dominant_flow_regime", "dominant_structure_regime",
        "oos_median_response_score", "oos_avg_response_score", "oos_positive_active_score_rate",
        "event_median_response_score", "event_score_sum", "event_positive_score_rate", "expected_downside_rate",
        "median_future_ret_30m", "median_future_ret_60m", "median_future_min_ret_30m",
        "median_future_min_ret_60m", "downside_hit_rate_30m", "downside_hit_rate_60m", "reclaim_rate_60m",
        "top_symbol_event_share", "top_day_event_share", "month_positive_score_share", "first_half_event_score_sum",
        "second_half_event_score_sum", "odd_day_event_score_sum", "even_day_event_score_sum",
        "top_event_removal_10pct_score_retention_rate", "top_event_removal_10pct_remaining_positive",
        "top_symbol_removal_10pct_score_retention_rate", "top_symbol_removal_10pct_remaining_positive",
        "min_active_days_gate", "min_events_gate", "min_symbols_gate", "min_sessions_gate",
        "min_positive_active_score_rate_gate", "min_median_response_score_gate", "max_top_symbol_event_share_gate",
        "max_top_day_event_share_gate", "max_month_positive_score_share_gate", "top_removal_fraction_gate",
        "oos_verdict_model", "selection_uses_test_day_outcomes", "verdict_uses_oos_outcomes", "uses_pnl",
        "uses_short_entry", "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]


def _daily_oos_event_columns() -> list[str]:
    return [
        "research_id", "test_day_ord", "test_date", "train_window_days", "train_start_day_ord", "train_end_day_ord",
        "selected_rank", "basin_id", "center_rule_id", "selection_key", "basin_status_at_selection", "event_rank",
        "event_id", "symbol", "session_bucket", "mechanism_family", "mechanism_id", "acceptance_regime", "oi_regime",
        "flow_regime", "price_progress_regime", "structure_regime", "late_buyer_regime", "future_ret_30m",
        "future_ret_60m", "future_min_ret_30m", "future_min_ret_60m", "future_max_ret_30m", "future_max_ret_60m",
        "down_mfe_30m", "down_mfe_60m", "up_mae_30m", "up_mae_60m", "downside_hit_30m", "downside_hit_60m",
        "reclaimed_pump_high_60m", "broke_structural_low_60m", "event_response_score", "event_expected_downside",
        "daily_oos_event_ledger_model", "selection_uses_test_day_outcomes", "evaluation_uses_test_day_outcomes", "uses_pnl",
        "uses_short_entry", "uses_final_holdout_tuning", "future_label_available_at_entry", "data_access_model",
    ]


def _oos_diagnostics_columns() -> list[str]:
    return [
        "research_id", "diagnostic_source", "split_axis", "split_value", "rows", "active_rows", "unique_test_days",
        "unique_selection_keys", "unique_symbols", "total_events", "median_response_score", "avg_response_score",
        "positive_score_rate", "positive_active_score_rate", "expected_downside_rate", "median_future_ret_30m",
        "median_future_ret_60m", "median_future_min_ret_30m", "median_future_min_ret_60m", "downside_hit_rate_30m",
        "downside_hit_rate_60m", "reclaim_rate_60m", "diagnostics_model", "uses_pnl", "uses_short_entry",
        "uses_final_holdout_tuning", "data_access_model",
    ]


def _oos_top_removal_columns() -> list[str]:
    return [
        "research_id", "group_axis", "group_value", "removal_kind", "remove_fraction", "base_events", "base_symbols",
        "removed_units", "remaining_events", "remaining_symbols", "base_score_sum", "remaining_score_sum", "score_retention_rate",
        "remaining_score_positive", "top_removal_model", "uses_pnl", "uses_short_entry", "uses_final_holdout_tuning",
        "data_access_model",
    ]


def _oos_selection_summary_columns() -> list[str]:
    return [
        "research_id", "selection_key", "selected_rows", "selected_days", "train_windows_seen", "first_selected_date",
        "last_selected_date", "basin_statuses_seen", "oos_rows", "oos_active_rows", "oos_total_events",
        "oos_median_response_score", "oos_avg_response_score", "oos_positive_active_score_rate", "event_rows",
        "event_symbols", "event_sessions", "event_median_response_score", "event_score_sum", "event_positive_score_rate",
        "top_symbol_event_share", "top_day_event_share", "selection_summary_model", "uses_pnl", "uses_short_entry",
        "uses_final_holdout_tuning", "data_access_model",
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
    """Audit the mechanism-stability protocol without changing selection."""

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

    rule_bad = _rows_where_train_touches_test_day(rule_universe)
    add(
        "rule_universe_thresholds_fit_only_on_prior_train_days",
        rule_bad == 0,
        observed_rows=int(len(rule_universe)),
        failing_rows=rule_bad,
        expected="rule_universe train_end_day_ord < test_day_ord",
        observed=f"bad_rule_rows={rule_bad}",
    )

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

    event_columns = set(events.columns) if events is not None else set()
    event_forbidden = [column for column in _outcome_columns() if column in event_columns and column != "event_id"]
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

    selected_basin_ids = _string_set(daily_selection, "basin_id")
    neg_basin_ids = _string_set(negative_space, "basin_id")
    missing_negative_space = sorted(selected_basin_ids - neg_basin_ids)
    selected_with_failed_neighbors = _selected_basin_ids_with_failed_neighbors(negative_space, selected_basin_ids)
    missing_failed_neighbors = sorted(selected_basin_ids - selected_with_failed_neighbors)
    failed_neighbor_count = _failed_negative_space_neighbor_count(negative_space, selected_basin_ids)
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

    daily_oos_flag_bad = _rows_with_truthy_flags(
        daily_oos,
        {
            "selection_uses_test_day_outcomes": False,
            "uses_pnl": False,
            "uses_short_entry": False,
            "uses_final_holdout_tuning": False,
        },
    )
    if daily_oos is not None and not daily_oos.empty and "test_day_not_in_train_window" in daily_oos.columns:
        daily_oos_flag_bad += int((~daily_oos["test_day_not_in_train_window"].map(_to_bool)).sum())
    add(
        "daily_oos_is_evaluation_only_not_selection_or_pnl",
        daily_oos_flag_bad == 0,
        observed_rows=int(len(daily_oos)),
        failing_rows=daily_oos_flag_bad,
        expected="daily OOS uses outcomes only after train-only selection; no PnL/short entry/final tuning",
        observed=f"bad_daily_oos_flags={daily_oos_flag_bad}",
    )

    oi_bad = 0
    if events is not None and not events.empty and {"oi_available", "oi_asof_timestamp_ms", "feature_cutoff_ms"}.issubset(events.columns):
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

    data_access_bad = 0
    frames = (events, outcomes, taxonomy, rule_universe, negative_space, plateau_basins, daily_selection, daily_oos, window_health, selection_drift)
    for frame in frames:
        data_access_bad += _rows_without_expected_data_access_model(frame)
    add(
        "all_major_artifacts_declare_cache_only_data_access_model",
        data_access_bad == 0,
        observed_rows=sum(int(len(frame)) for frame in frames),
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
    return int((train_end.notna() & test_day.notna() & (train_end >= test_day)).sum())


def _rows_with_truthy_flags(frame: pd.DataFrame, expected_flags: dict[str, bool]) -> int:
    if frame is None or frame.empty:
        return 0
    bad = pd.Series(False, index=frame.index)
    for column, expected in expected_flags.items():
        if column not in frame.columns:
            continue
        bad |= frame[column].map(_to_bool) != bool(expected)
    return int(bad.sum())


def _string_set(frame: pd.DataFrame, column: str) -> set[str]:
    if frame is None or frame.empty or column not in frame.columns:
        return set()
    return set(frame[column].dropna().astype(str))


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
    return _string_set(failed, "basin_id")


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
    daily_oos_events: pd.DataFrame,
    oos_diagnostics: pd.DataFrame,
    oos_top_removal: pd.DataFrame,
    oos_selection_summary: pd.DataFrame,
    oos_verdict: pd.DataFrame,
    short_trade_candidates: pd.DataFrame,
    short_trade_grid: pd.DataFrame,
    short_trade_diagnostics: pd.DataFrame,
    short_trade_top_removal: pd.DataFrame,
    short_trade_verdict: pd.DataFrame,
    short_trade_guard: pd.DataFrame,
    short_portfolio_candidates: pd.DataFrame,
    short_portfolio_oos: pd.DataFrame,
    short_portfolio_verdict: pd.DataFrame,
    short_portfolio_guard: pd.DataFrame,
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
    daily_oos_event_rows = int(len(daily_oos_events)) if daily_oos_events is not None else 0
    oos_diagnostics_rows = int(len(oos_diagnostics)) if oos_diagnostics is not None else 0
    oos_top_removal_rows = int(len(oos_top_removal)) if oos_top_removal is not None else 0
    oos_selection_summary_rows = int(len(oos_selection_summary)) if oos_selection_summary is not None else 0
    oos_verdict_rows = int(len(oos_verdict)) if oos_verdict is not None else 0
    oos_verdict_pass_rows = int((oos_verdict.get("oos_verdict", pd.Series(dtype=str)).astype(str) == "accepted_mechanism").sum()) if oos_verdict is not None and not oos_verdict.empty else 0
    short_trade_candidate_rows = int(len(short_trade_candidates)) if short_trade_candidates is not None else 0
    short_trade_confirmed_rows = int((short_trade_candidates.get("short_mapping_status", pd.Series(dtype=str)).astype(str) == "confirmed_short_candidate").sum()) if short_trade_candidates is not None and not short_trade_candidates.empty else 0
    short_trade_grid_rows = int(len(short_trade_grid)) if short_trade_grid is not None else 0
    short_trade_grid_simulated_rows = int((short_trade_grid.get("trade_grid_status", pd.Series(dtype=str)).astype(str) == "simulated_trade").sum()) if short_trade_grid is not None and not short_trade_grid.empty else 0
    short_trade_grid_total_r = float(pd.to_numeric(short_trade_grid.get("realized_r", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().sum()) if short_trade_grid is not None and not short_trade_grid.empty else 0.0
    short_trade_diagnostics_rows = int(len(short_trade_diagnostics)) if short_trade_diagnostics is not None else 0
    short_trade_top_removal_rows = int(len(short_trade_top_removal)) if short_trade_top_removal is not None else 0
    short_trade_verdict_rows = int(len(short_trade_verdict)) if short_trade_verdict is not None else 0
    short_trade_verdict_pass_rows = int((short_trade_verdict.get("trading_sleeve_verdict", pd.Series(dtype=str)).astype(str) == "accepted_trading_sleeve").sum()) if short_trade_verdict is not None and not short_trade_verdict.empty else 0
    short_trade_guard_fail_rows = int((short_trade_guard.get("guard_status", pd.Series(dtype=str)).astype(str) != "pass").sum()) if short_trade_guard is not None and not short_trade_guard.empty else 0
    short_portfolio_candidate_rows = int(len(short_portfolio_candidates)) if short_portfolio_candidates is not None else 0
    short_portfolio_oos_rows = int(len(short_portfolio_oos)) if short_portfolio_oos is not None else 0
    short_portfolio_selected_rows = int((short_portfolio_oos.get("portfolio_action", pd.Series(dtype=str)).astype(str) == "selected").sum()) if short_portfolio_oos is not None and not short_portfolio_oos.empty else 0
    short_portfolio_total_r = float(pd.to_numeric(short_portfolio_oos.loc[short_portfolio_oos.get("portfolio_action", pd.Series(dtype=str)).astype(str) == "selected", "realized_r"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().sum()) if short_portfolio_oos is not None and not short_portfolio_oos.empty and "realized_r" in short_portfolio_oos.columns else 0.0
    short_portfolio_verdict_rows = int(len(short_portfolio_verdict)) if short_portfolio_verdict is not None else 0
    short_portfolio_accepted_rows = int((short_portfolio_verdict.get("portfolio_verdict", pd.Series(dtype=str)).astype(str) == "accepted_short_portfolio").sum()) if short_portfolio_verdict is not None and not short_portfolio_verdict.empty else 0
    short_portfolio_guard_fail_rows = int((short_portfolio_guard.get("guard_status", pd.Series(dtype=str)).astype(str) != "pass").sum()) if short_portfolio_guard is not None and not short_portfolio_guard.empty else 0
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
        "daily_oos_event_rows": daily_oos_event_rows,
        "oos_diagnostics_rows": oos_diagnostics_rows,
        "oos_top_removal_rows": oos_top_removal_rows,
        "oos_selection_summary_rows": oos_selection_summary_rows,
        "oos_verdict_rows": oos_verdict_rows,
        "oos_verdict_pass_rows": oos_verdict_pass_rows,
        "oos_verdict_model": OOS_VERDICT_MODEL,
        "short_trade_candidate_rows": short_trade_candidate_rows,
        "short_trade_confirmed_rows": short_trade_confirmed_rows,
        "short_mapping_model": SHORT_MAPPING_MODEL,
        "short_confirm_model": SHORT_CONFIRM_MODEL,
        "short_entry_model": SHORT_ENTRY_MODEL,
        "short_stop_model": SHORT_STOP_MODEL,
        "short_oi_model": SHORT_OI_MODEL,
        "short_confirm_window_minutes": int(SHORT_CONFIRM_WINDOW_MINUTES),
        "short_entry_adverse_slippage_bps": float(SHORT_ENTRY_ADVERSE_SLIPPAGE_BPS),
        "short_mapping_uses_only_accepted_mechanisms": True,
        "short_mapping_uses_pnl": False,
        "short_mapping_uses_future_exit": False,
        "short_trade_grid_rows": short_trade_grid_rows,
        "short_trade_grid_simulated_rows": short_trade_grid_simulated_rows,
        "short_trade_grid_total_realized_r": short_trade_grid_total_r,
        "short_trade_grid_model": SHORT_TRADE_GRID_MODEL,
        "short_exit_policies": ",".join(SHORT_EXIT_POLICIES),
        "short_exit_max_hold_minutes": int(SHORT_EXIT_MAX_HOLD_MINUTES),
        "short_trail_pivot_left_bars": int(SHORT_TRAIL_PIVOT_LEFT_BARS),
        "short_trail_pivot_right_bars": int(SHORT_TRAIL_PIVOT_RIGHT_BARS),
        "short_trail_buffer_pct": float(SHORT_TRAIL_BUFFER_PCT),
        "short_partial_tp_r_multiple": float(SHORT_PARTIAL_TP_R_MULTIPLE),
        "short_partial_tp_fraction": float(SHORT_PARTIAL_TP_FRACTION),
        "short_trade_grid_excludes_no_oi_confirmation": True,
        "short_trade_grid_uses_future_optimal_exit": False,
        "short_trade_diagnostics_rows": short_trade_diagnostics_rows,
        "short_trade_top_removal_rows": short_trade_top_removal_rows,
        "short_trade_verdict_rows": short_trade_verdict_rows,
        "short_trade_verdict_pass_rows": short_trade_verdict_pass_rows,
        "short_trade_guard_fail_rows": short_trade_guard_fail_rows,
        "short_trade_diagnostics_model": SHORT_TRADE_DIAGNOSTICS_MODEL,
        "short_trade_top_removal_model": SHORT_TRADE_TOP_REMOVAL_MODEL,
        "short_trade_verdict_model": SHORT_TRADE_VERDICT_MODEL,
        "short_trade_guard_model": SHORT_TRADE_GUARD_MODEL,
        "short_trade_verdict_min_trades": int(SHORT_TRADE_VERDICT_MIN_TRADES),
        "short_trade_verdict_min_active_days": int(SHORT_TRADE_VERDICT_MIN_ACTIVE_DAYS),
        "short_trade_verdict_min_symbols": int(SHORT_TRADE_VERDICT_MIN_SYMBOLS),
        "short_trade_verdict_min_sessions": int(SHORT_TRADE_VERDICT_MIN_SESSIONS),
        "short_trade_verdict_min_sum_r": float(SHORT_TRADE_VERDICT_MIN_SUM_R),
        "short_trade_verdict_min_avg_r": float(SHORT_TRADE_VERDICT_MIN_AVG_R),
        "short_trade_verdict_min_median_r": float(SHORT_TRADE_VERDICT_MIN_MEDIAN_R),
        "short_trade_verdict_min_positive_active_day_rate": float(SHORT_TRADE_VERDICT_MIN_POSITIVE_ACTIVE_DAY_RATE),
        "short_trade_verdict_max_top_symbol_trade_share": float(SHORT_TRADE_VERDICT_MAX_TOP_SYMBOL_TRADE_SHARE),
        "short_trade_verdict_max_top_day_trade_share": float(SHORT_TRADE_VERDICT_MAX_TOP_DAY_TRADE_SHARE),
        "short_trade_verdict_max_month_positive_r_share": float(SHORT_TRADE_VERDICT_MAX_MONTH_POSITIVE_R_SHARE),
        "short_trade_top_removal_fraction": float(SHORT_TRADE_TOP_REMOVAL_FRACTION),
        "short_portfolio_candidate_rows": short_portfolio_candidate_rows,
        "short_portfolio_oos_rows": short_portfolio_oos_rows,
        "short_portfolio_selected_rows": short_portfolio_selected_rows,
        "short_portfolio_total_realized_r": short_portfolio_total_r,
        "short_portfolio_verdict_rows": short_portfolio_verdict_rows,
        "short_portfolio_accepted_rows": short_portfolio_accepted_rows,
        "short_portfolio_guard_fail_rows": short_portfolio_guard_fail_rows,
        "short_portfolio_candidate_model": SHORT_PORTFOLIO_CANDIDATE_MODEL,
        "short_portfolio_oos_model": SHORT_PORTFOLIO_OOS_MODEL,
        "short_portfolio_verdict_model": SHORT_PORTFOLIO_VERDICT_MODEL,
        "short_portfolio_guard_model": SHORT_PORTFOLIO_GUARD_MODEL,
        "short_portfolio_max_concurrent_trades": int(SHORT_PORTFOLIO_MAX_CONCURRENT_TRADES),
        "short_portfolio_max_concurrent_per_symbol": int(SHORT_PORTFOLIO_MAX_CONCURRENT_PER_SYMBOL),
        "short_portfolio_symbol_cooldown_minutes": int(SHORT_PORTFOLIO_SYMBOL_COOLDOWN_MINUTES),
        "short_portfolio_uses_only_accepted_trading_sleeves": True,
        "short_portfolio_uses_future_optimal_exit": False,
        "oos_verdict_min_active_days": int(OOS_VERDICT_MIN_ACTIVE_DAYS),
        "oos_verdict_min_events": int(OOS_VERDICT_MIN_EVENTS),
        "oos_verdict_min_symbols": int(OOS_VERDICT_MIN_SYMBOLS),
        "oos_verdict_min_sessions": int(OOS_VERDICT_MIN_SESSIONS),
        "oos_verdict_min_positive_active_score_rate": float(OOS_VERDICT_MIN_POSITIVE_ACTIVE_SCORE_RATE),
        "oos_verdict_min_median_response_score": float(OOS_VERDICT_MIN_MEDIAN_RESPONSE_SCORE),
        "oos_verdict_max_top_symbol_event_share": float(OOS_VERDICT_MAX_TOP_SYMBOL_EVENT_SHARE),
        "oos_verdict_max_top_day_event_share": float(OOS_VERDICT_MAX_TOP_DAY_EVENT_SHARE),
        "oos_verdict_max_month_positive_score_share": float(OOS_VERDICT_MAX_MONTH_POSITIVE_SCORE_SHARE),
        "oos_verdict_top_removal_fraction": float(OOS_VERDICT_TOP_REMOVAL_FRACTION),
        "daily_oos_event_ledger_model": DAILY_OOS_EVENT_LEDGER_MODEL,
        "oos_diagnostics_model": OOS_DIAGNOSTICS_MODEL,
        "oos_top_removal_model": OOS_TOP_REMOVAL_MODEL,
        "oos_selection_summary_model": OOS_SELECTION_SUMMARY_MODEL,
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
        ("pump_mechanism_daily_oos_events.csv", "written", "event-level ledger for selected daily OOS basins"),
        ("pump_mechanism_oos_diagnostics.csv", "written", "daily OOS split diagnostics by time/window/status"),
        ("pump_mechanism_oos_top_removal.csv", "written", "selected-event top-removal stress by event and symbol"),
        ("pump_mechanism_oos_selection_summary.csv", "written", "selection-key OOS aggregation and breadth diagnostics"),
        ("pump_mechanism_oos_verdict.csv", "written", "explicit OOS mechanism acceptance verdict and fail reasons"),
        ("pump_mechanism_short_trade_candidates.csv", "written", "honest short confirm/entry candidates for accepted mechanisms only; no exit/PnL"),
        ("pump_mechanism_short_trade_grid.csv", "written", "deterministic short exit policy grid for accepted OI-confirmed candidates only"),
        ("pump_mechanism_short_trade_diagnostics.csv", "written", "trade-level OOS split diagnostics by session/family/exit/time"),
        ("pump_mechanism_short_trade_top_removal.csv", "written", "trade-level top trade and top symbol removal stress"),
        ("pump_mechanism_short_trade_verdict.csv", "written", "explicit trading sleeve acceptance verdict and fail reasons"),
        ("pump_mechanism_short_trade_guard.csv", "written", "guardrail audit proving only accepted non-audit candidates entered the trade grid"),
        ("pump_mechanism_short_portfolio_candidates.csv", "written", "accepted trading-sleeve trades eligible for portfolio aggregation"),
        ("pump_mechanism_short_portfolio_oos.csv", "written", "chronological portfolio replay with event/symbol/concurrency overlap controls"),
        ("pump_mechanism_short_portfolio_verdict.csv", "written", "portfolio-level OOS verdict for accepted trading sleeves"),
        ("pump_mechanism_short_portfolio_guard.csv", "written", "guardrail audit proving only accepted sleeves enter portfolio aggregation"),
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
