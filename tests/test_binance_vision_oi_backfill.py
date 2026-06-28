from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from anomaly_science.binance_vision_oi_backfill import (
    _console_safe_symbol,
    merge_causal_open_interest,
    metrics_archive_url,
)


def test_metrics_archive_url_uses_daily_usd_m_contract() -> None:
    assert metrics_archive_url(symbol="btcusdt", day=date(2025, 1, 2)) == (
        "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/"
        "BTCUSDT-metrics-2025-01-02.zip"
    )


def test_console_symbol_is_safe_for_windows_legacy_encoding() -> None:
    rendered = _console_safe_symbol("币安人生USDT")

    rendered.encode("cp1252")
    assert rendered.startswith("\\u")


def test_merge_causal_open_interest_never_uses_future_or_previous_day_sample() -> None:
    def ts(value: str) -> int:
        return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)

    candles = pl.DataFrame(
        {
            "timestamp": [
                ts("2024-12-31T23:50:00"),  # Future sample must not be used.
                ts("2024-12-31T23:51:00"),
                ts("2024-12-31T23:53:00"),
                ts("2024-12-31T23:58:00"),  # Sample is too stale.
                ts("2025-01-01T00:02:00"),  # No cross-day carry.
                ts("2025-01-01T00:05:00"),  # Exact sample is causal.
            ]
        }
    )
    metrics = pl.DataFrame(
        {
            "timestamp": [ts("2024-12-31T23:51:00"), ts("2025-01-01T00:05:00")],
            "open_interest": [100.0, 200.0],
        }
    )

    result = merge_causal_open_interest(candles=candles, metrics=metrics, max_staleness_minutes=5)

    assert result["open_interest"].to_list() == [None, 100.0, 100.0, None, None, 200.0]
    assert result["oi_available"].to_list() == [False, True, True, False, False, True]
    assert result["missing_oi_flag"].to_list() == [True, False, False, True, True, False]


def test_empty_metrics_explicitly_marks_oi_missing() -> None:
    candles = pl.DataFrame(
        {
            "timestamp": [1],
            "open_interest": [123.0],
            "oi_available": [True],
            "missing_oi_flag": [False],
        }
    )
    metrics = pl.DataFrame(schema={"timestamp": pl.Int64, "open_interest": pl.Float64})

    result = merge_causal_open_interest(candles=candles, metrics=metrics, max_staleness_minutes=10)

    assert result["open_interest"].to_list() == [None]
    assert result["oi_available"].to_list() == [False]
    assert result["missing_oi_flag"].to_list() == [True]
