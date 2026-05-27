import pandas as pd

from research_tools.htf_ltf_runner_discovery import (
    HtfLtfRunnerDiscoveryConfig,
    _build_first_ltf_signal,
    _future_runner_label,
    _simulate_no_tp_runner_trade,
)


def test_runner_label_marks_low_break_before_10pct_hit() -> None:
    frame = pd.DataFrame(
        [
            {"timestamp": 0, "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0, "volume": 1.0},
            {"timestamp": 5_000, "open": 101.0, "high": 111.0, "low": 100.5, "close": 110.0, "volume": 1.0},
        ]
    )

    label = _future_runner_label(
        frame,
        start_ms=0,
        horizon_ms=60_000,
        reference_price=100.0,
        anomaly_low=99.0,
        target_return_pct=0.10,
    )

    assert label["runner_10pct_next_hour"] is True
    assert label["anomaly_low_broken_before_runner"] is True
    assert label["clean_runner_without_low_break"] is False


def test_ltf_signal_enters_next_open_after_closed_confirmation() -> None:
    ltf = pd.DataFrame(
        [
            {"timestamp": i * 5_000, "open": 100.0 + i * 0.1, "high": 100.4 + i * 0.1, "low": 100.0 + i * 0.1, "close": 100.2 + i * 0.1, "volume": 10.0, "quote_volume": 1000.0, "number_of_trades": 10.0, "taker_buy_quote_volume": 700.0}
            for i in range(8)
        ]
    )
    candidate = {
        "symbol": "AAA/USDT:USDT",
        "status": "ok",
        "setup_nature": "test",
        "htf_close_ms": 0,
        "htf_timeframe": "1m",
        "ltf_timeframe": "5s",
        "anomaly_low": 99.0,
        "anomaly_close": 100.0,
        "baseline_quote_volume_median": 1000.0,
        "baseline_number_of_trades_median": 10.0,
        "future_label_available_at_entry": False,
        "runner_10pct_next_hour": False,
        "clean_runner_without_low_break": False,
    }
    config = HtfLtfRunnerDiscoveryConfig(
        min_ltf_confirm_return_pct=0.001,
        min_ltf_quote_pace_ratio=1.0,
        min_ltf_trade_pace_ratio=1.0,
        min_ltf_quote_acceleration=0.1,
        min_ltf_trade_acceleration=0.1,
        max_initial_risk_pct=0.05,
    )

    signal = _build_first_ltf_signal(candidate, ltf=ltf, oi=pd.DataFrame(), config=config)

    assert signal is not None
    assert signal["decision_timestamp_ms"] == 25_000
    assert signal["decision_available_timestamp_ms"] == 30_000
    assert signal["entry_timestamp_ms"] == 30_000


def test_no_tp_runner_simulation_never_marks_tp_hit() -> None:
    ltf = pd.DataFrame(
        [
            {"timestamp": 0, "open": 100.0, "high": 100.5, "low": 99.8, "close": 100.2, "volume": 1.0},
            {"timestamp": 5_000, "open": 100.2, "high": 115.0, "low": 100.1, "close": 112.0, "volume": 1.0},
            {"timestamp": 10_000, "open": 112.0, "high": 113.0, "low": 111.0, "close": 112.5, "volume": 1.0},
        ]
    )
    signal = {
        "symbol": "AAA/USDT:USDT",
        "setup_nature": "test",
        "decision_timestamp_ms": 0,
        "decision_timestamp_utc": "",
        "entry_timestamp_ms": 0,
        "entry_timestamp_utc": "",
        "entry_price": 100.0,
        "initial_stop": 99.0,
        "runner_10pct_next_hour": True,
        "clean_runner_without_low_break": True,
    }

    trade = _simulate_no_tp_runner_trade(signal, ltf=ltf, config=HtfLtfRunnerDiscoveryConfig(max_hold_candles=3))

    assert trade["status"] == "closed"
    assert trade["tp1_hit"] is False
    assert trade["tp_model"] == "none"
    assert trade["net_return"] > 0
