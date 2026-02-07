"""CoinGecko market data adapter with in-memory market-cap cache."""

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
)
from domain.abstract.market_data_client import MarketDataClient


class CoinGeckoClient(MarketDataClient):
    """Market cap provider backed by CoinGecko REST API."""

    BASE_URL = COINGECKO_BASE_URL

    def __init__(self, api_key: str = "", cache_ttl_hours: int = 24, cache_path: str | Path | None = None) -> None:
        self._api_key = api_key
        self._cache_ttl = timedelta(hours=cache_ttl_hours)
        self._market_cap_cache: dict[str, tuple[float, datetime]] = {}
        self._symbol_to_id: dict[str, str] = {}
        self._cache_path = Path(cache_path) if cache_path else None
        self._logger = logging.getLogger(self.__class__.__name__)
        self._load_market_cap_cache()

    # region Private

    def _load_market_cap_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return

        try:
            raw_payload = pd.read_parquet(self._cache_path)
            required_columns = {"symbol", "market_cap", "expires_at"}
            if not required_columns.issubset(raw_payload.columns):
                raise ValueError(f"cache payload must contain columns: {required_columns}")

            now = datetime.now(tz=timezone.utc)
            loaded_cache: dict[str, tuple[float, datetime]] = {}
            for row in raw_payload[["symbol", "market_cap", "expires_at"]].itertuples(index=False):
                symbol = row.symbol
                if not isinstance(symbol, str) or not symbol:
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

                loaded_cache[symbol.upper()] = (float(row.market_cap), expires_at)

            self._market_cap_cache = loaded_cache
        except (OSError, ValueError, TypeError) as exc:
            self._logger.warning("Failed to load CoinGecko cache from %s: %s", self._cache_path, exc)
            self._market_cap_cache = {}

    def _save_market_cap_cache(self) -> None:
        if self._cache_path is None:
            return

        records = [
            {
                "symbol": symbol,
                "market_cap": market_cap,
                "expires_at": pd.Timestamp(expires_at).tz_convert(timezone.utc),
            }
            for symbol, (market_cap, expires_at) in self._market_cap_cache.items()
        ]
        cache_frame = pd.DataFrame.from_records(records, columns=["symbol", "market_cap", "expires_at"])
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

    def _resolve_coin_id(self, symbol: str) -> str:
        normalized = symbol.lower().replace(SIMULATION_COIN_SUFFIX_SLASH_USDT, "").replace(SIMULATION_COIN_SUFFIX_USDT, "")
        if normalized in self._symbol_to_id:
            return self._symbol_to_id[normalized]

        response = requests.get(
            f"{self.BASE_URL}/coins/list",
            headers=self._headers(),
            timeout=COINGECKO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        for item in response.json():
            item_symbol = str(item.get("symbol") or "").lower()
            if item_symbol == normalized and normalized not in self._symbol_to_id:
                self._symbol_to_id[normalized] = str(item["id"])
                break

        if normalized not in self._symbol_to_id:
            raise ValueError(f"Unable to resolve CoinGecko ID for symbol: {symbol}")

        return self._symbol_to_id[normalized]

    # endregion Private

    def get_market_cap(self, symbol: str) -> float:
        normalized = symbol.upper()
        now = datetime.now(tz=timezone.utc)

        cached = self._market_cap_cache.get(normalized)
        if cached and cached[1] > now:
            return cached[0]

        coin_id = self._resolve_coin_id(symbol)
        response = requests.get(
            f"{self.BASE_URL}/coins/markets",
            headers=self._headers(),
            params={
                COINGECKO_VS_CURRENCY_KEY: COINGECKO_VS_CURRENCY_USD,
                COINGECKO_PARAM_IDS: coin_id,
                COINGECKO_ORDER_KEY: COINGECKO_ORDER_MARKET_CAP_DESC,
                COINGECKO_PARAM_PER_PAGE: COINGECKO_DEFAULT_PAGE,
                COINGECKO_PARAM_PAGE: COINGECKO_DEFAULT_PAGE,
            },
            timeout=COINGECKO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload:
            raise ValueError(f"CoinGecko returned empty market data for symbol: {symbol}")

        market_cap = float(payload[0].get("market_cap") or 0.0)
        self._market_cap_cache[normalized] = (market_cap, now + self._cache_ttl)
        self._save_market_cap_cache()
        return market_cap

    def get_top_coins_by_market_cap(self, limit: int) -> list[str]:
        response = requests.get(
            f"{self.BASE_URL}/coins/markets",
            headers=self._headers(),
            params={
                COINGECKO_VS_CURRENCY_KEY: COINGECKO_VS_CURRENCY_USD,
                COINGECKO_ORDER_KEY: COINGECKO_ORDER_MARKET_CAP_DESC,
                COINGECKO_PARAM_PER_PAGE: limit,
                COINGECKO_PARAM_PAGE: COINGECKO_DEFAULT_PAGE,
                COINGECKO_PARAM_SPARKLINE: COINGECKO_SPARKLINE_FALSE,
            },
            timeout=COINGECKO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        return [str(item.get("symbol") or "").upper() for item in response.json() if item.get("symbol")]
