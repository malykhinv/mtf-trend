"""Configuration model for funding kill switch settings."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FundingKillSwitchSettings:
    funding_block_s: int
    max_stops_per_min: int
    max_drawdown_frac: float
    block_min: int


__all__ = ["FundingKillSwitchSettings"]
