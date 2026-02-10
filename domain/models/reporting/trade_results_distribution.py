"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TradeResultsDistribution:
    SL: int
    BE: int
    TP1_BE: int
    TP2: int
