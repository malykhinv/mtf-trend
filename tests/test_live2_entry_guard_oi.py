import pytest

from research_tools.anomaly_live2.entry_guard import Live2EntryGuardEngine
from research_tools.anomaly_live2.signal import Live2SignalDecision
from research_tools.anomaly_live2.state import SymbolState


def _selected_signal() -> Live2SignalDecision:
    return Live2SignalDecision(
        verdict="selected",
        reason="test",
        signal_entry_price=1.0,
        initial_stop_at_decision=0.97,
        tp1_at_decision=1.05,
    )


def _priced_state() -> SymbolState:
    state = SymbolState("AAA/USDT:USDT")
    state.aggtrade_last_price = 1.0
    state.aggtrade_source = "test"
    return state


def test_entry_guard_rejects_current_oi_drop_from_session_baseline() -> None:
    state = _priced_state()
    state.current_oi_status = "ok"
    state.current_oi_last_seen_ms = 10_000
    state.current_oi_timestamp_ms = 9_900
    state.current_oi_open_interest = 99.0
    state.current_oi_first_ok_status = "ok"
    state.current_oi_first_ok_seen_ms = 1_000
    state.current_oi_first_ok_timestamp_ms = 900
    state.current_oi_first_ok_open_interest = 100.0

    result = Live2EntryGuardEngine().evaluate(
        state=state,
        signal_decision=_selected_signal(),
        signal_timestamp_ms=9_900,
        now_ms=10_100,
    )

    assert result.verdict == "rejected_entry_guard"
    assert result.reason == "current_oi_drop_from_first_ok_before_execution"
    assert result.features["current_oi_change_pct_from_first_ok"] == pytest.approx(-0.01)


def test_entry_guard_rejects_negative_5m_oi_change() -> None:
    state = _priced_state()
    state.oi_status = "ok"
    state.oi_open_interest = 99.0
    state.oi_previous_open_interest = 100.0
    state.oi_change_pct_3x5m = -0.01
    state.oi_latest_timestamp_ms = 10_000

    result = Live2EntryGuardEngine().evaluate(
        state=state,
        signal_decision=_selected_signal(),
        signal_timestamp_ms=9_900,
        now_ms=10_100,
    )

    assert result.verdict == "rejected_entry_guard"
    assert result.reason == "oi_3x5m_drop_before_execution"
    assert result.features["oi_3x5m_change_pct_for_entry_guard"] == pytest.approx(-0.01)


def test_entry_guard_does_not_reject_missing_oi_context() -> None:
    result = Live2EntryGuardEngine().evaluate(
        state=_priced_state(),
        signal_decision=_selected_signal(),
        signal_timestamp_ms=9_900,
        now_ms=10_100,
    )

    assert result.verdict == "accepted"
    assert result.reason == "entry_guard_passed"
