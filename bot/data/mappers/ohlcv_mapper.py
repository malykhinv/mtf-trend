from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle


def map_ohlcv(
    raw: Sequence[Any],
    symbol: str,
    exchange: Exchange,
    timeframe: Timeframe,
) -> Candle:
    if len(raw) < 6:
        raise ValueError("raw OHLCV should have at least 6 elements")
    timestamp = datetime.fromtimestamp(raw[0] / 1000 if raw[0] > 1e12 else raw[0], tz=timezone.utc)
    candle_id = f"{exchange.value}:{symbol}:{timeframe.value}:{int(timestamp.timestamp())}"
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
