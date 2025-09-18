from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

from .data.io.config_loader import AppConfig, ConfigLoader
from .data.io.storage import Storage
from .data.providers.base import BaseExchangeProvider
from .data.providers.binance import BinanceFuturesProvider
from .data.providers.bybit import BybitPerpetualProvider
from .data.repositories.signal_repository import SignalRepository
from .data.repositories.trade_repository import TradeRepository
from .domain.enums import Timeframe
from .domain.models.entities import Thresholds
from .domain.services.backtest_runner import BacktestRunner
from .domain.services.dedup_policy import DeduplicationPolicy
from .domain.services.live_runner import LiveTradingRunner
from .domain.services.metrics_service import MetricsService
from .domain.services.selector_service import SignalSelectorService
from .domain.services.tp_sl_service import TpSlService
from .utils.clock import utcnow
from .utils.logging import configure_logging, get_logger


def load_config() -> AppConfig:
    settings_path = Path("settings.toml")
    env_path = Path(".env")
    loader = ConfigLoader(settings_path, env_path if env_path.exists() else None)
    return loader.load()


def init_storage(config: AppConfig) -> Storage:
    storage_path = Path(config.get("storage.path", "state.json"))
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    return Storage(storage_path)


def init_providers(config: AppConfig) -> Dict[str, BaseExchangeProvider]:
    providers: Dict[str, BaseExchangeProvider] = {}
    providers_cfg = config.get("providers", {})
    for name, cfg in providers_cfg.items():
        rate = int(cfg.get("rate_limit_per_minute", 60))
        min_volume = float(cfg.get("min_quote_volume", 0))
        api_base = cfg.get("api_base")
        ws_base = cfg.get("ws_base")
        if name == "binance":
            providers[name] = BinanceFuturesProvider(api_base, ws_base, rate, min_volume)
        elif name == "bybit":
            providers[name] = BybitPerpetualProvider(api_base, ws_base, rate, min_volume)
    return providers


def build_thresholds(config: AppConfig) -> Dict[str, Thresholds]:
    raw = config.get("thresholds", {})
    thresholds: Dict[str, Thresholds] = {}
    default_raw = raw.get("default", {})
    thresholds["default"] = Thresholds(**default_raw)
    for symbol, data in raw.get("symbols", {}).items():
        thresholds[symbol] = Thresholds(**data)
    return thresholds


def init_services(config: AppConfig, storage: Storage) -> tuple[
    SignalRepository,
    TradeRepository,
    MetricsService,
    SignalSelectorService,
    TpSlService,
    DeduplicationPolicy,
]:
    signal_repo = SignalRepository(storage)
    trade_repo = TradeRepository(storage)
    metrics_cfg = config.get("metrics", {})
    metrics_service = MetricsService(
        atr_period=int(metrics_cfg.get("atr_period", 14)),
        volume_period=int(metrics_cfg.get("volume_period", 20)),
        momentum_period=int(metrics_cfg.get("momentum_period", 5)),
    )
    selector = SignalSelectorService(metrics_service)
    tp_sl_cfg = config.get("tp_sl", {})
    tp_sl_service = TpSlService(risk_reward_ratio=float(tp_sl_cfg.get("risk_reward_ratio", 2.0)))
    dedup_cfg = config.get("dedup", {})
    dedup_policy = DeduplicationPolicy(
        ttl_seconds=int(dedup_cfg.get("ttl_seconds", 1800)),
        max_records=int(dedup_cfg.get("max_records", 1000)),
    )
    return signal_repo, trade_repo, metrics_service, selector, tp_sl_service, dedup_policy


def select_provider_for_symbol(
    providers: Dict[str, BaseExchangeProvider],
    symbol: str,
    config: AppConfig,
) -> BaseExchangeProvider:
    mapping = config.get("symbols.providers", {})
    name = mapping.get(symbol) or mapping.get("default") or next(iter(providers))
    return providers[name]


async def run_backtest(
    config: AppConfig,
    providers: Dict[str, BaseExchangeProvider],
    thresholds_map: Dict[str, Thresholds],
    services: tuple[
        SignalRepository,
        TradeRepository,
        MetricsService,
        SignalSelectorService,
        TpSlService,
        DeduplicationPolicy,
    ],
) -> None:
    logger = get_logger("backtest")
    signal_repo, trade_repo, _, selector, tp_sl_service, dedup_policy = services
    backtest_cfg = config.get("backtest", {})
    if not backtest_cfg.get("enabled", False):
        logger.info("Backtest disabled")
        return
    runner = BacktestRunner(selector, tp_sl_service, signal_repo, trade_repo, dedup_policy, window=int(backtest_cfg.get("window", 50)))
    timeframe = Timeframe(backtest_cfg.get("timeframe", Timeframe.M15.value))
    limit = int(backtest_cfg.get("limit", 500))
    for symbol in backtest_cfg.get("symbols", []):
        provider = select_provider_for_symbol(providers, symbol, config)
        try:
            candles = await provider.fetch_ohlcv(symbol, timeframe, limit)
        except Exception as exc:  # pragma: no cover - network errors
            logger.error("Failed to fetch backtest data for %s: %s", symbol, exc)
            continue
        thresholds = thresholds_map.get(symbol) or thresholds_map["default"]
        result = runner.run(symbol, candles, thresholds)
        logger.info("Backtest for %s produced %d trades", symbol, len(result.trades))


async def run_live(
    config: AppConfig,
    providers: Dict[str, BaseExchangeProvider],
    thresholds_map: Dict[str, Thresholds],
    services: tuple[
        SignalRepository,
        TradeRepository,
        MetricsService,
        SignalSelectorService,
        TpSlService,
        DeduplicationPolicy,
    ],
) -> None:
    logger = get_logger("live")
    signal_repo, trade_repo, _, selector, tp_sl_service, dedup_policy = services
    live_cfg = config.get("live", {})
    if not live_cfg.get("enabled", False):
        logger.info("Live trading disabled")
        return
    provider_name = live_cfg.get("provider") or next(iter(providers))
    provider = providers[provider_name]
    timeframe = Timeframe(live_cfg.get("timeframe", Timeframe.M5.value))
    window = int(live_cfg.get("window", 50))
    poll_interval = float(live_cfg.get("poll_interval", 10.0))
    runner = LiveTradingRunner(
        provider,
        selector,
        tp_sl_service,
        signal_repo,
        trade_repo,
        dedup_policy,
        window=window,
        poll_interval=poll_interval,
    )
    symbols = live_cfg.get("symbols", [])

    async def deposit_monitor() -> None:
        while True:
            try:
                balance = await provider.update_deposit()
                logger.info("Deposit update at %s: %s", utcnow().isoformat(), balance)
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning("Failed to update deposit: %s", exc)
            await asyncio.sleep(float(live_cfg.get("balance_interval", 60.0)))

    asyncio.create_task(deposit_monitor())

    thresholds_selection = {symbol: thresholds_map.get(symbol, thresholds_map["default"]) for symbol in symbols}
    await runner.run(symbols, timeframe, thresholds_selection)


async def main_async() -> None:
    config = load_config()
    configure_logging(config.get("logging.level", "INFO"))
    logger = get_logger("app")
    storage = init_storage(config)
    providers = init_providers(config)
    if not providers:
        logger.error("No providers configured")
        return
    services = init_services(config, storage)
    thresholds_map = build_thresholds(config)
    try:
        await run_backtest(config, providers, thresholds_map, services)
        await run_live(config, providers, thresholds_map, services)
    finally:
        for provider in providers.values():
            close = getattr(provider, "close", None)
            if close:
                await close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
