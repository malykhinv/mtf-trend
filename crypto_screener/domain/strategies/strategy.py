from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Setup
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class StrategyRuntimeConfig:
    capture_timeout_multiplier: int
    risk_per_trade_usdt: float
    min_position_notional_usdt: float
    min_position_quantity: float


@dataclass(frozen=True)
class StrategyVolumeConfig:
    min_side_bars: int
    high_volume_factor: float
    min_high_fraction: float


class Strategy(ABC):
    name: str

    def trim_by_volume(self, bars: list[Bar]) -> list[Bar]:
        volume_config = self.get_volume_config()
        length = len(bars)
        min_side_bars = volume_config.min_side_bars
        if length < 2 * min_side_bars:
            return []
        volumes = [bar.volume for bar in bars]
        prefix = [0.0] * (length + 1)
        for i, v in enumerate(volumes):
            prefix[i + 1] = prefix[i] + v
        best_ratio = -1.0
        best_index = -1
        high_volume_factor = volume_config.high_volume_factor
        min_high_fraction = volume_config.min_high_fraction
        for split in range(min_side_bars, length - min_side_bars + 1):
            left_avg = (prefix[split] - prefix[0]) / split
            if left_avg <= 0:
                continue
            right_avg = (prefix[length] - prefix[split]) / (length - split)
            ratio = right_avg / left_avg
            if ratio > best_ratio:
                best_ratio = ratio
                best_index = split
        if best_ratio < high_volume_factor or best_index == -1:
            return []
        left_avg = (prefix[best_index] - prefix[0]) / best_index
        right_volumes = volumes[best_index:]
        high_threshold = left_avg * high_volume_factor
        high_count = sum(1 for v in right_volumes if v >= high_threshold)
        if (high_count / len(right_volumes)) < min_high_fraction:
            return []
        right_count = len(bars) - best_index
        start_index = max(0, best_index - right_count)
        return bars[start_index:]

    @abstractmethod
    def detect_setup(
            self,
            symbol: str,
            bars: list[Bar],
            timeframe: Timeframe,
            context: Context,
    ) -> Setup:
        """Определяет сетап для заданного символа."""

    @abstractmethod
    def get_runtime_config(self) -> StrategyRuntimeConfig:
        """Возвращает параметры исполнения стратегии."""

    @abstractmethod
    def get_volume_config(self) -> StrategyVolumeConfig:
        """Возвращает параметры для анализа объемов."""
