from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileWeights:
    top: float
    alt: float
    listing: float
    auto: float


__all__ = ["ProfileWeights"]
