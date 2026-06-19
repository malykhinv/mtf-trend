from __future__ import annotations

import math
from dataclasses import dataclass

from .horizons import is_supported_research_horizon, supported_research_horizon_error_message
from .market import MarketDataContractError
from .time import enforce_snapshot_contract, validate_timestamp_ms

EXPECTED_VALUE_TEMPORAL_CONTRACT = "features<=snapshot_time<label_future_start;oos_probabilities_only;no_trade_simulation"
DECISION_ACTIONS = frozenset(("long", "short", "wait", "no_trade"))


@dataclass(frozen=True, slots=True)
class ExpectedValueRow:
    ev_version: str
    strategy_name: str
    strategy_version: str
    event_id: str
    symbol: str
    state_time_ms: int
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int
    target_horizon_minutes: int
    entry_reference_price: float
    core_atr_1440: float
    stop_distance: float
    target_distance: float
    fee_bps: float
    slippage_bps: float
    cost_penalty: float
    p_follow_through_long: float
    p_adverse_long: float
    p_follow_through_short: float
    p_adverse_short: float
    confidence_calibrated: float
    RR_long_proxy: float
    RR_short_proxy: float
    EV_long: float
    EV_short: float
    EV_wait: float
    EV_no_trade: float
    best_action: str
    is_prediction_confident: bool
    is_RR_still_acceptable: bool
    temporal_contract: str

    def __post_init__(self) -> None:
        if not self.ev_version:
            raise MarketDataContractError("ev_version is required")
        if not self.strategy_name:
            raise MarketDataContractError("strategy_name is required")
        if not self.strategy_version:
            raise MarketDataContractError("strategy_version is required")
        if not self.event_id:
            raise MarketDataContractError("event_id is required")
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        validate_timestamp_ms(self.state_time_ms, field_name="state_time_ms")
        validate_timestamp_ms(self.snapshot_time_ms, field_name="snapshot_time_ms")
        validate_timestamp_ms(self.feature_cutoff_time_ms, field_name="feature_cutoff_time_ms")
        validate_timestamp_ms(self.future_start_time_ms, field_name="future_start_time_ms")
        if self.state_time_ms != self.snapshot_time_ms:
            raise MarketDataContractError("state_time_ms must equal snapshot_time_ms for MVP1 EV rows")
        enforce_snapshot_contract(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
            future_start_time_ms=self.future_start_time_ms,
        )
        if not is_supported_research_horizon(self.target_horizon_minutes):
            raise MarketDataContractError(supported_research_horizon_error_message("target_horizon_minutes"))
        _require_positive_finite(self.entry_reference_price, "entry_reference_price")
        _require_positive_finite(self.core_atr_1440, "core_atr_1440")
        _require_positive_finite(self.stop_distance, "stop_distance")
        _require_positive_finite(self.target_distance, "target_distance")
        if self.fee_bps < 0.0 or self.slippage_bps < 0.0:
            raise MarketDataContractError("fee_bps and slippage_bps must be non-negative")
        if not math.isfinite(self.cost_penalty) or self.cost_penalty < 0.0:
            raise MarketDataContractError("cost_penalty must be finite and non-negative")
        for field_name in (
            "p_follow_through_long",
            "p_adverse_long",
            "p_follow_through_short",
            "p_adverse_short",
            "confidence_calibrated",
        ):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise MarketDataContractError(f"{field_name} must be within [0, 1]")
        for field_name in ("RR_long_proxy", "RR_short_proxy", "EV_long", "EV_short", "EV_wait", "EV_no_trade"):
            if not math.isfinite(getattr(self, field_name)):
                raise MarketDataContractError(f"{field_name} must be finite")
        if self.best_action not in DECISION_ACTIONS:
            raise MarketDataContractError(f"best_action has unknown value: {self.best_action!r}")
        if self.temporal_contract != EXPECTED_VALUE_TEMPORAL_CONTRACT:
            raise MarketDataContractError("temporal_contract must document the EV time boundary")


@dataclass(frozen=True, slots=True)
class ExpectedValueMetricRow:
    ev_version: str
    target_horizon_minutes: int
    metric_name: str
    metric_value: str
    row_count: int
    notes: str

    def __post_init__(self) -> None:
        if not self.ev_version:
            raise MarketDataContractError("ev_version is required")
        if not is_supported_research_horizon(self.target_horizon_minutes):
            raise MarketDataContractError(supported_research_horizon_error_message("target_horizon_minutes"))
        if not self.metric_name:
            raise MarketDataContractError("metric_name is required")
        if self.row_count < 0:
            raise MarketDataContractError("row_count must be non-negative")


def _require_positive_finite(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise MarketDataContractError(f"{field_name} must be positive and finite")
