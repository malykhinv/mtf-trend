from __future__ import annotations

import csv
from pathlib import Path

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus
from anomaly_science.contracts.features import FeatureFamily, FeatureNormalization
from anomaly_science.features import (
    FEATURE_SCHEMA_VERSION,
    build_default_feature_catalog,
    feature_rows_to_artifact,
    run_mvp1_features,
    validate_relative_over_absolute_contract,
)


def test_feature_catalog_schema_matches_methodology_contract() -> None:
    schema = get_artifact_schema("anomaly_feature_catalog.csv")

    assert schema.required_columns == (
        "feature_schema_version",
        "feature_name",
        "feature_family",
        "source_artifact",
        "available_asof_time",
        "uses_future_data",
        "normalization_type",
        "is_model_feature",
        "is_audit_field",
        "missing_policy",
        "dtype",
        "description",
    )


def test_default_feature_catalog_enforces_relative_over_absolute() -> None:
    rows = build_default_feature_catalog()

    assert rows
    assert {row.feature_schema_version for row in rows} == {FEATURE_SCHEMA_VERSION}
    assert validate_relative_over_absolute_contract(rows) == []
    assert all(not row.uses_future_data for row in rows)
    assert all(row.is_model_feature or row.is_audit_field for row in rows)
    assert all(
        row.normalization_type is not FeatureNormalization.RAW_AUDIT_ONLY
        for row in rows
        if row.is_model_feature
    )
    feature_names = {row.feature_name for row in rows}
    assert "core_atr_1440" in feature_names
    assert "ATR_1d_asof_t" in feature_names
    assert {
        FeatureFamily.PRICE_PATH,
        FeatureFamily.SPEED_TIME,
        FeatureFamily.VOLUME,
        FeatureFamily.OPEN_INTEREST,
        FeatureFamily.LIQUIDATION,
        FeatureFamily.CVD_DIVERGENCE,
        FeatureFamily.CROSS_SECTIONAL,
        FeatureFamily.SIGNAL_CLUSTERING,
        FeatureFamily.MARKET_CONTEXT,
        FeatureFamily.DATA_QUALITY,
    }.issubset({row.feature_family for row in rows})


def test_feature_rows_to_artifact_uses_strict_schema_names() -> None:
    payload = feature_rows_to_artifact(build_default_feature_catalog())
    schema = get_artifact_schema("anomaly_feature_catalog.csv")

    assert payload
    assert set(payload[0]) == set(schema.required_columns)
    assert {row["uses_future_data"] for row in payload} == {False}
    assert "raw_audit_only" in {row["normalization_type"] for row in payload}
    assert "atr_normalized" in {row["normalization_type"] for row in payload}
    assert "market_relative" in {row["normalization_type"] for row in payload}


def test_run_mvp1_features_writes_catalog_and_audit(tmp_path: Path) -> None:
    out = run_mvp1_features(out_dir=tmp_path)

    catalog_path = out / "anomaly_feature_catalog.csv"
    audit_path = out / "anomaly_protocol_audit.csv"
    run_config_path = out / "anomaly_run_config.csv"
    manifest_path = out / "artifact_manifest.json"

    assert catalog_path.is_file()
    assert audit_path.is_file()
    assert run_config_path.is_file()
    assert manifest_path.is_file()

    with catalog_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        catalog_rows = list(csv.DictReader(file_obj))
    assert catalog_rows
    assert catalog_rows[0]["feature_schema_version"] == FEATURE_SCHEMA_VERSION

    with audit_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        audit_rows = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    relative = audit_rows["relative_over_absolute_feature_contract_enforced"]
    assert relative["status"] == AuditStatus.PASS.value
    assert relative["artifact"] == "anomaly_feature_catalog.csv"
