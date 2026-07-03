"""Broad-anomaly strategy trigger configuration."""

from __future__ import annotations

from dataclasses import dataclass


DETECTOR_VERSION = "broad_anomaly_detector_v2"


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
    fast_burst_window_minutes: int = 3
    min_fast_burst_return_pct: float = 0.025
    grind_pump_window_minutes: int = 15
    min_grind_pump_return_pct: float = 0.05
    min_grind_pump_positive_candles: int = 10
    max_grind_pump_single_candle_abs_return_pct: float = 0.018
    breakout_lookback_minutes: int = 20
    min_breakout_pct: float = 0.002
    min_pump_inside_noise_return_pct: float = 0.01
    session_activity_start_minutes_utc: tuple[int, ...] = (0, 480, 780)
    session_activity_window_minutes: int = 30
    min_session_activity_quote_volume_zscore: float = 3.0
    market_wide_window_min_symbols: int = 3
    min_market_wide_abs_return_pct: float = 0.01
    min_market_wide_quote_volume_zscore: float = 2.0
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
        for field_name in (
            "min_quote_volume_zscore",
            "min_volume_zscore",
            "min_trade_count_zscore",
            "min_range_zscore",
            "min_fast_burst_return_pct",
            "min_grind_pump_return_pct",
            "min_breakout_pct",
            "min_pump_inside_noise_return_pct",
            "min_session_activity_quote_volume_zscore",
            "min_market_wide_abs_return_pct",
            "min_market_wide_quote_volume_zscore",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        for field_name in (
            "fast_burst_window_minutes",
            "grind_pump_window_minutes",
            "min_grind_pump_positive_candles",
            "breakout_lookback_minutes",
            "session_activity_window_minutes",
            "market_wide_window_min_symbols",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.min_grind_pump_positive_candles > self.grind_pump_window_minutes:
            raise ValueError("min_grind_pump_positive_candles must be <= grind_pump_window_minutes")
        if any(start_minute < 0 or start_minute >= 1_440 for start_minute in self.session_activity_start_minutes_utc):
            raise ValueError("session_activity_start_minutes_utc values must be in [0, 1439]")
        if self.cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be non-negative")
        if not self.detector_version:
            raise ValueError("detector_version is required")
