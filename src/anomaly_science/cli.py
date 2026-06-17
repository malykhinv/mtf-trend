from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from anomaly_science.data import run_mvp1_data_audit
from anomaly_science.events import run_mvp1_events


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

    data_audit = subparsers.add_parser(
        "run-mvp1-data-audit",
        help="Run MVP1 CSV data-source, quality, universe, protocol, and manifest audit.",
    )
    data_audit.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    data_audit.add_argument("--out", required=True, help="Directory where audit artifacts will be written.")

    events = subparsers.add_parser(
        "run-mvp1-events",
        help="Run MVP1 data audit, point-in-time universe, and broad anomaly event detector.",
    )
    events.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    events.add_argument("--out", required=True, help="Directory where event artifacts will be written.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        print(_BOOTSTRAP_MESSAGE)
        return 0

    if args.command == "run-mvp1-data-audit":
        output_dir = run_mvp1_data_audit(input_dir=Path(args.input), out_dir=Path(args.out))
        print(f"mvp1 data audit artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-events":
        output_dir = run_mvp1_events(input_dir=Path(args.input), out_dir=Path(args.out))
        print(f"mvp1 broad event artifacts written: {output_dir}")
        return 0

    parser.print_help()
    return 2
