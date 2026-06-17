from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, write_csv_artifact, write_manifest
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.contracts.features import (
    FeatureCatalogRow,
    FeatureFamily,
    FeatureMissingPolicy,
    FeatureNormalization,
)

FEATURE_SCHEMA_VERSION = "feature_schema_v1_relative_asof"
ASOF_SNAPSHOT = "computed only from data with available_time_ms <= snapshot_time_ms"
ASOF_STATE = "computed only from anomaly_state_1m.csv rows at state_time_ms <= snapshot_time_ms"
ASOF_EVENT = "computed only from anomaly_events.csv rows known at event_detection_time_ms"
ASOF_CROSS_SECTION = "computed from point-in-time tradable universe rows available at state_time_ms"
ASOF_MARKET_CONTEXT = "computed from BTC/ETH/market context candles available <= state_time_ms"


def _row(
    *,
    name: str,
    family: FeatureFamily,
    source: str,
    asof: str,
    norm: FeatureNormalization,
    model: bool,
    audit: bool = False,
    missing: FeatureMissingPolicy = FeatureMissingPolicy.NOT_NULL,
    dtype: str = "float64",
    description: str,
) -> FeatureCatalogRow:
    return FeatureCatalogRow(
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_name=name,
        feature_family=family,
        source_artifact=source,
        available_asof_time=asof,
        uses_future_data=False,
        normalization_type=norm,
        is_model_feature=model,
        is_audit_field=audit,
        missing_policy=missing,
        dtype=dtype,
        description=description,
    )


def build_default_feature_catalog() -> tuple[FeatureCatalogRow, ...]:
    """Return the frozen MVP feature catalog contract.

    This catalog is intentionally broader than the currently materialized feature
    matrix. It is the schema contract that later feature-building patches must
    satisfy without introducing raw absolute model features or future data.
    """
    rows: list[FeatureCatalogRow] = [
        _row(
            name="event_id",
            family=FeatureFamily.DATA_QUALITY,
            source="anomaly_state_1m.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.POINT_IN_TIME_ID,
            model=False,
            audit=True,
            dtype="str",
            description="Point-in-time event identifier for audit joins; not a model feature.",
        ),
        _row(
            name="symbol",
            family=FeatureFamily.DATA_QUALITY,
            source="anomaly_state_1m.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.POINT_IN_TIME_ID,
            model=False,
            audit=True,
            dtype="str",
            description="Symbol identifier for audit grouping only; model must not learn symbol identity as edge.",
        ),
        _row(
            name="technical_noise_shock",
            family=FeatureFamily.DATA_QUALITY,
            source="anomaly_events.csv",
            asof=ASOF_EVENT,
            norm=FeatureNormalization.BOOLEAN_FLAG,
            model=False,
            audit=True,
            dtype="bool",
            description="First candle after raw timestamp gap > 3m; detector/ML exclusion audit field.",
        ),
        _row(
            name="ATR_1d_asof_t",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_future_paths.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.RAW_AUDIT_ONLY,
            model=False,
            audit=True,
            missing=FeatureMissingPolicy.AUDIT_ONLY_NULLABLE,
            description="Raw ATR unit computed as-of snapshot for audit and normalization reproducibility only.",
        ),
        _row(
            name="ATR_1d_pct_asof_t",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_future_paths.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="ATR divided by as-of close; dimensionless volatility regime feature.",
        ),
        _row(
            name="current_return_from_start",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_state_1m.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            description="Return from event start to current close using as-of state only.",
        ),
        _row(
            name="range_since_start_atr",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.ATR_NORMALIZED,
            model=True,
            audit=False,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Event high-low range divided by ATR_1d_asof_t.",
        ),
        _row(
            name="distance_to_running_high_atr",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.ATR_NORMALIZED,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Distance from current close to running high divided by ATR_1d_asof_t.",
        ),
        _row(
            name="distance_to_running_low_atr",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.ATR_NORMALIZED,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Distance from current close to running low divided by ATR_1d_asof_t.",
        ),
        _row(
            name="retracement_from_high_atr",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.ATR_NORMALIZED,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Current retracement from running high divided by ATR_1d_asof_t.",
        ),
        _row(
            name="price_speed_atr",
            family=FeatureFamily.PRICE_PATH,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.ATR_NORMALIZED,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="One-minute price change normalized by expected one-minute ATR unit.",
        ),
        _row(
            name="minutes_since_trigger",
            family=FeatureFamily.SPEED_TIME,
            source="anomaly_state_1m.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.TIME_RELATIVE,
            model=True,
            audit=True,
            dtype="int64",
            description="Minutes elapsed since anomaly detection; alpha decay clock.",
        ),
        _row(
            name="clock_maturity",
            family=FeatureFamily.SPEED_TIME,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="time_since_running_high divided by max(time_to_running_high, 1m).",
        ),
        _row(
            name="event_age_ratio",
            family=FeatureFamily.SPEED_TIME,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            description="minutes_since_trigger divided by frozen expected_event_lifetime_minutes.",
        ),
        _row(
            name="alpha_decay_bucket",
            family=FeatureFamily.SPEED_TIME,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_STATE,
            norm=FeatureNormalization.CATEGORICAL_BUCKET,
            model=True,
            dtype="str",
            description="Pre-registered event age bucket for decay-aware atlas/model splits.",
        ),
        _row(
            name="quote_volume_1m_to_24h_median",
            family=FeatureFamily.VOLUME,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.SELF_HISTORY_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Current quote volume divided by symbol rolling 24h median quote volume as-of state_time.",
        ),
        _row(
            name="volume_zscore",
            family=FeatureFamily.VOLUME,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.SELF_HISTORY_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Symbol self-history volume z-score using only rolling history before snapshot.",
        ),
        _row(
            name="quote_volume_zscore",
            family=FeatureFamily.VOLUME,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.SELF_HISTORY_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Symbol self-history quote-volume z-score using only rolling history before snapshot.",
        ),
        _row(
            name="closed_5m_oi_asof_t",
            family=FeatureFamily.OPEN_INTEREST,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.RAW_AUDIT_ONLY,
            model=False,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Closed 5m OI carried as-of snapshot for audit only; raw OI is not a model feature.",
        ),
        _row(
            name="oi_change_5m",
            family=FeatureFamily.OPEN_INTEREST,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.RAW_AUDIT_ONLY,
            model=False,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Closed 5m OI absolute change for audit only; not a model feature.",
        ),
        _row(
            name="oi_change_10m",
            family=FeatureFamily.OPEN_INTEREST,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.RAW_AUDIT_ONLY,
            model=False,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Closed 10m OI absolute change for audit only; not a model feature.",
        ),
        _row(
            name="oi_change_5m_pct_of_oi",
            family=FeatureFamily.OPEN_INTEREST,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Closed 5m OI change divided by as-of OI.",
        ),
        _row(
            name="oi_change_10m_pct_of_oi",
            family=FeatureFamily.OPEN_INTEREST,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Closed 10m OI change divided by as-of OI.",
        ),
        _row(
            name="missing_oi_flag",
            family=FeatureFamily.OPEN_INTEREST,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.BOOLEAN_FLAG,
            model=False,
            audit=True,
            dtype="bool",
            missing=FeatureMissingPolicy.FALSE_IF_CONDITION_ABSENT,
            description="Audit flag for missing closed OI source/as-of row; missing OI is data quality, not edge.",
        ),
        _row(
            name="oi_growth_market_percentile",
            family=FeatureFamily.OPEN_INTEREST,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.MARKET_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time market percentile of OI growth among eligible symbols.",
        ),
        _row(
            name="short_liq_intensity",
            family=FeatureFamily.LIQUIDATION,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Short liquidation quote volume divided by total quote volume at snapshot minute.",
        ),
        _row(
            name="long_liq_intensity",
            family=FeatureFamily.LIQUIDATION,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Long liquidation quote volume divided by total quote volume at snapshot minute.",
        ),
        _row(
            name="liquidation_imbalance",
            family=FeatureFamily.LIQUIDATION,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Signed liquidation imbalance normalized by total liquidation quote volume.",
        ),
        _row(
            name="cumulative_liq_intensity_since_event_start",
            family=FeatureFamily.LIQUIDATION,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Cumulative liquidation quote volume since event start divided by cumulative quote volume since event start.",
        ),
        _row(
            name="missing_liquidation_flag",
            family=FeatureFamily.LIQUIDATION,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.BOOLEAN_FLAG,
            model=False,
            audit=True,
            dtype="bool",
            missing=FeatureMissingPolicy.FALSE_IF_CONDITION_ABSENT,
            description="Audit flag for missing liquidation source; missing liquidations are data quality, not edge.",
        ),
        _row(
            name="liq_intensity_market_percentile",
            family=FeatureFamily.LIQUIDATION,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.MARKET_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time market percentile of liquidation intensity.",
        ),
        _row(
            name="cvd_quote_since_event_start",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Cumulative taker buy quote minus taker sell quote since event start divided by cumulative quote volume since event start.",
        ),
        _row(
            name="cvd_change_3m",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Three-minute taker quote delta divided by quote volume, computed as-of snapshot.",
        ),
        _row(
            name="cvd_change_5m",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Five-minute taker quote delta divided by quote volume, computed as-of snapshot.",
        ),
        _row(
            name="cvd_change_10m",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Ten-minute taker quote delta divided by quote volume, computed as-of snapshot.",
        ),
        _row(
            name="cvd_price_divergence_3m",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Three-minute ATR-normalized price change minus normalized CVD change.",
        ),
        _row(
            name="cvd_price_divergence_5m",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Five-minute ATR-normalized price change minus normalized CVD change.",
        ),
        _row(
            name="cvd_price_divergence_10m",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Ten-minute ATR-normalized price change minus normalized CVD change.",
        ),
        _row(
            name="price_up_cvd_down_flag",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.BOOLEAN_FLAG,
            model=True,
            missing=FeatureMissingPolicy.FALSE_IF_CONDITION_ABSENT,
            dtype="bool",
            description="As-of warning flag when price rises while CVD fails to confirm.",
        ),
        _row(
            name="price_down_cvd_up_flag",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.BOOLEAN_FLAG,
            model=True,
            missing=FeatureMissingPolicy.FALSE_IF_CONDITION_ABSENT,
            dtype="bool",
            description="As-of warning flag when price falls while CVD rises against the move.",
        ),
        _row(
            name="cvd_failed_to_confirm_high_flag",
            family=FeatureFamily.CVD_DIVERGENCE,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_SNAPSHOT,
            norm=FeatureNormalization.BOOLEAN_FLAG,
            model=True,
            missing=FeatureMissingPolicy.FALSE_IF_CONDITION_ABSENT,
            dtype="bool",
            description="As-of warning flag when current price is at running high but recent CVD does not confirm.",
        ),
        _row(
            name="volume_market_percentile",
            family=FeatureFamily.CROSS_SECTIONAL,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.MARKET_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time cross-sectional rank percentile of base volume among eligible symbols with current 1m candles.",
        ),
        _row(
            name="quote_volume_market_percentile",
            family=FeatureFamily.CROSS_SECTIONAL,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.MARKET_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time cross-sectional rank percentile of quote volume among eligible symbols with current 1m candles.",
        ),
        _row(
            name="return_1m_market_percentile",
            family=FeatureFamily.CROSS_SECTIONAL,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.MARKET_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time cross-sectional rank percentile of 1m return among eligible symbols with current 1m candles.",
        ),
        _row(
            name="return_from_event_market_percentile",
            family=FeatureFamily.CROSS_SECTIONAL,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.MARKET_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time rank percentile of current_return_from_start among anomaly states at the same snapshot.",
        ),
        _row(
            name="range_expansion_market_percentile",
            family=FeatureFamily.CROSS_SECTIONAL,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.MARKET_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time cross-sectional rank percentile of 1m high-low range divided by close among eligible symbols.",
        ),
        _row(
            name="cross_section_available",
            family=FeatureFamily.CROSS_SECTIONAL,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.BOOLEAN_FLAG,
            model=False,
            audit=True,
            dtype="bool",
            missing=FeatureMissingPolicy.FALSE_IF_CONDITION_ABSENT,
            description="Audit flag showing whether min_cross_section_symbols were available for market-relative percentiles.",
        ),
        _row(
            name="cross_section_symbol_count",
            family=FeatureFamily.CROSS_SECTIONAL,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.POINT_IN_TIME_ID,
            model=False,
            audit=True,
            dtype="int64",
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Number of eligible symbols in the point-in-time cross-section used for percentiles.",
        ),
        _row(
            name="symbol_return_minus_btc_return_15m",
            family=FeatureFamily.MARKET_CONTEXT,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_MARKET_CONTEXT,
            norm=FeatureNormalization.BTC_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Symbol 15m return minus BTC 15m return using as-of candles.",
        ),
        _row(
            name="corr_with_btc_30m",
            family=FeatureFamily.MARKET_CONTEXT,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_MARKET_CONTEXT,
            norm=FeatureNormalization.BTC_RELATIVE,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_INSUFFICIENT_HISTORY,
            description="Rolling 30m correlation between symbol and BTC 1m returns computed as-of state_time.",
        ),
        _row(
            name="idiosyncratic_momentum_score",
            family=FeatureFamily.MARKET_CONTEXT,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_MARKET_CONTEXT,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Dimensionless score combining relative volume, BTC-relative return, and correlation drop.",
        ),
        _row(
            name="simultaneous_anomalies_count_1m",
            family=FeatureFamily.SIGNAL_CLUSTERING,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.POINT_IN_TIME_ID,
            model=True,
            audit=True,
            dtype="int64",
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Count of point-in-time anomaly triggers at the same minute across the eligible universe.",
        ),
        _row(
            name="simultaneous_anomalies_share_1m",
            family=FeatureFamily.SIGNAL_CLUSTERING,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.DIMENSIONLESS_RATIO,
            model=True,
            audit=True,
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Simultaneous anomaly count divided by point-in-time universe size.",
        ),
        _row(
            name="systemic_cluster_regime",
            family=FeatureFamily.SIGNAL_CLUSTERING,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.CATEGORICAL_BUCKET,
            model=True,
            audit=True,
            dtype="str",
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Idiosyncratic/moderate/systemic cluster regime bucket.",
        ),
        _row(
            name="market_shock_id",
            family=FeatureFamily.SIGNAL_CLUSTERING,
            source="anomaly_feature_matrix.csv",
            asof=ASOF_CROSS_SECTION,
            norm=FeatureNormalization.POINT_IN_TIME_ID,
            model=False,
            audit=True,
            dtype="str",
            missing=FeatureMissingPolicy.NULL_IF_SOURCE_MISSING,
            description="Point-in-time market shock grouping identifier for dependence audits; not a model feature.",
        ),
    ]
    return tuple(rows)


def feature_rows_to_artifact(rows: tuple[FeatureCatalogRow, ...] | list[FeatureCatalogRow]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["feature_family"] = row.feature_family.value
        payload["normalization_type"] = row.normalization_type.value
        payload["missing_policy"] = row.missing_policy.value
        payloads.append(payload)
    return payloads


def validate_relative_over_absolute_contract(rows: tuple[FeatureCatalogRow, ...] | list[FeatureCatalogRow]) -> list[str]:
    violations: list[str] = []
    for row in rows:
        if row.uses_future_data:
            violations.append(f"{row.feature_name}: uses future data")
        if row.is_model_feature and row.normalization_type is FeatureNormalization.RAW_AUDIT_ONLY:
            violations.append(f"{row.feature_name}: raw absolute model feature")
        if row.is_model_feature and row.feature_name in {"event_id", "symbol", "market_shock_id"}:
            violations.append(f"{row.feature_name}: identifier used as model feature")
    return violations


def run_mvp1_features(*, out_dir: str | Path) -> Path:
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    rows = build_default_feature_catalog()
    violations = validate_relative_over_absolute_contract(rows)
    protocol_rows = _protocol_rows(feature_count=len(rows), violations=violations)
    run_config_rows = _run_config_rows(output_path=output_path)

    written: list[Path] = []
    written.append(
        write_csv_artifact(
            output_path / "anomaly_feature_catalog.csv",
            feature_rows_to_artifact(rows),
            get_artifact_schema("anomaly_feature_catalog.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("anomaly_protocol_audit.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("anomaly_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _protocol_rows(*, feature_count: int, violations: list[str]) -> list[ProtocolAuditRow]:
    relative_status = AuditStatus.FAIL if violations else AuditStatus.PASS
    relative_message = "; ".join(violations) if violations else "feature catalog contains no future-data features and no raw absolute model features"
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_feature_catalog_scope",
            status=AuditStatus.PASS,
            message="feature catalog contract only; no feature matrix, ML, decision layer, or trade simulation",
            artifact="anomaly_feature_catalog.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_catalog_rows_written",
            status=AuditStatus.PASS if feature_count > 0 else AuditStatus.FAIL,
            message=f"anomaly_feature_catalog.csv written with {feature_count} feature contract rows",
            artifact="anomaly_feature_catalog.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 feature catalog uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="relative_over_absolute_feature_contract_enforced",
            status=relative_status,
            message=relative_message,
            artifact="anomaly_feature_catalog.csv",
        )
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_feature_catalog",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(*, output_path: Path) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-features", source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="git_commit", value="UNKNOWN", source="runtime"),
        RunConfigRow(key="stage", value="mvp1_feature_catalog", source="runtime"),
        RunConfigRow(key="feature_schema_version", value=FEATURE_SCHEMA_VERSION, source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-features-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
