from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.decision import EXPECTED_VALUE_TEMPORAL_CONTRACT
from anomaly_science.contracts.labels import TEMPORAL_LABEL_CONTRACT, AnomalyOutcomeLabelRow
from anomaly_science.contracts.prediction import PREDICTION_TEMPORAL_CONTRACT, OosPredictionRow
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.decision import (
    ExpectedValueConfig,
    build_expected_value_rows,
    expected_value_rows_to_artifact,
    load_anomaly_decision_timing_csv,
)
from anomaly_science.labels import outcome_label_rows_to_artifact
from anomaly_science.prediction import oos_prediction_rows_to_artifact
from anomaly_science.state import state_rows_to_artifact

BASE_MS = 1_704_067_200_000
ONE_MINUTE_MS = 60_000


def _state(event_id: str, *, price: float = 100.0) -> AnomalyState1mRow:
    return AnomalyState1mRow(
        event_id=event_id,
        symbol="AAA/USDT:USDT",
        state_time_ms=BASE_MS,
        snapshot_time_ms=BASE_MS,
        feature_cutoff_time_ms=BASE_MS,
        minutes_since_event_start=4,
        minutes_since_detection=2,
        event_alive=True,
        running_high_asof_t=103.0,
        running_high_time_asof_t_ms=BASE_MS,
        running_low_asof_t=99.0,
        running_low_time_asof_t_ms=BASE_MS - ONE_MINUTE_MS,
        time_since_running_high_minutes=0,
        current_close=price,
        current_return_from_start=0.02,
        distance_to_running_high=-0.0005,
        distance_to_running_low=0.03,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )


def _label(state: AnomalyState1mRow) -> AnomalyOutcomeLabelRow:
    return AnomalyOutcomeLabelRow(
        label_schema_version="atr_outcome_labels_v1",
        atr_window_minutes=1440,
        core_atr_1440=2.0,
        k_continuation=1.5,
        k_fade=1.0,
        k_chop=0.25,
        event_id=state.event_id,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        feature_cutoff_time_ms=state.feature_cutoff_time_ms,
        future_start_time_ms=state.snapshot_time_ms + ONE_MINUTE_MS,
        scenario_15m="long_continuation",
        scenario_30m="long_continuation",
        scenario_60m="long_continuation",
        scenario_120m="long_continuation",
        label_available_15m=True,
        label_available_30m=True,
        label_available_60m=True,
        label_available_120m=True,
        label_source="atr_normalized_future_paths_only",
        temporal_contract=TEMPORAL_LABEL_CONTRACT,
    )


def _prediction(state: AnomalyState1mRow, *, p_long: float, p_short: float) -> OosPredictionRow:
    p_static = 1.0 - p_long - p_short
    return OosPredictionRow(
        prediction_version="mvp1_weekly_walk_forward_calibrated_baseline_v1",
        event_id=state.event_id,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        feature_cutoff_time_ms=state.feature_cutoff_time_ms,
        future_start_time_ms=state.snapshot_time_ms + ONE_MINUTE_MS,
        target_horizon_minutes=30,
        target_scenario="long_continuation",
        test_day="2024-01-01",
        train_cutoff_time_ms=BASE_MS - 60 * ONE_MINUTE_MS,
        model_family="empirical_state_bins",
        model_key="weekly_freeze=2024-W01:1704067200000|global_prior",
        model_train_row_count=10,
        model_group_row_count=10,
        raw_p_long_continuation=p_long,
        raw_p_short_fade=p_short,
        raw_p_static_or_chop=p_static,
        raw_p_unclear=0.0,
        p_long_continuation=p_long,
        p_short_fade=p_short,
        p_static_or_chop=p_static,
        p_unclear=0.0,
        predicted_scenario="long_continuation",
        prediction_confidence=max(p_long, p_short, p_static),
        temporal_contract=PREDICTION_TEMPORAL_CONTRACT,
    )


def test_expected_value_uses_oos_probabilities_atr_and_costs() -> None:
    state = _state("ev_long", price=100.0)
    rows = build_expected_value_rows(
        state_rows=[state],
        label_rows=[_label(state)],
        prediction_rows=[_prediction(state, p_long=0.8, p_short=0.1)],
        config=ExpectedValueConfig(fee_bps=4.0, slippage_bps=2.0, min_prediction_confidence=0.7),
    )

    row = rows[0]

    assert row.target_distance == 4.0
    assert row.stop_distance == 2.2
    assert row.cost_penalty == 0.1
    assert round(row.EV_long, 10) == 2.88
    assert round(row.EV_short, 10) == -1.46
    assert row.best_action == "long"
    assert row.is_prediction_confident is True
    assert row.is_RR_still_acceptable is True
    assert row.strategy_name == "broad_anomaly_v1_h30"
    assert row.strategy_version == "1.0.0"
    assert row.temporal_contract == EXPECTED_VALUE_TEMPORAL_CONTRACT


def test_expected_value_artifact_roundtrip(tmp_path: Path) -> None:
    state = _state("roundtrip")
    rows = build_expected_value_rows(
        state_rows=[state],
        label_rows=[_label(state)],
        prediction_rows=[_prediction(state, p_long=0.7, p_short=0.2)],
    )
    path = tmp_path / "anomaly_decision_timing.csv"
    write_csv_artifact(path, expected_value_rows_to_artifact(rows), get_artifact_schema("anomaly_decision_timing.csv"))

    loaded = load_anomaly_decision_timing_csv(path)

    assert len(loaded) == len(rows)
    loaded_payload = asdict(loaded[0])
    expected_payload = asdict(rows[0])
    for key, expected_value in expected_payload.items():
        loaded_value = loaded_payload[key]
        if isinstance(expected_value, float):
            assert loaded_value == pytest.approx(expected_value)
        else:
            assert loaded_value == expected_value


def test_expected_value_prefers_no_trade_when_directional_ev_is_not_positive() -> None:
    state = _state("no_trade", price=100.0)
    rows = build_expected_value_rows(
        state_rows=[state],
        label_rows=[_label(state)],
        prediction_rows=[_prediction(state, p_long=0.25, p_short=0.25)],
        config=ExpectedValueConfig(fee_bps=50.0, slippage_bps=0.0),
    )

    assert rows[0].EV_long < 0.0
    assert rows[0].EV_short < 0.0
    assert rows[0].best_action == "no_trade"


def test_run_mvp1_expected_value_cli_writes_artifacts(tmp_path: Path) -> None:
    state = _state("cli")
    label = _label(state)
    prediction = _prediction(state, p_long=0.8, p_short=0.1)
    state_path = tmp_path / "anomaly_state_1m.csv"
    labels_path = tmp_path / "anomaly_outcome_labels.csv"
    predictions_path = tmp_path / "anomaly_oos_predictions.csv"
    out_dir = tmp_path / "ev"
    write_csv_artifact(state_path, state_rows_to_artifact([state]), get_artifact_schema("anomaly_state_1m.csv"))
    write_csv_artifact(labels_path, outcome_label_rows_to_artifact([label]), get_artifact_schema("anomaly_outcome_labels.csv"))
    write_csv_artifact(predictions_path, oos_prediction_rows_to_artifact([prediction]), get_artifact_schema("anomaly_oos_predictions.csv"))

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-expected-value",
            "--state",
            str(state_path),
            "--labels",
            str(labels_path),
            "--predictions",
            str(predictions_path),
            "--out",
            str(out_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 expected-value artifacts written" in result.stdout
    assert (out_dir / "anomaly_decision_timing.csv").is_file()
    assert (out_dir / "strategy_decision_timing.csv").is_file()
    assert (out_dir / "anomaly_ev_metrics.csv").is_file()
    assert (out_dir / "anomaly_protocol_audit.csv").is_file()
    assert (out_dir / "anomaly_run_config.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "anomaly_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["expected_value_computed_before_trade_simulation"]["status"] == "PASS"
    assert audit_by_name["fixed_percent_stop_target_forbidden"]["status"] == "PASS"
    assert audit_by_name["protocol_interpretation_gate"]["status"] == "PASS"

    with (out_dir / "anomaly_run_config.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        run_config = {row["key"]: row["value"] for row in csv.DictReader(file_obj)}
    assert run_config["strategy_name"] == "broad_anomaly_v1_h30"
    assert run_config["take_profit_atr_1440"] == "2.0"
