"""Post-selection structural protection EV study for stressed ladder inventory."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
class StructuralEVError(ValueError):
    """Raised when the structural EV study violates its frozen contract."""


@dataclass(frozen=True, slots=True)
class StructuralEVSpec:
    protocol_version: str = "drawdown_ladder_structural_protection_ev_v1"
    protocol_freeze_id: str = "drawdown_ladder_structural_protection_ev_20260803_v1"
    source_prediction_protocol_freeze_id: str = (
        "drawdown_ladder_recovery_prediction_20260802_v1:structural_baseline"
    )
    research_partition: str = "is_internal_weekly_oos"
    primary_grid_step_pct: int = 3
    primary_deepest_level_pct: int = 6
    decision_states: tuple[tuple[int, int], ...] = ((3, 6), (5, 10), (10, 20))
    hold_probability_threshold: float = 0.92
    horizon_minutes: int = 2880
    primary_cost_bps: int = 25
    sensitivity_cost_bps: tuple[int, ...] = (10, 25, 50)
    bootstrap_iterations: int = 20_000
    random_seed: int = 20260803
    post_selection_arm_count: int = 2
    primary_endpoint_count: int = 2
    viewed_state_count: int = 3
    familywise_alpha: float = 0.004166666666666667
    min_primary_rows: int = 10_000
    min_primary_hold_rows: int = 1_000
    min_primary_exit_rows: int = 500
    min_positive_months: int = 4
    min_top_trade_removal_fraction: float = 0.01

    def __post_init__(self) -> None:
        if self.research_partition != "is_internal_weekly_oos":
            raise ValueError("structural EV is locked to frozen internal OOS predictions")
        if self.decision_states[0] != (
            self.primary_grid_step_pct,
            self.primary_deepest_level_pct,
        ):
            raise ValueError("primary structural EV state must be first")
        if len(set(self.decision_states)) != len(self.decision_states):
            raise ValueError("structural EV decision states must be unique")
        if not 0.0 < self.hold_probability_threshold < 1.0:
            raise ValueError("hold probability threshold must lie in (0, 1)")
        if self.primary_cost_bps not in self.sensitivity_cost_bps:
            raise ValueError("primary cost must be included in sensitivities")
        expected_alpha = 0.05 / (
            self.post_selection_arm_count
            * self.primary_endpoint_count
            * self.viewed_state_count
        )
        if not math.isclose(self.familywise_alpha, expected_alpha):
            raise ValueError("familywise alpha must adjust arms and primary endpoints")
        if self.bootstrap_iterations < 10_000:
            raise ValueError("structural EV bootstrap requires at least 10000 iterations")
        if self.min_positive_months <= 0:
            raise ValueError("positive-month gate must be positive")


def write_structural_ev_protocol(
    path: str | Path,
    spec: StructuralEVSpec = StructuralEVSpec(),
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "spec": asdict(spec),
        "scientific_status": "POST_SELECTION_IS_EVIDENCE_REQUIRES_FORWARD_CONFIRMATION",
        "decision_time": "after the fill bar closes and the frozen weekly model scores the state",
        "policy": {
            "hold": (
                f"calibrated_probability >= {spec.hold_probability_threshold}; "
                "retain inventory to 48h terminal mark"
            ),
            "exit": (
                f"calibrated_probability < {spec.hold_probability_threshold}; "
                "close inventory at snapshot close"
            ),
            "baseline": "retain every stressed state to 48h terminal mark",
        },
        "primary_endpoints": (
            "mean 25bps-net policy return from blended entry",
            "mean policy-minus-unconditional-hold return",
        ),
        "inference": {
            "clusters": ("ISO_week", "symbol"),
            "bootstrap_iterations": spec.bootstrap_iterations,
            "one_sided_familywise_alpha": spec.familywise_alpha,
            "selection_adjustment": (
                "0.05 / 2 viewed model arms / 2 primary endpoints / "
                "3 viewed structural states"
            ),
        },
        "gates": (
            "primary support and hold/exit support",
            "absolute policy EV > 0 under week and symbol cluster inference",
            "policy improvement over unconditional hold > 0 under both clusters",
            "positive mean policy EV in at least four calendar months",
            "policy CVaR5 no worse than unconditional hold",
            "removing the top 1% of policy rows leaves positive total return",
        ),
        "limitations": (
            "not an ex-ante entry strategy; it is a post-fill inventory protection decision",
            "no funding-rate history in the Stage-0 outcome contract",
            "no physical TP/SL or portfolio concurrency is simulated at this gate",
            "10/25/50 bps are cost sensitivities, not price-based exit anchors",
        ),
        "oos_2026_accessed": False,
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)
    return target


def _repository_state() -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return revision, dirty


def _validate_prediction_artifact(
    directory: Path,
    spec: StructuralEVSpec,
) -> Path:
    prediction_path = directory / "oos_predictions.parquet"
    protocol_path = directory / "frozen_probability_protocol.json"
    metadata_path = directory / "binary_probability.metadata.json"
    audit_path = directory / "temporal_audit.csv"
    for path in (prediction_path, protocol_path, metadata_path, audit_path):
        if not path.is_file():
            raise StructuralEVError(f"structural prediction artifact is missing: {path}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if protocol.get("protocol_freeze_id") != spec.source_prediction_protocol_freeze_id:
        raise StructuralEVError("structural prediction protocol mismatch")
    if metadata.get("evidence_status") != "FROZEN_DEVELOPMENT":
        raise StructuralEVError("structural prediction artifact is not frozen")
    if metadata.get("pristine_holdout_accessed") is not False:
        raise StructuralEVError("structural prediction artifact accessed pristine holdout")
    audit = pd.read_csv(audit_path)
    if audit.empty or not audit["status"].eq("PASS").all():
        raise StructuralEVError("structural prediction temporal audit failed")
    return prediction_path


def assemble_structural_ev_rows(
    *,
    predictions: pd.DataFrame,
    features: pd.DataFrame,
    outcomes: pd.DataFrame,
    spec: StructuralEVSpec = StructuralEVSpec(),
) -> pd.DataFrame:
    """Create causal policy rows from already frozen weekly OOF predictions."""

    work = predictions.merge(
        features,
        left_on="group",
        right_on="candidate_id",
        how="left",
        validate="one_to_one",
    ).merge(
        outcomes,
        on="candidate_id",
        how="left",
        validate="one_to_one",
        suffixes=("", "_outcome"),
    )
    if len(work) != len(predictions) or bool(work["future_return_2880m"].isna().any()):
        raise StructuralEVError("structural EV join is incomplete")
    equality_checks = (
        ("parent_event_id", "parent_event_id_outcome"),
        ("symbol", "symbol_outcome"),
        ("snapshot_time_ms", "snapshot_time_ms_outcome"),
        ("feature_cutoff_time_ms", "feature_cutoff_time_ms_outcome"),
        ("future_start_time_ms", "future_start_time_ms_outcome"),
    )
    for left, right in equality_checks:
        if not work[left].astype(str).eq(work[right].astype(str)).all():
            raise StructuralEVError(f"structural EV join mismatch: {left}")
    if not work["target"].astype(bool).eq(
        work["break_even_25bps_reached"].astype(bool)
    ).all():
        raise StructuralEVError("structural prediction target differs from Stage-0 outcome")
    if not work["horizon_complete"].astype(bool).all() or not work[
        "label_available_2880m"
    ].astype(bool).all():
        raise StructuralEVError("structural EV population contains incomplete horizons")
    if work["untouched_2026_row_used"].astype(bool).any():
        raise StructuralEVError("structural EV input reports OOS access")
    if not work["feature_cutoff_time_ms"].le(work["snapshot_time_ms"]).all():
        raise StructuralEVError("structural EV feature cutoff exceeds snapshot")
    if not work["future_start_time_ms"].gt(work["snapshot_time_ms"]).all():
        raise StructuralEVError("structural EV future starts at or before snapshot")
    if not work["weekly_model_freeze_time_ms"].le(work["snapshot_time_ms"]).all():
        raise StructuralEVError("weekly model was frozen after an EV decision snapshot")
    untouched_oos_start_ms = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000)
    implied_future_end_ms = work["snapshot_time_ms"] + spec.horizon_minutes * 60_000
    if implied_future_end_ms.ge(untouched_oos_start_ms).any():
        raise StructuralEVError("structural EV horizon reaches untouched 2026 OOS")
    numeric_outcomes = work[
        [
            "snapshot_close_to_entry",
            "future_return_2880m",
            "future_max_return_2880m",
            "future_min_return_2880m",
        ]
    ].to_numpy(dtype=float)
    if not np.isfinite(numeric_outcomes).all():
        raise StructuralEVError("structural EV contains non-finite return paths")
    state_mask = np.zeros(len(work), dtype=bool)
    for grid_step, depth in spec.decision_states:
        state_mask |= work["grid_step_pct"].eq(grid_step) & work[
            "deepest_filled_level_pct"
        ].eq(depth)
    work = work.loc[state_mask].copy()
    if work.empty:
        raise StructuralEVError("structural EV decision-state population is empty")
    if work[["parent_event_id", "grid_step_pct"]].duplicated().any():
        raise StructuralEVError("structural EV contains repeated parent/grid decisions")
    work["decision"] = np.where(
        work["calibrated_probability"].ge(spec.hold_probability_threshold),
        "HOLD_48H",
        "EXIT_AT_SNAPSHOT",
    )
    for cost_bps in spec.sensitivity_cost_bps:
        cost = cost_bps / 10_000.0
        hold_net = work["future_return_2880m"].astype(float) - cost
        exit_net = work["snapshot_close_to_entry"].astype(float) - cost
        hold_decision = work["decision"].eq("HOLD_48H")
        policy_net = np.where(hold_decision, hold_net, exit_net)
        worst_hold_gross = np.minimum(
            work["snapshot_close_to_entry"].astype(float),
            work["future_min_return_2880m"].astype(float),
        )
        best_hold_gross = np.maximum(
            work["snapshot_close_to_entry"].astype(float),
            work["future_max_return_2880m"].astype(float),
        )
        work[f"unconditional_hold_net_return_{cost_bps}bps"] = hold_net
        work[f"immediate_exit_net_return_{cost_bps}bps"] = exit_net
        work[f"policy_net_return_{cost_bps}bps"] = policy_net
        work[f"policy_minus_hold_return_{cost_bps}bps"] = policy_net - hold_net
        work[f"policy_worst_net_return_{cost_bps}bps"] = np.where(
            hold_decision,
            worst_hold_gross - cost,
            exit_net,
        )
        work[f"policy_best_net_return_{cost_bps}bps"] = np.where(
            hold_decision,
            best_hold_gross - cost,
            exit_net,
        )
    return work


def build_structural_ev_dataset(
    *,
    structural_probability_dir: str | Path,
    stage1_dataset_path: str | Path,
    stage0_outcomes_path: str | Path,
    output_dir: str | Path,
    spec: StructuralEVSpec = StructuralEVSpec(),
) -> Path:
    """Join frozen OOF decisions to strictly future Stage-0 return paths."""

    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise StructuralEVError(f"structural EV output must be absent or empty: {root}")
    revision, dirty = _repository_state()
    if dirty:
        raise StructuralEVError("structural EV evidence requires a clean committed worktree")
    prediction_path = _validate_prediction_artifact(
        Path(structural_probability_dir), spec
    )
    feature_path = Path(stage1_dataset_path)
    outcome_path = Path(stage0_outcomes_path)
    predictions = pd.read_parquet(prediction_path)
    features = pd.read_parquet(
        feature_path,
        columns=[
            "candidate_id",
            "snapshot_close_to_entry",
            "feature_schema_version",
        ],
    )
    outcomes = pd.read_parquet(
        outcome_path,
        columns=[
            "candidate_id",
            "parent_event_id",
            "symbol",
            "snapshot_time_ms",
            "feature_cutoff_time_ms",
            "future_start_time_ms",
            "horizon_complete",
            "break_even_25bps_reached",
            "label_available_2880m",
            "future_return_2880m",
            "future_max_return_2880m",
            "future_min_return_2880m",
            "untouched_2026_row_used",
        ],
    )
    work = assemble_structural_ev_rows(
        predictions=predictions,
        features=features,
        outcomes=outcomes,
        spec=spec,
    )
    keep_columns = [
        "group",
        "candidate_id",
        "parent_event_id",
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "future_start_time_ms_outcome",
        "test_week",
        "weekly_model_freeze_time_ms",
        "model_id",
        "grid_step_pct",
        "deepest_filled_level_pct",
        "session_name",
        "calibrated_probability",
        "target",
        "snapshot_close_to_entry",
        "future_return_2880m",
        "future_max_return_2880m",
        "future_min_return_2880m",
        "decision",
    ]
    for cost_bps in spec.sensitivity_cost_bps:
        keep_columns.extend(
            (
                f"unconditional_hold_net_return_{cost_bps}bps",
                f"immediate_exit_net_return_{cost_bps}bps",
                f"policy_net_return_{cost_bps}bps",
                f"policy_minus_hold_return_{cost_bps}bps",
                f"policy_worst_net_return_{cost_bps}bps",
                f"policy_best_net_return_{cost_bps}bps",
            )
        )
    evidence = work.loc[:, keep_columns].sort_values(
        ["snapshot_time_ms", "symbol", "grid_step_pct"],
        kind="mergesort",
    )
    root.mkdir(parents=True, exist_ok=True)
    dataset_path = root / "structural_ev_rows.parquet"
    temporary = dataset_path.with_suffix(dataset_path.suffix + ".tmp")
    evidence.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, dataset_path)
    protocol_path = write_structural_ev_protocol(root / "frozen_structural_ev_protocol.json", spec)
    audit = {
        "status": "PASS",
        "protocol_freeze_id": spec.protocol_freeze_id,
        "row_count": len(evidence),
        "unique_candidate_count": evidence["candidate_id"].nunique(),
        "unique_parent_grid_count": len(
            evidence[["parent_event_id", "grid_step_pct"]].drop_duplicates()
        ),
        "feature_cutoff_le_snapshot": True,
        "future_start_gt_snapshot": True,
        "weekly_model_freeze_le_snapshot": True,
        "future_horizon_before_2026": True,
        "complete_48h_horizons": True,
        "oos_2026_rows_read": 0,
    }
    audit_path = root / "temporal_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    provenance_path = root / "source_provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "code_commit": revision,
                "working_tree_dirty": dirty,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_sha256": {
                    "structural_predictions": sha256_file(prediction_path),
                    "stage1_features": sha256_file(feature_path),
                    "stage0_outcomes": sha256_file(outcome_path),
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest = build_manifest(
        run_id="drawdown-structural-ev-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=(dataset_path, protocol_path, audit_path, provenance_path),
        root=root,
    )
    write_manifest(root / "structural_ev.manifest.json", manifest)
    return root


def _cvar(values: np.ndarray, fraction: float = 0.05) -> float:
    count = max(1, int(math.ceil(len(values) * fraction)))
    return float(np.mean(np.partition(values, count - 1)[:count]))


def _top_removal_fraction(values: np.ndarray) -> float:
    total = float(np.sum(values))
    if total <= 0.0:
        return 0.0
    ordered = np.sort(values)[::-1]
    remaining = total - np.cumsum(ordered)
    indices = np.flatnonzero(remaining <= 0.0)
    return float((indices[0] + 1) / len(values)) if len(indices) else 1.0


def _cluster_bootstrap_means(
    frame: pd.DataFrame,
    *,
    cluster_column: str,
    policy_column: str,
    improvement_column: str,
    iterations: int,
    seed: int,
    alpha: float,
) -> dict[str, float | int | str]:
    grouped = frame.groupby(cluster_column, sort=True).agg(
        count=(policy_column, "size"),
        policy_sum=(policy_column, "sum"),
        improvement_sum=(improvement_column, "sum"),
    )
    counts = grouped["count"].to_numpy(dtype=float)
    policy_sums = grouped["policy_sum"].to_numpy(dtype=float)
    improvement_sums = grouped["improvement_sum"].to_numpy(dtype=float)
    cluster_count = len(grouped)
    rng = np.random.default_rng(seed)
    policy_draws = np.empty(iterations, dtype=float)
    improvement_draws = np.empty(iterations, dtype=float)
    for iteration in range(iterations):
        sampled = rng.integers(0, cluster_count, size=cluster_count)
        denominator = float(np.sum(counts[sampled]))
        policy_draws[iteration] = float(np.sum(policy_sums[sampled]) / denominator)
        improvement_draws[iteration] = float(
            np.sum(improvement_sums[sampled]) / denominator
        )
    return {
        "cluster": cluster_column,
        "cluster_count": cluster_count,
        "iterations": iterations,
        "alpha": alpha,
        "policy_mean": float(frame[policy_column].mean()),
        "policy_lower_one_sided": float(np.quantile(policy_draws, alpha)),
        "policy_upper": float(np.quantile(policy_draws, 1.0 - alpha)),
        "policy_bootstrap_tail_probability_nonpositive": float(
            (1 + np.sum(policy_draws <= 0.0)) / (iterations + 1)
        ),
        "improvement_mean": float(frame[improvement_column].mean()),
        "improvement_lower_one_sided": float(
            np.quantile(improvement_draws, alpha)
        ),
        "improvement_upper": float(
            np.quantile(improvement_draws, 1.0 - alpha)
        ),
        "improvement_bootstrap_tail_probability_nonpositive": float(
            (1 + np.sum(improvement_draws <= 0.0)) / (iterations + 1)
        ),
    }


def analyze_structural_ev(
    *,
    ev_dir: str | Path,
    spec: StructuralEVSpec = StructuralEVSpec(),
) -> Path:
    """Open the frozen EV rows and apply pre-registered gates."""

    root = Path(ev_dir)
    report_path = root / "structural_ev_report.json"
    analysis_paths = (
        root / "structural_ev_summary.csv",
        root / "structural_ev_month_stability.csv",
        root / "structural_ev_cluster_inference.csv",
        root / "structural_ev_gates.csv",
        report_path,
        root / "structural_ev_analysis.manifest.json",
    )
    existing = [path for path in analysis_paths if path.exists()]
    if existing:
        raise StructuralEVError(f"refusing to overwrite EV result: {existing[0]}")
    revision, dirty = _repository_state()
    if dirty:
        raise StructuralEVError("structural EV analysis requires a clean committed worktree")
    provenance = json.loads((root / "source_provenance.json").read_text(encoding="utf-8"))
    if provenance.get("code_commit") != revision:
        raise StructuralEVError("structural EV analysis code differs from build commit")
    protocol = json.loads(
        (root / "frozen_structural_ev_protocol.json").read_text(encoding="utf-8")
    )
    expected_spec = json.loads(json.dumps(asdict(spec)))
    if protocol.get("spec") != expected_spec:
        raise StructuralEVError("structural EV analysis spec differs from frozen protocol")
    manifest = json.loads(
        (root / "structural_ev.manifest.json").read_text(encoding="utf-8")
    )
    manifest_hashes = {
        entry["name"]: entry["sha256"] for entry in manifest.get("artifacts", [])
    }
    dataset_path = root / "structural_ev_rows.parquet"
    if manifest_hashes.get(dataset_path.name) != sha256_file(dataset_path):
        raise StructuralEVError("structural EV rows changed after the frozen build")
    audit = json.loads((root / "temporal_audit.json").read_text(encoding="utf-8"))
    if audit.get("status") != "PASS" or audit.get("protocol_freeze_id") != spec.protocol_freeze_id:
        raise StructuralEVError("structural EV temporal audit is not frozen PASS")
    frame = pd.read_parquet(dataset_path)
    summary_rows: list[dict[str, object]] = []
    month_rows: list[dict[str, object]] = []
    for grid_step, depth in spec.decision_states:
        state = frame.loc[
            frame["grid_step_pct"].eq(grid_step)
            & frame["deepest_filled_level_pct"].eq(depth)
        ].copy()
        if state.empty:
            raise StructuralEVError(
                f"structural EV state is absent: grid={grid_step}, depth={depth}"
            )
        for cost_bps in spec.sensitivity_cost_bps:
            policy_column = f"policy_net_return_{cost_bps}bps"
            hold_column = f"unconditional_hold_net_return_{cost_bps}bps"
            improvement_column = f"policy_minus_hold_return_{cost_bps}bps"
            policy = state[policy_column].to_numpy(dtype=float)
            hold = state[hold_column].to_numpy(dtype=float)
            day = pd.to_datetime(state["snapshot_time_ms"], unit="ms", utc=True).dt.floor("D")
            daily = state.assign(_day=day).groupby("_day")[policy_column].sum()
            summary_rows.append(
                {
                    "grid_step_pct": grid_step,
                    "deepest_filled_level_pct": depth,
                    "cost_bps": cost_bps,
                    "row_count": len(state),
                    "hold_row_count": int(state["decision"].eq("HOLD_48H").sum()),
                    "exit_row_count": int(state["decision"].eq("EXIT_AT_SNAPSHOT").sum()),
                    "policy_mean_net_return": float(np.mean(policy)),
                    "policy_median_net_return": float(np.median(policy)),
                    "policy_positive_rate": float(np.mean(policy > 0.0)),
                    "policy_q05": float(np.quantile(policy, 0.05)),
                    "policy_q01": float(np.quantile(policy, 0.01)),
                    "policy_cvar_5": _cvar(policy),
                    "unconditional_hold_mean_net_return": float(np.mean(hold)),
                    "unconditional_hold_cvar_5": _cvar(hold),
                    "policy_minus_hold_mean": float(np.mean(policy - hold)),
                    "positive_trading_day_fraction": float(np.mean(daily.to_numpy() > 0.0)),
                    "trading_day_count": len(daily),
                    "top_trade_removal_fraction_to_nonpositive": _top_removal_fraction(policy),
                }
            )
        primary_cost_state = state.assign(
            month=pd.to_datetime(state["snapshot_time_ms"], unit="ms", utc=True).dt.strftime(
                "%Y-%m"
            )
        )
        for month, month_frame in primary_cost_state.groupby("month", sort=True):
            month_rows.append(
                {
                    "grid_step_pct": grid_step,
                    "deepest_filled_level_pct": depth,
                    "month": month,
                    "row_count": len(month_frame),
                    "policy_mean_net_return": float(
                        month_frame[f"policy_net_return_{spec.primary_cost_bps}bps"].mean()
                    ),
                    "policy_minus_hold_mean": float(
                        month_frame[
                            f"policy_minus_hold_return_{spec.primary_cost_bps}bps"
                        ].mean()
                    ),
                }
            )
    summary = pd.DataFrame(summary_rows)
    months = pd.DataFrame(month_rows)
    primary = frame.loc[
        frame["grid_step_pct"].eq(spec.primary_grid_step_pct)
        & frame["deepest_filled_level_pct"].eq(spec.primary_deepest_level_pct)
    ].copy()
    policy_column = f"policy_net_return_{spec.primary_cost_bps}bps"
    improvement_column = f"policy_minus_hold_return_{spec.primary_cost_bps}bps"
    inference = pd.DataFrame(
        [
            _cluster_bootstrap_means(
                primary,
                cluster_column=cluster,
                policy_column=policy_column,
                improvement_column=improvement_column,
                iterations=spec.bootstrap_iterations,
                seed=spec.random_seed + offset,
                alpha=spec.familywise_alpha,
            )
            for offset, cluster in enumerate(("test_week", "symbol"))
        ]
    )
    primary_summary = summary.loc[
        summary["grid_step_pct"].eq(spec.primary_grid_step_pct)
        & summary["deepest_filled_level_pct"].eq(spec.primary_deepest_level_pct)
        & summary["cost_bps"].eq(spec.primary_cost_bps)
    ].iloc[0]
    primary_months = months.loc[
        months["grid_step_pct"].eq(spec.primary_grid_step_pct)
        & months["deepest_filled_level_pct"].eq(spec.primary_deepest_level_pct)
    ]
    checks = (
        ("min_primary_rows", primary_summary["row_count"], ">=", spec.min_primary_rows),
        (
            "min_primary_hold_rows",
            primary_summary["hold_row_count"],
            ">=",
            spec.min_primary_hold_rows,
        ),
        (
            "min_primary_exit_rows",
            primary_summary["exit_row_count"],
            ">=",
            spec.min_primary_exit_rows,
        ),
        ("positive_policy_mean", primary_summary["policy_mean_net_return"], ">", 0.0),
        ("positive_policy_improvement", primary_summary["policy_minus_hold_mean"], ">", 0.0),
        (
            "week_policy_lower_positive",
            inference.loc[inference["cluster"].eq("test_week"), "policy_lower_one_sided"].iloc[0],
            ">",
            0.0,
        ),
        (
            "symbol_policy_lower_positive",
            inference.loc[inference["cluster"].eq("symbol"), "policy_lower_one_sided"].iloc[0],
            ">",
            0.0,
        ),
        (
            "week_improvement_lower_positive",
            inference.loc[
                inference["cluster"].eq("test_week"), "improvement_lower_one_sided"
            ].iloc[0],
            ">",
            0.0,
        ),
        (
            "symbol_improvement_lower_positive",
            inference.loc[
                inference["cluster"].eq("symbol"), "improvement_lower_one_sided"
            ].iloc[0],
            ">",
            0.0,
        ),
        (
            "positive_month_count",
            int(primary_months["policy_mean_net_return"].gt(0.0).sum()),
            ">=",
            spec.min_positive_months,
        ),
        (
            "cvar_not_worse_than_hold",
            primary_summary["policy_cvar_5"] - primary_summary["unconditional_hold_cvar_5"],
            ">=",
            0.0,
        ),
        (
            "top_trade_removal_fraction",
            primary_summary["top_trade_removal_fraction_to_nonpositive"],
            ">=",
            spec.min_top_trade_removal_fraction,
        ),
    )
    gate_rows = []
    for name, observed, operator, required in checks:
        passed = (
            float(observed) >= float(required)
            if operator == ">="
            else float(observed) > float(required)
            if operator == ">"
            else float(observed) <= float(required)
        )
        gate_rows.append(
            {
                "gate": name,
                "observed": observed,
                "operator": operator,
                "required": required,
                "status": "PASS" if passed else "FAIL",
            }
        )
    gates = pd.DataFrame(gate_rows)
    for name, frame_to_write in (
        ("structural_ev_summary.csv", summary),
        ("structural_ev_month_stability.csv", months),
        ("structural_ev_cluster_inference.csv", inference),
        ("structural_ev_gates.csv", gates),
    ):
        temporary = root / (name + ".tmp")
        frame_to_write.to_csv(temporary, index=False)
        os.replace(temporary, root / name)
    all_pass = bool(gates["status"].eq("PASS").all())
    report_payload = {
        "protocol_freeze_id": spec.protocol_freeze_id,
        "status": (
            "IS_STRUCTURAL_PROTECTION_EV_GATES_PASS"
            if all_pass
            else "IS_STRUCTURAL_PROTECTION_EV_GATES_FAIL"
        ),
        "all_pre_registered_gates_pass": all_pass,
        "primary_state": [
            spec.primary_grid_step_pct,
            spec.primary_deepest_level_pct,
        ],
        "primary_cost_bps": spec.primary_cost_bps,
        "post_selection_evidence": True,
        "requires_forward_confirmation": True,
        "oos_2026_accessed": False,
        "execution_simulation_authorized": all_pass,
    }
    temporary_report = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary_report.write_text(
        json.dumps(report_payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary_report, report_path)
    analysis_manifest = build_manifest(
        run_id="drawdown-structural-ev-analysis-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=list(analysis_paths[:-1]),
        root=root,
    )
    write_manifest(analysis_paths[-1], analysis_manifest)
    return report_path


__all__ = [
    "StructuralEVError",
    "StructuralEVSpec",
    "analyze_structural_ev",
    "assemble_structural_ev_rows",
    "build_structural_ev_dataset",
    "write_structural_ev_protocol",
]
