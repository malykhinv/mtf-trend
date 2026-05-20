import json
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from cli.parser import build_parser
from research_tools.anomaly_live2.artifacts import Live2ArtifactWriter
from research_tools.anomaly_live2.config import AnomalyLive2Config
from research_tools.anomaly_live2.market_data.candles import Live2AggTradeEvent, Live2CandleRing
from research_tools.anomaly_live2.market_data.prior_context import (
    LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS,
    Live2PriorContextPollConfig,
    Live2PriorContextPoller,
)
from research_tools.anomaly_live2.signal import _effective_context_status
from research_tools.anomaly_live2.state import LIVE2_AGGTRADE_WS_SOURCE, SymbolStateStore


def _trade(*, trade_time_ms: int, price: float = 1.0, source: str = LIVE2_AGGTRADE_WS_SOURCE) -> Live2AggTradeEvent:
    return Live2AggTradeEvent(
        symbol="AAA/USDT:USDT",
        market_id="AAAUSDT",
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


def test_live2_prior_context_rejects_large_live_5m_gap() -> None:
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

    assert poller._apply_live_closed_5m_candles(now_ms=bucket_open + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS) == 0
    state = store.get_or_create("AAA/USDT:USDT")
    assert state.prior_context_status == "ws_gap_exceeds_tolerance"
    assert state.prior_context_live_5m_gap_rejected_count == 1
    assert "gap_exceeds_tolerance" in state.prior_context_reason
