"""Backtest runner configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from constants import (
    DEFAULT_BACKTEST_OUTPUT_FILE,
    DEFAULT_CACHE_DIR,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_DIR,
    DEFAULT_RESULTS_DIR,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
)


@dataclass(slots=True)
class BacktestConfig:
    log_level: str = DEFAULT_LOG_LEVEL
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    logs_dir: Path = Path(DEFAULT_LOGS_DIR)
    results_dir: Path = Path(DEFAULT_RESULTS_DIR)
    results_file_name: str = DEFAULT_BACKTEST_OUTPUT_FILE
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
