from __future__ import annotations

import asyncio
import time
from typing import Dict, Iterable, List, Sequence

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ..models import DepositSnapshot, OhlcvSnapshot
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
    max_ohlcv_limit: int | None = None
    _ohlcv_limit_fallback: int = 500

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

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    async def _filter_liquidity(self, candles: Sequence[Candle]) -> List[Candle]:
        filtered: List[Candle] = []
        for candle in candles:
            quote_volume = candle.quote_volume if candle.quote_volume is not None else candle.volume
            if quote_volume >= self._min_quote_volume:
                filtered.append(candle)
        return filtered

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        since: int | None = None,
    ) -> List[Candle]:
        raise NotImplementedError

    def resolve_ohlcv_limit(self, requested_limit: int | None) -> int:
        """Return a safe OHLCV limit supported by the provider."""
        max_limit = self.max_ohlcv_limit or self._ohlcv_limit_fallback
        if requested_limit is None or requested_limit <= 0:
            return max_limit
        return min(requested_limit, max_limit)

    async def get_symbols(self) -> Iterable[str]:
        raise NotImplementedError

    async def get_24h_quote_volume(self) -> Dict[str, float]:
        """Return a mapping of symbol to 24 hour quote volume."""
        raise NotImplementedError

    async def update_deposit(self) -> DepositSnapshot:
        raise NotImplementedError

    async def close(self) -> None:
        """Release provider resources."""

    def map_candles(
        self, entries: Iterable[OhlcvSnapshot], symbol: str, timeframe: Timeframe
    ) -> List[Candle]:
        return [map_ohlcv(item, symbol, self.exchange, timeframe) for item in entries]

    def _sort_and_deduplicate(self, candles: Iterable[Candle]) -> List[Candle]:
        """Return candles ordered by start time without duplicates."""

        sorted_candles = sorted(candles, key=lambda candle: candle.started_at)
        deduplicated: List[Candle] = []
        seen: set[tuple[str | None, int]] = set()
        for candle in sorted_candles:
            timestamp_ms = int(candle.started_at.timestamp() * 1000)
            key = (candle.id, timestamp_ms)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(candle)
        return deduplicated
