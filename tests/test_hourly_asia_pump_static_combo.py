from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.static_combo import (
    _build_combo_events,
    _build_priority_selected_events,
    _index_to_variant_label,
    build_hourly_asia_pump_static_combo_artifacts,
)
from strategy.hourly_asia_pump.static_combo_trade_plotter import (
    StaticComboTradePlotSpec,
    StaticComboTradePlotter,
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


def test_static_combo_trade_plotter_renders_bee_bite_style_chart(tmp_path: Path) -> None:
    timestamps = pd.date_range("2025-09-22 02:00:00+00:00", periods=28, freq="5min")
    frame = pd.DataFrame(
        {
            "timestamp": (timestamps.view("int64") // 1_000_000).astype("int64"),
            "open": [
                100.0, 100.1, 100.0, 99.9, 100.0, 100.1, 100.0, 100.1, 100.0, 100.2,
                100.1, 100.2, 100.3, 100.4, 102.8, 103.2, 104.0, 105.1, 104.9, 105.7,
                106.5, 107.2, 106.8, 107.9, 108.3, 108.0, 107.4, 107.8,
            ],
            "high": [
                100.2, 100.2, 100.1, 100.1, 100.2, 100.2, 100.1, 100.2, 100.2, 100.3,
                100.3, 100.4, 100.5, 103.4, 103.6, 104.4, 105.6, 105.8, 106.1, 106.9,
                107.4, 107.6, 108.4, 108.7, 108.6, 108.2, 108.0, 108.1,
            ],
            "low": [
                99.9, 99.9, 99.8, 99.8, 99.9, 100.0, 99.9, 100.0, 99.9, 100.0,
                100.0, 100.1, 100.2, 100.3, 102.6, 103.0, 103.7, 104.6, 104.7, 105.2,
                106.0, 106.7, 106.5, 107.7, 107.8, 107.2, 107.1, 107.5,
            ],
            "close": [
                100.1, 100.0, 99.9, 100.0, 100.1, 100.0, 100.1, 100.0, 100.2, 100.1,
                100.2, 100.3, 100.4, 103.2, 103.4, 104.1, 105.2, 104.9, 105.8, 106.6,
                107.1, 106.9, 108.1, 108.4, 108.1, 107.5, 107.7, 107.9,
            ],
            "volume": [
                1000, 950, 980, 930, 960, 970, 990, 985, 1005, 1010,
                995, 1008, 1020, 5200, 4700, 4300, 4100, 3600, 3400, 3200,
                3000, 2850, 2700, 2550, 2400, 2300, 2200, 2100,
            ],
        }
    )
    trigger_timestamp_ms = int(frame.iloc[13]["timestamp"])
    entry_timestamp_ms = int(frame.iloc[14]["timestamp"])
    exit_timestamp_ms = int(frame.iloc[24]["timestamp"])
    output_path = tmp_path / "trade.png"

    plotter = StaticComboTradePlotter()
    plotter.plot_trade(
        frame=frame,
        spec=StaticComboTradePlotSpec(
            symbol="TEST/USDT",
            combo_variant="A",
            component_ids="mb5_01_shallow,tf7_raw",
            trigger_timestamp_ms=trigger_timestamp_ms,
            entry_timestamp_ms=entry_timestamp_ms,
            exit_timestamp_ms=exit_timestamp_ms,
            entry_price=103.2,
            stop_price=101.6,
            exit_price=108.1,
            trigger_open=100.4,
            trigger_high=103.4,
            trigger_low=100.3,
            trigger_close=103.2,
            trigger_return_pct=0.028,
            trigger_range_pct=0.031,
            range_atr=3.4,
            body_atr=2.2,
            volume_mult=5.6,
            close_to_high_frac=0.06,
            pre_base_range_pct_60m=0.012,
            pre_base_drift_pct_60m=0.002,
            pre_base_range_vs_trigger=0.41,
            pre_entry_pullback_frac=0.18,
            pre_entry_red_volume_frac=0.27,
            initial_risk_pct=0.0155,
            peak_timestamp_ms=int(frame.iloc[23]["timestamp"]),
            peak_price=108.7,
            exit_return_pct=0.047,
            exit_reason="trail_stop",
            entry_reason="break_high",
            initial_stop_reason="trigger_low",
            source_trade_model_id="monster_break_5pct",
            source_trade_model_label="Monster Break 5%",
            source_config_id="balanced",
            hour_utc=3,
        ),
        output_path=output_path,
    )

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_build_combo_events_uses_conservative_min_return_for_overlaps() -> None:
    candidate_frames = {
        "alpha": pd.DataFrame(
            [
                {
                    "symbol": "AAA/USDT",
                    "timestamp_ms": 1_700_000_000_000,
                    "entry_timestamp_ms": 1_700_000_300_000,
                    "entry_timestamp_utc": "2023-11-14T22:18:20+00:00",
                    "date_utc": "2023-11-14",
                    "month_utc": "2023-11",
                    "hour_utc": 0,
                    "exit_return_pct": 0.10,
                    "trigger_return_pct": 0.04,
                    "volume_mult": 3.0,
                    "range_atr": 2.0,
                    "entry_price": 100.0,
                    "trade_model_id": "m1",
                    "config_id": "c1",
                }
            ]
        ),
        "beta": pd.DataFrame(
            [
                {
                    "symbol": "AAA/USDT",
                    "timestamp_ms": 1_700_000_000_000,
                    "entry_timestamp_ms": 1_700_000_300_000,
                    "entry_timestamp_utc": "2023-11-14T22:18:20+00:00",
                    "date_utc": "2023-11-14",
                    "month_utc": "2023-11",
                    "hour_utc": 0,
                    "exit_return_pct": -0.02,
                    "trigger_return_pct": 0.04,
                    "volume_mult": 3.0,
                    "range_atr": 2.0,
                    "entry_price": 100.0,
                    "trade_model_id": "m2",
                    "config_id": "c2",
                }
            ]
        ),
    }

    combo_events = _build_combo_events(
        candidate_frames=candidate_frames,
        component_ids=["alpha", "beta"],
        top_k_per_timestamp=99,
    )

    assert len(combo_events) == 1
    assert float(combo_events.iloc[0]["exit_return_pct"]) == -0.02
    assert str(combo_events.iloc[0]["combo_return_resolution"]) == "conservative_min"
    assert float(combo_events.iloc[0]["combo_matched_return_min_pct"]) == -0.02
    assert float(combo_events.iloc[0]["combo_matched_return_mean_pct"]) == 0.04
    assert float(combo_events.iloc[0]["combo_matched_return_max_pct"]) == 0.10
