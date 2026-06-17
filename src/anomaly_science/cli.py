from __future__ import annotations

import argparse
from collections.abc import Sequence


_BOOTSTRAP_MESSAGE = "anomaly_science bootstrap ok"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly-science",
        description="Clean scientific anomaly research CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "doctor",
        help="Run a minimal bootstrap check for the clean anomaly_science core.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        print(_BOOTSTRAP_MESSAGE)
        return 0

    parser.print_help()
    return 2
