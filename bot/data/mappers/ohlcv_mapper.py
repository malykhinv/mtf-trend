from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ...utils.clock import get_timezone


def map_ohlcv(
    raw: Sequence[Any],
    symbol: str,
    exchange: Exchange,
    timeframe: Timeframe,
) -> Candle:
    if len(raw) < 6:
        raise ValueError("raw OHLCV should have at least 6 elements")
    raw_timestamp = raw[0]
    if isinstance(raw_timestamp, datetime):
        if raw_timestamp.tzinfo is None:
            raise ValueError("raw OHLCV timestamp must include timezone information")
        utc_timestamp = raw_timestamp.astimezone(timezone.utc)
    else:
        try:
            base_timestamp = float(raw_timestamp)
        except (TypeError, ValueError) as exc:
            raise TypeError("raw OHLCV timestamp must be numeric or datetime") from exc
        if base_timestamp > 1e12:
            base_timestamp /= 1000
        utc_timestamp = datetime.fromtimestamp(base_timestamp, tz=timezone.utc)
    timestamp = utc_timestamp.astimezone(get_timezone())
    if not isinstance(timestamp, datetime):  # pragma: no cover - defensive
        raise TypeError("Candle timestamp must be a datetime instance")
    candle_id = f"{exchange.value}:{symbol}:{timeframe.value}:{int(utc_timestamp.timestamp())}"
    volume = float(raw[5])
    quote_volume = _extract_quote_volume(raw, exchange)
    return Candle(
        id=candle_id,
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=volume,
        quote_volume=quote_volume,
        started_at=timestamp,
        closed_at=timestamp,
    )


def _extract_quote_volume(raw: Sequence[Any], exchange: Exchange) -> float | None:
    if exchange == Exchange.BINANCE and len(raw) > 7:
        return _safe_float(raw[7])
    if exchange == Exchange.BYBIT and len(raw) > 6:
        # Bybit provides turnover (quote volume) at index 6 in the kline array
        return _safe_float(raw[6])
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
