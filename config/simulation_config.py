"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from constants import DEFAULT_COMMISSION_RATE, DEFAULT_SLIPPAGE, DEFAULT_SPREAD, DEFAULT_TIMEZONE


@dataclass(slots=True)
class SimulationConfig:
    commission_rate: float = DEFAULT_COMMISSION_RATE
    slippage: float = DEFAULT_SLIPPAGE
    spread: float = DEFAULT_SPREAD
    timezone: str = DEFAULT_TIMEZONE
