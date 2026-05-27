import json
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from cli.parser import build_parser
from data.exchanges.ccxt_types import ExchangeLiveAccountPreflight, ExchangeOpenInterestSnapshot, ExchangeOrderFill
from research_tools.anomaly_live2.artifacts import Live2ArtifactWriter
from research_tools.anomaly_live2.config import AnomalyLive2Config
from research_tools.anomaly_live2.contracts import Live2Severity
from research_tools.anomaly_live2.deadline import Live2DeadlineEngine, Live2DeadlineEngineConfig, Live2DecisionRecord
from research_tools.anomaly_live2.entry_guard import Live2EntryGuardResult
from research_tools.anomaly_live2.execution import Live2ExecutionConfig, Live2ExecutionEngine
from research_tools.anomaly_live2.market_data.candles import Live2AggTradeEvent, Live2Candle, Live2CandleRing
from research_tools.anomaly_live2.position_supervisor import Live2PositionSupervisor, Live2PositionSupervisorAction, Live2PositionSupervisorConfig
from research_tools.anomaly_live2.market_data.prior_context import (
    LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS,
    Live2PriorContextPollConfig,
    Live2PriorContextPoller,
)
from research_tools.anomaly_live2.market_data.open_interest import Live2OpenInterestPollConfig, Live2OpenInterestPoller
from research_tools.anomaly_live2.market_data.warmup import (
    Live2StartupHtfBaselineConfig,
    Live2StartupHtfBaselineWarmup,
)
from research_tools.anomaly_live2.signal import (
    Live2SignalEngine,
    _effective_context_status,
    _live_backtest_like_setup,
    _post_htf_acceptance_setup,
    _runner_shape_accepts,
)
from research_tools.anomaly_live2.signal import Live2SignalDecision
from research_tools.anomaly_live2.state import LIVE2_AGGTRADE_WS_SOURCE, SymbolStateStore
from research_tools.anomaly_live2.status_grid import format_live2_status_grid
from research_tools.anomaly_live2.top_growth import HOUR_MS, Live2TopGrowthAudit, Live2TopGrowthAuditConfig
from research_tools.anomaly_category_contract import SUPPORTED_PUMP_CATEGORIES
from research_tools.anomaly_continuation_lab import AnomalyLabConfig
from research_tools.anomaly_strategy_backtest import AnomalyBacktestConfig, _build_pair_candidate_row
from research_tools.anomaly_live2.telegram import format_final_close_message


def _trade(
    *,
    trade_time_ms: int,
    price: float = 1.0,
    source: str = LIVE2_AGGTRADE_WS_SOURCE,
    symbol: str = "AAA/USDT:USDT",
) -> Live2AggTradeEvent:
    return Live2AggTradeEvent(
        symbol=symbol,
        market_id=symbol.split("/")[0] + "USDT",
        aggregate_trade_id=trade_time_ms,
        event_time_ms=trade_time_ms,
        trade_time_ms=trade_time_ms,
        price=price,
        quantity=1.0,
        quote_quantity=price,
        taker_buy_quote_quantity=price,
        buyer_is_maker=False,
        source=source,
    )


def _candle(
    *,
    timeframe_ms: int,
    open_time_ms: int,
    open_price: float = 1.0,
    high: float = 1.0,
    low: float = 1.0,
    close: float = 1.0,
    quote_volume: float = 100.0,
    number_of_trades: int = 10,
) -> Live2Candle:
    return Live2Candle(
        timeframe_ms=timeframe_ms,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + timeframe_ms,
        open=open_price,
        high=high,
        low=low,
        close=close,
        base_volume=quote_volume / close if close > 0 else quote_volume,
        quote_volume=quote_volume,
        number_of_trades=number_of_trades,
        taker_buy_quote_volume=quote_volume * 0.55,
        first_trade_time_ms=open_time_ms,
        last_trade_time_ms=open_time_ms + timeframe_ms - 1,
        first_source=LIVE2_AGGTRADE_WS_SOURCE,
        last_source=LIVE2_AGGTRADE_WS_SOURCE,
        live_ws_trade_count=number_of_trades,
    )


def test_live2_candle_closes_on_wall_clock_without_next_trade() -> None:
    ring = Live2CandleRing(timeframe_ms=5_000, max_closed_candles=10)

    assert ring.add_trade(_trade(trade_time_ms=1_000)).updated
    assert ring.latest_closed() is None

    result = ring.close_due(now_ms=5_000)

    assert result.closed_count == 1
    closed = ring.latest_closed()
    assert closed is not None
    assert closed.open_time_ms == 0
    assert closed.close_time_ms == 5_000
    assert closed.number_of_trades == 1


def test_live2_candle_gap_is_preserved_after_wall_clock_close() -> None:
    ring = Live2CandleRing(timeframe_ms=5_000, max_closed_candles=10)

    ring.add_trade(_trade(trade_time_ms=1_000))
    ring.close_due(now_ms=5_000)
    result = ring.add_trade(_trade(trade_time_ms=16_000))

    assert result.gap_count == 2
    assert ring.gap_count == 2


def test_live2_state_store_marks_due_closed_candle_for_decision() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(
        selected=True,
        rank=1,
        reason="test",
        selected_at_ms=0,
    )
    store.update_aggtrade(_trade(trade_time_ms=1_000), received_at_ms=1_010)

    closed = store.close_due_candles(now_ms=5_000)

    assert closed == 1
    assert store.decision_snapshot() == (state,)
    assert state.candle_book.rings[5_000].latest_closed() is not None


def test_live2_state_store_counts_recent_actionable_symbols() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT", "BBB/USDT:USDT", "CCC/USDT:USDT"))
    store.get_or_create("AAA/USDT:USDT").actionable_since_ms = 10_000
    store.get_or_create("AAA/USDT:USDT").last_actionable_ms = 10_000
    store.get_or_create("BBB/USDT:USDT").actionable_since_ms = 1_000
    store.get_or_create("BBB/USDT:USDT").last_actionable_ms = 1_000

    counts = store.actionable_symbol_counts(now_ms=11_000, ttl_ms=5_000, session_start_ms=5_000)

    assert counts["current"] == 1
    assert counts["seen"] == 1
    assert counts["session_seen"] == 1
    assert counts["session_start_ms"] == 5_000


def test_live2_signal_context_status_marks_stale_ok_context() -> None:
    assert (
        _effective_context_status(
            status="ok",
            last_seen_ms=1_000,
            decision_time_ms=3_001,
            stale_ms=2_000,
        )
        == "stale"
    )
    assert (
        _effective_context_status(
            status="ok",
            last_seen_ms=1_000,
            decision_time_ms=3_000,
            stale_ms=2_000,
        )
        == "ok"
    )


def test_live2_cli_prior_context_defaults_match_runtime_config() -> None:
    args = build_parser().parse_args(["run-anomaly-live2"])
    config = AnomalyLive2Config(output_dir=Path("."))

    assert args.prior_context_stale_ms == config.prior_context_stale_ms
    assert args.prior_context_symbol_cooldown_seconds == config.prior_context_symbol_cooldown_seconds
    assert args.prior_context_max_symbols_per_cycle == config.prior_context_max_symbols_per_cycle
    assert args.decision_backlog_expire_ms == config.decision_backlog_expire_ms


def test_live2_artifact_writer_replaces_symbol_state_atomically(tmp_path) -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    writer = Live2ArtifactWriter(tmp_path)
    try:
        writer.write_symbol_state(store, aggtrade_stale_ms=5_000)
        writer._queue.join()
        first = tmp_path / "live2_symbol_state.csv"
        first_bytes = first.read_bytes()
        assert first_bytes.startswith(b"\xef\xbb\xbf")
        assert b"AAA/USDT:USDT" in first_bytes

        writer.write_symbol_state(store, aggtrade_stale_ms=5_000)
        writer._queue.join()
        second_bytes = first.read_bytes()
        assert second_bytes
        assert b"AAA/USDT:USDT" in second_bytes
        assert list(tmp_path.glob("*.tmp")) == []
    finally:
        writer.close()


def test_live2_artifact_writer_replaces_json_atomically(tmp_path) -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    writer = Live2ArtifactWriter(tmp_path)
    try:
        writer.write_status(
            runtime_generation="test",
            started_at_utc="2026-05-20T00:00:00+00:00",
            readiness=type("Readiness", (), {"as_dict": lambda self: {"new_entries_allowed": False}})(),
            state_store=store,
            status="running",
            reason="test",
        )
        writer.write_diagnostics_summary({"status": "ok", "updated_at_ms": int(time.time() * 1000)})
        writer._queue.join()

        status = json.loads((tmp_path / "live2_status.json").read_text(encoding="utf-8"))
        summary = json.loads((tmp_path / "live2_diagnostics_summary.json").read_text(encoding="utf-8"))
        assert status["status"] == "running"
        assert summary["status"] == "ok"
        assert list(tmp_path.glob("*.tmp")) == []
    finally:
        writer.close()


def test_live2_artifact_writer_records_near_miss_csv(tmp_path) -> None:
    writer = Live2ArtifactWriter(tmp_path)
    try:
        decision = Live2DecisionRecord(
            symbol="AAA/USDT:USDT",
            verdict="rejected_signal_contract",
            reason="runner_flow:prior_whipsaw_24h_above_category_max",
            bucket_open_ms=1_000,
            bucket_close_ms=6_000,
            decision_timestamp_ms=6_040,
            deadline_ms=6_750,
            latency_ms=40,
            quote_volume=10_000.0,
            number_of_trades=100,
            return_pct=0.02,
            candle_first_source="binance_futures_aggtrade_ws",
            candle_last_source="binance_futures_aggtrade_ws",
            candle_live_ws_trade_count=100,
            signal_features={
                "actionable_reason": "abs_return_threshold_crossed",
                "prior_context_status": "ok",
                "prior_up_down_whipsaw_to_impulse_range": 1.2,
                "prior_spike_count_24h": 0,
                "prior_fast_fade_count_24h": 0,
            },
            signal_reject_reasons=("runner_flow:prior_whipsaw_24h_above_category_max",),
        )
        row = decision.as_near_miss_row()
        assert row is not None
        writer.write_near_miss(row)
        writer._queue.join()
        writer.close()

        text = (tmp_path / "live2_near_misses.csv").read_text(encoding="utf-8-sig")
        assert "AAA/USDT:USDT" in text
        assert "category_contract_rejected_after_actionable" in text
        assert "runner_flow:prior_whipsaw_24h_above_category_max" in text
    finally:
        writer.close()


class _FakePriorContextExchange:
    def fetch_ohlcv(self, symbol, timeframe, start_timestamp_ms, end_timestamp_ms):
        timestamps = list(range(
            end_timestamp_ms - (287 * LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS),
            end_timestamp_ms + 1,
            LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS,
        ))
        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": [1.0] * len(timestamps),
                "high": [1.01] * len(timestamps),
                "low": [0.99] * len(timestamps),
                "close": [1.0] * len(timestamps),
            }
        )


class _FakeHtfBaselineExchange:
    def fetch_binance_klines(self, *, symbol, timeframe, start_timestamp_ms, end_timestamp_ms, limit=1000):
        return [
            [0, "1.0", "1.02", "0.99", "1.01", "10", 59_999, "1000", 42, "5", "550", "0"],
            [60_000, "1.01", "1.03", "1.00", "1.02", "11", 119_999, "1200", 48, "6", "660", "0"],
        ]


class _FakeOpenInterestExchange:
    def fetch_open_interest(self, symbol, timeframe, start_timestamp_ms, end_timestamp_ms):
        return pd.DataFrame(
            {
                "timestamp": [0, 300_000, 600_000, 900_000],
                "open_interest": [100.0, 102.0, 104.0, 110.0],
            }
        )

    def fetch_current_open_interest(self, symbol):
        return ExchangeOpenInterestSnapshot(
            symbol=symbol,
            exchange_symbol="AAAUSDT",
            fetched_at_ms=1_000_123,
            timestamp_ms=1_000_111,
            open_interest=112.0,
            source="fapiPublicGetOpenInterest",
            status="ok",
            reason="ok",
        )


def test_live2_open_interest_poller_records_current_open_interest_separately() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    poller = Live2OpenInterestPoller(
        state_store=store,
        exchange_client=_FakeOpenInterestExchange(),
        config=Live2OpenInterestPollConfig(poll_interval_seconds=1.0, symbol_cooldown_seconds=1.0, max_symbols_per_cycle=1),
    )

    summary = poller.poll_symbols_once(("AAA/USDT:USDT",), request_sleep_seconds=0.0)

    assert summary["ok"] == 1
    state = store.get_or_create("AAA/USDT:USDT")
    assert state.oi_status == "ok"
    assert state.oi_open_interest == pytest.approx(110.0)
    assert state.oi_change_pct_3x5m == pytest.approx(0.10)
    assert state.current_oi_status == "ok"
    assert state.current_oi_open_interest == pytest.approx(112.0)
    assert state.current_oi_timestamp_ms == 1_000_111
    assert state.current_oi_source == "fapiPublicGetOpenInterest"


def test_live2_startup_htf_baseline_warmup_loads_raw_kline_flow_fields() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    warmup = Live2StartupHtfBaselineWarmup(
        state_store=store,
        exchange_client=_FakeHtfBaselineExchange(),
        config=Live2StartupHtfBaselineConfig(lookback_minutes=75, request_sleep_seconds=0.0),
    )

    result = warmup.run(("AAA/USDT:USDT",), now_ms=180_000)

    assert result.status == "ready"
    state = store.get_or_create("AAA/USDT:USDT")
    candles = state.candle_book.rings[60_000].closed_snapshot()
    assert len(candles) == 2
    assert candles[-1].quote_volume == 1200.0
    assert candles[-1].number_of_trades == 48
    assert candles[-1].taker_buy_quote_volume == 660.0
    assert candles[-1].first_source == "binance_futures_klines_startup_rest_1m_htf_baseline"


def test_live2_prior_context_rolls_forward_from_live_5m_with_tolerated_gap() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    poller = Live2PriorContextPoller(
        state_store=store,
        exchange_client=_FakePriorContextExchange(),
        config=Live2PriorContextPollConfig(),
    )
    assert poller.poll_symbols_once(("AAA/USDT:USDT",))["ok"] == 1

    bucket_open = (int(time.time() * 1000) // LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS) * LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS
    store.update_aggtrade(
        replace(
            _trade(trade_time_ms=bucket_open + 1_000, price=1.0, source=LIVE2_AGGTRADE_WS_SOURCE),
            aggregate_trade_id=bucket_open + 1,
        ),
        received_at_ms=bucket_open + 1_010,
    )
    store.update_aggtrade(
        replace(
            _trade(trade_time_ms=bucket_open + 2_000, price=1.02, source=LIVE2_AGGTRADE_WS_SOURCE),
            aggregate_trade_id=bucket_open + 4,
        ),
        received_at_ms=bucket_open + 2_010,
    )
    store.close_due_candles(now_ms=bucket_open + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS)

    assert poller._apply_live_closed_5m_candles(now_ms=bucket_open + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS) == 1
    state = store.get_or_create("AAA/USDT:USDT")
    assert state.prior_context_status == "ok"
    assert state.prior_context_maintenance_source == "binance_futures_aggtrade_ws_5m_rolling_prior_context"
    assert state.prior_context_live_5m_appended_count == 1
    assert state.prior_context_live_5m_gap_tolerated_count == 1
    assert state.prior_context_last_live_5m_missing_aggtrade_ids == 2


def test_live2_prior_context_tolerates_large_live_5m_gap_as_diagnostic() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    poller = Live2PriorContextPoller(
        state_store=store,
        exchange_client=_FakePriorContextExchange(),
        config=Live2PriorContextPollConfig(),
    )
    assert poller.poll_symbols_once(("AAA/USDT:USDT",))["ok"] == 1

    bucket_open = (int(time.time() * 1000) // LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS) * LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS
    store.update_aggtrade(
        replace(
            _trade(trade_time_ms=bucket_open + 1_000, price=1.0, source=LIVE2_AGGTRADE_WS_SOURCE),
            aggregate_trade_id=bucket_open + 1,
        ),
        received_at_ms=bucket_open + 1_010,
    )
    store.update_aggtrade(
        replace(
            _trade(trade_time_ms=bucket_open + 2_000, price=1.02, source=LIVE2_AGGTRADE_WS_SOURCE),
            aggregate_trade_id=bucket_open + 100,
        ),
        received_at_ms=bucket_open + 2_010,
    )
    store.close_due_candles(now_ms=bucket_open + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS)

    assert poller._apply_live_closed_5m_candles(now_ms=bucket_open + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS) == 1
    state = store.get_or_create("AAA/USDT:USDT")
    assert state.prior_context_status == "ok"
    assert state.prior_context_live_5m_gap_rejected_count == 0
    assert state.prior_context_live_5m_gap_tolerated_count == 1
    status = poller.status()
    assert status["total_ws_5m_gap_rejected"] == 0
    assert status["total_ws_5m_gap_above_tolerance_tolerated"] == 1
    assert "gap_above_tolerance_tolerated" in str(status["last_ws_5m_gap_reason"])


def test_live2_signal_marks_stale_mark_context_as_dependency_not_ready() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.update_mark_price(
        market_id="AAAUSDT",
        received_at_ms=1_000,
        event_time_ms=1_000,
        mark_price=1.01,
        index_price=1.0,
        estimated_settle_price=None,
        funding_rate=None,
        next_funding_time_ms=None,
        source="test",
        status="ok",
        reason="ok",
    )
    ring = Live2CandleRing(timeframe_ms=5_000, max_closed_candles=10)
    ring.add_trade(_trade(trade_time_ms=10_000, price=1.0))
    ring.add_trade(_trade(trade_time_ms=14_000, price=1.01))
    ring.close_due(now_ms=15_000)
    candle = ring.latest_closed()
    assert candle is not None

    features = Live2SignalEngine(mark_stale_ms=5_000)._features(
        state=state,
        candle=candle,
        actionable_reason="test",
    )

    assert features["mark_status"] == "stale"
    assert features["mark_raw_status"] == "ok"
    assert features["mark_basis_status"] == "stale"


def test_live2_signal_features_use_decision_box_and_daily_quote_proxy() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    state.update_prior_context(
        fetched_at_ms=20_000,
        context_start_ms=0,
        context_end_ms=20_000,
        rows_received=288,
        rows_used=288,
        prior_spike_count_24h=0,
        prior_fast_fade_count_24h=0,
        prior_high_24h=1.20,
        prior_low_before_high_24h=1.00,
        prior_low_after_high_24h=1.10,
        spike_return_pct=0.03,
        fast_fade_retrace_fraction=0.55,
        source="test",
        status="ok",
        reason="test",
    )
    for ts, price in [(1_000, 1.00), (6_000, 0.99), (11_000, 1.08)]:
        state.update_aggtrade(_trade(trade_time_ms=ts, price=price), received_at_ms=ts)
        state.candle_book.close_due(now_ms=((ts // 5_000) + 1) * 5_000)
    candle = state.candle_book.rings[5_000].latest_closed()
    assert candle is not None

    features = Live2SignalEngine()._features(
        state=state,
        candle=candle,
        actionable_reason="test",
    )

    assert features["baseline_quote_daily_proxy"] == features["baseline_quote_5s"] * 17280.0
    assert features["initial_stop_at_decision"] == 0.99
    assert features["decision_box_low"] == 0.99
    assert features["decision_box_high"] == 1.08
    assert features["live_setup_reason"] == "live_setup_1m_baseline_not_ready"
    assert features["prior_up_down_whipsaw_to_impulse_range"] is None


def test_live2_backtest_like_setup_uses_backtest_stop_and_tp1_model() -> None:
    baseline_1m = tuple(
        _candle(timeframe_ms=60_000, open_time_ms=idx * 60_000, high=1.005, low=0.995)
        for idx in range(60)
    )
    setup_open_ms = 60 * 60_000
    segment = (
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms, open_price=1.0, high=1.02, low=0.98, close=1.02, quote_volume=75.0, number_of_trades=8),
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms + 5_000, open_price=1.02, high=1.05, low=1.01, close=1.045, quote_volume=75.0, number_of_trades=8),
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms + 10_000, open_price=1.045, high=1.07, low=1.04, close=1.065, quote_volume=75.0, number_of_trades=8),
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms + 15_000, open_price=1.065, high=1.08, low=1.06, close=1.07, quote_volume=75.0, number_of_trades=8),
    )

    setup = _live_backtest_like_setup(
        closed_5s=segment,
        closed_1m=baseline_1m,
        decision_candle=segment[-1],
    )

    assert setup["status"] == "ok"
    assert setup["initial_stop_at_decision"] > setup["low"]
    assert round(float(setup["initial_stop_at_decision"]), 6) == round(float(setup["decision_ema20"]), 6)
    assert setup["tp1_r"] == 0.75
    assert setup["tp1_at_decision"] == 1.15
    assert round(float(setup["prior_up_down_whipsaw_to_impulse_range"]), 6) == 0.1
    assert setup["flow_hold_count"] == 0


def test_live2_backtest_like_setup_uses_rolling_60s_not_calendar_minute() -> None:
    baseline_1m = tuple(
        _candle(timeframe_ms=60_000, open_time_ms=idx * 60_000, high=1.005, low=0.995)
        for idx in range(60)
    )
    first_open_ms = 60 * 60_000 + 25_000
    previous_close = 1.0
    segment: list[Live2Candle] = []
    for index in range(12):
        close = previous_close + 0.003
        segment.append(
            _candle(
                timeframe_ms=5_000,
                open_time_ms=first_open_ms + index * 5_000,
                open_price=previous_close,
                high=close + 0.001,
                low=previous_close - 0.001,
                close=close,
                quote_volume=75.0,
                number_of_trades=10,
            )
        )
        previous_close = close

    decision = segment[-1]
    setup = _live_backtest_like_setup(
        closed_5s=tuple(segment),
        closed_1m=baseline_1m,
        decision_candle=decision,
    )

    assert setup["status"] == "ok"
    assert setup["alignment"] == "rolling_60s_5s_step"
    assert setup["calendar_aligned"] is False
    assert setup["setup_open_ms"] == first_open_ms
    assert setup["setup_open_ms"] != (decision.open_time_ms // 60_000) * 60_000
    assert setup["closed_entry_candles"] == 12


def test_live2_backtest_like_setup_exposes_runner_shape_acceleration() -> None:
    baseline_1m = tuple(
        _candle(timeframe_ms=60_000, open_time_ms=idx * 60_000, high=1.005, low=0.995, quote_volume=100.0, number_of_trades=10)
        for idx in range(60)
    )
    first_open_ms = 60 * 60_000 + 5_000
    previous_close = 1.0
    segment: list[Live2Candle] = []
    for index in range(12):
        quote_volume = 5.0 if index < 6 else 85.0
        number_of_trades = 2 if index < 6 else 20
        close = previous_close + (0.001 if index < 6 else 0.010)
        segment.append(
            _candle(
                timeframe_ms=5_000,
                open_time_ms=first_open_ms + index * 5_000,
                open_price=previous_close,
                high=close + 0.001,
                low=previous_close - 0.001,
                close=close,
                quote_volume=quote_volume,
                number_of_trades=number_of_trades,
            )
        )
        previous_close = close

    setup = _live_backtest_like_setup(
        closed_5s=tuple(segment),
        closed_1m=baseline_1m,
        decision_candle=segment[-1],
    )

    assert setup["status"] == "ok"
    assert setup["runner_shape_quote_acceleration"] == 17.0
    assert round(float(setup["runner_shape_trade_acceleration"]), 6) == 10.0
    assert float(setup["runner_shape_range_acceleration"]) > 5.0
    assert float(setup["runner_shape_second_half_return_pct"]) > 0.0
    assert float(setup["runner_shape_top1_quote_share"]) < 0.70
    assert setup["elapsed_fraction"] == 1.0


def test_live2_runner_shape_rejects_single_print_flow() -> None:
    category = SUPPORTED_PUMP_CATEGORIES["runner_balanced"]
    evaluation = _runner_shape_accepts(
        category=category,
        features={
            "runner_shape_quote_ratio": 20.0,
            "runner_shape_trade_ratio": 8.0,
            "runner_shape_range_ratio": 4.0,
            "runner_shape_quote_acceleration": 3.0,
            "runner_shape_trade_acceleration": 2.0,
            "runner_shape_range_acceleration": 1.5,
            "runner_shape_second_half_return_pct": 0.01,
            "runner_shape_top1_quote_share": 0.85,
        },
    )

    assert evaluation is not None
    assert evaluation.accepted is False
    assert evaluation.reason == "runner_shape_top1_quote_share_above_category_max"


def test_live2_setup_math_matches_backtest_pair_candidate_row() -> None:
    baseline_1m = tuple(
        _candle(timeframe_ms=60_000, open_time_ms=idx * 60_000, high=1.005, low=0.995)
        for idx in range(60)
    )
    setup_open_ms = 60 * 60_000
    segment = (
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms, open_price=1.0, high=1.02, low=0.98, close=1.02, quote_volume=75.0, number_of_trades=8),
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms + 5_000, open_price=1.02, high=1.05, low=1.01, close=1.045, quote_volume=75.0, number_of_trades=8),
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms + 10_000, open_price=1.045, high=1.07, low=1.04, close=1.065, quote_volume=75.0, number_of_trades=8),
        _candle(timeframe_ms=5_000, open_time_ms=setup_open_ms + 15_000, open_price=1.065, high=1.08, low=1.06, close=1.07, quote_volume=75.0, number_of_trades=8),
    )
    baseline_frame = pd.DataFrame([item.to_summary_dict(prefix="").copy() for item in baseline_1m])
    entry_segment = pd.DataFrame([item.to_summary_dict(prefix="").copy() for item in segment])
    for frame in (baseline_frame, entry_segment):
        frame.rename(
            columns={
                "_open_time_ms": "timestamp",
                "_open": "open",
                "_high": "high",
                "_low": "low",
                "_close": "close",
                "_quote_volume": "quote_volume",
                "_number_of_trades": "number_of_trades",
                "_taker_buy_quote_volume": "taker_buy_quote_volume",
            },
            inplace=True,
        )
    setup_row = pd.Series(
        {
            "timestamp": setup_open_ms,
            "open": entry_segment["open"].iloc[0],
            "high": entry_segment["high"].max(),
            "low": entry_segment["low"].min(),
            "close": entry_segment["close"].iloc[-1],
            "quote_volume": entry_segment["quote_volume"].sum(),
            "number_of_trades": entry_segment["number_of_trades"].sum(),
            "taker_buy_quote_volume": entry_segment["taker_buy_quote_volume"].sum(),
        }
    )

    live_setup = _live_backtest_like_setup(
        closed_5s=segment,
        closed_1m=baseline_1m,
        decision_candle=segment[-1],
    )
    backtest_row = _build_pair_candidate_row(
        symbol="AAA/USDT:USDT",
        setup_timeframe="1m",
        entry_timeframe="5s",
        setup_ms=60_000,
        entry_ms=5_000,
        setup_idx=60,
        baseline=baseline_frame,
        setup_row=setup_row,
        entry_segment=entry_segment,
        entry_frame=entry_segment,
        config=AnomalyBacktestConfig(
            lab_config=AnomalyLabConfig(min_quote_ratio_start=5.0, min_trade_ratio_start=5.0)
        ),
    )

    assert backtest_row is not None
    for live_key, backtest_key in (
        ("quote_ratio", "start_quote_ratio"),
        ("trade_ratio", "start_trade_ratio"),
        ("start_quote_ratio_per_abs_return", "start_quote_ratio_per_abs_return"),
        ("start_trade_ratio_per_abs_return", "start_trade_ratio_per_abs_return"),
        ("prior_up_down_whipsaw_to_impulse_range", "prior_up_down_whipsaw_to_impulse_range"),
        ("flow_hold_count", "flow_hold_count_next_n_candles"),
        ("start_taker_buy_quote_share_delta", "start_taker_buy_quote_share_delta"),
        ("next_n_taker_buy_quote_share_mean", "next_n_taker_buy_quote_share_mean"),
        ("decision_ema20", "decision_ema20"),
    ):
        assert float(live_setup[live_key]) == pytest.approx(float(backtest_row[backtest_key]))


def _post_htf_acceptance_candles() -> tuple[tuple[Live2Candle, ...], tuple[Live2Candle, ...], Live2Candle]:
    baseline_1m = tuple(
        _candle(
            timeframe_ms=60_000,
            open_time_ms=idx * 60_000,
            open_price=1.0,
            high=1.005,
            low=0.995,
            close=1.0,
            quote_volume=100.0,
            number_of_trades=10,
        )
        for idx in range(60)
    )
    htf_open_ms = 60 * 60_000
    htf_closes = (1.002, 1.004, 1.006, 1.008, 1.010, 1.012, 1.014, 1.016, 1.018, 1.019, 1.020, 1.020)
    htf_segment: list[Live2Candle] = []
    previous_close = 1.0
    for index, close in enumerate(htf_closes):
        open_time_ms = htf_open_ms + index * 5_000
        htf_segment.append(
            _candle(
                timeframe_ms=5_000,
                open_time_ms=open_time_ms,
                open_price=previous_close,
                high=max(previous_close, close) + 0.001,
                low=0.995 if index == 0 else min(previous_close, close) - 0.001,
                close=close,
                quote_volume=125.0,
                number_of_trades=10,
            )
        )
        previous_close = close
    post_open_ms = htf_open_ms + 60_000
    closes = (1.022, 1.023, 1.024, 1.025, 1.026, 1.027)
    quote_volumes = (100.0, 110.0, 120.0, 90.0, 100.0, 110.0)
    confirmation_segment: list[Live2Candle] = []
    for index, (close, quote_volume) in enumerate(zip(closes, quote_volumes, strict=True)):
        open_time_ms = post_open_ms + index * 5_000
        confirmation_segment.append(
            _candle(
                timeframe_ms=5_000,
                open_time_ms=open_time_ms,
                open_price=previous_close,
                high=max(previous_close, close) + 0.001,
                low=min(previous_close, close) - 0.001,
                close=close,
                quote_volume=quote_volume,
                number_of_trades=20,
            )
        )
        previous_close = close
    return baseline_1m, tuple((*htf_segment, *confirmation_segment)), confirmation_segment[-1]


def test_live2_post_htf_acceptance_setup_uses_closed_htf_low_stop() -> None:
    closed_1m, closed_5s, decision = _post_htf_acceptance_candles()

    setup = _post_htf_acceptance_setup(
        closed_5s=closed_5s,
        closed_1m=closed_1m,
        decision_candle=decision,
    )

    assert setup["status"] == "ok"
    assert setup["confirmation_candles"] == 6
    assert setup["htf_alignment"] == "rolling_60s_5s_step"
    assert setup["htf_calendar_aligned"] is False
    assert setup["htf_return_pct"] == pytest.approx(0.02)
    assert setup["htf_quote_ratio"] == pytest.approx(15.0)
    assert setup["htf_trade_ratio"] == pytest.approx(12.0)
    assert setup["ltf6_return_pct"] > 0.005
    assert setup["ltf6_last3_quote_share"] <= 0.50
    assert setup["ltf6_top1_quote_share"] <= 0.75
    assert setup["structural_stop_source"] == "rolling_closed_htf_anomaly_low_buffered_5bps"
    assert setup["initial_stop_at_decision"] == pytest.approx(0.995 * (1.0 - 0.0005))
    assert 0.015 <= float(setup["initial_risk_pct_at_decision"]) <= 0.050
    assert setup["tp1_at_decision"] == pytest.approx(decision.close + 1.5 * (decision.close - setup["initial_stop_at_decision"]))


def test_live2_signal_selects_post_htf_acceptance_category_with_artifact_marker() -> None:
    closed_1m, closed_5s, decision = _post_htf_acceptance_candles()
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    state.update_prior_context(
        fetched_at_ms=decision.close_time_ms,
        context_start_ms=0,
        context_end_ms=decision.close_time_ms,
        rows_received=288,
        rows_used=288,
        prior_spike_count_24h=1,
        prior_fast_fade_count_24h=0,
        prior_high_24h=1.05,
        prior_low_before_high_24h=0.99,
        prior_low_after_high_24h=1.00,
        spike_return_pct=0.03,
        fast_fade_retrace_fraction=0.2,
        source="test",
        status="ok",
        reason="test",
    )
    state.candle_book.rings[60_000].closed.extend(closed_1m)
    state.candle_book.rings[5_000].closed.extend(closed_5s)

    signal = Live2SignalEngine(category_ids=("post_htf_acceptance_long",)).evaluate(
        state=state,
        candle=decision,
        actionable_reason="test",
    )

    assert signal.verdict == "selected"
    assert signal.category_id == "post_htf_acceptance_long"
    assert signal.features["post_htf_acceptance_artifact_mode"] == "post_htf_acceptance_long"
    assert signal.features["post_htf_acceptance_selected"] is True
    assert signal.signal_entry_price == decision.close
    assert signal.initial_stop_at_decision == pytest.approx(0.995 * (1.0 - 0.0005))
    assert signal.initial_risk_pct_at_decision == pytest.approx(
        (decision.close - signal.initial_stop_at_decision) / decision.close
    )
    assert signal.tp1_at_decision == pytest.approx(decision.close + 1.5 * (decision.close - signal.initial_stop_at_decision))


def test_live2_post_htf_acceptance_rejects_oi_up_price_down() -> None:
    closed_1m, closed_5s, decision = _post_htf_acceptance_candles()
    downtrend_context = tuple(
        replace(item, close=1.08, open=1.08, high=1.08, low=1.08)
        if item.close_time_ms <= decision.close_time_ms - 15 * 60_000
        else item
        for item in closed_1m
    )
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    state.update_prior_context(
        fetched_at_ms=decision.close_time_ms,
        context_start_ms=0,
        context_end_ms=decision.close_time_ms,
        rows_received=288,
        rows_used=288,
        prior_spike_count_24h=1,
        prior_fast_fade_count_24h=0,
        prior_high_24h=1.05,
        prior_low_before_high_24h=0.99,
        prior_low_after_high_24h=1.00,
        spike_return_pct=0.03,
        fast_fade_retrace_fraction=0.2,
        source="test",
        status="ok",
        reason="test",
    )
    state.update_open_interest(
        fetched_at_ms=decision.close_time_ms,
        latest_timestamp_ms=decision.close_time_ms,
        previous_timestamp_ms=decision.close_time_ms - 15 * 60_000,
        open_interest=105.0,
        previous_open_interest=100.0,
        open_interest_change_pct_3x5m=0.05,
        rows_received=4,
        source="test",
        status="ok",
        reason="test",
    )
    state.candle_book.rings[60_000].closed.extend(downtrend_context)
    state.candle_book.rings[5_000].closed.extend(closed_5s)

    signal = Live2SignalEngine(category_ids=("post_htf_acceptance_long",)).evaluate(
        state=state,
        candle=decision,
        actionable_reason="test",
    )

    assert signal.verdict == "rejected_signal_contract"
    assert "post_htf_acceptance_long:oi_up_price_down_blocked" in signal.reject_reasons
    assert signal.features["post_htf_acceptance_oi_divergence_rejected"] is True


def test_live2_deadline_evaluates_low_volume_real_bucket_for_backtest_parity() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    engine = Live2DeadlineEngine(
        state_store=store,
        config=Live2DeadlineEngineConfig(
            actionable_min_quote_volume=2_500.0,
            actionable_min_trade_count=20,
            actionable_min_abs_return_pct=0.003,
        ),
    )
    candle = _candle(timeframe_ms=5_000, open_time_ms=0, quote_volume=1.0, number_of_trades=1)

    assert engine._actionable_reason(candle=candle, return_pct=0.0) == "real_trade_bucket_for_backtest_parity"


def test_live2_deadline_expires_backlog_without_counting_near_deadline_miss() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    state.set_universe_selection(selected=True, rank=1, reason="test", selected_at_ms=0)
    for offset in range(30):
        store.update_aggtrade(
            _trade(trade_time_ms=1_000 + offset * 10, price=1.0 + offset * 0.001),
            received_at_ms=1_010 + offset * 10,
        )
    store.close_due_candles(now_ms=5_000)

    engine = Live2DeadlineEngine(
        state_store=store,
        config=Live2DeadlineEngineConfig(
            decision_deadline_ms=750,
            backlog_expire_ms=5_000,
            actionable_min_quote_volume=0.0,
            actionable_min_trade_count=1,
            actionable_min_abs_return_pct=0.0,
        ),
        live_decision_watermark_ms=lambda: 0,
    )
    result = engine.run_cycle(now_ms=11_001)

    assert result.deadline_missed_count == 0
    assert result.deadline_expired_backlog_count == 1
    assert result.decisions[0].verdict == "deadline_expired_backlog"


class _FakeExecutionExchange:
    def __init__(self) -> None:
        self.position_amount = 0.0
        self.stop_visible = True
        self.visible_stop_client_ids: set[str] = set()

    def fetch_live_account_preflight(self):
        return ExchangeLiveAccountPreflight(exchange="fake", position_mode="one_way", hedge_mode_enabled=False)

    def fetch_symbol_position_amount(self, symbol):
        return self.position_amount

    def create_market_order_with_fill(self, symbol, side, amount, *, reduce_only, client_order_id):
        if not reduce_only:
            self.position_amount += float(amount)
        else:
            self.position_amount -= float(amount)
        return ExchangeOrderFill(
            order_id=f"order-{client_order_id}",
            status="closed",
            timestamp_ms=int(time.time() * 1000),
            average_price=1.0,
            filled_amount=float(amount),
        )

    def create_stop_market_order(self, symbol, side, amount, stop_price, *, client_order_id):
        self.stop_visible = True
        self.visible_stop_client_ids.add(str(client_order_id))
        return {"id": f"stop-{client_order_id}"}

    def fetch_stop_order_by_client_order_id(self, symbol, client_order_id):
        if not self.stop_visible:
            raise LookupError("stop not visible")
        if str(client_order_id) not in self.visible_stop_client_ids:
            raise LookupError("stop not visible")
        return {"id": f"stop-{client_order_id}", "clientOrderId": client_order_id}

    def cancel_stop_order(self, symbol, order_id):
        raw = str(order_id)
        client_id = raw[5:] if raw.startswith("stop-") else raw
        self.visible_stop_client_ids.discard(client_id)
        self.stop_visible = bool(self.visible_stop_client_ids)
        return {"id": order_id, "status": "canceled"}


class _FakeStopTriggerSettlingExchange(_FakeExecutionExchange):
    def __init__(self) -> None:
        super().__init__()
        self.position_reads_after_trigger = 0

    def fetch_symbol_position_amount(self, symbol):
        if not self.stop_visible:
            self.position_reads_after_trigger += 1
            if self.position_reads_after_trigger >= 2:
                self.position_amount = 0.0
        return self.position_amount


def test_live2_execution_records_entry_attempt_timing() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    exchange = _FakeExecutionExchange()
    engine = Live2ExecutionEngine(
        exchange_client=exchange,
        config=Live2ExecutionConfig(stop_visibility_sleep_seconds=0.0),
    )
    assert engine.preflight().ready
    signal = Live2SignalDecision(
        verdict="selected",
        reason="test",
        category_id="test_category",
        category_rank=1,
        signal_entry_price=1.0,
        initial_stop_at_decision=0.99,
        initial_risk_pct_at_decision=0.01,
        tp1_at_decision=1.02,
    )
    guard = Live2EntryGuardResult(
        verdict="accepted",
        reason="entry_guard_passed",
        live_price=1.0,
        signal_age_ms=100,
        entry_price_drift_pct=0.0,
        rr_to_tp1_at_live_price=2.0,
    )

    result = engine.execute_selected(state=state, signal_decision=signal, entry_guard_result=guard)

    assert result.verdict == "selected"
    assert result.started_at_ms is not None
    assert result.finished_at_ms is not None
    assert result.duration_ms is not None
    assert result.timing["pre_position_fetch_duration_ms"] >= 0
    assert result.timing["entry_order_submit_duration_ms"] >= 0
    assert result.timing["post_position_fetch_duration_ms"] >= 0
    assert result.timing["stop_order_submit_duration_ms"] >= 0
    assert result.timing["stop_verify_duration_ms"] >= 0
    assert result.details["execution_timing"]["duration_ms"] == result.duration_ms


def test_live2_execution_zero_max_open_positions_means_unlimited_capacity() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT", "BBB/USDT:USDT"))
    exchange = _FakeExecutionExchange()
    engine = Live2ExecutionEngine(
        exchange_client=exchange,
        config=Live2ExecutionConfig(max_open_positions=0, stop_visibility_sleep_seconds=0.0),
    )
    assert engine.preflight().ready
    signal = Live2SignalDecision(
        verdict="selected",
        reason="test",
        category_id="test_category",
        category_rank=1,
        signal_entry_price=1.0,
        initial_stop_at_decision=0.99,
        initial_risk_pct_at_decision=0.01,
        tp1_at_decision=1.02,
    )
    guard = Live2EntryGuardResult(
        verdict="accepted",
        reason="entry_guard_passed",
        live_price=1.0,
        signal_age_ms=100,
        entry_price_drift_pct=0.0,
        rr_to_tp1_at_live_price=2.0,
    )

    first = engine.execute_selected(state=store.get_or_create("AAA/USDT:USDT"), signal_decision=signal, entry_guard_result=guard)
    exchange.position_amount = 0.0
    second = engine.execute_selected(state=store.get_or_create("BBB/USDT:USDT"), signal_decision=signal, entry_guard_result=guard)

    assert first.verdict == "selected"
    assert second.verdict == "selected"
    assert engine.status()["max_open_positions_unlimited"] is True
    assert engine.status()["open_protected_positions"] == 2


def test_live2_supervisor_treats_settled_stop_trigger_as_final_close() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    exchange = _FakeStopTriggerSettlingExchange()
    engine = Live2ExecutionEngine(
        exchange_client=exchange,
        config=Live2ExecutionConfig(stop_visibility_sleep_seconds=0.0),
    )
    assert engine.preflight().ready
    signal = Live2SignalDecision(
        verdict="selected",
        reason="test",
        category_id="test_category",
        category_rank=1,
        signal_entry_price=1.0,
        initial_stop_at_decision=0.99,
        initial_risk_pct_at_decision=0.01,
        tp1_at_decision=1.02,
    )
    guard = Live2EntryGuardResult(
        verdict="accepted",
        reason="entry_guard_passed",
        live_price=1.0,
        signal_age_ms=100,
        entry_price_drift_pct=0.0,
        rr_to_tp1_at_live_price=2.0,
    )
    result = engine.execute_selected(state=state, signal_decision=signal, entry_guard_result=guard)
    assert result.verdict == "selected"

    exchange.stop_visible = False
    supervisor = Live2PositionSupervisor(
        exchange_client=exchange,
        execution_engine=engine,
        config=Live2PositionSupervisorConfig(
            monitor_interval_ms=1,
            stop_trigger_settle_attempts=2,
            stop_trigger_settle_sleep_seconds=0.0,
        ),
    )
    position = engine.protected_positions_snapshot()[0]
    supervisor.set_user_data_status_provider(
        lambda: {
            "recent_order_events": [
                {
                    "symbol": "AAAUSDT",
                    "client_order_id": position.stop_client_order_id,
                    "order_id": position.stop_order_id,
                    "execution_type": "TRADE",
                    "order_status": "FILLED",
                    "last_filled_quantity": position.remaining_amount,
                    "last_filled_price": 0.99,
                    "average_price": 0.99,
                    "realized_profit": -0.12,
                    "transaction_time_ms": 123,
                    "event_time_ms": 123,
                }
            ]
        }
    )

    cycle = supervisor.run_cycle(store)

    assert cycle.integrity_error_count == 0
    assert cycle.final_close_count == 1
    assert cycle.actions[0].event_type == "position_final_close_verified"
    assert cycle.actions[0].data["realized_pnl_status"] == "recovered_from_user_data_order_trade_update"
    assert cycle.actions[0].data["realized_pnl_usdt"] == pytest.approx(-0.12)
    assert supervisor.status()["total_stop_closes"] == 1
    assert supervisor.status()["total_realized_pnl_usdt"] == pytest.approx(-0.12)
    assert engine.ready
    assert engine.protected_positions_snapshot() == ()


def test_live2_final_stop_telegram_does_not_report_fake_zero_pnl() -> None:
    action = Live2PositionSupervisorAction(
        event_type="position_final_close_verified",
        symbol="AAA/USDT:USDT",
        position_id="pos-1",
        severity=Live2Severity.INFO,
        message="flat",
        data={
            "reason": "exchange_position_flat_stop_gone",
            "realized_pnl_status": "unavailable",
            "position": {"realized_pnl_usdt": 0.0, "close_reason": "exchange_position_flat_stop_gone"},
        },
    )

    text = format_final_close_message(action)

    assert "PNL: n/a" in text
    assert "PNL: +0" not in text


def test_live2_supervisor_tp1_closes_half_and_resizes_verified_stop() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    exchange = _FakeExecutionExchange()
    engine = Live2ExecutionEngine(
        exchange_client=exchange,
        config=Live2ExecutionConfig(stop_visibility_sleep_seconds=0.0),
    )
    assert engine.preflight().ready
    signal = Live2SignalDecision(
        verdict="selected",
        reason="test",
        category_id="test_category",
        category_rank=1,
        signal_entry_price=1.0,
        initial_stop_at_decision=0.98,
        initial_risk_pct_at_decision=0.02,
        tp1_at_decision=1.04,
        features={
            "selected_source_flow_window_ms": 30_000,
            "selected_source_flow_quote_per_second": 25.0,
            "selected_source_flow_trades_per_second": 2.0,
            "selected_source_flow_quote_ratio": 12.0,
            "selected_source_flow_trade_ratio": 8.0,
            "oi_open_interest": 100.0,
            "oi_previous_open_interest": 98.0,
            "oi_change_pct_3x5m": 0.0204,
            "oi_latest_timestamp_ms": 123_000,
        },
    )
    guard = Live2EntryGuardResult(
        verdict="accepted",
        reason="entry_guard_passed",
        live_price=1.0,
        signal_age_ms=100,
        entry_price_drift_pct=0.0,
        rr_to_tp1_at_live_price=2.0,
    )
    result = engine.execute_selected(state=state, signal_decision=signal, entry_guard_result=guard)
    assert result.verdict == "selected"
    initial = engine.protected_positions_snapshot()[0]
    initial_stop_client_id = initial.stop_client_order_id
    state.aggtrade_last_price = 1.041
    supervisor = Live2PositionSupervisor(
        exchange_client=exchange,
        execution_engine=engine,
        config=Live2PositionSupervisorConfig(
            monitor_interval_ms=1,
            stop_trigger_settle_sleep_seconds=0.0,
            tp1_close_fraction=0.5,
        ),
    )

    cycle = supervisor.run_cycle(store)

    assert cycle.tp1_close_count == 1
    assert cycle.final_close_count == 0
    assert cycle.actions[0].event_type == "position_tp1_partial_close_verified"
    assert cycle.actions[0].data["reason"] == "tp1_partial_close_verified_remaining_stop_resized"
    protected = engine.protected_positions_snapshot()
    assert len(protected) == 1
    updated = protected[0]
    assert updated.status == "tp1_partial_protected_stop_verified"
    assert updated.amount == pytest.approx(6.0)
    assert updated.remaining_amount == pytest.approx(6.0)
    assert updated.tp1_close_fraction == pytest.approx(0.5)
    assert updated.tp1_closed_amount == pytest.approx(6.0)
    assert updated.stop_client_order_id != initial_stop_client_id
    assert initial_stop_client_id not in exchange.visible_stop_client_ids
    assert updated.stop_client_order_id in exchange.visible_stop_client_ids
    assert exchange.position_amount == pytest.approx(6.0)
    assert updated.entry_5m_oi_open_interest == pytest.approx(100.0)
    assert updated.source_flow_quote_per_second == pytest.approx(25.0)


def test_live2_supervisor_early_exits_when_oi_rises_and_price_stalls() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    exchange = _FakeExecutionExchange()
    engine = Live2ExecutionEngine(
        exchange_client=exchange,
        config=Live2ExecutionConfig(stop_visibility_sleep_seconds=0.0),
    )
    assert engine.preflight().ready
    signal = Live2SignalDecision(
        verdict="selected",
        reason="test",
        category_id="test_category",
        category_rank=1,
        signal_entry_price=1.0,
        initial_stop_at_decision=0.98,
        initial_risk_pct_at_decision=0.02,
        tp1_at_decision=1.04,
    )
    guard = Live2EntryGuardResult(
        verdict="accepted",
        reason="entry_guard_passed",
        live_price=1.0,
        signal_age_ms=100,
        entry_price_drift_pct=0.0,
        rr_to_tp1_at_live_price=2.0,
    )
    result = engine.execute_selected(state=state, signal_decision=signal, entry_guard_result=guard)
    assert result.verdict == "selected"
    position = engine.protected_positions_snapshot()[0]
    start_ms = ((position.opened_at_ms + 4_999) // 5_000) * 5_000
    candles = tuple(
        Live2Candle(
            timeframe_ms=5_000,
            open_time_ms=start_ms + index * 5_000,
            close_time_ms=start_ms + (index + 1) * 5_000,
            open=1.003 if index < 3 else 1.001,
            high=1.006 if index < 3 else 1.002,
            low=0.999,
            close=1.001 if index < 11 else 0.999,
            base_volume=100.0,
            quote_volume=1_000.0 if index < 3 else 250.0,
            number_of_trades=40 if index < 3 else 10,
            taker_buy_quote_volume=700.0 if index < 3 else 80.0,
            first_trade_time_ms=start_ms + index * 5_000,
            last_trade_time_ms=start_ms + (index + 1) * 5_000 - 1,
            first_source=LIVE2_AGGTRADE_WS_SOURCE,
            last_source=LIVE2_AGGTRADE_WS_SOURCE,
            live_ws_trade_count=40 if index < 3 else 10,
        )
        for index in range(12)
    )
    store.append_closed_candles(symbol=state.symbol, candles=candles)
    state.aggtrade_last_price = candles[-1].close
    state.oi_status = "ok"
    state.oi_change_pct_3x5m = 0.001
    supervisor = Live2PositionSupervisor(
        exchange_client=exchange,
        execution_engine=engine,
        config=Live2PositionSupervisorConfig(
            monitor_interval_ms=1,
            stop_trigger_settle_sleep_seconds=0.0,
        ),
    )

    cycle = supervisor.run_cycle(store)

    assert cycle.early_exit_close_count == 1
    assert cycle.final_close_count == 1
    assert cycle.actions[0].event_type == "position_early_exit_full_close_verified"
    assert cycle.actions[0].data["reason"] == "early_exit_oi_up_price_not_progressing"
    assert engine.protected_positions_snapshot() == ()
    assert exchange.position_amount == 0.0
    assert not exchange.stop_visible


def test_live2_supervisor_does_not_early_exit_before_min_hold() -> None:
    store = SymbolStateStore(("AAA/USDT:USDT",))
    state = store.get_or_create("AAA/USDT:USDT")
    exchange = _FakeExecutionExchange()
    engine = Live2ExecutionEngine(
        exchange_client=exchange,
        config=Live2ExecutionConfig(stop_visibility_sleep_seconds=0.0),
    )
    assert engine.preflight().ready
    signal = Live2SignalDecision(
        verdict="selected",
        reason="test",
        category_id="test_category",
        category_rank=1,
        signal_entry_price=1.0,
        initial_stop_at_decision=0.98,
        initial_risk_pct_at_decision=0.02,
        tp1_at_decision=1.04,
    )
    guard = Live2EntryGuardResult(
        verdict="accepted",
        reason="entry_guard_passed",
        live_price=1.0,
        signal_age_ms=100,
        entry_price_drift_pct=0.0,
        rr_to_tp1_at_live_price=2.0,
    )
    result = engine.execute_selected(state=state, signal_decision=signal, entry_guard_result=guard)
    assert result.verdict == "selected"
    position = engine.protected_positions_snapshot()[0]
    start_ms = ((position.opened_at_ms + 4_999) // 5_000) * 5_000
    candles = tuple(
        Live2Candle(
            timeframe_ms=5_000,
            open_time_ms=start_ms + index * 5_000,
            close_time_ms=start_ms + (index + 1) * 5_000,
            open=1.001,
            high=1.002,
            low=0.999,
            close=0.999,
            base_volume=100.0,
            quote_volume=500.0,
            number_of_trades=20,
            taker_buy_quote_volume=50.0,
            first_trade_time_ms=start_ms + index * 5_000,
            last_trade_time_ms=start_ms + (index + 1) * 5_000 - 1,
            first_source=LIVE2_AGGTRADE_WS_SOURCE,
            last_source=LIVE2_AGGTRADE_WS_SOURCE,
            live_ws_trade_count=20,
        )
        for index in range(3)
    )
    store.append_closed_candles(symbol=state.symbol, candles=candles)
    state.aggtrade_last_price = candles[-1].close
    state.oi_status = "ok"
    state.oi_change_pct_3x5m = 0.001
    supervisor = Live2PositionSupervisor(
        exchange_client=exchange,
        execution_engine=engine,
        config=Live2PositionSupervisorConfig(
            monitor_interval_ms=1,
            stop_trigger_settle_sleep_seconds=0.0,
        ),
    )

    cycle = supervisor.run_cycle(store)

    assert cycle.actions == []
    assert len(engine.protected_positions_snapshot()) == 1


def test_live2_status_grid_uses_four_column_operator_sections() -> None:
    text = format_live2_status_grid(
        runtime_seconds=1560.0,
        cycle_seconds=0.05,
        state_counts={"watching": 578, "actionable": 0},
        ticker_counts={"ok": 578},
        aggtrade_counts={"ok": 578},
        mark_counts={"ok": 578, "stale": 0},
        open_interest_counts={"ok": 576, "stale": 0},
        prior_context_counts={"ok": 578, "stale": 0},
        candle_counts={"live_ready": 578},
        market_data_status={
            "stream_coverage_ready": True,
            "entry_stream_ready": True,
            "market_data_ready_for_entries": True,
            "clean_windows": 647,
            "ws_health": {
                "ticker_ready": True,
                "aggtrade_ready": True,
                "shards_total": 4,
                "shards_connected": 4,
                "reconnect_attempts": 0,
            },
            "open_interest": {"ready_symbols": 576, "active_target_symbols": 578, "total_errors": 4},
            "prior_context": {
                "ready_symbols": 578,
                "active_target_symbols": 578,
                "total_ws_5m_gap_tolerated": 0,
                "total_ws_5m_gap_above_tolerance_tolerated": 0,
                "total_ws_5m_gap_rejected": 0,
            },
            "universe": {"selected_symbols": 578},
            "actionable_symbol_counts": {"current": 17, "seen": 143},
        },
        decision_status={
            "total_decisions": 14167,
            "selected_count": 0,
            "total_deadline_missed": 28,
            "total_deadline_expired_backlog": 6,
            "total_data_not_ready": 4,
            "total_data_dependency_not_ready": 0,
        },
        execution_status={
            "max_open_positions": 0,
            "max_open_positions_unlimited": True,
            "open_protected_positions": 0,
            "total_positions_protected": 2,
            "position_supervisor": {
                "total_realized_pnl_usdt": -0.34,
                "total_stop_closes": 1,
                "total_be_closes": 0,
                "total_tp1_closes": 1,
                "total_early_exit_closes": 1,
                "total_final_closes": 2,
            },
        },
        user_data_stream_status={"ready": True},
        runtime_gate_status={
            "readiness": {"new_entries_allowed": True},
            "session_seconds": {"allowed_seconds": 100.0, "blocked_seconds": 0.0},
            "decision_loop_overrun_count": 5,
            "decision_loop_max_elapsed_ms": 81,
        },
        artifact_writer_status={"ready": True, "queue_size": 0, "queue_max_size": 8192},
        session_top_snapshot={
            "session_label": "Европа",
            "items": [
                {"symbol": "BTC/USDT:USDT", "growth_fraction": 0.018},
                {"symbol": "ZEC/USDT:USDT", "growth_fraction": 0.015},
                {"symbol": "HYPE/USDT:USDT", "growth_fraction": 0.011},
                {"symbol": "SOL/USDT:USDT", "growth_fraction": 0.009},
            ],
        },
    )

    assert "◆ Соединение" in text
    assert "◆ Задержки" in text
    assert "◆ Контекст" in text
    assert "◆ Рынок" in text
    assert "◆ Торговля 100%" in text
    assert "Аномалии 14167" in text
    assert "Активные 17/143" in text
    assert "Разрывы контекста 0/0/0" in text
    assert "PNL -0.34" in text
    assert "SL 1" in text and "TP 1" in text
    assert "BTC 1.8%" in text and "SOL 0.9%" in text


class _FakeTopGrowthExchange:
    def fetch_ohlcv(self, symbol, timeframe, start_timestamp_ms, end_timestamp_ms):
        close = 1.15 if symbol.startswith("AAA") else 1.02
        return pd.DataFrame(
            {
                "timestamp": [start_timestamp_ms],
                "open": [1.0],
                "high": [close],
                "low": [0.99],
                "close": [close],
                "quote_volume": [1000.0],
                "number_of_trades": [100],
                "taker_buy_quote_volume": [700.0],
            }
        )


def test_live2_top_growth_writes_closed_hour_top_status_and_index(tmp_path) -> None:
    audit = Live2TopGrowthAudit(
        output_dir=tmp_path,
        exchange_client=_FakeTopGrowthExchange(),
        config=Live2TopGrowthAuditConfig(symbols_per_cycle=10, fetch_spacing_seconds=0.0),
    )
    now_ms = 10 * HOUR_MS + 123_000

    stats = audit.process_due(symbols=("AAA/USDT:USDT", "BBB/USDT:USDT"), now_ms=now_ms)

    assert stats.status == "completed"
    assert stats.top_count == 1
    index = tmp_path / "top_growth" / "top_growth_index.csv"
    assert index.exists()
    assert (tmp_path / stats.top_file).exists()
    assert (tmp_path / stats.status_file).exists()
    top_text = (tmp_path / stats.top_file).read_text(encoding="utf-8-sig")
    status_text = (tmp_path / stats.status_file).read_text(encoding="utf-8-sig")
    assert "AAA/USDT:USDT" in top_text
    assert "BBB/USDT:USDT" in status_text
    assert "below_threshold" in status_text
