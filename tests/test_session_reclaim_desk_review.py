from __future__ import annotations

import json

import pandas as pd

from anomaly_science.annotation.desk.ui import LABELER_HTML
from anomaly_science.strategy.session_reclaim.desk_review import build_desk_candidates
from anomaly_science.strategy.session_reclaim.spec import (
    MECHANICS_SCHEMA_VERSION,
    PROTOCOL_FREEZE_ID,
)


def _event(event_id: str, signal_ms: int) -> dict[str, object]:
    return {
        "event_id": event_id,
        "symbol": "TESTUSDT",
        "reference_start_time_ms": signal_ms - 10 * 3_600_000,
        "reference_end_time_ms": signal_ms - 4 * 3_600_000,
        "reference_high_time_ms": signal_ms - 6 * 3_600_000,
        "reference_high": 110.0,
        "reference_mid": 100.0,
        "reference_low": 90.0,
        "reference_swing_rise_atr": 2.4,
        "reference_swing_fall_atr": 2.2,
        "poke_start_time_ms": signal_ms - 2 * 3_600_000,
        "poke_peak_time_ms": signal_ms - 3_600_000,
        "poke_high": 112.0,
        "poke_depth_fraction_of_range": 0.1,
        "signal_bar_start_time_ms": signal_ms - 3_600_000,
        "signal_time_ms": signal_ms,
        "order_activation_time_ms": signal_ms,
        "signal_close": 108.0,
        "signal_quote_volume": 1_000_000.0,
        "signal_trade_count": 10_000.0,
        "signal_taker_buy_share": 0.49,
        "upper_structure_time_ms": signal_ms - 12 * 3_600_000,
        "upper_structure_price": 115.0,
    }


def _mechanics(event_id: str, status: str) -> dict[str, object]:
    return {
        "protocol_freeze_id": PROTOCOL_FREEZE_ID,
        "mechanics_schema_version": MECHANICS_SCHEMA_VERSION,
        "event_id": event_id,
        "variant_id": "market_close1_mid",
        "entry_policy": "market_reclaim",
        "invalidation_policy": "close1_uppercat",
        "profit_policy": "mid_full",
        "status": status,
        "entry_status": "filled",
        "hard_stop_price": 115.0,
        "same_minute_ambiguous": False,
    }


def test_desk_membership_keeps_censored_signals_and_is_outcome_independent() -> None:
    first_signal = int(pd.Timestamp("2025-07-01T12:00:00Z").timestamp() * 1_000)
    universe = pd.DataFrame([_event("a", first_signal), _event("b", first_signal + 3_600_000)])
    mechanics = pd.DataFrame([_mechanics("a", "resolved"), _mechanics("b", "censored")])

    candidates = build_desk_candidates(universe, mechanics)
    changed = mechanics.copy()
    changed.loc[changed["event_id"] == "a", "status"] = "censored"
    changed_candidates = build_desk_candidates(universe, changed)

    assert set(candidates["event_id"]) == {"a", "b"}
    assert candidates[["event_id", "selection_hash", "score"]].equals(
        changed_candidates[["event_id", "selection_hash", "score"]]
    )
    payload = json.loads(candidates.set_index("event_id").loc["b", "trade_review_variants_json"])
    assert payload[0]["status"] == "censored"
    assert candidates["review_end_ms"].lt(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000).all()


def test_core_desk_exposes_complete_generic_trade_passport() -> None:
    assert 'id="tradePassport"' in LABELER_HTML
    assert "function renderTradePassport()" in LABELER_HTML
    assert "No executions. This signal remains in the denominator." in LABELER_HTML
    assert "complete 48h lifecycle" in LABELER_HTML
    assert "selectedTradeReviewVariant(e)" in LABELER_HTML
