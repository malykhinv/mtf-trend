"""CoinGecko market data adapter with in-memory market-cap cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from constants import COINGECKO_TIMEOUT_SECONDS
from domain.abstract.market_data_client import MarketDataClient


class CoinGeckoClient(MarketDataClient):
    """Market cap provider backed by CoinGecko REST API."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self, api_key: str = "", cache_ttl_hours: int = 24) -> None:
        self._api_key = api_key
        self._cache_ttl = timedelta(hours=cache_ttl_hours)
        self._market_cap_cache: dict[str, tuple[float, datetime]] = {}
        self._symbol_to_id: dict[str, str] = {}

    # region Private

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key
        return headers

    def _resolve_coin_id(self, symbol: str) -> str:
        normalized = symbol.lower().replace("/usdt", "").replace("usdt", "")
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
            params={"vs_currency": "usd", "ids": coin_id, "order": "market_cap_desc", "per_page": 1, "page": 1},
            timeout=COINGECKO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload:
            raise ValueError(f"CoinGecko returned empty market data for symbol: {symbol}")

        market_cap = float(payload[0].get("market_cap") or 0.0)
        self._market_cap_cache[normalized] = (market_cap, now + self._cache_ttl)
        return market_cap

    def get_top_coins_by_market_cap(self, limit: int) -> list[str]:
        response = requests.get(
            f"{self.BASE_URL}/coins/markets",
            headers=self._headers(),
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": limit,
                "page": 1,
                "sparkline": "false",
            },
            timeout=COINGECKO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        return [str(item.get("symbol") or "").upper() for item in response.json() if item.get("symbol")]
