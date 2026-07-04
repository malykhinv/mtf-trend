from __future__ import annotations

import numpy as np
import pytest

from anomaly_science.strategy.bee_bite.spring import (
    CAUSAL_METRICS,
    _pump_shape,
    _swing_geometry,
    _support_tests,
)


def test_support_tests_count_distinct_visits() -> None:
    lows = np.array([10.5, 10.05, 10.04, 10.4, 10.08, 10.5])

    count, bars_since = _support_tests(
        lows, start=0, end=len(lows), range_low=10.0, range_height=1.0
    )

    assert count == 2
    assert bars_since == 2


def test_causal_metrics_exclude_post_entry_diagnostics() -> None:
    forbidden = {
        "vol_after_vs_before",
        "trade_after_vs_before",
        "trade_flow_after",
        "taker_after",
        "taker_delta",
        "mfe5",
        "mae5",
        "ret5",
    }

    assert forbidden.isdisjoint(CAUSAL_METRICS)


def test_swing_geometry_counts_boundary_episodes_and_alternation() -> None:
    low = np.array([10.2, 10.0, 10.4, 10.1, 10.5])
    high = np.array([10.4, 10.5, 11.0, 10.6, 11.0])
    close = np.array([10.3, 10.2, 10.8, 10.3, 10.9])

    result = _swing_geometry(low, high, close, 0, 4, 10.0, 11.0)

    assert result["lower_test_count"] == 2
    assert result["upper_test_count"] == 1
    assert result["swing_alternation_ratio"] == 1.0


def test_pump_shape_distinguishes_direct_green_path() -> None:
    o = np.array([10.0, 11.0, 12.0])
    h = np.array([11.1, 12.1, 13.1])
    low = np.array([9.9, 10.9, 11.9])
    c = np.array([11.0, 12.0, 13.0])
    vol = np.ones(3)

    result = _pump_shape(o, h, low, c, vol, 0, 2, 10.0, 13.0)

    assert result["pump_duration"] == 3
    assert result["pump_path_efficiency"] == pytest.approx(1.0)
    assert result["pump_green_fraction"] == 1.0
