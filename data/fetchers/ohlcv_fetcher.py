"""Модуль проекта."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from constants import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    LOG_MSG_SKIP_UP_TO_DATE,
    TIMEFRAME_TO_DELTA,
)
from data.quality.data_validator import DataValidator
from data.quality.deduplicator import Deduplicator
from data.quality.gap_detector import GapDetector
from data.quality.time_alignment import TimeAlignment
from data.storage.parquet_storage import ParquetCacheValidationError, ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.formatters import datetime_to_utc, format_datetime_human
from utils.logger import get_logger


class OhlcvFetcher:
    """Класс."""
    # region Приватные
    def __init__(
        self,
        exchange_client: ExchangeClient,
        storage: ParquetStorage,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        log_level: int | str = DEFAULT_LOG_LEVEL,
        logs_dir: str | Path = DEFAULT_LOGS_DIR,
    ) -> None:
        self._exchange_client = exchange_client
        self._storage = storage
        self._request_timeout_seconds = request_timeout_seconds
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)
        self._aligner = TimeAlignment()
        self._deduplicator = Deduplicator()
        self._gap_detector = GapDetector()
        self._validator = DataValidator()

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_time: datetime, end_time: datetime) -> int:
        """Загружает OHLCV-данные для одного символа."""
        start_time_utc = datetime_to_utc(start_time)
        end_time_utc = datetime_to_utc(end_time)
        next_start = start_time_utc
        watermark_column = "close"
        last_timestamp = self._storage.get_last_timestamp_for_column(symbol, timeframe, watermark_column)
        if last_timestamp is not None:
            next_start = max(start_time_utc, last_timestamp.to_pydatetime() + TIMEFRAME_TO_DELTA[timeframe])

        watermark_display = format_datetime_human(last_timestamp.to_pydatetime()) if last_timestamp is not None else "None"
        next_start_display = format_datetime_human(next_start)
        end_time_display = format_datetime_human(end_time_utc)
        self._logger.info(
            f"OHLCV водораздел: {symbol} {timeframe.value} колонка={watermark_column} последний={watermark_display} выбранный={next_start_display}"
        )
        self._logger.info(f"OHLCV старт: {symbol} {timeframe.value} {next_start_display} -> {end_time_display}")
        if next_start > end_time_utc:
            self._logger.info(LOG_MSG_SKIP_UP_TO_DATE, "OHLCV", symbol)
            return 0

        data = self._exchange_client.fetch_ohlcv(symbol, timeframe, next_start, end_time_utc)
        data = self._aligner.align_to_utc(data)
        data = self._deduplicator.deduplicate(data)

        gaps = self._gap_detector.detect_gaps(data, timeframe)
        if gaps:
            self._logger.info(f"OHLCV пропуски: {symbol} найдено {len(gaps)} пропусков")

        issues = self._validator.validate(symbol, timeframe, data)
        if issues:
            self._logger.info(f"OHLCV качество: {symbol} найдено {len(issues)} аномалий")

        added_rows = self._storage.save_incremental(symbol, timeframe, data)
        self._logger.info(f"OHLCV завершен: {symbol}, добавлено {added_rows} строк")
        return added_rows

    def fetch_many(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, SymbolFetchResult]:
        """Последовательно загружает OHLCV для набора символов."""
        results: dict[str, SymbolFetchResult] = {}
        for index, symbol in enumerate(symbols):
            try:
                added_rows = self.fetch_symbol(symbol, timeframe, start_time, end_time)
                results[symbol] = SymbolFetchResult.ok(added_rows)
            except Exception as exc:
                if isinstance(exc, ParquetCacheValidationError) or "parquet cache validation failed" in str(exc).lower():
                    self._logger.exception(
                        "cache-validation-error: source=ohlcv symbol=%s timeframe=%s cause=%s",
                        symbol,
                        timeframe.value,
                        exc,
                    )
                msg = f"OHLCV ошибка исполнения: {symbol}: {exc}"
                self._logger.exception(msg)
                results[symbol] = SymbolFetchResult.error(msg)

                if self._is_system_error(exc):
                    diagnostic_message = (
                        "OHLCV fail-fast: системная ошибка, прерывание обработки TF "
                        f"{timeframe.value} после {symbol}: {exc}"
                    )
                    remaining_symbols = symbols[index + 1 :]
                    self._logger.warning(
                        "fetch-abort-system-error: source=ohlcv timeframe=%s failure_symbol=%s remaining=%s cause=%s",
                        timeframe.value,
                        symbol,
                        len(remaining_symbols),
                        exc,
                    )
                    for remaining_symbol in remaining_symbols:
                        results[remaining_symbol] = SymbolFetchResult.error(diagnostic_message)
                    break

        return results

    @staticmethod
    def _is_system_error(exc: Exception) -> bool:
        message = str(exc).lower()
        signatures = (
            "normalize",
            "timestamp normalization",
            "tz-naive",
            "tz-aware",
        )
        return isinstance(exc, AttributeError) or any(signature in message for signature in signatures)
