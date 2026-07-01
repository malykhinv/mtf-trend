from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import json
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.phenotypes.builder import (
    _benjamini_yekutieli,
    _week_probabilities,
    _week_sign_flip_test,
    _wilson_lower,
    prepare_phenotype_data,
)
from anomaly_science.phenotypes.config import CrossFittedPhenotypeConfig


@dataclass(frozen=True, slots=True)
class PhenotypeCompositionConfig:
    protocol_freeze_id: str
    min_order: int = 2
    max_order: int = 3
    calibration_min_events: int = 50
    calibration_min_probability: float = 0.70
    calibration_min_wilson_lower_95: float = 0.60
    beta_prior_strength: float = 20.0
    verification_min_events: int = 50
    verification_min_observed_rate: float = 0.65
    verification_min_wilson_lower_95: float = 0.60
    verification_max_calibration_gap: float = 0.08
    verification_min_positive_week_fraction: float = 0.60
    fdr_alpha: float = 0.05
    max_membership_jaccard: float = 0.90
    max_frozen_compositions: int = 50
    null_permutations: int = 19
    random_seed: int = 20260702

    def __post_init__(self) -> None:
        if not self.protocol_freeze_id.strip():
            raise ValueError("composition protocol_freeze_id is required")
        if not 2 <= self.min_order <= self.max_order <= 4:
            raise ValueError("composition order must satisfy 2 <= min <= max <= 4")
        if min(self.calibration_min_events, self.verification_min_events) <= 0:
            raise ValueError("composition support minima must be positive")
        for field in (
            "calibration_min_probability",
            "calibration_min_wilson_lower_95",
            "verification_min_observed_rate",
            "verification_min_wilson_lower_95",
            "verification_max_calibration_gap",
            "verification_min_positive_week_fraction",
            "fdr_alpha",
        ):
            if not 0.0 < float(getattr(self, field)) < 1.0:
                raise ValueError(f"{field} must lie in (0,1)")
        if self.beta_prior_strength <= 0.0:
            raise ValueError("composition beta prior strength must be positive")
        if not 0.0 <= self.max_membership_jaccard < 1.0:
            raise ValueError("composition Jaccard must lie in [0,1)")
        if self.max_frozen_compositions <= 0 or self.null_permutations < 19:
            raise ValueError("composition count must be positive and nulls at least 19")


@dataclass(frozen=True, slots=True)
class PhenotypeCompositionResult:
    catalog: pd.DataFrame
    screening: pd.DataFrame
    assignments: pd.DataFrame
    controls: pd.DataFrame
    coverage: pd.DataFrame


def load_phenotype_composition_config(path: Path) -> PhenotypeCompositionConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(payload) - set(PhenotypeCompositionConfig.__dataclass_fields__))
    if unknown:
        raise ValueError(f"unknown phenotype composition fields: {unknown}")
    return PhenotypeCompositionConfig(**payload)


def build_phenotype_compositions(
    frame: pd.DataFrame,
    base_rules: tuple[dict[str, object], ...],
    phenotype_config: CrossFittedPhenotypeConfig,
    composition_config: PhenotypeCompositionConfig,
) -> PhenotypeCompositionResult:
    if len(base_rules) < composition_config.min_order:
        raise ValueError("insufficient frozen base phenotypes for composition")
    ids = [str(item["phenotype_id"]) for item in base_rules]
    if len(ids) != len(set(ids)):
        raise ValueError("base phenotype ids must be unique")
    prepared = prepare_phenotype_data(frame, phenotype_config)
    calibration_masks = [
        _rule_mask(prepared.x_calibration, tuple(item["rule"])) for item in base_rules
    ]
    verification_masks = [
        _rule_mask(prepared.x_verification, tuple(item["rule"])) for item in base_rules
    ]
    cal_y = prepared.calibration[phenotype_config.input.label_column].to_numpy(np.int8)
    ver_y = prepared.verification[phenotype_config.input.label_column].to_numpy(np.int8)
    cal_base = float(cal_y.mean())
    raw: list[dict[str, object]] = []
    for order in range(composition_config.min_order, composition_config.max_order + 1):
        for indices in combinations(range(len(base_rules)), order):
            cal_mask = np.logical_and.reduce([calibration_masks[index] for index in indices])
            ver_mask = np.logical_and.reduce([verification_masks[index] for index in indices])
            cal_count = int(cal_mask.sum())
            fades = int(cal_y[cal_mask].sum())
            rate = fades / cal_count if cal_count else 0.0
            probability = (
                fades + composition_config.beta_prior_strength * cal_base
            ) / (cal_count + composition_config.beta_prior_strength)
            wilson = _wilson_lower(fades, cal_count)
            passed = (
                cal_count >= composition_config.calibration_min_events
                and probability >= composition_config.calibration_min_probability
                and wilson >= composition_config.calibration_min_wilson_lower_95
            )
            raw.append(
                {
                    "component_indices": indices,
                    "component_ids": ";".join(ids[index] for index in indices),
                    "order": order,
                    "calibration_count": cal_count,
                    "calibration_fades": fades,
                    "calibration_rate": rate,
                    "calibrated_probability": probability,
                    "calibration_wilson_lower_95": wilson,
                    "passed_calibration_gate": passed,
                    "rejection_reason": _calibration_rejection_reason(
                        cal_count, probability, wilson, composition_config
                    ),
                    "calibration_mask": cal_mask,
                    "verification_mask": ver_mask,
                }
            )
    screening = pd.DataFrame(
        [{key: value for key, value in row.items() if not key.endswith("_mask") and key != "component_indices"} for row in raw]
    )
    eligible = [row for row in raw if bool(row["passed_calibration_gate"])]
    frozen = _freeze_distinct(eligible, composition_config)
    controls = _permutation_controls(raw, cal_y, prepared.calibration, composition_config)
    count_p = _empirical_upper(
        controls["calibration_candidate_count"].to_numpy(), len(eligible)
    )
    best_real = max((float(row["calibrated_probability"]) for row in raw), default=0.0)
    best_p = _empirical_upper(
        controls["best_calibrated_probability"].to_numpy(), best_real
    )
    controls["observed_calibration_candidate_count"] = len(eligible)
    controls["observed_best_calibrated_probability"] = best_real
    controls["count_empirical_p_value"] = count_p
    controls["best_probability_empirical_p_value"] = best_p
    controls_passed = count_p <= 0.05 and best_p <= 0.05
    catalog, assignments = _verify(
        frozen,
        base_rules,
        prepared.verification,
        ver_y,
        phenotype_config,
        composition_config,
        controls_passed=controls_passed,
    )
    covered = set(assignments[phenotype_config.input.group_column].astype(str)) if not assignments.empty else set()
    verified_ids = set(
        catalog.loc[catalog["status"] == "HIGH_PROBABILITY_VERIFIED", "composition_id"]
    ) if not catalog.empty else set()
    verified_covered = set(
        assignments.loc[assignments["composition_id"].isin(verified_ids), phenotype_config.input.group_column].astype(str)
    ) if not assignments.empty else set()
    total = prepared.verification[phenotype_config.input.group_column].nunique()
    coverage = pd.DataFrame(
        [
            _coverage_row("all_frozen_compositions", total, len(covered)),
            _coverage_row("high_probability_verified", total, len(verified_covered)),
        ]
    )
    return PhenotypeCompositionResult(catalog, screening, assignments, controls, coverage)


def _rule_mask(matrix: pd.DataFrame, rule: tuple[dict[str, object], ...]) -> np.ndarray:
    mask = np.ones(len(matrix), dtype=bool)
    for condition in rule:
        values = matrix[str(condition["feature"])].to_numpy(dtype=float)
        if condition["operator"] == ">":
            mask &= values > float(condition["value"])
        elif condition["operator"] == "<=":
            mask &= values <= float(condition["value"])
        else:
            raise ValueError(f"unknown frozen rule operator: {condition['operator']}")
    return mask


def _calibration_rejection_reason(
    count: int,
    probability: float,
    wilson: float,
    config: PhenotypeCompositionConfig,
) -> str:
    reasons: list[str] = []
    if count < config.calibration_min_events:
        reasons.append("CALIBRATION_SUPPORT")
    if probability < config.calibration_min_probability:
        reasons.append("CALIBRATED_PROBABILITY")
    if wilson < config.calibration_min_wilson_lower_95:
        reasons.append("CALIBRATION_WILSON")
    return ";".join(reasons) or "PASS"


def _freeze_distinct(
    eligible: list[dict[str, object]],
    config: PhenotypeCompositionConfig,
) -> list[dict[str, object]]:
    ordered = sorted(
        eligible,
        key=lambda row: (
            -float(row["calibrated_probability"]),
            -int(row["calibration_count"]),
            int(row["order"]),
            str(row["component_ids"]),
        ),
    )
    frozen: list[dict[str, object]] = []
    seen: set[bytes] = set()
    for row in ordered:
        mask = np.asarray(row["calibration_mask"], dtype=bool)
        packed = np.packbits(mask).tobytes()
        if packed in seen:
            continue
        if any(_jaccard(mask, np.asarray(item["calibration_mask"], dtype=bool)) > config.max_membership_jaccard for item in frozen):
            continue
        seen.add(packed)
        frozen.append(row)
        if len(frozen) >= config.max_frozen_compositions:
            break
    return frozen


def _jaccard(left: np.ndarray, right: np.ndarray) -> float:
    union = int(np.count_nonzero(left | right))
    return int(np.count_nonzero(left & right)) / union if union else 1.0


def _calendar_permute(labels: np.ndarray, frame: pd.DataFrame, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    month = pd.to_datetime(frame["__snapshot_ms"], unit="ms", utc=True).dt.strftime("%Y-%m").to_numpy()
    result = labels.copy()
    for value in pd.unique(month):
        positions = np.flatnonzero(month == value)
        result[positions] = result[rng.permutation(positions)]
    return result


def _permutation_controls(
    raw: list[dict[str, object]],
    labels: np.ndarray,
    frame: pd.DataFrame,
    config: PhenotypeCompositionConfig,
) -> pd.DataFrame:
    base = float(labels.mean())
    rows: list[dict[str, object]] = []
    for iteration in range(config.null_permutations):
        shuffled = _calendar_permute(labels, frame, config.random_seed + 1009 * iteration)
        count = 0
        best = 0.0
        for candidate in raw:
            mask = np.asarray(candidate["calibration_mask"], dtype=bool)
            support = int(mask.sum())
            fades = int(shuffled[mask].sum())
            probability = (
                fades + config.beta_prior_strength * base
            ) / (support + config.beta_prior_strength)
            wilson = _wilson_lower(fades, support)
            best = max(best, probability)
            count += int(
                support >= config.calibration_min_events
                and probability >= config.calibration_min_probability
                and wilson >= config.calibration_min_wilson_lower_95
            )
        rows.append(
            {
                "control": "within_month_calibration_label_permutation",
                "iteration": iteration,
                "calibration_candidate_count": count,
                "best_calibrated_probability": best,
            }
        )
    return pd.DataFrame(rows)


def _empirical_upper(null: np.ndarray, observed: float | int) -> float:
    return float((1 + np.count_nonzero(null >= observed)) / (len(null) + 1))


def _verify(
    frozen: list[dict[str, object]],
    base_rules: tuple[dict[str, object], ...],
    verification: pd.DataFrame,
    labels: np.ndarray,
    phenotype_config: CrossFittedPhenotypeConfig,
    config: PhenotypeCompositionConfig,
    *,
    controls_passed: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    week_base = _week_probabilities(
        verification, labels, phenotype_config.input.group_column
    )
    evaluated: list[dict[str, object]] = []
    p_values: list[float] = []
    for index, row in enumerate(frozen, start=1):
        mask = np.asarray(row["verification_mask"], dtype=bool)
        positions = np.flatnonzero(mask)
        selected = labels[positions]
        count = len(positions)
        fades = int(selected.sum())
        rate = float(selected.mean()) if count else 0.0
        wilson = _wilson_lower(fades, count)
        p_value, positive_weeks = _week_sign_flip_test(
            verification,
            positions,
            labels,
            week_base,
            seed=config.random_seed + index * 101,
        )
        evaluated.append(
            {
                **row,
                "composition_id": f"fade_composition_{index:03d}",
                "verification_count": count,
                "verification_fades": fades,
                "verification_rate": rate,
                "verification_wilson_lower_95": wilson,
                "verification_calibration_gap": abs(
                    rate - float(row["calibrated_probability"])
                ) if count else 1.0,
                "verification_p_value": p_value,
                "verification_positive_week_fraction": positive_weeks,
                "verification_positions": positions,
            }
        )
        p_values.append(p_value)
    q_values = _benjamini_yekutieli(p_values)
    catalog_rows: list[dict[str, object]] = []
    assignment_frames: list[pd.DataFrame] = []
    base_by_id = {str(item["phenotype_id"]): item for item in base_rules}
    identity = [
        phenotype_config.input.group_column,
        phenotype_config.input.split_group_column,
        phenotype_config.input.symbol_column,
        "__snapshot_ms",
        phenotype_config.input.label_column,
    ]
    for row, q_value in zip(evaluated, q_values, strict=True):
        verified = (
            controls_passed
            and int(row["verification_count"]) >= config.verification_min_events
            and float(row["verification_rate"]) >= config.verification_min_observed_rate
            and float(row["verification_wilson_lower_95"]) >= config.verification_min_wilson_lower_95
            and float(row["verification_calibration_gap"]) <= config.verification_max_calibration_gap
            and float(row["verification_positive_week_fraction"])
            >= config.verification_min_positive_week_fraction
            and q_value <= config.fdr_alpha
        )
        status = (
            "CONTROL_FAILED"
            if not controls_passed
            else "HIGH_PROBABILITY_VERIFIED"
            if verified
            else "VERIFICATION_REJECTED"
        )
        failure_mode = _verification_failure_mode(
            row, q_value, config, controls_passed=controls_passed
        )
        disposition, priority, next_study = _composition_disposition(
            status, float(row["verification_rate"])
        )
        component_ids = str(row["component_ids"]).split(";")
        combined_rule = [
            condition
            for component_id in component_ids
            for condition in base_by_id[component_id]["rule"]
        ]
        catalog_rows.append(
            {
                "composition_id": row["composition_id"],
                "status": status,
                "research_disposition": disposition,
                "followup_priority": priority,
                "failure_mode": failure_mode,
                "next_registered_study": next_study,
                "allowed_action": (
                    "freeze a new mechanism or drift-correction protocol and confirm on data "
                    "after the source verification boundary"
                ),
                "forbidden_action": (
                    "retune component rules, composition membership, or gates on the viewed "
                    "calibration/verification periods"
                ),
                "component_ids": row["component_ids"],
                "order": row["order"],
                "combined_rule_json": json.dumps(combined_rule, separators=(",", ":")),
                "calibration_count": row["calibration_count"],
                "calibration_fades": row["calibration_fades"],
                "calibration_rate": row["calibration_rate"],
                "calibrated_probability": row["calibrated_probability"],
                "calibration_wilson_lower_95": row["calibration_wilson_lower_95"],
                "verification_count": row["verification_count"],
                "verification_fades": row["verification_fades"],
                "verification_rate": row["verification_rate"],
                "verification_wilson_lower_95": row["verification_wilson_lower_95"],
                "verification_calibration_gap": row["verification_calibration_gap"],
                "verification_p_value": row["verification_p_value"],
                "verification_q_value": q_value,
                "verification_positive_week_fraction": row["verification_positive_week_fraction"],
            }
        )
        assigned = verification.iloc[np.asarray(row["verification_positions"], dtype=int)][identity].copy()
        assigned.insert(0, "composition_id", row["composition_id"])
        assigned.insert(1, "status", status)
        assigned.insert(2, "frozen_probability", row["calibrated_probability"])
        assignment_frames.append(assigned)
    catalog = pd.DataFrame(catalog_rows)
    assignments = pd.concat(assignment_frames, ignore_index=True) if assignment_frames else pd.DataFrame(
        columns=["composition_id", "status", "frozen_probability", *identity]
    )
    return catalog, assignments


def _verification_failure_mode(
    row: dict[str, object],
    q_value: float,
    config: PhenotypeCompositionConfig,
    *,
    controls_passed: bool,
) -> str:
    if not controls_passed:
        return "composition family failed registered null controls"
    reasons: list[str] = []
    if int(row["verification_count"]) < config.verification_min_events:
        reasons.append("verification support")
    if float(row["verification_rate"]) < config.verification_min_observed_rate:
        reasons.append("observed rate")
    if float(row["verification_wilson_lower_95"]) < config.verification_min_wilson_lower_95:
        reasons.append("Wilson lower bound")
    if float(row["verification_calibration_gap"]) > config.verification_max_calibration_gap:
        reasons.append("temporal calibration drift")
    if float(row["verification_positive_week_fraction"]) < config.verification_min_positive_week_fraction:
        reasons.append("positive-week stability")
    if q_value > config.fdr_alpha:
        reasons.append("BY-FDR")
    return "; ".join(reasons) or "none within registered prediction protocol"


def _composition_disposition(status: str, verification_rate: float) -> tuple[str, str, str]:
    if status == "HIGH_PROBABILITY_VERIFIED":
        return (
            "FORWARD_EV_CANDIDATE",
            "HIGH",
            "test unchanged composition probability and EV on new forward data",
        )
    if status == "VERIFICATION_REJECTED":
        priority = "HIGH" if verification_rate >= 0.60 else "MEDIUM"
        return (
            "FORWARD_COMPOSITION_STABILITY_CANDIDATE",
            priority,
            "diagnose regime drift and confirm any preregistered correction on new forward data",
        )
    return (
        "ARCHIVED_CONTROL_INVALIDATED",
        "LOW",
        "redesign composition controls before forward confirmation",
    )


def _coverage_row(kind: str, total: int, covered: int) -> dict[str, object]:
    return {
        "coverage_kind": kind,
        "verification_group_count": total,
        "covered_group_count": covered,
        "coverage_fraction": covered / total if total else 0.0,
        "unclassified_group_count": total - covered,
    }


__all__ = [
    "PhenotypeCompositionConfig",
    "PhenotypeCompositionResult",
    "build_phenotype_compositions",
    "load_phenotype_composition_config",
]
