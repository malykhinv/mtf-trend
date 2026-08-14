"""Run the frozen 1h structure-aware short re-entry prediction audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.hourly_adverse_long import AdverseLongSpec
from anomaly_science.strategy.xsect_momentum.research.hourly_structure_reentry import (
    ReentrySpec,
    VisibleStructureSpec,
    bootstrap_mean_bounds,
    build_reentry_ledgers,
    build_structure_snapshot_table,
    reentry_acceptance_gates,
    reentry_metric_table,
)


OUT = Path(".output/results/xsect_momentum")
AUDIT_OUT = OUT / "hourly_structure_reentry_audit_v1"
HOURLY_ROOT = Path(".output/market/binance_vision/um_futures/klines_1h")
LEDGER = OUT / "hourly_structural_runs_v2/real_u100_k20_reb7_no_stop_trades.parquet"
PROTOCOL = Path("docs/strategies/xsect_momentum_1h_structure_reentry_protocol_v1.md")
IS_END = pd.Timestamp("2026-01-01", tz="UTC")


def main() -> None:
    if not LEDGER.exists():
        raise FileNotFoundError(LEDGER)
    if not PROTOCOL.exists():
        raise FileNotFoundError(PROTOCOL)
    AUDIT_OUT.mkdir(parents=True, exist_ok=True)
    hazard_spec = AdverseLongSpec()
    structure_spec = VisibleStructureSpec()
    reentry_spec = ReentrySpec()
    ledger = pd.read_parquet(LEDGER)
    ledger = ledger.loc[ledger["entry_time"] < IS_END].copy()
    if bool((ledger["exit_time"] > IS_END).any()):
        raise ValueError("position ledger crosses the frozen OOS boundary")

    print("building causal structure-enriched short snapshots ...", flush=True)
    snapshots = build_structure_snapshot_table(
        HOURLY_ROOT,
        ledger,
        hazard_spec=hazard_spec,
        structure_spec=structure_spec,
    )
    if bool((snapshots["snapshot_time"] >= IS_END).any()):
        raise AssertionError("structure snapshot table entered frozen OOS")
    snapshots.to_parquet(AUDIT_OUT / "structure_snapshots.parquet", index=False)
    print(
        f"  rows={len(snapshots)} shorts={snapshots['trade_id'].nunique()} "
        f"symbols={snapshots['symbol'].nunique()}",
        flush=True,
    )

    print("building warning episodes and registered re-entry signals ...", flush=True)
    episodes, signals = build_reentry_ledgers(snapshots, spec=reentry_spec)
    metrics = reentry_metric_table(episodes, signals)
    bootstrap = bootstrap_mean_bounds(signals, simulations=2_000)
    gates = reentry_acceptance_gates(metrics, bootstrap)
    rejection = pd.DataFrame([
        {
            "warning_episodes": len(episodes),
            "valid_origin": int(episodes["origin_valid"].sum()),
            "eligible_warning_24h": int(episodes["eligible_warning_24h"].sum()),
            "ever_midpoint": int(episodes["ever_midpoint_condition"].sum()),
            "ever_flow_decay": int(episodes["ever_flow_decay_condition"].sum()),
            "ever_bearish_structure": int(episodes["ever_bearish_structure_condition"].sum()),
            "midpoint_only_signal": int(episodes["midpoint_only_signal_found"].sum()),
            "midpoint_flow_signal": int(episodes["midpoint_flow_signal_found"].sum()),
            "midpoint_structure_signal": int(episodes["midpoint_structure_signal_found"].sum()),
            "combined_signal": int(episodes["combined_signal_found"].sum()),
        }
    ])

    artifacts = {
        "warning_episodes.parquet": episodes,
        "reentry_signals.parquet": signals,
        "reentry_metrics.parquet": metrics,
        "bootstrap_bounds.parquet": bootstrap,
        "acceptance_gates.parquet": gates,
        "rejection_counts.parquet": rejection,
    }
    for name, frame in artifacts.items():
        frame.to_parquet(AUDIT_OUT / name, index=False)
        frame.to_csv(AUDIT_OUT / name.replace(".parquet", ".csv"), index=False)

    metadata = {
        "hazard_spec": hazard_spec.as_dict(),
        "structure_spec": structure_spec.as_dict(),
        "reentry_spec": reentry_spec.as_dict(),
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "position_ledger": str(LEDGER),
        "position_ledger_sha256": hashlib.sha256(LEDGER.read_bytes()).hexdigest(),
        "source_hourly_root": str(HOURLY_ROOT),
        "source_hourly_file_count": len(list(HOURLY_ROOT.glob("*.parquet"))),
        "is_end_exclusive": IS_END.isoformat(),
        "snapshot_rows": len(snapshots),
        "warning_episodes": len(episodes),
        "reentry_signals": len(signals),
        "all_years_pass": bool(gates["all_reentry_gates_pass"].all()),
    }
    (AUDIT_OUT / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )

    print(rejection.to_string(index=False), flush=True)
    print(
        metrics.loc[
            (metrics["variant"].isin(["midpoint_only", "combined"]))
            & (metrics["horizon_hours"] == 24),
            [
                "year",
                "variant",
                "reentry_events",
                "reentry_coverage",
                "negative_close_return_share",
                "mean_future_close_return",
                "structural_stop_breach_share_24h",
            ],
        ].to_string(index=False),
        flush=True,
    )
    print(gates.to_string(index=False), flush=True)
    print(f"wrote {AUDIT_OUT}", flush=True)


if __name__ == "__main__":
    main()

