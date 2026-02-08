"""Fetch layer configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo

from constants import DEFAULT_TIMEFRAME, DEFAULT_TIMEZONE
from domain.enums.timeframe import Timeframe
from utils.formatters import resolve_timezone


@dataclass(slots=True)
class FetchConfig:
    binance_api_key: str
    binance_secret_key: str
    coingecko_api_key: str
    timeframe: Timeframe = DEFAULT_TIMEFRAME
    timezone: str = DEFAULT_TIMEZONE

    @property
    def tzinfo(self) -> tzinfo:
        return resolve_timezone(self.timezone)
