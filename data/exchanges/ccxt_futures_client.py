"""Модуль проекта."""

from __future__ import annotations

import logging
import re
from math import isfinite
from typing import Any, Callable, cast

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
)
from domain.abstract.exchange_client import ExchangeClient
from domain.exceptions import ExchangeConnectivityError
from domain.enums.exchange import Exchange
from domain.enums.liquidity_quality_state import LiquidityQualityState
from domain.enums.timeframe import Timeframe
from utils.retry import RetryExhaustedError, run_with_retry

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover
    ccxt = None


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
        self._logger = logging.getLogger(self.__class__.__name__)
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
            raise RuntimeError(
                f"Exchange retry exhausted: operation={operation} symbol={symbol} endpoint={endpoint} attempts={self._retry_attempts} cause={exc}"
            ) from exc

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
