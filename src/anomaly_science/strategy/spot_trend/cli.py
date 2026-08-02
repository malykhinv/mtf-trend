from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from .contracts import ResearchConfig
from .research import run_spot_trend_research, write_research_artifacts


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unsupported input format for {path}; use Parquet or CSV")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the frozen Binance Spot trend scientific protocol.")
    parser.add_argument("--daily-bars", type=Path, required=True)
    parser.add_argument("--symbol-master", type=Path, required=True)
    parser.add_argument("--prediction-start", required=True, help="First OOS decision date (YYYY-MM-DD).")
    parser.add_argument("--prediction-end", required=True, help="Last OOS decision date (YYYY-MM-DD).")
    parser.add_argument("--output-dir", type=Path, required=True)
    attestation = parser.add_mutually_exclusive_group()
    attestation.add_argument("--candidate-was-unseen", action="store_true")
    attestation.add_argument("--candidate-was-inspected", action="store_true")
    parser.add_argument("--skip-donchian-sensitivity", action="store_true")
    parser.add_argument("--skip-target-horizon-robustness", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    attestation: bool | None = None
    if args.candidate_was_unseen:
        attestation = True
    elif args.candidate_was_inspected:
        attestation = False
    config = ResearchConfig()
    config = replace(config, holdout=replace(config.holdout, candidate_was_unseen=attestation))
    run = run_spot_trend_research(
        _read_table(args.daily_bars),
        _read_table(args.symbol_master),
        prediction_start=args.prediction_start,
        prediction_end=args.prediction_end,
        config=config,
        run_horizon_sensitivity=not args.skip_donchian_sensitivity,
        run_target_horizon_robustness=not args.skip_target_horizon_robustness,
    )
    write_research_artifacts(run, args.output_dir)
    print(
        f"selected_variant={run.selected_variant} "
        f"holdout_status={run.config.holdout.candidate_status} "
        f"output_dir={args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
