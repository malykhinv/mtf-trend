from research_tools.anomaly_live2.market_data.candles import Live2AggTradeEvent, Live2CandleRing
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
