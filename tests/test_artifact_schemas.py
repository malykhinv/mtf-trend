from __future__ import annotations

import json
from pathlib import Path

import pytest

from anomaly_science.artifacts import ArtifactWriteError, build_manifest, write_csv_artifact, write_manifest
from anomaly_science.contracts.artifacts import MVP1_ARTIFACT_SCHEMAS, get_artifact_schema


REQUIRED_MVP1 = {
    "anomaly_events.csv",
    "anomaly_state_1m.csv",
    "anomaly_future_paths.csv",
    "anomaly_outcome_labels.csv",
    "anomaly_oos_predictions.csv",
    "anomaly_calibration.csv",
    "anomaly_prediction_metrics.csv",
    "anomaly_placebo_tests.csv",
    "anomaly_baseline_comparison.csv",
    "anomaly_feature_catalog.csv",
    "anomaly_feature_matrix.csv",
    "anomaly_nature_atlas.csv",
    "anomaly_context_splits.csv",
    "anomaly_response_surfaces.csv",
    "anomaly_market_shock_groups.csv",
    "anomaly_data_quality.csv",
    "symbol_universe_by_day.csv",
    "anomaly_protocol_audit.csv",
    "anomaly_run_config.csv",
    "artifact_manifest.json",
}


def test_mvp1_artifact_schemas_are_fixed() -> None:
    assert set(MVP1_ARTIFACT_SCHEMAS) == REQUIRED_MVP1
    for schema in MVP1_ARTIFACT_SCHEMAS.values():
        assert schema.required_columns
        assert len(set(schema.required_columns)) == len(schema.required_columns)


def test_csv_writer_requires_declared_schema_columns(tmp_path: Path) -> None:
    schema = get_artifact_schema("anomaly_run_config.csv")
    out = tmp_path / schema.name

    write_csv_artifact(
        out,
        rows=[{"key": "git_commit", "value": "UNKNOWN", "source": "runtime"}],
        schema=schema,
    )

    text = out.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "key,value,source"

    with pytest.raises(ArtifactWriteError):
        write_csv_artifact(out, rows=[{"key": "missing_value", "source": "test"}], schema=schema)

    with pytest.raises(ArtifactWriteError):
        write_csv_artifact(
            out,
            rows=[{"key": "x", "value": "y", "source": "test", "extra": "forbidden"}],
            schema=schema,
        )


def test_manifest_records_written_artifacts(tmp_path: Path) -> None:
    schema = get_artifact_schema("anomaly_protocol_audit.csv")
    artifact = write_csv_artifact(
        tmp_path / schema.name,
        rows=[{"check_name": "temporal_contract", "status": "PASS", "message": "ok", "artifact": schema.name}],
        schema=schema,
    )
    manifest = build_manifest(run_id="run-1", artifact_paths=[artifact], root=tmp_path)
    manifest_path = write_manifest(tmp_path / "artifact_manifest.json", manifest)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1"
    assert payload["artifacts"][0]["name"] == "anomaly_protocol_audit.csv"
    assert payload["artifacts"][0]["sha256"]
