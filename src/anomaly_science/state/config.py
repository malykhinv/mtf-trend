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
    # Lower bound of the emitted online-state window. Rows with
    # minutes_since_detection < this are still used to accumulate running/structural
    # state but are not emitted. Set min == max to materialize a single anchor offset
    # (one row per event) for a memory-bounded supervised gate at that offset.
    min_state_minutes_after_detection: int = 0
    state_builder_version: str = STATE_BUILDER_VERSION

    def __post_init__(self) -> None:
        if self.max_state_minutes_after_detection < 0:
            raise ValueError("max_state_minutes_after_detection must be non-negative")
        if self.min_state_minutes_after_detection < 0:
            raise ValueError("min_state_minutes_after_detection must be non-negative")
        if self.min_state_minutes_after_detection > self.max_state_minutes_after_detection:
            raise ValueError("min_state_minutes_after_detection must be <= max_state_minutes_after_detection")
        if not self.state_builder_version:
            raise ValueError("state_builder_version is required")
