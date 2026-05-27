import pandas as pd

from research_tools.htf_ltf_runner_discovery import (
    HtfLtfRunnerDiscoveryConfig,
    _build_ltf_entry_windows,
    _build_first_ltf_signal,
    _collect_symbol_candidates,
    _daily_summary,
    _future_runner_label,
    _htf_internal_ltf_features,
    _apply_same_symbol_overlap_filter,
    _score_candidate_rules,
    _score_entry_window_rules,
    _score_trade_rules,
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


def test_htf_internal_ltf_features_require_distributed_real_flow() -> None:
    ltf = pd.DataFrame(
        [
            {"timestamp": 0, "open": 100.0, "high": 100.5, "low": 99.9, "close": 100.2, "volume": 1.0, "quote_volume": 100.0, "number_of_trades": 10},
            {"timestamp": 5_000, "open": 100.2, "high": 100.7, "low": 100.1, "close": 100.5, "volume": 1.0, "quote_volume": 120.0, "number_of_trades": 11},
            {"timestamp": 10_000, "open": 100.5, "high": 100.9, "low": 100.4, "close": 100.7, "volume": 1.0, "quote_volume": 130.0, "number_of_trades": 12},
            {"timestamp": 15_000, "open": 100.7, "high": 101.1, "low": 100.6, "close": 100.9, "volume": 1.0, "quote_volume": 140.0, "number_of_trades": 13},
        ]
    )

    features = _htf_internal_ltf_features(ltf, start_ms=0, end_ms=20_000, htf_open=100.0, htf_close=101.0)

    assert features["htf_ltf_status"] == "ok"
    assert features["htf_ltf_trade_count_status"] == "ok"
    assert features["htf_ltf_sustained_flow_ok"] is True
    assert features["htf_ltf_quote_top1_share"] < 0.55


def test_candidate_rule_scores_keep_future_labels_as_research_only() -> None:
    candidates = pd.DataFrame(
        [
            {
                "symbol": "AAA/USDT:USDT",
                "htf_anomaly_gate": True,
                "dormancy_ok": True,
                "smooth_price_growth_ok": True,
                "pregrowth_oi_status": "ok",
                "pregrowth_oi_change_pct": 0.05,
                "htf_ltf_sustained_flow_ok": True,
                "htf_quote_ratio": 12.0,
                "htf_trade_ratio": 9.0,
                "pregrowth_return_pct": 0.01,
                "htf_ltf_quote_top1_share": 0.35,
                "runner_10pct_next_hour": True,
                "clean_runner_without_low_break": True,
                "anomaly_low_broken_before_runner": False,
            },
            {
                "symbol": "BBB/USDT:USDT",
                "htf_anomaly_gate": True,
                "dormancy_ok": True,
                "smooth_price_growth_ok": False,
                "pregrowth_oi_status": "missing",
                "pregrowth_oi_change_pct": float("nan"),
                "htf_ltf_sustained_flow_ok": False,
                "htf_quote_ratio": 7.0,
                "htf_trade_ratio": 6.0,
                "pregrowth_return_pct": 0.0,
                "htf_ltf_quote_top1_share": 0.80,
                "runner_10pct_next_hour": False,
                "clean_runner_without_low_break": False,
                "anomaly_low_broken_before_runner": False,
            },
        ]
    )

    scores = _score_candidate_rules(candidates)
    row = scores.loc[scores["rule"].eq("dormancy_smooth_price_oi")].iloc[0]

    assert row["events"] == 1
    assert row["clean_runner_share"] == 1.0
    assert bool(row["uses_future_label_as_entry_filter"]) is False


def test_trade_rule_scores_report_balance_and_top20_dependency() -> None:
    trades = pd.DataFrame(
        [
            {
                "symbol": f"AAA{i}/USDT:USDT",
                "status": "closed",
                "htf_anomaly_gate": True,
                "dormancy_ok": True,
                "smooth_price_growth_ok": True,
                "pregrowth_oi_status": "ok",
                "pregrowth_oi_change_pct": 0.01,
                "htf_ltf_sustained_flow_ok": True,
                "initial_risk_pct": 0.02,
                "ltf_quote_pace_ratio": 6.0,
                "ltf_trade_pace_ratio": 6.0,
                "ltf_second_half_return_pct": 0.001,
                "ltf_taker_buy_quote_share": 0.56,
                "net_return": 0.01 if i < 8 else -0.002,
                "mfe_pct": 0.03,
                "mae_pct": -0.01,
                "runner_10pct_next_hour": i < 6,
                "clean_runner_without_low_break": i < 5,
            }
            for i in range(10)
        ]
    )

    scores = _score_trade_rules(trades, scope="test")
    row = scores.loc[scores["rule"].eq("balanced_runner_with_oi")].iloc[0]

    assert row["closed_trades"] == 10
    assert row["win_rate"] == 0.8
    assert row["top20pct_trade_count"] == 2
    assert row["balance_score_0_100"] > 0


def test_live_filter_allows_parallel_different_symbols_but_blocks_same_symbol_overlap() -> None:
    trades = pd.DataFrame(
        [
            {
                "symbol": "AAA/USDT:USDT",
                "status": "closed",
                "entry_timestamp_ms": 1_000,
                "decision_timestamp_ms": 900,
                "exit_timestamp_ms": 10_000,
                "net_return": 0.01,
            },
            {
                "symbol": "BBB/USDT:USDT",
                "status": "closed",
                "entry_timestamp_ms": 2_000,
                "decision_timestamp_ms": 1_900,
                "exit_timestamp_ms": 9_000,
                "net_return": 0.02,
            },
            {
                "symbol": "AAA/USDT:USDT",
                "status": "closed",
                "entry_timestamp_ms": 3_000,
                "decision_timestamp_ms": 2_900,
                "exit_timestamp_ms": 8_000,
                "net_return": 0.03,
            },
        ]
    )

    filtered = _apply_same_symbol_overlap_filter(trades)

    assert filtered.iloc[0]["status"] == "closed"
    assert filtered.iloc[1]["status"] == "closed"
    assert filtered.iloc[1]["parallel_other_symbol_positions_at_entry"] == 1
    assert filtered.iloc[2]["status"] == "skipped"
    assert filtered.iloc[2]["skip_reason"] == "same_symbol_overlap_position_at_entry"


def test_candidate_collection_writes_only_htf_anomaly_gate_rows() -> None:
    htf = pd.DataFrame(
        [
            {"timestamp": i * 60_000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0, "quote_volume": 100.0, "number_of_trades": 10.0}
            for i in range(7)
        ]
    )
    htf.loc[4, ["high", "close", "quote_volume", "number_of_trades"]] = [104.0, 103.0, 1000.0, 100.0]
    ltf = pd.DataFrame(
        [
            {"timestamp": 240_000 + i * 30_000, "open": 100.0 + i, "high": 104.0 + i, "low": 99.0 + i, "close": 101.0 + i, "volume": 1.0, "quote_volume": 500.0, "number_of_trades": 50.0}
            for i in range(5)
        ]
    )
    config = HtfLtfRunnerDiscoveryConfig(
        htf_timeframe="1m",
        ltf_timeframe="30s",
        baseline_candles=2,
        dormancy_candles=2,
        pregrowth_candles=1,
        min_htf_quote_ratio=5.0,
        min_htf_trade_ratio=5.0,
        min_htf_return_pct=0.01,
        runner_horizon_minutes=1,
    )

    candidates, scanned_rows = _collect_symbol_candidates(
        symbol="AAA/USDT:USDT",
        htf=htf,
        ltf=ltf,
        oi=pd.DataFrame(),
        config=config,
    )

    assert scanned_rows > len(candidates)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "ok"
    assert candidates[0]["htf_anomaly_gate"] is True
    assert "prior_spike_count_24h" in candidates[0]


def test_entry_windows_are_available_only_after_closed_ltf_and_next_open() -> None:
    ltf = pd.DataFrame(
        [
            {"timestamp": 0, "open": 100.0, "high": 100.3, "low": 99.9, "close": 100.2, "volume": 1.0, "quote_volume": 160.0, "number_of_trades": 16.0},
            {"timestamp": 5_000, "open": 100.2, "high": 100.5, "low": 100.1, "close": 100.4, "volume": 1.0, "quote_volume": 150.0, "number_of_trades": 15.0},
            {"timestamp": 10_000, "open": 100.4, "high": 100.7, "low": 100.3, "close": 100.5, "volume": 1.0, "quote_volume": 145.0, "number_of_trades": 14.0},
            {"timestamp": 15_000, "open": 100.5, "high": 100.8, "low": 100.4, "close": 100.6, "volume": 1.0, "quote_volume": 140.0, "number_of_trades": 13.0},
            {"timestamp": 20_000, "open": 100.6, "high": 101.0, "low": 100.5, "close": 100.8, "volume": 1.0, "quote_volume": 130.0, "number_of_trades": 12.0},
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
        "baseline_quote_volume_median": 600.0,
        "baseline_number_of_trades_median": 60.0,
        "pregrowth_oi_end": float("nan"),
        "current_vs_prior_spike_median_quote": 1.2,
        "future_label_available_at_entry": False,
        "runner_10pct_next_hour": False,
        "clean_runner_without_low_break": False,
    }
    config = HtfLtfRunnerDiscoveryConfig(
        ltf_min_confirm_candles=2,
        ltf_max_confirm_candles=4,
        min_ltf_quote_pace_ratio=3.0,
        min_ltf_trade_pace_ratio=3.0,
        max_entry_drift_pct=0.01,
        max_initial_risk_pct=0.05,
    )

    windows = _build_ltf_entry_windows(candidate, ltf=ltf, oi=pd.DataFrame(), config=config)
    first = windows[0]

    assert [row["confirmation_candles"] for row in windows] == [2, 4]
    assert first["decision_timestamp_ms"] == 5_000
    assert first["decision_available_timestamp_ms"] == 10_000
    assert first["entry_timestamp_ms"] == 10_000
    assert first["window_execution_ok"] is True
    assert first["ltf_quote_decay_under50"] is False
    assert first["window_volume_sustain_ok"] is True
    assert first["window_future_label_available_at_entry"] is False


def test_entry_window_rule_scores_apply_same_symbol_overlap_per_rule() -> None:
    trades = pd.DataFrame(
        [
            {
                "symbol": "AAA/USDT:USDT",
                "status": "closed",
                "entry_timestamp_ms": 1_000,
                "decision_timestamp_ms": 900,
                "exit_timestamp_ms": 10_000,
                "net_return": 0.02,
                "mfe_pct": 0.04,
                "mae_pct": -0.01,
                "runner_10pct_next_hour": True,
                "clean_runner_without_low_break": True,
                "ltf_quote_decay_under50": False,
                "ltf_confirm_return_pct": 0.01,
                "window_volume_sustain_ok": True,
                "window_volume_sustain_above_prior_median_ok": True,
                "window_volume_sustain_above_prior_150pct_ok": False,
                "window_oi_nonnegative_from_pregrowth_end": True,
                "initial_risk_pct": 0.02,
                "ltf_quote_pace_ratio": 6.0,
                "ltf_trade_pace_ratio": 6.0,
            },
            {
                "symbol": "AAA/USDT:USDT",
                "status": "closed",
                "entry_timestamp_ms": 2_000,
                "decision_timestamp_ms": 1_900,
                "exit_timestamp_ms": 9_000,
                "net_return": -0.01,
                "mfe_pct": 0.01,
                "mae_pct": -0.02,
                "runner_10pct_next_hour": False,
                "clean_runner_without_low_break": False,
                "ltf_quote_decay_under50": False,
                "ltf_confirm_return_pct": 0.01,
                "window_volume_sustain_ok": True,
                "window_volume_sustain_above_prior_median_ok": True,
                "window_volume_sustain_above_prior_150pct_ok": False,
                "window_oi_nonnegative_from_pregrowth_end": True,
                "initial_risk_pct": 0.02,
                "ltf_quote_pace_ratio": 6.0,
                "ltf_trade_pace_ratio": 6.0,
            },
            {
                "symbol": "BBB/USDT:USDT",
                "status": "closed",
                "entry_timestamp_ms": 2_000,
                "decision_timestamp_ms": 1_900,
                "exit_timestamp_ms": 9_000,
                "net_return": 0.01,
                "mfe_pct": 0.02,
                "mae_pct": -0.01,
                "runner_10pct_next_hour": False,
                "clean_runner_without_low_break": False,
                "ltf_quote_decay_under50": False,
                "ltf_confirm_return_pct": 0.01,
                "window_volume_sustain_ok": True,
                "window_volume_sustain_above_prior_median_ok": True,
                "window_volume_sustain_above_prior_150pct_ok": False,
                "window_oi_nonnegative_from_pregrowth_end": True,
                "initial_risk_pct": 0.02,
                "ltf_quote_pace_ratio": 6.0,
                "ltf_trade_pace_ratio": 6.0,
            },
        ]
    )

    scores = _score_entry_window_rules(trades, scope="test", apply_same_symbol_filter=True)
    row = scores.loc[scores["rule"].eq("volume_sustain")].iloc[0]

    assert row["signals"] == 3
    assert row["closed_trades"] == 2
    assert row["win_rate"] == 1.0


def test_daily_summary_groups_closed_trades_by_entry_day() -> None:
    trades = pd.DataFrame(
        [
            {
                "symbol": "AAA/USDT:USDT",
                "status": "closed",
                "entry_timestamp_utc": "2026-05-01T01:00:00+00:00",
                "net_return": 0.02,
                "mfe_pct": 0.04,
                "mae_pct": -0.01,
                "runner_10pct_next_hour": True,
                "clean_runner_without_low_break": True,
            },
            {
                "symbol": "BBB/USDT:USDT",
                "status": "closed",
                "entry_timestamp_utc": "2026-05-01T02:00:00+00:00",
                "net_return": -0.01,
                "mfe_pct": 0.01,
                "mae_pct": -0.02,
                "runner_10pct_next_hour": False,
                "clean_runner_without_low_break": False,
            },
            {
                "symbol": "AAA/USDT:USDT",
                "status": "skipped",
                "entry_timestamp_utc": "2026-05-02T01:00:00+00:00",
                "net_return": 0.50,
            },
        ]
    )

    summary = _daily_summary(trades)
    row = summary.iloc[0]

    assert len(summary) == 1
    assert row["entry_day_utc"] == "2026-05-01"
    assert row["closed_trades"] == 2
    assert row["symbols"] == 2
    assert row["win_rate"] == 0.5
    assert row["sum_net_return"] == 0.01
    assert row["runner_10pct_label_share"] == 0.5
