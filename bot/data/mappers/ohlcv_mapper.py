from __future__ import annotations

from datetime import timezone

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ...utils.clock import get_timezone
from ..models import OhlcvSnapshot


def map_ohlcv(
    snapshot: OhlcvSnapshot,
    symbol: str,
    exchange: Exchange,
    timeframe: Timeframe,
) -> Candle:
    opened_at = snapshot.opened_at.astimezone(timezone.utc)
    closed_at = snapshot.closed_at.astimezone(timezone.utc)
    timestamp = opened_at.astimezone(get_timezone())
    candle_id = f"{exchange.value}:{symbol}:{timeframe.value}:{int(opened_at.timestamp())}"
    return Candle(
        id=candle_id,
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        open=snapshot.open_price,
        high=snapshot.high_price,
        low=snapshot.low_price,
        close=snapshot.close_price,
        volume=snapshot.volume,
        quote_volume=_extract_quote_volume(snapshot),
        started_at=timestamp,
        closed_at=closed_at.astimezone(get_timezone()),
    )


def _extract_quote_volume(snapshot: OhlcvSnapshot) -> float | None:
    if snapshot.quote_volume is not None:
        return snapshot.quote_volume
    return None
