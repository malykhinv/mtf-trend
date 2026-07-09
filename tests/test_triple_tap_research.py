from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.triple_tap.detect import CAP_CONSOL_HOURS, CAP_GAP_HOURS, GAP_HOURS, TFS
from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.audit import _causal_quantile_selection, _ledger
from anomaly_science.strategy.triple_tap.research.labels import _simulate


def test_trade_key_distinguishes_same_symbol_time_across_timeframes() -> None:
    rows = pd.DataFrame(
        [
            {"symbol": "XUSDT", "tf": "1m", "setup_type": "cap", "entry_time_ms": 1},
            {"symbol": "XUSDT", "tf": "5m", "setup_type": "cap", "entry_time_ms": 1},
        ]
    )
    assert not rows.duplicated(TRADE_KEY).any()
    assert rows.duplicated(["symbol", "entry_time_ms"], keep=False).all()


def test_label_outcome_time_is_after_fill() -> None:
    ts = np.array([1_000, 61_000, 121_000], dtype=np.int64)
    result = _simulate(
        ts,
        high=np.array([10.0, 12.0, 12.0]),
        low=np.array([10.0, 10.0, 10.0]),
        close=np.array([10.0, 11.0, 11.0]),
        entry_ms=61_000,
        entry=10.0,
        stop=9.0,
        take=11.5,
        horizon_ms=120_000,
        future_cutoff_ms=None,
    )
    assert result["outcome_time_ms"] == 121_000
    assert result["outcome_time_ms"] > 61_000
    assert result["target_resolution_time_ms"] == 121_000


def test_unresolved_horizon_does_not_cross_is_boundary() -> None:
    ts = np.array([1_000, 61_000, 121_000], dtype=np.int64)
    result = _simulate(
        ts,
        high=np.array([10.0, 10.1, 10.1]),
        low=np.array([10.0, 9.9, 9.9]),
        close=np.array([10.0, 10.0, 10.0]),
        entry_ms=61_000,
        entry=10.0,
        stop=9.0,
        take=12.0,
        horizon_ms=120_000,
        future_cutoff_ms=121_000,
    )
    assert result["label"] == "unresolved_is_boundary"
    assert np.isnan(result["r_multiple"])
    assert np.isnan(result["outcome_time_ms"])


def test_causal_threshold_never_uses_current_week_scores() -> None:
    frame = pd.DataFrame(
        {"model_week": ["2025-W01", "2025-W01", "2025-W02", "2025-W02"], "oof_prob": [0.1, 0.9, 0.69, 0.8]}
    )
    frame = frame.rename(columns={"oof_prob": "model_score_r"})
    selected = _causal_quantile_selection(frame, 0.75)
    assert not selected.iloc[:2].any()
    assert selected.iloc[2:].tolist() == [False, True]


def test_portfolio_rejects_overlapping_same_symbol() -> None:
    base = {
        "tf": "1m",
        "setup_type": "cap",
        "r_multiple": 1.0,
        "dist_stop": 0.1,
        "label": "win",
        "model_score_r": 0.8,
    }
    trades = pd.DataFrame(
        [
            {**base, "symbol": "XUSDT", "entry_time_ms": 0, "fill_time_ms": 0, "outcome_time_ms": 100},
            {**base, "symbol": "XUSDT", "entry_time_ms": 1, "fill_time_ms": 1, "outcome_time_ms": 101},
        ]
    )
    ledger, rejects = _ledger(trades, risk_pct=0.02, max_open=3, slip_pct=0.005)
    assert len(ledger) == 1
    assert rejects["same_symbol"] == 1


def test_notional_cap_reduces_effective_risk_for_wide_stop() -> None:
    trade = pd.DataFrame(
        [
            {
                "symbol": "XUSDT",
                "tf": "1m",
                "setup_type": "cap",
                "entry_time_ms": 0,
                "fill_time_ms": 0,
                "outcome_time_ms": 100,
                "r_multiple": 1.0,
                "dist_stop": 0.04,
                "label": "win",
                "model_score_r": 1.0,
            }
        ]
    )
    ledger, _ = _ledger(
        trade,
        risk_pct=0.02,
        max_open=1,
        slip_pct=0.0,
        max_notional_multiple=0.25,
    )
    assert ledger.loc[0, "risk_dollars"] == 100.0


def test_three_minute_timeframe_has_explicit_structural_contract() -> None:
    assert TFS["3m"] == 3
    assert GAP_HOURS["1m"][0] < GAP_HOURS["3m"][0] < GAP_HOURS["5m"][0]
    assert CAP_GAP_HOURS["1m"][1] < CAP_GAP_HOURS["3m"][1] < CAP_GAP_HOURS["5m"][1]
    assert CAP_CONSOL_HOURS["1m"] < CAP_CONSOL_HOURS["3m"] < CAP_CONSOL_HOURS["5m"]
