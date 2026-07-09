"""Reject/open accounting for the current triple-tap IS artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from anomaly_science.strategy.triple_tap.research.audit import _ledger, _metrics
from anomaly_science.strategy.triple_tap.research.economics import RES

OUT = Path("research/triple_tap_current_accounting.json")
POLICY = "structure_0.50_part_0.50_runner_initial_stop_be_0.50_inactive_6h"


def run(out_path: Path = OUT) -> dict:
    setups = pd.read_parquet(RES / "setups_discovery.parquet")
    labels = pd.read_parquet(RES / "labels_close.parquet")
    policies = pd.read_parquet(RES / "execution_policies.parquet")
    candidates = policies[
        (policies["entry_mode"] == "close")
        & (policies["exit_policy"] == POLICY)
        & policies["r_multiple"].notna()
    ].copy()
    candidates["model_score_r"] = 0.0
    ledger, portfolio_rejects = _ledger(
        candidates,
        risk_pct=0.02,
        max_open=3,
        slip_pct=0.005,
        priority_by_score=False,
        max_notional_multiple=0.50,
    )
    policy_status = (
        policies[(policies["entry_mode"] == "close") & (policies["exit_policy"] == POLICY)]
        ["status"]
        .value_counts(dropna=False)
        .to_dict()
    )
    report = {
        "scope": "IS current artifacts; no ML selection; combined family portfolio accounting",
        "setups_discovery_rows": int(len(setups)),
        "valid_entry_setups": int(setups["valid_entry"].sum()) if "valid_entry" in setups else int(len(setups)),
        "labels_close_rows": int(len(labels)),
        "labels_close_resolved_rows": int(labels["r_multiple"].notna().sum()),
        "policy": POLICY,
        "policy_status_counts": {str(k): int(v) for k, v in policy_status.items()},
        "portfolio_candidates": int(len(candidates)),
        "portfolio_taken": int(len(ledger)),
        "portfolio_not_opened": int(sum(portfolio_rejects.values())),
        "portfolio_rejects": portfolio_rejects,
        "portfolio_metrics": _metrics(ledger),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote accounting -> {out_path}", flush=True)
    return report


if __name__ == "__main__":
    run()
