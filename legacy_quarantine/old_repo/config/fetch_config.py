"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from constants import (
    DEFAULT_FETCH_TIMEFRAMES,
    DEFAULT_LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD,
    DEFAULT_MIN_VOLUME_USD,
)
from domain.enums.timeframe import Timeframe


@dataclass(slots=True)
class FetchConfig:
    binance_api_key: str
    binance_secret_key: str
    timeframes: tuple[Timeframe, ...] = DEFAULT_FETCH_TIMEFRAMES
    min_volume_usd: float = DEFAULT_MIN_VOLUME_USD
    liquidity_skip_error_ratio_threshold: float = DEFAULT_LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD
    anchor_timestamp_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("FetchConfig.timeframes must contain at least one timeframe")

    @property
    def timeframe(self) -> Timeframe:
        """Возвращает основной таймфрейм для обратной совместимости."""
        return self.timeframes[0]
