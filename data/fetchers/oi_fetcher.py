"""Open interest fetch coordinator with parallel processing and timeout controls."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime
from pathlib import Path

from constants import (
    DEFAULT_FETCHER_MAX_WORKERS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    TIMEFRAME_TO_DELTA,
)
from data.quality.data_validator import DataValidator
from data.quality.deduplicator import Deduplicator
from data.quality.oi_aligner import OiAligner
from data.quality.time_alignment import TimeAlignment
from data.storage.parquet_storage import ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from utils.logger import get_logger


class OiFetcher:
    """Fetches and stores open-interest data incrementally for one or many symbols."""

    def __init__(
        self,
        exchange_client: ExchangeClient,
        storage: ParquetStorage,
        max_workers: int = DEFAULT_FETCHER_MAX_WORKERS,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        log_level: int | str = DEFAULT_LOG_LEVEL,
        logs_dir: str | Path = DEFAULT_LOGS_DIR,
    ) -> None:
        self._exchange_client = exchange_client
        self._storage = storage
        self._max_workers = max_workers
        self._request_timeout_seconds = request_timeout_seconds
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)
        self._aligner = TimeAlignment()
        self._deduplicator = Deduplicator()
        self._oi_aligner = OiAligner()
        self._validator = DataValidator()

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_time: datetime, end_time: datetime) -> int:
        next_start = start_time
        last_timestamp = self._storage.get_last_timestamp(symbol, timeframe)
        if last_timestamp is not None:
            next_start = max(start_time, last_timestamp.to_pydatetime() + TIMEFRAME_TO_DELTA[timeframe])

        self._logger.info(f"OI старт: {symbol} {timeframe.value} {next_start.isoformat()} -> {end_time.isoformat()}")
        if next_start > end_time:
            self._logger.info(f"OI пропуск: {symbol} уже актуален")
            return 0

        data = self._exchange_client.fetch_open_interest(symbol, timeframe, next_start, end_time)
        data = self._aligner.align_to_utc(data)
        data = self._deduplicator.deduplicate(data)

        ohlcv = self._storage.load(symbol, timeframe)
        if not ohlcv.empty and "timestamp" in ohlcv.columns:
            aligned = self._oi_aligner.align(ohlcv[["timestamp"]], data)
            data = aligned[["timestamp", "open_interest"]]

        issues = self._validator.validate(symbol, timeframe, data)
        if issues:
            self._logger.info(f"OI quality: {symbol} найдено {len(issues)} аномалий")

        added_rows = self._storage.save_incremental(symbol, timeframe, data)
        self._logger.info(f"OI завершен: {symbol}, добавлено {added_rows} строк")
        return added_rows

    def fetch_many(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, int | str]:
        results: dict[str, int | str] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self.fetch_symbol, s, timeframe, start_time, end_time): s for s in symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results[symbol] = future.result(timeout=self._request_timeout_seconds)
                except TimeoutError:
                    msg = f"OI таймаут: {symbol}"
                    self._logger.info(msg)
                    results[symbol] = msg
                except Exception as exc:  # noqa: BLE001
                    msg = f"OI ошибка: {symbol}: {exc}"
                    self._logger.info(msg)
                    results[symbol] = msg

        return results
