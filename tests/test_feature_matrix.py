from __future__ import annotations

import csv
import math
from pathlib import Path

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus
from anomaly_science.contracts.market import Candle1m, ONE_MINUTE_MS
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.features import (
    FeatureMatrixConfig,
    alpha_decay_bucket,
    build_price_time_feature_matrix,
    feature_matrix_rows_to_artifact,
    run_mvp1_feature_matrix,
)
from anomaly_science.state.builder import state_rows_to_artifact


def test_alpha_decay_buckets_are_pre_registered() -> None:
    assert alpha_decay_bucket(0) == "0-2m"
    assert alpha_decay_bucket(2) == "0-2m"
    assert alpha_decay_bucket(3) == "3-5m"
    assert alpha_decay_bucket(6) == "6-10m"
    assert alpha_decay_bucket(11) == "11-20m"
    assert alpha_decay_bucket(21) == "21-40m"
    assert alpha_decay_bucket(41) == ">40m"


def test_price_time_feature_matrix_is_atr_normalized_and_asof_only() -> None:
    candles = list(_candles(count=1442))
    snapshot_time_ms = candles[-2].available_time_ms
    state = _state(snapshot_time_ms=snapshot_time_ms, current_close=candles[-2].close)
    future_candle = Candle1m(
        symbol="AAAUSDT",
        open_time_ms=candles[-1].open_time_ms,
        available_time_ms=candles[-1].available_time_ms,
        open=500.0,
        high=600.0,
        low=400.0,
        close=550.0,
        volume=1.0,
        quote_volume=550.0,
    )

    rows = build_price_time_feature_matrix(candles_1m=[*candles[:-1], future_candle], state_rows=[state])

    assert len(rows) == 1
    row = rows[0]
    assert row.snapshot_time_ms == snapshot_time_ms
    assert row.feature_cutoff_time_ms == snapshot_time_ms
    assert row.ATR_1d_asof_t is not None
    assert row.ATR_1d_pct_asof_t is not None
    assert row.range_since_start_atr == (state.running_high_asof_t - state.running_low_asof_t) / row.ATR_1d_asof_t
    assert row.distance_to_running_high_atr == (state.running_high_asof_t - state.current_close) / row.ATR_1d_asof_t
    assert row.distance_to_running_low_atr == (state.current_close - state.running_low_asof_t) / row.ATR_1d_asof_t
    assert row.retracement_from_high_atr == row.distance_to_running_high_atr
    assert row.price_speed_atr is not None
    assert abs(row.price_speed_atr) < 100.0
    assert row.clock_maturity == state.time_since_running_high_minutes / 7
    assert row.event_age_ratio == state.minutes_since_detection / 60
    assert row.alpha_decay_bucket == "3-5m"
    assert row.feature_source_status == "ok"


def test_feature_matrix_leaves_atr_features_null_without_fallback() -> None:
    candles = list(_candles(count=10))
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)

    rows = build_price_time_feature_matrix(candles_1m=candles, state_rows=[state])

    row = rows[0]
    assert row.ATR_1d_asof_t is None
    assert row.ATR_1d_pct_asof_t is None
    assert row.range_since_start_atr is None
    assert row.price_speed_atr is None
    assert row.feature_source_status == "insufficient_atr_history"


def test_feature_matrix_artifact_schema_roundtrip() -> None:
    candles = list(_candles(count=1442))
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)
    rows = build_price_time_feature_matrix(candles_1m=candles, state_rows=[state])
    payload = feature_matrix_rows_to_artifact(rows)
    schema = get_artifact_schema("anomaly_feature_matrix.csv")

    assert payload
    assert tuple(payload[0]) == schema.required_columns


def test_run_mvp1_feature_matrix_writes_artifacts(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    out_dir = tmp_path / "features"

    candles = list(_candles(count=1442))
    _write_candles_csv(input_dir / "candles_1m.csv", candles)
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)
    write_csv_artifact(
        state_dir / "anomaly_state_1m.csv",
        state_rows_to_artifact([state]),
        get_artifact_schema("anomaly_state_1m.csv"),
    )

    out = run_mvp1_feature_matrix(
        input_dir=input_dir,
        state_path=state_dir / "anomaly_state_1m.csv",
        out_dir=out_dir,
        config=FeatureMatrixConfig(expected_event_lifetime_minutes=30),
    )

    matrix_path = out / "anomaly_feature_matrix.csv"
    catalog_path = out / "anomaly_feature_catalog.csv"
    audit_path = out / "anomaly_protocol_audit.csv"
    run_config_path = out / "anomaly_run_config.csv"
    manifest_path = out / "artifact_manifest.json"
    assert matrix_path.is_file()
    assert catalog_path.is_file()
    assert audit_path.is_file()
    assert run_config_path.is_file()
    assert manifest_path.is_file()

    with matrix_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        matrix_rows = list(csv.DictReader(file_obj))
    assert len(matrix_rows) == 1
    assert matrix_rows[0]["alpha_decay_bucket"] == "3-5m"
    assert float(matrix_rows[0]["event_age_ratio"]) == state.minutes_since_detection / 30

    with audit_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        audit_rows = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_rows["relative_over_absolute_feature_contract_enforced"]["status"] == AuditStatus.PASS.value
    assert audit_rows["ATR_1d_asof_t_computed_from_closed_past_candles"]["status"] == AuditStatus.PASS.value


def _candles(*, count: int) -> tuple[Candle1m, ...]:
    rows: list[Candle1m] = []
    for index in range(count):
        open_price = 100.0 + index * 0.01
        rows.append(
            Candle1m(
                symbol="AAAUSDT",
                open_time_ms=index * ONE_MINUTE_MS,
                available_time_ms=(index + 1) * ONE_MINUTE_MS,
                open=open_price,
                high=open_price + 1.0,
                low=open_price - 1.0,
                close=open_price + 0.2,
                volume=10.0,
                quote_volume=10.0 * open_price,
            )
        )
    return tuple(rows)


def _state(*, snapshot_time_ms: int, current_close: float) -> AnomalyState1mRow:
    return AnomalyState1mRow(
        event_id="e1",
        symbol="AAAUSDT",
        state_time_ms=snapshot_time_ms,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_event_start=10,
        minutes_since_detection=5,
        event_alive=True,
        running_high_asof_t=current_close + 2.0,
        running_high_time_asof_t_ms=snapshot_time_ms - 3 * ONE_MINUTE_MS,
        running_low_asof_t=current_close - 3.0,
        running_low_time_asof_t_ms=snapshot_time_ms - 9 * ONE_MINUTE_MS,
        time_since_running_high_minutes=3,
        current_close=current_close,
        current_return_from_start=0.03,
        distance_to_running_high=(current_close / (current_close + 2.0)) - 1.0,
        distance_to_running_low=(current_close / (current_close - 3.0)) - 1.0,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )


def _write_candles_csv(path: Path, candles: list[Candle1m]) -> None:
    fieldnames = [
        "symbol",
        "open_time_ms",
        "available_time_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    ]
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for candle in candles:
            writer.writerow({name: getattr(candle, name) for name in fieldnames})
