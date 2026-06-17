from __future__ import annotations

from dataclasses import dataclass


DETECTOR_VERSION = "broad_anomaly_detector_v1"


@dataclass(frozen=True, slots=True)
class BroadAnomalyDetectorConfig:
    """Configuration for the MVP1 broad activity detector.

    The detector is intentionally broad. It is not a trade setup, does not use
    future outcomes, and only compares the current closed 1m candle with earlier
    candles of the same symbol.
    """

    baseline_bars: int = 60
    min_baseline_bars: int = 20
    min_abs_return_pct: float = 0.015
    min_quote_volume_zscore: float = 4.0
    min_volume_zscore: float = 4.0
    min_trade_count_zscore: float = 4.0
    min_range_zscore: float = 4.0
    cooldown_minutes: int = 0
    detector_version: str = DETECTOR_VERSION

    def __post_init__(self) -> None:
        if self.baseline_bars <= 0:
            raise ValueError("baseline_bars must be positive")
        if self.min_baseline_bars <= 0:
            raise ValueError("min_baseline_bars must be positive")
        if self.min_baseline_bars > self.baseline_bars:
            raise ValueError("min_baseline_bars must be <= baseline_bars")
        if self.min_abs_return_pct < 0:
            raise ValueError("min_abs_return_pct must be non-negative")
        if self.cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be non-negative")
        if not self.detector_version:
            raise ValueError("detector_version is required")
