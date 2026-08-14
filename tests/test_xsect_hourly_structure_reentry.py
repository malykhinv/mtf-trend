from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.hourly_structure_reentry import (
    VisibleStructureSpec,
    attach_visible_structure,
    build_reentry_ledgers,
)


def _structure_frame(prices: np.ndarray) -> pd.DataFrame:
    open_time = pd.date_range("2025-01-01", periods=len(prices), freq="h", tz="UTC")
    return pd.DataFrame({
        "open_time": open_time,
        "snapshot_time": open_time + pd.Timedelta(hours=1),
        "high": prices * 1.001,
        "low": prices * 0.999,
        "close": prices,
    })


def test_adaptive_structure_is_prefix_causal_and_anchors_exact_highs() -> None:
    prices = np.concatenate([
        np.linspace(100.0, 110.0, 30),
        np.linspace(110.0, 100.0, 20),
        np.linspace(100.0, 106.0, 20),
        np.linspace(106.0, 95.0, 20),
        np.linspace(95.0, 100.0, 20),
    ])
    base = _structure_frame(prices)
    changed = base.copy()
    changed.loc[100:, ["high", "low", "close"]] *= 3.0
    spec = VisibleStructureSpec(range_lookback_bars=12, reversal_range_multiple=2.0)
    left = attach_visible_structure(base.iloc[:100], spec=spec)
    right = attach_visible_structure(changed, spec=spec)
    columns = [
        "last_swing_high_price",
        "last_swing_high_pivot_time",
        "last_swing_high_confirmed_time",
        "last_swing_low_price",
        "protected_lower_high_price",
        "protected_lower_high_pivot_time",
        "protected_lower_high_confirmed_time",
        "bearish_structure",
    ]
    pd.testing.assert_frame_equal(left[columns], right.iloc[:100][columns].reset_index(drop=True))
    protected = left["protected_lower_high_price"].dropna()
    assert len(protected) > 0
    assert np.isclose(protected.iloc[-1], base.loc[49:75, "high"].max())
    valid = left["protected_lower_high_confirmed_time"].notna()
    assert (
        left.loc[valid, "protected_lower_high_confirmed_time"]
        <= left.loc[valid, "snapshot_time"]
    ).all()


def _episode_snapshots(*, bearish: bool = True) -> pd.DataFrame:
    open_time = pd.date_range("2025-02-01", periods=30, freq="h", tz="UTC")
    rows = []
    for i, timestamp in enumerate(open_time):
        close = 108.0 if i < 3 else 98.0 - 0.1 * (i - 3)
        high = 110.0 if i == 0 else max(close + 1.0, 105.0 if i == 2 else close + 1.0)
        rows.append({
            "trade_id": "TEST|2025-02-01",
            "symbol": "TESTUSDT",
            "entry_time": open_time[0] - pd.Timedelta(hours=12),
            "scheduled_exit_time": open_time[-1] + pd.Timedelta(hours=48),
            "open_time": timestamp,
            "snapshot_time": timestamp + pd.Timedelta(hours=1),
            "remaining_holding_hours": 100.0 - i,
            "eligible_24h": True,
            "core_z2": i == 0,
            "last_swing_low_price": 90.0,
            "last_swing_low_pivot_time": open_time[0] - pd.Timedelta(hours=6),
            "last_swing_low_confirmed_time": open_time[0] - pd.Timedelta(hours=3),
            "open": close,
            "high": high,
            "low": close - 1.0,
            "close": close,
            "quote_volume": 1_000_000.0,
            "taker_buy_share_3h": 0.60 if i < 3 else 0.45,
            "quote_volume_z60": 4.0 if i == 0 else 0.0,
            "trade_count_z60": 4.0 if i == 0 else 0.0,
            "protected_lower_high_price": 105.0 if i >= 3 else np.nan,
            "protected_lower_high_pivot_time": open_time[1] if i >= 3 else pd.NaT,
            "protected_lower_high_confirmed_time": open_time[3] + pd.Timedelta(hours=1) if i >= 3 else pd.NaT,
            "bearish_structure": bearish and i >= 3,
            "future_close_return_12h": -0.03,
            "future_close_return_24h": -0.06,
            "future_mae_12h": 0.01,
            "future_mae_24h": 0.02,
            "future_mfe_12h": -0.05,
            "future_mfe_24h": -0.08,
        })
    return pd.DataFrame(rows)


def test_combined_reentry_requires_zone_flow_and_restored_structure() -> None:
    episodes, signals = build_reentry_ledgers(_episode_snapshots())
    assert len(episodes) == 1
    assert set(signals["variant"]) == {
        "midpoint_only",
        "midpoint_flow",
        "midpoint_structure",
        "combined",
    }
    combined = signals.loc[signals["variant"] == "combined"].iloc[0]
    assert combined["signal_delay_hours"] >= 2.0
    assert combined["signal_close"] < combined["zone_lower"]
    assert combined["signal_close"] < combined["structural_stop_anchor"] < combined["event_high_price"]
    assert combined["future_close_return_24h"] < 0.0

    _, no_structure = build_reentry_ledgers(_episode_snapshots(bearish=False))
    assert "combined" not in set(no_structure["variant"])
    assert "midpoint_flow" in set(no_structure["variant"])

