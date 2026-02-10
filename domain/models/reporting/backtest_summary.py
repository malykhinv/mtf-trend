"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    total_combinations: int
    profitable_combinations: int
    best_pf: float
