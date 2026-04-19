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
    run_bt.add_argument("--days", type=_positive_int_for("--days"), default=None, help="Limit backtest to the last N days of cached data")
    run_bt.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for --days window (unix ms)")
    run_bt.add_argument("--levels-tf", default=None, help="Levels timeframe, e.g. 1d")
    run_bt.add_argument("--entry-tf", default=None, help="Entry timeframe, e.g. 15m")
    run_bt.add_argument(
        "--strategy",
        choices=["pno"],
        default="pno",
        help="Strategy id for backtest",
    )
    run_bt.add_argument("--pno-deposit", type=float, default=None, help="Deposit used for PNO position sizing")
    run_bt.add_argument("--pno-risk-pct", type=float, default=None, help="Risk per trade for PNO")
    run_bt.add_argument(
        "--pno-entry-confirmation-mode",
        choices=["cross", "close_above"],
        default=None,
        help="Filter PNO grid by entry confirmation mode (cross or close_above)",
    )
    run_bt.add_argument(
        "--pno-category-mode",
        choices=["all", "core", "discovery"],
        default=None,
        help="PNO category runtime mode: all, core only, or discovery only",
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

    plot_bt = subparsers.add_parser(
        "plot-backtest",
        help="Rebuild plots for a saved backtest run",
    )
    plot_bt.add_argument("--run-dir", required=True, help="Path to the saved backtest run directory")

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
    pno_stage.add_argument(
        "--pno-category-mode",
        choices=["all", "core", "discovery"],
        default=None,
        help="PNO category runtime mode: all, core only, or discovery only",
    )
    pno_stage.add_argument("--output-dir", default=None, help="Root directory for stage review results")

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
        "plot-backtest": commands.plot_backtest,
        "pno-stage": commands.run_pno_stage,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
