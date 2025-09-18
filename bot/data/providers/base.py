from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, Iterable, List, Sequence

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ..mappers.ohlcv_mapper import map_ohlcv
from ...utils.logging import get_logger


class RateLimiter:
    def __init__(self, rate_per_minute: int) -> None:
        self._interval = 60.0 / max(rate_per_minute, 1)
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last_call + self._interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


class BaseExchangeProvider:
    exchange: Exchange

    def __init__(
        self,
        api_base: str,
        ws_base: str,
        rate_limit_per_minute: int,
        min_quote_volume: float,
    ) -> None:
        self._api_base = api_base
        self._ws_base = ws_base
        self._rate_limiter = RateLimiter(rate_limit_per_minute)
        self._min_quote_volume = min_quote_volume
        self._logger = get_logger(self.__class__.__name__)

    async def _filter_liquidity(self, candles: Sequence[Candle]) -> List[Candle]:
        filtered: List[Candle] = []
        for candle in candles:
            quote_volume = candle.quote_volume if candle.quote_volume is not None else candle.volume
            if quote_volume >= self._min_quote_volume:
                filtered.append(candle)
        return filtered

    async def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int) -> List[Candle]:
        raise NotImplementedError

    async def stream_candles(
        self, symbol: str, timeframe: Timeframe
    ) -> AsyncIterator[Candle]:
        raise NotImplementedError

    async def get_symbols(self) -> Iterable[str]:
        raise NotImplementedError

    async def get_24h_quote_volume(self) -> Dict[str, float]:
        """Return a mapping of symbol to 24 hour quote volume."""
        raise NotImplementedError

    async def update_deposit(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        """Release provider resources."""

    def map_candles(
        self, raw: Iterable[Sequence[Any]], symbol: str, timeframe: Timeframe
    ) -> List[Candle]:
        return [map_ohlcv(item, symbol, self.exchange, timeframe) for item in raw]

    async def ensure_rate_limit(self) -> None:
        await self._rate_limiter.throttle()
