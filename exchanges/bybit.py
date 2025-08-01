"""Bybit exchange implementation."""
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
    """Minimal Bybit client supporting REST and WebSocket operations."""

    REST_URL = "https://api.bybit.com"
    WS_URL = "wss://stream.bybit.com/v5/public/linear"
    SPOT_WS_URL = "wss://stream.bybit.com/v5/public/spot"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        # Spot websocket management
        self._spot_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._spot_orderbooks: Dict[str, Dict[str, Any]] = {}
        self._spot_ws_tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # REST utilities
    # ------------------------------------------------------------------

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    def _sign(self, method: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params.copy()
        params["api_key"] = self.api_key
        params["timestamp"] = int(time.time() * 1000)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign_str = method + path + query
        signature = hmac.new(
            self.api_secret.encode(), sign_str.encode(), hashlib.sha256
        ).hexdigest()
        params["sign"] = signature
        return params

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def fetch_funding(self, symbol: str) -> float:
        session = await self._session_get()
        url = f"{self.REST_URL}/v5/market/funding/history"
        params = {"symbol": symbol, "limit": 1}
        async with session.get(url, params=params) as resp:
            data = await resp.json()
        return float(data["result"]["list"][0]["fundingRate"])

    async def fetch_funding_history(
        self, symbol: str, hours: int = 8, limit: int = 3
    ) -> list[float]:
        session = await self._session_get()
        url = f"{self.REST_URL}/v5/market/funding/history"
        end_time = int(time.time() * 1000)
        start_time = end_time - hours * 3600 * 1000
        params = {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": limit}
        async with session.get(url, params=params) as resp:
            data = await resp.json()
        records = data.get("result", {}).get("list", [])
        return [float(item.get("fundingRate", 0.0)) for item in records]

    async def place_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        session = await self._session_get()
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
        async with session.post(url, json=body, headers=headers) as resp:
            return await resp.json()

    async def get_balance(self) -> dict:
        session = await self._session_get()
        url_path = "/v5/account/wallet-balance"
        url = f"{self.REST_URL}{url_path}"
        params = self._sign("GET", url_path, {"accountType": "UNIFIED"})
        async with session.get(url, params=params) as resp:
            return await resp.json()

    async def get_stats(self, symbol: str) -> dict:
        session = await self._session_get()
        ticker_url = f"{self.REST_URL}/v5/market/tickers"
        t_params = {"category": "linear", "symbol": symbol}
        async with session.get(ticker_url, params=t_params) as resp:
            ticker = await resp.json()
        tick = (ticker.get("result", {}).get("list") or [{}])[0]
        volume = float(tick.get("turnover24h", 0.0))
        oi_url = f"{self.REST_URL}/v5/market/open-interest"
        oi_params = {"category": "linear", "symbol": symbol}
        async with session.get(oi_url, params=oi_params) as resp:
            oi = await resp.json()
        oi_list = oi.get("result", {}).get("list") or [{}]
        open_interest = float(oi_list[0].get("openInterest", 0.0))
        return {"volume_24h": volume, "open_interest": open_interest}

    # ------------------------------------------------------------------
    # Spot REST methods
    # ------------------------------------------------------------------

    async def place_spot_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        session = await self._session_get()
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
        async with session.post(url, json=body, headers=headers) as resp:
            return await resp.json()

    async def get_spot_balance(self) -> dict:
        session = await self._session_get()
        url_path = "/v5/account/wallet-balance"
        url = f"{self.REST_URL}{url_path}"
        params = self._sign("GET", url_path, {"accountType": "SPOT"})
        async with session.get(url, params=params) as resp:
            return await resp.json()

    # ------------------------------------------------------------------
    # Spot WebSocket handling
    # ------------------------------------------------------------------

    async def _connect_spot(self, symbol: str) -> websockets.WebSocketClientProtocol:
        while True:
            try:
                ws = await websockets.connect(self.SPOT_WS_URL)
                sub = {"op": "subscribe", "args": [f"orderbook.1.{symbol}"]}
                await ws.send(json.dumps(sub))
                return ws
            except Exception:
                await asyncio.sleep(5)

    async def _listen_spot(self, symbol: str) -> None:
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
                await asyncio.sleep(1)
                if self._spot_ws is not None:
                    try:
                        await self._spot_ws.close()
                    except Exception:
                        pass
                self._spot_ws = None

    async def get_spot_orderbook(self, symbol: str, depth: int = 5) -> dict:
        if symbol not in self._spot_ws_tasks:
            self._spot_ws_tasks[symbol] = asyncio.create_task(
                self._listen_spot(symbol)
            )
        return self._spot_orderbooks.get(symbol, {"bids": [], "asks": []})

    # ------------------------------------------------------------------
    # WebSocket handling
    # ------------------------------------------------------------------

    async def _connect(self, symbol: str) -> websockets.WebSocketClientProtocol:
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
                await asyncio.sleep(1)
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                self._ws = None

    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        if symbol not in self._ws_tasks:
            self._ws_tasks[symbol] = asyncio.create_task(self._listen(symbol))
        return self._orderbooks.get(symbol, {"bids": [], "asks": []})

    async def __aexit__(self, *exc_info: Any) -> None:  # pragma: no cover
        if self._session is not None:
            await self._session.close()
        if self._ws is not None:
            await self._ws.close()
        if self._spot_ws is not None:
            await self._spot_ws.close()


# Register exchange
register("bybit", BybitExchange)
