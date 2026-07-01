from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass(slots=True)
class ScreenedLeaf:
    source_kind: str
    fold_id: str
    generator_seed: int
    generator_depth: int
    tree_index: int
    leaf_index: int
    rule: tuple[dict[str, Any], ...]
    rule_text: str
    feature_names: tuple[str, ...]
    validation_count: int
    validation_fades: int
    validation_rate: float
    validation_base_rate: float
    validation_lift: float
    validation_wilson_lower_95: float
    passed: bool
    rejection_reason: str
    reference_groups: frozenset[str] = frozenset()


@dataclass(slots=True)
class FrozenPhenotype:
    phenotype_id: str
    representative: ScreenedLeaf
    fold_support_count: int
    generator_support_count: int
    cluster_rule_count: int
    search_oof_count: int
    search_oof_fades: int
    search_oof_rate: float
    search_oof_wilson_lower_95: float
    calibration_count: int = 0
    calibration_fades: int = 0
    calibration_rate: float = 0.0
    calibrated_probability: float = 0.0
    calibration_wilson_lower_95: float = 0.0
    verification_count: int = 0
    verification_fades: int = 0
    verification_rate: float = 0.0
    verification_wilson_lower_95: float = 0.0
    verification_calibration_gap: float = 1.0
    verification_p_value: float = 1.0
    verification_q_value: float = 1.0
    verification_positive_week_fraction: float = 0.0
    status: str = "FROZEN_UNCALIBRATED"


@dataclass(frozen=True, slots=True)
class PreparedPhenotypeData:
    search: pd.DataFrame
    calibration: pd.DataFrame
    verification: pd.DataFrame
    x_search: pd.DataFrame
    x_calibration: pd.DataFrame
    x_verification: pd.DataFrame
    imputation_values: dict[str, float]
    categorical_levels: dict[str, tuple[str, ...]]
    model_feature_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PhenotypeDiscoveryResult:
    catalog: pd.DataFrame
    followup_registry: pd.DataFrame
    screening: pd.DataFrame
    assignments: pd.DataFrame
    coverage: pd.DataFrame
    controls: pd.DataFrame
    frozen_rules: tuple[dict[str, Any], ...]
    feature_transform: dict[str, Any]
    search_row_count: int
    calibration_row_count: int
    verification_row_count: int
    candidate_count: int
    frozen_count: int
    verified_count: int
    high_probability_verified_count: int


__all__ = [
    "FrozenPhenotype",
    "PhenotypeDiscoveryResult",
    "PreparedPhenotypeData",
    "ScreenedLeaf",
]
