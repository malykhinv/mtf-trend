"""Модуль проекта."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from constants import (
    COINGECKO_BASE_URL,
    COINGECKO_DEFAULT_PAGE,
    COINGECKO_HEADER_ACCEPT_JSON,
    COINGECKO_HEADER_ACCEPT_KEY,
    COINGECKO_HEADER_API_KEY,
    COINGECKO_ORDER_KEY,
    COINGECKO_ORDER_MARKET_CAP_DESC,
    COINGECKO_PARAM_IDS,
    COINGECKO_PARAM_PAGE,
    COINGECKO_PARAM_PER_PAGE,
    COINGECKO_PARAM_SPARKLINE,
    COINGECKO_SPARKLINE_FALSE,
    COINGECKO_TIMEOUT_SECONDS,
    COINGECKO_VS_CURRENCY_KEY,
    COINGECKO_VS_CURRENCY_USD,
    SIMULATION_COIN_SUFFIX_SLASH_USDT,
    SIMULATION_COIN_SUFFIX_USDT,
    LOG_MSG_LOAD_ERROR,
    LOG_MSG_RETRY_EXHAUSTED,
)
from domain.abstract.market_data_client import MarketDataClient
from utils.retry import RetryExhaustedError, run_with_retry


class CoinGeckoClient(MarketDataClient):
    """Класс."""
    BASE_URL = COINGECKO_BASE_URL

    def __init__(
        self,
        api_key: str = "",
        cache_ttl_hours: int = 24,
        cache_path: str | Path | None = None,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._cache_ttl = timedelta(hours=cache_ttl_hours)
        self._market_cap_cache: dict[str, tuple[float, datetime]] = {}
        self._symbol_to_id: dict[str, str] = {}
        self._cache_path = Path(cache_path) if cache_path else None
        self._logger = logging.getLogger(self.__class__.__name__)
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._load_market_cap_cache()

    # область Приватные

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return (
            symbol.lower()
            .replace(SIMULATION_COIN_SUFFIX_SLASH_USDT, "")
            .replace(SIMULATION_COIN_SUFFIX_USDT, "")
        )

    @classmethod
    def _canonical_symbol_key(cls, symbol: str) -> str:
        return cls._normalize_symbol(symbol).upper()

    def _load_market_cap_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return

        try:
            raw_payload = pd.read_parquet(self._cache_path)
            required_columns = {"symbol", "market_cap", "expires_at"}
            if not required_columns.issubset(raw_payload.columns):
                raise ValueError(f"Кэш должен содержать колонки: {required_columns}")

            now = datetime.now(tz=timezone.utc)
            loaded_cache: dict[str, tuple[float, datetime]] = {}
            has_coin_id = "coin_id" in raw_payload.columns
            load_columns = ["symbol", "market_cap", "expires_at", "coin_id"] if has_coin_id else ["symbol", "market_cap", "expires_at"]
            for row in raw_payload[load_columns].itertuples(index=False):
                symbol = row.symbol
                if not isinstance(symbol, str) or not symbol:
                    continue
                canonical_symbol = self._canonical_symbol_key(symbol)
                if not canonical_symbol:
                    continue

                expires_at_dt = pd.Timestamp(row.expires_at)
                if pd.isna(expires_at_dt):
                    continue

                if expires_at_dt.tzinfo is None:
                    expires_at_dt = expires_at_dt.tz_localize(timezone.utc)
                else:
                    expires_at_dt = expires_at_dt.tz_convert(timezone.utc)

                expires_at = expires_at_dt.to_pydatetime()
                if expires_at <= now:
                    continue

                loaded_cache[canonical_symbol] = (float(row.market_cap), expires_at)
                if has_coin_id:
                    coin_id = str(row.coin_id or "")
                    if coin_id:
                        self._symbol_to_id[canonical_symbol] = coin_id

            self._market_cap_cache = loaded_cache
        except (OSError, ValueError, TypeError) as exc:
            self._logger.warning(LOG_MSG_LOAD_ERROR, self._cache_path, exc)
            self._market_cap_cache = {}

    def _save_market_cap_cache(self) -> None:
        if self._cache_path is None:
            return

        records = [
            {
                "symbol": symbol,
                "market_cap": market_cap,
                "expires_at": pd.Timestamp(expires_at).tz_convert(timezone.utc),
                "coin_id": self._symbol_to_id.get(symbol, ""),
            }
            for symbol, (market_cap, expires_at) in self._market_cap_cache.items()
        ]
        cache_frame = pd.DataFrame.from_records(records, columns=["symbol", "market_cap", "expires_at", "coin_id"])
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".tmp",
            prefix=f"{self._cache_path.stem}.",
            dir=self._cache_path.parent,
            delete=False,
        )
        temp_path = Path(temp_file.name)
        temp_file.close()
        try:
            cache_frame.to_parquet(temp_path, index=False)
            os.replace(temp_path, self._cache_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _headers(self) -> dict[str, str]:
        headers = {COINGECKO_HEADER_ACCEPT_KEY: COINGECKO_HEADER_ACCEPT_JSON}
        if self._api_key:
            headers[COINGECKO_HEADER_API_KEY] = self._api_key
        return headers

    def _request(self, endpoint: str, symbol: str, params: dict[str, str | int] | None = None) -> requests.Response:
        def _perform_get() -> requests.Response:
            response = requests.get(
                url=f"{self.BASE_URL}{endpoint}",
                headers=self._headers(),
                params=params,
                timeout=COINGECKO_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response

        try:
            return run_with_retry(
                operation="coingecko_get",
                call=_perform_get,
                attempts=self._retry_attempts,
                backoff_seconds=self._retry_backoff_seconds,
                retriable_exceptions=(requests.RequestException,),
                logger=self._logger,
                endpoint=endpoint,
                symbol=symbol,
                jitter_seconds=0.25,
            )
        except RetryExhaustedError as exc:
            raise RuntimeError(
                LOG_MSG_RETRY_EXHAUSTED % (endpoint, symbol, self._retry_attempts)
            ) from exc

    def _candidate_has_usdt_market(self, coin_id: str, base_symbol: str) -> bool:
        response = self._request(endpoint=f"/coins/{coin_id}/tickers", symbol=base_symbol)
        for ticker in response.json().get("tickers", []):
            base = str(ticker.get("base") or "").lower()
            target = str(ticker.get("target") or "").lower()
            if base == base_symbol and target == "usdt":
                return True
        return False

    def _select_candidate_by_market_metrics(self, candidates: list[dict[str, str]]) -> dict[str, str] | None:
        candidate_ids = [candidate["id"] for candidate in candidates if candidate.get("id")]
        if not candidate_ids:
            return None

        response = self._request(
            endpoint="/coins/markets",
            symbol="multiple",
            params={
                COINGECKO_VS_CURRENCY_KEY: COINGECKO_VS_CURRENCY_USD,
                COINGECKO_PARAM_IDS: ",".join(candidate_ids),
                COINGECKO_ORDER_KEY: COINGECKO_ORDER_MARKET_CAP_DESC,
                COINGECKO_PARAM_PER_PAGE: len(candidate_ids),
                COINGECKO_PARAM_PAGE: COINGECKO_DEFAULT_PAGE,
            },
        )

        metrics_by_id = {
            str(item.get("id") or ""): item
            for item in response.json()
            if item.get("id")
        }
        if not metrics_by_id:
            return None

        def score(candidate: dict[str, str]) -> tuple[float, float, float, str]:
            metrics = metrics_by_id.get(candidate["id"], {})
            rank = metrics.get("market_cap_rank")
            total_volume = float(metrics.get("total_volume") or 0.0)
            market_cap = float(metrics.get("market_cap") or 0.0)
            normalized_rank = float(rank) if isinstance(rank, (int, float)) and rank > 0 else float("inf")
            return normalized_rank, -total_volume, -market_cap, candidate["id"]

        return min(candidates, key=score)

    def _resolve_coin_id(self, symbol: str) -> str:
        normalized = self._normalize_symbol(symbol)
        canonical_symbol = self._canonical_symbol_key(symbol)
        if canonical_symbol in self._symbol_to_id:
            return self._symbol_to_id[canonical_symbol]

        response = self._request(endpoint="/coins/list", symbol=symbol)
        candidates = []
        for item in response.json():
            item_symbol = str(item.get("symbol") or "").lower()
            if item_symbol == normalized:
                candidates.append(
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "symbol": item_symbol,
                    }
                )

        if not candidates:
            raise ValueError(f"Не удалось определить CoinGecko ID для символа: {symbol}")

        selected = candidates[0]
        resolved_by_strategy = len(candidates) == 1
        if len(candidates) > 1:
            if symbol.upper().endswith("/USDT") or symbol.upper().endswith("USDT"):
                usdt_candidates = [
                    candidate
                    for candidate in sorted(candidates, key=lambda candidate: candidate["id"])
                    if self._candidate_has_usdt_market(candidate["id"], normalized)
                ]
                if len(usdt_candidates) == 1:
                    selected = usdt_candidates[0]
                    resolved_by_strategy = True
                    self._logger.info(
                        "Символ КоинГекко '%s' сопоставлен по точному рынку %s/USDT: %s",
                        symbol,
                        normalized.upper(),
                        selected["id"],
                    )
                elif len(usdt_candidates) > 1:
                    selected = usdt_candidates[0]
                    resolved_by_strategy = True
                    self._logger.warning(
                        "Для '%s' найдено несколько кандидатов КоинГекко по %s/USDT; используется детерминированный вариант: %s",
                        symbol,
                        normalized.upper(),
                        selected["id"],
                    )

            if not resolved_by_strategy:
                by_metrics = self._select_candidate_by_market_metrics(candidates)
                if by_metrics:
                    selected = by_metrics
                    resolved_by_strategy = True
                    self._logger.info(
                        "Неоднозначный символ КоинГекко '%s' сопоставлен по рангу/ликвидности рынка: %s",
                        symbol,
                        selected["id"],
                    )

            if not resolved_by_strategy:
                deterministic_fallback = sorted(candidates, key=lambda candidate: candidate["id"])[0]
                if not deterministic_fallback.get("id"):
                    raise ValueError(
                        f"Не удалось разрешить неоднозначный CoinGecko ID для символа '{symbol}' (normalized='{normalized}'). "
                        f"Кандидаты: {candidates}"
                    )

                selected = deterministic_fallback
                self._logger.warning(
                    "Неоднозначный символ КоинГекко '%s' не удалось разрешить эвристиками рынка; переход на детерминированного кандидата: %s",
                    symbol,
                    selected["id"],
                )

        if not selected.get("id"):
            raise ValueError(
                f"Не удалось определить CoinGecko ID для символа '{symbol}' (normalized='{normalized}') из кандидатов: {candidates}"
            )

        self._symbol_to_id[canonical_symbol] = selected["id"]

        return self._symbol_to_id[canonical_symbol]

    # конец области Приватные

    def get_market_cap(self, symbol: str) -> float:
        """Возвращает капитализацию монеты на нужный момент."""
        canonical_symbol = self._canonical_symbol_key(symbol)
        now = datetime.now(tz=timezone.utc)

        cached = self._market_cap_cache.get(canonical_symbol)
        if cached and cached[1] > now:
            return cached[0]

        coin_id = self._resolve_coin_id(symbol)
        response = self._request(
            endpoint="/coins/markets",
            symbol=symbol,
            params={
                COINGECKO_VS_CURRENCY_KEY: COINGECKO_VS_CURRENCY_USD,
                COINGECKO_PARAM_IDS: coin_id,
                COINGECKO_ORDER_KEY: COINGECKO_ORDER_MARKET_CAP_DESC,
                COINGECKO_PARAM_PER_PAGE: COINGECKO_DEFAULT_PAGE,
                COINGECKO_PARAM_PAGE: COINGECKO_DEFAULT_PAGE,
            },
        )
        payload = response.json()
        if not payload:
            raise ValueError(f"CoinGecko вернул пустые рыночные данные для символа: {symbol}")

        market_cap = float(payload[0].get("market_cap") or 0.0)
        self._market_cap_cache[canonical_symbol] = (market_cap, now + self._cache_ttl)
        self._save_market_cap_cache()
        return market_cap

    def get_top_coins_by_market_cap(self, limit: int) -> list[str]:
        """Возвращает список монет с наибольшей капитализацией."""
        response = self._request(
            endpoint="/coins/markets",
            symbol=f"top_{limit}",
            params={
                COINGECKO_VS_CURRENCY_KEY: COINGECKO_VS_CURRENCY_USD,
                COINGECKO_ORDER_KEY: COINGECKO_ORDER_MARKET_CAP_DESC,
                COINGECKO_PARAM_PER_PAGE: limit,
                COINGECKO_PARAM_PAGE: COINGECKO_DEFAULT_PAGE,
                COINGECKO_PARAM_SPARKLINE: COINGECKO_SPARKLINE_FALSE,
            },
        )

        return [str(item.get("symbol") or "").upper() for item in response.json() if item.get("symbol")]

    def get_total_volumes(self, symbols_or_coin_ids: list[str]) -> dict[str, float]:
        """Возвращает объёмы торгов для списка монет."""
        if not symbols_or_coin_ids:
            return {}

        requested = [str(item).strip() for item in symbols_or_coin_ids if str(item).strip()]
        if not requested:
            return {}

        coin_ids_by_input: dict[str, str] = {}
        for item in requested:
            if "/" in item:
                coin_ids_by_input[item] = self._resolve_coin_id(item)
                continue

            canonical_symbol = self._canonical_symbol_key(item)
            known_coin_id = self._symbol_to_id.get(canonical_symbol)
            if known_coin_id:
                coin_ids_by_input[item] = known_coin_id
                continue

            try:
                coin_ids_by_input[item] = self._resolve_coin_id(item)
            except ValueError:
                coin_ids_by_input[item] = item

        response = self._request(
            endpoint="/coins/markets",
            symbol="volume_batch",
            params={
                COINGECKO_VS_CURRENCY_KEY: COINGECKO_VS_CURRENCY_USD,
                COINGECKO_PARAM_IDS: ",".join(sorted(set(coin_ids_by_input.values()))),
                COINGECKO_ORDER_KEY: COINGECKO_ORDER_MARKET_CAP_DESC,
                COINGECKO_PARAM_PER_PAGE: len(coin_ids_by_input),
                COINGECKO_PARAM_PAGE: COINGECKO_DEFAULT_PAGE,
                COINGECKO_PARAM_SPARKLINE: COINGECKO_SPARKLINE_FALSE,
            },
        )
        payload = response.json()
        volumes_by_coin_id = {
            str(item.get("id") or ""): float(item.get("total_volume") or 0.0)
            for item in payload
            if item.get("id")
        }

        return {
            input_name: volumes_by_coin_id.get(coin_id, 0.0)
            for input_name, coin_id in coin_ids_by_input.items()
        }
