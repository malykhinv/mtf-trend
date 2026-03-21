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
    fetch.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    fetch.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_FETCH_DAYS)
    fetch.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    fetch.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")
    update = subparsers.add_parser("update-cache", help="Incrementally update the local cache")
    update.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    update.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_UPDATE_DAYS)
    update.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    update.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")
    run_bt = subparsers.add_parser("run-backtest", help="Run a backtest on cached data")
    run_bt.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    run_bt.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None, help="Number of symbols after bee_bite stage-1 filtering")
    run_bt.add_argument("--levels-tf", default=None, help="Levels timeframe, e.g. 1d")
    run_bt.add_argument("--entry-tf", default=None, help="Entry timeframe, e.g. 15m")
    run_bt.add_argument("--strategy", choices=["bee_bite"], default=None, help="Only bee_bite strategy is available")
    run_bt.add_argument("--bee-bite-grid", choices=["baseline", "expanded", "research"], default=None, help="Bee bite grid mode")
    run_bt.add_argument("--bee-bite-reclaim-mode", choices=["strict", "balanced", "aggressive"], default=None, help="Bee bite reclaim mode")
    run_bt.add_argument("--bee-bite-retest-mode", choices=["confirmation", "immediate"], default=None, help="Bee bite entry mode")
    run_bt.add_argument("--bee-bite-cooldown-hours", "--bee-bite-cooldown-bars", dest="bee_bite_cooldown_hours", type=_positive_int_for("--bee-bite-cooldown-hours"), default=None, help="Cooldown for bee_bite in hours")
    run_bt.add_argument("--bee-bite-max-age-range-hours", "--bee-bite-max-age-range", dest="bee_bite_max_age_range_hours", type=_positive_int_for("--bee-bite-max-age-range-hours"), default=None, help="Max range age for bee_bite in hours")
    run_bt.add_argument("--plot", default=False, help="Save diagnostic files for the best combination (true/false)")
    run_bt.add_argument("--plot-from-results", action="store_true", help="Build diagnostics from results.csv without a full backtest")
    run_bt.add_argument("--results-input", default=None, help="Path to CSV with results for --plot-from-results")
    run_bt.add_argument("--id", type=_positive_int_for("--id"), default=None, help="Combination ID in the CSV")

    report = subparsers.add_parser("make-report", help="Build a JSON report from backtest results")
    report.add_argument("--input", default=None, help="Path to input CSV with results")
    report.add_argument("--output", default=None, help="Path to output JSON report")

    stage1 = subparsers.add_parser("review-stage1", help="Find historical bee_bite stage-1 events and save review PNGs")
    stage1.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    stage1.add_argument("--tf", default=None, help="Review timeframe, e.g. 15m, 5m, 1m")
    stage1.add_argument("--tf-all", action="store_true", help="Run review for both 15m and 5m")
    stage1.add_argument("--plot-limit", type=_positive_int_for("--plot-limit"), default=20, help="Maximum number of stage-1 review plots")
    stage1.add_argument("--output", default=None, help="Path to CSV with detected stage-1 events")

    stage2 = subparsers.add_parser("review-stage2", help="Find bee_bite stage-2 structure and save PNGs with balance boxes")
    stage2.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    stage2.add_argument("--tf", default=None, help="Review timeframe, e.g. 15m, 5m, 1m")
    stage2.add_argument("--tf-all", action="store_true", help="Run review for both 15m and 5m")
    stage2.add_argument("--plot-limit", type=_positive_int_for("--plot-limit"), default=20, help="Maximum number of stage-2 review plots")
    stage2.add_argument("--plot-scope", choices=["latest", "all"], default=None, help="Plot only the latest setup or all found setups")
    stage2.add_argument("--output", default=None, help="Path to CSV with detected stage-2 structures")

    stage3 = subparsers.add_parser("review-stage3", help="Find bee_bite stage-3 sweeps and save PNGs with reclaim review")
    stage3.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    stage3.add_argument("--tf", default=None, help="Review timeframe, e.g. 15m, 5m, 1m")
    stage3.add_argument("--tf-all", action="store_true", help="Run review for both 15m and 5m")
    stage3.add_argument("--review-mode", choices=["snapshot", "evolution"], default="snapshot", help="Snapshot plots or candle-by-candle stage-2/stage-3 backtest")
    stage3.add_argument("--plot-limit", type=_positive_int_for("--plot-limit"), default=20, help="Maximum number of stage-3 review plots")
    stage3.add_argument("--plot-scope", choices=["latest", "all"], default=None, help="Plot only the latest setup or all found setups")
    stage3.add_argument("--output", default=None, help="Path to CSV with detected stage-3 sweeps")

    stage4 = subparsers.add_parser("postmortem-stage4", help="Run stage-4 postmortem on the built-in parameter grid")
    stage4.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    stage4.add_argument("--tf", default=None, help="Review timeframe, e.g. 15m, 5m, 1m")
    stage4.add_argument("--tf-all", action="store_true", help="Run postmortem for both 15m and 5m")
    stage4.add_argument("--output", default=None, help="Path to CSV with stage-4 postmortem results")

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
        "make-report": commands.make_report,
        "review-stage1": commands.review_stage1,
        "review-stage2": commands.review_stage2,
        "review-stage3": commands.review_stage3,
        "postmortem-stage4": commands.postmortem_stage4,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
