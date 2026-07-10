"""Triple-tap annotation-iteration diagnostics."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from anomaly_science.strategy.triple_tap.research.current_trade_report import run
from anomaly_science.strategy.triple_tap.research.policy_ev_model import (
    CANDIDATE_POLICY,
    run_candidate_policy_controls,
    score_path,
    shuffled_score_path,
)


def run_trade_report_iteration(run_dir: Path) -> dict[str, Any]:
    report_dir = run_dir / "trade_report"
    training_dir = run_dir / "training"
    training_dir.mkdir(parents=True, exist_ok=True)
    run_candidate_policy_controls()
    shutil.copy2(score_path(CANDIDATE_POLICY), training_dir / score_path(CANDIDATE_POLICY).name)
    shutil.copy2(shuffled_score_path(CANDIDATE_POLICY), training_dir / shuffled_score_path(CANDIDATE_POLICY).name)
    return run(
        out_json=report_dir / "triple_tap_current_trade_report.json",
        out_md=report_dir / "triple_tap_current_trade_report.md",
        out_trades_json=report_dir / "trades.json",
        out_trades_csv=report_dir / "trades.csv",
        out_dashboard_json=report_dir / "dashboard.json",
    )
