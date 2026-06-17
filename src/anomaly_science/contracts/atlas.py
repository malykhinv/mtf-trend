from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AtlasNatureRow:
    atlas_version: str
    split_family: str
    split_value: str
    outcome_horizon_minutes: int
    atlas_outcome_bin: str
    row_count: int
    unique_event_count: int
    unique_symbol_count: int
    mean_future_return: float | None
    median_future_return: float | None
    mean_future_max: float | None
    mean_future_min: float | None
    reclaim_rate: float | None
    feature_min_snapshot_time_ms: int
    feature_max_snapshot_time_ms: int
    temporal_contract: str

    def __post_init__(self) -> None:
        _require_text(self.atlas_version, "atlas_version")
        _require_text(self.split_family, "split_family")
        _require_text(self.split_value, "split_value")
        _require_text(self.atlas_outcome_bin, "atlas_outcome_bin")
        _require_positive_int(self.outcome_horizon_minutes, "outcome_horizon_minutes")
        _require_non_negative_int(self.row_count, "row_count")
        _require_non_negative_int(self.unique_event_count, "unique_event_count")
        _require_non_negative_int(self.unique_symbol_count, "unique_symbol_count")
        _require_non_negative_int(self.feature_min_snapshot_time_ms, "feature_min_snapshot_time_ms")
        _require_non_negative_int(self.feature_max_snapshot_time_ms, "feature_max_snapshot_time_ms")
        if self.feature_max_snapshot_time_ms < self.feature_min_snapshot_time_ms:
            raise ValueError("feature_max_snapshot_time_ms must be >= feature_min_snapshot_time_ms")
        _require_text(self.temporal_contract, "temporal_contract")


@dataclass(frozen=True, slots=True)
class AtlasContextSplitRow:
    atlas_version: str
    context_name: str
    context_value: str
    row_count: int
    unique_event_count: int
    unique_symbol_count: int
    mean_minutes_since_detection: float | None
    mean_current_return_from_start: float | None
    mean_distance_to_running_high: float | None
    upside_continuation_share: float | None
    downside_extension_share: float | None
    two_sided_share: float | None
    range_chop_share: float | None
    mixed_drift_share: float | None
    missing_future_share: float | None

    def __post_init__(self) -> None:
        _require_text(self.atlas_version, "atlas_version")
        _require_text(self.context_name, "context_name")
        _require_text(self.context_value, "context_value")
        _require_non_negative_int(self.row_count, "row_count")
        _require_non_negative_int(self.unique_event_count, "unique_event_count")
        _require_non_negative_int(self.unique_symbol_count, "unique_symbol_count")


@dataclass(frozen=True, slots=True)
class AtlasResponseSurfaceRow:
    atlas_version: str
    surface_name: str
    x_axis: str
    x_bin: str
    y_axis: str
    y_bin: str
    outcome_horizon_minutes: int
    row_count: int
    unique_event_count: int
    mean_future_return: float | None
    mean_future_max: float | None
    mean_future_min: float | None
    reclaim_rate: float | None
    dominant_outcome_bin: str

    def __post_init__(self) -> None:
        _require_text(self.atlas_version, "atlas_version")
        _require_text(self.surface_name, "surface_name")
        _require_text(self.x_axis, "x_axis")
        _require_text(self.x_bin, "x_bin")
        _require_text(self.y_axis, "y_axis")
        _require_text(self.y_bin, "y_bin")
        _require_positive_int(self.outcome_horizon_minutes, "outcome_horizon_minutes")
        _require_non_negative_int(self.row_count, "row_count")
        _require_non_negative_int(self.unique_event_count, "unique_event_count")
        _require_text(self.dominant_outcome_bin, "dominant_outcome_bin")


@dataclass(frozen=True, slots=True)
class AtlasMarketShockGroupRow:
    atlas_version: str
    market_shock_group_id: str
    snapshot_time_ms: int
    row_count: int
    unique_event_count: int
    unique_symbol_count: int
    symbols: str
    market_shock_candidate: bool
    mean_current_return_from_start: float | None
    mean_future_return_30m: float | None
    dominant_outcome_bin: str
    temporal_contract: str

    def __post_init__(self) -> None:
        _require_text(self.atlas_version, "atlas_version")
        _require_text(self.market_shock_group_id, "market_shock_group_id")
        _require_non_negative_int(self.snapshot_time_ms, "snapshot_time_ms")
        _require_non_negative_int(self.row_count, "row_count")
        _require_non_negative_int(self.unique_event_count, "unique_event_count")
        _require_non_negative_int(self.unique_symbol_count, "unique_symbol_count")
        _require_text(self.dominant_outcome_bin, "dominant_outcome_bin")
        _require_text(self.temporal_contract, "temporal_contract")


def _require_text(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} is required")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive_int(value: int, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
