"""Run the frozen v1 prediction/timing audit for adverse 1h long anomalies.

Protocol: docs/strategies/xsect_momentum_1h_adverse_long_hazard_protocol_v1.md
This command never reads 2026 bars and never executes anomaly exits.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.hourly_adverse_long import (
    AdverseLongSpec,
    REGISTERED_VARIANTS,
    acceptance_gate_table,
    build_active_short_snapshot_table,
    circular_shift_null,
    feature_ic_table,
    snapshot_metric_table,
    trade_catastrophe_table,
    trade_metric_table,
)


OUT = Path(".output/results/xsect_momentum")
AUDIT_OUT = OUT / "hourly_adverse_long_audit_v1_corrected"
INVALID_AUDIT_OUT = OUT / "hourly_adverse_long_audit_v1"
HOURLY_ROOT = Path(".output/market/binance_vision/um_futures/klines_1h")
LEDGER = OUT / "hourly_structural_runs_v2/real_u100_k20_reb7_no_stop_trades.parquet"
PROTOCOL = Path("docs/strategies/xsect_momentum_1h_adverse_long_hazard_protocol_v1.md")
IS_END = pd.Timestamp("2026-01-01", tz="UTC")


def _component_prevalence(snapshots: pd.DataFrame) -> pd.DataFrame:
    components = (
        "component_one_shot",
        "component_fast_burst",
        "component_grind",
        "component_breakout",
        "positive_price_evidence",
        "activity_z2",
        "activity_z3",
        "activity_z4",
        *REGISTERED_VARIANTS,
    )
    rows: list[dict[str, object]] = []
    for year in (2023, 2024, 2025):
        eligible = snapshots.loc[
            snapshots["eligible_12h"]
            & (snapshots["snapshot_time"].dt.year == year)
        ]
        for component in components:
            rows.append({
                "year": year,
                "component": component,
                "eligible_snapshots": len(eligible),
                "count": int(eligible[component].fillna(False).sum()),
                "share": float(eligible[component].fillna(False).mean()),
            })
    return pd.DataFrame(rows)


def _write_metadata(spec: AdverseLongSpec, snapshots: pd.DataFrame, gates: pd.DataFrame) -> None:
    protocol_bytes = PROTOCOL.read_bytes()
    source_files = len(list(HOURLY_ROOT.glob("*.parquet")))
    metadata = {
        "detector_spec": spec.as_dict(),
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
        "timing_correction": str(
            Path("docs/strategies/xsect_momentum_1h_adverse_long_hazard_protocol_v1_correction.md")
        ),
        "invalidated_artifact": str(INVALID_AUDIT_OUT),
        "source_hourly_root": str(HOURLY_ROOT),
        "source_hourly_file_count": source_files,
        "position_ledger": str(LEDGER),
        "position_ledger_sha256": hashlib.sha256(LEDGER.read_bytes()).hexdigest(),
        "is_end_exclusive": IS_END.isoformat(),
        "snapshot_rows": len(snapshots),
        "short_trade_count": int(snapshots["trade_id"].nunique()),
        "first_snapshot": snapshots["snapshot_time"].min().isoformat(),
        "last_snapshot": snapshots["snapshot_time"].max().isoformat(),
        "all_years_pass": bool(gates["all_prediction_gates_pass"].all()),
    }
    (AUDIT_OUT / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main() -> None:
    if not LEDGER.exists():
        raise FileNotFoundError(LEDGER)
    if not PROTOCOL.exists():
        raise FileNotFoundError(PROTOCOL)
    AUDIT_OUT.mkdir(parents=True, exist_ok=True)
    spec = AdverseLongSpec()
    ledger = pd.read_parquet(LEDGER)
    ledger = ledger.loc[ledger["entry_time"] < IS_END].copy()
    if bool((ledger["exit_time"] > IS_END).any()):
        raise ValueError("position ledger crosses the frozen OOS boundary")

    reusable_snapshots = INVALID_AUDIT_OUT / "snapshots.parquet"
    if reusable_snapshots.exists():
        print("loading immutable causal snapshots from the invalidated timing audit ...", flush=True)
        snapshots = pd.read_parquet(reusable_snapshots)
    else:
        print("building causal active-short snapshot table ...", flush=True)
        snapshots = build_active_short_snapshot_table(HOURLY_ROOT, ledger, spec=spec)
    if bool((snapshots["snapshot_time"] >= IS_END).any()):
        raise AssertionError("snapshot table entered frozen OOS")
    snapshots.to_parquet(AUDIT_OUT / "snapshots.parquet", index=False)
    print(
        f"  rows={len(snapshots)} shorts={snapshots['trade_id'].nunique()} "
        f"symbols={snapshots['symbol'].nunique()}",
        flush=True,
    )

    print("evaluating registered features, variants, and timing ...", flush=True)
    feature_ic = feature_ic_table(snapshots)
    snapshot_metrics = snapshot_metric_table(snapshots)
    catastrophe = trade_catastrophe_table(snapshots)
    trade_metrics = trade_metric_table(catastrophe)
    prevalence = _component_prevalence(snapshots)
    null = circular_shift_null(snapshots, simulations=500)
    gates = acceptance_gate_table(snapshots, snapshot_metrics, trade_metrics, null)

    artifacts = {
        "feature_ic.parquet": feature_ic,
        "snapshot_metrics.parquet": snapshot_metrics,
        "trade_catastrophes.parquet": catastrophe,
        "trade_metrics.parquet": trade_metrics,
        "component_prevalence.parquet": prevalence,
        "circular_shift_null.parquet": null,
        "acceptance_gates.parquet": gates,
    }
    for name, frame in artifacts.items():
        frame.to_parquet(AUDIT_OUT / name, index=False)
        frame.to_csv(AUDIT_OUT / name.replace(".parquet", ".csv"), index=False)
    _write_metadata(spec, snapshots, gates)

    primary = snapshot_metrics.loc[
        (snapshot_metrics["horizon_hours"] == 12)
        & (snapshot_metrics["variant"] == "core_z3")
    ]
    print(primary[["year", "trigger_rate", "mae_lift", "top_adverse_recall"]].to_string(index=False), flush=True)
    print(
        trade_metrics.loc[trade_metrics["variant"] == "core_z3", [
            "year",
            "catastrophic_trade_recall",
            "median_warning_lead_hours",
        ]].to_string(index=False),
        flush=True,
    )
    print(gates.to_string(index=False), flush=True)
    print(f"wrote {AUDIT_OUT}", flush=True)


if __name__ == "__main__":
    main()
