from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence

from ..models.entities import Candle, Thresholds
from ...utils import math_ops


@dataclass(slots=True)
class Metrics:
    atr: float
    average_volume: float
    momentum: float


class MetricsService:
    def __init__(self, atr_period: int, volume_period: int, momentum_period: int) -> None:
        self._atr_period = atr_period
        self._volume_period = volume_period
        self._momentum_period = momentum_period

    def calculate(self, candles: Sequence[Candle]) -> Metrics:
        if len(candles) < max(self._atr_period, self._volume_period, self._momentum_period):
            raise ValueError("not enough candles to compute metrics")
        atr = math_ops.average_true_range(candles, self._atr_period)
        avg_volume = math_ops.rolling_volume(candles, self._volume_period)
        closes = [c.close for c in candles]
        momentum = closes[-1] - closes[-self._momentum_period]
        return Metrics(atr=atr, average_volume=avg_volume, momentum=momentum)

    def passes_thresholds(self, metrics: Metrics, thresholds: Thresholds) -> Dict[str, bool]:
        conditions = {
            "atr": metrics.atr >= thresholds.atr_multiplier,
            "volume": metrics.average_volume >= thresholds.volume_multiplier,
            "momentum": abs(metrics.momentum) >= thresholds.breakout_threshold,
        }
        return conditions
