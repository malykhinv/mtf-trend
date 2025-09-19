from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _ensure_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected {field} to be str, got {type(value)!r}")
    return value


def _ensure_float(value: Any, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid float value for {field}: {value!r}") from exc
    raise TypeError(f"Expected numeric value for {field}, got {type(value)!r}")


@dataclass(slots=True)
class BinanceSymbolInfo:
    symbol: str
    status: str

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "BinanceSymbolInfo":
        symbol = _ensure_str(raw["symbol"], "symbol").upper()
        status = _ensure_str(raw["status"], "status")
        return cls(symbol=symbol, status=status)


@dataclass(slots=True)
class BinanceTicker24h:
    symbol: str
    quote_volume: float

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "BinanceTicker24h":
        symbol = _ensure_str(raw["symbol"], "symbol").upper()
        quote_volume = _ensure_float(raw["quoteVolume"], "quoteVolume")
        return cls(symbol=symbol, quote_volume=quote_volume)


@dataclass(slots=True)
class BybitInstrument:
    symbol: str
    status: str

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "BybitInstrument":
        symbol = _ensure_str(raw["symbol"], "symbol").upper()
        status = _ensure_str(raw["status"], "status")
        return cls(symbol=symbol, status=status)


@dataclass(slots=True)
class BybitTicker:
    symbol: str
    turnover24h: float | None
    turnover: float | None

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "BybitTicker":
        symbol = _ensure_str(raw["symbol"], "symbol").upper()
        turnover24h = raw["turnover24h"] if "turnover24h" in raw else None
        turnover = raw["turnover"] if "turnover" in raw else None
        parsed_turnover24h = None
        parsed_turnover = None
        if turnover24h is not None:
            parsed_turnover24h = _ensure_float(turnover24h, "turnover24h")
        if turnover is not None:
            parsed_turnover = _ensure_float(turnover, "turnover")
        return cls(symbol=symbol, turnover24h=parsed_turnover24h, turnover=parsed_turnover)

    def quote_volume(self) -> float | None:
        if self.turnover24h is not None:
            return self.turnover24h
        return self.turnover
