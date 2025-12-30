"""Signal analyzer computing strategy decisions for closed bars."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
from uuid import uuid4

from bot import config
from bot.domain.models.anomaly import AnomalyThresholdSnapshot, Anomaly
from bot.domain.models.bar import Bar
from bot.domain.models.signal import ThresholdSnapshot, Signal, SignalLevels
from bot.domain.models.signal_direction import SignalDirection


@dataclass(frozen=True)
class AnalyzerSettings:
    min_green_move_pct: float = config.ANOMALY_MIN_GROWTH_PCT
    min_volume_spike: float = config.ANOMALY_MIN_VOLUME_SPIKE
    min_anomaly_atr_mult: float = config.ANOMALY_MIN_ATR_MULT
    min_anomaly_relative_volume: float = config.ANOMALY_MIN_RELATIVE_VOLUME
    min_anomaly_upper_wick_pct: float = config.ANOMALY_MIN_UPPER_WICK_PCT
    min_relative_volume: float = config.MIN_REL_VOL
    max_relative_volume: float = config.MAX_REL_VOL
    min_atr_mult: float = config.MIN_ATR_MULT
    take_profit_atr_mult: float = config.TAKE_PROFIT_ATR_MULT
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
            take_profit_atr_mult=self.take_profit_atr_mult,
            min_pct_move=self.min_pct_move,
            max_pct_move=self.max_pct_move,
            max_upper_wick_pct=self.max_upper_wick_pct,
            max_lower_wick_pct=self.max_lower_wick_pct,
        )

    def anomaly_snapshot(self) -> AnomalyThresholdSnapshot:
        return AnomalyThresholdSnapshot(
            min_green_move_pct=self.min_green_move_pct,
            min_volume_spike=self.min_volume_spike,
            min_relative_volume=self.min_anomaly_relative_volume,
            min_atr_mult=self.min_anomaly_atr_mult,
            min_upper_wick_pct=self.min_anomaly_upper_wick_pct,
        )


class SignalAnalyzer:
    """Evaluates bars and produces strategy signals."""

    def __init__(self, settings: AnalyzerSettings | None = None) -> None:
        self._settings = settings or AnalyzerSettings()

    def apply_settings(self, settings: AnalyzerSettings) -> None:
        """Replace the analyzer thresholds with a new configuration."""

        self._settings = settings

    def analyze_bar(
        self, bar: Bar, timestamp: Optional[datetime] = None
    ) -> Tuple[Optional[Signal], Optional[Anomaly]]:
        anomaly = self.detect_anomaly(bar)
        if anomaly is None:
            return None, anomaly

        metrics = bar.metrics
        thresholds = self._settings

        volume_within_bounds = (
            thresholds.min_relative_volume
            <= metrics.relative_volume
            <= thresholds.max_relative_volume
        )
        atr_above_min = metrics.atr_mult > thresholds.min_atr_mult
        pct_move_within_bounds = (
            thresholds.min_pct_move
            <= metrics.pct_move
            <= thresholds.max_pct_move
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
        if not allow_long:
            return None, anomaly

        direction = SignalDirection.LONG
        levels = SignalLevels(
            entry_price=bar.close,
            take_profit_price=bar.high,
            stop_loss_price=bar.low,
        )

        entry_price = levels.entry_price
        take_profit_price = levels.take_profit_price
        stop_loss_price = levels.stop_loss_price

        risk = entry_price - stop_loss_price
        if risk == 0:
            return None, anomaly
        reward = take_profit_price - entry_price

        rr = reward / risk

        if rr < config.MIN_RR:
            return None, anomaly

        signal = Signal(
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

        return signal, anomaly

    @staticmethod
    def _generate_signal_id(bar: Bar) -> str:
        return f"{bar.bar_id}:{uuid4().hex}"

    def detect_anomaly(self, bar: Bar) -> Optional[Anomaly]:
        metrics = bar.metrics
        thresholds = self._settings

        if bar.close <= bar.open:
            return None

        if metrics.pct_move < thresholds.min_green_move_pct:
            return None

        if metrics.atr_mult < thresholds.min_anomaly_atr_mult:
            return None

        if metrics.upper_wick_pct < thresholds.min_anomaly_upper_wick_pct:
            return None

        relative_volume = metrics.relative_volume
        if relative_volume < thresholds.min_anomaly_relative_volume:
            return None

        if relative_volume < thresholds.min_volume_spike:
            return None

        return Anomaly(
            bar_id=bar.bar_id,
            exchange=bar.exchange,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            timestamp=bar.close_time,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            metrics=metrics,
            thresholds=thresholds.anomaly_snapshot(),
        )

