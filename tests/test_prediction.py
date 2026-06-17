from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.labels import TEMPORAL_LABEL_CONTRACT, AnomalyOutcomeLabelRow
from anomaly_science.contracts.prediction import PREDICTION_TEMPORAL_CONTRACT
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.prediction import (
    PredictionArtifactError,
    WalkForwardPredictionConfig,
    build_prediction_inputs,
    build_prediction_metric_rows,
    build_walk_forward_predictions,
    load_anomaly_oos_predictions_csv,
    oos_prediction_rows_to_artifact,
)
from anomaly_science.state import state_rows_to_artifact

BASE_DAY_MS = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC
ONE_MINUTE_MS = 60_000
ONE_DAY_MS = 86_400_000


def _state_row(
    *,
    event_id: str,
    day_offset: int,
    minute_of_day: int,
    symbol: str = "AAA/USDT:USDT",
    current_return_from_start: float = 0.02,
    distance_to_running_high: float = -0.0005,
) -> AnomalyState1mRow:
    snapshot_time_ms = BASE_DAY_MS + day_offset * ONE_DAY_MS + minute_of_day * ONE_MINUTE_MS
    return AnomalyState1mRow(
        event_id=event_id,
        symbol=symbol,
        state_time_ms=snapshot_time_ms,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_event_start=4,
        minutes_since_detection=2,
        event_alive=True,
        running_high_asof_t=103.0,
        running_high_time_asof_t_ms=snapshot_time_ms,
        running_low_asof_t=99.0,
        running_low_time_asof_t_ms=snapshot_time_ms - ONE_MINUTE_MS,
        time_since_running_high_minutes=0,
        current_close=102.0,
        current_return_from_start=current_return_from_start,
        distance_to_running_high=distance_to_running_high,
        distance_to_running_low=0.03,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )


def _label_row(
    *,
    state: AnomalyState1mRow,
    scenario_30m: str,
    scenario_15m: str | None = None,
    scenario_60m: str | None = None,
) -> AnomalyOutcomeLabelRow:
    scenario_15m = scenario_15m or scenario_30m
    scenario_60m = scenario_60m or scenario_30m
    return AnomalyOutcomeLabelRow(
        label_policy_version="mvp1_outcome_labels_v1",
        event_id=state.event_id,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        feature_cutoff_time_ms=state.feature_cutoff_time_ms,
        future_start_time_ms=state.snapshot_time_ms + ONE_MINUTE_MS,
        scenario_15m=scenario_15m,
        scenario_30m=scenario_30m,
        scenario_60m=scenario_60m,
        label_available_15m=scenario_15m != "missing_future",
        label_available_30m=scenario_30m != "missing_future",
        label_available_60m=scenario_60m != "missing_future",
        label_source="raw_future_paths_only",
        temporal_contract=TEMPORAL_LABEL_CONTRACT,
    )


def _training_and_test_rows() -> tuple[list[AnomalyState1mRow], list[AnomalyOutcomeLabelRow]]:
    states = [
        _state_row(event_id="train_long_1", day_offset=0, minute_of_day=10),
        _state_row(event_id="train_long_2", day_offset=0, minute_of_day=20),
        _state_row(event_id="train_long_3", day_offset=0, minute_of_day=30),
        _state_row(event_id="purged_short_1", day_offset=0, minute_of_day=23 * 60 + 30),
        _state_row(event_id="purged_short_2", day_offset=0, minute_of_day=23 * 60 + 40),
        _state_row(event_id="purged_short_3", day_offset=0, minute_of_day=23 * 60 + 50),
        _state_row(event_id="test_row", day_offset=1, minute_of_day=60),
    ]
    labels = [
        _label_row(state=states[0], scenario_30m="long_continuation"),
        _label_row(state=states[1], scenario_30m="long_continuation"),
        _label_row(state=states[2], scenario_30m="long_continuation"),
        _label_row(state=states[3], scenario_30m="short_fade"),
        _label_row(state=states[4], scenario_30m="short_fade"),
        _label_row(state=states[5], scenario_30m="short_fade"),
        _label_row(state=states[6], scenario_30m="long_continuation"),
    ]
    return states, labels


def _write_state(path: Path, rows: list[AnomalyState1mRow]) -> None:
    write_csv_artifact(path, state_rows_to_artifact(rows), get_artifact_schema("anomaly_state_1m.csv"))


def _write_labels(path: Path, rows: list[AnomalyOutcomeLabelRow]) -> None:
    write_csv_artifact(path, [asdict(row) for row in rows], get_artifact_schema("anomaly_outcome_labels.csv"))


def test_walk_forward_prediction_enforces_purge_before_test_day() -> None:
    states, labels = _training_and_test_rows()
    inputs = build_prediction_inputs(state_rows=states, label_rows=labels)
    predictions = build_walk_forward_predictions(
        inputs=inputs,
        config=WalkForwardPredictionConfig(min_train_rows=1, min_group_rows=1, smoothing_strength=1.0),
    )

    assert len(predictions) == 1
    row = predictions[0]
    assert row.event_id == "test_row"
    assert row.train_cutoff_time_ms == BASE_DAY_MS + ONE_DAY_MS - 60 * ONE_MINUTE_MS
    assert row.model_train_row_count == 3
    assert row.p_long_continuation > row.p_short_fade
    assert row.predicted_scenario == "long_continuation"
    assert row.temporal_contract == PREDICTION_TEMPORAL_CONTRACT


def test_missing_future_is_excluded_from_prediction_metrics() -> None:
    train = _state_row(event_id="train_long", day_offset=0, minute_of_day=10)
    missing = _state_row(event_id="missing", day_offset=1, minute_of_day=60)
    test = _state_row(event_id="test_long", day_offset=1, minute_of_day=90)
    states = [train, missing, test]
    labels = [
        _label_row(state=train, scenario_30m="long_continuation"),
        _label_row(state=missing, scenario_30m="missing_future"),
        _label_row(state=test, scenario_30m="long_continuation"),
    ]
    config = WalkForwardPredictionConfig(min_train_rows=1, min_group_rows=1)
    inputs = build_prediction_inputs(state_rows=states, label_rows=labels)
    predictions = build_walk_forward_predictions(inputs=inputs, config=config)
    metrics = build_prediction_metric_rows(inputs=inputs, predictions=predictions, config=config)

    excluded = [row for row in metrics if row.metric_name == "missing_future_rows_excluded"]
    assert excluded[0].metric_value == "1"
    assert len(predictions) == 1
    assert predictions[0].event_id == "test_long"


def test_oos_prediction_artifact_boundary_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "anomaly_oos_predictions.csv"
    columns = list(get_artifact_schema("anomaly_oos_predictions.csv").required_columns) + ["extra"]
    path.write_text(",".join(columns) + "\n", encoding="utf-8")

    with pytest.raises(PredictionArtifactError, match="columns must match"):
        load_anomaly_oos_predictions_csv(path)


def test_prediction_artifact_roundtrip(tmp_path: Path) -> None:
    states, labels = _training_and_test_rows()
    inputs = build_prediction_inputs(state_rows=states, label_rows=labels)
    predictions = build_walk_forward_predictions(
        inputs=inputs,
        config=WalkForwardPredictionConfig(min_train_rows=1, min_group_rows=1, smoothing_strength=1.0),
    )
    path = tmp_path / "anomaly_oos_predictions.csv"
    write_csv_artifact(path, oos_prediction_rows_to_artifact(predictions), get_artifact_schema("anomaly_oos_predictions.csv"))

    loaded = load_anomaly_oos_predictions_csv(path)

    assert loaded == predictions


def test_run_mvp1_prediction_cli_writes_prediction_artifacts(tmp_path: Path) -> None:
    states, labels = _training_and_test_rows()
    state_path = tmp_path / "anomaly_state_1m.csv"
    labels_path = tmp_path / "anomaly_outcome_labels.csv"
    out_dir = tmp_path / "prediction"
    _write_state(state_path, states)
    _write_labels(labels_path, labels)

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-prediction",
            "--state",
            str(state_path),
            "--labels",
            str(labels_path),
            "--out",
            str(out_dir),
            "--horizon-minutes",
            "30",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 walk-forward prediction artifacts written" in result.stdout
    assert (out_dir / "anomaly_oos_predictions.csv").is_file()
    assert (out_dir / "anomaly_calibration.csv").is_file()
    assert (out_dir / "anomaly_prediction_metrics.csv").is_file()
    assert (out_dir / "anomaly_protocol_audit.csv").is_file()
    assert (out_dir / "anomaly_run_config.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "anomaly_oos_predictions.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert rows[0]["event_id"] == "test_row"
