"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class Percentage:
    """Класс."""
    value: float

    # region Приватные
    def __post_init__(self) -> None:
        if not isfinite(self.value):
            msg = "Percentage must be a finite number."
            raise ValueError(msg)

        if self.value < -100.0:
            msg = "Percentage must be greater than or equal to -100.0."
            raise ValueError(msg)
    # endregion Приватные
