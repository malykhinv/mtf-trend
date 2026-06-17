from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .market import MarketDataContractError
from .time import enforce_snapshot_contract, validate_timestamp_ms


class FeatureFamily(str, Enum):
    PRICE_PATH = "price_path"
    SPEED_TIME = "speed_time"
    VOLUME = "volume"
    FLOW = "flow"
    OPEN_INTEREST = "open_interest"
    LIQUIDATION = "liquidation"
    CVD_DIVERGENCE = "cvd_divergence"
    CROSS_SECTIONAL = "cross_sectional_market_relative"
    SIGNAL_CLUSTERING = "signal_clustering_systemic_beta"
    MARKET_CONTEXT = "market_context"
    STRUCTURE = "structure"
    DATA_QUALITY = "data_quality"


class FeatureNormalization(str, Enum):
    RAW_AUDIT_ONLY = "raw_audit_only"
    ATR_NORMALIZED = "atr_normalized"
    SELF_HISTORY_RELATIVE = "self_history_relative"
    MARKET_RELATIVE = "market_relative"
    BTC_RELATIVE = "btc_relative"
    DIMENSIONLESS_RATIO = "dimensionless_ratio"
    BOOLEAN_FLAG = "boolean_flag"
    CATEGORICAL_BUCKET = "categorical_bucket"
    TIME_RELATIVE = "time_relative"
    POINT_IN_TIME_ID = "point_in_time_id"


class FeatureMissingPolicy(str, Enum):
    NOT_NULL = "not_null"
    NULL_IF_INSUFFICIENT_HISTORY = "null_if_insufficient_history"
    NULL_IF_SOURCE_MISSING = "null_if_source_missing"
    FALSE_IF_CONDITION_ABSENT = "false_if_condition_absent"
    AUDIT_ONLY_NULLABLE = "audit_only_nullable"


@dataclass(frozen=True, slots=True)
class FeatureCatalogRow:
    feature_schema_version: str
    feature_name: str
    feature_family: FeatureFamily
    source_artifact: str
    available_asof_time: str
    uses_future_data: bool
    normalization_type: FeatureNormalization
    is_model_feature: bool
    is_audit_field: bool
    missing_policy: FeatureMissingPolicy
    dtype: str
    description: str

    def __post_init__(self) -> None:
        if not self.feature_schema_version:
            raise ValueError("feature_schema_version is required")
        if not self.feature_name:
            raise ValueError("feature_name is required")
        if not self.source_artifact:
            raise ValueError("source_artifact is required")
        if not self.available_asof_time:
            raise ValueError("available_asof_time is required")
        if not self.dtype:
            raise ValueError("dtype is required")
        if not self.description:
            raise ValueError("description is required")
        if self.uses_future_data:
            raise ValueError("feature catalog rows must not use future data")
        if not self.is_model_feature and not self.is_audit_field:
            raise ValueError("feature must be either a model feature or an audit field")
        if self.normalization_type is FeatureNormalization.RAW_AUDIT_ONLY and self.is_model_feature:
            raise ValueError("raw absolute values may be audit fields only, not model features")
        if self.missing_policy == FeatureMissingPolicy.AUDIT_ONLY_NULLABLE and self.is_model_feature:
            raise ValueError("audit-only missing policy is not valid for model features")


@dataclass(frozen=True, slots=True)
class AnomalyFeatureMatrixRow:
    feature_schema_version: str
    feature_matrix_version: str
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    minutes_since_trigger: int
    ATR_1d_asof_t: float | None
    ATR_1d_pct_asof_t: float | None
    current_return_from_start: float
    range_since_start_atr: float | None
    distance_to_running_high_atr: float | None
    distance_to_running_low_atr: float | None
    retracement_from_high_atr: float | None
    price_speed_atr: float | None
    clock_maturity: float
    event_age_ratio: float
    alpha_decay_bucket: str
    feature_source_status: str
    quote_volume_1m_to_24h_median: float | None = None
    volume_zscore: float | None = None
    quote_volume_zscore: float | None = None
    closed_5m_oi_asof_t: float | None = None
    oi_change_5m: float | None = None
    oi_change_10m: float | None = None
    oi_change_5m_pct_of_oi: float | None = None
    oi_change_10m_pct_of_oi: float | None = None
    missing_oi_flag: bool = True
    short_liq_intensity: float | None = None
    long_liq_intensity: float | None = None
    liquidation_imbalance: float | None = None
    cumulative_liq_intensity_since_event_start: float | None = None
    missing_liquidation_flag: bool = True
    cvd_quote_since_event_start: float | None = None
    cvd_change_3m: float | None = None
    cvd_change_5m: float | None = None
    cvd_change_10m: float | None = None
    cvd_price_divergence_3m: float | None = None
    cvd_price_divergence_5m: float | None = None
    cvd_price_divergence_10m: float | None = None
    price_up_cvd_down_flag: bool = False
    price_down_cvd_up_flag: bool = False
    cvd_failed_to_confirm_high_flag: bool = False

    def __post_init__(self) -> None:
        if not self.feature_schema_version:
            raise MarketDataContractError("feature_schema_version is required")
        if not self.feature_matrix_version:
            raise MarketDataContractError("feature_matrix_version is required")
        if not self.event_id:
            raise MarketDataContractError("event_id is required")
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        validate_timestamp_ms(self.snapshot_time_ms, field_name="snapshot_time_ms")
        validate_timestamp_ms(self.feature_cutoff_time_ms, field_name="feature_cutoff_time_ms")
        enforce_snapshot_contract(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
        )
        if self.minutes_since_trigger < 0:
            raise MarketDataContractError("minutes_since_trigger must be non-negative")
        for field_name in (
            "ATR_1d_asof_t",
            "ATR_1d_pct_asof_t",
            "range_since_start_atr",
            "distance_to_running_high_atr",
            "distance_to_running_low_atr",
            "retracement_from_high_atr",
            "price_speed_atr",
            "quote_volume_1m_to_24h_median",
            "volume_zscore",
            "quote_volume_zscore",
            "closed_5m_oi_asof_t",
            "oi_change_5m",
            "oi_change_10m",
            "oi_change_5m_pct_of_oi",
            "oi_change_10m_pct_of_oi",
            "short_liq_intensity",
            "long_liq_intensity",
            "liquidation_imbalance",
            "cumulative_liq_intensity_since_event_start",
            "cvd_quote_since_event_start",
            "cvd_change_3m",
            "cvd_change_5m",
            "cvd_change_10m",
            "cvd_price_divergence_3m",
            "cvd_price_divergence_5m",
            "cvd_price_divergence_10m",
        ):
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(value):
                raise MarketDataContractError(f"{field_name} must be finite when present")
        if self.ATR_1d_asof_t is not None and self.ATR_1d_asof_t <= 0:
            raise MarketDataContractError("ATR_1d_asof_t must be positive when present")
        if self.ATR_1d_pct_asof_t is not None and self.ATR_1d_pct_asof_t <= 0:
            raise MarketDataContractError("ATR_1d_pct_asof_t must be positive when present")
        for field_name in (
            "range_since_start_atr",
            "distance_to_running_high_atr",
            "distance_to_running_low_atr",
            "retracement_from_high_atr",
            "clock_maturity",
            "event_age_ratio",
            "quote_volume_1m_to_24h_median",
            "closed_5m_oi_asof_t",
            "short_liq_intensity",
            "long_liq_intensity",
            "cumulative_liq_intensity_since_event_start",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise MarketDataContractError(f"{field_name} must be non-negative")
        if not self.alpha_decay_bucket:
            raise MarketDataContractError("alpha_decay_bucket is required")
        if self.alpha_decay_bucket not in {"0-2m", "3-5m", "6-10m", "11-20m", "21-40m", ">40m"}:
            raise MarketDataContractError("alpha_decay_bucket must be a pre-registered bucket")
        if not self.feature_source_status:
            raise MarketDataContractError("feature_source_status is required")
        if self.liquidation_imbalance is not None and not -1.0 <= self.liquidation_imbalance <= 1.0:
            raise MarketDataContractError("liquidation_imbalance must be inside [-1, 1]")
