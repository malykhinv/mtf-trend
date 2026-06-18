from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TradeSimulationConfig:
    simulation_version: str = "mvp1_pessimistic_trade_simulation_v1"
    target_horizon_minutes: int = 30
    require_prediction_confident: bool = True
    require_rr_acceptable: bool = True

    def __post_init__(self) -> None:
        if not self.simulation_version:
            raise ValueError("simulation_version is required")
        if self.target_horizon_minutes <= 0:
            raise ValueError("target_horizon_minutes must be positive")
