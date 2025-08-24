# domain/exchange_clients/BybitClient.py  (или рядом с BinanceClient.py)
from datetime import datetime
from typing import List, Optional

import ccxt.async_support as ccxt  # важно: асинхронная версия ccxt

from config.constants import TIMEZONE, BARS_LIMIT
from domain.exchange_client import ExchangeClient
from domain.models.Bar import Bar
from domain.models.Exchange import Exchange
from domain.models.Timeframe import Timeframe


class BybitClient(ExchangeClient):
    """Клиент Bybit на ccxt."""

    def __init__(self, api_key: str, api_secret: str, default_type: str = "linear") -> None:
        """
        default_type:
            - "linear"  — USDT/USDC-перпетуалы (рекомендуется)
            - "inverse" — инверсные контракты (BTC/USD и т.п.)
            - "spot"    — спот (если понадобится)
        """
        super(BybitClient, self).__init__(api_key, api_secret)
        self._client = ccxt.bybit({
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "enableRateLimit": True,
            "options": {
                "adjustForTimeDifference": True,
                "defaultType": default_type,
            },
        })

    async def close(self) -> None:
        """Закрыть http-сессию ccxt."""
        await self._client.close()

    # === Интерфейс ===

    async def fetch_symbols(self) -> list[str]:
        """
        Получить список символов для линейных (USDT/USDC) деривативов.
        Возвращает ccxt-символы вида 'BTC/USDT' или 'BTC/USDT:USDT' в зависимости от версии ccxt.
        """
        markets = await self._client.load_markets()
        symbols: List[str] = []
        for m in markets.values():
            if not m.get("active", True):
                continue
            # Берём только деривативы на USDT/USDC (linear), чтобы совпадало с логикой детектора
            if (
                m.get("contract")
                and m.get("linear")
                and m.get("quote") in {"USDT", "USDC"}
                and m.get("type") in {"swap", "future"}
            ):
                symbols.append(m["symbol"])
        return sorted(set(symbols))

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = BARS_LIMIT,
        end_dt: Optional[datetime] = None,
    ) -> list[Bar]:
        """
        История свечей. Если задан end_dt — вернём правый край не позже end_dt.
        Для Bybit используем 'since' + обрезаем по end_dt (у Bybit нет унифицированного endTime).
        """
        if end_dt is not None:
            end_ms = int(end_dt.timestamp() * 1000)
            tf_ms = int(self._client.parse_timeframe(timeframe.value) * 1000)
            since = max(0, end_ms - limit * tf_ms)

            raw = await self._client.fetch_ohlcv(symbol, timeframe.value, since=since, limit=limit)
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
        return Exchange.BYBIT
