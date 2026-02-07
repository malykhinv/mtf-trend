"""CLI parser and command dispatch helpers."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from config import AppConfig
from cli import commands

Handler = Callable[[AppConfig, argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtf-trend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data", help="Загрузка данных с бирж и CoinGecko")
    fetch.add_argument("--top-n", type=int, default=50)
    fetch.add_argument("--days", type=int, default=60)

    update = subparsers.add_parser("update-cache", help="Инкрементальное обновление кэша")
    update.add_argument("--top-n", type=int, default=50)
    update.add_argument("--days", type=int, default=7)

    run_bt = subparsers.add_parser("run-backtest", help="Запуск бектеста по данным в кэше")
    run_bt.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")

    report = subparsers.add_parser("make-report", help="Сформировать JSON-отчет по результатам бектеста")
    report.add_argument("--input", default=None, help="Путь к CSV с результатами")
    report.add_argument("--output", default=None, help="Путь к JSON отчету")

    quality = subparsers.add_parser("check-quality", help="Проверка качества кэша")
    quality.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    quality.add_argument(
        "--output",
        default=None,
        help="Путь к отчету качества (.json или .csv). По умолчанию: <results_dir>/quality_report.json",
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
