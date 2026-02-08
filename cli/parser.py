"""CLI parser and command dispatch helpers."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from config import AppConfig
from constants import DEFAULT_FETCH_DAYS, DEFAULT_MIN_VOLUME_USD, DEFAULT_QUALITY_REPORT_OUTPUT_FILE, DEFAULT_TOP_N, DEFAULT_UPDATE_DAYS
from cli import commands

Handler = Callable[[AppConfig, argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtf-trend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data", help="Загрузка данных с бирж и CoinGecko")
    fetch.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    fetch.add_argument("--days", type=int, default=DEFAULT_FETCH_DAYS)
    fetch.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)

    update = subparsers.add_parser("update-cache", help="Инкрементальное обновление кэша")
    update.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    update.add_argument("--days", type=int, default=DEFAULT_UPDATE_DAYS)
    update.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)

    run_bt = subparsers.add_parser("run-backtest", help="Запуск бектеста по данным в кэше")
    run_bt.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    run_bt.add_argument(
        "--levels-tf",
        default=None,
        help="Таймфрейм уровней (например 1d). Приоритетнее LEVELS_TIMEFRAME из env",
    )
    run_bt.add_argument(
        "--entry-tf",
        default=None,
        help="Таймфрейм входов (например 15m). Приоритетнее ENTRY_TIMEFRAME из env",
    )

    report = subparsers.add_parser("make-report", help="Сформировать JSON-отчет по результатам бектеста")
    report.add_argument("--input", default=None, help="Путь к CSV с результатами")
    report.add_argument("--output", default=None, help="Путь к JSON отчету")

    quality = subparsers.add_parser("check-quality", help="Проверка качества кэша")
    quality.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    quality.add_argument(
        "--output",
        default=None,
        help=(
            f"Путь к отчету качества (.json или .csv). По умолчанию: <results_dir>/{DEFAULT_QUALITY_REPORT_OUTPUT_FILE}"
        ),
    )

    return parser


def resolve_handler(command_name: str) -> Handler:
    handlers: dict[str, Handler] = {
        "fetch-data": commands.fetch_data,
        "update-cache": commands.update_cache,
        "run-backtest": commands.run_backtest,
        "make-report": commands.make_report,
        "check-quality": commands.check_quality,
    }
    return handlers[command_name]
