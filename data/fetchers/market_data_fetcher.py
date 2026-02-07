"""High-level coordinator for OHLCV, OI and market-cap loading."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from domain.abstract.market_data_client import MarketDataClient
from domain.enums.timeframe import Timeframe
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from utils.logger import get_logger


class MarketDataFetcher:
    """Coordinates parallel OHLCV/OI downloads and market-cap fetching."""

    def __init__(
        self,
        ohlcv_fetcher: OhlcvFetcher,
        oi_fetcher: OiFetcher,
        market_data_client: MarketDataClient,
        max_workers: int = 5,
        request_timeout_seconds: int = 60,
        log_level: int | str = "INFO",
        logs_dir: str | Path = "./logs",
    ) -> None:
        self._ohlcv_fetcher = ohlcv_fetcher
        self._oi_fetcher = oi_fetcher
        self._market_data_client = market_data_client
        self._max_workers = max_workers
        self._request_timeout_seconds = request_timeout_seconds
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)

    def fetch_market_caps(self, symbols: list[str]) -> dict[str, Any]:
        self._logger.info(f"MarketCap старт: {len(symbols)} инструментов")
        results: dict[str, Any] = {}

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._market_data_client.get_market_cap, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results[symbol] = future.result(timeout=self._request_timeout_seconds)
                    self._logger.info(f"MarketCap готово: {symbol}")
                except TimeoutError:
                    msg = f"MarketCap таймаут: {symbol}"
                    self._logger.info(msg)
                    results[symbol] = msg
                except Exception as exc:  # noqa: BLE001
                    msg = f"MarketCap ошибка: {symbol}: {exc}"
                    self._logger.info(msg)
                    results[symbol] = msg

        self._logger.info("MarketCap завершен")
        return results

    def fetch_all(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        self._logger.info(f"Загрузка старт: {len(symbols)} символов, TF={timeframe.value}")

        with ThreadPoolExecutor(max_workers=2) as executor:
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

            ohlcv_result = ohlcv_future.result(timeout=self._request_timeout_seconds)
            oi_result = oi_future.result(timeout=self._request_timeout_seconds)

        market_caps = self.fetch_market_caps(symbols)
        self._logger.info("Загрузка завершена")

        return {
            "ohlcv": ohlcv_result,
            "open_interest": oi_result,
            "market_caps": market_caps,
        }
