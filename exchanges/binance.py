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
from websockets.exceptions import WebSocketException

from . import BaseExchange, register


class BinanceExchange(BaseExchange):
    """Минимальный клиент Binance с поддержкой REST и WebSocket."""

    REST_URL = "https://fapi.binance.com"
    WS_URL = "wss://fstream.binance.com/ws"
    SPOT_REST_URL = "https://api.binance.com"
    SPOT_WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(self, api_key: str, api_secret: str, **_ignored: Any) -> None:
        """Инициализация клиента.

        Параметры
        ----------
        api_key, api_secret:
            Ключ и секрет API для авторизации на бирже.
        """
        super().__init__(**_ignored)
        self.name = "binance"
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None
        # WebSocket соединения для фьючерсных стаканов по каждому символу
        self._ws: Dict[str, Any] = {}
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        # Обработка WebSocket для спота по каждому символу
        self._spot_ws: Dict[str, Any] = {}
        self._spot_orderbooks: Dict[str, Dict[str, Any]] = {}
        self._spot_ws_tasks: Dict[str, asyncio.Task] = {}
        # Соответствие ``orderId`` -> ``symbol`` для запросов статуса ордера
        self._order_symbols: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Утилиты REST
    # ------------------------------------------------------------------

    async def _session_get(self) -> aiohttp.ClientSession:
        """Создаёт ``ClientSession`` при первом обращении."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _request(
        self, method: str, url: str, retries: int = 3, **kwargs: Any
    ) -> Any:
        """Выполняет HTTP-запрос с повторными попытками.

        При статусе ответа вне диапазона ``200-299`` возбуждает исключение
        ``aiohttp.ClientResponseError``. В случае ошибок соединения или
        ответов сервера ``5xx`` выполняет ограниченное число повторов с
        экспоненциальной задержкой.
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
                    # Повторяем при ошибках сервера
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
        return None

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
        url = f"{self.REST_URL}/fapi/v1/fundingRate"
        params: dict[str, str | int] = {"symbol": symbol, "limit": 1}
        data = await self._request("GET", url, params=params)
        return float(data[0]["fundingRate"])

    async def fetch_funding_history(
        self, symbol: str, hours: int = 8, limit: int = 3
    ) -> list[float]:
        """Возвращает историю ставок фондирования за заданный период."""
        url = f"{self.REST_URL}/fapi/v1/fundingRate"
        end_time = int(time.time() * 1000)
        start_time = end_time - hours * 3600 * 1000
        params: dict[str, int | str] = {
            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit,
        }
        data = await self._request("GET", url, params=params)
        return [float(entry["fundingRate"]) for entry in data]

    async def place_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Размещает фьючерсный ордер на бирже."""
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
        data = await self._request("POST", url, params=params, headers=headers)
        order_id = str(data.get("orderId") or data.get("id") or "")
        if order_id:
            self._order_symbols[order_id] = symbol
        return data

    async def get_order_status(self, order_id: str) -> dict:
        """Возвращает статус ордера ``order_id``.

        Сначала выполняет запрос к фьючерсному API. Если ордер не найден или
        запрос завершился ошибкой, повторяет попытку для спотового API.
        Повторные попытки сетевых обращений обрабатываются методом
        :meth:`_request`.
        """

        headers = {"X-MBX-APIKEY": self.api_key}
        symbol = self._order_symbols.get(order_id)
        params: Dict[str, Any] = {"orderId": order_id}
        if symbol:
            params["symbol"] = symbol
        params = self._sign(params)
        url = f"{self.REST_URL}/fapi/v1/order"
        try:
            data = await self._request("GET", url, params=params, headers=headers)
        except aiohttp.ClientError:
            spot_url = f"{self.SPOT_REST_URL}/api/v3/order"
            params = {"orderId": order_id}
            if symbol:
                params["symbol"] = symbol
            params = self._sign(params)
            data = await self._request(
                "GET", spot_url, params=params, headers=headers
            )
        self._order_symbols.pop(order_id, None)
        return data

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Отменяет ордер по ``order_id`` для указанного ``symbol``.

        Пытается отменить фьючерсный ордер. При неудаче повторяет попытку для
        спотового рынка, что позволяет использовать метод для обоих типов
        ордеров.
        """

        headers = {"X-MBX-APIKEY": self.api_key}
        params = self._sign({"symbol": symbol, "orderId": order_id})
        url = f"{self.REST_URL}/fapi/v1/order"
        try:
            data = await self._request("DELETE", url, params=params, headers=headers)
        except aiohttp.ClientError:
            # Попытка отменить спотовый ордер
            spot_url = f"{self.SPOT_REST_URL}/api/v3/order"
            params = self._sign({"symbol": symbol, "orderId": order_id})
            data = await self._request("DELETE", spot_url, params=params, headers=headers)
        self._order_symbols.pop(order_id, None)
        return data

    async def get_balance(self) -> dict:
        """Возвращает баланс фьючерсного аккаунта."""
        url = f"{self.REST_URL}/fapi/v2/balance"
        params = self._sign({})
        headers = {"X-MBX-APIKEY": self.api_key}
        return await self._request("GET", url, params=params, headers=headers)

    async def get_stats(self, symbol: str) -> dict:
        """Получает 24‑часовой объём и открытый интерес для ``symbol``."""
        ticker_url = f"{self.REST_URL}/fapi/v1/ticker/24hr?symbol={symbol}"
        ticker = await self._request("GET", ticker_url)
        # ``volume`` в базовой валюте; используем ``quoteVolume`` для USD
        volume = float(ticker.get("quoteVolume", ticker.get("volume", 0.0)))
        oi_url = f"{self.REST_URL}/fapi/v1/openInterest?symbol={symbol}"
        oi = await self._request("GET", oi_url)
        open_interest = float(oi.get("openInterest", 0.0))
        return {"volume_24h": volume, "open_interest": open_interest}

    async def get_ohlc(
        self, symbol: str, interval: str, limit: int = 1
    ) -> list[Dict[str, float]]:
        """Возвращает свечи OHLC для ``symbol``."""
        url = f"{self.REST_URL}/fapi/v1/klines"
        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        data = await self._request("GET", url, params=params)
        return [
            {
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            }
            for k in data
        ]

    async def get_futures_symbols(self) -> list[str]:
        """Возвращает список фьючерсных символов Binance."""
        url = f"{self.REST_URL}/fapi/v1/exchangeInfo"
        data = await self._request("GET", url)
        return [s.get("symbol", "") for s in data.get("symbols", [])]

    async def get_spot_symbols(self) -> list[str]:
        """Возвращает список спотовых символов Binance."""
        url = f"{self.SPOT_REST_URL}/api/v3/exchangeInfo"
        data = await self._request("GET", url)
        return [s.get("symbol", "") for s in data.get("symbols", [])]

    # ------------------------------------------------------------------
    # REST-методы спота
    # ------------------------------------------------------------------

    async def place_spot_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Размещает спотовый ордер."""
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
        data = await self._request("POST", url, params=params, headers=headers)
        order_id = str(data.get("orderId") or data.get("id") or "")
        if order_id:
            self._order_symbols[order_id] = symbol
        return data

    async def get_spot_balance(self) -> dict:
        """Возвращает баланс спотового аккаунта."""
        url = f"{self.SPOT_REST_URL}/api/v3/account"
        params = self._sign({})
        headers = {"X-MBX-APIKEY": self.api_key}
        return await self._request("GET", url, params=params, headers=headers)

    # ------------------------------------------------------------------
    # Обработка спотового WebSocket
    # ------------------------------------------------------------------

    async def _connect_spot(
        self, symbol: str, depth: int
    ) -> Any:
        """Подключается к спотовому WebSocket и возвращает соединение."""
        while True:
            try:
                return await websockets.connect(
                    f"{self.SPOT_WS_URL}/{symbol.lower()}@depth{depth}@100ms"
                )
            except (WebSocketException, OSError):
                await asyncio.sleep(5)

    async def _listen_spot(self, symbol: str, depth: int) -> None:
        """Слушает поток спотового order book и кэширует данные."""
        while True:
            try:
                if symbol not in self._spot_ws:
                    self._spot_ws[symbol] = await self._connect_spot(symbol, depth)
                msg = await self._spot_ws[symbol].recv()
                data = json.loads(msg)
                if "bids" in data and "asks" in data:
                    self._spot_orderbooks[symbol] = {
                        "bids": data["bids"],
                        "asks": data["asks"],
                    }
            except (WebSocketException, json.JSONDecodeError, OSError):
                # При ошибке пересоздаём соединение для конкретного символа
                await asyncio.sleep(1)
                ws = self._spot_ws.pop(symbol, None)
                if ws is not None:
                    try:
                        await ws.close()
                    except WebSocketException:
                        pass

    async def _ensure_orderbook(self, symbol: str, depth: int, spot: bool = False) -> None:
        """Гарантирует наличие снимка стакана в кэше.

        Если для ``symbol`` ещё нет данных, выполняет REST‑запрос ``/depth``
        (для фьючерсов) или ``/api/v3/depth`` (для спота) и сохраняет результат
        в соответствующий кэш. При неудаче создаёт пустую запись, чтобы
        последующие вызовы возвращали пустой стакан.
        """

        cache = self._spot_orderbooks if spot else self._orderbooks
        book = cache.get(symbol)
        if book and book.get("bids") and book.get("asks"):
            return

        url = (
            f"{self.SPOT_REST_URL}/api/v3/depth"
            if spot
            else f"{self.REST_URL}/fapi/v1/depth"
        )
        params = {"symbol": symbol, "limit": depth}
        try:
            data = await asyncio.wait_for(
                self._request("GET", url, params=params), timeout=5
            )
        except Exception:
            cache.setdefault(symbol, {"bids": [], "asks": []})
            return

        if data and "bids" in data and "asks" in data:
            cache[symbol] = {"bids": data["bids"], "asks": data["asks"]}
        else:
            cache.setdefault(symbol, {"bids": [], "asks": []})

    async def get_spot_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Возвращает кэшированный спотовый стакан для ``symbol``."""
        if symbol not in self._spot_ws_tasks:
            self._spot_ws_tasks[symbol] = asyncio.create_task(
                self._listen_spot(symbol, depth)
            )
        await self._ensure_orderbook(symbol, depth, spot=True)
        return self._spot_orderbooks.get(symbol, {"bids": [], "asks": []})

    # ------------------------------------------------------------------
    # Обработка WebSocket
    # ------------------------------------------------------------------

    async def _connect(self, symbol: str) -> Any:
        """Создаёт WebSocket‑соединение для фьючерсного стакана."""
        while True:
            try:
                return await websockets.connect(
                    f"{self.WS_URL}/{symbol.lower()}@depth5@100ms"
                )
            except (WebSocketException, OSError):
                await asyncio.sleep(5)

    async def _listen(self, symbol: str) -> None:
        """Слушает поток фьючерсного стакана и сохраняет его в кэше."""
        while True:
            try:
                if symbol not in self._ws:
                    self._ws[symbol] = await self._connect(symbol)
                msg = await self._ws[symbol].recv()
                data = json.loads(msg)
                if "bids" in data and "asks" in data:
                    self._orderbooks[symbol] = {
                        "bids": data["bids"],
                        "asks": data["asks"],
                    }
            except (WebSocketException, json.JSONDecodeError, OSError):
                # При ошибке пробуем подключиться заново для данного символа
                await asyncio.sleep(1)
                ws = self._ws.pop(symbol, None)
                if ws is not None:
                    try:
                        await ws.close()
                    except WebSocketException:
                        pass

    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Возвращает кэшированный фьючерсный стакан."""
        if symbol not in self._ws_tasks:
            self._ws_tasks[symbol] = asyncio.create_task(self._listen(symbol))
        await self._ensure_orderbook(symbol, depth)
        return self._orderbooks.get(symbol, {"bids": [], "asks": []})

    async def close(self) -> None:
        """Закрывает HTTP-сессию и все WebSocket соединения."""
        await super().close()
        if self._session is not None:
            await self._session.close()
            self._session = None

        for task in list(self._ws_tasks.values()) + list(self._spot_ws_tasks.values()):
            task.cancel()
        await asyncio.gather(
            *self._ws_tasks.values(),
            *self._spot_ws_tasks.values(),
            return_exceptions=True,
        )
        self._ws_tasks.clear()
        self._spot_ws_tasks.clear()

        for ws in list(self._ws.values()):
            if ws is not None:
                try:
                    await ws.close()
                except WebSocketException:
                    pass
        for ws in list(self._spot_ws.values()):
            if ws is not None:
                try:
                    await ws.close()
                except WebSocketException:
                    pass
        self._ws.clear()
        self._spot_ws.clear()

    async def __aexit__(self, *exc_info: Any) -> None:  # pragma: no cover
        """Закрывает все сетевые соединения при выходе из контекста."""
        await self.close()


# Регистрация биржи
register("binance", BinanceExchange)
