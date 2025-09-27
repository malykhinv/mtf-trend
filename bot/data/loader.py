"""Market data loader abstractions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from bot.domain.models.bar import Bar
from bot.domain.models.exchange import Exchange
from bot.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class HistoricalRequest:
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    limit: int | None = None


class MarketDataLoader:
    """Base synchronous interface for loading historical bars."""

    def load(self, request: HistoricalRequest) -> Iterable[Bar]:  # pragma: no cover - interface definition
        raise NotImplementedError


class LiveDataStream:
    """Streaming interface that delivers closed bars to listeners."""

    def subscribe(self, listener: Callable[[Bar], None]) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError
