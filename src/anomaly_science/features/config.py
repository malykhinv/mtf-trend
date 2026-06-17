from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.future.atr import ATR_1D_WINDOW_MINUTES


@dataclass(frozen=True, slots=True)
class FeatureMatrixConfig:
    """Configuration for MVP feature matrix materialization.

    This stage is intentionally limited to as-of price/time/alpha-decay
    features. Flow, OI, liquidation, cross-sectional and BTC-relative feature
    families are added in later patches.
    """

    feature_matrix_version: str = "feature_matrix_v1_price_time_alpha_decay"
    atr_window_minutes: int = ATR_1D_WINDOW_MINUTES
    expected_event_lifetime_minutes: int = 60

    def __post_init__(self) -> None:
        if not self.feature_matrix_version:
            raise ValueError("feature_matrix_version is required")
        if self.atr_window_minutes <= 0:
            raise ValueError("atr_window_minutes must be positive")
        if self.expected_event_lifetime_minutes <= 0:
            raise ValueError("expected_event_lifetime_minutes must be positive")
