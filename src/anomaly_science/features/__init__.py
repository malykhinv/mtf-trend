from __future__ import annotations

from anomaly_science.features.catalog import (
    FEATURE_SCHEMA_VERSION,
    build_default_feature_catalog,
    feature_rows_to_artifact,
    run_mvp1_features,
    validate_relative_over_absolute_contract,
)
from anomaly_science.features.config import FeatureMatrixConfig
from anomaly_science.features.matrix import (
    AnomalyFeatureMatrixArtifactError,
    StrategyFeatureMatrixArtifactError,
    alpha_decay_bucket,
    build_price_time_feature_matrix,
    build_price_time_feature_matrix_from_source,
    feature_matrix_rows_to_artifact,
    iter_strategy_feature_matrix_frame_chunks_prefer_parquet,
    load_anomaly_feature_matrix_csv,
    load_strategy_feature_matrix_csv,
    run_mvp1_feature_matrix,
)

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "FeatureMatrixConfig",
    "AnomalyFeatureMatrixArtifactError",
    "StrategyFeatureMatrixArtifactError",
    "alpha_decay_bucket",
    "build_default_feature_catalog",
    "build_price_time_feature_matrix",
    "build_price_time_feature_matrix_from_source",
    "feature_matrix_rows_to_artifact",
    "iter_strategy_feature_matrix_frame_chunks_prefer_parquet",
    "load_anomaly_feature_matrix_csv",
    "load_strategy_feature_matrix_csv",
    "feature_rows_to_artifact",
    "run_mvp1_feature_matrix",
    "run_mvp1_features",
    "validate_relative_over_absolute_contract",
]
