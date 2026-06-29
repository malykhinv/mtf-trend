from __future__ import annotations

import math
from dataclasses import dataclass

from .execution import EV_EXECUTION_REFERENCE_MODEL, ROUND_TRIP_COST_MODEL, SIMULATION_ENTRY_PRICE_BASIS
from .horizons import is_supported_research_horizon, supported_research_horizon_error_message
from .market import MarketDataContractError
from .time import validate_timestamp_ms

TRADE_SIMULATION_TEMPORAL_CONTRACT = "features<=snapshot_time<entry_reference_time<=exit_time;structural_policy;pessimistic_stop_first"
BARRIER_OUTCOME_TEMPORAL_CONTRACT = "features<=snapshot_time<entry_reference_time<=barrier_outcome_time;labels_only;not_model_feature"
BARRIER_OUTCOME_VERSION = "mvp1_realized_barrier_outcome_v1"
SIMULATION_EXIT_REASONS = frozenset(
    ("target_hit", "stop_loss", "horizon_close", "partial_target_then_stop", "partial_target_then_horizon")
)
SIMULATION_SIDES = frozenset(("long", "short"))


@dataclass(frozen=True, slots=True)
class TradeSimulationRow:
    simulation_version: str
    strategy_name: str
    strategy_version: str
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    target_horizon_minutes: int
    execution_reference_model: str
    entry_price_basis: str
    decision_action: str
    simulated_side: str
    entry_reference_time_ms: int
    entry_reference_open: float
    entry_price: float
    core_atr_1440: float
    execution_policy_version: str
    stop_policy_id: str
    target_policy_id: str
    stop_anchor: str
    target_anchor: str
    stop_trigger: str
    target_trigger: str
    target_close_fraction: float
    stop_distance: float
    target_distance: float
    stop_price: float
    final_stop_price: float
    target_price: float
    target_was_hit: bool
    target_hit_time_ms: int | None
    fee_bps: float
    slippage_bps: float
    cost_model: str
    total_cost: float
    funding_cost: float
    exit_time_ms: int
    exit_price: float
    exit_reason: str
    gross_pnl: float
    net_pnl: float
    net_pnl_r: float
    net_return: float
    barrier_resolution: str
    temporal_contract: str

    def __post_init__(self) -> None:
        for field_name in ("simulation_version", "strategy_name", "strategy_version", "event_id", "symbol"):
            if not getattr(self, field_name):
                raise MarketDataContractError(f"{field_name} is required")
        for field_name in ("snapshot_time_ms", "feature_cutoff_time_ms", "entry_reference_time_ms", "exit_time_ms"):
            validate_timestamp_ms(getattr(self, field_name), field_name=field_name)
        if self.feature_cutoff_time_ms > self.snapshot_time_ms:
            raise MarketDataContractError("feature_cutoff_time_ms must be <= snapshot_time_ms")
        if self.entry_reference_time_ms <= self.snapshot_time_ms:
            raise MarketDataContractError("entry_reference_time_ms must be > snapshot_time_ms")
        if self.exit_time_ms < self.entry_reference_time_ms:
            raise MarketDataContractError("exit_time_ms must be >= entry_reference_time_ms")
        if not is_supported_research_horizon(self.target_horizon_minutes):
            raise MarketDataContractError(supported_research_horizon_error_message("target_horizon_minutes"))
        if self.execution_reference_model != EV_EXECUTION_REFERENCE_MODEL:
            raise MarketDataContractError("execution_reference_model must match the EV/simulation execution contract")
        if self.entry_price_basis != SIMULATION_ENTRY_PRICE_BASIS:
            raise MarketDataContractError("entry_price_basis must document the simulation entry reference")
        if self.decision_action not in SIMULATION_SIDES:
            raise MarketDataContractError("decision_action must be long or short for simulated trade rows")
        if self.simulated_side not in SIMULATION_SIDES:
            raise MarketDataContractError("simulated_side must be long or short")
        for field_name in (
            "entry_reference_open",
            "entry_price",
            "core_atr_1440",
            "stop_distance",
            "target_distance",
            "stop_price",
            "final_stop_price",
            "target_price",
            "exit_price",
        ):
            _require_positive_finite(getattr(self, field_name), field_name)
        for field_name in (
            "execution_policy_version",
            "stop_policy_id",
            "target_policy_id",
            "stop_anchor",
            "target_anchor",
            "stop_trigger",
            "target_trigger",
        ):
            if not getattr(self, field_name):
                raise MarketDataContractError(f"{field_name} is required")
        if not 0.0 < self.target_close_fraction <= 1.0:
            raise MarketDataContractError("target_close_fraction must be inside (0, 1]")
        if self.target_was_hit != (self.target_hit_time_ms is not None):
            raise MarketDataContractError("target_was_hit and target_hit_time_ms must agree")
        if self.target_hit_time_ms is not None:
            validate_timestamp_ms(self.target_hit_time_ms, field_name="target_hit_time_ms")
            if not self.entry_reference_time_ms <= self.target_hit_time_ms <= self.exit_time_ms:
                raise MarketDataContractError("target_hit_time_ms must be inside the simulated hold")
        if self.fee_bps < 0.0 or self.slippage_bps < 0.0:
            raise MarketDataContractError("fee_bps and slippage_bps must be non-negative")
        if self.cost_model != ROUND_TRIP_COST_MODEL:
            raise MarketDataContractError("cost_model must match the EV/simulation cost contract")
        if self.total_cost < 0.0 or not math.isfinite(self.total_cost):
            raise MarketDataContractError("total_cost must be finite and non-negative")
        if not math.isfinite(self.funding_cost):
            raise MarketDataContractError("funding_cost must be finite")
        for field_name in ("gross_pnl", "net_pnl", "net_pnl_r", "net_return"):
            if not math.isfinite(getattr(self, field_name)):
                raise MarketDataContractError(f"{field_name} must be finite")
        if self.exit_reason not in SIMULATION_EXIT_REASONS:
            raise MarketDataContractError(f"exit_reason has unknown value: {self.exit_reason!r}")
        if self.barrier_resolution not in {
            "single_barrier",
            "stop_loss_first",
            "horizon_close",
            "partial_then_single_barrier",
            "partial_then_horizon_close",
        }:
            raise MarketDataContractError(f"barrier_resolution has unknown value: {self.barrier_resolution!r}")
        if self.temporal_contract != TRADE_SIMULATION_TEMPORAL_CONTRACT:
            raise MarketDataContractError("temporal_contract must document pessimistic simulation timing")


@dataclass(frozen=True, slots=True)
class BarrierOutcomeRow:
    barrier_outcome_version: str
    strategy_name: str
    strategy_version: str
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    target_horizon_minutes: int
    execution_reference_model: str
    entry_price_basis: str
    side: str
    entry_reference_time_ms: int
    entry_reference_price: float
    stop_reference_price: float
    target_reference_price: float
    stop_distance: float
    target_distance: float
    stop_policy_id: str
    target_policy_id: str
    stop_trigger: str
    target_trigger: str
    target_hit: bool
    stop_hit: bool
    timeout: bool
    first_resolution: str
    first_hit_time_ms: int | None
    target_hit_time_ms: int | None
    stop_hit_time_ms: int | None
    intracandle_collision: bool
    barrier_resolution: str
    net_pnl_before_model: float
    temporal_contract: str

    def __post_init__(self) -> None:
        if self.barrier_outcome_version != BARRIER_OUTCOME_VERSION:
            raise MarketDataContractError("barrier_outcome_version must match the registered contract")
        for field_name in ("strategy_name", "strategy_version", "event_id", "symbol"):
            if not getattr(self, field_name):
                raise MarketDataContractError(f"{field_name} is required")
        for field_name in ("snapshot_time_ms", "feature_cutoff_time_ms", "entry_reference_time_ms"):
            validate_timestamp_ms(getattr(self, field_name), field_name=field_name)
        if self.feature_cutoff_time_ms > self.snapshot_time_ms:
            raise MarketDataContractError("feature_cutoff_time_ms must be <= snapshot_time_ms")
        if self.entry_reference_time_ms <= self.snapshot_time_ms:
            raise MarketDataContractError("entry_reference_time_ms must be > snapshot_time_ms")
        if not is_supported_research_horizon(self.target_horizon_minutes):
            raise MarketDataContractError(supported_research_horizon_error_message("target_horizon_minutes"))
        if self.execution_reference_model != EV_EXECUTION_REFERENCE_MODEL:
            raise MarketDataContractError("execution_reference_model must match the EV/simulation execution contract")
        if self.entry_price_basis != SIMULATION_ENTRY_PRICE_BASIS:
            raise MarketDataContractError("entry_price_basis must document next-open barrier-outcome entry")
        if self.side not in SIMULATION_SIDES:
            raise MarketDataContractError("side must be long or short")
        for field_name in (
            "entry_reference_price",
            "stop_reference_price",
            "target_reference_price",
            "stop_distance",
            "target_distance",
        ):
            _require_positive_finite(getattr(self, field_name), field_name)
        for field_name in ("stop_policy_id", "target_policy_id", "stop_trigger", "target_trigger"):
            if not getattr(self, field_name):
                raise MarketDataContractError(f"{field_name} is required")
        allowed_resolutions = {"target_first", "stop_loss_first", "horizon_close"}
        if self.first_resolution not in allowed_resolutions:
            raise MarketDataContractError(f"first_resolution has unknown value: {self.first_resolution!r}")
        if self.barrier_resolution != self.first_resolution:
            raise MarketDataContractError("barrier_resolution must equal first_resolution for static realized barrier outcomes")
        if self.timeout != (not self.target_hit and not self.stop_hit):
            raise MarketDataContractError("timeout must mean neither target nor stop was hit")
        if self.intracandle_collision and not (self.target_hit and self.stop_hit):
            raise MarketDataContractError("intracandle_collision requires both target_hit and stop_hit")
        if self.first_resolution == "horizon_close" and not self.timeout:
            raise MarketDataContractError("horizon_close outcome must be a timeout")
        if self.first_resolution == "target_first" and not self.target_hit:
            raise MarketDataContractError("target_first outcome requires target_hit")
        if self.first_resolution == "stop_loss_first" and not self.stop_hit:
            raise MarketDataContractError("stop_loss_first outcome requires stop_hit")
        for field_name in ("first_hit_time_ms", "target_hit_time_ms", "stop_hit_time_ms"):
            value = getattr(self, field_name)
            if value is not None:
                validate_timestamp_ms(value, field_name=field_name)
                if value < self.entry_reference_time_ms:
                    raise MarketDataContractError(f"{field_name} must be >= entry_reference_time_ms")
        if self.target_hit != (self.target_hit_time_ms is not None):
            raise MarketDataContractError("target_hit and target_hit_time_ms must agree")
        if self.stop_hit != (self.stop_hit_time_ms is not None):
            raise MarketDataContractError("stop_hit and stop_hit_time_ms must agree")
        if self.first_resolution == "target_first" and self.first_hit_time_ms != self.target_hit_time_ms:
            raise MarketDataContractError("first_hit_time_ms must equal target_hit_time_ms for target_first")
        if self.first_resolution == "stop_loss_first" and self.first_hit_time_ms != self.stop_hit_time_ms:
            raise MarketDataContractError("first_hit_time_ms must equal stop_hit_time_ms for stop_loss_first")
        if self.first_resolution == "horizon_close" and self.first_hit_time_ms is not None:
            raise MarketDataContractError("horizon_close must not have first_hit_time_ms")
        if not math.isfinite(self.net_pnl_before_model):
            raise MarketDataContractError("net_pnl_before_model must be finite")
        if self.temporal_contract != BARRIER_OUTCOME_TEMPORAL_CONTRACT:
            raise MarketDataContractError("temporal_contract must document the realized barrier label boundary")


@dataclass(frozen=True, slots=True)
class TradeSimulationMetricRow:
    simulation_version: str
    target_horizon_minutes: int
    execution_variant_id: str
    stop_policy_id: str
    target_policy_id: str
    target_close_fraction: float | None
    metric_name: str
    metric_value: str
    row_count: int
    notes: str

    def __post_init__(self) -> None:
        if not self.simulation_version:
            raise MarketDataContractError("simulation_version is required")
        if not is_supported_research_horizon(self.target_horizon_minutes):
            raise MarketDataContractError(supported_research_horizon_error_message("target_horizon_minutes"))
        if not self.metric_name:
            raise MarketDataContractError("metric_name is required")
        if not self.execution_variant_id:
            raise MarketDataContractError("execution_variant_id is required")
        if self.execution_variant_id != "all_declared_variants":
            if not self.stop_policy_id or not self.target_policy_id:
                raise MarketDataContractError("scoped simulation metrics require structural policy ids")
            if self.target_close_fraction is None or not 0.0 < self.target_close_fraction <= 1.0:
                raise MarketDataContractError("scoped simulation metrics require target_close_fraction inside (0, 1]")
        if self.row_count < 0:
            raise MarketDataContractError("row_count must be non-negative")


def _require_positive_finite(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise MarketDataContractError(f"{field_name} must be positive and finite")
