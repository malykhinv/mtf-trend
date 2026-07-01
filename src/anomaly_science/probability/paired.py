from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, roc_auc_score


class PairedProbabilityError(ValueError):
    """Raised when two OOS probability artifacts are not exactly comparable."""


@dataclass(frozen=True, slots=True)
class PairedProbabilityComparisonConfig:
    protocol_freeze_id: str
    bootstrap_iterations: int = 2_000
    sign_flip_iterations: int = 999
    alpha: float = 0.016666666666666666
    min_auc_delta: float = 0.01
    min_log_loss_improvement: float = 0.002
    min_brier_improvement: float = 0.001
    random_seed: int = 20260630

    def __post_init__(self) -> None:
        if not self.protocol_freeze_id:
            raise PairedProbabilityError("protocol_freeze_id is required")
        if self.bootstrap_iterations < 1_000:
            raise PairedProbabilityError("bootstrap_iterations must be at least 1000")
        if self.sign_flip_iterations < 999:
            raise PairedProbabilityError("sign_flip_iterations must be at least 999")
        if not 0.0 < self.alpha < 0.5:
            raise PairedProbabilityError("alpha must lie in (0, 0.5)")
        if min(self.min_auc_delta, self.min_log_loss_improvement, self.min_brier_improvement) < 0.0:
            raise PairedProbabilityError("minimum incremental effects must be non-negative")


@dataclass(frozen=True, slots=True)
class PairedProbabilityComparisonResult:
    metrics: pd.DataFrame
    inference: pd.DataFrame
    gates: pd.DataFrame


def compare_paired_oos_probabilities(
    baseline: pd.DataFrame,
    augmented: pd.DataFrame,
    config: PairedProbabilityComparisonConfig,
) -> PairedProbabilityComparisonResult:
    paired = _paired_frame(baseline, augmented)
    y = paired["target"].to_numpy(dtype=int)
    base = paired["baseline_probability"].to_numpy(dtype=float)
    aug = paired["augmented_probability"].to_numpy(dtype=float)
    observed = _incremental_metrics(y, base, aug)
    rng = np.random.default_rng(config.random_seed)
    weeks = paired["test_week"].astype(str).to_numpy()
    unique_weeks = np.unique(weeks)
    bootstrap = {name: [] for name in observed}
    for _ in range(config.bootstrap_iterations):
        sampled_weeks = rng.choice(unique_weeks, size=len(unique_weeks), replace=True)
        indices = np.concatenate([np.flatnonzero(weeks == week) for week in sampled_weeks])
        values = _incremental_metrics(y[indices], base[indices], aug[indices])
        for name, value in values.items():
            if math.isfinite(value):
                bootstrap[name].append(value)
    null = {name: np.empty(config.sign_flip_iterations, dtype=float) for name in observed}
    for iteration in range(config.sign_flip_iterations):
        swap_weeks = set(unique_weeks[rng.random(len(unique_weeks)) < 0.5])
        swap = np.fromiter((week in swap_weeks for week in weeks), dtype=bool, count=len(weeks))
        perm_base = np.where(swap, aug, base)
        perm_aug = np.where(swap, base, aug)
        values = _incremental_metrics(y, perm_base, perm_aug)
        for name, value in values.items():
            null[name][iteration] = value
    inference_rows: list[dict[str, object]] = []
    for name, value in observed.items():
        draws = np.asarray(bootstrap[name], dtype=float)
        inference_rows.append(
            {
                "metric": name,
                "observed_delta": value,
                "bootstrap_ci_lower_95": float(np.quantile(draws, 0.025)),
                "bootstrap_ci_upper_95": float(np.quantile(draws, 0.975)),
                "cluster_sign_flip_p_value": float(
                    (1 + np.sum(null[name] >= value)) / (config.sign_flip_iterations + 1)
                ),
                "bootstrap_iterations": config.bootstrap_iterations,
                "sign_flip_iterations": config.sign_flip_iterations,
                "cluster": "ISO_week",
            }
        )
    inference = pd.DataFrame(inference_rows)
    required = {
        "auc_delta": config.min_auc_delta,
        "log_loss_improvement": config.min_log_loss_improvement,
        "brier_improvement": config.min_brier_improvement,
    }
    gate_rows: list[dict[str, object]] = []
    for metric, minimum in required.items():
        row = inference.loc[inference["metric"] == metric].iloc[0]
        checks = (
            (f"{metric}_minimum", float(row.observed_delta), ">=", minimum),
            (f"{metric}_ci_lower_positive", float(row.bootstrap_ci_lower_95), ">", 0.0),
            (f"{metric}_familywise_p", float(row.cluster_sign_flip_p_value), "<=", config.alpha),
        )
        for name, value, operator, threshold in checks:
            passed = value >= threshold if operator == ">=" else value > threshold if operator == ">" else value <= threshold
            gate_rows.append(
                {
                    "gate": name,
                    "observed": value,
                    "operator": operator,
                    "required": threshold,
                    "status": "PASS" if passed else "FAIL",
                }
            )
    metric_rows = []
    for arm, probabilities in (("baseline", base), ("augmented", aug)):
        metric_rows.extend(
            (
                {"arm": arm, "metric": "auc", "value": float(roc_auc_score(y, probabilities)), "row_count": len(y)},
                {"arm": arm, "metric": "log_loss", "value": float(log_loss(y, probabilities, labels=[0, 1])), "row_count": len(y)},
                {"arm": arm, "metric": "brier", "value": float(np.mean((probabilities - y) ** 2)), "row_count": len(y)},
                {"arm": arm, "metric": "mean_probability", "value": float(np.mean(probabilities)), "row_count": len(y)},
                {"arm": arm, "metric": "positive_rate", "value": float(np.mean(y)), "row_count": len(y)},
            )
        )
    return PairedProbabilityComparisonResult(
        metrics=pd.DataFrame(metric_rows),
        inference=inference,
        gates=pd.DataFrame(gate_rows),
    )


def _paired_frame(baseline: pd.DataFrame, augmented: pd.DataFrame) -> pd.DataFrame:
    keys = ["group", "symbol", "snapshot_time_ms", "test_week"]
    required = {*keys, "target", "calibrated_probability", "weekly_model_freeze_time_ms"}
    for name, frame in (("baseline", baseline), ("augmented", augmented)):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise PairedProbabilityError(f"{name} predictions missing columns: {missing}")
        if frame[keys].duplicated().any():
            raise PairedProbabilityError(f"{name} predictions contain duplicate paired keys")
    left = baseline.loc[:, [*keys, "target", "calibrated_probability", "weekly_model_freeze_time_ms"]].rename(
        columns={
            "calibrated_probability": "baseline_probability",
            "weekly_model_freeze_time_ms": "baseline_freeze",
        }
    )
    right = augmented.loc[:, [*keys, "target", "calibrated_probability", "weekly_model_freeze_time_ms"]].rename(
        columns={
            "target": "augmented_target",
            "calibrated_probability": "augmented_probability",
            "weekly_model_freeze_time_ms": "augmented_freeze",
        }
    )
    paired = left.merge(right, on=keys, how="outer", validate="one_to_one", indicator=True)
    if (paired["_merge"] != "both").any():
        raise PairedProbabilityError("baseline and augmented OOS populations differ")
    if not (paired["target"] == paired["augmented_target"]).all():
        raise PairedProbabilityError("baseline and augmented targets differ")
    if not (paired["baseline_freeze"] == paired["augmented_freeze"]).all():
        raise PairedProbabilityError("baseline and augmented weekly freezes differ")
    return paired.sort_values(keys, kind="mergesort").reset_index(drop=True)


def _incremental_metrics(y: np.ndarray, baseline: np.ndarray, augmented: np.ndarray) -> dict[str, float]:
    return {
        "auc_delta": float(roc_auc_score(y, augmented) - roc_auc_score(y, baseline)),
        "log_loss_improvement": float(
            log_loss(y, baseline, labels=[0, 1]) - log_loss(y, augmented, labels=[0, 1])
        ),
        "brier_improvement": float(np.mean((baseline - y) ** 2) - np.mean((augmented - y) ** 2)),
    }


__all__ = [
    "PairedProbabilityComparisonConfig",
    "PairedProbabilityComparisonResult",
    "PairedProbabilityError",
    "compare_paired_oos_probabilities",
]
