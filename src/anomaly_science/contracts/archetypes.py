from __future__ import annotations

import math
from dataclasses import dataclass

from .market import MarketDataContractError


@dataclass(frozen=True, slots=True)
class ArchetypeCategoryRow:
    category_id: str
    status: str
    evidence_mode: str
    protocol_freeze_id: str
    tree_index: int
    leaf_index: int
    generator_seed: int
    generator_depth: int
    origin_fraction: float
    origin_support_count: int
    origin_support_fraction: float
    generator_support_count: int
    generator_support_fraction: float
    rule_text: str
    rule_json: str
    feature_names: str
    discovery_event_count: int
    discovery_fade_count: int
    discovery_matched_event_count: int
    discovery_unique_event_fraction: float
    discovery_inference_block_count: int
    discovery_fade_rate: float
    discovery_global_blind_rate: float
    discovery_matched_blind_rate: float
    discovery_lift: float
    discovery_cluster_lower_95: float
    discovery_cluster_edge_lower_95: float
    verification_event_count: int
    verification_fade_count: int
    verification_matched_event_count: int
    verification_inference_block_count: int
    verification_fade_rate: float
    verification_global_blind_rate: float
    verification_matched_blind_rate: float
    verification_lift: float
    verification_cluster_lower_95: float
    verification_cluster_edge_lower_95: float
    verification_p_value: float
    verification_q_value: float
    verification_stability_periods: int
    verification_positive_period_fraction: float
    first_signal_policy: str
    temporal_contract: str

    def __post_init__(self) -> None:
        if not self.category_id or not self.rule_text or not self.rule_json:
            raise MarketDataContractError("archetype category identity and rule are required")
        if self.status not in {
            "PRISTINE_VERIFIED",
            "DEVELOPMENT_REPLICATED",
            "CONTROL_FAILED",
            "REJECTED",
        }:
            raise MarketDataContractError("unknown archetype evidence status")
        if self.evidence_mode not in {"development", "pristine_holdout"}:
            raise MarketDataContractError("unknown archetype evidence_mode")
        if self.status == "PRISTINE_VERIFIED" and not self.protocol_freeze_id:
            raise MarketDataContractError("PRISTINE_VERIFIED requires protocol_freeze_id")
        if self.tree_index < 0 or self.leaf_index < 0 or self.generator_seed < 0:
            raise MarketDataContractError("tree_index and leaf_index must be non-negative")
        if not 1 <= self.generator_depth <= 8:
            raise MarketDataContractError("generator_depth must be within [1, 8]")
        if not 0.0 < self.origin_fraction <= 1.0:
            raise MarketDataContractError("origin_fraction must be within (0, 1]")
        for count_name in (
            "discovery_event_count",
            "discovery_fade_count",
            "discovery_matched_event_count",
            "discovery_inference_block_count",
            "verification_event_count",
            "verification_fade_count",
            "verification_matched_event_count",
            "verification_inference_block_count",
            "verification_stability_periods",
            "origin_support_count",
            "generator_support_count",
        ):
            if getattr(self, count_name) < 0:
                raise MarketDataContractError(f"{count_name} must be non-negative")
        for probability_name in (
            "discovery_fade_rate",
            "discovery_unique_event_fraction",
            "discovery_global_blind_rate",
            "discovery_matched_blind_rate",
            "discovery_cluster_lower_95",
            "verification_fade_rate",
            "verification_global_blind_rate",
            "verification_matched_blind_rate",
            "verification_cluster_lower_95",
            "verification_p_value",
            "verification_q_value",
            "verification_positive_period_fraction",
            "origin_support_fraction",
            "generator_support_fraction",
        ):
            value = getattr(self, probability_name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{probability_name} must be finite and within [0, 1]")
        for edge_name in (
            "discovery_cluster_edge_lower_95",
            "verification_cluster_edge_lower_95",
        ):
            value = getattr(self, edge_name)
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise MarketDataContractError(f"{edge_name} must be finite and within [-1, 1]")
        if self.discovery_lift < 0.0 or self.verification_lift < 0.0:
            raise MarketDataContractError("archetype lifts must be non-negative")
        if self.first_signal_policy != "first_matching_snapshot_per_group":
            raise MarketDataContractError("unknown archetype first-signal policy")
        if not self.temporal_contract:
            raise MarketDataContractError("archetype temporal_contract is required")


@dataclass(frozen=True, slots=True)
class ArchetypeThresholdStabilityRow:
    category_id: str
    status: str
    split: str
    condition_index: int
    feature: str
    operator: str
    original_threshold: float
    variant: str
    variant_threshold: float
    threshold_source: str
    event_count: int
    fade_count: int
    matched_event_count: int
    inference_block_count: int
    fade_rate: float
    matched_blind_rate: float
    lift: float
    cluster_edge_lower_95: float
    support_jaccard_vs_original: float
    passes_minimum_effect_gates: bool
    notes: str

    def __post_init__(self) -> None:
        if not self.category_id or not self.status or not self.split:
            raise MarketDataContractError("threshold-stability identity fields are required")
        if self.status not in {
            "PRISTINE_VERIFIED",
            "DEVELOPMENT_REPLICATED",
            "CONTROL_FAILED",
            "REJECTED",
        }:
            raise MarketDataContractError("unknown threshold-stability category status")
        if self.split not in {"discovery", "verification"}:
            raise MarketDataContractError("threshold-stability split must be discovery or verification")
        if self.condition_index < 0:
            raise MarketDataContractError("condition_index must be non-negative")
        if not self.feature or self.operator not in {">", "<="}:
            raise MarketDataContractError("threshold-stability feature/operator are invalid")
        for name in ("original_threshold", "variant_threshold"):
            if not math.isfinite(getattr(self, name)):
                raise MarketDataContractError(f"{name} must be finite")
        if not self.variant or not self.threshold_source or not self.notes:
            raise MarketDataContractError("threshold-stability variant/source/notes are required")
        for name in ("event_count", "fade_count", "matched_event_count", "inference_block_count"):
            if getattr(self, name) < 0:
                raise MarketDataContractError(f"{name} must be non-negative")
        for name in (
            "fade_rate",
            "matched_blind_rate",
            "support_jaccard_vs_original",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{name} must be finite and within [0, 1]")
        if self.lift < 0.0 or not math.isfinite(self.lift):
            raise MarketDataContractError("lift must be finite and non-negative")
        if not math.isfinite(self.cluster_edge_lower_95) or not -1.0 <= self.cluster_edge_lower_95 <= 1.0:
            raise MarketDataContractError("cluster_edge_lower_95 must be finite and within [-1, 1]")


@dataclass(frozen=True, slots=True)
class ArchetypeCoverageRow:
    split: str
    total_event_count: int
    first_signal_fade_event_count: int
    covered_event_count: int
    covered_first_signal_fade_event_count: int
    overlapping_event_count: int
    unclassified_event_count: int
    covered_fraction: float
    covered_first_signal_fade_fraction: float
    unclassified_first_signal_fade_rate: float
    accepted_category_count: int
    generated_candidate_count: int
    distinct_candidate_count: int
    registered_generator_count: int
    rolling_origin_count: int
    search_truncated: bool
    controls_passed: bool
    claim_scope: str

    def __post_init__(self) -> None:
        if self.split not in {"discovery", "verification"}:
            raise MarketDataContractError("coverage split must be discovery or verification")
        for name in (
            "total_event_count",
            "first_signal_fade_event_count",
            "covered_event_count",
            "covered_first_signal_fade_event_count",
            "overlapping_event_count",
            "unclassified_event_count",
            "accepted_category_count",
            "generated_candidate_count",
            "distinct_candidate_count",
            "registered_generator_count",
            "rolling_origin_count",
        ):
            if getattr(self, name) < 0:
                raise MarketDataContractError(f"{name} must be non-negative")
        for name in (
            "covered_fraction",
            "covered_first_signal_fade_fraction",
            "unclassified_first_signal_fade_rate",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{name} must be finite and within [0, 1]")
        if not self.claim_scope:
            raise MarketDataContractError("coverage claim_scope is required")


@dataclass(frozen=True, slots=True)
class ArchetypeCandidateFunnelRow:
    stage: str
    candidate_count: int
    notes: str

    def __post_init__(self) -> None:
        if not self.stage or not self.notes:
            raise MarketDataContractError("candidate funnel stage and notes are required")
        if self.candidate_count < 0:
            raise MarketDataContractError("candidate_count must be non-negative")


@dataclass(frozen=True, slots=True)
class ArchetypeControlRow:
    control_name: str
    random_seed: int
    discovery_event_count: int
    verification_event_count: int
    discovery_blind_rate: float
    verification_blind_rate: float
    verification_auc: float
    discovery_candidate_count: int
    distinct_candidate_count: int
    verified_category_count: int
    shuffled_row_fraction: float
    notes: str

    def __post_init__(self) -> None:
        if self.control_name not in {"real_labels", "blind", "calendar_block_shuffled_labels"}:
            raise MarketDataContractError("unknown archetype control_name")
        if self.random_seed < 0:
            raise MarketDataContractError("random_seed must be non-negative")
        for name in (
            "discovery_event_count",
            "verification_event_count",
            "discovery_candidate_count",
            "distinct_candidate_count",
            "verified_category_count",
        ):
            if getattr(self, name) < 0:
                raise MarketDataContractError(f"{name} must be non-negative")
        for name in (
            "discovery_blind_rate",
            "verification_blind_rate",
            "verification_auc",
            "shuffled_row_fraction",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{name} must be finite and within [0, 1]")
        if not self.notes:
            raise MarketDataContractError("archetype control notes are required")
