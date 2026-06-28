from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import re

import pytest

from anomaly_science.artifacts import (
    ArtifactWriteError,
    build_manifest,
    runtime_reproducibility_rows,
    write_csv_artifact,
    write_csv_artifact_with_aliases,
    write_manifest,
)
from anomaly_science.contracts.artifacts import MVP1_ARTIFACT_SCHEMAS, STRATEGY_ARTIFACT_ALIASES, get_artifact_schema


REQUIRED_MVP1 = {
    "anomaly_archetype_catalog.csv",
    "anomaly_archetype_controls.csv",
    "anomaly_archetype_coverage.csv",
    "anomaly_archetype_candidate_funnel.csv",
    "anomaly_events.csv",
    "anomaly_state_1m.csv",
    "anomaly_future_paths.csv",
    "anomaly_outcome_labels.csv",
    "anomaly_oos_predictions.csv",
    "anomaly_calibration.csv",
    "anomaly_calibration_breakdown.csv",
    "anomaly_prediction_metrics.csv",
    "strategy_model_metadata.csv",
    "strategy_feature_importance.csv",
    "strategy_model_training_diagnostics.csv",
    "anomaly_decision_timing.csv",
    "anomaly_ev_metrics.csv",
    "anomaly_trade_simulation.csv",
    "anomaly_trade_simulation_metrics.csv",
    "anomaly_placebo_tests.csv",
    "anomaly_baseline_comparison.csv",
    "anomaly_feature_catalog.csv",
    "strategy_feature_schema.csv",
    "strategy_registry.csv",
    "strategy_implementation_status.csv",
    "strategy_reject_reasons.csv",
    "anomaly_feature_matrix.csv",
    "anomaly_nature_atlas.csv",
    "anomaly_context_splits.csv",
    "anomaly_response_surfaces.csv",
    "anomaly_market_shock_groups.csv",
    "anomaly_data_quality.csv",
    "anomaly_rejection_funnel.csv",
    "symbol_universe_by_day.csv",
    "research_ledger.csv",
    "holdout_access_log.csv",
    "anomaly_protocol_audit.csv",
    "anomaly_run_config.csv",
    "anomaly_stage_timings.csv",
    "anomaly_resource_usage.csv",
    "artifact_manifest.json",
    "strategy_events.csv",
    "strategy_state_1m.csv",
    "strategy_archetype_catalog.csv",
    "strategy_archetype_controls.csv",
    "strategy_archetype_coverage.csv",
    "strategy_archetype_candidate_funnel.csv",
    "strategy_future_paths.csv",
    "strategy_outcome_labels.csv",
    "strategy_oos_predictions.csv",
    "strategy_calibration.csv",
    "strategy_calibration_breakdown.csv",
    "strategy_decision_timing.csv",
    "strategy_trade_simulation.csv",
    "strategy_feature_catalog.csv",
    "strategy_feature_matrix.csv",
    "strategy_nature_atlas.csv",
    "strategy_context_splits.csv",
    "strategy_response_surfaces.csv",
    "strategy_market_shock_groups.csv",
    "strategy_data_quality.csv",
    "strategy_rejection_funnel.csv",
    "strategy_protocol_audit.csv",
    "strategy_run_config.csv",
    "strategy_stage_timings.csv",
    "strategy_resource_usage.csv",
    "strategy_prediction_metrics.csv",
    "strategy_ev_metrics.csv",
    "strategy_trade_simulation_metrics.csv",
    "strategy_placebo_tests.csv",
    "strategy_baseline_comparison.csv",
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


def test_csv_writer_streams_iterable_rows(tmp_path: Path) -> None:
    schema = get_artifact_schema("anomaly_run_config.csv")

    def rows():
        yield {"key": "git_commit", "value": "abc123", "source": "runtime"}
        yield {"key": "stage", "value": "test", "source": "config"}

    out = write_csv_artifact(tmp_path / schema.name, rows(), schema)

    assert out.read_text(encoding="utf-8-sig").splitlines() == [
        "key,value,source",
        "git_commit,abc123,runtime",
        "stage,test,config",
    ]


def test_runtime_reproducibility_rows_include_methodology_keys(tmp_path: Path) -> None:
    input_file = tmp_path / "input.csv"
    input_file.write_text("symbol,value\nBTCUSDT,1\n", encoding="utf-8")

    rows = runtime_reproducibility_rows(
        data_paths=(input_file,),
        extra_config={"command": "test-command", "stage": "test-stage"},
    )
    by_key = {row.key: row.value for row in rows}

    assert len(by_key["data_snapshot_hash"]) == 64
    assert len(by_key["config_hash"]) == 64
    assert by_key["internal_time_type"] == "pl.Datetime[ms, UTC]"
    assert by_key["max_feature_lookback_minutes"] == "1440"
    assert by_key["trigger_deduplication_policy"] == "same_symbol_detection_time_within_strategy_horizon_suppressed"
    assert by_key["git_commit"]
    assert by_key["dependency_versions"]


def test_strategy_artifact_aliases_keep_source_columns() -> None:
    for source_name, alias_name in STRATEGY_ARTIFACT_ALIASES.items():
        source = get_artifact_schema(source_name)
        alias = get_artifact_schema(alias_name)

        assert alias.required_columns == source.required_columns
        assert alias.stage == source.stage


def test_stage_protocol_audits_name_canonical_strategy_artifacts() -> None:
    from anomaly_science.atlas import run as atlas_run
    from anomaly_science.controls import run as controls_run
    from anomaly_science.features import matrix as feature_matrix
    from anomaly_science.future import run as future_run
    from anomaly_science.labels import run as labels_run
    from anomaly_science.prediction import run as prediction_run
    from anomaly_science.simulation import run as simulation_run
    from anomaly_science.state import run as state_run

    protocol_sources = [
        inspect.getsource(module._protocol_rows)
        for module in (atlas_run, controls_run, feature_matrix, future_run, labels_run, prediction_run, simulation_run, state_run)
    ]

    for source in protocol_sources:
        assert not re.search(r"anomaly_[a-z_]+\.csv", source)


def test_csv_writer_can_write_strategy_aliases(tmp_path: Path) -> None:
    schema = get_artifact_schema("strategy_events.csv")
    rows = [
        {
            "event_id": "evt_1",
            "symbol": "BTCUSDT",
            "state_time_ms": 2,
            "event_start_time_ms": 1,
            "minutes_since_start": 0,
            "is_trigger": True,
            "event_detection_time_ms": 2,
            "seed_time_ms": 1,
            "seed_open": 1.0,
            "seed_high": 1.1,
            "seed_low": 0.9,
            "seed_close": 1.0,
            "initial_move_pct": 0.0,
            "initial_volume_zscore": "",
            "initial_quote_volume_zscore": "",
            "initial_trade_count_zscore": "",
            "trigger_component": "one_shot_spike",
            "trigger_components": "one_shot_spike",
            "technical_noise_shock": False,
            "raw_candle_gap_minutes": "",
            "excluded_by_data_quality_gate": False,
            "daily_return_asof_t": "",
            "trade_count_market_percentile_asof_t": "",
            "detector_version": "test",
        }
    ]

    written = write_csv_artifact_with_aliases(tmp_path / "strategy_events.csv", rows, schema)

    assert [path.name for path in written] == ["strategy_events.csv", "anomaly_events.csv"]
    assert (tmp_path / "anomaly_events.csv").is_file()


def test_csv_writer_skips_heavy_strategy_alias_materialization(tmp_path: Path) -> None:
    schema = get_artifact_schema("strategy_future_paths.csv")
    row = {name: "" for name in schema.required_columns}
    row.update(
        {
            "event_id": "evt_1",
            "symbol": "BTCUSDT",
            "snapshot_time_ms": 1_700_000_000_000,
            "feature_cutoff_time_ms": 1_700_000_000_000,
            "future_start_time_ms": 1_700_000_060_000,
        }
    )

    written = write_csv_artifact_with_aliases(tmp_path / "strategy_future_paths.csv", [row], schema)

    assert [path.name for path in written] == ["strategy_future_paths.csv"]
    assert not (tmp_path / "anomaly_future_paths.csv").exists()

def test_csv_writer_uses_hardlink_for_identical_strategy_aliases(tmp_path: Path) -> None:
    probe_source = tmp_path / "probe_source.txt"
    probe_alias = tmp_path / "probe_alias.txt"
    probe_source.write_text("probe", encoding="utf-8")
    try:
        os.link(probe_source, probe_alias)
    except OSError:
        pytest.skip("filesystem does not support hardlinks")

    schema = get_artifact_schema("strategy_protocol_audit.csv")
    rows = [
        {
            "check_name": "temporal_contract",
            "status": "PASS",
            "message": "ok",
            "artifact": "strategy_protocol_audit.csv",
        }
    ]

    write_csv_artifact_with_aliases(tmp_path / "strategy_protocol_audit.csv", rows, schema)

    source = tmp_path / "strategy_protocol_audit.csv"
    alias = tmp_path / "anomaly_protocol_audit.csv"
    assert source.samefile(alias)
    assert source.stat().st_nlink >= 2


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
