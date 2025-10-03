"""Configuration model for ODR settings."""
from dataclasses import dataclass


@dataclass(frozen=True)
class OdrSettings:
    odr_in_short: float
    odr_in_long: float
    odr_neutral_low: float
    odr_neutral_high: float
    odr_neutral_hold_ms: int
    focus_pre_odr_short: float
    focus_pre_odr_long: float


__all__ = ["OdrSettings"]
