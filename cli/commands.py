"""CLI command handlers."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from config import AppConfig
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
from utils.logger import get_logger
from vectorbt_runner.backtest_runner import BacktestRunner
from vectorbt_runner.data_preparer import DataPreparer


def _run_with_logging(command_name: str, body: Callable[[], int]) -> int:
    logger = get_logger(command_name)
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
    )
    oi_fetcher = OiFetcher(
        exchange_client=exchange_client,
        storage=storage,
        max_workers=config.fetch.max_concurrent_requests,
    )
    market_client = CoinGeckoClient(api_key=config.fetch.coingecko_api_key)
    return (
        MarketDataFetcher(
            ohlcv_fetcher=ohlcv_fetcher,
            oi_fetcher=oi_fetcher,
            market_data_client=market_client,
            max_workers=config.fetch.max_concurrent_requests,
        ),
        exchange_client,
        market_client,
    )


def _resolve_symbols(exchange_client: CcxtFuturesClient, market_client: CoinGeckoClient, top_n: int) -> list[str]:
    top_symbols = {f"{symbol}/USDT" for symbol in market_client.get_top_coins_by_market_cap(limit=top_n)}
    futures_symbols = set(exchange_client.get_futures_symbols())
    return sorted(top_symbols.intersection(futures_symbols))


def fetch_data(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("fetch-data")
        fetcher, exchange_client, market_client = _build_fetch_stack(config)
        symbols = _resolve_symbols(exchange_client, market_client, top_n=args.top_n)
        if not symbols:
            logger.info("fetch-data: не найдено символов для загрузки")
            return 0

        end_time = datetime.now(tz=timezone.utc)
        start_time = end_time - timedelta(days=args.days)
        fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time, end_time=end_time)
        logger.info(f"fetch-data: загружено symbols={len(symbols)}")
        return 0

    return _run_with_logging("fetch-data", _inner)


def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("update-cache")
        fetcher, exchange_client, market_client = _build_fetch_stack(config)
        symbols = _resolve_symbols(exchange_client, market_client, top_n=args.top_n)
        if not symbols:
            logger.info("update-cache: не найдено символов для обновления")
            return 0

        end_time = datetime.now(tz=timezone.utc)
        start_time = end_time - timedelta(days=args.days)
        fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time, end_time=end_time)
        logger.info(f"update-cache: обновлено symbols={len(symbols)}")
        return 0

    return _run_with_logging("update-cache", _inner)


def run_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("run-backtest")
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

    return _run_with_logging("run-backtest", _inner)


def make_report(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("make-report")
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

        output_path = Path(args.output) if args.output else config.backtest.results_dir / "report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"make-report: сохранено {output_path}")
        return 0

    return _run_with_logging("make-report", _inner)


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    def _inner() -> int:
        logger = get_logger("check-quality")
        preparer = DataPreparer(config.backtest.cache_dir)
        symbols = args.symbols or preparer.list_symbols(config.fetch.timeframe)
        if not symbols:
            logger.info("check-quality: нет данных для проверки")
            return 0

        validator = DataValidator()
        gap_detector = GapDetector()

        total_issues = 0
        total_gaps = 0
        for symbol in symbols:
            frame = preparer.load_symbol_data(symbol, config.fetch.timeframe)
            if frame.empty:
                continue
            issues = validator.validate(symbol, config.fetch.timeframe, frame)
            gaps = gap_detector.detect_gaps(frame, config.fetch.timeframe)
            total_issues += len(issues)
            total_gaps += len(gaps)
            logger.info(f"check-quality: {symbol} issues={len(issues)} gaps={len(gaps)}")

        logger.info(f"check-quality: итог issues={total_issues} gaps={total_gaps}")
        return 0

    return _run_with_logging("check-quality", _inner)
