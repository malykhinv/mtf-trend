from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from anomaly_science.events.config import BroadAnomalyDetectorConfig
from anomaly_science.strategy.anomaly import BroadAnomalyStrategy
from anomaly_science.strategy.base import BaseStrategy


class StrategyRegistryError(ValueError):
    """Raised when a requested strategy is not registered cleanly."""


@dataclass(frozen=True, slots=True)
class StrategyRegistryEntry:
    strategy_name: str
    strategy_family: str
    strategy_contract_version: str
    factory: Callable[[], BaseStrategy]


def available_strategies() -> tuple[StrategyRegistryEntry, ...]:
    return (
        StrategyRegistryEntry(
            strategy_name="broad_anomaly_v1",
            strategy_family="anomaly",
            strategy_contract_version="base_strategy_v1",
            factory=lambda: BroadAnomalyStrategy(),
        ),
    )


def get_strategy(strategy_name: str) -> BaseStrategy:
    for entry in available_strategies():
        if entry.strategy_name == strategy_name:
            return entry.factory()
    raise StrategyRegistryError(f"unknown strategy_name: {strategy_name!r}")


def get_broad_anomaly_strategy(config: BroadAnomalyDetectorConfig | None = None) -> BroadAnomalyStrategy:
    return BroadAnomalyStrategy(config=config or BroadAnomalyDetectorConfig())
