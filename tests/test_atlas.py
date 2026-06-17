from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.atlas import (
    AnomalyFutureArtifactError,
    assign_atlas_contexts,
    build_atlas_artifacts,
    load_anomaly_future_paths_csv,
)
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.future import FuturePathRow
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.future import future_rows_to_artifact
from anomaly_science.state import state_rows_to_artifact

BASE_TS = 1_704_067_200_000


def _state_row(*, event_id: str = "evt_atlas", symbol: str = "AAA/USDT:USDT", snapshot_offset_minutes: int = 2) -> AnomalyState1mRow:
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


def _future_row(
    *,
    event_id: str = "evt_atlas",
    symbol: str = "AAA/USDT:USDT",
    snapshot_offset_minutes: int = 2,
    future_return_30m: float | None = 0.02,
    future_max_30m: float | None = 0.04,
    future_min_30m: float | None = -0.002,
    reclaimed_running_high_30m: bool | None = True,
) -> FuturePathRow:
    snapshot_time_ms = BASE_TS + snapshot_offset_minutes * 60_000
    return FuturePathRow(
        event_id=event_id,
        symbol=symbol,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        future_start_time_ms=snapshot_time_ms + 60_000,
        future_return_5m=0.005,
        future_return_15m=0.01,
        future_return_30m=future_return_30m,
        future_return_60m=None,
        future_max_5m=0.01,
        future_max_15m=0.02,
        future_max_30m=future_max_30m,
        future_max_60m=None,
        future_min_5m=-0.001,
        future_min_15m=-0.001,
        future_min_30m=future_min_30m,
        future_min_60m=None,
        reclaimed_running_high_30m=reclaimed_running_high_30m,
        reclaimed_running_high_60m=None,
        broke_structural_low_30m=None,
        broke_structural_low_60m=None,
        time_to_new_high_minutes=2,
        time_to_structural_break_minutes=None,
    )


def _write_state(path: Path, rows: list[AnomalyState1mRow]) -> None:
    write_csv_artifact(path, state_rows_to_artifact(rows), get_artifact_schema("anomaly_state_1m.csv"))


def _write_future(path: Path, rows: list[FuturePathRow]) -> None:
    write_csv_artifact(path, future_rows_to_artifact(rows), get_artifact_schema("anomaly_future_paths.csv"))


def test_future_artifact_boundary_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "anomaly_future_paths.csv"
    columns = list(get_artifact_schema("anomaly_future_paths.csv").required_columns) + ["extra"]
    path.write_text(",".join(columns) + "\n", encoding="utf-8")

    with pytest.raises(AnomalyFutureArtifactError, match="columns must match"):
        load_anomaly_future_paths_csv(path)


def test_atlas_grouping_contexts_ignore_future_values() -> None:
    state = _state_row()
    upside_future = _future_row(future_return_30m=0.03, future_max_30m=0.05, future_min_30m=-0.001, reclaimed_running_high_30m=True)
    downside_future = _future_row(future_return_30m=-0.03, future_max_30m=0.002, future_min_30m=-0.05, reclaimed_running_high_30m=False)

    assert assign_atlas_contexts(state) == assign_atlas_contexts(state)

    upside_artifacts = build_atlas_artifacts(state_rows=[state], future_rows=[upside_future])
    downside_artifacts = build_atlas_artifacts(state_rows=[state], future_rows=[downside_future])

    upside_contexts = {(row.context_name, row.context_value) for row in upside_artifacts.context_split_rows}
    downside_contexts = {(row.context_name, row.context_value) for row in downside_artifacts.context_split_rows}
    assert upside_contexts == downside_contexts
    assert {row.atlas_outcome_bin for row in upside_artifacts.nature_atlas_rows} == {"upside_continuation_30m"}
    assert {row.atlas_outcome_bin for row in downside_artifacts.nature_atlas_rows} == {"downside_extension_30m"}


def test_atlas_preserves_temporal_contract_in_outputs() -> None:
    state = _state_row()
    future = _future_row()

    artifacts = build_atlas_artifacts(state_rows=[state], future_rows=[future])

    assert artifacts.nature_atlas_rows
    assert all(
        row.temporal_contract == "feature_cutoff_time_ms<=snapshot_time_ms<future_start_time_ms"
        for row in artifacts.nature_atlas_rows
    )
    assert artifacts.market_shock_group_rows[0].snapshot_time_ms == state.snapshot_time_ms
    assert artifacts.market_shock_group_rows[0].market_shock_candidate is False


def test_run_mvp1_atlas_cli_writes_atlas_artifacts(tmp_path: Path) -> None:
    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    out_dir = tmp_path / "atlas"
    _write_state(state_path, [_state_row()])
    _write_future(future_path, [_future_row()])

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-atlas",
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
    assert "mvp1 anomaly atlas artifacts written" in result.stdout
    assert (out_dir / "anomaly_nature_atlas.csv").is_file()
    assert (out_dir / "anomaly_context_splits.csv").is_file()
    assert (out_dir / "anomaly_response_surfaces.csv").is_file()
    assert (out_dir / "anomaly_market_shock_groups.csv").is_file()
    assert (out_dir / "anomaly_protocol_audit.csv").is_file()
    assert (out_dir / "anomaly_run_config.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "anomaly_nature_atlas.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert rows
    assert rows[0]["atlas_version"] == "mvp1_atlas_v1"
    assert rows[0]["atlas_outcome_bin"] == "upside_continuation_30m"
