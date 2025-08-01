"""Реализация клиента биржи Binance."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

import aiohttp
import websockets

from . import BaseExchange, register


class BinanceExchange(BaseExchange):
    """Минимальный клиент Binance с поддержкой REST и WebSocket."""

    REST_URL = "https://fapi.binance.com"
    WS_URL = "wss://fstream.binance.com/ws"
    SPOT_REST_URL = "https://api.binance.com"
    SPOT_WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(self, api_key: str, api_secret: str) -> None:
        """Инициализация клиента.

        Параметры
        ----------
        api_key, api_secret:
            Ключ и секрет API для авторизации на бирже.
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        # Обработка WebSocket для спота
        self._spot_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._spot_orderbooks: Dict[str, Dict[str, Any]] = {}
        self._spot_ws_tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Утилиты REST
    # ------------------------------------------------------------------

    async def _session_get(self) -> aiohttp.ClientSession:
        """Создаёт ``ClientSession`` при первом обращении."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Подписывает параметры запроса с помощью HMAC SHA256."""
        params = params.copy()
        params["timestamp"] = int(time.time() * 1000)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        # Формируем подпись из строки запроса
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    async def fetch_funding(self, symbol: str) -> float:
        """Получает текущую ставку фондирования для ``symbol``."""
        session = await self._session_get()
        url = f"{self.REST_URL}/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": 1}
        async with session.get(url, params=params) as resp:
            data = await resp.json()
        return float(data[0]["fundingRate"])

    async def fetch_funding_history(
        self, symbol: str, hours: int = 8, limit: int = 3
    ) -> list[float]:
        """Возвращает историю ставок фондирования за заданный период."""
        session = await self._session_get()
        url = f"{self.REST_URL}/fapi/v1/fundingRate"
        end_time = int(time.time() * 1000)
        start_time = end_time - hours * 3600 * 1000
        params = {
            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit,
        }
        async with session.get(url, params=params) as resp:
            data = await resp.json()
        return [float(entry["fundingRate"]) for entry in data]

    async def place_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Размещает фьючерсный ордер на бирже."""
        session = await self._session_get()
        url = f"{self.REST_URL}/fapi/v1/order"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET" if price is None else "LIMIT",
            "quantity": quantity,
        }
        if price is not None:
            params.update({"price": price, "timeInForce": "GTC"})
        headers = {"X-MBX-APIKEY": self.api_key}
        params = self._sign(params)
        async with session.post(url, params=params, headers=headers) as resp:
            return await resp.json()

    async def get_balance(self) -> dict:
        """Возвращает баланс фьючерсного аккаунта."""
        session = await self._session_get()
        url = f"{self.REST_URL}/fapi/v2/balance"
        params = self._sign({})
        headers = {"X-MBX-APIKEY": self.api_key}
        async with session.get(url, params=params, headers=headers) as resp:
            return await resp.json()

    async def get_stats(self, symbol: str) -> dict:
        """Получает 24‑часовой объём и открытый интерес для ``symbol``."""
        session = await self._session_get()
        ticker_url = f"{self.REST_URL}/fapi/v1/ticker/24hr"
        params = {"symbol": symbol}
        async with session.get(ticker_url, params=params) as resp:
            ticker = await resp.json()
        # ``volume`` в базовой валюте; используем ``quoteVolume`` для USD
        volume = float(ticker.get("quoteVolume", ticker.get("volume", 0.0)))
        oi_url = f"{self.REST_URL}/fapi/v1/openInterest"
        async with session.get(oi_url, params=params) as resp:
            oi = await resp.json()
        open_interest = float(oi.get("openInterest", 0.0))
        return {"volume_24h": volume, "open_interest": open_interest}

    async def get_ohlc(
        self, symbol: str, interval: str, limit: int = 1
    ) -> list[Dict[str, float]]:
        """Возвращает свечи OHLC для ``symbol``."""
        session = await self._session_get()
        url = f"{self.REST_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        async with session.get(url, params=params) as resp:
            data = await resp.json()
        return [
            {
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            }
            for k in data
        ]

    # ------------------------------------------------------------------
    # REST-методы спота
    # ------------------------------------------------------------------

    async def place_spot_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Размещает спотовый ордер."""
        session = await self._session_get()
        url = f"{self.SPOT_REST_URL}/api/v3/order"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET" if price is None else "LIMIT",
            "quantity": quantity,
        }
        if price is not None:
            params.update({"price": price, "timeInForce": "GTC"})
        headers = {"X-MBX-APIKEY": self.api_key}
        params = self._sign(params)
        async with session.post(url, params=params, headers=headers) as resp:
            return await resp.json()

    async def get_spot_balance(self) -> dict:
        """Возвращает баланс спотового аккаунта."""
        session = await self._session_get()
        url = f"{self.SPOT_REST_URL}/api/v3/account"
        params = self._sign({})
        headers = {"X-MBX-APIKEY": self.api_key}
        async with session.get(url, params=params, headers=headers) as resp:
            return await resp.json()

    # ------------------------------------------------------------------
    # Обработка спотового WebSocket
    # ------------------------------------------------------------------

    async def _connect_spot(
        self, symbol: str, depth: int
    ) -> websockets.WebSocketClientProtocol:
        """Подключается к спотовому WebSocket и возвращает соединение."""
        while True:
            try:
                return await websockets.connect(
                    f"{self.SPOT_WS_URL}/{symbol.lower()}@depth{depth}@100ms"
                )
            except Exception:
                await asyncio.sleep(5)

    async def _listen_spot(self, symbol: str, depth: int) -> None:
        """Слушает поток спотового order book и кэширует данные."""
        while True:
            try:
                if self._spot_ws is None:
                    self._spot_ws = await self._connect_spot(symbol, depth)
                msg = await self._spot_ws.recv()
                data = json.loads(msg)
                if "bids" in data and "asks" in data:
                    self._spot_orderbooks[symbol] = {
                        "bids": data["bids"],
                        "asks": data["asks"],
                    }
            except Exception:
                # При ошибке пересоздаём соединение
                await asyncio.sleep(1)
                if self._spot_ws is not None:
                    try:
                        await self._spot_ws.close()
                    except Exception:
                        pass
                self._spot_ws = None

    async def get_spot_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Возвращает кэшированный спотовый стакан для ``symbol``."""
        if symbol not in self._spot_ws_tasks:
            self._spot_ws_tasks[symbol] = asyncio.create_task(
                self._listen_spot(symbol, depth)
            )
        return self._spot_orderbooks.get(symbol, {"bids": [], "asks": []})

    # ------------------------------------------------------------------
    # Обработка WebSocket
    # ------------------------------------------------------------------

    async def _connect(self, symbol: str) -> websockets.WebSocketClientProtocol:
        """Создаёт WebSocket‑соединение для фьючерсного стакана."""
        while True:
            try:
                return await websockets.connect(
                    f"{self.WS_URL}/{symbol.lower()}@depth5@100ms"
                )
            except Exception:
                await asyncio.sleep(5)

    async def _listen(self, symbol: str) -> None:
        """Слушает поток фьючерсного стакана и сохраняет его в кэше."""
        while True:
            try:
                if self._ws is None:
                    self._ws = await self._connect(symbol)
                msg = await self._ws.recv()
                data = json.loads(msg)
                if "bids" in data and "asks" in data:
                    self._orderbooks[symbol] = {
                        "bids": data["bids"],
                        "asks": data["asks"],
                    }
            except Exception:
                # При ошибке пробуем подключиться заново
                await asyncio.sleep(1)
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                self._ws = None

    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Возвращает кэшированный фьючерсный стакан."""
        if symbol not in self._ws_tasks:
            self._ws_tasks[symbol] = asyncio.create_task(self._listen(symbol))
        return self._orderbooks.get(symbol, {"bids": [], "asks": []})

    async def __aexit__(self, *exc_info: Any) -> None:  # pragma: no cover
        """Закрывает все сетевые соединения при выходе из контекста."""
        if self._session is not None:
            await self._session.close()
        if self._ws is not None:
            await self._ws.close()
        if self._spot_ws is not None:
            await self._spot_ws.close()


# Регистрация биржи
register("binance", BinanceExchange)
