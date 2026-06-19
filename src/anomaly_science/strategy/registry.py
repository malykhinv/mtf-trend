from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from anomaly_science.contracts.horizons import validate_supported_research_horizon
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


@dataclass(frozen=True, slots=True)
class StrategyImplementationStatus:
    strategy_name: str
    strategy_family: str
    strategy_contract_version: str
    horizon_minutes: int
    allowed_horizons: tuple[int, ...]
    default_horizon_minutes: int
    implementation_status: str
    executable: bool
    registry_error: str


BROAD_ANOMALY_VARIANTS: tuple[str, ...] = (
    "broad_anomaly_v1_h15",
    "broad_anomaly_v1_h30",
    "broad_anomaly_v1_h60",
)

EXECUTABLE_STRATEGY_NAMES: tuple[str, ...] = BROAD_ANOMALY_VARIANTS
SPECIFIED_NOT_IMPLEMENTED_STRATEGY_NAMES: tuple[str, ...] = tuple(
    strategy_name for strategy_name in ANOMALY_STRATEGY_DEFAULTS if strategy_name not in EXECUTABLE_STRATEGY_NAMES
)


def available_strategies() -> tuple[StrategyRegistryEntry, ...]:
    """Return executable strategy variants only.

    Specified-only variants are intentionally excluded. They can appear in the
    implementation-status artifact but must never be instantiated through a
    default or broad-anomaly fallback factory.
    """
    return tuple(
        StrategyRegistryEntry(
            strategy_name=strategy_name,
            strategy_family="anomaly",
            strategy_contract_version="base_strategy_v1",
            factory=lambda strategy_name=strategy_name: make_broad_anomaly_strategy(strategy_name=strategy_name),
        )
        for strategy_name in EXECUTABLE_STRATEGY_NAMES
    )


def executable_strategy_names() -> tuple[str, ...]:
    return EXECUTABLE_STRATEGY_NAMES


def specified_not_implemented_strategy_names() -> tuple[str, ...]:
    return SPECIFIED_NOT_IMPLEMENTED_STRATEGY_NAMES


def strategy_implementation_statuses() -> tuple[StrategyImplementationStatus, ...]:
    rows: list[StrategyImplementationStatus] = []
    for strategy_name, defaults in ANOMALY_STRATEGY_DEFAULTS.items():
        executable = strategy_name in EXECUTABLE_STRATEGY_NAMES
        rows.append(
            StrategyImplementationStatus(
                strategy_name=strategy_name,
                strategy_family="anomaly",
                strategy_contract_version="base_strategy_v1",
                horizon_minutes=int(defaults["horizon_minutes"]),
                allowed_horizons=tuple(int(horizon) for horizon in defaults["allowed_horizons"]),
                default_horizon_minutes=int(defaults["default_horizon_minutes"]),
                implementation_status="implemented" if executable else "specified_not_implemented",
                executable=executable,
                registry_error="" if executable else "strategy variant is specified but not implemented yet",
            )
        )
    return tuple(rows)


def get_strategy(strategy_name: str) -> BaseStrategy:
    for entry in available_strategies():
        if entry.strategy_name == strategy_name:
            return entry.factory()
    if strategy_name in SPECIFIED_NOT_IMPLEMENTED_STRATEGY_NAMES:
        raise StrategyRegistryError(f"strategy variant is specified but not implemented yet: {strategy_name!r}")
    raise StrategyRegistryError(f"unknown strategy_name: {strategy_name!r}")


def validate_strategy_horizon(strategy_name: str, horizon_minutes: int) -> None:
    """Validate an executable strategy/horizon pair at the registry boundary.

    Core owns the technical supported-horizon whitelist. Strategy metadata owns
    the semantic allowed horizons for a concrete hypothesis. The registry is the
    enforcement point that prevents arbitrary horizons, specified-but-not-
    implemented strategies, and mismatched selected horizons from reaching ML,
    controls, EV, or simulation.
    """
    if not strategy_name:
        raise StrategyRegistryError("strategy_name is required")
    try:
        validate_supported_research_horizon(horizon_minutes, field_name="horizon_minutes")
    except ValueError as exc:
        raise StrategyRegistryError(str(exc)) from exc

    strategy = get_strategy(strategy_name)
    metadata = strategy.metadata
    if horizon_minutes != metadata.horizon_minutes:
        raise StrategyRegistryError(
            "strategy/horizon mismatch: "
            f"{strategy_name!r} selects h{metadata.horizon_minutes}, but h{horizon_minutes} was requested"
        )
    if horizon_minutes not in metadata.allowed_horizons:
        raise StrategyRegistryError(
            "strategy horizon is not semantically allowed: "
            f"{strategy_name!r} requested h{horizon_minutes}, allowed={metadata.allowed_horizons}"
        )


def get_broad_anomaly_strategy(
    config: BroadAnomalyDetectorConfig | None = None,
    *,
    strategy_name: str = "broad_anomaly_v1_h30",
) -> BroadAnomalyStrategy:
    return make_broad_anomaly_strategy(strategy_name=strategy_name, config=config)
