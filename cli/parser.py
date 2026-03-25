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
        choices=["bee_bite", "post_pump_absorption"],
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

    quality = subparsers.add_parser("check-quality", help="Validate cache quality")
    quality.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    quality.add_argument("--output", default=None, help=f"Path to quality report (.json or .csv). Default: <results_dir>/{DEFAULT_QUALITY_REPORT_OUTPUT_FILE}")

    subparsers.add_parser("clear-cache", help="Fully clear the cache directory")
    return parser


def resolve_handler(command_name: str) -> Handler:
    handlers: dict[str, Handler] = {
        "fetch-data": commands.fetch_data,
        "update-cache": commands.update_cache,
        "run-backtest": commands.run_backtest,
        "run-ppa-research": commands.run_ppa_research,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
