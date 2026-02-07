"""OHLCV fetch coordinator with parallel processing and timeout controls."""

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
    TIMEFRAME_TO_DELTA,
)
from data.quality.data_validator import DataValidator
from data.quality.deduplicator import Deduplicator
from data.quality.gap_detector import GapDetector
from data.quality.time_alignment import TimeAlignment
from data.storage.parquet_storage import ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from utils.logger import get_logger


class OhlcvFetcher:
    """Fetches and stores OHLCV incrementally for one or many symbols."""

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
        self._gap_detector = GapDetector()
        self._validator = DataValidator()

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_time: datetime, end_time: datetime) -> int:
        next_start = start_time
        watermark_column = "close"
        last_timestamp = self._storage.get_last_timestamp_for_column(symbol, timeframe, watermark_column)
        if last_timestamp is not None:
            next_start = max(start_time, last_timestamp.to_pydatetime() + TIMEFRAME_TO_DELTA[timeframe])

        watermark_display = last_timestamp.isoformat() if last_timestamp is not None else "None"
        self._logger.info(
            f"OHLCV watermark: {symbol} {timeframe.value} column={watermark_column} last={watermark_display} selected={next_start.isoformat()}"
        )
        self._logger.info(f"OHLCV старт: {symbol} {timeframe.value} {next_start.isoformat()} -> {end_time.isoformat()}")
        if next_start > end_time:
            self._logger.info(f"OHLCV пропуск: {symbol} уже актуален")
            return 0

        data = self._exchange_client.fetch_ohlcv(symbol, timeframe, next_start, end_time)
        data = self._aligner.align_to_utc(data)
        data = self._deduplicator.deduplicate(data)

        gaps = self._gap_detector.detect_gaps(data, timeframe)
        if gaps:
            self._logger.info(f"OHLCV gaps: {symbol} найдено {len(gaps)} пропусков")

        issues = self._validator.validate(symbol, timeframe, data)
        if issues:
            self._logger.info(f"OHLCV quality: {symbol} найдено {len(issues)} аномалий")

        added_rows = self._storage.save_incremental(symbol, timeframe, data)
        self._logger.info(f"OHLCV завершен: {symbol}, добавлено {added_rows} строк")
        return added_rows

    def fetch_many(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, int | str]:
        results: dict[str, int | str] = {}
        poll_timeout_seconds = 0.5
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self.fetch_symbol, s, timeframe, start_time, end_time): s for s in symbols
            }
            start_times = {future: monotonic() for future in futures}
            soft_timeout_logged: set = set()
            pending = set(futures)
            deadline = monotonic() + self._request_timeout_seconds

            while pending:
                now = monotonic()
                soft_expired = [future for future in pending if now - start_times[future] > self._request_timeout_seconds]
                for future in soft_expired:
                    if future in soft_timeout_logged:
                        continue
                    symbol = futures[future]
                    msg = f"OHLCV soft-timeout задачи: {symbol}"
                    self._logger.info(msg)

                    soft_timeout_logged.add(future)

                if now >= deadline:
                    break

                completed: list = []
                try:
                    for future in as_completed(pending, timeout=poll_timeout_seconds):
                        completed.append(future)
                except TimeoutError:
                    continue

                for future in completed:
                    pending.remove(future)
                    symbol = futures[future]
                    try:
                        results[symbol] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        msg = f"OHLCV ошибка исполнения: {symbol}: {exc}"
                        self._logger.info(msg)
                        results[symbol] = msg

            hard_timed_out: list = []
            for future in list(pending):
                symbol = futures[future]
                try:
                    cancelled = future.cancel()
                except Exception:  # noqa: BLE001
                    cancelled = False

                if cancelled:
                    pending.remove(future)
                    msg = f"OHLCV hard-timeout задачи: {symbol}"
                    self._logger.info(msg)
                    results[symbol] = msg
                    continue

                hard_timed_out.append(future)

            for future in as_completed(hard_timed_out):
                if future in pending:
                    pending.remove(future)
                symbol = futures[future]
                try:
                    results[symbol] = future.result()
                except Exception as exc:  # noqa: BLE001
                    msg = f"OHLCV ошибка исполнения: {symbol}: {exc}"
                    self._logger.info(msg)
                    results[symbol] = msg

            for future in pending:
                symbol = futures[future]
                msg = f"OHLCV hard-timeout задачи: {symbol}"
                self._logger.info(msg)
                results[symbol] = msg

        for symbol in symbols:
            if symbol not in results:
                msg = f"OHLCV hard-timeout задачи: {symbol}"
                self._logger.info(msg)
                results[symbol] = msg

        return results
