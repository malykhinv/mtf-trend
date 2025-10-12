from __future__ import annotations

import json
import socket
import time
from http.client import RemoteDisconnected
from typing import Callable, Iterable, Mapping, Optional, Sequence, Tuple
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.models.exchange_name import ExchangeName
from config.models.profile_weights import ProfileWeights
from config.models.trading_profile import TradingProfile
from config.models.turnover_thresholds import TurnoverThresholds


class MarketScanner:
    def __init__(
        self,
        exchange: ExchangeName,
        profile: TradingProfile,
        thresholds: TurnoverThresholds,
        profile_weights: ProfileWeights,
        *,
        timeout: float = 5.0,
        retries: int = 3,
        retry_delay: float = 0.5,
        retry_backoff: float = 2.0,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._exchange = exchange
        self._profile = profile
        self._thresholds = thresholds
        self._profile_weights = profile_weights
        self._timeout = timeout
        self._retries = max(0, int(retries))
        self._retry_delay = max(0.0, float(retry_delay))
        self._retry_backoff = max(1.0, float(retry_backoff))
        self._log = log
        self._last_symbols: Tuple[Tuple[str, TradingProfile], ...] = ()
        self._last_weights: dict[str, float] = {}
        self._listing_dates: dict[str, int] = {}
        self._recent_listing_cache: dict[str, bool] = {}
        self._binance_exchange_info: dict[str, dict[str, object]] = {}
        self._exchange_info_fetched_at: float | None = None
        self._exchange_info_ttl = 300.0
        self._max_symbols = 550

    def scan(self) -> Tuple[Tuple[str, TradingProfile], ...]:
        try:
            if self._exchange is ExchangeName.BINANCE:
                symbols = self._scan_binance()
            elif self._exchange is ExchangeName.BYBIT:
                symbols = self._scan_bybit()
            else:
                raise RuntimeError("unsupported exchange for scanner")
            if symbols:
                self._last_symbols = symbols
            return self._last_symbols
        except Exception as exc:  # pragma: no cover - defensive logging
            if self._log is not None:
                self._log(f"ошибка сканера: {exc}")
            return self._last_symbols

    def _scan_binance(self) -> Tuple[Tuple[str, TradingProfile], ...]:
        exchange_info = self._load_binance_exchange_info()
        listing_dates = self._extract_listing_dates(exchange_info)
        if listing_dates:
            self._listing_dates = listing_dates
        self._recent_listing_cache.clear()
        url = "https://api.binance.com/api/v3/ticker/24hr"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._request_json(request)
        symbols: Sequence[dict[str, object]] = []
        if isinstance(data, Sequence):
            symbols = [item for item in data if isinstance(item, dict)]
        allowed = exchange_info or self._binance_exchange_info
        seen: set[str] = set()
        entries: list[Tuple[str, object]] = []
        for item in symbols:
            raw_symbol = item.get("symbol")
            if not raw_symbol:
                continue
            symbol = str(raw_symbol).upper()
            if not symbol.endswith("USDT"):
                continue
            if allowed and symbol not in allowed:
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            entries.append((symbol, item.get("quoteVolume") or item.get("volume")))
        return self._filter_and_sort(entries)

    def _scan_bybit(self) -> Tuple[Tuple[str, TradingProfile], ...]:
        params = parse.urlencode({"category": "linear"})
        url = f"https://api.bybit.com/v5/market/tickers?{params}"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._request_json(request)
        if str(data.get("retCode")) not in {"0", "OK"}:
            raise RuntimeError(f"unexpected bybit response code: {data.get('retCode')}")
        result = data.get("result") or {}
        entries: Sequence[dict[str, object]] = result.get("list") or []
        self._listing_dates = self._fetch_listing_dates()
        self._recent_listing_cache.clear()
        rows = (
            (
                row.get("symbol", ""),
                row.get("turnover24h") or row.get("turnover24Hours") or row.get("volume24h"),
            )
            for row in entries
        )
        return self._filter_and_sort(rows)

    def _load_binance_exchange_info(
        self, *, refresh: bool = False
    ) -> Mapping[str, dict[str, object]]:
        if self._binance_exchange_info and not refresh:
            fetched_at = self._exchange_info_fetched_at or 0.0
            if time.time() - fetched_at < self._exchange_info_ttl:
                return self._binance_exchange_info
        url = "https://api.binance.com/api/v3/exchangeInfo"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._request_json(request)
        exchange_info = self._parse_binance_exchange_info(data)
        if exchange_info:
            self._binance_exchange_info = exchange_info
            self._exchange_info_fetched_at = time.time()
        return self._binance_exchange_info

    def _parse_binance_exchange_info(
        self, payload: object
    ) -> dict[str, dict[str, object]]:
        symbols: Sequence[dict[str, object]] = []
        if isinstance(payload, dict):
            raw_symbols = payload.get("symbols")
            if isinstance(raw_symbols, Sequence):
                symbols = [item for item in raw_symbols if isinstance(item, dict)]
        result: dict[str, dict[str, object]] = {}
        for item in symbols:
            raw_symbol = item.get("symbol")
            if not raw_symbol:
                continue
            symbol = str(raw_symbol).upper()
            status = str(item.get("status") or "").upper()
            if status != "TRADING":
                continue
            if not bool(item.get("isSpotTradingAllowed", False)):
                continue
            permissions = item.get("permissions")
            if isinstance(permissions, Sequence) and permissions:
                normalized = {str(entry).upper() for entry in permissions}
                if "SPOT" not in normalized:
                    continue
            result[symbol] = dict(item)
        return result

    def _extract_listing_dates(
        self, exchange_info: Mapping[str, Mapping[str, object]]
    ) -> dict[str, int]:
        listing_dates: dict[str, int] = {}
        for symbol, item in exchange_info.items():
            raw_onboard = item.get("onboardDate")
            try:
                onboard_ts = int(str(raw_onboard))
            except (TypeError, ValueError):
                continue
            listing_dates[symbol] = onboard_ts
        return listing_dates

    def _request_json(self, request: Request) -> dict[str, object] | list[object]:
        retries_remaining = self._retries
        delay = self._retry_delay
        while True:
            try:
                with urlopen(request, timeout=self._timeout) as response:
                    payload = response.read().decode("utf-8")
                return json.loads(payload)
            except HTTPError:
                raise
            except (
                URLError,
                RemoteDisconnected,
                TimeoutError,
                socket.timeout,
                socket.gaierror,
                ConnectionError,
                OSError,
            ):
                if retries_remaining <= 0:
                    raise
                if delay > 0.0:
                    time.sleep(delay)
                retries_remaining -= 1
                delay *= self._retry_backoff

    def _filter_and_sort(
        self, entries: Iterable[Tuple[object, object]]
    ) -> Tuple[Tuple[str, TradingProfile], ...]:
        threshold = self._resolve_threshold()
        minutes_per_day = 1440.0
        pairs: list[Tuple[str, float]] = []
        for raw_symbol, raw_turnover in entries:
            symbol = str(raw_symbol or "").upper()
            if not symbol.endswith("USDT"):
                continue
            try:
                turnover_24h = float(raw_turnover or 0.0)
            except (TypeError, ValueError):
                continue
            turnover_per_minute = turnover_24h / minutes_per_day
            if turnover_per_minute >= threshold:
                if self._profile is TradingProfile.LISTING and not self._is_recent_listing(symbol):
                    continue
                pairs.append((symbol, turnover_per_minute))
        pairs.sort(key=lambda item: item[1], reverse=True)
        max_symbols = self._max_symbols if self._max_symbols > 0 else None
        self._last_weights = {}
        if self._profile is TradingProfile.AUTO:
            classified: list[Tuple[str, TradingProfile]] = []
            for symbol, turnover in pairs:
                profile = self._classify_turnover(symbol, turnover)
                if profile is TradingProfile.LISTING and not self._is_recent_listing(symbol):
                    continue
                self._last_weights[symbol] = self._resolve_weight(profile)
                classified.append((symbol, profile))
                if max_symbols is not None and len(classified) >= max_symbols:
                    break
            return tuple(classified)
        default_profile = self._profile
        default_weight = self._resolve_weight(default_profile)
        result: list[Tuple[str, TradingProfile]] = []
        for symbol, _ in pairs:
            if default_profile is TradingProfile.LISTING and not self._is_recent_listing(symbol):
                continue
            self._last_weights[symbol] = default_weight
            result.append((symbol, default_profile))
            if max_symbols is not None and len(result) >= max_symbols:
                break
        return tuple(result)

    def _resolve_threshold(self) -> float:
        thresholds = self._thresholds
        if self._profile is TradingProfile.AUTO:
            values = (
                float(thresholds.top_usd),
                float(thresholds.listing_usd),
                float(thresholds.alt_usd),
            )
            positives = [value for value in values if value > 0.0]
            if not positives:
                return 0.0
            return min(positives)
        if self._profile is TradingProfile.TOP:
            return float(thresholds.top_usd)
        if self._profile is TradingProfile.ALT:
            return float(thresholds.alt_usd)
        if self._profile is TradingProfile.LISTING:
            return float(thresholds.listing_usd)
        return float(thresholds.top_usd)

    def _resolve_weight(self, profile: TradingProfile) -> float:
        weights = self._profile_weights
        if profile is TradingProfile.TOP:
            return float(weights.top)
        if profile is TradingProfile.ALT:
            return float(weights.alt)
        if profile is TradingProfile.LISTING:
            return float(weights.listing)
        return float(weights.auto)

    @property
    def symbol_weights(self) -> dict[str, float]:
        return dict(self._last_weights)

    def get_symbol_weight(self, symbol: str) -> float:
        normalized = symbol.upper()
        weight = self._last_weights.get(normalized)
        if weight is not None:
            return weight
        if self._profile is TradingProfile.TOP:
            return self._resolve_weight(TradingProfile.TOP)
        if self._profile is TradingProfile.ALT:
            return self._resolve_weight(TradingProfile.ALT)
        if self._profile is TradingProfile.LISTING:
            return self._resolve_weight(TradingProfile.LISTING)
        return self._resolve_weight(TradingProfile.AUTO)

    def _classify_turnover(self, symbol: str, turnover: float) -> TradingProfile:
        thresholds = self._thresholds
        top_threshold = float(thresholds.top_usd)
        listing_threshold = float(thresholds.listing_usd)
        alt_threshold = float(thresholds.alt_usd)
        if turnover >= top_threshold:
            return TradingProfile.TOP
        if turnover >= listing_threshold:
            if self._is_recent_listing(symbol):
                return TradingProfile.LISTING
            if turnover >= alt_threshold:
                return TradingProfile.ALT
            return TradingProfile.ALT
        if turnover >= alt_threshold:
            return TradingProfile.ALT
        return TradingProfile.LISTING

    def _is_recent_listing(self, symbol: str) -> bool:
        normalized = symbol.upper()
        cached = self._recent_listing_cache.get(normalized)
        if cached is not None:
            return cached
        if normalized not in self._listing_dates:
            listing_dates = self._fetch_listing_dates()
            if listing_dates:
                self._listing_dates.update(listing_dates)
        timestamp_ms = self._listing_dates.get(normalized)
        result = False
        if timestamp_ms is not None:
            try:
                launch_ts = float(timestamp_ms)
            except (TypeError, ValueError):
                result = False
            else:
                now_ms = time.time() * 1000.0
                thirty_days_ms = 30.0 * 24.0 * 3600.0 * 1000.0
                result = now_ms - launch_ts <= thirty_days_ms
        self._recent_listing_cache[normalized] = result
        return result

    def _fetch_listing_dates(self) -> dict[str, int]:
        if self._exchange is ExchangeName.BINANCE:
            return self._fetch_binance_listing_dates()
        if self._exchange is ExchangeName.BYBIT:
            return self._fetch_bybit_listing_dates()
        return {}

    def _fetch_binance_listing_dates(self) -> dict[str, int]:
        exchange_info = self._load_binance_exchange_info(refresh=True)
        return self._extract_listing_dates(exchange_info)

    def _fetch_bybit_listing_dates(self) -> dict[str, int]:
        params = parse.urlencode({"category": "linear"})
        url = f"https://api.bybit.com/v5/market/instruments-info?{params}"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._request_json(request)
        if not isinstance(data, dict) or str(data.get("retCode")) not in {"0", "OK"}:
            raise RuntimeError(f"unexpected bybit response code: {getattr(data, 'get', lambda *_: None)('retCode')}")
        result = data.get("result")
        entries: Sequence[dict[str, object]] = []
        if isinstance(result, dict):
            raw_entries = result.get("list")
            if isinstance(raw_entries, Sequence):
                entries = [row for row in raw_entries if isinstance(row, dict)]
        mapping: dict[str, int] = {}
        for row in entries:
            raw_symbol = row.get("symbol")
            if not raw_symbol:
                continue
            symbol = str(raw_symbol).upper()
            raw_time = row.get("listTime")
            try:
                list_ts = int(str(raw_time))
            except (TypeError, ValueError):
                continue
            mapping[symbol] = list_ts
        return mapping


__all__ = ["MarketScanner"]
