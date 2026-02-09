"""Price value object."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Price:
    """Represents a non-negative market price."""

    value: float

    # область Приватные
    def __post_init__(self) -> None:
        if self.value < 0:
            msg = "Price cannot be negative."
            raise ValueError(msg)
    # конец области Приватные
