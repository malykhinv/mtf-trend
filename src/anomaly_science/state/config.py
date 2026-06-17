from __future__ import annotations

from dataclasses import dataclass


STATE_BUILDER_VERSION = "online_1m_state_builder_v1"


@dataclass(frozen=True, slots=True)
class OnlineStateBuilderConfig:
    """Configuration for MVP1 online 1m anomaly state construction.

    The horizon only bounds artifact size. It is not a trade holding period,
    label horizon, exit rule, or PnL assumption.
    """

    max_state_minutes_after_detection: int = 120
    state_builder_version: str = STATE_BUILDER_VERSION

    def __post_init__(self) -> None:
        if self.max_state_minutes_after_detection < 0:
            raise ValueError("max_state_minutes_after_detection must be non-negative")
        if not self.state_builder_version:
            raise ValueError("state_builder_version is required")
