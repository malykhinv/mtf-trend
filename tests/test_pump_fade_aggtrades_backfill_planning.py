from __future__ import annotations

import pandas as pd

from anomaly_science.binance_vision_aggtrades_backfill import required_symbol_days


def test_required_symbol_days_covers_ignition_lookback_and_last_snapshot() -> None:
    # Ignition at 2024-01-02T00:30:00Z; a 240-minute lookback reaches back into 2024-01-01.
    ignition_ms = pd.Timestamp("2024-01-02T00:30:00Z").value // 1_000_000
    last_snapshot_ms = pd.Timestamp("2024-01-02T05:00:00Z").value // 1_000_000
    events = pd.DataFrame(
        {
            "event_id": [f"BTCUSDT:{ignition_ms}", f"BTCUSDT:{ignition_ms}"],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "snapshot_time_ms": [ignition_ms, last_snapshot_ms],
        }
    )

    day_map = required_symbol_days(events, lookback_minutes=240)

    assert set(day_map) == {"BTCUSDT"}
    days = day_map["BTCUSDT"]
    assert {d.isoformat() for d in days} == {"2024-01-01", "2024-01-02"}


def test_required_symbol_days_short_lookback_stays_within_single_day() -> None:
    ignition_ms = pd.Timestamp("2024-03-05T12:00:00Z").value // 1_000_000
    events = pd.DataFrame(
        {
            "event_id": [f"ETHUSDT:{ignition_ms}"],
            "symbol": ["ETHUSDT"],
            "snapshot_time_ms": [ignition_ms],
        }
    )

    day_map = required_symbol_days(events, lookback_minutes=30)

    assert {d.isoformat() for d in day_map["ETHUSDT"]} == {"2024-03-05"}


def test_required_symbol_days_empty_events_returns_empty_map() -> None:
    events = pd.DataFrame(columns=["event_id", "symbol", "snapshot_time_ms"])
    assert required_symbol_days(events, lookback_minutes=240) == {}
