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
from data.storage.parquet_storage import ParquetCacheValidationError, ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger


class OiFetcher:
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
        self._deduplicator = Deduplicator()
        self._validator = DataValidator()

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_time: datetime, end_time: datetime) -> int:
        """Загружает историю open interest для одного символа."""
        start_timestamp_ms = int(start_time.timestamp() * 1000)
        end_timestamp_ms = int(end_time.timestamp() * 1000)
        next_start_ms = start_timestamp_ms
        watermark_column = "open_interest"
        last_timestamp_ms = self._storage.get_last_timestamp_for_column(symbol, timeframe, watermark_column)
        if last_timestamp_ms is not None:
            timeframe_ms = int(TIMEFRAME_TO_DELTA[timeframe].total_seconds() * 1000)
            next_start_ms = max(start_timestamp_ms, last_timestamp_ms + timeframe_ms)

        self._logger.info(
            "OI водораздел: %s %s колонка=%s последний_ms=%s выбранный_ms=%s",
            symbol,
            timeframe.value,
            watermark_column,
            last_timestamp_ms,
            next_start_ms,
        )
        self._logger.info("OI старт: %s %s %s -> %s", symbol, timeframe.value, next_start_ms, end_timestamp_ms)
        if next_start_ms > end_timestamp_ms:
            self._logger.info(LOG_MSG_SKIP_UP_TO_DATE, "OI", symbol)
            return 0

        data = self._exchange_client.fetch_open_interest(symbol, timeframe, next_start_ms, end_timestamp_ms)
        data = self._deduplicator.deduplicate(data)

        if "timestamp" not in data.columns:
            if data.empty:
                self._logger.info(f"OI пустой ряд без метки времени: {symbol} {timeframe.value}")
                return 0
            raise ValueError(
                f"OI fetch_symbol: отсутствует колонка 'timestamp' в непустом OI для {symbol} {timeframe.value}"
            )

        interval_mask = (data["timestamp"] >= next_start_ms) & (data["timestamp"] <= end_timestamp_ms)
        data = data.loc[interval_mask].copy()

        if not data.empty and "timestamp" in data.columns:
            data = data.loc[(data["timestamp"] >= next_start_ms) & (data["timestamp"] <= end_timestamp_ms)].copy()

        issues = self._validator.validate(symbol, timeframe, data)
        if issues:
            self._logger.info(f"OI качество: {symbol} найдено {len(issues)} аномалий")

        added_rows = self._storage.save_incremental(symbol, timeframe, data)
        self._logger.info(f"OI завершен: {symbol}, добавлено {added_rows} строк")
        return added_rows

    def fetch_many(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, SymbolFetchResult]:
        """Последовательно загружает open interest для набора символов."""
        results: dict[str, SymbolFetchResult] = {}
        for index, symbol in enumerate(symbols):
            try:
                added_rows = self.fetch_symbol(symbol, timeframe, start_time, end_time)
                results[symbol] = SymbolFetchResult.ok(added_rows)
            except Exception as exc:
                if isinstance(exc, ParquetCacheValidationError) or "parquet cache validation failed" in str(exc).lower():
                    self._logger.exception(
                        "cache-validation-error: source=oi symbol=%s timeframe=%s cause=%s",
                        symbol,
                        timeframe.value,
                        exc,
                    )
                msg = f"OI ошибка исполнения: {symbol}: {exc}"
                self._logger.exception(msg)
                results[symbol] = SymbolFetchResult.error(msg)

                if self._is_system_error(exc):
                    diagnostic_message = (
                        "OI fail-fast: системная ошибка, прерывание обработки TF "
                        f"{timeframe.value} после {symbol}: {exc}"
                    )
                    remaining_symbols = symbols[index + 1 :]
                    self._logger.warning(
                        "fetch-abort-system-error: source=oi timeframe=%s failure_symbol=%s remaining=%s cause=%s",
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
        return isinstance(exc, AttributeError)
