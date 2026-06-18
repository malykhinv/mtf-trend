from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from anomaly_science.events.config import BroadAnomalyDetectorConfig
from anomaly_science.strategy.anomaly import ANOMALY_STRATEGY_DEFAULTS, BroadAnomalyStrategy, make_broad_anomaly_strategy
from anomaly_science.strategy.base import BaseStrategy


class StrategyRegistryError(ValueError):
    """Raised when a requested strategy is not registered cleanly."""


@dataclass(frozen=True, slots=True)
class StrategyRegistryEntry:
    strategy_name: str
    strategy_family: str
    strategy_contract_version: str
    factory: Callable[[], BaseStrategy]


BROAD_ANOMALY_VARIANTS: tuple[str, ...] = (
    "broad_anomaly_v1_h15",
    "broad_anomaly_v1_h30",
    "broad_anomaly_v1_h60",
)


def available_strategies() -> tuple[StrategyRegistryEntry, ...]:
    return tuple(
        StrategyRegistryEntry(
            strategy_name=strategy_name,
            strategy_family="anomaly",
            strategy_contract_version="base_strategy_v1",
            factory=lambda strategy_name=strategy_name: make_broad_anomaly_strategy(strategy_name=strategy_name),
        )
        for strategy_name in BROAD_ANOMALY_VARIANTS
    )


def get_strategy(strategy_name: str) -> BaseStrategy:
    for entry in available_strategies():
        if entry.strategy_name == strategy_name:
            return entry.factory()
    if strategy_name in ANOMALY_STRATEGY_DEFAULTS:
        raise StrategyRegistryError(f"strategy variant is specified but not implemented yet: {strategy_name!r}")
    raise StrategyRegistryError(f"unknown strategy_name: {strategy_name!r}")


def get_broad_anomaly_strategy(
    config: BroadAnomalyDetectorConfig | None = None,
    *,
    strategy_name: str = "broad_anomaly_v1_h30",
) -> BroadAnomalyStrategy:
    return make_broad_anomaly_strategy(strategy_name=strategy_name, config=config)
