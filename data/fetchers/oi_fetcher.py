"""Open interest fetch coordinator with parallel processing and timeout controls."""

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
        watermark_column = "open_interest"
        last_timestamp = self._storage.get_last_timestamp_for_column(symbol, timeframe, watermark_column)
        if last_timestamp is not None:
            next_start = max(start_time, last_timestamp.to_pydatetime() + TIMEFRAME_TO_DELTA[timeframe])

        watermark_display = last_timestamp.isoformat() if last_timestamp is not None else "None"
        self._logger.info(
            f"OI watermark: {symbol} {timeframe.value} column={watermark_column} last={watermark_display} selected={next_start.isoformat()}"
        )
        self._logger.info(f"OI старт: {symbol} {timeframe.value} {next_start.isoformat()} -> {end_time.isoformat()}")
        if next_start > end_time:
            self._logger.info(f"OI пропуск: {symbol} уже актуален")
            return 0

        data = self._exchange_client.fetch_open_interest(symbol, timeframe, next_start, end_time)
        data = self._aligner.align_to_utc(data)
        data = self._deduplicator.deduplicate(data)

        if "timestamp" not in data.columns:
            if data.empty:
                self._logger.info(f"OI пустой OI без timestamp: {symbol} {timeframe.value}")
                return 0
            raise ValueError(
                f"OI fetch_symbol: отсутствует колонка 'timestamp' в непустом OI для {symbol} {timeframe.value}"
            )

        next_start_ms = int(next_start.timestamp() * 1000)
        end_time_ms = int(end_time.timestamp() * 1000)
        interval_mask = (data["timestamp"] >= next_start_ms) & (data["timestamp"] <= end_time_ms)
        data = data.loc[interval_mask].copy()

        ohlcv = self._storage.load(symbol, timeframe)
        if not ohlcv.empty and "timestamp" in ohlcv.columns:
            ohlcv_interval = ohlcv.loc[
                (ohlcv["timestamp"] >= next_start_ms) & (ohlcv["timestamp"] <= end_time_ms), ["timestamp"]
            ].copy()
            if not ohlcv_interval.empty:
                aligned = self._oi_aligner.align(ohlcv_interval, data)
                expected_columns = {"timestamp", "open_interest"}
                missing_columns = expected_columns.difference(aligned.columns)
                if missing_columns:
                    raise ValueError(
                        "OI align: после OiAligner.align отсутствуют ожидаемые колонки "
                        f"{sorted(missing_columns)} для {symbol} {timeframe.value}"
                    )
                data = aligned[["timestamp", "open_interest"]]

        if not data.empty and "timestamp" in data.columns:
            data = data.loc[(data["timestamp"] >= next_start_ms) & (data["timestamp"] <= end_time_ms)].copy()

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
        poll_timeout_seconds = 0.5
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self.fetch_symbol, s, timeframe, start_time, end_time): s for s in symbols
            }
            start_times = {future: monotonic() for future in futures}
            pending = set(futures)

            while pending:
                now = monotonic()
                expired = [
                    future
                    for future in pending
                    if now - start_times[future] > self._request_timeout_seconds
                ]
                for future in expired:
                    pending.remove(future)
                    symbol = futures[future]
                    msg = f"OI таймаут задачи: {symbol}"
                    self._logger.info(msg)
                    results[symbol] = msg
                    future.cancel()

                if not pending:
                    break

                try:
                    for future in as_completed(pending, timeout=poll_timeout_seconds):
                        pending.remove(future)
                        symbol = futures[future]
                        try:
                            results[symbol] = future.result()
                        except Exception as exc:  # noqa: BLE001
                            msg = f"OI ошибка исполнения: {symbol}: {exc}"
                            self._logger.info(msg)
                            results[symbol] = msg
                except TimeoutError:
                    continue

        for symbol in symbols:
            if symbol not in results:
                msg = f"OI таймаут задачи: {symbol}"
                self._logger.info(msg)
                results[symbol] = msg

        return results
