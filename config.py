"""Application configuration dataclasses and .env loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from constants import (
    DEFAULT_BACKTEST_OUTPUT_FILE,
    DEFAULT_CACHE_DIR,
    DEFAULT_COMMISSION_RATE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_RESULTS_DIR,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_SLIPPAGE,
    DEFAULT_SPREAD,
    DEFAULT_TIMEFRAME,
    DEFAULT_TIMEZONE,
)
from domain.enums.timeframe import Timeframe


@dataclass(slots=True)
class FetchConfig:
    binance_api_key: str
    binance_secret_key: str
    coingecko_api_key: str
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS
    timeframe: Timeframe = DEFAULT_TIMEFRAME
    timezone: str = DEFAULT_TIMEZONE


@dataclass(slots=True)
class StrategyConfig:
    timezone: str = DEFAULT_TIMEZONE
    default_timeframe: Timeframe = DEFAULT_TIMEFRAME


@dataclass(slots=True)
class SimulationConfig:
    commission_rate: float = DEFAULT_COMMISSION_RATE
    slippage: float = DEFAULT_SLIPPAGE
    spread: float = DEFAULT_SPREAD
    timezone: str = DEFAULT_TIMEZONE


@dataclass(slots=True)
class BacktestConfig:
    log_level: str = DEFAULT_LOG_LEVEL
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    logs_dir: Path = Path(DEFAULT_LOGS_DIR)
    results_dir: Path = Path(DEFAULT_RESULTS_DIR)
    results_file_name: str = DEFAULT_BACKTEST_OUTPUT_FILE
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS


@dataclass(slots=True)
class AppConfig:
    fetch: FetchConfig
    strategy: StrategyConfig
    simulation: SimulationConfig
    backtest: BacktestConfig


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _parse_timeframe(raw: str | None, *, default: Timeframe) -> Timeframe:
    if not raw:
        return default
    normalized = raw.strip().upper()
    return Timeframe(normalized)


def load_config(env_path: str | Path = ".env") -> AppConfig:
    env_file = Path(env_path)
    _load_env_file(env_file)

    timezone = os.getenv("TIMEZONE", DEFAULT_TIMEZONE)

    timeframe = _parse_timeframe(os.getenv("TIMEFRAME"), default=DEFAULT_TIMEFRAME)

    fetch_config = FetchConfig(
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_secret_key=os.getenv("BINANCE_SECRET_KEY", ""),
        coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
        max_concurrent_requests=int(
            os.getenv("MAX_CONCURRENT_REQUESTS", str(DEFAULT_MAX_CONCURRENT_REQUESTS))
        ),
        timeframe=timeframe,
        timezone=timezone,
    )

    strategy_config = StrategyConfig(
        timezone=timezone,
        default_timeframe=_parse_timeframe(os.getenv("STRATEGY_TIMEFRAME"), default=timeframe),
    )

    simulation_config = SimulationConfig(
        commission_rate=float(os.getenv("COMMISSION_RATE", str(DEFAULT_COMMISSION_RATE))),
        slippage=float(os.getenv("SLIPPAGE", str(DEFAULT_SLIPPAGE))),
        spread=float(os.getenv("SPREAD", str(DEFAULT_SPREAD))),
        timezone=timezone,
    )

    backtest_config = BacktestConfig(
        log_level=os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        cache_dir=Path(os.getenv("CACHE_DIR", DEFAULT_CACHE_DIR)),
        logs_dir=Path(os.getenv("LOGS_DIR", DEFAULT_LOGS_DIR)),
        results_dir=Path(os.getenv("RESULTS_DIR", DEFAULT_RESULTS_DIR)),
        results_file_name=os.getenv("RESULTS_FILE_NAME", DEFAULT_BACKTEST_OUTPUT_FILE),
        retry_attempts=int(os.getenv("RETRY_ATTEMPTS", str(DEFAULT_RETRY_ATTEMPTS))),
        retry_backoff_seconds=float(
            os.getenv("RETRY_BACKOFF_SECONDS", str(DEFAULT_RETRY_BACKOFF_SECONDS))
        ),
    )

    for path in (backtest_config.cache_dir, backtest_config.logs_dir, backtest_config.results_dir):
        path.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        fetch=fetch_config,
        strategy=strategy_config,
        simulation=simulation_config,
        backtest=backtest_config,
    )
