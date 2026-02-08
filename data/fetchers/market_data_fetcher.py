"""High-level coordinator for OHLCV, OI and market-cap loading."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from constants import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
)
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from domain.abstract.market_data_client import MarketDataClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.fetch_all_result import FetchAllResult
from domain.models.reporting.market_caps_result import MarketCapsResult
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger


class MarketDataFetcher:
    """Coordinates OHLCV/OI downloads and market-cap fetching."""

    def __init__(
        self,
        ohlcv_fetcher: OhlcvFetcher,
        oi_fetcher: OiFetcher,
        market_data_client: MarketDataClient,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        log_level: int | str = DEFAULT_LOG_LEVEL,
        logs_dir: str | Path = DEFAULT_LOGS_DIR,
    ) -> None:
        self._ohlcv_fetcher = ohlcv_fetcher
        self._oi_fetcher = oi_fetcher
        self._market_data_client = market_data_client
        self._request_timeout_seconds = request_timeout_seconds
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)

    def fetch_market_caps(self, symbols: list[str]) -> MarketCapsResult:
        self._logger.info(f"MarketCap старт: {len(symbols)} инструментов")
        results: dict[str, float | str] = {}
        for symbol in symbols:
            try:
                results[symbol] = self._market_data_client.get_market_cap(symbol)
                self._logger.info(f"MarketCap готово: {symbol}")
            except Exception as exc:  # noqa: BLE001
                msg = f"MarketCap ошибка исполнения: {symbol}: {exc}"
                self._logger.info(msg)
                results[symbol] = msg

        self._logger.info("MarketCap завершен")
        return MarketCapsResult(market_caps=results)

    def _log_stage_summary(self, stage: str, total: int, ok: int, failed: int) -> None:
        self._logger.info("%s summary: total=%s ok=%s failed=%s", stage, total, ok, failed)

    @staticmethod
    def _count_structured_results(results: dict[str, SymbolFetchResult]) -> tuple[int, int, int]:
        total = len(results)
        ok = sum(1 for result in results.values() if result.success)
        return total, ok, total - ok

    @staticmethod
    def _count_market_caps_results(results: MarketCapsResult) -> tuple[int, int, int]:
        total = len(results.market_caps)
        ok = sum(1 for value in results.market_caps.values() if isinstance(value, (int, float)))
        return total, ok, total - ok

    def fetch_all(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchAllResult:
        self._logger.info(f"Загрузка старт: {len(symbols)} символов, TF={timeframe.value}")

        ohlcv_result = self._ohlcv_fetcher.fetch_many(
            symbols,
            timeframe,
            start_time,
            end_time,
        )
        oi_result = self._oi_fetcher.fetch_many(
            symbols,
            timeframe,
            start_time,
            end_time,
        )

        market_caps = self.fetch_market_caps(symbols)

        ohlcv_total, ohlcv_ok, ohlcv_failed = self._count_structured_results(ohlcv_result)
        oi_total, oi_ok, oi_failed = self._count_structured_results(oi_result)
        mcap_total, mcap_ok, mcap_failed = self._count_market_caps_results(market_caps)

        self._log_stage_summary("OHLCV", ohlcv_total, ohlcv_ok, ohlcv_failed)
        self._log_stage_summary("OI", oi_total, oi_ok, oi_failed)
        self._log_stage_summary("MarketCap", mcap_total, mcap_ok, mcap_failed)

        failed_symbols_count = len(
            {
                symbol
                for symbol in symbols
                if (symbol in ohlcv_result and not ohlcv_result[symbol].success)
                or (symbol in oi_result and not oi_result[symbol].success)
                or isinstance(market_caps.market_caps.get(symbol), str)
            }
        )
        has_errors = failed_symbols_count > 0

        self._logger.info("Загрузка завершена")

        return FetchAllResult(
            ohlcv=ohlcv_result,
            open_interest=oi_result,
            market_caps=market_caps,
            failed_symbols_count=failed_symbols_count,
            has_errors=has_errors,
        )
