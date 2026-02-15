"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from constants import (
    DEFAULT_COINGECKO_MIN_REQUEST_INTERVAL_SECONDS,
    DEFAULT_COINGECKO_VOLUME_BATCH_SIZE,
    DEFAULT_FETCH_TIMEFRAMES,
    DEFAULT_LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD,
    DEFAULT_MIN_VOLUME_USD,
    DEFAULT_TIMEZONE,
)
from domain.enums.timeframe import Timeframe


@dataclass(slots=True)
class FetchConfig:
    binance_api_key: str
    binance_secret_key: str
    coingecko_api_key: str
    timeframes: tuple[Timeframe, ...] = DEFAULT_FETCH_TIMEFRAMES
    timezone: str = DEFAULT_TIMEZONE
    min_volume_usd: float = DEFAULT_MIN_VOLUME_USD
    coingecko_min_request_interval_seconds: float = DEFAULT_COINGECKO_MIN_REQUEST_INTERVAL_SECONDS
    coingecko_volume_batch_size: int = DEFAULT_COINGECKO_VOLUME_BATCH_SIZE
    liquidity_skip_error_ratio_threshold: float = DEFAULT_LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD
    ignore_coingecko: bool = True
    anchor_datetime: datetime | None = None

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("FetchConfig.timeframes must contain at least one timeframe")

    @property
    def timeframe(self) -> Timeframe:
        """Возвращает основной таймфрейм для обратной совместимости."""
        return self.timeframes[0]
