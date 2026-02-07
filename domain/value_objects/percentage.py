"""Percentage value object."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Percentage:
    """Represents percentage value in range [-100.0, 100.0]."""

    value: float

    def __post_init__(self) -> None:
        if self.value < -100.0 or self.value > 100.0:
            msg = "Percentage must be between -100.0 and 100.0."
            raise ValueError(msg)
