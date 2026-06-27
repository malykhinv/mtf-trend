from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PumpFadeDecisionConfig:
    baseline_window_minutes: int = 1440
    baseline_min_periods: int = 1440
    activity_ignition_multiple: float = 10.0
    quiet_peak_fraction: float = 0.25
    quiet_confirmation_minutes: int = 5
    maximum_period_minutes: int = 60
    minimum_pump_size: float = 0.05
    minimum_atr_multiple: float = 3.0
    minimum_turnover: float = 300_000.0
    candle_interval_ms: int = 60_000

    def __post_init__(self) -> None:
        for name in (
            "baseline_window_minutes",
            "baseline_min_periods",
            "quiet_confirmation_minutes",
            "maximum_period_minutes",
            "candle_interval_ms",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.baseline_min_periods > self.baseline_window_minutes:
            raise ValueError("baseline_min_periods cannot exceed baseline_window_minutes")
        for name in (
            "activity_ignition_multiple",
            "quiet_peak_fraction",
            "minimum_pump_size",
            "minimum_atr_multiple",
            "minimum_turnover",
        ):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be positive")
        if self.quiet_peak_fraction >= 1.0:
            raise ValueError("quiet_peak_fraction must be below one")
