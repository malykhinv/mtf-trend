"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo

from constants import (
    DEFAULT_COINGECKO_MIN_REQUEST_INTERVAL_SECONDS,
    DEFAULT_COINGECKO_VOLUME_BATCH_SIZE,
    DEFAULT_MIN_VOLUME_USD,
    DEFAULT_TIMEFRAME,
    DEFAULT_TIMEZONE,
)
from domain.enums.timeframe import Timeframe
from utils.formatters import resolve_timezone


@dataclass(slots=True)
class FetchConfig:
    binance_api_key: str
    binance_secret_key: str
    coingecko_api_key: str
    timeframe: Timeframe = DEFAULT_TIMEFRAME
    timezone: str = DEFAULT_TIMEZONE
    min_volume_usd: float = DEFAULT_MIN_VOLUME_USD
    coingecko_min_request_interval_seconds: float = DEFAULT_COINGECKO_MIN_REQUEST_INTERVAL_SECONDS
    coingecko_volume_batch_size: int = DEFAULT_COINGECKO_VOLUME_BATCH_SIZE
    ignore_coingecko: bool = False

    @property
    def tzinfo(self) -> tzinfo:
        """Возвращает объект часового пояса для загрузки данных."""
        return resolve_timezone(self.timezone)
