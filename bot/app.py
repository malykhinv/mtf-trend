from __future__ import annotations

import asyncio
import asyncio
import contextlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VALID_MODES = {"backtest", "live"}

from .data.io.config_loader import AppConfig, ConfigLoader
from .data.io.storage import Storage
from .data.providers.base import BaseExchangeProvider
from .data.providers.binance import BinanceFuturesProvider
from .data.providers.bybit import BybitPerpetualProvider
from .data.repositories.signal_repository import SignalRepository
from .data.repositories.state_repository import StateRepository
from .data.repositories.trade_repository import TradeRepository
from .domain.enums import Timeframe
from .domain.models.entities import Candle, Thresholds
from .domain.services.backtest_runner import BacktestRunner
from .domain.services.dedup_policy import DeduplicationPolicy
from .domain.services.live_runner import LiveTradingRunner
from .domain.services.metrics_service import MetricsService
from .domain.services.selector_service import SignalSelectorService
from .domain.services.tp_sl_service import TpSlService
from .utils.clock import init_clock, utcnow
from .utils.logging import configure_logging, get_logger


@dataclass(frozen=True)
class SymbolUniverse:
    assignments: Dict[str, str]
    pipelines: Dict[str, tuple[str, ...]]

    def provider_for(self, symbol: str) -> str | None:
        return self.assignments.get(symbol)

    def symbols(self) -> tuple[str, ...]:
        return tuple(self.assignments.keys())

    def symbols_for_provider(self, provider: str) -> tuple[str, ...]:
        return self.pipelines.get(provider, tuple())


def load_config() -> AppConfig:
    settings_path = Path("settings.toml")
    env_path = Path(".env")
    loader = ConfigLoader(settings_path, env_path if env_path.exists() else None)
    return loader.load()


def init_storage(config: AppConfig) -> Storage:
    path_setting = config.get("storage.path", "var/state.xlsx")
    storage_path = Path(path_setting)
    if storage_path.suffix.lower() != ".xlsx":
        storage_path = storage_path.with_suffix(".xlsx")
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
        env_prefix = name.upper()

        def _resolve_secret(key: str, env_suffix: str) -> str | None:
            value = cfg.get(key)
            if value:
                return str(value)
            env_key = cfg.get(f"{key}_env") or f"{env_prefix}_{env_suffix}"
            return config.get(f"env.{env_key}") or os.getenv(env_key)

        api_key = _resolve_secret("api_key", "API_KEY")
        api_secret = _resolve_secret("api_secret", "API_SECRET")
        if name == "binance":
            providers[name] = BinanceFuturesProvider(
                api_base,
                ws_base,
                rate,
                min_volume,
                api_key=api_key,
                api_secret=api_secret,
            )
        elif name == "bybit":
            providers[name] = BybitPerpetualProvider(
                api_base,
                ws_base,
                rate,
                min_volume,
                api_key=api_key,
                api_secret=api_secret,
            )
    return providers


def build_thresholds(config: AppConfig) -> Dict[str, Thresholds]:
    thresholds_cfg = config.thresholds
    thresholds: Dict[str, Thresholds] = {"default": thresholds_cfg.default.to_domain()}
    for symbol, cfg in thresholds_cfg.symbols.items():
        thresholds[symbol] = cfg.to_domain()
    return thresholds


def init_services(config: AppConfig, storage: Storage) -> tuple[
    SignalRepository,
    TradeRepository,
    StateRepository,
    MetricsService,
    SignalSelectorService,
    TpSlService,
    DeduplicationPolicy,
]:
    signal_repo = SignalRepository(storage)
    trade_repo = TradeRepository(storage)
    state_repo = StateRepository(storage)
    metrics_cfg = config.get("metrics", {})
    metrics_service = MetricsService(
        atr_period=int(metrics_cfg.get("atr_period", 14)),
        volume_period=int(metrics_cfg.get("volume_period", 20)),
        momentum_period=int(metrics_cfg.get("momentum_period", 5)),
    )
    selector = SignalSelectorService(metrics_service)
    tp_sl_service = TpSlService()
    dedup_cfg = config.get("dedup", {})
    dedup_policy = DeduplicationPolicy(
        ttl_seconds=int(dedup_cfg.get("ttl_seconds", 14400)),
        max_records=int(dedup_cfg.get("max_records", 1000)),
    )
    return (
        signal_repo,
        trade_repo,
        state_repo,
        metrics_service,
        selector,
        tp_sl_service,
        dedup_policy,
    )


def _resolve_provider_name(
    symbol: str,
    config: AppConfig,
    providers: Dict[str, BaseExchangeProvider],
    default: str | None = None,
) -> str:
    mapping = config.symbol_provider_mapping
    symbol_key = symbol.upper()
    provider_name = (
        mapping.get(symbol_key)
        or mapping.get(symbol)
        or mapping.get("default")
    )
    if not provider_name and default:
        provider_name = default
    if not provider_name:
        provider_name = next(iter(providers))
    if provider_name not in providers:
        provider_name = next(iter(providers))
    return provider_name


async def discover_symbol_universe(
    config: AppConfig,
    providers: Dict[str, BaseExchangeProvider],
    mode: str,
    provider_scope: Iterable[str] | None = None,
) -> SymbolUniverse:
    logger = get_logger("symbol-discovery")
    selection_cfg = config.symbol_selection
    suffix = selection_cfg.quote_suffix
    min_quote_volume = selection_cfg.min_quote_volume
    allow: set[str] = set(selection_cfg.allow)
    deny: set[str] = set(selection_cfg.deny)
    overrides = config.selection_overrides_for(mode)
    allow.update(overrides.allow)
    allow.update(overrides.symbols)
    deny.update(overrides.deny)

    provider_names = list(provider_scope or providers.keys())
    discovered: Dict[str, str] = {}
    volumes: Dict[str, float] = {}

    for provider_name in provider_names:
        provider = providers.get(provider_name)
        if not provider:
            continue
        try:
            stats = await provider.get_24h_quote_volume()
        except NotImplementedError:
            logger.debug("Provider %s does not expose 24h statistics", provider_name)
            continue
        except Exception as exc:  # pragma: no cover - network errors
            logger.warning("Failed to fetch 24h statistics from %s: %s", provider_name, exc)
            continue
        for symbol, volume in stats.items():
            normalized = symbol.upper()
            if suffix and not normalized.endswith(suffix):
                continue
            if normalized in deny:
                continue
            volume_value = float(volume)
            if volume_value < min_quote_volume:
                continue
            existing_volume = volumes.get(normalized)
            if existing_volume is None or volume_value > existing_volume:
                volumes[normalized] = volume_value
                discovered[normalized] = provider_name

    default_provider = provider_names[0] if len(provider_names) == 1 else None
    for symbol in allow:
        if symbol in deny:
            continue
        if symbol not in discovered:
            provider_name = _resolve_provider_name(symbol, config, providers, default_provider)
            if provider_name in providers:
                discovered[symbol] = provider_name

    for symbol in list(discovered):
        if symbol in deny:
            discovered.pop(symbol, None)

    assignments = dict(sorted(discovered.items()))
    pipelines: Dict[str, tuple[str, ...]] = {}
    for symbol, provider_name in assignments.items():
        existing = set(pipelines.get(provider_name, ()))
        existing.add(symbol)
        pipelines[provider_name] = tuple(sorted(existing))

    return SymbolUniverse(assignments=assignments, pipelines=pipelines)


def select_provider_for_symbol(
    providers: Dict[str, BaseExchangeProvider],
    symbol: str,
    config: AppConfig,
    discovered: Mapping[str, str] | None = None,
) -> BaseExchangeProvider:
    if discovered and symbol in discovered:
        provider_name = discovered[symbol]
    else:
        provider_name = _resolve_provider_name(symbol, config, providers)
    return providers[provider_name]


async def run_backtest(
    config: AppConfig,
    providers: Dict[str, BaseExchangeProvider],
    thresholds_map: Dict[str, Thresholds],
    services: tuple[
        SignalRepository,
        TradeRepository,
        StateRepository,
        MetricsService,
        SignalSelectorService,
        TpSlService,
        DeduplicationPolicy,
    ],
) -> None:
    logger = get_logger("backtest")
    signal_repo, trade_repo, state_repo, _, selector, tp_sl_service, dedup_policy = services
    backtest_cfg = config.backtest
    if not backtest_cfg.enabled:
        logger.info("Backtest disabled")
        return
    runner = BacktestRunner(
        selector,
        tp_sl_service,
        signal_repo,
        state_repo,
        trade_repo,
        dedup_policy,
        window=backtest_cfg.window,
    )
    timeframes = backtest_cfg.timeframes
    requested_limit = backtest_cfg.limit
    universe = await discover_symbol_universe(config, providers, "backtest")
    if not universe.assignments:
        logger.warning("No symbols available for backtest after applying liquidity filters")
        return
    for symbol in universe.assignments:
        provider = select_provider_for_symbol(
            providers, symbol, config, universe.assignments
        )
        for timeframe in timeframes:
            try:
                limit = provider.resolve_ohlcv_limit(requested_limit)
                history_batches = backtest_cfg.history_batches
                timeframe_delta = timeframe.to_timedelta()
                timeframe_ms = int(timeframe_delta.total_seconds() * 1000)
                total_candles = limit * history_batches
                start_dt = utcnow() - timeframe_delta * total_candles
                since_ms = int(start_dt.timestamp() * 1000)
                candles: list[Candle] = []
                seen: set[tuple[str | None, int]] = set()
                for _ in range(history_batches):
                    batch = await provider.fetch_ohlcv(
                        symbol, timeframe, limit, since=since_ms
                    )
                    if batch:
                        for candle in batch:
                            key = (
                                candle.id,
                                int(candle.started_at.timestamp() * 1000),
                            )
                            if key in seen:
                                continue
                            seen.add(key)
                            candles.append(candle)
                        last_candle = max(batch, key=lambda c: c.started_at)
                        since_ms = int(
                            (last_candle.started_at + timeframe_delta).timestamp()
                            * 1000
                        )
                    else:
                        since_ms += timeframe_ms * limit
                candles.sort(key=lambda candle: candle.started_at)
            except Exception as exc:  # pragma: no cover - network errors
                logger.error(
                    "Failed to fetch backtest data for %s (%s): %s",
                    symbol,
                    timeframe.value,
                    exc,
                )
                continue
            thresholds = thresholds_map.get(symbol) or thresholds_map["default"]
            result = await runner.run(symbol, candles, thresholds, provider, timeframe=timeframe)
            logger.info(
                "Backtest for %s (%s) produced %d trades",
                symbol,
                result.timeframe.value if result.timeframe else timeframe.value,
                len(result.trades),
            )


async def run_live(
    config: AppConfig,
    providers: Dict[str, BaseExchangeProvider],
    thresholds_map: Dict[str, Thresholds],
    services: tuple[
        SignalRepository,
        TradeRepository,
        StateRepository,
        MetricsService,
        SignalSelectorService,
        TpSlService,
        DeduplicationPolicy,
    ],
) -> None:
    logger = get_logger("live")
    signal_repo, trade_repo, state_repo, _, selector, tp_sl_service, dedup_policy = services
    live_cfg = config.live
    if not live_cfg.enabled:
        logger.info("Live trading disabled")
        return
    provider_names: list[str] = list(live_cfg.providers) or list(providers.keys())

    invalid = [name for name in provider_names if name not in providers]
    if invalid:
        logger.warning("Unknown providers configured for live mode: %s", ", ".join(invalid))
    provider_names = [name for name in provider_names if name in providers]
    if not provider_names:
        logger.warning("No valid providers configured for live trading")
        return

    timeframe = live_cfg.timeframe
    window = live_cfg.window
    universe = await discover_symbol_universe(
        config,
        providers,
        "live",
        provider_scope=provider_names,
    )
    pipelines = {
        name: list(sorted(universe.symbols_for_provider(name)))
        for name in provider_names
        if universe.symbols_for_provider(name)
    }
    if not pipelines:
        logger.warning("No symbols available for live trading after applying liquidity filters")
        return

    async def run_pipeline(provider_name: str, symbols: list[str]) -> None:
        provider = providers[provider_name]
        scoped_state = state_repo.derive(provider_name)
        runner = LiveTradingRunner(
            provider,
            selector,
            tp_sl_service,
            signal_repo,
            scoped_state,
            trade_repo,
            dedup_policy,
            window=window,
        )

        await runner.refresh_deposit(force=True)

        async def deposit_monitor() -> None:
            while True:
                try:
                    snapshot = await runner.refresh_deposit(force=True)
                    logger.info(
                        "[%s] Deposit update at %s: %s",
                        provider_name,
                        utcnow().isoformat(),
                        snapshot,
                    )
                except Exception as exc:  # pragma: no cover - network errors
                    logger.warning("[%s] Failed to update deposit: %s", provider_name, exc)
                await asyncio.sleep(3600.0)

        monitor_task = asyncio.create_task(deposit_monitor())
        thresholds_selection = {
            symbol: thresholds_map.get(symbol, thresholds_map["default"])
            for symbol in symbols
        }
        try:
            await runner.run(symbols, timeframe, thresholds_selection)
        finally:
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task

    tasks = [asyncio.create_task(run_pipeline(name, symbols)) for name, symbols in pipelines.items()]
    await asyncio.gather(*tasks)


def _normalize_mode(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            return normalized
    return None


def resolve_mode(config: AppConfig, cli_args: Sequence[str] | None = None) -> str:
    cli_args = tuple(cli_args or ())
    cli_mode: str | None = None
    for index, arg in enumerate(cli_args):
        if arg in {"--mode", "-m"}:
            try:
                candidate = cli_args[index + 1]
            except IndexError as exc:  # pragma: no cover - defensive
                raise ValueError("Missing value for --mode flag") from exc
            cli_mode = candidate
            break
        if arg.startswith("--mode="):
            cli_mode = arg.split("=", 1)[1]
            break
    mode = _normalize_mode(cli_mode) or _normalize_mode(config.get("mode")) or "backtest"
    if mode not in VALID_MODES:
        raise ValueError(
            f"Unsupported mode '{mode}'. Expected one of: {', '.join(sorted(VALID_MODES))}"
        )
    return mode


async def main_async(cli_args: Sequence[str] | None = None) -> None:
    config = load_config()
    timezone_name = config.timezone_name
    timezone_warning: str | None = None
    try:
        app_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        app_timezone = ZoneInfo("UTC")
        timezone_warning = (
            f"Invalid timezone '{timezone_name}', defaulting to UTC"
            if timezone_name.upper() != "UTC"
            else ""
        )
    init_clock(app_timezone)
    configure_logging(config.get("logging.level", "INFO"))
    logger = get_logger("app")
    if timezone_warning:
        logger.warning(timezone_warning)
    mode = resolve_mode(config, cli_args)
    storage = init_storage(config)
    providers = init_providers(config)
    if not providers:
        logger.error("No providers configured")
        return
    services = init_services(config, storage)
    thresholds_map = build_thresholds(config)
    try:
        if mode == "backtest":
            await run_backtest(config, providers, thresholds_map, services)
        elif mode == "live":
            await run_live(config, providers, thresholds_map, services)
    finally:
        for provider in providers.values():
            await provider.close()


def main(argv: Sequence[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    asyncio.run(main_async(argv))


if __name__ == "__main__":
    main()
