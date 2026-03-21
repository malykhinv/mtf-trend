"""Модуль проекта."""

from __future__ import annotations

from pathlib import Path

from constants import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    LOG_MSG_SKIP_UP_TO_DATE,
)
from data.quality.data_validator import DataValidator
from data.quality.deduplicator import Deduplicator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetCacheValidationError, ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger


class OhlcvFetcher:
    """Класс."""

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
        self._gap_detector = GapDetector()
        self._validator = DataValidator()

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_timestamp_ms: int, end_timestamp_ms: int) -> int:
        """Загружает OHLCV-данные для одного символа."""
        start_timestamp_ms = int(start_timestamp_ms)
        end_timestamp_ms = int(end_timestamp_ms)
        timeframe_ms = timeframe.to_milliseconds()
        watermark_column = "close"
        first_timestamp_ms = self._storage.get_first_timestamp_for_column(symbol, timeframe, watermark_column)
        last_timestamp_ms = self._storage.get_last_timestamp_for_column(symbol, timeframe, watermark_column)

        segments: list[tuple[int, int, str]] = []
        if first_timestamp_ms is None or last_timestamp_ms is None:
            segments.append((start_timestamp_ms, end_timestamp_ms, "full"))
        else:
            prefix_end_ms = min(end_timestamp_ms, first_timestamp_ms - timeframe_ms)
            if start_timestamp_ms <= prefix_end_ms:
                segments.append((start_timestamp_ms, prefix_end_ms, "prefix"))

            suffix_start_ms = max(start_timestamp_ms, last_timestamp_ms + timeframe_ms)
            if suffix_start_ms <= end_timestamp_ms:
                segments.append((suffix_start_ms, end_timestamp_ms, "suffix"))

        self._logger.info(
            "OHLCV водораздел: %s %s колонка=%s первый_ms=%s последний_ms=%s сегментов=%s",
            symbol,
            timeframe.value,
            watermark_column,
            first_timestamp_ms,
            last_timestamp_ms,
            len(segments),
        )
        if not segments:
            self._logger.info(LOG_MSG_SKIP_UP_TO_DATE, "OHLCV", symbol)
            return 0

        added_rows = 0
        for segment_start_ms, segment_end_ms, segment_kind in segments:
            self._logger.info(
                "OHLCV сегмент: %s %s %s %s -> %s",
                symbol,
                timeframe.value,
                segment_kind,
                segment_start_ms,
                segment_end_ms,
            )
            data = self._exchange_client.fetch_ohlcv(symbol, timeframe, segment_start_ms, segment_end_ms)
            data = self._deduplicator.deduplicate(data)

            gaps = self._gap_detector.detect_gaps(data, expected_step_ms=timeframe_ms)
            if gaps:
                self._logger.info("OHLCV пропуски: %s найдено %s пропусков", symbol, len(gaps))

            issues = self._validator.validate(symbol, timeframe, data)
            if issues:
                self._logger.info("OHLCV качество: %s найдено %s аномалий", symbol, len(issues))

            added_rows += self._storage.save_incremental(symbol, timeframe, data)

        self._logger.info("OHLCV завершен: %s, добавлено %s строк", symbol, added_rows)
        return added_rows

    def fetch_many(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> dict[str, SymbolFetchResult]:
        """Последовательно загружает OHLCV для набора символов."""
        results: dict[str, SymbolFetchResult] = {}
        for index, symbol in enumerate(symbols):
            try:
                added_rows = self.fetch_symbol(symbol, timeframe, start_timestamp_ms, end_timestamp_ms)
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
        return isinstance(exc, AttributeError)
