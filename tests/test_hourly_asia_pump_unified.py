from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.unified import build_hourly_asia_pump_unified_artifacts


def _build_confirmed_events_csv(path: Path) -> None:
    rows: list[dict[str, object]] = []
    row_index = 0
    for month in range(1, 11):
        for day_offset in range(5):
            timestamp = pd.Timestamp(year=2025, month=month, day=1 + day_offset * 5, hour=0, minute=0, tz="UTC")
            entry_timestamp = timestamp + pd.Timedelta(minutes=10)
            is_negative = month == 10 and day_offset >= 3
            rows.append(
                {
                    "symbol": f"AAA{month:02d}{day_offset}/USDT",
                    "timeframe": "5m",
                    "row_index": row_index,
                    "timestamp_ms": int(timestamp.timestamp() * 1000),
                    "timestamp_utc": timestamp.isoformat(),
                    "hour_utc": 0,
                    "trigger_high": 108.0,
                    "trigger_low": 100.0,
                    "trigger_return_pct": 0.072,
                    "range_atr": 9.2,
                    "body_atr": 7.4,
                    "volume_mult": 16.0,
                    "close_to_high_frac": 0.10,
                    "pre_base_range_pct_60m": 0.05,
                    "pre_base_drift_pct_60m": 0.02,
                    "pre_base_range_vs_trigger": 0.70,
                    "config_id": "synthetic_trigger_low",
                    "stop_style": "trigger_low",
                    "next_bar_high_price": 109.0,
                    "next_bar_low_price": 106.5,
                    "next_bar_close_price": 108.4,
                    "next_bar_is_green": True,
                    "next_bar_pullback_frac": 0.22,
                    "next_bar_low_frac_of_trigger_range": 0.8125,
                    "trade_triggered": True,
                    "entry_timestamp_ms": int(entry_timestamp.timestamp() * 1000),
                    "entry_timestamp_utc": entry_timestamp.isoformat(),
                    "initial_risk_pct": 0.07,
                    "exit_timestamp_ms": int((entry_timestamp + pd.Timedelta(minutes=50)).timestamp() * 1000),
                    "exit_timestamp_utc": (entry_timestamp + pd.Timedelta(minutes=50)).isoformat(),
                    "exit_return_pct": -0.012 if is_negative else 0.031,
                }
            )
            row_index += 1

    pd.DataFrame(rows).to_csv(path, index=False)


def test_build_hourly_asia_pump_unified_artifacts_finds_unified_config(tmp_path: Path) -> None:
    confirmed_events_path = tmp_path / "confirmed_trade_events.csv"
    output_dir = tmp_path / "unified_report"
    _build_confirmed_events_csv(confirmed_events_path)

    artifacts = build_hourly_asia_pump_unified_artifacts(
        confirmed_events_path=confirmed_events_path,
        output_dir=output_dir,
    )

    assert artifacts["goal_passed"] is True
    best_summary = pd.read_csv(artifacts["best_summary"])
    assert len(best_summary) == 1
    assert best_summary.iloc[0]["stop_style"] == "trigger_low"
    assert float(best_summary.iloc[0]["trades_per_year"]) >= 50.0
    assert float(best_summary.iloc[0]["mean_return_pct"]) >= 0.02
    assert float(best_summary.iloc[0]["win_rate"]) >= 0.40
    assert float(best_summary.iloc[0]["max_drawdown_pct"]) <= 0.30
    assert int(best_summary.iloc[0]["positive_months_count"]) >= 9

    context = json.loads(Path(artifacts["context"]).read_text(encoding="utf-8"))
    assert context["search_scope"]["uses_hour_utc_in_optimization"] is False
    resilience = pd.read_csv(artifacts["candidate_resilience"])
    assert "resilience_score" in resilience.columns
    assert Path(artifacts["report"]).exists()
