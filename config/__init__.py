"""Application configuration dataclasses and .env loader."""

from __future__ import annotations

import os
from pathlib import Path

from config.app_config import AppConfig
from config.backtest_config import BacktestConfig
from config.fetch_config import FetchConfig
from config.simulation_config import SimulationConfig
from config.strategy_config import StrategyConfig
from domain.enums.timeframe import Timeframe
from constants import (
    DEFAULT_BACKTEST_OUTPUT_FILE,
    DEFAULT_CACHE_DIR,
    DEFAULT_COMMISSION_RATE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_RESULTS_DIR,
    DEFAULT_SLIPPAGE,
    DEFAULT_TIMEZONE,
    DEFAULT_SPREAD,
    DEFAULT_LOGS_DIR,
    DEFAULT_MIN_VOLUME_USD,
)

__all__ = [
    "AppConfig",
    "BacktestConfig",
    "FetchConfig",
    "SimulationConfig",
    "StrategyConfig",
    "load_config",
]


# область Приватные

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


def _parse_timeframe(value: str, *, env_name: str) -> Timeframe:
    normalized = value.strip().lower()
    for timeframe in Timeframe:
        if timeframe.value == normalized:
            return timeframe
    supported = ", ".join(tf.value for tf in Timeframe)
    raise ValueError(f"Invalid {env_name}: {value}. Supported values: {supported}")

# конец области Приватные

def load_config(env_path: str | Path = ".env") -> AppConfig:
    env_file = Path(env_path)
    _load_env_file(env_file)

    default_timezone = os.getenv("TIMEZONE", DEFAULT_TIMEZONE)

    fetch_timezone = os.getenv("FETCH_TIMEZONE", default_timezone)
    strategy_timezone = os.getenv("STRATEGY_TIMEZONE", default_timezone)
    simulation_timezone = os.getenv("SIMULATION_TIMEZONE", default_timezone)

    fetch_config = FetchConfig(
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_secret_key=os.getenv("BINANCE_SECRET_KEY", ""),
        coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
        timezone=fetch_timezone,
        min_volume_usd=float(os.getenv("MIN_VOLUME_USD", os.getenv("FETCH_MIN_VOLUME_USD", str(DEFAULT_MIN_VOLUME_USD)))),
    )

    strategy_levels_timeframe = _parse_timeframe(
        os.getenv("LEVELS_TIMEFRAME", Timeframe.D1.value),
        env_name="LEVELS_TIMEFRAME",
    )
    strategy_entry_timeframe = _parse_timeframe(
        os.getenv("ENTRY_TIMEFRAME", Timeframe.M15.value),
        env_name="ENTRY_TIMEFRAME",
    )

    strategy_config = StrategyConfig(
        timezone=strategy_timezone,
        levels_timeframe=strategy_levels_timeframe,
        entry_timeframe=strategy_entry_timeframe,
    )

    simulation_config = SimulationConfig(
        commission_rate=float(os.getenv("COMMISSION_RATE", str(DEFAULT_COMMISSION_RATE))),
        slippage=float(os.getenv("SLIPPAGE", str(DEFAULT_SLIPPAGE))),
        spread=float(os.getenv("SPREAD", str(DEFAULT_SPREAD))),
        timezone=simulation_timezone,
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

    for path in (
        backtest_config.cache_dir,
        backtest_config.logs_dir,
        backtest_config.results_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        fetch=fetch_config,
        strategy=strategy_config,
        simulation=simulation_config,
        backtest=backtest_config,
    )
