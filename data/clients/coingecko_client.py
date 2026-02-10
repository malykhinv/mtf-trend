"""Модуль проекта."""

from __future__ import annotations

import logging
import os
import random
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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


class CoinGeckoClient(MarketDataClient):
    """Класс."""
    BASE_URL = COINGECKO_BASE_URL
    TICKER_CHECK_TOP_K = 5

    def __init__(
        self,
        api_key: str = "",
        cache_ttl_hours: int = 24,
        cache_path: str | Path | None = None,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        min_request_interval_seconds: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._cache_ttl = timedelta(hours=cache_ttl_hours)
        self._market_cap_cache: dict[str, tuple[float, datetime]] = {}
        self._symbol_to_id: dict[str, str] = {}
        self._coins_list_cache: list[dict[str, str]] | None = None
        self._coins_by_symbol_index: dict[str, list[dict[str, str]]] = {}
        self._cache_path = Path(cache_path) if cache_path else None
        self._logger = logging.getLogger(self.__class__.__name__)
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._min_request_interval_seconds = max(0.0, float(min_request_interval_seconds))
        self._last_request_monotonic: float | None = None
        self._load_market_cap_cache()

    # region Приватные

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
            required_columns = {"symbol"}
            if not required_columns.issubset(raw_payload.columns):
                raise ValueError(f"Кэш должен содержать колонки: {required_columns}")

            now = datetime.now(tz=timezone.utc)
            loaded_cache: dict[str, tuple[float, datetime]] = {}
            has_market_cap = {"market_cap", "expires_at"}.issubset(raw_payload.columns)
            has_coin_id = "coin_id" in raw_payload.columns
            load_columns = ["symbol"]
            if has_market_cap:
                load_columns.extend(["market_cap", "expires_at"])
            if has_coin_id:
                load_columns.append("coin_id")
            for row in raw_payload[load_columns].itertuples(index=False):
                symbol = row.symbol
                if not isinstance(symbol, str) or not symbol:
                    continue
                canonical_symbol = self._canonical_symbol_key(symbol)
                if not canonical_symbol:
                    continue

                if has_market_cap:
                    expires_at_dt = pd.Timestamp(row.expires_at)
                    if not pd.isna(expires_at_dt):
                        if expires_at_dt.tzinfo is None:
                            expires_at_dt = expires_at_dt.tz_localize(timezone.utc)
                        else:
                            expires_at_dt = expires_at_dt.tz_convert(timezone.utc)

                        expires_at = expires_at_dt.to_pydatetime()
                        market_cap = row.market_cap
                        if expires_at > now and pd.notna(market_cap):
                            loaded_cache[canonical_symbol] = (float(market_cap), expires_at)
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

        records_by_symbol: dict[str, dict[str, object]] = {}
        for symbol, coin_id in self._symbol_to_id.items():
            records_by_symbol[symbol] = {
                "symbol": symbol,
                "market_cap": None,
                "expires_at": None,
                "coin_id": coin_id,
            }

        for symbol, (market_cap, expires_at) in self._market_cap_cache.items():
            record = records_by_symbol.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "market_cap": None,
                    "expires_at": None,
                    "coin_id": self._symbol_to_id.get(symbol, ""),
                },
            )
            record["market_cap"] = market_cap
            record["expires_at"] = pd.Timestamp(expires_at).tz_convert(timezone.utc)
            record["coin_id"] = self._symbol_to_id.get(symbol, "")

        records = list(records_by_symbol.values())
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

    @staticmethod
    def _parse_retry_after_seconds(header_value: str | None) -> float | None:
        if not header_value:
            return None

        normalized = header_value.strip()
        if not normalized:
            return None

        try:
            return max(float(normalized), 0.0)
        except ValueError:
            pass

        try:
            retry_at = parsedate_to_datetime(normalized)
        except (TypeError, ValueError):
            return None

        now = datetime.now(tz=timezone.utc)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max((retry_at - now).total_seconds(), 0.0)

    def _request(self, endpoint: str, symbol: str, params: dict[str, str | int] | None = None) -> requests.Response:
        last_error: requests.RequestException | None = None
        last_status_code: int | None = None

        for attempt_number in range(1, self._retry_attempts + 1):
            try:
                if self._last_request_monotonic is not None and self._min_request_interval_seconds > 0:
                    elapsed = time.monotonic() - self._last_request_monotonic
                    wait_seconds = self._min_request_interval_seconds - elapsed
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)

                response = requests.get(
                    url=f"{self.BASE_URL}{endpoint}",
                    headers=self._headers(),
                    params=params,
                    timeout=COINGECKO_TIMEOUT_SECONDS,
                )
                self._last_request_monotonic = time.monotonic()
                response.raise_for_status()
                self._logger.info(
                    "повтор операция=%s попытка=%s/%s эндпоинт=%s символ=%s результат=успех",
                    "coingecko_get",
                    attempt_number,
                    self._retry_attempts,
                    endpoint,
                    symbol,
                )
                return response
            except requests.HTTPError as exc:
                self._last_request_monotonic = time.monotonic()
                last_error = exc
                response = exc.response
                status_code = response.status_code if response is not None else None
                last_status_code = status_code

                if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                    raise RuntimeError(
                        f"Неретраимая HTTP ошибка: статус={status_code} эндпоинт={endpoint} символ={symbol}"
                    ) from exc

                is_last = attempt_number >= self._retry_attempts
                if is_last:
                    break

                jitter = random.uniform(0.0, 0.25)
                if status_code == 429:
                    retry_after_header = response.headers.get("Retry-After") if response is not None else None
                    retry_after_seconds = self._parse_retry_after_seconds(retry_after_header)
                    exponential_backoff = self._retry_backoff_seconds * (2 ** (attempt_number - 1)) + jitter
                    sleep_seconds = max(retry_after_seconds or 0.0, exponential_backoff)
                elif status_code is not None and 500 <= status_code < 600:
                    sleep_seconds = self._retry_backoff_seconds * 1.5 * (2 ** (attempt_number - 1)) + jitter
                else:
                    sleep_seconds = self._retry_backoff_seconds * (2 ** (attempt_number - 1)) + jitter

                self._logger.warning(
                    "повтор операция=%s попытка=%s/%s эндпоинт=%s символ=%s статус=%s задержка=%.2fs причина=%s",
                    "coingecko_get",
                    attempt_number,
                    self._retry_attempts,
                    endpoint,
                    symbol,
                    status_code,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)
            except requests.RequestException as exc:
                self._last_request_monotonic = time.monotonic()
                last_error = exc
                last_status_code = None
                is_last = attempt_number >= self._retry_attempts
                if is_last:
                    break

                sleep_seconds = self._retry_backoff_seconds * (2 ** (attempt_number - 1)) + random.uniform(0.0, 0.25)
                self._logger.warning(
                    "повтор операция=%s попытка=%s/%s эндпоинт=%s символ=%s задержка=%.2fs причина=%s",
                    "coingecko_get",
                    attempt_number,
                    self._retry_attempts,
                    endpoint,
                    symbol,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)

        details = (
            f"{LOG_MSG_RETRY_EXHAUSTED % (endpoint, symbol, self._retry_attempts)}; "
            f"статус={last_status_code}; эндпоинт={endpoint}"
        )
        raise RuntimeError(details) from last_error

    def _candidate_has_usdt_market(self, coin_id: str, base_symbol: str) -> bool:
        response = self._request(endpoint=f"/coins/{coin_id}/tickers", symbol=base_symbol)
        for ticker in response.json().get("tickers", []):
            base = str(ticker.get("base") or "").lower()
            target = str(ticker.get("target") or "").lower()
            if base == base_symbol and target == "usdt":
                return True
        return False

    def _load_coins_list_index(self, symbol: str) -> None:
        if self._coins_list_cache is not None:
            return

        response = self._request(endpoint="/coins/list", symbol=symbol)
        payload = response.json()
        if not isinstance(payload, list):
            payload = []

        self._coins_list_cache = payload
        index: dict[str, list[dict[str, str]]] = {}
        for item in payload:
            item_symbol = str(item.get("symbol") or "").lower()
            coin_id = str(item.get("id") or "")
            if not item_symbol or not coin_id:
                continue
            index.setdefault(item_symbol, []).append(
                {
                    "id": coin_id,
                    "name": str(item.get("name") or ""),
                    "symbol": item_symbol,
                }
            )

        for item_symbol, candidates in index.items():
            index[item_symbol] = sorted(candidates, key=lambda candidate: candidate["id"])
        self._coins_by_symbol_index = index

    @staticmethod
    def _deterministic_candidate_score(candidate: dict[str, str], normalized_symbol: str) -> tuple[int, int, str]:
        coin_id = candidate.get("id") or ""
        candidate_name = (candidate.get("name") or "").lower()
        prefix = f"{normalized_symbol}-"
        if coin_id == normalized_symbol:
            priority = 0
        elif coin_id.startswith(prefix):
            priority = 1
        elif candidate_name == normalized_symbol:
            priority = 2
        elif candidate_name.startswith(f"{normalized_symbol} "):
            priority = 3
        else:
            priority = 4
        return priority, len(coin_id), coin_id

    def _select_candidate_deterministically(
        self,
        candidates: list[dict[str, str]],
        normalized_symbol: str,
    ) -> tuple[dict[str, str] | None, bool]:
        if not candidates:
            return None, False
        if len(candidates) == 1:
            return candidates[0], True

        ranked = sorted(candidates, key=lambda candidate: self._deterministic_candidate_score(candidate, normalized_symbol))
        best = ranked[0]
        best_score = self._deterministic_candidate_score(best, normalized_symbol)
        is_unique_best = sum(1 for candidate in ranked if self._deterministic_candidate_score(candidate, normalized_symbol) == best_score) == 1
        return best, is_unique_best

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

        self._load_coins_list_index(symbol)
        candidates = list(self._coins_by_symbol_index.get(normalized, []))

        if not candidates:
            raise ValueError(f"Не удалось определить CoinGecko ID для символа: {symbol}")

        selected, resolved_by_strategy = self._select_candidate_deterministically(candidates, normalized)
        if selected is None:
            raise ValueError(f"Не удалось определить CoinGecko ID для символа: {symbol}")

        if len(candidates) > 1:
            if not resolved_by_strategy and (symbol.upper().endswith("/USDT") or symbol.upper().endswith("USDT")):
                top_candidates = sorted(
                    candidates,
                    key=lambda candidate: self._deterministic_candidate_score(candidate, normalized),
                )[: self.TICKER_CHECK_TOP_K]
                usdt_candidates = [
                    candidate
                    for candidate in top_candidates
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
                        "Для '%s' найдено несколько кандидатов КоинГекко по %s/USDT среди top-%s; используется детерминированный вариант: %s",
                        symbol,
                        normalized.upper(),
                        self.TICKER_CHECK_TOP_K,
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
        self._save_market_cap_cache()

        return self._symbol_to_id[canonical_symbol]

    # endregion Приватные

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
