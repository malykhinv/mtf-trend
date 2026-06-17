from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.future.atr import ATR_1D_WINDOW_MINUTES


@dataclass(frozen=True, slots=True)
class FeatureMatrixConfig:
    """Configuration for MVP feature matrix materialization.

    This stage materializes as-of price/time/alpha-decay plus the first market
    physics families: volume self-history, closed 5m OI, liquidation flow, and
    CVD divergence. Cross-sectional and BTC-relative features are added later.
    """

    feature_matrix_version: str = "feature_matrix_v2_price_time_flow_oi_liq_cvd"
    atr_window_minutes: int = ATR_1D_WINDOW_MINUTES
    expected_event_lifetime_minutes: int = 60
    volume_baseline_window_minutes: int = 1440
    cvd_windows_minutes: tuple[int, ...] = (3, 5, 10)

    def __post_init__(self) -> None:
        if not self.feature_matrix_version:
            raise ValueError("feature_matrix_version is required")
        if self.atr_window_minutes <= 0:
            raise ValueError("atr_window_minutes must be positive")
        if self.expected_event_lifetime_minutes <= 0:
            raise ValueError("expected_event_lifetime_minutes must be positive")
        if self.volume_baseline_window_minutes <= 1:
            raise ValueError("volume_baseline_window_minutes must be greater than 1")
        if not self.cvd_windows_minutes:
            raise ValueError("cvd_windows_minutes must not be empty")
        if any(window <= 0 for window in self.cvd_windows_minutes):
            raise ValueError("cvd_windows_minutes must contain positive windows")
        if tuple(sorted(set(self.cvd_windows_minutes))) != self.cvd_windows_minutes:
            raise ValueError("cvd_windows_minutes must be sorted unique values")
