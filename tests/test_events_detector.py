from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

from anomaly_science.contracts.market import Candle1m
from anomaly_science.events import BroadAnomalyDetectorConfig, detect_broad_anomaly_events


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
    assert (output_dir / "anomaly_events.csv").is_file()
    assert (output_dir / "strategy_events.csv").is_file()
    assert (output_dir / "anomaly_data_quality.csv").is_file()
    assert (output_dir / "symbol_universe_by_day.csv").is_file()
    assert (output_dir / "anomaly_protocol_audit.csv").is_file()
    assert (output_dir / "anomaly_run_config.csv").is_file()
    assert (output_dir / "artifact_manifest.json").is_file()

    with (output_dir / "anomaly_events.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        header = next(csv.reader(file_obj))
    assert header == [
        "event_id",
        "symbol",
        "event_start_time_ms",
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

    with (output_dir / "anomaly_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["base_strategy_contract_valid"]["status"] == "PASS"
