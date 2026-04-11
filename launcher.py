"""PNO-only launcher for common project modes."""

from __future__ import annotations

import argparse
import os
import sys
from ctypes import windll
from typing import Any

from cli import commands
from config import AppConfig, load_config

MODE_FETCH_DATA = "fetch-data"
MODE_UPDATE_CACHE = "update-cache"
MODE_RUN_BACKTEST = "run-backtest"
MODE_PNO_STAGE = "pno-stage"
MODE_CHECK_QUALITY = "check-quality"
MODE_CLEAR_CACHE = "clear-cache"

MODE_LABELS: dict[str, str] = {
    MODE_FETCH_DATA: "Fetch market cache",
    MODE_UPDATE_CACHE: "Update market cache",
    MODE_RUN_BACKTEST: "Run PNO backtest",
    MODE_PNO_STAGE: "Run PNO stage diagnostics",
    MODE_CHECK_QUALITY: "Check cache quality",
    MODE_CLEAR_CACHE: "Clear cache",
}


def _to_bool(value: Any, *, fallback: bool | None = False) -> bool | None:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _force_single_thread_mode() -> None:
    single_thread_env = {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    for key, value in single_thread_env.items():
        os.environ[key] = value


def _configure_console_encoding() -> None:
    if os.name == "nt":
        try:
            windll.kernel32.SetConsoleCP(65001)
            windll.kernel32.SetConsoleOutputCP(65001)
        except OSError:
            pass
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="launcher", description="Run PNO project modes without typing raw CLI commands")
    parser.add_argument("--env", default=".env", help="Path to env file")
    parser.add_argument("--mode", choices=tuple(MODE_LABELS.keys()), default=None, help="Single run mode")
    parser.add_argument("--top-n", type=int, default=None, help="Limit symbols after liquidity ranking")
    parser.add_argument("--min-volume-usd", type=float, default=None, help="Minimum 24h volume in USD")
    parser.add_argument("--days", type=int, default=30, help="Number of trailing days")
    parser.add_argument("--timeframes", nargs="*", default=None, help="Cache timeframes, e.g. 5m 1m")
    parser.add_argument("--skip-open-interest", action="store_true", default=False, help="Skip open interest fetching")
    parser.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")
    parser.add_argument("--symbols", nargs="*", default=None, help="Explicit symbol list, e.g. BTC/USDT ETH/USDT")
    parser.add_argument("--levels-tf", default=None, help="Levels timeframe for PNO")
    parser.add_argument("--entry-tf", default=None, help="Entry timeframe for PNO")
    parser.add_argument("--strategy", choices=["pno"], default="pno", help="Strategy id")
    parser.add_argument("--pno-deposit", type=float, default=None, help="Deposit used for PNO sizing")
    parser.add_argument("--pno-risk-pct", type=float, default=None, help="Risk per trade for PNO")
    parser.add_argument("--pno-entry-confirmation-mode", choices=["baseline_cross", "close_above"], default=None, help="PNO entry confirmation mode")
    parser.add_argument("--pno-stage", type=int, default=None, help="Single PNO stage to export")
    parser.add_argument("--pno-through-stage", type=int, default=None, help="Export all PNO stages through this number")
    parser.add_argument("--output-dir", default=None, help="Directory for results or diagnostics")
    parser.add_argument("--output", default=None, help="Output JSON/CSV path")
    parser.add_argument("--plot", default=None, help="Save diagnostic files (true/false)")
    parser.add_argument("--id", type=int, default=None, help="Combination ID for plot-from-results mode")
    parser.add_argument("--plot-from-results", action="store_true", help="Build diagnostics from results.csv without a full backtest")
    parser.add_argument("--results-input", default=None, help="Path to CSV with results for --plot-from-results")
    return parser


def _task_namespace(cli_args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        top_n=cli_args.top_n,
        min_volume_usd=cli_args.min_volume_usd,
        days=cli_args.days,
        timeframes=cli_args.timeframes,
        skip_open_interest=cli_args.skip_open_interest,
        end_timestamp_ms=cli_args.end_timestamp_ms,
        symbols=cli_args.symbols,
        levels_tf=cli_args.levels_tf,
        entry_tf=cli_args.entry_tf,
        strategy="pno",
        pno_deposit=cli_args.pno_deposit,
        pno_risk_pct=cli_args.pno_risk_pct,
        pno_entry_confirmation_mode=cli_args.pno_entry_confirmation_mode,
        pno_stage=cli_args.pno_stage,
        pno_through_stage=cli_args.pno_through_stage,
        output_dir=cli_args.output_dir,
        output=cli_args.output,
        results_input=cli_args.results_input,
        plot=_to_bool(cli_args.plot, fallback=False),
        plot_from_results=cli_args.plot_from_results,
        id=cli_args.id,
    )


def _run_mode(config: AppConfig, mode: str, task_args: argparse.Namespace) -> int:
    handlers = {
        MODE_FETCH_DATA: commands.fetch_data,
        MODE_UPDATE_CACHE: commands.update_cache,
        MODE_RUN_BACKTEST: commands.run_backtest,
        MODE_PNO_STAGE: commands.run_pno_stage,
        MODE_CHECK_QUALITY: commands.check_quality,
        MODE_CLEAR_CACHE: commands.clear_cache,
    }
    return handlers[mode](config, task_args)


def _prompt_menu() -> str:
    print("Select run mode:")
    modes = list(MODE_LABELS.items())
    for index, (_, label) in enumerate(modes, start=1):
        print(f"  {index}. {label}")
    while True:
        raw = input("Enter mode number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(modes):
            return modes[int(raw) - 1][0]
        print("Invalid number. Try again.")


def main() -> int:
    _force_single_thread_mode()
    _configure_console_encoding()
    parser = _build_parser()
    args = parser.parse_args()
    app_config = load_config(args.env)
    mode = args.mode or _prompt_menu()
    task_args = _task_namespace(args)
    return _run_mode(app_config, mode, task_args)


if __name__ == "__main__":
    raise SystemExit(main())
