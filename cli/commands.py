"""CLI command handlers."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from config import AppConfig
from constants import DEFAULT_QUALITY_REPORT_OUTPUT_FILE, DEFAULT_REPORT_OUTPUT_FILE, OI_STALE_RATIO_THRESHOLD
from data.clients.coingecko_client import CoinGeckoClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.quality.data_validator import DataValidator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetStorage
from domain.enums.exchange import Exchange
from strategy.breakout.breakout_strategy import BreakoutStrategy
from utils.formatters import datetime_to_utc
from utils.logger import get_logger
from strategy.breakout.config import TARGET_PARAMETER_COMBINATIONS
from vectorbt_runner.backtest_runner import BacktestRunner
from vectorbt_runner.data_preparer import DataPreparer


def _run_with_logging(command_name: str, config: AppConfig, body: Callable[[], int]) -> int:
    logger = get_logger(
        command_name,
        level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    logger.info(f"{command_name}: старт")
    try:
        code = body()
        logger.info(f"{command_name}: завершено (code={code})")
        return code
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"{command_name}: ошибка: {exc}")
        return 1


def _build_fetch_stack(config: AppConfig) -> tuple[MarketDataFetcher, CcxtFuturesClient, CoinGeckoClient]:
    exchange_client = CcxtFuturesClient(
        exchange=Exchange.BINANCE,
        api_key=config.fetch.binance_api_key,
        secret=config.fetch.binance_secret_key,
    )
    storage = ParquetStorage(base_dir=config.backtest.cache_dir)
    ohlcv_fetcher = OhlcvFetcher(
        exchange_client=exchange_client,
        storage=storage,
        max_workers=config.fetch.max_concurrent_requests,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    oi_fetcher = OiFetcher(
        exchange_client=exchange_client,
        storage=storage,
        max_workers=config.fetch.max_concurrent_requests,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    market_client = CoinGeckoClient(api_key=config.fetch.coingecko_api_key)
    return (
        MarketDataFetcher(
            ohlcv_fetcher=ohlcv_fetcher,
            oi_fetcher=oi_fetcher,
            market_data_client=market_client,
            max_workers=config.fetch.max_concurrent_requests,
            log_level=config.backtest.log_level,
            logs_dir=config.backtest.logs_dir,
        ),
        exchange_client,
        market_client,
    )


def _resolve_symbols(exchange_client: CcxtFuturesClient, market_client: CoinGeckoClient, top_n: int) -> list[str]:
    top_symbols = {f"{symbol}/USDT" for symbol in market_client.get_top_coins_by_market_cap(limit=top_n)}
    futures_symbols = set(exchange_client.get_futures_symbols())
    return sorted(top_symbols.intersection(futures_symbols))



def _fetch_period(config: AppConfig, days: int) -> tuple[datetime, datetime]:
    local_end = datetime.now(tz=config.fetch.tzinfo)
    local_start = local_end - timedelta(days=days)
    return datetime_to_utc(local_start), datetime_to_utc(local_end)

def fetch_data(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("fetch-data", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
        fetcher, exchange_client, market_client = _build_fetch_stack(config)
        symbols = _resolve_symbols(exchange_client, market_client, top_n=args.top_n)
        if not symbols:
            logger.info("fetch-data: не найдено символов для загрузки")
            return 0

        start_time, end_time = _fetch_period(config, args.days)
        fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time, end_time=end_time)
        logger.info(f"fetch-data: загружено symbols={len(symbols)}")
        return 0

    return _run_with_logging("fetch-data", config, _inner)


def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("update-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
        fetcher, exchange_client, market_client = _build_fetch_stack(config)
        symbols = _resolve_symbols(exchange_client, market_client, top_n=args.top_n)
        if not symbols:
            logger.info("update-cache: не найдено символов для обновления")
            return 0

        start_time, end_time = _fetch_period(config, args.days)
        fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time, end_time=end_time)
        logger.info(f"update-cache: обновлено symbols={len(symbols)}")
        return 0

    return _run_with_logging("update-cache", config, _inner)


def run_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("run-backtest", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
        preparer = DataPreparer(config.backtest.cache_dir)
        symbols = args.symbols or preparer.list_symbols(config.fetch.timeframe)
        if not symbols:
            logger.info("run-backtest: нет данных в кэше")
            return 0

        symbol_frames = {
            symbol: preparer.load_symbol_data(symbol, config.fetch.timeframe)
            for symbol in symbols
        }
        symbol_frames = {k: v for k, v in symbol_frames.items() if not v.empty}
        if not symbol_frames:
            logger.info("run-backtest: не удалось подготовить данные")
            return 0

        strategy = BreakoutStrategy(
            commission_rate=config.simulation.commission_rate,
            slippage=config.simulation.slippage,
            strategy_timezone=config.strategy.timezone,
            simulation_timezone=config.simulation.timezone,
        )
        runner = BacktestRunner(config.backtest.results_dir, config.backtest.results_file_name)
        results = runner.run(strategy, symbol_frames)
        summary = runner.build_summary(results)
        logger.info(
            "run-backtest: total=%s profitable=%s best_pf=%.4f",
            summary.total_combinations,
            summary.profitable_combinations,
            summary.best_pf,
        )
        return 0

    return _run_with_logging("run-backtest", config, _inner)


def make_report(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("make-report", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
        csv_path = Path(args.input) if args.input else config.backtest.results_dir / config.backtest.results_file_name
        if not csv_path.exists():
            logger.info(f"make-report: файл не найден: {csv_path}")
            return 1

        frame = pd.read_csv(csv_path)
        if frame.empty:
            logger.info("make-report: пустой файл результатов")
            return 1

        filtered = frame[(frame["trades_count"] >= 30) & (frame["profit_factor"] > 1.0)].copy()
        filtered = filtered.sort_values("profit_factor", ascending=False)

        if len(frame) != TARGET_PARAMETER_COMBINATIONS:
            logger.warning(
                "make-report: фактическое число комбинаций=%s отличается от целевого=%s",
                len(frame),
                TARGET_PARAMETER_COMBINATIONS,
            )

        summary = {
            "total_combinations": int(len(frame)),
            "profitable_combinations": int((frame["profit_factor"] > 1.0).sum()),
            "best_pf": round(float(frame["profit_factor"].max()), 4),
        }

        source = filtered if not filtered.empty else frame

        optimal_ranges = {
            "lookback": [int(source["lookback"].min()), int(source["lookback"].max())],
            "volume_multiplier": [round(float(source["volume_mult"].min()), 4), round(float(source["volume_mult"].max()), 4)],
        }

        distribution = {
            "SL": int(source["sl_count"].sum()),
            "BE": int(source["be_count"].sum()),
            "TP1_BE": int(source["tp1_be_count"].sum()),
            "TP2": int(source["tp2_count"].sum()),
        }

        report = {
            "summary": summary,
            "optimal_ranges": optimal_ranges,
            "trade_results_distribution": distribution,
        }

        output_path = Path(args.output) if args.output else config.backtest.results_dir / DEFAULT_REPORT_OUTPUT_FILE
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"make-report: сохранено {output_path}")
        return 0

    return _run_with_logging("make-report", config, _inner)


def _collect_oi_alignment_issues(frame: pd.DataFrame) -> list[dict[str, str]]:
    if "open_interest" not in frame.columns:
        return [
            {
                "issue_type": "oi_missing_column",
                "severity": "ERROR",
                "description": "Отсутствует колонка open_interest",
            }
        ]

    oi = pd.to_numeric(frame["open_interest"], errors="coerce")
    issues: list[dict[str, str]] = []

    if oi.isna().any():
        issues.append(
            {
                "issue_type": "oi_alignment_missing_values",
                "severity": "WARNING",
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
                        "issue_type": "oi_alignment_leading_gaps",
                        "severity": "WARNING",
                        "description": "Обнаружены пропуски open_interest в начале ряда",
                    }
                )

        stale_ratio = (oi.ffill().diff().fillna(0) == 0).mean()
        if stale_ratio > OI_STALE_RATIO_THRESHOLD:
            issues.append(
                {
                    "issue_type": "oi_alignment_stale_series",
                    "severity": "ERROR",
                    "description": "open_interest почти не меняется, вероятна рассинхронизация",
                }
            )

    return issues



def _build_quality_recommendations(summary: dict[str, object], symbols: dict[str, dict[str, object]]) -> list[str]:
    recommendations: list[str] = []
    if int(summary["gaps_total"]) > 0:
        recommendations.append("Дозагрузка диапазона: запустите update-cache для символов с пропусками")

    if any(int(data["gaps"]) > 0 for data in symbols.values()):
        recommendations.append("Проверка таймфрейма: убедитесь, что timeframe совпадает с кэшем")

    if int(summary["issues_total"]) > 0:
        recommendations.append("Дедупликация и очистка: переcохраните ряды с удалением дублей и аномалий")

    oi_problem_types = {
        "oi_missing_column",
        "oi_alignment_missing_values",
        "oi_alignment_leading_gaps",
        "oi_alignment_stale_series",
    }
    if any(problem in oi_problem_types for problem in summary["by_issue_type"]):
        recommendations.append("Ресинхронизация OI: перезапустите загрузку OI с выравниванием относительно OHLCV")

    if int(summary["issues_total"]) > 0 or int(summary["gaps_total"]) > 0:
        recommendations.append("Повторная валидация: после исправлений выполните check-quality повторно")

    return recommendations



def _save_quality_report(report: dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".csv":
        symbols = report["symbols"]
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["symbol", "issues", "gaps", "warning", "error", "critical", "info"])
            for symbol, data in symbols.items():
                sev = data["by_severity"]
                writer.writerow(
                    [
                        symbol,
                        data["issues"],
                        data["gaps"],
                        sev.get("WARNING", 0),
                        sev.get("ERROR", 0),
                        sev.get("CRITICAL", 0),
                        sev.get("INFO", 0),
                    ]
                )
    else:
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")



def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("check-quality", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
        preparer = DataPreparer(config.backtest.cache_dir)
        symbols = args.symbols or preparer.list_symbols(config.fetch.timeframe)
        if not symbols:
            logger.info("check-quality: нет данных для проверки")
            return 0

        validator = DataValidator()
        gap_detector = GapDetector()

        issues_by_type: Counter[str] = Counter()
        issues_by_severity: Counter[str] = Counter()
        symbols_report: dict[str, dict[str, object]] = {}

        total_issues = 0
        total_gaps = 0
        for symbol in symbols:
            frame = preparer.load_symbol_data(symbol, config.fetch.timeframe)
            if frame.empty:
                logger.info(f"check-quality: {symbol} пропущен, пустой датасет")
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

            symbols_report[symbol] = {
                "issues": symbol_total_issues,
                "gaps": len(gaps),
                "by_issue_type": dict(sorted(symbol_issue_counter.items())),
                "by_severity": dict(sorted(symbol_severity_counter.items())),
            }

            logger.info(
                f"check-quality: {symbol} issues={symbol_total_issues} gaps={len(gaps)} "
                f"oi_alignment_issues={len(oi_alignment_issues)}"
            )

        summary = {
            "symbols_checked": len(symbols_report),
            "issues_total": total_issues,
            "gaps_total": total_gaps,
            "by_issue_type": dict(sorted(issues_by_type.items())),
            "by_severity": dict(sorted(issues_by_severity.items())),
        }
        report = {
            "summary": summary,
            "symbols": symbols_report,
            "recommendations": _build_quality_recommendations(summary, symbols_report),
        }

        output_path = Path(args.output) if args.output else config.backtest.results_dir / DEFAULT_QUALITY_REPORT_OUTPUT_FILE
        _save_quality_report(report, output_path)

        logger.info(f"check-quality: итог issues={total_issues} gaps={total_gaps}")
        logger.info(f"check-quality: отчет сохранен {output_path}")
        return 0

    return _run_with_logging("check-quality", config, _inner)
