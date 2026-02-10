"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Volume:
    """Класс."""
    value: float

    # область Приватные
    def __post_init__(self) -> None:
        if self.value < 0:
            msg = "Volume cannot be negative."
            raise ValueError(msg)
    # конец области Приватные
