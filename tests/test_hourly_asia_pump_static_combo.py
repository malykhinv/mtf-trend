from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.static_combo import (
    _build_priority_selected_events,
    _index_to_variant_label,
    build_hourly_asia_pump_static_combo_artifacts,
)


def _build_empty_base_events_csv(path: Path) -> None:
    pd.DataFrame(
        columns=[
            "symbol",
            "timestamp_ms",
            "entry_timestamp_ms",
            "entry_timestamp_utc",
            "date_utc",
            "month_utc",
            "hour_utc",
            "trade_model_id",
            "pre_entry_pullback_frac",
            "trigger_return_pct",
            "exit_return_pct",
            "trade_triggered",
        ]
    ).to_csv(path, index=False)


def _build_confirmed_events_csv(path: Path) -> None:
    rows: list[dict[str, object]] = []
    for month in range(1, 11):
        for day_offset in range(6):
            timestamp = pd.Timestamp(year=2025, month=month, day=1 + day_offset * 4, hour=0, minute=5, tz="UTC")
            entry_timestamp = timestamp + pd.Timedelta(minutes=5)
            rows.append(
                {
                    "symbol": f"AAA{month:02d}{day_offset}/USDT",
                    "timeframe": "5m",
                    "row_index": len(rows),
                    "timestamp_ms": int(timestamp.timestamp() * 1000),
                    "timestamp_utc": timestamp.isoformat(),
                    "date_utc": entry_timestamp.strftime("%Y-%m-%d"),
                    "hour_utc": 0,
                    "trigger_open": 100.0,
                    "trigger_high": 108.0,
                    "trigger_low": 100.0,
                    "trigger_close": 108.0,
                    "trigger_volume": 1000.0,
                    "trigger_return_pct": 0.08 + (day_offset * 0.001),
                    "trigger_range_pct": 0.085,
                    "range_atr": 3.5,
                    "body_atr": 2.6,
                    "volume_mult": 4.8,
                    "close_to_high_frac": 0.05,
                    "breakout_pct": 0.0,
                    "pre_base_range_pct_60m": 0.04,
                    "pre_base_drift_pct_60m": 0.01,
                    "pre_base_range_vs_trigger": 0.5,
                    "atr_window_minutes": 60,
                    "volume_window_minutes": 60,
                    "breakout_lookback_minutes": 60,
                    "max_follow_minutes": 720,
                    "peak_price_before_50pct_retrace": 114.0,
                    "peak_timestamp_ms": int((entry_timestamp + pd.Timedelta(minutes=15)).timestamp() * 1000),
                    "peak_timestamp_utc": (entry_timestamp + pd.Timedelta(minutes=15)).isoformat(),
                    "peak_return_pct": 0.06,
                    "continuation_peak_return_pct": 0.04,
                    "bars_to_peak": 3,
                    "minutes_to_peak": 15,
                    "retraced_50pct_within_window": True,
                    "retrace_50_timestamp_ms": int((entry_timestamp + pd.Timedelta(minutes=30)).timestamp() * 1000),
                    "retrace_50_timestamp_utc": (entry_timestamp + pd.Timedelta(minutes=30)).isoformat(),
                    "bars_to_50pct_retrace": 6,
                    "minutes_to_50pct_retrace": 30,
                    "max_return_next_5m_pct": 0.02,
                    "close_return_5m_pct": 0.01,
                    "survived_5m": True,
                    "max_return_next_15m_pct": 0.04,
                    "close_return_15m_pct": 0.02,
                    "survived_15m": True,
                    "max_return_next_30m_pct": 0.05,
                    "close_return_30m_pct": 0.03,
                    "survived_30m": True,
                    "max_return_next_60m_pct": 0.07,
                    "close_return_60m_pct": 0.04,
                    "survived_60m": True,
                    "grid_id": "grid",
                    "profile_id": "balanced",
                    "selection_min_range_atr": 1.0,
                    "selection_min_body_atr": 1.0,
                    "selection_min_volume_mult": 1.0,
                    "selection_max_close_to_high_frac": 0.5,
                    "selection_min_breakout_pct": 0.0,
                    "selection_profile": "balanced",
                    "config_id": "confirmed",
                    "min_trigger_return_pct": 0.065,
                    "max_next_pullback_frac": 0.25,
                    "require_next_green": True,
                    "stop_style": "next_low",
                    "next_bar_open_price": 108.0,
                    "next_bar_high_price": 110.0,
                    "next_bar_low_price": 107.4,
                    "next_bar_close_price": 109.6,
                    "next_bar_is_green": True,
                    "next_bar_return_pct": 0.0148,
                    "next_bar_pullback_frac": 0.23,
                    "next_bar_low_frac_of_trigger_range": 0.075,
                    "trade_triggered": True,
                    "entry_timestamp_ms": int(entry_timestamp.timestamp() * 1000),
                    "entry_timestamp_utc": entry_timestamp.isoformat(),
                    "entry_delay_bars": 1,
                    "entry_delay_minutes": 5,
                    "entry_sequence_source": "native",
                    "entry_sequence_status": "confirmed",
                    "entry_sequence_micro_bars": 0,
                    "entry_bar_stop_hit": False,
                    "entry_bar_stop_ambiguous": False,
                    "initial_risk_pct": 0.03,
                    "exit_reason": "trail",
                    "exit_timestamp_ms": int((entry_timestamp + pd.Timedelta(minutes=45)).timestamp() * 1000),
                    "exit_timestamp_utc": (entry_timestamp + pd.Timedelta(minutes=45)).isoformat(),
                    "exit_return_pct": 0.03 + month * 0.0005 + day_offset * 0.0002,
                    "exit_r_multiple": 1.5,
                    "max_return_after_entry_pct": 0.06,
                    "max_r_multiple": 2.0,
                    "bars_held_after_entry": 9,
                    "minutes_held_after_entry": 45,
                    "partial_taken": False,
                    "realized_ge_5pct": False,
                    "realized_ge_10pct": False,
                    "mfe_ge_1pct": True,
                    "mfe_ge_2pct": True,
                    "mfe_ge_5pct": True,
                    "mfe_ge_10pct": False,
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_index_to_variant_label_supports_excel_like_sequence() -> None:
    assert _index_to_variant_label(0) == "A"
    assert _index_to_variant_label(25) == "Z"
    assert _index_to_variant_label(26) == "AA"
    assert _index_to_variant_label(27) == "AB"


def test_build_priority_selected_events_picks_best_combo_for_same_signal() -> None:
    combo_events = pd.DataFrame(
        [
            {
                "combo_id": "combo_a",
                "symbol": "AAA/USDT",
                "timestamp_ms": 1_700_000_000_000,
                "entry_timestamp_ms": 1_700_000_300_000,
                "entry_timestamp_utc": "2023-11-14T22:18:20+00:00",
                "date_utc": "2023-11-14",
                "month_utc": "2023-11",
                "hour_utc": 0,
                "exit_return_pct": 0.04,
            },
            {
                "combo_id": "combo_b",
                "symbol": "AAA/USDT",
                "timestamp_ms": 1_700_000_000_000,
                "entry_timestamp_ms": 1_700_000_300_000,
                "entry_timestamp_utc": "2023-11-14T22:18:20+00:00",
                "date_utc": "2023-11-14",
                "month_utc": "2023-11",
                "hour_utc": 0,
                "exit_return_pct": 0.02,
            },
        ]
    )
    combo_catalog = pd.DataFrame(
        [
            {"combo_id": "combo_a", "combo_variant": "A", "combo_priority": 1, "priority_score": 9.0},
            {"combo_id": "combo_b", "combo_variant": "B", "combo_priority": 2, "priority_score": 8.0},
        ]
    )

    selected = _build_priority_selected_events(combo_events=combo_events, combo_catalog=combo_catalog)

    assert len(selected) == 1
    assert selected.iloc[0]["combo_id"] == "combo_a"
    assert selected.iloc[0]["combo_variant"] == "A"
    assert int(selected.iloc[0]["matched_combo_count"]) == 2
    assert selected.iloc[0]["matched_combo_variants"] == "A,B"


def test_build_hourly_asia_pump_static_combo_artifacts_builds_great_combo_catalog(tmp_path: Path) -> None:
    base_events_path = tmp_path / "base_events.csv"
    confirmed_events_path = tmp_path / "confirmed_events.csv"
    output_dir = tmp_path / "static_combo"

    _build_empty_base_events_csv(base_events_path)
    _build_confirmed_events_csv(confirmed_events_path)

    artifacts = build_hourly_asia_pump_static_combo_artifacts(
        base_events_path=base_events_path,
        confirmed_events_path=confirmed_events_path,
        output_dir=output_dir,
    )

    great_catalog = pd.read_csv(artifacts["great_combo_catalog"])
    priority_events = pd.read_csv(artifacts["priority_selected_events"])
    priority_summary = pd.read_csv(artifacts["priority_selected_summary"])
    holdout_sanity = pd.read_csv(artifacts["great_combo_holdout_sanity"])
    priority_topn_summary = pd.read_csv(artifacts["priority_topn_summary"])
    combo_robustness_summary = pd.read_csv(artifacts["combo_robustness_summary"])
    recommended_shortlist = pd.read_csv(artifacts["recommended_combo_shortlist"])
    priority_monthly = pd.read_csv(artifacts["priority_selected_monthly"])
    trade_chart_manifest = pd.read_csv(artifacts["priority_trade_chart_manifest"])

    assert artifacts["static_combo_context"].exists()
    assert artifacts["report"].exists()
    assert artifacts["charts_manifest"].exists()
    assert artifacts["priority_trade_chart_manifest"].exists()
    assert len(great_catalog) == 1
    assert great_catalog.iloc[0]["combo_variant"] == "A"
    assert int(great_catalog.iloc[0]["combo_priority"]) == 1
    assert float(great_catalog.iloc[0]["trades_per_year"]) >= 50.0
    assert float(great_catalog.iloc[0]["mean_return_pct"]) >= 0.02
    assert float(great_catalog.iloc[0]["win_rate"]) > 0.40
    assert int(great_catalog.iloc[0]["positive_months_count"]) >= 9
    assert len(priority_events) == 60
    assert int(priority_summary.iloc[0]["matched_combo_variants_count"]) == 1
    assert float(priority_summary.iloc[0]["mean_return_pct"]) >= 0.02
    assert set(holdout_sanity["split_id"].astype(str)) == {"train8_test4", "train9_test3"}
    assert set(priority_topn_summary["top_n_variants"].astype(int)) == {1}
    assert len(combo_robustness_summary) == 1
    assert combo_robustness_summary.iloc[0]["combo_variant"] == "A"
    assert len(recommended_shortlist) == 1
    assert set(priority_monthly.columns) >= {"month_utc", "total_return_pct", "trades_count", "month_positive"}
    assert trade_chart_manifest.empty
