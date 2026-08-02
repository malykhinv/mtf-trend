"""Frozen, cluster-aware comparison of drawdown/long and rally/short ladders."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


class MirrorComparisonError(ValueError):
    """Raised when mirror artifacts cannot satisfy the comparison contract."""


@dataclass(frozen=True, slots=True)
class MirrorComparisonSpec:
    protocol_freeze_id: str = "drawdown_vs_mirrored_rally_20260802_v1"
    primary_strata: tuple[tuple[int, int], ...] = ((3, 6), (5, 10), (10, 20))
    recovery_cost_bps: int = 25
    bootstrap_iterations: int = 2_000
    random_seed: int = 20_260_802
    minimum_rows_per_arm: int = 200
    minimum_clusters_per_arm: int = 100
    minimum_month_rows_per_arm: int = 30

    def __post_init__(self) -> None:
        if not self.primary_strata:
            raise ValueError("at least one primary mirror stratum is required")
        if len(set(self.primary_strata)) != len(self.primary_strata):
            raise ValueError("primary mirror strata must be unique")
        if self.recovery_cost_bps <= 0:
            raise ValueError("recovery cost must be positive")
        if self.bootstrap_iterations < 100:
            raise ValueError("mirror comparison requires at least 100 bootstrap draws")
        if self.minimum_rows_per_arm <= 0 or self.minimum_clusters_per_arm <= 0:
            raise ValueError("mirror support thresholds must be positive")


def _load_arm(root: Path, *, arm: str, cost_bps: int) -> pd.DataFrame:
    candidates = pd.read_parquet(
        root / "ladder_state_candidates.parquet",
        columns=[
            "candidate_id",
            "parent_event_id",
            "symbol",
            "snapshot_time_ms",
            "grid_step_pct",
            "deepest_filled_level_pct",
        ],
    )
    recovery_column = f"break_even_{cost_bps}bps_reached"
    outcomes = pd.read_parquet(
        root / "ladder_state_outcomes.parquet",
        columns=[
            "candidate_id",
            "horizon_complete",
            recovery_column,
            "future_return_2880m",
        ],
    )
    joined = candidates.merge(outcomes, on="candidate_id", how="left", validate="one_to_one")
    if len(joined) != len(candidates) or bool(joined[recovery_column].isna().any()):
        raise MirrorComparisonError(f"{arm} candidate/outcome join is incomplete")
    timestamp = pd.to_datetime(joined["snapshot_time_ms"], unit="ms", utc=True)
    iso = timestamp.dt.isocalendar()
    joined["calendar_month"] = timestamp.dt.strftime("%Y-%m")
    joined["cluster_id"] = (
        joined["symbol"].astype(str)
        + "|"
        + iso["year"].astype(str)
        + "-W"
        + iso["week"].astype(str).str.zfill(2)
    )
    joined["arm"] = arm
    joined["recovered"] = joined[recovery_column].astype(bool).astype(float)
    joined["signed_return_48h"] = pd.to_numeric(
        joined["future_return_2880m"], errors="coerce"
    )
    joined.loc[~joined["horizon_complete"].astype(bool), "signed_return_48h"] = np.nan
    return joined


def _cluster_bootstrap_delta(
    long: pd.DataFrame,
    short: pd.DataFrame,
    *,
    value_column: str,
    iterations: int,
    seed: int,
) -> tuple[float, float, float, float, int]:
    def aggregate(frame: pd.DataFrame) -> pd.DataFrame:
        valid = frame.loc[np.isfinite(pd.to_numeric(frame[value_column], errors="coerce"))]
        return valid.groupby("cluster_id", sort=True)[value_column].agg(["count", "sum"])

    long_cluster = aggregate(long)
    short_cluster = aggregate(short)
    cluster_ids = long_cluster.index.union(short_cluster.index).sort_values()
    if len(cluster_ids) < 2:
        raise MirrorComparisonError("cluster bootstrap requires at least two union clusters")
    long_count = long_cluster["count"].reindex(cluster_ids, fill_value=0).to_numpy(float)
    long_sum = long_cluster["sum"].reindex(cluster_ids, fill_value=0.0).to_numpy(float)
    short_count = short_cluster["count"].reindex(cluster_ids, fill_value=0).to_numpy(float)
    short_sum = short_cluster["sum"].reindex(cluster_ids, fill_value=0.0).to_numpy(float)
    observed = float(long_sum.sum() / long_count.sum() - short_sum.sum() / short_count.sum())
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    count = len(cluster_ids)
    for _ in range(iterations):
        sampled = rng.integers(0, count, size=count)
        weight = np.bincount(sampled, minlength=count).astype(float)
        long_n = float(weight @ long_count)
        short_n = float(weight @ short_count)
        if long_n <= 0.0 or short_n <= 0.0:
            continue
        draws.append(float((weight @ long_sum) / long_n - (weight @ short_sum) / short_n))
    if len(draws) < max(100, int(iterations * 0.9)):
        raise MirrorComparisonError("too few valid cluster bootstrap draws")
    values = np.asarray(draws, dtype=float)
    lower, upper = np.quantile(values, [0.025, 0.975])
    one_sided_p = float((1 + np.sum(values <= 0.0)) / (len(values) + 1))
    return observed, float(lower), float(upper), one_sided_p, len(values)


def _holm_adjust(p_values: dict[tuple[int, int], float]) -> dict[tuple[int, int], float]:
    ordered = sorted(p_values, key=lambda key: (p_values[key], key))
    adjusted: dict[tuple[int, int], float] = {}
    running = 0.0
    total = len(ordered)
    for rank, key in enumerate(ordered):
        value = min(1.0, (total - rank) * p_values[key])
        running = max(running, value)
        adjusted[key] = running
    return adjusted


def _month_rows(
    long: pd.DataFrame,
    short: pd.DataFrame,
    *,
    step: int,
    depth: int,
    minimum_rows: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    months = sorted(set(long["calendar_month"]) | set(short["calendar_month"]))
    for month in months:
        long_month = long.loc[long["calendar_month"].eq(month)]
        short_month = short.loc[short["calendar_month"].eq(month)]
        eligible = len(long_month) >= minimum_rows and len(short_month) >= minimum_rows
        rows.append(
            {
                "grid_step_pct": step,
                "deepest_filled_level_pct": depth,
                "calendar_month": month,
                "long_rows": len(long_month),
                "mirror_short_rows": len(short_month),
                "eligible_for_stability": eligible,
                "long_recovery_25bps": (
                    float(long_month["recovered"].mean()) if len(long_month) else None
                ),
                "mirror_short_recovery_25bps": (
                    float(short_month["recovered"].mean()) if len(short_month) else None
                ),
                "long_minus_mirror_recovery_25bps": (
                    float(long_month["recovered"].mean() - short_month["recovered"].mean())
                    if len(long_month) and len(short_month)
                    else None
                ),
                "long_mean_signed_return_48h": (
                    float(long_month["signed_return_48h"].mean()) if len(long_month) else None
                ),
                "mirror_short_mean_signed_return_48h": (
                    float(short_month["signed_return_48h"].mean()) if len(short_month) else None
                ),
            }
        )
    return rows


def build_mirror_comparison(
    *,
    long_stage0_dir: str | Path,
    mirror_stage0_dir: str | Path,
    output_dir: str | Path,
    spec: MirrorComparisonSpec = MirrorComparisonSpec(),
    progress: Callable[[str], None] | None = None,
) -> Path:
    long = _load_arm(Path(long_stage0_dir), arm="long_drawdown", cost_bps=spec.recovery_cost_bps)
    short = _load_arm(
        Path(mirror_stage0_dir), arm="short_rally_mirror", cost_bps=spec.recovery_cost_bps
    )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    exact_rows: list[dict[str, object]] = []
    month_rows: list[dict[str, object]] = []
    common_strata = sorted(
        set(zip(long["grid_step_pct"], long["deepest_filled_level_pct"], strict=True))
        & set(zip(short["grid_step_pct"], short["deepest_filled_level_pct"], strict=True))
    )
    primary_p_recovery: dict[tuple[int, int], float] = {}
    primary_p_return: dict[tuple[int, int], float] = {}
    for index, (step_raw, depth_raw) in enumerate(common_strata):
        step, depth = int(step_raw), int(depth_raw)
        long_group = long.loc[
            long["grid_step_pct"].eq(step)
            & long["deepest_filled_level_pct"].eq(depth)
        ]
        short_group = short.loc[
            short["grid_step_pct"].eq(step)
            & short["deepest_filled_level_pct"].eq(depth)
        ]
        long_clusters = int(long_group["cluster_id"].nunique())
        short_clusters = int(short_group["cluster_id"].nunique())
        eligible = (
            len(long_group) >= spec.minimum_rows_per_arm
            and len(short_group) >= spec.minimum_rows_per_arm
            and long_clusters >= spec.minimum_clusters_per_arm
            and short_clusters >= spec.minimum_clusters_per_arm
        )
        row: dict[str, object] = {
            "protocol_freeze_id": spec.protocol_freeze_id,
            "grid_step_pct": step,
            "deepest_filled_level_pct": depth,
            "primary_stratum": (step, depth) in spec.primary_strata,
            "eligible_for_inference": eligible,
            "long_rows": len(long_group),
            "mirror_short_rows": len(short_group),
            "long_clusters": long_clusters,
            "mirror_short_clusters": short_clusters,
            "long_symbols": int(long_group["symbol"].nunique()),
            "mirror_short_symbols": int(short_group["symbol"].nunique()),
            "long_recovery_25bps": float(long_group["recovered"].mean()),
            "mirror_short_recovery_25bps": float(short_group["recovered"].mean()),
            "long_mean_signed_return_48h": float(long_group["signed_return_48h"].mean()),
            "mirror_short_mean_signed_return_48h": float(
                short_group["signed_return_48h"].mean()
            ),
        }
        if eligible:
            recovery = _cluster_bootstrap_delta(
                long_group,
                short_group,
                value_column="recovered",
                iterations=spec.bootstrap_iterations,
                seed=spec.random_seed + index * 2,
            )
            returns = _cluster_bootstrap_delta(
                long_group,
                short_group,
                value_column="signed_return_48h",
                iterations=spec.bootstrap_iterations,
                seed=spec.random_seed + index * 2 + 1,
            )
            for prefix, result in (("recovery_25bps", recovery), ("signed_return_48h", returns)):
                delta, lower, upper, p_value, valid_draws = result
                row[f"long_minus_mirror_{prefix}"] = delta
                row[f"{prefix}_cluster_ci_lower_95"] = lower
                row[f"{prefix}_cluster_ci_upper_95"] = upper
                row[f"{prefix}_one_sided_p"] = p_value
                row[f"{prefix}_valid_bootstrap_draws"] = valid_draws
            if (step, depth) in spec.primary_strata:
                primary_p_recovery[(step, depth)] = recovery[3]
                primary_p_return[(step, depth)] = returns[3]
        else:
            for prefix in ("recovery_25bps", "signed_return_48h"):
                row[f"long_minus_mirror_{prefix}"] = None
                row[f"{prefix}_cluster_ci_lower_95"] = None
                row[f"{prefix}_cluster_ci_upper_95"] = None
                row[f"{prefix}_one_sided_p"] = None
                row[f"{prefix}_valid_bootstrap_draws"] = 0
        exact_rows.append(row)
        month_rows.extend(
            _month_rows(
                long_group,
                short_group,
                step=step,
                depth=depth,
                minimum_rows=spec.minimum_month_rows_per_arm,
            )
        )
        if progress and (index + 1) % 10 == 0:
            progress(f"mirror comparison {index + 1}/{len(common_strata)} strata")

    adjusted_recovery = _holm_adjust(primary_p_recovery)
    adjusted_return = _holm_adjust(primary_p_return)
    for row in exact_rows:
        key = (int(row["grid_step_pct"]), int(row["deepest_filled_level_pct"]))
        row["primary_recovery_holm_p"] = adjusted_recovery.get(key)
        row["primary_return_holm_p"] = adjusted_return.get(key)
    exact = pd.DataFrame(exact_rows)
    months = pd.DataFrame(month_rows)
    exact_path = root / "mirror_exact_strata.parquet"
    month_path = root / "mirror_month_stability.parquet"
    exact.to_parquet(exact_path, index=False, compression="zstd")
    months.to_parquet(month_path, index=False, compression="zstd")
    primary = exact.loc[exact["primary_stratum"].astype(bool)].copy()
    report = {
        "protocol": asdict(spec),
        "status": "DESCRIPTIVE_CONTROL_NO_EDGE_CLAIM",
        "oos_rows_read": 0,
        "long_rows": len(long),
        "mirror_short_rows": len(short),
        "common_exact_strata": len(common_strata),
        "eligible_exact_strata": int(exact["eligible_for_inference"].astype(bool).sum()),
        "primary_strata_present": [
            [int(row.grid_step_pct), int(row.deepest_filled_level_pct)]
            for row in primary.itertuples()
        ],
        "multiplicity": "Holm within the three frozen primary strata, separately by metric",
        "cluster_unit": "symbol_x_iso_week",
        "artifacts": [str(exact_path), str(month_path)],
    }
    report_path = root / "mirror_comparison_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


__all__ = [
    "MirrorComparisonError",
    "MirrorComparisonSpec",
    "build_mirror_comparison",
]
