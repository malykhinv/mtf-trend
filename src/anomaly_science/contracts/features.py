from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
