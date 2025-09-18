from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from ..models.entities import Candle, SignalMetric, Thresholds
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

    def evaluate_thresholds(self, metrics: Metrics, thresholds: Thresholds) -> List[SignalMetric]:
        evaluations: List[SignalMetric] = []
        for metric_threshold in thresholds.metrics:
            value = getattr(metrics, metric_threshold.name, None)
            if value is None:
                raise KeyError(f"unknown metric '{metric_threshold.name}'")
            passed = True
            if metric_threshold.min_value is not None and value < metric_threshold.min_value:
                passed = False
            if metric_threshold.max_value is not None and value > metric_threshold.max_value:
                passed = False
            if metric_threshold.min_abs_value is not None and abs(value) < metric_threshold.min_abs_value:
                passed = False
            evaluations.append(
                SignalMetric(
                    name=metric_threshold.name,
                    value=value,
                    passed=passed,
                    threshold=metric_threshold,
                )
            )
        return evaluations
