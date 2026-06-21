from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m
from anomaly_science.data.source import CsvDataSourceError
from anomaly_science.state import (
    AnomalyEventsArtifactError,
    OnlineStateBuilderConfig,
    build_online_anomaly_state_1m,
    iter_online_strategy_state_1m_from_grouped_csv,
    load_anomaly_events_csv,
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


def _event(
    *,
    event_id: str = "evt_state_test",
    technical_noise_shock: bool = False,
    excluded_by_data_quality_gate: bool = False,
) -> AnomalyEvent:
    return AnomalyEvent(
        event_id=event_id,
        symbol="AAA/USDT:USDT",
        event_start_time_ms=BASE_TS + 60_000,
        event_detection_time_ms=BASE_TS + 120_000,
        seed_time_ms=BASE_TS + 60_000,
        seed_open=100.0,
        seed_high=103.0,
        seed_low=99.0,
        seed_close=102.0,
        initial_move_pct=0.02,
        initial_volume_zscore=None,
        initial_quote_volume_zscore=None,
        initial_trade_count_zscore=None,
        trigger_component="one_shot_spike",
        trigger_components=("one_shot_spike",),
        detector_version="test_detector",
        technical_noise_shock=technical_noise_shock,
        raw_candle_gap_minutes=6.0 if technical_noise_shock else None,
        excluded_by_data_quality_gate=excluded_by_data_quality_gate,
    )


def _write_events_csv(path: Path) -> None:
    schema = get_artifact_schema("anomaly_events.csv")
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=schema.required_columns)
        writer.writeheader()
        writer.writerow(
            {
                "event_id": "evt_fixture",
                "symbol": "AAA/USDT:USDT",
                "state_time_ms": "1704067260000",
                "event_start_time_ms": "1704067200000",
                "minutes_since_start": "1",
                "is_trigger": "True",
                "event_detection_time_ms": "1704067260000",
                "seed_time_ms": "1704067200000",
                "seed_open": "100.0",
                "seed_high": "101.0",
                "seed_low": "99.5",
                "seed_close": "100.5",
                "initial_move_pct": "0.005",
                "initial_volume_zscore": "",
                "initial_quote_volume_zscore": "",
                "initial_trade_count_zscore": "",
                "trigger_component": "one_shot_spike",
                "trigger_components": "one_shot_spike",
                "technical_noise_shock": "False",
                "raw_candle_gap_minutes": "",
                "excluded_by_data_quality_gate": "False",
                "daily_return_asof_t": "",
                "trade_count_market_percentile_asof_t": "",
                "detector_version": "test_detector",
            }
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
        "number_of_trades",
        "taker_buy_quote_volume",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for candle in candles:
            writer.writerow({name: getattr(candle, name) for name in fieldnames})


def test_state_builder_uses_only_closed_candles_available_asof_state_time() -> None:
    candles = [
        _candle(0, open_price=99.0, high=100.0, low=98.0, close=99.5),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=104.0, low=101.0, close=103.0),
        _candle(3, open_price=103.0, high=103.5, low=97.0, close=98.0),
    ]

    rows = build_online_anomaly_state_1m(candles_1m=candles, events=[_event()])

    assert [row.state_time_ms for row in rows] == [BASE_TS + 120_000, BASE_TS + 180_000, BASE_TS + 240_000]
    assert all(row.feature_cutoff_time_ms <= row.state_time_ms for row in rows)
    assert all(row.state_time_ms >= _event().event_detection_time_ms for row in rows)
    assert rows[0].running_high_asof_t == 103.0
    assert rows[0].running_low_asof_t == 99.0
    assert rows[1].running_high_asof_t == 104.0
    assert rows[1].running_low_asof_t == 99.0
    assert rows[2].running_high_asof_t == 104.0
    assert rows[2].running_low_asof_t == 97.0
    assert rows[1].current_return_from_start == pytest.approx(0.03)


def test_state_builder_materializes_only_confirmed_structural_levels() -> None:
    candles = [
        _candle(0, open_price=99.0, high=100.0, low=98.0, close=99.5),
        _candle(1, open_price=100.0, high=101.0, low=99.0, close=100.5),
        _candle(2, open_price=100.5, high=103.0, low=100.0, close=102.5),
        _candle(3, open_price=102.5, high=106.0, low=101.0, close=105.0),
        _candle(4, open_price=105.0, high=105.5, low=100.0, close=101.0),
        _candle(5, open_price=101.0, high=103.0, low=97.0, close=98.0),
        _candle(6, open_price=98.0, high=101.0, low=97.5, close=100.0),
    ]

    rows = build_online_anomaly_state_1m(
        candles_1m=candles,
        events=[_event()],
        config=OnlineStateBuilderConfig(max_state_minutes_after_detection=5),
    )

    early_row = next(row for row in rows if row.state_time_ms == BASE_TS + 180_000)
    final_row = rows[-1]
    assert early_row.structural_high_asof_t is None
    assert early_row.structural_low_asof_t is None
    assert final_row.structural_high_asof_t == 106.0
    assert final_row.structural_high_time_asof_t_ms == BASE_TS + 180_000
    assert final_row.structural_low_asof_t == 97.0
    assert final_row.structural_low_time_asof_t_ms == BASE_TS + 300_000
    assert final_row.distance_to_structural_low == pytest.approx((100.0 / 97.0) - 1.0)
    assert final_row.distance_to_structural_high == pytest.approx((100.0 / 106.0) - 1.0)


def test_mutating_future_candles_does_not_change_already_built_past_state_rows() -> None:
    candles = [
        _candle(0, open_price=99.0, high=100.0, low=98.0, close=99.5),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=104.0, low=101.0, close=103.0),
        _candle(3, open_price=103.0, high=103.5, low=100.0, close=101.0),
        _candle(4, open_price=101.0, high=102.0, low=100.0, close=101.5),
    ]
    config = OnlineStateBuilderConfig(max_state_minutes_after_detection=3)

    rows = build_online_anomaly_state_1m(candles_1m=candles, events=[_event()], config=config)
    mutated = list(candles)
    mutated[4] = _candle(4, open_price=101.0, high=999.0, low=1.0, close=500.0)
    mutated_rows = build_online_anomaly_state_1m(candles_1m=mutated, events=[_event()], config=config)

    cutoff = BASE_TS + 240_000
    past_rows = [row for row in rows if row.state_time_ms <= cutoff]
    mutated_past_rows = [row for row in mutated_rows if row.state_time_ms <= cutoff]
    assert past_rows == mutated_past_rows


def test_state_builder_excludes_technical_noise_events_before_ml_inputs() -> None:
    candles = [
        _candle(0, open_price=99.0, high=100.0, low=98.0, close=99.5),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=104.0, low=101.0, close=103.0),
    ]
    normal_event = _event(event_id="normal_event")
    noise_event = _event(
        event_id="noise_event",
        technical_noise_shock=True,
        excluded_by_data_quality_gate=True,
    )

    rows = build_online_anomaly_state_1m(candles_1m=candles, events=[noise_event, normal_event])

    assert rows
    assert {row.event_id for row in rows} == {"normal_event"}


def test_events_artifact_boundary_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "anomaly_events.csv"
    path.write_text(
        "event_id,symbol,event_start_time_ms,event_detection_time_ms,seed_time_ms,seed_open,seed_high,seed_low,seed_close,initial_move_pct,initial_volume_zscore,initial_quote_volume_zscore,initial_trade_count_zscore,detector_version,extra\n",
        encoding="utf-8",
    )

    with pytest.raises(AnomalyEventsArtifactError, match="columns must match"):
        load_anomaly_events_csv(path)


def test_grouped_csv_state_builder_streams_one_symbol_at_a_time(tmp_path: Path) -> None:
    candles_path = tmp_path / "candles_1m.csv"
    candles = [
        _candle(0, open_price=99.0, high=100.0, low=98.0, close=99.5),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
        _candle(2, open_price=102.0, high=104.0, low=101.0, close=103.0),
        replace(_candle(0, open_price=50.0, high=51.0, low=49.0, close=50.5), symbol="BBB/USDT:USDT"),
        replace(_candle(1, open_price=50.5, high=52.0, low=50.0, close=51.5), symbol="BBB/USDT:USDT"),
    ]
    _write_candles_csv(candles_path, candles)

    rows = list(
        iter_online_strategy_state_1m_from_grouped_csv(
            candles_path=candles_path,
            events=[_event()],
            config=OnlineStateBuilderConfig(max_state_minutes_after_detection=1),
        )
    )

    assert [row.event_id for row in rows] == ["evt_state_test", "evt_state_test"]
    assert {row.symbol for row in rows} == {"AAA/USDT:USDT"}


def test_grouped_csv_state_builder_rejects_repeated_symbol_groups(tmp_path: Path) -> None:
    candles_path = tmp_path / "candles_1m.csv"
    candles = [
        _candle(0, open_price=99.0, high=100.0, low=98.0, close=99.5),
        replace(_candle(0, open_price=50.0, high=51.0, low=49.0, close=50.5), symbol="BBB/USDT:USDT"),
        _candle(1, open_price=100.0, high=103.0, low=99.0, close=102.0),
    ]
    _write_candles_csv(candles_path, candles)

    with pytest.raises(CsvDataSourceError, match="grouped by symbol"):
        list(iter_online_strategy_state_1m_from_grouped_csv(candles_path=candles_path, events=[_event()]))


def test_run_mvp1_state_cli_writes_state_artifacts(tmp_path: Path) -> None:
    events_path = tmp_path / "anomaly_events.csv"
    out_dir = tmp_path / "state"
    _write_events_csv(events_path)

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-state",
            "--input",
            str(FIXTURE_DIR),
            "--events",
            str(events_path),
            "--out",
            str(out_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 online state artifacts written" in result.stdout
    assert (out_dir / "strategy_state_1m.csv").is_file()
    assert (out_dir / "strategy_protocol_audit.csv").is_file()
    assert (out_dir / "strategy_run_config.csv").is_file()
    assert (out_dir / "anomaly_state_1m.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "strategy_state_1m.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["state_time_ms"] == "1704067260000"
    assert rows[0]["running_high_asof_t"] == "101.0"
    assert rows[1]["running_high_asof_t"] == "102.0"

    with (out_dir / "strategy_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["technical_noise_shock_excluded_from_ml_train_validation_calibration_test"]["status"] == "PASS"
