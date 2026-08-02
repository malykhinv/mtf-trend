from __future__ import annotations

import numpy as np

from anomaly_science.strategy.daily_anomaly_hold.research.daily_anomaly_hold import _event_rows


def _daily_fixture(days: int = 40) -> dict[str, np.ndarray]:
    return {
        "timestamp": np.arange(days, dtype=np.int64) * 86_400_000,
        "open": np.full(days, 100.0),
        "high": np.full(days, 102.0),
        "low": np.full(days, 98.0),
        "close": np.full(days, 100.0),
        "quote_volume": np.ones(days),
    }


def _add_sleep_to_pump(daily: dict[str, np.ndarray]) -> None:
    # Two positive daily candles start from an inactive-volume day.  Together
    # they rise 25%, each carries x5 of the 20-day sleep-volume median, and
    # their combined range is [100, 130].
    daily["open"][20], daily["high"][20], daily["low"][20], daily["close"][20], daily["quote_volume"][20] = 100.0, 115.0, 100.0, 112.0, 5.0
    daily["open"][21], daily["high"][21], daily["low"][21], daily["close"][21], daily["quote_volume"][21] = 112.0, 130.0, 110.0, 125.0, 5.0


def _add_compact_hold(daily: dict[str, np.ndarray], start: int, count: int) -> None:
    for idx in range(start, start + count):
        daily["open"][idx], daily["high"][idx], daily["low"][idx], daily["close"][idx] = 126.0, 128.0, 118.0, 126.0


def test_daily_anomaly_hold_requires_two_day_sleep_to_volume_pump_and_compact_hold() -> None:
    daily = _daily_fixture()
    _add_sleep_to_pump(daily)
    _add_compact_hold(daily, start=22, count=3)
    daily["close"][25:] = 126.0

    rows = _event_rows("TESTUSDT", daily)

    assert len(rows) == 4
    assert {row["tf"] for row in rows} == {"1d", "1h", "15m", "10m"}
    assert all(row["daily_hold_floor"] == 124.0 for row in rows)
    assert all(row["daily_next_close"] == 126.0 for row in rows)
    assert all(row["daily_next_close_position_pct"] == 26.0 / 30.0 for row in rows)
    assert all(row["daily_hold_candle_count"] == 3 for row in rows)
    assert all(row["daily_pump_candle_count"] == 2 for row in rows)
    assert all(row["daily_pump_total_close_to_close_pct"] == 0.25 for row in rows)
    assert all(row["daily_pump_first_volume_ratio"] == 5.0 for row in rows)
    assert all(row["daily_pump_second_volume_ratio"] == 5.0 for row in rows)
    assert all(row["feature_cutoff_time_ms"] == daily["timestamp"][24] + 86_400_000 for row in rows)
    assert all(row["outcome_label"] == "unresolved" for row in rows)


def test_daily_anomaly_hold_continuation_outcome_is_not_a_cohort_filter() -> None:
    daily = _daily_fixture()
    _add_sleep_to_pump(daily)
    _add_compact_hold(daily, start=22, count=2)
    # This is a future outcome, not an admission condition: the cohort row must
    # still be created and the feature cutoff must remain at the hold close.
    daily["close"][24], daily["high"][24] = 135.0, 140.0

    rows = _event_rows("TESTUSDT", daily)

    assert len(rows) == 4
    assert all(row["outcome_label"] == "new_range" for row in rows)
    assert all(row["outcome_time_ms"] == daily["timestamp"][24] + 86_400_000 for row in rows)
    assert all(row["daily_hold_candle_count"] == 2 for row in rows)
    assert all(row["feature_cutoff_time_ms"] == daily["timestamp"][23] + 86_400_000 for row in rows)


def test_daily_anomaly_hold_requires_volume_on_both_pump_days() -> None:
    daily = _daily_fixture()
    _add_sleep_to_pump(daily)
    daily["quote_volume"][21] = 2.9
    _add_compact_hold(daily, start=22, count=1)

    assert _event_rows("TESTUSDT", daily) == []


def test_daily_anomaly_hold_rejects_a_pump_that_starts_as_a_dump() -> None:
    daily = _daily_fixture()
    # The second day is strong, but the proposed two-day event begins with a
    # negative day.  It is a bounce after a dump, not a sleep-to-pump sequence.
    daily["open"][20], daily["high"][20], daily["low"][20], daily["close"][20], daily["quote_volume"][20] = 100.0, 102.0, 80.0, 90.0, 5.0
    daily["open"][21], daily["high"][21], daily["low"][21], daily["close"][21], daily["quote_volume"][21] = 90.0, 135.0, 88.0, 125.0, 5.0
    _add_compact_hold(daily, start=22, count=1)

    assert _event_rows("TESTUSDT", daily) == []


def test_daily_anomaly_hold_does_not_slide_a_new_pump_window_into_an_active_pump() -> None:
    daily = _daily_fixture()
    # Day 20 is already a rising, anomalous-volume pump candle.  The scanner
    # must not call days 21-22 a new sleep-to-pump onset merely because they
    # form another qualifying two-day suffix.
    for idx, close, high in ((20, 110.0, 112.0), (21, 121.0, 123.0), (22, 133.0, 135.0)):
        daily["open"][idx], daily["high"][idx], daily["low"][idx], daily["close"][idx], daily["quote_volume"][idx] = close / 1.1, high, 100.0, close, 5.0
    _add_compact_hold(daily, start=23, count=1)

    assert _event_rows("TESTUSDT", daily) == []


def test_daily_anomaly_hold_rejects_a_rejection_wick_instead_of_calling_it_a_hold() -> None:
    daily = _daily_fixture()
    _add_sleep_to_pump(daily)
    daily["open"][22], daily["high"][22], daily["low"][22], daily["close"][22] = 126.0, 140.0, 118.0, 126.0

    assert _event_rows("TESTUSDT", daily) == []


def test_daily_anomaly_hold_rejects_a_fresh_directional_leg_after_the_pump() -> None:
    daily = _daily_fixture()
    _add_sleep_to_pump(daily)
    daily["open"][22], daily["high"][22], daily["low"][22], daily["close"][22] = 126.0, 155.0, 120.0, 150.0

    assert _event_rows("TESTUSDT", daily) == []


def test_daily_anomaly_hold_requires_a_bounded_retrace_of_the_pump_range() -> None:
    daily = _daily_fixture()
    _add_sleep_to_pump(daily)
    # A top close cannot compensate for a retrace deeper than half of the
    # two-day pump range; this is a failed hold rather than a compact pullback.
    daily["open"][22], daily["high"][22], daily["low"][22], daily["close"][22] = 126.0, 128.0, 110.0, 126.0

    assert _event_rows("TESTUSDT", daily) == []
