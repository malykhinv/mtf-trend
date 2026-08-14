from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.hourly_adverse_long import (
    AdverseLongSpec,
    attach_active_short_trades,
    build_symbol_hazard_frame,
    trade_catastrophe_table,
)


def _source(periods: int = 220) -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-01", periods=periods, freq="h", tz="UTC")
    phase = np.arange(periods, dtype=float)
    open_ = 100.0 + 0.02 * np.sin(phase / 5.0)
    close = open_ * (1.0 + 0.0005 * np.sin(phase / 3.0))
    quote = 1_000_000.0 * (1.0 + 0.08 * np.sin(phase / 7.0))
    trades = 10_000.0 * (1.0 + 0.07 * np.cos(phase / 9.0))
    return pd.DataFrame({
        "timestamp": timestamps.astype("int64") // 1_000_000,
        "open": open_,
        "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999,
        "close": close,
        "volume": quote / open_,
        "quote_volume": quote,
        "trade_count": trades,
        "taker_buy_quote_volume": quote * 0.50,
    })


def test_hourly_hazard_features_are_prefix_causal() -> None:
    source = _source()
    prefix = source.iloc[:170].copy()
    changed = source.copy()
    changed.loc[190:, ["open", "high", "low", "close"]] *= 5.0
    changed.loc[190:, ["volume", "quote_volume", "trade_count", "taker_buy_quote_volume"]] *= 20.0

    left = build_symbol_hazard_frame(prefix, symbol="TESTUSDT")
    right = build_symbol_hazard_frame(changed, symbol="TESTUSDT")
    cutoff = pd.Timestamp("2025-01-07", tz="UTC")
    columns = [
        "open_time",
        "snapshot_time",
        "move_1h",
        "return_3h",
        "return_12h",
        "quote_volume_z60",
        "trade_count_z60",
        "range_z60",
        "breakout_24h",
        "taker_buy_share_3h",
        "price_only",
        "core_z2",
        "core_z3",
        "core_z4",
        "core_z3_buyflow",
    ]
    pd.testing.assert_frame_equal(
        left.loc[left["snapshot_time"] <= cutoff, columns].reset_index(drop=True),
        right.loc[right["snapshot_time"] <= cutoff, columns].reset_index(drop=True),
    )


def test_hourly_hazard_time_contract_is_strict() -> None:
    frame = build_symbol_hazard_frame(_source(), symbol="TESTUSDT")
    assert (frame["feature_cutoff_time"] == frame["snapshot_time"]).all()
    assert (frame["future_start_time"] > frame["snapshot_time"]).all()
    assert (frame["label_end_time_12h"] > frame["snapshot_time"]).all()


def test_positive_price_and_activity_are_both_required_for_primary_trigger() -> None:
    source = _source()
    event = 100
    source.loc[event, "close"] = source.loc[event, "open"] * 1.04
    source.loc[event, "high"] = source.loc[event, "close"] * 1.002
    source.loc[event, "low"] = source.loc[event, "open"] * 0.999
    source.loc[event, "quote_volume"] *= 12.0
    source.loc[event, "volume"] *= 12.0
    source.loc[event, "trade_count"] *= 12.0
    source.loc[event, "taker_buy_quote_volume"] = source.loc[event, "quote_volume"] * 0.60
    frame = build_symbol_hazard_frame(source, symbol="TESTUSDT")
    row = frame.iloc[event]
    assert bool(row["price_only"])
    assert bool(row["core_z3"])
    assert bool(row["core_z3_buyflow"])

    down = _source()
    down.loc[event, "close"] = down.loc[event, "open"] * 0.96
    down.loc[event, "low"] = down.loc[event, "close"] * 0.998
    down.loc[event, "high"] = down.loc[event, "open"] * 1.001
    down.loc[event, "quote_volume"] *= 12.0
    down.loc[event, "trade_count"] *= 12.0
    down_frame = build_symbol_hazard_frame(down, symbol="TESTUSDT")
    assert not bool(down_frame.iloc[event]["core_z3"])


def test_active_short_attachment_excludes_longs_and_post_exit_rows() -> None:
    features = build_symbol_hazard_frame(_source(), symbol="TESTUSDT")
    short_entry = pd.Timestamp("2025-01-04 00:00", tz="UTC")
    short_exit = pd.Timestamp("2025-01-06 00:00", tz="UTC")
    trades = pd.DataFrame([
        {
            "symbol": "TESTUSDT",
            "entry_time": short_entry,
            "exit_time": short_exit,
            "entry_price": 100.0,
            "weight": -0.05,
            "seg_ret": 0.01,
        },
        {
            "symbol": "TESTUSDT",
            "entry_time": short_entry,
            "exit_time": short_exit,
            "entry_price": 100.0,
            "weight": 0.05,
            "seg_ret": -0.01,
        },
    ])
    active = attach_active_short_trades(features, trades)
    assert active["trade_id"].nunique() == 1
    assert (active["snapshot_time"] > short_entry).all()
    assert (active["snapshot_time"] < short_exit).all()
    assert (active["entry_weight"] < 0.0).all()
    expected = active["remaining_holding_hours"] >= 12
    assert (active["eligible_12h"] == expected).all()


def test_peak_bar_close_signal_is_not_counted_as_pre_peak_warning() -> None:
    times = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    rows = []
    for index, open_time in enumerate(times):
        rows.append({
            "trade_id": "TEST|trade",
            "symbol": "TESTUSDT",
            "open_time": open_time,
            "snapshot_time": open_time + pd.Timedelta(hours=1),
            "entry_time": times[0] - pd.Timedelta(hours=1),
            "scheduled_exit_time": times[-1] + pd.Timedelta(hours=2),
            "entry_price": 100.0,
            "trade_seg_ret": 0.10,
            "high": 100.0 if index == 0 else 120.0,
            "price_only": False,
            "core_z2": False,
            "core_z3": index == 1,
            "core_z4": False,
            "core_z3_buyflow": False,
        })
    result = trade_catastrophe_table(pd.DataFrame(rows))
    assert not bool(result.iloc[0]["core_z3_covered_before_peak"])
    assert pd.isna(result.iloc[0]["core_z3_first_warning_time"])
