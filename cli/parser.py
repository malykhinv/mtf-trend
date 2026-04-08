"""CLI parser."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from cli import commands
from config import AppConfig
from constants import (
    DEFAULT_FETCH_DAYS,
    DEFAULT_MIN_VOLUME_USD,
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
    DEFAULT_UPDATE_DAYS,
)

Handler = Callable[[AppConfig, argparse.Namespace], int]


def positive_int(value: str, argument_name: str = "value") -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{argument_name} must be > 0") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{argument_name} must be > 0")
    return parsed


def _positive_int_for(argument_name: str) -> Callable[[str], int]:
    def _validator(value: str) -> int:
        return positive_int(value, argument_name=argument_name)

    return _validator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtf-trend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data", help="Load market data from exchange sources")
    fetch.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    fetch.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    fetch.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_FETCH_DAYS)
    fetch.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    fetch.add_argument("--timeframes", nargs="*", default=None, help="Timeframes to fetch, e.g. 1m 3m 5m")
    fetch.add_argument("--skip-open-interest", action="store_true", default=False, help="Skip open interest fetching")
    fetch.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")

    fetch_ppa = subparsers.add_parser(
        "fetch-ppa-cache",
        help="Load post_pump_absorption cache: 1m/3m/5m OHLCV only, without OI",
    )
    fetch_ppa.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    fetch_ppa.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    fetch_ppa.add_argument("--days", type=_positive_int_for("--days"), default=366)
    fetch_ppa.add_argument("--min-volume-usd", type=float, default=1_000_000.0)
    fetch_ppa.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")

    update = subparsers.add_parser("update-cache", help="Incrementally update the local cache")
    update.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    update.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    update.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_UPDATE_DAYS)
    update.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    update.add_argument("--timeframes", nargs="*", default=None, help="Timeframes to update, e.g. 1m 3m 5m")
    update.add_argument("--skip-open-interest", action="store_true", default=False, help="Skip open interest fetching")
    update.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")
    run_bt = subparsers.add_parser("run-backtest", help="Run a backtest on cached data")
    run_bt.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    run_bt.add_argument(
        "--top-n",
        type=_positive_int_for("--top-n"),
        default=None,
        help="Number of symbols after strategy pre-filtering or generic volume pre-rank",
    )
    run_bt.add_argument("--levels-tf", default=None, help="Levels timeframe, e.g. 1d")
    run_bt.add_argument("--entry-tf", default=None, help="Entry timeframe, e.g. 15m")
    run_bt.add_argument(
        "--strategy",
        choices=["bee_bite", "post_pump_absorption", "pno"],
        default=None,
        help="Strategy id for backtest",
    )
    run_bt.add_argument("--bee-bite-grid", choices=["baseline", "expanded", "research"], default=None, help="Bee bite grid mode")
    run_bt.add_argument("--bee-bite-reclaim-mode", choices=["strict", "balanced", "aggressive"], default=None, help="Bee bite reclaim mode")
    run_bt.add_argument("--bee-bite-retest-mode", choices=["confirmation", "immediate"], default=None, help="Bee bite entry mode")
    run_bt.add_argument("--bee-bite-cooldown-hours", "--bee-bite-cooldown-bars", dest="bee_bite_cooldown_hours", type=_positive_int_for("--bee-bite-cooldown-hours"), default=None, help="Cooldown for bee_bite in hours")
    run_bt.add_argument("--bee-bite-max-age-range-hours", "--bee-bite-max-age-range", dest="bee_bite_max_age_range_hours", type=_positive_int_for("--bee-bite-max-age-range-hours"), default=None, help="Max range age for bee_bite in hours")
    run_bt.add_argument("--bee-bite-deposit", type=float, default=None, help="Deposit used for position sizing")
    run_bt.add_argument("--bee-bite-risk-pct", type=float, default=None, help="Risk per trade as a decimal share of deposit, e.g. 0.02")
    run_bt.add_argument(
        "--ppa-profile",
        choices=["loose", "balanced", "strict"],
        default=None,
        help="Post pump absorption profile",
    )
    run_bt.add_argument("--ppa-deposit", type=float, default=None, help="Deposit used for post_pump_absorption sizing")
    run_bt.add_argument("--ppa-risk-pct", type=float, default=None, help="Risk per trade for post_pump_absorption")
    run_bt.add_argument("--pno-deposit", type=float, default=None, help="Deposit used for PNO position sizing")
    run_bt.add_argument("--pno-risk-pct", type=float, default=None, help="Risk per trade for PNO")
    run_bt.add_argument(
        "--ppa-stage",
        type=_positive_int_for("--ppa-stage"),
        default=None,
        help="Export only one PPA logical stage in diagnostics (1..6)",
    )
    run_bt.add_argument(
        "--ppa-through-stage",
        type=_positive_int_for("--ppa-through-stage"),
        default=None,
        help="Export cumulative PPA logical stages 1..N in diagnostics (1..6)",
    )
    run_bt.add_argument(
        "--pno-stage",
        type=_positive_int_for("--pno-stage"),
        default=None,
        help="Export only one PNO logical stage in diagnostics (1..5)",
    )
    run_bt.add_argument(
        "--pno-through-stage",
        type=_positive_int_for("--pno-through-stage"),
        default=None,
        help="Export cumulative PNO logical stages 1..N in diagnostics (1..5)",
    )
    run_bt.add_argument("--plot", default=False, help="Save diagnostic files for the best combination (true/false)")
    run_bt.add_argument("--plot-from-results", action="store_true", help="Build diagnostics from results.csv without a full backtest")
    run_bt.add_argument("--results-input", default=None, help="Path to CSV with results for --plot-from-results")
    run_bt.add_argument("--id", type=_positive_int_for("--id"), default=None, help="Combination ID in the CSV")

    ppa_research = subparsers.add_parser(
        "run-ppa-research",
        help="Run post_pump_absorption across multiple micro timeframes and build research artifacts",
    )
    ppa_research.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    ppa_research.add_argument(
        "--top-n",
        type=_positive_int_for("--top-n"),
        default=None,
        help="Number of symbols after volume pre-rank",
    )
    ppa_research.add_argument(
        "--timeframes",
        nargs="*",
        default=None,
        help="Micro timeframes to run, default: 1m 3m 5m",
    )
    ppa_research.add_argument(
        "--ppa-profile",
        choices=["loose", "balanced", "strict"],
        default=None,
        help="Post pump absorption profile",
    )
    ppa_research.add_argument("--ppa-deposit", type=float, default=None, help="Deposit used for post_pump_absorption sizing")
    ppa_research.add_argument("--ppa-risk-pct", type=float, default=None, help="Risk per trade for post_pump_absorption")
    ppa_research.add_argument("--output-dir", default=None, help="Root directory for research results")

    hourly_pump = subparsers.add_parser(
        "run-hourly-pump-research",
        help="Run hourly Asia-session top-of-hour pump research on cached micro timeframes",
    )
    hourly_pump.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    hourly_pump.add_argument(
        "--top-n",
        type=_positive_int_for("--top-n"),
        default=None,
        help="Number of symbols after cache-based volume pre-rank",
    )
    hourly_pump.add_argument(
        "--timeframes",
        nargs="*",
        default=None,
        help="Micro timeframes to run, default: 1m 3m 5m",
    )
    hourly_pump.add_argument(
        "--selection-profile",
        choices=["loose", "balanced", "strict"],
        default="balanced",
        help="Named threshold profile used for the detailed event table",
    )
    hourly_pump.add_argument(
        "--asia-start-hour-utc",
        type=int,
        default=0,
        help="Inclusive Asia session start hour in UTC",
    )
    hourly_pump.add_argument(
        "--asia-end-hour-utc",
        type=int,
        default=9,
        help="Exclusive Asia session end hour in UTC",
    )
    hourly_pump.add_argument(
        "--trigger-minute",
        type=int,
        default=0,
        help="Minute inside the hour used as the top-of-hour trigger, default: 0",
    )
    hourly_pump.add_argument(
        "--max-follow-minutes",
        type=_positive_int_for("--max-follow-minutes"),
        default=720,
        help="Maximum post-trigger window used to track the move before a 50%% retrace",
    )
    hourly_pump.add_argument("--output-dir", default=None, help="Root directory for research results")

    hourly_pump_static_combo = subparsers.add_parser(
        "run-hourly-pump-static-combo-analysis",
        help="Build a static full-year combo catalog and priority-selected portfolio from hourly pump event exports",
    )
    hourly_pump_static_combo.add_argument(
        "--base-events",
        required=True,
        help="Path to trade_model_events.csv produced by run-hourly-pump-research",
    )
    hourly_pump_static_combo.add_argument(
        "--confirmed-events",
        required=True,
        help="Path to confirmed_trade_events.csv from the confirmed continuation search",
    )
    hourly_pump_static_combo.add_argument(
        "--output-dir",
        default=None,
        help="Root directory for static combo analysis results",
    )

    hourly_pump_production = subparsers.add_parser(
        "run-hourly-pump-production-report",
        help="Build a fixed production pack report and KPI gate from an existing hourly pump static-combo run",
    )
    hourly_pump_production.add_argument(
        "--static-combo-dir",
        required=True,
        help="Directory produced by run-hourly-pump-static-combo-analysis",
    )
    hourly_pump_production.add_argument(
        "--output-dir",
        default=None,
        help="Where to write the production report; default: static-combo directory itself",
    )

    hourly_pump_unified = subparsers.add_parser(
        "run-hourly-pump-unified-analysis",
        help="Build one unified XX:00 strategy without hour-specific rule branches",
    )
    hourly_pump_unified.add_argument(
        "--confirmed-events",
        required=True,
        help="Path to confirmed_trade_events.csv from hourly pump research",
    )
    hourly_pump_unified.add_argument(
        "--output-dir",
        default=None,
        help="Directory for unified XX:00 analysis artifacts",
    )

    hourly_pump_unified_edge = subparsers.add_parser(
        "run-hourly-pump-unified-edge-search",
        help="Run full-year unified XX:00 execution search without hour-specific branches",
    )
    hourly_pump_unified_edge.add_argument(
        "--base-events",
        required=True,
        help="Path to trade_model_events.csv produced by hourly pump research",
    )
    hourly_pump_unified_edge.add_argument(
        "--output-dir",
        default=None,
        help="Directory for unified edge-search artifacts",
    )

    hourly_pump_short_edge = subparsers.add_parser(
        "run-hourly-pump-short-edge-search",
        help="Run unified short-side search after XX:00 anomalies without hour-specific branches",
    )
    hourly_pump_short_edge.add_argument(
        "--base-events",
        required=True,
        help="Path to trade_model_events.csv produced by hourly pump research",
    )
    hourly_pump_short_edge.add_argument(
        "--output-dir",
        default=None,
        help="Directory for short edge-search artifacts",
    )

    hourly_pump_session_short = subparsers.add_parser(
        "run-hourly-pump-session-short-search",
        help="Run short-edge search from raw cache for one session with minute-of-hour comparison",
    )
    hourly_pump_session_short.add_argument(
        "--session",
        required=True,
        choices=["asia", "europe", "america"],
        help="Session to analyze",
    )
    hourly_pump_session_short.add_argument(
        "--output-dir",
        default=None,
        help="Directory for session short-search artifacts",
    )
    hourly_pump_session_short.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Optional explicit symbol list",
    )

    ppa_stage = subparsers.add_parser(
        "ppa-stage",
        help="Run compact post_pump_absorption stage review across micro timeframes",
    )
    ppa_stage.add_argument(
        "preset",
        choices=[
            "s1", "s2", "s3", "s4", "s5", "s6",
            "t1", "t2", "t3", "t4", "t5", "t6",
            "stage1", "stage2", "stage3", "stage4", "stage5", "stage6",
            "through1", "through2", "through3", "through4", "through5", "through6",
        ],
        help="Stage preset: sN/stageN for one stage, tN/throughN for cumulative 1..N",
    )
    ppa_stage.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    ppa_stage.add_argument(
        "--top-n",
        type=_positive_int_for("--top-n"),
        default=None,
        help="Number of symbols after volume pre-rank",
    )
    ppa_stage.add_argument(
        "--timeframes",
        nargs="*",
        default=None,
        help="Micro timeframes to run, default: 1m 3m 5m",
    )
    ppa_stage.add_argument(
        "--ppa-profile",
        choices=["loose", "balanced", "strict"],
        default=None,
        help="Post pump absorption profile",
    )
    ppa_stage.add_argument("--ppa-deposit", type=float, default=None, help="Deposit used for post_pump_absorption sizing")
    ppa_stage.add_argument("--ppa-risk-pct", type=float, default=None, help="Risk per trade for post_pump_absorption")
    ppa_stage.add_argument("--output-dir", default=None, help="Root directory for stage review results")

    pno_stage = subparsers.add_parser(
        "pno-stage",
        help="Run compact PNO stage review on the PNO backtest timeframe pair",
    )
    pno_stage.add_argument(
        "preset",
        choices=[
            "s1", "s2", "s3", "s4", "s5",
            "t1", "t2", "t3", "t4", "t5",
            "stage1", "stage2", "stage3", "stage4", "stage5",
            "through1", "through2", "through3", "through4", "through5",
        ],
        help="Stage preset: sN/stageN for one stage, tN/throughN for cumulative 1..N",
    )
    pno_stage.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    pno_stage.add_argument(
        "--top-n",
        type=_positive_int_for("--top-n"),
        default=None,
        help="Number of symbols after volume pre-rank",
    )
    pno_stage.add_argument("--levels-tf", default=None, help="PNO pump/search timeframe, backtest pair only")
    pno_stage.add_argument("--entry-tf", default=None, help="PNO pullback/entry timeframe, backtest pair only")
    pno_stage.add_argument("--pno-deposit", type=float, default=None, help="Deposit used for PNO sizing")
    pno_stage.add_argument("--pno-risk-pct", type=float, default=None, help="Risk per trade for PNO")
    pno_stage.add_argument("--output-dir", default=None, help="Root directory for stage review results")

    quality = subparsers.add_parser("check-quality", help="Validate cache quality")
    quality.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    quality.add_argument("--output", default=None, help=f"Path to quality report (.json or .csv). Default: <results_dir>/{DEFAULT_QUALITY_REPORT_OUTPUT_FILE}")

    subparsers.add_parser("clear-cache", help="Fully clear the cache directory")
    return parser


def resolve_handler(command_name: str) -> Handler:
    handlers: dict[str, Handler] = {
        "fetch-data": commands.fetch_data,
        "fetch-ppa-cache": commands.fetch_ppa_cache,
        "update-cache": commands.update_cache,
        "run-backtest": commands.run_backtest,
        "run-ppa-research": commands.run_ppa_research,
        "run-hourly-pump-research": commands.run_hourly_pump_research,
        "run-hourly-pump-static-combo-analysis": commands.run_hourly_pump_static_combo_analysis,
        "run-hourly-pump-production-report": commands.run_hourly_pump_production_report,
        "run-hourly-pump-unified-analysis": commands.run_hourly_pump_unified_analysis,
        "run-hourly-pump-unified-edge-search": commands.run_hourly_pump_unified_edge_search,
        "run-hourly-pump-short-edge-search": commands.run_hourly_pump_short_edge_search,
        "run-hourly-pump-session-short-search": commands.run_hourly_pump_session_short_search,
        "ppa-stage": commands.run_ppa_stage,
        "pno-stage": commands.run_pno_stage,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
