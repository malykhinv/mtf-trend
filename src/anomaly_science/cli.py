from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from anomaly_science.atlas import run_mvp1_atlas
from anomaly_science.archetypes import load_archetype_discovery_config, run_archetype_discovery
from anomaly_science.contracts.horizons import SUPPORTED_RESEARCH_HORIZONS
from anomaly_science.controls import ControlsConfig, run_mvp1_controls
from anomaly_science.cache_export import CacheMvp1CsvExportConfig, export_cache_to_mvp1_csv
from anomaly_science.cache_validation import CacheExportProofValidationConfig, validate_cache_export_proof
from anomaly_science.data import run_mvp1_data_audit
from anomaly_science.decision import ExpectedValueConfig, run_mvp1_expected_value
from anomaly_science.events.run import run_mvp1_events
from anomaly_science.features import FeatureMatrixConfig, run_mvp1_feature_matrix, run_mvp1_features
from anomaly_science.future import run_mvp1_future
from anomaly_science.labels import run_mvp1_labels
from anomaly_science.market_context import (
    EventScopedPerpCrowdingArchiveConfig,
    EventScopedMetricsArchiveConfig,
    ReferenceMetricsArchiveConfig,
    run_attach_reference_market_context,
    run_attach_reference_positioning_context,
    run_attach_event_positioning_context,
    run_attach_perp_crowding_context,
    run_event_scoped_metrics_archive_build,
    run_event_scoped_perp_crowding_archive_build,
    run_reference_metrics_archive_build,
)
from anomaly_science.prediction import WalkForwardPredictionConfig, run_mvp1_prediction
from anomaly_science.phenotypes import (
    load_cross_fitted_phenotype_config,
    run_cross_fitted_phenotype_discovery,
)
from anomaly_science.prediction.config import (
    REGISTERED_STATE_LATTICE_ANCHORS_MINUTES,
    SUPERVISED_ANCHOR_POLICY_REGISTERED_STATE_LATTICE,
    SUPERVISED_ANCHOR_POLICY_T0_ONLY,
)
from anomaly_science.progress import make_stderr_progress_callback
from anomaly_science.probability import (
    load_binary_weekly_walk_forward_config,
    run_binary_weekly_walk_forward,
)
from anomaly_science.regimes import (
    load_causal_regime_atlas_config,
    run_causal_regime_atlas,
)
from anomaly_science.research import ResearchDatasetBuildConfig, ResearchRunConfig, build_research_dataset, run_research_pipeline
from anomaly_science.simulation import TradeSimulationConfig, run_mvp1_trade_simulation
from anomaly_science.state import run_mvp1_state
from anomaly_science.strategy import run_mvp1_strategy_registry
from anomaly_science.strategy.pump_fade import (
    run_pump_fade_dataset_build,
    run_pump_fade_nature_projection,
    run_pump_fade_oi_incremental_experiment,
    run_pump_fade_state_lattice_projection,
    run_pump_fade_state_probability_family,
    load_pump_fade_oi_probability_config,
    run_pump_fade_oi_probability_experiment,
    PUMP_FADE_REFERENCE_MARKET_CONTEXT,
    PUMP_FADE_REFERENCE_POSITIONING_CONTEXT,
    PUMP_FADE_SYMBOL_POSITIONING_CONTEXT,
    PUMP_FADE_PERP_CROWDING_CONTEXT,
    load_pump_fade_market_context_probability_config,
    run_pump_fade_market_context_probability_experiment,
    load_pump_fade_event_memory_probability_config,
    run_pump_fade_event_memory_probability_experiment,
    load_pump_fade_interaction_atlas_family_config,
    run_pump_fade_interaction_atlas_family,
    load_pump_fade_phenotype_config,
)
from anomaly_science.strategy.registry import StrategyRegistryError, validate_strategy_horizon
from anomaly_science.strategy.drawdown_ladder.stage0 import (
    DrawdownLadderStage0BuildConfig,
    build_stage0_is as build_drawdown_ladder_stage0_is,
)
from anomaly_science.strategy.drawdown_ladder.stage1_build import (
    Stage1BuildConfig,
    build_stage1_is as build_drawdown_ladder_stage1_is,
)
from anomaly_science.strategy.drawdown_ladder.stage1_analysis import (
    build_stage1_probability_gate_amendment,
    build_stage1_probability_comparison,
)
from anomaly_science.strategy.drawdown_ladder.stage1_diagnostics import (
    build_stage1_post_gate_diagnostics,
)
from anomaly_science.strategy.drawdown_ladder.structural_ev import (
    analyze_structural_ev,
    build_structural_ev_dataset,
)
from anomaly_science.strategy.drawdown_ladder.spec import MirroredRallyStage0Spec
from anomaly_science.strategy.drawdown_ladder.mirror_analysis import build_mirror_comparison
from anomaly_science.strategy.drawdown_ladder.matched_control import (
    MatchedControlBuildConfig,
    build_prior_non_drawdown_controls,
)
from anomaly_science.strategy.drawdown_ladder.matched_analysis import (
    build_matched_control_comparison,
)
from anomaly_science.validation import run_mvp1_holdout_governance


_BOOTSTRAP_MESSAGE = "anomaly_science bootstrap ok"


def _broad_strategy_name_for_horizon(horizon_minutes: int) -> str:
    return f"broad_anomaly_v1_h{horizon_minutes}"



def _parse_anchor_offsets(value: str) -> tuple[int, ...]:
    try:
        offsets = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError(f"invalid --supervised-anchor-offsets: {value!r}") from exc
    if not offsets:
        raise ValueError("--supervised-anchor-offsets must contain at least one integer offset")
    return offsets


def _add_strategy_horizon_arguments(parser: argparse.ArgumentParser, *, verb: str) -> None:
    parser.add_argument(
        "--strategy-name",
        default="",
        help=(
            "Optional executable strategy variant. If omitted, the CLI resolves "
            "broad_anomaly_v1_h{horizon} for backward-compatible MVP1 debugging. "
            "The final strategy/horizon pair is still validated by the registry."
        ),
    )
    parser.add_argument(
        "--horizon-minutes",
        type=int,
        default=30,
        choices=SUPPORTED_RESEARCH_HORIZONS,
        help=(
            f"Core-supported scenario horizon to {verb}. "
            "Must also be allowed by the selected executable strategy. Default: 30."
        ),
    )


def _resolve_cli_strategy_name(
    *,
    parser: argparse.ArgumentParser,
    strategy_name: str,
    horizon_minutes: int,
) -> str:
    resolved_strategy_name = (
        strategy_name.strip() if strategy_name.strip() else _broad_strategy_name_for_horizon(horizon_minutes)
    )
    try:
        validate_strategy_horizon(resolved_strategy_name, horizon_minutes)
    except StrategyRegistryError as exc:
        parser.error(str(exc))
    return resolved_strategy_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly-science",
        description="Clean scientific anomaly research CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "doctor",
        help="Run a minimal bootstrap check for the clean anomaly_science core.",
    )

    research = subparsers.add_parser(
        "run-research",
        help="Run the full MVP1 research pipeline from the local market cache.",
    )
    research.add_argument("strategy", help="Registered strategy name, for example broad_anomaly_v1_h30.")
    research.add_argument(
        "--days",
        type=int,
        default=None,
        help="Optional lookback days. Omit to use the full available cache period.",
    )
    research.add_argument(
        "--cache-dir",
        default="",
        help="Optional cache directory override. Default: Binance Vision enriched 1m cache.",
    )
    research.add_argument(
        "--research-mode",
        choices=("is", "frozen_holdout"),
        default="is",
        help="Research data access mode. Default: is excludes the final holdout from downstream stages.",
    )
    research.add_argument(
        "--holdout-days",
        type=int,
        default=60,
        help="Final locked holdout length in calendar days. Default: 60.",
    )
    research.add_argument(
        "--protocol-freeze-id",
        default="",
        help="Required for --research-mode frozen_holdout. Optional explicit freeze id for IS governance.",
    )
    research.add_argument(
        "--dataset-store",
        default="",
        help=(
            "Optional prebuilt research dataset store (from build-research-dataset --max-phase feature_matrix). "
            "When given, run-research reuses its deterministic heavy phases instead of rebuilding them, "
            "so weekly walk-forward retraining only pays the heavy build once."
        ),
    )
    research.add_argument(
        "--forensic-evidence-mode",
        choices=("smoke", "development", "evidence"),
        default="development",
        help=(
            "How strictly to interpret independent forensic audit WARN rows. "
            "smoke/development may complete with WARN but remain non-evidential; "
            "evidence requires a clean PASS audit. Default: development."
        ),
    )

    dataset = subparsers.add_parser(
        "build-research-dataset",
        help="Build a reusable research dataset store from the local market cache without running experiments.",
    )
    dataset.add_argument("strategy", help="Registered strategy name, for example broad_anomaly_v1_h30.")
    dataset.add_argument("--cache-dir", required=True, help="Directory containing enriched market cache parquet files.")
    dataset.add_argument("--out", required=True, help="Research dataset store directory to create or update.")
    dataset.add_argument("--days", type=int, default=None, help="Optional lookback days. Omit to use the full available cache period.")
    dataset.add_argument(
        "--max-phase",
        choices=("input", "data_audit", "events", "state", "future", "feature_catalog", "feature_matrix"),
        default="input",
        help="Highest reusable phase to build. Default: input.",
    )
    dataset.add_argument(
        "--research-mode",
        choices=("is", "frozen_holdout"),
        default="is",
        help="Dataset phase access mode. Default: is excludes the final holdout from derived phases.",
    )
    dataset.add_argument("--holdout-days", type=int, default=60, help="Final locked holdout length in calendar days. Default: 60.")
    dataset.add_argument(
        "--protocol-freeze-id",
        default="",
        help="Required for --research-mode frozen_holdout. Optional explicit freeze id for IS dataset metadata.",
    )
    dataset.add_argument("--expected-days", type=int, default=None, help="Optional validation gate: require the exported global calendar span to be at least this many days.")
    dataset.add_argument("--fail-on-missing-utc-days", action="store_true", help="Fail if any symbol has missing UTC days inside its exported first/last date span.")
    dataset.add_argument("--fail-on-missing-1m-rows", action="store_true", help="Fail if any symbol has missing 1m timestamps inside its exported first/last minute span.")
    dataset.add_argument("--fail-on-missing-open-interest", action="store_true", help="Fail if any exported symbol has no open_interest samples.")
    dataset.add_argument("--include-delivery-contracts", action="store_true", help="Include fixed-date delivery contract parquet files. Disabled by default.")
    dataset.add_argument("--progress-every", type=int, default=25, help="Print build progress every N symbols. Use 0 to disable progress output.")
    dataset.add_argument("--parquet-use-threads", action="store_true", help="Allow parquet reader to use multiple threads. Disabled by default to keep laptop RAM bounded.")
    dataset.add_argument(
        "--supervised-anchor-only",
        action="store_true",
        help=(
            "Build only the per-event anchor snapshot (minutes_since_detection==0) of state/future/feature_matrix. "
            "The supervised gate keeps the same anchor, so results are identical while the heavy stages are ~H_max "
            "times smaller and faster. Omits the per-minute online window used by the decision-timing layer."
        ),
    )
    dataset.add_argument(
        "--state-window-minutes",
        type=int,
        default=None,
        help=(
            "Explicit online-state window after detection (minutes). Build minutes 0..N once and sweep the "
            "supervised anchor offset over {0..N} (decision-timing study) without rebuilding per offset. "
            "Default: anchor-only when --supervised-anchor-only, else H_max."
        ),
    )
    dataset.add_argument(
        "--state-anchor-offset-minutes",
        type=int,
        default=None,
        help=(
            "Build a single supervised anchor offset: emit only the state row at this minutes_since_detection "
            "(one row per event). Memory-bounded per offset for the decision-timing sweep on a laptop. "
            "Overrides --state-window-minutes / --supervised-anchor-only when set."
        ),
    )
    dataset.add_argument(
        "--expected-event-lifetime-minutes",
        type=int,
        default=60,
        help="Frozen denominator for event_age_ratio when building feature_matrix. Default: 60.",
    )

    data_audit = subparsers.add_parser(
        "run-mvp1-data-audit",
        help="Run MVP1 CSV data-source, quality, universe, protocol, and manifest audit.",
    )
    data_audit.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    data_audit.add_argument("--out", required=True, help="Directory where audit artifacts will be written.")

    events = subparsers.add_parser(
        "run-mvp1-events",
        help="Run MVP1 data audit, point-in-time universe, and strategy event detector.",
    )
    events.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    events.add_argument("--out", required=True, help="Directory where event artifacts will be written.")
    _add_strategy_horizon_arguments(events, verb="detect")

    state = subparsers.add_parser(
        "run-mvp1-state",
        help="Build MVP1 online 1m strategy state from normalized candles and strategy_events.csv.",
    )
    state.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    state.add_argument("--events", required=True, help="Path to strategy_events.csv from run-mvp1-events.")
    state.add_argument("--out", required=True, help="Directory where state artifacts will be written.")

    future = subparsers.add_parser(
        "run-mvp1-future",
        help="Build MVP1 raw future paths from normalized candles and strategy_state_1m.csv.",
    )
    future.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    future.add_argument("--state", required=True, help="Path to strategy_state_1m.csv from run-mvp1-state.")
    future.add_argument("--out", required=True, help="Directory where future path artifacts will be written.")

    features = subparsers.add_parser(
        "run-mvp1-features",
        help="Write the MVP1 feature catalog contract with as-of and normalization metadata.",
    )
    features.add_argument("--out", required=True, help="Directory where feature catalog artifacts will be written.")

    strategy_registry = subparsers.add_parser(
        "run-mvp1-strategy-registry",
        help="Write registered strategy metadata exposed through the BaseStrategy contract.",
    )
    strategy_registry.add_argument("--out", required=True, help="Directory where strategy registry artifacts will be written.")

    feature_matrix = subparsers.add_parser(
        "run-mvp1-feature-matrix",
        help="Build MVP1 as-of price/time/alpha-decay feature matrix from normalized candles and strategy_state_1m.csv.",
    )
    feature_matrix.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    feature_matrix.add_argument("--state", required=True, help="Path to strategy_state_1m.csv from run-mvp1-state.")
    feature_matrix.add_argument("--out", required=True, help="Directory where feature matrix artifacts will be written.")
    feature_matrix.add_argument(
        "--expected-event-lifetime-minutes",
        type=int,
        default=60,
        help="Frozen denominator for event_age_ratio. Default: 60.",
    )

    atlas = subparsers.add_parser(
        "run-mvp1-atlas",
        help="Build MVP1 descriptive strategy nature atlas from strategy_state_1m.csv and strategy_future_paths.csv.",
    )
    atlas.add_argument("--state", required=True, help="Path to strategy_state_1m.csv from run-mvp1-state.")
    atlas.add_argument("--future", required=True, help="Path to strategy_future_paths.csv from run-mvp1-future.")
    atlas.add_argument(
        "--features",
        required=True,
        help="Path to strategy_feature_matrix.csv from run-mvp1-feature-matrix for relative atlas slices.",
    )
    atlas.add_argument("--out", required=True, help="Directory where atlas artifacts will be written.")

    labels = subparsers.add_parser(
        "run-mvp1-labels",
        help="Build MVP1 descriptive future-nature scenario labels from state and raw future path artifacts.",
    )
    labels.add_argument("--state", required=True, help="Path to strategy_state_1m.csv from run-mvp1-state.")
    labels.add_argument("--future", required=True, help="Path to strategy_future_paths.csv from run-mvp1-future.")
    labels.add_argument("--out", required=True, help="Directory where outcome label artifacts will be written.")

    prediction = subparsers.add_parser(
        "run-mvp1-prediction",
        help="Run MVP1 weekly CatBoost+Isotonic calibrated prediction from state, features, and labels.",
    )
    prediction.add_argument("--state", required=True, help="Path to strategy_state_1m.csv from run-mvp1-state.")
    prediction.add_argument("--labels", required=True, help="Path to strategy_outcome_labels.csv from run-mvp1-labels.")
    prediction.add_argument(
        "--features",
        required=True,
        help="Path to strategy_feature_matrix.csv from run-mvp1-feature-matrix for rich as-of model features.",
    )
    prediction.add_argument("--out", required=True, help="Directory where prediction artifacts will be written.")
    prediction.add_argument(
        "--supervised-anchor-policy",
        choices=(SUPERVISED_ANCHOR_POLICY_T0_ONLY, SUPERVISED_ANCHOR_POLICY_REGISTERED_STATE_LATTICE),
        default=SUPERVISED_ANCHOR_POLICY_REGISTERED_STATE_LATTICE,
        help="Pre-registered supervised anchor policy. Default uses bounded state-lattice anchors, not full per-minute rows.",
    )
    prediction.add_argument(
        "--supervised-anchor-offsets",
        default=",".join(str(value) for value in REGISTERED_STATE_LATTICE_ANCHORS_MINUTES),
        help="Comma-separated minutes_since_detection offsets for registered_state_lattice_v1.",
    )
    _add_strategy_horizon_arguments(prediction, verb="predict")

    controls = subparsers.add_parser(
        "run-mvp1-controls",
        help="Run MVP1 placebo/control checks for walk-forward prediction artifacts.",
    )
    controls.add_argument("--state", required=True, help="Path to strategy_state_1m.csv from run-mvp1-state.")
    controls.add_argument("--labels", required=True, help="Path to strategy_outcome_labels.csv from run-mvp1-labels.")
    controls.add_argument(
        "--features",
        required=True,
        help="Path to strategy_feature_matrix.csv from run-mvp1-feature-matrix for feature-aware baselines and ablations.",
    )
    controls.add_argument("--out", required=True, help="Directory where control artifacts will be written.")
    _add_strategy_horizon_arguments(controls, verb="control-test")

    expected_value = subparsers.add_parser(
        "run-mvp1-expected-value",
        help="Run MVP1 pre-simulation expected-value analysis from OOS predictions.",
    )
    expected_value.add_argument("--state", required=True, help="Path to strategy_state_1m.csv from run-mvp1-state.")
    expected_value.add_argument("--labels", required=True, help="Path to strategy_outcome_labels.csv from run-mvp1-labels.")
    expected_value.add_argument("--predictions", required=True, help="Path to strategy_oos_predictions.csv from run-mvp1-prediction.")
    expected_value.add_argument("--out", required=True, help="Directory where expected-value artifacts will be written.")
    _add_strategy_horizon_arguments(expected_value, verb="evaluate")
    expected_value.add_argument("--fee-bps", type=float, default=4.0, help="Per-side fee basis points. Default: 4.0.")
    expected_value.add_argument("--slippage-bps", type=float, default=2.0, help="Slippage penalty basis points. Default: 2.0.")
    expected_value.add_argument("--min-confidence", type=float, default=0.40, help="Minimum calibrated confidence flag. Default: 0.40.")
    expected_value.add_argument("--min-rr", type=float, default=1.0, help="Minimum RR proxy flag. Default: 1.0.")

    simulation = subparsers.add_parser(
        "run-mvp1-trade-simulation",
        help="Run MVP1 simplified pessimistic trade simulation from decision timing rows.",
    )
    simulation.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    simulation.add_argument("--decision-timing", required=True, help="Path to strategy_decision_timing.csv from run-mvp1-expected-value.")
    simulation.add_argument("--out", required=True, help="Directory where trade simulation artifacts will be written.")
    _add_strategy_horizon_arguments(simulation, verb="simulate")
    simulation.add_argument(
        "--allow-unconfident",
        action="store_true",
        help="Simulate long/short best_action rows even if confidence flag is false.",
    )
    simulation.add_argument(
        "--allow-low-rr",
        action="store_true",
        help="Simulate long/short best_action rows even if RR flag is false.",
    )
    simulation.add_argument(
        "--min-rr",
        type=float,
        default=1.0,
        help="Minimum RR proxy used when re-anchoring random controls. Must match expected-value --min-rr. Default: 1.0.",
    )

    governance = subparsers.add_parser(
        "run-mvp1-holdout-governance",
        help="Write MVP1 research ledger and empty final-holdout access log before holdout reads.",
    )
    governance.add_argument("--out", required=True, help="Directory where governance artifacts will be written.")
    governance.add_argument("--start-date", required=True, help="Research period start date YYYY-MM-DD.")
    governance.add_argument("--end-date", required=True, help="Research period end date YYYY-MM-DD.")
    governance.add_argument("--freeze-id", required=True, help="Explicit protocol freeze identifier.")
    governance.add_argument("--holdout-days", type=int, default=60, help="Final locked holdout length in calendar days. Default: 60.")
    governance.add_argument(
        "--research-mode",
        choices=("is", "frozen_holdout"),
        default="is",
        help="Governance access mode. Default: is keeps holdout_access_log.csv empty.",
    )
    governance.add_argument(
        "--holdout-access-artifact",
        default="",
        help="Artifact or boundary being accessed when --research-mode frozen_holdout is used.",
    )

    cache = subparsers.add_parser(
        "build-binance-vision-cache",
        help="Build per-symbol 1m Parquet cache from Binance Vision USD-M Futures archives.",
    )
    cache.add_argument("--days", type=int, default=380, help="Inclusive lookback window in calendar days. Default: 380.")
    cache.add_argument(
        "--out-dir",
        default="",
        help="Optional isolated cache directory. Empty uses the canonical market cache.",
    )
    cache.add_argument(
        "--end-date",
        default="",
        help="Inclusive UTC end date YYYY-MM-DD. Default: yesterday UTC, because daily archives lag by one day.",
    )
    cache.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated symbols. Empty means discover all archived USD-M Futures symbols.",
    )
    cache.add_argument("--symbols-file", default="", help="Optional text file with one symbol per line.")
    cache.add_argument("--max-symbols", type=int, default=None, help="Optional cap for smoke tests.")
    cache.add_argument("--download-workers", type=int, default=3, help="Concurrent downloads per block; valid range: 1..3.")
    cache.add_argument("--timeout", type=float, default=45.0, help="Per-request read timeout in seconds.")
    cache.add_argument("--connect-timeout", type=float, default=8.0, help="Per-request connect timeout in seconds.")
    cache.add_argument("--retries", type=int, default=8, help="Retries per network request, including metadata preflight and file downloads.")
    cache.add_argument("--overwrite", action="store_true", help="Rebuild symbols even if {symbol}.parquet already exists.")
    cache.add_argument(
        "--oi-join-strategy",
        choices=("backward",),
        default="backward",
        help="Align sparse metrics/OI using only closed samples at or before each candle timestamp.",
    )
    cache.add_argument("--request-sleep", type=float, default=0.0, help="Optional sleep after each processed block.")
    archive_index_group = cache.add_mutually_exclusive_group()
    archive_index_group.add_argument(
        "--archive-file-index",
        action="store_true",
        help=(
            "Compatibility no-op: scoped S3 kline archive preflight is enabled by default "
            "to avoid thousands of missing archive probes."
        ),
    )
    archive_index_group.add_argument(
        "--no-archive-file-index",
        action="store_true",
        help=(
            "Disable scoped S3 kline archive preflight and probe archives directly. "
            "Use only for debugging or when S3 listing is unavailable."
        ),
    )
    cache.add_argument(
        "--daily-fallback-for-missing-monthly",
        action="store_true",
        help=(
            "Probe daily archives when a closed monthly kline archive is missing. "
            "Disabled by default because missing historical monthly klines usually mean the symbol was not listed yet."
        ),
    )
    cache.add_argument(
        "--refresh-archive-file-index",
        action="store_true",
        help="Refresh cached Binance Vision file listings before processing each symbol when --archive-file-index is enabled.",
    )

    oi_backfill = subparsers.add_parser(
        "backfill-binance-vision-oi",
        help="Backfill daily Binance Vision USD-M open interest into an existing enriched 1m cache.",
    )
    oi_backfill.add_argument("--cache-dir", default="", help="Existing enriched parquet cache directory.")
    oi_backfill.add_argument("--symbols", default="", help="Optional comma-separated symbols. Empty means all cache symbols.")
    oi_backfill.add_argument("--max-symbols", type=int, default=None, help="Optional cap for smoke tests.")
    oi_backfill.add_argument("--workers", type=int, default=16, help="Concurrent small daily metrics downloads; valid range: 1..32.")
    oi_backfill.add_argument("--retries", type=int, default=5, help="Retries per network request.")
    oi_backfill.add_argument("--timeout", type=float, default=45.0, help="Per-request read timeout in seconds.")
    oi_backfill.add_argument("--connect-timeout", type=float, default=8.0, help="Per-request connect timeout in seconds.")
    oi_backfill.add_argument(
        "--max-staleness-minutes",
        type=int,
        default=10,
        help="Maximum causal age of an OI sample. Samples never cross a UTC-day boundary.",
    )
    oi_backfill.add_argument("--refresh", action="store_true", help="Re-download symbols with an intact completion proof.")

    aggtrades_backfill = subparsers.add_parser(
        "backfill-pump-fade-aggtrades",
        help="Backfill event-scoped Binance Vision USD-M aggTrades minute features for pump-fade symbols.",
    )
    aggtrades_backfill.add_argument("--sidecar-dir", default="", help="Output directory for {symbol}.parquet minute sidecars. Empty uses the canonical event-scoped cache.")
    aggtrades_backfill.add_argument("--events-source", default="", help="Decisions parquet used to derive required (symbol, day) pairs. Empty uses the canonical pump-fade decisions file.")
    aggtrades_backfill.add_argument("--symbols", default="", help="Optional comma-separated symbols. Empty means all symbols with events in --events-source.")
    aggtrades_backfill.add_argument("--max-symbols", type=int, default=None, help="Optional cap for pilot runs.")
    aggtrades_backfill.add_argument("--symbol-workers", type=int, default=4, help="Concurrent symbol processes (ProcessPoolExecutor); valid range: 1..8. Parsing/aggregation is CPU-bound, so this is where real speedup comes from.")
    aggtrades_backfill.add_argument("--workers", type=int, default=3, help="Concurrent daily aggTrades downloads per symbol process; valid range: 1..16. Total concurrent downloads is roughly symbol-workers * workers.")
    aggtrades_backfill.add_argument("--retries", type=int, default=5, help="Retries per network request.")
    aggtrades_backfill.add_argument("--timeout", type=float, default=45.0, help="Per-request read timeout in seconds.")
    aggtrades_backfill.add_argument("--connect-timeout", type=float, default=8.0, help="Per-request connect timeout in seconds.")
    aggtrades_backfill.add_argument("--lookback-minutes", type=int, default=240, help="Causal pre-ignition lookback minutes to include when deriving required days.")
    aggtrades_backfill.add_argument("--refresh", action="store_true", help="Re-download days already marked completed or missing in the manifest.")
    aggtrades_backfill.add_argument("--network-pause-seconds", type=float, default=30.0, help="Initial pause after a request exhausts its internal retries (network outage), before trying again. Doubles on each further outage, capped by --network-pause-max-seconds. No day is ever marked failed for this — it just waits.")
    aggtrades_backfill.add_argument("--network-pause-max-seconds", type=float, default=300.0, help="Cap for the exponential network-outage pause.")

    export_cache = subparsers.add_parser(
        "export-cache-mvp1-csv",
        help="Export Binance Vision enriched parquet cache into the explicit MVP1 CSV data boundary.",
    )
    export_cache.add_argument("--cache-dir", required=True, help="Directory containing {symbol}.parquet cache files.")
    export_cache.add_argument("--symbols", default="", help="Optional comma-separated symbols. Empty means discover all cache parquet files.")
    export_cache.add_argument("--days", type=int, default=None, help="Optional lookback days. Omit to export the full available cache period.")
    export_cache.add_argument("--expected-days", type=int, default=None, help="Optional validation gate: require the exported global calendar span to be at least this many days.")
    export_cache.add_argument("--fail-on-missing-utc-days", action="store_true", help="Fail after writing proof artifacts if any symbol has missing UTC days inside its exported first/last date span.")
    export_cache.add_argument("--fail-on-missing-1m-rows", action="store_true", help="Fail after writing proof artifacts if any symbol has missing 1m timestamps inside its exported first/last minute span.")
    export_cache.add_argument("--fail-on-missing-open-interest", action="store_true", help="Fail after writing proof artifacts if any exported symbol has no open_interest samples.")
    export_cache.add_argument("--include-delivery-contracts", action="store_true", help="Include fixed-date delivery contract parquet files such as BTCUSDT_250627. Disabled by default; use only for explicit delivery-contract experiments.")
    export_cache.add_argument("--progress-every", type=int, default=25, help="Print cache export progress every N symbols. Use 0 to disable progress output.")
    export_cache.add_argument("--parquet-use-threads", action="store_true", help="Allow the parquet reader to use multiple threads. Disabled by default to keep CPU/RAM bounded on laptop hardware.")
    export_cache.add_argument("--out", required=True, help="Directory where MVP1 CSV files will be written.")

    validate_cache = subparsers.add_parser(
        "validate-cache-export-proof",
        help="Validate cache_export_manifest.json and cache_export_coverage.csv without rewriting large CSV files.",
    )
    validate_cache.add_argument("--manifest", required=True, help="Path to cache_export_manifest.json.")
    validate_cache.add_argument("--coverage", required=True, help="Path to cache_export_coverage.csv.")
    validate_cache.add_argument("--out", required=True, help="Path where validation JSON will be written.")
    validate_cache.add_argument("--expected-days", type=int, default=380, help="Required exported global calendar span. Default: 380.")
    validate_cache.add_argument("--allow-settlement-transition-gaps", action="store_true", help="Classify gaps covered by a {symbol}SETTLED sibling as explicit lifecycle transitions.")
    validate_cache.add_argument("--allow-missing-utc-days", action="store_true", help="Do not fail if a symbol has missing UTC days inside its exported first/last date span.")
    validate_cache.add_argument("--allow-unclassified-1m-gaps", action="store_true", help="Do not fail if missing 1m rows cannot be classified.")

    archetypes = subparsers.add_parser(
        "run-archetype-discovery",
        help="Mine interpretable causal CatBoost tree-path archetypes and verify them on a later period.",
    )
    archetypes.add_argument("--input", required=True, help="Causal decision-row parquet or CSV dataset.")
    archetypes.add_argument(
        "--config",
        required=True,
        help="Explicit JSON feature/time contract and frozen discovery/verification protocol.",
    )
    archetypes.add_argument("--out", required=True, help="Directory for catalog, controls, model, and assignments.")
    archetypes.add_argument(
        "--limit-symbols",
        type=int,
        default=None,
        help="Deterministic sorted-symbol limit for a smoke run. Omit for the registered full run.",
    )

    pump_fade_dataset = subparsers.add_parser(
        "build-pump-fade-dataset",
        help="Build separate causal online states, offline labels, and an explicit supervised research view.",
    )
    pump_fade_dataset.add_argument(
        "--cache-dir", required=True, help="Per-symbol enriched 1m parquet cache directory."
    )
    pump_fade_dataset.add_argument(
        "--out",
        required=True,
        help="Online-state parquet path; sibling .labels and .supervised parquet files are also written.",
    )

    regime_atlas = subparsers.add_parser(
        "run-causal-regime-atlas",
        help="Evaluate frozen coarse online-state regimes before any ML model search.",
    )
    regime_atlas.add_argument(
        "--input",
        required=True,
        help="Pump-fade supervised or event-nature parquet with explicit future labels.",
    )
    regime_atlas.add_argument(
        "--config",
        required=True,
        help="Frozen JSON axis, time, matching, inference, and FDR protocol.",
    )
    regime_atlas.add_argument(
        "--out",
        required=True,
        help="Directory for regime evidence, controls, stability, freeze, and access logs.",
    )
    regime_atlas.add_argument(
        "--allow-dirty-development",
        action="store_true",
        help="Write explicitly UNFROZEN dirty-worktree artifacts; forbidden for evidence runs.",
    )
    binary_probability = subparsers.add_parser(
        "run-binary-weekly-probability",
        help="Run generic weekly frozen CatBoost plus isotonic binary probability estimation.",
    )
    binary_probability.add_argument("--input", required=True, help="Causal labeled parquet or CSV.")
    binary_probability.add_argument(
        "--config", required=True, help="Pre-registered binary probability JSON protocol."
    )
    phenotype_discovery = subparsers.add_parser(
        "run-cross-fitted-phenotype-discovery",
        help="Discover stable CatBoost leaf-rule phenotypes with separate calibration and verification.",
    )
    phenotype_discovery.add_argument("--input", required=True, help="Causal labeled parquet or CSV.")
    phenotype_discovery.add_argument("--config", required=True, help="Frozen phenotype JSON protocol.")
    phenotype_discovery.add_argument("--out", required=True, help="Phenotype artifact directory.")
    phenotype_discovery.add_argument(
        "--allow-dirty-development", action="store_true",
        help="Write explicitly unfrozen development artifacts.",
    )
    pump_fade_phenotypes = subparsers.add_parser(
        "run-pump-fade-cross-fitted-phenotypes",
        help="Discover broad causal pump-fade phenotypes using the strategy-owned feature surface.",
    )
    pump_fade_phenotypes.add_argument("--input", required=True, help="Broad-context nature parquet.")
    pump_fade_phenotypes.add_argument("--config", required=True, help="Frozen pump-fade phenotype protocol.")
    pump_fade_phenotypes.add_argument("--out", required=True, help="Phenotype artifact directory.")
    pump_fade_phenotypes.add_argument(
        "--allow-dirty-development", action="store_true",
        help="Write explicitly unfrozen development artifacts.",
    )
    binary_probability.add_argument("--out", required=True, help="Output artifact directory.")
    binary_probability.add_argument(
        "--weekly-jobs",
        type=int,
        default=1,
        help="Independent calendar weeks to fit concurrently; model settings are unchanged.",
    )
    binary_probability.add_argument(
        "--allow-dirty-development",
        action="store_true",
        help="Write explicitly UNFROZEN dirty-worktree artifacts; forbidden for evidence runs.",
    )
    pump_fade_dataset.add_argument(
        "--limit-symbols",
        type=int,
        default=None,
        help="Deterministic sorted-symbol limit for a smoke build. Omit for all symbols.",
    )
    pump_fade_dataset.add_argument(
        "--progress-every", type=int, default=10, help="Print progress every N symbols; zero disables it."
    )
    pump_fade_dataset.add_argument(
        "--workers", type=int, default=4, help="Independent symbol builder processes; valid range 1..16."
    )
    pump_fade_dataset.add_argument(
        "--max-inflight-symbols",
        type=int,
        default=None,
        help="Bound queued/in-flight symbol build tasks; defaults to workers*2.",
    )

    pump_fade_nature = subparsers.add_parser(
        "build-pump-fade-nature-dataset",
        help="Project the causal decision dataset to one immutable event-nature row per pump.",
    )
    pump_fade_nature.add_argument("--input", required=True, help="Canonical decision parquet path.")
    pump_fade_nature.add_argument("--out", required=True, help="Output event-nature parquet path.")

    pump_fade_lattice = subparsers.add_parser(
        "build-pump-fade-state-lattice",
        help="Project supervised pump-fade decisions to registered causal new-high states.",
    )
    pump_fade_lattice.add_argument("--input", required=True, help="Supervised lifecycle parquet.")
    pump_fade_lattice.add_argument("--out", required=True, help="Output state-lattice parquet.")

    pump_fade_state_probability = subparsers.add_parser(
        "run-pump-fade-state-probability",
        help="Run separate registered weekly probability models for each causal state ordinal.",
    )
    pump_fade_state_probability.add_argument("--input", required=True, help="State-lattice parquet.")
    pump_fade_state_probability.add_argument("--config", required=True, help="Base family JSON protocol.")
    pump_fade_state_probability.add_argument("--out", required=True, help="Family output directory.")
    pump_fade_state_probability.add_argument(
        "--allow-dirty-development", action="store_true",
        help="Write explicitly UNFROZEN dirty-worktree artifacts; forbidden for evidence runs.",
    )
    pump_fade_market_context = subparsers.add_parser(
        "build-pump-fade-market-context",
        help="Attach generic causal BTC/ETH reference-market features to a pump-fade artifact.",
    )
    pump_fade_market_context.add_argument("--input", required=True, help="Nature or state-lattice parquet.")
    pump_fade_market_context.add_argument("--cache-dir", required=True, help="Enriched per-symbol cache directory.")
    pump_fade_market_context.add_argument("--out", required=True, help="Augmented output parquet.")
    pump_fade_market_probability = subparsers.add_parser(
        "run-pump-fade-market-context-probability",
        help="Run paired baseline/reference-context weekly probability experiments.",
    )
    pump_fade_market_probability.add_argument("--nature", required=True, help="Context-augmented nature parquet.")
    pump_fade_market_probability.add_argument("--state-lattice", required=True, help="Context-augmented state lattice.")
    pump_fade_market_probability.add_argument("--config", required=True, help="Registered context JSON protocol.")
    pump_fade_market_probability.add_argument("--out", required=True, help="Paired experiment output directory.")
    pump_fade_market_probability.add_argument(
        "--allow-dirty-development", action="store_true",
        help="Write explicitly UNFROZEN dirty-worktree artifacts; forbidden for evidence runs.",
    )
    reference_metrics = subparsers.add_parser(
        "build-reference-metrics-cache",
        help="Download causal daily BTC/ETH positioning-ratio metrics archives.",
    )
    reference_metrics.add_argument("--start", required=True, help="Inclusive YYYY-MM-DD.")
    reference_metrics.add_argument("--end", required=True, help="Inclusive YYYY-MM-DD.")
    reference_metrics.add_argument("--out", required=True, help="Output cache directory.")
    reference_metrics.add_argument("--workers", type=int, default=8, help="Concurrent daily archive downloads; 1..16.")
    event_metrics = subparsers.add_parser(
        "build-event-scoped-symbol-metrics",
        help="Download only same-symbol positioning metrics required by registered pump-fade rows.",
    )
    event_metrics.add_argument("--nature", required=True, help="Pump-fade nature parquet.")
    event_metrics.add_argument("--state-lattice", required=True, help="Pump-fade state-lattice parquet.")
    event_metrics.add_argument("--out", required=True, help="Event-scoped metrics cache directory.")
    event_metrics.add_argument("--workers", type=int, default=16, help="Concurrent symbol workers; 1..16.")
    event_metrics.add_argument(
        "--resume", action="store_true", help="Resume only atomically completed symbol artifacts with exact scope."
    )
    pump_fade_positioning = subparsers.add_parser(
        "build-pump-fade-positioning-context",
        help="Attach causal BTC/ETH top-trader/long-short/taker ratio features.",
    )
    pump_fade_positioning.add_argument("--input", required=True, help="Nature or state-lattice parquet.")
    pump_fade_positioning.add_argument("--metrics-dir", required=True, help="Reference metrics cache directory.")
    pump_fade_positioning.add_argument("--out", required=True, help="Augmented output parquet.")
    pump_fade_symbol_positioning = subparsers.add_parser(
        "build-pump-fade-symbol-positioning-context",
        help="Attach causal same-symbol positioning levels, changes, ignition deltas, and divergences.",
    )
    pump_fade_symbol_positioning.add_argument("--input", required=True, help="Nature or state-lattice parquet.")
    pump_fade_symbol_positioning.add_argument("--metrics-dir", required=True, help="Event-scoped symbol metrics cache.")
    pump_fade_symbol_positioning.add_argument("--out", required=True, help="Augmented output parquet.")
    event_perp_crowding = subparsers.add_parser(
        "build-event-scoped-perp-crowding",
        help="Download event-scoped same-symbol premium-index and funding archives.",
    )
    event_perp_crowding.add_argument("--nature", required=True, help="Pump-fade nature parquet.")
    event_perp_crowding.add_argument("--state-lattice", required=True, help="Pump-fade state-lattice parquet.")
    event_perp_crowding.add_argument("--out", required=True, help="Perp-crowding archive directory.")
    event_perp_crowding.add_argument("--workers", type=int, default=16, help="Concurrent symbol workers; 1..16.")
    event_perp_crowding.add_argument(
        "--resume", action="store_true", help="Resume exact atomically completed symbol scopes."
    )
    pump_fade_perp_crowding = subparsers.add_parser(
        "build-pump-fade-perp-crowding-context",
        help="Attach causal same-symbol premium-index and funding-history features.",
    )
    pump_fade_perp_crowding.add_argument("--input", required=True, help="Nature or state-lattice parquet.")
    pump_fade_perp_crowding.add_argument("--archive-dir", required=True, help="Event-scoped perp-crowding archive.")
    pump_fade_perp_crowding.add_argument("--out", required=True, help="Augmented output parquet.")

    pump_fade_oi = subparsers.add_parser(
        "run-pump-fade-oi-incremental",
        help="Compare no-OI and with-OI archetype discovery on identical OI-covered event rows.",
    )
    pump_fade_oi.add_argument("--input", required=True, help="Canonical pump-fade nature parquet path.")
    pump_fade_oi.add_argument("--config", required=True, help="Registered no-OI archetype config used as the paired baseline.")
    pump_fade_oi.add_argument("--out", required=True, help="Output directory for both paired runs and summary.")
    pump_fade_oi.add_argument("--limit-symbols", type=int, default=None, help="Deterministic smoke limit; omit for the full registered run.")

    pump_fade_oi_probability = subparsers.add_parser(
        "run-pump-fade-oi-probability",
        help="Run paired baseline/with-OI weekly probability experiments on identical rows.",
    )
    pump_fade_oi_probability.add_argument("--nature", required=True, help="Separated lifecycle nature parquet.")
    pump_fade_oi_probability.add_argument("--state-lattice", required=True, help="Causal state-lattice parquet.")
    pump_fade_oi_probability.add_argument("--config", required=True, help="Registered paired OI JSON protocol.")
    pump_fade_oi_probability.add_argument("--out", required=True, help="Paired experiment output directory.")
    pump_fade_oi_probability.add_argument(
        "--allow-dirty-development", action="store_true",
        help="Write explicitly UNFROZEN dirty-worktree artifacts; forbidden for evidence runs.",
    )
    pump_fade_event_memory_probability = subparsers.add_parser(
        "run-pump-fade-event-memory-probability",
        help="Run paired baseline/resolved-event-memory weekly probability experiments.",
    )
    pump_fade_event_memory_probability.add_argument("--nature", required=True)
    pump_fade_event_memory_probability.add_argument("--state-lattice", required=True)
    pump_fade_event_memory_probability.add_argument("--config", required=True)
    pump_fade_event_memory_probability.add_argument("--out", required=True)
    pump_fade_event_memory_probability.add_argument(
        "--allow-dirty-development",
        action="store_true",
        help="Write explicitly UNFROZEN dirty-worktree artifacts; forbidden for evidence runs.",
    )
    pump_fade_interaction_atlas = subparsers.add_parser(
        "run-pump-fade-interaction-atlas-family",
        help="Run registered single/interaction atlases at T0 and new-high ordinals 1-2.",
    )
    pump_fade_interaction_atlas.add_argument("--nature", required=True)
    pump_fade_interaction_atlas.add_argument("--state-lattice", required=True)
    pump_fade_interaction_atlas.add_argument("--config", required=True)
    pump_fade_interaction_atlas.add_argument("--out", required=True)
    pump_fade_interaction_atlas.add_argument(
        "--allow-dirty-development",
        action="store_true",
        help="Write explicitly UNFROZEN dirty-worktree artifacts; forbidden for evidence runs.",
    )

    drawdown_ladder_stage0 = subparsers.add_parser(
        "build-drawdown-ladder-stage0",
        help="Build the frozen IS-only session-anchored drawdown/recovery event study.",
    )
    drawdown_ladder_stage0.add_argument(
        "--source-dir",
        default=str(DrawdownLadderStage0BuildConfig().source_dir),
        help="Directory containing enriched 1m perpetual parquet files.",
    )
    drawdown_ladder_stage0.add_argument(
        "--out",
        default=str(DrawdownLadderStage0BuildConfig().output_dir),
        help="New Stage-0 output directory.",
    )
    drawdown_ladder_stage0.add_argument("--workers", type=int, default=4)
    drawdown_ladder_stage0.add_argument("--max-inflight-symbols", type=int, default=None)
    drawdown_ladder_stage0.add_argument(
        "--limit-symbols",
        type=int,
        default=None,
        help="Deterministic alphabetic smoke limit; omit for the full IS universe.",
    )

    mirrored_rally_stage0 = subparsers.add_parser(
        "build-mirrored-rally-stage0",
        help="Build the frozen IS-only mechanically mirrored rally/short control.",
    )
    mirrored_rally_stage0.add_argument(
        "--source-dir",
        default=str(DrawdownLadderStage0BuildConfig().source_dir),
        help="Directory containing enriched 1m perpetual parquet files.",
    )
    mirrored_rally_stage0.add_argument(
        "--out",
        default=".output/research/drawdown_ladder/stage0_mirror_short_is",
        help="New mirrored Stage-0 output directory.",
    )
    mirrored_rally_stage0.add_argument("--workers", type=int, default=4)
    mirrored_rally_stage0.add_argument("--max-inflight-symbols", type=int, default=None)
    mirrored_rally_stage0.add_argument(
        "--limit-symbols",
        type=int,
        default=None,
        help="Deterministic alphabetic smoke limit; omit for the full IS universe.",
    )

    mirror_comparison = subparsers.add_parser(
        "compare-drawdown-mirror-stage0",
        help="Run the frozen cluster-aware long-drawdown versus short-rally comparison.",
    )
    mirror_comparison.add_argument("--long", required=True)
    mirror_comparison.add_argument("--mirror", required=True)
    mirror_comparison.add_argument("--out", required=True)

    prior_control = subparsers.add_parser(
        "build-drawdown-prior-control",
        help="Build frozen causal prior non-drawdown controls for long ladder states.",
    )
    prior_control.add_argument(
        "--source-dir",
        default=str(MatchedControlBuildConfig().source_dir),
    )
    prior_control.add_argument(
        "--long-stage0",
        default=str(MatchedControlBuildConfig().long_stage0_dir),
    )
    prior_control.add_argument(
        "--out",
        default=str(MatchedControlBuildConfig().output_dir),
    )
    prior_control.add_argument("--workers", type=int, default=4)
    prior_control.add_argument("--max-inflight-symbols", type=int, default=None)
    prior_control.add_argument("--limit-symbols", type=int, default=None)

    prior_comparison = subparsers.add_parser(
        "compare-drawdown-prior-control",
        help="Run the frozen paired signal versus prior non-drawdown comparison.",
    )
    prior_comparison.add_argument("--long", required=True)
    prior_comparison.add_argument("--control", required=True)
    prior_comparison.add_argument("--out", required=True)

    drawdown_ladder_stage1 = subparsers.add_parser(
        "build-drawdown-ladder-stage1",
        help="Build the frozen IS-only causal recovery-prediction feature matrix.",
    )
    drawdown_ladder_stage1.add_argument(
        "--source-dir",
        default=str(Stage1BuildConfig().source_dir),
    )
    drawdown_ladder_stage1.add_argument(
        "--stage0",
        default=str(Stage1BuildConfig().stage0_dir),
    )
    drawdown_ladder_stage1.add_argument(
        "--out",
        default=str(Stage1BuildConfig().output_dir),
    )
    drawdown_ladder_stage1.add_argument("--workers", type=int, default=4)
    drawdown_ladder_stage1.add_argument("--max-inflight-symbols", type=int, default=None)
    drawdown_ladder_stage1.add_argument("--limit-symbols", type=int, default=None)

    drawdown_stage1_comparison = subparsers.add_parser(
        "compare-drawdown-ladder-stage1-probability",
        help="Compare frozen full-causal and structural Stage-1 probability arms.",
    )
    drawdown_stage1_comparison.add_argument("--structural", required=True)
    drawdown_stage1_comparison.add_argument("--full", required=True)
    drawdown_stage1_comparison.add_argument("--config", required=True)
    drawdown_stage1_comparison.add_argument("--out", required=True)

    drawdown_stage1_gate_amendment = subparsers.add_parser(
        "amend-drawdown-ladder-stage1-gates",
        help="Re-evaluate frozen Stage-1 gates with guaranteed gate-threshold reliability.",
    )
    drawdown_stage1_gate_amendment.add_argument("--probability", required=True)
    drawdown_stage1_gate_amendment.add_argument("--config", required=True)
    drawdown_stage1_gate_amendment.add_argument(
        "--arm",
        choices=("structural_baseline", "full_causal"),
        required=True,
    )
    drawdown_stage1_gate_amendment.add_argument("--out", required=True)

    drawdown_stage1_diagnostics = subparsers.add_parser(
        "diagnose-drawdown-ladder-stage1",
        help="Write post-gate win/loss, coin, context, and importance diagnostics.",
    )
    drawdown_stage1_diagnostics.add_argument("--dataset", required=True)
    drawdown_stage1_diagnostics.add_argument("--catalog", required=True)
    drawdown_stage1_diagnostics.add_argument("--structural", required=True)
    drawdown_stage1_diagnostics.add_argument("--full", required=True)
    drawdown_stage1_diagnostics.add_argument("--out", required=True)

    drawdown_structural_ev_build = subparsers.add_parser(
        "build-drawdown-structural-ev",
        help="Join frozen structural OOF decisions to IS-only 48h outcomes.",
    )
    drawdown_structural_ev_build.add_argument("--structural", required=True)
    drawdown_structural_ev_build.add_argument("--stage1", required=True)
    drawdown_structural_ev_build.add_argument("--outcomes", required=True)
    drawdown_structural_ev_build.add_argument("--out", required=True)

    drawdown_structural_ev_analysis = subparsers.add_parser(
        "analyze-drawdown-structural-ev",
        help="Apply the frozen post-selection structural protection EV gates.",
    )
    drawdown_structural_ev_analysis.add_argument("--ev-dir", required=True)


    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        print(_BOOTSTRAP_MESSAGE)
        return 0

    if args.command == "build-drawdown-ladder-stage0":
        result = build_drawdown_ladder_stage0_is(
            DrawdownLadderStage0BuildConfig(
                source_dir=Path(args.source_dir),
                output_dir=Path(args.out),
                workers=args.workers,
                max_inflight_symbols=args.max_inflight_symbols,
                max_symbols=args.limit_symbols,
            ),
            progress=lambda message: print(message, flush=True),
        )
        print(f"drawdown-ladder Stage-0 artifacts written: {result.output_dir}")
        return 0

    if args.command == "build-mirrored-rally-stage0":
        result = build_drawdown_ladder_stage0_is(
            DrawdownLadderStage0BuildConfig(
                source_dir=Path(args.source_dir),
                output_dir=Path(args.out),
                workers=args.workers,
                max_inflight_symbols=args.max_inflight_symbols,
                max_symbols=args.limit_symbols,
            ),
            spec=MirroredRallyStage0Spec(),
            progress=lambda message: print(message, flush=True),
        )
        print(f"mirrored-rally Stage-0 artifacts written: {result.output_dir}")
        return 0

    if args.command == "compare-drawdown-mirror-stage0":
        report = build_mirror_comparison(
            long_stage0_dir=Path(args.long),
            mirror_stage0_dir=Path(args.mirror),
            output_dir=Path(args.out),
            progress=lambda message: print(message, flush=True),
        )
        print(f"drawdown/mirror comparison written: {report}")
        return 0

    if args.command == "build-drawdown-prior-control":
        result = build_prior_non_drawdown_controls(
            MatchedControlBuildConfig(
                source_dir=Path(args.source_dir),
                long_stage0_dir=Path(args.long_stage0),
                output_dir=Path(args.out),
                workers=args.workers,
                max_inflight_symbols=args.max_inflight_symbols,
                max_symbols=args.limit_symbols,
            ),
            progress=lambda message: print(message, flush=True),
        )
        print(f"prior non-drawdown controls written: {result.output_dir}")
        return 0

    if args.command == "compare-drawdown-prior-control":
        report = build_matched_control_comparison(
            long_stage0_dir=Path(args.long),
            control_dir=Path(args.control),
            output_dir=Path(args.out),
        )
        print(f"drawdown/prior-control comparison written: {report}")
        return 0

    if args.command == "build-drawdown-ladder-stage1":
        result = build_drawdown_ladder_stage1_is(
            Stage1BuildConfig(
                source_dir=Path(args.source_dir),
                stage0_dir=Path(args.stage0),
                output_dir=Path(args.out),
                workers=args.workers,
                max_inflight_symbols=args.max_inflight_symbols,
                max_symbols=args.limit_symbols,
            ),
            progress=lambda message: print(message, flush=True),
        )
        print(f"drawdown-ladder Stage-1 artifacts written: {result.output_dir}")
        return 0

    if args.command == "compare-drawdown-ladder-stage1-probability":
        output_dir = build_stage1_probability_comparison(
            structural_dir=Path(args.structural),
            full_dir=Path(args.full),
            comparison_config_path=Path(args.config),
            output_dir=Path(args.out),
        )
        print(f"drawdown-ladder Stage-1 probability comparison written: {output_dir}")
        return 0

    if args.command == "amend-drawdown-ladder-stage1-gates":
        output_dir = build_stage1_probability_gate_amendment(
            probability_dir=Path(args.probability),
            probability_config_path=Path(args.config),
            arm=args.arm,
            output_dir=Path(args.out),
        )
        print(f"drawdown-ladder Stage-1 gate amendment written: {output_dir}")
        return 0

    if args.command == "diagnose-drawdown-ladder-stage1":
        output_dir = build_stage1_post_gate_diagnostics(
            dataset_path=Path(args.dataset),
            feature_catalog_path=Path(args.catalog),
            structural_dir=Path(args.structural),
            full_dir=Path(args.full),
            output_dir=Path(args.out),
        )
        print(f"drawdown-ladder Stage-1 diagnostics written: {output_dir}")
        return 0

    if args.command == "build-drawdown-structural-ev":
        output_dir = build_structural_ev_dataset(
            structural_probability_dir=Path(args.structural),
            stage1_dataset_path=Path(args.stage1),
            stage0_outcomes_path=Path(args.outcomes),
            output_dir=Path(args.out),
        )
        print(f"drawdown structural EV rows written: {output_dir}")
        return 0

    if args.command == "analyze-drawdown-structural-ev":
        report = analyze_structural_ev(ev_dir=Path(args.ev_dir))
        print(f"drawdown structural EV report written: {report}")
        return 0

    if args.command == "run-causal-regime-atlas":
        output_dir = run_causal_regime_atlas(
            input_path=Path(args.input),
            out_dir=Path(args.out),
            config=load_causal_regime_atlas_config(Path(args.config)),
            allow_dirty_development=args.allow_dirty_development,
        )
        print(f"causal regime-atlas artifacts written: {output_dir}")
        return 0

    if args.command == "run-binary-weekly-probability":
        output_dir = run_binary_weekly_walk_forward(
            input_path=Path(args.input),
            out_dir=Path(args.out),
            config=load_binary_weekly_walk_forward_config(Path(args.config)),
            allow_dirty_development=args.allow_dirty_development,
            weekly_jobs=args.weekly_jobs,
        )
        print(f"binary weekly probability artifacts written: {output_dir}")
        return 0

    if args.command == "run-cross-fitted-phenotype-discovery":
        output_dir = run_cross_fitted_phenotype_discovery(
            input_path=Path(args.input),
            out_dir=Path(args.out),
            config=load_cross_fitted_phenotype_config(Path(args.config)),
            allow_dirty_development=args.allow_dirty_development,
            progress_callback=lambda stage, completed, total: print(
                f"phenotypes {stage}: {completed}/{total}", flush=True
            ),
        )
        print(f"cross-fitted phenotype artifacts written: {output_dir}")
        return 0

    if args.command == "run-pump-fade-cross-fitted-phenotypes":
        output_dir = run_cross_fitted_phenotype_discovery(
            input_path=Path(args.input),
            out_dir=Path(args.out),
            config=load_pump_fade_phenotype_config(Path(args.config)),
            allow_dirty_development=args.allow_dirty_development,
            progress_callback=lambda stage, completed, total: print(
                f"pump-fade phenotypes {stage}: {completed}/{total}", flush=True
            ),
        )
        print(f"pump-fade cross-fitted phenotype artifacts written: {output_dir}")
        return 0

    if args.command == "run-archetype-discovery":
        output_dir = run_archetype_discovery(
            input_path=Path(args.input),
            out_dir=Path(args.out),
            config=load_archetype_discovery_config(Path(args.config)),
            limit_symbols=args.limit_symbols,
        )
        print(f"archetype discovery artifacts written: {output_dir}")
        return 0

    if args.command == "build-pump-fade-dataset":
        output_path = run_pump_fade_dataset_build(
            cache_dir=Path(args.cache_dir),
            output_path=Path(args.out),
            limit_symbols=args.limit_symbols,
            progress_every=args.progress_every,
            workers=args.workers,
            max_inflight_symbols=args.max_inflight_symbols,
        )
        print(f"pump-fade online states and offline labels written: {output_path}")
        return 0

    if args.command == "build-pump-fade-nature-dataset":
        output_path = run_pump_fade_nature_projection(
            input_path=Path(args.input),
            output_path=Path(args.out),
        )
        print(f"pump-fade event-nature dataset written: {output_path}")
        return 0

    if args.command == "build-pump-fade-state-lattice":
        output_path = run_pump_fade_state_lattice_projection(
            input_path=Path(args.input), output_path=Path(args.out)
        )
        print(f"pump-fade causal state lattice written: {output_path}")
        return 0

    if args.command == "run-pump-fade-state-probability":
        output_dir = run_pump_fade_state_probability_family(
            input_path=Path(args.input),
            out_dir=Path(args.out),
            base_config=load_binary_weekly_walk_forward_config(Path(args.config)),
            allow_dirty_development=args.allow_dirty_development,
        )
        print(f"pump-fade state probability family written: {output_dir}")
        return 0

    if args.command == "build-pump-fade-market-context":
        output_path = run_attach_reference_market_context(
            input_path=Path(args.input),
            cache_dir=Path(args.cache_dir),
            output_path=Path(args.out),
            config=PUMP_FADE_REFERENCE_MARKET_CONTEXT,
        )
        print(f"pump-fade reference-market context written: {output_path}")
        return 0

    if args.command == "run-pump-fade-market-context-probability":
        output_dir = run_pump_fade_market_context_probability_experiment(
            nature_path=Path(args.nature),
            state_lattice_path=Path(args.state_lattice),
            out_dir=Path(args.out),
            config=load_pump_fade_market_context_probability_config(Path(args.config)),
            repository_root=Path.cwd(),
            allow_dirty_development=args.allow_dirty_development,
        )
        print(f"pump-fade market-context probability artifacts written: {output_dir}")
        return 0

    if args.command == "build-reference-metrics-cache":
        output_dir = run_reference_metrics_archive_build(
            ReferenceMetricsArchiveConfig(
                start_date=date.fromisoformat(args.start),
                end_date=date.fromisoformat(args.end),
                references=PUMP_FADE_REFERENCE_MARKET_CONTEXT.references,
                output_dir=Path(args.out),
                workers=args.workers,
            )
        )
        print(f"reference metrics cache written: {output_dir}")
        return 0

    if args.command == "build-event-scoped-symbol-metrics":
        output_dir = run_event_scoped_metrics_archive_build(
            input_paths=(Path(args.nature), Path(args.state_lattice)),
            config=EventScopedMetricsArchiveConfig(
                output_dir=Path(args.out),
                positioning=PUMP_FADE_SYMBOL_POSITIONING_CONTEXT,
                workers=args.workers,
                resume=args.resume,
            ),
            progress_callback=lambda completed, total, symbol: print(
                f"event metrics: {completed}/{total} symbols; last={symbol}", flush=True
            ) if completed % 25 == 0 or completed == total else None,
        )
        print(f"event-scoped symbol metrics cache written: {output_dir}")
        return 0

    if args.command == "build-pump-fade-positioning-context":
        output_path = run_attach_reference_positioning_context(
            input_path=Path(args.input),
            metrics_dir=Path(args.metrics_dir),
            output_path=Path(args.out),
            config=PUMP_FADE_REFERENCE_POSITIONING_CONTEXT,
        )
        print(f"pump-fade reference-positioning context written: {output_path}")
        return 0

    if args.command == "build-pump-fade-symbol-positioning-context":
        output_path = run_attach_event_positioning_context(
            input_path=Path(args.input),
            metrics_dir=Path(args.metrics_dir),
            output_path=Path(args.out),
            config=PUMP_FADE_SYMBOL_POSITIONING_CONTEXT,
        )
        print(f"pump-fade symbol-positioning context written: {output_path}")
        return 0

    if args.command == "build-event-scoped-perp-crowding":
        output_dir = run_event_scoped_perp_crowding_archive_build(
            input_paths=(Path(args.nature), Path(args.state_lattice)),
            config=EventScopedPerpCrowdingArchiveConfig(
                output_dir=Path(args.out),
                context=PUMP_FADE_PERP_CROWDING_CONTEXT,
                workers=args.workers,
                resume=args.resume,
            ),
            progress_callback=lambda completed, total, symbol: print(
                f"perp crowding: {completed}/{total} symbols; last={symbol}", flush=True
            ) if completed % 25 == 0 or completed == total else None,
        )
        print(f"event-scoped perp-crowding archive written: {output_dir}")
        return 0

    if args.command == "build-pump-fade-perp-crowding-context":
        output_path = run_attach_perp_crowding_context(
            input_path=Path(args.input),
            archive_dir=Path(args.archive_dir),
            output_path=Path(args.out),
            config=PUMP_FADE_PERP_CROWDING_CONTEXT,
        )
        print(f"pump-fade perp-crowding context written: {output_path}")
        return 0

    if args.command == "run-pump-fade-oi-incremental":
        output_dir = run_pump_fade_oi_incremental_experiment(
            input_path=Path(args.input),
            out_dir=Path(args.out),
            base_config=load_archetype_discovery_config(Path(args.config)),
            limit_symbols=args.limit_symbols,
        )
        print(f"pump-fade paired OI experiment artifacts written: {output_dir}")
        return 0

    if args.command == "run-pump-fade-oi-probability":
        output_dir = run_pump_fade_oi_probability_experiment(
            nature_path=Path(args.nature),
            state_lattice_path=Path(args.state_lattice),
            out_dir=Path(args.out),
            config=load_pump_fade_oi_probability_config(Path(args.config)),
            repository_root=Path.cwd(),
            allow_dirty_development=args.allow_dirty_development,
        )
        print(f"pump-fade paired OI probability artifacts written: {output_dir}")
        return 0

    if args.command == "run-pump-fade-event-memory-probability":
        output_dir = run_pump_fade_event_memory_probability_experiment(
            nature_path=Path(args.nature),
            state_lattice_path=Path(args.state_lattice),
            out_dir=Path(args.out),
            config=load_pump_fade_event_memory_probability_config(Path(args.config)),
            repository_root=Path.cwd(),
            allow_dirty_development=args.allow_dirty_development,
        )
        print(f"pump-fade event-memory probability artifacts written: {output_dir}")
        return 0

    if args.command == "run-pump-fade-interaction-atlas-family":
        output_dir = run_pump_fade_interaction_atlas_family(
            nature_path=Path(args.nature),
            state_lattice_path=Path(args.state_lattice),
            out_dir=Path(args.out),
            config=load_pump_fade_interaction_atlas_family_config(Path(args.config)),
            repository_root=Path.cwd(),
            allow_dirty_development=args.allow_dirty_development,
        )
        print(f"pump-fade interaction-atlas family written: {output_dir}")
        return 0

    if args.command == "run-research":
        from anomaly_science.binance_vision_cache import DEFAULT_MARKET_CACHE_DIR

        cache_dir = Path(args.cache_dir) if args.cache_dir else DEFAULT_MARKET_CACHE_DIR
        output_dir = run_research_pipeline(
            ResearchRunConfig(
                strategy_name=args.strategy,
                cache_dir=cache_dir,
                days=args.days,
                research_mode=args.research_mode,
                holdout_days=args.holdout_days,
                protocol_freeze_id=args.protocol_freeze_id,
                dataset_store_dir=Path(args.dataset_store) if args.dataset_store else None,
                forensic_evidence_mode=args.forensic_evidence_mode,
            )
        )
        print(f"research pipeline written: {output_dir}")
        return 0

    if args.command == "build-research-dataset":
        output_dir = build_research_dataset(
            ResearchDatasetBuildConfig(
                strategy_name=args.strategy,
                cache_dir=Path(args.cache_dir),
                out_dir=Path(args.out),
                days=args.days,
                max_phase=args.max_phase,
                research_mode=args.research_mode,
                holdout_days=args.holdout_days,
                protocol_freeze_id=args.protocol_freeze_id,
                expected_days=args.expected_days,
                fail_on_missing_utc_days=args.fail_on_missing_utc_days,
                fail_on_missing_1m_rows=args.fail_on_missing_1m_rows,
                fail_on_missing_open_interest=args.fail_on_missing_open_interest,
                include_delivery_contracts=args.include_delivery_contracts,
                progress_every=args.progress_every,
                parquet_use_threads=args.parquet_use_threads,
                expected_event_lifetime_minutes=args.expected_event_lifetime_minutes,
                supervised_anchor_only=args.supervised_anchor_only,
                state_window_minutes_after_detection=args.state_window_minutes,
                state_anchor_offset_minutes=args.state_anchor_offset_minutes,
            )
        )
        print(f"research dataset written: {output_dir}")
        return 0

    if args.command == "run-mvp1-data-audit":
        output_dir = run_mvp1_data_audit(input_dir=Path(args.input), out_dir=Path(args.out))
        print(f"mvp1 data audit artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-events":
        resolved_strategy_name = _resolve_cli_strategy_name(
            parser=parser,
            strategy_name=args.strategy_name,
            horizon_minutes=args.horizon_minutes,
        )
        output_dir = run_mvp1_events(
            input_dir=Path(args.input),
            out_dir=Path(args.out),
            strategy_name=resolved_strategy_name,
        )
        print(f"mvp1 strategy event artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-state":
        output_dir = run_mvp1_state(input_dir=Path(args.input), events_path=Path(args.events), out_dir=Path(args.out))
        print(f"mvp1 online state artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-future":
        output_dir = run_mvp1_future(input_dir=Path(args.input), state_path=Path(args.state), out_dir=Path(args.out))
        print(f"mvp1 raw future path artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-features":
        output_dir = run_mvp1_features(out_dir=Path(args.out))
        print(f"mvp1 feature catalog artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-strategy-registry":
        output_dir = run_mvp1_strategy_registry(out_dir=Path(args.out))
        print(f"mvp1 strategy registry artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-feature-matrix":
        config = FeatureMatrixConfig(expected_event_lifetime_minutes=args.expected_event_lifetime_minutes)
        output_dir = run_mvp1_feature_matrix(
            input_dir=Path(args.input),
            state_path=Path(args.state),
            out_dir=Path(args.out),
            config=config,
        )
        print(f"mvp1 feature matrix artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-atlas":
        output_dir = run_mvp1_atlas(
            state_path=Path(args.state),
            future_path=Path(args.future),
            feature_matrix_path=Path(args.features),
            out_dir=Path(args.out),
            progress_callback=make_stderr_progress_callback(stage_name="atlas", unit="rows"),
        )
        print(f"mvp1 strategy atlas artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-labels":
        output_dir = run_mvp1_labels(state_path=Path(args.state), future_path=Path(args.future), out_dir=Path(args.out))
        print(f"mvp1 outcome label artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-prediction":
        config = WalkForwardPredictionConfig(
            strategy_name=_resolve_cli_strategy_name(
                parser=parser,
                strategy_name=args.strategy_name,
                horizon_minutes=args.horizon_minutes,
            ),
            target_horizon_minutes=args.horizon_minutes,
            supervised_anchor_policy_id=args.supervised_anchor_policy,
            supervised_anchor_offsets_minutes_since_detection=_parse_anchor_offsets(args.supervised_anchor_offsets),
        )
        output_dir = run_mvp1_prediction(
            state_path=Path(args.state),
            labels_path=Path(args.labels),
            feature_matrix_path=Path(args.features),
            out_dir=Path(args.out),
            config=config,
        )
        print(f"mvp1 walk-forward prediction artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-controls":
        config = ControlsConfig(
            strategy_name=_resolve_cli_strategy_name(
                parser=parser,
                strategy_name=args.strategy_name,
                horizon_minutes=args.horizon_minutes,
            ),
            target_horizon_minutes=args.horizon_minutes,
        )
        output_dir = run_mvp1_controls(
            state_path=Path(args.state),
            labels_path=Path(args.labels),
            feature_matrix_path=Path(args.features),
            out_dir=Path(args.out),
            config=config,
        )
        print(f"mvp1 placebo/control artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-expected-value":
        config = ExpectedValueConfig(
            strategy_name=_resolve_cli_strategy_name(
                parser=parser,
                strategy_name=args.strategy_name,
                horizon_minutes=args.horizon_minutes,
            ),
            target_horizon_minutes=args.horizon_minutes,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            min_prediction_confidence=args.min_confidence,
            min_rr=args.min_rr,
        )
        output_dir = run_mvp1_expected_value(
            state_path=Path(args.state),
            labels_path=Path(args.labels),
            predictions_path=Path(args.predictions),
            out_dir=Path(args.out),
            config=config,
        )
        print(f"mvp1 expected-value artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-trade-simulation":
        config = TradeSimulationConfig(
            strategy_name=_resolve_cli_strategy_name(
                parser=parser,
                strategy_name=args.strategy_name,
                horizon_minutes=args.horizon_minutes,
            ),
            target_horizon_minutes=args.horizon_minutes,
            require_prediction_confident=not bool(args.allow_unconfident),
            require_rr_acceptable=not bool(args.allow_low_rr),
            min_rr=args.min_rr,
        )
        output_dir = run_mvp1_trade_simulation(
            input_dir=Path(args.input),
            decision_timing_path=Path(args.decision_timing),
            out_dir=Path(args.out),
            config=config,
        )
        print(f"mvp1 trade simulation artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-holdout-governance":
        output_dir = run_mvp1_holdout_governance(
            out_dir=Path(args.out),
            start_date=datetime.strptime(args.start_date, "%Y-%m-%d").date(),
            end_date=datetime.strptime(args.end_date, "%Y-%m-%d").date(),
            protocol_freeze_id=args.freeze_id,
            holdout_days=args.holdout_days,
            research_mode=args.research_mode,
            holdout_access_artifact=args.holdout_access_artifact,
        )
        print(f"mvp1 holdout governance artifacts written: {output_dir}")
        return 0

    if args.command == "build-binance-vision-cache":
        from anomaly_science.binance_vision_cache import (
            DEFAULT_MARKET_CACHE_DIR,
            CacheConfig,
            build_binance_vision_cache,
            parse_optional_date,
            read_symbols_arg,
        )

        config = CacheConfig(
            out_dir=Path(args.out_dir) if args.out_dir else DEFAULT_MARKET_CACHE_DIR,
            days=args.days,
            end_date=parse_optional_date(args.end_date),
            symbols=tuple(read_symbols_arg(args.symbols, args.symbols_file)),
            max_symbols=args.max_symbols,
            download_workers=args.download_workers,
            timeout_seconds=args.timeout,
            connect_timeout_seconds=args.connect_timeout,
            retries=args.retries,
            overwrite=args.overwrite,
            oi_join_strategy=args.oi_join_strategy,
            request_sleep_seconds=args.request_sleep,
            use_archive_file_index=not bool(args.no_archive_file_index),
            refresh_archive_file_index=args.refresh_archive_file_index,
            daily_fallback_for_missing_monthly=args.daily_fallback_for_missing_monthly,
        )
        stats = build_binance_vision_cache(config)
        written = sum(1 for item in stats if item.rows_written > 0)
        skipped_existing = sum(1 for item in stats if item.rows_written == -1)
        print(f"binance vision cache done: written={written}, skipped_existing={skipped_existing}, out={config.out_dir}")
        return 0

    if args.command == "backfill-binance-vision-oi":
        from anomaly_science.binance_vision_cache import DEFAULT_MARKET_CACHE_DIR
        from anomaly_science.binance_vision_oi_backfill import OiBackfillConfig, backfill_binance_vision_open_interest

        cache_dir = Path(args.cache_dir) if args.cache_dir else DEFAULT_MARKET_CACHE_DIR
        symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
        stats = backfill_binance_vision_open_interest(
            OiBackfillConfig(
                cache_dir=cache_dir,
                symbols=symbols,
                max_symbols=args.max_symbols,
                workers=args.workers,
                retries=args.retries,
                timeout_seconds=args.timeout,
                connect_timeout_seconds=args.connect_timeout,
                max_staleness_minutes=args.max_staleness_minutes,
                refresh=args.refresh,
            )
        )
        print(
            "binance vision OI backfill done: "
            f"symbols={len(stats)}, archive_days={sum(item.archive_days for item in stats)}, "
            f"oi_rows={sum(item.oi_rows_after for item in stats)}, out={cache_dir}"
        )
        return 0

    if args.command == "backfill-pump-fade-aggtrades":
        from anomaly_science.strategy.pump_fade.aggtrades_backfill import (
            DEFAULT_AGGTRADES_SIDECAR_DIR,
            DEFAULT_EVENTS_SOURCE,
            AggTradesBackfillConfig,
            backfill_pump_fade_aggtrades,
        )

        sidecar_dir = Path(args.sidecar_dir) if args.sidecar_dir else DEFAULT_AGGTRADES_SIDECAR_DIR
        events_source = Path(args.events_source) if args.events_source else DEFAULT_EVENTS_SOURCE
        symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
        stats = backfill_pump_fade_aggtrades(
            AggTradesBackfillConfig(
                sidecar_dir=sidecar_dir,
                events_source=events_source,
                symbols=symbols,
                max_symbols=args.max_symbols,
                symbol_workers=args.symbol_workers,
                workers=args.workers,
                retries=args.retries,
                timeout_seconds=args.timeout,
                connect_timeout_seconds=args.connect_timeout,
                lookback_minutes=args.lookback_minutes,
                refresh=args.refresh,
                network_pause_seconds=args.network_pause_seconds,
                network_pause_max_seconds=args.network_pause_max_seconds,
            )
        )
        total_bytes = sum(item.bytes_downloaded for item in stats)
        print(
            "pump-fade aggTrades backfill done: "
            f"symbols={len(stats)}, downloaded_days={sum(item.downloaded_days for item in stats)}, "
            f"missing_days={sum(item.missing_days for item in stats)}, failed_days={sum(item.failed_days for item in stats)}, "
            f"bytes_downloaded={total_bytes}, minute_rows={sum(item.minute_rows for item in stats)}, out={sidecar_dir}"
        )
        return 0

    if args.command == "export-cache-mvp1-csv":
        symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
        output_dir = export_cache_to_mvp1_csv(
            CacheMvp1CsvExportConfig(
                cache_dir=Path(args.cache_dir),
                out_dir=Path(args.out),
                symbols=symbols,
                days=args.days,
                expected_days=args.expected_days,
                fail_on_missing_utc_days=args.fail_on_missing_utc_days,
                fail_on_missing_1m_rows=args.fail_on_missing_1m_rows,
                fail_on_missing_open_interest=args.fail_on_missing_open_interest,
                include_delivery_contracts=args.include_delivery_contracts,
                progress_every=args.progress_every,
                parquet_use_threads=args.parquet_use_threads,
            )
        )
        print(f"mvp1 csv export written: {output_dir}")
        return 0

    if args.command == "validate-cache-export-proof":
        output_path = validate_cache_export_proof(
            CacheExportProofValidationConfig(
                manifest_path=Path(args.manifest),
                coverage_path=Path(args.coverage),
                out_path=Path(args.out),
                expected_days=args.expected_days,
                fail_on_missing_utc_days=not bool(args.allow_missing_utc_days),
                fail_on_unclassified_1m_gaps=not bool(args.allow_unclassified_1m_gaps),
                allow_settlement_transition_gaps=args.allow_settlement_transition_gaps,
            )
        )
        print(f"cache export proof validation written: {output_path}")
        return 0

    parser.print_help()
    return 2
