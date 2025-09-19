from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence


class OhlcvSnapshot(Protocol):
    """Protocol representing a validated OHLCV snapshot."""

    opened_at: datetime
    closed_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float | None


def _ensure_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{field} must include timezone information")
        return value.astimezone(timezone.utc)
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise TypeError(f"{field} must be a datetime or numeric timestamp") from exc
    if timestamp > 1e12:
        timestamp /= 1000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _ensure_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise TypeError(f"{field} must be numeric") from exc


def _ensure_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


@dataclass(slots=True)
class BinanceKline:
    opened_at: datetime
    closed_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float | None
    raw: Sequence[Any]

    @classmethod
    def from_raw(cls, raw: Any) -> "BinanceKline":
        if not isinstance(raw, Sequence):
            raise TypeError("Binance kline payload must be a sequence")
        if len(raw) < 6:
            raise ValueError("Binance kline payload must include at least 6 entries")
        opened_at = _ensure_datetime(raw[0], "open time")
        close_source = raw[6] if len(raw) > 6 else raw[0]
        closed_at = _ensure_datetime(close_source, "close time")
        open_price = _ensure_float(raw[1], "open price")
        high_price = _ensure_float(raw[2], "high price")
        low_price = _ensure_float(raw[3], "low price")
        close_price = _ensure_float(raw[4], "close price")
        volume = _ensure_float(raw[5], "volume")
        quote_volume = _ensure_optional_float(raw[7] if len(raw) > 7 else None)
        return cls(
            opened_at=opened_at,
            closed_at=closed_at,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            quote_volume=quote_volume,
            raw=raw,
        )


def _extract_first(raw: Mapping[str, Any], keys: Sequence[str], field: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    raise KeyError(f"{field} not found in Bybit kline payload")


@dataclass(slots=True)
class BybitKline:
    opened_at: datetime
    closed_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float | None
    raw: Mapping[str, Any]

    @classmethod
    def from_raw(cls, raw: Any) -> "BybitKline":
        if not isinstance(raw, Mapping):
            raise TypeError("Bybit kline payload must be a mapping")
        opened_value = _extract_first(
            raw,
            ("startTime", "start", "openTime", "open_time", "timestamp"),
            "open time",
        )
        closed_value = raw.get("endTime") or raw.get("end") or opened_value
        opened_at = _ensure_datetime(opened_value, "open time")
        closed_at = _ensure_datetime(closed_value, "close time")
        open_price = _ensure_float(
            _extract_first(raw, ("openPrice", "open"), "open price"), "open price"
        )
        high_price = _ensure_float(
            _extract_first(raw, ("highPrice", "high"), "high price"), "high price"
        )
        low_price = _ensure_float(
            _extract_first(raw, ("lowPrice", "low"), "low price"), "low price"
        )
        close_price = _ensure_float(
            _extract_first(raw, ("closePrice", "close"), "close price"), "close price"
        )
        volume = _ensure_float(
            _extract_first(raw, ("volume", "vol", "turnover"), "volume"), "volume"
        )
        quote_volume = _ensure_optional_float(
            raw.get("turnover") or raw.get("quoteVolume") or raw.get("quote_volume")
        )
        return cls(
            opened_at=opened_at,
            closed_at=closed_at,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            quote_volume=quote_volume,
            raw=raw,
        )

