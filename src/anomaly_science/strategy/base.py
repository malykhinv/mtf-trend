from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import polars as pl


class StrategyContractError(ValueError):
    """Raised when a strategy declaration violates the base strategy contract."""


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_name: str
    strategy_version: str
    strategy_contract_version: str
    strategy_family: str
    horizon_minutes: int
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
        if self.horizon_minutes <= 0:
            raise StrategyContractError("horizon_minutes must be positive")
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

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.Series:
        """Return is_trigger using only the supplied point-in-time market frame."""

    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        """Return strategy-specific as-of features, without fitting or reading labels."""
