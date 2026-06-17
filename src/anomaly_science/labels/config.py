from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutcomeLabelConfig:
    """Configuration for MVP1 descriptive future-nature labels.

    Thresholds are research-protocol constants for coarse scenario description.
    They are not entry, exit, EV, or PnL thresholds.
    """

    label_policy_version: str = "mvp1_outcome_labels_v1"
    horizons_minutes: tuple[int, ...] = (15, 30, 60)
    continuation_move_threshold: float = 0.015
    fade_move_threshold: float = 0.015
    chop_return_threshold: float = 0.005

    def __post_init__(self) -> None:
        if self.horizons_minutes != (15, 30, 60):
            raise ValueError("MVP1 outcome labels support exactly 15m, 30m, and 60m horizons")
        if self.continuation_move_threshold <= 0:
            raise ValueError("continuation_move_threshold must be positive")
        if self.fade_move_threshold <= 0:
            raise ValueError("fade_move_threshold must be positive")
        if self.chop_return_threshold <= 0:
            raise ValueError("chop_return_threshold must be positive")
