from datetime import datetime
from typing import List, Optional

import ccxt.async_support as ccxt  # важно: асинхронная версия ccxt

from config.constants import TIMEZONE, BARS_LIMIT
from domain.exchange_client import ExchangeClient
from domain.models.Bar import Bar
from domain.models.Exchange import Exchange
from domain.models.Timeframe import Timeframe


class BinanceClient(ExchangeClient):
    """Клиент Binance на ccxt."""

    def __init__(self, api_key: str, api_secret: str, default_type: str = "future") -> None:
        super(BinanceClient, self).__init__(api_key, api_secret)
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
            quote = market.get("quote")
            if (market.get("contract") and
                    market.get("linear") and
                    quote == "USDT" and
                    market.get("type") in {"swap", "future"}):
                base = market.get("base")
                symbols.append(f"{base}{quote}")
        return sorted(set(symbols))

    async def fetch_bars(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = BARS_LIMIT,
            end_dt: Optional[datetime] = None,
    ) -> list[Bar]:
        params = {}
        if end_dt is not None:
            end_ms = int(end_dt.timestamp() * 1000)
            tf_ms = int(self._client.parse_timeframe(timeframe.value) * 1000)
            since = max(0, end_ms - limit * tf_ms)
            params["endTime"] = end_ms  # поддерживается Binance

            raw = await self._client.fetch_ohlcv(
                symbol, timeframe.value, since=since, limit=limit, params=params
            )
            # Гарантируем правый край и лимит
            raw = [row for row in raw if row and row[0] <= end_ms]
            if len(raw) > limit:
                raw = raw[-limit:]
        else:
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

    @property
    def exchange(self) -> Exchange:
        return Exchange.BINANCE