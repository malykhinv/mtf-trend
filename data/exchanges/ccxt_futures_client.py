"""Модуль проекта."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from math import isfinite
from typing import Any, Callable, Protocol, cast

import pandas as pd

from constants import (
    CCXT_MARKET_TYPE_SWAP,
    DEFAULT_FETCH_BATCH_SIZE,
    EXCHANGE_TIMEOUT_SECONDS,
    FUTURES_SETTLEMENT_QUOTE_ASSET,
    MILLISECONDS_IN_SECOND,
    OHLCV_EXTENDED_FRAME_COLUMNS,
    OHLCV_FRAME_COLUMNS,
    OHLCV_OPTIONAL_MARKET_DATA_COLUMNS,
    OPEN_INTEREST_FRAME_COLUMNS,
)
from data.exchanges.ccxt_types import (
    CcxtAggTradePayload,
    CcxtBinanceKlineApi,
    CcxtClientOptions,
    CcxtFuturesApi,
    CcxtOpenInterestApi,
    ExchangeLiveAccountPreflight,
    ExchangeOpenInterestSnapshot,
    ExchangeOrderFill,
    ExchangePositionSnapshot,
    ExchangeTickerSnapshot,
)
from domain.abstract.exchange_client import ExchangeClient
from domain.exceptions import ExchangeConnectivityError, ExchangeOrderNotFound
from domain.enums.exchange import Exchange
from domain.enums.liquidity_quality_state import LiquidityQualityState
from domain.enums.timeframe import Timeframe
from utils.retry import RetryExhaustedError, run_with_retry

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover
    ccxt = None


class CcxtRetryLogger(Protocol):
    def debug(self, message: str, *args: object) -> None:
        ...

    def warning(self, message: str, *args: object) -> None:
        ...


def _position_symbol_key_for_exchange(symbol: str) -> str:
    return str(symbol).replace("/", "").replace(":", "").upper()


class CcxtFuturesClient(ExchangeClient):
    """Класс."""
    def __init__(
        self,
        exchange: Exchange,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        enable_rate_limit: bool = True,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        if ccxt is None:
            raise RuntimeError("ccxt is required for CcxtFuturesClient")

        self.exchange = exchange
        self._logger: CcxtRetryLogger = logging.getLogger(self.__class__.__name__)
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._client: CcxtFuturesApi = self._build_client(
            exchange=self.exchange,
            api_key=api_key,
            secret=secret,
            password=password,
            enable_rate_limit=enable_rate_limit,
        )
        self._markets_loaded = False

    def set_retry_logger(self, logger: CcxtRetryLogger) -> None:
        """Redirect retry diagnostics to a caller-owned logger."""
        self._logger = logger

    # region Приватные

    @staticmethod
    def _build_client(
        exchange: Exchange,
        api_key: str,
        secret: str,
        password: str,
        enable_rate_limit: bool,
    ) -> CcxtFuturesApi:
        params: dict[str, object] = {
            "apiKey": api_key,
            "secret": secret,
            "password": password,
            "enableRateLimit": enable_rate_limit,
            "timeout": EXCHANGE_TIMEOUT_SECONDS * MILLISECONDS_IN_SECOND,
        }
        if exchange == Exchange.BINANCE:
            params["options"] = CcxtClientOptions(
                defaultType=CCXT_MARKET_TYPE_SWAP,
                fetchCurrencies=False,
            )
            return cast(CcxtFuturesApi, cast(object, ccxt.binanceusdm(cast(Any, params))))
        if exchange == Exchange.BYBIT:
            params["options"] = CcxtClientOptions(defaultType=CCXT_MARKET_TYPE_SWAP)
            return cast(CcxtFuturesApi, cast(object, ccxt.bybit(cast(Any, params))))
        if exchange == Exchange.OKX:
            params["options"] = CcxtClientOptions(defaultType=CCXT_MARKET_TYPE_SWAP)
            return cast(CcxtFuturesApi, cast(object, ccxt.okx(cast(Any, params))))
        raise ValueError(f"Unsupported exchange for CCXT futures client: {exchange}")

    def _retry_exchange_call(
        self,
        operation: str,
        symbol: str,
        endpoint: str,
        call: Callable[..., object],
        args: tuple[object, ...] = (),
        should_retry: Callable[[Exception], bool] | None = None,
        **kwargs: object,
    ) -> object:
        try:
            return run_with_retry(
                operation,
                call,
                *args,
                attempts=self._retry_attempts,
                backoff_seconds=self._retry_backoff_seconds,
                retriable_exceptions=(Exception,),
                logger=self._logger,
                endpoint=endpoint,
                symbol=symbol,
                jitter_seconds=0.25,
                should_retry=should_retry,
                **kwargs,
            )
        except RetryExhaustedError as exc:
            raise ExchangeConnectivityError(
                f"Exchange retry exhausted: operation={operation} symbol={symbol} endpoint={endpoint} attempts={self._retry_attempts} cause={exc}"
            ) from exc


    @staticmethod
    def _is_exchange_order_not_found_exception(exc: Exception) -> bool:
        """Return true when the exchange explicitly says the order does not exist.

        Binance USD-M returns code -2013 for unresolved order lookups. This is a
        deterministic lookup result, not a transport failure, so live stop
        verification must not retry/report it as connectivity.
        """
        message = str(exc).lower()
        return "-2013" in message and "order" in message and ("does not exist" in message or "not exist" in message)

    @classmethod
    def _should_retry_order_lookup(cls, exc: Exception) -> bool:
        return not cls._is_exchange_order_not_found_exception(exc)

    @staticmethod
    def _is_non_retriable_binance_oi_error(exc: Exception) -> bool:
        message = str(exc)
        lowered = message.lower()
        return "starttime" in lowered and "invalid" in lowered and "-1130" in lowered

    def _retry_exchange_startup_call(
        self,
        operation: str,
        endpoint: str,
        call: Callable[..., object],
        args: tuple[object, ...] = (),
        **kwargs: object,
    ) -> object:
        try:
            return run_with_retry(
                operation,
                call,
                *args,
                attempts=self._retry_attempts,
                backoff_seconds=self._retry_backoff_seconds,
                retriable_exceptions=(Exception,),
                logger=self._logger,
                endpoint=endpoint,
                jitter_seconds=0.25,
                **kwargs,
            )
        except RetryExhaustedError as exc:
            raise ExchangeConnectivityError(
                "Не удалось загрузить рынки биржи после повторных попыток. "
                "Вероятная причина: сетевая недоступность или DNS-сбой при обращении к API биржи. "
                "Проверьте DNS-резолвинг, настройки прокси и правила firewall. "
                f"exchange={self.exchange.value} endpoint={endpoint} attempts={self._retry_attempts}"
            ) from exc

    def _ensure_markets_loaded(self) -> None:
        if self._markets_loaded:
            return
        self._retry_exchange_startup_call(
            operation="ccxt_load_markets",
            endpoint="load_markets",
            call=self._client.load_markets,
        )
        self._markets_loaded = True

    @staticmethod
    def _aggregate_ohlcv_frame(frame: pd.DataFrame, *, target_timeframe: Timeframe) -> pd.DataFrame:
        output_columns = [
            *OHLCV_FRAME_COLUMNS,
            *[column for column in OHLCV_OPTIONAL_MARKET_DATA_COLUMNS if column in frame.columns],
        ]
        if frame.empty:
            return pd.DataFrame(columns=output_columns)

        required_columns = list(OHLCV_FRAME_COLUMNS)
        if not set(required_columns).issubset(frame.columns):
            return pd.DataFrame(columns=output_columns)

        prepared = frame.loc[:, output_columns].copy()
        for column in output_columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        if prepared.empty:
            return pd.DataFrame(columns=output_columns)

        timeframe_ms = target_timeframe.to_milliseconds()
        prepared["bucket"] = (prepared["timestamp"] // timeframe_ms) * timeframe_ms
        aggregation: dict[str, tuple[str, str]] = {
            "open": ("open", "first"),
            "high": ("high", "max"),
            "low": ("low", "min"),
            "close": ("close", "last"),
            "volume": ("volume", "sum"),
        }
        for column in OHLCV_OPTIONAL_MARKET_DATA_COLUMNS:
            if column in prepared.columns:
                aggregation[column] = (column, "sum")
        aggregated = (
            prepared.groupby("bucket", as_index=False)
            .agg(**aggregation)
            .rename(columns={"bucket": "timestamp"})
        )
        return aggregated.loc[:, output_columns].reset_index(drop=True)

    @staticmethod
    def _aggregate_open_interest_frame(frame: pd.DataFrame, *, target_timeframe: Timeframe) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=OPEN_INTEREST_FRAME_COLUMNS)

        prepared = frame.loc[:, list(OPEN_INTEREST_FRAME_COLUMNS)].copy()
        for column in OPEN_INTEREST_FRAME_COLUMNS:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open_interest"])
        if prepared.empty:
            return pd.DataFrame(columns=OPEN_INTEREST_FRAME_COLUMNS)

        timeframe_ms = target_timeframe.to_milliseconds()
        prepared["bucket"] = (prepared["timestamp"] // timeframe_ms) * timeframe_ms
        aggregated = (
            prepared.groupby("bucket", as_index=False)
            .agg(open_interest=("open_interest", "last"))
            .rename(columns={"bucket": "timestamp"})
        )
        return aggregated.loc[:, list(OPEN_INTEREST_FRAME_COLUMNS)].reset_index(drop=True)

    # endregion Приватные

    def get_market_id(self, symbol: str) -> str:
        """Returns exchange-specific market id for a normalized CCXT symbol."""
        self._ensure_markets_loaded()
        return str(self._client.market_id(symbol))

    def fetch_current_open_interest(self, symbol: str) -> ExchangeOpenInterestSnapshot:
        """Fetch Binance USD-M current open interest for one symbol.

        This is intentionally a narrow exchange boundary for live execution guards.
        Strategy/live code must not call private CCXT raw endpoints directly.
        """
        fetched_at_ms = int(time.time() * 1000)
        market_id = self.get_market_id(symbol)
        endpoint = "fapiPublicGetOpenInterest"
        call = self._require_binance_raw_endpoint(endpoint)
        payload = self._retry_exchange_call(
            operation="binance_fetch_current_open_interest",
            symbol=symbol,
            endpoint=endpoint,
            call=call,
            params={"symbol": market_id},
        )
        if not isinstance(payload, dict):
            return ExchangeOpenInterestSnapshot(
                symbol=symbol,
                exchange_symbol=market_id,
                fetched_at_ms=fetched_at_ms,
                timestamp_ms=None,
                open_interest=None,
                source=endpoint,
                status="invalid_payload",
                reason=f"payload_type={type(payload).__name__}",
            )
        raw_open_interest = payload.get("openInterest")
        try:
            open_interest = float(cast(Any, raw_open_interest))
        except (TypeError, ValueError):
            open_interest = float("nan")
        raw_timestamp = payload.get("time")
        timestamp_ms: int | None
        try:
            timestamp_ms = int(float(cast(Any, raw_timestamp))) if raw_timestamp not in (None, "") else None
        except (TypeError, ValueError):
            timestamp_ms = None
        if not isfinite(open_interest) or open_interest <= 0.0:
            return ExchangeOpenInterestSnapshot(
                symbol=symbol,
                exchange_symbol=str(payload.get("symbol") or market_id),
                fetched_at_ms=fetched_at_ms,
                timestamp_ms=timestamp_ms,
                open_interest=None,
                source=endpoint,
                status="invalid_open_interest",
                reason=f"openInterest={raw_open_interest!r}",
            )
        return ExchangeOpenInterestSnapshot(
            symbol=symbol,
            exchange_symbol=str(payload.get("symbol") or market_id),
            fetched_at_ms=fetched_at_ms,
            timestamp_ms=timestamp_ms,
            open_interest=open_interest,
            source=endpoint,
            status="ok",
        )

    def list_usdt_swap_symbols(self) -> list[str]:
        """Returns active USDT-settled swap symbols from the loaded exchange markets."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        markets = getattr(raw_client, "markets", {}) or {}
        symbols: list[str] = []
        for market in markets.values():
            if not isinstance(market, dict):
                continue
            if not bool(market.get("active", True)):
                continue
            if not bool(market.get("swap", False)):
                continue
            if str(market.get("quote", "")).upper() != "USDT":
                continue
            symbol = market.get("symbol")
            if isinstance(symbol, str) and symbol:
                symbols.append(symbol)
        return sorted(set(symbols))

    def fetch_usdt_free_balance(self) -> float:
        """Returns free USDT futures balance. Missing balance is a hard data error."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        payload = self._retry_exchange_call(
            operation="ccxt_fetch_balance",
            symbol="USDT",
            endpoint="fetch_balance",
            call=raw_client.fetch_balance,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("fetch_balance returned invalid payload")
        free = payload.get("free")
        if not isinstance(free, dict) or "USDT" not in free:
            raise RuntimeError("fetch_balance has no free USDT value")
        return float(free["USDT"])

    def fetch_last_price(self, symbol: str) -> float:
        """Returns current exchange last/mark price from ticker payload; missing price is a hard error."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        payload = self._retry_exchange_call(
            operation="ccxt_fetch_ticker",
            symbol=symbol,
            endpoint="fetch_ticker",
            call=raw_client.fetch_ticker,
            args=(symbol,),
        )
        if not isinstance(payload, dict):
            raise RuntimeError("fetch_ticker returned invalid payload")
        price = self._first_finite_float(payload.get("last"))
        info = payload.get("info")
        if price is None and isinstance(info, dict):
            price = self._first_finite_float(info.get("lastPrice"))
        if price is None or price <= 0.0:
            raise RuntimeError("fetch_ticker returned no finite positive last price")
        return price

    def fetch_ticker_snapshots(self, symbols: tuple[str, ...] | list[str]) -> list[ExchangeTickerSnapshot]:
        """Returns normalized ticker snapshots for live scheduling priority.

        This method deliberately does not synthesize quote volume from base volume
        and price. Missing ticker fields are explicit status/source values because
        ticker radar must never become hidden proxy flow evidence.
        """
        self._ensure_markets_loaded()
        requested_symbols = list(dict.fromkeys(str(symbol) for symbol in symbols if str(symbol).strip()))
        fetched_at_ms = int(time.time() * 1000)
        if not requested_symbols:
            return []
        payload = self._retry_exchange_call(
            operation="ccxt_fetch_tickers",
            symbol="__tickers__",
            endpoint="fetch_tickers",
            call=self._client.fetch_tickers,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("fetch_tickers returned invalid payload")

        tickers_by_symbol: dict[str, dict[str, object]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            if isinstance(key, str):
                tickers_by_symbol[key] = value
            raw_symbol = value.get("symbol")
            if isinstance(raw_symbol, str) and raw_symbol:
                tickers_by_symbol.setdefault(raw_symbol, value)

        snapshots: list[ExchangeTickerSnapshot] = []
        for symbol in requested_symbols:
            ticker = tickers_by_symbol.get(symbol)
            if not isinstance(ticker, dict):
                snapshots.append(
                    ExchangeTickerSnapshot(
                        symbol=symbol,
                        fetched_at_ms=fetched_at_ms,
                        last_price=None,
                        quote_volume_24h=None,
                        trade_count_24h=None,
                        last_price_source="missing",
                        quote_volume_source="missing",
                        trade_count_source="missing",
                        status="missing_ticker",
                        reason="fetch_tickers_payload_missing_symbol",
                    )
                )
                continue
            snapshots.append(self._normalize_ticker_snapshot(symbol, ticker, fetched_at_ms=fetched_at_ms))
        return snapshots

    @classmethod
    def _normalize_ticker_snapshot(
        cls,
        symbol: str,
        ticker: dict[str, object],
        *,
        fetched_at_ms: int,
    ) -> ExchangeTickerSnapshot:
        info = ticker.get("info")
        info_dict = info if isinstance(info, dict) else {}
        last_price, last_price_source = cls._first_positive_float_with_source(
            (
                ("last", ticker.get("last")),
                ("close", ticker.get("close")),
                ("info.lastPrice", info_dict.get("lastPrice")),
            )
        )
        quote_volume, quote_volume_source = cls._first_positive_float_with_source(
            (
                ("quoteVolume", ticker.get("quoteVolume")),
                ("info.quoteVolume", info_dict.get("quoteVolume")),
            )
        )
        trade_count_float, trade_count_source = cls._first_positive_float_with_source(
            (
                ("count", ticker.get("count")),
                ("trades", ticker.get("trades")),
                ("info.count", info_dict.get("count")),
                ("info.tradeCount", info_dict.get("tradeCount")),
                ("info.numberOfTrades", info_dict.get("numberOfTrades")),
            )
        )
        trade_count = int(trade_count_float) if trade_count_float is not None else None
        missing: list[str] = []
        if last_price is None:
            missing.append("last_price")
        if quote_volume is None:
            missing.append("quote_volume_24h")
        status = "ok" if not missing else "missing_required_fields"
        reason = ",".join(missing) if missing else None
        return ExchangeTickerSnapshot(
            symbol=symbol,
            fetched_at_ms=fetched_at_ms,
            last_price=last_price,
            quote_volume_24h=quote_volume,
            trade_count_24h=trade_count,
            last_price_source=last_price_source,
            quote_volume_source=quote_volume_source,
            trade_count_source=trade_count_source,
            status=status,
            reason=reason,
        )

    @staticmethod
    def _first_positive_float_with_source(candidates: tuple[tuple[str, object], ...]) -> tuple[float | None, str]:
        for source, value in candidates:
            if not isinstance(value, (int, float, str, bytes)):
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if isfinite(parsed) and parsed > 0.0:
                return parsed, source
        return None, "missing"

    @staticmethod
    def _normalize_client_order_id(client_order_id: str) -> str:
        raw = str(client_order_id).strip()
        if not raw:
            raise ValueError("client_order_id is required for live order placement")
        normalized = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
        if len(normalized) <= 36:
            return normalized
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return f"{normalized[:19]}_{digest}"

    def _create_order_client_id_param(self, client_order_id: str) -> dict[str, object]:
        normalized = self._normalize_client_order_id(client_order_id)
        if self.exchange == Exchange.BINANCE:
            return {"newClientOrderId": normalized}
        raise NotImplementedError("idempotent live order placement is implemented only for Binance futures")

    def _fetch_order_client_id_param(self, client_order_id: str) -> dict[str, object]:
        normalized = self._normalize_client_order_id(client_order_id)
        if self.exchange == Exchange.BINANCE:
            return {"origClientOrderId": normalized}
        raise NotImplementedError("client-order-id order reconciliation is implemented only for Binance futures")

    @staticmethod
    def _resolve_order_id(payload: dict[str, object]) -> str | None:
        value = payload.get("id")
        if value is None:
            info = payload.get("info")
            if isinstance(info, dict):
                value = info.get("orderId")
        if value is None:
            return None
        order_id = str(value).strip()
        return order_id or None

    def fetch_order_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, object]:
        """Fetches an exchange order by explicit client order id; unresolved lookup is a hard error."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        try:
            payload = self._retry_exchange_call(
                operation="ccxt_fetch_order_by_client_order_id",
                symbol=symbol,
                endpoint="fetch_order",
                call=raw_client.fetch_order,
                args=(None, symbol),
                should_retry=self._should_retry_order_lookup,
                params=self._fetch_order_client_id_param(client_order_id),
            )
        except Exception as exc:
            if self._is_exchange_order_not_found_exception(exc):
                normalized_client_order_id = self._normalize_client_order_id(client_order_id)
                raise ExchangeOrderNotFound(
                    f"order not found: symbol={symbol} client_order_id={normalized_client_order_id} cause={exc}"
                ) from exc
            raise
        if not isinstance(payload, dict):
            raise RuntimeError("fetch_order by client order id returned invalid payload")
        return dict(payload)


    @staticmethod
    def _is_ambiguous_order_mutation_exception(exc: Exception) -> bool:
        if ccxt is None:
            return False
        ambiguous_names = (
            "NetworkError",
            "RequestTimeout",
            "ExchangeNotAvailable",
            "DDoSProtection",
            "RateLimitExceeded",
        )
        ambiguous_types = tuple(
            exc_type
            for name in ambiguous_names
            if isinstance((exc_type := getattr(ccxt, name, None)), type)
        )
        return bool(ambiguous_types) and isinstance(exc, ambiguous_types)

    def _create_order_once_or_reconcile(
        self,
        *,
        symbol: str,
        order_type: str,
        side: str,
        amount: str,
        price: str | None = None,
        client_order_id: str,
        params: dict[str, object],
        operation: str,
    ) -> dict[str, object]:
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        order_params = dict(params)
        order_params.update(self._create_order_client_id_param(client_order_id))
        try:
            payload = raw_client.create_order(symbol, order_type, side, amount, price=price, params=order_params)
        except Exception as create_exc:
            if not self._is_ambiguous_order_mutation_exception(create_exc):
                raise
            try:
                return self.fetch_order_by_client_order_id(symbol, client_order_id)
            except Exception as reconcile_exc:
                raise ExchangeConnectivityError(
                    f"Ambiguous create_order result unresolved: operation={operation} symbol={symbol} "
                    f"client_order_id={self._normalize_client_order_id(client_order_id)} "
                    f"create_error={type(create_exc).__name__}: {create_exc} "
                    f"reconcile_error={type(reconcile_exc).__name__}: {reconcile_exc}"
                ) from create_exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{operation} returned invalid payload")
        return dict(payload)

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        *,
        reduce_only: bool,
        client_order_id: str,
    ) -> dict[str, object]:
        """Places one market order with deterministic client id and reconciles ambiguous transport failure."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        precise_amount = raw_client.amount_to_precision(symbol, amount)
        return self._create_order_once_or_reconcile(
            symbol=symbol,
            order_type="market",
            side=side,
            amount=precise_amount,
            client_order_id=client_order_id,
            params={"reduceOnly": reduce_only},
            operation="ccxt_create_market_order",
        )

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        *,
        reduce_only: bool,
        client_order_id: str,
    ) -> dict[str, object]:
        """Places one limit order with deterministic client id and reconciles ambiguous transport failure."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        precise_amount = raw_client.amount_to_precision(symbol, amount)
        precise_price = raw_client.price_to_precision(symbol, price)
        return self._create_order_once_or_reconcile(
            symbol=symbol,
            order_type="limit",
            side=side,
            amount=precise_amount,
            price=precise_price,
            client_order_id=client_order_id,
            params={"reduceOnly": reduce_only},
            operation="ccxt_create_limit_order",
        )

    def create_market_order_with_fill(
        self,
        symbol: str,
        side: str,
        amount: float,
        *,
        reduce_only: bool,
        client_order_id: str,
    ) -> ExchangeOrderFill:
        """Places a market order and returns verified execution fill fields.

        The method deliberately does not infer execution price from candles or current ticker.
        If exchange order/trade payloads do not expose fill price and amount, the caller
        gets a hard error instead of an optimistic synthetic fill.
        """
        order = self.create_market_order(symbol, side, amount, reduce_only=reduce_only, client_order_id=client_order_id)
        order_id = self._resolve_order_id(order)
        if not order_id:
            raise RuntimeError("create_order returned no order id")
        return self.fetch_order_fill(symbol, order_id, submitted_order=order)

    def fetch_order_fill(
        self,
        symbol: str,
        order_id: str,
        *,
        submitted_order: dict[str, object] | None = None,
    ) -> ExchangeOrderFill:
        """Returns normalized fill for an existing order; unresolved fill is a hard error."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        payloads: list[dict[str, object]] = []
        if submitted_order is not None:
            payloads.append(dict(submitted_order))
        fetched = self._retry_exchange_call(
            operation="ccxt_fetch_order",
            symbol=symbol,
            endpoint="fetch_order",
            call=raw_client.fetch_order,
            args=(order_id, symbol),
        )
        if not isinstance(fetched, dict):
            raise RuntimeError("fetch_order returned invalid payload")
        payloads.append(dict(fetched))
        trades = self.fetch_my_trades_for_order(symbol, order_id)
        fill = self._normalize_order_fill(order_id=order_id, order_payloads=payloads, trade_payloads=trades)
        if fill is None:
            raise RuntimeError(f"order fill unresolved: symbol={symbol} order_id={order_id}")
        return fill

    def fetch_my_trades_for_order(self, symbol: str, order_id: str) -> list[dict[str, object]]:
        """Returns account trades for one order id through CCXT."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        payload = self._retry_exchange_call(
            operation="ccxt_fetch_my_trades_for_order",
            symbol=symbol,
            endpoint="fetch_my_trades",
            call=raw_client.fetch_my_trades,
            args=(symbol, None, None),
            params={"orderId": order_id},
        )
        if not isinstance(payload, list):
            raise RuntimeError("fetch_my_trades returned invalid payload")
        return [dict(row) for row in payload if isinstance(row, dict)]

    @classmethod
    def _normalize_order_fill(
        cls,
        *,
        order_id: str,
        order_payloads: list[dict[str, object]],
        trade_payloads: list[dict[str, object]],
    ) -> ExchangeOrderFill | None:
        for payload in reversed(order_payloads):
            status = str(payload.get("status") or "").lower()
            timestamp_ms = cls._first_finite_int(payload.get("timestamp"), payload.get("lastTradeTimestamp"))
            average_price = cls._first_finite_float(payload.get("average"))
            filled_amount = cls._first_finite_float(payload.get("filled"))
            cost = cls._first_finite_float(payload.get("cost"))
            info = payload.get("info")
            if isinstance(info, dict):
                timestamp_ms = timestamp_ms or cls._first_finite_int(
                    info.get("updateTime"),
                    info.get("time"),
                    info.get("transactTime"),
                )
                average_price = average_price or cls._first_finite_float(info.get("avgPrice"))
                filled_amount = filled_amount or cls._first_finite_float(
                    info.get("executedQty"),
                    info.get("cumQty"),
                )
                cost = cost or cls._first_finite_float(
                    info.get("cumQuote"),
                    info.get("cummulativeQuoteQty"),
                )
            fee_cost, fee_currency = cls._extract_fee(payload.get("fee"))
            if timestamp_ms is not None and average_price and filled_amount:
                if cost is None and trade_payloads:
                    cost = cls._sum_trade_cost(trade_payloads)
                return ExchangeOrderFill(
                    order_id=order_id,
                    status=status or "unknown",
                    timestamp_ms=timestamp_ms,
                    average_price=average_price,
                    filled_amount=filled_amount,
                    cost=cost,
                    fee_cost=fee_cost,
                    fee_currency=fee_currency,
                )

        if not trade_payloads:
            return None
        total_amount = 0.0
        total_cost = 0.0
        timestamps: list[int] = []
        fee_cost_total = 0.0
        fee_currency: str | None = None
        for trade in trade_payloads:
            amount = cls._first_finite_float(trade.get("amount"))
            price = cls._first_finite_float(trade.get("price"))
            cost = cls._first_finite_float(trade.get("cost"))
            timestamp_ms = cls._first_finite_int(trade.get("timestamp"))
            if timestamp_ms is not None:
                timestamps.append(timestamp_ms)
            if amount is None or amount <= 0.0:
                return None
            if cost is None:
                if price is None or price <= 0.0:
                    return None
                cost = price * amount
            total_amount += amount
            total_cost += cost
            fee_cost, current_fee_currency = cls._extract_fee(trade.get("fee"))
            if fee_cost is not None:
                fee_cost_total += fee_cost
                fee_currency = fee_currency or current_fee_currency
        if total_amount <= 0.0 or total_cost <= 0.0 or not timestamps:
            return None
        return ExchangeOrderFill(
            order_id=order_id,
            status="closed",
            timestamp_ms=max(timestamps),
            average_price=total_cost / total_amount,
            filled_amount=total_amount,
            cost=total_cost,
            fee_cost=fee_cost_total if fee_cost_total > 0.0 else None,
            fee_currency=fee_currency,
        )

    @staticmethod
    def _extract_fee(payload: object) -> tuple[float | None, str | None]:
        if not isinstance(payload, dict):
            return None, None
        cost = CcxtFuturesClient._first_finite_float(payload.get("cost"))
        currency = payload.get("currency")
        return cost, str(currency) if isinstance(currency, str) and currency else None

    @staticmethod
    def _sum_trade_cost(trades: list[dict[str, object]]) -> float | None:
        total = 0.0
        seen = False
        for trade in trades:
            cost = CcxtFuturesClient._first_finite_float(trade.get("cost"))
            if cost is None:
                amount = CcxtFuturesClient._first_finite_float(trade.get("amount"))
                price = CcxtFuturesClient._first_finite_float(trade.get("price"))
                if amount is None or price is None:
                    return None
                cost = amount * price
            total += cost
            seen = True
        return total if seen and total > 0.0 else None

    @staticmethod
    def _first_finite_float(*values: object) -> float | None:
        for value in values:
            if value is None or value == "":
                continue
            try:
                parsed = float(cast(Any, value))
            except (TypeError, ValueError):
                continue
            if isfinite(parsed):
                return parsed
        return None

    @staticmethod
    def _first_finite_int(*values: object) -> int | None:
        for value in values:
            if value is None or value == "":
                continue
            try:
                parsed = int(float(cast(Any, value)))
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
        return None


    def _require_binance_raw_endpoint(self, endpoint: str) -> Callable[..., object]:
        if self.exchange != Exchange.BINANCE:
            raise NotImplementedError("Binance raw endpoint boundary is implemented only for Binance USD-M futures")
        raw_client = cast(Any, self._client)
        call = getattr(raw_client, endpoint, None)
        if not callable(call):
            raise RuntimeError(
                f"ccxt client does not expose required Binance raw endpoint: {endpoint}. "
                "Upgrade ccxt or disable real-order live trading until the endpoint is available."
            )
        return cast(Callable[..., object], call)

    def _create_algo_client_id_param(self, client_order_id: str) -> dict[str, object]:
        normalized = self._normalize_client_order_id(client_order_id)
        if self.exchange == Exchange.BINANCE:
            return {"clientAlgoId": normalized}
        raise NotImplementedError("conditional stop client ids are implemented only for Binance futures")

    @staticmethod
    def _normalize_binance_algo_order(payload: dict[str, object]) -> dict[str, object]:
        """Normalize Binance USD-M conditional/algo order payloads to the live order contract.

        Binance exposes UI-visible TP/SL/STOP orders through the algo-order API.  These
        rows do not look like ordinary CCXT orders, so live verification must normalize
        algoId/clientAlgoId/orderType/triggerPrice instead of pretending they are absent.
        """
        info = dict(payload)
        order: dict[str, object] = {"info": info, "source": "binance_algo_order"}
        algo_id = payload.get("algoId", payload.get("orderId"))
        if algo_id is not None and str(algo_id).strip():
            order["id"] = str(algo_id).strip()
        client_algo_id = payload.get("clientAlgoId", payload.get("clientOrderId"))
        if client_algo_id is not None and str(client_algo_id).strip():
            order["clientOrderId"] = str(client_algo_id).strip()
        order_type = payload.get("orderType", payload.get("type"))
        if order_type is not None and str(order_type).strip():
            order["type"] = str(order_type).strip()
        side = payload.get("side")
        if side is not None and str(side).strip():
            order["side"] = str(side).strip().lower()
        status = payload.get("algoStatus", payload.get("status"))
        if status is not None and str(status).strip():
            order["status"] = str(status).strip().lower()
        amount = CcxtFuturesClient._first_finite_float(payload.get("quantity"), payload.get("origQty"), payload.get("amount"))
        if amount is not None:
            order["amount"] = amount
        stop_price = CcxtFuturesClient._first_finite_float(payload.get("triggerPrice"), payload.get("stopPrice"))
        if stop_price is not None:
            order["stopPrice"] = stop_price
        reduce_only = payload.get("reduceOnly")
        if isinstance(reduce_only, str):
            normalized_reduce_only = reduce_only.strip().lower()
            if normalized_reduce_only in {"true", "1", "yes"}:
                order["reduceOnly"] = True
            elif normalized_reduce_only in {"false", "0", "no"}:
                order["reduceOnly"] = False
        elif isinstance(reduce_only, bool):
            order["reduceOnly"] = reduce_only
        elif isinstance(reduce_only, (int, float)) and isfinite(float(reduce_only)):
            order["reduceOnly"] = bool(reduce_only)
        working_type = payload.get("workingType")
        if working_type is not None and str(working_type).strip():
            order["workingType"] = str(working_type).strip()
        return order

    def _fetch_open_binance_algo_orders_raw(self, symbol: str) -> list[dict[str, object]]:
        self._ensure_markets_loaded()
        endpoint = "fapiPrivateGetOpenAlgoOrders"
        call = self._require_binance_raw_endpoint(endpoint)
        payload = self._retry_exchange_call(
            operation="binance_fetch_open_algo_orders",
            symbol=symbol,
            endpoint=endpoint,
            call=call,
            params={"symbol": self.get_market_id(symbol)},
        )
        if not isinstance(payload, list):
            raise RuntimeError(f"{endpoint} returned invalid payload")
        return [dict(row) for row in payload if isinstance(row, dict)]

    def fetch_open_stop_orders(self, symbol: str) -> list[dict[str, object]]:
        """Returns open Binance conditional/algo stop orders for a symbol.

        This is the primary live stop visibility path for Binance. Ordinary
        fetch_open_orders does not reliably include UI-visible conditional stops.
        """
        rows = self._fetch_open_binance_algo_orders_raw(symbol)
        return [self._normalize_binance_algo_order(row) for row in rows]

    def fetch_stop_order_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, object]:
        normalized_client_order_id = self._normalize_client_order_id(client_order_id)
        for order in self.fetch_open_stop_orders(symbol):
            info = order.get("info")
            info_client_id = info.get("clientAlgoId") if isinstance(info, dict) else None
            order_client_id = order.get("clientOrderId")
            if str(order_client_id or "").strip() == normalized_client_order_id or str(info_client_id or "").strip() == normalized_client_order_id:
                return dict(order)
        raise ExchangeOrderNotFound(
            f"conditional stop order not found: symbol={symbol} client_algo_id={normalized_client_order_id}"
        )

    def cancel_stop_order(self, symbol: str, order_id: str) -> dict[str, object]:
        """Cancels a Binance conditional/algo stop order by algoId or clientAlgoId."""
        self._ensure_markets_loaded()
        endpoint = "fapiPrivateDeleteAlgoOrder"
        call = self._require_binance_raw_endpoint(endpoint)
        raw_id = str(order_id).strip()
        if not raw_id:
            raise ValueError("order_id is required for conditional stop cancellation")
        params: dict[str, object] = {"symbol": self.get_market_id(symbol)}
        if raw_id.isdigit():
            params["algoId"] = raw_id
        else:
            params["clientAlgoId"] = self._normalize_client_order_id(raw_id)
        payload = self._retry_exchange_call(
            operation="binance_cancel_algo_order",
            symbol=symbol,
            endpoint=endpoint,
            call=call,
            params=params,
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"{endpoint} returned invalid payload")
        return self._normalize_binance_algo_order(dict(payload))

    def create_stop_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        *,
        client_order_id: str,
    ) -> dict[str, object]:
        """Places one reduce-only Binance conditional STOP_MARKET order with deterministic client id.

        Binance UI-visible TP/SL/STOP orders are verified/cancelled through the
        algo-order API. Creating the stop through the same boundary avoids the
        legacy mismatch where an order is visible in Binance Conditional UI but
        invisible to ordinary fetch_open_orders/fetch_order/cancel_order.
        """
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        precise_amount = raw_client.amount_to_precision(symbol, amount)
        precise_stop = raw_client.price_to_precision(symbol, stop_price)
        endpoint = "fapiPrivatePostAlgoOrder"
        call = self._require_binance_raw_endpoint(endpoint)
        params: dict[str, object] = {
            "symbol": self.get_market_id(symbol),
            "side": str(side).upper(),
            "type": "STOP_MARKET",
            "algoType": "CONDITIONAL",
            "quantity": precise_amount,
            "triggerPrice": precise_stop,
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
        }
        params.update(self._create_algo_client_id_param(client_order_id))
        payload = self._retry_exchange_call(
            operation="binance_create_algo_stop_market_order",
            symbol=symbol,
            endpoint=endpoint,
            call=call,
            params=params,
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"{endpoint} returned invalid payload")
        return self._normalize_binance_algo_order(dict(payload))

    def cancel_order(self, symbol: str, order_id: str) -> dict[str, object]:
        """Cancels an exchange order by id."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        payload = self._retry_exchange_call(
            operation="ccxt_cancel_order",
            symbol=symbol,
            endpoint="cancel_order",
            call=raw_client.cancel_order,
            args=(order_id, symbol),
        )
        if not isinstance(payload, dict):
            raise RuntimeError("cancel_order returned invalid payload")
        return dict(payload)

    def fetch_open_orders(self, symbol: str) -> list[dict[str, object]]:
        """Returns currently open exchange orders for a symbol."""
        self._ensure_markets_loaded()
        payload = self._retry_exchange_call(
            operation="ccxt_fetch_open_orders",
            symbol=symbol,
            endpoint="fetch_open_orders",
            call=self._client.fetch_open_orders,
            args=(symbol,),
        )
        if not isinstance(payload, list):
            raise RuntimeError("fetch_open_orders returned invalid payload")
        orders: list[dict[str, object]] = []
        for row in payload:
            if isinstance(row, dict):
                orders.append(dict(row))
        return orders

    def fetch_symbol_position_amount(self, symbol: str) -> float:
        """Returns signed contract amount for a symbol. Missing position means zero."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        payload = self._retry_exchange_call(
            operation="ccxt_fetch_positions",
            symbol=symbol,
            endpoint="fetch_positions",
            call=raw_client.fetch_positions,
            args=([symbol],),
        )
        if not isinstance(payload, list):
            raise RuntimeError("fetch_positions returned invalid payload")
        for row in payload:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol", "")) != symbol:
                continue
            contracts = row.get("contracts", row.get("contractSize", 0.0))
            side = str(row.get("side", "")).lower()
            amount = float(contracts or 0.0)
            info = row.get("info")
            if amount == 0.0 and isinstance(info, dict) and "positionAmt" in info:
                amount = float(info["positionAmt"])
            if side == "short":
                return -abs(amount)
            if side == "long":
                return abs(amount)
            if isinstance(info, dict) and "positionAmt" in info:
                return float(info["positionAmt"])
            return amount
        return 0.0

    @staticmethod
    def _parse_required_exchange_bool(value: object, *, field: str, endpoint: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
        if isinstance(value, (int, float)) and isfinite(float(value)):
            if int(value) == 1:
                return True
            if int(value) == 0:
                return False
        raise RuntimeError(f"{endpoint} returned invalid boolean field: {field}={value!r}")

    def fetch_live_account_preflight(self) -> ExchangeLiveAccountPreflight:
        """Returns the explicit account mode snapshot required before live real-order trading."""
        self._ensure_markets_loaded()
        if self.exchange != Exchange.BINANCE:
            raise NotImplementedError("live account-mode preflight is implemented only for Binance USD-M futures")
        raw_client = cast(Any, self._client)
        endpoint = "fapiPrivateGetPositionSideDual"
        call = getattr(raw_client, endpoint, None)
        if not callable(call):
            raise RuntimeError(f"ccxt client does not expose required Binance endpoint: {endpoint}")
        payload = self._retry_exchange_startup_call(
            operation="ccxt_binance_position_side_dual",
            endpoint=endpoint,
            call=call,
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"{endpoint} returned invalid payload")
        hedge_mode_enabled = self._parse_required_exchange_bool(
            payload.get("dualSidePosition"),
            field="dualSidePosition",
            endpoint=endpoint,
        )
        return ExchangeLiveAccountPreflight(
            exchange=self.exchange.value,
            position_mode="hedge" if hedge_mode_enabled else "one_way",
            hedge_mode_enabled=hedge_mode_enabled,
        )

    @staticmethod
    def _position_row_symbol(row: dict[str, object]) -> str | None:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol.strip():
            return symbol.strip()
        info = row.get("info")
        if isinstance(info, dict):
            raw_symbol = info.get("symbol")
            if isinstance(raw_symbol, str) and raw_symbol.strip():
                return raw_symbol.strip()
        return None

    @staticmethod
    def _position_row_signed_amount(row: dict[str, object]) -> float:
        info = row.get("info")
        amount: float | None = None
        source = row.get("contracts")
        if source is not None and source != "":
            try:
                amount = float(source)
            except (TypeError, ValueError):
                amount = None
        if (amount is None or amount == 0.0) and isinstance(info, dict) and "positionAmt" in info:
            return float(info["positionAmt"])
        amount = float(amount or 0.0)
        side = str(row.get("side", "")).lower()
        if side == "short":
            return -abs(amount)
        if side == "long":
            return abs(amount)
        if isinstance(info, dict) and "positionAmt" in info:
            return float(info["positionAmt"])
        return amount

    def fetch_position_snapshots(self, symbols: tuple[str, ...] | list[str]) -> list[ExchangePositionSnapshot]:
        """Returns signed exchange position amounts for a startup live universe in one account read."""
        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        requested_symbols = tuple(dict.fromkeys(str(symbol) for symbol in symbols))
        payload = self._retry_exchange_call(
            operation="ccxt_fetch_positions_batch",
            symbol="__account__",
            endpoint="fetch_positions",
            call=raw_client.fetch_positions,
            args=(list(requested_symbols),),
        )
        if not isinstance(payload, list):
            raise RuntimeError("fetch_positions returned invalid payload")
        requested_by_symbol: dict[str, str] = {}
        for symbol in requested_symbols:
            requested_by_symbol[_position_symbol_key_for_exchange(symbol)] = symbol
            try:
                requested_by_symbol[_position_symbol_key_for_exchange(self.get_market_id(symbol))] = symbol
            except Exception:
                pass
        snapshots: list[ExchangePositionSnapshot] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            row_symbol = self._position_row_symbol(row)
            if row_symbol is None:
                continue
            symbol = requested_by_symbol.get(_position_symbol_key_for_exchange(row_symbol))
            if symbol is None:
                continue
            signed_amount = self._position_row_signed_amount(row)
            if signed_amount > 0.0:
                side = "long"
            elif signed_amount < 0.0:
                side = "short"
            else:
                side = "flat"
            snapshots.append(
                ExchangePositionSnapshot(
                    symbol=symbol,
                    signed_amount=signed_amount,
                    side=side,
                    source="ccxt_fetch_positions_batch",
                )
            )
        return snapshots

    def fetch_binance_agg_trades(
        self,
        *,
        symbol: str,
        params: dict[str, object],
    ) -> list[CcxtAggTradePayload]:
        """Fetches raw Binance futures aggTrades through a typed client boundary."""
        if self.exchange != Exchange.BINANCE:
            raise NotImplementedError("aggTrades raw endpoint is currently implemented only for Binance futures")

        raw_client = cast(Any, self._client)
        batch = self._retry_exchange_call(
            operation="binance_fetch_agg_trades",
            symbol=symbol,
            endpoint="fapiPublicGetAggTrades",
            call=raw_client.fapiPublicGetAggTrades,
            params=params,
        )
        if not isinstance(batch, list):
            raise ValueError(
                f"binance_fetch_agg_trades returned invalid payload type: {type(batch).__name__}"
            )

        payloads: list[CcxtAggTradePayload] = []
        for row in batch:
            if isinstance(row, dict):
                payloads.append(cast(CcxtAggTradePayload, cast(dict[str, object], row)))
        return payloads

    @staticmethod
    def _normalize_binance_context_kline_rows(rows: list[object]) -> pd.DataFrame:
        normalized_rows: list[list[object]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            normalized_rows.append([row[0], row[1], row[2], row[3], row[4]])
        return pd.DataFrame(normalized_rows, columns=["timestamp", "open", "high", "low", "close"])

    @staticmethod
    def _normalize_binance_context_ratio_rows(rows: list[object]) -> pd.DataFrame:
        normalized_rows: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized_rows.append(
                {
                    "timestamp": row.get("timestamp"),
                    "long_short_ratio": row.get("longShortRatio"),
                    "long_account": row.get("longAccount"),
                    "short_account": row.get("shortAccount"),
                }
            )
        return pd.DataFrame(normalized_rows, columns=["timestamp", "long_short_ratio", "long_account", "short_account"])

    @staticmethod
    def _normalize_binance_context_taker_rows(rows: list[object]) -> pd.DataFrame:
        normalized_rows: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized_rows.append(
                {
                    "timestamp": row.get("timestamp"),
                    "buy_sell_ratio": row.get("buySellRatio"),
                    "buy_vol": row.get("buyVol"),
                    "sell_vol": row.get("sellVol"),
                }
            )
        return pd.DataFrame(normalized_rows, columns=["timestamp", "buy_sell_ratio", "buy_vol", "sell_vol"])

    @staticmethod
    def _normalize_binance_context_funding_rows(rows: list[object]) -> pd.DataFrame:
        normalized_rows: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized_rows.append(
                {
                    "timestamp": row.get("fundingTime"),
                    "funding_rate": row.get("fundingRate"),
                }
            )
        return pd.DataFrame(normalized_rows, columns=["timestamp", "funding_rate"])

    def fetch_binance_derivatives_context(
        self,
        *,
        symbol: str,
        source: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        period: str = "5m",
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetches Binance futures derivatives context without proxy substitutes."""
        if self.exchange != Exchange.BINANCE:
            raise NotImplementedError("derivatives context is currently implemented only for Binance futures")

        self._ensure_markets_loaded()
        raw_client = cast(Any, self._client)
        market_id = self.get_market_id(symbol)
        params: dict[str, object] = {
            "symbol": market_id,
            "startTime": int(start_timestamp_ms),
            "endTime": int(end_timestamp_ms),
            "limit": int(limit),
        }
        endpoint_by_source: dict[str, tuple[str, Callable[..., object], Callable[[list[object]], pd.DataFrame]]] = {
            "funding": ("fapiPublicGetFundingRate", raw_client.fapiPublicGetFundingRate, self._normalize_binance_context_funding_rows),
            "premium": ("fapiPublicGetPremiumIndexKlines", raw_client.fapiPublicGetPremiumIndexKlines, self._normalize_binance_context_kline_rows),
            "mark": ("fapiPublicGetMarkPriceKlines", raw_client.fapiPublicGetMarkPriceKlines, self._normalize_binance_context_kline_rows),
            "global_ls": ("fapiDataGetGlobalLongShortAccountRatio", raw_client.fapiDataGetGlobalLongShortAccountRatio, self._normalize_binance_context_ratio_rows),
            "top_account_ls": ("fapiDataGetTopLongShortAccountRatio", raw_client.fapiDataGetTopLongShortAccountRatio, self._normalize_binance_context_ratio_rows),
            "top_position_ls": ("fapiDataGetTopLongShortPositionRatio", raw_client.fapiDataGetTopLongShortPositionRatio, self._normalize_binance_context_ratio_rows),
            "taker_ls": ("fapiDataGetTakerlongshortRatio", raw_client.fapiDataGetTakerlongshortRatio, self._normalize_binance_context_taker_rows),
        }
        if source not in endpoint_by_source:
            raise ValueError(f"unsupported_derivatives_context_source:{source}")

        endpoint, call, normalizer = endpoint_by_source[source]
        if source in {"premium", "mark"}:
            params["interval"] = period
        elif source != "funding":
            params["period"] = period
        batch = self._retry_exchange_call(
            operation=f"binance_fetch_derivatives_context_{source}",
            symbol=symbol,
            endpoint=endpoint,
            call=call,
            params=params,
        )
        if not isinstance(batch, list):
            raise ValueError(f"{endpoint} returned invalid payload type: {type(batch).__name__}")
        frame = normalizer(batch)
        if frame.empty:
            return frame
        for column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.loc[
            (frame["timestamp"] >= int(start_timestamp_ms))
            & (frame["timestamp"] <= int(end_timestamp_ms))
        ]
        return frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)

    def get_futures_symbols(self) -> list[str]:
        """Возвращает список доступных фьючерсных символов."""
        self._ensure_markets_loaded()
        symbols: list[str] = []
        for market in self._client.markets.values():
            if not market.get("active", True):
                continue
            if not market.get("swap"):
                continue

            quote = str(market.get("quote") or "").upper()
            settle = str(market.get("settle") or "").upper()
            linear = bool(market.get("linear", False))

            if quote != FUTURES_SETTLEMENT_QUOTE_ASSET and settle != FUTURES_SETTLEMENT_QUOTE_ASSET:
                continue
            if not linear and settle != FUTURES_SETTLEMENT_QUOTE_ASSET:
                continue

            symbol = str(market.get("symbol") or "")
            if symbol and not re.search(r"-\d{4,}$", symbol):
                symbols.append(symbol)

        return sorted(set(symbols))

    def get_futures_symbols_by_quote_volume(self) -> list[str]:
        """Возвращает фьючерсные символы, отсортированные по 24ч quote volume (убывание)."""
        ranked = self.get_futures_symbols_with_liquidity_metrics()
        min_quote_volume = 10_000_000.0
        return [
            str(item["symbol"])
            for item in ranked
            if float(item.get("quote_volume", 0.0) or 0.0) >= min_quote_volume
        ]

    def get_futures_symbols_with_liquidity_metrics(self) -> list[dict[str, object]]:
        """Возвращает символы с 24ч метриками ликвидности и quality-флагами."""
        symbols = self.get_futures_symbols()
        if not symbols:
            return []


        tickers_payload = self._retry_exchange_startup_call(
            operation="ccxt_fetch_tickers",
            endpoint="fetch_tickers",
            call=self._client.fetch_tickers,
            args=(symbols,),
        )

        def _safe_float(value: object) -> float:
            if not isinstance(value, (int, float, str, bytes)):
                return 0.0
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return 0.0
            return parsed if isfinite(parsed) and parsed > 0 else 0.0

        def _safe_int(value: object) -> int:
            if not isinstance(value, (int, float, str, bytes)):
                return 0
            try:
                parsed = int(float(value))
            except (TypeError, ValueError):
                return 0
            return parsed if parsed > 0 else 0

        def _extract_trade_count_24h(ticker_payload: dict[str, object]) -> int:
            info = ticker_payload.get("info")
            candidates = [ticker_payload.get("count"), ticker_payload.get("trades")]
            if isinstance(info, dict):
                candidates.extend([info.get("count"), info.get("tradeCount"), info.get("numberOfTrades")])
            for candidate in candidates:
                parsed = _safe_int(candidate)
                if parsed > 0:
                    return parsed
            return 0

        def _extract_open_interest(ticker_payload: dict[str, object]) -> float:
            info = ticker_payload.get("info")
            candidates = [ticker_payload.get("openInterest")]
            if isinstance(info, dict):
                candidates.extend([info.get("openInterest"), info.get("openInterestValue")])
            for candidate in candidates:
                parsed = _safe_float(candidate)
                if parsed > 0.0:
                    return parsed
            return 0.0

        def _extract_taker_buy_volume(ticker_payload: dict[str, object]) -> float:
            info = ticker_payload.get("info")
            if not isinstance(info, dict):
                return 0.0
            candidates = [
                info.get("takerBuyQuoteVolume"),
                info.get("taker_buy_quote_volume"),
                info.get("takerBuyVolume"),
                info.get("taker_buy_volume"),
            ]
            for candidate in candidates:
                parsed = _safe_float(candidate)
                if parsed > 0.0:
                    return parsed
            return 0.0

        def _resolve_quality_state(
            value: float,
            *,
            ticker_quote_volume: float,
            low_ratio_threshold: float,
        ) -> LiquidityQualityState:
            if value <= 0.0:
                return LiquidityQualityState.MISSING
            if ticker_quote_volume <= 0.0:
                return LiquidityQualityState.LOW_QUALITY
            if (value / ticker_quote_volume) < low_ratio_threshold:
                return LiquidityQualityState.LOW_QUALITY
            return LiquidityQualityState.OK

        records: list[dict[str, object]] = []
        for symbol in symbols:
            ticker = tickers_payload.get(symbol) if isinstance(tickers_payload, dict) else None
            if not isinstance(ticker, dict):
                records.append({
                    "symbol": symbol,
                    "quote_volume": 0.0,
                    "trade_count_24h": 0,
                    "liquidity_score": 0.0,
                    "quality_flags": ["no_oi", "no_taker", "questionable_sync"],
                    "quality_metadata": {
                        "no_oi": True,
                        "no_taker": True,
                        "questionable_sync": True,
                        "oi_quality_state": LiquidityQualityState.MISSING.value,
                        "taker_quality_state": LiquidityQualityState.MISSING.value,
                    },
                })
                continue

            quote_volume = _safe_float(ticker.get("quoteVolume"))
            base_volume = _safe_float(ticker.get("baseVolume"))
            last_price = _safe_float(ticker.get("last"))
            quote_volume_proxy = base_volume * last_price if base_volume > 0.0 and last_price > 0.0 else 0.0
            quote_volume_source = "quoteVolume" if quote_volume > 0.0 else "missing_real_quote_volume"

            trade_count_24h = _extract_trade_count_24h(ticker)
            open_interest_24h = _extract_open_interest(ticker)
            taker_buy_volume_24h = _extract_taker_buy_volume(ticker)
            oi_quality_state = _resolve_quality_state(
                open_interest_24h,
                ticker_quote_volume=quote_volume,
                low_ratio_threshold=0.001,
            )
            taker_quality_state = _resolve_quality_state(
                taker_buy_volume_24h,
                ticker_quote_volume=quote_volume,
                low_ratio_threshold=0.005,
            )

            liquidity_score = quote_volume if quote_volume > 0.0 else 0.0
            if oi_quality_state == LiquidityQualityState.OK:
                liquidity_score *= 1.05
            if taker_quality_state == LiquidityQualityState.OK:
                liquidity_score *= 1.05

            quality_metadata = {
                "no_real_quote_volume": quote_volume <= 0.0,
                "quote_volume_source": quote_volume_source,
                "quote_volume_proxy": quote_volume_proxy,
                "quote_volume_proxy_source": "baseVolume_last" if quote_volume_proxy > 0.0 else "missing",
                "no_oi": open_interest_24h <= 0.0,
                "no_taker": taker_buy_volume_24h <= 0.0,
                "questionable_sync": quote_volume <= 0.0 or trade_count_24h <= 0,
                "open_interest_24h": open_interest_24h,
                "taker_buy_volume_24h": taker_buy_volume_24h,
                "oi_quality_state": oi_quality_state.value,
                "taker_quality_state": taker_quality_state.value,
            }
            quality_flags = [
                flag
                for flag in ("no_real_quote_volume", "no_oi", "no_taker", "questionable_sync")
                if bool(quality_metadata[flag])
            ]
            if quote_volume <= 0.0 and quote_volume_proxy > 0.0:
                quality_flags.append("quote_volume_proxy_available_ignored")
            if oi_quality_state == LiquidityQualityState.LOW_QUALITY:
                quality_flags.append("oi_low_quality")
            if taker_quality_state == LiquidityQualityState.LOW_QUALITY:
                quality_flags.append("taker_low_quality")

            records.append({
                "symbol": symbol,
                "quote_volume": quote_volume,
                "quote_volume_source": quote_volume_source,
                "quote_volume_proxy": quote_volume_proxy,
                "trade_count_24h": trade_count_24h,
                "liquidity_score": liquidity_score,
                "quality_flags": quality_flags,
                "quality_metadata": quality_metadata,
            })

        return sorted(
            records,
            key=lambda row: (float(row["liquidity_score"]), float(row["quote_volume"]), str(row["symbol"])),
            reverse=True,
        )

    @staticmethod
    def _normalize_binance_kline_rows(rows: list[list[object]]) -> pd.DataFrame:
        normalized_rows: list[list[object]] = []
        for row in rows:
            if len(row) < 11:
                continue
            normalized_rows.append(
                [
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                ]
            )
        return pd.DataFrame(normalized_rows, columns=OHLCV_EXTENDED_FRAME_COLUMNS)

    @staticmethod
    def _normalize_ccxt_ohlcv_rows(rows: list[list[object]]) -> pd.DataFrame:
        normalized_rows = [
            list(row[: len(OHLCV_FRAME_COLUMNS)])
            if len(row) >= len(OHLCV_FRAME_COLUMNS)
            else [*row, *([None] * (len(OHLCV_FRAME_COLUMNS) - len(row)))]
            for row in rows
        ]
        return pd.DataFrame(normalized_rows, columns=OHLCV_FRAME_COLUMNS)

    def _fetch_binance_ohlcv_frame(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        self._ensure_markets_loaded()
        raw_client = cast(CcxtBinanceKlineApi, self._client)
        market_id = self.get_market_id(symbol)
        since = int(start_timestamp_ms)
        timeframe_ms = int(timeframe.to_milliseconds())
        all_rows: list[list[object]] = []

        while since <= end_timestamp_ms:
            batch = self._retry_exchange_call(
                operation="binance_fetch_klines",
                symbol=symbol,
                endpoint="fapiPublicGetKlines",
                call=raw_client.fapiPublicGetKlines,
                params={
                    "symbol": market_id,
                    "interval": timeframe.value,
                    "startTime": int(since),
                    "endTime": int(end_timestamp_ms),
                    "limit": int(DEFAULT_FETCH_BATCH_SIZE),
                },
            )
            if not isinstance(batch, list) or not batch:
                break

            typed_batch: list[list[object]] = []
            for row in batch:
                if isinstance(row, list):
                    typed_batch.append(row)
                elif isinstance(row, tuple):
                    typed_batch.append(list(row))
            if not typed_batch:
                break

            all_rows.extend(typed_batch)
            last_ts = int(typed_batch[-1][0])
            next_since = last_ts + timeframe_ms
            if last_ts >= end_timestamp_ms or next_since <= since:
                break
            since = next_since

        return self._normalize_binance_kline_rows(all_rows)

    def _fetch_ccxt_ohlcv_frame(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        self._ensure_markets_loaded()
        since = int(start_timestamp_ms)
        all_rows: list[list[object]] = []
        while since <= end_timestamp_ms:
            batch = self._retry_exchange_call(
                operation="ccxt_fetch_ohlcv",
                symbol=symbol,
                endpoint="fetch_ohlcv",
                call=self._client.fetch_ohlcv,
                args=(symbol,),
                timeframe=timeframe.value,
                since=since,
                limit=DEFAULT_FETCH_BATCH_SIZE,
            )
            if not isinstance(batch, list) or not batch:
                break

            typed_batch: list[list[object]] = []
            for row in batch:
                if isinstance(row, list):
                    typed_batch.append(row)
                elif isinstance(row, tuple):
                    typed_batch.append(list(row))
            if not typed_batch:
                break

            all_rows.extend(typed_batch)
            last_ts = int(typed_batch[-1][0])
            if last_ts >= end_timestamp_ms:
                break
            since = last_ts + 1

        return self._normalize_ccxt_ohlcv_rows(all_rows)

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Запрашивает свечи по символу и интервалу."""
        if timeframe == Timeframe.M10:
            base_frame = self.fetch_ohlcv(
                symbol=symbol,
                timeframe=Timeframe.M5,
                start_timestamp_ms=start_timestamp_ms,
                end_timestamp_ms=end_timestamp_ms,
            )
            aggregated = self._aggregate_ohlcv_frame(base_frame, target_timeframe=timeframe)
            return aggregated.loc[
                (aggregated["timestamp"] >= start_timestamp_ms)
                & (aggregated["timestamp"] <= end_timestamp_ms)
            ].reset_index(drop=True)

        frame = (
            self._fetch_binance_ohlcv_frame(
                symbol=symbol,
                timeframe=timeframe,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
            if self.exchange == Exchange.BINANCE
            else self._fetch_ccxt_ohlcv_frame(
                symbol=symbol,
                timeframe=timeframe,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
        )
        if frame.empty:
            return frame

        for column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.loc[
            (frame["timestamp"] >= start_timestamp_ms)
            & (frame["timestamp"] <= end_timestamp_ms)
        ]
        return frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    def fetch_open_interest(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Запрашивает историю open interest по символу."""
        if timeframe == Timeframe.M10:
            base_frame = self.fetch_open_interest(
                symbol=symbol,
                timeframe=Timeframe.M5,
                start_timestamp_ms=start_timestamp_ms,
                end_timestamp_ms=end_timestamp_ms,
            )
            aggregated = self._aggregate_open_interest_frame(base_frame, target_timeframe=timeframe)
            return aggregated.loc[
                (aggregated["timestamp"] >= start_timestamp_ms)
                & (aggregated["timestamp"] <= end_timestamp_ms)
            ].reset_index(drop=True)

        if timeframe == Timeframe.M3:
            self._logger.debug("OI %s %s биржа не ведёт. Возвращаю пустой ряд и продолжаю.", symbol, timeframe.value)
            return pd.DataFrame(columns=OPEN_INTEREST_FRAME_COLUMNS)

        self._ensure_markets_loaded()
        if not isinstance(self._client, CcxtOpenInterestApi):
            raise NotImplementedError(f"Exchange {self.exchange.value} does not support fetch_open_interest_history in CCXT")

        since = start_timestamp_ms
        ccxt_timeframe = timeframe.value
        timeframe_ms = timeframe.to_milliseconds()
        oi_limit = min(DEFAULT_FETCH_BATCH_SIZE, 500) if self.exchange == Exchange.BINANCE else DEFAULT_FETCH_BATCH_SIZE

        rows: list[dict[str, object]] = []
        while since <= end_timestamp_ms:
            request_end_ms = min(end_timestamp_ms, since + timeframe_ms * oi_limit - 1)
            params: dict[str, object] | None = None
            if self.exchange == Exchange.BINANCE:
                params = {
                    "period": ccxt_timeframe,
                    "endTime": request_end_ms,
                }

            try:
                try:
                    batch = self._retry_exchange_call(
                        operation="ccxt_fetch_open_interest_history",
                        symbol=symbol,
                        endpoint="fetch_open_interest_history",
                        call=self._client.fetch_open_interest_history,
                        args=(symbol,),
                        timeframe=ccxt_timeframe,
                        since=since,
                        limit=oi_limit,
                        params=params,
                        should_retry=(
                            None
                            if self.exchange != Exchange.BINANCE
                            else lambda retry_exc: not self._is_non_retriable_binance_oi_error(retry_exc)
                        ),
                    )
                except TypeError:
                    batch = self._retry_exchange_call(
                        operation="ccxt_fetch_open_interest_history",
                        symbol=symbol,
                        endpoint="fetch_open_interest_history",
                        call=self._client.fetch_open_interest_history,
                        args=(symbol,),
                        timeframe=ccxt_timeframe,
                        since=since,
                        limit=oi_limit,
                        should_retry=(
                            None
                            if self.exchange != Exchange.BINANCE
                            else lambda retry_exc: not self._is_non_retriable_binance_oi_error(retry_exc)
                        ),
                    )
            except Exception as exc:
                message = str(exc)
                if self.exchange == Exchange.BINANCE and "startTime" in message and "invalid" in message:
                    self._logger.debug(
                        "OI %s %s: начало окна вне правил биржи. Перешагиваю участок %s..%s. Причина: %s",
                        symbol,
                        timeframe.value,
                        since,
                        request_end_ms,
                        exc,
                    )
                    since = request_end_ms + 1
                    continue
                raise
            if not isinstance(batch, list):
                break
            if not batch:
                break

            rows.extend(batch)
            last_ts = int(batch[-1].get("timestamp") or 0)
            if last_ts >= end_timestamp_ms:
                break
            if last_ts < since:
                since = request_end_ms + 1
                continue
            since = max(last_ts + 1, request_end_ms + 1)

        if not rows:
            return pd.DataFrame(columns=OPEN_INTEREST_FRAME_COLUMNS)

        frame = pd.DataFrame(rows)
        if "openInterestAmount" in frame.columns:
            frame["open_interest"] = pd.to_numeric(frame["openInterestAmount"], errors="coerce")
        elif "openInterestValue" in frame.columns:
            frame["open_interest"] = pd.to_numeric(frame["openInterestValue"], errors="coerce")
        else:
            frame["open_interest"] = pd.to_numeric(frame.get("openInterest"), errors="coerce")

        frame = frame.loc[
            (frame["timestamp"] >= start_timestamp_ms)
            & (frame["timestamp"] <= end_timestamp_ms)
        ]
        frame = frame[list(OPEN_INTEREST_FRAME_COLUMNS)]
        return frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
