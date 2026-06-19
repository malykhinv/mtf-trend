from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m
from anomaly_science.events import BroadAnomalyDetectorConfig, detect_broad_anomaly_events, events_to_artifact
from anomaly_science.events.run import run_mvp1_events


BASE_TS = 1_704_067_200_000


def _candle(index: int, *, close: float, quote_volume: float = 100.0) -> Candle1m:
    open_time_ms = BASE_TS + index * 60_000
    open_price = 100.0 if index == 0 else 100.0
    high = max(open_price, close) + 0.10
    low = min(open_price, close) - 0.10
    return Candle1m(
        symbol="AAA/USDT:USDT",
        open_time_ms=open_time_ms,
        available_time_ms=open_time_ms + 60_000,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        quote_volume=quote_volume,
        number_of_trades=10.0,
        taker_buy_quote_volume=quote_volume * 0.5,
    )


def _event(*, event_id: str, symbol: str, detection_offset_minutes: int) -> AnomalyEvent:
    detection_time_ms = BASE_TS + detection_offset_minutes * 60_000
    start_time_ms = detection_time_ms - 60_000
    return AnomalyEvent(
        event_id=event_id,
        symbol=symbol,
        event_start_time_ms=start_time_ms,
        event_detection_time_ms=detection_time_ms,
        seed_time_ms=start_time_ms,
        seed_open=100.0,
        seed_high=101.0,
        seed_low=99.0,
        seed_close=100.5,
        initial_move_pct=0.005,
        initial_volume_zscore=None,
        initial_quote_volume_zscore=None,
        initial_trade_count_zscore=None,
        detector_version="test",
    )


def test_broad_detector_uses_current_closed_candle_and_past_baseline_only() -> None:
    candles = [
        _candle(0, close=100.0),
        _candle(1, close=100.1),
        _candle(2, close=99.9),
        _candle(3, close=100.0),
        _candle(4, close=100.1),
        _candle(5, close=103.5),
        _candle(6, close=101.0),
    ]
    config = BroadAnomalyDetectorConfig(
        baseline_bars=5,
        min_baseline_bars=5,
        min_abs_return_pct=0.03,
        min_quote_volume_zscore=99.0,
        min_volume_zscore=99.0,
        min_trade_count_zscore=99.0,
        min_range_zscore=99.0,
    )

    events = detect_broad_anomaly_events(candles, config=config)
    mutated_future = list(candles)
    mutated_future[6] = _candle(6, close=160.0, quote_volume=1_000_000.0)
    mutated_events = detect_broad_anomaly_events(mutated_future, config=config)

    assert len(events) == 1
    assert events[0].symbol == "AAA/USDT:USDT"
    assert events[0].event_start_time_ms == BASE_TS + 5 * 60_000
    assert events[0].event_detection_time_ms == BASE_TS + 6 * 60_000
    assert events[0].seed_time_ms == events[0].event_start_time_ms
    assert events[0].initial_move_pct > 0.03
    assert events[0] == mutated_events[0]


def test_events_to_artifact_orders_rows_by_state_time() -> None:
    rows = events_to_artifact(
        [
            _event(event_id="late", symbol="BBB/USDT:USDT", detection_offset_minutes=3),
            _event(event_id="early", symbol="AAA/USDT:USDT", detection_offset_minutes=1),
            _event(event_id="same_time", symbol="CCC/USDT:USDT", detection_offset_minutes=1),
        ]
    )

    assert [row["event_id"] for row in rows] == ["early", "same_time", "late"]
    assert [row["state_time_ms"] for row in rows] == sorted(row["state_time_ms"] for row in rows)


def test_broad_detector_can_detect_volume_anomaly_without_return_threshold() -> None:
    candles = [
        _candle(0, close=100.0, quote_volume=100.0),
        _candle(1, close=100.0, quote_volume=110.0),
        _candle(2, close=100.0, quote_volume=90.0),
        _candle(3, close=100.0, quote_volume=105.0),
        _candle(4, close=100.0, quote_volume=95.0),
        _candle(5, close=100.2, quote_volume=1_000.0),
    ]
    config = BroadAnomalyDetectorConfig(
        baseline_bars=5,
        min_baseline_bars=5,
        min_abs_return_pct=0.10,
        min_quote_volume_zscore=4.0,
        min_volume_zscore=99.0,
        min_trade_count_zscore=99.0,
        min_range_zscore=99.0,
    )

    events = detect_broad_anomaly_events(candles, config=config)

    assert len(events) == 1
    assert events[0].initial_quote_volume_zscore is not None
    assert events[0].initial_quote_volume_zscore >= 4.0


def test_broad_detector_excludes_first_candle_after_raw_gap_from_candidates_and_baseline() -> None:
    candles = [
        _candle(0, close=100.0, quote_volume=100.0),
        _candle(1, close=100.1, quote_volume=105.0),
        _candle(2, close=99.9, quote_volume=95.0),
        _candle(3, close=100.0, quote_volume=100.0),
        _candle(4, close=100.1, quote_volume=100.0),
        _candle(10, close=150.0, quote_volume=1_000_000.0),  # first candle after 6m raw gap
        _candle(11, close=100.0, quote_volume=100.0),
        _candle(12, close=100.1, quote_volume=105.0),
        _candle(13, close=99.9, quote_volume=95.0),
        _candle(14, close=100.0, quote_volume=100.0),
        _candle(15, close=100.1, quote_volume=100.0),
        _candle(16, close=104.0, quote_volume=1_000.0),
    ]
    config = BroadAnomalyDetectorConfig(
        baseline_bars=5,
        min_baseline_bars=5,
        min_abs_return_pct=0.03,
        min_quote_volume_zscore=99.0,
        min_volume_zscore=99.0,
        min_trade_count_zscore=99.0,
        min_range_zscore=99.0,
    )

    events = detect_broad_anomaly_events(candles, config=config)

    assert [event.seed_time_ms for event in events] == [BASE_TS + 16 * 60_000]
    assert events[0].technical_noise_shock is False
    assert events[0].excluded_by_data_quality_gate is False
    assert events[0].raw_candle_gap_minutes == 1.0




def test_run_mvp1_events_enforces_data_quality_mask_before_trigger(tmp_path: Path) -> None:
    source_dir = tmp_path / "data"
    output_dir = tmp_path / "events"
    source_dir.mkdir()
    candles_1m_rows = [
        "symbol,open_time_ms,available_time_ms,open,high,low,close,volume,quote_volume,number_of_trades,taker_buy_quote_volume"
    ]
    for i in range(7):
        close = "-1.0" if i == 5 else "100.0"
        low = "-1.5" if i == 5 else "99.0"
        candles_1m_rows.append(
            f"AAA/USDT:USDT,{BASE_TS + i * 60_000},{BASE_TS + (i + 1) * 60_000},100.0,101.0,{low},{close},10.0,1000.0,20,500.0"
        )
    (source_dir / "candles_1m.csv").write_text("\n".join(candles_1m_rows) + "\n", encoding="utf-8")
    (source_dir / "candles_5m.csv").write_text(
        "symbol,open_time_ms,available_time_ms,open,high,low,close,volume,quote_volume,number_of_trades,taker_buy_quote_volume\n"
        f"AAA/USDT:USDT,{BASE_TS},{BASE_TS + 5 * 60_000},100.0,101.0,99.0,100.0,50.0,5000.0,100,2500.0\n",
        encoding="utf-8",
    )

    run_mvp1_events(
        input_dir=source_dir,
        out_dir=output_dir,
        config=BroadAnomalyDetectorConfig(
            baseline_bars=5,
            min_baseline_bars=5,
            min_abs_return_pct=0.03,
            min_quote_volume_zscore=99.0,
            min_volume_zscore=99.0,
            min_trade_count_zscore=99.0,
            min_range_zscore=99.0,
        ),
    )

    with (output_dir / "strategy_data_quality.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        quality_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert quality_by_name["candles_1m_positive_prices"]["status"] == "FAIL"
    assert quality_by_name["candles_1m_pre_trigger_quality_mask"]["status"] == "WARN"
    assert quality_by_name["candles_1m_pre_trigger_quality_mask"]["affected_rows"] == "2"

    with (output_dir / "strategy_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["data_quality_critical_fail_gate"]["status"] == "PASS"
    assert audit_by_name["data_quality_mask_enforced_before_trigger_generation"]["status"] == "PASS"

    with (output_dir / "strategy_events.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        event_rows = list(csv.DictReader(file_obj))
    assert event_rows == []

def test_run_mvp1_events_cli_writes_event_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "events"

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-events",
            "--input",
            "tests/fixtures/minimal_market_data",
            "--out",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / "strategy_events.csv").is_file()
    assert (output_dir / "anomaly_events.csv").is_file()
    assert (output_dir / "strategy_data_quality.csv").is_file()
    assert (output_dir / "anomaly_data_quality.csv").is_file()
    assert (output_dir / "symbol_universe_by_day.csv").is_file()
    assert (output_dir / "strategy_protocol_audit.csv").is_file()
    assert (output_dir / "strategy_run_config.csv").is_file()
    assert (output_dir / "artifact_manifest.json").is_file()

    with (output_dir / "strategy_events.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        header = next(csv.reader(file_obj))
    assert header == [
        "event_id",
        "symbol",
        "state_time_ms",
        "event_start_time_ms",
        "minutes_since_start",
        "is_trigger",
        "event_detection_time_ms",
        "seed_time_ms",
        "seed_open",
        "seed_high",
        "seed_low",
        "seed_close",
        "initial_move_pct",
        "initial_volume_zscore",
        "initial_quote_volume_zscore",
        "initial_trade_count_zscore",
        "technical_noise_shock",
        "raw_candle_gap_minutes",
        "excluded_by_data_quality_gate",
        "detector_version",
    ]

    with (output_dir / "strategy_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["base_strategy_contract_valid"]["status"] == "PASS"
    assert audit_by_name["required_data_streams_applied_before_trigger_generation"]["status"] == "PASS"

    with (output_dir / "strategy_run_config.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        run_config = {row["key"]: row["value"] for row in csv.DictReader(file_obj)}
    assert run_config["strategy_name"] == "broad_anomaly_v1_h30"
    assert run_config["required_data_streams"] == "liquidations=false;open_interest=false"
