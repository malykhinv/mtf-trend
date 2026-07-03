from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.future.atr import ATR_1D_WINDOW_MINUTES
from anomaly_science.strategy.defaults import DEFAULT_RESEARCH_STRATEGY_NAME


@dataclass(frozen=True, slots=True)
class FeatureMatrixConfig:
    """Configuration for MVP feature matrix materialization.

    This stage materializes as-of price/time/alpha-decay plus the first market
    physics families: volume self-history, closed 5m OI, liquidation flow, CVD
    divergence, point-in-time cross-section, BTC-relative context, and systemic
    cluster context. It still does not train ML or make decisions.
    """

    feature_matrix_version: str = "feature_matrix_v5_relaxed_geometry"
    strategy_name: str = DEFAULT_RESEARCH_STRATEGY_NAME
    atr_window_minutes: int = ATR_1D_WINDOW_MINUTES
    expected_event_lifetime_minutes: int = 60
    volume_baseline_window_minutes: int = 1440
    cvd_windows_minutes: tuple[int, ...] = (3, 5, 10)
    min_cross_section_symbols: int = 3
    btc_symbol: str = "BTCUSDT"
    btc_corr_window_minutes: tuple[int, ...] = (15, 30, 60)
    btc_relative_return_windows_minutes: tuple[int, ...] = (5, 15)
    moderate_cluster_min_count: int = 3
    systemic_cluster_min_count: int = 21

    def __post_init__(self) -> None:
        if not self.feature_matrix_version:
            raise ValueError("feature_matrix_version is required")
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
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
        if self.min_cross_section_symbols <= 1:
            raise ValueError("min_cross_section_symbols must be greater than 1")
        if not self.btc_symbol:
            raise ValueError("btc_symbol is required")
        if not self.btc_corr_window_minutes:
            raise ValueError("btc_corr_window_minutes must not be empty")
        if any(window <= 1 for window in self.btc_corr_window_minutes):
            raise ValueError("btc_corr_window_minutes must contain windows greater than 1")
        if tuple(sorted(set(self.btc_corr_window_minutes))) != self.btc_corr_window_minutes:
            raise ValueError("btc_corr_window_minutes must be sorted unique values")
        if tuple(self.btc_corr_window_minutes) != (15, 30, 60):
            raise ValueError("btc_corr_window_minutes must remain frozen at (15, 30, 60)")
        if tuple(sorted(set(self.btc_relative_return_windows_minutes))) != self.btc_relative_return_windows_minutes:
            raise ValueError("btc_relative_return_windows_minutes must be sorted unique values")
        if tuple(self.btc_relative_return_windows_minutes) != (5, 15):
            raise ValueError("btc_relative_return_windows_minutes must remain frozen at (5, 15)")
        if self.moderate_cluster_min_count <= 2:
            raise ValueError("moderate_cluster_min_count must be greater than 2")
        if self.systemic_cluster_min_count <= self.moderate_cluster_min_count:
            raise ValueError("systemic_cluster_min_count must be greater than moderate_cluster_min_count")
