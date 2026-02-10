"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo

from constants import DEFAULT_MIN_VOLUME_USD, DEFAULT_TIMEFRAME, DEFAULT_TIMEZONE
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

    @property
    def tzinfo(self) -> tzinfo:
        """Возвращает объект часового пояса для загрузки данных."""
        return resolve_timezone(self.timezone)
