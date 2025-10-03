from __future__ import annotations

import json
from typing import Callable, Iterable, Optional, Sequence, Tuple
from urllib import parse
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
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._exchange = exchange
        self._profile = profile
        self._thresholds = thresholds
        self._timeout = timeout
        self._log = log
        self._last_symbols: Tuple[str, ...] = ()

    def scan(self) -> Tuple[str, ...]:
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

    def _scan_binance(self) -> Tuple[str, ...]:
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        with urlopen(request, timeout=self._timeout) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        entries = ((item.get("symbol", ""), item.get("quoteVolume", "0")) for item in data)
        return self._filter_and_sort(entries)

    def _scan_bybit(self) -> Tuple[str, ...]:
        params = parse.urlencode({"category": "linear"})
        url = f"https://api.bybit.com/v5/market/tickers?{params}"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        with urlopen(request, timeout=self._timeout) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
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

    def _filter_and_sort(self, entries: Iterable[Tuple[object, object]]) -> Tuple[str, ...]:
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
        return tuple(symbol for symbol, _ in pairs)

    def _resolve_threshold(self) -> float:
        thresholds = self._thresholds
        if self._profile is TradingProfile.TOP:
            return float(thresholds.top_usd)
        if self._profile is TradingProfile.ALT:
            return float(thresholds.alt_usd)
        if self._profile is TradingProfile.LISTING:
            return float(thresholds.listing_usd)
        return float(thresholds.top_usd)


__all__ = ["MarketScanner"]
