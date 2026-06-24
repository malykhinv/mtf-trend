from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus
from anomaly_science.contracts.market import Candle1m
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.future import (
    AnomalyStateArtifactError,
    FuturePathBuilderConfig,
    build_anomaly_future_paths,
    future_row_to_artifact,
    iter_strategy_future_path_csv_value_rows_from_grouped_csv,
    iter_strategy_future_paths_from_grouped_csv,
    load_anomaly_future_paths_csv,
    load_anomaly_state_1m_csv,
)


BASE_TS = 1_704_067_200_000
FIXTURE_DIR = Path("tests/fixtures/minimal_market_data")


def _candle(index: int, *, open_price: float, high: float, low: float, close: float) -> Candle1m:
    open_time_ms = BASE_TS + index * 60_000
    return Candle1m(
        symbol="AAA/USDT:USDT",
        open_time_ms=open_time_ms,
        available_time_ms=open_time_ms + 60_000,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        quote_volume=100.0,
        number_of_trades=10.0,
        taker_buy_quote_volume=50.0,
    )


def _state_row() -> AnomalyState1mRow:
    return AnomalyState1mRow(
        event_id="evt_future_test",
        symbol="AAA/USDT:USDT",
        state_time_ms=BASE_TS + 120_000,
        snapshot_time_ms=BASE_TS + 120_000,
        feature_cutoff_time_ms=BASE_TS + 120_000,
        minutes_since_event_start=1,
        minutes_since_detection=0,
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


def _write_state_csv(path: Path) -> None:
    schema = get_artifact_schema("anomaly_state_1m.csv")
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=schema.required_columns)
        writer.writeheader()
        writer.writerow(
            {
                "event_id": "evt_fixture",
                "symbol": "AAA/USDT:USDT",
                "state_time_ms": "1704067260000",
                "snapshot_time_ms": "1704067260000",
                "feature_cutoff_time_ms": "1704067260000",
                "minutes_since_event_start": "1",
                "minutes_since_detection": "0",
                "event_alive": "True",
                "running_high_asof_t": "101.0",
                "running_high_time_asof_t_ms": "1704067200000",
                "running_low_asof_t": "99.5",
                "running_low_time_asof_t_ms": "1704067200000",
                "time_since_running_high_minutes": "1",
                "current_close": "100.5",
                "current_return_from_start": "0.005",
                "distance_to_running_high": str((100.5 / 101.0) - 1.0),
                "distance_to_running_low": str((100.5 / 99.5) - 1.0),
                "distance_to_structural_low": "",
                "distance_to_structural_high": "",
            }
        )


def _write_state_rows_csv(path: Path, rows: list[AnomalyState1mRow]) -> None:
    schema = get_artifact_schema("strategy_state_1m.csv")
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=schema.required_columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if value is None else value for key, value in asdict(row).items()})


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
        "number_of_trades",
        "taker_buy_quote_volume",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for candle in candles:
            writer.writerow({name: getattr(candle, name) for name in fieldnames})


def test_future_builder_uses_only_candles_after_snapshot_time() -> None:
    candles = [
        _candle(0, open_price=100.0, high=500.0, low=1.0, close=101.0),
        _candle(1, open_price=101.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=102.5, low=101.0, close=102.2),
        _candle(3, open_price=102.2, high=106.0, low=101.5, close=105.0),
        _candle(4, open_price=105.0, high=105.5, low=100.0, close=101.0),
        _candle(5, open_price=101.0, high=103.0, low=98.0, close=99.0),
        _candle(6, open_price=99.0, high=104.0, low=97.5, close=103.0),
    ]

    rows = build_anomaly_future_paths(
        candles_1m=candles,
        state_rows=[_state_row()],
        config=FuturePathBuilderConfig(atr_window_minutes=1),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.future_start_time_ms == _state_row().snapshot_time_ms + 60_000
    assert row.future_start_time_ms > row.snapshot_time_ms
    assert row.future_return_5m == pytest.approx((103.0 / 102.0) - 1.0)
    assert row.future_max_5m == pytest.approx((106.0 / 102.0) - 1.0)
    assert row.future_min_5m == pytest.approx((97.5 / 102.0) - 1.0)
    assert row.future_return_120m is None
    assert row.future_return_180m is None
    assert row.future_max_120m == pytest.approx((106.0 / 102.0) - 1.0)
    assert row.future_max_180m == pytest.approx((106.0 / 102.0) - 1.0)
    assert row.future_min_120m == pytest.approx((97.5 / 102.0) - 1.0)
    assert row.future_min_180m == pytest.approx((97.5 / 102.0) - 1.0)
    assert row.core_atr_1440 == pytest.approx(4.0)
    assert row.ATR_1d_pct_asof_t == pytest.approx(4.0 / 102.0)
    assert row.future_return_atr_5m == pytest.approx((103.0 - 102.0) / 4.0)
    assert row.future_max_atr_5m == pytest.approx((106.0 - 102.0) / 4.0)
    assert row.future_min_atr_5m == pytest.approx((97.5 - 102.0) / 4.0)
    assert row.future_return_atr_120m is None
    assert row.future_return_atr_180m is None
    assert row.reclaimed_running_high_30m is True
    assert row.reclaimed_running_high_60m is True
    assert row.time_to_new_high_minutes == 2
    assert row.broke_structural_low_30m is None
    assert row.broke_structural_low_60m is None
    assert row.time_to_structural_break_minutes is None



def test_future_builder_marks_intracandle_double_barrier_as_stop_loss_first() -> None:
    candles = [
        _candle(0, open_price=100.0, high=101.0, low=99.0, close=100.0),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=102.5, low=101.0, close=102.0),
        _candle(3, open_price=102.0, high=106.0, low=98.0, close=103.0),
        _candle(4, open_price=103.0, high=103.5, low=102.5, close=103.0),
        _candle(5, open_price=103.0, high=103.5, low=102.5, close=103.0),
        _candle(6, open_price=103.0, high=103.5, low=102.5, close=103.0),
    ]

    row = build_anomaly_future_paths(
        candles_1m=candles,
        state_rows=[_state_row()],
        config=FuturePathBuilderConfig(atr_window_minutes=1),
    )[0]

    assert row.double_barrier_k_continuation == 1.0
    assert row.double_barrier_k_fade == 1.0
    assert row.intracandle_double_barrier_hit_5m is True
    assert row.barrier_resolution_5m == "stop_loss_first"


def test_mutating_pre_snapshot_candles_does_not_change_future_paths() -> None:
    candles = [
        _candle(0, open_price=100.0, high=101.0, low=99.0, close=100.0),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=104.0, low=101.0, close=103.0),
        _candle(3, open_price=103.0, high=105.0, low=102.0, close=104.0),
        _candle(4, open_price=104.0, high=105.0, low=103.0, close=104.5),
        _candle(5, open_price=104.5, high=106.0, low=104.0, close=105.0),
        _candle(6, open_price=105.0, high=107.0, low=104.0, close=106.0),
    ]
    mutated = list(candles)
    mutated[0] = _candle(0, open_price=100.0, high=999.0, low=1.0, close=500.0)

    rows = build_anomaly_future_paths(candles_1m=candles, state_rows=[_state_row()])
    mutated_rows = build_anomaly_future_paths(candles_1m=mutated, state_rows=[_state_row()])

    assert rows == mutated_rows


def test_state_artifact_boundary_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "anomaly_state_1m.csv"
    path.write_text(
        "event_id,symbol,state_time_ms,snapshot_time_ms,feature_cutoff_time_ms,minutes_since_event_start,minutes_since_detection,event_alive,running_high_asof_t,running_high_time_asof_t_ms,running_low_asof_t,running_low_time_asof_t_ms,time_since_running_high_minutes,current_close,current_return_from_start,distance_to_running_high,distance_to_running_low,distance_to_structural_low,distance_to_structural_high,extra\n",
        encoding="utf-8",
    )

    with pytest.raises(AnomalyStateArtifactError, match="columns must match"):
        load_anomaly_state_1m_csv(path)


def test_grouped_csv_future_builder_matches_in_memory_builder(tmp_path: Path) -> None:
    candles = [
        _candle(0, open_price=100.0, high=500.0, low=1.0, close=101.0),
        _candle(1, open_price=101.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=102.5, low=101.0, close=102.2),
        _candle(3, open_price=102.2, high=106.0, low=101.5, close=105.0),
        _candle(4, open_price=105.0, high=105.5, low=100.0, close=101.0),
        _candle(5, open_price=101.0, high=103.0, low=98.0, close=99.0),
        _candle(6, open_price=99.0, high=104.0, low=97.5, close=103.0),
    ]
    state = _state_row()
    candles_path = tmp_path / "candles_1m.csv"
    state_path = tmp_path / "strategy_state_1m.csv"
    _write_candles_csv(candles_path, candles)
    _write_state_rows_csv(state_path, [state])

    expected = build_anomaly_future_paths(
        candles_1m=candles,
        state_rows=[state],
        config=FuturePathBuilderConfig(atr_window_minutes=1),
    )
    actual = tuple(
        iter_strategy_future_paths_from_grouped_csv(
            candles_path=candles_path,
            state_path=state_path,
            config=FuturePathBuilderConfig(atr_window_minutes=1),
        )
    )

    assert actual == expected


def test_direct_csv_value_future_builder_matches_typed_builder(tmp_path: Path) -> None:
    candles = [
        _candle(0, open_price=100.0, high=500.0, low=1.0, close=101.0),
        _candle(1, open_price=101.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=102.5, low=101.0, close=102.2),
        _candle(3, open_price=102.2, high=106.0, low=101.5, close=105.0),
        _candle(4, open_price=105.0, high=105.5, low=100.0, close=101.0),
        _candle(5, open_price=101.0, high=103.0, low=98.0, close=99.0),
        _candle(6, open_price=99.0, high=104.0, low=97.5, close=103.0),
    ]
    state = _state_row()
    candles_path = tmp_path / "candles_1m.csv"
    state_path = tmp_path / "strategy_state_1m.csv"
    _write_candles_csv(candles_path, candles)
    _write_state_rows_csv(state_path, [state])
    fieldnames = get_artifact_schema("strategy_future_paths.csv").required_columns

    expected_row = future_row_to_artifact(
        build_anomaly_future_paths(
            candles_1m=candles,
            state_rows=[state],
            config=FuturePathBuilderConfig(atr_window_minutes=1),
        )[0]
    )
    actual_values = next(
        iter_strategy_future_path_csv_value_rows_from_grouped_csv(
            candles_path=candles_path,
            state_path=state_path,
            fieldnames=fieldnames,
            config=FuturePathBuilderConfig(atr_window_minutes=1),
        )
    )

    assert dict(zip(fieldnames, actual_values, strict=True)) == expected_row


def test_run_mvp1_future_cli_writes_future_artifacts(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    state_path = tmp_path / "anomaly_state_1m.csv"
    out_dir = tmp_path / "future"
    _write_state_csv(state_path)

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-future",
            "--input",
            str(FIXTURE_DIR),
            "--state",
            str(state_path),
            "--out",
            str(out_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 raw future path artifacts written" in result.stdout
    assert (out_dir / "strategy_future_paths.csv").is_file()
    assert (out_dir / "strategy_protocol_audit.csv").is_file()
    assert (out_dir / "strategy_run_config.csv").is_file()
    assert not (out_dir / "anomaly_future_paths.csv").exists()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "strategy_future_paths.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        rows = list(reader)
    assert rows == []

    sidecar_dir = out_dir / "strategy_future_paths.parquet"
    sidecar_manifest = out_dir / "strategy_future_paths.parquet_manifest.json"
    assert sidecar_dir.is_dir()
    assert sidecar_manifest.is_file()
    manifest = json.loads(sidecar_manifest.read_text(encoding="utf-8"))
    assert manifest["csv_delivery"] == "schema_header_only"
    assert manifest["parquet_delivery"] == "canonical_partitioned_future_paths"
    assert manifest["row_count"] == 1
    assert manifest["part_paths"]

    loaded = load_anomaly_future_paths_csv(out_dir / "strategy_future_paths.csv")
    assert len(loaded) == 1
    assert loaded[0].event_id == "evt_fixture"
    assert loaded[0].future_start_time_ms > loaded[0].snapshot_time_ms
    assert loaded[0].broke_structural_low_30m is None

    with (out_dir / "strategy_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["fixed_percent_labels_forbidden"]["status"] == AuditStatus.PASS.value
    assert audit_by_name["intracandle_double_barrier_resolved_as_stop_loss_first"]["status"] == AuditStatus.PASS.value


def test_future_builder_emits_missing_atr_fields_without_fallback_when_history_is_short() -> None:
    candles = [
        _candle(0, open_price=100.0, high=101.0, low=99.0, close=100.0),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=104.0, low=101.0, close=103.0),
        _candle(3, open_price=103.0, high=105.0, low=102.0, close=104.0),
        _candle(4, open_price=104.0, high=105.0, low=103.0, close=104.5),
        _candle(5, open_price=104.5, high=106.0, low=104.0, close=105.0),
        _candle(6, open_price=105.0, high=107.0, low=104.0, close=106.0),
    ]

    row = build_anomaly_future_paths(candles_1m=candles, state_rows=[_state_row()])[0]

    assert row.atr_window_minutes == 1440
    assert row.core_atr_1440 is None
    assert row.ATR_1d_pct_asof_t is None
    assert row.future_return_atr_5m is None
    assert row.future_max_atr_5m is None
    assert row.future_min_atr_5m is None


def test_future_builder_emits_missing_atr_fields_when_history_atr_is_zero() -> None:
    candles = [
        _candle(0, open_price=102.0, high=102.0, low=102.0, close=102.0),
        _candle(1, open_price=102.0, high=102.0, low=102.0, close=102.0),
        _candle(2, open_price=102.0, high=102.0, low=102.0, close=102.0),
        _candle(3, open_price=102.0, high=103.0, low=101.0, close=102.5),
    ]

    row = build_anomaly_future_paths(
        candles_1m=candles,
        state_rows=[_state_row()],
        config=FuturePathBuilderConfig(atr_window_minutes=1),
    )[0]

    assert row.core_atr_1440 is None
    assert row.ATR_1d_pct_asof_t is None
    assert row.double_barrier_k_continuation is None
    assert row.double_barrier_k_fade is None
    assert row.future_return_atr_5m is None
    assert row.future_max_atr_5m is None
    assert row.future_min_atr_5m is None
    assert row.intracandle_double_barrier_hit_5m is None
    assert row.barrier_resolution_5m is None
    assert row.future_max_5m == pytest.approx((103.0 / 102.0) - 1.0)


def test_future_path_artifact_roundtrip_accepts_atr_normalized_schema(tmp_path: Path) -> None:
    candles = [
        _candle(0, open_price=100.0, high=100.5, low=99.5, close=100.0),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=104.0, low=101.0, close=103.0),
        _candle(3, open_price=103.0, high=105.0, low=102.0, close=104.0),
        _candle(4, open_price=104.0, high=106.0, low=103.0, close=105.0),
        _candle(5, open_price=105.0, high=107.0, low=104.0, close=106.0),
        _candle(6, open_price=106.0, high=108.0, low=105.0, close=107.0),
    ]
    from anomaly_science.artifacts import write_csv_artifact
    from anomaly_science.future import future_rows_to_artifact

    rows = build_anomaly_future_paths(
        candles_1m=candles,
        state_rows=[_state_row()],
        config=FuturePathBuilderConfig(atr_window_minutes=1),
    )
    path = tmp_path / "anomaly_future_paths.csv"
    write_csv_artifact(path, future_rows_to_artifact(rows), get_artifact_schema("anomaly_future_paths.csv"))

    loaded = load_anomaly_future_paths_csv(path)

    assert len(loaded) == len(rows)
    assert loaded[0].event_id == rows[0].event_id
    assert loaded[0].core_atr_1440 == pytest.approx(rows[0].core_atr_1440)
    assert loaded[0].future_max_atr_5m == pytest.approx(rows[0].future_max_atr_5m)
    header = path.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert "ATR_1d_asof_t" in header
    assert "future_max_atr_120m" in header
    assert "future_max_atr_180m" in header


def test_future_parquet_sidecar_manifest_is_strict(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    state_path = tmp_path / "anomaly_state_1m.csv"
    out_dir = tmp_path / "future"
    _write_state_csv(state_path)

    from anomaly_science.future import run_mvp1_future

    run_mvp1_future(input_dir=FIXTURE_DIR, state_path=state_path, out_dir=out_dir)
    manifest_path = out_dir / "strategy_future_paths.parquet_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["csv_sha256"] = "broken"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(Exception, match="csv_sha256"):
        load_anomaly_future_paths_csv(out_dir / "strategy_future_paths.csv")
