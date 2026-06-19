from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TradeSimulationConfig:
    simulation_version: str = "mvp1_pessimistic_trade_simulation_v1"
    strategy_name: str = "broad_anomaly_v1_h30"
    target_horizon_minutes: int = 30
    require_prediction_confident: bool = True
    require_rr_acceptable: bool = True
    toxic_entry_atr_1m_fraction: float = 0.2
    random_seed: int = 1729

    def __post_init__(self) -> None:
        if not self.simulation_version:
            raise ValueError("simulation_version is required")
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if self.target_horizon_minutes <= 0:
            raise ValueError("target_horizon_minutes must be positive")
        if self.toxic_entry_atr_1m_fraction < 0.0:
            raise ValueError("toxic_entry_atr_1m_fraction must be non-negative")
        if self.random_seed < 0:
            raise ValueError("random_seed must be non-negative")
