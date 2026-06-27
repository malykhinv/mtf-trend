from __future__ import annotations

import math
from dataclasses import dataclass

from .market import MarketDataContractError


@dataclass(frozen=True, slots=True)
class ArchetypeCategoryRow:
    category_id: str
    status: str
    tree_index: int
    leaf_index: int
    rule_text: str
    rule_json: str
    feature_names: str
    discovery_event_count: int
    discovery_fade_count: int
    discovery_fade_rate: float
    discovery_blind_rate: float
    discovery_lift: float
    discovery_wilson_lower_95: float
    verification_event_count: int
    verification_fade_count: int
    verification_fade_rate: float
    verification_blind_rate: float
    verification_lift: float
    verification_wilson_lower_95: float
    verification_p_value: float
    verification_q_value: float
    verification_stability_periods: int
    verification_positive_period_fraction: float
    first_signal_policy: str
    temporal_contract: str

    def __post_init__(self) -> None:
        if not self.category_id or not self.rule_text or not self.rule_json:
            raise MarketDataContractError("archetype category identity and rule are required")
        if self.status not in {"VERIFIED", "REJECTED"}:
            raise MarketDataContractError("archetype category status must be VERIFIED or REJECTED")
        if self.tree_index < 0 or self.leaf_index < 0:
            raise MarketDataContractError("tree_index and leaf_index must be non-negative")
        for count_name in (
            "discovery_event_count",
            "discovery_fade_count",
            "verification_event_count",
            "verification_fade_count",
            "verification_stability_periods",
        ):
            if getattr(self, count_name) < 0:
                raise MarketDataContractError(f"{count_name} must be non-negative")
        for probability_name in (
            "discovery_fade_rate",
            "discovery_blind_rate",
            "discovery_wilson_lower_95",
            "verification_fade_rate",
            "verification_blind_rate",
            "verification_wilson_lower_95",
            "verification_p_value",
            "verification_q_value",
            "verification_positive_period_fraction",
        ):
            value = getattr(self, probability_name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{probability_name} must be finite and within [0, 1]")
        if self.discovery_lift < 0.0 or self.verification_lift < 0.0:
            raise MarketDataContractError("archetype lifts must be non-negative")
        if self.first_signal_policy != "first_matching_snapshot_per_group":
            raise MarketDataContractError("unknown archetype first-signal policy")
        if not self.temporal_contract:
            raise MarketDataContractError("archetype temporal_contract is required")


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
    shuffled_group_fraction: float
    notes: str

    def __post_init__(self) -> None:
        if self.control_name not in {"real_labels", "blind", "group_shuffled_labels"}:
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
            "shuffled_group_fraction",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{name} must be finite and within [0, 1]")
        if not self.notes:
            raise MarketDataContractError("archetype control notes are required")
