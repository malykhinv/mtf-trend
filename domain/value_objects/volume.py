"""Volume value object."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Volume:
    """Represents a non-negative trade or market volume."""

    value: float

    # область Приватные
    def __post_init__(self) -> None:
        if self.value < 0:
            msg = "Volume cannot be negative."
            raise ValueError(msg)
    # конец области Приватные
