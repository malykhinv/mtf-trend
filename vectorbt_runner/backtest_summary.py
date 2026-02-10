"""Модуль проекта."""

from dataclasses import dataclass


@dataclass(slots=True)
class BacktestSummary:
    total_combinations: int
    profitable_combinations: int
    best_pf: float
