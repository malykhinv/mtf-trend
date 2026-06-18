from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m


class StrategyContractError(ValueError):
    """Raised when a strategy declaration violates the base strategy contract."""


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_name: str
    strategy_version: str
    strategy_contract_version: str
    strategy_family: str
    label_horizons_minutes: tuple[int, ...]
    primary_horizon_minutes: int
    take_profit_atr: float
    stop_loss_atr: float
    feature_schema_version: str
    label_schema_version: str

    def __post_init__(self) -> None:
        if not self.strategy_name:
            raise StrategyContractError("strategy_name is required")
        if not self.strategy_version:
            raise StrategyContractError("strategy_version is required")
        if not self.strategy_contract_version:
            raise StrategyContractError("strategy_contract_version is required")
        if not self.strategy_family:
            raise StrategyContractError("strategy_family is required")
        if not self.label_horizons_minutes:
            raise StrategyContractError("label_horizons_minutes is required")
        if any(horizon <= 0 for horizon in self.label_horizons_minutes):
            raise StrategyContractError("label_horizons_minutes must be positive")
        if len(set(self.label_horizons_minutes)) != len(self.label_horizons_minutes):
            raise StrategyContractError("label_horizons_minutes must be unique")
        if self.primary_horizon_minutes not in self.label_horizons_minutes:
            raise StrategyContractError("primary_horizon_minutes must be one of label_horizons_minutes")
        if self.take_profit_atr <= 0:
            raise StrategyContractError("take_profit_atr must be positive")
        if self.stop_loss_atr <= 0:
            raise StrategyContractError("stop_loss_atr must be positive")
        if not self.feature_schema_version:
            raise StrategyContractError("feature_schema_version is required")
        if not self.label_schema_version:
            raise StrategyContractError("label_schema_version is required")


class BaseStrategy(Protocol):
    metadata: StrategyMetadata

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[AnomalyEvent, ...]:
        """Return strategy trigger events using only point-in-time market data."""

    def generate_custom_features(self, market_frame_asof: pd.DataFrame) -> pd.DataFrame:
        """Return strategy-specific as-of features, without fitting or reading labels."""
