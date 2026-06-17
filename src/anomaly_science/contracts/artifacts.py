from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactSchema:
    name: str
    stage: str
    required_columns: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        if not self.name.endswith((".csv", ".json")):
            raise ValueError("artifact name must include .csv or .json extension")
        if not self.stage:
            raise ValueError("stage is required")
        if len(set(self.required_columns)) != len(self.required_columns):
            raise ValueError(f"duplicate columns in artifact schema {self.name}")


MVP1_ARTIFACT_SCHEMAS: dict[str, ArtifactSchema] = {
    "anomaly_events.csv": ArtifactSchema(
        name="anomaly_events.csv",
        stage="mvp1_events",
        required_columns=(
            "event_id",
            "symbol",
            "event_start_time_ms",
            "event_detection_time_ms",
            "seed_time_ms",
            "seed_open",
            "seed_high",
            "seed_low",
            "seed_close",
            "initial_move_pct",
            "initial_volume_zscore",
            "initial_quote_volume_zscore",
            "initial_trade_count_zscore",
            "detector_version",
        ),
        description="Broad anomaly events; detector output, not trade setups.",
    ),
    "anomaly_state_1m.csv": ArtifactSchema(
        name="anomaly_state_1m.csv",
        stage="mvp1_state",
        required_columns=(
            "event_id",
            "symbol",
            "state_time_ms",
            "snapshot_time_ms",
            "feature_cutoff_time_ms",
            "minutes_since_event_start",
            "minutes_since_detection",
            "event_alive",
            "running_high_asof_t",
            "running_high_time_asof_t_ms",
            "running_low_asof_t",
            "running_low_time_asof_t_ms",
            "time_since_running_high_minutes",
            "current_close",
            "current_return_from_start",
            "distance_to_running_high",
            "distance_to_running_low",
            "distance_to_structural_low",
            "distance_to_structural_high",
        ),
        description="Online 1m anomaly state using only data available as-of state_time.",
    ),
    "anomaly_future_paths.csv": ArtifactSchema(
        name="anomaly_future_paths.csv",
        stage="mvp1_future",
        required_columns=(
            "event_id",
            "symbol",
            "snapshot_time_ms",
            "feature_cutoff_time_ms",
            "future_start_time_ms",
            "future_return_5m",
            "future_return_15m",
            "future_return_30m",
            "future_return_60m",
            "future_max_5m",
            "future_max_15m",
            "future_max_30m",
            "future_max_60m",
            "future_min_5m",
            "future_min_15m",
            "future_min_30m",
            "future_min_60m",
            "reclaimed_running_high_30m",
            "reclaimed_running_high_60m",
            "broke_structural_low_30m",
            "broke_structural_low_60m",
            "time_to_new_high_minutes",
            "time_to_structural_break_minutes",
        ),
        description="Raw future paths strictly after each snapshot_time.",
    ),
    "anomaly_feature_catalog.csv": ArtifactSchema(
        name="anomaly_feature_catalog.csv",
        stage="mvp1_features",
        required_columns=(
            "feature_name",
            "family",
            "dtype",
            "description",
            "availability_rule",
            "uses_future_data",
            "nullable",
        ),
        description="Feature schema catalog and availability rules.",
    ),
    "anomaly_data_quality.csv": ArtifactSchema(
        name="anomaly_data_quality.csv",
        stage="mvp1_quality",
        required_columns=("check_name", "status", "severity", "affected_rows", "message", "artifact"),
        description="Data-quality checks; critical FAIL invalidates downstream interpretation.",
    ),
    "symbol_universe_by_day.csv": ArtifactSchema(
        name="symbol_universe_by_day.csv",
        stage="mvp1_universe",
        required_columns=(
            "trade_date",
            "symbol",
            "listed_asof_day",
            "delisted_asof_day",
            "tradable_on_day",
            "has_1m_data",
            "has_5m_data",
            "has_oi_data",
            "has_liquidation_data",
            "liquidity_eligible_on_day",
            "reason_if_excluded",
        ),
        description="Point-in-time symbol universe by day.",
    ),
    "anomaly_protocol_audit.csv": ArtifactSchema(
        name="anomaly_protocol_audit.csv",
        stage="mvp1_audit",
        required_columns=("check_name", "status", "message", "artifact"),
        description="Protocol audit for temporal correctness and anti-leakage invariants.",
    ),
    "anomaly_run_config.csv": ArtifactSchema(
        name="anomaly_run_config.csv",
        stage="mvp1_config",
        required_columns=("key", "value", "source"),
        description="Run configuration and reproducibility metadata.",
    ),
    "artifact_manifest.json": ArtifactSchema(
        name="artifact_manifest.json",
        stage="all",
        required_columns=("run_id", "created_at_utc", "artifacts"),
        description="Machine-readable manifest for artifacts written by a run.",
    ),
}


def get_artifact_schema(name: str) -> ArtifactSchema:
    try:
        return MVP1_ARTIFACT_SCHEMAS[name]
    except KeyError as exc:
        raise KeyError(f"unknown MVP1 artifact schema: {name}") from exc
