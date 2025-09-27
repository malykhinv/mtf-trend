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
    min_relative_volume: float = config.MIN_REL_VOL
    max_relative_volume: float = config.MAX_REL_VOL
    min_atr_mult: float = config.MIN_ATR_MULT
    min_pct_move: float = config.MIN_PCT_MOVE
    max_pct_move: float = config.MAX_PCT_MOVE
    max_upper_wick_pct: float = config.MAX_UPPER_WICK_PCT
    max_lower_wick_pct: float = config.MAX_LOWER_WICK_PCT

    def snapshot(self) -> ThresholdSnapshot:
        return ThresholdSnapshot(
            min_green_move_pct=self.min_green_move_pct,
            min_volume_spike=self.min_volume_spike,
            min_relative_volume=self.min_relative_volume,
            max_relative_volume=self.max_relative_volume,
            min_atr_mult=self.min_atr_mult,
            min_pct_move=self.min_pct_move,
            max_pct_move=self.max_pct_move,
            max_upper_wick_pct=self.max_upper_wick_pct,
            max_lower_wick_pct=self.max_lower_wick_pct,
        )


class SignalAnalyzer:
    """Evaluates bars and produces strategy signals."""

    def __init__(self) -> None:
        self._settings = AnalyzerSettings()

    def analyze_bar(self, bar: Bar, timestamp: Optional[datetime] = None) -> Optional[Signal]:
        metrics = bar.metrics
        thresholds = self._settings

        volume_within_bounds = (
            thresholds.min_relative_volume
            <= metrics.relative_volume
            <= thresholds.max_relative_volume
        )
        volume_outside_bounds = (
            metrics.relative_volume < thresholds.min_relative_volume
            or metrics.relative_volume > thresholds.max_relative_volume
        )
        atr_above_min = metrics.atr_mult > thresholds.min_atr_mult
        atr_below_min = metrics.atr_mult < thresholds.min_atr_mult
        pct_move_within_bounds = (
            thresholds.min_pct_move
            <= metrics.pct_move
            <= thresholds.max_pct_move
        )
        pct_move_outside_bounds = (
            metrics.pct_move > thresholds.max_pct_move
            or metrics.pct_move < thresholds.min_pct_move
        )
        upper_wick_ok = metrics.upper_wick_pct < thresholds.max_upper_wick_pct
        lower_wick_ok = metrics.lower_wick_pct < thresholds.max_lower_wick_pct

        allow_long = (
            volume_within_bounds
            and atr_above_min
            and pct_move_within_bounds
            and upper_wick_ok
            and lower_wick_ok
        )
        allow_short = (
            volume_outside_bounds
            and atr_below_min
            and pct_move_outside_bounds
            and upper_wick_ok
            and lower_wick_ok
        )

        if not allow_long and not allow_short:
            return None

        if allow_long:
            direction = SignalDirection.LONG
            levels = SignalLevels(
                entry_price=bar.close,
                take_profit_price=bar.high,
                stop_loss_price=bar.low,
            )
        else:
            direction = SignalDirection.SHORT
            levels = SignalLevels(
                entry_price=bar.close,
                take_profit_price=bar.low,
                stop_loss_price=bar.high,
            )

        return Signal(
            signal_id=self._generate_signal_id(bar),
            bar=bar,
            timestamp=timestamp or bar.close_time,
            exchange=bar.exchange,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            thresholds=thresholds.snapshot(),
            direction=direction,
            levels=levels,
        )

    @staticmethod
    def _generate_signal_id(bar: Bar) -> str:
        return f"{bar.bar_id}:{uuid4().hex}"

