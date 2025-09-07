from __future__ import annotations

import hmac
import time
from hashlib import sha256
from typing import Dict

import httpx

from config.credentials import BINANCE
from constants import BINANCE_FAPI_REST
from domain.models.trading import OrderSpec
from domain.models.enums import OrderType


class Trader:
    """Minimal wrapper for Binance Futures trading endpoints."""

    _ORDER_ENDPOINT = "/fapi/v1/order"
    _CANCEL_ALL_ENDPOINT = "/fapi/v1/allOpenOrders"

    def __init__(self) -> None:
        self._client = httpx.Client(base_url=BINANCE_FAPI_REST, timeout=10.0)

    def _signed_request(self, method: str, endpoint: str, params: Dict[str, str]) -> httpx.Response:
        ts = int(time.time() * 1000)
        params["timestamp"] = str(ts)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            BINANCE.api_secret.encode("utf-8"), query.encode("utf-8"), sha256
        ).hexdigest()
        headers = {"X-MBX-APIKEY": BINANCE.api_key}
        url = endpoint + f"?{query}&signature={signature}"
        return self._client.request(method, url, headers=headers)

    def place(self, order: OrderSpec) -> None:  # pragma: no cover - network
        params: Dict[str, str] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.type.value,
            "quantity": f"{order.quantity}",
        }
        if order.type is OrderType.LIMIT and order.price is not None:
            params["price"] = f"{order.price}"
            params["timeInForce"] = "GTC"
        response = self._signed_request("POST", self._ORDER_ENDPOINT, params)
        response.raise_for_status()

    def cancel(self, symbol: str, order_id: int | None) -> None:  # pragma: no cover - network
        if order_id is None:
            endpoint = self._CANCEL_ALL_ENDPOINT
            params = {"symbol": symbol}
        else:
            endpoint = self._ORDER_ENDPOINT
            params = {"symbol": symbol, "orderId": str(order_id)}
        response = self._signed_request("DELETE", endpoint, params)
        response.raise_for_status()
