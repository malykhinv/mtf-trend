"""High-level coordinator for OHLCV, OI and market-cap loading."""

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
    FETCH_ALL_MAX_WORKERS,
)
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from domain.abstract.market_data_client import MarketDataClient
from domain.enums.timeframe import Timeframe
from domain.models.reporting.fetch_all_result import FetchAllResult
from domain.models.reporting.market_caps_result import MarketCapsResult
from utils.logger import get_logger


class MarketDataFetcher:
    """Coordinates parallel OHLCV/OI downloads and market-cap fetching."""

    def __init__(
        self,
        ohlcv_fetcher: OhlcvFetcher,
        oi_fetcher: OiFetcher,
        market_data_client: MarketDataClient,
        max_workers: int = DEFAULT_FETCHER_MAX_WORKERS,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        log_level: int | str = DEFAULT_LOG_LEVEL,
        logs_dir: str | Path = DEFAULT_LOGS_DIR,
    ) -> None:
        self._ohlcv_fetcher = ohlcv_fetcher
        self._oi_fetcher = oi_fetcher
        self._market_data_client = market_data_client
        self._max_workers = max_workers
        self._request_timeout_seconds = request_timeout_seconds
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)

    def fetch_market_caps(self, symbols: list[str]) -> MarketCapsResult:
        self._logger.info(f"MarketCap старт: {len(symbols)} инструментов")
        results: dict[str, float | str] = {}
        poll_timeout_seconds = 0.5

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._market_data_client.get_market_cap, symbol): symbol for symbol in symbols}
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
                    msg = f"MarketCap таймаут задачи: {symbol}"
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
                            self._logger.info(f"MarketCap готово: {symbol}")
                        except Exception as exc:  # noqa: BLE001
                            msg = f"MarketCap ошибка исполнения: {symbol}: {exc}"
                            self._logger.info(msg)
                            results[symbol] = msg
                except TimeoutError:
                    continue

        for symbol in symbols:
            if symbol not in results:
                msg = f"MarketCap таймаут задачи: {symbol}"
                self._logger.info(msg)
                results[symbol] = msg

        self._logger.info("MarketCap завершен")
        return MarketCapsResult(market_caps=results)

    def fetch_all(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchAllResult:
        self._logger.info(f"Загрузка старт: {len(symbols)} символов, TF={timeframe.value}")

        ohlcv_result: dict[str, int | str]
        oi_result: dict[str, int | str]

        channel_timeout_seconds = self._request_timeout_seconds
        graceful_fallback = True

        with ThreadPoolExecutor(max_workers=FETCH_ALL_MAX_WORKERS) as executor:
            channels = {
                "ohlcv": executor.submit(
                    self._ohlcv_fetcher.fetch_many,
                    symbols,
                    timeframe,
                    start_time,
                    end_time,
                ),
                "oi": executor.submit(
                    self._oi_fetcher.fetch_many,
                    symbols,
                    timeframe,
                    start_time,
                    end_time,
                ),
            }
            start_times = {name: monotonic() for name in channels}
            soft_timeout_logged: set[str] = set()
            pending = set(channels)
            channel_results: dict[str, dict[str, int | str]] = {}
            deadline = monotonic() + channel_timeout_seconds

            while pending:
                now = monotonic()
                soft_expired = [name for name in pending if now - start_times[name] > channel_timeout_seconds]
                for name in soft_expired:
                    if name in soft_timeout_logged:
                        continue
                    channel_label = "OHLCV" if name == "ohlcv" else "OI"
                    msg = f"{channel_label} канал: soft-timeout fetch_many"
                    self._logger.info(msg)

                    soft_timeout_logged.add(name)

                if now >= deadline:
                    break

                completed = []
                for name in list(pending):
                    future = channels[name]
                    if not future.done():
                        continue
                    completed.append(name)
                    try:
                        channel_results[name] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        channel_label = "OHLCV" if name == "ohlcv" else "OI"
                        msg = f"{channel_label} канал: ошибка исполнения fetch_many: {exc}"
                        self._logger.info(msg)
                        if graceful_fallback:
                            channel_results[name] = {symbol: msg for symbol in symbols}

                for name in completed:
                    pending.remove(name)

                if pending:
                    sleep_seconds = 0.5
                    try:
                        for _ in as_completed((channels[name] for name in pending), timeout=sleep_seconds):
                            break
                    except TimeoutError:
                        continue

            hard_timed_out: list[str] = []
            for name in list(pending):
                future = channels[name]
                channel_label = "OHLCV" if name == "ohlcv" else "OI"
                try:
                    cancelled = future.cancel()
                except Exception:  # noqa: BLE001
                    cancelled = False

                if cancelled:
                    pending.remove(name)
                    msg = f"{channel_label} канал: hard-timeout fetch_many"
                    self._logger.info(msg)
                    if graceful_fallback:
                        channel_results[name] = {symbol: msg for symbol in symbols}
                    continue

                hard_timed_out.append(name)

            for future in as_completed([channels[name] for name in hard_timed_out]):
                name = "ohlcv" if future is channels["ohlcv"] else "oi"
                if name in pending:
                    pending.remove(name)
                channel_label = "OHLCV" if name == "ohlcv" else "OI"
                try:
                    channel_results[name] = future.result()
                except Exception as exc:  # noqa: BLE001
                    msg = f"{channel_label} канал: ошибка исполнения fetch_many: {exc}"
                    self._logger.info(msg)
                    if graceful_fallback:
                        channel_results[name] = {symbol: msg for symbol in symbols}

            for name in pending:
                channel_label = "OHLCV" if name == "ohlcv" else "OI"
                msg = f"{channel_label} канал: hard-timeout fetch_many"
                self._logger.info(msg)
                if graceful_fallback:
                    channel_results[name] = {symbol: msg for symbol in symbols}

            ohlcv_result = channel_results.get("ohlcv", {}) if graceful_fallback else channel_results["ohlcv"]
            oi_result = channel_results.get("oi", {}) if graceful_fallback else channel_results["oi"]

        market_caps = self.fetch_market_caps(symbols)
        self._logger.info("Загрузка завершена")

        return FetchAllResult(
            ohlcv=ohlcv_result,
            open_interest=oi_result,
            market_caps=market_caps,
        )
