from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReferenceMarketSpec:
    symbol: str
    alias: str

    def __post_init__(self) -> None:
        if not self.symbol or not self.alias:
            raise ValueError("reference market symbol and alias are required")
        if not self.alias.replace("_", "").isalnum() or self.alias.lower() != self.alias:
            raise ValueError("reference market alias must be lowercase alphanumeric/underscore")


@dataclass(frozen=True, slots=True)
class ReferenceMarketContextConfig:
    schema_version: str
    references: tuple[ReferenceMarketSpec, ...]
    return_lags_minutes: tuple[int, ...] = (5, 15, 60, 240, 1440)
    oi_lags_minutes: tuple[int, ...] = (15, 60, 240)
    taker_windows_minutes: tuple[int, ...] = (15, 60)
    range_window_minutes: int = 60
    activity_window_minutes: int = 60
    activity_baseline_minutes: int = 1440
    interval_ms: int = 60_000

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("market-context schema_version is required")
        if not self.references:
            raise ValueError("at least one reference market is required")
        aliases = [item.alias for item in self.references]
        symbols = [item.symbol for item in self.references]
        if len(aliases) != len(set(aliases)) or len(symbols) != len(set(symbols)):
            raise ValueError("reference symbols and aliases must be unique")
        for name in ("return_lags_minutes", "oi_lags_minutes", "taker_windows_minutes"):
            values = getattr(self, name)
            if not values or tuple(sorted(set(values))) != values or any(value <= 0 for value in values):
                raise ValueError(f"{name} must be positive, unique, and sorted")
        for name in (
            "range_window_minutes", "activity_window_minutes",
            "activity_baseline_minutes", "interval_ms",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.activity_baseline_minutes < self.activity_window_minutes:
            raise ValueError("activity baseline must be at least the activity window")


@dataclass(frozen=True, slots=True)
class ReferencePositioningContextConfig:
    schema_version: str
    references: tuple[ReferenceMarketSpec, ...]
    change_lags_minutes: tuple[int, ...] = (15, 60, 240)
    metrics_interval_minutes: int = 5
    max_age_minutes: int = 10

    def __post_init__(self) -> None:
        if not self.schema_version or not self.references:
            raise ValueError("positioning context schema/references are required")
        if tuple(sorted(set(self.change_lags_minutes))) != self.change_lags_minutes:
            raise ValueError("positioning change lags must be sorted and unique")
        if any(
            lag <= 0 or lag % self.metrics_interval_minutes
            for lag in self.change_lags_minutes
        ):
            raise ValueError("positioning lags must be positive multiples of metrics interval")
        if self.metrics_interval_minutes <= 0 or self.max_age_minutes < self.metrics_interval_minutes:
            raise ValueError("positioning metrics interval/max age are invalid")


@dataclass(frozen=True, slots=True)
class EventScopedPositioningContextConfig:
    """Generic same-symbol positioning contract for event-indexed research rows."""

    schema_version: str
    change_lags_minutes: tuple[int, ...] = (15, 60)
    metrics_interval_minutes: int = 5
    max_age_minutes: int = 10
    publication_lag_minutes: int = 5

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("event-scoped positioning schema_version is required")
        if tuple(sorted(set(self.change_lags_minutes))) != self.change_lags_minutes:
            raise ValueError("event-scoped positioning lags must be sorted and unique")
        if any(
            lag <= 0 or lag % self.metrics_interval_minutes
            for lag in self.change_lags_minutes
        ):
            raise ValueError("event-scoped positioning lags must align to metrics interval")
        if self.metrics_interval_minutes <= 0:
            raise ValueError("metrics_interval_minutes must be positive")
        if self.max_age_minutes < self.metrics_interval_minutes:
            raise ValueError("max_age_minutes must cover at least one metrics interval")
        if self.publication_lag_minutes < self.metrics_interval_minutes:
            raise ValueError("publication lag must cover at least one metrics interval")


@dataclass(frozen=True, slots=True)
class EventScopedPerpCrowdingContextConfig:
    """Generic same-symbol premium-index and funding-history contract."""

    schema_version: str
    premium_change_lags_minutes: tuple[int, ...] = (5, 15, 60)
    premium_stat_windows_minutes: tuple[int, ...] = (15, 60)
    premium_zscore_window_minutes: int = 240
    premium_zscore_min_periods: int = 120
    premium_max_age_minutes: int = 2
    funding_publication_lag_minutes: int = 1
    funding_max_age_minutes: int = 12 * 60
    funding_mean_observations: int = 3

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("perp-crowding schema_version is required")
        for name in ("premium_change_lags_minutes", "premium_stat_windows_minutes"):
            values = getattr(self, name)
            if not values or tuple(sorted(set(values))) != values or any(v <= 0 for v in values):
                raise ValueError(f"{name} must be positive, unique, and sorted")
        if self.premium_zscore_window_minutes < max(self.premium_stat_windows_minutes):
            raise ValueError("premium z-score window must cover registered statistic windows")
        if not 2 <= self.premium_zscore_min_periods <= self.premium_zscore_window_minutes:
            raise ValueError("premium z-score min periods are invalid")
        if self.premium_max_age_minutes < 1:
            raise ValueError("premium max age must cover one closed minute")
        if self.funding_publication_lag_minutes < 1:
            raise ValueError("funding publication lag must be at least one minute")
        if self.funding_max_age_minutes < 8 * 60:
            raise ValueError("funding max age must cover the standard eight-hour interval")
        if self.funding_mean_observations < 2:
            raise ValueError("funding mean requires at least two observations")


__all__ = [
    "ReferenceMarketContextConfig",
    "ReferenceMarketSpec",
    "ReferencePositioningContextConfig",
    "EventScopedPositioningContextConfig",
    "EventScopedPerpCrowdingContextConfig",
]
