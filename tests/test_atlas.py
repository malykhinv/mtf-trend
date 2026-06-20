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
from anomaly_science.contracts.features import AnomalyFeatureMatrixRow
from anomaly_science.contracts.future import FuturePathRow
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.features import feature_matrix_rows_to_artifact
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
        future_return_15m=future_return_30m,
        future_return_30m=future_return_30m,
        future_return_60m=future_return_30m,
        future_return_120m=future_return_30m,
        future_return_180m=future_return_30m,
        core_atr_1440=1.0,
        ATR_1d_pct_asof_t=0.01,
        double_barrier_k_continuation=1.0,
        double_barrier_k_fade=1.0,
        future_max_5m=0.01,
        future_max_15m=future_max_30m,
        future_max_30m=future_max_30m,
        future_max_60m=future_max_30m,
        future_max_120m=future_max_30m,
        future_max_180m=future_max_30m,
        future_return_atr_15m=future_return_30m,
        future_return_atr_30m=future_return_30m,
        future_return_atr_60m=future_return_30m,
        future_return_atr_120m=future_return_30m,
        future_return_atr_180m=future_return_30m,
        future_max_atr_15m=future_max_30m,
        future_max_atr_30m=future_max_30m,
        future_max_atr_60m=future_max_30m,
        future_max_atr_120m=future_max_30m,
        future_max_atr_180m=future_max_30m,
        future_min_5m=-0.001,
        future_min_15m=future_min_30m,
        future_min_30m=future_min_30m,
        future_min_60m=future_min_30m,
        future_min_120m=future_min_30m,
        future_min_180m=future_min_30m,
        future_min_atr_15m=future_min_30m,
        future_min_atr_30m=future_min_30m,
        future_min_atr_60m=future_min_30m,
        future_min_atr_120m=future_min_30m,
        future_min_atr_180m=future_min_30m,
        intracandle_double_barrier_hit_15m=False,
        intracandle_double_barrier_hit_30m=False,
        intracandle_double_barrier_hit_60m=False,
        intracandle_double_barrier_hit_120m=False,
        intracandle_double_barrier_hit_180m=False,
        barrier_resolution_15m="none",
        barrier_resolution_30m="none",
        barrier_resolution_60m="none",
        barrier_resolution_120m="none",
        barrier_resolution_180m="none",
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


def _feature_row(
    *,
    event_id: str = "evt_atlas",
    symbol: str = "AAA/USDT:USDT",
    snapshot_offset_minutes: int = 2,
    market_shock_id: str = "idiosyncratic:AAA/USDT:USDT",
    systemic_cluster_regime: str = "idiosyncratic",
) -> AnomalyFeatureMatrixRow:
    snapshot_time_ms = BASE_TS + snapshot_offset_minutes * 60_000
    return AnomalyFeatureMatrixRow(
        feature_schema_version="mvp1_feature_schema_v1",
        feature_matrix_version="mvp1_feature_matrix_v1",
        event_id=event_id,
        symbol=symbol,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_trigger=2,
        core_atr_1440=1.0,
        ATR_1d_pct_asof_t=0.01,
        current_return_from_start=0.02,
        range_since_start_atr=2.5,
        distance_to_running_high_atr=0.2,
        distance_to_running_low_atr=2.0,
        retracement_from_high_atr=0.2,
        price_speed_atr=0.5,
        clock_maturity=0.5,
        event_age_ratio=0.1,
        alpha_decay_bucket="0-2m",
        feature_source_status="ok",
        quote_volume_market_percentile=0.95,
        oi_growth_market_percentile=0.8,
        liq_intensity_market_percentile=0.9,
        liquidation_imbalance=0.7,
        return_from_event_market_percentile=0.92,
        cvd_price_divergence_5m=1.2,
        price_up_cvd_down_flag=True,
        corr_with_btc_30m=0.1,
        symbol_return_minus_btc_return_15m=0.03,
        idiosyncratic_momentum_score=1.5,
        simultaneous_anomalies_count_1m=1,
        simultaneous_anomalies_share_1m=0.1,
        systemic_cluster_regime=systemic_cluster_regime,
        market_shock_id=market_shock_id,
        cross_section_available=True,
        cross_section_symbol_count=10,
        initial_pump_height_core_atr_1440=3.5,
        post_pump_consolidation_minutes=12,
        consolidation_width_ratio=0.35,
        shelf_low_asof_t=100.0,
        shelf_high_asof_t=103.0,
        current_low_minus_shelf_low_core_atr_1440=-0.1,
        current_close_minus_shelf_low_core_atr_1440=0.2,
        current_high_minus_shelf_high_core_atr_1440=0.1,
        minutes_spent_below_shelf=1,
        minutes_since_reclaim=1,
        volume_on_sweep_percentile=0.95,
        trade_count_on_sweep_percentile=0.9,
        cvd_change_during_sweep=-0.3,
        oi_change_during_sweep=0.2,
        liq_intensity_during_sweep=1.2,
    )


def _write_features(path: Path, rows: list[AnomalyFeatureMatrixRow]) -> None:
    write_csv_artifact(path, feature_matrix_rows_to_artifact(rows), get_artifact_schema("anomaly_feature_matrix.csv"))


def test_future_artifact_boundary_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "anomaly_future_paths.csv"
    columns = list(get_artifact_schema("anomaly_future_paths.csv").required_columns) + ["extra"]
    path.write_text(",".join(columns) + "\n", encoding="utf-8")

    with pytest.raises(AnomalyFutureArtifactError, match="columns must match"):
        load_anomaly_future_paths_csv(path)


def test_atlas_grouping_contexts_ignore_future_values() -> None:
    state = _state_row()
    feature = _feature_row()
    upside_future = _future_row(future_return_30m=0.03, future_max_30m=0.05, future_min_30m=-0.001, reclaimed_running_high_30m=True)
    downside_future = _future_row(future_return_30m=-0.03, future_max_30m=0.002, future_min_30m=-0.05, reclaimed_running_high_30m=False)

    assert assign_atlas_contexts(state, feature=feature) == assign_atlas_contexts(state, feature=feature)

    upside_artifacts = build_atlas_artifacts(state_rows=[state], future_rows=[upside_future], feature_rows=[feature])
    downside_artifacts = build_atlas_artifacts(state_rows=[state], future_rows=[downside_future], feature_rows=[feature])

    upside_contexts = {(row.context_name, row.context_value) for row in upside_artifacts.context_split_rows}
    downside_contexts = {(row.context_name, row.context_value) for row in downside_artifacts.context_split_rows}
    assert upside_contexts == downside_contexts
    assert {row.atlas_outcome_bin for row in upside_artifacts.nature_atlas_rows if row.outcome_horizon_minutes == 30} == {"range_chop_atr_30m"}
    assert {row.atlas_outcome_bin for row in downside_artifacts.nature_atlas_rows if row.outcome_horizon_minutes == 30} == {"range_chop_atr_30m"}


def test_atlas_preserves_temporal_contract_in_outputs() -> None:
    state = _state_row()
    future = _future_row()
    feature = _feature_row()

    artifacts = build_atlas_artifacts(state_rows=[state], future_rows=[future], feature_rows=[feature])

    assert artifacts.nature_atlas_rows
    assert all(
        row.temporal_contract == "feature_cutoff_time_ms<=snapshot_time_ms<future_start_time_ms"
        for row in artifacts.nature_atlas_rows
    )
    assert artifacts.market_shock_group_rows[0].snapshot_time_ms == state.snapshot_time_ms
    assert artifacts.market_shock_group_rows[0].market_shock_candidate is False


def test_run_mvp1_atlas_cli_requires_feature_matrix(tmp_path: Path) -> None:
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

    assert result.returncode != 0
    assert "--features" in result.stderr


def test_atlas_uses_feature_matrix_relative_contexts_and_market_shock_ids(tmp_path: Path) -> None:
    state = _state_row()
    future = _future_row(future_return_30m=1.2, future_max_30m=1.4, future_min_30m=-0.1)
    feature = _feature_row(market_shock_id="idiosyncratic:AAA", systemic_cluster_regime="idiosyncratic")

    artifacts = build_atlas_artifacts(state_rows=[state], future_rows=[future], feature_rows=[feature])

    contexts = {(row.context_name, row.context_value) for row in artifacts.context_split_rows}
    assert ("volume_regime_relative", "top_decile") in contexts
    assert ("liquidation_regime_relative", "top_decile_short_liq_dominant") in contexts
    assert ("cvd_divergence_regime", "price_up_cvd_down") in contexts
    assert ("systemic_cluster_regime", "idiosyncratic") in contexts
    assert ("initial_pump_height_atr", "large_initial_pump_atr") in contexts
    assert ("shelf_break_risk", "shallow_shelf_sweep_asof") in contexts
    assert {row.outcome_horizon_minutes for row in artifacts.nature_atlas_rows} == {15, 30, 60, 120, 180}
    assert {row.atlas_outcome_bin for row in artifacts.nature_atlas_rows if row.outcome_horizon_minutes == 30} == {"upside_continuation_atr_30m"}
    assert {row.surface_name for row in artifacts.response_surface_rows} >= {"initial_pump_x_consolidation", "shelf_break_x_systemic_cluster", "sweep_flow_x_liquidation"}
    assert artifacts.market_shock_group_rows[0].market_shock_id == "idiosyncratic:AAA"
    assert artifacts.market_shock_group_rows[0].systemic_cluster_regime == "idiosyncratic"


def test_run_mvp1_atlas_cli_accepts_feature_matrix(tmp_path: Path) -> None:
    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    feature_path = tmp_path / "anomaly_feature_matrix.csv"
    out_dir = tmp_path / "atlas_with_features"
    _write_state(state_path, [_state_row()])
    _write_future(future_path, [_future_row(future_return_30m=1.2, future_max_30m=1.4, future_min_30m=-0.1)])
    _write_features(feature_path, [_feature_row()])

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-atlas",
            "--state",
            str(state_path),
            "--future",
            str(future_path),
            "--features",
            str(feature_path),
            "--out",
            str(out_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 strategy atlas artifacts written" in result.stdout
    assert (out_dir / "strategy_nature_atlas.csv").is_file()
    assert (out_dir / "strategy_context_splits.csv").is_file()
    assert (out_dir / "strategy_response_surfaces.csv").is_file()
    assert (out_dir / "strategy_market_shock_groups.csv").is_file()
    assert (out_dir / "strategy_protocol_audit.csv").is_file()
    assert (out_dir / "strategy_run_config.csv").is_file()
    assert (out_dir / "anomaly_nature_atlas.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "strategy_nature_atlas.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        atlas_rows = list(csv.DictReader(file_obj))
    assert atlas_rows
    assert atlas_rows[0]["atlas_version"] == "mvp1_atlas_v3"
    assert {int(row["outcome_horizon_minutes"]) for row in atlas_rows} == {15, 30, 60, 120, 180}
    atlas_30m_rows = [row for row in atlas_rows if row["outcome_horizon_minutes"] == "30"]
    assert atlas_30m_rows
    assert {row["outcome_coordinate"] for row in atlas_30m_rows} == {"ATR_normalized_30m"}
    assert {row["atlas_outcome_bin"] for row in atlas_30m_rows} == {"upside_continuation_atr_30m"}

    with (out_dir / "strategy_market_shock_groups.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert {int(row["outcome_horizon_minutes"]) for row in rows} == {15, 30, 60, 120, 180}
    assert rows[0]["market_shock_id"] == "idiosyncratic:AAA/USDT:USDT"
    assert rows[0]["systemic_cluster_regime"] == "idiosyncratic"
