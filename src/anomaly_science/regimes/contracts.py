from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FrozenRegimeBin:
    regime_id: str
    feature_name: str
    bin_index: int
    lower_bound: float | None
    upper_bound: float | None


@dataclass(frozen=True, slots=True)
class FrozenRegimeInteraction:
    regime_id: str
    component_features: str
    component_bins: str
    rationale: str


@dataclass(frozen=True, slots=True)
class RegimeEvidenceRow:
    regime_id: str
    protocol_freeze_id: str
    evidence_scope: str
    pristine_claim_allowed: bool
    feature_name: str
    bin_index: int
    lower_bound: float | None
    upper_bound: float | None
    discovery_event_count: int
    discovery_matched_count: int
    discovery_matched_coverage: float
    discovery_label_rate: float
    discovery_matched_rate: float
    discovery_delta: float
    discovery_ci_lower_95: float
    discovery_ci_upper_95: float
    discovery_month_ci_lower_95: float
    discovery_month_ci_upper_95: float
    discovery_symbol_ci_lower_95: float
    discovery_symbol_ci_upper_95: float
    verification_event_count: int
    verification_matched_count: int
    verification_matched_coverage: float
    verification_label_rate: float
    verification_matched_rate: float
    verification_delta: float
    verification_ci_lower_95: float
    verification_ci_upper_95: float
    verification_month_ci_lower_95: float
    verification_month_ci_upper_95: float
    verification_symbol_ci_lower_95: float
    verification_symbol_ci_upper_95: float
    verification_lift: float
    shuffled_p_value: float
    fdr_q_value: float
    positive_month_fraction: float
    eligible_month_count: int
    positive_symbol_fraction: float
    eligible_symbol_count: int
    status: str
    rejection_reasons: str
    hypothesis_kind: str = "single"
    component_features: str = ""
    component_bins: str = ""


@dataclass(frozen=True, slots=True)
class RegimeStabilityRow:
    regime_id: str
    dimension: str
    value: str
    event_count: int
    label_rate: float
    matched_rate: float
    delta: float
    eligible: bool


@dataclass(frozen=True, slots=True)
class RegimeControlRow:
    control_name: str
    iterations: int
    candidate_count: int
    replicated_regime_count: int
    minimum_p_value: float
    notes: str


@dataclass(frozen=True, slots=True)
class RegimeScreeningRow:
    regime_id: str
    hypothesis_kind: str
    component_features: str
    component_bins: str
    discovery_event_count: int
    discovery_matched_count: int
    discovery_matched_coverage: float
    discovery_delta: float
    discovery_week_ci_lower_95: float
    discovery_month_ci_lower_95: float
    discovery_symbol_ci_lower_95: float
    status: str
    rejection_reasons: str


@dataclass(frozen=True, slots=True)
class CausalRegimeAtlasResult:
    frozen_bins: tuple[FrozenRegimeBin, ...]
    frozen_interactions: tuple[FrozenRegimeInteraction, ...]
    evidence_rows: tuple[RegimeEvidenceRow, ...]
    stability_rows: tuple[RegimeStabilityRow, ...]
    control_rows: tuple[RegimeControlRow, ...]
    screening_rows: tuple[RegimeScreeningRow, ...]
    discovery_row_count: int
    verification_row_count: int


__all__ = [
    "CausalRegimeAtlasResult",
    "FrozenRegimeBin",
    "FrozenRegimeInteraction",
    "RegimeControlRow",
    "RegimeEvidenceRow",
    "RegimeScreeningRow",
    "RegimeStabilityRow",
]
