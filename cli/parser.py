"""CLI parser."""

from __future__ import annotations

import argparse
from collections.abc import Callable

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


def _str_to_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


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
    fetch.add_argument("--with-derivatives-context", action="store_true", default=False, help="Also fetch funding/premium/long-short context")
    fetch.add_argument("--skip-derivatives-context", action="store_true", default=False, help="Deprecated no-op unless --with-derivatives-context is used")
    fetch.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")

    update = subparsers.add_parser("update-cache", help="Incrementally update the local cache")
    update.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    update.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    update.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_UPDATE_DAYS)
    update.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    update.add_argument("--timeframes", nargs="*", default=None, help="Timeframes to update, e.g. 1m 3m 5m")
    update.add_argument("--skip-open-interest", action="store_true", default=False, help="Skip open interest fetching")
    update.add_argument("--with-derivatives-context", action="store_true", default=False, help="Also fetch funding/premium/long-short context")
    update.add_argument("--skip-derivatives-context", action="store_true", default=False, help="Deprecated no-op unless --with-derivatives-context is used")
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
    run_bt.add_argument("--pno-risk-pct", type=float, default=None, help="Risk per position for PNO")
    run_bt.add_argument(
        "--pno-entry-confirmation-mode",
        choices=["close_above"],
        default=None,
        help="Filter PNO grid by entry confirmation mode (close_above)",
    )
    run_bt.add_argument(
        "--pno-category-mode",
        choices=["all", "core", "discovery"],
        default=None,
        help="PNO category runtime mode: all, core only, or discovery only",
    )
    run_bt.add_argument(
        "--pno-all-tf-pairs",
        action="store_true",
        help="Run all supported PNO backtest timeframe pairs in one command and save each pair into its own subdirectory",
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
    run_bt.add_argument(
        "--plot-rejected",
        default=None,
        help="Export PNO rejected stage-review artifacts when plotting (true/false)",
    )
    run_bt.add_argument(
        "--collect-diagnostics",
        default=None,
        help="Collect full PNO diagnostics during the backtest pass (true/false)",
    )
    run_bt.add_argument("--plot", default=None, help="Deprecated alias for --plot-rejected (true/false)")
    run_bt.add_argument(
        "--light-run",
        default=None,
        help="Deprecated inverse alias for --collect-diagnostics (true/false)",
    )
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
    pno_stage.add_argument("--pno-risk-pct", type=float, default=None, help="Risk per position for PNO")
    pno_stage.add_argument(
        "--pno-category-mode",
        choices=["all", "core", "discovery"],
        default=None,
        help="PNO category runtime mode: all, core only, or discovery only",
    )
    pno_stage.add_argument("--output-dir", default=None, help="Root directory for stage review results")

    anomaly_lab = subparsers.add_parser(
        "run-anomaly-lab",
        help="Run early anomaly-continuation research backtest on cached data",
    )
    anomaly_lab.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT:USDT")
    anomaly_lab.add_argument("--days", type=_positive_int_for("--days"), default=31)
    anomaly_lab.add_argument("--timeframe", default="1m")
    anomaly_lab.add_argument("--end-timestamp-ms", type=int, default=None)
    anomaly_lab.add_argument("--output-dir", default=None)
    anomaly_lab.add_argument("--baseline-candles", type=_positive_int_for("--baseline-candles"), default=60)
    anomaly_lab.add_argument("--confirmation-candles", type=_positive_int_for("--confirmation-candles"), default=4)
    anomaly_lab.add_argument("--forward-high-candles", type=_positive_int_for("--forward-high-candles"), default=240)
    anomaly_lab.add_argument("--forward-low-candles", type=_positive_int_for("--forward-low-candles"), default=60)
    anomaly_lab.add_argument("--min-quote-ratio-start", type=float, default=5.0)
    anomaly_lab.add_argument("--min-trade-ratio-start", type=float, default=5.0)
    anomaly_lab.add_argument("--min-price-retention", type=float, default=0.70)
    anomaly_lab.add_argument("--max-price-retention", type=float, default=None)
    anomaly_lab.add_argument("--min-verticality-score", type=float, default=0.25)
    anomaly_lab.add_argument("--min-hold-count", type=int, default=0)
    anomaly_lab.add_argument("--min-oi-change-pct-3x5m", type=float, default=None)
    anomaly_lab.add_argument("--require-oi-status-ok", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--exhaustion-profile", choices=["none", "mild", "balanced", "strict"], default="none")
    anomaly_lab.add_argument("--max-start-quote-ratio", type=float, default=None)
    anomaly_lab.add_argument("--max-start-trade-ratio", type=float, default=None)
    anomaly_lab.add_argument("--max-start-avg-trade-quote-size-ratio", type=float, default=None)
    anomaly_lab.add_argument("--max-start-quote-ratio-per-abs-return", type=float, default=None)
    anomaly_lab.add_argument("--max-start-range-pct-ratio-to-baseline", type=float, default=None)
    anomaly_lab.add_argument("--max-prior-up-down-whipsaw-to-impulse-range", type=float, default=0.60)
    anomaly_lab.add_argument("--min-next-taker-buy-quote-share", type=float, default=None)
    anomaly_lab.add_argument("--max-initial-risk-pct", type=float, default=0.16)
    anomaly_lab.add_argument(
        "--entry-method",
        choices=["market", "break_box_high", "pullback_box_fraction"],
        default="market",
    )
    anomaly_lab.add_argument("--pullback-box-fraction", type=float, default=0.75)
    anomaly_lab.add_argument("--entry-timeout-candles", type=_positive_int_for("--entry-timeout-candles"), default=60)
    anomaly_lab.add_argument("--tp1-r", type=float, default=1.0)
    anomaly_lab.add_argument("--tp1-fraction", type=float, default=0.50)
    anomaly_lab.add_argument("--trail-lookback-candles", type=_positive_int_for("--trail-lookback-candles"), default=5)
    anomaly_lab.add_argument("--trail-buffer-r", type=float, default=0.10)
    anomaly_lab.add_argument(
        "--exit-rule",
        choices=["structural_trail", "ema20_close", "ema20_negative_pnl_be_escape"],
        default="structural_trail",
    )
    anomaly_lab.add_argument("--max-hold-candles", type=_positive_int_for("--max-hold-candles"), default=240)
    anomaly_lab.add_argument("--fee-rate", type=float, default=0.0004)
    anomaly_lab.add_argument("--run-entry-grid", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--grid-oi3-values", default="0.01,0.02,0.03")
    anomaly_lab.add_argument("--grid-hold-values", default="1,2")
    anomaly_lab.add_argument("--grid-pullback-fractions", default="0.65,0.75,0.85")
    anomaly_lab.add_argument("--grid-exhaustion-profiles", default="none")
    anomaly_lab.add_argument("--grid-exit-rules", default="structural_trail")

    anomaly_live = subparsers.add_parser(
        "run-anomaly-live",
        help="Run strict REST-only micro-live anomaly wake-up loop",
    )
    anomaly_live.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT:USDT")
    anomaly_live.add_argument("--confirm-real-orders", action="store_true", help="Required guard for real orders")
    anomaly_live.add_argument("--max-cycles", type=_positive_int_for("--max-cycles"), default=None)
    anomaly_live.add_argument(
        "--pump-categories",
        default="balanced_market,mild_market",
        help="Comma-separated live pump categories, tried by priority. Supported: balanced_market,mild_market",
    )
    anomaly_live.add_argument("--baseline-candles", type=_positive_int_for("--baseline-candles"), default=60)
    anomaly_live.add_argument("--confirmation-candles", type=_positive_int_for("--confirmation-candles"), default=4)
    anomaly_live.add_argument("--min-quote-ratio-start", type=float, default=5.0)
    anomaly_live.add_argument("--min-trade-ratio-start", type=float, default=5.0)
    anomaly_live.add_argument("--min-price-retention", type=float, default=0.70)
    anomaly_live.add_argument("--min-verticality-score", type=float, default=0.25)
    anomaly_live.add_argument("--min-hold-count", type=int, default=2)
    anomaly_live.add_argument("--min-oi-change-pct-3x5m", type=float, default=0.05)
    anomaly_live.add_argument("--max-initial-risk-pct", type=float, default=0.16)
    anomaly_live.add_argument("--stop-buffer-range-fraction", type=float, default=0.05)
    anomaly_live.add_argument("--max-prior-up-down-whipsaw-to-impulse-range", type=float, default=0.60)
    anomaly_live.add_argument("--position-notional-usdt", type=float, default=12.0)
    anomaly_live.add_argument("--max-open-positions", type=_positive_int_for("--max-open-positions"), default=3)
    anomaly_live.add_argument("--scan-sleep-seconds", type=float, default=2.0)
    anomaly_live.add_argument("--trail-lookback-candles", type=_positive_int_for("--trail-lookback-candles"), default=5)
    anomaly_live.add_argument("--trail-buffer-r", type=float, default=0.10)

    quality = subparsers.add_parser("check-quality", help="Validate cache quality")
    quality.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    quality.add_argument("--output", default=None, help=f"Path to quality report (.json or .csv). Default: <results_dir>/{DEFAULT_QUALITY_REPORT_OUTPUT_FILE}")

    subparsers.add_parser("clear-cache", help="Fully clear the cache directory")
    return parser


def resolve_handler(command_name: str) -> Handler:
    from cli import commands

    handlers: dict[str, Handler] = {
        "fetch-data": commands.fetch_data,
        "update-cache": commands.update_cache,
        "run-backtest": commands.run_backtest,
        "plot-backtest": commands.plot_backtest,
        "pno-stage": commands.run_pno_stage,
        "run-anomaly-lab": commands.run_anomaly_lab,
        "run-anomaly-live": commands.run_anomaly_live,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
