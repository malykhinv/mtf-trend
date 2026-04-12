"""Модуль проекта."""
from __future__ import annotations

import time
from pathlib import Path

from constants import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    LOG_MSG_TASK_COMPLETED,
)
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from domain.abstract.market_data_client import MarketDataClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.fetch_all_result import FetchAllResult
from domain.models.reporting.market_caps_result import MarketCapsResult
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger


class MarketDataFetcher:
    """Класс."""
    def __init__(
        self,
        ohlcv_fetcher: OhlcvFetcher,
        oi_fetcher: OiFetcher,
        market_data_client: MarketDataClient,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        log_level: int | str = DEFAULT_LOG_LEVEL,
        logs_dir: str | Path = DEFAULT_LOGS_DIR,
    ) -> None:
        self._ohlcv_fetcher = ohlcv_fetcher
        self._oi_fetcher = oi_fetcher
        self._market_data_client = market_data_client
        self._request_timeout_seconds = request_timeout_seconds
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)

    # region Приватные

    def _log_stage_summary(self, stage: str, total: int, ok: int, failed: int) -> None:
        self._logger.info("%s сводка: всего=%s успешно=%s с ошибками=%s", stage, total, ok, failed)

    @staticmethod
    def _count_structured_results(results: dict[str, SymbolFetchResult]) -> tuple[int, int, int]:
        total = len(results)
        ok = sum(1 for result in results.values() if result.success)
        return total, ok, total - ok

    @staticmethod
    def _count_market_caps_results(results: MarketCapsResult) -> tuple[int, int, int]:
        total = len(results.market_caps)
        ok = sum(1 for value in results.market_caps.values() if isinstance(value, (int, float)))
        return total, ok, total - ok

    # endregion Приватные

    def fetch_market_caps(self, symbols: list[str]) -> MarketCapsResult:
        """Загружает капитализации для списка тикеров."""
        self._logger.info(f"Рыночная капитализация старт: {len(symbols)} инструментов")
        results: dict[str, float | str] = {}

        try:
            batch_caps = self._market_data_client.get_market_caps(symbols)
            for symbol in symbols:
                if symbol in batch_caps:
                    results[symbol] = float(batch_caps.get(symbol, 0.0))
                    self._logger.info(f"Рыночная капитализация готово (batch): {symbol}")
        except Exception as exc:
            self._logger.warning(
                "Рыночная капитализация: batch-запрос не удался, переключение на fallback get_market_cap(): %s",
                exc,
            )

        unresolved_symbols = [symbol for symbol in symbols if symbol not in results]
        for symbol in unresolved_symbols:
            try:
                results[symbol] = self._market_data_client.get_market_cap(symbol)
                self._logger.info(f"Рыночная капитализация готово (fallback): {symbol}")
            except Exception as exc:
                msg = f"Рыночная капитализация ошибка исполнения: {symbol}: {exc}"
                self._logger.info(msg)
                results[symbol] = msg

        self._logger.info(LOG_MSG_TASK_COMPLETED, "Рыночная капитализация")
        return MarketCapsResult(market_caps=results)

    def fetch_all(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        *,
        include_open_interest: bool = True,
    ) -> FetchAllResult:
        """Загружает полный набор рыночных метрик."""
        self._logger.info(f"Загрузка старт: {len(symbols)} символов, TF={timeframe.value}")

        ohlcv_result = self._ohlcv_fetcher.fetch_many(
            symbols,
            timeframe,
            start_timestamp_ms,
            end_timestamp_ms,
        )
        if include_open_interest:
            # Open interest всегда только на 5м и за последние 30 дней
            oi_timeframe = Timeframe.M5
            thirty_days_ms = 30 * 24 * 60 * 60 * 1000
            current_time_ms = int(time.time() * 1000)
            oi_start_timestamp_ms = max(start_timestamp_ms, current_time_ms - thirty_days_ms)
            
            self._logger.info(
                "OI ограничения: TF=%s период=%s..%s (оригинал=%s..%s)",
                oi_timeframe.value,
                oi_start_timestamp_ms,
                end_timestamp_ms,
                start_timestamp_ms,
                end_timestamp_ms,
            )
            
            oi_result = self._oi_fetcher.fetch_many(
                symbols,
                oi_timeframe,
                oi_start_timestamp_ms,
                end_timestamp_ms,
            )
        else:
            self._logger.info("OI skipped for TF=%s", timeframe.value)
            oi_result = {symbol: SymbolFetchResult.ok(0) for symbol in symbols}

        market_caps = self.fetch_market_caps(symbols)

        ohlcv_total, ohlcv_ok, ohlcv_failed = self._count_structured_results(ohlcv_result)
        oi_total, oi_ok, oi_failed = self._count_structured_results(oi_result)
        mcap_total, mcap_ok, mcap_failed = self._count_market_caps_results(market_caps)

        self._log_stage_summary("OHLCV", ohlcv_total, ohlcv_ok, ohlcv_failed)
        self._log_stage_summary("OI", oi_total, oi_ok, oi_failed)
        self._log_stage_summary("Рыночная капитализация", mcap_total, mcap_ok, mcap_failed)

        failed_symbols_count = len(
            {
                symbol
                for symbol in symbols
                if (symbol in ohlcv_result and not ohlcv_result[symbol].success)
                or (symbol in oi_result and not oi_result[symbol].success)
                or isinstance(market_caps.market_caps.get(symbol), str)
            }
        )
        has_errors = failed_symbols_count > 0

        self._logger.info("Загрузка завершена")

        return FetchAllResult(
            ohlcv=ohlcv_result,
            open_interest=oi_result,
            market_caps=market_caps,
            failed_symbols_count=failed_symbols_count,
            has_errors=has_errors,
        )
