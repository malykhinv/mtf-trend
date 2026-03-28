from __future__ import annotations

from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.config import HourlyAsiaPumpTradeModel
from strategy.hourly_asia_pump.research import (
    _find_trade_model_entry,
    _simulate_trade_model_from_entry,
    build_hourly_asia_pump_research_artifacts,
)


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


def _aggregate_ohlcv(frame: pd.DataFrame, freq: str) -> pd.DataFrame:
    indexed = frame.copy()
    indexed["datetime_utc"] = pd.to_datetime(indexed["timestamp"], unit="ms", utc=True)
    aggregated = (
        indexed.set_index("datetime_utc")
        .resample(freq, label="left", closed="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna()
        .reset_index()
    )
    aggregated["timestamp"] = (aggregated["datetime_utc"].astype("int64") // 1_000_000).astype("int64")
    return aggregated[["timestamp", "open", "high", "low", "close", "volume"]]


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
    trade_portfolio_component_events = pd.read_csv(artifacts["trade_portfolio_component_events"])
    trade_portfolio_events = pd.read_csv(artifacts["trade_portfolio_events"])
    trade_portfolio_summary = pd.read_csv(artifacts["trade_portfolio_summary"])
    trade_portfolio_monthly = pd.read_csv(artifacts["trade_portfolio_monthly"])
    trade_portfolio_walk_forward_folds = pd.read_csv(artifacts["trade_portfolio_walk_forward_folds"])
    trade_portfolio_walk_forward_summary = pd.read_csv(artifacts["trade_portfolio_walk_forward_summary"])
    trade_search_component_summary = pd.read_csv(artifacts["trade_search_component_summary"])
    trade_search_holdout_summary = pd.read_csv(artifacts["trade_search_holdout_summary"])
    trade_search_holdout_selected_events = pd.read_csv(artifacts["trade_search_holdout_selected_events"])
    trade_search_walk_forward_folds = pd.read_csv(artifacts["trade_search_walk_forward_folds"])
    trade_search_walk_forward_summary = pd.read_csv(artifacts["trade_search_walk_forward_summary"])

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
        "entry_sequence_source",
        "entry_sequence_status",
        "entry_bar_stop_ambiguous",
        "pre_entry_pullback_frac",
        "pre_entry_red_volume_frac",
        "pre_entry_low_frac_of_trigger_range",
    } <= set(trade_model_events.columns)
    assert {
        "trade_model_id",
        "trades_count",
        "profit_factor",
        "trades_per_year",
        "sequenced_entry_rate",
        "entry_bar_ambiguous_rate",
    } <= set(trade_model_summary.columns)
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
    } <= set(trade_portfolio_component_events.columns)
    assert {
        "trade_portfolio_id",
        "trade_portfolio_label",
        "portfolio_component_count",
        "portfolio_component_model_ids",
        "portfolio_has_ambiguous_entry",
    } <= set(trade_portfolio_events.columns)
    assert {
        "trade_portfolio_id",
        "trades_count",
        "mean_component_count",
        "ambiguous_entry_rate",
        "positive_months_count",
        "all_active_months_positive",
    } <= set(trade_portfolio_summary.columns)
    assert {
        "single_context_uq_65_no3",
        "single_context_close_65",
        "single_context_uq_65_tight_mid",
        "single_monster_break_5pct_h1",
        "stacked_context_core_65",
        "stacked_context_all_positive_v1",
    } <= set(trade_portfolio_summary["trade_portfolio_id"].dropna().astype(str))
    assert {"trade_portfolio_id", "month_utc", "total_return_pct", "month_positive"} <= set(trade_portfolio_monthly.columns)
    assert {
        "timeframe",
        "folds_count",
        "oos_trades_count",
        "oos_mean_return_pct",
        "all_active_test_months_positive",
    } <= set(trade_portfolio_walk_forward_summary.columns)
    assert {
        "timeframe",
        "walk_forward_fold",
        "test_month_utc",
        "selected_trade_portfolio_id",
        "test_trades_count",
    } <= set(trade_portfolio_walk_forward_folds.columns)
    assert {
        "search_component_id",
        "trade_model_id",
        "hour_utc",
        "mean_return_pct",
        "ambiguous_entry_rate",
    } <= set(trade_search_component_summary.columns)
    assert {
        "search_combo_id",
        "search_component_ids",
        "selected_by_train",
        "train_mean_return_pct",
        "test_mean_return_pct",
        "test_meets_full_goal",
    } <= set(trade_search_holdout_summary.columns)
    assert {"search_combo_id", "search_component_ids"} <= set(trade_search_holdout_selected_events.columns)
    assert {
        "walk_forward_fold",
        "selection_status",
        "search_combo_id",
        "test_mean_return_pct",
    } <= set(trade_search_walk_forward_folds.columns)
    assert {
        "selected_folds_count",
        "oos_trades_count",
        "oos_mean_return_pct",
        "oos_annualized_sum_return_pct",
    } <= set(trade_search_walk_forward_summary.columns)


def test_trade_model_execution_sequences_entry_bar_with_m1_data(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    frame_1m = _build_flat_frame(start="2026-01-01 12:00:00", periods=900, freq="1min")

    pump_rows = [
        (780, 100.0, 103.0, 100.0, 103.0, 40.0),
        (781, 103.0, 106.0, 103.0, 105.0, 40.0),
        (782, 105.0, 107.0, 105.0, 106.5, 40.0),
        (783, 106.5, 108.0, 106.5, 107.5, 40.0),
        (784, 107.5, 109.0, 107.5, 108.0, 40.0),
        (785, 108.0, 110.0, 99.0, 101.0, 55.0),
        (786, 101.0, 101.5, 100.0, 100.5, 15.0),
        (787, 100.5, 101.0, 100.0, 100.4, 12.0),
        (788, 100.4, 100.9, 99.8, 100.2, 11.0),
        (789, 100.2, 100.5, 99.7, 100.0, 10.0),
    ]
    for idx, open_price, high_price, low_price, close_price, volume in pump_rows:
        frame_1m.loc[idx, ["open", "high", "low", "close", "volume"]] = [
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
        ]

    frame_5m = _aggregate_ohlcv(frame_1m, "5min")
    _write_symbol_cache(cache_dir, "AAA/USDT", Timeframe.M1, frame_1m)
    _write_symbol_cache(cache_dir, "AAA/USDT", Timeframe.M5, frame_5m)

    output_dir = tmp_path / "research_m5"
    artifacts = build_hourly_asia_pump_research_artifacts(
        cache_dir=cache_dir,
        output_dir=output_dir,
        timeframes=[Timeframe.M5],
        asia_start_hour_utc=0,
        asia_end_hour_utc=9,
        trigger_minute=0,
        max_follow_minutes=120,
        selection_profile="balanced",
    )

    trade_model_events = pd.read_csv(artifacts["trade_model_events"])
    portfolio_events = pd.read_csv(artifacts["trade_portfolio_events"])

    sequenced_trade = trade_model_events[
        (trade_model_events["trade_model_id"].astype(str) == "monster_break_5pct")
        & (trade_model_events["trade_triggered"].astype(str).str.lower() == "true")
    ].iloc[0]
    assert sequenced_trade["entry_sequence_source"] == "m1"
    assert sequenced_trade["entry_sequence_status"] == "ambiguous_same_minute_stop"
    assert bool(sequenced_trade["entry_bar_stop_hit"]) is True
    assert bool(sequenced_trade["entry_bar_stop_ambiguous"]) is True
    assert sequenced_trade["exit_reason"] == "entry_bar_stop"

    portfolio_trade = portfolio_events[
        portfolio_events["trade_portfolio_id"].astype(str) == "single_monster_break_5pct_h1"
    ].iloc[0]
    assert int(portfolio_trade["portfolio_component_count"]) == 1
    assert bool(portfolio_trade["portfolio_has_ambiguous_entry"]) is True


def test_next_bar_open_trade_model_trails_under_last_red_after_new_high() -> None:
    model = HourlyAsiaPumpTradeModel(
        model_id="next_open_red_trail",
        label="Next Open Red Trail",
        entry_style="next_bar_open",
        trail_style="last_red_low",
        initial_stop_style="trigger_low",
        min_trigger_return_pct=0.0,
        min_range_atr=0.0,
        min_body_atr=0.0,
        min_volume_mult=0.0,
        max_close_to_high_frac=1.0,
        max_entry_bars=1,
        max_pullback_frac=0.0,
        pullback_volume_frac=1.0,
        flag_bars=0,
        flag_max_range_frac=0.0,
        partial_take_pct=0.0,
        partial_take_r=0.0,
        partial_fraction=0.0,
        move_stop_to_be_after_partial=False,
        trail_activation_pct=0.0,
        fast_fail_bars=0,
        fast_fail_min_return_pct=0.0,
        max_hold_minutes=60,
    )
    open_values = pd.Series([100.0, 109.0, 106.0, 112.0, 106.0], dtype="float64").to_numpy()
    high_values = pd.Series([110.0, 111.0, 113.0, 112.5, 106.5], dtype="float64").to_numpy()
    low_values = pd.Series([99.0, 105.0, 104.0, 104.5, 105.0], dtype="float64").to_numpy()
    close_values = pd.Series([109.0, 106.0, 112.0, 105.2, 105.8], dtype="float64").to_numpy()
    volume_values = pd.Series([100.0, 80.0, 75.0, 45.0, 30.0], dtype="float64").to_numpy()
    timestamp_values = pd.Series(
        [
            1_704_067_200_000,
            1_704_067_500_000,
            1_704_067_800_000,
            1_704_068_100_000,
            1_704_068_400_000,
        ],
        dtype="int64",
    ).to_numpy()
    event = {
        "trigger_return_pct": 0.09,
        "range_atr": 3.0,
        "body_atr": 2.0,
        "volume_mult": 4.0,
        "close_to_high_frac": 0.10,
    }

    entry = _find_trade_model_entry(
        model=model,
        event=event,
        open_values=open_values,
        high_values=high_values,
        low_values=low_values,
        close_values=close_values,
        volume_values=volume_values,
        timestamp_values=timestamp_values,
        row_index=0,
    )

    assert bool(entry["trade_triggered"]) is True
    assert entry["entry_idx"] == 1
    assert float(entry["entry_price"]) == 109.0
    assert str(entry["entry_execution_mode"]) == "bar_open"
    assert float(entry["initial_stop_price"]) == 99.0
    assert round(float(entry["next_bar_pullback_frac"]), 8) == round(5.0 / 11.0, 8)

    trade = _simulate_trade_model_from_entry(
        model=model,
        timeframe=Timeframe.M5,
        open_values=open_values,
        high_values=high_values,
        low_values=low_values,
        close_values=close_values,
        timestamp_values=timestamp_values,
        row_index=0,
        entry_idx=int(entry["entry_idx"]),
        entry_price=float(entry["entry_price"]),
        initial_stop_price=float(entry["initial_stop_price"]),
        entry_execution_mode=str(entry["entry_execution_mode"]),
        commission_rate=0.0004,
    )

    expected_return_pct = -0.0004 + (((105.0 / 109.0) - 1.0) - 0.0004)
    assert trade["entry_sequence_status"] == "confirmed_bar_open_native"
    assert bool(trade["entry_bar_stop_hit"]) is False
    assert trade["exit_reason"] == "stop"
    assert round(float(trade["exit_return_pct"]), 8) == round(expected_return_pct, 8)
