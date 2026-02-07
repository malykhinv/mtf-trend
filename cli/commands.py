"""CLI command handlers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import AppConfig
from data.clients.coingecko_client import CoinGeckoClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.storage.parquet_storage import ParquetStorage
from domain.enums.exchange import Exchange
from utils.logger import get_logger


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


def _resolve_symbols(exchange_client: CcxtFuturesClient, market_client: CoinGeckoClient, top_n: int = 50) -> list[str]:
    top_symbols = {f"{symbol}/USDT" for symbol in market_client.get_top_coins_by_market_cap(limit=top_n)}
    futures_symbols = set(exchange_client.get_futures_symbols())
    return sorted(top_symbols.intersection(futures_symbols))


def fetch_data(config: AppConfig) -> int:
    logger = get_logger("fetch-data")
    try:
        fetcher, exchange_client, market_client = _build_fetch_stack(config)
        symbols = _resolve_symbols(exchange_client, market_client)
        if not symbols:
            logger.info("fetch-data: не найдено символов для загрузки")
            return 0

        end_time = datetime.now(tz=timezone.utc)
        start_time = end_time - timedelta(days=60)
        fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time, end_time=end_time)
        logger.info(f"fetch-data: завершено, symbols={len(symbols)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.info(f"fetch-data ошибка: {exc}")
        return 1


def update_cache(config: AppConfig) -> int:
    logger = get_logger("update-cache")
    try:
        fetcher, exchange_client, market_client = _build_fetch_stack(config)
        symbols = _resolve_symbols(exchange_client, market_client)
        if not symbols:
            logger.info("update-cache: не найдено символов для обновления")
            return 0

        end_time = datetime.now(tz=timezone.utc)
        start_time = end_time - timedelta(days=7)
        fetcher.fetch_all(symbols=symbols, timeframe=config.fetch.timeframe, start_time=start_time, end_time=end_time)
        logger.info(f"update-cache: завершено, symbols={len(symbols)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.info(f"update-cache ошибка: {exc}")
        return 1


def run_backtest(config: AppConfig) -> int:
    _ = config
    return 0


def make_report(config: AppConfig) -> int:
    _ = config
    return 0


def check_quality(config: AppConfig) -> int:
    _ = config
    return 0
