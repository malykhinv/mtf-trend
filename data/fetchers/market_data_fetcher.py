"""High-level coordinator for OHLCV, OI and market-cap loading."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime
from pathlib import Path
from time import monotonic

from constants import (
    DEFAULT_FETCHER_MAX_WORKERS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    FETCH_ALL_MAX_WORKERS,
)
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from domain.abstract.market_data_client import MarketDataClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.fetch_all_result import FetchAllResult
from domain.models.reporting.market_caps_result import MarketCapsResult
from utils.logger import get_logger


class MarketDataFetcher:
    """Coordinates parallel OHLCV/OI downloads and market-cap fetching."""

    def __init__(
        self,
        ohlcv_fetcher: OhlcvFetcher,
        oi_fetcher: OiFetcher,
        market_data_client: MarketDataClient,
        max_workers: int = DEFAULT_FETCHER_MAX_WORKERS,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        log_level: int | str = DEFAULT_LOG_LEVEL,
        logs_dir: str | Path = DEFAULT_LOGS_DIR,
    ) -> None:
        self._ohlcv_fetcher = ohlcv_fetcher
        self._oi_fetcher = oi_fetcher
        self._market_data_client = market_data_client
        self._max_workers = max_workers
        self._request_timeout_seconds = request_timeout_seconds
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)

    def fetch_market_caps(self, symbols: list[str]) -> MarketCapsResult:
        self._logger.info(f"MarketCap старт: {len(symbols)} инструментов")
        results: dict[str, float | str] = {}

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._market_data_client.get_market_cap, symbol): symbol for symbol in symbols}
            pending = set(futures)
            deadline = monotonic() + self._request_timeout_seconds

            while pending:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    for future in pending:
                        symbol = futures[future]
                        msg = f"MarketCap таймаут: {symbol}"
                        self._logger.info(msg)
                        results[symbol] = msg
                        future.cancel()
                    pending.clear()
                    break

                try:
                    for future in as_completed(pending, timeout=remaining):
                        pending.remove(future)
                        symbol = futures[future]
                        try:
                            results[symbol] = future.result()
                            self._logger.info(f"MarketCap готово: {symbol}")
                        except Exception as exc:  # noqa: BLE001
                            msg = f"MarketCap ошибка: {symbol}: {exc}"
                            self._logger.info(msg)
                            results[symbol] = msg
                except TimeoutError:
                    for future in pending:
                        symbol = futures[future]
                        msg = f"MarketCap таймаут: {symbol}"
                        self._logger.info(msg)
                        results[symbol] = msg
                        future.cancel()
                    pending.clear()

        for symbol in symbols:
            if symbol not in results:
                msg = f"MarketCap таймаут: {symbol}"
                self._logger.info(msg)
                results[symbol] = msg

        self._logger.info("MarketCap завершен")
        return MarketCapsResult(market_caps=results)

    def fetch_all(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchAllResult:
        self._logger.info(f"Загрузка старт: {len(symbols)} символов, TF={timeframe.value}")

        ohlcv_result: dict[str, int | str]
        oi_result: dict[str, int | str]

        with ThreadPoolExecutor(max_workers=FETCH_ALL_MAX_WORKERS) as executor:
            ohlcv_future = executor.submit(
                self._ohlcv_fetcher.fetch_many,
                symbols,
                timeframe,
                start_time,
                end_time,
            )
            oi_future = executor.submit(
                self._oi_fetcher.fetch_many,
                symbols,
                timeframe,
                start_time,
                end_time,
            )

            try:
                ohlcv_result = ohlcv_future.result(timeout=self._request_timeout_seconds)
            except TimeoutError:
                ohlcv_future.cancel()
                msg = "OHLCV канал: таймаут при ожидании результата fetch_many"
                self._logger.info(msg)
                ohlcv_result = {symbol: msg for symbol in symbols}

            try:
                oi_result = oi_future.result(timeout=self._request_timeout_seconds)
            except TimeoutError:
                oi_future.cancel()
                msg = "OI канал: таймаут при ожидании результата fetch_many"
                self._logger.info(msg)
                oi_result = {symbol: msg for symbol in symbols}

        market_caps = self.fetch_market_caps(symbols)
        self._logger.info("Загрузка завершена")

        return FetchAllResult(
            ohlcv=ohlcv_result,
            open_interest=oi_result,
            market_caps=market_caps,
        )
