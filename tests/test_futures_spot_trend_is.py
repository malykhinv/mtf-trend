from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.spot_trend.contracts import ResearchConfig, SpotTrendContractError, UniverseConfig
from anomaly_science.strategy.spot_trend.features import build_feature_matrix
from anomaly_science.strategy.spot_trend.futures_data import (
    LocalFuturesAggregationConfig,
    _aggregate_symbol,
    build_data_inferred_futures_master,
    canonical_futures_base,
)
from anomaly_science.strategy.spot_trend.futures_breakout import (
    BreakoutFlowConfig,
    build_breakout_flow_features,
)
from anomaly_science.strategy.spot_trend.futures_causal_crossings import build_all_causal_crossings
from anomaly_science.strategy.spot_trend.futures_event_paths import (
    EventPathConfig,
    build_causal_crossing_future_paths,
)
from anomaly_science.strategy.spot_trend.futures_features import (
    build_futures_feature_matrix,
    futures_feature_catalog,
)
from anomaly_science.strategy.spot_trend.futures_frequency import (
    SignalDensityConfig,
    build_signal_density_audit,
)
from anomaly_science.strategy.spot_trend.futures_is import FuturesISConfig
from anomaly_science.strategy.spot_trend.futures_structural_stops import (
    _RangeMaximumIndex,
    _confirmed_structural_lows,
)
from anomaly_science.strategy.spot_trend.futures_trade_profiles import _pre_entry_feature_columns
from anomaly_science.strategy.spot_trend.trend import build_trend_state
from anomaly_science.strategy.spot_trend.universe import build_point_in_time_universe


def _futures_bars(days: int = 430) -> pd.DataFrame:
    symbols = ("BTCUSDT", "ETHUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")
    dates = pd.date_range("2023-01-01", periods=days, freq="D", tz="UTC")
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for index, day in enumerate(dates):
            close = 100.0 + symbol_index * 20.0 + index * 0.05 + np.sin(index / (5.0 + symbol_index))
            quote_volume = 20_000_000.0 - symbol_index * 1_000_000.0
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "open": close * 0.999,
                    "high": close * 1.015,
                    "low": close * 0.985,
                    "close": close,
                    "base_volume": quote_volume / close,
                    "quote_volume": quote_volume,
                    "number_of_trades": 10_000.0 + index,
                    "taker_buy_quote_volume": quote_volume * (0.45 + 0.05 * np.sin(index / 9.0)),
                    "open_interest_open": 100_000.0 + index * 10.0,
                    "open_interest_close": 100_005.0 + index * 10.0,
                    "open_interest_low": 99_990.0 + index * 10.0,
                    "open_interest_high": 100_020.0 + index * 10.0,
                    "open_interest_mean": 100_003.0 + index * 10.0,
                    "open_interest_observations": 1_440,
                    "oi_available_minutes": 1_440,
                    "missing_oi_minutes": 0,
                    "minute_count": 1_440,
                    "complete_daily_bar": True,
                    "available_time_ms": int((day + pd.Timedelta(days=1)).timestamp() * 1_000),
                }
            )
    return pd.DataFrame(rows)


def test_futures_is_contract_is_exactly_calendar_2025() -> None:
    with pytest.raises(SpotTrendContractError, match="calendar year 2025"):
        FuturesISConfig(analysis_end="2026-01-01")
    with pytest.raises(SpotTrendContractError, match="calendar IS"):
        LocalFuturesAggregationConfig(is_start=date(2025, 2, 1))


def test_local_minute_aggregation_filters_before_oos(tmp_path: Path) -> None:
    source = tmp_path / "BTCUSDT.parquet"
    timestamps = pd.to_datetime(
        ["2025-12-31 23:58:00Z", "2025-12-31 23:59:00Z", "2026-01-01 00:00:00Z"],
        utc=True,
    )
    minute = pd.DataFrame(
        {
            "timestamp": timestamps.astype("int64") // 1_000_000,
            "open": [100.0, 101.0, 999.0],
            "high": [101.0, 102.0, 1_000.0],
            "low": [99.0, 100.0, 998.0],
            "close": [101.0, 102.0, 999.0],
            "volume": [1.0, 2.0, 100.0],
            "quote_volume": [100.0, 202.0, 99_900.0],
            "trade_count": [10, 20, 1_000],
            "taker_buy_quote_volume": [50.0, 100.0, 50_000.0],
            "open_interest": [1_000.0, 1_010.0, 9_999.0],
            "oi_available": [1, 1, 1],
            "missing_oi_flag": [0, 0, 0],
        }
    )
    minute.to_parquet(source, index=False)

    daily = _aggregate_symbol(source, LocalFuturesAggregationConfig(minimum_daily_minutes=2))

    assert daily["date"].tolist() == [pd.Timestamp("2025-12-31", tz="UTC")]
    assert daily.iloc[0]["close"] == 102.0
    assert daily.iloc[0]["open_interest_close"] == 1_010.0


def test_breakout_flow_uses_only_minutes_before_crossing_and_same_clock_history(tmp_path: Path) -> None:
    dates = pd.date_range("2025-01-01", periods=100, freq="D", tz="UTC")
    closes = np.full(len(dates), 100.0)
    closes[-1] = 110.0
    daily = pd.DataFrame(
        {
            "date": dates,
            "symbol": "BTCUSDT",
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "base_volume": 1_000.0,
            "quote_volume": 100_000.0,
            "number_of_trades": 1_000,
            "taker_buy_quote_volume": 50_000.0,
            "available_time_ms": (dates + pd.Timedelta(days=1)).astype("int64") // 1_000_000,
        }
    )
    trend = build_trend_state(daily, horizons=(3,))
    event_day = dates[-1]
    minute_rows = []
    for day_offset in range(31):
        day = event_day - pd.Timedelta(days=day_offset)
        for minute_offset in range(61):
            timestamp = day + pd.Timedelta(hours=11, minutes=minute_offset)
            is_event_crossing = day_offset == 0 and minute_offset == 60
            pre_event_multiplier = 2.0 if day_offset == 0 and minute_offset < 60 else 1.0
            minute_rows.append(
                {
                    "timestamp": int(timestamp.timestamp() * 1_000),
                    "close": 110.0 if is_event_crossing else 100.0,
                    "volume": pre_event_multiplier,
                    "quote_volume": 100.0 * pre_event_multiplier,
                    "trade_count": 10.0 * pre_event_multiplier,
                    "taker_buy_quote_volume": 50.0 * pre_event_multiplier,
                }
            )
    pd.DataFrame(minute_rows).sort_values("timestamp").to_parquet(tmp_path / "BTCUSDT.parquet", index=False)

    events = build_breakout_flow_features(
        daily,
        trend,
        (3,),
        {"BTCUSDT"},
        config=BreakoutFlowConfig(source_dir=tmp_path),
    )

    assert len(events) == 1
    event = events.iloc[0]
    assert event["breakout_timestamp"] == event_day + pd.Timedelta(hours=12)
    assert event["pre_breakout_quote_volume_60m"] == 12_000.0
    assert event["pre_breakout_quote_volume_60m_log_ratio"] > 0
    assert event["pre_breakout_quote_volume_60m_percentile"] == 1.0


def test_causal_crossing_cohort_keeps_false_daily_breakouts_without_lookahead(tmp_path: Path) -> None:
    dates = pd.date_range("2025-01-01", periods=100, freq="D", tz="UTC")
    daily = pd.DataFrame(
        {
            "date": dates,
            "symbol": "BTCUSDT",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "base_volume": 1_000.0,
            "quote_volume": 100_000.0,
            "number_of_trades": 1_000,
            "taker_buy_quote_volume": 50_000.0,
            "available_time_ms": (dates + pd.Timedelta(days=1)).astype("int64") // 1_000_000,
        }
    )
    daily.loc[daily.index[-1], "close"] = 99.0
    trend = build_trend_state(daily, horizons=(3,))
    event_day = dates[-1]
    rows = []
    for day_offset in range(30, 0, -1):
        day = event_day - pd.Timedelta(days=day_offset)
        for minute_offset in range(60):
            timestamp = day + pd.Timedelta(hours=11, minutes=minute_offset)
            rows.append(
                {
                    "timestamp": int(timestamp.timestamp() * 1_000),
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1.0,
                    "quote_volume": 100.0,
                    "trade_count": 10.0,
                    "taker_buy_quote_volume": 50.0,
                }
            )
    for minute_offset in range(121):
        timestamp = event_day + pd.Timedelta(hours=11, minutes=minute_offset)
        crossing = minute_offset == 60
        close = 101.0 if crossing else (100.5 if minute_offset > 60 else 100.0)
        rows.append(
            {
                "timestamp": int(timestamp.timestamp() * 1_000),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 2.0,
                "quote_volume": 200.0,
                "trade_count": 20.0,
                "taker_buy_quote_volume": 100.0,
            }
        )
    pd.DataFrame(rows).sort_values("timestamp").to_parquet(tmp_path / "BTCUSDT.parquet", index=False)
    membership = pd.DataFrame({"date": [event_day], "symbol": ["BTCUSDT"]})

    crossings = build_all_causal_crossings(
        daily,
        trend,
        membership,
        (3,),
        config=BreakoutFlowConfig(source_dir=tmp_path),
    )
    paths = build_causal_crossing_future_paths(
        crossings,
        config=replace(EventPathConfig(), source_dir=tmp_path),
    )

    assert len(crossings) == 1
    assert bool(crossings.iloc[0]["daily_close_confirmed"]) is False
    assert crossings.iloc[0]["snapshot_time"] == event_day + pd.Timedelta(hours=12, minutes=1)
    assert paths.iloc[0]["future_start_time"] > paths.iloc[0]["snapshot_time"]
    assert bool(paths.iloc[0]["path_resolved_1h"]) is True


def test_range_maximum_index_finds_exact_first_strict_break() -> None:
    values = np.array([1.0, 4.0, 2.0, 7.0, 5.0])
    index = _RangeMaximumIndex(values)

    assert index.range_maximum(1, 4) == 7.0
    assert index.first_greater(0, 4.0) == 3
    assert index.first_greater(2, 4.0) == 3
    assert index.first_greater(4, 5.0) is None


def test_structural_low_is_usable_only_after_completed_break_of_prior_high() -> None:
    start = pd.Timestamp("2025-01-01", tz="UTC")
    highs = [10, 12, 15, 13, 12, 10, 11, 12, 16, 17, 16]
    lows = [8, 9, 10, 9, 8, 6, 8, 9, 10, 12, 11]
    closes = [9, 11, 14, 12, 10, 8, 10, 11, 16, 16, 15]
    rows: list[dict[str, float | int]] = []
    for bar_index, (high, low, close) in enumerate(zip(highs, lows, closes, strict=True)):
        for minute_index in range(5):
            timestamp = start + pd.Timedelta(minutes=bar_index * 5 + minute_index)
            rows.append(
                {
                    "timestamp": int(timestamp.timestamp() * 1_000),
                    "open": close,
                    "high": high,
                    "low": low,
                    "close": close,
                }
            )
    minute = pd.DataFrame(rows)

    before_break = _confirmed_structural_lows(minute.iloc[: 8 * 5], 5)
    structural = _confirmed_structural_lows(minute, 5)

    assert before_break.empty
    assert len(structural) == 1
    assert structural.iloc[0]["pivot_price"] == 6.0
    assert structural.iloc[0]["broken_high_price"] == 15.0
    assert structural.iloc[0]["confirmation_time"] == start + pd.Timedelta(minutes=45)


def test_signal_density_uses_period_average_and_not_a_daily_quota() -> None:
    crossings = pd.DataFrame(
        {
            "event_id": ["a", "b", "c", "d"],
            "snapshot_time": pd.to_datetime(
                [
                    "2025-01-01 01:00:00Z",
                    "2025-01-01 02:00:00Z",
                    "2025-01-01 03:00:00Z",
                    "2025-01-03 01:00:00Z",
                ],
                utc=True,
            ),
        }
    )

    daily, summary = build_signal_density_audit(crossings)

    assert daily["unique_signals"].tolist() == [3, 0, 1]
    assert summary.iloc[0]["mean_signals_per_calendar_day"] == pytest.approx(4 / 3)
    assert bool(summary.iloc[0]["quality_reduction_required"]) is False
    with pytest.raises(SpotTrendContractError, match="frozen at 3"):
        SignalDensityConfig(target_mean_entries_per_calendar_day=10.0)


def test_win_loss_feature_catalog_excludes_future_confirmation() -> None:
    crossings = pd.DataFrame(
        columns=[
            "trigger_horizon",
            "crossing_minute_of_day",
            "daily_close_confirmed",
            "pre_crossing_trade_count_5m_robust_z",
        ]
    )

    features = _pre_entry_feature_columns(crossings)

    assert "daily_close_confirmed" not in features
    assert "pre_crossing_trade_count_5m_robust_z" in features


def test_data_inferred_master_records_gaps_causally_and_canonicalizes_multiplier_contracts() -> None:
    bars = _futures_bars(10)
    gap_day = pd.Timestamp("2023-01-05", tz="UTC")
    bars = bars.loc[~(bars["symbol"].eq("AAAUSDT") & bars["date"].eq(gap_day))]

    master = build_data_inferred_futures_master(bars)
    aaa = master.loc[master["symbol"].eq("AAAUSDT")]

    assert list(aaa["as_of_date"]) == [
        pd.Timestamp("2023-01-01", tz="UTC"),
        gap_day,
        pd.Timestamp("2023-01-06", tz="UTC"),
    ]
    assert list(aaa["status"]) == ["TRADING", "DATA_INFERRED_INACTIVE", "TRADING"]
    assert canonical_futures_base("1000PEPEUSDT") == "PEPE"
    assert canonical_futures_base("BTCUSDT") == "BTC"


def test_futures_feature_catalog_is_mechanism_grouped_and_label_free() -> None:
    bars = _futures_bars()
    master = build_data_inferred_futures_master(bars)
    universe_config = UniverseConfig(
        minimum_history_bars=60,
        minimum_median_quote_volume=1_000_000.0,
        entry_rank=5,
        retention_rank=6,
        maximum_members=5,
    )
    universe = build_point_in_time_universe(bars, master, universe_config)
    trend = build_trend_state(bars)
    base = build_feature_matrix(bars, universe, trend)
    futures = build_futures_feature_matrix(base, bars)
    catalog = futures_feature_catalog(futures)

    names = {spec.name for spec in catalog}
    families = {spec.family for spec in catalog}
    assert {
        "trend_path",
        "volatility_range",
        "liquidity_flow",
        "open_interest_positioning",
        "cross_section_market",
    }.issubset(families)
    assert "log_price_trend_r2_20" in names
    assert "parkinson_volatility_20" in names
    assert "taker_imbalance_mean_20" in names
    assert "oi_log_change_5" in names
    assert "open" not in names
    assert "close" not in names
    assert "symbol" not in names
    assert not any("future" in name or "target" in name for name in names)
