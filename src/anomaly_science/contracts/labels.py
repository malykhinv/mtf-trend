from __future__ import annotations

import math
from dataclasses import dataclass

from .market import MarketDataContractError
from .time import enforce_snapshot_contract, validate_timestamp_ms

MISSING_FUTURE_SCENARIO = "missing_future"
PREDICTABLE_OUTCOME_SCENARIOS = (
    "long_continuation",
    "short_fade",
    "static_or_chop",
    "unclear",
)
VALID_OUTCOME_SCENARIOS = frozenset((*PREDICTABLE_OUTCOME_SCENARIOS, MISSING_FUTURE_SCENARIO))
TEMPORAL_LABEL_CONTRACT = "feature_cutoff_time_ms<=snapshot_time_ms<future_start_time_ms"
ATR_LABEL_SOURCE = "atr_normalized_future_paths_only"


@dataclass(frozen=True, slots=True)
class AnomalyOutcomeLabelRow:
    label_schema_version: str
    atr_window_minutes: int
    ATR_1d_asof_t: float | None
    k_continuation: float
    k_fade: float
    k_chop: float
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int
    scenario_15m: str
    scenario_30m: str
    scenario_60m: str
    scenario_120m: str
    label_available_15m: bool
    label_available_30m: bool
    label_available_60m: bool
    label_available_120m: bool
    label_source: str
    temporal_contract: str

    def __post_init__(self) -> None:
        if not self.label_schema_version:
            raise MarketDataContractError("label_schema_version is required")
        if self.atr_window_minutes <= 0:
            raise MarketDataContractError("atr_window_minutes must be positive")
        if self.ATR_1d_asof_t is not None and (not math.isfinite(self.ATR_1d_asof_t) or self.ATR_1d_asof_t <= 0):
            raise MarketDataContractError("ATR_1d_asof_t must be positive and finite when present")
        for field_name in ("k_continuation", "k_fade", "k_chop"):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value <= 0:
                raise MarketDataContractError(f"{field_name} must be positive and finite")
        if self.k_chop > min(self.k_continuation, self.k_fade):
            raise MarketDataContractError("k_chop must not exceed k_continuation or k_fade")
        if not self.event_id:
            raise MarketDataContractError("event_id is required")
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        validate_timestamp_ms(self.snapshot_time_ms, field_name="snapshot_time_ms")
        validate_timestamp_ms(self.feature_cutoff_time_ms, field_name="feature_cutoff_time_ms")
        validate_timestamp_ms(self.future_start_time_ms, field_name="future_start_time_ms")
        enforce_snapshot_contract(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
            future_start_time_ms=self.future_start_time_ms,
        )
        for field_name in ("scenario_15m", "scenario_30m", "scenario_60m", "scenario_120m"):
            value = getattr(self, field_name)
            if value not in VALID_OUTCOME_SCENARIOS:
                raise MarketDataContractError(f"{field_name} has unknown scenario value: {value!r}")
        _check_label_available(self.scenario_15m, self.label_available_15m, "label_available_15m")
        _check_label_available(self.scenario_30m, self.label_available_30m, "label_available_30m")
        _check_label_available(self.scenario_60m, self.label_available_60m, "label_available_60m")
        _check_label_available(self.scenario_120m, self.label_available_120m, "label_available_120m")
        if self.label_source != ATR_LABEL_SOURCE:
            raise MarketDataContractError(f"label_source must be {ATR_LABEL_SOURCE}")
        if self.temporal_contract != TEMPORAL_LABEL_CONTRACT:
            raise MarketDataContractError("temporal_contract must document the MVP1 label time boundary")


def _check_label_available(scenario: str, available: bool, field_name: str) -> None:
    expected = scenario != MISSING_FUTURE_SCENARIO
    if available != expected:
        raise MarketDataContractError(f"{field_name} must be false only for missing_future")
