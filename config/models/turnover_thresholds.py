"""Configuration model for turnover thresholds."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnoverThresholds:
    top_usd: int
    alt_usd: int
    listing_usd: int
    market_scan_interval_s: int


__all__ = ["TurnoverThresholds"]
