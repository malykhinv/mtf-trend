from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

from anomaly_science.strategy.session_reclaim.spec import IS_START_MS, UniverseConfig
from anomaly_science.strategy.session_reclaim.universe import (
    FORBIDDEN_UNIVERSE_COLUMNS,
    detect_symbol_events,
    partition_eligible_sources,
)


def _hour_to_minutes(timestamp_ms: int, open_price: float, high: float, low: float, close: float) -> list[dict[str, float]]:
    points = np.linspace(open_price, close, 61)
    rows: list[dict[str, float]] = []
    for minute in range(60):
        minute_open = float(points[minute])
        minute_close = float(points[minute + 1])
        minute_high = max(minute_open, minute_close) + 0.02
        minute_low = min(minute_open, minute_close) - 0.02
        if minute == 30:
            minute_high = max(minute_high, high)
            minute_low = min(minute_low, low)
        rows.append(
            {
                "timestamp": timestamp_ms + minute * 60_000,
                "open": minute_open,
                "high": minute_high,
                "low": minute_low,
                "close": minute_close,
                "quote_volume": 1_000.0,
                "trade_count": 100.0,
                "taker_buy_quote_volume": 480.0,
            }
        )
    return rows


def _synthetic_source() -> pd.DataFrame:
    start = IS_START_MS - 48 * 3_600_000
    hourly: list[tuple[int, float, float, float, float]] = []
    for hour in range(48):
        timestamp = start + hour * 3_600_000
        hourly.append((timestamp, 100.0, 100.5, 99.5, 100.0))

    day_start = IS_START_MS
    reference = (
        (100.0, 101.0, 99.5, 100.5),
        (100.5, 103.0, 100.0, 102.5),
        (102.5, 106.0, 102.0, 105.5),
        (105.5, 110.0, 105.0, 108.0),
        (108.0, 108.5, 105.0, 106.0),
        (106.0, 106.5, 102.5, 103.0),
        (103.0, 103.5, 100.5, 101.0),
        (101.0, 101.5, 99.5, 100.5),
    )
    hourly.extend((day_start + hour * 3_600_000, *values) for hour, values in enumerate(reference))
    current = (
        (105.0, 107.0, 104.0, 106.0),
        (106.0, 111.0, 105.5, 110.5),
        (110.5, 110.7, 108.5, 109.0),
        (109.0, 109.5, 106.0, 107.0),
        (107.0, 108.0, 104.0, 105.0),
    )
    hourly.extend((day_start + (8 + offset) * 3_600_000, *values) for offset, values in enumerate(current))
    rows = [row for hour in hourly for row in _hour_to_minutes(*hour)]
    return pd.DataFrame(rows)


def test_event_membership_is_final_at_signal_and_retains_unresolved_future() -> None:
    source = _synthetic_source()
    events = detect_symbol_events(source, symbol="TESTUSDT", config=UniverseConfig(minimum_swing_atr=1.0))

    assert len(events) == 1
    event = events.iloc[0]
    assert event["signal_time_ms"] == IS_START_MS + 11 * 3_600_000
    assert event["feature_cutoff_time_ms"] == event["signal_time_ms"]
    assert event["source_max_timestamp_ms"] < event["signal_time_ms"]
    assert not (FORBIDDEN_UNIVERSE_COLUMNS & set(events.columns))


def test_future_price_mutation_cannot_change_event_or_causal_fields() -> None:
    source = _synthetic_source()
    config = UniverseConfig(minimum_swing_atr=1.0)
    original = detect_symbol_events(source, symbol="TESTUSDT", config=config)
    signal_time = int(original.iloc[0]["signal_time_ms"])
    changed = source.copy()
    future = changed["timestamp"] >= signal_time
    changed.loc[future, ["open", "high", "low", "close"]] *= 1.75
    mutated = detect_symbol_events(changed, symbol="TESTUSDT", config=config)

    pd.testing.assert_frame_equal(original, mutated)


def test_future_timestamps_are_rejected_at_the_source_boundary() -> None:
    source = _synthetic_source()
    future = source.iloc[[-1]].copy()
    future["timestamp"] = pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000

    with np.testing.assert_raises_regex(ValueError, "forbidden 2026"):
        detect_symbol_events(pd.concat([source, future], ignore_index=True), symbol="TESTUSDT")


def test_source_preflight_reports_incompatible_delivery_contract(tmp_path: Path) -> None:
    valid = tmp_path / "TESTUSDT.parquet"
    delivery = tmp_path / "BTCUSDT_250627.parquet"
    _synthetic_source().head(2).to_parquet(valid, index=False)
    _synthetic_source().head(2).drop(columns=["quote_volume", "trade_count"]).to_parquet(delivery, index=False)

    eligible, excluded = partition_eligible_sources([delivery, valid])

    assert eligible == (valid,)
    assert excluded == (
        {
            "symbol": "BTCUSDT_250627",
            "reasons": ["dated_delivery_contract", "missing_required_columns"],
            "missing_columns": ["quote_volume", "trade_count"],
        },
    )
