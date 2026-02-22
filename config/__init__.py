"""Модуль проекта."""

from __future__ import annotations

import os
from pathlib import Path

from config.app_config import AppConfig
from config.backtest_config import BacktestConfig
from config.fetch_config import FetchConfig
from config.simulation_config import SimulationConfig
from config.strategy_config import StrategyConfig
from constants import (
    DEFAULT_BACKTEST_OUTPUT_FILE,
    DEFAULT_CACHE_DIR,
    DEFAULT_COMMISSION_RATE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_RESULTS_DIR,
    DEFAULT_SLIPPAGE,
    DEFAULT_SPREAD,
    DEFAULT_LOGS_DIR,
    DEFAULT_MIN_VOLUME_USD,
    DEFAULT_COINGECKO_MIN_REQUEST_INTERVAL_SECONDS,
    DEFAULT_COINGECKO_VOLUME_BATCH_SIZE,
    DEFAULT_LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD,
)
from domain.enums.timeframe import Timeframe
from strategy.bee_bite.config import (
    get_bee_bite_runtime,
    parse_bee_bite_grid_mode,
    parse_bee_bite_profile_id,
    parse_bee_bite_reclaim_mode,
    parse_bee_bite_retest_mode,
)

__all__ = [
    "AppConfig",
    "BacktestConfig",
    "FetchConfig",
    "SimulationConfig",
    "StrategyConfig",
    "load_config",
]

SUPPORTED_STRATEGY_IDS = {"retest", "breakout", "bee_bite"}


# region Приватные

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


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_strategy_id(value: str | None, *, default: str = "retest") -> str:
    strategy_id = (value or default).strip().lower()
    if strategy_id == "breakout":
        strategy_id = "retest"
    if strategy_id not in SUPPORTED_STRATEGY_IDS:
        supported = ", ".join(sorted(SUPPORTED_STRATEGY_IDS))
        raise ValueError(f"Invalid STRATEGY_ID: {strategy_id}. Supported values: {supported}")
    return strategy_id


def _parse_anchor_timestamp_ms(value: str | None, *, env_name: str) -> int | None:
    if value is None:
        return None

    raw_value = value.strip()
    if not raw_value:
        return None

    try:
        parsed = int(raw_value)
    except ValueError as error:
        raise ValueError(
            f"Invalid {env_name}: {value}. Expected unix timestamp in milliseconds, for example 1735689599000"
        ) from error

    if parsed < 0:
        raise ValueError(f"Invalid {env_name}: {value}. Timestamp must be non-negative.")

    return parsed


# endregion Приватные

def load_config(env_path: str | Path = ".env") -> AppConfig:
    """Загружает конфигурацию приложения из файла."""
    env_file = Path(env_path)
    _load_env_file(env_file)


    fetch_config = FetchConfig(
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_secret_key=os.getenv("BINANCE_SECRET_KEY", ""),
        coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
        min_volume_usd=float(
            os.getenv("MIN_VOLUME_USD", os.getenv("FETCH_MIN_VOLUME_USD", str(DEFAULT_MIN_VOLUME_USD)))),
        coingecko_min_request_interval_seconds=float(
            os.getenv(
                "COINGECKO_MIN_REQUEST_INTERVAL_SECONDS",
                str(DEFAULT_COINGECKO_MIN_REQUEST_INTERVAL_SECONDS),
            )
        ),
        coingecko_volume_batch_size=max(
            1,
            int(os.getenv("COINGECKO_VOLUME_BATCH_SIZE", str(DEFAULT_COINGECKO_VOLUME_BATCH_SIZE))),
        ),
        liquidity_skip_error_ratio_threshold=float(
            os.getenv(
                "LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD",
                str(DEFAULT_LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD),
            )
        ),
        ignore_coingecko=_parse_bool(os.getenv("IGNORE_COINGECKO"), default=False),
        anchor_timestamp_ms=_parse_anchor_timestamp_ms(
            os.getenv("FETCH_ANCHOR_TIMESTAMP_MS"),
            env_name="FETCH_ANCHOR_TIMESTAMP_MS",
        ),
    )

    strategy_levels_timeframe = _parse_timeframe(
        os.getenv("LEVELS_TIMEFRAME", Timeframe.D1.value),
        env_name="LEVELS_TIMEFRAME",
    )
    strategy_entry_timeframe = _parse_timeframe(
        os.getenv("ENTRY_TIMEFRAME", Timeframe.M15.value),
        env_name="ENTRY_TIMEFRAME",
    )

    bee_bite_profile = parse_bee_bite_profile_id(os.getenv("BEE_BITE_PROFILE"))
    bee_bite_runtime = get_bee_bite_runtime(bee_bite_profile)
    strategy_config = StrategyConfig(
        strategy_id=_parse_strategy_id(os.getenv("STRATEGY_ID")),
        levels_timeframe=strategy_levels_timeframe,
        entry_timeframe=strategy_entry_timeframe,
        bee_bite_profile=bee_bite_profile,
        bee_bite_grid_mode=parse_bee_bite_grid_mode(os.getenv("BEE_BITE_GRID_MODE")),
        bee_bite_reclaim_mode=parse_bee_bite_reclaim_mode(
            os.getenv("BEE_BITE_RECLAIM_MODE"),
            default=bee_bite_runtime.reclaim_mode,
        ),
        bee_bite_retest_mode=parse_bee_bite_retest_mode(
            os.getenv("BEE_BITE_RETEST_MODE"),
            default=bee_bite_runtime.retest_mode,
        ),
        bee_bite_cooldown_bars=int(os.getenv("BEE_BITE_COOLDOWN_BARS", str(bee_bite_runtime.cooldown_hours))),
        bee_bite_max_age_range=int(os.getenv("BEE_BITE_MAX_AGE_RANGE", str(bee_bite_runtime.max_age_range_hours))),
    )

    simulation_config = SimulationConfig(
        commission_rate=float(os.getenv("COMMISSION_RATE", str(DEFAULT_COMMISSION_RATE))),
        slippage=float(os.getenv("SLIPPAGE", str(DEFAULT_SLIPPAGE))),
        spread=float(os.getenv("SPREAD", str(DEFAULT_SPREAD))),
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
