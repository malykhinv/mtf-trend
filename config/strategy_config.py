"""Anomaly runtime strategy configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class StrategyConfig:
    strategy_id: str = "anomaly"
