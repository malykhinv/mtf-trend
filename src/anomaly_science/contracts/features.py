from __future__ import annotations

import math
import json
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
class StrategyFeatureMatrixRow:
    feature_schema_version: str
    feature_matrix_version: str
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    minutes_since_trigger: int
    core_atr_1440: float | None
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
    volume_market_percentile: float | None = None
    quote_volume_market_percentile: float | None = None
    return_1m_market_percentile: float | None = None
    return_from_event_market_percentile: float | None = None
    oi_growth_market_percentile: float | None = None
    liq_intensity_market_percentile: float | None = None
    range_expansion_market_percentile: float | None = None
    cross_section_available: bool = False
    cross_section_symbol_count: int = 0
    corr_with_btc_15m: float | None = None
    corr_with_btc_30m: float | None = None
    corr_with_btc_60m: float | None = None
    symbol_return_minus_btc_return_5m: float | None = None
    symbol_return_minus_btc_return_15m: float | None = None
    idiosyncratic_momentum_score: float | None = None
    simultaneous_anomalies_count_1m: int = 0
    simultaneous_anomalies_share_1m: float | None = None
    systemic_cluster_regime: str = "unknown"
    market_shock_id: str = "unknown"
    initial_pump_height_core_atr_1440: float | None = None
    post_pump_consolidation_minutes: int | None = None
    consolidation_width_ratio: float | None = None
    shelf_low_asof_t: float | None = None
    shelf_high_asof_t: float | None = None
    current_low_minus_shelf_low_core_atr_1440: float | None = None
    current_close_minus_shelf_low_core_atr_1440: float | None = None
    current_high_minus_shelf_high_core_atr_1440: float | None = None
    minutes_spent_below_shelf: int | None = None
    minutes_since_reclaim: int | None = None
    volume_on_sweep_percentile: float | None = None
    trade_count_on_sweep_percentile: float | None = None
    cvd_change_during_sweep: float | None = None
    oi_change_during_sweep: float | None = None
    liq_intensity_during_sweep: float | None = None
    custom_features_json: str = "{}"

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
            "core_atr_1440",
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
            "initial_pump_height_core_atr_1440",
            "cvd_quote_since_event_start",
            "cvd_change_3m",
            "cvd_change_5m",
            "cvd_change_10m",
            "cvd_price_divergence_3m",
            "cvd_price_divergence_5m",
            "cvd_price_divergence_10m",
            "volume_market_percentile",
            "quote_volume_market_percentile",
            "return_1m_market_percentile",
            "return_from_event_market_percentile",
            "oi_growth_market_percentile",
            "liq_intensity_market_percentile",
            "range_expansion_market_percentile",
            "corr_with_btc_15m",
            "corr_with_btc_30m",
            "corr_with_btc_60m",
            "symbol_return_minus_btc_return_5m",
            "symbol_return_minus_btc_return_15m",
            "idiosyncratic_momentum_score",
            "simultaneous_anomalies_share_1m",
            "initial_pump_height_core_atr_1440",
            "consolidation_width_ratio",
            "shelf_low_asof_t",
            "shelf_high_asof_t",
            "current_low_minus_shelf_low_core_atr_1440",
            "current_close_minus_shelf_low_core_atr_1440",
            "current_high_minus_shelf_high_core_atr_1440",
            "volume_on_sweep_percentile",
            "trade_count_on_sweep_percentile",
            "cvd_change_during_sweep",
            "oi_change_during_sweep",
            "liq_intensity_during_sweep",
        ):
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(value):
                raise MarketDataContractError(f"{field_name} must be finite when present")
        if self.core_atr_1440 is not None and self.core_atr_1440 <= 0:
            raise MarketDataContractError("core_atr_1440 must be positive when present")
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
        for field_name in ("post_pump_consolidation_minutes", "minutes_spent_below_shelf", "minutes_since_reclaim"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise MarketDataContractError(f"{field_name} must be non-negative")
        for field_name in ("volume_on_sweep_percentile", "trade_count_on_sweep_percentile"):
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{field_name} must be within [0, 1] when present")
        if not self.alpha_decay_bucket:
            raise MarketDataContractError("alpha_decay_bucket is required")
        if self.alpha_decay_bucket not in {"0-2m", "3-5m", "6-10m", "11-20m", "21-40m", ">40m"}:
            raise MarketDataContractError("alpha_decay_bucket must be a pre-registered bucket")
        if not self.feature_source_status:
            raise MarketDataContractError("feature_source_status is required")
        try:
            custom_features = json.loads(self.custom_features_json)
        except json.JSONDecodeError as exc:
            raise MarketDataContractError("custom_features_json must be valid JSON") from exc
        if not isinstance(custom_features, dict):
            raise MarketDataContractError("custom_features_json must contain an object")
        if self.liquidation_imbalance is not None and not -1.0 <= self.liquidation_imbalance <= 1.0:
            raise MarketDataContractError("liquidation_imbalance must be inside [-1, 1]")
        for field_name in (
            "volume_market_percentile",
            "quote_volume_market_percentile",
            "return_1m_market_percentile",
            "return_from_event_market_percentile",
            "oi_growth_market_percentile",
            "liq_intensity_market_percentile",
            "range_expansion_market_percentile",
            "simultaneous_anomalies_share_1m",
        ):
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise MarketDataContractError(f"{field_name} must be inside [0, 1]")
        for field_name in ("corr_with_btc_15m", "corr_with_btc_30m", "corr_with_btc_60m"):
            value = getattr(self, field_name)
            if value is not None and not -1.0 <= value <= 1.0:
                raise MarketDataContractError(f"{field_name} must be inside [-1, 1]")
        if self.idiosyncratic_momentum_score is not None and self.idiosyncratic_momentum_score < 0:
            raise MarketDataContractError("idiosyncratic_momentum_score must be non-negative")
        if self.simultaneous_anomalies_count_1m < 0:
            raise MarketDataContractError("simultaneous_anomalies_count_1m must be non-negative")
        if self.systemic_cluster_regime not in {"unknown", "idiosyncratic", "moderate_cluster", "systemic_beta_shock"}:
            raise MarketDataContractError("systemic_cluster_regime must be a pre-registered bucket")
        if not self.market_shock_id:
            raise MarketDataContractError("market_shock_id is required")
        if self.cross_section_symbol_count < 0:
            raise MarketDataContractError("cross_section_symbol_count must be non-negative")
        if not self.cross_section_available:
            for field_name in (
                "volume_market_percentile",
                "quote_volume_market_percentile",
                "return_1m_market_percentile",
                "oi_growth_market_percentile",
                "liq_intensity_market_percentile",
                "range_expansion_market_percentile",
            ):
                if getattr(self, field_name) is not None:
                    raise MarketDataContractError(
                        f"{field_name} must be null when cross_section_available is false"
                    )


AnomalyFeatureMatrixRow = StrategyFeatureMatrixRow
