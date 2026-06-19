from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.decision import EXPECTED_VALUE_TEMPORAL_CONTRACT, ExpectedValueRow
from anomaly_science.contracts.market import Candle1m, FundingRate
from anomaly_science.contracts.simulation import TRADE_SIMULATION_TEMPORAL_CONTRACT
from anomaly_science.decision import expected_value_rows_to_artifact
from anomaly_science.simulation import (
    TradeSimulationConfig,
    TradeSimulationInputError,
    build_trade_simulation_rows,
    load_anomaly_trade_simulation_csv,
    run_mvp1_trade_simulation,
    trade_simulation_rows_to_artifact,
)

BASE_MS = 1_704_067_200_000
ONE_MINUTE_MS = 60_000


def _candle(index: int, *, open_price: float, high: float, low: float, close: float) -> Candle1m:
    open_time_ms = BASE_MS + index * ONE_MINUTE_MS
    return Candle1m(
        symbol="AAA/USDT:USDT",
        open_time_ms=open_time_ms,
        available_time_ms=open_time_ms + ONE_MINUTE_MS,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        quote_volume=100.0,
        number_of_trades=10.0,
        taker_buy_quote_volume=50.0,
    )


def _decision(event_id: str = "sim_long") -> ExpectedValueRow:
    return ExpectedValueRow(
        ev_version="mvp1_expected_value_oos_calibrated_proxy_v1",
        strategy_name="anomaly",
        strategy_version="broad_anomaly_v1_h30",
        event_id=event_id,
        symbol="AAA/USDT:USDT",
        state_time_ms=BASE_MS,
        snapshot_time_ms=BASE_MS,
        feature_cutoff_time_ms=BASE_MS,
        future_start_time_ms=BASE_MS + ONE_MINUTE_MS,
        target_horizon_minutes=30,
        entry_reference_price=100.0,
        core_atr_1440=2.0,
        stop_distance=2.0,
        target_distance=3.0,
        fee_bps=4.0,
        slippage_bps=2.0,
        cost_penalty=0.1,
        p_follow_through_long=0.8,
        p_adverse_long=0.1,
        p_follow_through_short=0.1,
        p_adverse_short=0.8,
        confidence_calibrated=0.8,
        RR_long_proxy=1.5,
        RR_short_proxy=1.5,
        EV_long=2.1,
        EV_short=-1.4,
        EV_wait=0.0,
        EV_no_trade=0.0,
        best_action="long",
        is_prediction_confident=True,
        is_RR_still_acceptable=True,
        temporal_contract=EXPECTED_VALUE_TEMPORAL_CONTRACT,
    )


def test_trade_simulation_uses_next_open_with_slippage_and_stop_first() -> None:
    decision = _decision()
    rows = build_trade_simulation_rows(
        candles_1m=[
            _candle(0, open_price=100.0, high=100.5, low=99.5, close=100.0),
            _candle(1, open_price=101.0, high=105.0, low=98.0, close=102.0),
        ],
        decision_rows=[decision],
        config=TradeSimulationConfig(target_horizon_minutes=30),
    )

    row = rows[0]

    assert row.entry_reference_time_ms == BASE_MS + ONE_MINUTE_MS
    assert row.entry_price > row.entry_reference_open
    assert row.exit_reason == "stop_loss"
    assert row.barrier_resolution == "stop_loss_first"
    assert row.net_pnl < 0.0
    assert row.temporal_contract == TRADE_SIMULATION_TEMPORAL_CONTRACT


def test_trade_simulation_debits_funding_inside_hold() -> None:
    rows = build_trade_simulation_rows(
        candles_1m=[
            _candle(0, open_price=100.0, high=100.5, low=99.5, close=100.0),
            _candle(1, open_price=101.0, high=104.5, low=100.5, close=103.0),
        ],
        decision_rows=[_decision("funding")],
        funding_rates=[
            FundingRate(
                symbol="AAA/USDT:USDT",
                timestamp_ms=BASE_MS + ONE_MINUTE_MS,
                funding_rate=0.01,
            )
        ],
        config=TradeSimulationConfig(target_horizon_minutes=30),
    )

    row = rows[0]
    assert row.funding_cost > 0.0
    assert row.net_pnl == row.gross_pnl - row.total_cost - row.funding_cost


def test_trade_simulation_blocks_parallel_positions_per_symbol_strategy_variant() -> None:
    first = _decision("first")
    second = _decision("second")
    object.__setattr__(second, "state_time_ms", BASE_MS + ONE_MINUTE_MS)
    object.__setattr__(second, "snapshot_time_ms", BASE_MS + ONE_MINUTE_MS)
    object.__setattr__(second, "feature_cutoff_time_ms", BASE_MS + ONE_MINUTE_MS)
    object.__setattr__(second, "future_start_time_ms", BASE_MS + 2 * ONE_MINUTE_MS)

    rows = build_trade_simulation_rows(
        candles_1m=[
            _candle(0, open_price=100.0, high=100.5, low=99.5, close=100.0),
            _candle(1, open_price=101.0, high=101.5, low=100.5, close=101.0),
            _candle(2, open_price=102.0, high=102.5, low=101.5, close=102.0),
            _candle(30, open_price=103.0, high=103.5, low=102.5, close=103.0),
        ],
        decision_rows=[first, second],
        config=TradeSimulationConfig(target_horizon_minutes=30),
    )

    assert [row.event_id for row in rows] == ["first"]


def test_trade_simulation_rejects_strategy_horizon_mismatch() -> None:
    with pytest.raises(TradeSimulationInputError, match="does not match strategy horizon"):
        build_trade_simulation_rows(
            candles_1m=[],
            decision_rows=[],
            config=TradeSimulationConfig(
                strategy_version="broad_anomaly_v1_h30",
                target_horizon_minutes=60,
            ),
        )


def test_trade_simulation_artifact_roundtrip(tmp_path: Path) -> None:
    rows = build_trade_simulation_rows(
        candles_1m=[
            _candle(0, open_price=100.0, high=100.5, low=99.5, close=100.0),
            _candle(1, open_price=101.0, high=104.5, low=100.5, close=103.0),
        ],
        decision_rows=[_decision("roundtrip")],
    )
    path = tmp_path / "anomaly_trade_simulation.csv"
    write_csv_artifact(path, trade_simulation_rows_to_artifact(rows), get_artifact_schema("anomaly_trade_simulation.csv"))

    loaded = load_anomaly_trade_simulation_csv(path)

    assert loaded[0].event_id == rows[0].event_id
    assert loaded[0].exit_reason == rows[0].exit_reason
    assert loaded[0].entry_price == rows[0].entry_price


def test_run_mvp1_trade_simulation_cli_writes_artifacts(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    decision_path = tmp_path / "anomaly_decision_timing.csv"
    out_dir = tmp_path / "simulation"
    candle_schema = (
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
    )
    with (input_dir / "candles_1m.csv").open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=candle_schema)
        writer.writeheader()
        for candle in [
            _candle(0, open_price=100.0, high=100.5, low=99.5, close=100.0),
            _candle(1, open_price=101.0, high=104.5, low=100.5, close=103.0),
        ]:
            writer.writerow(
                {
                    "symbol": candle.symbol,
                    "open_time_ms": candle.open_time_ms,
                    "available_time_ms": candle.available_time_ms,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "quote_volume": candle.quote_volume,
                    "number_of_trades": candle.number_of_trades,
                    "taker_buy_quote_volume": candle.taker_buy_quote_volume,
                }
            )
    write_csv_artifact(
        decision_path,
        expected_value_rows_to_artifact([_decision("cli")]),
        get_artifact_schema("anomaly_decision_timing.csv"),
    )

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-trade-simulation",
            "--input",
            str(input_dir),
            "--decision-timing",
            str(decision_path),
            "--out",
            str(out_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 trade simulation artifacts written" in result.stdout
    assert (out_dir / "anomaly_trade_simulation.csv").is_file()
    assert (out_dir / "strategy_trade_simulation.csv").is_file()
    assert (out_dir / "anomaly_trade_simulation_metrics.csv").is_file()
    assert (out_dir / "anomaly_protocol_audit.csv").is_file()
    assert (out_dir / "anomaly_run_config.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "anomaly_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["trade_simulation_after_calibration_and_decision_timing"]["status"] == "PASS"
    assert audit_by_name["pessimistic_entry_price_includes_slippage_penalty"]["status"] == "PASS"
    assert audit_by_name["intracandle_double_barrier_resolved_as_stop_loss_first"]["status"] == "PASS"
    assert audit_by_name["fixed_percent_stop_target_forbidden"]["status"] == "PASS"
    assert audit_by_name["funding_rate_boundary"]["status"] == "WARN"
    assert audit_by_name["protocol_interpretation_gate"]["status"] == "PASS"

    with (out_dir / "anomaly_trade_simulation_metrics.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        metrics_by_name = {row["metric_name"]: row for row in csv.DictReader(file_obj)}
    assert metrics_by_name["always_no_trade_baseline_net_pnl"]["metric_value"] == "0"
    assert "delta_vs_always_no_trade_net_pnl" in metrics_by_name

    with (out_dir / "anomaly_run_config.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        run_config = {row["key"]: row["value"] for row in csv.DictReader(file_obj)}
    assert run_config["strategy_name"] == "broad_anomaly_v1_h30"
    assert run_config["stop_loss_atr_1440"] == "1.1"


def test_run_mvp1_trade_simulation_debits_present_funding_rate_stream(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    decision_path = tmp_path / "anomaly_decision_timing.csv"
    out_dir = tmp_path / "simulation"
    candle_schema = (
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
    )
    with (input_dir / "candles_1m.csv").open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=candle_schema)
        writer.writeheader()
        for candle in [
            _candle(0, open_price=100.0, high=100.5, low=99.5, close=100.0),
            _candle(1, open_price=101.0, high=104.5, low=100.5, close=103.0),
        ]:
            writer.writerow(
                {
                    "symbol": candle.symbol,
                    "open_time_ms": candle.open_time_ms,
                    "available_time_ms": candle.available_time_ms,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "quote_volume": candle.quote_volume,
                    "number_of_trades": candle.number_of_trades,
                    "taker_buy_quote_volume": candle.taker_buy_quote_volume,
                }
            )
    (input_dir / "funding_rate.csv").write_text(
        f"symbol,timestamp_ms,funding_rate\nAAA/USDT:USDT,{BASE_MS + ONE_MINUTE_MS},0.01\n",
        encoding="utf-8",
    )
    write_csv_artifact(
        decision_path,
        expected_value_rows_to_artifact([_decision("funding_cli")]),
        get_artifact_schema("anomaly_decision_timing.csv"),
    )

    run_mvp1_trade_simulation(
        input_dir=input_dir,
        decision_timing_path=decision_path,
        out_dir=out_dir,
    )

    with (out_dir / "anomaly_trade_simulation.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert float(rows[0]["funding_cost"]) > 0.0

    with (out_dir / "anomaly_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["funding_rate_boundary"]["status"] == "PASS"
