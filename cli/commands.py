"""Модуль проекта."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, tzinfo
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
from domain.enums.timeframe import Timeframe
from domain.models.reporting.backtest_report import BacktestReport
from domain.models.reporting.backtest_summary import BacktestSummary
from domain.models.reporting.optimal_parameter_ranges import OptimalParameterRanges
from domain.models.reporting.quality_report import QualityReport
from domain.models.reporting.quality_summary import QualitySummary
from domain.models.reporting.quality_symbol_stats import QualitySymbolStats
from domain.models.reporting.trade_results_distribution import TradeResultsDistribution
from strategy.breakout.breakout_strategy import BreakoutStrategy
from strategy.breakout.config import TARGET_PARAMETER_COMBINATIONS
from utils.formatters import datetime_to_utc
from utils.logger import get_logger
from utils.retry import RetryExhaustedError
from utils.symbols import normalize_symbol
from vectorbt_runner import BacktestRunner, DataPreparer, SymbolMtfFrames


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
    storage = ParquetStorage(base_dir=config.backtest.cache_dir)
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
            "подбор-символов: CoinGecko отключен, всего на бирже=%s → прошли расчёт объёма=%s → после фильтра ликвидности=%s → после top_n=%s",
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
        target_timezone: tzinfo,
) -> datetime:
    normalized_value = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized_value)
    except ValueError as error:
        raise ValueError(
            f"Некорректный формат {argument_name}: {value}. "
            f"Ожидается ISO дата/дата-время, например 2025-01-31 или 2025-01-31T23:59:59+03:00"
        ) from error

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=target_timezone)
    return parsed.astimezone(target_timezone)


def _resolve_fetch_anchor_datetime(config: AppConfig, end_datetime_raw: datetime | str | None) -> datetime:
    if isinstance(end_datetime_raw, datetime):
        if end_datetime_raw.tzinfo is None:
            return end_datetime_raw.replace(tzinfo=config.fetch.tzinfo)
        return end_datetime_raw.astimezone(config.fetch.tzinfo)

    if isinstance(end_datetime_raw, str):
        return _parse_iso_datetime(
            end_datetime_raw,
            argument_name="--end-datetime",
            target_timezone=config.fetch.tzinfo,
        )

    if config.fetch.anchor_datetime is not None:
        return config.fetch.anchor_datetime

    return datetime.now(tz=config.fetch.tzinfo)


def _fetch_period(config: AppConfig, days: int, end_datetime_raw: datetime | str | None = None) -> tuple[datetime, datetime]:
    local_end = _resolve_fetch_anchor_datetime(config, end_datetime_raw)
    local_start = local_end - timedelta(days=days)
    return datetime_to_utc(local_start), datetime_to_utc(local_end)


def _log_fetch_summary(command_name: str, logger: Logger, total_symbols: int, failed_symbols_count: int) -> None:
    failed_ratio = (failed_symbols_count / total_symbols) if total_symbols else 0.0
    logger.info(
        "%s: сводка загрузки всего=%s успешно=%s с ошибками=%s доля_ошибок=%.2f%%",
        command_name,
        total_symbols,
        total_symbols - failed_symbols_count,
        failed_symbols_count,
        failed_ratio * 100,
    )


def _fetch_exit_code(failed_symbols_count: int, critical_fail_threshold: int = 1) -> int:
    return 1 if failed_symbols_count >= critical_fail_threshold else 0


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
        "загрузка-данных: найдено фьючерсов=%s отправлено в fetch_all=%s",
        all_futures_count,
        len(symbols),
    )
    if not symbols:
        logger.info("загрузка-данных: не найдено символов для загрузки")
        return 0

    start_time, end_time = _fetch_period(config, args.days, getattr(args, "end_datetime", None))
    result = fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time,
                               end_time=end_time)
    _log_fetch_summary("fetch-data", logger, len(symbols), result.failed_symbols_count)
    exit_code = _fetch_exit_code(result.failed_symbols_count)
    _log_loaded_coins(logger, len(symbols), "loaded")
    return exit_code


def _update_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("update-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetcher, exchange_client, market_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    ignore_coingecko = args.ignore_coingecko if args.ignore_coingecko is not None else config.fetch.ignore_coingecko
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
    result = fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time,
                               end_time=end_time)
    _log_fetch_summary("update-cache", logger, len(symbols), result.failed_symbols_count)
    exit_code = _fetch_exit_code(result.failed_symbols_count)
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
    for symbol in symbols:
        frames_by_tf = preparer.load_symbol_data_multi(symbol, [levels_timeframe, entry_timeframe])
        levels_frame = frames_by_tf.get(levels_timeframe, pd.DataFrame())
        entry_frame = frames_by_tf.get(entry_timeframe, pd.DataFrame())
        if levels_frame.empty or entry_frame.empty:
            continue
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
        strategy_timezone=config.strategy.timezone,
        simulation_timezone=config.simulation.timezone,
    )
    runner = BacktestRunner(config.backtest.results_dir, config.backtest.results_file_name)
    results = runner.run(
        strategy,
        symbol_frames,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    summary = runner.build_summary(results)
    logger.info(
        "запуск-бэктеста: всего=%s прибыльных=%s лучший_pf=%.4f",
        summary.total_combinations,
        summary.profitable_combinations,
        summary.best_pf,
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
