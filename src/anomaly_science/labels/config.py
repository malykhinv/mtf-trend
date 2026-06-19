from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutcomeLabelConfig:
    """Configuration for ATR-normalized future-nature labels.

    These constants are frozen scientific label-schema parameters. They are not
    entry, exit, EV, or PnL thresholds.
    """

    label_schema_version: str = "atr_outcome_labels_v1"
    horizons_minutes: tuple[int, ...] = SUPPORTED_RESEARCH_HORIZONS
    atr_window_minutes: int = 1440
    k_continuation: float = 1.0
    k_fade: float = 1.0
    k_chop: float = 0.25
    trap_policy: str = "map_to_unclear"

    def __post_init__(self) -> None:
        if self.horizons_minutes != SUPPORTED_RESEARCH_HORIZONS:
            raise ValueError(f"ATR outcome labels must use Core research horizons {SUPPORTED_RESEARCH_HORIZONS}")
        if self.atr_window_minutes <= 0:
            raise ValueError("atr_window_minutes must be positive")
        if self.k_continuation <= 0:
            raise ValueError("k_continuation must be positive")
        if self.k_fade <= 0:
            raise ValueError("k_fade must be positive")
        if self.k_chop <= 0:
            raise ValueError("k_chop must be positive")
        if self.k_chop > min(self.k_continuation, self.k_fade):
            raise ValueError("k_chop must not exceed k_continuation or k_fade")
        if not self.label_schema_version:
            raise ValueError("label_schema_version is required")
        if self.trap_policy != "map_to_unclear":
            raise ValueError("MVP Level 6 supports only trap_policy='map_to_unclear'")
