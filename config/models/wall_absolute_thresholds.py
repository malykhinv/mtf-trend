from dataclasses import dataclass


@dataclass(frozen=True)
class WallAbsoluteThresholds:
    top_usd: int
    alt_usd: int
    listing_usd: int


__all__ = ["WallAbsoluteThresholds"]
