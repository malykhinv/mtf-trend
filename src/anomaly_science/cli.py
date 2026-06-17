from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from anomaly_science.atlas import run_mvp1_atlas
from anomaly_science.controls import ControlsConfig, run_mvp1_controls
from anomaly_science.data import run_mvp1_data_audit
from anomaly_science.events import run_mvp1_events
from anomaly_science.future import run_mvp1_future
from anomaly_science.labels import run_mvp1_labels
from anomaly_science.prediction import WalkForwardPredictionConfig, run_mvp1_prediction
from anomaly_science.state import run_mvp1_state


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

    state = subparsers.add_parser(
        "run-mvp1-state",
        help="Build MVP1 online 1m anomaly state from normalized candles and anomaly_events.csv.",
    )
    state.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    state.add_argument("--events", required=True, help="Path to anomaly_events.csv from run-mvp1-events.")
    state.add_argument("--out", required=True, help="Directory where state artifacts will be written.")

    future = subparsers.add_parser(
        "run-mvp1-future",
        help="Build MVP1 raw future paths from normalized candles and anomaly_state_1m.csv.",
    )
    future.add_argument("--input", required=True, help="Directory containing normalized MVP1 CSV inputs.")
    future.add_argument("--state", required=True, help="Path to anomaly_state_1m.csv from run-mvp1-state.")
    future.add_argument("--out", required=True, help="Directory where future path artifacts will be written.")

    atlas = subparsers.add_parser(
        "run-mvp1-atlas",
        help="Build MVP1 descriptive anomaly nature atlas from anomaly_state_1m.csv and anomaly_future_paths.csv.",
    )
    atlas.add_argument("--state", required=True, help="Path to anomaly_state_1m.csv from run-mvp1-state.")
    atlas.add_argument("--future", required=True, help="Path to anomaly_future_paths.csv from run-mvp1-future.")
    atlas.add_argument("--out", required=True, help="Directory where atlas artifacts will be written.")

    labels = subparsers.add_parser(
        "run-mvp1-labels",
        help="Build MVP1 descriptive future-nature scenario labels from state and raw future path artifacts.",
    )
    labels.add_argument("--state", required=True, help="Path to anomaly_state_1m.csv from run-mvp1-state.")
    labels.add_argument("--future", required=True, help="Path to anomaly_future_paths.csv from run-mvp1-future.")
    labels.add_argument("--out", required=True, help="Directory where outcome label artifacts will be written.")

    prediction = subparsers.add_parser(
        "run-mvp1-prediction",
        help="Run MVP1 daily prequential calibrated baseline prediction from state and outcome labels.",
    )
    prediction.add_argument("--state", required=True, help="Path to anomaly_state_1m.csv from run-mvp1-state.")
    prediction.add_argument("--labels", required=True, help="Path to anomaly_outcome_labels.csv from run-mvp1-labels.")
    prediction.add_argument("--out", required=True, help="Directory where prediction artifacts will be written.")
    prediction.add_argument(
        "--horizon-minutes",
        type=int,
        default=30,
        choices=(15, 30, 60),
        help="Descriptive scenario horizon to predict. Default: 30.",
    )

    controls = subparsers.add_parser(
        "run-mvp1-controls",
        help="Run MVP1 placebo/control checks for walk-forward prediction artifacts.",
    )
    controls.add_argument("--state", required=True, help="Path to anomaly_state_1m.csv from run-mvp1-state.")
    controls.add_argument("--labels", required=True, help="Path to anomaly_outcome_labels.csv from run-mvp1-labels.")
    controls.add_argument("--out", required=True, help="Directory where control artifacts will be written.")
    controls.add_argument(
        "--horizon-minutes",
        type=int,
        default=30,
        choices=(15, 30, 60),
        help="Descriptive scenario horizon to control-test. Default: 30.",
    )

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

    if args.command == "run-mvp1-state":
        output_dir = run_mvp1_state(input_dir=Path(args.input), events_path=Path(args.events), out_dir=Path(args.out))
        print(f"mvp1 online state artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-future":
        output_dir = run_mvp1_future(input_dir=Path(args.input), state_path=Path(args.state), out_dir=Path(args.out))
        print(f"mvp1 raw future path artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-atlas":
        output_dir = run_mvp1_atlas(state_path=Path(args.state), future_path=Path(args.future), out_dir=Path(args.out))
        print(f"mvp1 anomaly atlas artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-labels":
        output_dir = run_mvp1_labels(state_path=Path(args.state), future_path=Path(args.future), out_dir=Path(args.out))
        print(f"mvp1 outcome label artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-prediction":
        config = WalkForwardPredictionConfig(target_horizon_minutes=args.horizon_minutes)
        output_dir = run_mvp1_prediction(
            state_path=Path(args.state),
            labels_path=Path(args.labels),
            out_dir=Path(args.out),
            config=config,
        )
        print(f"mvp1 walk-forward prediction artifacts written: {output_dir}")
        return 0

    if args.command == "run-mvp1-controls":
        config = ControlsConfig(target_horizon_minutes=args.horizon_minutes)
        output_dir = run_mvp1_controls(
            state_path=Path(args.state),
            labels_path=Path(args.labels),
            out_dir=Path(args.out),
            config=config,
        )
        print(f"mvp1 placebo/control artifacts written: {output_dir}")
        return 0

    parser.print_help()
    return 2
