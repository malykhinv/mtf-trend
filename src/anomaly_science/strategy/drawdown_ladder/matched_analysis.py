"""Paired, cluster-aware analysis for frozen prior non-drawdown controls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd


class MatchedComparisonError(ValueError):
    """Raised when signal/control artifacts violate the paired contract."""


def _require_passed_audit(root: Path, *, protocol_freeze_id: str) -> None:
    audit_path = root / "temporal_audit.json"
    if not audit_path.is_file():
        raise MatchedComparisonError(f"required temporal audit is missing: {audit_path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("status") != "PASS":
        raise MatchedComparisonError(f"temporal audit is not PASS: {audit_path}")
    if audit.get("protocol_freeze_id") != protocol_freeze_id:
        raise MatchedComparisonError(f"unexpected temporal-audit protocol: {audit_path}")
    if int(audit.get("untouched_2026_rows_used", 0)) != 0:
        raise MatchedComparisonError(f"temporal audit reports OOS access: {audit_path}")


@dataclass(frozen=True, slots=True)
class MatchedComparisonSpec:
    protocol_freeze_id: str = "drawdown_prior_control_comparison_20260802_v1"
    primary_strata: tuple[tuple[int, int], ...] = ((3, 6), (5, 10), (10, 20))
    recovery_cost_bps: int = 25
    bootstrap_iterations: int = 2_000
    random_seed: int = 20_260_803
    minimum_matched_rows: int = 200
    minimum_coverage_fraction: float = 0.70
    minimum_week_clusters: int = 100
    minimum_symbol_clusters: int = 100
    minimum_month_pairs: int = 30

    def __post_init__(self) -> None:
        if not self.primary_strata or len(set(self.primary_strata)) != len(self.primary_strata):
            raise ValueError("matched comparison primary strata must be non-empty and unique")
        if self.bootstrap_iterations < 100:
            raise ValueError("matched comparison requires at least 100 bootstrap draws")
        if not 0.0 < self.minimum_coverage_fraction <= 1.0:
            raise ValueError("matched comparison coverage threshold is invalid")


def _holm_adjust(p_values: dict[tuple[int, int], float]) -> dict[tuple[int, int], float]:
    ordered = sorted(p_values, key=lambda key: (p_values[key], key))
    total = len(ordered)
    running = 0.0
    result: dict[tuple[int, int], float] = {}
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, (total - rank) * p_values[key]))
        result[key] = running
    return result


def _paired_cluster_bootstrap(
    frame: pd.DataFrame,
    *,
    difference_column: str,
    cluster_column: str,
    iterations: int,
    seed: int,
) -> tuple[float, float, float, float, int]:
    valid = frame.loc[np.isfinite(pd.to_numeric(frame[difference_column], errors="coerce"))]
    grouped = valid.groupby(cluster_column, sort=True)[difference_column].agg(["count", "sum"])
    if len(grouped) < 2:
        raise MatchedComparisonError("paired bootstrap requires at least two clusters")
    count = grouped["count"].to_numpy(float)
    total = grouped["sum"].to_numpy(float)
    observed = float(total.sum() / count.sum())
    rng = np.random.default_rng(seed)
    draws = np.empty(iterations, dtype=float)
    cluster_count = len(grouped)
    for index in range(iterations):
        sampled = rng.integers(0, cluster_count, size=cluster_count)
        weight = np.bincount(sampled, minlength=cluster_count).astype(float)
        draws[index] = float((weight @ total) / (weight @ count))
    lower, upper = np.quantile(draws, [0.025, 0.975])
    p_value = float((1 + np.sum(draws <= 0.0)) / (iterations + 1))
    return observed, float(lower), float(upper), p_value, iterations


def _load_pairs(
    *,
    long_stage0_dir: Path,
    control_dir: Path,
    cost_bps: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    signal_candidates = pd.read_parquet(
        long_stage0_dir / "ladder_state_candidates.parquet",
        columns=[
            "candidate_id",
            "symbol",
            "grid_step_pct",
            "deepest_filled_level_pct",
            "snapshot_time_ms",
        ],
    )
    signal_outcomes = pd.read_parquet(
        long_stage0_dir / "ladder_state_outcomes.parquet",
        columns=[
            "candidate_id",
            "horizon_complete",
            f"break_even_{cost_bps}bps_reached",
            "future_return_2880m",
        ],
    )
    all_signal = signal_candidates.merge(
        signal_outcomes,
        on="candidate_id",
        how="left",
        validate="one_to_one",
    )
    controls = pd.read_parquet(
        control_dir / "matched_candidates.parquet",
        columns=[
            "pair_id",
            "signal_candidate_id",
            "symbol",
            "grid_step_pct",
            "deepest_filled_level_pct",
            "signal_snapshot_time_ms",
            "control_snapshot_time_ms",
            "match_score",
            "realized_vol_log_distance",
            "quote_volume_log_distance",
            "abs_return_distance",
        ],
    )
    control_outcomes = pd.read_parquet(
        control_dir / "matched_outcomes.parquet",
        columns=[
            "pair_id",
            "horizon_complete",
            f"break_even_{cost_bps}bps_reached",
            "future_return_2880m",
        ],
    )
    pairs = controls.merge(
        control_outcomes,
        on="pair_id",
        how="left",
        validate="one_to_one",
        suffixes=("", "_control_outcome"),
    )
    signal_for_join = all_signal.rename(
        columns={
            "candidate_id": "signal_candidate_id",
            "symbol": "expected_signal_symbol",
            "grid_step_pct": "expected_grid_step_pct",
            "deepest_filled_level_pct": "expected_deepest_filled_level_pct",
            "snapshot_time_ms": "expected_signal_snapshot_time_ms",
            "horizon_complete": "signal_horizon_complete",
            f"break_even_{cost_bps}bps_reached": "signal_recovered",
            "future_return_2880m": "signal_return_48h",
        }
    )
    pairs = pairs.merge(
        signal_for_join[
            [
                "signal_candidate_id",
                "expected_signal_symbol",
                "expected_grid_step_pct",
                "expected_deepest_filled_level_pct",
                "expected_signal_snapshot_time_ms",
                "signal_horizon_complete",
                "signal_recovered",
                "signal_return_48h",
            ]
        ],
        on="signal_candidate_id",
        how="left",
        validate="one_to_one",
    )
    control_recovery = f"break_even_{cost_bps}bps_reached"
    pairs = pairs.rename(
        columns={
            "horizon_complete": "control_horizon_complete",
            control_recovery: "control_recovered",
            "future_return_2880m": "control_return_48h",
        }
    )
    required = ["signal_recovered", "control_recovered", "signal_horizon_complete"]
    if any(bool(pairs[column].isna().any()) for column in required):
        raise MatchedComparisonError("paired signal/control join is incomplete")
    equality_checks = (
        pairs["symbol"].astype(str).eq(pairs["expected_signal_symbol"].astype(str)),
        pairs["grid_step_pct"].eq(pairs["expected_grid_step_pct"]),
        pairs["deepest_filled_level_pct"].eq(
            pairs["expected_deepest_filled_level_pct"]
        ),
        pairs["signal_snapshot_time_ms"].eq(
            pairs["expected_signal_snapshot_time_ms"]
        ),
    )
    if any(not bool(check.all()) for check in equality_checks):
        raise MatchedComparisonError("matched controls do not preserve signal identity")
    signal_time = pd.to_datetime(pairs["signal_snapshot_time_ms"], unit="ms", utc=True)
    iso = signal_time.dt.isocalendar()
    pairs["calendar_month"] = signal_time.dt.strftime("%Y-%m")
    pairs["week_cluster"] = (
        pairs["symbol"].astype(str)
        + "|"
        + iso["year"].astype(str)
        + "-W"
        + iso["week"].astype(str).str.zfill(2)
    )
    pairs["symbol_cluster"] = pairs["symbol"].astype(str)
    pairs["recovery_difference"] = (
        pairs["signal_recovered"].astype(bool).astype(float)
        - pairs["control_recovered"].astype(bool).astype(float)
    )
    both_complete = (
        pairs["signal_horizon_complete"].astype(bool)
        & pairs["control_horizon_complete"].astype(bool)
    )
    pairs["return_difference"] = np.where(
        both_complete,
        pd.to_numeric(pairs["signal_return_48h"], errors="coerce")
        - pd.to_numeric(pairs["control_return_48h"], errors="coerce"),
        np.nan,
    )
    return all_signal, pairs


def build_matched_control_comparison(
    *,
    long_stage0_dir: str | Path,
    control_dir: str | Path,
    output_dir: str | Path,
    spec: MatchedComparisonSpec = MatchedComparisonSpec(),
) -> Path:
    long_root = Path(long_stage0_dir)
    control_root = Path(control_dir)
    _require_passed_audit(
        long_root,
        protocol_freeze_id="drawdown_ladder_stage0_20260802_v1",
    )
    _require_passed_audit(
        control_root,
        protocol_freeze_id="prior_non_drawdown_control_20260802_v1",
    )
    all_signal, pairs = _load_pairs(
        long_stage0_dir=long_root,
        control_dir=control_root,
        cost_bps=spec.recovery_cost_bps,
    )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    exact_rows: list[dict[str, object]] = []
    month_rows: list[dict[str, object]] = []
    primary_p_recovery: dict[tuple[int, int], float] = {}
    primary_p_return: dict[tuple[int, int], float] = {}
    strata = sorted(
        set(zip(all_signal["grid_step_pct"], all_signal["deepest_filled_level_pct"], strict=True))
    )
    for stratum_index, (step_raw, depth_raw) in enumerate(strata):
        step, depth = int(step_raw), int(depth_raw)
        total = all_signal.loc[
            all_signal["grid_step_pct"].eq(step)
            & all_signal["deepest_filled_level_pct"].eq(depth)
        ]
        group = pairs.loc[
            pairs["grid_step_pct"].eq(step)
            & pairs["deepest_filled_level_pct"].eq(depth)
        ]
        coverage = len(group) / len(total) if len(total) else 0.0
        week_clusters = int(group["week_cluster"].nunique())
        symbol_clusters = int(group["symbol_cluster"].nunique())
        eligible = (
            len(group) >= spec.minimum_matched_rows
            and coverage >= spec.minimum_coverage_fraction
            and week_clusters >= spec.minimum_week_clusters
            and symbol_clusters >= spec.minimum_symbol_clusters
        )
        row: dict[str, object] = {
            "protocol_freeze_id": spec.protocol_freeze_id,
            "grid_step_pct": step,
            "deepest_filled_level_pct": depth,
            "primary_stratum": (step, depth) in spec.primary_strata,
            "eligible_for_inference": eligible,
            "signal_rows": len(total),
            "matched_pairs": len(group),
            "matched_coverage": coverage,
            "week_clusters": week_clusters,
            "symbol_clusters": symbol_clusters,
            "signal_recovery_25bps": (
                float(group["signal_recovered"].astype(bool).mean()) if len(group) else None
            ),
            "control_recovery_25bps": (
                float(group["control_recovered"].astype(bool).mean()) if len(group) else None
            ),
            "mean_match_score": float(group["match_score"].mean()) if len(group) else None,
            "p95_match_score": float(group["match_score"].quantile(0.95)) if len(group) else None,
            "unique_control_fraction": (
                float(
                    group[["symbol", "control_snapshot_time_ms"]]
                    .drop_duplicates()
                    .shape[0]
                    / len(group)
                )
                if len(group)
                else None
            ),
        }
        if eligible:
            results: dict[tuple[str, str], tuple[float, float, float, float, int]] = {}
            for metric_index, (metric, difference_column) in enumerate(
                (("recovery_25bps", "recovery_difference"), ("signed_return_48h", "return_difference"))
            ):
                for cluster_index, cluster_column in enumerate(("week_cluster", "symbol_cluster")):
                    cluster_name = "week" if cluster_column == "week_cluster" else "symbol"
                    result = _paired_cluster_bootstrap(
                        group,
                        difference_column=difference_column,
                        cluster_column=cluster_column,
                        iterations=spec.bootstrap_iterations,
                        seed=spec.random_seed + stratum_index * 10 + metric_index * 2 + cluster_index,
                    )
                    results[(metric, cluster_name)] = result
                    delta, lower, upper, p_value, draws = result
                    row[f"paired_{metric}_delta"] = delta
                    row[f"{metric}_{cluster_name}_ci_lower_95"] = lower
                    row[f"{metric}_{cluster_name}_ci_upper_95"] = upper
                    row[f"{metric}_{cluster_name}_one_sided_p"] = p_value
                    row[f"{metric}_{cluster_name}_bootstrap_draws"] = draws
            if (step, depth) in spec.primary_strata:
                primary_p_recovery[(step, depth)] = max(
                    results[("recovery_25bps", "week")][3],
                    results[("recovery_25bps", "symbol")][3],
                )
                primary_p_return[(step, depth)] = max(
                    results[("signed_return_48h", "week")][3],
                    results[("signed_return_48h", "symbol")][3],
                )
        else:
            for metric in ("recovery_25bps", "signed_return_48h"):
                row[f"paired_{metric}_delta"] = None
                for cluster_name in ("week", "symbol"):
                    row[f"{metric}_{cluster_name}_ci_lower_95"] = None
                    row[f"{metric}_{cluster_name}_ci_upper_95"] = None
                    row[f"{metric}_{cluster_name}_one_sided_p"] = None
                    row[f"{metric}_{cluster_name}_bootstrap_draws"] = 0
        exact_rows.append(row)
        for month, month_group in group.groupby("calendar_month", sort=True):
            month_rows.append(
                {
                    "grid_step_pct": step,
                    "deepest_filled_level_pct": depth,
                    "calendar_month": month,
                    "pair_count": len(month_group),
                    "eligible_for_stability": len(month_group) >= spec.minimum_month_pairs,
                    "paired_recovery_25bps_delta": float(month_group["recovery_difference"].mean()),
                    "paired_signed_return_48h_delta": float(month_group["return_difference"].mean()),
                }
            )
    recovery_holm = _holm_adjust(primary_p_recovery)
    return_holm = _holm_adjust(primary_p_return)
    for row in exact_rows:
        key = (int(row["grid_step_pct"]), int(row["deepest_filled_level_pct"]))
        row["primary_recovery_conservative_holm_p"] = recovery_holm.get(key)
        row["primary_return_conservative_holm_p"] = return_holm.get(key)
    exact = pd.DataFrame(exact_rows)
    months = pd.DataFrame(month_rows)
    exact_path = root / "matched_exact_strata.parquet"
    month_path = root / "matched_month_stability.parquet"
    exact.to_parquet(exact_path, index=False, compression="zstd")
    months.to_parquet(month_path, index=False, compression="zstd")
    report = {
        "protocol": asdict(spec),
        "status": "PAIRED_CONTROL_NO_EDGE_CLAIM",
        "oos_rows_read": 0,
        "signal_rows": len(all_signal),
        "matched_pairs": len(pairs),
        "overall_coverage": len(pairs) / len(all_signal) if len(all_signal) else None,
        "cluster_units": ["signal_symbol_x_iso_week", "signal_symbol"],
        "multiplicity": "Holm across three frozen primary strata using the worse cluster p-value",
        "artifacts": [str(exact_path), str(month_path)],
    }
    report_path = root / "matched_comparison_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


__all__ = [
    "MatchedComparisonError",
    "MatchedComparisonSpec",
    "build_matched_control_comparison",
]
