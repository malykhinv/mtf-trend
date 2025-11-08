from __future__ import annotations

import json
import math
import socket
import time
from http.client import RemoteDisconnected
from typing import Callable, Iterable, Mapping, Optional, Sequence, Tuple
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.models.exchange_name import ExchangeName
from config.models.profile_stream_weights import ProfileStreamWeights
from config.models.trading_profile import TradingProfile
from config.models.turnover_thresholds import TurnoverThresholds


class MarketScanner:
    def __init__(
            self,
            exchange: ExchangeName,
            profile: TradingProfile,
            thresholds: TurnoverThresholds,
            profile_weights: ProfileStreamWeights,
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
        self._last_weights: dict[str, dict[str, float]] = {}
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
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        request = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._request_json(request)
        symbols: Sequence[dict[str, object]] = []
        if isinstance(data, Sequence):
            symbols = [item for item in data if isinstance(item, dict)]
        allowed = exchange_info or self._binance_exchange_info
        seen: set[str] = set()
        entries: list[dict[str, object]] = []
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
            entries.append(
                {
                    "symbol": symbol,
                    "turnover": item.get("quoteVolume")
                                or item.get("volume"),
                    "trade_count": item.get("count"),
                    "price_change": item.get("priceChangePercent"),
                }
            )
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
        normalized_rows: list[dict[str, object]] = []
        for row in entries:
            if not isinstance(row, Mapping):
                continue
            symbol = row.get("symbol", "")
            turnover = (
                    row.get("turnover24h")
                    or row.get("turnover24Hours")
                    or row.get("volume24h")
            )
            trade_count = (
                    row.get("tradeCnt24h")
                    or row.get("tradeCnt24Hours")
                    or row.get("tradeCount24h")
                    or row.get("tradeCount")
            )
            price_change = row.get("price24hPcnt")
            if price_change not in (None, ""):
                try:
                    price_change = float(price_change) * 100.0
                except (TypeError, ValueError):
                    price_change = None
            normalized_rows.append(
                {
                    "symbol": symbol,
                    "turnover": turnover,
                    "trade_count": trade_count,
                    "price_change": price_change,
                }
            )
        return self._filter_and_sort(normalized_rows)

    def _load_binance_exchange_info(
            self, *, refresh: bool = False
    ) -> Mapping[str, dict[str, object]]:
        if self._binance_exchange_info and not refresh:
            fetched_at = self._exchange_info_fetched_at or 0.0
            if time.time() - fetched_at < self._exchange_info_ttl:
                return self._binance_exchange_info
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
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
        symbols: Sequence[Mapping[str, object]] = ()
        if isinstance(payload, Mapping):
            raw_symbols = payload.get("symbols")
            if isinstance(raw_symbols, Sequence) and not isinstance(raw_symbols, (str, bytes)):
                symbols = tuple(
                    item for item in raw_symbols if isinstance(item, Mapping)
                )
        existing = self._binance_exchange_info
        result: dict[str, dict[str, object]] = {}
        target_quote_asset = "USDT"
        for item in symbols:
            raw_symbol = item.get("symbol")
            if not raw_symbol:
                continue
            symbol = str(raw_symbol).upper()
            status = str(item.get("status") or "").upper()
            contract_status = str(item.get("contractStatus") or "").upper()
            if contract_status:
                if contract_status != "TRADING":
                    continue
            elif status != "TRADING":
                continue
            contract_type = str(item.get("contractType") or "").upper()
            if contract_type not in {"PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER"}:
                continue
            quote_asset = str(item.get("quoteAsset") or "").upper()
            if target_quote_asset and quote_asset != target_quote_asset:
                continue
            permissions = item.get("permissions")
            if not (
                    isinstance(permissions, Sequence)
                    and not isinstance(permissions, (str, bytes))
            ):
                continue
            normalized_permissions = {str(entry).upper() for entry in permissions}
            if not normalized_permissions.intersection({"UMFUTURE", "CMFUTURE"}):
                continue
            permissions_tuple = tuple(str(entry) for entry in permissions)
            filters = item.get("filters")
            filter_entries: Sequence[Mapping[str, object]] = ()
            if isinstance(filters, Sequence) and not isinstance(filters, (str, bytes)):
                filter_entries = tuple(
                    entry for entry in filters if isinstance(entry, Mapping)
                )

            def _find_filter(filter_type: str) -> Mapping[str, object]:
                return next(
                    (
                        entry
                        for entry in filter_entries
                        if str(entry.get("filterType") or "").upper() == filter_type
                    ),
                    {},
                )

            price_filter: Mapping[str, object] = _find_filter("PRICE_FILTER")
            lot_filter: Mapping[str, object] = _find_filter("LOT_SIZE")
            market_lot_filter: Mapping[str, object] = _find_filter("MARKET_LOT_SIZE")
            notional_filter: Mapping[str, object] = next(
                (
                    entry
                    for entry in filter_entries
                    if str(entry.get("filterType") or "").upper() in {"MIN_NOTIONAL", "NOTIONAL"}
                ),
                {},
            )
            percent_price_filter: Mapping[str, object] = _find_filter("PERCENT_PRICE")
            current = existing.get(symbol) if isinstance(existing, Mapping) else None
            previous_tick = 0.1
            previous_step = 0.001
            previous_market_step = previous_step
            previous_notional = 5.0
            previous_multiplier_down = 0.0
            previous_multiplier_up = 0.0
            if isinstance(current, Mapping):
                current_filters = current.get("filters")
                if isinstance(current_filters, Mapping):
                    prev_price = current_filters.get("price")
                    if isinstance(prev_price, Mapping):
                        previous_tick = self._safe_float(
                            prev_price.get("tickSize"), previous_tick
                        )
                    prev_lot = current_filters.get("lot")
                    if isinstance(prev_lot, Mapping):
                        previous_step = self._safe_float(
                            prev_lot.get("stepSize"), previous_step
                        )
                    prev_market_lot = current_filters.get("marketLot")
                    if isinstance(prev_market_lot, Mapping):
                        previous_market_step = self._safe_float(
                            prev_market_lot.get("stepSize"), previous_market_step
                        )
                    prev_notional = current_filters.get("notional")
                    if isinstance(prev_notional, Mapping):
                        previous_notional = self._safe_float(
                            prev_notional.get("minNotional"), previous_notional
                        )
                    prev_percent = current_filters.get("percentPrice")
                    if isinstance(prev_percent, Mapping):
                        previous_multiplier_down = self._safe_float(
                            prev_percent.get("multiplierDown"), previous_multiplier_down
                        )
                        previous_multiplier_up = self._safe_float(
                            prev_percent.get("multiplierUp"), previous_multiplier_up
                        )
                else:
                    previous_tick = self._safe_float(current.get("tickSize"), previous_tick)
                    previous_step = self._safe_float(current.get("stepSize"), previous_step)
                    previous_notional = self._safe_float(current.get("notional"), previous_notional)
            min_price_value = max(self._safe_float(price_filter.get("minPrice"), 0.0), 0.0)
            max_price_value = self._safe_float(price_filter.get("maxPrice"), math.inf)
            if max_price_value <= 0.0:
                max_price_value = math.inf
            tick_size_value = max(
                self._safe_float(price_filter.get("tickSize"), previous_tick),
                10 ** -8,
            )
            step_size_value = max(
                self._safe_float(lot_filter.get("stepSize"), previous_step),
                10 ** -8,
            )
            min_qty_value = max(
                self._safe_float(lot_filter.get("minQty"), previous_step),
                step_size_value,
            )
            max_qty_value = self._safe_float(lot_filter.get("maxQty"), math.inf)
            if max_qty_value <= 0.0:
                max_qty_value = math.inf
            market_step_size_value = max(
                self._safe_float(market_lot_filter.get("stepSize"), previous_market_step),
                10 ** -8,
            )
            market_min_qty_value = max(
                self._safe_float(market_lot_filter.get("minQty"), market_step_size_value),
                market_step_size_value,
            )
            market_max_qty_value = self._safe_float(market_lot_filter.get("maxQty"), math.inf)
            if market_max_qty_value <= 0.0:
                market_max_qty_value = math.inf
            min_notional_value = max(
                self._safe_float(
                    notional_filter.get("minNotional")
                    or notional_filter.get("notional"),
                    previous_notional,
                ),
                0.0,
            )
            multiplier_down_value = max(
                self._safe_float(
                    percent_price_filter.get("multiplierDown"), previous_multiplier_down
                ),
                0.0,
            )
            multiplier_up_value = max(
                self._safe_float(
                    percent_price_filter.get("multiplierUp"), previous_multiplier_up
                ),
                0.0,
            )
            price_filters = {
                "minPrice": min_price_value,
                "maxPrice": max_price_value,
                "tickSize": tick_size_value,
            }
            lot_filters = {
                "minQty": min_qty_value,
                "maxQty": max_qty_value,
                "stepSize": step_size_value,
            }
            market_lot_filters = {
                "minQty": market_min_qty_value,
                "maxQty": market_max_qty_value,
                "stepSize": market_step_size_value,
            }
            filters_payload = {
                "price": price_filters,
                "lot": lot_filters,
                "marketLot": market_lot_filters,
                "notional": {
                    "minNotional": min_notional_value,
                },
                "percentPrice": {
                    "multiplierDown": multiplier_down_value,
                    "multiplierUp": multiplier_up_value,
                    "multiplierDecimal": percent_price_filter.get("multiplierDecimal"),
                },
            }
            listing_date = item.get("listingDate")
            if listing_date is None:
                listing_date = item.get("onboardDate")
            entry_payload: dict[str, object] = {
                "baseAsset": str(item.get("baseAsset", "")),
                "quoteAsset": str(item.get("quoteAsset", "")),
                "marginAsset": str(item.get("marginAsset", "")),
                "contractType": contract_type,
                "contractStatus": contract_status or status,
                "tickSize": tick_size_value,
                "stepSize": step_size_value,
                "lotSize": lot_filters,
                "marketLotSize": market_lot_filters,
                "notional": min_notional_value,
                "filters": filters_payload,
                "listingDate": listing_date,
                "meta": {
                    "status": status,
                    "contractStatus": contract_status or status,
                    "contractType": contract_type,
                    "onboardDate": item.get("onboardDate"),
                    "listingDate": listing_date,
                    "permissions": permissions_tuple,
                    "baseAssetPrecision": item.get("baseAssetPrecision"),
                    "quoteAssetPrecision": item.get("quoteAssetPrecision"),
                    "quotePrecision": item.get("quotePrecision"),
                    "pricePrecision": item.get("pricePrecision"),
                    "quantityPrecision": item.get("quantityPrecision"),
                    "deliveryDate": item.get("deliveryDate"),
                    "pair": item.get("pair"),
                },
            }
            if isinstance(current, Mapping) and "profile" in current:
                entry_payload["profile"] = current["profile"]
            result[symbol] = entry_payload
        return result

    @staticmethod
    def _safe_float(value: object, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _extract_listing_dates(
            self, exchange_info: Mapping[str, Mapping[str, object]]
    ) -> dict[str, int]:
        listing_dates: dict[str, int] = {}
        for symbol, item in exchange_info.items():
            raw_onboard = item.get("listingDate")
            if raw_onboard is None:
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
            self, entries: Iterable[Mapping[str, object] | Sequence[object]]
    ) -> Tuple[Tuple[str, TradingProfile], ...]:
        threshold = self._resolve_threshold()
        minutes_per_day = 1440.0
        normalized: list[Tuple[str, float, float, float | None]] = []
        btc_trades: float | None = None
        for entry in entries:
            symbol: str
            turnover_value: object
            trade_count_value: object | None
            price_change_value: object | None
            if isinstance(entry, Mapping):
                symbol = str(entry.get("symbol") or "")
                turnover_value = entry.get("turnover")
                trade_count_value = entry.get("trade_count")
                price_change_value = entry.get("price_change")
            elif isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)):
                if not entry:
                    continue
                symbol = str(entry[0] or "")
                turnover_value = entry[1] if len(entry) > 1 else None
                trade_count_value = entry[2] if len(entry) > 2 else None
                price_change_value = entry[3] if len(entry) > 3 else None
            else:
                continue
            symbol = symbol.upper()
            if not symbol.endswith("USDT"):
                continue
            try:
                turnover_24h = float(turnover_value or 0.0)
            except (TypeError, ValueError):
                continue
            trade_count: float | None = None
            if trade_count_value not in (None, ""):
                try:
                    trade_count = float(trade_count_value)
                except (TypeError, ValueError):
                    trade_count = None
            if trade_count is None:
                continue
            price_change: float | None = None
            if price_change_value not in (None, ""):
                try:
                    price_change = float(price_change_value)
                except (TypeError, ValueError):
                    price_change = None
            normalized.append((symbol, turnover_24h, trade_count, price_change))
            if symbol == "BTCUSDT" and trade_count is not None:
                btc_trades = trade_count
        trade_threshold = 0.0
        if btc_trades is not None:
            trade_threshold = min(1_000_000.0, 0.5 * btc_trades)
        pairs: list[Tuple[str, float]] = []
        for symbol, turnover_24h, trade_count, price_change in normalized:
            meets_trade_requirement = True
            if trade_threshold > 0.0:
                meets_trade_requirement = trade_count >= trade_threshold
            meets_price_requirement = False
            if price_change is not None:
                meets_price_requirement = abs(price_change) <= 15.0
            if not meets_trade_requirement and not meets_price_requirement:
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
                self._store_symbol_profile(symbol, profile)
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
            self._last_weights[symbol] = dict(default_weight)
            self._store_symbol_profile(symbol, default_profile)
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

    def _resolve_weight(self, profile: TradingProfile) -> dict[str, float]:
        stream_weights = self._profile_weights

        def resolve_for_stream(name: str) -> float:
            stream_profile = getattr(stream_weights, name)
            if profile is TradingProfile.TOP:
                return float(stream_profile.top)
            if profile is TradingProfile.ALT:
                return float(stream_profile.alt)
            if profile is TradingProfile.LISTING:
                return float(stream_profile.listing)
            return float(stream_profile.auto)

        return {
            "depth": resolve_for_stream("depth"),
            "trades": resolve_for_stream("trades"),
            "book_ticker": resolve_for_stream("book_ticker"),
        }

    @property
    def symbol_weights(self) -> dict[str, dict[str, float]]:
        return {symbol: dict(weights) for symbol, weights in self._last_weights.items()}

    @property
    def binance_exchange_info(self) -> dict[str, dict[str, object]]:
        return dict(self._binance_exchange_info)

    def get_symbol_weight(self, symbol: str) -> dict[str, float]:
        normalized = symbol.upper()
        weight = self._last_weights.get(normalized)
        if weight is not None:
            return dict(weight)
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

    def _store_symbol_profile(self, symbol: str, profile: TradingProfile) -> None:
        if not symbol.upper().endswith("USDT"):
            return
        entry = self._binance_exchange_info.get(symbol.upper())
        if not isinstance(entry, dict):
            return
        updated = dict(entry)
        updated["profile"] = profile
        self._binance_exchange_info[symbol.upper()] = updated

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
