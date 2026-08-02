"""Pre-registered stage-1 residual catch-up falsification report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPORT_VERSION = "residual_response_falsification_v1"


def cluster_bootstrap_primary(
    frame: pd.DataFrame,
    *,
    cluster_column: str,
    repetitions: int = 2_000,
    seed: int = 20250803,
) -> dict[str, object]:
    """Resample whole clusters and recompute pooled weighted estimands exactly."""

    if repetitions < 200:
        raise ValueError("cluster bootstrap requires at least 200 repetitions")
    x = frame["direction_adjusted_underreaction_15m"].to_numpy(dtype=float)
    y = frame["catchup_residual_change_60m"].to_numpy(dtype=float)
    codes, clusters = pd.factorize(frame[cluster_column], sort=True)
    if len(clusters) < 2:
        raise ValueError("cluster bootstrap requires at least two clusters")
    x_groups = np.unique(x, return_inverse=True)[1]
    y_groups = np.unique(y, return_inverse=True)[1]
    positive = x > 0.0
    positive_order = np.argsort(y[positive], kind="mergesort")
    positive_y = y[positive][positive_order]
    rng = np.random.default_rng(seed)
    correlations = np.empty(repetitions, dtype=float)
    medians = np.empty(repetitions, dtype=float)
    probabilities = np.full(len(clusters), 1.0 / len(clusters))
    for index in range(repetitions):
        cluster_weights = rng.multinomial(len(clusters), probabilities)
        weights = cluster_weights[codes].astype(float)
        correlations[index] = _weighted_spearman(
            x_groups=x_groups,
            y_groups=y_groups,
            weights=weights,
        )
        positive_weights = weights[positive][positive_order]
        medians[index] = _weighted_median_sorted(positive_y, positive_weights)
    return {
        "cluster_column": cluster_column,
        "cluster_count": len(clusters),
        "repetitions": repetitions,
        "seed": seed,
        "spearman_95pct_percentile_ci": _percentile_interval(correlations),
        "positive_underreaction_median_60m_95pct_percentile_ci": _percentile_interval(
            medians
        ),
    }


def build_stage1_falsification_report(
    *,
    stage1_dir: str | Path,
    repetitions: int = 2_000,
) -> Path:
    root = Path(stage1_dir)
    profiles = pd.read_parquet(root / "residual_response_profiles_is.parquet")
    outcomes = pd.read_parquet(root / "response_outcomes_is.parquet")
    events = pd.read_parquet(root / "market_impulse_events_is.parquet")
    frame = profiles.merge(
        outcomes,
        left_on=["event_id", "symbol"],
        right_on=["event_id", "symbol"],
        validate="one_to_one",
        suffixes=("", "_outcome"),
    ).merge(
        events[["event_id", "utc_day", "reference_confirmed"]],
        on="event_id",
        validate="many_to_one",
    )
    frame = frame.loc[frame["resolution_time_ms"].notna()].copy()
    if frame.empty:
        raise ValueError("falsification report has no fully resolved profiles")
    frame["calendar_month"] = pd.to_datetime(
        frame["snapshot_time_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    x = frame["direction_adjusted_underreaction_15m"]
    y = frame["catchup_residual_change_60m"]
    positive = x > 0.0
    point_spearman = float(spearmanr(x, y).statistic)
    point_median = float(y.loc[positive].median())
    day_bootstrap = cluster_bootstrap_primary(
        frame,
        cluster_column="utc_day",
        repetitions=repetitions,
        seed=20250803,
    )
    symbol_bootstrap = cluster_bootstrap_primary(
        frame,
        cluster_column="symbol",
        repetitions=repetitions,
        seed=20250804,
    )
    day_rho_ci = day_bootstrap["spearman_95pct_percentile_ci"]
    day_median_ci = day_bootstrap[
        "positive_underreaction_median_60m_95pct_percentile_ci"
    ]
    passed = bool(
        point_spearman > 0.0
        and point_median > 0.0
        and float(day_rho_ci[0]) > 0.0
        and float(day_median_ci[0]) > 0.0
    )
    secondary_horizons = {}
    for horizon in (15, 30, 60, 120):
        values = frame[f"catchup_residual_change_{horizon}m"]
        secondary_horizons[str(horizon)] = {
            "spearman": float(spearmanr(x, values).statistic),
            "positive_underreaction_median": float(values.loc[positive].median()),
            "positive_underreaction_positive_rate": float((values.loc[positive] > 0.0).mean()),
        }
    deciles = pd.qcut(x, q=10, labels=False, duplicates="drop")
    decile_rows = []
    for decile, group in frame.assign(underreaction_decile=deciles).groupby(
        "underreaction_decile"
    ):
        decile_rows.append(
            {
                "decile": int(decile) + 1,
                "count": len(group),
                "median_underreaction": float(
                    group["direction_adjusted_underreaction_15m"].median()
                ),
                "median_catchup_60m": float(group["catchup_residual_change_60m"].median()),
                "positive_catchup_rate_60m": float(
                    (group["catchup_residual_change_60m"] > 0.0).mean()
                ),
            }
        )
    monthly = []
    for month, group in frame.groupby("calendar_month"):
        monthly.append(
            {
                "month": str(month),
                "count": len(group),
                "spearman": float(
                    spearmanr(
                        group["direction_adjusted_underreaction_15m"],
                        group["catchup_residual_change_60m"],
                    ).statistic
                ),
                "positive_underreaction_median_60m": float(
                    group.loc[
                        group["direction_adjusted_underreaction_15m"] > 0.0,
                        "catchup_residual_change_60m",
                    ].median()
                ),
            }
        )
    report = {
        "report_version": REPORT_VERSION,
        "partition": "is",
        "status": "PASS" if passed else "FAIL",
        "interpretation_limit": (
            "A pass establishes residual-response predictability only; it is not a tradable-edge result."
        ),
        "population_count": len(frame),
        "positive_underreaction_count": int(positive.sum()),
        "co_primary": {
            "spearman_underreaction_vs_catchup_60m": point_spearman,
            "positive_underreaction_median_catchup_60m": point_median,
            "positive_underreaction_median_catchup_60m_bps": point_median * 10_000.0,
            "utc_day_cluster_bootstrap": day_bootstrap,
            "symbol_cluster_sensitivity": symbol_bootstrap,
        },
        "secondary_horizons": secondary_horizons,
        "underreaction_deciles": decile_rows,
        "calendar_months": monthly,
    }
    path = root / "stage1_falsification_report_is.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _weighted_spearman(
    *,
    x_groups: np.ndarray,
    y_groups: np.ndarray,
    weights: np.ndarray,
) -> float:
    rank_x = _weighted_midrank(x_groups, weights)
    rank_y = _weighted_midrank(y_groups, weights)
    total = float(weights.sum())
    mean_x = float(np.dot(weights, rank_x) / total)
    mean_y = float(np.dot(weights, rank_y) / total)
    centered_x = rank_x - mean_x
    centered_y = rank_y - mean_y
    covariance = float(np.dot(weights, centered_x * centered_y))
    variance_x = float(np.dot(weights, centered_x**2))
    variance_y = float(np.dot(weights, centered_y**2))
    return covariance / np.sqrt(variance_x * variance_y)


def _weighted_midrank(groups: np.ndarray, weights: np.ndarray) -> np.ndarray:
    group_weights = np.bincount(groups, weights=weights)
    centers = np.cumsum(group_weights) - 0.5 * group_weights
    return centers[groups]


def _weighted_median_sorted(values: np.ndarray, weights: np.ndarray) -> float:
    cumulative = np.cumsum(weights)
    total = int(round(float(cumulative[-1])))
    if total <= 0:
        return float("nan")
    lower_position = (total + 1) // 2
    upper_position = (total + 2) // 2
    lower_index = min(
        np.searchsorted(cumulative, lower_position, side="left"), len(values) - 1
    )
    upper_index = min(
        np.searchsorted(cumulative, upper_position, side="left"), len(values) - 1
    )
    return float((values[lower_index] + values[upper_index]) / 2.0)


def _percentile_interval(values: np.ndarray) -> list[float]:
    lower, upper = np.quantile(values[np.isfinite(values)], (0.025, 0.975))
    return [float(lower), float(upper)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the locked stage-1 IS falsification report.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    parser.add_argument("--bootstrap-repetitions", type=int, default=2_000)
    args = parser.parse_args()
    print(
        build_stage1_falsification_report(
            stage1_dir=args.stage1_dir,
            repetitions=args.bootstrap_repetitions,
        )
    )


__all__ = [
    "REPORT_VERSION",
    "build_stage1_falsification_report",
    "cluster_bootstrap_primary",
]


if __name__ == "__main__":
    main()
