from __future__ import annotations

import hmac
import time
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
from typing import Any, Dict, Tuple

import httpx

from config.credentials import BINANCE
from constants import BINANCE_FAPI_REST
from domain.models.enums import OrderType
from domain.models.trading import OrderSpec


class Trader:
    """Minimal wrapper for Binance Futures trading endpoints."""

    _ORDER_ENDPOINT = "/fapi/v1/order"
    _CANCEL_ALL_ENDPOINT = "/fapi/v1/allOpenOrders"
    _ACCOUNT_ENDPOINT = "/fapi/v2/account"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=BINANCE_FAPI_REST, timeout=10.0)

    async def _signed_request(
        self, method: str, endpoint: str, params: Dict[str, str]
    ) -> httpx.Response:
        """Send a signed request to a Binance endpoint."""

        ts = int(time.time() * 1000)
        params["timestamp"] = str(ts)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            BINANCE.api_secret.encode("utf-8"), query.encode("utf-8"), sha256
        ).hexdigest()
        headers = {"X-MBX-APIKEY": BINANCE.api_key}
        url = f"{endpoint}?{query}&signature={signature}"
        return await self._client.request(method, url, headers=headers)

    async def _get_filters(self, symbol: str) -> Tuple[Decimal, Decimal]:
        """Fetch price tick and lot size filters for ``symbol``."""

        info = await self._client.get(
            "/fapi/v1/exchangeInfo", params={"symbol": symbol}
        )
        info.raise_for_status()
        data = info.json()["symbols"][0]["filters"]
        filters = {f["filterType"]: f for f in data}
        tick_size = Decimal(filters["PRICE_FILTER"]["tickSize"])
        step_size = Decimal(filters["LOT_SIZE"]["stepSize"])
        return tick_size, step_size

    async def place(self, order: OrderSpec) -> dict[str, Any] | None:  # pragma: no cover - network
        """Submit an order to the exchange."""

        tick_size, step_size = await self._get_filters(order.symbol)

        quantity = Decimal(str(order.quantity)).quantize(
            step_size, rounding=ROUND_DOWN
        )
        params: Dict[str, str] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.type.value,
            "quantity": f"{quantity}",
        }
        if order.type is OrderType.LIMIT and order.price is not None:
            price = Decimal(str(order.price)).quantize(
                tick_size, rounding=ROUND_DOWN
            )
            params["price"] = f"{price}"
            params["timeInForce"] = "GTC"

        response = await self._signed_request("POST", self._ORDER_ENDPOINT, params)
        response.raise_for_status()
        return response.json()

    async def cancel(
        self, symbol: str, order_id: int | None
    ) -> None:  # pragma: no cover - network
        """Cancel an order or all orders for ``symbol``."""

        if order_id is None:
            endpoint = self._CANCEL_ALL_ENDPOINT
            params = {"symbol": symbol}
        else:
            endpoint = self._ORDER_ENDPOINT
            params = {"symbol": symbol, "orderId": str(order_id)}

        response = await self._signed_request("DELETE", endpoint, params)
        response.raise_for_status()

    async def get_balance_usdt(self) -> float:
        """Fetch available USDT balance."""

        resp = await self._signed_request("GET", self._ACCOUNT_ENDPOINT, {})
        resp.raise_for_status()
        data = resp.json()
        for asset in data.get("assets", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("availableBalance", 0.0))
        return 0.0
