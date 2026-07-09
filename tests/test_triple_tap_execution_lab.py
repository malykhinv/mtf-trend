from __future__ import annotations

import numpy as np

from anomaly_science.strategy.triple_tap.research.execution_lab import _simulate_from_fill


def test_partial_culmination_then_level_stop_is_structural_and_pessimistic() -> None:
    ts = np.array([0, 60_000, 120_000], dtype=np.int64)
    result = _simulate_from_fill(
        ts,
        high=np.array([11.0, 12.5, 12.0]),
        low=np.array([10.5, 10.4, 10.0]),
        close=np.array([10.8, 11.0, 10.5]),
        first_bar=0,
        fill_time_ms=0,
        entry=11.0,
        stop=9.0,
        final_target=15.0,
        end_ms=180_000,
        middle_target=12.0,
        partial_fraction=0.5,
        trail_to_level=10.5,
    )
    # 0.5 * (+0.5R at culmination) + 0.5 * (-0.25R at level stop)
    assert result["r_multiple"] == 0.125
    assert result["status"] == "middle_then_stop_same_bar"


def test_full_middle_exit_uses_prior_culmination_not_fixed_percent() -> None:
    ts = np.array([0, 60_000], dtype=np.int64)
    result = _simulate_from_fill(
        ts,
        high=np.array([11.5, 12.1]),
        low=np.array([10.5, 10.5]),
        close=np.array([11.0, 12.0]),
        first_bar=0,
        fill_time_ms=0,
        entry=11.0,
        stop=9.0,
        final_target=15.0,
        end_ms=120_000,
        middle_target=12.0,
        partial_fraction=1.0,
    )
    assert result["status"] == "middle_target"
    assert result["r_multiple"] == 0.5


def test_structural_fraction_target_is_between_known_anchors() -> None:
    level = 10.0
    final_target = 18.0
    structural_half = level + 0.5 * (final_target - level)
    assert structural_half == 14.0
