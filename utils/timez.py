"""Timezone-aware datetime helpers."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Union

from config.timezone import BELGRADE_TIMEZONE

NumberLike = Union[int, float, str, Decimal]

_NANO_THRESHOLD: Decimal = Decimal("1000000000000000000")
_MICRO_THRESHOLD: Decimal = Decimal("1000000000000000")
_MILLI_THRESHOLD: Decimal = Decimal("1000000000000")


def get_current_time() -> datetime:
    """Return the current time in the configured timezone."""

    current: datetime = datetime.now(BELGRADE_TIMEZONE)
    return current


def from_exchange_timestamp(timestamp: NumberLike) -> datetime:
    """Convert a raw exchange timestamp to a timezone-aware datetime."""

    seconds: Decimal = _normalize_to_seconds(timestamp)
    return datetime.fromtimestamp(float(seconds), BELGRADE_TIMEZONE)


def _normalize_to_seconds(timestamp: NumberLike) -> Decimal:
    value: Decimal = _coerce_to_decimal(timestamp)
    absolute: Decimal = abs(value)
    if absolute == 0:
        return Decimal(0)
    if absolute >= _NANO_THRESHOLD:
        return value / Decimal(1_000_000_000)
    if absolute >= _MICRO_THRESHOLD:
        return value / Decimal(1_000_000)
    if absolute >= _MILLI_THRESHOLD:
        return value / Decimal(1_000)
    return value


def _coerce_to_decimal(timestamp: NumberLike) -> Decimal:
    if isinstance(timestamp, str):
        stripped: str = timestamp.strip()
        if not stripped:
            raise ValueError("timestamp string must not be empty")
        try:
            decimal_value: Decimal = Decimal(stripped)
        except (ArithmeticError, DecimalException, ValueError) as error:
            raise ValueError("timestamp string must contain a numeric value") from error
        _validate_decimal(decimal_value)
        return decimal_value
    if isinstance(timestamp, int):
        return Decimal(timestamp)
    if isinstance(timestamp, float):
        if math.isnan(timestamp) or math.isinf(timestamp):
            raise ValueError("timestamp must be a finite number")
        return Decimal(str(timestamp))
    if isinstance(timestamp, Decimal):
        _validate_decimal(timestamp)
        return timestamp
    raise TypeError("timestamp must be int, float, str, or Decimal")


def _validate_decimal(value: Decimal) -> None:
    if value.is_nan() or value.is_infinite():
        raise ValueError("timestamp must be a finite number")


__all__ = ["NumberLike", "get_current_time", "from_exchange_timestamp"]

