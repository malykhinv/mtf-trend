from __future__ import annotations

import json
import socket
import time
from http.client import RemoteDisconnected
from typing import Callable, Iterable, Optional, Sequence, Tuple
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.models.exchange_name import ExchangeName
from config.models.trading_profile import TradingProfile
from config.models.turnover_thresholds import TurnoverThresholds


class MarketScanner:
    def __init__(
        self,
        exchange: ExchangeName,
        profile: TradingProfile,
        thresholds: TurnoverThresholds,
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
        self._timeout = timeout
        self._retries = max(0, int(retries))
        self._retry_delay = max(0.0, float(retry_delay))
        self._retry_backoff = max(1.0, float(retry_backoff))
        self._log = log
        self._last_symbols: Tuple[Tuple[str, TradingProfile], ...] = ()
        self._listing_dates: dict[str, int] = {}
        self._recent_listing_cache: dict[str, bool] = {}
        self._degraded_until: dict[str, float] = {}

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
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._request_json(request)
        self._listing_dates = self._fetch_listing_dates()
        self._recent_listing_cache.clear()
        entries = ((item.get("symbol", ""), item.get("quoteVolume", "0")) for item in data)
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
            if self._is_degraded(symbol):
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
        if self._profile is TradingProfile.AUTO:
            classified: list[Tuple[str, TradingProfile]] = []
            for symbol, turnover in pairs:
                profile = self._classify_turnover(symbol, turnover)
                if profile is TradingProfile.LISTING and not self._is_recent_listing(symbol):
                    continue
                classified.append((symbol, profile))
            return tuple(classified)
        return tuple((symbol, self._profile) for symbol, _ in pairs)

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

    def record_degradation(self, symbol: str, hold_seconds: float) -> None:
        normalized = symbol.upper()
        deadline = time.monotonic() + max(0.0, float(hold_seconds))
        self._degraded_until[normalized] = deadline

    def clear_degradation(self, symbol: str) -> None:
        self._degraded_until.pop(symbol.upper(), None)

    def _is_degraded(self, symbol: str) -> bool:
        normalized = symbol.upper()
        expiry = self._degraded_until.get(normalized)
        if expiry is None:
            return False
        if time.monotonic() >= expiry:
            self._degraded_until.pop(normalized, None)
            return False
        return True

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
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._request_json(request)
        symbols: Sequence[dict[str, object]] = []
        if isinstance(data, dict):
            raw_symbols = data.get("symbols")
            if isinstance(raw_symbols, Sequence):
                symbols = [item for item in raw_symbols if isinstance(item, dict)]
        mapping: dict[str, int] = {}
        for item in symbols:
            raw_symbol = item.get("symbol")
            if not raw_symbol:
                continue
            symbol = str(raw_symbol).upper()
            raw_onboard = item.get("onboardDate")
            try:
                onboard_ts = int(str(raw_onboard))
            except (TypeError, ValueError):
                continue
            mapping[symbol] = onboard_ts
        return mapping

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
