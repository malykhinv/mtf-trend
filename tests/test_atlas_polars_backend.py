from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

import pytest

from anomaly_science.atlas.builder import (
    build_atlas_artifacts_from_csv_paths,
    context_split_rows_to_artifact,
    market_shock_group_rows_to_artifact,
    nature_rows_to_artifact,
    response_surface_rows_to_artifact,
)
from anomaly_science.atlas.polars_backend import build_atlas_artifacts_polars_from_csv_paths
from anomaly_science.contracts.artifacts import get_artifact_schema

BASE_TS = 1_704_067_200_000


def _write_rows(path: Path, *, schema_name: str, rows: list[dict[str, object]]) -> None:
    fieldnames = list(get_artifact_schema(schema_name).required_columns)
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for payload in rows:
            row = {column: "" for column in fieldnames}
            row.update(payload)
            writer.writerow(row)


def _state_payload(*, event_id: str, offset_minutes: int, symbol: str = "AAA/USDT:USDT") -> dict[str, object]:
    snapshot_time_ms = BASE_TS + offset_minutes * 60_000
    return {
        "run_id": "fixture_run",
        "strategy_name": "broad_anomaly_v1_h30",
        "strategy_version": "v1",
        "strategy_contract_version": "base_strategy_v1",
        "event_id": event_id,
        "symbol": symbol,
        "state_time_ms": snapshot_time_ms,
        "snapshot_time_ms": snapshot_time_ms,
        "feature_cutoff_time_ms": snapshot_time_ms,
        "minutes_since_trigger": 2,
        "is_trigger": True,
        "state_alive": True,
        "minutes_since_event_start": offset_minutes,
        "minutes_since_detection": 1,
        "event_alive": True,
        "running_high_asof_t": 103.0,
        "running_high_time_asof_t_ms": BASE_TS + 60_000,
        "running_low_asof_t": 99.0,
        "running_low_time_asof_t_ms": BASE_TS + 60_000,
        "time_since_running_high_minutes": 1,
        "current_close": 102.0,
        "current_return_from_start": 0.02,
        "distance_to_running_high": -0.0097,
        "distance_to_running_low": 0.0303,
    }


def _future_payload(
    *,
    event_id: str,
    offset_minutes: int,
    future_return: float,
    future_max: float,
    future_min: float,
    reclaimed: bool,
    symbol: str = "AAA/USDT:USDT",
) -> dict[str, object]:
    snapshot_time_ms = BASE_TS + offset_minutes * 60_000
    payload = {
        "event_id": event_id,
        "symbol": symbol,
        "snapshot_time_ms": snapshot_time_ms,
        "feature_cutoff_time_ms": snapshot_time_ms,
        "future_start_time_ms": snapshot_time_ms + 60_000,
        "core_atr_1440": 1.0,
        "ATR_1d_pct_asof_t": 0.01,
        "double_barrier_k_continuation": 1.0,
        "double_barrier_k_fade": 1.0,
        "reclaimed_running_high_30m": reclaimed,
        "reclaimed_running_high_60m": reclaimed,
    }
    for horizon in (5, 15, 30, 60, 120, 180):
        payload[f"future_return_{horizon}m"] = future_return
        payload[f"future_max_{horizon}m"] = future_max
        payload[f"future_min_{horizon}m"] = future_min
    for horizon in (15, 30, 60, 120, 180):
        payload[f"future_return_atr_{horizon}m"] = future_return
        payload[f"future_max_atr_{horizon}m"] = future_max
        payload[f"future_min_atr_{horizon}m"] = future_min
        payload[f"intracandle_double_barrier_hit_{horizon}m"] = False
        payload[f"barrier_resolution_{horizon}m"] = "none"
    return payload


def _feature_payload(
    *,
    event_id: str,
    offset_minutes: int,
    systemic_cluster_regime: str,
    symbol: str = "AAA/USDT:USDT",
) -> dict[str, object]:
    snapshot_time_ms = BASE_TS + offset_minutes * 60_000
    return {
        "feature_schema_version": "mvp1_feature_schema_v1",
        "feature_matrix_version": "mvp1_feature_matrix_v1",
        "event_id": event_id,
        "symbol": symbol,
        "snapshot_time_ms": snapshot_time_ms,
        "feature_cutoff_time_ms": snapshot_time_ms,
        "minutes_since_trigger": 2,
        "core_atr_1440": 1.0,
        "ATR_1d_pct_asof_t": 0.01,
        "current_return_from_start": 0.02,
        "range_since_start_atr": 2.5,
        "distance_to_running_high_atr": 0.2,
        "distance_to_running_low_atr": 2.0,
        "retracement_from_high_atr": 0.2,
        "price_speed_atr": 0.5,
        "clock_maturity": 0.5,
        "event_age_ratio": 0.1,
        "alpha_decay_bucket": "0-2m",
        "feature_source_status": "ok",
        "quote_volume_market_percentile": 0.95,
        "oi_growth_market_percentile": 0.8,
        "liq_intensity_market_percentile": 0.9,
        "liquidation_imbalance": 0.7,
        "return_from_event_market_percentile": 0.92,
        "cvd_price_divergence_5m": 1.2,
        "price_up_cvd_down_flag": True,
        "price_down_cvd_up_flag": False,
        "cvd_failed_to_confirm_high_flag": False,
        "corr_with_btc_30m": 0.1,
        "symbol_return_minus_btc_return_15m": 0.03,
        "idiosyncratic_momentum_score": 1.5,
        "simultaneous_anomalies_count_1m": 1,
        "simultaneous_anomalies_share_1m": 0.1,
        "systemic_cluster_regime": systemic_cluster_regime,
        "market_shock_id": f"{systemic_cluster_regime}:{symbol}",
        "cross_section_available": True,
        "cross_section_symbol_count": 10,
        "initial_pump_height_core_atr_1440": 3.5,
        "post_pump_consolidation_minutes": 12,
        "consolidation_width_ratio": 0.35,
        "shelf_low_asof_t": 100.0,
        "shelf_high_asof_t": 103.0,
        "current_low_minus_shelf_low_core_atr_1440": -0.1,
        "current_close_minus_shelf_low_core_atr_1440": 0.2,
        "current_high_minus_shelf_high_core_atr_1440": 0.1,
        "minutes_spent_below_shelf": 1,
        "minutes_since_reclaim": 1,
        "volume_on_sweep_percentile": 0.95,
        "trade_count_on_sweep_percentile": 0.9,
        "cvd_change_during_sweep": -0.3,
        "oi_change_during_sweep": 0.2,
        "liq_intensity_during_sweep": 1.2,
    }


def _artifacts_payload(artifacts) -> dict[str, list[dict[str, object]]]:
    return {
        "nature": [asdict(row) for row in artifacts.nature_atlas_rows],
        "context": [asdict(row) for row in artifacts.context_split_rows],
        "response": [asdict(row) for row in artifacts.response_surface_rows],
        "market": [asdict(row) for row in artifacts.market_shock_group_rows],
    }


def _read_csv_artifact_rows(path: Path, *, schema_name: str) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        assert reader.fieldnames == list(get_artifact_schema(schema_name).required_columns)
        return [dict(row) for row in reader]


def _csv_string_rows(*, schema_name: str, rows: list[dict[str, object]]) -> list[dict[str, str]]:
    fieldnames = list(get_artifact_schema(schema_name).required_columns)
    result: list[dict[str, str]] = []
    for row in rows:
        result.append({name: "" if row[name] is None else str(row[name]) for name in fieldnames})
    return result


def _assert_output_artifact_matches_rows(path: Path, *, schema_name: str, rows: list[dict[str, object]]) -> None:
    assert _read_csv_artifact_rows(path, schema_name=schema_name) == _csv_string_rows(
        schema_name=schema_name,
        rows=rows,
    )


def test_polars_atlas_backend_matches_legacy_csv_backend(tmp_path: Path) -> None:
    pytest.importorskip("polars")
    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    feature_path = tmp_path / "anomaly_feature_matrix.csv"
    _write_rows(
        state_path,
        schema_name="anomaly_state_1m.csv",
        rows=[
            _state_payload(event_id="evt_atlas_1", offset_minutes=2),
            _state_payload(event_id="evt_atlas_2", offset_minutes=3),
        ],
    )
    _write_rows(
        future_path,
        schema_name="anomaly_future_paths.csv",
        rows=[
            _future_payload(event_id="evt_atlas_1", offset_minutes=2, future_return=1.2, future_max=1.4, future_min=-0.1, reclaimed=True),
            _future_payload(event_id="evt_atlas_2", offset_minutes=3, future_return=-1.2, future_max=0.1, future_min=-1.4, reclaimed=False),
        ],
    )
    _write_rows(
        feature_path,
        schema_name="anomaly_feature_matrix.csv",
        rows=[
            _feature_payload(event_id="evt_atlas_1", offset_minutes=2, systemic_cluster_regime="idiosyncratic"),
            _feature_payload(event_id="evt_atlas_2", offset_minutes=3, systemic_cluster_regime="systemic_beta_shock"),
        ],
    )

    legacy = build_atlas_artifacts_from_csv_paths(
        state_path=state_path,
        future_path=future_path,
        feature_matrix_path=feature_path,
    )
    polars_artifacts = build_atlas_artifacts_polars_from_csv_paths(
        state_path=state_path,
        future_path=future_path,
        feature_matrix_path=feature_path,
    )

    assert _artifacts_payload(polars_artifacts) == _artifacts_payload(legacy)


def test_polars_atlas_backend_builds_nature_and_response_rows_without_legacy_accumulators(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("polars")
    import anomaly_science.atlas.builder as legacy_builder

    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    feature_path = tmp_path / "anomaly_feature_matrix.csv"
    _write_rows(
        state_path,
        schema_name="anomaly_state_1m.csv",
        rows=[_state_payload(event_id="evt_atlas_1", offset_minutes=2)],
    )
    _write_rows(
        future_path,
        schema_name="anomaly_future_paths.csv",
        rows=[
            _future_payload(
                event_id="evt_atlas_1",
                offset_minutes=2,
                future_return=1.2,
                future_max=1.4,
                future_min=-0.1,
                reclaimed=True,
            )
        ],
    )
    _write_rows(
        feature_path,
        schema_name="anomaly_feature_matrix.csv",
        rows=[_feature_payload(event_id="evt_atlas_1", offset_minutes=2, systemic_cluster_regime="idiosyncratic")],
    )

    def fail_exact_median_accumulator(cls):
        raise AssertionError("Polars atlas backend must not use legacy exact-median accumulators")

    monkeypatch.setattr(legacy_builder._NatureAccumulator, "with_exact_medians", classmethod(fail_exact_median_accumulator))

    artifacts = build_atlas_artifacts_polars_from_csv_paths(
        state_path=state_path,
        future_path=future_path,
        feature_matrix_path=feature_path,
    )

    assert artifacts.nature_atlas_rows
    assert artifacts.response_surface_rows
    assert {row.split_family for row in artifacts.nature_atlas_rows} >= {"price_shape_atr", "feature_matrix"}
    assert {row.surface_name for row in artifacts.response_surface_rows} >= {"price_shape_atr_x_alpha_decay", "session_x_market_context"}
    assert {row.median_future_return_atr for row in artifacts.nature_atlas_rows} == {None}
    assert artifacts.context_split_rows
    assert artifacts.market_shock_group_rows
    assert {row.context_name for row in artifacts.context_split_rows} >= {"price_shape_atr", "feature_matrix"}
    assert {row.market_shock_id for row in artifacts.market_shock_group_rows} == {"idiosyncratic:AAA/USDT:USDT"}


def test_run_mvp1_atlas_uses_polars_backend_without_legacy_fallback(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("polars")
    import anomaly_science.atlas.builder as legacy_builder
    from anomaly_science.atlas.run import run_mvp1_atlas

    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    feature_path = tmp_path / "anomaly_feature_matrix.csv"
    out_dir = tmp_path / "atlas"
    _write_rows(
        state_path,
        schema_name="anomaly_state_1m.csv",
        rows=[_state_payload(event_id="evt_atlas_1", offset_minutes=2)],
    )
    _write_rows(
        future_path,
        schema_name="anomaly_future_paths.csv",
        rows=[
            _future_payload(
                event_id="evt_atlas_1",
                offset_minutes=2,
                future_return=1.2,
                future_max=1.4,
                future_min=-0.1,
                reclaimed=True,
            )
        ],
    )
    _write_rows(
        feature_path,
        schema_name="anomaly_feature_matrix.csv",
        rows=[_feature_payload(event_id="evt_atlas_1", offset_minutes=2, systemic_cluster_regime="idiosyncratic")],
    )

    def fail_legacy(*args, **kwargs):
        raise AssertionError("run_mvp1_atlas must not fall back to the legacy pandas atlas backend")

    monkeypatch.setattr(legacy_builder, "build_atlas_artifacts_from_csv_paths", fail_legacy)

    output_dir = run_mvp1_atlas(
        state_path=state_path,
        future_path=future_path,
        feature_matrix_path=feature_path,
        out_dir=out_dir,
    )

    assert output_dir == out_dir
    assert (out_dir / "strategy_nature_atlas.csv").is_file()
    assert (out_dir / "strategy_response_surfaces.csv").is_file()

def test_run_mvp1_atlas_outputs_match_explicit_polars_backend_artifacts(tmp_path: Path) -> None:
    pytest.importorskip("polars")
    from anomaly_science.atlas.run import run_mvp1_atlas

    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    feature_path = tmp_path / "anomaly_feature_matrix.csv"
    out_dir = tmp_path / "atlas"
    _write_rows(
        state_path,
        schema_name="anomaly_state_1m.csv",
        rows=[
            _state_payload(event_id="evt_atlas_1", offset_minutes=2),
            _state_payload(event_id="evt_atlas_2", offset_minutes=3, symbol="BBB/USDT:USDT"),
        ],
    )
    _write_rows(
        future_path,
        schema_name="anomaly_future_paths.csv",
        rows=[
            _future_payload(
                event_id="evt_atlas_1",
                offset_minutes=2,
                future_return=1.2,
                future_max=1.4,
                future_min=-0.1,
                reclaimed=True,
            ),
            _future_payload(
                event_id="evt_atlas_2",
                offset_minutes=3,
                future_return=-1.2,
                future_max=0.1,
                future_min=-1.4,
                reclaimed=False,
                symbol="BBB/USDT:USDT",
            ),
        ],
    )
    _write_rows(
        feature_path,
        schema_name="anomaly_feature_matrix.csv",
        rows=[
            _feature_payload(event_id="evt_atlas_1", offset_minutes=2, systemic_cluster_regime="idiosyncratic"),
            _feature_payload(
                event_id="evt_atlas_2",
                offset_minutes=3,
                systemic_cluster_regime="systemic_beta_shock",
                symbol="BBB/USDT:USDT",
            ),
        ],
    )

    expected = build_atlas_artifacts_polars_from_csv_paths(
        state_path=state_path,
        future_path=future_path,
        feature_matrix_path=feature_path,
    )

    run_mvp1_atlas(
        state_path=state_path,
        future_path=future_path,
        feature_matrix_path=feature_path,
        out_dir=out_dir,
    )

    _assert_output_artifact_matches_rows(
        out_dir / "strategy_nature_atlas.csv",
        schema_name="strategy_nature_atlas.csv",
        rows=nature_rows_to_artifact(expected.nature_atlas_rows),
    )
    _assert_output_artifact_matches_rows(
        out_dir / "strategy_context_splits.csv",
        schema_name="strategy_context_splits.csv",
        rows=context_split_rows_to_artifact(expected.context_split_rows),
    )
    _assert_output_artifact_matches_rows(
        out_dir / "strategy_response_surfaces.csv",
        schema_name="strategy_response_surfaces.csv",
        rows=response_surface_rows_to_artifact(expected.response_surface_rows),
    )
    _assert_output_artifact_matches_rows(
        out_dir / "strategy_market_shock_groups.csv",
        schema_name="strategy_market_shock_groups.csv",
        rows=market_shock_group_rows_to_artifact(expected.market_shock_group_rows),
    )

    assert (out_dir / "artifact_manifest.json").is_file()



def test_polars_atlas_backend_large_fixture_has_bounded_progress_and_no_median_accumulators(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("polars")
    import anomaly_science.atlas.builder as legacy_builder
    from anomaly_science.atlas.config import AtlasConfig

    state_path = tmp_path / "anomaly_state_1m.csv"
    future_path = tmp_path / "anomaly_future_paths.csv"
    feature_path = tmp_path / "anomaly_feature_matrix.csv"
    row_count = 1024
    state_rows = []
    future_rows = []
    feature_rows = []
    regimes = ("idiosyncratic", "systemic_beta_shock", "localized_cluster")
    for index in range(row_count):
        symbol = f"SYM{index % 32:03d}/USDT:USDT"
        event_id = f"evt_perf_{index:05d}"
        offset_minutes = index + 2
        state_rows.append(_state_payload(event_id=event_id, offset_minutes=offset_minutes, symbol=symbol))
        future_return = 1.2 if index % 3 == 0 else (-1.2 if index % 3 == 1 else 0.05)
        future_rows.append(
            _future_payload(
                event_id=event_id,
                offset_minutes=offset_minutes,
                future_return=future_return,
                future_max=1.4 if future_return > 1.0 else 0.2,
                future_min=-1.4 if future_return < -1.0 else -0.2,
                reclaimed=index % 2 == 0,
                symbol=symbol,
            )
        )
        feature_rows.append(
            _feature_payload(
                event_id=event_id,
                offset_minutes=offset_minutes,
                systemic_cluster_regime=regimes[index % len(regimes)],
                symbol=symbol,
            )
        )

    _write_rows(state_path, schema_name="anomaly_state_1m.csv", rows=state_rows)
    _write_rows(future_path, schema_name="anomaly_future_paths.csv", rows=future_rows)
    _write_rows(feature_path, schema_name="anomaly_feature_matrix.csv", rows=feature_rows)

    def fail_exact_median_accumulator(cls):
        raise AssertionError("Polars atlas backend must not use legacy exact-median accumulators")

    monkeypatch.setattr(legacy_builder._NatureAccumulator, "with_exact_medians", classmethod(fail_exact_median_accumulator))
    progress_updates = []

    artifacts = build_atlas_artifacts_polars_from_csv_paths(
        state_path=state_path,
        future_path=future_path,
        feature_matrix_path=feature_path,
        config=AtlasConfig(outcome_horizon_minutes=30),
        progress_callback=progress_updates.append,
    )

    assert artifacts.nature_atlas_rows
    assert artifacts.context_split_rows
    assert artifacts.response_surface_rows
    assert artifacts.market_shock_group_rows
    assert {row.median_future_return_atr for row in artifacts.nature_atlas_rows} == {None}
    assert {row.median_future_return for row in artifacts.nature_atlas_rows} == {None}
    assert [update.done for update in progress_updates] == [0, 1, 2, 3, 4, 5, 6]
    assert {update.total for update in progress_updates} == {6}
    assert all(update.unit == "steps" for update in progress_updates)
