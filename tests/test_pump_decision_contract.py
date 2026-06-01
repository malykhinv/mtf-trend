from dataclasses import replace

import pandas as pd

from research_tools.anomaly_live2.config import AnomalyLive2Config
from research_tools.anomaly_live2.deadline import (
    Live2DeadlineEngine,
    Live2DeadlineEngineConfig,
    _last_decision_bucket_ms,
    _set_last_decision_bucket_ms,
)
from research_tools.anomaly_live2.market_data.candles import Live2Candle
from research_tools.anomaly_live2.signal import _pre_seed_context_for_live, _seed_passes_basic_core_gate, _seed_passes_return_gate
from research_tools.anomaly_live2.state import SymbolStateStore
from research_tools.htf_ltf_runner_discovery import _oi_asof, _pair_can_pass_confirm_upper_bounds
from research_tools.pump_decision_core import (
    DataDependency,
    DecisionCandle,
    DecisionSnapshot,
    ROLLING_PROFILE_SPECS,
    SUPPORTED_ROLLING_TF_SETS,
    RollingSeedSnapshot,
    _build_seed_first_core_smoke_snapshot,
    decision_snapshot_hash,
    evaluate_first_ltf_confirm_after_seed,
)


def test_snapshot_hash_changes_for_core_affecting_dependencies_and_source_status() -> None:
    snapshot, post_seed = _build_seed_first_core_smoke_snapshot()
    selected = evaluate_first_ltf_confirm_after_seed(snapshot, post_seed_ltf_candles=post_seed)
    assert selected.verdict == "selected"
    selected_hash = decision_snapshot_hash(selected.snapshot)

    source_label_only = replace(
        selected.snapshot,
        source="backtest",
        source_labels={"adapter": "different"},
    )
    assert decision_snapshot_hash(source_label_only) == selected_hash

    bad_dependency = replace(
        selected.snapshot,
        dependencies=(
            DataDependency(
                name="number_of_trades",
                status="missing",
                reason="unit_test_missing_real_trade_count",
                asof_ms=selected.snapshot.decision_time_ms,
                source="unit_test",
            ),
        ),
    )
    dependency_verdict = evaluate_first_ltf_confirm_after_seed(
        bad_dependency,
        post_seed_ltf_candles=post_seed,
    )
    assert decision_snapshot_hash(bad_dependency) != selected_hash
    assert dependency_verdict.verdict == "data_dependency_not_ready"

    assert selected.snapshot.rolling_seed is not None
    degraded_seed_candles = (
        replace(selected.snapshot.rolling_seed.seed_candles[0], source_status="gap"),
        *selected.snapshot.rolling_seed.seed_candles[1:],
    )
    degraded_seed = replace(selected.snapshot.rolling_seed, seed_candles=degraded_seed_candles)
    degraded_snapshot = replace(selected.snapshot, rolling_seed=degraded_seed)
    degraded_verdict = evaluate_first_ltf_confirm_after_seed(
        degraded_snapshot,
        post_seed_ltf_candles=post_seed,
    )
    assert decision_snapshot_hash(degraded_snapshot) != selected_hash
    assert degraded_verdict.verdict == "data_dependency_not_ready"


def test_first_confirm_returns_final_exact_rejected_confirm_window() -> None:
    snapshot, post_seed = _build_seed_first_core_smoke_snapshot()
    flat_confirm = tuple(
        DecisionCandle(
            open_time_ms=candle.open_time_ms,
            close_time_ms=candle.close_time_ms,
            open=candle.open,
            high=candle.open * 1.0005,
            low=candle.open * 0.9995,
            close=candle.open,
            quote_volume=candle.quote_volume,
            number_of_trades=candle.number_of_trades,
            taker_buy_quote_volume=candle.taker_buy_quote_volume,
            source="unit_test",
        )
        for candle in post_seed
    )

    verdict = evaluate_first_ltf_confirm_after_seed(snapshot, post_seed_ltf_candles=flat_confirm)

    assert verdict.verdict == "rejected"
    assert verdict.rejects[0].reason == "ltf_confirm_return_below_min"
    assert verdict.snapshot.ltf_confirm is not None
    assert verdict.snapshot.ltf_confirm.confirm_end_ms == flat_confirm[-1].close_time_ms
    assert verdict.features["checked_confirm_candles"] == 2
    assert verdict.features["last_confirm_end_ms"] == flat_confirm[-1].close_time_ms


def test_extra_adapter_history_does_not_change_contract_hash() -> None:
    snapshot, post_seed = _build_seed_first_core_smoke_snapshot()
    selected = evaluate_first_ltf_confirm_after_seed(snapshot, post_seed_ltf_candles=post_seed)
    assert selected.verdict == "selected"
    assert snapshot.rolling_seed is not None

    first_context = snapshot.rolling_seed.pre_seed_context_candles[0]
    extra = tuple(
        DecisionCandle(
            open_time_ms=first_context.open_time_ms - idx * 300_000,
            close_time_ms=first_context.close_time_ms - idx * 300_000,
            open=50.0,
            high=50.2,
            low=49.8,
            close=50.1,
            quote_volume=10.0,
            number_of_trades=10,
            source="unit_test_extra_history",
        )
        for idx in range(3, 0, -1)
    )
    extended_seed = RollingSeedSnapshot(
        tf_set=snapshot.rolling_seed.tf_set,
        seed_open_ms=snapshot.rolling_seed.seed_open_ms,
        seed_close_ms=snapshot.rolling_seed.seed_close_ms,
        seed_candles=snapshot.rolling_seed.seed_candles,
        pre_seed_context_candles=extra + snapshot.rolling_seed.pre_seed_context_candles,
    )
    extended_snapshot = DecisionSnapshot(
        symbol=snapshot.symbol,
        tf_set=snapshot.tf_set,
        decision_time_ms=snapshot.decision_time_ms,
        source=snapshot.source,
        rolling_seed=extended_seed,
    )
    extended = evaluate_first_ltf_confirm_after_seed(extended_snapshot, post_seed_ltf_candles=post_seed)

    assert extended.verdict == selected.verdict
    assert decision_snapshot_hash(extended.snapshot) == decision_snapshot_hash(selected.snapshot)


def test_15s_profiles_are_first_class_contract_profiles() -> None:
    assert SUPPORTED_ROLLING_TF_SETS == ("5m_30s", "3m_30s", "5m_15s", "3m_15s")
    assert ROLLING_PROFILE_SPECS["5m_15s"].ltf_seconds == 15
    assert ROLLING_PROFILE_SPECS["5m_15s"].min_confirm_candles == 4
    assert ROLLING_PROFILE_SPECS["5m_15s"].max_confirm_candles == 16
    assert ROLLING_PROFILE_SPECS["3m_15s"].ltf_seconds == 15
    assert ROLLING_PROFILE_SPECS["3m_15s"].min_confirm_candles == 4
    assert ROLLING_PROFILE_SPECS["3m_15s"].max_confirm_candles == 12


def test_confirm_upper_bound_gate_rejects_only_proven_impossibility() -> None:
    prepared = pd.DataFrame(
        [
            {"timestamp": 0, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "quote_volume": 1_000.0, "number_of_trades": 1_000.0},
            {"timestamp": 300_000, "open": 101.0, "high": 101.1, "low": 100.9, "close": 101.0, "quote_volume": 10_000.0, "number_of_trades": 10_000.0},
            {"timestamp": 600_000, "open": 101.0, "high": 101.1, "low": 100.9, "close": 101.0, "quote_volume": 10_000.0, "number_of_trades": 10_000.0},
        ]
    )

    possible, bounds = _pair_can_pass_confirm_upper_bounds(
        prepared=prepared,
        idx=0,
        htf_ms=300_000,
        ltf_ms=15_000,
        max_confirm_candles=16,
        min_confirm_candles=4,
        baseline_quote_values=[100.0],
        baseline_trade_values=[100.0],
    )

    assert possible is False
    assert bounds["confirm_bound_reason"] == "impossible_confirm_return"
    assert bounds["confirm_bound_trading_signal"] is False


def test_confirm_upper_bound_gate_keeps_missing_or_non_adjacent_htf_context() -> None:
    missing_next = pd.DataFrame(
        [
            {"timestamp": 0, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "quote_volume": 1_000.0, "number_of_trades": 1_000.0},
            {"timestamp": 300_000, "open": 101.0, "high": 103.0, "low": 100.5, "close": 102.0, "quote_volume": 1_000.0, "number_of_trades": 1_000.0},
        ]
    )
    possible, bounds = _pair_can_pass_confirm_upper_bounds(
        prepared=missing_next,
        idx=0,
        htf_ms=300_000,
        ltf_ms=15_000,
        max_confirm_candles=16,
        min_confirm_candles=4,
        baseline_quote_values=[100.0],
        baseline_trade_values=[100.0],
    )
    assert possible is True
    assert bounds["confirm_bound_model"] == "not_checked_missing_next_htf_candle"

    non_adjacent = pd.concat(
        [
            missing_next,
            pd.DataFrame(
                [
                    {"timestamp": 900_000, "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "quote_volume": 1_000.0, "number_of_trades": 1_000.0}
                ]
            ),
        ],
        ignore_index=True,
    )
    possible, bounds = _pair_can_pass_confirm_upper_bounds(
        prepared=non_adjacent,
        idx=0,
        htf_ms=300_000,
        ltf_ms=15_000,
        max_confirm_candles=16,
        min_confirm_candles=4,
        baseline_quote_values=[100.0],
        baseline_trade_values=[100.0],
    )
    assert possible is True
    assert bounds["confirm_bound_model"] == "not_checked_non_adjacent_next_htf_candle"


def test_oi_asof_uses_only_exchange_available_5m_rows() -> None:
    oi = pd.DataFrame(
        [
            {"timestamp": 0, "available_timestamp_ms": 300_000, "open_interest": 100.0},
            {"timestamp": 300_000, "available_timestamp_ms": 600_000, "open_interest": 110.0},
        ]
    )

    assert _oi_asof(oi, decision_ms=299_999)["status"] == "missing"
    first = _oi_asof(oi, decision_ms=300_000)
    second = _oi_asof(oi, decision_ms=600_000)

    assert first["status"] == "ok"
    assert first["timestamp_ms"] == 0
    assert first["open_interest"] == 100.0
    assert second["timestamp_ms"] == 300_000
    assert second["open_interest"] == 110.0


def test_live2_default_decision_timeframes_are_independent_streams() -> None:
    config = AnomalyLive2Config(output_dir=".")
    assert config.decision_timeframes_ms == (15_000, 30_000)

    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    _set_last_decision_bucket_ms(state, 15_000, 150_000)
    _set_last_decision_bucket_ms(state, 30_000, 120_000)

    assert _last_decision_bucket_ms(state, 15_000) == 150_000
    assert _last_decision_bucket_ms(state, 30_000) == 120_000
    assert state.last_decision_bucket_ms_by_timeframe == {15_000: 150_000, 30_000: 120_000}


def test_live_pre_seed_context_can_use_startup_1m_for_minute_aligned_seed() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",), max_closed_candles=2_000)
    state = store.get_or_create("AAA/USDT:USDT")
    one_minute = tuple(
        Live2Candle(
            timeframe_ms=60_000,
            open_time_ms=index * 60_000,
            close_time_ms=(index + 1) * 60_000,
            open=100.0,
            high=100.2,
            low=99.8,
            close=100.1,
            base_volume=1.0,
            quote_volume=100.0,
            number_of_trades=100,
            taker_buy_quote_volume=55.0,
            first_trade_time_ms=index * 60_000,
            last_trade_time_ms=(index + 1) * 60_000 - 1,
            first_source="unit_test_1m",
            last_source="unit_test_1m",
        )
        for index in range(1_500)
    )
    store.append_closed_candles(symbol="AAA/USDT:USDT", candles=one_minute)

    context = _pre_seed_context_for_live(
        state=state,
        closed_ltf=(),
        tf_set="5m_15s",
        htf_timeframe_ms=300_000,
        ltf_timeframe_ms=15_000,
        before_ms=1_500 * 60_000,
    )

    assert len(context) == 288
    assert context[-1].close_time_ms == 1_500 * 60_000
    assert context[-1].quote_volume == 500.0
    assert context[-1].number_of_trades == 500


def test_live_basic_seed_gate_rejects_quiet_seed_before_pending_storage() -> None:
    context = tuple(
        Live2Candle(
            timeframe_ms=300_000,
            open_time_ms=index * 300_000,
            close_time_ms=(index + 1) * 300_000,
            open=100.0,
            high=100.1,
            low=99.9,
            close=100.0,
            base_volume=1.0,
            quote_volume=100.0,
            number_of_trades=100,
            taker_buy_quote_volume=55.0,
            first_trade_time_ms=index * 300_000,
            last_trade_time_ms=(index + 1) * 300_000 - 1,
            first_source="unit_test_context",
            last_source="unit_test_context",
        )
        for index in range(288)
    )
    quiet_seed = tuple(
        Live2Candle(
            timeframe_ms=15_000,
            open_time_ms=288 * 300_000 + index * 15_000,
            close_time_ms=288 * 300_000 + (index + 1) * 15_000,
            open=100.0,
            high=100.1,
            low=99.9,
            close=100.0,
            base_volume=1.0,
            quote_volume=10.0,
            number_of_trades=10,
            taker_buy_quote_volume=5.0,
            first_trade_time_ms=288 * 300_000 + index * 15_000,
            last_trade_time_ms=288 * 300_000 + (index + 1) * 15_000 - 1,
            first_source="unit_test_seed",
            last_source="unit_test_seed",
        )
        for index in range(20)
    )

    assert _seed_passes_basic_core_gate(seed_candles=quiet_seed, context=context) is False


def test_live_seed_return_gate_rejects_before_context_work() -> None:
    weak_seed = tuple(
        Live2Candle(
            timeframe_ms=15_000,
            open_time_ms=index * 15_000,
            close_time_ms=(index + 1) * 15_000,
            open=100.0,
            high=100.2,
            low=99.9,
            close=100.05,
            base_volume=100.0,
            quote_volume=10_000.0,
            number_of_trades=100,
            taker_buy_quote_volume=6_000.0,
            first_trade_time_ms=index * 15_000,
            last_trade_time_ms=(index + 1) * 15_000 - 1,
            first_source="unit_test",
            last_source="unit_test",
        )
        for index in range(20)
    )
    strong_seed = tuple(
        Live2Candle(
            timeframe_ms=item.timeframe_ms,
            open_time_ms=item.open_time_ms,
            close_time_ms=item.close_time_ms,
            open=item.open,
            high=102.0,
            low=item.low,
            close=101.2 if index == 19 else item.close,
            base_volume=item.base_volume,
            quote_volume=item.quote_volume,
            number_of_trades=item.number_of_trades,
            taker_buy_quote_volume=item.taker_buy_quote_volume,
            first_trade_time_ms=item.first_trade_time_ms,
            last_trade_time_ms=item.last_trade_time_ms,
            first_source=item.first_source,
            last_source=item.last_source,
        )
        for index, item in enumerate(weak_seed)
    )

    assert _seed_passes_return_gate(seed_candles=weak_seed) is False
    assert _seed_passes_return_gate(seed_candles=strong_seed) is True


def test_live_deadline_engine_drains_quiet_buckets_before_deadline_records() -> None:
    store = SymbolStateStore(("QUIET/USDT:USDT",))
    close_ms = 30_000
    candle = Live2Candle(
        timeframe_ms=15_000,
        open_time_ms=15_000,
        close_time_ms=close_ms,
        open=100.0,
        high=100.01,
        low=99.99,
        close=100.0,
        base_volume=0.1,
        quote_volume=10.0,
        number_of_trades=1,
        taker_buy_quote_volume=5.0,
        first_trade_time_ms=15_001,
        last_trade_time_ms=close_ms - 1,
        first_source="unit_test",
        last_source="unit_test",
    )
    store.append_closed_candles(symbol="QUIET/USDT:USDT", candles=(candle,))
    state = store.get_or_create("QUIET/USDT:USDT")
    engine = Live2DeadlineEngine(
        state_store=store,
        config=Live2DeadlineEngineConfig(
            timeframe_ms=15_000,
            decision_deadline_ms=500,
            backlog_expire_ms=30_000,
            actionable_min_quote_volume=2_500.0,
            actionable_min_trade_count=20,
            actionable_min_abs_return_pct=0.003,
        ),
        live_decision_watermark_ms=lambda: close_ms,
    )

    result = engine.run_cycle(now_ms=close_ms + 5_000, candidates=(state,), symbols_total=1)

    assert result.decisions == []
    assert result.deadline_missed_count == 0
    assert state.last_verdict == "market_quiet_non_actionable"
    assert _last_decision_bucket_ms(state, 15_000) == candle.open_time_ms


def test_live_deadline_engine_drains_actionable_bucket_without_seed_candidate() -> None:
    store = SymbolStateStore(("HOT/USDT:USDT",))
    close_ms = 30_000
    candle = Live2Candle(
        timeframe_ms=15_000,
        open_time_ms=15_000,
        close_time_ms=close_ms,
        open=100.0,
        high=100.5,
        low=99.9,
        close=100.4,
        base_volume=100.0,
        quote_volume=10_000.0,
        number_of_trades=100,
        taker_buy_quote_volume=6_000.0,
        first_trade_time_ms=15_001,
        last_trade_time_ms=close_ms - 1,
        first_source="unit_test",
        last_source="unit_test",
    )
    store.append_closed_candles(symbol="HOT/USDT:USDT", candles=(candle,))
    state = store.get_or_create("HOT/USDT:USDT")
    engine = Live2DeadlineEngine(
        state_store=store,
        config=Live2DeadlineEngineConfig(
            timeframe_ms=15_000,
            decision_deadline_ms=500,
            backlog_expire_ms=30_000,
            actionable_min_quote_volume=2_500.0,
            actionable_min_trade_count=20,
            actionable_min_abs_return_pct=0.003,
        ),
        live_decision_watermark_ms=lambda: close_ms,
    )

    result = engine.run_cycle(now_ms=close_ms + 5_000, candidates=(state,), symbols_total=1)

    assert result.decisions == []
    assert result.deadline_missed_count == 0
    assert state.last_verdict == "market_quiet_non_actionable"
    assert state.last_verdict_reason == "no_pending_or_new_rolling_seed_candidate"
    assert _last_decision_bucket_ms(state, 15_000) == candle.open_time_ms
