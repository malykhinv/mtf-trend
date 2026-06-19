from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.future import FuturePathRow
from anomaly_science.contracts.labels import TEMPORAL_LABEL_CONTRACT
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.future import future_rows_to_artifact
from anomaly_science.labels import (
    OutcomeLabelArtifactError,
    assign_future_nature_scenario,
    build_anomaly_outcome_labels,
    load_anomaly_outcome_labels_csv,
    outcome_label_rows_to_artifact,
)
from anomaly_science.state import state_rows_to_artifact

BASE_TS = 1_704_067_200_000


def _state_row(*, event_id: str = "evt_labels", symbol: str = "AAA/USDT:USDT", snapshot_offset_minutes: int = 2) -> AnomalyState1mRow:
    snapshot_time_ms = BASE_TS + snapshot_offset_minutes * 60_000
    return AnomalyState1mRow(
        event_id=event_id,
        symbol=symbol,
        state_time_ms=snapshot_time_ms,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_event_start=snapshot_offset_minutes,
        minutes_since_detection=1,
        event_alive=True,
        running_high_asof_t=103.0,
        running_high_time_asof_t_ms=BASE_TS + 60_000,
        running_low_asof_t=99.0,
        running_low_time_asof_t_ms=BASE_TS + 60_000,
        time_since_running_high_minutes=1,
        current_close=102.0,
        current_return_from_start=0.02,
        distance_to_running_high=(102.0 / 103.0) - 1.0,
        distance_to_running_low=(102.0 / 99.0) - 1.0,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )


def _mutated_state_row_same_key() -> AnomalyState1mRow:
    snapshot_time_ms = BASE_TS + 2 * 60_000
    return AnomalyState1mRow(
        event_id="evt_labels",
        symbol="AAA/USDT:USDT",
        state_time_ms=snapshot_time_ms,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_event_start=25,
        minutes_since_detection=20,
        event_alive=False,
        running_high_asof_t=150.0,
        running_high_time_asof_t_ms=BASE_TS + 2 * 60_000,
        running_low_asof_t=80.0,
        running_low_time_asof_t_ms=BASE_TS + 60_000,
        time_since_running_high_minutes=0,
        current_close=81.0,
        current_return_from_start=-0.19,
        distance_to_running_high=-0.46,
        distance_to_running_low=0.0125,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )


def _future_row(
    *,
    event_id: str = "evt_labels",
    symbol: str = "AAA/USDT:USDT",
    snapshot_offset_minutes: int = 2,
    future_return_15m: float | None = 0.02,
    future_return_30m: float | None = 0.02,
    future_return_60m: float | None = 0.02,
    future_return_120m: float | None = 0.02,
    future_return_180m: float | None = 0.02,
    future_max_15m: float | None = 0.03,
    future_max_30m: float | None = 0.04,
    future_max_60m: float | None = 0.05,
    future_max_120m: float | None = 0.05,
    future_max_180m: float | None = 0.05,
    future_min_15m: float | None = -0.002,
    future_min_30m: float | None = -0.002,
    future_min_60m: float | None = -0.002,
    future_min_120m: float | None = -0.002,
    future_min_180m: float | None = -0.002,
    future_return_atr_15m: float | None = 1.2,
    future_return_atr_30m: float | None = 1.2,
    future_return_atr_60m: float | None = 1.2,
    future_return_atr_120m: float | None = 1.2,
    future_return_atr_180m: float | None = 1.2,
    future_max_atr_15m: float | None = 2.0,
    future_max_atr_30m: float | None = 2.0,
    future_max_atr_60m: float | None = 2.0,
    future_max_atr_120m: float | None = 2.0,
    future_max_atr_180m: float | None = 2.0,
    future_min_atr_15m: float | None = -0.1,
    future_min_atr_30m: float | None = -0.1,
    future_min_atr_60m: float | None = -0.1,
    future_min_atr_120m: float | None = -0.1,
    future_min_atr_180m: float | None = -0.1,
    reclaimed_running_high_30m: bool | None = True,
    reclaimed_running_high_60m: bool | None = True,
    intracandle_double_barrier_hit_30m: bool | None = None,
    barrier_resolution_30m: str | None = None,
    double_barrier_k_continuation: float | None = None,
    double_barrier_k_fade: float | None = None,
) -> FuturePathRow:
    snapshot_time_ms = BASE_TS + snapshot_offset_minutes * 60_000
    return FuturePathRow(
        event_id=event_id,
        symbol=symbol,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        future_start_time_ms=snapshot_time_ms + 60_000,
        atr_window_minutes=1440,
        core_atr_1440=2.0,
        ATR_1d_pct_asof_t=0.02,
        double_barrier_k_continuation=double_barrier_k_continuation,
        double_barrier_k_fade=double_barrier_k_fade,
        future_return_5m=0.005,
        future_return_15m=future_return_15m,
        future_return_30m=future_return_30m,
        future_return_60m=future_return_60m,
        future_return_120m=future_return_120m,
        future_return_180m=future_return_180m,
        future_max_5m=0.01,
        future_max_15m=future_max_15m,
        future_max_30m=future_max_30m,
        future_max_60m=future_max_60m,
        future_max_120m=future_max_120m,
        future_max_180m=future_max_180m,
        future_min_5m=-0.001,
        future_min_15m=future_min_15m,
        future_min_30m=future_min_30m,
        future_min_60m=future_min_60m,
        future_min_120m=future_min_120m,
        future_min_180m=future_min_180m,
        future_return_atr_5m=0.3,
        future_return_atr_15m=future_return_atr_15m,
        future_return_atr_30m=future_return_atr_30m,
        future_return_atr_60m=future_return_atr_60m,
        future_return_atr_120m=future_return_atr_120m,
        future_return_atr_180m=future_return_atr_180m,
        future_max_atr_5m=0.5,
        future_max_atr_15m=future_max_atr_15m,
        future_max_atr_30m=future_max_atr_30m,
        future_max_atr_60m=future_max_atr_60m,
        future_max_atr_120m=future_max_atr_120m,
        future_max_atr_180m=future_max_atr_180m,
        future_min_atr_5m=-0.05,
        future_min_atr_15m=future_min_atr_15m,
        future_min_atr_30m=future_min_atr_30m,
        future_min_atr_60m=future_min_atr_60m,
        future_min_atr_120m=future_min_atr_120m,
        future_min_atr_180m=future_min_atr_180m,
        intracandle_double_barrier_hit_30m=intracandle_double_barrier_hit_30m,
        barrier_resolution_30m=barrier_resolution_30m,
        reclaimed_running_high_30m=reclaimed_running_high_30m,
        reclaimed_running_high_60m=reclaimed_running_high_60m,
        broke_structural_low_30m=None,
        broke_structural_low_60m=None,
        time_to_new_high_minutes=2,
        time_to_structural_break_minutes=None,
    )


def _write_state(path: Path, rows: list[AnomalyState1mRow]) -> None:
    write_csv_artifact(path, state_rows_to_artifact(rows), get_artifact_schema("anomaly_state_1m.csv"))


def _write_future(path: Path, rows: list[FuturePathRow]) -> None:
    write_csv_artifact(path, future_rows_to_artifact(rows), get_artifact_schema("anomaly_future_paths.csv"))


def test_future_nature_scenarios_are_descriptive_not_trade_labels() -> None:
    assert assign_future_nature_scenario(future=_future_row(), horizon_minutes=30) == "long_continuation"
    assert (
        assign_future_nature_scenario(
            future=_future_row(
                future_return_atr_30m=-1.2,
                future_max_atr_30m=0.1,
                future_min_atr_30m=-2.0,
                reclaimed_running_high_30m=False,
            ),
            horizon_minutes=30,
        )
        == "short_fade"
    )
    assert (
        assign_future_nature_scenario(
            future=_future_row(
                future_return_atr_30m=0.05,
                future_max_atr_30m=0.1,
                future_min_atr_30m=-0.1,
                reclaimed_running_high_30m=False,
            ),
            horizon_minutes=30,
        )
        == "static_or_chop"
    )
    assert (
        assign_future_nature_scenario(
            future=_future_row(
                future_return_atr_30m=-0.1,
                future_max_atr_30m=1.5,
                future_min_atr_30m=-1.5,
                reclaimed_running_high_30m=True,
            ),
            horizon_minutes=30,
        )
        == "unclear"
    )
    assert assign_future_nature_scenario(future=_future_row(future_return_atr_30m=None), horizon_minutes=30) == "missing_future"



def test_double_barrier_stop_first_prevents_profit_label() -> None:
    scenario = assign_future_nature_scenario(
        future=_future_row(
            future_return_atr_30m=1.2,
            future_max_atr_30m=1.5,
            future_min_atr_30m=-1.5,
            intracandle_double_barrier_hit_30m=True,
            barrier_resolution_30m="stop_loss_first",
            double_barrier_k_continuation=1.0,
            double_barrier_k_fade=1.0,
        ),
        horizon_minutes=30,
    )

    assert scenario == "unclear"


def test_outcome_labels_use_state_only_for_join_and_temporal_audit() -> None:
    future = _future_row()

    base_labels = build_anomaly_outcome_labels(state_rows=[_state_row()], future_rows=[future])
    mutated_state_labels = build_anomaly_outcome_labels(state_rows=[_mutated_state_row_same_key()], future_rows=[future])

    assert base_labels == mutated_state_labels
    assert base_labels[0].scenario_15m == "long_continuation"
    assert base_labels[0].scenario_30m == "long_continuation"
    assert base_labels[0].scenario_60m == "long_continuation"
    assert base_labels[0].label_available_30m is True
    assert base_labels[0].label_source == "atr_normalized_future_paths_only"
    assert base_labels[0].temporal_contract == TEMPORAL_LABEL_CONTRACT


def test_missing_future_is_explicit_data_condition() -> None:
    labels = build_anomaly_outcome_labels(
        state_rows=[_state_row()],
        future_rows=[
            _future_row(
                future_return_15m=None,
                future_max_15m=None,
                future_min_15m=None,
                future_return_atr_15m=None,
                future_max_atr_15m=None,
                future_min_atr_15m=None,
                future_return_30m=0.001,
                future_max_30m=0.003,
                future_min_30m=-0.002,
                future_return_atr_30m=0.05,
                future_max_atr_30m=0.1,
                future_min_atr_30m=-0.1,
                reclaimed_running_high_30m=False,
            )
        ],
    )

    assert labels[0].scenario_15m == "missing_future"
    assert labels[0].label_available_15m is False
    assert labels[0].scenario_30m == "static_or_chop"
    assert labels[0].label_available_30m is True


def test_outcome_label_artifact_boundary_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "anomaly_outcome_labels.csv"
    columns = list(get_artifact_schema("anomaly_outcome_labels.csv").required_columns) + ["extra"]
    path.write_text(",".join(columns) + "\n", encoding="utf-8")

    with pytest.raises(OutcomeLabelArtifactError, match="columns must match"):
        load_anomaly_outcome_labels_csv(path)


def test_outcome_label_artifact_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "anomaly_outcome_labels.csv"
    labels = build_anomaly_outcome_labels(state_rows=[_state_row()], future_rows=[_future_row()])
    write_csv_artifact(path, outcome_label_rows_to_artifact(labels), get_artifact_schema("anomaly_outcome_labels.csv"))

    loaded = load_anomaly_outcome_labels_csv(path)

    assert loaded == labels


def test_run_mvp1_labels_cli_writes_label_artifacts(tmp_path: Path) -> None:
    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    out_dir = tmp_path / "labels"
    _write_state(state_path, [_state_row()])
    _write_future(future_path, [_future_row()])

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-labels",
            "--state",
            str(state_path),
            "--future",
            str(future_path),
            "--out",
            str(out_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 outcome label artifacts written" in result.stdout
    assert (out_dir / "anomaly_outcome_labels.csv").is_file()
    assert (out_dir / "anomaly_protocol_audit.csv").is_file()
    assert (out_dir / "anomaly_run_config.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "anomaly_outcome_labels.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert rows == [
        {
            "label_schema_version": "atr_outcome_labels_v1",
            "atr_window_minutes": "1440",
            "ATR_1d_asof_t": "2.0",
            "k_continuation": "1.0",
            "k_fade": "1.0",
            "k_chop": "0.25",
            "event_id": "evt_labels",
            "symbol": "AAA/USDT:USDT",
            "snapshot_time_ms": str(BASE_TS + 2 * 60_000),
            "feature_cutoff_time_ms": str(BASE_TS + 2 * 60_000),
            "future_start_time_ms": str(BASE_TS + 3 * 60_000),
            "scenario_15m": "long_continuation",
            "scenario_30m": "long_continuation",
            "scenario_60m": "long_continuation",
            "scenario_120m": "long_continuation",
            "scenario_180m": "long_continuation",
            "label_available_15m": "True",
            "label_available_30m": "True",
            "label_available_60m": "True",
            "label_available_120m": "True",
            "label_available_180m": "True",
            "label_source": "atr_normalized_future_paths_only",
            "temporal_contract": TEMPORAL_LABEL_CONTRACT,
        }
    ]
