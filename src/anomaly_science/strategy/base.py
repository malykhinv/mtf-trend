from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping

import polars as pl


class StrategyContractError(ValueError):
    """Raised when a strategy declaration violates the base strategy contract."""


REQUIRED_TRIGGER_FRAME_COLUMNS: tuple[str, ...] = (
    "symbol",
    "state_time_ms",
    "is_trigger",
    "event_id",
    "event_start_time_ms",
)


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
        if type(self.horizon_minutes) is not int:
            raise StrategyContractError("horizon_minutes must be one fixed int per strategy instance")
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


class BaseStrategy(ABC):
    """Stable strategy boundary used by Core without strategy-specific branching.

    One concrete strategy instance represents exactly one trading hypothesis version
    and one fixed prediction horizon. Multi-horizon research must register separate
    strategy variants instead of passing tuple/list horizons through one instance.
    """

    @property
    @abstractmethod
    def metadata(self) -> StrategyMetadata:
        """Return immutable strategy identity, horizon, schemas and simulation defaults."""

    @property
    @abstractmethod
    def required_data_streams(self) -> Mapping[str, bool]:
        """Declare which optional data streams are mandatory for this strategy.

        Core data-quality gates use this matrix before trigger generation. Missing
        required streams must become explicit rejects, never model features or edge.
        """

    @abstractmethod
    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        """Return a trigger frame using only the supplied point-in-time market frame.

        The returned frame must include REQUIRED_TRIGGER_FRAME_COLUMNS. Strategies may
        add audit columns; Core stores them only through declared artifact schemas.
        """

    @abstractmethod
    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        """Return strategy-specific as-of features, without fitting or reading labels."""


def validate_required_data_streams(streams: Mapping[str, bool]) -> None:
    if not streams:
        raise StrategyContractError("required_data_streams must declare at least one stream")
    for stream_name, required in streams.items():
        if not stream_name:
            raise StrategyContractError("required_data_streams contains an empty stream name")
        if type(required) is not bool:
            raise StrategyContractError(f"required_data_streams[{stream_name!r}] must be bool")


def validate_trigger_frame(trigger_frame: pl.DataFrame) -> None:
    missing = [name for name in REQUIRED_TRIGGER_FRAME_COLUMNS if name not in trigger_frame.columns]
    if missing:
        raise StrategyContractError(f"trigger frame is missing required columns: {missing}")
    if trigger_frame.height == 0:
        return
    if trigger_frame["symbol"].null_count() > 0:
        raise StrategyContractError("trigger frame symbol must not be null")
    if trigger_frame["event_id"].null_count() > 0:
        raise StrategyContractError("trigger frame event_id must not be null")
    if trigger_frame["state_time_ms"].null_count() > 0:
        raise StrategyContractError("trigger frame state_time_ms must not be null")
    if trigger_frame["event_start_time_ms"].null_count() > 0:
        raise StrategyContractError("trigger frame event_start_time_ms must not be null")
    if trigger_frame["is_trigger"].dtype != pl.Boolean:
        raise StrategyContractError("trigger frame is_trigger must be boolean")
