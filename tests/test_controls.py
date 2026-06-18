from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.controls import ControlsConfig, build_baseline_comparison_rows, build_placebo_test_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.controls import CONTROL_STATUS_DEFERRED, CONTROL_STATUS_OK
from anomaly_science.contracts.labels import TEMPORAL_LABEL_CONTRACT, AnomalyOutcomeLabelRow
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.prediction import build_prediction_inputs
from anomaly_science.state import state_rows_to_artifact

BASE_DAY_MS = 1_704_067_200_000
ONE_MINUTE_MS = 60_000
ONE_DAY_MS = 86_400_000


def _state_row(
    *,
    event_id: str,
    day_offset: int,
    minute_of_day: int,
    symbol: str,
    current_return_from_start: float,
    distance_to_running_high: float,
) -> AnomalyState1mRow:
    snapshot_time_ms = BASE_DAY_MS + day_offset * ONE_DAY_MS + minute_of_day * ONE_MINUTE_MS
    return AnomalyState1mRow(
        event_id=event_id,
        symbol=symbol,
        state_time_ms=snapshot_time_ms,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_event_start=minute_of_day % 40,
        minutes_since_detection=minute_of_day % 12,
        event_alive=True,
        running_high_asof_t=103.0,
        running_high_time_asof_t_ms=snapshot_time_ms,
        running_low_asof_t=99.0,
        running_low_time_asof_t_ms=snapshot_time_ms - ONE_MINUTE_MS,
        time_since_running_high_minutes=minute_of_day % 5,
        current_close=102.0,
        current_return_from_start=current_return_from_start,
        distance_to_running_high=distance_to_running_high,
        distance_to_running_low=0.03,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )


def _label_row(*, state: AnomalyState1mRow, scenario_30m: str) -> AnomalyOutcomeLabelRow:
    return AnomalyOutcomeLabelRow(
        label_schema_version="atr_outcome_labels_v1",
        atr_window_minutes=1440,
        ATR_1d_asof_t=2.0,
        k_continuation=1.0,
        k_fade=1.0,
        k_chop=0.25,
        event_id=state.event_id,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        feature_cutoff_time_ms=state.feature_cutoff_time_ms,
        future_start_time_ms=state.snapshot_time_ms + ONE_MINUTE_MS,
        scenario_15m=scenario_30m,
        scenario_30m=scenario_30m,
        scenario_60m=scenario_30m,
        scenario_120m=scenario_30m,
        label_available_15m=True,
        label_available_30m=True,
        label_available_60m=True,
        label_available_120m=True,
        label_source="atr_normalized_future_paths_only",
        temporal_contract=TEMPORAL_LABEL_CONTRACT,
    )


def _control_rows() -> tuple[list[AnomalyState1mRow], list[AnomalyOutcomeLabelRow]]:
    states = [
        _state_row(event_id="aaa_train_1", day_offset=-1, minute_of_day=10, symbol="AAA/USDT:USDT", current_return_from_start=0.025, distance_to_running_high=-0.0005),
        _state_row(event_id="aaa_train_2", day_offset=-1, minute_of_day=20, symbol="AAA/USDT:USDT", current_return_from_start=0.022, distance_to_running_high=-0.0004),
        _state_row(event_id="bbb_train_1", day_offset=-1, minute_of_day=30, symbol="BBB/USDT:USDT", current_return_from_start=-0.02, distance_to_running_high=-0.04),
        _state_row(event_id="bbb_train_2", day_offset=-1, minute_of_day=40, symbol="BBB/USDT:USDT", current_return_from_start=-0.018, distance_to_running_high=-0.035),
        _state_row(event_id="aaa_test_1", day_offset=1, minute_of_day=70, symbol="AAA/USDT:USDT", current_return_from_start=0.024, distance_to_running_high=-0.0006),
        _state_row(event_id="bbb_test_1", day_offset=1, minute_of_day=80, symbol="BBB/USDT:USDT", current_return_from_start=-0.021, distance_to_running_high=-0.045),
        _state_row(event_id="aaa_test_2", day_offset=2, minute_of_day=60, symbol="AAA/USDT:USDT", current_return_from_start=0.023, distance_to_running_high=-0.0007),
        _state_row(event_id="bbb_test_2", day_offset=2, minute_of_day=90, symbol="BBB/USDT:USDT", current_return_from_start=-0.022, distance_to_running_high=-0.05),
    ]
    labels = [
        _label_row(state=states[0], scenario_30m="long_continuation"),
        _label_row(state=states[1], scenario_30m="long_continuation"),
        _label_row(state=states[2], scenario_30m="short_fade"),
        _label_row(state=states[3], scenario_30m="short_fade"),
        _label_row(state=states[4], scenario_30m="long_continuation"),
        _label_row(state=states[5], scenario_30m="short_fade"),
        _label_row(state=states[6], scenario_30m="long_continuation"),
        _label_row(state=states[7], scenario_30m="short_fade"),
    ]
    return states, labels


def _write_state(path: Path, rows: list[AnomalyState1mRow]) -> None:
    write_csv_artifact(path, state_rows_to_artifact(rows), get_artifact_schema("anomaly_state_1m.csv"))


def _write_labels(path: Path, rows: list[AnomalyOutcomeLabelRow]) -> None:
    write_csv_artifact(path, [asdict(row) for row in rows], get_artifact_schema("anomaly_outcome_labels.csv"))


def test_placebo_controls_emit_negative_control_rows() -> None:
    states, labels = _control_rows()
    inputs = build_prediction_inputs(state_rows=states, label_rows=labels)
    rows = build_placebo_test_rows(inputs=inputs, config=ControlsConfig(min_train_rows=2, min_group_rows=1, smoothing_strength=1.0))

    assert {row.control_name for row in rows} == {"random_labels", "time_shuffled_labels", "symbol_shuffled_labels", "random_entry_times"}
    assert {row.control_name for row in rows if row.status == CONTROL_STATUS_OK} == {"random_labels", "time_shuffled_labels", "symbol_shuffled_labels"}
    assert {row.control_name for row in rows if row.status == CONTROL_STATUS_DEFERRED} == {"random_entry_times"}
    assert all(row.oos_prediction_rows > 0 for row in rows if row.status == CONTROL_STATUS_OK)
    assert all(row.reference_real_brier >= 0.0 for row in rows)


def test_baseline_comparison_defers_volume_without_proxy_fields() -> None:
    states, labels = _control_rows()
    inputs = build_prediction_inputs(state_rows=states, label_rows=labels)
    rows = build_baseline_comparison_rows(inputs=inputs, config=ControlsConfig(min_train_rows=2, min_group_rows=1, smoothing_strength=1.0))

    by_name = {row.baseline_name: row for row in rows}
    assert set(by_name) == {
        "global_prior_only",
        "session_only",
        "event_time_only",
        "price_path_only",
        "volume_only",
        "btc_eth_only",
        "always_no_trade",
        "strategy_specific_heuristic",
    }
    assert by_name["volume_only"].status == CONTROL_STATUS_DEFERRED
    assert by_name["btc_eth_only"].status == CONTROL_STATUS_DEFERRED
    assert by_name["always_no_trade"].status == CONTROL_STATUS_DEFERRED
    assert by_name["strategy_specific_heuristic"].status == CONTROL_STATUS_DEFERRED
    assert "no proxy volume baseline" in by_name["volume_only"].notes
    assert by_name["price_path_only"].oos_prediction_rows > 0


def test_run_mvp1_controls_cli_writes_control_artifacts(tmp_path: Path) -> None:
    states, labels = _control_rows()
    state_path = tmp_path / "anomaly_state_1m.csv"
    labels_path = tmp_path / "anomaly_outcome_labels.csv"
    out_dir = tmp_path / "controls"
    _write_state(state_path, states)
    _write_labels(labels_path, labels)

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-controls",
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
    assert "mvp1 placebo/control artifacts written" in result.stdout
    assert (out_dir / "anomaly_placebo_tests.csv").is_file()
    assert (out_dir / "anomaly_baseline_comparison.csv").is_file()
    assert (out_dir / "anomaly_protocol_audit.csv").is_file()
    assert (out_dir / "anomaly_run_config.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "anomaly_protocol_audit.csv").open(newline="", encoding="utf-8-sig") as handle:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(handle)}
    assert audit_by_name["non_empty_oos_control_gate"]["status"] == "PASS"
    assert audit_by_name["technical_noise_shock_excluded_from_ml_train_validation_calibration_test"]["status"] == "PASS"
    assert audit_by_name["fixed_percent_labels_forbidden"]["status"] == "PASS"
    assert audit_by_name["purge_rule_snapshot_time_plus_Hmax_before_test_start"]["status"] == "PASS"

    with (out_dir / "anomaly_baseline_comparison.csv").open(newline="", encoding="utf-8-sig") as handle:
        baseline_rows = list(csv.DictReader(handle))
    assert any(row["baseline_name"] == "volume_only" and row["status"] == CONTROL_STATUS_DEFERRED for row in baseline_rows)
