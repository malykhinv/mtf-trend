from __future__ import annotations

import pandas as pd

from strategy.hourly_asia_pump.session_short_edge import (
    _build_dynamic_minute_filters,
    _build_standard_minute_filters,
    _resolve_session_window,
)


def test_resolve_session_window() -> None:
    assert _resolve_session_window("america") == (16, 0)
    assert _resolve_session_window("asia") == (0, 8)


def test_build_standard_minute_filters_contains_expected_groups() -> None:
    filters = {item.filter_id: item for item in _build_standard_minute_filters()}
    assert tuple(filters["quarter_hours"].allowed_minutes) == (0, 15, 30, 45)
    assert tuple(filters["all_5m_minutes"].allowed_minutes) == tuple(range(0, 60, 5))


def test_build_dynamic_minute_filters_uses_best_exact_minutes() -> None:
    exact_best = pd.DataFrame(
        [
            {"minute_filter_id": "minute_00", "trades_per_year": 8.0, "mean_return_pct": 0.04, "annualized_unit_pnl_pct": 0.30, "stability_score": 100.0},
            {"minute_filter_id": "minute_15", "trades_per_year": 8.0, "mean_return_pct": 0.03, "annualized_unit_pnl_pct": 0.25, "stability_score": 90.0},
            {"minute_filter_id": "minute_20", "trades_per_year": 7.0, "mean_return_pct": 0.025, "annualized_unit_pnl_pct": 0.22, "stability_score": 80.0},
            {"minute_filter_id": "minute_25", "trades_per_year": 6.0, "mean_return_pct": 0.021, "annualized_unit_pnl_pct": 0.20, "stability_score": 70.0},
        ]
    )

    filters = _build_dynamic_minute_filters(exact_best, max_source_minutes=4)
    filter_ids = {item.filter_id for item in filters}

    assert "combo_00_15" in filter_ids
    assert "combo_00_15_20_25" in filter_ids
