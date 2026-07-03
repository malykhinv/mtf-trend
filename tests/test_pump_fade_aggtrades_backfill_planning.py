from __future__ import annotations

import pandas as pd

import anomaly_science.strategy.pump_fade.aggtrades_backfill as aggtrades_backfill
from anomaly_science.strategy.pump_fade.aggtrades_backfill import (
    AggTradesBackfillConfig,
    _console_safe,
    _format_duration,
    _format_eta,
    required_symbol_days,
)


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


def test_format_duration_uses_the_coarsest_useful_unit() -> None:
    assert _format_duration(45) == "45s"
    assert _format_duration(125) == "2m05s"
    assert _format_duration(3725) == "1h02m"


def test_format_eta_projects_remaining_time_from_observed_rate() -> None:
    # 10 days done in 100s => 0.1 days/s; 10 days remain => 100s more.
    eta = _format_eta(days_done=10, total_days=20, elapsed_seconds=100.0)
    assert eta == _format_duration(100.0)


def test_format_eta_is_unknown_before_any_progress_and_zero_when_finished() -> None:
    assert _format_eta(days_done=0, total_days=20, elapsed_seconds=5.0) == "unknown"
    assert _format_eta(days_done=20, total_days=20, elapsed_seconds=5.0) == "0s"


def test_console_safe_survives_cjk_ticker_under_cp1252() -> None:
    # Real meme perpetuals carry CJK tickers (e.g. lobster-USDT); progress lines
    # must stay printable when stdout is a cp1252-encoded redirected file.
    safe = _console_safe("aggTrades backfill [693/693] 龙虾USDT - done")
    assert safe.isascii()
    # Encoding under the Windows locale codec must not raise anymore.
    safe.encode("cp1252")


def test_download_one_pauses_through_a_network_outage_instead_of_failing(monkeypatch) -> None:
    calls = {"attempts": 0}

    def flaky_download(url, request_config, *, session=None):
        calls["attempts"] += 1
        if calls["attempts"] < 3:
            raise RuntimeError("simulated network outage")
        return b"payload"

    sleeps: list[float] = []
    monkeypatch.setattr(aggtrades_backfill, "download_optional_bytes", flaky_download)
    monkeypatch.setattr(aggtrades_backfill.time, "sleep", lambda seconds: sleeps.append(seconds))

    pool = aggtrades_backfill._AggTradesDownloadPool(
        AggTradesBackfillConfig(network_pause_seconds=1.0, network_pause_max_seconds=4.0)
    )
    try:
        result = pool._download_one("https://example.invalid/day.zip")
    finally:
        pool.__exit__(None, None, None)

    assert result == b"payload"
    assert calls["attempts"] == 3
    # Two outages before success; the pause doubles and is capped by network_pause_max_seconds.
    assert sleeps == [1.0, 2.0]
