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
from data.storage.parquet_storage import ParquetCacheValidationError, ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger


class OiFetcher:
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
        self._validator = DataValidator()

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_timestamp_ms: int, end_timestamp_ms: int) -> int:
        """Загружает историю open interest для одного символа."""
        start_timestamp_ms = int(start_timestamp_ms)
        end_timestamp_ms = int(end_timestamp_ms)
        timeframe_ms = timeframe.to_milliseconds()
        watermark_column = "open_interest"
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
            "OI %s %s: найдено сегментов для дозагрузки: %s.",
            symbol,
            timeframe.value,
            len(segments),
        )
        if not segments:
            self._logger.info(LOG_MSG_SKIP_UP_TO_DATE, "OI", symbol)
            return 0

        added_rows = 0
        for segment_start_ms, segment_end_ms, segment_kind in segments:
            self._logger.info(
                "OI %s %s: загружаю %s сегмент %s..%s.",
                symbol,
                timeframe.value,
                segment_kind,
                segment_start_ms,
                segment_end_ms,
            )
            data = self._exchange_client.fetch_open_interest(symbol, timeframe, segment_start_ms, segment_end_ms)
            data = self._deduplicator.deduplicate(data)

            if "timestamp" not in data.columns:
                if data.empty:
                    self._logger.info("OI %s %s: пустой ряд, пропускаю.", symbol, timeframe.value)
                    continue
                raise ValueError(
                    f"OI fetch_symbol: отсутствует колонка 'timestamp' в непустом OI для {symbol} {timeframe.value}"
                )

            data = data.loc[(data["timestamp"] >= segment_start_ms) & (data["timestamp"] <= segment_end_ms)].copy()

            added_rows += self._storage.save_incremental(symbol, timeframe, data)

        self._logger.info("OI %s: добавлено строк: %s.", symbol, added_rows)
        return added_rows

    def fetch_many(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> dict[str, SymbolFetchResult]:
        """Последовательно загружает open interest для набора символов."""
        results: dict[str, SymbolFetchResult] = {}
        for index, symbol in enumerate(symbols):
            try:
                added_rows = self.fetch_symbol(symbol, timeframe, start_timestamp_ms, end_timestamp_ms)
                results[symbol] = SymbolFetchResult.ok(added_rows)
            except Exception as exc:
                if isinstance(exc, ParquetCacheValidationError) or "parquet cache validation failed" in str(exc).lower():
                    self._logger.exception(
                        "Кэш OI не прошёл проверку: %s %s. Причина: %s",
                        symbol,
                        timeframe.value,
                        exc,
                    )
                msg = f"OI ошибка исполнения: {symbol}: {exc}"
                self._logger.exception(msg)
                results[symbol] = SymbolFetchResult.error(msg)

                if self._is_system_error(exc):
                    diagnostic_message = (
                        f"OI: системная ошибка на {symbol} {timeframe.value}. "
                        f"Остальные символы этого TF пропущены. Причина: {exc}"
                    )
                    remaining_symbols = symbols[index + 1 :]
                    self._logger.warning(
                        "OI: остановил TF %s после %s. Осталось символов: %s. Причина: %s",
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
