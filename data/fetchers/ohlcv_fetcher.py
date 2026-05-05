"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from constants import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    LOG_MSG_SKIP_UP_TO_DATE,
    OHLCV_FRAME_COLUMNS,
    OHLCV_OPTIONAL_MARKET_DATA_COLUMNS,
)
from data.quality.data_validator import DataValidator
from data.quality.deduplicator import Deduplicator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetCacheValidationError, ParquetStorage
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger


@dataclass(frozen=True, slots=True)
class CachedBaseFrameLoadResult:
    frame: pd.DataFrame
    ok: bool
    status: str
    reason: str
    source_timeframe: Timeframe
    path: str = ""
    rows: int = 0
    missing_columns: tuple[str, ...] = ()


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

    @staticmethod
    def _aggregate_cached_frame(frame: pd.DataFrame, target_timeframe: Timeframe) -> pd.DataFrame:
        output_columns = [
            *OHLCV_FRAME_COLUMNS,
            *[column for column in OHLCV_OPTIONAL_MARKET_DATA_COLUMNS if column in frame.columns],
        ]
        if frame.empty or not set(OHLCV_FRAME_COLUMNS).issubset(frame.columns):
            return pd.DataFrame(columns=output_columns)

        timeframe_ms = target_timeframe.to_milliseconds()
        prepared = frame.loc[:, output_columns].copy()
        for column in output_columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=list(OHLCV_FRAME_COLUMNS))
        if prepared.empty:
            return pd.DataFrame(columns=output_columns)

        prepared["bucket"] = (prepared["timestamp"] // timeframe_ms) * timeframe_ms
        aggregation: dict[str, tuple[str, str]] = {
            "open": ("open", "first"),
            "high": ("high", "max"),
            "low": ("low", "min"),
            "close": ("close", "last"),
            "volume": ("volume", "sum"),
        }
        for column in OHLCV_OPTIONAL_MARKET_DATA_COLUMNS:
            if column in prepared.columns:
                aggregation[column] = (column, "sum")
        aggregated = (
            prepared.groupby("bucket", as_index=False)
            .agg(**aggregation)
            .rename(columns={"bucket": "timestamp"})
        )
        return aggregated.loc[:, output_columns].reset_index(drop=True)

    def _load_cached_base_frame_result(
        self,
        symbol: str,
        source_timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> CachedBaseFrameLoadResult:
        load_result = self._storage.load_result(symbol, source_timeframe)
        if not load_result.ok:
            return CachedBaseFrameLoadResult(
                frame=pd.DataFrame(),
                ok=False,
                status=load_result.status,
                reason=load_result.reason,
                source_timeframe=source_timeframe,
                path=str(load_result.path),
                rows=int(load_result.rows),
                missing_columns=load_result.missing_columns,
            )
        frame = load_result.frame
        prepared = frame.copy()
        for column in (*OHLCV_FRAME_COLUMNS, *OHLCV_OPTIONAL_MARKET_DATA_COLUMNS):
            if column in prepared.columns:
                prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=list(OHLCV_FRAME_COLUMNS))
        if prepared.empty:
            return CachedBaseFrameLoadResult(
                frame=pd.DataFrame(),
                ok=False,
                status="invalid_required_ohlcv_rows",
                reason="cached_base_rows_invalid_after_numeric_cleaning",
                source_timeframe=source_timeframe,
                path=str(load_result.path),
                rows=int(load_result.rows),
            )
        sliced = prepared.loc[
            (prepared["timestamp"] >= start_timestamp_ms)
            & (prepared["timestamp"] <= end_timestamp_ms)
        ].sort_values("timestamp").reset_index(drop=True)
        if sliced.empty:
            return CachedBaseFrameLoadResult(
                frame=sliced,
                ok=False,
                status="requested_window_empty",
                reason="cached_base_window_empty",
                source_timeframe=source_timeframe,
                path=str(load_result.path),
                rows=0,
            )
        return CachedBaseFrameLoadResult(
            frame=sliced,
            ok=True,
            status="ok",
            reason="cached_base_loaded",
            source_timeframe=source_timeframe,
            path=str(load_result.path),
            rows=int(len(sliced)),
        )

    def _load_cached_base_frame(
        self,
        symbol: str,
        source_timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        return self._load_cached_base_frame_result(
            symbol=symbol,
            source_timeframe=source_timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        ).frame

    def fetch_symbol(self, symbol: str, timeframe: Timeframe, start_timestamp_ms: int, end_timestamp_ms: int) -> int:
        """Загружает OHLCV-данные для одного символа."""
        if timeframe == Timeframe.M10:
            cached_base_result = self._load_cached_base_frame_result(
                symbol=symbol,
                source_timeframe=Timeframe.M5,
                start_timestamp_ms=start_timestamp_ms,
                end_timestamp_ms=end_timestamp_ms,
            )
            cached_base_frame = cached_base_result.frame
            if not cached_base_frame.empty:
                data = self._aggregate_cached_frame(cached_base_frame, target_timeframe=timeframe)
                added_rows = self._storage.save_incremental(symbol, timeframe, data)
                self._logger.debug(
                    "OHLCV %s %s собран из уже имеющегося %s. Новых строк: %s.",
                    symbol,
                    timeframe.value,
                    Timeframe.M5.value,
                    added_rows,
                )
                return added_rows

        start_timestamp_ms = int(start_timestamp_ms)
        end_timestamp_ms = int(end_timestamp_ms)
        timeframe_ms = timeframe.to_milliseconds()
        watermark_column = "quote_volume"
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

        self._logger.debug(
            "OHLCV %s %s: сверил кэш, нужно закрыть сегментов: %s.",
            symbol,
            timeframe.value,
            len(segments),
        )
        if not segments:
            self._logger.debug(LOG_MSG_SKIP_UP_TO_DATE, "OHLCV", symbol)
            return 0

        added_rows = 0
        for segment_start_ms, segment_end_ms, segment_kind in segments:
            self._logger.debug(
                "OHLCV %s %s: дописываю %s сегмент %s..%s.",
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
                self._logger.debug("OHLCV %s: в свечах есть разрывы, найдено %s.", symbol, len(gaps))

            added_rows += self._storage.save_incremental(symbol, timeframe, data)

        self._logger.debug("OHLCV %s: свечная история обновлена, новых строк %s.", symbol, added_rows)
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
                        "OHLCV-кэш не выдержал проверку: %s %s. Причина: %s",
                        symbol,
                        timeframe.value,
                        exc,
                    )
                msg = f"OHLCV ошибка исполнения: {symbol}: {exc}"
                self._logger.exception(msg)
                results[symbol] = SymbolFetchResult.error(msg)

                if self._is_system_error(exc):
                    diagnostic_message = (
                        f"OHLCV остановлен на {symbol} {timeframe.value}: системная ошибка. "
                        f"Остальные символы этого TF пропущены. Причина: {exc}"
                    )
                    remaining_symbols = symbols[index + 1 :]
                    self._logger.warning(
                        "OHLCV: закрываю TF %s после %s. Непройденных символов: %s. Причина: %s",
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
