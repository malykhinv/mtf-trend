from __future__ import annotations

import json
import socket
import time
from typing import Callable, Iterable, Optional, Sequence, Tuple
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from http.client import RemoteDisconnected

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
                ConnectionError,
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
        pairs: list[Tuple[str, float]] = []
        for raw_symbol, raw_turnover in entries:
            symbol = str(raw_symbol or "").upper()
            if not symbol.endswith("USDT"):
                continue
            try:
                turnover = float(raw_turnover or 0.0)
            except (TypeError, ValueError):
                continue
            if turnover >= threshold:
                pairs.append((symbol, turnover))
        pairs.sort(key=lambda item: item[1], reverse=True)
        if self._profile is TradingProfile.AUTO:
            return tuple((symbol, self._classify_turnover(turnover)) for symbol, turnover in pairs)
        return tuple((symbol, self._profile) for symbol, _ in pairs)

    def _resolve_threshold(self) -> float:
        thresholds = self._thresholds
        if self._profile is TradingProfile.AUTO:
            return float(min(thresholds.top_usd, thresholds.alt_usd, thresholds.listing_usd))
        if self._profile is TradingProfile.TOP:
            return float(thresholds.top_usd)
        if self._profile is TradingProfile.ALT:
            return float(thresholds.alt_usd)
        if self._profile is TradingProfile.LISTING:
            return float(thresholds.listing_usd)
        return float(thresholds.top_usd)

    def _classify_turnover(self, turnover: float) -> TradingProfile:
        thresholds = self._thresholds
        if turnover >= float(thresholds.top_usd):
            return TradingProfile.TOP
        if turnover >= float(thresholds.alt_usd):
            return TradingProfile.ALT
        return TradingProfile.LISTING


__all__ = ["MarketScanner"]
