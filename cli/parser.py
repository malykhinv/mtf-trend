"""CLI parser."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from config import AppConfig
from constants import (
    DEFAULT_ANOMALY_LAB_DAYS,
    DEFAULT_FETCH_DAYS,
    DEFAULT_HOURLY_LEVELS_DAYS,
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
    anomaly_lab = subparsers.add_parser(
        "run-anomaly-lab",
        help="Run early anomaly-continuation research backtest on cached data",
    )
    anomaly_lab.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT:USDT")
    anomaly_lab.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_ANOMALY_LAB_DAYS)
    anomaly_lab.add_argument("--timeframe", default="1m", help="Legacy single-timeframe mode; used as setup timeframe unless --setup-timeframe is set")
    anomaly_lab.add_argument("--setup-timeframe", default=None, help="HTF setup timeframe, e.g. 5m")
    anomaly_lab.add_argument("--entry-timeframe", default=None, help="LTF execution timeframe, e.g. 30s. If omitted, equals setup/timeframe")
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
    anomaly_lab.add_argument("--min-hold-count", type=int, default=2)
    anomaly_lab.add_argument("--min-oi-change-pct-3x5m", type=float, default=None)
    anomaly_lab.add_argument("--require-oi-status-ok", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--exhaustion-profile", choices=["none", "mild", "balanced", "strict"], default="none")
    anomaly_lab.add_argument("--max-start-quote-ratio", type=float, default=None)
    anomaly_lab.add_argument("--max-start-trade-ratio", type=float, default=None)
    anomaly_lab.add_argument("--max-start-avg-trade-quote-size-ratio", type=float, default=None)
    anomaly_lab.add_argument("--max-start-quote-ratio-per-abs-return", type=float, default=None)
    anomaly_lab.add_argument("--max-start-trade-ratio-per-abs-return", type=float, default=None)
    anomaly_lab.add_argument("--max-start-range-pct-ratio-to-baseline", type=float, default=None)
    anomaly_lab.add_argument("--max-prior-up-down-whipsaw-to-impulse-range", type=float, default=0.60)
    anomaly_lab.add_argument("--min-next-taker-buy-quote-share", type=float, default=None)
    anomaly_lab.add_argument("--red-flag-profile", choices=["none", "cautious", "strict"], default="none")
    anomaly_lab.add_argument("--min-mark-close-vs-decision-close-basis", type=float, default=None)
    anomaly_lab.add_argument("--reject-oi-down-mark-discount", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--reject-stale-derivatives-context", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--max-start-taker-buy-quote-share-delta", type=float, default=None)
    anomaly_lab.add_argument("--max-next-taker-buy-quote-share-delta", type=float, default=None)
    anomaly_lab.add_argument("--max-initial-risk-pct", type=float, default=0.16)
    anomaly_lab.add_argument(
        "--entry-method",
        choices=["market", "break_box_high", "pullback_box_fraction"],
        default="market",
    )
    anomaly_lab.add_argument("--pullback-box-fraction", type=float, default=0.75)
    anomaly_lab.add_argument("--entry-timeout-candles", type=_positive_int_for("--entry-timeout-candles"), default=60)
    anomaly_lab.add_argument("--market-entry-latency-candles", type=_positive_int_for("--market-entry-latency-candles"), default=1)
    anomaly_lab.add_argument("--max-market-entry-drift-pct", type=float, default=0.003)
    anomaly_lab.add_argument("--min-market-rr-to-signal-tp1", type=float, default=0.75)
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
    anomaly_lab.add_argument("--render-charts", type=_str_to_bool, default=True)
    anomaly_lab.add_argument("--run-entry-grid", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--grid-oi3-values", default="0.01,0.02,0.03")
    anomaly_lab.add_argument("--grid-hold-values", default="1,2")
    anomaly_lab.add_argument("--grid-pullback-fractions", default="0.65,0.75,0.85")
    anomaly_lab.add_argument("--grid-exhaustion-profiles", default="none")
    anomaly_lab.add_argument("--grid-exit-rules", default="structural_trail")

    materialize_subminute = subparsers.add_parser(
        "materialize-anomaly-subminute-cache",
        help="Materialize honest 1s-derived 5s/15s/30s anomaly entry caches",
    )
    materialize_subminute.add_argument("--symbols", nargs="*", default=None, help="Optional symbols; default scans all 1s cache symbols")
    materialize_subminute.add_argument("--timeframes", nargs="*", default=["5s", "15s", "30s"], help="Target subminute timeframes derived from 1s")
    materialize_subminute.add_argument("--overwrite", type=_str_to_bool, default=False)
    materialize_subminute.add_argument("--output", default=None, help="Optional materialization manifest CSV path")

    backfill_aggtrades = subparsers.add_parser(
        "backfill-anomaly-aggtrade-cache",
        help="Backfill true 1s anomaly cache from Binance futures aggTrades",
    )
    backfill_aggtrades.add_argument("--symbols", nargs="*", default=None, help="Optional symbols; default uses liquid universe")
    backfill_aggtrades.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    backfill_aggtrades.add_argument("--days", type=_positive_int_for("--days"), default=14)
    backfill_aggtrades.add_argument("--end-timestamp-ms", type=int, default=None)
    backfill_aggtrades.add_argument("--chunk-hours", type=_positive_int_for("--chunk-hours"), default=1)
    backfill_aggtrades.add_argument("--max-symbols", type=_positive_int_for("--max-symbols"), default=None)
    backfill_aggtrades.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    backfill_aggtrades.add_argument("--output", default=None, help="Optional backfill manifest CSV path")

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
    anomaly_live.add_argument("--symbol-batch-size", type=_positive_int_for("--symbol-batch-size"), default=20)
    anomaly_live.add_argument(
        "--inactive-scan-slots-per-cycle",
        type=int,
        default=None,
        help="Optional cold round-robin slots per live cycle; 0 means only active/ticker-radar symbols are scanned.",
    )
    anomaly_live.add_argument(
        "--scan-hot-timeframes-per-symbol",
        type=_str_to_bool,
        default=True,
        help="When true, each selected symbol is scanned across all due timeframe pairs before moving on.",
    )
    anomaly_live.add_argument("--active-symbol-ttl-ms", type=_positive_int_for("--active-symbol-ttl-ms"), default=60_000)
    anomaly_live.add_argument("--ticker-radar-enabled", type=_str_to_bool, default=True)
    anomaly_live.add_argument("--ticker-radar-interval-seconds", type=float, default=5.0)
    anomaly_live.add_argument("--ticker-radar-watch-ttl-ms", type=_positive_int_for("--ticker-radar-watch-ttl-ms"), default=120_000)
    anomaly_live.add_argument("--ticker-radar-watch-batch-size", type=int, default=5)
    anomaly_live.add_argument("--ticker-radar-max-promotions-per-cycle", type=_positive_int_for("--ticker-radar-max-promotions-per-cycle"), default=20)
    anomaly_live.add_argument("--ticker-radar-min-price-delta-pct", type=float, default=0.003)
    anomaly_live.add_argument("--ticker-radar-min-quote-volume-delta-usdt", type=float, default=10_000.0)
    anomaly_live.add_argument("--ticker-radar-min-quote-volume-delta-ratio", type=float, default=3.0)
    anomaly_live.add_argument("--max-signal-age-ms", type=_positive_int_for("--max-signal-age-ms"), default=60_000)
    anomaly_live.add_argument("--max-entry-price-drift-pct", type=float, default=0.003)
    anomaly_live.add_argument("--min-executable-rr-to-signal-tp1", type=float, default=0.75)
    anomaly_live.add_argument("--max-position-amount-slippage-ratio", type=float, default=0.05)
    anomaly_live.add_argument("--scan-sleep-seconds", type=float, default=2.0)
    anomaly_live.add_argument(
        "--live-ohlcv-cache-enabled",
        type=_str_to_bool,
        default=True,
        help="Use local OHLCV parquet cache in live and fetch only missing ranges.",
    )
    anomaly_live.add_argument(
        "--live-ohlcv-cache-write-enabled",
        type=_str_to_bool,
        default=True,
        help="Persist live-fetched OHLCV/aggTrade-derived frames into the local parquet cache.",
    )
    anomaly_live.add_argument("--live-ohlcv-cache-flush-interval-seconds", type=float, default=10.0)
    anomaly_live.add_argument("--live-ohlcv-cache-max-buffer-rows", type=_positive_int_for("--live-ohlcv-cache-max-buffer-rows"), default=5_000)
    anomaly_live.add_argument("--trail-lookback-candles", type=_positive_int_for("--trail-lookback-candles"), default=5)
    anomaly_live.add_argument("--trail-buffer-r", type=float, default=0.10)

    top_growth = subparsers.add_parser(
        "run-anomaly-top-growth",
        help="Export standalone closed-hour top-growth artifacts without running live trading",
    )
    top_growth.add_argument("--symbols", nargs="*", default=None, help="Optional symbols; default scans all USDT swap symbols")
    top_growth.add_argument(
        "--period-start-utc",
        default=None,
        help="Closed 1h candle start, e.g. 2026-05-12T04:00:00Z. Default: previous closed hour",
    )
    top_growth.add_argument("--top-growth-min-return-pct", type=float, default=0.10)
    top_growth.add_argument("--top-growth-limit", type=_positive_int_for("--top-growth-limit"), default=5)
    top_growth.add_argument("--top-growth-fetch-spacing-seconds", type=float, default=0.05)

    hourly_levels = subparsers.add_parser(
        "run-hourly-levels",
        help="Scan cached symbols for important 1h overhead levels and save review charts",
    )
    hourly_levels.add_argument("--symbols", nargs="*", default=None, help="Optional symbols; default scans all cached symbols")
    hourly_levels.add_argument("--source-timeframe", default="5m", help="Cached timeframe to read; 5m is aggregated to 1h")
    hourly_levels.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_HOURLY_LEVELS_DAYS)
    hourly_levels.add_argument("--lookback-bars", type=_positive_int_for("--lookback-bars"), default=720)
    hourly_levels.add_argument("--chart-bars", type=_positive_int_for("--chart-bars"), default=240)
    hourly_levels.add_argument("--min-touches", type=_positive_int_for("--min-touches"), default=3)
    hourly_levels.add_argument("--min-touch-spacing-hours", type=_positive_int_for("--min-touch-spacing-hours"), default=6)
    hourly_levels.add_argument("--level-source-close-lookback-hours", type=_positive_int_for("--level-source-close-lookback-hours"), default=12)
    hourly_levels.add_argument("--touch-tolerance-pct", type=float, default=0.006)
    hourly_levels.add_argument("--min-bounce-pct", type=float, default=0.05)
    hourly_levels.add_argument("--bounce-lookahead-bars", type=_positive_int_for("--bounce-lookahead-bars"), default=12)
    hourly_levels.add_argument("--min-target-room-pct", type=float, default=0.05)
    hourly_levels.add_argument("--max-overhead-distance-pct", type=float, default=0.60)
    hourly_levels.add_argument("--min-recent-move-pct", type=float, default=0.05)
    hourly_levels.add_argument("--recent-move-lookback-bars", type=_positive_int_for("--recent-move-lookback-bars"), default=24)
    hourly_levels.add_argument("--reaction-to-move-threshold", type=float, default=0.80)
    hourly_levels.add_argument("--pivot-side-bars", type=_positive_int_for("--pivot-side-bars"), default=3)
    hourly_levels.add_argument("--break-close-tolerance-pct", type=float, default=0.004)
    hourly_levels.add_argument("--break-hold-bars", type=_positive_int_for("--break-hold-bars"), default=2)
    hourly_levels.add_argument("--reject-downtrend-symbols", type=_str_to_bool, default=True)
    hourly_levels.add_argument("--reject-downtrend-levels", type=_str_to_bool, default=True)
    hourly_levels.add_argument("--max-levels-per-symbol", type=_positive_int_for("--max-levels-per-symbol"), default=4)
    hourly_levels.add_argument("--reject-pierced-levels", type=_str_to_bool, default=True)
    hourly_levels.add_argument("--max-level-pierce-pct", type=float, default=0.0)
    hourly_levels.add_argument("--fast-source-trim", type=_str_to_bool, default=True)
    hourly_levels.add_argument("--save-empty-charts", type=_str_to_bool, default=False)
    hourly_levels.add_argument("--progress-every-symbols", type=_positive_int_for("--progress-every-symbols"), default=5)
    hourly_levels.add_argument("--progress-min-seconds", type=float, default=5.0)
    hourly_levels.add_argument("--output-dir", default=None)

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
        "run-anomaly-lab": commands.run_anomaly_lab,
        "materialize-anomaly-subminute-cache": commands.materialize_anomaly_subminute_cache,
        "backfill-anomaly-aggtrade-cache": commands.backfill_anomaly_aggtrade_cache,
        "run-anomaly-live": commands.run_anomaly_live,
        "run-anomaly-top-growth": commands.run_anomaly_top_growth,
        "run-hourly-levels": commands.run_hourly_levels,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
