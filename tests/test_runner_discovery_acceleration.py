import warnings

import pandas as pd

from data.storage.parquet_storage import ParquetStorage
from research_tools.anomaly_strategy_backtest import (
    DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
    DIRECT_TARGET_AGGTRADE_COVERAGE_INDEX_FILE,
    _cache_data_path,
    _trusted_materialized_entry_cache_missing_intervals,
    _write_direct_aggtrade_target_ltf_delta,
)
from research_tools.htf_ltf_runner_discovery import (
    HtfLtfRunnerDiscoveryConfig,
    TRADE_ARTIFACT_COLUMNS,
    _artifact_frame,
    _build_selected_signal_post_entry_backfill_plan,
    _build_targeted_ltf_signal_entry_backfill_plan,
    _collect_symbol_seed_candidates_for_signal_entry_plan,
    _htf_context_warmup_ms,
    _pre_seed_context_candles_for_backtest,
    _pre_seed_context_candles_from_htf,
    _post_entry_fetch_window_for_signal,
    _signal_entry_tail_ms,
    _targeted_ltf_backfill_seeds_for_symbol,
    _targeted_ltf_seed_timestamps_by_symbol,
)
from research_tools.runner_coarse_prefilter import (
    coarse_minute_pair_prefilter,
    prepare_minute_coarse_frame,
)


def _minute_frame(start_ms: int, rows: int, *, quote: float, trades: float, close_step: float = 0.0) -> pd.DataFrame:
    data = []
    price = 100.0
    for index in range(rows):
        open_price = price
        close_price = price + close_step
        high = max(open_price, close_price)
        low = min(open_price, close_price)
        data.append(
            {
                "timestamp": start_ms + index * 60_000,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": 1.0,
                "quote_volume": quote,
                "number_of_trades": trades,
            }
        )
        price = close_price
    return pd.DataFrame(data)


def test_coarse_minute_prefilter_rejects_only_proven_impossible_window() -> None:
    frame = prepare_minute_coarse_frame(_minute_frame(0, 12, quote=100.0, trades=10.0))

    verdict = coarse_minute_pair_prefilter(
        frame,
        pair_start_ms=0,
        htf_ms=300_000,
        ltf_ms=30_000,
        min_seed_return_pct=0.01,
        min_seed_quote_ratio=5.0,
        min_seed_trade_ratio=5.0,
        min_confirm_return_pct=0.004,
        min_confirm_quote_pace_ratio=3.0,
        min_confirm_trade_pace_ratio=3.0,
        min_confirm_candles=2,
        max_confirm_candles=8,
        baseline_quote_values=[1_000.0],
        baseline_trade_values=[100.0],
    )

    assert verdict.possible is False
    assert verdict.reason.startswith("impossible_minute_")
    assert verdict.bounds["coarse_prefilter_trading_signal"] is False


def test_coarse_minute_prefilter_keeps_uncertain_or_possible_window() -> None:
    possible_frame = _minute_frame(0, 12, quote=5_000.0, trades=500.0, close_step=0.5)
    possible_frame.loc[4, "high"] = 106.0
    frame = prepare_minute_coarse_frame(possible_frame)

    verdict = coarse_minute_pair_prefilter(
        frame,
        pair_start_ms=0,
        htf_ms=300_000,
        ltf_ms=30_000,
        min_seed_return_pct=0.01,
        min_seed_quote_ratio=5.0,
        min_seed_trade_ratio=5.0,
        min_confirm_return_pct=0.004,
        min_confirm_quote_pace_ratio=3.0,
        min_confirm_trade_pace_ratio=3.0,
        min_confirm_candles=2,
        max_confirm_candles=8,
        baseline_quote_values=[1_000.0],
        baseline_trade_values=[100.0],
    )

    assert verdict.possible is True
    assert verdict.reason == "possible_by_1m_bounds"

    incomplete = prepare_minute_coarse_frame(possible_frame.drop(index=3))
    uncertain = coarse_minute_pair_prefilter(
        incomplete,
        pair_start_ms=0,
        htf_ms=300_000,
        ltf_ms=30_000,
        min_seed_return_pct=0.01,
        min_seed_quote_ratio=5.0,
        min_seed_trade_ratio=5.0,
        min_confirm_return_pct=0.004,
        min_confirm_quote_pace_ratio=3.0,
        min_confirm_trade_pace_ratio=3.0,
        min_confirm_candles=2,
        max_confirm_candles=8,
        baseline_quote_values=[1_000.0],
        baseline_trade_values=[100.0],
    )

    assert uncertain.possible is True
    assert uncertain.reason == "not_checked_incomplete_1m_coverage"


def test_targeted_post_entry_replay_plan_uses_only_selected_signals() -> None:
    config = HtfLtfRunnerDiscoveryConfig(htf_timeframe="5m", ltf_timeframe="30s", max_hold_candles=10, runner_horizon_minutes=60)
    selected = {
        "symbol": "AAA/USDT:USDT",
        "signal_verdict": "selected",
        "timestamp_ms": 0,
        "htf_close_ms": 300_000,
        "entry_timestamp_ms": 420_000,
    }
    rejected = {
        "symbol": "BBB/USDT:USDT",
        "signal_verdict": "rejected",
        "timestamp_ms": 0,
        "htf_close_ms": 300_000,
        "entry_timestamp_ms": 420_000,
    }

    window = _post_entry_fetch_window_for_signal(selected, config=config, ltf_ms=30_000)
    assert window == (300_000, 3_899_999)

    plan, windows = _build_selected_signal_post_entry_backfill_plan([selected, rejected], config=config, ltf_ms=30_000)

    assert windows == {"AAA/USDT:USDT": [(300_000, 3_899_999)]}
    assert int(plan.iloc[0]["selected_signals"]) == 1
    assert plan.loc[plan["targeted_ltf_plan_status"].eq("planned"), "signal_scope"].tolist() == ["core_selected_signal"]


def test_signal_entry_tail_is_confirm_plus_next_open_only() -> None:
    config = HtfLtfRunnerDiscoveryConfig(htf_timeframe="3m", ltf_timeframe="15s")

    assert _signal_entry_tail_ms(config, 15_000) == 195_000


def test_empty_trade_artifact_keeps_csv_headers() -> None:
    frame = _artifact_frame([], columns=TRADE_ARTIFACT_COLUMNS)

    assert frame.empty
    assert "status" in frame.columns
    assert "entry_timestamp_ms" in frame.columns


def test_pre_seed_context_uses_closed_htf_cache_without_subminute_history() -> None:
    htf_ms = 300_000
    seed_open_ms = 288 * htf_ms
    htf = pd.DataFrame(
        [
            {
                "timestamp": index * htf_ms,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1.0,
                "quote_volume": 1000.0 + index,
                "number_of_trades": 100 + index,
            }
            for index in range(288)
        ]
    )

    context = _pre_seed_context_candles_from_htf(htf, seed_open_ms=seed_open_ms, tf_set="5m_30s", htf_ms=htf_ms)

    assert len(context) == 288
    assert context[0].open_time_ms == 0
    assert context[-1].close_time_ms == seed_open_ms


def test_pre_seed_context_uses_cheap_1m_context_for_offset_rolling_seed() -> None:
    htf_ms = 300_000
    seed_open_ms = 288 * htf_ms + 30_000
    minute = _minute_frame(0, 288 * 5 + 1, quote=1000.0, trades=100.0)

    context = _pre_seed_context_candles_for_backtest(
        htf=pd.DataFrame(),
        minute=minute,
        seed_open_ms=seed_open_ms,
        tf_set="5m_30s",
        htf_ms=htf_ms,
    )

    assert len(context) == 288
    assert context[-1].close_time_ms == seed_open_ms - 30_000
    assert context[-1].source == "backtest_cache_1m_closed_htf_context"


def test_targeted_pre_entry_planner_uses_htf_warmup_but_scans_requested_range_only() -> None:
    htf_ms = 300_000
    scan_start = 288 * htf_ms
    config = HtfLtfRunnerDiscoveryConfig(
        htf_timeframe="5m",
        ltf_timeframe="30s",
        targeted_pair_gate_use_category_necessary_bounds=False,
    )
    htf = pd.DataFrame(
        [
            {
                "timestamp": index * htf_ms,
                "open": 100.0,
                "high": 100.2,
                "low": 99.8,
                "close": 100.0,
                "volume": 1.0,
                "quote_volume": 1000.0,
                "number_of_trades": 100,
            }
            for index in range(288)
        ]
        + [
            {
                "timestamp": scan_start,
                "open": 100.0,
                "high": 102.0,
                "low": 99.8,
                "close": 101.2,
                "volume": 1.0,
                "quote_volume": 8_000.0,
                "number_of_trades": 800,
            },
            {
                "timestamp": scan_start + htf_ms,
                "open": 101.2,
                "high": 102.5,
                "low": 101.0,
                "close": 101.8,
                "volume": 1.0,
                "quote_volume": 7_000.0,
                "number_of_trades": 700,
            },
        ]
    )

    rows, windows = _targeted_ltf_backfill_seeds_for_symbol(
        symbol="AAA/USDT:USDT",
        htf=htf,
        minute_frame=None,
        config=config,
        htf_ms=htf_ms,
        ltf_ms=30_000,
        scan_start_ms=scan_start,
        scan_end_ms=scan_start + 2 * htf_ms,
    )

    planned = [row for row in rows if row.get("targeted_ltf_plan_status") == "planned"]
    summary = [row for row in rows if row.get("targeted_ltf_plan_status") == "symbol_summary"][-1]
    assert _htf_context_warmup_ms(config, htf_ms) == 288 * htf_ms
    assert len(planned) == 1
    assert windows == [(scan_start, scan_start + 2 * htf_ms - 1)]
    assert int(summary["total_adjacent_pair_candidates"]) == 1
    assert int(summary["planned_pairs"]) == 1


def test_signal_entry_plan_keeps_seed_stage_reject_rows_without_fetch_windows(tmp_path) -> None:
    symbol = "AAA/USDT:USDT"
    htf_ms = 300_000
    ltf_ms = 30_000
    scan_start = 288 * htf_ms
    scan_end = scan_start + htf_ms
    config = HtfLtfRunnerDiscoveryConfig(
        cache_dir=tmp_path,
        output_dir=tmp_path / "out",
        htf_timeframe="5m",
        ltf_timeframe="30s",
    )
    htf_rows = [
        {
            "timestamp": index * htf_ms,
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": 1.0,
            "quote_volume": 1000.0,
            "number_of_trades": 100,
        }
        for index in range(289)
    ]
    ltf_rows = [
        {
            "timestamp": scan_start + index * ltf_ms,
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": 1.0,
            "quote_volume": 100.0,
            "number_of_trades": 10,
        }
        for index in range(10)
    ]
    htf_path = _cache_data_path(tmp_path, symbol, "5m")
    ltf_path = _cache_data_path(tmp_path, symbol, "30s")
    htf_path.parent.mkdir(parents=True)
    ltf_path.parent.mkdir(parents=True)
    pd.DataFrame(htf_rows).to_parquet(htf_path, index=False)
    pd.DataFrame(ltf_rows).to_parquet(ltf_path, index=False)

    plan, windows = _build_targeted_ltf_signal_entry_backfill_plan(
        storage=ParquetStorage(tmp_path),
        symbols=(symbol,),
        start_ms=scan_start,
        end_ms=scan_end,
        htf_context_start_ms=scan_start - _htf_context_warmup_ms(config, htf_ms),
        config=config,
        progress_label="test signal-entry plan",
        seed_timestamps_by_symbol={symbol: {scan_start}},
    )

    statuses = plan["targeted_ltf_plan_status"].astype(str).tolist()
    assert windows == {}
    assert "not_planned_seed_stage_terminal_reject" in statuses
    assert int(plan.iloc[0]["seed_stage_prefilter_rejected"]) >= 1


def test_signal_entry_plan_lightweight_seed_collector_avoids_future_label_fields() -> None:
    htf_ms = 300_000
    ltf_ms = 30_000
    ltf = pd.DataFrame(
        [
            {
                "timestamp": index * ltf_ms,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1.0,
                "quote_volume": 100.0,
                "number_of_trades": 10,
            }
            for index in range(20)
        ]
    )
    config = HtfLtfRunnerDiscoveryConfig(htf_timeframe="5m", ltf_timeframe="30s")

    rows = _collect_symbol_seed_candidates_for_signal_entry_plan(
        symbol="AAA/USDT:USDT",
        ltf=ltf,
        config=config,
        allowed_timestamps_ms={0},
    )

    assert len(rows) == 11
    assert rows[0]["timestamp_ms"] == 0
    assert rows[-1]["timestamp_ms"] == htf_ms
    assert {row["signal_entry_plan_candidate_model"] for row in rows} == {"lightweight_exact_rolling_seed_no_future_label"}
    assert not any("runner_10pct_next_hour" in row for row in rows)


def test_final_targeted_decision_uses_signal_entry_exact_seed_timestamps_only() -> None:
    htf_ms = 300_000
    ltf_ms = 30_000
    ltf = pd.DataFrame(
        [
            {
                "timestamp": index * ltf_ms,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1.0,
                "quote_volume": 100.0,
                "number_of_trades": 10,
            }
            for index in range(30)
        ]
    )
    plan = pd.DataFrame(
        [
            {
                "targeted_ltf_phase": "pre_entry",
                "targeted_ltf_plan_status": "planned",
                "symbol": "AAA/USDT:USDT",
                "timestamp_ms": 0,
            },
            {
                "targeted_ltf_phase": "signal_entry",
                "targeted_ltf_plan_status": "not_planned_seed_stage_terminal_reject",
                "symbol": "AAA/USDT:USDT",
                "timestamp_ms": 0,
            },
            {
                "targeted_ltf_phase": "signal_entry",
                "targeted_ltf_plan_status": "planned",
                "symbol": "AAA/USDT:USDT",
                "timestamp_ms": htf_ms,
            },
        ]
    )
    config = HtfLtfRunnerDiscoveryConfig(htf_timeframe="5m", ltf_timeframe="30s")

    exact = _targeted_ltf_seed_timestamps_by_symbol(plan, phase_name="signal_entry")
    rows = _collect_symbol_seed_candidates_for_signal_entry_plan(
        symbol="AAA/USDT:USDT",
        ltf=ltf,
        config=config,
        allowed_timestamps_ms=exact["AAA/USDT:USDT"],
        expand_pair_starts=False,
    )

    assert exact == {"AAA/USDT:USDT": {htf_ms}}
    assert [row["timestamp_ms"] for row in rows] == [htf_ms]


def test_targeted_direct_ltf_cache_missing_intervals_subtracts_trusted_buckets(tmp_path) -> None:
    symbol = "AAA/USDT:USDT"
    path = _cache_data_path(tmp_path, symbol, "30s")
    path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "timestamp": 0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "quote_volume": 10.0,
                "number_of_trades": 1,
                "aggregation_source_timeframe": "aggTrades",
                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                "aggtrade_coverage_verified": True,
            },
            {
                "timestamp": 30_000,
                "open": 100.5,
                "high": 101.0,
                "low": 100.0,
                "close": 100.7,
                "quote_volume": 10.0,
                "number_of_trades": 1,
                "aggregation_source_timeframe": "aggTrades",
                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                "aggtrade_coverage_verified": True,
            },
            {
                "timestamp": 90_000,
                "open": 100.7,
                "high": 101.2,
                "low": 100.5,
                "close": 101.0,
                "quote_volume": 10.0,
                "number_of_trades": 1,
                "aggregation_source_timeframe": "aggTrades",
                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                "aggtrade_coverage_verified": True,
            },
        ]
    ).to_parquet(path, index=False)

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        missing = _trusted_materialized_entry_cache_missing_intervals(
            tmp_path,
            symbol,
            target_timeframe="30s",
            window_start_ms=0,
            window_end_ms=119_999,
        )

    assert missing == [(60_000, 89_999)]


def test_targeted_direct_ltf_cache_subtraction_reads_delta_files(tmp_path) -> None:
    symbol = "AAA/USDT:USDT"
    path = _cache_data_path(tmp_path, symbol, "30s")
    delta_dir = path.parent / "delta"
    delta_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "timestamp": 0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "quote_volume": 10.0,
                "number_of_trades": 1,
                "aggregation_source_timeframe": "aggTrades",
                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                "aggtrade_coverage_verified": True,
            },
            {
                "timestamp": 30_000,
                "open": 100.5,
                "high": 101.0,
                "low": 100.0,
                "close": 100.7,
                "quote_volume": 10.0,
                "number_of_trades": 1,
                "aggregation_source_timeframe": "aggTrades",
                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                "aggtrade_coverage_verified": True,
            },
        ]
    ).to_parquet(delta_dir / "000.parquet", index=False)

    missing = _trusted_materialized_entry_cache_missing_intervals(
        tmp_path,
        symbol,
        target_timeframe="30s",
        window_start_ms=0,
        window_end_ms=59_999,
    )

    assert missing == []


def test_targeted_direct_ltf_cache_subtraction_reads_coverage_index(tmp_path) -> None:
    symbol = "AAA/USDT:USDT"
    path = _cache_data_path(tmp_path, symbol, "30s")
    path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "timestamp": 0,
                "aggregation_source_timeframe": "aggTrades",
                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                "aggtrade_coverage_verified": True,
            },
            {
                "timestamp": 30_000,
                "aggregation_source_timeframe": "aggTrades",
                "aggregation_version": DIRECT_TARGET_AGGTRADE_CACHE_VERSION,
                "aggtrade_coverage_verified": True,
            },
        ]
    ).to_parquet(path.parent / DIRECT_TARGET_AGGTRADE_COVERAGE_INDEX_FILE, index=False)

    missing = _trusted_materialized_entry_cache_missing_intervals(
        tmp_path,
        symbol,
        target_timeframe="30s",
        window_start_ms=0,
        window_end_ms=59_999,
    )

    assert missing == []


def test_empty_aggtrade_window_writes_coverage_index(tmp_path) -> None:
    symbol = "AAA/USDT:USDT"

    status, rows, path = _write_direct_aggtrade_target_ltf_delta(
        cache_dir=tmp_path,
        symbol=symbol,
        target_timeframe="30s",
        trades=pd.DataFrame(),
        start_timestamp_ms=0,
        end_timestamp_ms=59_999,
    )
    missing = _trusted_materialized_entry_cache_missing_intervals(
        tmp_path,
        symbol,
        target_timeframe="30s",
        window_start_ms=0,
        window_end_ms=59_999,
    )

    assert status == "empty_aggtrades_coverage_index_written"
    assert rows == 2
    assert path.endswith(DIRECT_TARGET_AGGTRADE_COVERAGE_INDEX_FILE)
    assert missing == []
