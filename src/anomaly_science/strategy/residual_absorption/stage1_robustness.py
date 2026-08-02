"""Post-primary robustness and placebo checks for stage-1 response catch-up."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

from anomaly_science.strategy.residual_absorption.stage1_falsification import (
    weighted_median,
    weighted_spearman,
)

ROBUSTNESS_REPORT_VERSION = "residual_response_robustness_v1"


def build_stage1_analysis_frame(stage1_dir: str | Path) -> pd.DataFrame:
    root = Path(stage1_dir)
    profiles = pd.read_parquet(root / "residual_response_profiles_is.parquet")
    outcomes = pd.read_parquet(root / "response_outcomes_is.parquet")
    events = pd.read_parquet(root / "market_impulse_events_is.parquet")
    frame = profiles.merge(
        outcomes,
        on=["event_id", "symbol"],
        validate="one_to_one",
        suffixes=("", "_outcome"),
    ).merge(
        events[
            [
                "event_id",
                "utc_day",
                "factor_robust_z_15m",
                "directional_breadth_15m",
                "cross_section_symbol_count",
                "reference_support_count",
                "reference_confirmed",
            ]
        ],
        on="event_id",
        validate="many_to_one",
    )
    frame = frame.loc[frame["resolution_time_ms"].notna()].copy()
    frame["calendar_month"] = pd.to_datetime(
        frame["snapshot_time_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    return frame


def build_stage1_robustness_report(
    *,
    stage1_dir: str | Path,
    permutation_repetitions: int = 2_000,
    seed: int = 20250805,
) -> Path:
    if permutation_repetitions < 200:
        raise ValueError("placebo requires at least 200 repetitions")
    root = Path(stage1_dir)
    frame = build_stage1_analysis_frame(root)
    x = frame["direction_adjusted_underreaction_15m"].to_numpy(dtype=float)
    y = frame["catchup_residual_change_60m"].to_numpy(dtype=float)
    event_sizes = frame.groupby("event_id")["event_id"].transform("size").to_numpy()
    equal_event_weights = 1.0 / event_sizes
    equal_event = _estimands(x, y, weights=equal_event_weights)

    selected_event_ids = _non_overlapping_event_ids(frame, horizon_minutes=120)
    non_overlap = frame.loc[frame["event_id"].isin(selected_event_ids)]
    non_overlap_result = _estimands(
        non_overlap["direction_adjusted_underreaction_15m"].to_numpy(dtype=float),
        non_overlap["catchup_residual_change_60m"].to_numpy(dtype=float),
    )
    non_overlap_result["event_count"] = len(selected_event_ids)
    non_overlap_result["row_count"] = len(non_overlap)

    leave_one_month_out = []
    for month in sorted(frame["calendar_month"].unique()):
        subset = frame.loc[frame["calendar_month"].ne(month)]
        leave_one_month_out.append(
            {
                "excluded_month": str(month),
                **_estimands(
                    subset["direction_adjusted_underreaction_15m"].to_numpy(dtype=float),
                    subset["catchup_residual_change_60m"].to_numpy(dtype=float),
                ),
            }
        )

    removal_sensitivity = []
    descending = np.argsort(y, kind="mergesort")[::-1]
    for fraction in (0.01, 0.05, 0.10):
        removed = int(np.floor(fraction * len(frame)))
        keep = np.ones(len(frame), dtype=bool)
        keep[descending[:removed]] = False
        removal_sensitivity.append(
            {
                "removed_top_outcome_fraction": fraction,
                "removed_count": removed,
                **_estimands(x[keep], y[keep]),
            }
        )

    placebo = within_event_permutation_placebo(
        frame,
        repetitions=permutation_repetitions,
        seed=seed,
    )
    event_concentration = _event_concentration(frame)
    passed = bool(
        equal_event["spearman"] > 0.0
        and non_overlap_result["spearman"] > 0.0
        and all(row["spearman"] > 0.0 for row in leave_one_month_out)
        and placebo["observed_spearman"]
        > placebo["null_spearman_95th_percentile"]
    )
    report = {
        "report_version": ROBUSTNESS_REPORT_VERSION,
        "partition": "is",
        "status": "PASS" if passed else "FAIL",
        "population_count": len(frame),
        "event_count": int(frame["event_id"].nunique()),
        "equal_event_weighted": equal_event,
        "non_overlapping_120m_events": non_overlap_result,
        "leave_one_month_out": leave_one_month_out,
        "top_outcome_removal_sensitivity": removal_sensitivity,
        "within_event_permutation_placebo": placebo,
        "event_concentration": event_concentration,
        "interpretation_limit": (
            "This post-primary report can weaken confidence but cannot create independent confirmation."
        ),
    }
    path = root / "stage1_robustness_report_is.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def within_event_permutation_placebo(
    frame: pd.DataFrame,
    *,
    repetitions: int,
    seed: int,
) -> dict[str, object]:
    x = frame["direction_adjusted_underreaction_15m"].to_numpy(dtype=float)
    y = frame["catchup_residual_change_60m"].to_numpy(dtype=float)
    positive = x > 0.0
    x_rank = rankdata(x, method="average")
    y_rank = rankdata(y, method="average")
    x_centered = x_rank - x_rank.mean()
    x_norm = float(np.sqrt(np.dot(x_centered, x_centered)))
    groups = [
        indices.to_numpy(dtype=np.int64)
        for _, indices in frame.groupby("event_id").groups.items()
    ]
    observed_rho = float(spearmanr(x, y).statistic)
    observed_contrast = float(np.median(y[positive]) - np.median(y[~positive]))
    rng = np.random.default_rng(seed)
    null_rho = np.empty(repetitions, dtype=float)
    null_contrast = np.empty(repetitions, dtype=float)
    permuted_rank = np.empty_like(y_rank)
    permuted_y = np.empty_like(y)
    for repetition in range(repetitions):
        for indices in groups:
            order = rng.permutation(len(indices))
            permuted_rank[indices] = y_rank[indices[order]]
            permuted_y[indices] = y[indices[order]]
        centered = permuted_rank - permuted_rank.mean()
        null_rho[repetition] = float(
            np.dot(x_centered, centered)
            / (x_norm * np.sqrt(np.dot(centered, centered)))
        )
        null_contrast[repetition] = float(
            np.median(permuted_y[positive]) - np.median(permuted_y[~positive])
        )
    return {
        "repetitions": repetitions,
        "seed": seed,
        "observed_spearman": observed_rho,
        "null_spearman_95th_percentile": float(np.quantile(null_rho, 0.95)),
        "spearman_one_sided_randomization_p": float(
            (1 + np.sum(null_rho >= observed_rho)) / (repetitions + 1)
        ),
        "observed_positive_minus_nonpositive_median": observed_contrast,
        "null_median_contrast_95th_percentile": float(np.quantile(null_contrast, 0.95)),
        "median_contrast_one_sided_randomization_p": float(
            (1 + np.sum(null_contrast >= observed_contrast)) / (repetitions + 1)
        ),
    }


def _estimands(
    x: np.ndarray,
    y: np.ndarray,
    *,
    weights: np.ndarray | None = None,
) -> dict[str, float | int]:
    weight_values = np.ones(len(x), dtype=float) if weights is None else weights
    positive = x > 0.0
    return {
        "row_count": len(x),
        "spearman": weighted_spearman(x, y, weight_values),
        "positive_underreaction_median_60m": weighted_median(
            y[positive], weight_values[positive]
        ),
        "positive_minus_nonpositive_median_60m": weighted_median(
            y[positive], weight_values[positive]
        )
        - weighted_median(y[~positive], weight_values[~positive]),
    }


def _non_overlapping_event_ids(frame: pd.DataFrame, *, horizon_minutes: int) -> set[str]:
    events = frame[["event_id", "snapshot_time_ms"]].drop_duplicates().sort_values(
        ["snapshot_time_ms", "event_id"]
    )
    selected: set[str] = set()
    last_time: int | None = None
    horizon_ms = horizon_minutes * 60_000
    for raw in events.itertuples(index=False):
        timestamp = int(raw.snapshot_time_ms)
        if last_time is None or timestamp - last_time >= horizon_ms:
            selected.add(str(raw.event_id))
            last_time = timestamp
    return selected


def _event_concentration(frame: pd.DataFrame) -> dict[str, object]:
    positive = frame.loc[frame["catchup_residual_change_60m"] > 0.0].copy()
    contributions = (
        positive.groupby("event_id")["catchup_residual_change_60m"].sum().sort_values(
            ascending=False
        )
    )
    total = float(contributions.sum())
    shares = contributions / total if total > 0.0 else contributions * np.nan
    return {
        "positive_catchup_event_count": len(contributions),
        "largest_event_share_of_positive_catchup": float(shares.iloc[0]),
        "top_1pct_events_share_of_positive_catchup": float(
            shares.iloc[: max(1, int(np.ceil(0.01 * len(shares))))].sum()
        ),
        "top_5pct_events_share_of_positive_catchup": float(
            shares.iloc[: max(1, int(np.ceil(0.05 * len(shares))))].sum()
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run post-primary IS robustness checks.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    parser.add_argument("--permutation-repetitions", type=int, default=2_000)
    args = parser.parse_args()
    print(
        build_stage1_robustness_report(
            stage1_dir=args.stage1_dir,
            permutation_repetitions=args.permutation_repetitions,
        )
    )


__all__ = [
    "ROBUSTNESS_REPORT_VERSION",
    "build_stage1_analysis_frame",
    "build_stage1_robustness_report",
    "within_event_permutation_placebo",
]


if __name__ == "__main__":
    main()
