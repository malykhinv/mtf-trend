from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle


def map_ohlcv(
    raw: Sequence[float],
    symbol: str,
    exchange: Exchange,
    timeframe: Timeframe,
) -> Candle:
    if len(raw) < 6:
        raise ValueError("raw OHLCV should have at least 6 elements")
    timestamp = datetime.fromtimestamp(raw[0] / 1000 if raw[0] > 1e12 else raw[0], tz=timezone.utc)
    return Candle(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]),
        started_at=timestamp,
        closed_at=timestamp,
    )
