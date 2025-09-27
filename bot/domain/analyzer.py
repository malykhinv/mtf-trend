"""Signal analyzer computing strategy decisions for closed bars."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import uuid4

from bot import config
from .models.bar import Bar
from .models.signal import Signal, SignalLevels, ThresholdSnapshot
from .models.signal_direction import SignalDirection


@dataclass(frozen=True)
class AnalyzerSettings:
    min_green_move_pct: float = config.ANOMALY_MIN_GROWTH_PCT
    min_volume_spike: float = config.ANOMALY_MIN_VOLUME_SPIKE

    def snapshot(self) -> ThresholdSnapshot:
        return ThresholdSnapshot(
            min_green_move_pct=self.min_green_move_pct,
            min_volume_spike=self.min_volume_spike,
        )


class SignalAnalyzer:
    """Evaluates bars and produces strategy signals."""

    def __init__(self) -> None:
        self._settings = AnalyzerSettings()

    def analyze_bar(self, bar: Bar, timestamp: Optional[datetime] = None) -> Optional[Signal]:
        if not self._is_green(bar):
            return None

        if not self._meets_move_threshold(bar):
            return None

        if not self._meets_volume_threshold(bar):
            return None

        levels = SignalLevels(
            entry_price=bar.close,
            take_profit_price=bar.high,
            stop_loss_price=bar.low,
        )
        signal = Signal(
            signal_id=self._generate_signal_id(bar),
            bar=bar,
            timestamp=timestamp or bar.close_time,
            exchange=bar.exchange,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            thresholds=self._settings.snapshot(),
            direction=SignalDirection.LONG,
            levels=levels,
        )
        return signal

    @staticmethod
    def _generate_signal_id(bar: Bar) -> str:
        return f"{bar.bar_id}:{uuid4().hex}"

    @staticmethod
    def _is_green(bar: Bar) -> bool:
        return bar.close > bar.open

    def _meets_move_threshold(self, bar: Bar) -> bool:
        return bar.metrics.pct_move >= self._settings.min_green_move_pct

    def _meets_volume_threshold(self, bar: Bar) -> bool:
        return bar.metrics.relative_volume >= self._settings.min_volume_spike
