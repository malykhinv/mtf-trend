from __future__ import annotations

import csv
from pathlib import Path

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.rejection import build_rejection_funnel_rows, write_rejection_funnel


def _write_artifact(path: Path, schema_name: str, rows: list[dict[str, object]]) -> None:
    schema = get_artifact_schema(schema_name)
    payload = []
    for row in rows:
        item = {column: "" for column in schema.required_columns}
        item.update(row)
        payload.append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(schema.required_columns))
        writer.writeheader()
        writer.writerows(payload)


def _fixture_run(root: Path) -> None:
    _write_artifact(
        root / "strategy_run_config.csv",
        "strategy_run_config.csv",
        [
            {"key": "strategy_name", "value": "broad_anomaly_v1_h30", "source": "fixture"},
            {"key": "strategy_version", "value": "v1", "source": "fixture"},
            {"key": "strategy_contract_version", "value": "base_strategy_v2_structural_execution", "source": "fixture"},
            {"key": "target_horizon_minutes", "value": "30", "source": "fixture"},
        ],
    )
    _write_artifact(
        root / "stages" / "events" / "strategy_data_quality.csv",
        "strategy_data_quality.csv",
        [
            {
                "check_name": "technical_noise_shock",
                "status": "PASS",
                "severity": "warn",
                "affected_rows": 1,
                "message": "one row excluded",
                "excluded_from_detector": True,
                "excluded_from_ml_dataset": True,
                "reason": "technical_noise_shock",
                "artifact": "strategy_data_quality.csv",
            }
        ],
    )
    _write_artifact(
        root / "stages" / "events" / "symbol_universe_by_day.csv",
        "symbol_universe_by_day.csv",
        [
            {
                "trade_date": "2024-01-01",
                "symbol": "AAAUSDT",
                "listed_asof_day": True,
                "tradable_on_day": True,
                "has_1m_data": True,
                "has_5m_data": True,
                "has_oi_data": True,
                "has_liquidation_data": True,
                "liquidity_eligible_on_day": True,
                "eligible_for_cross_section": True,
            }
        ],
    )
    _write_artifact(
        root / "stages" / "events" / "strategy_events.csv",
        "strategy_events.csv",
        [
            {
                "event_id": "e1",
                "symbol": "AAAUSDT",
                "state_time_ms": 1_800_000,
                "event_start_time_ms": 1_740_000,
                "minutes_since_start": 1,
                "is_trigger": True,
                "event_detection_time_ms": 1_800_000,
                "seed_time_ms": 1_740_000,
                "seed_open": 100.0,
                "seed_high": 103.0,
                "seed_low": 99.0,
                "seed_close": 102.0,
                "initial_move_pct": 0.02,
                "trigger_component": "one_shot_spike",
                "trigger_components": "one_shot_spike",
                "technical_noise_shock": False,
                "excluded_by_data_quality_gate": False,
                "detector_version": "fixture",
            }
        ],
    )
    _write_artifact(
        root / "stages" / "state" / "strategy_state_1m.csv",
        "strategy_state_1m.csv",
        [
            {
                "event_id": "e1",
                "symbol": "AAAUSDT",
                "state_time_ms": 1_800_000,
                "snapshot_time_ms": 1_800_000,
                "feature_cutoff_time_ms": 1_800_000,
                "minutes_since_event_start": 1,
                "minutes_since_detection": 0,
                "event_alive": True,
                "running_high_asof_t": 103.0,
                "running_high_time_asof_t_ms": 1_800_000,
                "running_low_asof_t": 99.0,
                "running_low_time_asof_t_ms": 1_740_000,
                "time_since_running_high_minutes": 0,
                "current_close": 102.0,
                "current_return_from_start": 0.02,
            }
        ],
    )
    _write_artifact(root / "stages" / "future" / "strategy_future_paths.csv", "strategy_future_paths.csv", [])
    _write_artifact(
        root / "stages" / "labels" / "strategy_outcome_labels.csv",
        "strategy_outcome_labels.csv",
        [
            {
                "label_schema_version": "fixture",
                "atr_window_minutes": 1440,
                "ATR_1d_asof_t": 1.0,
                "k_continuation": 1.5,
                "k_fade": 1.0,
                "k_chop": 0.5,
                "event_id": "e1",
                "symbol": "AAAUSDT",
                "snapshot_time_ms": 1_800_000,
                "feature_cutoff_time_ms": 1_800_000,
                "future_start_time_ms": 1_860_000,
                "scenario_30m": "missing_future",
                "label_available_30m": False,
                "label_source": "fixture",
                "temporal_contract": "fixture",
            }
        ],
    )
    _write_artifact(root / "stages" / "prediction" / "strategy_oos_predictions.csv", "strategy_oos_predictions.csv", [])
    _write_artifact(root / "stages" / "expected_value" / "strategy_decision_timing.csv", "strategy_decision_timing.csv", [])
    _write_artifact(root / "stages" / "simulation" / "strategy_trade_simulation.csv", "strategy_trade_simulation.csv", [])


def test_rejection_funnel_records_stage_lineage_and_explicit_reasons(tmp_path: Path) -> None:
    _fixture_run(tmp_path)

    rows = build_rejection_funnel_rows(tmp_path)
    by_stage_status = {(row.stage, row.status, row.reason_code) for row in rows}

    assert ("data_quality", "EXCLUDED", "technical_noise_shock") in by_stage_status
    assert ("events", "INCLUDED", "") in by_stage_status
    assert ("future_path", "EXCLUDED", "future_path_incomplete") in by_stage_status
    assert ("labels", "EXCLUDED", "horizon_not_available") in by_stage_status
    assert any(row.stage.startswith("summary:") for row in rows)


def test_rejection_funnel_compacts_large_included_stage_rows(tmp_path: Path) -> None:
    _fixture_run(tmp_path)

    rows = build_rejection_funnel_rows(tmp_path)
    state_rows = [row for row in rows if row.stage == "state" and row.status == "INCLUDED"]

    assert len(state_rows) == 1
    assert state_rows[0].row_key == "state|included_summary"
    assert state_rows[0].row_count == 1


def test_write_rejection_funnel_writes_strategy_and_anomaly_alias(tmp_path: Path) -> None:
    _fixture_run(tmp_path)

    out_dir = write_rejection_funnel(run_dir=tmp_path)

    assert (out_dir / "strategy_rejection_funnel.csv").is_file()
    assert (out_dir / "anomaly_rejection_funnel.csv").is_file()
