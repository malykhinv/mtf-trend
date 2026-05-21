import json
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from cli.parser import build_parser
from data.exchanges.ccxt_types import ExchangeLiveAccountPreflight, ExchangeOrderFill
from research_tools.anomaly_live2.artifacts import Live2ArtifactWriter
from research_tools.anomaly_live2.config import AnomalyLive2Config
from research_tools.anomaly_live2.deadline import Live2DeadlineEngine, Live2DeadlineEngineConfig, Live2DecisionRecord
from research_tools.anomaly_live2.entry_guard import Live2EntryGuardResult
from research_tools.anomaly_live2.execution import Live2ExecutionConfig, Live2ExecutionEngine
from research_tools.anomaly_live2.market_data.candles import Live2AggTradeEvent, Live2CandleRing
from research_tools.anomaly_live2.market_data.prior_context import (
    LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS,
    Live2PriorContextPollConfig,
    Live2PriorContextPoller,
)
from research_tools.anomaly_live2.signal import Live2SignalEngine, _effective_context_status
from research_tools.anomaly_live2.signal import Live2SignalDecision
from research_tools.anomaly_live2.state import LIVE2_AGGTRADE_WS_SOURCE, SymbolStateStore
from research_tools.anomaly_live2.status_grid import format_live2_status_grid
from research_tools.anomaly_live2.top_growth import HOUR_MS, Live2TopGrowthAudit, Live2TopGrowthAuditConfig


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
    store.get_or_create("BBB/USDT:USDT").actionable_since_ms = 1_000

    counts = store.actionable_symbol_counts(now_ms=11_000, ttl_ms=5_000)

    assert counts == {"current": 1, "seen": 2}


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
    assert features["prior_up_down_whipsaw_to_impulse_range"] < 2.0


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
        return {"id": f"stop-{client_order_id}"}

    def fetch_stop_order_by_client_order_id(self, symbol, client_order_id):
        return {"id": f"stop-{client_order_id}", "clientOrderId": client_order_id}

    def cancel_stop_order(self, symbol, order_id):
        return {"id": order_id, "status": "canceled"}


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
        execution_status={"max_open_positions": 1, "open_protected_positions": 0, "total_positions_protected": 0},
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
