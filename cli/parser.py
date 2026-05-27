"""CLI parser."""

from __future__ import annotations

import argparse
from pathlib import Path
from collections.abc import Callable

from config import AppConfig
from constants import (
    DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS,
    DEFAULT_ANOMALY_LAB_DAYS,
    DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT,
    DEFAULT_FETCH_DAYS,
    DEFAULT_HOURLY_LEVELS_DAYS,
    DEFAULT_MIN_VOLUME_USD,
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
    DEFAULT_SLIPPAGE,
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


def non_negative_int(value: str, argument_name: str = "value") -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{argument_name} must be >= 0") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{argument_name} must be >= 0")
    return parsed


def _non_negative_int_for(argument_name: str) -> Callable[[str], int]:
    def _validator(value: str) -> int:
        return non_negative_int(value, argument_name=argument_name)

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
    anomaly_lab.add_argument("--timeframe", default=None, help="Legacy single-timeframe mode; used as setup timeframe unless --setup-timeframe is set")
    anomaly_lab.add_argument("--setup-timeframe", default=None, help="HTF setup timeframe, e.g. 5m")
    anomaly_lab.add_argument("--entry-timeframe", default=None, help="LTF execution timeframe, e.g. 30s. If omitted, equals setup/timeframe")
    anomaly_lab.add_argument(
        "--pair-collection-mode",
        choices=[
            "forming",
            "post_htf_close_ltf_confirmation",
            "post_htf_close_ltf_forward_confirmation",
            "bare_htf_short_fader",
        ],
        default="forming",
        help=(
            "For setup/entry pairs, use forming to evaluate every LTF decision inside the current HTF candle, "
            "post_htf_close_ltf_confirmation to evaluate one signal after the HTF candle closes, "
            "post_htf_close_ltf_forward_confirmation to wait for LTF confirmation after that close, "
            "or bare_htf_short_fader to research post-close short/fader triggers from bare HTF anomalies."
        ),
    )
    anomaly_lab.add_argument("--end-timestamp-ms", type=int, default=None)
    anomaly_lab.add_argument("--output-dir", default=None)
    anomaly_lab.add_argument(
        "--reuse-candidates-dir",
        default=None,
        help="Reuse existing anomaly_candidates.csv artifacts from a previous anomaly-lab output root.",
    )
    anomaly_lab.add_argument(
        "--allow-cache-snapshot-universe",
        type=_str_to_bool,
        default=False,
        help=(
            "Allow historical anomaly-lab runs with no explicit --symbols to scan the current local cache "
            "universe. By default this is refused when --end-timestamp-ms is set because it is not an "
            "as-of historical listing universe."
        ),
    )
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
    anomaly_lab.add_argument(
        "--red-flag-profile",
        choices=["none", "cautious", "strict", "runner_balanced", "runner_reclaim", "runner_flow", "runner_oi_confirmed"],
        default="none",
    )
    anomaly_lab.add_argument("--min-mark-close-vs-decision-close-basis", type=float, default=None)
    anomaly_lab.add_argument("--reject-oi-down-mark-discount", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--reject-stale-derivatives-context", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--max-start-taker-buy-quote-share-delta", type=float, default=None)
    anomaly_lab.add_argument("--max-next-taker-buy-quote-share-delta", type=float, default=None)
    anomaly_lab.add_argument("--min-flow-hold-count", type=int, default=None)
    anomaly_lab.add_argument("--max-prior-spike-count-72h", type=int, default=None)
    anomaly_lab.add_argument("--max-prior-fast-fade-count-72h", type=int, default=None)
    anomaly_lab.add_argument("--min-start-lower-wick-to-range", type=float, default=None)
    anomaly_lab.add_argument("--max-start-upper-wick-to-range", type=float, default=None)
    anomaly_lab.add_argument("--max-initial-risk-pct", type=float, default=0.16)
    anomaly_lab.add_argument(
        "--entry-method",
        choices=["market", "break_box_high", "pullback_box_fraction"],
        default="market",
    )
    anomaly_lab.add_argument("--pullback-box-fraction", type=float, default=0.75)
    anomaly_lab.add_argument("--entry-timeout-candles", type=_positive_int_for("--entry-timeout-candles"), default=60)
    anomaly_lab.add_argument("--market-entry-latency-candles", type=_positive_int_for("--market-entry-latency-candles"), default=1)
    anomaly_lab.add_argument(
        "--latency",
        type=_str_to_bool,
        default=False,
        help="Run hidden 1s-cache live-latency execution stress test and write anomaly_latency_grid_summary.csv.",
    )
    anomaly_lab.add_argument(
        "--max-market-entry-drift-pct",
        type=float,
        default=DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT,
    )
    anomaly_lab.add_argument("--min-market-rr-to-signal-tp1", type=float, default=0.70)
    anomaly_lab.add_argument("--tp1-r", type=float, default=0.75)
    anomaly_lab.add_argument("--tp1-fraction", type=float, default=1.0)
    anomaly_lab.add_argument("--trail-lookback-candles", type=_positive_int_for("--trail-lookback-candles"), default=5)
    anomaly_lab.add_argument("--trail-buffer-r", type=float, default=0.10)
    anomaly_lab.add_argument(
        "--exit-rule",
        choices=["structural_trail", "ema20_close", "ema20_negative_pnl_be_escape"],
        default="structural_trail",
    )
    anomaly_lab.add_argument("--max-hold-candles", type=_positive_int_for("--max-hold-candles"), default=240)
    anomaly_lab.add_argument("--max-open-positions", type=_positive_int_for("--max-open-positions"), default=DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS)
    anomaly_lab.add_argument("--fee-rate", type=float, default=0.0004)
    anomaly_lab.add_argument("--entry-slippage-pct", type=float, default=DEFAULT_SLIPPAGE)
    anomaly_lab.add_argument("--exit-slippage-pct", type=float, default=DEFAULT_SLIPPAGE)
    anomaly_lab.add_argument("--short-fader-analysis-minutes", type=_positive_int_for("--short-fader-analysis-minutes"), default=60)
    anomaly_lab.add_argument("--short-fader-target-r", type=float, default=2.5)
    anomaly_lab.add_argument("--short-fader-min-prior-spike-count-72h", type=_non_negative_int_for("--short-fader-min-prior-spike-count-72h"), default=10)
    anomaly_lab.add_argument("--short-fader-min-prior-fast-fade-count-72h", type=_non_negative_int_for("--short-fader-min-prior-fast-fade-count-72h"), default=3)
    anomaly_lab.add_argument("--short-fader-prefilter-min-quote-ratio", type=float, default=10.0)
    anomaly_lab.add_argument("--short-fader-prefilter-min-trade-ratio", type=float, default=8.0)
    anomaly_lab.add_argument("--short-fader-prefilter-min-htf-return", type=float, default=0.015)
    anomaly_lab.add_argument(
        "--short-fader-triggers",
        default=(
            "failed_new_high,taker_fade_red,close_below_htf_close,close_below_post_mid,"
            "lower_high_close_down,effort_no_progress,pullback_without_recovery"
        ),
    )
    anomaly_lab.add_argument("--short-fader-require-prior-context", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--short-fader-run-exit-grid", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--render-charts", type=_str_to_bool, default=True)
    anomaly_lab.add_argument("--targeted-flow-backfill", type=_str_to_bool, default=True)
    anomaly_lab.add_argument("--run-entry-grid", type=_str_to_bool, default=False)
    anomaly_lab.add_argument("--grid-oi3-values", default="0.01,0.02,0.03")
    anomaly_lab.add_argument("--grid-hold-values", default="1,2")
    anomaly_lab.add_argument("--grid-pullback-fractions", default="0.65,0.75,0.85")
    anomaly_lab.add_argument("--grid-exhaustion-profiles", default="none")
    anomaly_lab.add_argument("--grid-exit-rules", default="structural_trail")

    runner_discovery = subparsers.add_parser(
        "run-htf-ltf-runner-discovery",
        help="Research HTF anomaly plus LTF confirmation runner discovery with structural no-TP replay",
    )
    runner_discovery.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT:USDT")
    runner_discovery.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_ANOMALY_LAB_DAYS)
    runner_discovery.add_argument("--htf-timeframe", default="1m")
    runner_discovery.add_argument("--ltf-timeframe", default="5s")
    runner_discovery.add_argument("--end-timestamp-ms", type=int, default=None)
    runner_discovery.add_argument("--output-dir", default=None)
    runner_discovery.add_argument("--baseline-candles", type=_positive_int_for("--baseline-candles"), default=60)
    runner_discovery.add_argument("--dormancy-candles", type=_positive_int_for("--dormancy-candles"), default=30)
    runner_discovery.add_argument("--pregrowth-candles", type=_positive_int_for("--pregrowth-candles"), default=5)
    runner_discovery.add_argument("--runner-target-return-pct", type=float, default=0.10)
    runner_discovery.add_argument("--runner-horizon-minutes", type=_positive_int_for("--runner-horizon-minutes"), default=60)
    runner_discovery.add_argument("--min-htf-quote-ratio", type=float, default=5.0)
    runner_discovery.add_argument("--min-htf-trade-ratio", type=float, default=5.0)
    runner_discovery.add_argument("--min-htf-return-pct", type=float, default=0.010)
    runner_discovery.add_argument("--min-dormancy-to-anomaly-quote-ratio", type=float, default=6.0)
    runner_discovery.add_argument("--min-dormancy-to-anomaly-trade-ratio", type=float, default=5.0)
    runner_discovery.add_argument("--max-dormancy-range-pct-median", type=float, default=0.004)
    runner_discovery.add_argument("--min-pregrowth-return-pct", type=float, default=0.002)
    runner_discovery.add_argument("--max-pregrowth-single-candle-return-pct", type=float, default=0.020)
    runner_discovery.add_argument("--min-pregrowth-positive-step-share", type=float, default=0.55)
    runner_discovery.add_argument("--min-pregrowth-oi-change-pct", type=float, default=0.0)
    runner_discovery.add_argument("--require-pregrowth-oi", type=_str_to_bool, default=False)
    runner_discovery.add_argument("--ltf-min-confirm-candles", type=_positive_int_for("--ltf-min-confirm-candles"), default=6)
    runner_discovery.add_argument("--ltf-max-confirm-candles", type=_positive_int_for("--ltf-max-confirm-candles"), default=24)
    runner_discovery.add_argument("--min-ltf-confirm-return-pct", type=float, default=0.004)
    runner_discovery.add_argument("--min-ltf-quote-pace-ratio", type=float, default=3.0)
    runner_discovery.add_argument("--min-ltf-trade-pace-ratio", type=float, default=3.0)
    runner_discovery.add_argument("--min-ltf-taker-buy-share", type=float, default=None)
    runner_discovery.add_argument("--min-ltf-second-half-return-pct", type=float, default=0.0)
    runner_discovery.add_argument("--min-ltf-quote-acceleration", type=float, default=1.0)
    runner_discovery.add_argument("--min-ltf-trade-acceleration", type=float, default=1.0)
    runner_discovery.add_argument("--max-entry-drift-pct", type=float, default=0.004)
    runner_discovery.add_argument("--max-initial-risk-pct", type=float, default=0.05)
    runner_discovery.add_argument("--structural-stop-buffer-pct", type=float, default=0.0005)
    runner_discovery.add_argument("--trail-lookback-candles", type=_positive_int_for("--trail-lookback-candles"), default=6)
    runner_discovery.add_argument("--trail-buffer-pct", type=float, default=0.0005)
    runner_discovery.add_argument("--max-hold-candles", type=_positive_int_for("--max-hold-candles"), default=720)
    runner_discovery.add_argument("--max-open-positions", type=_positive_int_for("--max-open-positions"), default=1)
    runner_discovery.add_argument("--fee-rate", type=float, default=0.0004)
    runner_discovery.add_argument("--entry-slippage-pct", type=float, default=DEFAULT_SLIPPAGE)
    runner_discovery.add_argument("--exit-slippage-pct", type=float, default=DEFAULT_SLIPPAGE)

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
    backfill_aggtrades.add_argument("--start-timestamp-ms", type=int, default=None)
    backfill_aggtrades.add_argument("--end-timestamp-ms", type=int, default=None)
    backfill_aggtrades.add_argument("--window-timestamps-ms", nargs="*", type=int, default=None)
    backfill_aggtrades.add_argument("--window-before-ms", type=_non_negative_int_for("--window-before-ms"), default=5_400_000)
    backfill_aggtrades.add_argument("--window-after-ms", type=_non_negative_int_for("--window-after-ms"), default=2_700_000)
    backfill_aggtrades.add_argument("--chunk-hours", type=_positive_int_for("--chunk-hours"), default=1)
    backfill_aggtrades.add_argument("--max-symbols", type=_positive_int_for("--max-symbols"), default=None)
    backfill_aggtrades.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    backfill_aggtrades.add_argument("--output", default=None, help="Optional backfill manifest CSV path")

    anomaly_live2 = subparsers.add_parser(
        "run-anomaly-live2",
        help="Run deadline-driven anomaly live2 runtime",
    )
    anomaly_live2.add_argument("--symbols", nargs="*", default=None, help="Optional explicit futures symbols, e.g. BTC/USDT:USDT")
    anomaly_live2.add_argument("--universe-max-symbols", type=_positive_int_for("--universe-max-symbols"), default=600)
    anomaly_live2.add_argument("--universe-min-quote-volume-24h", type=float, default=30_000.0)
    anomaly_live2.add_argument("--universe-min-trade-count-24h", type=_non_negative_int_for("--universe-min-trade-count-24h"), default=0)
    anomaly_live2.add_argument("--universe-min-auto-symbols", type=_non_negative_int_for("--universe-min-auto-symbols"), default=300)
    anomaly_live2.add_argument("--decision-loop-interval-seconds", type=float, default=0.05)
    anomaly_live2.add_argument("--decision-backlog-expire-ms", type=_positive_int_for("--decision-backlog-expire-ms"), default=5_000)
    anomaly_live2.add_argument("--startup-warmup-lookback-minutes", type=_positive_int_for("--startup-warmup-lookback-minutes"), default=15)
    anomaly_live2.add_argument("--startup-warmup-max-trades-per-symbol", type=_positive_int_for("--startup-warmup-max-trades-per-symbol"), default=1000)
    anomaly_live2.add_argument("--startup-warmup-max-pages-per-symbol", type=_positive_int_for("--startup-warmup-max-pages-per-symbol"), default=1)
    anomaly_live2.add_argument("--startup-htf-baseline-lookback-minutes", type=_positive_int_for("--startup-htf-baseline-lookback-minutes"), default=75)
    anomaly_live2.add_argument("--ws-connection-max-age-seconds", type=float, default=84_600.0)
    anomaly_live2.add_argument("--user-data-stream-startup-wait-seconds", type=float, default=10.0)
    anomaly_live2.add_argument("--user-data-stream-keepalive-interval-seconds", type=float, default=1_800.0)
    anomaly_live2.add_argument("--mark-price-stale-ms", type=_positive_int_for("--mark-price-stale-ms"), default=5_000)
    anomaly_live2.add_argument("--mark-price-startup-wait-seconds", type=float, default=10.0)
    anomaly_live2.add_argument("--oi-stale-ms", type=_positive_int_for("--oi-stale-ms"), default=720_000)
    anomaly_live2.add_argument("--oi-poll-interval-seconds", type=float, default=5.0)
    anomaly_live2.add_argument("--oi-symbol-cooldown-seconds", type=float, default=60.0)
    anomaly_live2.add_argument("--oi-lookback-minutes", type=_positive_int_for("--oi-lookback-minutes"), default=20)
    anomaly_live2.add_argument("--oi-max-symbols-per-cycle", type=_positive_int_for("--oi-max-symbols-per-cycle"), default=20)
    anomaly_live2.add_argument("--oi-radar-symbol-ttl-ms", type=_positive_int_for("--oi-radar-symbol-ttl-ms"), default=60_000)
    anomaly_live2.add_argument("--prior-context-stale-ms", type=_positive_int_for("--prior-context-stale-ms"), default=1_200_000)
    anomaly_live2.add_argument("--prior-context-poll-interval-seconds", type=float, default=10.0)
    anomaly_live2.add_argument("--prior-context-symbol-cooldown-seconds", type=float, default=600.0)
    anomaly_live2.add_argument("--prior-context-lookback-hours", type=int, default=24, help="Live2 prior context lookback; must stay exactly 24.")
    anomaly_live2.add_argument("--prior-context-max-symbols-per-cycle", type=_positive_int_for("--prior-context-max-symbols-per-cycle"), default=10)
    anomaly_live2.add_argument("--prior-context-radar-symbol-ttl-ms", type=_positive_int_for("--prior-context-radar-symbol-ttl-ms"), default=60_000)
    anomaly_live2.add_argument("--prior-context-spike-return-pct", type=float, default=0.03)
    anomaly_live2.add_argument("--prior-context-fast-fade-retrace-fraction", type=float, default=0.55)
    anomaly_live2.add_argument("--top-growth-enabled", type=_str_to_bool, default=True)
    anomaly_live2.add_argument("--top-growth-min-return-pct", type=float, default=0.10)
    anomaly_live2.add_argument("--top-growth-limit", type=_positive_int_for("--top-growth-limit"), default=5)
    anomaly_live2.add_argument("--top-growth-symbols-per-cycle", type=_positive_int_for("--top-growth-symbols-per-cycle"), default=1)
    anomaly_live2.add_argument("--top-growth-max-cycle-seconds", type=float, default=0.75)
    anomaly_live2.add_argument("--top-growth-fetch-spacing-seconds", type=float, default=0.02)
    anomaly_live2.add_argument("--execution-order-notional-usdt", type=float, default=12.0)
    anomaly_live2.add_argument("--execution-max-open-positions", type=int, default=0, help="0 means unlimited live2 protected positions.")
    anomaly_live2.add_argument("--position-supervisor-tp1-close-fraction", type=float, default=0.5)
    anomaly_live2.add_argument("--position-supervisor-early-exit-enabled", type=_str_to_bool, default=True)
    anomaly_live2.add_argument("--position-supervisor-early-exit-min-hold-candles", type=_positive_int_for("--position-supervisor-early-exit-min-hold-candles"), default=6)
    anomaly_live2.add_argument("--position-supervisor-early-exit-stall-candles", type=_positive_int_for("--position-supervisor-early-exit-stall-candles"), default=12)
    anomaly_live2.add_argument("--position-supervisor-early-exit-min-mfe-r", type=float, default=0.25)
    anomaly_live2.add_argument("--output-dir", default=None, help="Optional artifact output directory")

    live_order_smoke = subparsers.add_parser(
        "run-live-order-smoke",
        help="Place one minimal real Binance USD-M order, verify stop visibility, then cleanup reduce-only",
    )
    live_order_smoke.add_argument("--symbol", required=True, help="Single futures symbol, e.g. EDEN/USDT:USDT")
    live_order_smoke.add_argument(
        "--confirm-real-order-smoke",
        action="store_true",
        help="Required explicit guard: this command places real Binance futures orders.",
    )
    live_order_smoke.add_argument("--notional-usdt", type=float, default=12.0)
    live_order_smoke.add_argument(
        "--max-notional-usdt",
        type=float,
        default=25.0,
        help="Hard guard against fat-finger notional. Command aborts when --notional-usdt is above this value.",
    )
    live_order_smoke.add_argument(
        "--stop-distance-pct",
        type=float,
        default=0.05,
        help="Stop distance from entry fill as a fraction. Default 0.05 means 5%% below entry for the long smoke.",
    )
    live_order_smoke.add_argument("--verification-attempts", type=_positive_int_for("--verification-attempts"), default=5)
    live_order_smoke.add_argument("--verification-sleep-seconds", type=float, default=0.5)
    live_order_smoke.add_argument(
        "--replacement-stop-distance-pct",
        type=float,
        default=None,
        help=(
            "Optional management-smoke step: create a new closer stop before cleanup. "
            "Must be greater than 0 and lower than --stop-distance-pct."
        ),
    )
    live_order_smoke.add_argument(
        "--close-position-before-stop-cancel",
        action="store_true",
        help=(
            "For management smoke, close the position reduce-only while the verified stop is still open, "
            "then cancel remaining stop/orders and assert final flat/no orders."
        ),
    )
    live_order_smoke.add_argument(
        "--leave-protected-position-open",
        action="store_true",
        help="After stop verification, leave the protected position open instead of cancelling stop and closing reduce-only.",
    )
    live_order_smoke.add_argument("--output-dir", default=None)


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
        "run-htf-ltf-runner-discovery": commands.run_htf_ltf_runner_discovery,
        "materialize-anomaly-subminute-cache": commands.materialize_anomaly_subminute_cache,
        "backfill-anomaly-aggtrade-cache": commands.backfill_anomaly_aggtrade_cache,
        "run-anomaly-live2": commands.run_anomaly_live2,
        "run-live-order-smoke": commands.run_live_order_smoke,
        "run-anomaly-top-growth": commands.run_anomaly_top_growth,
        "run-hourly-levels": commands.run_hourly_levels,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
