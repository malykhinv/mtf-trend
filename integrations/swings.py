from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence, TypedDict, overload

from domain.models import Band, Candle, SwingsOutput, SwingHigh


class RawSwing(TypedDict):
    """Raw representation of a swing high returned by the external module."""

    price: float
    timestamp: int | float | datetime
    index: int


class RawBand(TypedDict):
    """Raw representation of a consolidation band."""

    low: float
    high: float
    start_timestamp: int | float | datetime
    end_timestamp: int | float | datetime


class RawSwingsOutput(TypedDict):
    """Raw payload describing extracted swings on the low timeframe."""

    swings: list[RawSwing]
    has_consolidation: bool
    consolidation_band: RawBand | None


class SwingsExtractor(Protocol):
    """Protocol describing the expected swings extraction interface."""

    def extract(self, candles: Sequence[Candle]) -> RawSwingsOutput:
        """Return swings identified on the provided ``candles``."""


@dataclass
class SwingsAdapter:
    """Adapter converting raw swings payloads into domain models."""

    extractor: SwingsExtractor

    def extract_swings(self, candles: Sequence[Candle]) -> SwingsOutput:
        """Extract swings and convert them into domain objects."""

        raw_output = self.extractor.extract(candles)
        swings = [_convert_raw_swing(raw_swing) for raw_swing in raw_output["swings"]]
        band = _convert_raw_band(raw_output.get("consolidation_band"))
        return SwingsOutput(
            swings=swings,
            has_consolidation=raw_output["has_consolidation"],
            consolidation_band=band,
        )


def _convert_raw_swing(raw: RawSwing) -> SwingHigh:
    return SwingHigh(
        price=float(raw["price"]),
        timestamp=_normalise_datetime(raw["timestamp"]),
        index=int(raw["index"]),
    )


def _convert_raw_band(raw: RawBand | None) -> Band | None:
    if raw is None:
        return None
    return Band(
        low=float(raw["low"]),
        high=float(raw["high"]),
        start_timestamp=_normalise_datetime(raw["start_timestamp"]),
        end_timestamp=_normalise_datetime(raw["end_timestamp"]),
    )


@overload
def _normalise_datetime(value: datetime) -> datetime:
    ...


@overload
def _normalise_datetime(value: int | float) -> datetime:
    ...


def _normalise_datetime(value: datetime | int | float) -> datetime:
    """Normalise ``value`` into a timezone-aware ``datetime`` instance."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if not isinstance(value, (int, float)):
        raise TypeError("timestamp должен быть datetime, int или float")

    timestamp = float(value)
    # ``Swings`` may emit timestamps either in seconds or milliseconds.
    if timestamp > 1_000_000_000_000:
        timestamp /= 1_000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


__all__ = [
    "RawBand",
    "RawSwing",
    "RawSwingsOutput",
    "SwingsAdapter",
    "SwingsExtractor",
]
