from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List

from config.config import AppConfig, RestDataConfig, ScanConfig, SymbolFiltersConfig
from data_providers import (
    CcxtClient,
    CcxtClientConfig,
    ListingAge,
    SymbolFilter,
    SymbolFilterResult,
    TradingStats,
)

from data_providers.ccxt_client import OHLCV


@dataclass
class SymbolMarketSnapshot:
    """Snapshot of the latest market data tracked for a symbol."""

    symbol: str
    listing_age: ListingAge | None
    stats: TradingStats
    htf_candles: List[OHLCV]
    ltf_candles: List[OHLCV]
    extended_mode: bool
    last_updated: datetime


@dataclass
class MarketScanState:
    """Mutable state of all symbols observed by the market scanner."""

    symbols: Dict[str, SymbolMarketSnapshot] = field(default_factory=dict)

    def update_symbol(self, snapshot: SymbolMarketSnapshot) -> None:
        self.symbols[snapshot.symbol] = snapshot

    def remove_symbols(self, symbols: Iterable[str]) -> None:
        for symbol in symbols:
            self.symbols.pop(symbol, None)


@dataclass
class MarketScanner:
    """Iterative scanner that runs filters and collects market data."""

    client: CcxtClient
    symbol_filter: SymbolFilter
    scan_config: ScanConfig
    rest_config: RestDataConfig
    symbol_filters_config: SymbolFiltersConfig
    now_factory: Callable[[], datetime] = field(default=lambda: datetime.now(tz=timezone.utc))
    logger: logging.Logger = logging.getLogger(__name__)
    state: MarketScanState = field(default_factory=MarketScanState)

    def _should_enable_extended_mode(self, filter_result: SymbolFilterResult, stats: TradingStats) -> bool:
        if not self.rest_config.fetch_ltf:
            return False
        if filter_result["is_new_listing"]:
            return False
        trades = int(stats.get("trades_24h", 0))
        return trades >= self.symbol_filters_config.min_trades_established

    def _collect_symbol_data(self, symbol: str, filter_result: SymbolFilterResult, stats: TradingStats) -> SymbolMarketSnapshot:
        htf_candles = self.client.fetch_htf_ohlcv(symbol)
        extended_mode = self._should_enable_extended_mode(filter_result, stats)
        ltf_candles = self.client.fetch_ltf_ohlcv(symbol) if extended_mode else []
        snapshot = SymbolMarketSnapshot(
            symbol=symbol,
            listing_age=filter_result["listing_age"],
            stats=stats,
            htf_candles=htf_candles,
            ltf_candles=ltf_candles,
            extended_mode=extended_mode,
            last_updated=self.now_factory(),
        )
        return snapshot

    def _run_filters(self, symbol: str) -> tuple[bool, SymbolFilterResult, TradingStats]:
        market = self.client.get_market(symbol)
        stats = self.client.fetch_trading_stats(symbol, avg_days=self.symbol_filters_config.n_avg_days)
        filter_result = self.symbol_filter.evaluate(symbol, market, stats)
        return filter_result["allowed"], filter_result, stats

    def scan_once(self) -> MarketScanState:
        futures_symbols = self.client.get_futures_symbols()
        observed_now: set[str] = set()
        for symbol in futures_symbols:
            allowed, filter_result, stats = self._run_filters(symbol)
            if not allowed:
                self.state.remove_symbols([symbol])
                continue
            observed_now.add(symbol)
            snapshot = self._collect_symbol_data(symbol, filter_result, stats)
            self.state.update_symbol(snapshot)

        stale_symbols = [symbol for symbol in self.state.symbols if symbol not in observed_now]
        if stale_symbols:
            self.state.remove_symbols(stale_symbols)
        return self.state

    def run_forever(self) -> None:
        interval_seconds = max(self.scan_config.market_scan_interval_h, 1) * 3600
        while True:
            start = self.now_factory()
            self.logger.info("Запуск сканирования рынка")
            try:
                self.scan_once()
            except Exception as exc:  # pragma: no cover - safeguard for runtime
                self.logger.exception("Ошибка сканирования рынка: %s", exc)
            elapsed = (self.now_factory() - start).total_seconds()
            sleep_for = max(interval_seconds - elapsed, 0.0)
            if sleep_for:
                time.sleep(sleep_for)


def build_market_scanner(config: AppConfig) -> MarketScanner:
    client_config = CcxtClientConfig(
        exchange_name=config.exchange.name,
        api_key=config.exchange.api_key,
        api_secret=config.exchange.api_secret,
        htf=config.timeframes.htf,
        ltf=config.timeframes.ltf,
        rest_htf=config.rest_data.fetch_htf,
        rest_ltf=config.rest_data.fetch_ltf,
    )
    client = CcxtClient(client_config)
    symbol_filter = SymbolFilter(config.symbol_filters)
    return MarketScanner(
        client=client,
        symbol_filter=symbol_filter,
        scan_config=config.scan,
        rest_config=config.rest_data,
        symbol_filters_config=config.symbol_filters,
    )


__all__ = [
    "MarketScanner",
    "MarketScanState",
    "SymbolMarketSnapshot",
    "build_market_scanner",
]
