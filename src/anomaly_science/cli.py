from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from anomaly_science.atlas import run_mvp1_atlas
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
from anomaly_science.prediction import WalkForwardPredictionConfig, run_mvp1_prediction
from anomaly_science.research import ResearchRunConfig, run_research_pipeline
from anomaly_science.simulation import TradeSimulationConfig, run_mvp1_trade_simulation
from anomaly_science.state import run_mvp1_state
from anomaly_science.strategy import run_mvp1_strategy_registry
from anomaly_science.strategy.registry import StrategyRegistryError, validate_strategy_horizon
from anomaly_science.validation import run_mvp1_holdout_governance


_BOOTSTRAP_MESSAGE = "anomaly_science bootstrap ok"


def _broad_strategy_name_for_horizon(horizon_minutes: int) -> str:
    return f"broad_anomaly_v1_h{horizon_minutes}"


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


    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        print(_BOOTSTRAP_MESSAGE)
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
            )
        )
        print(f"research pipeline written: {output_dir}")
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
            out_dir=DEFAULT_MARKET_CACHE_DIR,
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
