from __future__ import annotations

import hashlib
import hmac
import json
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
            request = Request(url=url, headers={"X-MBX-APIKEY": self._api_key})

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
        executed_at = datetime.now(tz=CURRENT_TIMEZONE)
        return ExecutionReport(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            order_id="SIMULATED",
            side=side,
            price=0.0,
            quantity=quantity,
            executed_qty=quantity,
            status="FILLED",
            commission=0.0,
            executed_at=executed_at,
        )

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        return None


class BybitTradingAdapter(IExchangeTrade):
    def __init__(
        self,
        *,
        symbol: str,
        settle_coin: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._symbol = symbol.upper()
        self._settle_coin = settle_coin
        self._api_key = api_key
        self._api_secret = api_secret

    def get_balance(self, source: BalanceSource) -> float:
        return 0.0

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        return leverage

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        executed_at = datetime.now(tz=CURRENT_TIMEZONE)
        return ExecutionReport(
            exchange=Exchange.BYBIT,
            symbol=self._symbol,
            order_id="SIMULATED",
            side=side,
            price=0.0,
            quantity=quantity,
            executed_qty=quantity,
            status="FILLED",
            commission=0.0,
            executed_at=executed_at,
        )

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        return None


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

