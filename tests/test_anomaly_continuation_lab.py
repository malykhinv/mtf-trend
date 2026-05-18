import pandas as pd

from research_tools.anomaly_continuation_lab import (
    build_oi_context_status,
    collect_symbol_anomaly_rows,
    compute_start_verticality_metrics,
    enrich_candidates_with_open_interest,
)
from research_tools.anomaly_runner_review import RunnerTarget, build_runner_review, parse_runner_target
from research_tools import anomaly_strategy_backtest
from research_tools.anomaly_strategy_backtest import (
    AnomalyBacktestConfig,
    AnomalyLabConfig,
    build_anomaly_signals,
    simulate_long_signal,
)


def test_start_verticality_score_rewards_straight_impulse() -> None:
    vertical = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0, 13.0],
            "high": [11.2, 12.2, 13.2, 14.2],
            "low": [9.9, 10.9, 11.9, 12.9],
            "close": [11.0, 12.0, 13.0, 14.0],
        }
    )
    choppy = pd.DataFrame(
        {
            "open": [10.0, 12.0, 10.8, 12.4],
            "high": [12.4, 12.5, 12.6, 12.8],
            "low": [9.8, 10.7, 10.6, 11.2],
            "close": [12.0, 10.8, 12.4, 11.6],
        }
    )

    vertical_metrics = compute_start_verticality_metrics(vertical)
    choppy_metrics = compute_start_verticality_metrics(choppy)

    assert vertical_metrics["start_verticality_score"] > choppy_metrics["start_verticality_score"]
    assert vertical_metrics["start_verticality_path_efficiency"] > choppy_metrics["start_verticality_path_efficiency"]
    assert vertical_metrics["start_verticality_slope_pct_per_candle"] > 0


def test_start_verticality_score_zero_for_negative_segment() -> None:
    segment = pd.DataFrame(
        {
            "open": [10.0, 9.8, 9.6],
            "high": [10.1, 9.9, 9.7],
            "low": [9.7, 9.5, 9.2],
            "close": [9.8, 9.6, 9.4],
        }
    )

    metrics = compute_start_verticality_metrics(segment)

    assert metrics["start_verticality_score"] == 0.0
    assert metrics["start_verticality_slope_pct_per_candle"] < 0


def test_runner_review_ranks_target_date_by_future_return() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "IO/USDT:USDT",
                "timestamp_utc": "2026-05-06T06:00:00+00:00",
                "decision_timestamp_utc": "2026-05-06T06:04:00+00:00",
                "future_ret_high_after_decision": 0.40,
                "hold_count_next_n_candles": 2,
                "start_verticality_score": 0.5,
            },
            {
                "symbol": "IO/USDT:USDT",
                "timestamp_utc": "2026-05-06T07:00:00+00:00",
                "decision_timestamp_utc": "2026-05-06T07:04:00+00:00",
                "future_ret_high_after_decision": 0.10,
                "hold_count_next_n_candles": 4,
                "start_verticality_score": 0.9,
            },
        ]
    )

    review = build_runner_review(
        frame,
        targets=[RunnerTarget(symbol="IO/USDT:USDT", local_date="2026-05-06")],
        local_utc_offset_hours=2,
        top_n=1,
    )

    assert len(review) == 1
    assert review.iloc[0]["timestamp_utc"] == "2026-05-06T06:00:00+00:00"
    assert review.iloc[0]["status"] == "ranked_by_future_for_review"


def test_parse_runner_target_adds_default_usdt_suffix() -> None:
    target = parse_runner_target("LAB=2026-05-01")

    assert target.symbol == "LAB/USDT:USDT"
    assert target.local_date == "2026-05-01"


def test_anomaly_signal_filter_uses_decision_time_features() -> None:
    candidates = pd.DataFrame(
        [
            {
                "symbol": "TEST/USDT:USDT",
                "timestamp_ms": 0,
                "decision_timestamp_ms": 240_000,
                "decision_close": 11.0,
                "decision_box_low": 9.8,
                "decision_box_high": 11.0,
                "decision_box_range": 1.2,
                "decision_ema20": 10.0,
                "price_retention_next_n": 0.8,
                "start_verticality_score": 0.4,
                "hold_count_next_n_candles": 2,
                "prior_up_down_whipsaw_to_impulse_range": 0.1,
            },
            {
                "symbol": "TEST/USDT:USDT",
                "timestamp_ms": 300_000,
                "decision_timestamp_ms": 540_000,
                "decision_close": 10.0,
                "decision_box_low": 9.8,
                "decision_box_high": 10.2,
                "decision_box_range": 0.4,
                "decision_ema20": 9.9,
                "price_retention_next_n": 0.4,
                "start_verticality_score": 0.8,
                "hold_count_next_n_candles": 4,
                "prior_up_down_whipsaw_to_impulse_range": 0.1,
            },
        ]
    )

    signals = build_anomaly_signals(
        candidates,
        config=AnomalyBacktestConfig(lab_config=AnomalyLabConfig()),
    )

    assert signals["decision_timestamp_ms"].tolist() == [240_000]


def test_anomaly_signal_filter_can_apply_anti_exhaustion_caps() -> None:
    candidates = pd.DataFrame(
        [
            {
                "symbol": "TEST/USDT:USDT",
                "timestamp_ms": 0,
                "decision_timestamp_ms": 240_000,
                "decision_close": 11.0,
                "decision_box_low": 9.8,
                "decision_box_high": 11.0,
                "decision_box_range": 1.2,
                "decision_ema20": 10.0,
                "price_retention_next_n": 0.85,
                "start_verticality_score": 0.5,
                "hold_count_next_n_candles": 2,
                "prior_up_down_whipsaw_to_impulse_range": 0.1,
                "oi_status": "ok",
                "oi_change_pct_3x5m": 0.04,
                "start_quote_ratio": 40.0,
                "start_trade_ratio": 18.0,
                "start_avg_trade_quote_size_ratio": 2.0,
                "start_quote_ratio_per_abs_return": 2_000.0,
                "start_range_pct_ratio_to_baseline": 6.0,
                "next_n_taker_buy_quote_share_mean": 0.52,
            },
            {
                "symbol": "TEST/USDT:USDT",
                "timestamp_ms": 300_000,
                "decision_timestamp_ms": 540_000,
                "decision_close": 11.0,
                "decision_box_low": 9.8,
                "decision_box_high": 11.0,
                "decision_box_range": 1.2,
                "decision_ema20": 10.0,
                "price_retention_next_n": 0.99,
                "start_verticality_score": 0.5,
                "hold_count_next_n_candles": 2,
                "prior_up_down_whipsaw_to_impulse_range": 0.1,
                "oi_status": "ok",
                "oi_change_pct_3x5m": 0.04,
                "start_quote_ratio": 180.0,
                "start_trade_ratio": 90.0,
                "start_avg_trade_quote_size_ratio": 12.0,
                "start_quote_ratio_per_abs_return": 40_000.0,
                "start_range_pct_ratio_to_baseline": 40.0,
                "next_n_taker_buy_quote_share_mean": 0.42,
            },
        ]
    )

    signals = build_anomaly_signals(
        candidates,
        config=AnomalyBacktestConfig(
            lab_config=AnomalyLabConfig(),
            min_hold_count=2,
            min_oi_change_pct_3x5m=0.03,
            require_oi_status_ok=True,
            max_start_quote_ratio=80.0,
            max_start_trade_ratio=40.0,
            max_start_avg_trade_quote_size_ratio=7.0,
            max_start_quote_ratio_per_abs_return=15_000.0,
            max_start_range_pct_ratio_to_baseline=25.0,
            min_next_taker_buy_quote_share=0.48,
            max_price_retention=0.96,
        ),
    )

    assert signals["decision_timestamp_ms"].tolist() == [240_000]


def test_simulate_long_signal_takes_tp1_and_trails_remaining() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [0, 60_000, 120_000, 180_000, 240_000, 300_000, 360_000, 420_000],
            "open": [10.0, 10.4, 10.8, 11.0, 11.2, 12.0, 12.2, 12.1],
            "high": [10.6, 10.9, 11.2, 11.4, 12.1, 13.2, 12.4, 12.2],
            "low": [9.9, 10.3, 10.7, 10.9, 11.1, 11.8, 11.4, 10.9],
            "close": [10.5, 10.8, 11.0, 11.2, 12.0, 12.2, 12.0, 11.0],
        }
    )
    signal = pd.Series(
        {
            "symbol": "TEST/USDT:USDT",
            "timestamp_ms": 0,
            "decision_timestamp_ms": 180_000,
            "decision_close": 11.2,
            "outcome_label": "test",
        }
    )

    result = simulate_long_signal(
        frame,
        signal,
        config=AnomalyBacktestConfig(
            lab_config=AnomalyLabConfig(),
            fee_rate=0.0,
            trail_lookback_candles=2,
            max_hold_candles=4,
        ),
    )

    assert result["status"] == "closed"
    assert result["tp1_hit"] is True
    assert result["tp1_fill_model"] == "conservative_limit_proxy"
    assert result["tp1_fill_status"] == "filled_conservative_trade_through"
    assert result["exit_reason"] == "tp1_full_exit"
    assert result["gross_r"] > 0


def test_simulate_long_signal_does_not_fill_tp1_on_exact_touch() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [0, 60_000, 120_000, 180_000, 240_000, 300_000],
            "open": [10.0, 10.4, 10.8, 11.0, 11.2, 11.2],
            "high": [10.6, 10.9, 11.2, 11.4, 12.0, 12.5],
            "low": [9.9, 10.3, 10.7, 10.9, 11.1, 11.0],
            "close": [10.5, 10.8, 11.0, 11.2, 11.3, 11.1],
        }
    )
    signal = pd.Series(
        {
            "symbol": "TEST/USDT:USDT",
            "timestamp_ms": 0,
            "decision_timestamp_ms": 180_000,
            "decision_close": 11.2,
            "outcome_label": "test",
        }
    )

    result = simulate_long_signal(
        frame,
        signal,
        config=AnomalyBacktestConfig(
            lab_config=AnomalyLabConfig(),
            fee_rate=0.0,
            max_hold_candles=2,
        ),
    )

    assert result["status"] == "closed"
    assert result["tp1_hit"] is False
    assert result["tp1_fill_status"] == "touched_not_filled_conservative"


def test_pre_context_universe_does_not_require_missing_mark_basis_column() -> None:
    candidates = pd.DataFrame(
        [
            {
                "symbol": "TEST/USDT:USDT",
                "setup_timeframe": "5m",
                "entry_timeframe": "30s",
                "price_retention_next_n": 0.9,
                "start_verticality_score": 0.5,
                "hold_count_next_n_candles": 3,
                "decision_close": 11.2,
                "decision_box_low": 10.8,
                "decision_box_high": 11.4,
                "decision_box_range": 0.6,
                "decision_ema20": 10.9,
                "timestamp_ms": 0,
                "decision_timestamp_ms": 180_000,
                "start_quote_ratio": 5.0,
                "start_trade_ratio": 5.0,
                "baseline_quote_volume_median": 2_000.0,
                "start_avg_trade_quote_size_ratio": 1.0,
                "start_quote_ratio_per_abs_return": 1_000.0,
                "start_range_pct_ratio_to_baseline": 6.0,
                "prior_up_down_whipsaw_to_impulse_range": 0.1,
                "flow_hold_count_next_n_candles": 3,
                "prior_spike_count_72h": 0,
                "prior_fast_fade_count_72h": 0,
                "start_lower_wick_to_range": 0.1,
                "start_upper_wick_to_range": 0.1,
                "next_n_taker_buy_quote_share_mean": 0.5,
                "start_taker_buy_quote_share_delta": 0.0,
                "next_n_taker_buy_quote_share_delta": 0.0,
                "start_trade_ratio_per_abs_return": 100.0,
            }
        ]
    )

    result = anomaly_strategy_backtest._build_pre_context_signal_universe(
        candidates,
        AnomalyBacktestConfig(
            lab_config=AnomalyLabConfig(),
            red_flag_profile="runner_oi_confirmed",
        ),
    )

    assert not result.empty
    assert result["pre_context_intent"].str.contains("runner_oi_confirmed").any()


def test_simulate_long_signal_can_wait_for_structural_pullback_entry() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [0, 60_000, 120_000, 180_000, 240_000, 300_000, 360_000],
            "open": [10.0, 10.5, 11.0, 11.2, 11.5, 11.3, 12.0],
            "high": [10.8, 11.1, 11.5, 11.6, 11.8, 11.4, 12.8],
            "low": [9.8, 10.4, 10.9, 11.1, 11.3, 10.7, 11.9],
            "close": [10.6, 11.0, 11.2, 11.5, 11.4, 12.0, 12.5],
        }
    )
    signal = pd.Series(
        {
            "symbol": "TEST/USDT:USDT",
            "timestamp_ms": 0,
            "decision_timestamp_ms": 180_000,
            "decision_close": 11.5,
            "outcome_label": "test",
        }
    )

    result = simulate_long_signal(
        frame,
        signal,
        config=AnomalyBacktestConfig(
            lab_config=AnomalyLabConfig(),
            entry_method="pullback_box_fraction",
            pullback_box_fraction=0.75,
            fee_rate=0.0,
        ),
    )

    assert result["status"] == "closed"
    assert result["entry_timestamp_ms"] == 300_000
    assert result["entry_price"] < signal["decision_close"]
    assert result["entry_method"] == "pullback_box_fraction"


def test_open_interest_context_uses_5m_asof_for_1m_candidate(tmp_path) -> None:
    symbol_dir = tmp_path / "TEST%2FUSDT%3AUSDT" / "5m"
    symbol_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "timestamp": [0, 300_000, 600_000, 900_000],
            "open_interest": [100.0, 110.0, 121.0, 130.0],
        }
    ).to_parquet(symbol_dir / "data.parquet", index=False)
    candidates = pd.DataFrame(
        [
            {
                "symbol": "TEST/USDT:USDT",
                "timeframe": "1m",
                "decision_timestamp_ms": 900_000,
            }
        ]
    )

    enriched = enrich_candidates_with_open_interest(candidates, cache_dir=tmp_path)

    assert enriched.iloc[0]["oi_timeframe"] == "5m"
    assert enriched.iloc[0]["oi_status"] == "ok"
    assert enriched.iloc[0]["oi_timestamp_ms"] == 900_000
    assert enriched.iloc[0]["oi_age_ms"] == 0
    assert enriched.iloc[0]["oi_open_interest"] == 130.0
    assert enriched.iloc[0]["oi_change_1x5m"] == 9.0
    assert round(float(enriched.iloc[0]["oi_change_pct_3x5m"]), 6) == 0.3


def test_open_interest_missing_column_is_explicit_not_zero_filled(tmp_path) -> None:
    symbol_dir = tmp_path / "TEST%2FUSDT%3AUSDT" / "5m"
    symbol_dir.mkdir(parents=True)
    pd.DataFrame({"timestamp": [0, 300_000], "close": [1.0, 1.1]}).to_parquet(
        symbol_dir / "data.parquet",
        index=False,
    )
    candidates = pd.DataFrame(
        [
            {
                "symbol": "TEST/USDT:USDT",
                "timeframe": "1m",
                "decision_timestamp_ms": 300_000,
            }
        ]
    )

    enriched = enrich_candidates_with_open_interest(candidates, cache_dir=tmp_path)
    status = build_oi_context_status(enriched)

    assert enriched.iloc[0]["oi_status"] == "missing_column"
    assert pd.isna(enriched.iloc[0]["oi_open_interest"])
    assert status.iloc[0]["oi_status"] == "missing_column"


def test_anomaly_rows_include_taker_effort_and_sleep_metrics() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [i * 60_000 for i in range(10)],
            "open": [10.0, 10.0, 10.0, 10.0, 10.0, 12.0, 12.4, 12.6, 12.5, 12.7],
            "high": [10.1, 10.1, 10.1, 10.1, 10.1, 13.0, 12.8, 12.9, 12.8, 13.0],
            "low": [9.9, 9.9, 9.9, 9.9, 9.9, 11.8, 12.1, 12.3, 12.2, 12.4],
            "close": [10.0, 10.0, 10.0, 10.0, 10.0, 12.6, 12.5, 12.7, 12.6, 12.9],
            "volume": [100, 100, 100, 100, 100, 5000, 2200, 2100, 1000, 900],
            "quote_volume": [1000, 1000, 1000, 1000, 1000, 90000, 27000, 26500, 12500, 11500],
            "number_of_trades": [10, 10, 10, 10, 10, 600, 250, 240, 100, 90],
            "taker_buy_quote_volume": [500, 500, 500, 500, 500, 72000, 15000, 14800, 6000, 5500],
        }
    )

    rows = collect_symbol_anomaly_rows(
        symbol="TEST/USDT:USDT",
        frame=frame,
        config=AnomalyLabConfig(
            baseline_candles=4,
            confirmation_candles=2,
            forward_high_candles=1,
            forward_low_candles=1,
            min_quote_ratio_start=5,
            min_trade_ratio_start=5,
        ),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["flow_taker_buy_status"] == "ok"
    assert row["start_taker_buy_quote_share"] > row["baseline_taker_buy_quote_share_median"]
    assert row["start_avg_trade_quote_size_ratio"] > 1
    assert row["start_quote_per_abs_return"] > 0
    assert row["start_range_pct_ratio_to_baseline"] > 1
