from __future__ import annotations

import pandas as pd
import pytest

from anomaly_science.strategy.bee_bite.analyze import (
    equal_weight_symbol_ev,
    trader_metrics,
    top_symbol_concentration,
)


def test_equal_weight_symbol_ev_does_not_overweight_frequent_symbol() -> None:
    frame = pd.DataFrame(
        {"symbol": ["A", "A", "A", "B"], "net_wick_perehai": [0.03, 0.03, 0.03, -0.01]}
    )

    assert equal_weight_symbol_ev(frame) == pytest.approx(0.01)


def test_top_symbol_concentration_exposes_losing_remainder() -> None:
    frame = pd.DataFrame(
        {"symbol": ["A", "B", "C"], "net_wick_perehai": [0.03, 0.02, -0.01]}
    )

    assert top_symbol_concentration(frame, count=2) == 1.25


def test_trader_metrics_separate_win_and_loss_holding_times() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["A", "B", "C"],
            "mo": ["2025-01", "2025-01", "2025-02"],
            "entry_date_utc": ["2025-01-01", "2025-01-01", "2025-02-01"],
            "entry_time_ms": [1, 2, 3],
            "net_wick_perehai": [-0.02, 0.03, 0.01],
            "holding_min_wick_perehai": [2, 8, 12],
        }
    )

    metrics = trader_metrics(frame)

    assert metrics["monthly_min"] == 1
    assert metrics["monthly_max"] == 2
    assert metrics["loss_holding_median"] == 2
    assert metrics["win_holding_median"] == 10
    assert metrics["positive_days"] == 2
