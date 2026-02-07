"""CLI parser and command dispatch helpers."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from config import AppConfig
from cli import commands

Handler = Callable[[AppConfig], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtf-trend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("fetch-data", "update-cache", "run-backtest", "make-report", "check-quality"):
        subparsers.add_parser(command_name)

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
