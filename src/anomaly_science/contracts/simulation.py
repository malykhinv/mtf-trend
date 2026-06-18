from __future__ import annotations

import math
from dataclasses import dataclass

from .market import MarketDataContractError
from .time import validate_timestamp_ms

TRADE_SIMULATION_TEMPORAL_CONTRACT = "features<=snapshot_time<entry_reference_time<=exit_time;pessimistic_stop_first"
SIMULATION_EXIT_REASONS = frozenset(("target_hit", "stop_loss", "horizon_close"))
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
    decision_action: str
    simulated_side: str
    entry_reference_time_ms: int
    entry_reference_open: float
    entry_price: float
    ATR_1d_asof_t: float
    stop_distance: float
    target_distance: float
    stop_price: float
    target_price: float
    fee_bps: float
    slippage_bps: float
    total_cost: float
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
        if self.target_horizon_minutes <= 0:
            raise MarketDataContractError("target_horizon_minutes must be positive")
        if self.decision_action not in SIMULATION_SIDES:
            raise MarketDataContractError("decision_action must be long or short for simulated trade rows")
        if self.simulated_side not in SIMULATION_SIDES:
            raise MarketDataContractError("simulated_side must be long or short")
        for field_name in (
            "entry_reference_open",
            "entry_price",
            "ATR_1d_asof_t",
            "stop_distance",
            "target_distance",
            "stop_price",
            "target_price",
            "exit_price",
        ):
            _require_positive_finite(getattr(self, field_name), field_name)
        if self.fee_bps < 0.0 or self.slippage_bps < 0.0:
            raise MarketDataContractError("fee_bps and slippage_bps must be non-negative")
        if self.total_cost < 0.0 or not math.isfinite(self.total_cost):
            raise MarketDataContractError("total_cost must be finite and non-negative")
        for field_name in ("gross_pnl", "net_pnl", "net_pnl_r", "net_return"):
            if not math.isfinite(getattr(self, field_name)):
                raise MarketDataContractError(f"{field_name} must be finite")
        if self.exit_reason not in SIMULATION_EXIT_REASONS:
            raise MarketDataContractError(f"exit_reason has unknown value: {self.exit_reason!r}")
        if self.barrier_resolution not in {"single_barrier", "stop_loss_first", "horizon_close"}:
            raise MarketDataContractError(f"barrier_resolution has unknown value: {self.barrier_resolution!r}")
        if self.temporal_contract != TRADE_SIMULATION_TEMPORAL_CONTRACT:
            raise MarketDataContractError("temporal_contract must document pessimistic simulation timing")


@dataclass(frozen=True, slots=True)
class TradeSimulationMetricRow:
    simulation_version: str
    target_horizon_minutes: int
    metric_name: str
    metric_value: str
    row_count: int
    notes: str

    def __post_init__(self) -> None:
        if not self.simulation_version:
            raise MarketDataContractError("simulation_version is required")
        if self.target_horizon_minutes <= 0:
            raise MarketDataContractError("target_horizon_minutes must be positive")
        if not self.metric_name:
            raise MarketDataContractError("metric_name is required")
        if self.row_count < 0:
            raise MarketDataContractError("row_count must be non-negative")


def _require_positive_finite(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise MarketDataContractError(f"{field_name} must be positive and finite")
