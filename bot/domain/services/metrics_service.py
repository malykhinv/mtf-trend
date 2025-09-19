from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Union, cast

from ..enums import BreakDirection
from ..models.entities import Candle, SignalMetric, Thresholds
from ...utils import math_ops


SNAPSHOT_FIELDS = (
    "atr",
    "average_volume",
    "momentum",
    "pct_move",
    "relative_volume",
    "atr_multiple",
    "upper_wick_pct",
    "body_pct",
    "lower_wick_pct",
    "pct_to_high",
    "pct_to_low",
    "pct_to_high_break",
    "pct_to_low_break",
    "break_direction",
)


@dataclass(slots=True)
class SelectionMetricsSnapshot:
    atr: float
    average_volume: float
    momentum: float
    pct_move: float
    relative_volume: float
    atr_multiple: float
    upper_wick_pct: float
    body_pct: float
    lower_wick_pct: float
    pct_to_high: float
    pct_to_low: float
    pct_to_high_break: float
    pct_to_low_break: float
    break_direction: BreakDirection

    def to_mapping(self) -> Dict[str, float | int]:
        return {
            "atr": self.atr,
            "average_volume": self.average_volume,
            "momentum": self.momentum,
            "pct_move": self.pct_move,
            "relative_volume": self.relative_volume,
            "atr_multiple": self.atr_multiple,
            "upper_wick_pct": self.upper_wick_pct,
            "body_pct": self.body_pct,
            "lower_wick_pct": self.lower_wick_pct,
            "pct_to_high": self.pct_to_high,
            "pct_to_low": self.pct_to_low,
            "pct_to_high_break": self.pct_to_high_break,
            "pct_to_low_break": self.pct_to_low_break,
            "break_direction": int(self.break_direction.value),
        }

    @classmethod
    def from_metrics(cls, metrics: "Metrics") -> "SelectionMetricsSnapshot":
        return cls(
            atr=metrics.atr,
            average_volume=metrics.average_volume,
            momentum=metrics.momentum,
            pct_move=metrics.pct_move,
            relative_volume=metrics.relative_volume,
            atr_multiple=metrics.atr_multiple,
            upper_wick_pct=metrics.upper_wick_pct,
            body_pct=metrics.body_pct,
            lower_wick_pct=metrics.lower_wick_pct,
            pct_to_high=metrics.pct_to_high,
            pct_to_low=metrics.pct_to_low,
            pct_to_high_break=metrics.pct_to_high_break,
            pct_to_low_break=metrics.pct_to_low_break,
            break_direction=metrics.break_direction,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "SelectionMetricsSnapshot":
        values: Dict[str, float] = {}
        break_direction: BreakDirection | None = None
        for key in SNAPSHOT_FIELDS:
            raw_value = data.get(key)
            if raw_value is None:
                raise KeyError(f"missing '{key}' in metrics snapshot")
            if key == "break_direction":
                try:
                    direction_value = int(float(cast(Union[int, float, str], raw_value)))
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid break_direction value") from exc
                try:
                    break_direction = BreakDirection(direction_value)
                except ValueError as exc:
                    raise ValueError("unknown break_direction value") from exc
                continue
            values[key] = float(cast(Union[int, float, str], raw_value))
        if break_direction is None:
            raise KeyError("missing 'break_direction' in metrics snapshot")
        return cls(
            atr=values["atr"],
            average_volume=values["average_volume"],
            momentum=values["momentum"],
            pct_move=values["pct_move"],
            relative_volume=values["relative_volume"],
            atr_multiple=values["atr_multiple"],
            upper_wick_pct=values["upper_wick_pct"],
            body_pct=values["body_pct"],
            lower_wick_pct=values["lower_wick_pct"],
            pct_to_high=values["pct_to_high"],
            pct_to_low=values["pct_to_low"],
            pct_to_high_break=values["pct_to_high_break"],
            pct_to_low_break=values["pct_to_low_break"],
            break_direction=break_direction,
        )


@dataclass(slots=True)
class Metrics:
    atr: float
    average_volume: float
    momentum: float
    pct_move: float
    relative_volume: float
    atr_multiple: float
    upper_wick_pct: float
    body_pct: float
    lower_wick_pct: float
    pct_to_high: float
    pct_to_low: float
    pct_to_high_break: float
    pct_to_low_break: float
    break_direction: BreakDirection

    def as_dict(self) -> Dict[str, float]:
        snapshot = SelectionMetricsSnapshot.from_metrics(self)
        return snapshot.to_mapping()


class MetricsService:
    def __init__(self, atr_period: int, volume_period: int, momentum_period: int) -> None:
        self._atr_period = atr_period
        self._volume_period = volume_period
        self._momentum_period = momentum_period

    def calculate(
        self, candles: Sequence[Candle], future_candles: Sequence[Candle] | None = None
    ) -> Metrics:
        if len(candles) < max(self._atr_period, self._volume_period, self._momentum_period):
            raise ValueError("not enough candles to compute metrics")
        atr = math_ops.average_true_range(candles, self._atr_period)
        avg_volume = math_ops.rolling_volume(candles, self._volume_period)
        closes = [c.close for c in candles]
        momentum = closes[-1] - closes[-self._momentum_period]
        current = candles[-1]
        body = current.close - current.open
        body_abs = abs(body)
        pct_move = (
            (current.close - current.open) / current.open * 100 if current.open else 0.0
        )
        median_window = min(20, len(candles))
        relative_volume = (
            current.volume / math_ops.median([c.volume for c in candles], median_window)
            if median_window
            else 0.0
        )
        range_total = current.high - current.low
        atr_multiple = (range_total / atr) if atr else 0.0
        upper_wick = max(current.high - max(current.open, current.close), 0.0)
        lower_wick = max(min(current.open, current.close) - current.low, 0.0)
        if range_total:
            upper_wick_pct = (upper_wick / range_total) * 100
            body_pct = (body_abs / range_total) * 100
            lower_wick_pct = (lower_wick / range_total) * 100
            remainder = 100.0 - (upper_wick_pct + body_pct + lower_wick_pct)
            body_pct += remainder
        else:
            upper_wick_pct = 0.0
            body_pct = 0.0
            lower_wick_pct = 0.0

        future = list(future_candles or [])
        pct_to_high = float("nan")
        pct_to_low = float("nan")
        pct_to_high_break = 0.0
        pct_to_low_break = 0.0
        break_direction = BreakDirection.NONE
        if future:
            max_future_high = max(c.high for c in future)
            min_future_low = min(c.low for c in future)
            pct_to_high = (
                (max_future_high - current.close) / current.close * 100
                if current.close
                else 0.0
            )
            pct_to_low = (
                (min_future_low - current.close) / current.close * 100
                if current.close
                else 0.0
            )
            high_break_index: int | None = None
            low_break_index: int | None = None
            for idx, candle in enumerate(future):
                broke_high = candle.high >= current.high
                broke_low = candle.low <= current.low
                if broke_high and high_break_index is None:
                    high_break_index = idx
                if broke_low and low_break_index is None:
                    low_break_index = idx
                if high_break_index is not None and low_break_index is not None:
                    break
            if high_break_index is not None:
                pct_to_high_break = (
                    max(current.high - current.close, 0.0) / current.close * 100
                    if current.close
                    else 0.0
                )
            if low_break_index is not None:
                pct_to_low_break = (
                    max(current.close - current.low, 0.0) / current.close * 100
                    if current.close
                    else 0.0
                )
            if high_break_index is not None and low_break_index is not None:
                if high_break_index < low_break_index:
                    break_direction = BreakDirection.HIGH_FIRST
                elif low_break_index < high_break_index:
                    break_direction = BreakDirection.LOW_FIRST
                else:
                    break_direction = BreakDirection.NONE
            elif high_break_index is not None:
                break_direction = BreakDirection.HIGH_FIRST
            elif low_break_index is not None:
                break_direction = BreakDirection.LOW_FIRST
        return Metrics(
            atr=atr,
            average_volume=avg_volume,
            momentum=momentum,
            pct_move=pct_move,
            relative_volume=relative_volume,
            atr_multiple=atr_multiple,
            upper_wick_pct=upper_wick_pct,
            body_pct=body_pct,
            lower_wick_pct=lower_wick_pct,
            pct_to_high=pct_to_high,
            pct_to_low=pct_to_low,
            pct_to_high_break=pct_to_high_break,
            pct_to_low_break=pct_to_low_break,
            break_direction=break_direction,
        )

    def evaluate_thresholds(self, metrics: Metrics, thresholds: Thresholds) -> List[SignalMetric]:
        metrics_map = metrics.as_dict()
        evaluations: List[SignalMetric] = []
        for metric_threshold in thresholds.metrics:
            value = metrics_map.get(metric_threshold.name)
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
        existing_names = {metric.name for metric in evaluations}
        for name, value in metrics_map.items():
            if name in existing_names:
                continue
            evaluations.append(
                SignalMetric(
                    name=name,
                    value=value,
                    passed=True,
                    threshold=None,
                )
            )
        return evaluations
