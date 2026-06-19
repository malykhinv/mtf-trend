from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.strategy.metadata import active_strategy_h_max_minutes


@dataclass(frozen=True, slots=True)
class ControlsConfig:
    """Configuration for MVP1 placebo/control checks.

    These checks are negative scientific controls for prediction artifacts. They
    intentionally do not define decision thresholds, EV, PnL, entries, exits, or
    trade simulation.
    """

    control_version: str = "mvp1_placebo_controls_v1"
    strategy_name: str = "broad_anomaly_v1_h30"
    target_horizon_minutes: int = 30
    active_strategy_names: tuple[str, ...] = ()
    min_train_rows: int = 3
    min_group_rows: int = 2
    smoothing_strength: float = 5.0
    random_seed: int = 1729

    def __post_init__(self) -> None:
        if not self.control_version:
            raise ValueError("control_version is required")
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if not self.active_strategy_names:
            object.__setattr__(self, "active_strategy_names", (self.strategy_name,))
        if self.strategy_name not in self.active_strategy_names:
            raise ValueError("active_strategy_names must include strategy_name")
        if self.target_horizon_minutes not in (15, 30, 60, 120, 180):
            raise ValueError("target_horizon_minutes must be one of 15, 30, 60, 120, or 180")
        if self.purge_horizon_minutes < self.target_horizon_minutes:
            raise ValueError("active strategy H_max must be >= target_horizon_minutes")
        if self.min_train_rows <= 0:
            raise ValueError("min_train_rows must be positive")
        if self.min_group_rows <= 0:
            raise ValueError("min_group_rows must be positive")
        if self.smoothing_strength <= 0:
            raise ValueError("smoothing_strength must be positive")
        if self.random_seed < 0:
            raise ValueError("random_seed must be non-negative")

    @property
    def purge_horizon_minutes(self) -> int:
        return active_strategy_h_max_minutes(self.active_strategy_names)
