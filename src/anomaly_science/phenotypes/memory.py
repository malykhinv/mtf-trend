from __future__ import annotations

import json

import pandas as pd

from anomaly_science.phenotypes.config import CrossFittedPhenotypeConfig


_REQUIRED_COLUMNS = {
    "phenotype_id",
    "status",
    "rule_text",
    "feature_names",
    "search_oof_count",
    "search_oof_rate",
    "calibration_count",
    "calibrated_probability",
    "verification_count",
    "verification_rate",
    "verification_wilson_lower_95",
    "verification_calibration_gap",
    "verification_q_value",
    "verification_positive_week_fraction",
}


def build_phenotype_followup_registry(
    catalog: pd.DataFrame,
    config: CrossFittedPhenotypeConfig,
) -> pd.DataFrame:
    """Preserve every frozen phenotype without weakening confirmatory gates.

    The registry is research governance, not a second acceptance test.  It records
    what may be learned next while making reuse of the viewed verification period
    for rule or threshold tuning explicitly inadmissible.
    """
    missing = sorted(_REQUIRED_COLUMNS - set(catalog.columns))
    if missing:
        raise ValueError(f"phenotype catalog is missing follow-up fields: {missing}")

    rows: list[dict[str, object]] = []
    for row in catalog.itertuples(index=False):
        disposition, priority, failure_mode, next_study = _disposition(row, config)
        evidence = {
            "search_oof_count": int(row.search_oof_count),
            "search_oof_rate": float(row.search_oof_rate),
            "calibration_count": int(row.calibration_count),
            "calibrated_probability": float(row.calibrated_probability),
            "verification_count": int(row.verification_count),
            "verification_rate": float(row.verification_rate),
            "verification_wilson_lower_95": float(row.verification_wilson_lower_95),
            "verification_calibration_gap": float(row.verification_calibration_gap),
            "verification_q_value": float(row.verification_q_value),
            "verification_positive_week_fraction": float(
                row.verification_positive_week_fraction
            ),
        }
        rows.append(
            {
                "phenotype_id": row.phenotype_id,
                "current_status": row.status,
                "research_disposition": disposition,
                "followup_priority": priority,
                "failure_mode": failure_mode,
                "rule_text": row.rule_text,
                "mechanism_features": row.feature_names,
                "evidence_json": json.dumps(evidence, separators=(",", ":")),
                "next_registered_study": next_study,
                "allowed_action": (
                    "preregister mechanism ablation or feature refinement; freeze the new "
                    "protocol; evaluate on new forward data not used by this run"
                ),
                "forbidden_action": (
                    "change this frozen rule, probability threshold, or acceptance gate using "
                    "the viewed calibration/verification periods; present exploratory reuse as "
                    "independent confirmation"
                ),
                "source_protocol_freeze_id": config.protocol_freeze_id,
                "source_verification_end_utc": config.verification_end_utc,
            }
        )
    return pd.DataFrame(rows)


def _disposition(
    row: object,
    config: CrossFittedPhenotypeConfig,
) -> tuple[str, str, str, str]:
    status = str(row.status)
    if status == "HIGH_PROBABILITY_VERIFIED":
        return (
            "FORWARD_EV_CANDIDATE",
            "HIGH",
            "none within the registered prediction protocol; EV remains unproven",
            "freeze the phenotype and test calibrated EV on a new forward period",
        )
    if status == "VERIFIED_PHENOTYPE":
        failures: list[str] = []
        if float(row.calibrated_probability) < config.high_probability_threshold:
            failures.append("calibrated probability below high-probability gate")
        if float(row.verification_rate) < config.verification_min_observed_rate:
            failures.append("verification rate below high-probability gate")
        if float(row.verification_wilson_lower_95) < config.verification_min_wilson_lower_95:
            failures.append("verification Wilson lower bound below high-probability gate")
        if float(row.verification_calibration_gap) > config.verification_max_calibration_gap:
            failures.append("temporal calibration drift above gate")
        return (
            "FORWARD_RECALIBRATION_CANDIDATE",
            "HIGH",
            "; ".join(failures) or "high-probability composite gate not met",
            "test the frozen rule and a preregistered recalibration model on new forward data",
        )
    if status == "VERIFICATION_REJECTED":
        return (
            "FORWARD_STABILITY_CANDIDATE",
            "MEDIUM",
            "calibration candidate failed later temporal verification gates",
            "study regime stability and mechanism ablations, then confirm only on new forward data",
        )
    if status == "CALIBRATION_REJECTED":
        return (
            "ARCHIVED_RESEARCH_CANDIDATE",
            "LOW",
            "cross-fitted search candidate failed untouched calibration admission",
            "retain as negative evidence; revisit only under a new mechanistic protocol",
        )
    if status == "CONTROL_FAILED":
        return (
            "ARCHIVED_CONTROL_INVALIDATED",
            "LOW",
            "the discovery family did not beat registered null controls",
            "redesign the discovery protocol before any forward confirmation",
        )
    raise ValueError(f"unknown phenotype status for follow-up registry: {status}")


__all__ = ["build_phenotype_followup_registry"]
