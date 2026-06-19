from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.contracts.execution import EV_EXECUTION_REFERENCE_MODEL, ROUND_TRIP_COST_MODEL, SIMULATION_ENTRY_PRICE_BASIS


@dataclass(frozen=True, slots=True)
class TradeSimulationConfig:
    simulation_version: str = "mvp1_pessimistic_trade_simulation_v1"
    strategy_name: str = "broad_anomaly_v1_h30"
    target_horizon_minutes: int = 30
    require_prediction_confident: bool = True
    require_rr_acceptable: bool = True
    execution_reference_model: str = EV_EXECUTION_REFERENCE_MODEL
    entry_price_basis: str = SIMULATION_ENTRY_PRICE_BASIS
    cost_model: str = ROUND_TRIP_COST_MODEL
    toxic_entry_atr_1m_fraction: float = 0.2
    random_seed: int = 1729

    def __post_init__(self) -> None:
        if not self.simulation_version:
            raise ValueError("simulation_version is required")
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        from anomaly_science.strategy.registry import validate_strategy_horizon
        validate_strategy_horizon(self.strategy_name, self.target_horizon_minutes)
        if self.execution_reference_model != EV_EXECUTION_REFERENCE_MODEL:
            raise ValueError("execution_reference_model must match EV_EXECUTION_REFERENCE_MODEL")
        if self.entry_price_basis != SIMULATION_ENTRY_PRICE_BASIS:
            raise ValueError("entry_price_basis must match SIMULATION_ENTRY_PRICE_BASIS")
        if self.cost_model != ROUND_TRIP_COST_MODEL:
            raise ValueError("cost_model must match ROUND_TRIP_COST_MODEL")
        if self.toxic_entry_atr_1m_fraction < 0.0:
            raise ValueError("toxic_entry_atr_1m_fraction must be non-negative")
        if self.random_seed < 0:
            raise ValueError("random_seed must be non-negative")
