"""Модуль проекта."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from logging import Logger
from pathlib import Path
from typing import Callable

import pandas as pd

from config import AppConfig
from constants import (
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
    DEFAULT_REPORT_OUTPUT_FILE,
    OI_STALE_MIN_OBSERVATIONS,
    OI_STALE_RATIO_THRESHOLD,
    QUALITY_OI_ALIGNMENT_LEADING_GAPS_ISSUE,
    QUALITY_OI_ALIGNMENT_MISSING_VALUES_ISSUE,
    QUALITY_OI_ALIGNMENT_STALE_SERIES_ISSUE,
    QUALITY_OI_MISSING_COLUMN_ISSUE,
    QUALITY_SEVERITY_CRITICAL,
    QUALITY_SEVERITY_ERROR,
    QUALITY_SEVERITY_INFO,
    QUALITY_SEVERITY_WARNING,
    REPORT_PROFITABLE_PF_THRESHOLD,
    REPORT_PROFIT_FACTOR_FILTER,
    REPORT_TRADES_COUNT_FILTER,
    LOG_MSG_TASK_COMPLETED,
)
from data.clients.coingecko_client import CoinGeckoClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.liquidity.daily_volume_ranker import DailyVolumeRanker
from data.quality.data_validator import DataValidator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetStorage
from domain.enums.exchange import Exchange
from domain.enums.position_side import PositionSide
from domain.enums.timeframe import Timeframe
from domain.models.retest_plot_span import RetestPlotSpan
from domain.models.reporting.backtest_report import BacktestReport
from domain.models.reporting.backtest_summary import BacktestSummary
from domain.models.reporting.optimal_parameter_ranges import OptimalParameterRanges
from domain.models.reporting.quality_report import QualityReport
from domain.models.reporting.quality_summary import QualitySummary
from domain.models.reporting.quality_symbol_stats import QualitySymbolStats
from domain.models.reporting.trade_results_distribution import TradeResultsDistribution
from strategy.breakout.breakout_strategy import BreakoutStrategy
from strategy.breakout.config import TARGET_PARAMETER_COMBINATIONS, BreakoutParams
from utils.logger import get_logger
from utils.retry import RetryExhaustedError
from utils.symbols import normalize_symbol
from vectorbt_runner import BacktestRunner, DataPreparer, SymbolMtfFrames, StrategyPlotter


# region Приватные

def _run_with_logging(command_name: str, config: AppConfig, body: Callable[[], int]) -> int:
    logger = get_logger(
        command_name,
        level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    logger.info(f"{command_name}: старт")
    try:
        code = body()
        logger.info(f"{LOG_MSG_TASK_COMPLETED % command_name} (код={code})")
        return code
    except Exception as exc:
        logger.exception(f"{command_name}: ошибка: {exc}")
        return 1


def _build_fetch_stack(config: AppConfig) -> tuple[MarketDataFetcher, CcxtFuturesClient, CoinGeckoClient]:
    market_caps_cache_path = config.backtest.cache_dir / "market_caps.parquet"
    exchange_client = CcxtFuturesClient(
        exchange=Exchange.BINANCE,
        api_key=config.fetch.binance_api_key,
        secret=config.fetch.binance_secret_key,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
    )
    storage = ParquetStorage(
        base_dir=config.backtest.cache_dir,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    ohlcv_fetcher = OhlcvFetcher(
        exchange_client=exchange_client,
        storage=storage,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    oi_fetcher = OiFetcher(
        exchange_client=exchange_client,
        storage=storage,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    market_client = CoinGeckoClient(
        api_key=config.fetch.coingecko_api_key,
        cache_path=market_caps_cache_path,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        min_request_interval_seconds=config.fetch.coingecko_min_request_interval_seconds,
    )
    return (
        MarketDataFetcher(
            ohlcv_fetcher=ohlcv_fetcher,
            oi_fetcher=oi_fetcher,
            market_data_client=market_client,
            retry_attempts=config.backtest.retry_attempts,
            retry_backoff_seconds=config.backtest.retry_backoff_seconds,
            log_level=config.backtest.log_level,
            logs_dir=config.backtest.logs_dir,
        ),
        exchange_client,
        market_client,
    )


def _resolve_symbols(
        exchange_client: CcxtFuturesClient,
        market_client: CoinGeckoClient,
        top_n: int,
        min_volume_usd: float,
        logger: Logger,
        coingecko_volume_batch_size: int,
        ignore_coingecko: bool,
        cache_dir: Path,
        liquidity_timeframe: Timeframe,
        futures_symbols_raw: list[str] | None = None,
) -> list[str]:
    futures_symbols_raw = futures_symbols_raw or exchange_client.get_futures_symbols()
    futures_symbol_map = {
        normalize_symbol(symbol): symbol
        for symbol in futures_symbols_raw
    }
    exchange_symbols_normalized = sorted(futures_symbol_map)

    if ignore_coingecko:
        ranker = DailyVolumeRanker(cache_dir=cache_dir)
        symbols_raw = [futures_symbol_map[symbol] for symbol in exchange_symbols_normalized]
        avg_daily_volumes = ranker.calculate_avg_daily_volume_usd(
            symbols=symbols_raw,
            timeframe=liquidity_timeframe,
            logger=logger,
        )
        avg_daily_volumes_normalized = {
            normalize_symbol(raw_symbol): volume
            for raw_symbol, volume in avg_daily_volumes.items()
        }
        symbols_with_volume = [
            symbol
            for symbol in exchange_symbols_normalized
            if symbol in avg_daily_volumes_normalized
        ]

        min_cache_ready_symbols = min(top_n, len(exchange_symbols_normalized))
        is_cold_start = len(symbols_with_volume) < min_cache_ready_symbols
        if is_cold_start:
            bootstrap_symbols = exchange_symbols_normalized[:top_n]
            logger.info(
                "подбор-символов: кэш пуст, выполняется bootstrap без фильтра ликвидности (режим=bootstrap mode)"
            )
            logger.info(
                "подбор-символов: CoinGecko отключен, всего на бирже=%s → в кэше с объёмом=%s (порог готовности=%s) → после bootstrap top_n=%s",
                len(exchange_symbols_normalized),
                len(symbols_with_volume),
                min_cache_ready_symbols,
                len(bootstrap_symbols),
            )
            return [futures_symbol_map[symbol] for symbol in bootstrap_symbols]

        liquid_symbols = [
            symbol
            for symbol in symbols_with_volume
            if avg_daily_volumes_normalized.get(symbol, 0.0) >= min_volume_usd
        ]
        ranked_liquid_symbols = sorted(
            liquid_symbols,
            key=lambda symbol: (avg_daily_volumes_normalized[symbol], symbol),
            reverse=True,
        )
        ranked_top_symbols = ranked_liquid_symbols[:top_n]

        logger.info(
            "подбор-символов: CoinGecko отключен (режим=cache-liquidity mode), всего на бирже=%s → прошли расчёт объёма=%s → после фильтра ликвидности=%s → после top_n=%s",
            len(exchange_symbols_normalized),
            len(symbols_with_volume),
            len(liquid_symbols),
            len(ranked_top_symbols),
        )
        logger.info(
            "подбор-символов: фильтр ликвидности по среднедневному объёму (мин_avg_daily_volume_usd=%.2f) исключено=%s",
            min_volume_usd,
            len(symbols_with_volume) - len(liquid_symbols),
        )
        return [futures_symbol_map[symbol] for symbol in ranked_top_symbols]

    market_caps_by_symbol = market_client.get_market_caps(exchange_symbols_normalized)
    ranked_symbols = sorted(
        exchange_symbols_normalized,
        key=lambda symbol: (market_caps_by_symbol.get(symbol, 0.0), symbol),
        reverse=True,
    )
    ranked_top_symbols = ranked_symbols[:top_n]

    volumes_by_symbol: dict[str, float] = {}
    symbols_skipped_by_api_errors = 0

    for start in range(0, len(ranked_top_symbols), coingecko_volume_batch_size):
        batch = ranked_top_symbols[start:start + coingecko_volume_batch_size]
        try:
            volumes_by_symbol.update(market_client.get_total_volumes(batch))
        except (RuntimeError, RetryExhaustedError) as exc:
            symbols_skipped_by_api_errors += len(batch)
            logger.warning(
                "подбор-символов: ошибка внешнего API при получении объёма батча (%s символов, first=%s): %s; символы будут исключены",
                len(batch),
                batch[0] if batch else "n/a",
                exc,
            )
            continue
        except Exception as exc:
            root_cause = exc.__cause__
            if isinstance(root_cause, RetryExhaustedError):
                symbols_skipped_by_api_errors += len(batch)
                logger.warning(
                    "подбор-символов: ошибка внешнего API при получении объёма батча (%s символов, first=%s): %s; символы будут исключены",
                    len(batch),
                    batch[0] if batch else "n/a",
                    exc,
                )
                continue
            raise

    symbols_with_volume = [symbol for symbol in ranked_top_symbols if symbol in volumes_by_symbol]

    liquid_symbols: list[str] = []
    for symbol in symbols_with_volume:
        total_volume = volumes_by_symbol.get(symbol, 0.0)
        if total_volume >= min_volume_usd:
            liquid_symbols.append(symbol)

    excluded_by_liquidity = len(symbols_with_volume) - len(liquid_symbols)
    logger.info(
        "подбор-символов: всего на бирже=%s → после ранжирования top_n=%s → после фильтра ликвидности=%s",
        len(exchange_symbols_normalized),
        len(ranked_top_symbols),
        len(liquid_symbols),
    )
    logger.info(
        "подбор-символов: пропущено символов из-за ошибок внешнего API=%s",
        symbols_skipped_by_api_errors,
    )
    logger.info(
        "подбор-символов: фильтр ликвидности (мин_объем_usd=%.2f) исключено=%s итоговых_символов=%s",
        min_volume_usd,
        excluded_by_liquidity,
        len(liquid_symbols),
    )
    return [futures_symbol_map[symbol] for symbol in liquid_symbols]


def _parse_iso_datetime(
        value: str,
        *,
        argument_name: str,
) -> datetime:
    raw_value = value.strip()
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError as error:
        raise ValueError(
            f"Некорректный формат {argument_name}: {value}. "
            f"Ожидается ISO дата/дата-время, например 2025-01-31 или 2025-01-31T23:59:59"
        ) from error

    return parsed


def _resolve_fetch_anchor_datetime(config: AppConfig, end_datetime_raw: datetime | str | None) -> datetime:
    if isinstance(end_datetime_raw, datetime):
        return end_datetime_raw

    if isinstance(end_datetime_raw, str):
        return _parse_iso_datetime(
            end_datetime_raw,
            argument_name="--end-datetime",
        )

    if config.fetch.anchor_datetime is not None:
        return config.fetch.anchor_datetime

    return datetime.now()


def _fetch_period(config: AppConfig, days: int, end_datetime_raw: datetime | str | None = None) -> tuple[datetime, datetime]:
    local_end = _resolve_fetch_anchor_datetime(config, end_datetime_raw)
    local_start = local_end - timedelta(days=days)
    return local_start, local_end


@dataclass(frozen=True, slots=True)
class FetchSummary:
    total_symbols: int
    success_symbols: int
    failed_symbols: int

    @property
    def failed_ratio(self) -> float:
        return (self.failed_symbols / self.total_symbols) if self.total_symbols else 0.0


def _log_fetch_summary(
    command_name: str,
    logger: Logger,
    total_symbols: int,
    failed_symbols_count: int,
    *,
    emit_log: bool = True,
) -> FetchSummary:
    summary = FetchSummary(
        total_symbols=total_symbols,
        success_symbols=total_symbols - failed_symbols_count,
        failed_symbols=failed_symbols_count,
    )
    if emit_log:
        logger.info(
            "%s: сводка загрузки всего=%s успешно=%s с ошибками=%s доля_ошибок=%.2f%%",
            command_name,
            summary.total_symbols,
            summary.success_symbols,
            summary.failed_symbols,
            summary.failed_ratio * 100,
        )
    return summary


def _fetch_exit_code(failed_symbols_count: int, critical_fail_threshold: int = 1) -> int:
    return 1 if failed_symbols_count >= critical_fail_threshold else 0


def _resolve_liquidity_skip_reason(summary: FetchSummary | None, threshold: float) -> str | None:
    if summary is None:
        return "no_ohlcv_cache_data"
    if summary.success_symbols == 0:
        return "no_ohlcv_cache_data"
    if summary.failed_ratio > threshold:
        return "ohlcv_error_ratio_above_threshold"
    return None


def _log_loaded_coins(logger: Logger, count: int, action: str) -> None:
    templates = {
        "loaded": "Загружено %s монет.",
        "updated": "Обновлено %s монет.",
    }
    template = templates.get(action)
    if template is None:
        raise ValueError(f"Неподдерживаемое действие: {action}")
    logger.info(template, count)


def _resolve_timeframe(value: str | None, *, fallback: Timeframe, argument_name: str) -> Timeframe:
    if value is None:
        return fallback

    normalized = value.strip().lower()
    for timeframe in Timeframe:
        if timeframe.value == normalized:
            return timeframe

    supported = ", ".join(tf.value for tf in Timeframe)
    raise ValueError(f"Некорректное значение {argument_name}: {value}. Поддерживаемые значения: {supported}")


def _fetch_data_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("fetch-data", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetcher, exchange_client, market_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    ignore_coingecko = args.ignore_coingecko if args.ignore_coingecko is not None else config.fetch.ignore_coingecko
    market_client.set_skip_invalid_coin_id_filter(ignore_coingecko)
    top_n = args.top_n if args.top_n is not None else all_futures_count
    if ignore_coingecko:
        futures_symbol_map = {
            normalize_symbol(symbol): symbol
            for symbol in futures_symbols
        }
        exchange_symbols_normalized = sorted(futures_symbol_map)
        bootstrap_symbols = exchange_symbols_normalized[:top_n]
        symbols = [futures_symbol_map[symbol] for symbol in bootstrap_symbols]
        logger.info(
            "загрузка-данных: CoinGecko отключен, первичная загрузка кэша до расчёта ликвидности (bootstrap symbols=%s)",
            len(symbols),
        )
    else:
        symbols = _resolve_symbols(
            exchange_client,
            market_client,
            top_n=top_n,
            min_volume_usd=min_volume_usd,
            logger=logger,
            coingecko_volume_batch_size=config.fetch.coingecko_volume_batch_size,
            ignore_coingecko=ignore_coingecko,
            cache_dir=config.backtest.cache_dir,
            liquidity_timeframe=config.fetch.timeframe,
            futures_symbols_raw=futures_symbols,
        )
    logger.info(
        "загрузка-данных: найдено фьючерсов=%s отправлено в fetch_all=%s",
        all_futures_count,
        len(symbols),
    )
    if not symbols:
        logger.info("загрузка-данных: не найдено символов для загрузки")
        return 0

    start_time, end_time = _fetch_period(config, args.days, getattr(args, "end_datetime", None))
    failed_symbols: set[str] = set()
    fetch_summaries: dict[Timeframe, FetchSummary] = {}
    for timeframe in config.fetch.timeframes:
        logger.info("загрузка-данных: сбор кэша для TF=%s", timeframe.value)
        result = fetcher.fetch_all(symbols=symbols, timeframe=timeframe, start_time=start_time, end_time=end_time)
        fetch_summaries[timeframe] = _log_fetch_summary(
            f"fetch-data[{timeframe.value}]",
            logger,
            len(symbols),
            result.failed_symbols_count,
            emit_log=(not ignore_coingecko or timeframe == config.fetch.timeframe),
        )
        failed_symbols.update(
            symbol
            for symbol in symbols
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )

    root_stage_status = "ok"
    if ignore_coingecko:
        liquidity_summary = fetch_summaries.get(config.fetch.timeframe)
        skip_reason = _resolve_liquidity_skip_reason(
            liquidity_summary,
            config.fetch.liquidity_skip_error_ratio_threshold,
        )
        if skip_reason is None:
            _ = _resolve_symbols(
                exchange_client,
                market_client,
                top_n=top_n,
                min_volume_usd=min_volume_usd,
                logger=logger,
                coingecko_volume_batch_size=config.fetch.coingecko_volume_batch_size,
                ignore_coingecko=True,
                cache_dir=config.backtest.cache_dir,
                liquidity_timeframe=config.fetch.timeframe,
                futures_symbols_raw=futures_symbols,
            )
        else:
            root_stage_status = "ohlcv_cache_failed"
            logger.warning(
                "liquidity-skip: reason=%s timeframe=%s",
                skip_reason,
                config.fetch.timeframe.value,
            )

    exit_code = _fetch_exit_code(len(failed_symbols))
    if root_stage_status == "ohlcv_cache_failed":
        exit_code = 2

    logger.info("fetch-data: status=%s exit_code=%s", root_stage_status, exit_code)
    _log_loaded_coins(logger, len(symbols), "loaded")
    return exit_code


def _update_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("update-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetcher, exchange_client, market_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    ignore_coingecko = args.ignore_coingecko if args.ignore_coingecko is not None else config.fetch.ignore_coingecko
    market_client.set_skip_invalid_coin_id_filter(ignore_coingecko)
    top_n = args.top_n if args.top_n is not None else all_futures_count
    symbols = _resolve_symbols(
        exchange_client,
        market_client,
        top_n=top_n,
        min_volume_usd=min_volume_usd,
        logger=logger,
        coingecko_volume_batch_size=config.fetch.coingecko_volume_batch_size,
        ignore_coingecko=ignore_coingecko,
        cache_dir=config.backtest.cache_dir,
        liquidity_timeframe=config.fetch.timeframe,
        futures_symbols_raw=futures_symbols,
    )
    logger.info(
        "обновление-кэша: найдено фьючерсов=%s отправлено в fetch_all=%s",
        all_futures_count,
        len(symbols),
    )
    if not symbols:
        logger.info("обновление-кэша: не найдено символов для обновления")
        return 0

    start_time, end_time = _fetch_period(config, args.days, getattr(args, "end_datetime", None))
    failed_symbols: set[str] = set()
    for timeframe in config.fetch.timeframes:
        logger.info("обновление-кэша: сбор кэша для TF=%s", timeframe.value)
        result = fetcher.fetch_all(symbols=symbols, timeframe=timeframe, start_time=start_time, end_time=end_time)
        _log_fetch_summary(f"update-cache[{timeframe.value}]", logger, len(symbols), result.failed_symbols_count)
        failed_symbols.update(
            symbol
            for symbol in symbols
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )

    exit_code = _fetch_exit_code(len(failed_symbols))
    _log_loaded_coins(logger, len(symbols), "updated")
    return exit_code


def _run_backtest_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-backtest", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    levels_timeframe = _resolve_timeframe(
        getattr(args, "levels_tf", None),
        fallback=config.strategy.levels_timeframe,
        argument_name="--levels-tf",
    )
    entry_timeframe = _resolve_timeframe(
        getattr(args, "entry_tf", None),
        fallback=config.strategy.entry_timeframe,
        argument_name="--entry-tf",
    )
    logger.info(
        "запуск-бэктеста: явный запуск, уровни: %s, входы: %s",
        levels_timeframe.value,
        entry_timeframe.value,
    )

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = args.symbols or preparer.list_symbols(entry_timeframe)
    if not symbols:
        logger.info("запуск-бектеста: нет данных в кэше")
        return 0

    symbol_frames: dict[str, SymbolMtfFrames] = {}
    symbols_total = len(symbols)
    symbols_missing_levels_tf = 0
    symbols_missing_entry_tf = 0
    symbols_used = 0
    for symbol in symbols:
        frames_by_tf = preparer.load_symbol_data_multi(symbol, [levels_timeframe, entry_timeframe])
        levels_frame = frames_by_tf.get(levels_timeframe, pd.DataFrame())
        entry_frame = frames_by_tf.get(entry_timeframe, pd.DataFrame())
        if levels_frame.empty:
            symbols_missing_levels_tf += 1
        if entry_frame.empty:
            symbols_missing_entry_tf += 1
        if levels_frame.empty or entry_frame.empty:
            continue
        symbols_used += 1
        symbol_frames[symbol] = SymbolMtfFrames(
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            levels_frame=levels_frame,
            entry_frame=entry_frame,
        )
    if not symbol_frames:
        logger.info("запуск-бектеста: не удалось подготовить данные")
        return 0

    strategy = BreakoutStrategy(
        commission_rate=config.simulation.commission_rate,
        slippage=config.simulation.slippage,
        logger=logger,
    )
    runner = BacktestRunner(
        config.backtest.results_dir,
        config.backtest.results_file_name,
        logger=logger,
    )
    symbols_used_ratio = symbols_used / symbols_total if symbols_total else 0.0
    logger.info(
        "запуск-бэктеста: сводка по символам всего=%s использовано=%s без_данных_levels_tf=%s без_данных_entry_tf=%s",
        symbols_total,
        symbols_used,
        symbols_missing_levels_tf,
        symbols_missing_entry_tf,
    )
    if symbols_total and symbols_used_ratio < 0.2:
        logger.warning(
            "запуск-бэктеста: используется только %.1f%% символов (%s из %s); результат бэктеста может быть нерепрезентативным",
            symbols_used_ratio * 100,
            symbols_used,
            symbols_total,
        )
    results = runner.run(
        strategy,
        symbol_frames,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    summary = runner.build_summary(results)
    combinations_with_trades = int((results["trades_count"] > 0).sum()) if not results.empty else 0
    total_trades = int(results["trades_count"].sum()) if not results.empty else 0
    logger.info(
        "запуск-бэктеста: всего=%s прибыльных=%s лучший_pf=%.4f комбинаций_со_сделками=%s",
        summary.total_combinations,
        summary.profitable_combinations,
        summary.best_pf,
        combinations_with_trades,
    )
    if summary.best_pf == 0 and total_trades == 0:
        logger.warning(
            "запуск-бэктеста: отсутствуют сделки по всем комбинациям; проверьте достаточность истории для levels_tf=%s и соответствие таймфреймов в кэше (%s/%s)",
            levels_timeframe.value,
            levels_timeframe.value,
            entry_timeframe.value,
        )
    return 0


def _make_report_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("make-report", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    csv_path = Path(args.input) if args.input else config.backtest.results_dir / config.backtest.results_file_name
    if not csv_path.exists():
        logger.info(f"подготовка-отчета: файл не найден: {csv_path}")
        return 1

    frame = pd.read_csv(csv_path)
    required_columns = [
        "trades_count",
        "profit_factor",
        "lookback",
        "volume_mult",
        "sl_count",
        "be_count",
        "tp1_be_count",
        "tp2_count",
    ]
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        logger.error("подготовка-отчета: отсутствуют обязательные колонки: %s", ", ".join(missing_columns))
        return 1

    if frame.empty:
        logger.info("подготовка-отчета: пустой файл результатов")
        return 1

    filtered = frame[(frame["trades_count"] >= REPORT_TRADES_COUNT_FILTER) & (
                frame["profit_factor"] > REPORT_PROFIT_FACTOR_FILTER)].copy()
    filtered = filtered.sort_values("profit_factor", ascending=False)

    if len(frame) != TARGET_PARAMETER_COMBINATIONS:
        logger.warning(
            "подготовка-отчета: фактическое число комбинаций=%s отличается от целевого=%s",
            len(frame),
            TARGET_PARAMETER_COMBINATIONS,
        )

    summary = BacktestSummary(
        total_combinations=int(len(frame)),
        profitable_combinations=int((frame["profit_factor"] > REPORT_PROFITABLE_PF_THRESHOLD).sum()),
        best_pf=round(float(frame["profit_factor"].max()), 4),
    )

    source = filtered if not filtered.empty else frame

    optimal_ranges = OptimalParameterRanges(
        lookback=[int(source["lookback"].min()), int(source["lookback"].max())],
        volume_multiplier=[round(float(source["volume_mult"].min()), 4), round(float(source["volume_mult"].max()), 4)],
    )

    distribution = TradeResultsDistribution(
        SL=int(source["sl_count"].sum()),
        BE=int(source["be_count"].sum()),
        TP1_BE=int(source["tp1_be_count"].sum()),
        TP2=int(source["tp2_count"].sum()),
    )

    report = BacktestReport(
        summary=summary,
        optimal_ranges=optimal_ranges,
        trade_results_distribution=distribution,
    )

    output_path = Path(args.output) if args.output else config.backtest.results_dir / DEFAULT_REPORT_OUTPUT_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"подготовка-отчета: сохранено {output_path}")
    return 0


def _collect_oi_alignment_issues(frame: pd.DataFrame) -> list[dict[str, str]]:
    if "open_interest" not in frame.columns:
        return [
            {
                "issue_type": QUALITY_OI_MISSING_COLUMN_ISSUE,
                "severity": QUALITY_SEVERITY_ERROR,
                "description": "Отсутствует колонка open_interest",
            }
        ]

    oi = pd.to_numeric(frame["open_interest"], errors="coerce")
    issues: list[dict[str, str]] = []

    if oi.isna().any():
        issues.append(
            {
                "issue_type": QUALITY_OI_ALIGNMENT_MISSING_VALUES_ISSUE,
                "severity": QUALITY_SEVERITY_WARNING,
                "description": "Есть пропуски open_interest после выравнивания",
            }
        )

    if len(oi) > 1:
        first_valid = oi.first_valid_index()
        if first_valid is not None:
            leading_missing = oi.loc[:first_valid].isna().sum()
            if leading_missing > 0:
                issues.append(
                    {
                        "issue_type": QUALITY_OI_ALIGNMENT_LEADING_GAPS_ISSUE,
                        "severity": QUALITY_SEVERITY_WARNING,
                        "description": "Обнаружены пропуски open_interest в начале ряда",
                    }
                )

        aligned_oi = oi.ffill()
        aligned_valid_mask = aligned_oi.notna()
        comparison_mask = aligned_valid_mask & aligned_valid_mask.shift(1, fill_value=False)
        compared_observations = int(comparison_mask.sum())

        if compared_observations >= OI_STALE_MIN_OBSERVATIONS:
            stale_ratio = (aligned_oi.diff().eq(0) & comparison_mask).sum() / compared_observations
        else:
            stale_ratio = 0.0

        if stale_ratio > OI_STALE_RATIO_THRESHOLD:
            issues.append(
                {
                    "issue_type": QUALITY_OI_ALIGNMENT_STALE_SERIES_ISSUE,
                    "severity": QUALITY_SEVERITY_ERROR,
                    "description": "open_interest почти не меняется, вероятна рассинхронизация",
                }
            )

    return issues


def _build_quality_recommendations(summary: QualitySummary, symbols: dict[str, QualitySymbolStats]) -> list[str]:
    recommendations: list[str] = []
    if summary.gaps_total > 0:
        recommendations.append("Дозагрузка диапазона: запустите update-cache для символов с пропусками")

    if any(data.gaps > 0 for data in symbols.values()):
        recommendations.append("Проверка таймфрейма: убедитесь, что timeframe совпадает с кэшем")

    if summary.issues_total > 0:
        recommendations.append("Дедупликация и очистка: переcохраните ряды с удалением дублей и аномалий")

    oi_problem_types = {
        QUALITY_OI_MISSING_COLUMN_ISSUE,
        QUALITY_OI_ALIGNMENT_MISSING_VALUES_ISSUE,
        QUALITY_OI_ALIGNMENT_LEADING_GAPS_ISSUE,
        QUALITY_OI_ALIGNMENT_STALE_SERIES_ISSUE,
    }
    if any(problem in oi_problem_types for problem in summary.by_issue_type):
        recommendations.append("Ресинхронизация OI: перезапустите загрузку OI с выравниванием относительно OHLCV")

    if summary.issues_total > 0 or summary.gaps_total > 0:
        recommendations.append("Повторная валидация: после исправлений выполните check-quality повторно")

    return recommendations


def _save_quality_report(report: QualityReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".csv":
        symbols = report.symbols
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["symbol", "issues", "gaps", "warning", "error", "critical", "info"])
            for symbol, data in symbols.items():
                sev = data.by_severity
                writer.writerow(
                    [
                        symbol,
                        data.issues,
                        data.gaps,
                        sev.get(QUALITY_SEVERITY_WARNING, 0),
                        sev.get(QUALITY_SEVERITY_ERROR, 0),
                        sev.get(QUALITY_SEVERITY_CRITICAL, 0),
                        sev.get(QUALITY_SEVERITY_INFO, 0),
                    ]
                )
    else:
        output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")


def _check_quality_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("check-quality", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = args.symbols or preparer.list_symbols(config.fetch.timeframe)
    if not symbols:
        logger.info("проверка-качества: нет данных для проверки")
        return 0

    validator = DataValidator()
    gap_detector = GapDetector()

    issues_by_type: Counter[str] = Counter()
    issues_by_severity: Counter[str] = Counter()
    symbols_report: dict[str, QualitySymbolStats] = {}

    total_issues = 0
    total_gaps = 0
    for symbol in symbols:
        frame = preparer.load_symbol_data(symbol, config.fetch.timeframe)
        if frame.empty:
            logger.info(f"проверка-качества: {symbol} пропущен, пустой датасет")
            continue

        issues = validator.validate(symbol, config.fetch.timeframe, frame)
        gaps = gap_detector.detect_gaps(frame, config.fetch.timeframe)
        oi_alignment_issues = _collect_oi_alignment_issues(frame)

        all_issue_types = [issue.issue_type for issue in issues]
        all_severities = [issue.severity.value for issue in issues]
        all_issue_types.extend(item["issue_type"] for item in oi_alignment_issues)
        all_severities.extend(item["severity"] for item in oi_alignment_issues)

        symbol_issue_counter = Counter(all_issue_types)
        symbol_severity_counter = Counter(all_severities)
        issues_by_type.update(symbol_issue_counter)
        issues_by_severity.update(symbol_severity_counter)

        symbol_total_issues = len(issues) + len(oi_alignment_issues)
        total_issues += symbol_total_issues
        total_gaps += len(gaps)

        symbols_report[symbol] = QualitySymbolStats(
            issues=symbol_total_issues,
            gaps=len(gaps),
            by_issue_type=dict(sorted(symbol_issue_counter.items())),
            by_severity=dict(sorted(symbol_severity_counter.items())),
        )

        logger.info(
            f"проверка-качества: {symbol} проблемы={symbol_total_issues} пропуски={len(gaps)} "
            f"проблемы_выравнивания_oi={len(oi_alignment_issues)}"
        )

    summary = QualitySummary(
        symbols_checked=len(symbols_report),
        issues_total=total_issues,
        gaps_total=total_gaps,
        by_issue_type=dict(sorted(issues_by_type.items())),
        by_severity=dict(sorted(issues_by_severity.items())),
    )
    report = QualityReport(
        summary=summary,
        symbols=symbols_report,
        recommendations=_build_quality_recommendations(summary, symbols_report),
    )

    output_path = Path(args.output) if args.output else config.backtest.results_dir / DEFAULT_QUALITY_REPORT_OUTPUT_FILE
    _save_quality_report(report, output_path)

    logger.info(f"проверка-качества: итог проблемы={report.summary.issues_total} пропуски={report.summary.gaps_total}")
    logger.info(f"проверка-качества: отчет сохранен {output_path}")
    return 0


def _clear_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    del args
    logger = get_logger("clear-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    cache_dir = config.backtest.cache_dir
    cache_dir_str = str(cache_dir).strip()
    if not cache_dir_str:
        logger.error("очистка-кэша: путь к директории кэша пустой, удаление отменено")
        return 1

    resolved_cache_dir = cache_dir.expanduser().resolve()
    home_dir = Path.home().resolve()
    if resolved_cache_dir == Path(resolved_cache_dir.anchor):
        logger.error(f"очистка-кэша: путь '{resolved_cache_dir}' указывает на корень ФС, удаление отменено")
        return 1

    if resolved_cache_dir == home_dir:
        logger.error(f"очистка-кэша: путь '{resolved_cache_dir}' указывает на домашнюю директорию, удаление отменено")
        return 1

    logger.info(f"очистка-кэша: удаление содержимого {resolved_cache_dir}")
    shutil.rmtree(resolved_cache_dir, ignore_errors=True)
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"очистка-кэша: директория пересоздана {resolved_cache_dir}")
    return 0


def _plot_daily_levels_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("plot-daily-levels", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    levels_timeframe = _resolve_timeframe(
        getattr(args, "levels_tf", None),
        fallback=config.strategy.levels_timeframe,
        argument_name="--levels-tf",
    )
    entry_timeframe = _resolve_timeframe(
        getattr(args, "entry_tf", None),
        fallback=config.strategy.entry_timeframe,
        argument_name="--entry-tf",
    )
    if levels_timeframe == entry_timeframe:
        raise ValueError("--levels-tf и --entry-tf должны отличаться")

    limit = getattr(args, "limit", None)
    if limit is not None and limit <= 0:
        raise ValueError("--limit должен быть положительным числом")

    output_dir_raw = str(getattr(args, "output_dir", "") or "").strip()
    output_dir = Path(output_dir_raw) if output_dir_raw else config.backtest.results_dir / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = list(dict.fromkeys(args.symbols or preparer.list_symbols(entry_timeframe)))
    if not symbols:
        logger.info("plot-daily-levels: нет символов для построения")
        return 0

    base_params = BacktestRunner.build_parameter_grid()[0]
    strategy = BreakoutStrategy(
        commission_rate=config.simulation.commission_rate,
        slippage=config.simulation.slippage,
        logger=logger,
    )
    plotter = StrategyPlotter(data_preparer=preparer, strategy=strategy)

    built = 0
    failed = 0
    skipped_empty = 0
    for symbol in symbols:
        params = BreakoutParams(
            lookback=base_params.lookback,
            volume_mult=base_params.volume_mult,
            retest_window_hours=base_params.retest_window_hours,
            retest_zone=base_params.retest_zone,
            min_rr=base_params.min_rr,
            sl_mode=base_params.sl_mode,
            tp2_mult=base_params.tp2_mult,
            min_body_ratio=base_params.min_body_ratio,
            min_move_atr=base_params.min_move_atr,
            max_retest_depth=base_params.max_retest_depth,
            confirmation_bars=base_params.confirmation_bars,
            entry_trigger=base_params.entry_trigger,
            symbol=symbol,
            retest_zone_atr=base_params.retest_zone_atr,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )
        try:
            output_path = plotter.plot_daily_levels(symbol=symbol, params=params, output_dir=output_dir)
            if output_path is None:
                skipped_empty += 1
                logger.warning("plot-daily-levels: %s пропущен — пустые данные", symbol)
                continue
            built += 1
            logger.info("plot-daily-levels: сохранен график %s", output_path)
        except Exception as exc:
            failed += 1
            logger.warning("plot-daily-levels: ошибка по символу %s: %s", symbol, exc)

    logger.info(
        "plot-daily-levels: итог symbols=%s saved=%s skipped_empty=%s failed=%s output_dir=%s limit=%s",
        len(symbols),
        built,
        skipped_empty,
        failed,
        output_dir,
        limit,
    )
    if built == 0 and failed > 0:
        logger.error("plot-daily-levels: не удалось построить ни одного графика")
        return 1
    return 0


def _load_retest_spans_artifact(path: Path, logger: Logger) -> list[RetestPlotSpan]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("plot-retests: не удалось прочитать артефакт ретестов %s: %s", path, exc)
        return []

    if not isinstance(raw, list):
        logger.warning("plot-retests: артефакт ретестов %s имеет некорректный формат", path)
        return []

    spans: list[RetestPlotSpan] = []
    for item in raw:
        try:
            if not isinstance(item, dict):
                continue
            spans.append(
                RetestPlotSpan(
                    symbol=str(item["symbol"]),
                    side=PositionSide(str(item["side"])),
                    level_price=float(item["level_price"]),
                    retest_low=float(item["retest_low"]),
                    retest_high=float(item["retest_high"]),
                    retest_start_time=pd.Timestamp(item["retest_start_time"]),
                    retest_end_time=pd.Timestamp(item["retest_end_time"]),
                    status=str(item["status"]),
                )
            )
        except Exception:
            continue
    return spans


def _plot_retests_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("plot-retests", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    levels_timeframe = _resolve_timeframe(
        getattr(args, "levels_tf", None),
        fallback=config.strategy.levels_timeframe,
        argument_name="--levels-tf",
    )
    entry_timeframe = _resolve_timeframe(
        getattr(args, "entry_tf", None),
        fallback=config.strategy.entry_timeframe,
        argument_name="--entry-tf",
    )
    if levels_timeframe == entry_timeframe:
        raise ValueError("--levels-tf и --entry-tf должны отличаться")

    limit = getattr(args, "limit", None)
    if limit is not None and limit <= 0:
        raise ValueError("--limit должен быть положительным числом")

    output_dir_raw = str(getattr(args, "output_dir", "") or "").strip()
    output_dir = Path(output_dir_raw) if output_dir_raw else config.backtest.results_dir / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = list(dict.fromkeys(args.symbols or preparer.list_symbols(entry_timeframe)))
    if not symbols:
        logger.info("plot-retests: нет символов для построения")
        return 0

    base_params = BacktestRunner.build_parameter_grid()[0]
    strategy = BreakoutStrategy(
        commission_rate=config.simulation.commission_rate,
        slippage=config.simulation.slippage,
        logger=logger,
    )
    plotter = StrategyPlotter(data_preparer=preparer, strategy=strategy)

    retest_artifact_path = config.backtest.results_dir / "retest_plot_spans.json"
    artifact_spans = _load_retest_spans_artifact(retest_artifact_path, logger)
    artifact_spans_by_symbol: dict[str, list[RetestPlotSpan]] = {}
    for span in artifact_spans:
        artifact_spans_by_symbol.setdefault(span.symbol, []).append(span)

    saved = 0
    failed = 0
    skipped_empty = 0
    skipped_no_spans = 0

    for symbol in symbols:
        params = BreakoutParams(
            lookback=base_params.lookback,
            volume_mult=base_params.volume_mult,
            retest_window_hours=base_params.retest_window_hours,
            retest_zone=base_params.retest_zone,
            min_rr=base_params.min_rr,
            sl_mode=base_params.sl_mode,
            tp2_mult=base_params.tp2_mult,
            min_body_ratio=base_params.min_body_ratio,
            min_move_atr=base_params.min_move_atr,
            max_retest_depth=base_params.max_retest_depth,
            confirmation_bars=base_params.confirmation_bars,
            entry_trigger=base_params.entry_trigger,
            symbol=symbol,
            retest_zone_atr=base_params.retest_zone_atr,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )
        try:
            mtf_frames = SymbolMtfFrames(
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                levels_frame=preparer.load_symbol_data(symbol, levels_timeframe),
                entry_frame=preparer.load_symbol_data(symbol, entry_timeframe),
            )
            diagnostics_spans: list[RetestPlotSpan] = []
            if not mtf_frames.levels_frame.empty and not mtf_frames.entry_frame.empty:
                strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
                diagnostics = strategy.consume_last_generation_diagnostics()
                raw_spans = diagnostics.get("retest_plot_spans", [])
                diagnostics_spans = [span for span in raw_spans if isinstance(span, RetestPlotSpan)]

            if diagnostics_spans:
                retest_spans = diagnostics_spans
            else:
                retest_spans = artifact_spans_by_symbol.get(symbol, [])

            if limit is not None and retest_spans:
                retest_spans = retest_spans[:limit]

            if not retest_spans:
                if mtf_frames.levels_frame.empty or mtf_frames.entry_frame.empty:
                    skipped_empty += 1
                    logger.warning("plot-retests: %s пропущен — пустые данные", symbol)
                else:
                    skipped_no_spans += 1
                    logger.info("plot-retests: %s пропущен — ретесты не найдены", symbol)
                continue

            saved_paths = plotter.plot_retests(
                symbol=symbol,
                params=params,
                output_dir=output_dir,
                retest_spans=retest_spans,
            )
            if not saved_paths:
                skipped_empty += 1
                logger.warning("plot-retests: %s пропущен — пустые данные для визуализации", symbol)
                continue
            saved += len(saved_paths)
            logger.info("plot-retests: %s сохранено графиков=%s", symbol, len(saved_paths))
        except Exception as exc:
            failed += 1
            logger.warning("plot-retests: ошибка по символу %s: %s", symbol, exc)

    logger.info(
        "plot-retests: итог symbols=%s saved=%s skipped_empty=%s skipped_no_spans=%s failed=%s output_dir=%s limit=%s",
        len(symbols),
        saved,
        skipped_empty,
        skipped_no_spans,
        failed,
        output_dir,
        limit,
    )
    if saved == 0 and failed > 0:
        logger.error("plot-retests: не удалось построить ни одного графика")
        return 1
    return 0


# endregion Приватные

# Публичные точки входа

def fetch_data(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает сценарий загрузки рыночных данных."""
    return _run_with_logging("fetch-data", config, lambda: _fetch_data_inner(config, args))


def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Обновляет локальный кэш данных."""
    return _run_with_logging("update-cache", config, lambda: _update_cache_inner(config, args))


def run_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает бэктест по текущей конфигурации."""
    return _run_with_logging("run-backtest", config, lambda: _run_backtest_inner(config, args))


def make_report(config: AppConfig, args: argparse.Namespace) -> int:
    """Формирует итоговый отчёт по результатам."""
    return _run_with_logging("make-report", config, lambda: _make_report_inner(config, args))


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """Проверяет качество и целостность данных."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Очищает директорию локального кэша и пересоздаёт её."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))


def plot_daily_levels(config: AppConfig, args: argparse.Namespace) -> int:
    """Строит графики с дневными уровнями."""
    return _run_with_logging("plot-daily-levels", config, lambda: _plot_daily_levels_inner(config, args))


def plot_retests(config: AppConfig, args: argparse.Namespace) -> int:
    """Строит графики с ретестами уровней."""
    return _run_with_logging("plot-retests", config, lambda: _plot_retests_inner(config, args))
