from __future__ import annotations

from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.research import build_hourly_asia_pump_research_artifacts


def _write_symbol_cache(base_dir: Path, symbol: str, timeframe: Timeframe, frame: pd.DataFrame) -> None:
    symbol_dir = symbol.replace("/", "%2F")
    path = base_dir / symbol_dir / timeframe.value
    path.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path / "data.parquet", index=False)


def _build_flat_frame(*, start: str, periods: int, freq: str) -> pd.DataFrame:
    timestamps = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    frame = pd.DataFrame(
        {
            "timestamp": (timestamps.view("int64") // 1_000_000).astype("int64"),
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 100.0,
            "volume": 10.0,
        }
    )
    return frame


def test_build_hourly_asia_pump_research_artifacts_writes_expected_outputs(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    frame = _build_flat_frame(start="2026-01-01 12:00:00", periods=900, freq="1min")

    pump_idx = 780
    frame.loc[pump_idx, ["open", "high", "low", "close", "volume"]] = [100.0, 109.0, 99.9, 108.0, 120.0]
    frame.loc[pump_idx + 1, ["open", "high", "low", "close", "volume"]] = [108.0, 111.0, 107.5, 110.2, 80.0]
    frame.loc[pump_idx + 2, ["open", "high", "low", "close", "volume"]] = [110.2, 113.4, 109.8, 112.6, 75.0]
    frame.loc[pump_idx + 3, ["open", "high", "low", "close", "volume"]] = [112.6, 112.8, 110.9, 111.5, 40.0]
    frame.loc[pump_idx + 4, ["open", "high", "low", "close", "volume"]] = [111.5, 111.8, 107.4, 108.2, 35.0]

    _write_symbol_cache(cache_dir, "AAA/USDT", Timeframe.M1, frame)

    output_dir = tmp_path / "research"
    artifacts = build_hourly_asia_pump_research_artifacts(
        cache_dir=cache_dir,
        output_dir=output_dir,
        timeframes=[Timeframe.M1],
        asia_start_hour_utc=0,
        asia_end_hour_utc=9,
        trigger_minute=0,
        max_follow_minutes=120,
        selection_profile="balanced",
    )

    selected_events = pd.read_csv(artifacts["selected_profile_events"])
    profile_summary = pd.read_csv(artifacts["profile_summary"])
    common_patterns = pd.read_csv(artifacts["common_patterns"])
    early_entry_events = pd.read_csv(artifacts["early_entry_events"])
    early_entry_summary = pd.read_csv(artifacts["early_entry_summary"])
    trade_model_events = pd.read_csv(artifacts["trade_model_events"])
    trade_model_summary = pd.read_csv(artifacts["trade_model_summary"])
    trade_portfolio_events = pd.read_csv(artifacts["trade_portfolio_events"])
    trade_portfolio_summary = pd.read_csv(artifacts["trade_portfolio_summary"])
    trade_portfolio_monthly = pd.read_csv(artifacts["trade_portfolio_monthly"])

    assert artifacts["report"].exists()
    assert len(selected_events) == 1
    assert selected_events.iloc[0]["symbol"] == "AAA/USDT"
    assert selected_events.iloc[0]["selection_profile"] == "balanced"
    assert {
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "pre_base_range_vs_trigger",
    } <= set(selected_events.columns)
    assert "balanced" in set(profile_summary["profile_id"].dropna().astype(str))
    assert int(profile_summary.loc[profile_summary["profile_id"] == "balanced", "events_count"].iloc[0]) == 1
    assert int(common_patterns.iloc[0]["events_count"]) == 1
    assert len(early_entry_events) == 1
    assert str(early_entry_events.iloc[0]["entry_triggered"]).lower() == "true"
    assert float(early_entry_events.iloc[0]["entry_delay_minutes"]) == 1.0
    assert "target_5pct_hit_rate" in set(early_entry_summary.columns)
    assert int(early_entry_summary.iloc[0]["entries_triggered_count"]) == 1
    assert {
        "trade_model_id",
        "trade_triggered",
        "exit_reason",
        "pre_entry_pullback_frac",
        "pre_entry_red_volume_frac",
        "pre_entry_low_frac_of_trigger_range",
    } <= set(trade_model_events.columns)
    assert {"trade_model_id", "trades_count", "profit_factor", "trades_per_year"} <= set(trade_model_summary.columns)
    assert int(trade_model_summary["trades_count"].max()) >= 1
    assert {
        "context_monster_5pct",
        "context_monster_5pct_tight",
        "context_break_65_uq_tight",
        "context_break_65_uq_tight_mid",
        "context_break_65_close_tight",
        "context_monster_7p5pct",
        "context_monster_7p5pct_mid",
        "context_monster_7p5pct_tight",
    } <= set(trade_model_summary["trade_model_id"].dropna().astype(str))
    assert {
        "trade_portfolio_id",
        "portfolio_component_model_id",
        "trade_portfolio_label",
    } <= set(trade_portfolio_events.columns)
    assert {
        "trade_portfolio_id",
        "trades_count",
        "positive_months_count",
        "all_active_months_positive",
    } <= set(trade_portfolio_summary.columns)
    assert {
        "stacked_context_core_65",
        "stacked_context_all_positive_v1",
    } <= set(trade_portfolio_summary["trade_portfolio_id"].dropna().astype(str))
    assert {"trade_portfolio_id", "month_utc", "total_return_pct", "month_positive"} <= set(trade_portfolio_monthly.columns)
