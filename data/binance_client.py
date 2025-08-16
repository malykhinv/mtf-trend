from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List

import ccxt.async_support as ccxt  # важно: асинхронная версия ccxt

from config.constants import TIMEZONE
from domain.exchange_client import ExchangeClient
from domain.models.bar import Bar
from domain.models.timeframe import Timeframe


@dataclass(slots=True)
class BinanceClient(ExchangeClient):
    """Клиент Binance на ccxt."""

    def __init__(self, api_key: str, api_secret: str, default_type: str = "future") -> None:
        super().__init__(api_key, api_secret)
        self._client = ccxt.binance({
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "enableRateLimit": True,
            "options": {
                "adjustForTimeDifference": True,
                "defaultType": default_type,
            },
        })

    async def close(self) -> None:
        """Закрыть http‑сессию ccxt."""
        await self._client.close()

    # === Интерфейс ===

    async def fetch_symbols(self) -> list[str]:
        """
        Получить список символов.
        """
        markets = await self._client.load_markets()
        symbols: List[str] = []
        for market in markets.values():
            if not market.get("active", True):
                continue
            if (market.get("contract") and
                    market.get("linear") and
                    market.get("quote") == "USDT" and
                    market.get("type") in {"swap", "future"}):
                symbols.append(market["symbol"])
        return sorted(set(symbols))

    async def fetch_bars(self, symbol: str, timeframe: Timeframe, limit: int = 500) -> list[Bar]:
        """
        Получить свечи OHLCV.
        CCXT возвращает список [timestamp, open, high, low, close, volume].
        """
        raw = await self._client.fetch_ohlcv(symbol, timeframe.value, limit=limit)
        return [self._to_bar(row) for row in raw]

    # === Внутреннее ===

    @staticmethod
    def _to_bar(row: list) -> Bar:
        """
        Преобразование CCXT OHLCV -> Bar.
        """
        ts, o, h, l, c, v = row
        return Bar(
            time=datetime.fromtimestamp(ts / 1000, tz=TIMEZONE),
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
        )
