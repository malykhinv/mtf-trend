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


@dataclass(frozen=True, slots=True)
class _WeekMetricCache:
    unique_weeks: np.ndarray
    week_codes: np.ndarray
    row_count: np.ndarray
    positive_count: np.ndarray
    negative_count: np.ndarray
    log_loss_delta_sum: np.ndarray
    brier_delta_sum: np.ndarray
    concordance: np.ndarray


def compare_paired_oos_probabilities(
    baseline: pd.DataFrame,
    augmented: pd.DataFrame,
    config: PairedProbabilityComparisonConfig,
) -> PairedProbabilityComparisonResult:
    paired = _paired_frame(baseline, augmented)
    y = paired["target"].to_numpy(dtype=int)
    base = paired["baseline_probability"].to_numpy(dtype=float)
    aug = paired["augmented_probability"].to_numpy(dtype=float)
    rng = np.random.default_rng(config.random_seed)
    weeks = paired["test_week"].astype(str).to_numpy()
    cache = _build_week_metric_cache(y, base, aug, weeks)
    unique_weeks = cache.unique_weeks
    observed = _cached_incremental_metrics(
        cache,
        np.ones(len(unique_weeks), dtype=float),
    )
    bootstrap = {name: [] for name in observed}
    for _ in range(config.bootstrap_iterations):
        sampled_weeks = rng.choice(unique_weeks, size=len(unique_weeks), replace=True)
        sampled_codes = np.searchsorted(unique_weeks, sampled_weeks)
        weights = np.bincount(sampled_codes, minlength=len(unique_weeks)).astype(float)
        values = _cached_incremental_metrics(cache, weights)
        for name, value in values.items():
            if math.isfinite(value):
                bootstrap[name].append(value)
    null = {name: np.empty(config.sign_flip_iterations, dtype=float) for name in observed}
    for iteration in range(config.sign_flip_iterations):
        swap = rng.random(len(unique_weeks)) < 0.5
        values = _cached_sign_flip_metrics(cache, swap)
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


def _build_week_metric_cache(
    y: np.ndarray,
    baseline: np.ndarray,
    augmented: np.ndarray,
    weeks: np.ndarray,
) -> _WeekMetricCache:
    unique_weeks, week_codes = np.unique(weeks, return_inverse=True)
    week_count = len(unique_weeks)
    row_count = np.bincount(week_codes, minlength=week_count).astype(float)
    positive_count = np.bincount(
        week_codes,
        weights=(y == 1).astype(float),
        minlength=week_count,
    )
    negative_count = row_count - positive_count
    epsilon = np.finfo(float).eps
    base_clipped = np.clip(baseline, epsilon, 1.0 - epsilon)
    aug_clipped = np.clip(augmented, epsilon, 1.0 - epsilon)
    base_loss = -(y * np.log(base_clipped) + (1 - y) * np.log1p(-base_clipped))
    aug_loss = -(y * np.log(aug_clipped) + (1 - y) * np.log1p(-aug_clipped))
    log_loss_delta_sum = np.bincount(
        week_codes,
        weights=base_loss - aug_loss,
        minlength=week_count,
    )
    brier_delta_sum = np.bincount(
        week_codes,
        weights=(baseline - y) ** 2 - (augmented - y) ** 2,
        minlength=week_count,
    )
    probabilities = (baseline, augmented)
    concordance = np.zeros((2, 2, week_count, week_count), dtype=float)
    positive = y == 1
    negative = ~positive
    for positive_arm in range(2):
        for negative_arm in range(2):
            for positive_week in range(week_count):
                positive_values = probabilities[positive_arm][
                    positive & (week_codes == positive_week)
                ]
                for negative_week in range(week_count):
                    negative_values = probabilities[negative_arm][
                        negative & (week_codes == negative_week)
                    ]
                    concordance[
                        positive_arm,
                        negative_arm,
                        positive_week,
                        negative_week,
                    ] = _concordant_pair_sum(positive_values, negative_values)
    return _WeekMetricCache(
        unique_weeks=unique_weeks,
        week_codes=week_codes,
        row_count=row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        log_loss_delta_sum=log_loss_delta_sum,
        brier_delta_sum=brier_delta_sum,
        concordance=concordance,
    )


def _concordant_pair_sum(positive: np.ndarray, negative: np.ndarray) -> float:
    if not len(positive) or not len(negative):
        return 0.0
    ordered_negative = np.sort(negative)
    lower = np.searchsorted(ordered_negative, positive, side="left")
    upper = np.searchsorted(ordered_negative, positive, side="right")
    return float(np.sum(lower + 0.5 * (upper - lower)))


def _weighted_cached_auc(
    cache: _WeekMetricCache,
    *,
    arm: int,
    weights: np.ndarray,
) -> float:
    positive_total = float(weights @ cache.positive_count)
    negative_total = float(weights @ cache.negative_count)
    if positive_total <= 0.0 or negative_total <= 0.0:
        return math.nan
    numerator = float(weights @ cache.concordance[arm, arm] @ weights)
    return numerator / (positive_total * negative_total)


def _cached_incremental_metrics(
    cache: _WeekMetricCache,
    weights: np.ndarray,
) -> dict[str, float]:
    row_total = float(weights @ cache.row_count)
    return {
        "auc_delta": _weighted_cached_auc(cache, arm=1, weights=weights)
        - _weighted_cached_auc(cache, arm=0, weights=weights),
        "log_loss_improvement": float(weights @ cache.log_loss_delta_sum) / row_total,
        "brier_improvement": float(weights @ cache.brier_delta_sum) / row_total,
    }


def _cached_sign_flip_metrics(
    cache: _WeekMetricCache,
    swap: np.ndarray,
) -> dict[str, float]:
    week_count = len(cache.unique_weeks)
    row_index, column_index = np.indices((week_count, week_count))
    base_arm = swap.astype(np.int8)
    augmented_arm = 1 - base_arm
    denominator = float(cache.positive_count.sum() * cache.negative_count.sum())
    base_auc = float(
        cache.concordance[
            base_arm[row_index],
            base_arm[column_index],
            row_index,
            column_index,
        ].sum()
        / denominator
    )
    augmented_auc = float(
        cache.concordance[
            augmented_arm[row_index],
            augmented_arm[column_index],
            row_index,
            column_index,
        ].sum()
        / denominator
    )
    sign = np.where(swap, -1.0, 1.0)
    row_total = float(cache.row_count.sum())
    return {
        "auc_delta": augmented_auc - base_auc,
        "log_loss_improvement": float(sign @ cache.log_loss_delta_sum) / row_total,
        "brier_improvement": float(sign @ cache.brier_delta_sum) / row_total,
    }


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
