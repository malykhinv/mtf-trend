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
from data.quality.oi_aligner import OiAligner
from data.quality.time_alignment import TimeAlignment
from data.storage.parquet_storage import ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.formatters import format_datetime_human
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
        self._aligner = TimeAlignment()
        self._deduplicator = Deduplicator()
        self._oi_aligner = OiAligner()
        self._validator = DataValidator()

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_time: datetime, end_time: datetime) -> int:
        """Загружает историю open interest для одного символа."""
        next_start = start_time
        watermark_column = "open_interest"
        last_timestamp = self._storage.get_last_timestamp_for_column(symbol, timeframe, watermark_column)
        if last_timestamp is not None:
            next_start = max(start_time, last_timestamp.to_pydatetime() + TIMEFRAME_TO_DELTA[timeframe])

        watermark_display = format_datetime_human(last_timestamp.to_pydatetime()) if last_timestamp is not None else "None"
        next_start_display = format_datetime_human(next_start)
        end_time_display = format_datetime_human(end_time)
        self._logger.info(
            f"OI водораздел: {symbol} {timeframe.value} колонка={watermark_column} последний={watermark_display} выбранный={next_start_display}"
        )
        self._logger.info(f"OI старт: {symbol} {timeframe.value} {next_start_display} -> {end_time_display}")
        if next_start > end_time:
            self._logger.info(LOG_MSG_SKIP_UP_TO_DATE, "OI", symbol)
            return 0

        data = self._exchange_client.fetch_open_interest(symbol, timeframe, next_start, end_time)
        data = self._aligner.align_to_utc(data)
        data = self._deduplicator.deduplicate(data)

        if "timestamp" not in data.columns:
            if data.empty:
                self._logger.info(f"OI пустой ряд без метки времени: {symbol} {timeframe.value}")
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
        for symbol in symbols:
            try:
                added_rows = self.fetch_symbol(symbol, timeframe, start_time, end_time)
                results[symbol] = SymbolFetchResult.ok(added_rows)
            except Exception as exc:
                msg = f"OI ошибка исполнения: {symbol}: {exc}"
                self._logger.error(msg)
                results[symbol] = SymbolFetchResult.error(msg)

        return results
