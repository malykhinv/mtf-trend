from __future__ import annotations

import hashlib
import hmac
import json
import socket
import time
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Any, Mapping, Optional
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from domain.models import BalanceSource, Exchange, ExecutionReport, MarginMode, Side, StopTrigger
from domain.trading_adapter import TradingAdapter
from utils.timez import from_exchange_timestamp, get_current_time


class BybitAPIError(RuntimeError):
    def __init__(self, message: str, *, code: Optional[int] = None, status_code: Optional[int] = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


@dataclass(slots=True)
class BybitTradeEndpoints:
    rest_base: str = "https://api.bybit.com"


class BybitTradingAdapter(TradingAdapter):
    def __init__(
        self,
        symbol: str,
        settle_coin: str,
        *,
        api_key: Optional[str],
        api_secret: Optional[str],
        recv_window: int = 5_000,
        timeout: float = 10.0,
        endpoints: Optional[BybitTradeEndpoints] = None,
        max_retries: int = 3,
        retry_delay: float = 0.5,
        retry_backoff: float = 2.0,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Bybit trading adapter requires API credentials")
        self._symbol = symbol.upper()
        self._settle_coin = settle_coin.upper()
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._recv_window = recv_window
        self._timeout = timeout
        self._endpoints = endpoints or BybitTradeEndpoints()
        self._category = "linear"
        self._max_retries = max(0, int(max_retries))
        self._retry_delay = max(0.0, float(retry_delay))
        self._retry_backoff = max(1.0, float(retry_backoff))

    def get_balance(self, source: BalanceSource) -> float:
        params = {
            "accountType": "UNIFIED",
            "coin": self._settle_coin,
        }
        data = self._signed_request("GET", "/v5/account/wallet-balance", params)
        result = data.get("result", {}) if isinstance(data, Mapping) else {}
        for account in result.get("list", []):
            if not isinstance(account, Mapping):
                continue
            for coin in account.get("coin", []):
                if not isinstance(coin, Mapping):
                    continue
                if coin.get("coin", "").upper() != self._settle_coin:
                    continue
                if source is BalanceSource.AVAILABLE:
                    return float(coin.get("availableToWithdraw", 0.0))
                if source is BalanceSource.WALLET:
                    return float(coin.get("walletBalance", 0.0))
        raise BybitAPIError(
            f"Balance for asset {self._settle_coin} not found",
            code=None,
        )

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        trade_mode = 1 if margin_mode is MarginMode.ISOLATED else 0
        body = {
            "category": self._category,
            "symbol": self._symbol,
            "tradeMode": trade_mode,
        }
        if trade_mode == 1:
            body["buyLeverage"] = str(leverage)
            body["sellLeverage"] = str(leverage)
        self._signed_request("POST", "/v5/position/switch-isolated", body)
        self._signed_request(
            "POST",
            "/v5/position/set-leverage",
            {
                "category": self._category,
                "symbol": self._symbol,
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
        )
        return leverage

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        body: dict[str, Any] = {
            "category": self._category,
            "symbol": self._symbol,
            "side": self._map_side(side),
            "orderType": "Market",
            "qty": self._format_decimal(quantity),
            "timeInForce": "IOC",
        }
        client_id = self._build_client_id(reason)
        if client_id:
            body["orderLinkId"] = client_id
        data = self._signed_request("POST", "/v5/order/create", body)
        result = data.get("result", {}) if isinstance(data, Mapping) else {}
        order_id = str(result.get("orderId", ""))
        return self._fetch_execution_report(order_id, side, quantity)

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        trigger_by = "MarkPrice" if trigger is StopTrigger.MARK else "LastPrice"
        body: dict[str, Any] = {
            "category": self._category,
            "symbol": self._symbol,
            "side": self._map_side(side),
            "orderType": "Market",
            "qty": self._format_decimal(quantity),
            "triggerPrice": self._format_decimal(stop_price),
            "triggerBy": trigger_by,
            "timeInForce": "GTC",
            "reduceOnly": True,
        }
        self._signed_request("POST", "/v5/order/create", body)

    def _fetch_execution_report(
        self,
        order_id: str,
        side: Side,
        requested_qty: float,
    ) -> ExecutionReport:
        if not order_id:
            executed_at = get_current_time()
            return ExecutionReport(
                exchange=Exchange.BYBIT,
                symbol=self._symbol,
                order_id="",
                side=side,
                price=0.0,
                quantity=requested_qty,
                executed_qty=0.0,
                status="unknown",
                commission=0.0,
                executed_at=executed_at,
            )
        params = {
            "category": self._category,
            "orderId": order_id,
        }
        data = self._signed_request("GET", "/v5/execution/list", params)
        result = data.get("result", {}) if isinstance(data, Mapping) else {}
        executions = [
            exec_item
            for exec_item in result.get("list", [])
            if isinstance(exec_item, Mapping)
        ]
        if not executions:
            executed_at = get_current_time()
            return ExecutionReport(
                exchange=Exchange.BYBIT,
                symbol=self._symbol,
                order_id=order_id,
                side=side,
                price=0.0,
                quantity=requested_qty,
                executed_qty=0.0,
                status="unknown",
                commission=0.0,
                executed_at=executed_at,
            )
        total_qty = sum(float(item.get("execQty", 0.0)) for item in executions)
        if total_qty <= 0.0:
            executed_price = 0.0
        else:
            total_notional = sum(
                float(item.get("execQty", 0.0)) * float(item.get("execPrice", 0.0))
                for item in executions
            )
            executed_price = total_notional / total_qty
        commission = sum(float(item.get("execFee", 0.0)) for item in executions)
        last_exec = executions[-1]
        status = str(last_exec.get("orderStatus", "Filled")).lower()
        exec_time_raw = last_exec.get("execTime")
        if exec_time_raw is None:
            executed_at = get_current_time()
        else:
            executed_at = from_exchange_timestamp(float(exec_time_raw) / 1000.0)
        quantity = float(last_exec.get("orderQty", requested_qty))
        return ExecutionReport(
            exchange=Exchange.BYBIT,
            symbol=self._symbol,
            order_id=order_id,
            side=side,
            price=executed_price,
            quantity=quantity,
            executed_qty=total_qty,
            status=status,
            commission=commission,
            executed_at=executed_at,
        )

    def _signed_request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any] | list[Any]:
        method = method.upper()
        params = dict(params or {})
        timestamp = int(time.time() * 1000)
        recv_window = self._recv_window
        url = f"{self._endpoints.rest_base}{path}"
        body = ""
        query = ""
        if method == "GET" and params:
            query = self._encode_query(params)
            url = f"{url}?{query}"
        elif method in {"POST", "DELETE"}:
            body = json.dumps(params, separators=(",", ":")) if params else "{}"
        sign_target = f"{timestamp}{self._api_key}{recv_window}{body or query}"
        signature = hmac.new(self._api_secret, sign_target.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {
            "User-Agent": "mtf-trend/1.0",
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-RECV-WINDOW": str(recv_window),
            "X-BAPI-SIGN": signature,
        }
        data_bytes = body.encode("utf-8") if body else None
        request = Request(url, data=data_bytes, headers=headers, method=method)
        payload = self._perform_request(request)
        data = json.loads(payload)
        if isinstance(data, Mapping) and int(data.get("retCode", 0)) != 0:
            raise BybitAPIError(
                str(data.get("retMsg", "unknown error")),
                code=int(data.get("retCode", 0)),
            )
        return data

    def _perform_request(self, request: Request) -> str:
        retries_remaining = self._max_retries
        delay = self._retry_delay
        while True:
            try:
                with urlopen(request, timeout=self._timeout) as response:
                    return response.read().decode("utf-8")
            except HTTPError as error:
                payload = error.read().decode("utf-8")
                raise self._translate_error(payload, status_code=error.code)
            except (
                URLError,
                RemoteDisconnected,
                TimeoutError,
                socket.timeout,
                ConnectionError,
            ) as error:
                if retries_remaining <= 0:
                    message = self._format_network_error(error)
                    raise BybitAPIError(message, code=None) from error
                if delay > 0.0:
                    time.sleep(delay)
                retries_remaining -= 1
                delay *= self._retry_backoff

    @staticmethod
    def _format_network_error(error: BaseException) -> str:
        reason = getattr(error, "reason", None)
        if isinstance(reason, Exception):
            text = str(reason)
        elif reason is not None:
            text = str(reason)
        else:
            text = str(error)
        text = (text or "").strip() or error.__class__.__name__
        return f"Connection error: {text}"

    @staticmethod
    def _translate_error(payload: str, *, status_code: Optional[int] = None) -> BybitAPIError:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return BybitAPIError(payload or "HTTP error", status_code=status_code)
        message = str(data.get("retMsg", "HTTP error"))
        code = data.get("retCode")
        if code is not None:
            try:
                parsed_code = int(code)
            except (TypeError, ValueError):
                parsed_code = None
        else:
            parsed_code = None
        return BybitAPIError(message, code=parsed_code, status_code=status_code)

    @staticmethod
    def _encode_query(params: Mapping[str, Any]) -> str:
        ordered = sorted((str(key), str(params[key])) for key in params)
        return parse.urlencode(ordered)

    @staticmethod
    def _map_side(side: Side) -> str:
        if side is Side.BID:
            return "Buy"
        if side is Side.ASK:
            return "Sell"
        raise ValueError("Unsupported side")

    @staticmethod
    def _format_decimal(value: float) -> str:
        text = format(value, "f")
        if "e" in text or "E" in text:
            text = f"{value:.8f}"
        return text.rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _build_client_id(reason: Optional[str]) -> Optional[str]:
        if not reason:
            return None
        sanitized = "".join(ch for ch in reason if ch.isalnum() or ch in {"-", "_"})
        return sanitized[:36] or None


__all__ = ["BybitTradingAdapter", "BybitTradeEndpoints", "BybitAPIError"]

