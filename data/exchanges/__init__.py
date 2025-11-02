from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Sequence

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config.config import CONFIG
from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink
from domain.models import (
    BalanceSource,
    Exchange,
    ExecutionReport,
    MarginMode,
    Side,
    OrderBookSnapshot,
    OrderBookUpdate,
    StopTrigger,
    SymbolFilters,
    Trade,
)

from .binance import (
    BestBidAsk,
    BinanceExchangeData,
    BinanceStreamManager,
    BinanceSymbolStreams,
    DepthStreamData,
    StreamLimitError,
    StreamSubscription,
)
from .bybit import BybitExchangeData
from .events import ResyncReason, StreamEvent, StreamEventType
from .stream_buffer import StreamBuffer


@dataclass(slots=True)
class ExchangeLogger:
    sink: LogSink

    def log(self, message: str) -> None:
        timestamp = datetime.now(tz=CURRENT_TIMEZONE)
        formatted = f"{timestamp:%H:%M:%S} {message}"
        self.sink(formatted)


class _BybitPrivateRestClient:
    _REST_HOST = "https://api.bybit.com"
    _RECV_WINDOW = 5000

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        timeout_s: float,
        log: LogSink,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._timeout = max(timeout_s, 1.0)
        self._log = log

    def _log_warning(self, message: str) -> None:
        try:
            self._log(message)
        except Exception:  # pragma: no cover - defensive logging
            pass

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return format(value, "g")
        if isinstance(value, int):
            return str(value)
        return str(value)

    def _encode_params(self, params: Mapping[str, Any]) -> str:
        items: list[tuple[str, str]] = []
        for key, value in sorted(params.items()):
            if value is None:
                continue
            items.append((key, self._stringify(value)))
        return urlencode(items)

    def _sign(self, payload: str, timestamp: int) -> str:
        message = f"{timestamp}{self._api_key}{self._RECV_WINDOW}{payload}".encode("utf-8")
        secret = self._api_secret.encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        context: str,
    ) -> Mapping[str, Any]:
        query_params = dict(params.items()) if params else {}
        query_string = self._encode_params(query_params) if query_params else ""
        url = f"{self._REST_HOST}{path}"
        if method.upper() == "GET" and query_string:
            url = f"{url}?{query_string}"

        body_string = ""
        data_bytes: Optional[bytes] = None
        if method.upper() != "GET":
            body_payload = dict(body.items()) if body else {}
            body_string = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":"))
            data_bytes = body_string.encode("utf-8")

        payload = body_string if method.upper() != "GET" else query_string
        timestamp = int(time.time() * 1000)
        signature = self._sign(payload, timestamp)

        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": str(self._RECV_WINDOW),
        }
        if method.upper() != "GET":
            headers["Content-Type"] = "application/json"

        request = Request(url=url, data=data_bytes, headers=headers, method=method.upper())
        max_attempts = 3
        base_delay = 0.5
        last_exception: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                    payload_bytes = response.read()
                data = json.loads(payload_bytes.decode("utf-8"))
                if not isinstance(data, Mapping):
                    raise RuntimeError("Bybit REST response malformed")
                return data
            except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_exception = exc
                if attempt < max_attempts:
                    delay = min(base_delay * (2 ** (attempt - 1)), 5.0)
                    self._log_warning(
                        (
                            f"[WARNING] Bybit REST {context} failed (attempt {attempt}): "
                            f"{exc}. Retrying in {delay:.2f}s"
                        )
                    )
                    time.sleep(delay)
                    continue
                self._log_warning(
                    f"[ERROR] Bybit REST {context} failed after {attempt} attempts: {exc}"
                )
                break

        assert last_exception is not None
        raise RuntimeError(f"Bybit REST {context} failed") from last_exception

    def get(
        self,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        context: str,
    ) -> Mapping[str, Any]:
        return self._request("GET", path, params=params, context=context)

    def post(
        self,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        context: str,
    ) -> Mapping[str, Any]:
        return self._request("POST", path, body=body, context=context)


class IExchangeData(Protocol):
    def fetch_symbol_filters(self) -> SymbolFilters: ...

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot: ...

    def fetch_next_funding_time(self) -> Optional[datetime]: ...

    def stream_depth(self) -> StreamSubscription[DepthStreamData]: ...

    def stream_trades(self) -> StreamSubscription[Trade]: ...

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]: ...


class IExchangeTrade(Protocol):
    def get_balance(self, source: BalanceSource) -> float: ...

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]: ...

    def place_market(self, side, quantity, *, reason: Optional[str] = None) -> ExecutionReport: ...

    def place_stop_market(
        self,
        side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None: ...


class BinanceTradingAdapter(IExchangeTrade):
    _REST_HOST = "https://fapi.binance.com"

    def __init__(
        self,
        *,
        symbol: str,
        quote_asset: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        log_writer: Optional[LogSink] = None,
    ) -> None:
        self._symbol = symbol.upper()
        self._quote_asset = quote_asset.upper()
        self._api_key = api_key
        self._api_secret = api_secret
        self._log_writer: LogSink = log_writer or (lambda message: None)
        settings = CONFIG.binance_trading
        self._client_order_id_prefix = self._sanitize_identifier(
            settings.new_client_order_id_prefix
        )
        self._market_reduce_only = settings.reduce_only
        self._stop_close_position = settings.close_position

    def _log(self, message: str) -> None:
        try:
            self._log_writer(message)
        except Exception:  # pragma: no cover - logging sink errors are ignored
            pass

    def _signed_request(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        timeout: float,
        context: Optional[str] = None,
        method: str = "GET",
    ) -> str:
        if not self._api_key or not self._api_secret:
            raise RuntimeError("Binance API credentials are required for signed requests")

        label = context or path
        max_attempts = 3
        base_delay = 0.5
        last_exception: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            query_params = dict(params.items()) if params else {}
            query_params.setdefault("timestamp", int(time.time() * 1000))
            query_params.setdefault("recvWindow", 5000)
            query_string = urlencode(query_params, doseq=True)
            signature = hmac.new(
                self._api_secret.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            url = f"{self._REST_HOST}{path}?{query_string}&signature={signature}"
            data_bytes = b"" if method.upper() != "GET" else None
            request = Request(
                url=url,
                headers={"X-MBX-APIKEY": self._api_key},
                data=data_bytes,
                method=method.upper(),
            )

            try:
                with urlopen(request, timeout=timeout) as response:  # noqa: S310
                    payload = response.read()
                return payload.decode("utf-8")
            except (URLError, HTTPError, TimeoutError, OSError) as exc:  # pragma: no cover - network
                last_exception = exc
                if attempt < max_attempts:
                    backoff = base_delay * (2 ** (attempt - 1))
                    self._log(
                        (
                            f"[WARNING] {label} attempt {attempt} failed for {self._symbol}:"
                            f" {exc}. Retrying in {backoff:.2f}s"
                        )
                    )
                    time.sleep(backoff)
                else:
                    self._log(
                        f"[ERROR] failed to execute {label} for {self._symbol}: {exc}"
                    )

        assert last_exception is not None
        raise RuntimeError(f"failed to execute {label} for {self._symbol}") from last_exception

    def get_balance(self, source: BalanceSource) -> float:
        timeout_s = getattr(CONFIG.general, "orderbook_snapshot_timeout_s", 5.0)
        response_text = self._signed_request(
            "/fapi/v2/balance",
            timeout=timeout_s,
            context="balance request",
        )

        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("failed to decode Binance balance response") from exc

        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            raise RuntimeError("Binance balance response malformed")

        asset_entry: Optional[Mapping[str, Any]] = None
        for entry in payload:
            if not isinstance(entry, Mapping):
                continue
            asset = entry.get("asset")
            if isinstance(asset, str) and asset.upper() == self._quote_asset:
                asset_entry = entry
                break

        if asset_entry is None:
            raise RuntimeError(
                f"Binance balance response missing asset {self._quote_asset}"
            )

        if source is BalanceSource.AVAILABLE:
            field_name = "availableBalance"
        elif source is BalanceSource.WALLET:
            field_name = "walletBalance"
        else:  # pragma: no cover - defensive clause for unexpected enum values
            raise RuntimeError(f"unsupported balance source: {source}")

        value = asset_entry.get(field_name)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Binance balance field {field_name} for {self._quote_asset} is invalid"
            ) from exc

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        return leverage

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        timeout_s = getattr(CONFIG.general, "orderbook_snapshot_timeout_s", 5.0)
        client_id = self._build_client_order_id(reason)
        params: dict[str, Any] = {
            "symbol": self._symbol,
            "side": "BUY" if side is Side.BID else "SELL",
            "type": "MARKET",
            "quantity": format(quantity, "g"),
        }
        if client_id is not None:
            params["newClientOrderId"] = client_id
        params["reduceOnly"] = self._bool_param(self._market_reduce_only)
        response_text = self._signed_request(
            "/fapi/v1/order",
            params=params,
            timeout=timeout_s,
            context="create market order",
            method="POST",
        )
        payload = self._decode_payload(response_text, context="create market order")
        return self._build_execution_report(
            payload,
            side=side,
            requested_qty=quantity,
        )

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        timeout_s = getattr(CONFIG.general, "orderbook_snapshot_timeout_s", 5.0)
        params: dict[str, Any] = {
            "symbol": self._symbol,
            "side": "BUY" if side is Side.BID else "SELL",
            "type": "STOP_MARKET",
            "stopPrice": format(stop_price, "g"),
            "workingType": self._map_working_type(trigger),
            "closePosition": self._bool_param(self._stop_close_position),
        }
        if not self._stop_close_position:
            params["quantity"] = format(quantity, "g")
        params["reduceOnly"] = self._bool_param(self._market_reduce_only)
        response_text = self._signed_request(
            "/fapi/v1/order",
            params=params,
            timeout=timeout_s,
            context="create stop order",
            method="POST",
        )
        payload = self._decode_payload(response_text, context="create stop order")
        order_id = str(payload.get("orderId") or payload.get("clientOrderId") or "").strip()
        status = str(payload.get("status") or "").strip()
        order_type = str(payload.get("type") or "").strip()
        if not order_id:
            order_id = "UNKNOWN"
        if not status:
            status = "UNKNOWN"
        if not order_type:
            order_type = "STOP_MARKET"
        self._log(
            (
                f"[INFO] Binance stop order ack for {self._symbol}: "
                f"id={order_id} type={order_type} status={status}"
            )
        )

    @staticmethod
    def _sanitize_identifier(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        filtered = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
        if not filtered:
            return None
        return filtered[:36]

    def _build_client_order_id(self, reason: Optional[str]) -> Optional[str]:
        prefix = self._client_order_id_prefix
        suffix = self._sanitize_identifier(reason)
        if prefix is None and suffix is None:
            return None
        parts = []
        if prefix is not None:
            parts.append(prefix)
        if suffix is not None:
            parts.append(suffix)
        combined = "_".join(parts)
        return combined[:36] if combined else None

    @staticmethod
    def _bool_param(value: bool) -> str:
        return "true" if value else "false"

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(result):
            return 0.0
        return result

    def _decode_payload(self, text: str, *, context: str) -> Mapping[str, Any]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self._log(
                f"[ERROR] Binance {context} response decode failed for {self._symbol}: {exc}"
            )
            raise RuntimeError(f"Binance {context} response invalid") from exc
        if not isinstance(payload, Mapping):
            self._log(
                f"[ERROR] Binance {context} response malformed for {self._symbol}"
            )
            raise RuntimeError(f"Binance {context} response malformed")
        code = payload.get("code")
        if code is not None:
            try:
                code_value = int(code)
            except (TypeError, ValueError):
                code_value = -1
            if code_value != 0:
                message = str(payload.get("msg") or "unknown error")
                self._log(
                    (
                        f"[ERROR] Binance {context} failed for {self._symbol}: "
                        f"{message} (code {code_value})"
                    )
                )
                raise RuntimeError(
                    f"Binance {context} failed: {message} (code {code_value})"
                )
        return payload

    def _build_execution_report(
        self,
        payload: Mapping[str, Any],
        *,
        side: Side,
        requested_qty: float,
    ) -> ExecutionReport:
        order_id = str(payload.get("orderId") or payload.get("clientOrderId") or "").strip()
        if not order_id:
            self._log(
                f"[ERROR] Binance order response missing orderId for {self._symbol}"
            )
            raise RuntimeError("Binance order response missing orderId")
        quantity_value = self._to_float(payload.get("origQty"))
        if quantity_value <= 0.0:
            quantity_value = max(requested_qty, 0.0)
        executed_qty = self._to_float(payload.get("executedQty") or payload.get("cumQty"))
        if executed_qty <= 0.0:
            executed_qty = quantity_value
        cum_quote = self._to_float(
            payload.get("cumQuote")
            or payload.get("cumQuoteQty")
            or payload.get("cummulativeQuoteQty")
        )
        price = self._to_float(payload.get("avgPrice"))
        if price <= 0.0:
            price = self._to_float(payload.get("price"))
        if price <= 0.0 and executed_qty > 0.0 and cum_quote > 0.0:
            price = cum_quote / executed_qty
        commission = self._to_float(
            payload.get("commission") or payload.get("commissionAmount")
        )
        status = str(payload.get("status") or "").strip()
        if not status:
            status = "UNKNOWN"
        executed_at = datetime.now(tz=CURRENT_TIMEZONE)
        time_value = payload.get("updateTime") or payload.get("transactTime")
        parsed_time = self._parse_time(time_value)
        if parsed_time is not None:
            executed_at = parsed_time
        return ExecutionReport(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            order_id=order_id,
            side=side,
            price=price,
            quantity=quantity_value,
            executed_qty=executed_qty,
            status=status,
            commission=commission,
            executed_at=executed_at,
        )

    @staticmethod
    def _parse_time(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(CURRENT_TIMEZONE)
        if isinstance(value, (int, float)):
            seconds = float(value)
            if seconds > 1e12:
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=CURRENT_TIMEZONE)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.isdigit():
                return BinanceTradingAdapter._parse_time(int(text))
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=CURRENT_TIMEZONE)
            return parsed.astimezone(CURRENT_TIMEZONE)
        return None

    @staticmethod
    def _map_working_type(trigger: StopTrigger) -> str:
        if trigger is StopTrigger.MARK:
            return "MARK_PRICE"
        if trigger is StopTrigger.LAST:
            return "LAST_PRICE"
        return "MARK_PRICE"


class BybitTradingAdapter(IExchangeTrade):
    def __init__(
        self,
        *,
        symbol: str,
        settle_coin: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        log_writer: Optional[LogSink] = None,
    ) -> None:
        self._symbol = symbol.upper()
        self._settle_coin = settle_coin
        self._api_key = api_key
        self._api_secret = api_secret
        self._timeout = getattr(CONFIG.general, "orderbook_snapshot_timeout_s", 5.0)
        self._log_writer: LogSink = log_writer or (lambda message: None)
        self._rest_client: Optional[_BybitPrivateRestClient] = None

    def _log(self, message: str) -> None:
        try:
            self._log_writer(message)
        except Exception:  # pragma: no cover - logging sink errors are ignored
            pass

    def _ensure_credentials(self, *, context: str) -> None:
        if self._api_key and self._api_secret:
            return
        self._log(
            (
                f"[ERROR] Bybit {context} requires API credentials. "
                "Проверьте API key/secret."
            )
        )
        raise RuntimeError("Bybit API credentials are required for trading operations")

    def _client(self) -> _BybitPrivateRestClient:
        self._ensure_credentials(context="REST call")
        if self._rest_client is None:
            assert self._api_key is not None
            assert self._api_secret is not None
            self._rest_client = _BybitPrivateRestClient(
                api_key=self._api_key,
                api_secret=self._api_secret,
                timeout_s=self._timeout,
                log=self._log,
            )
        return self._rest_client

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(result):
            return 0.0
        return result

    @staticmethod
    def _parse_time(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=CURRENT_TIMEZONE)
            return value.astimezone(CURRENT_TIMEZONE)
        if isinstance(value, (int, float)):
            seconds = float(value)
            if seconds > 1e12:
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=CURRENT_TIMEZONE)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.isdigit():
                return BybitTradingAdapter._parse_time(int(text))
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=CURRENT_TIMEZONE)
            return parsed.astimezone(CURRENT_TIMEZONE)
        return None

    @staticmethod
    def _format_ext_info(info: Any) -> str:
        if not isinstance(info, Mapping):
            return ""
        parts = [f"{key}={value}" for key, value in info.items() if value not in (None, "")]
        return f" ({', '.join(parts)})" if parts else ""

    def _extract_result(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
    ) -> tuple[Mapping[str, Any], Optional[float]]:
        ret_code_raw = payload.get("retCode", 0)
        try:
            ret_code = int(ret_code_raw)
        except (TypeError, ValueError):
            ret_code = -1
        if ret_code != 0:
            message = str(payload.get("retMsg") or "unknown error")
            ext = self._format_ext_info(payload.get("retExtInfo"))
            error_text = f"{message} (code {ret_code}){ext}"
            if ret_code in {10004, 10005, 10006, 130015, 130021, 130024}:
                self._log(
                    f"[ERROR] Bybit {context} authentication failed: {error_text}"
                )
            else:
                self._log(f"[ERROR] Bybit {context} failed: {error_text}")
            raise RuntimeError(f"Bybit {context} failed: {error_text}")

        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError(f"Bybit {context} response missing result")

        time_value: Optional[float] = None
        raw_time = payload.get("time")
        if isinstance(raw_time, (int, float)):
            time_value = float(raw_time)
        elif isinstance(raw_time, str) and raw_time.isdigit():
            time_value = float(raw_time)
        return result, time_value

    @staticmethod
    def _sanitize_reason(reason: Optional[str]) -> Optional[str]:
        if not reason:
            return None
        filtered = "".join(ch for ch in reason if ch.isalnum() or ch in {"-", "_"})
        if not filtered:
            return None
        return filtered[:32]

    def get_balance(self, source: BalanceSource) -> float:
        client = self._client()
        params = {
            "accountType": "UNIFIED",
            "coin": self._settle_coin,
        }
        payload = client.get(
            "/v5/account/wallet-balance",
            params=params,
            context="wallet balance",
        )
        result, _ = self._extract_result(payload, context="wallet balance")
        entries = result.get("list")
        if not isinstance(entries, Sequence):
            raise RuntimeError("Bybit wallet balance list missing")

        target_coin: Optional[Mapping[str, Any]] = None
        settle_upper = self._settle_coin.upper()
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            coins = entry.get("coin")
            if not isinstance(coins, Sequence):
                continue
            for coin in coins:
                if not isinstance(coin, Mapping):
                    continue
                symbol = coin.get("coin")
                if isinstance(symbol, str) and symbol.upper() == settle_upper:
                    target_coin = coin
                    break
            if target_coin is not None:
                break

        if target_coin is None:
            raise RuntimeError(
                f"Bybit wallet balance response missing coin {self._settle_coin}"
            )

        if source is BalanceSource.AVAILABLE:
            field_name = "availableToWithdraw"
        elif source is BalanceSource.WALLET:
            field_name = "walletBalance"
        else:  # pragma: no cover - defensive clause for unexpected enum values
            raise RuntimeError(f"unsupported balance source: {source}")

        balance_value = target_coin.get(field_name)
        try:
            amount = float(balance_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Bybit wallet balance field {field_name} for {self._settle_coin} is invalid"
            ) from exc
        if not math.isfinite(amount):
            raise RuntimeError(
                f"Bybit wallet balance field {field_name} for {self._settle_coin} is not finite"
            )
        return amount

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        self._ensure_credentials(context="set leverage")
        return leverage

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        client = self._client()
        side_text = "Buy" if side is Side.BID else "Sell"
        order_link_id = self._sanitize_reason(reason)
        body: dict[str, Any] = {
            "category": "linear",
            "symbol": self._symbol,
            "side": side_text,
            "orderType": "Market",
            "qty": format(quantity, "g"),
            "timeInForce": "GoodTillCancel",
            "reduceOnly": False,
            "closeOnTrigger": False,
        }
        if order_link_id is not None:
            body["orderLinkId"] = order_link_id

        payload = client.post(
            "/v5/order/create",
            body=body,
            context="create market order",
        )
        result, response_time = self._extract_result(payload, context="create market order")
        report = self._build_execution_report(
            result,
            side=side,
            requested_qty=quantity,
            response_time=response_time,
        )
        return report

    def _build_execution_report(
        self,
        order: Mapping[str, Any],
        *,
        side: Side,
        requested_qty: float,
        response_time: Optional[float],
    ) -> ExecutionReport:
        order_id = str(order.get("orderId") or order.get("orderID") or "").strip()
        if not order_id:
            raise RuntimeError("Bybit order response missing orderId")

        order_qty = self._to_float(order.get("qty") or order.get("orderQty"))
        quantity = order_qty if order_qty > 0.0 else max(requested_qty, 0.0)

        executed_qty = self._to_float(order.get("cumExecQty") or order.get("execQty"))
        if executed_qty <= 0.0:
            executed_qty = quantity

        price_candidates = (
            order.get("avgPrice"),
            order.get("price"),
            order.get("orderPrice"),
            order.get("lastPrice"),
        )
        price = 0.0
        for candidate in price_candidates:
            price = self._to_float(candidate)
            if price > 0.0:
                break
        if price <= 0.0 and executed_qty > 0.0:
            notional = self._to_float(order.get("cumExecValue"))
            if notional > 0.0:
                price = notional / executed_qty

        commission_candidates = (
            order.get("cumExecFee"),
            order.get("execFee"),
            order.get("commission"),
        )
        commission = 0.0
        for candidate in commission_candidates:
            commission = self._to_float(candidate)
            if commission > 0.0:
                break

        status = str(order.get("orderStatus") or order.get("status") or "UNKNOWN")
        status = status.upper() if status else "UNKNOWN"

        executed_at = self._parse_time(
            order.get("updatedTime")
            or order.get("createdTime")
            or order.get("transactTime")
        )
        if executed_at is None and response_time is not None:
            executed_at = self._parse_time(response_time)
        if executed_at is None:
            executed_at = datetime.now(tz=CURRENT_TIMEZONE)

        return ExecutionReport(
            exchange=Exchange.BYBIT,
            symbol=self._symbol,
            order_id=order_id,
            side=side,
            price=price,
            quantity=quantity,
            executed_qty=executed_qty,
            status=status,
            commission=commission,
            executed_at=executed_at,
        )

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        client = self._client()
        trigger_map = {
            StopTrigger.MARK: "MarkPrice",
            StopTrigger.LAST: "LastPrice",
        }
        trigger_by = trigger_map.get(trigger)
        if trigger_by is None:  # pragma: no cover - defensive clause
            raise RuntimeError(f"unsupported stop trigger: {trigger}")

        side_text = "Buy" if side is Side.BID else "Sell"
        body = {
            "category": "linear",
            "symbol": self._symbol,
            "side": side_text,
            "orderType": "Market",
            "orderFilter": "Stop",
            "qty": format(quantity, "g"),
            "triggerPrice": format(stop_price, "g"),
            "triggerDirection": 1 if side is Side.BID else 2,
            "triggerBy": trigger_by,
            "timeInForce": "GoodTillCancel",
            "reduceOnly": False,
            "closeOnTrigger": False,
        }

        payload = client.post(
            "/v5/order/create",
            body=body,
            context="create stop order",
        )
        self._extract_result(payload, context="create stop order")


__all__ = [
    "BestBidAsk",
    "BinanceExchangeData",
    "BinanceStreamManager",
    "BinanceSymbolStreams",
    "BinanceTradingAdapter",
    "StreamLimitError",
    "BybitExchangeData",
    "BybitTradingAdapter",
    "DepthStreamData",
    "ExchangeLogger",
    "IExchangeData",
    "IExchangeTrade",
    "ResyncReason",
    "StreamBuffer",
    "StreamEvent",
    "StreamEventType",
    "StreamSubscription",
]

