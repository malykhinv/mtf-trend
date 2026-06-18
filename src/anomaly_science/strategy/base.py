from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

import polars as pl


class StrategyContractError(ValueError):
    """Raised when a strategy declaration violates the base strategy contract."""


INTERNAL_TIME_DTYPE = pl.Datetime(time_unit="ms", time_zone="UTC")

REQUIRED_TRIGGER_FRAME_COLUMNS: tuple[str, ...] = (
    "symbol",
    "state_time",
    "is_trigger",
    "event_id",
    "event_start_time",
)

_FORBIDDEN_INTERNAL_TIME_COLUMNS: tuple[str, ...] = (
    "state_time_ms",
    "event_start_time_ms",
)


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_name: str
    strategy_version: str
    strategy_contract_version: str
    strategy_family: str
    horizon_minutes: int
    take_profit_atr_1440: float
    stop_loss_atr_1440: float
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
        if self.take_profit_atr_1440 <= 0:
            raise StrategyContractError("take_profit_atr_1440 must be positive")
        if self.stop_loss_atr_1440 <= 0:
            raise StrategyContractError("stop_loss_atr_1440 must be positive")
        if not self.feature_schema_version:
            raise StrategyContractError("feature_schema_version is required")
        if not self.label_schema_version:
            raise StrategyContractError("label_schema_version is required")


class BaseStrategy(ABC):
    """Stable strategy boundary used by Core without strategy-specific branching.

    One concrete strategy instance represents exactly one trading hypothesis version
    and one fixed prediction horizon. Multi-horizon research must register separate
    strategy variants instead of passing tuple/list horizons through one instance.

    All Polars frames crossing this boundary use native ``pl.Datetime[ms, UTC]``
    columns for internal time fields. Unix ``*_ms`` integers are reserved for
    external import/export artifacts and must not appear in trigger-frame output.
    """

    @property
    @abstractmethod
    def metadata(self) -> StrategyMetadata:
        """Return immutable strategy identity, horizon, schemas and ATR-1440 simulation defaults."""

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
        ``state_time`` and ``event_start_time`` must be native Polars datetimes.
        """

    @abstractmethod
    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        """Return strictly causal strategy-specific as-of features.

        Forbidden inside strategy implementations: negative shifts, centered/future
        rolling windows, backward fill, full-period normalizations, or future extrema.
        Geometry features must be normalized to Core ATR-1440 when price scale matters.
        """


def validate_required_data_streams(streams: Mapping[str, bool]) -> None:
    if not streams:
        raise StrategyContractError("required_data_streams must declare at least one stream")
    for stream_name, required in streams.items():
        if not stream_name:
            raise StrategyContractError("required_data_streams contains an empty stream name")
        if type(required) is not bool:
            raise StrategyContractError(f"required_data_streams[{stream_name!r}] must be bool")


def validate_trigger_frame(trigger_frame: pl.DataFrame) -> None:
    forbidden = [name for name in _FORBIDDEN_INTERNAL_TIME_COLUMNS if name in trigger_frame.columns]
    if forbidden:
        raise StrategyContractError(f"trigger frame must use native datetime time columns, not: {forbidden}")
    missing = [name for name in REQUIRED_TRIGGER_FRAME_COLUMNS if name not in trigger_frame.columns]
    if missing:
        raise StrategyContractError(f"trigger frame is missing required columns: {missing}")
    if trigger_frame.height == 0:
        return
    if trigger_frame["symbol"].null_count() > 0:
        raise StrategyContractError("trigger frame symbol must not be null")
    if trigger_frame["event_id"].null_count() > 0:
        raise StrategyContractError("trigger frame event_id must not be null")
    if trigger_frame["state_time"].null_count() > 0:
        raise StrategyContractError("trigger frame state_time must not be null")
    if trigger_frame["event_start_time"].null_count() > 0:
        raise StrategyContractError("trigger frame event_start_time must not be null")
    _validate_datetime_column(trigger_frame, "state_time")
    _validate_datetime_column(trigger_frame, "event_start_time")
    if trigger_frame["is_trigger"].dtype != pl.Boolean:
        raise StrategyContractError("trigger frame is_trigger must be boolean")
    invalid_time_order = trigger_frame.filter(pl.col("event_start_time") > pl.col("state_time"))
    if invalid_time_order.height:
        raise StrategyContractError("trigger frame event_start_time must be <= state_time")


def validate_point_in_time_feature_equivalence(
    strategy: BaseStrategy,
    *,
    full_market_frame: pl.DataFrame,
    point_in_time_market_frame: pl.DataFrame,
    join_columns: Sequence[str] = ("symbol", "state_time"),
) -> None:
    """Audit custom feature causality by comparing full-frame vs PIT-slice output.

    Core may call this in tests/audit runs. For rows common to both outputs, every
    non-key feature value must be identical. A mismatch means the strategy feature
    calculation read future rows or used non-causal whole-period normalization.
    """
    full_features = strategy.generate_custom_features(full_market_frame)
    pit_features = strategy.generate_custom_features(point_in_time_market_frame)
    if full_features.height == 0 and pit_features.height == 0:
        return
    missing_full = [name for name in join_columns if name not in full_features.columns]
    missing_pit = [name for name in join_columns if name not in pit_features.columns]
    if missing_full or missing_pit:
        raise StrategyContractError(
            "custom feature causality audit requires join columns in both outputs: "
            f"missing_full={missing_full}, missing_pit={missing_pit}"
        )
    comparable_columns = [name for name in pit_features.columns if name not in join_columns]
    full_prefixed = full_features.select([*join_columns, *comparable_columns]).rename(
        {name: f"full__{name}" for name in comparable_columns}
    )
    pit_prefixed = pit_features.select([*join_columns, *comparable_columns]).rename(
        {name: f"pit__{name}" for name in comparable_columns}
    )
    joined = pit_prefixed.join(full_prefixed, on=list(join_columns), how="left")
    for name in comparable_columns:
        mismatch = joined.filter(pl.col(f"pit__{name}") != pl.col(f"full__{name}"))
        if mismatch.height:
            raise StrategyContractError(
                f"custom feature {name!r} is not point-in-time stable; non-causal feature calculation suspected"
            )


def utc_ms_to_internal_datetime(value_ms: int) -> datetime:
    """Convert external Unix ms into the internal BaseStrategy UTC datetime value."""
    if type(value_ms) is not int:
        raise StrategyContractError(f"timestamp must be int unix milliseconds, got {type(value_ms).__name__}")
    if value_ms < 0:
        raise StrategyContractError("timestamp must be non-negative")
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)


def internal_datetime_to_utc_ms(value: datetime) -> int:
    """Convert an internal UTC datetime back to external artifact Unix ms."""
    if not isinstance(value, datetime):
        raise StrategyContractError(f"internal datetime expected, got {type(value).__name__}")
    if value.tzinfo is None:
        raise StrategyContractError("internal datetime must be timezone-aware")
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _validate_datetime_column(frame: pl.DataFrame, name: str) -> None:
    dtype = frame[name].dtype
    if not _is_datetime_ms(dtype):
        raise StrategyContractError(f"trigger frame {name} must be pl.Datetime with millisecond precision")


def _is_datetime_ms(dtype: object) -> bool:
    return getattr(dtype, "time_unit", None) == "ms" and str(dtype).startswith("Datetime")
