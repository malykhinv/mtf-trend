"""Реализация клиента биржи Bybit."""
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


class BybitExchange(BaseExchange):
    """Минимальный клиент Bybit с поддержкой REST и WebSocket."""

    REST_URL = "https://api.bybit.com"
    WS_URL = "wss://stream.bybit.com/v5/public/linear"
    SPOT_WS_URL = "wss://stream.bybit.com/v5/public/spot"

    def __init__(self, api_key: str, api_secret: str, **_ignored: Any) -> None:
        """Инициализация клиента.

        Параметры
        ----------
        api_key, api_secret:
            Пара ключей API для авторизации.
        """
        super().__init__(**_ignored)
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[Any] = None
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        # Управление WebSocket спота
        self._spot_ws: Optional[Any] = None
        self._spot_orderbooks: Dict[str, Dict[str, Any]] = {}
        self._spot_ws_tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Утилиты REST
    # ------------------------------------------------------------------

    async def _session_get(self) -> aiohttp.ClientSession:
        """Создаёт ``ClientSession`` при первом использовании."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _request(
        self, method: str, url: str, retries: int = 3, **kwargs: Any
    ) -> Any:
        """Выполняет HTTP-запрос с ограниченным числом повторов.

        При статусе ответа вне диапазона ``200-299`` возбуждает
        ``aiohttp.ClientResponseError``. В случае сетевых ошибок или
        ответов сервера ``5xx`` выполняет повтор с экспоненциальной задержкой.
        """

        session = await self._session_get()
        delay = 1.0
        for attempt in range(retries):
            try:
                request = getattr(session, method.lower())
                async with request(url, **kwargs) as resp:
                    if 200 <= resp.status < 300:
                        return await resp.json()

                    text = await resp.text()
                    if resp.status >= 500 and attempt < retries - 1:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue

                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=text,
                        headers=resp.headers,
                    )
            except aiohttp.ClientError:
                if attempt >= retries - 1:
                    raise
                await asyncio.sleep(delay)
                delay *= 2

    def _sign(self, method: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Подписывает параметры запроса для Bybit."""
        params = params.copy()
        params["api_key"] = self.api_key
        params["timestamp"] = int(time.time() * 1000)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign_str = method + path + query
        # Создаём подпись HMAC
        signature = hmac.new(
            self.api_secret.encode(), sign_str.encode(), hashlib.sha256
        ).hexdigest()
        params["sign"] = signature
        return params

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    async def fetch_funding(self, symbol: str) -> float:
        """Получает последнюю ставку фондирования для ``symbol``."""
        url = f"{self.REST_URL}/v5/market/funding/history"
        params: dict[str, str | int] = {"symbol": symbol, "limit": 1}
        data = await self._request("GET", url, params=params)
        return float(data["result"]["list"][0]["fundingRate"])

    async def fetch_funding_history(
        self, symbol: str, hours: int = 8, limit: int = 3
    ) -> list[float]:
        """Возвращает историю ставок фондирования."""
        url = f"{self.REST_URL}/v5/market/funding/history"
        end_time = int(time.time() * 1000)
        start_time = end_time - hours * 3600 * 1000
        params: dict[str, str | int] = {
            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit,
        }
        data = await self._request("GET", url, params=params)
        records = data.get("result", {}).get("list", [])
        return [float(item.get("fundingRate", 0.0)) for item in records]

    async def place_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Отправляет фьючерсный ордер на Bybit."""
        url_path = "/v5/order/create"
        url = f"{self.REST_URL}{url_path}"
        body: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "qty": quantity,
            "orderType": "Market" if price is None else "Limit",
        }
        if price is not None:
            body["price"] = price
        headers = {"Content-Type": "application/json"}
        body = self._sign("POST", url_path, body)
        return await self._request("POST", url, json=body, headers=headers)

    async def get_balance(self) -> dict:
        """Возвращает баланс унифицированного аккаунта."""
        url_path = "/v5/account/wallet-balance"
        url = f"{self.REST_URL}{url_path}"
        params: Dict[str, Any] = self._sign(
            "GET", url_path, {"accountType": "UNIFIED"}
        )
        return await self._request("GET", url, params=params)

    async def get_stats(self, symbol: str) -> dict:
        """Получает 24‑часовой объём и открытый интерес."""
        ticker_url = f"{self.REST_URL}/v5/market/tickers"
        t_params: dict[str, str] = {"category": "linear", "symbol": symbol}
        ticker = await self._request("GET", ticker_url, params=t_params)
        tick = (ticker.get("result", {}).get("list") or [{}])[0]
        volume = float(tick.get("turnover24h", 0.0))
        oi_url = f"{self.REST_URL}/v5/market/open-interest"
        oi_params: dict[str, str] = {"category": "linear", "symbol": symbol}
        oi = await self._request("GET", oi_url, params=oi_params)
        oi_list = oi.get("result", {}).get("list") or [{}]
        open_interest = float(oi_list[0].get("openInterest", 0.0))
        return {"volume_24h": volume, "open_interest": open_interest}

    async def get_ohlc(
        self, symbol: str, interval: str, limit: int = 1
    ) -> list[Dict[str, float]]:
        """Возвращает свечи OHLC."""
        url = f"{self.REST_URL}/v5/market/kline"
        params: dict[str, str | int] = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        data = await self._request("GET", url, params=params)
        klist = data.get("result", {}).get("list", [])
        return [
            {
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            }
            for k in klist
        ]

    # ------------------------------------------------------------------
    # REST-методы спота
    # ------------------------------------------------------------------

    async def place_spot_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Отправляет спотовый ордер."""
        url_path = "/v5/order/create"
        url = f"{self.REST_URL}{url_path}"
        body: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "qty": quantity,
            "orderType": "Market" if price is None else "Limit",
            "category": "spot",
        }
        if price is not None:
            body["price"] = price
        headers = {"Content-Type": "application/json"}
        body = self._sign("POST", url_path, body)
        return await self._request("POST", url, json=body, headers=headers)

    async def get_spot_balance(self) -> dict:
        """Получает баланс спотового аккаунта."""
        url_path = "/v5/account/wallet-balance"
        url = f"{self.REST_URL}{url_path}"
        params: Dict[str, Any] = self._sign(
            "GET", url_path, {"accountType": "SPOT"}
        )
        return await self._request("GET", url, params=params)

    # ------------------------------------------------------------------
    # Обработка спотового WebSocket
    # ------------------------------------------------------------------

    async def _connect_spot(self, symbol: str) -> Any:
        """Подключается к спотовому WebSocket потоку стакана."""
        while True:
            try:
                ws = await websockets.connect(self.SPOT_WS_URL)
                sub = {"op": "subscribe", "args": [f"orderbook.1.{symbol}"]}
                await ws.send(json.dumps(sub))
                return ws
            except Exception:
                await asyncio.sleep(5)

    async def _listen_spot(self, symbol: str) -> None:
        """Слушает обновления спотового стакана и кэширует их."""
        while True:
            try:
                if self._spot_ws is None:
                    self._spot_ws = await self._connect_spot(symbol)
                msg = await self._spot_ws.recv()
                data = json.loads(msg)
                if data.get("topic", "").startswith("orderbook"):
                    book = data.get("data") or {}
                    self._spot_orderbooks[symbol] = {
                        "bids": book.get("b", []),
                        "asks": book.get("a", []),
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
        """Возвращает кэшированный спотовый стакан."""
        if symbol not in self._spot_ws_tasks:
            self._spot_ws_tasks[symbol] = asyncio.create_task(
                self._listen_spot(symbol)
            )
        return self._spot_orderbooks.get(symbol, {"bids": [], "asks": []})

    # ------------------------------------------------------------------
    # Обработка WebSocket
    # ------------------------------------------------------------------

    async def _connect(self, symbol: str) -> Any:
        """Создаёт WebSocket‑соединение для фьючерсного стакана."""
        while True:
            try:
                ws = await websockets.connect(self.WS_URL)
                sub = {
                    "op": "subscribe",
                    "args": [f"orderbook.1.{symbol}"],
                }
                await ws.send(json.dumps(sub))
                return ws
            except Exception:
                await asyncio.sleep(5)

    async def _listen(self, symbol: str) -> None:
        """Слушает поток фьючерсного стакана и сохраняет его."""
        while True:
            try:
                if self._ws is None:
                    self._ws = await self._connect(symbol)
                msg = await self._ws.recv()
                data = json.loads(msg)
                if data.get("topic", "").startswith("orderbook"):
                    book = data.get("data") or {}
                    self._orderbooks[symbol] = {
                        "bids": book.get("b", []),
                        "asks": book.get("a", []),
                    }
            except Exception:
                # Ошибка — перезапускаем соединение
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
        """Закрывает все активные соединения при выходе."""
        if self._session is not None:
            await self._session.close()
        if self._ws is not None:
            await self._ws.close()
        if self._spot_ws is not None:
            await self._spot_ws.close()


# Регистрация биржи
register("bybit", BybitExchange)
