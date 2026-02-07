"""Order fill model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Fill:
    """Single order fill snapshot."""

    price: float
    commission: float
