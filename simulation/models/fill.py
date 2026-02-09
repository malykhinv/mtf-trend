"""Модель исполнения ордера."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Fill:
    """Снимок одного исполнения ордера."""

    price: float
    commission: float
