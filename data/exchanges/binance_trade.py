from __future__ import annotations

import hashlib
import hmac
import json
import logging
import socket
import time
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Any, Callable, Mapping, Optional
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from domain.models import BalanceSource, Exchange, ExecutionReport, MarginMode, Side, StopTrigger
from domain.trading_adapter import TradingAdapter
from utils.timez import from_exchange_timestamp, get_current_time


class BinanceAPIError(RuntimeError):
    def __init__(self, message: str, *, code: Optional[int] = None, status_code: Optional[int] = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


@dataclass(slots=True)
class BinanceTradeEndpoints:
    rest_base: str = "https://fapi.binance.com"


class BinanceTradingAdapter(TradingAdapter):
    def __init__(
        self,
        symbol: str,
        quote_asset: str,
        *,
        api_key: Optional[str],
        api_secret: Optional[str],
        recv_window: int = 5_000,
        timeout: float = 10.0,
        endpoints: Optional[BinanceTradeEndpoints] = None,
        max_retries: int = 3,
        retry_delay: float = 0.5,
        retry_backoff: float = 2.0,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Binance trading adapter requires API credentials")
        self._symbol = symbol.upper()
        self._quote_asset = quote_asset.upper()
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._recv_window = recv_window
        self._timeout = timeout
        self._endpoints = endpoints or BinanceTradeEndpoints()
        self._max_retries = max(0, int(max_retries))
        self._retry_delay = max(0.0, float(retry_delay))
        self._retry_backoff = max(1.0, float(retry_backoff))
        self._logger = logging.getLogger(__name__)
        self._time_offset_ms = 0

    def get_balance(self, source: BalanceSource) -> float:
        response = self._signed_request("GET", "/fapi/v2/balance")
        if not isinstance(response, list):
            raise BinanceAPIError("Unexpected balance payload", code=None)
        for entry in response:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("asset", "").upper() != self._quote_asset:
                continue
            if source is BalanceSource.AVAILABLE:
                return float(entry.get("availableBalance", 0.0))
            if source is BalanceSource.WALLET:
                return float(entry.get("walletBalance", 0.0))
        raise BinanceAPIError(
            f"Balance for asset {self._quote_asset} not found",
            code=None,
        )

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        margin_type = "ISOLATED" if margin_mode is MarginMode.ISOLATED else "CROSSED"
        try:
            self._signed_request(
                "POST",
                "/fapi/v1/marginType",
                {
                    "symbol": self._symbol,
                    "marginType": margin_type,
                },
            )
        except BinanceAPIError as error:
            # -4046 means the margin type is already set – ignore silently
            if error.code != -4046:
                raise
        try:
            self._signed_request(
                "POST",
                "/fapi/v1/leverage",
                {
                    "symbol": self._symbol,
                    "leverage": leverage,
                },
            )
            return leverage
        except BinanceAPIError as error:
            if not self._is_invalid_leverage_error(error):
                raise
            bounds = self._resolve_leverage_bounds()
            if bounds is None:
                self._logger.warning(
                    "Binance rejected leverage %s for %s (%s). Unable to fetch leverage bounds.",
                    leverage,
                    self._symbol,
                    error,
                )
                return None
            min_leverage, max_leverage = bounds
            adjusted = min(max(leverage, min_leverage), max_leverage)
            if adjusted == leverage:
                self._logger.warning(
                    "Binance rejected leverage %s for %s despite being within bounds [%s, %s]: %s",
                    leverage,
                    self._symbol,
                    min_leverage,
                    max_leverage,
                    error,
                )
                return None
            try:
                self._signed_request(
                    "POST",
                    "/fapi/v1/leverage",
                    {
                        "symbol": self._symbol,
                        "leverage": adjusted,
                    },
                )
                self._logger.warning(
                    "Adjusted leverage for %s from %s to nearest supported value %s.",
                    self._symbol,
                    leverage,
                    adjusted,
                )
                return adjusted
            except BinanceAPIError as adjusted_error:
                self._logger.warning(
                    "Failed to set adjusted leverage %s for %s: %s",
                    adjusted,
                    self._symbol,
                    adjusted_error,
                )
                return None

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        params: dict[str, Any] = {
            "symbol": self._symbol,
            "side": self._map_side(side),
            "type": "MARKET",
            "quantity": self._format_decimal(quantity),
        }
        client_id = self._build_client_id(reason)
        if client_id:
            params["newClientOrderId"] = client_id
        payload = self._signed_request("POST", "/fapi/v1/order", params)
        return self._build_execution_report(payload, side)

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        working_type = "MARK_PRICE" if trigger is StopTrigger.MARK else "CONTRACT_PRICE"
        params: dict[str, Any] = {
            "symbol": self._symbol,
            "side": self._map_side(side),
            "type": "STOP_MARKET",
            "stopPrice": self._format_decimal(stop_price),
            "quantity": self._format_decimal(quantity),
            "workingType": working_type,
            "timeInForce": "GTC",
        }
        self._signed_request("POST", "/fapi/v1/order", params)

    def _build_execution_report(self, payload: Mapping[str, Any], side: Side) -> ExecutionReport:
        order_id = str(payload.get("orderId", ""))
        price = float(payload.get("avgPrice") or payload.get("price") or 0.0)
        quantity = float(payload.get("origQty", 0.0))
        executed_qty = float(payload.get("executedQty", quantity))
        status = str(payload.get("status", "UNKNOWN")).lower()
        update_time = payload.get("updateTime") or payload.get("transactTime")
        if update_time:
            executed_at = from_exchange_timestamp(float(update_time) / 1000.0)
        else:
            executed_at = get_current_time()
        return ExecutionReport(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            order_id=order_id,
            side=side,
            price=price,
            quantity=quantity,
            executed_qty=executed_qty,
            status=status,
            commission=float(payload.get("cumQuote", 0.0)),
            executed_at=executed_at,
        )

    def _signed_request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        params = dict(params or {})
        params.setdefault("recvWindow", self._recv_window)

        def build_request() -> Request:
            final_params = dict(params)
            final_params.setdefault("timestamp", self._current_timestamp_ms())
            query = parse.urlencode(final_params)
            signature = hmac.new(self._api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
            signed_query = f"{query}&signature={signature}"
            url = f"{self._endpoints.rest_base}{path}?{signed_query}"
            return Request(
                url,
                method=method.upper(),
                headers={
                    "User-Agent": "mtf-trend/1.0",
                    "X-MBX-APIKEY": self._api_key,
                },
            )

        request = build_request()
        raw = self._perform_request(request, rebuild_signed_request=build_request)
        data = json.loads(raw)
        if isinstance(data, Mapping) and "code" in data and data.get("code") not in (0, 200):
            raise BinanceAPIError(
                str(data.get("msg", "unknown error")),
                code=int(data.get("code", 0)),
            )
        return data

    def _perform_request(
        self,
        request: Request,
        *,
        rebuild_signed_request: Optional[Callable[[], Request]] = None,
    ) -> str:
        retries_remaining = self._max_retries
        delay = self._retry_delay
        time_sync_attempted = False
        while True:
            try:
                with urlopen(request, timeout=self._timeout) as response:
                    return response.read().decode("utf-8")
            except HTTPError as error:
                payload = error.read().decode("utf-8")
                api_error = self._translate_error(payload, status_code=error.code)
                if (
                    not time_sync_attempted
                    and rebuild_signed_request is not None
                    and self._is_recv_window_error(api_error)
                ):
                    time_sync_attempted = True
                    try:
                        self._sync_server_time()
                    except Exception as sync_error:  # pragma: no cover - defensive logging
                        self._logger.warning(
                            "Failed to synchronize Binance server time after recvWindow error: %s",
                            sync_error,
                        )
                    else:
                        request = rebuild_signed_request()
                        continue
                raise api_error
            except (
                URLError,
                RemoteDisconnected,
                TimeoutError,
                socket.timeout,
                socket.gaierror,
                ConnectionError,
                OSError,
            ) as error:
                if retries_remaining <= 0:
                    message = self._format_network_error(error)
                    raise BinanceAPIError(message, code=None) from error
                if delay > 0.0:
                    time.sleep(delay)
                retries_remaining -= 1
                delay *= self._retry_backoff

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000 + self._time_offset_ms)

    def _sync_server_time(self) -> None:
        request = Request(
            f"{self._endpoints.rest_base}/fapi/v1/time",
            method="GET",
            headers={"User-Agent": "mtf-trend/1.0"},
        )
        raw = self._perform_request(request)
        data = json.loads(raw)
        server_time = data.get("serverTime") if isinstance(data, Mapping) else None
        if server_time is None:
            raise BinanceAPIError("Unable to fetch Binance server time", code=None)
        try:
            server_timestamp = int(server_time)
        except (TypeError, ValueError) as error:
            raise BinanceAPIError("Invalid Binance server time payload", code=None) from error
        local_timestamp = int(time.time() * 1000)
        self._time_offset_ms = server_timestamp - local_timestamp
        self._logger.info(
            "Synchronized Binance server time; applying offset %s ms.",
            self._time_offset_ms,
        )

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
    def _is_invalid_leverage_error(error: BinanceAPIError) -> bool:
        message = (str(error) or "").lower()
        return "leverage" in message and "not valid" in message

    @staticmethod
    def _is_recv_window_error(error: BinanceAPIError) -> bool:
        message = (str(error) or "").lower()
        return "recvwindow" in message or (error.code == -1021)

    def _resolve_leverage_bounds(self) -> Optional[tuple[int, int]]:
        try:
            payload = self._signed_request(
                "GET",
                "/fapi/v2/leverageBracket",
                {"symbol": self._symbol},
            )
        except BinanceAPIError as error:
            self._logger.warning(
                "Unable to fetch Binance leverage brackets for %s: %s",
                self._symbol,
                error,
            )
            return None
        brackets: list[int] = []
        entries = payload if isinstance(payload, list) else []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("symbol", "").upper() != self._symbol:
                continue
            raw_brackets = entry.get("brackets")
            if isinstance(raw_brackets, list):
                for bracket in raw_brackets:
                    if not isinstance(bracket, Mapping):
                        continue
                    leverage_value = bracket.get("initialLeverage")
                    try:
                        parsed = int(float(leverage_value))
                    except (TypeError, ValueError):
                        continue
                    if parsed > 0:
                        brackets.append(parsed)
        if not brackets:
            return None
        return min(brackets), max(brackets)

    @staticmethod
    def _translate_error(payload: str, *, status_code: Optional[int] = None) -> BinanceAPIError:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return BinanceAPIError(payload or "HTTP error", status_code=status_code)
        message = str(data.get("msg", "HTTP error"))
        code = data.get("code")
        if code is not None:
            try:
                parsed_code = int(code)
            except (TypeError, ValueError):
                parsed_code = None
        else:
            parsed_code = None
        return BinanceAPIError(message, code=parsed_code, status_code=status_code)

    @staticmethod
    def _map_side(side: Side) -> str:
        if side is Side.BID:
            return "BUY"
        if side is Side.ASK:
            return "SELL"
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
        return sanitized[:32] or None


__all__ = ["BinanceTradingAdapter", "BinanceTradeEndpoints", "BinanceAPIError"]

