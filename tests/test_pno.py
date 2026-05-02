import argparse
import json
import logging
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from cli import commands, pno_diagnostics
from cli.parser import build_parser, resolve_handler
from config.app_config import AppConfig
from config.backtest_config import BacktestConfig
from config.fetch_config import FetchConfig
from config.simulation_config import SimulationConfig
from config.strategy_config import StrategyConfig
from constants import SIMULATION_PARQUET_FILE_NAME
from domain.enums.timeframe import Timeframe
from domain.enums.trade_result_type import TradeResultType
from domain.models.trade_result import TradeResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price
from strategy.factory import build_strategy
from strategy.pno import PnoParams, PnoStrategy
from strategy.pno.config import (
    PNO_DEFAULT_RISK_PCT,
    PNO_BACKTEST_TIMEFRAME_PAIRS,
    PNO_LIVE_TIMEFRAME_PAIRS,
    build_pno_grid,
    describe_pno_category_profile_set,
    resolve_pno_category_profiles,
    resolve_pno_default_timeframe_pair,
    validate_pno_params,
    validate_pno_timeframe_pair,
    with_pno_risk,
)
from strategy.pno.engine import (
    ArmedContext,
    FiveMinuteFrame,
    OneMinuteFrame,
    PnoEngine,
    RetiredCluster,
    Stage1Context,
    Stage2Context,
    Stage3Context,
    Stage4Context,
)
from strategy.pno.pno_strategy import _PnoSecondsFrameProvider
from data.storage.parquet_storage import ParquetStorage
from vectorbt_runner import DataPreparer
from vectorbt_runner.mtf_frames import SymbolMtfFrames


_PNO_GOLDEN_CASES_RUN_DIR = Path(
    r"C:\Users\Ascf\PycharmProjects\mtf-trend-2\.output\results\backtest_runs\20260414_065918_pno"
)
_PNO_GOLDEN_CASES_BACKUP_DIR = Path(
    r"C:\Users\Ascf\PycharmProjects\mtf-trend-2\tests\fixtures\pno_golden_cases\20260414_065918_pno"
)
_PNO_GOLDEN_TRADE_CASES: tuple[dict[str, object], ...] = (
    {
        "case_id": "dogs_2024_08_28_pno_s30",
        "symbol": "DOGS/USDT:USDT",
        "category_mode": "discovery",
        "row_number": 1,
        "window_start_ms": 1724837400000,
        "window_end_ms": 1724838600000,
        "expected_entry_min_ms": 1724838000000,
        "expected_entry_max_ms": 1724838060000,
        "min_pnl_percent": 0.0,
    },
    {
        "case_id": "rave_2026_03_14_pno_s30",
        "symbol": "RAVE/USDT:USDT",
        "category_mode": "all",
        "row_number": 1,
        "window_start_ms": 1773618000000,  # 2026-03-14 23:40 UTC
        "window_end_ms": 1775764800000,  # 2026-04-09 20:00 UTC
        "expected_entry_min_ms": 1773618600000,  # 2026-03-14 23:50 UTC
        "expected_entry_max_ms": 1773618720000,  # 2026-03-14 23:52 UTC
        "min_pnl_percent": 0.05,
    },
    {
        "case_id": "vana_2026_01_26_pno_s30",
        "symbol": "VANA/USDT:USDT",
        "category_mode": "all",
        "row_number": 1,
        "window_start_ms": 1769367600000,
        "window_end_ms": 1771623300000,
        "expected_entry_min_ms": 1769368200000,
        "expected_entry_max_ms": 1769368320000,
        "min_pnl_percent": 0.1,
    },
)


def _build_test_stage4_context(**overrides) -> Stage4Context:
    base = {
        "active_high_idx": 0,
        "active_high_timestamp": 60_000,
        "active_high": 10.5,
        "pullback_low_idx": 0,
        "pullback_low_timestamp": 60_000,
        "pullback_low": 9.5,
        "pullback_depth": 0.4,
        "cluster_indices": (0, 1),
        "cluster_prices": (9.95, 10.0),
        "level": 9.95,
        "level_pos": 0.45,
        "touches": 2,
        "cluster_first_idx": 0,
        "cluster_last_idx": 1,
        "level_valid_idx": 0,
        "level_valid_timestamp": 60_000,
        "level_low": 9.5,
        "level_low_minor_break": False,
        "level_low_major_break": False,
        "penalty_level_low_break": 0,
        "penalty_untested_highs": 0,
        "base_bonus": 0,
        "pno_index": 1,
        "maturity_penalty": 0,
        "pno_order_adj": 8,
        "score_a": 10,
        "score_b": 10,
        "score_c": 10,
        "score_d": 10,
        "score_e": 7,
        "score_tp2": 5,
        "final_score": 80.0,
        "entry_plan": 10.0,
        "sl_plan": 9.5,
        "low_last_red_plan": 9.5,
        "tp1": 10.5,
        "tp2": 11.0,
        "stage4_ready": True,
        "hard_block": False,
        "is_valid_setup": True,
        "hard_block_reason": None,
    }
    base.update(overrides)
    return Stage4Context(**base)


def _build_test_stage1_context(**overrides) -> Stage1Context:
    base = {
        "start_idx": 0,
        "start_timestamp": 0,
        "pump_start_5m_idx": 0,
        "pump_start_timestamp": 0,
        "current_5m_idx": 0,
        "active_high_idx": 0,
        "active_high_timestamp": 60_000,
        "active_high": 10.5,
        "reference_high": 10.5,
        "leg_start_idx": 0,
        "leg_start_timestamp": 0,
        "leg_start": 9.5,
        "leg_size": 1.0,
        "reference_leg_size": 1.0,
        "hold_floor": 9.8,
        "pump_volume_ratio_start": 5.0,
        "pump_path_efficiency": 0.55,
        "active_high_bar_upper_wick_share": 0.25,
        "active_high_bar_close_position": 0.70,
    }
    base.update(overrides)
    return Stage1Context(**base)


def _build_test_one_frame(*, timestamp_ms: int) -> OneMinuteFrame:
    frame = pd.DataFrame(
        {
            "timestamp": [timestamp_ms],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }
    )
    zeros = pd.Series([0.0], dtype="float64").to_numpy()
    return OneMinuteFrame(
        frame=frame,
        timestamps=pd.Series([timestamp_ms], dtype="int64").to_numpy(),
        opens=pd.Series([1.0], dtype="float64").to_numpy(),
        highs=pd.Series([1.0], dtype="float64").to_numpy(),
        lows=pd.Series([1.0], dtype="float64").to_numpy(),
        closes=pd.Series([1.0], dtype="float64").to_numpy(),
        volumes=pd.Series([1.0], dtype="float64").to_numpy(),
        quote_volume=pd.Series([1.0], dtype="float64").to_numpy(),
        cumulative_quote_volume=pd.Series([1.0], dtype="float64").to_numpy(),
        tr=zeros.copy(),
        v1=pd.Series([0.5], dtype="float64").to_numpy(),
        red=pd.Series([False]).to_numpy(dtype=bool),
        confirmed_high_indices=pd.Series([], dtype="int64").to_numpy(),
        confirmed_high_confirmed_at=pd.Series([], dtype="int64").to_numpy(),
        confirmed_low_indices=pd.Series([], dtype="int64").to_numpy(),
        confirmed_low_confirmed_at=pd.Series([], dtype="int64").to_numpy(),
        low_range_tree=PnoEngine._build_range_tree(pd.Series([1.0], dtype="float64").to_numpy(), is_min_tree=True),
    )


def _build_test_one_frame_multi(*, timestamps_ms: list[int], price: float = 1.0) -> OneMinuteFrame:
    frame = pd.DataFrame(
        {
            "timestamp": timestamps_ms,
            "open": [price] * len(timestamps_ms),
            "high": [price + 0.1] * len(timestamps_ms),
            "low": [price - 0.1] * len(timestamps_ms),
            "close": [price] * len(timestamps_ms),
            "volume": [1.0] * len(timestamps_ms),
        }
    )
    timestamps = pd.Series(timestamps_ms, dtype="int64").to_numpy()
    highs = pd.Series([price + 0.1] * len(timestamps_ms), dtype="float64").to_numpy()
    lows = pd.Series([price - 0.1] * len(timestamps_ms), dtype="float64").to_numpy()
    return OneMinuteFrame(
        frame=frame,
        timestamps=timestamps,
        opens=pd.Series([price] * len(timestamps_ms), dtype="float64").to_numpy(),
        highs=highs,
        lows=lows,
        closes=pd.Series([price] * len(timestamps_ms), dtype="float64").to_numpy(),
        volumes=pd.Series([1.0] * len(timestamps_ms), dtype="float64").to_numpy(),
        quote_volume=pd.Series([price] * len(timestamps_ms), dtype="float64").to_numpy(),
        cumulative_quote_volume=pd.Series(range(1, len(timestamps_ms) + 1), dtype="float64").to_numpy(),
        tr=pd.Series([0.2] * len(timestamps_ms), dtype="float64").to_numpy(),
        v1=pd.Series([0.5] * len(timestamps_ms), dtype="float64").to_numpy(),
        red=pd.Series([False] * len(timestamps_ms)).to_numpy(dtype=bool),
        confirmed_high_indices=pd.Series([], dtype="int64").to_numpy(),
        confirmed_high_confirmed_at=pd.Series([], dtype="int64").to_numpy(),
        confirmed_low_indices=pd.Series([], dtype="int64").to_numpy(),
        confirmed_low_confirmed_at=pd.Series([], dtype="int64").to_numpy(),
        low_range_tree=PnoEngine._build_range_tree(lows, is_min_tree=True),
    )


def _build_test_five_frame(
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    ema20: list[float],
) -> FiveMinuteFrame:
    size = len(opens)
    timestamps = (pd.Series(range(size), dtype="int64") * 300_000).to_numpy()
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * size,
        }
    )
    zeros = pd.Series([0.0] * size, dtype="float64").to_numpy()
    bools = pd.Series([False] * size).to_numpy(dtype=bool)
    ints = pd.Series([0] * size, dtype="int64").to_numpy()
    return FiveMinuteFrame(
        frame=frame,
        timestamps=timestamps,
        opens=pd.Series(opens, dtype="float64").to_numpy(),
        highs=pd.Series(highs, dtype="float64").to_numpy(),
        lows=pd.Series(lows, dtype="float64").to_numpy(),
        closes=pd.Series(closes, dtype="float64").to_numpy(),
        volumes=pd.Series([1.0] * size, dtype="float64").to_numpy(),
        quote_volume=pd.Series([1.0] * size, dtype="float64").to_numpy(),
        trade_activity=pd.Series([1.0] * size, dtype="float64").to_numpy(),
        tr=pd.Series([0.5] * size, dtype="float64").to_numpy(),
        v5=pd.Series([0.5] * size, dtype="float64").to_numpy(),
        ema9=pd.Series([10.5] * size, dtype="float64").to_numpy(),
        ema20=pd.Series(ema20, dtype="float64").to_numpy(),
        ema50=pd.Series([10.0] * size, dtype="float64").to_numpy(),
        ema100=pd.Series([9.8] * size, dtype="float64").to_numpy(),
        ema200=pd.Series([9.6] * size, dtype="float64").to_numpy(),
        sleep=bools.copy(),
        wake=bools.copy(),
        inplay=~bools.copy(),
        pump_start_idx=ints.copy(),
        sleep_start_idx=ints.copy(),
        sleep_end_idx=ints.copy(),
        stage1_confirm_idx=ints.copy(),
        stage1_hold_price=zeros.copy(),
        r3_quote=zeros.copy(),
        b24_quote=zeros.copy(),
        r3_trade=zeros.copy(),
        b24_trade=zeros.copy(),
        activity_last6_quote=zeros.copy(),
        activity_prev24_quote=zeros.copy(),
        activity_last6_trade=zeros.copy(),
        activity_prev24_trade=zeros.copy(),
        cumulative_quote_volume=pd.Series([1.0] * size, dtype="float64").to_numpy(),
        pre_high_24h=zeros.copy(),
        pre_high_1h=zeros.copy(),
        ema_cross_count_1h=zeros.copy(),
        atr_pre_14=pd.Series([0.25] * size, dtype="float64").to_numpy(),
        pre_quote_median_24=pd.Series([1.0] * size, dtype="float64").to_numpy(),
        pre_trade_median_24=pd.Series([1.0] * size, dtype="float64").to_numpy(),
    )


def _load_pno_golden_case(
    *,
    symbol: str,
    row_number: int,
    window_end_ms: int,
) -> tuple[PnoParams, SymbolMtfFrames]:
    _sync_pno_golden_case_artifacts(symbol=symbol)
    results_path = _PNO_GOLDEN_CASES_BACKUP_DIR / "strategy" / "pno" / "results.csv"
    results = pd.read_csv(results_path)
    row = results.iloc[int(row_number) - 1]
    params = commands._build_pno_params_from_row(
        row,
        symbol=symbol,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.S30,
    )
    preparer = DataPreparer(_PNO_GOLDEN_CASES_BACKUP_DIR / "cache")
    levels_frame = preparer.load_symbol_data(
        symbol,
        Timeframe.M5,
        days=30,
        end_timestamp_ms=int(window_end_ms),
    )
    entry_frame = preparer.load_symbol_data(
        symbol,
        Timeframe.M1,
        days=30,
        end_timestamp_ms=int(window_end_ms),
    )
    return params, SymbolMtfFrames(
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.S30,
        levels_frame=levels_frame,
        entry_frame=entry_frame,
    )


def _sync_pno_golden_case_artifacts(*, symbol: str) -> None:
    source_results_path = _PNO_GOLDEN_CASES_RUN_DIR / "strategy" / "pno" / "results.csv"
    backup_results_path = _PNO_GOLDEN_CASES_BACKUP_DIR / "strategy" / "pno" / "results.csv"
    if source_results_path.exists():
        backup_results_path.parent.mkdir(parents=True, exist_ok=True)
        if (not backup_results_path.exists()) or source_results_path.stat().st_mtime > backup_results_path.stat().st_mtime:
            shutil.copy2(source_results_path, backup_results_path)
    assert backup_results_path.exists(), "golden-case results backup is missing"

    encoded_symbol = ParquetStorage.encode_symbol_for_path(symbol)
    for timeframe in (Timeframe.M1, Timeframe.M5):
        source_cache_path = Path(".output/cache") / encoded_symbol / timeframe.value / SIMULATION_PARQUET_FILE_NAME
        backup_cache_path = _PNO_GOLDEN_CASES_BACKUP_DIR / "cache" / encoded_symbol / timeframe.value / SIMULATION_PARQUET_FILE_NAME
        if source_cache_path.exists():
            backup_cache_path.parent.mkdir(parents=True, exist_ok=True)
            if (not backup_cache_path.exists()) or source_cache_path.stat().st_mtime > backup_cache_path.stat().st_mtime:
                shutil.copy2(source_cache_path, backup_cache_path)
        assert backup_cache_path.exists(), f"golden-case cache backup is missing for {symbol} {timeframe.value}"


def test_build_strategy_supports_pno() -> None:
    config = AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="pno"),
        simulation=SimulationConfig(),
        backtest=BacktestConfig(
            log_level="INFO",
            cache_dir=Path("."),
            logs_dir=Path("."),
            results_dir=Path("."),
            results_file_name="results.csv",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        ),
    )

    strategy = build_strategy(config)

    assert isinstance(strategy, PnoStrategy)


def test_build_strategy_supports_pno_discovery_mode() -> None:
    config = AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="pno", pno_category_mode="discovery"),
        simulation=SimulationConfig(),
        backtest=BacktestConfig(
            log_level="INFO",
            cache_dir=Path("."),
            logs_dir=Path("."),
            results_dir=Path("."),
            results_file_name="results.csv",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        ),
    )

    strategy = build_strategy(config)

    assert isinstance(strategy, PnoStrategy)
    grid = strategy.build_parameter_grid()
    assert [params.entry_confirmation_mode for params in grid] == ["close_above"]


def test_resolve_backtest_timeframes_normalizes_pno_defaults() -> None:
    levels_tf, entry_tf = commands._resolve_backtest_timeframes(
        strategy_id="pno",
        args=argparse.Namespace(entry_tf=None, levels_tf=None),
        configured_levels_timeframe=Timeframe.D1,
        configured_entry_timeframe=Timeframe.M15,
    )

    assert levels_tf == Timeframe.M5
    assert entry_tf == Timeframe.S30


def test_resolve_backtest_timeframes_rejects_live_only_pno_pair() -> None:
    with pytest.raises(ValueError, match="pno backtest supports only timeframe pairs"):
        commands._resolve_backtest_timeframes(
            strategy_id="pno",
            args=argparse.Namespace(entry_tf="10s", levels_tf="1m"),
            configured_levels_timeframe=Timeframe.M5,
            configured_entry_timeframe=Timeframe.M1,
        )


def test_pno_validate_timeframe_pair_supports_live_and_backtest_pairs() -> None:
    for levels_timeframe, entry_timeframe in (*PNO_BACKTEST_TIMEFRAME_PAIRS, *PNO_LIVE_TIMEFRAME_PAIRS):
        validate_pno_timeframe_pair(
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )
        validate_pno_params(
            PnoParams(
                symbol="TEST/USDT",
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
            )
        )


def test_resolve_pno_default_timeframe_pair_uses_backtest_pair() -> None:
    assert resolve_pno_default_timeframe_pair(mode="backtest") == (Timeframe.M5, Timeframe.S30)
    assert resolve_pno_default_timeframe_pair(mode="live") == (Timeframe.M5, Timeframe.S30)


def test_build_pno_params_from_row_roundtrips_strategy_params() -> None:
    strategy = PnoStrategy(deposit=2_000.0, risk_pct=0.03)
    original = PnoParams(
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        pno_variant_id="baseline",
        pno_deposit=2_000.0,
        pno_risk_pct=0.03,
        pno_r_trade=60.0,
        entry_confirmation_mode="close_above",
        fee_rate=0.0007,
        min_stage1_leg_v1=1.25,
        stage1_min_cumulative_quote_volume=750_000.0,
        stage1_pre_pump_ema_crosses_min=3,
        stage1_min_impulse_atr_pre=3.0,
        level_min_maturity_fraction=0.35,
        max_entry_pullback_fraction=0.45,
        min_entry_rr=1.1,
        close_above_max_entry_pos=0.52,
        close_above_max_pullback_fraction_of_leg=0.50,
        close_above_max_post_high_wick_share=0.55,
        close_above_min_signal_volume_vs_recent=0.9,
        close_above_min_signal_ema20_slope_3=0.18,
        close_above_min_signal_ema_spread_pct=1.2,
        close_above_min_signal_close_position_in_chop=0.30,
        close_above_choppy_overlap_threshold=0.92,
        pullback_min_pump_fraction_5m=0.22,
        pullback_valid_min_leg_fraction=0.82,
        pullback_valid_max_v5=4.0,
        close_above_be_start_fraction=0.7,
        close_above_be_step_fraction=0.05,
        close_above_be_step_bars=2,
        close_above_be_min_fraction=0.25,
        min_score=72.0,
        strong_score=84.0,
    )

    row = pd.Series(strategy.params_to_row(original))
    rebuilt = commands._build_pno_params_from_row(
        row,
        symbol="TEST/USDT",
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
    )

    assert rebuilt.pno_variant_id == original.pno_variant_id
    assert rebuilt.pno_deposit == original.pno_deposit
    assert rebuilt.pno_risk_pct == original.pno_risk_pct
    assert rebuilt.pno_r_trade == original.pno_r_trade
    assert rebuilt.entry_confirmation_mode == original.entry_confirmation_mode
    assert rebuilt.fee_rate == original.fee_rate
    assert rebuilt.min_stage1_leg_v1 == original.min_stage1_leg_v1
    assert rebuilt.stage1_min_cumulative_quote_volume == original.stage1_min_cumulative_quote_volume
    assert rebuilt.stage1_pre_pump_ema_crosses_min == original.stage1_pre_pump_ema_crosses_min
    assert rebuilt.stage1_min_impulse_atr_pre == original.stage1_min_impulse_atr_pre
    assert rebuilt.level_min_maturity_fraction == original.level_min_maturity_fraction
    assert rebuilt.max_entry_pullback_fraction == original.max_entry_pullback_fraction
    assert rebuilt.min_entry_rr == original.min_entry_rr
    assert rebuilt.close_above_max_entry_pos == original.close_above_max_entry_pos
    assert rebuilt.close_above_max_pullback_fraction_of_leg == original.close_above_max_pullback_fraction_of_leg
    assert rebuilt.close_above_max_post_high_wick_share == original.close_above_max_post_high_wick_share
    assert rebuilt.close_above_min_signal_volume_vs_recent == original.close_above_min_signal_volume_vs_recent
    assert rebuilt.close_above_min_signal_ema20_slope_3 == original.close_above_min_signal_ema20_slope_3
    assert rebuilt.close_above_min_signal_ema_spread_pct == original.close_above_min_signal_ema_spread_pct
    assert rebuilt.close_above_min_signal_close_position_in_chop == original.close_above_min_signal_close_position_in_chop
    assert rebuilt.close_above_choppy_overlap_threshold == original.close_above_choppy_overlap_threshold
    assert rebuilt.pullback_min_pump_fraction_5m == original.pullback_min_pump_fraction_5m
    assert rebuilt.pullback_valid_min_leg_fraction == original.pullback_valid_min_leg_fraction
    assert rebuilt.pullback_valid_max_v5 == original.pullback_valid_max_v5
    assert rebuilt.close_above_be_start_fraction == original.close_above_be_start_fraction
    assert rebuilt.close_above_be_step_fraction == original.close_above_be_step_fraction
    assert rebuilt.close_above_be_step_bars == original.close_above_be_step_bars
    assert rebuilt.close_above_be_min_fraction == original.close_above_be_min_fraction
    assert rebuilt.min_score == original.min_score
    assert rebuilt.strong_score == original.strong_score


def test_build_pno_grid_includes_cross_and_close_confirmation_variants() -> None:
    grid = build_pno_grid()

    assert [params.entry_confirmation_mode for params in grid] == ["close_above"]
    assert [params.pno_variant_id for params in grid] == ["baseline_close"]


def test_validate_pno_params_rejects_legacy_cross_confirmation_mode() -> None:
    with pytest.raises(ValueError, match="entry_confirmation_mode"):
        validate_pno_params(PnoParams(symbol="TEST/USDT", entry_confirmation_mode="cross"))


def test_resolve_pno_artifact_rows_uses_entry_mode_names() -> None:
    results = pd.DataFrame(
        [
            {"entry_confirmation_mode": "cross", "pno_variant_id": "baseline_cross"},
            {"entry_confirmation_mode": "close_above", "pno_variant_id": "baseline_close"},
        ]
    )

    resolved = commands._resolve_pno_artifact_rows(results)

    assert [name for name, _ in resolved] == ["cross", "close_above"]


def test_parser_restricts_pno_entry_confirmation_mode_to_close_above() -> None:
    parser = build_parser()

    args = parser.parse_args(["run-backtest", "--pno-entry-confirmation-mode", "close_above"])

    assert args.pno_entry_confirmation_mode == "close_above"


def test_with_pno_risk_caps_to_five_percent() -> None:
    params = with_pno_risk(PnoParams(symbol="TEST/USDT"), deposit=1_000.0, risk_pct=0.20)

    assert params.pno_risk_pct == pytest.approx(PNO_DEFAULT_RISK_PCT)
    assert params.pno_r_trade == pytest.approx(50.0)


def test_plot_pno_diagnostics_writes_trade_and_stage_artifacts(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params_row = pd.Series(strategy.params_to_row(PnoParams(symbol="BTC/USDT")))
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }
    trade = TradeResult(
        entry_price=Price(1.2),
        exit_price=Price(1.3),
        entry_timestamp_ms=120_000,
        exit_timestamp_ms=180_000,
        result_type=TradeResultType.TP2,
        pnl=10.0,
        pnl_percent=Percentage(1.0),
        metadata={
            "category": "tp2",
            "final_score": 82.0,
            "pump_start_timestamp_ms": 60_000,
            "level": 1.15,
            "level_first_local_high_timestamp_ms": 60_000,
            "entry_price_actual": 1.2,
            "exit_price_actual": 1.3,
            "sl_actual": 1.0,
            "tp1": 1.3,
            "tp2": 1.4,
        },
    )

    monkeypatch.setattr(strategy, "generate_events_multi_tf", lambda **_kwargs: [trade])
    monkeypatch.setattr(
        strategy,
        "consume_last_generation_diagnostics",
        lambda: {
            "trades_generated": 1,
            "stage_events": [
                {"stage_id": "stage_1_pump", "timestamp_ms": 60_000, "active_high": 1.4},
                {"stage_id": "stage_4_level", "timestamp_ms": 120_000, "level": 1.15},
                {"stage_id": "stage_5_trade", "timestamp_ms": 120_000, "entry_price": 1.2},
            ],
            "stage_hits": {
                "stage_1_pump": 1,
                "stage_2_high_pullback": 0,
                "stage_3_valid_pullback": 0,
                "stage_4_level": 1,
                "stage_5_trade": 1,
            },
        },
    )

    commands._plot_pno_diagnostics_for_symbols(
        config=SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path)),
        args=argparse.Namespace(output_dir=None),
        logger=logging.getLogger("test-pno-plot"),
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        log_prefix="test",
    )

    diagnostics_dir = tmp_path / "trade_plots" / "pno_diagnostics"
    assert (diagnostics_dir / "BTC_USDT_diagnostics.json").exists()
    assert (diagnostics_dir / "BTC_USDT_trades.csv").exists()
    assert any((diagnostics_dir / "charts").glob("BTC_USDT_*.png"))
    assert (diagnostics_dir / "stage_reviews" / "manifest.csv").exists()
    manifest = pd.read_csv(diagnostics_dir / "stage_reviews" / "manifest.csv")
    assert "stage_1_pump" in set(manifest["stage_id"])
    assert "stage_5_trade" in set(manifest["stage_id"])
    stage1_row = manifest.loc[manifest["stage_id"] == "stage_1_pump"].iloc[0]
    assert int(stage1_row["passed_count"]) == 1
    assert int(stage1_row["passed_charts_count"]) == 0


def test_load_plot_params_row_from_results_supports_pno(tmp_path) -> None:
    strategy_results_dir = tmp_path / "strategy" / "pno"
    strategy_results_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "pno_variant_id": "baseline",
                "pno_deposit": 1000.0,
                "pno_risk_pct": 0.02,
                "pno_r_trade": 20.0,
                "pno_fee_rate": 0.0005,
                "pno_min_score": 70.0,
                "pno_strong_score": 80.0,
                "profit_factor": 1.5,
                "trades_count": 3,
            }
        ]
    ).to_csv(strategy_results_dir / "results.csv", index=False)

    row = commands._load_plot_params_row_from_results(
        SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path, results_file_name="results.csv")),
        argparse.Namespace(results_input=None, input=None, id=None),
        logger=logging.getLogger("test-pno-results"),
        strategy_id="pno",
    )

    assert row is not None
    assert str(row["pno_variant_id"]) == "baseline"


def test_resolve_pno_stage_ids_supports_single_and_through() -> None:
    single = commands._resolve_pno_stage_ids(
        argparse.Namespace(pno_stage=3, pno_through_stage=None)
    )
    through = commands._resolve_pno_stage_ids(
        argparse.Namespace(pno_stage=None, pno_through_stage=2)
    )

    assert single == ("stage_3_valid_pullback",)
    assert through == ("stage_1_pump", "stage_2_high_pullback")


def test_plot_pno_diagnostics_filters_selected_stage_ids(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params_row = pd.Series(strategy.params_to_row(PnoParams(symbol="BTC/USDT")))
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }

    monkeypatch.setattr(strategy, "generate_events_multi_tf", lambda **_kwargs: [])
    monkeypatch.setattr(
        strategy,
        "consume_last_generation_diagnostics",
        lambda: {
            "trades_generated": 0,
            "stage_events": [
                {"stage_id": "stage_1_pump", "timestamp_ms": 60_000},
                {"stage_id": "stage_2_high_pullback", "timestamp_ms": 120_000},
                {"stage_id": "stage_4_level", "timestamp_ms": 180_000},
            ],
            "stage_hits": {
                "stage_1_pump": 1,
                "stage_2_high_pullback": 1,
                "stage_3_valid_pullback": 0,
                "stage_4_level": 1,
                "stage_5_trade": 0,
            },
        },
    )

    commands._plot_pno_diagnostics_for_symbols(
        config=SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path)),
        args=argparse.Namespace(output_dir=None, pno_stage=2, pno_through_stage=None),
        logger=logging.getLogger("test-pno-plot-filtered"),
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        log_prefix="test",
    )

    manifest = pd.read_csv(tmp_path / "trade_plots" / "pno_diagnostics" / "stage_reviews" / "manifest.csv")
    assert list(manifest["stage_id"]) == ["stage_2_high_pullback"]


def test_plot_pno_diagnostics_without_stage_filter_skips_passed_stage_charts(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params_row = pd.Series(strategy.params_to_row(PnoParams(symbol="BTC/USDT")))
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }
    rendered_statuses: list[str] = []

    monkeypatch.setattr(strategy, "generate_events_multi_tf", lambda **_kwargs: [])
    monkeypatch.setattr(
        strategy,
        "consume_last_generation_diagnostics",
        lambda: {
            "trades_generated": 0,
            "stage_events": [
                {"stage_id": "stage_1_pump", "timestamp_ms": 60_000},
            ],
            "stage_rejections": [
                {
                    "stage_id": "stage_1_pump",
                    "timestamp_ms": 120_000,
                    "reason": "volume_ratio_start_too_small",
                    "pump_volume_ratio_start": 2.95,
                    "stage1_min_volume_ratio_start": 3.0,
                }
            ],
            "stage_hits": {
                "stage_1_pump": 1,
                "stage_2_high_pullback": 0,
                "stage_3_valid_pullback": 0,
                "stage_4_level": 0,
                "stage_5_trade": 0,
            },
        },
    )
    monkeypatch.setattr(commands, "_export_pno_research_context", lambda **_kwargs: None)

    def _fake_render_stage_review_chart(**kwargs):
        rendered_statuses.append(str(kwargs["status"]))
        return str(Path(kwargs["charts_dir"]) / "chart.png")

    monkeypatch.setattr(pno_diagnostics, "_render_pno_stage_review_chart", _fake_render_stage_review_chart)

    commands._plot_pno_diagnostics_for_symbols(
        config=SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path)),
        args=argparse.Namespace(output_dir=None, pno_stage=None, pno_through_stage=None),
        logger=logging.getLogger("test-pno-plot-default-stage-review"),
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        log_prefix="test",
    )

    manifest = pd.read_csv(tmp_path / "trade_plots" / "pno_diagnostics" / "stage_reviews" / "manifest.csv")
    stage1_row = manifest.loc[manifest["stage_id"] == "stage_1_pump"].iloc[0]

    assert int(stage1_row["passed_count"]) == 1
    assert int(stage1_row["passed_charts_count"]) == 0
    assert int(stage1_row["rejected_review_count"]) == 1
    assert int(stage1_row["rejected_charts_count"]) == 1
    assert rendered_statuses == ["rejected"]


def test_plot_pno_diagnostics_without_stage_filter_skips_stage2_passed_stage_charts(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params_row = pd.Series(strategy.params_to_row(PnoParams(symbol="BTC/USDT")))
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }
    rendered_statuses: list[str] = []

    monkeypatch.setattr(strategy, "generate_events_multi_tf", lambda **_kwargs: [])
    monkeypatch.setattr(
        strategy,
        "consume_last_generation_diagnostics",
        lambda: {
            "trades_generated": 0,
            "stage_events": [
                {"stage_id": "stage_2_high_pullback", "timestamp_ms": 60_000},
            ],
            "stage_rejections": [],
            "stage_hits": {
                "stage_1_pump": 0,
                "stage_2_high_pullback": 1,
                "stage_3_valid_pullback": 0,
                "stage_4_level": 0,
                "stage_5_trade": 0,
            },
        },
    )
    monkeypatch.setattr(commands, "_export_pno_research_context", lambda **_kwargs: None)

    def _fake_render_stage_review_chart(**kwargs):
        rendered_statuses.append(str(kwargs["status"]))
        return str(Path(kwargs["charts_dir"]) / "chart.png")

    monkeypatch.setattr(pno_diagnostics, "_render_pno_stage_review_chart", _fake_render_stage_review_chart)

    commands._plot_pno_diagnostics_for_symbols(
        config=SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path)),
        args=argparse.Namespace(output_dir=None, pno_stage=None, pno_through_stage=None),
        logger=logging.getLogger("test-pno-plot-stage2-pass-review"),
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        log_prefix="test",
    )

    manifest = pd.read_csv(tmp_path / "trade_plots" / "pno_diagnostics" / "stage_reviews" / "manifest.csv")
    stage2_row = manifest.loc[manifest["stage_id"] == "stage_2_high_pullback"].iloc[0]

    assert int(stage2_row["passed_count"]) == 1
    assert int(stage2_row["passed_charts_count"]) == 0
    assert rendered_statuses == []


def test_export_pno_diagnostics_context_writes_research_artifacts_without_charts(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params_row = pd.Series(strategy.params_to_row(PnoParams(symbol="BTC/USDT")))
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }
    trade = TradeResult(
        entry_price=Price(1.2),
        exit_price=Price(1.3),
        entry_timestamp_ms=120_000,
        exit_timestamp_ms=180_000,
        result_type=TradeResultType.TP2,
        pnl=10.0,
        pnl_percent=Percentage(1.0),
        metadata={
            "category": "tp2",
            "final_score": 82.0,
            "pump_start_timestamp_ms": 60_000,
            "level": 1.15,
            "level_first_local_high_timestamp_ms": 60_000,
            "entry_price_actual": 1.2,
            "exit_price_actual": 1.3,
            "sl_actual": 1.0,
            "tp1": 1.3,
            "tp2": 1.4,
        },
    )

    monkeypatch.setattr(strategy, "generate_events_multi_tf", lambda **_kwargs: [trade])
    monkeypatch.setattr(
        strategy,
        "consume_last_generation_diagnostics",
        lambda: {
            "trades_generated": 1,
            "stage_events": [
                {"stage_id": "stage_1_pump", "timestamp_ms": 60_000, "active_high": 1.4},
                {"stage_id": "stage_4_level", "timestamp_ms": 120_000, "level": 1.15},
                {"stage_id": "stage_5_trade", "timestamp_ms": 120_000, "entry_price": 1.2},
            ],
            "stage_hits": {
                "stage_1_pump": 1,
                "stage_2_high_pullback": 0,
                "stage_3_valid_pullback": 0,
                "stage_4_level": 1,
                "stage_5_trade": 1,
            },
        },
    )

    diagnostics_dir = tmp_path / "trade_plots" / "pno_diagnostics"
    export_result = commands._export_pno_diagnostics_context_for_symbols(
        diagnostics_dir=diagnostics_dir,
        args=argparse.Namespace(output_dir=None, pno_stage=None, pno_through_stage=None),
        logger=logging.getLogger("test-pno-export-context"),
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
    )

    assert export_result["total_trades_generated"] == 1
    assert (diagnostics_dir / "research_context" / "trade_context.csv").exists()
    assert (diagnostics_dir / "stage_reviews" / "manifest.csv").exists()
    assert not (diagnostics_dir / "charts").exists()


def test_plot_pno_diagnostics_exports_research_context_before_rendering_charts(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params_row = pd.Series(strategy.params_to_row(PnoParams(symbol="BTC/USDT")))
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }
    trade = TradeResult(
        entry_price=Price(1.2),
        exit_price=Price(1.3),
        entry_timestamp_ms=120_000,
        exit_timestamp_ms=180_000,
        result_type=TradeResultType.TP2,
        pnl=10.0,
        pnl_percent=Percentage(1.0),
        metadata={
            "category": "tp2",
            "final_score": 82.0,
            "pump_start_timestamp_ms": 60_000,
            "level": 1.15,
            "level_first_local_high_timestamp_ms": 60_000,
            "entry_price_actual": 1.2,
            "exit_price_actual": 1.3,
            "sl_actual": 1.0,
            "tp1": 1.3,
            "tp2": 1.4,
        },
    )

    monkeypatch.setattr(strategy, "generate_events_multi_tf", lambda **_kwargs: [trade])
    monkeypatch.setattr(
        strategy,
        "consume_last_generation_diagnostics",
        lambda: {
            "trades_generated": 1,
            "stage_events": [
                {"stage_id": "stage_1_pump", "timestamp_ms": 60_000, "active_high": 1.4},
                {"stage_id": "stage_4_level", "timestamp_ms": 120_000, "level": 1.15},
                {"stage_id": "stage_5_trade", "timestamp_ms": 120_000, "entry_price": 1.2},
            ],
            "stage_hits": {
                "stage_1_pump": 1,
                "stage_2_high_pullback": 0,
                "stage_3_valid_pullback": 0,
                "stage_4_level": 1,
                "stage_5_trade": 1,
            },
        },
    )

    diagnostics_dir = tmp_path / "trade_plots" / "pno_diagnostics"

    def _fake_render_trade_chart(**kwargs):
        assert (diagnostics_dir / "research_context" / "trade_context.csv").exists()
        return [str(Path(kwargs["charts_dir"]) / "BTC_USDT_chart.png")]

    monkeypatch.setattr(commands, "_render_pno_trade_charts_for_symbol", _fake_render_trade_chart)

    commands._plot_pno_diagnostics_for_symbols(
        config=SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path)),
        args=argparse.Namespace(output_dir=None, pno_stage=None, pno_through_stage=None),
        logger=logging.getLogger("test-pno-plot-order"),
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        log_prefix="test",
    )

    assert (diagnostics_dir / "research_context" / "trade_context.csv").exists()
    assert (diagnostics_dir / "BTC_USDT_diagnostics.json").exists()


def test_pno_output_symbol_stem_sanitizes_windows_reserved_chars() -> None:
    assert commands._pno_output_symbol_stem("BTC/USDT:USDT") == "BTC_USDT_USDT"


def test_pno_setup_family_marks_impulse_followthrough_as_core() -> None:
    family, tier = pno_diagnostics._resolve_pno_setup_family(
        {
            "entry_signal_timestamp_ms": 2_100_000,
            "active_high_timestamp_ms": 1_200_000,
            "pump_start_timestamp_ms": 900_000,
            "level_valid_timestamp_ms": 1_500_000,
            "signal_bar_ema_spread_pct": 1.95,
            "signal_bar_ema20_slope_3": 0.24,
            "overhead_resistance_score": 0.41,
            "pump_counterflow_ratio_5m": 0.01,
            "entry_pos": 0.51,
        }
    )

    assert family == "impulse_followthrough"
    assert tier == "core"


def test_pno_setup_family_marks_stale_reclaim_as_mixed() -> None:
    family, tier = pno_diagnostics._resolve_pno_setup_family(
        {
            "entry_signal_timestamp_ms": 20_000_000,
            "active_high_timestamp_ms": 10_000_000,
            "pump_start_timestamp_ms": 8_000_000,
            "level_valid_timestamp_ms": 16_000_000,
            "signal_bar_ema_spread_pct": 0.42,
            "signal_bar_ema20_slope_3": 0.05,
            "overhead_resistance_score": 0.33,
            "pump_counterflow_ratio_5m": 0.0,
            "entry_pos": 0.52,
        }
    )

    assert family == "stale_reclaim"
    assert tier == "mixed"


def test_resolve_pno_category_profiles_splits_close_above_into_core_category2_and_discovery() -> None:
    params = PnoParams(
        symbol="BTC/USDT",
        pno_variant_id="baseline_close",
        entry_confirmation_mode="close_above",
    )
    profiles = resolve_pno_category_profiles(
        params
    )

    discovery_only = resolve_pno_category_profiles(params, category_mode="discovery")
    core_only = resolve_pno_category_profiles(params, category_mode="core")

    assert [profile.category_id for profile in discovery_only] == ["discovery"]
    assert [profile.category_id for profile in core_only] == ["cat_a_core"]
    assert [profile.category_id for profile in profiles] == ["cat_a_core", "cat_b_category_2", "cat_c_category_3"]
    assert profiles[0].params.stage1_max_active_high_upper_wick_share == pytest.approx(0.73)
    assert profiles[1].params.stage1_min_cumulative_quote_volume == pytest.approx(140_000.0)
    assert profiles[1].params.stage1_max_active_high_upper_wick_share == pytest.approx(0.84)
    assert profiles[1].params.stage1_max_counterflow_ratio_5m == pytest.approx(0.09)
    assert profiles[1].params.stage1_min_impulse_atr_pre == pytest.approx(0.95)
    assert profiles[1].params.stage1_min_body_wick_edge == pytest.approx(-0.09)
    assert profiles[1].params.stage1_max_micro_flat_bar_share == pytest.approx(0.70)
    assert profiles[1].params.stage1_max_red_body_share_1m == pytest.approx(0.93)
    assert profiles[1].params.stage3_max_post_high_wick_share == pytest.approx(0.88)
    assert profiles[1].params.stage3_max_post_high_body_overlap_rate == pytest.approx(0.90)
    assert profiles[1].params.stage3_min_post_high_5m_volume_support_fraction == pytest.approx(0.35)
    assert profiles[1].params.stage3_fast_reclaim_min_post_high_5m_volume_support_fraction == pytest.approx(0.0)
    assert profiles[0].params.close_above_min_signal_ema_spread_pct == pytest.approx(0.90)
    assert profiles[1].params.close_above_min_signal_ema_spread_pct == pytest.approx(0.90)
    assert profiles[1].params.close_above_max_entry_pos == pytest.approx(0.90)
    assert profiles[2].params.stage3_min_post_high_5m_volume_support_fraction == pytest.approx(0.35)
    assert profiles[2].params.stage3_fast_reclaim_min_post_high_5m_volume_support_fraction == pytest.approx(0.30)
    assert profiles[2].params.stage3_fast_reclaim_max_pullback_age_bars == 2
    assert profiles[2].params.ideal_like_impulse_enabled is True
    assert profiles[2].params.ideal_like_min_impulse_atr_pre == pytest.approx(14.0)
    assert profiles[2].params.ideal_like_min_peak_bar_tr_atr_pre == pytest.approx(6.0)
    assert profiles[2].params.ideal_like_min_volume_ratio_start == pytest.approx(7.0)
    assert profiles[2].params.ideal_like_relaxed_level_maturity_fraction == pytest.approx(0.05)
    assert profiles[2].params.ideal_like_ignore_decay_invalidation is True
    assert profiles[2].params.min_entry_rr == pytest.approx(3.0)
    assert discovery_only[0].params == params
    assert describe_pno_category_profile_set(params) == "cat_a_core+cat_b_category_2+cat_c_category_3"
    assert describe_pno_category_profile_set(params, category_mode="discovery") == "discovery"
    assert describe_pno_category_profile_set(params, category_mode="core") == "cat_a_core"


def test_pno_stage_reviews_export_rejected_events_even_without_review_charts(tmp_path: Path) -> None:
    stage_id = pno_diagnostics.PNO_STAGE_1_PUMP
    pno_diagnostics._export_pno_stage_reviews(
        diagnostics_dir=tmp_path,
        symbol_frames={},
        stage_rows_by_stage={},
        stage_rejections_by_stage={
            stage_id: {
                "flow_hold_not_confirmed": [
                    {
                        "symbol": "TEST/USDT:USDT",
                        "timestamp": 1_000,
                        "reason": "flow_hold_not_confirmed",
                    }
                ]
            }
        },
        selected_stage_ids=(stage_id,),
        render_charts=False,
    )

    rejected_events_path = (
        tmp_path
        / "stage_reviews"
        / stage_id
        / "rejected"
        / "flow_hold_not_confirmed"
        / "events.csv"
    )
    assert rejected_events_path.exists()
    exported = pd.read_csv(rejected_events_path)
    assert exported["symbol"].tolist() == ["TEST/USDT:USDT"]
    manifest = pd.read_csv(tmp_path / "stage_reviews" / "manifest.csv")
    assert int(manifest.loc[0, "rejected_count"]) == 1
    assert int(manifest.loc[0, "rejected_review_count"]) == 0


def test_run_backtest_explicit_plot_and_diagnostics_flags_are_resolved() -> None:
    args = argparse.Namespace(plot_rejected="true", collect_diagnostics="false", plot=None, light_run=None)

    assert commands._resolve_plot_rejected_flag(args) is True
    assert commands._resolve_collect_diagnostics_flag(args) is False


def test_run_backtest_legacy_plot_and_light_run_remain_aliases() -> None:
    args = argparse.Namespace(plot_rejected=None, collect_diagnostics=None, plot="true", light_run="true")

    assert commands._resolve_plot_rejected_flag(args) is True
    assert commands._resolve_collect_diagnostics_flag(args) is False


def test_pno_strategy_accepts_runtime_diagnostic_flags_for_multi_tf(monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params = PnoParams(
        symbol="BTC/USDT",
        pno_variant_id="baseline_close",
        entry_confirmation_mode="close_above",
    )
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000, 180_000],
            "open": [1.0, 1.1, 1.2],
            "high": [1.1, 1.2, 1.3],
            "low": [0.9, 1.0, 1.1],
            "close": [1.05, 1.15, 1.25],
            "volume": [10.0, 11.0, 12.0],
        }
    )
    mtf_frames = SymbolMtfFrames(
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        levels_frame=frame,
        entry_frame=frame,
    )
    captured_contexts: list[dict[str, object]] = []

    def _fake_generate_events_multi_tf(**kwargs):
        captured_contexts.append(kwargs)
        return []

    monkeypatch.setattr(strategy._engine, "generate_events_multi_tf", _fake_generate_events_multi_tf)

    trades = strategy.generate_events_multi_tf(
        mtf_frames=mtf_frames,
        params=params,
        collect_diagnostics=True,
        collect_stage_metrics=True,
    )

    assert trades == []
    assert captured_contexts
    assert captured_contexts[0]["collect_diagnostics"] is True
    assert captured_contexts[0]["collect_stage_metrics"] is True


def test_pno_strategy_merges_category_profiles_and_dedups_trade_entries(monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    params = PnoParams(
        symbol="BTC/USDT",
        pno_variant_id="baseline_close",
        entry_confirmation_mode="close_above",
    )
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000, 180_000],
            "open": [1.0, 1.1, 1.2],
            "high": [1.1, 1.2, 1.3],
            "low": [0.9, 1.0, 1.1],
            "close": [1.05, 1.15, 1.25],
            "volume": [10.0, 11.0, 12.0],
        }
    )
    mtf_frames = SymbolMtfFrames(
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        levels_frame=frame,
        entry_frame=frame,
    )
    core_trade = TradeResult(
        entry_price=Price(1.2),
        exit_price=Price(1.35),
        entry_timestamp_ms=180_000,
        exit_timestamp_ms=240_000,
        result_type=TradeResultType.TP2,
        pnl=8.0,
        pnl_percent=Percentage(2.0),
        metadata={"symbol": "BTC/USDT", "level": 1.18, "entry_price_actual": 1.2},
    )
    duplicate_category2_trade = TradeResult(
        entry_price=Price(1.2),
        exit_price=Price(1.32),
        entry_timestamp_ms=180_000,
        exit_timestamp_ms=300_000,
        result_type=TradeResultType.TP1_BE,
        pnl=3.0,
        pnl_percent=Percentage(1.0),
        metadata={"symbol": "BTC/USDT", "level": 1.18, "entry_price_actual": 1.2},
    )
    discovery_trade = TradeResult(
        entry_price=Price(1.28),
        exit_price=Price(1.42),
        entry_timestamp_ms=300_000,
        exit_timestamp_ms=360_000,
        result_type=TradeResultType.TP2,
        pnl=7.0,
        pnl_percent=Percentage(1.5),
        metadata={"symbol": "BTC/USDT", "level": 1.25, "entry_price_actual": 1.28},
    )
    diagnostics_queue = [
        {
            "trades_generated": 1,
            "stage_events": [{"stage_id": "stage_1_pump", "timestamp_ms": 120_000}],
            "stage_rejections": [{"stage_id": "stage_4_level", "reason": "no_level_found", "timestamp_ms": 150_000}],
            "stage_hits": {"stage_1_pump": 1, "stage_5_trade": 1},
            "context": {"symbol": "BTC/USDT", "stage_order": ["stage_1_pump", "stage_5_trade"]},
        },
        {
            "trades_generated": 2,
            "stage_events": [{"stage_id": "stage_3_valid_pullback", "timestamp_ms": 240_000}],
            "stage_rejections": [],
            "stage_hits": {"stage_3_valid_pullback": 1, "stage_5_trade": 1},
            "context": {"symbol": "BTC/USDT", "stage_order": ["stage_1_pump", "stage_5_trade"]},
        },
        {
            "trades_generated": 1,
            "stage_events": [{"stage_id": "stage_4_level", "timestamp_ms": 300_000}],
            "stage_rejections": [],
            "stage_hits": {"stage_4_level": 1, "stage_5_trade": 1},
            "context": {"symbol": "BTC/USDT", "stage_order": ["stage_1_pump", "stage_5_trade"]},
        },
    ]
    profile_calls: list[str] = []

    def _fake_generate_events_multi_tf(*, levels_frame, entry_frame, params, **_context):
        del levels_frame, entry_frame
        profile_calls.append(params.pno_variant_id)
        if params.pno_variant_id.endswith("__cat_a_core"):
            return [core_trade]
        if params.pno_variant_id.endswith("__cat_b_category_2"):
            return [duplicate_category2_trade]
        return [discovery_trade]

    monkeypatch.setattr(strategy._engine, "generate_events_multi_tf", _fake_generate_events_multi_tf)
    monkeypatch.setattr(strategy._engine, "consume_last_generation_diagnostics", lambda: diagnostics_queue.pop(0))

    trades = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)

    assert profile_calls == [
        "baseline_close__cat_a_core",
        "baseline_close__cat_b_category_2",
        "baseline_close__cat_c_category_3",
    ]
    assert len(trades) == 2
    assert trades[0].metadata["pno_category_id"] == "cat_a_core"
    assert trades[1].metadata["pno_category_id"] == "cat_c_category_3"
    diagnostics = strategy.consume_last_generation_diagnostics()
    assert diagnostics["trades_generated"] == 2
    assert diagnostics["context"]["category_profiles_run"] == ["cat_a_core", "cat_b_category_2", "cat_c_category_3"]
    assert diagnostics["stage_hits"]["stage_5_trade"] == 3
    assert {event["pno_category_id"] for event in diagnostics["stage_events"]} == {
        "cat_a_core",
        "cat_b_category_2",
        "cat_c_category_3",
    }


def test_pno_compact_geometry_adjustment_rewards_compact_setups() -> None:
    compact = PnoEngine._resolve_compact_geometry_adjustment(
        entry_plan=10.0,
        sl_plan=9.6,
        tp1=10.35,
        tp2=10.65,
    )
    stretched = PnoEngine._resolve_compact_geometry_adjustment(
        entry_plan=10.0,
        sl_plan=9.6,
        tp1=10.95,
        tp2=11.45,
    )

    assert compact > 0
    assert stretched < 0
    assert compact > stretched


def test_pno_overhead_quality_adjustment_prefers_clean_air() -> None:
    clean = PnoEngine._resolve_overhead_quality_adjustment(
        overhead_score=0.46,
        red_count=2,
    )
    heavy = PnoEngine._resolve_overhead_quality_adjustment(
        overhead_score=0.74,
        red_count=5,
    )

    assert clean > 0
    assert heavy < 0
    assert clean > heavy


def test_pno_setup_freshness_adjustment_penalizes_stale_reclaim() -> None:
    fresh = PnoEngine._resolve_setup_freshness_adjustment(
        current_timestamp_ms=25 * 60_000,
        pump_start_timestamp_ms=0,
        active_high_timestamp_ms=10 * 60_000,
        level_valid_timestamp_ms=18 * 60_000,
    )
    stale = PnoEngine._resolve_setup_freshness_adjustment(
        current_timestamp_ms=220 * 60_000,
        pump_start_timestamp_ms=0,
        active_high_timestamp_ms=45 * 60_000,
        level_valid_timestamp_ms=170 * 60_000,
    )

    assert fresh > stale


def test_pno_impulse_quality_adjustment_penalizes_choppy_precursor() -> None:
    clean = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.3,
        reference_high=1.3,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.3,
        reference_leg_size=0.3,
        hold_floor=1.1,
        pump_range_5m=0.3,
        pre_pump_ema_crosses_1h=1,
        pre_pump_barcode_fraction_1h=0.12,
        pump_volume_ratio_start=12.0,
        pump_volume_ratio_continue=10.0,
        pump_path_efficiency=0.58,
        pump_counterflow_ratio_5m=0.01,
    )
    choppy = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.3,
        reference_high=1.3,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.3,
        reference_leg_size=0.3,
        hold_floor=1.1,
        pump_range_5m=0.3,
        pre_pump_ema_crosses_1h=5,
        pre_pump_barcode_fraction_1h=0.58,
        pump_volume_ratio_start=12.0,
        pump_volume_ratio_continue=10.0,
        pump_path_efficiency=0.20,
        pump_counterflow_ratio_5m=0.09,
    )

    assert PnoEngine._resolve_impulse_quality_adjustment(stage1=clean) > PnoEngine._resolve_impulse_quality_adjustment(stage1=choppy)


def test_pno_close_trigger_filter_rejects_stale_supply_heavy_setup() -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT")
    stage1 = _build_test_stage1_context()
    stage3 = Stage3Context(
        active_high_idx=0,
        active_high_timestamp=60_000,
        active_high=10.5,
        pullback_start_idx=0,
        pullback_low_idx=0,
        pullback_low_timestamp=60_000,
        pullback_low=9.5,
        pullback_depth=0.5,
        pullback_age_bars=2,
        validation_timestamp=60_000,
        post_high_wick_share=0.20,
    )
    stage4 = _build_test_stage4_context(
        entry_pos=0.80,
        touches=5,
        level_maturity_fraction=0.92,
        overhead_resistance_score=0.80,
        overhead_red_count=5,
        level_life_ema_spread_growth_share=0.10,
    )

    reason = engine._resolve_close_trigger_filter_reason(
        params=params,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        signal_context={"signal_bar_volume_vs_recent": 1.2},
    )

    assert reason == "close_above_level_too_stale"


def test_pno_rebuild_stage4_scores_blocks_extremely_choppy_precursor(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=10.0)
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.4, 10.45],
        lows=[9.8, 9.95],
        closes=[10.3, 10.35],
        ema20=[9.9, 10.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        reference_high=10.45,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.9,
        leg_size=0.55,
        reference_leg_size=0.55,
        hold_floor=10.05,
        pump_range_5m=0.55,
        pre_pump_ema_crosses_1h=5,
        pre_pump_barcode_fraction_1h=0.55,
        pump_volume_ratio_start=9.0,
        pump_volume_ratio_continue=5.0,
        pump_path_efficiency=0.20,
        pump_counterflow_ratio_5m=0.09,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=10.0,
        pullback_depth=0.45,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.45,
        active_high_timestamp=60_000,
        pullback_low=10.0,
        pullback_low_timestamp=120_000,
        pullback_depth=0.45,
        level=10.15,
        level_valid_timestamp=120_000,
        level_valid_idx=2,
        cluster_indices=(1, 2),
        cluster_prices=(10.14, 10.15),
        cluster_first_idx=1,
        cluster_last_idx=2,
        touches=2,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.30,
            "penalty": 0,
            "red_body_share": 0.0,
            "red_count": 0,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 9.95)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)
    monkeypatch.setattr(engine, "_has_prior_upper_tf_atr_reclaim_above_level", lambda **_kwargs: True)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=3,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block is True
    assert rebuilt.hard_block_reason == "precursor_too_choppy"


def test_pno_rebuild_stage4_scores_blocks_late_entry_zone(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=10.0)
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.4, 10.45],
        lows=[9.8, 9.95],
        closes=[10.3, 10.35],
        ema20=[9.9, 10.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        reference_high=10.45,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.9,
        leg_size=0.55,
        reference_leg_size=0.55,
        hold_floor=10.05,
        pump_range_5m=0.55,
        pre_pump_ema_crosses_1h=1,
        pre_pump_barcode_fraction_1h=0.0,
        pump_volume_ratio_start=9.0,
        pump_volume_ratio_continue=5.0,
        pump_path_efficiency=0.55,
        pump_counterflow_ratio_5m=0.01,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=10.0,
        pullback_depth=0.45,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.45,
        active_high_timestamp=60_000,
        pullback_low=10.0,
        pullback_low_timestamp=120_000,
        pullback_depth=0.45,
        level=10.34,
        level_valid_timestamp=120_000,
        level_valid_idx=2,
        cluster_indices=(1, 2),
        cluster_prices=(10.33, 10.34),
        cluster_first_idx=1,
        cluster_last_idx=2,
        touches=2,
        level_maturity_fraction=0.5,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.30,
            "penalty": 0,
            "red_body_share": 0.0,
            "red_count": 0,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 9.95)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)
    monkeypatch.setattr(engine, "_has_prior_upper_tf_atr_reclaim_above_level", lambda **_kwargs: True)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=3,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block is True
    assert rebuilt.hard_block_reason == "entry_pos_too_high"


def test_pno_rebuild_stage4_scores_sets_stop_plan_to_last_red_extremum(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=10.0)
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.4, 10.45],
        lows=[9.8, 9.95],
        closes=[10.3, 10.35],
        ema20=[9.9, 10.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        reference_high=10.45,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.9,
        leg_size=0.55,
        reference_leg_size=0.55,
        hold_floor=10.05,
        pump_range_5m=0.55,
        pre_pump_ema_crosses_1h=1,
        pre_pump_barcode_fraction_1h=0.0,
        pump_volume_ratio_start=9.0,
        pump_volume_ratio_continue=5.0,
        pump_path_efficiency=0.55,
        pump_counterflow_ratio_5m=0.01,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=9.9,
        pullback_depth=0.45,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.45,
        active_high_timestamp=60_000,
        pullback_low=9.9,
        pullback_low_timestamp=120_000,
        pullback_depth=0.45,
        level=10.12,
        level_valid_timestamp=120_000,
        level_valid_idx=2,
        cluster_indices=(1,),
        cluster_prices=(10.10,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        touches=1,
        level_maturity_fraction=0.5,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.30,
            "penalty": 0,
            "red_body_share": 0.0,
            "red_count": 0,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 10.01)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=3,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT", min_entry_rr=0.0),
    )

    assert rebuilt.low_last_red_plan == pytest.approx(10.01)
    assert rebuilt.sl_plan == pytest.approx(10.01)


def test_resolve_low_last_red_plan_ignores_reclaimed_red_bar_above_level() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=10.0)
    one.red[:] = np.array([False, True, True, False], dtype=bool)
    one.highs[:] = np.array([10.0, 10.0, 10.0, 10.0], dtype=np.float64)
    one.lows[:] = np.array([9.9, 9.95, 10.12, 10.05], dtype=np.float64)
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=10.3,
        reference_high=10.3,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.8,
        leg_size=0.5,
        reference_leg_size=0.5,
        hold_floor=10.0,
    )
    stage3 = Stage3Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=10.3,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=9.9,
        pullback_depth=0.4,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(level=10.1)

    stop_level = engine._resolve_low_last_red_plan(
        one=one,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        idx=2,
    )

    assert stop_level == pytest.approx(9.95)


def test_resolve_low_last_red_plan_uses_level_life_before_first_cross_only() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000, 240_000], price=10.0)
    one.red[:] = np.array([False, True, False, True, False], dtype=bool)
    one.highs[:] = np.array([10.0, 10.0, 10.05, 10.08, 10.25], dtype=np.float64)
    one.lows[:] = np.array([9.9, 9.96, 10.05, 10.12, 10.1], dtype=np.float64)
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=10.4,
        reference_high=10.4,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.8,
        leg_size=0.6,
        reference_leg_size=0.6,
        hold_floor=10.0,
    )
    stage3 = Stage3Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=10.4,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=9.9,
        pullback_depth=0.5,
        pullback_age_bars=2,
        validation_timestamp=240_000,
    )
    stage4 = _build_test_stage4_context(
        level=10.1,
        cluster_first_idx=1,
        level_valid_idx=3,
        level_valid_timestamp=180_000,
    )

    stop_level = engine._resolve_low_last_red_plan(
        one=one,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        idx=4,
        confirmation_mode="close_above",
    )

    assert stop_level == pytest.approx(9.96)


def test_rebuild_stage4_scores_blocks_level_after_prior_upper_tf_atr_reclaim(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(
        timestamps_ms=[0, 60_000, 120_000, 180_000, 240_000, 300_000, 360_000, 420_000, 480_000, 540_000],
        price=10.0,
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.1, 10.2],
        highs=[10.2, 10.45, 10.3],
        lows=[9.9, 10.0, 10.15],
        closes=[10.1, 10.22, 10.25],
        ema20=[9.9, 10.0, 10.1],
    )
    five.atr_pre_14[:] = np.array([0.1, 0.1, 0.1], dtype=np.float64)
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.6,
        reference_high=10.6,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.8,
        leg_size=0.8,
        reference_leg_size=0.8,
        hold_floor=10.0,
        pump_range_5m=0.8,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.6,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=10.0,
        pullback_depth=0.6,
        pullback_age_bars=2,
        validation_timestamp=540_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.6,
        active_high_timestamp=60_000,
        pullback_low=10.0,
        pullback_low_timestamp=60_000,
        pullback_depth=0.6,
        level=10.2,
        cluster_indices=(1,),
        cluster_prices=(10.2,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        level_valid_idx=2,
        level_valid_timestamp=120_000,
        touches=1,
        entry_pos=0.65,
        level_maturity_fraction=0.5,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.2,
            "penalty": 0,
            "red_body_share": 0.0,
            "red_count": 0,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 9.95)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)
    monkeypatch.setattr(engine, "_has_prior_upper_tf_atr_reclaim_above_level", lambda **_kwargs: True)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=9,
        five_idx=2,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block is True
    assert rebuilt.hard_block_reason == "level_already_reclaimed_too_far"


def test_shallow_pre_trigger_wick_does_not_invalidate_armed_entry() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000], price=1.0)
    one.closes[:] = np.array([1.0, 1.001], dtype=np.float64)
    one.lows[:] = np.array([1.0, 0.969], dtype=np.float64)
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=0,
            pump_start_5m_idx=0,
            pump_start_timestamp=0,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=0,
            active_high=1.10,
            reference_high=1.10,
            leg_start_idx=0,
            leg_start_timestamp=0,
            leg_start=0.90,
            leg_size=0.20,
            reference_leg_size=0.20,
            hold_floor=0.96,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=0,
            active_high=1.10,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=0,
            pullback_low=0.95,
            pullback_depth=0.15,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=_build_test_stage4_context(
            active_high=1.10,
            pullback_low=0.95,
            pullback_depth=0.15,
            entry_plan=1.00,
            sl_plan=0.97,
            low_last_red_plan=0.97,
            level=0.99,
        ),
    )

    invalidated = engine._is_armed_entry_invalidated_before_trigger(
        one=one,
        idx=1,
        armed=armed,
    )

    assert invalidated is False


def test_close_above_pre_signal_decay_blocks_prior_fully_accepted_level() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.8, 10.0, 10.12, 10.05],
                "high": [9.9, 10.15, 10.20, 10.18],
                "low": [9.7, 9.95, 10.05, 10.0],
                "close": [9.85, 10.08, 10.16, 10.1],
                "volume": [10.0, 12.0, 14.0, 11.0],
            }
        )
    )
    stage4 = _build_test_stage4_context(
        level=10.0,
        tp1=10.4,
        level_valid_idx=0,
        level_valid_timestamp=60_000,
    )

    reason = engine._resolve_close_above_pre_signal_decay_reason(
        one=one,
        signal_idx=3,
        params=PnoParams(symbol="TEST/USDT"),
        stage4=stage4,
    )

    assert reason == "level_already_accepted_before_signal"


def test_close_above_pre_signal_decay_blocks_prior_tp1_tag() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.8, 10.0, 10.05, 10.02],
                "high": [9.9, 10.45, 10.2, 10.18],
                "low": [9.7, 9.95, 10.0, 10.0],
                "close": [9.85, 10.02, 10.12, 10.1],
                "volume": [10.0, 12.0, 14.0, 11.0],
            }
        )
    )
    stage4 = _build_test_stage4_context(
        level=10.0,
        tp1=10.4,
        level_valid_idx=0,
        level_valid_timestamp=60_000,
    )

    reason = engine._resolve_close_above_pre_signal_decay_reason(
        one=one,
        signal_idx=3,
        params=PnoParams(symbol="TEST/USDT"),
        stage4=stage4,
    )

    assert reason == "tp1_already_tagged_before_signal"


def test_close_above_pre_signal_decay_blocks_late_first_cross() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000, 360_000, 420_000],
                "open": [9.80, 9.86, 9.89, 9.91, 9.94, 9.95, 10.01],
                "high": [9.90, 9.94, 9.96, 9.97, 9.98, 10.03, 10.08],
                "low": [9.74, 9.83, 9.86, 9.88, 9.91, 9.93, 9.98],
                "close": [9.85, 9.90, 9.92, 9.95, 9.97, 10.01, 10.05],
                "volume": [10.0, 10.0, 11.0, 10.0, 10.0, 12.0, 13.0],
            }
        )
    )
    stage4 = _build_test_stage4_context(level=10.0, tp1=10.3, level_valid_idx=0, level_valid_timestamp=60_000)

    reason = engine._resolve_close_above_pre_signal_decay_reason(
        one=one,
        signal_idx=6,
        params=PnoParams(symbol="TEST/USDT", close_above_max_level_cross_bars=4),
        stage4=stage4,
    )

    assert reason == "level_crossed_too_late"


def test_close_above_pre_signal_decay_blocks_illiquid_level_life() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000, 360_000],
                "open": [9.80, 9.88, 9.90, 9.92, 9.93, 10.01],
                "high": [9.95, 9.98, 9.99, 10.00, 9.99, 10.05],
                "low": [9.78, 9.86, 9.89, 9.90, 9.91, 9.98],
                "close": [9.90, 9.91, 9.905, 9.915, 9.91, 10.02],
                "volume": [1.0, 1.0, 1.0, 1.0, 1.0, 15.0],
            }
        )
    )
    one.quote_volume[:] = np.array([500.0, 600.0, 550.0, 650.0, 700.0, 18_000.0], dtype=np.float64)
    one.frame.loc[:, "quote_volume"] = one.quote_volume
    stage4 = _build_test_stage4_context(level=10.0, tp1=10.3, level_valid_idx=0, level_valid_timestamp=60_000)

    reason = engine._resolve_close_above_pre_signal_decay_reason(
        one=one,
        signal_idx=5,
        params=PnoParams(symbol="TEST/USDT", close_above_min_level_life_quote_volume_median=10_000.0),
        stage4=stage4,
    )

    assert reason == "level_life_too_illiquid"


def test_pno_rebuild_stage4_scores_blocks_heavy_overhead(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=10.0)
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.4, 10.45],
        lows=[9.8, 9.95],
        closes=[10.3, 10.35],
        ema20=[9.9, 10.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        reference_high=10.45,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.9,
        leg_size=0.55,
        reference_leg_size=0.55,
        hold_floor=10.05,
        pump_range_5m=0.55,
        pre_pump_ema_crosses_1h=1,
        pre_pump_barcode_fraction_1h=0.0,
        pump_volume_ratio_start=9.0,
        pump_volume_ratio_continue=5.0,
        pump_path_efficiency=0.55,
        pump_counterflow_ratio_5m=0.01,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=10.0,
        pullback_depth=0.45,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.45,
        active_high_timestamp=60_000,
        pullback_low=10.0,
        pullback_low_timestamp=120_000,
        pullback_depth=0.45,
        level=10.18,
        level_valid_timestamp=120_000,
        level_valid_idx=2,
        cluster_indices=(1, 2),
        cluster_prices=(10.17, 10.18),
        cluster_first_idx=1,
        cluster_last_idx=2,
        touches=2,
        level_maturity_fraction=0.5,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 1.12,
            "penalty": 0,
            "red_body_share": 0.4,
            "red_count": 8,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 9.95)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=3,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block is True
    assert rebuilt.hard_block_reason == "overhead_too_heavy"


def test_pno_rebuild_stage4_scores_allows_fresh_moderate_overhead_setup(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=10.0)
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.4, 10.45],
        lows=[9.8, 9.95],
        closes=[10.3, 10.35],
        ema20=[9.9, 10.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        reference_high=10.45,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.9,
        leg_size=0.55,
        reference_leg_size=0.55,
        hold_floor=10.05,
        pump_range_5m=0.55,
        pre_pump_ema_crosses_1h=1,
        pre_pump_barcode_fraction_1h=0.0,
        pump_volume_ratio_start=9.0,
        pump_volume_ratio_continue=5.0,
        pump_path_efficiency=0.55,
        pump_counterflow_ratio_5m=0.01,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=9.9,
        pullback_depth=0.45,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.45,
        active_high_timestamp=60_000,
        pullback_low=9.9,
        pullback_low_timestamp=120_000,
        pullback_depth=0.45,
        level=10.12,
        level_valid_timestamp=120_000,
        level_valid_idx=2,
        cluster_indices=(1,),
        cluster_prices=(10.10,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        touches=1,
        level_maturity_fraction=0.5,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.92,
            "penalty": 0,
            "red_body_share": 0.4,
            "red_count": 4,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 9.95)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=3,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block is False


def test_pno_rebuild_stage4_scores_blocks_single_touch_level_already_reclaimed(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000, 240_000], price=10.0)
    one.opens[:] = np.array([9.9, 10.05, 10.22, 10.24, 10.26], dtype="float64")
    one.highs[:] = np.array([10.0, 10.2, 10.28, 10.3, 10.32], dtype="float64")
    one.lows[:] = np.array([9.85, 10.0, 10.21, 10.23, 10.25], dtype="float64")
    one.closes[:] = np.array([9.98, 10.18, 10.26, 10.28, 10.29], dtype="float64")
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.3, 10.35],
        lows=[9.8, 10.0],
        closes=[10.2, 10.3],
        ema20=[9.9, 10.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        reference_high=10.45,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.9,
        leg_size=0.55,
        reference_leg_size=0.55,
        hold_floor=10.05,
        pump_range_5m=0.55,
        pre_pump_ema_crosses_1h=1,
        pre_pump_barcode_fraction_1h=0.0,
        pump_volume_ratio_start=9.0,
        pump_volume_ratio_continue=5.0,
        pump_path_efficiency=0.55,
        pump_counterflow_ratio_5m=0.01,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=10.0,
        pullback_depth=0.45,
        pullback_age_bars=3,
        validation_timestamp=240_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.45,
        active_high_timestamp=60_000,
        pullback_low=10.0,
        pullback_low_timestamp=60_000,
        pullback_depth=0.45,
        level=10.18,
        entry_pos=0.68,
        level_valid_timestamp=120_000,
        level_valid_idx=2,
        cluster_indices=(1,),
        cluster_prices=(10.18,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        touches=1,
        level_maturity_fraction=0.5,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.35,
            "penalty": 0,
            "red_body_share": 0.2,
            "red_count": 1,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 10.1)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)
    monkeypatch.setattr(engine, "_has_stale_reclaim_above_level", lambda **_kwargs: True)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=4,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block is True
    assert rebuilt.hard_block_reason == "level_already_reclaimed_too_far"


def test_pno_rebuild_stage4_scores_does_not_block_fresh_single_touch_level_too_early(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=10.0)
    one.opens[:] = np.array([9.9, 10.05, 10.22, 10.24], dtype="float64")
    one.highs[:] = np.array([10.0, 10.2, 10.28, 10.3], dtype="float64")
    one.lows[:] = np.array([9.85, 10.0, 10.21, 10.23], dtype="float64")
    one.closes[:] = np.array([9.98, 10.18, 10.26, 10.28], dtype="float64")
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.3, 10.35],
        lows=[9.8, 10.0],
        closes=[10.2, 10.3],
        ema20=[9.9, 10.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.3,
        reference_high=10.3,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.8,
        leg_size=0.5,
        reference_leg_size=0.5,
        hold_floor=10.0,
        pump_range_5m=0.5,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.3,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=10.0,
        pullback_depth=0.3,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.3,
        active_high_timestamp=60_000,
        pullback_low=10.0,
        pullback_low_timestamp=60_000,
        pullback_depth=0.3,
        level=10.2,
        cluster_indices=(1,),
        cluster_prices=(10.2,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        level_valid_idx=2,
        level_valid_timestamp=120_000,
        touches=1,
        entry_pos=0.70,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.35,
            "penalty": 0,
            "red_body_share": 0.2,
            "red_count": 1,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 9.95)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.9)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=3,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block_reason != "level_already_reclaimed_too_far"


def test_pno_rebuild_stage4_scores_blocks_level_after_deep_stale_break_below_pullback(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000, 240_000, 300_000, 360_000], price=10.0)
    one.opens[:] = np.array([10.0, 10.1, 10.0, 9.72, 9.68, 9.62, 9.60], dtype="float64")
    one.highs[:] = np.array([10.2, 10.3, 10.2, 9.78, 9.72, 9.68, 9.64], dtype="float64")
    one.lows[:] = np.array([9.95, 10.0, 9.95, 9.60, 9.54, 9.49, 9.52], dtype="float64")
    one.closes[:] = np.array([10.1, 10.2, 10.1, 9.64, 9.58, 9.53, 9.56], dtype="float64")
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    five = _build_test_five_frame(
        opens=[10.0, 10.1],
        highs=[10.4, 10.3],
        lows=[9.8, 9.5],
        closes=[10.3, 9.7],
        ema20=[9.9, 9.4],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        reference_high=10.45,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.9,
        leg_size=0.55,
        reference_leg_size=0.55,
        hold_floor=10.05,
        pump_range_5m=0.55,
        pre_pump_ema_crosses_1h=1,
        pre_pump_barcode_fraction_1h=0.0,
        pump_volume_ratio_start=9.0,
        pump_volume_ratio_continue=5.0,
        pump_path_efficiency=0.55,
        pump_counterflow_ratio_5m=0.01,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=9.8,
        pullback_depth=0.8,
        pullback_age_bars=3,
        validation_timestamp=240_000,
    )
    stage4 = _build_test_stage4_context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=10.45,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=9.8,
        pullback_depth=0.8,
        level=10.02,
        level_valid_timestamp=120_000,
        level_valid_idx=2,
        cluster_indices=(2,),
        cluster_prices=(10.02,),
        cluster_first_idx=2,
        cluster_last_idx=2,
        touches=1,
        level_maturity_fraction=0.5,
    )

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **_kwargs: [])
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "_resolve_pullback_base_profile",
        lambda **_kwargs: {
            "bonus": 0,
            "start_idx": None,
            "end_idx": None,
            "low": None,
            "high": None,
            "quality": 0.0,
            "left_vacuum": 0.0,
        },
    )
    monkeypatch.setattr(
        engine,
        "_resolve_overhead_resistance_profile",
        lambda **_kwargs: {
            "score": 0.25,
            "penalty": 0,
            "red_body_share": 0.1,
            "red_count": 0,
            "dominant_timestamp": None,
            "dominant_high": None,
            "dominant_body": 0.0,
        },
    )
    monkeypatch.setattr(engine, "_resolve_untested_high_penalty", lambda **_kwargs: 0)
    monkeypatch.setattr(engine, "_resolve_low_last_red_plan", lambda **_kwargs: 9.7)
    monkeypatch.setattr(engine, "_resolve_tp2", lambda **_kwargs: 10.8)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=6,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rebuilt.hard_block is True
    assert rebuilt.hard_block_reason == "traded_too_far_below_pullback_after_level"


def test_pno_close_trigger_score_adjustment_rewards_clean_signal_and_penalizes_heavy_overhead() -> None:
    engine = PnoEngine()
    clean_stage4 = _build_test_stage4_context(
        overhead_resistance_score=0.46,
        overhead_red_count=2,
    )
    heavy_stage4 = _build_test_stage4_context(
        overhead_resistance_score=0.74,
        overhead_red_count=5,
    )

    clean_adjustments = engine._resolve_close_trigger_score_adjustment(
        stage4=clean_stage4,
        signal_context={
            "signal_bar_body_share": 0.42,
            "signal_bar_close_position": 0.93,
            "signal_bar_volume_vs_recent": 3.2,
            "signal_close_clearance_pct": 0.31,
        },
    )
    heavy_adjustments = engine._resolve_close_trigger_score_adjustment(
        stage4=heavy_stage4,
        signal_context={
            "signal_bar_body_share": 0.28,
            "signal_bar_close_position": 0.70,
            "signal_bar_volume_vs_recent": 0.60,
            "signal_close_clearance_pct": 0.05,
        },
    )

    assert clean_adjustments[0] > heavy_adjustments[0]
    assert clean_adjustments[1] > heavy_adjustments[1]
    assert clean_adjustments[2] > heavy_adjustments[2]
    assert clean_adjustments[3] > heavy_adjustments[3]


def test_build_filtered_pno_stage_rejections_keeps_stage2_full_and_trims_stage1() -> None:
    filtered = commands._build_filtered_pno_stage_rejections(
        stage_rejections_by_stage={
            "stage_1_pump": {
                "volume_ratio_start_too_small": [
                    {
                        "symbol": "BTC/USDT",
                        "timestamp_ms": 60_000,
                        "reason": "volume_ratio_start_too_small",
                        "pump_volume_ratio_start": 2.95,
                        "stage1_min_volume_ratio_start": 3.0,
                    },
                    {
                        "symbol": "BTC/USDT",
                        "timestamp_ms": 120_000,
                        "reason": "volume_ratio_start_too_small",
                        "pump_volume_ratio_start": 0.8,
                        "stage1_min_volume_ratio_start": 3.0,
                    },
                ]
            },
            "stage_2_high_pullback": {
                "new_main_high_before_pullback": [
                    {"symbol": "BTC/USDT", "timestamp_ms": 180_000, "reason": "new_main_high_before_pullback"},
                    {"symbol": "ETH/USDT", "timestamp_ms": 240_000, "reason": "new_main_high_before_pullback"},
                ]
            },
        },
        selected_stage_ids=("stage_1_pump", "stage_2_high_pullback"),
    )

    assert len(filtered["stage_1_pump"]["volume_ratio_start_too_small"]) == 1
    assert len(filtered["stage_2_high_pullback"]["new_main_high_before_pullback"]) == 2


def test_build_stage5_review_rejections_from_stage4_adds_only_crossed_levels() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000, 180_000],
            "open": [1.0, 1.0, 1.0],
            "high": [1.01, 1.08, 1.03],
            "low": [0.99, 0.98, 0.99],
            "close": [1.0, 1.02, 1.01],
            "volume": [10.0, 12.0, 9.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }
    stage_rows_by_stage = {stage_id: [] for stage_id in ("stage_1_pump", "stage_2_high_pullback", "stage_3_valid_pullback", "stage_4_level", "stage_5_trade")}
    stage_rejections_by_stage = {stage_id: {} for stage_id in ("stage_1_pump", "stage_2_high_pullback", "stage_3_valid_pullback", "stage_4_level", "stage_5_trade")}
    stage_rows_by_stage["stage_4_level"] = [
        {
            "symbol": "BTC/USDT",
            "stage_id": "stage_4_level",
            "timestamp_ms": 60_000,
            "level_valid_timestamp_ms": 60_000,
            "active_high_timestamp_ms": 30_000,
            "pullback_low_timestamp_ms": 45_000,
            "level_first_local_high_timestamp_ms": 60_000,
            "level_last_local_high_timestamp_ms": 60_000,
            "level": 1.05,
            "score": 60.0,
            "active_high": 1.2,
            "pullback_low": 0.95,
            "stage1_hold_price": 0.98,
        },
        {
            "symbol": "BTC/USDT",
            "stage_id": "stage_4_level",
            "timestamp_ms": 60_000,
            "level_valid_timestamp_ms": 60_000,
            "active_high_timestamp_ms": 30_000,
            "pullback_low_timestamp_ms": 45_000,
            "level_first_local_high_timestamp_ms": 90_000,
            "level_last_local_high_timestamp_ms": 90_000,
            "level": 1.20,
            "score": 80.0,
            "active_high": 1.3,
            "pullback_low": 0.95,
            "stage1_hold_price": 0.98,
        },
    ]

    commands._build_stage5_review_rejections_from_stage4(
        symbol_frames=symbol_frames,
        stage_rows_by_stage=stage_rows_by_stage,
        stage_rejections_by_stage=stage_rejections_by_stage,
        trade_rows=[],
        min_score=70.0,
    )

    rejected = stage_rejections_by_stage["stage_5_trade"]["level_crossed_below_min_score"]
    assert len(rejected) == 1
    assert rejected[0]["timestamp_ms"] == 120_000
    assert rejected[0]["entry_signal_timestamp_ms"] == 120_000


def test_pno_trade_chart_visuals_use_plan_for_close_above_and_keep_exec_fill() -> None:
    display_entry_price, execution_tag_price, signal_timestamp_ms = pno_diagnostics._resolve_pno_trade_chart_entry_visuals(
        {
            "entry_confirmation_mode": "close_above",
            "entry_signal_timestamp_ms": 120_000,
            "entry_plan": 0.412875,
            "level": 0.4128,
            "entry_price_actual": 0.4141,
        }
    )

    assert display_entry_price == pytest.approx(0.412875)
    assert execution_tag_price == pytest.approx(0.4141)
    assert signal_timestamp_ms == 120_000


def test_resolve_pno_structure_points_supports_json_like_payloads() -> None:
    points = pno_diagnostics._resolve_pno_structure_points(
        {
            "structure_pivot_timestamps_ms": "[60000, 120000, 180000]",
            "structure_pivot_prices": "[1.2, 1.0, 1.15]",
            "structure_pivot_kinds": "['H', 'L', 'H']",
        }
    )

    assert points == [(60_000, 1.2, "H"), (120_000, 1.0, "L"), (180_000, 1.15, "H")]


def test_pno_resolve_stage1_context_returns_rejection_payload_for_quality_fail(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000], price=1.5)
    one.highs[2] = 2.1
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[2.0],
        lows=[0.9],
        closes=[1.8],
        ema20=[1.0],
    )

    monkeypatch.setattr(engine, "_resolve_leg_start", lambda **_kwargs: (0, 1.0))
    monkeypatch.setattr(
        engine,
        "_resolve_stage1_quality_metrics",
        lambda **_kwargs: (
            None,
            {
                "reason": "volume_ratio_start_too_small",
                "pump_volume_ratio_start": 4.2,
                "stage1_min_volume_ratio_start": 5.0,
            },
        ),
    )

    context, rejection = engine._resolve_stage1_context(
        one=one,
        five=five,
        idx=2,
        five_idx=0,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert context is None
    assert rejection is not None
    assert rejection["reason"] == "volume_ratio_start_too_small"
    assert rejection["key"] == (0, 0)
    assert rejection["timestamp_ms"] == 120_000
    assert rejection["extra"]["pump_volume_ratio_start"] == pytest.approx(4.2)


def test_pno_stage1_quality_rejects_zero_body_1m_bar_inside_pump() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[300_000, 360_000, 420_000], price=1.0)
    one.opens = pd.Series([1.0, 1.1, 1.2], dtype="float64").to_numpy()
    one.closes = pd.Series([1.0, 1.1, 1.2], dtype="float64").to_numpy()
    one.highs = pd.Series([1.15, 1.2, 1.35], dtype="float64").to_numpy()
    one.lows = pd.Series([0.95, 1.05, 1.05], dtype="float64").to_numpy()
    one.quote_volume = pd.Series([100.0, 120.0, 140.0], dtype="float64").to_numpy()
    one.cumulative_quote_volume = pd.Series([100.0, 220.0, 360.0], dtype="float64").to_numpy()
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "close"] = one.closes
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "volume"] = pd.Series([10.0, 12.0, 14.0], dtype="float64").to_numpy()
    five = _build_test_five_frame(
        opens=[0.95, 1.0],
        highs=[1.05, 1.35],
        lows=[0.90, 0.95],
        closes=[1.0, 1.3],
        ema20=[0.85, 0.9],
    )
    five.quote_volume = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.pre_quote_median_24 = pd.Series([10.0, 10.0], dtype="float64").to_numpy()
    five.pre_trade_median_24 = pd.Series([10.0, 10.0], dtype="float64").to_numpy()
    five.tr = pd.Series([0.2, 0.4], dtype="float64").to_numpy()
    five.v5 = pd.Series([0.2, 0.4], dtype="float64").to_numpy()
    five.atr_pre_14 = pd.Series([0.2, 0.2], dtype="float64").to_numpy()
    five.r3_quote = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.b24_quote = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.r3_trade = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.b24_trade = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.activity_last6_quote = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.activity_prev24_quote = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.activity_last6_trade = pd.Series([80.0, 100.0], dtype="float64").to_numpy()
    five.activity_prev24_trade = pd.Series([80.0, 100.0], dtype="float64").to_numpy()

    quality, rejection = engine._resolve_stage1_quality_metrics(
        one=one,
        five=five,
        idx=2,
        five_idx=1,
        pump_start_5m_idx=1,
        start_idx=0,
        active_high_idx=2,
        active_high=1.35,
        leg_start=1.0,
        leg_size=0.35,
        params=PnoParams(
            symbol="TEST/USDT",
            stage1_min_impulse_atr_pre=0.1,
            stage1_min_peak_bar_tr_atr_pre=0.1,
            stage1_min_volume_ratio_start=0.0,
            stage1_min_volume_ratio_continue=0.0,
            stage1_min_path_efficiency=0.0,
            stage1_max_wick_share=1.0,
            stage1_min_body_share_mean=0.01,
            stage1_max_flat_body_share=1.0,
            stage1_min_body_wick_edge=-1.0,
            stage1_max_micro_flat_bar_share=1.0,
            stage1_max_active_high_upper_wick_share=1.0,
            stage1_max_red_body_share_5m=1.0,
            stage1_max_counterflow_ratio_5m=1.0,
            stage1_max_red_body_share_1m=1.0,
            stage1_max_counterflow_ratio_1m=1.0,
            stage1_min_cumulative_quote_volume=0.0,
        ),
    )

    assert quality is None
    assert rejection is not None
    assert rejection["reason"] == "zero_body_bar_present_1m"
    assert rejection["zero_body_bar_count_1m"] == 3


def test_pno_stage1_quality_rejects_non_contiguous_flow_step() -> None:
    engine = PnoEngine()
    timestamps_ms = [i * 60_000 for i in range(21)]
    one = _build_test_one_frame_multi(timestamps_ms=timestamps_ms, price=1.0)
    one.opens = np.linspace(1.00, 1.16, len(timestamps_ms))
    one.closes = one.opens + 0.01
    one.highs = one.closes + 0.015
    one.lows = one.opens - 0.015
    one.quote_volume = np.full(len(timestamps_ms), 100.0)
    one.cumulative_quote_volume = np.cumsum(one.quote_volume)
    one.tr = one.highs - one.lows
    one.v1 = np.full(len(timestamps_ms), 0.03)
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "close"] = one.closes
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "volume"] = np.full(len(timestamps_ms), 100.0)

    five = _build_test_five_frame(
        opens=[0.98, 1.00, 1.06, 1.08, 1.09],
        highs=[1.00, 1.08, 1.10, 1.11, 1.20],
        lows=[0.96, 0.99, 1.05, 1.07, 1.08],
        closes=[0.99, 1.06, 1.08, 1.09, 1.18],
        ema20=[0.95, 0.96, 0.97, 0.98, 0.99],
    )
    five.quote_volume = np.array([10.0, 100.0, 40.0, 5.0, 40.0])
    five.trade_activity = np.array([10.0, 100.0, 40.0, 5.0, 40.0])
    five.pre_quote_median_24 = np.full(5, 10.0)
    five.pre_trade_median_24 = np.full(5, 10.0)
    five.tr = five.highs - five.lows
    five.v5 = np.full(5, 0.05)
    five.atr_pre_14 = np.full(5, 0.05)
    five.r3_quote = five.quote_volume.copy()
    five.b24_quote = np.full(5, 10.0)
    five.r3_trade = five.trade_activity.copy()
    five.b24_trade = np.full(5, 10.0)
    five.activity_last6_quote = five.quote_volume.copy()
    five.activity_prev24_quote = np.full(5, 10.0)
    five.activity_last6_trade = five.trade_activity.copy()
    five.activity_prev24_trade = np.full(5, 10.0)

    quality, rejection = engine._resolve_stage1_quality_metrics(
        one=one,
        five=five,
        idx=20,
        five_idx=4,
        pump_start_5m_idx=1,
        start_idx=5,
        active_high_idx=20,
        active_high=1.20,
        leg_start=1.00,
        leg_size=0.20,
        params=PnoParams(
            symbol="TEST/USDT",
            stage1_min_impulse_atr_pre=0.1,
            stage1_min_peak_bar_tr_atr_pre=0.1,
            stage1_min_path_efficiency=0.0,
            stage1_max_wick_share=1.0,
            stage1_min_body_share_mean=0.0,
            stage1_max_flat_body_share=1.0,
            stage1_min_body_wick_edge=-1.0,
            stage1_max_micro_flat_bar_share=1.0,
            stage1_max_red_body_share_5m=1.0,
            stage1_max_counterflow_ratio_5m=1.0,
            stage1_max_red_body_share_1m=1.0,
            stage1_max_counterflow_ratio_1m=1.0,
            stage1_min_cumulative_quote_volume=0.0,
        ),
    )

    assert quality is None
    assert rejection is not None
    assert rejection["reason"] == "flow_step_not_confirmed"
    assert rejection["flow_step_initial_run"] == 2
    assert rejection["flow_step_required_run"] == 3


def test_pno_stage1_quality_rejects_noisy_pre_flow_before_step() -> None:
    engine = PnoEngine()
    timestamps_ms = [i * 60_000 for i in range(41)]
    one = _build_test_one_frame_multi(timestamps_ms=timestamps_ms, price=1.0)
    one.opens = np.linspace(1.00, 1.22, len(timestamps_ms))
    one.closes = one.opens + 0.01
    one.highs = one.closes + 0.015
    one.lows = one.opens - 0.015
    one.quote_volume = np.full(len(timestamps_ms), 100.0)
    one.cumulative_quote_volume = np.cumsum(one.quote_volume)
    one.tr = one.highs - one.lows
    one.v1 = np.full(len(timestamps_ms), 0.03)
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "close"] = one.closes
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "volume"] = np.full(len(timestamps_ms), 100.0)

    five = _build_test_five_frame(
        opens=[0.98, 1.00, 1.01, 1.02, 1.03, 1.10, 1.15, 1.18],
        highs=[1.00, 1.02, 1.03, 1.04, 1.12, 1.17, 1.20, 1.28],
        lows=[0.96, 0.99, 1.00, 1.01, 1.02, 1.09, 1.14, 1.17],
        closes=[0.99, 1.01, 1.02, 1.03, 1.10, 1.15, 1.18, 1.25],
        ema20=[0.95, 0.96, 0.97, 0.98, 0.99, 1.00, 1.01, 1.02],
    )
    five.quote_volume = np.array([10.0, 160.0, 10.0, 140.0, 100.0, 80.0, 75.0, 70.0])
    five.trade_activity = np.array([10.0, 160.0, 10.0, 140.0, 100.0, 80.0, 75.0, 70.0])
    five.pre_quote_median_24 = np.full(8, 10.0)
    five.pre_trade_median_24 = np.full(8, 10.0)
    five.tr = five.highs - five.lows
    five.v5 = np.full(8, 0.05)
    five.atr_pre_14 = np.full(8, 0.05)
    five.r3_quote = five.quote_volume.copy()
    five.b24_quote = np.full(8, 10.0)
    five.r3_trade = five.trade_activity.copy()
    five.b24_trade = np.full(8, 10.0)
    five.activity_last6_quote = five.quote_volume.copy()
    five.activity_prev24_quote = np.full(8, 10.0)
    five.activity_last6_trade = five.trade_activity.copy()
    five.activity_prev24_trade = np.full(8, 10.0)

    quality, rejection = engine._resolve_stage1_quality_metrics(
        one=one,
        five=five,
        idx=40,
        five_idx=7,
        pump_start_5m_idx=4,
        start_idx=20,
        active_high_idx=40,
        active_high=1.28,
        leg_start=1.03,
        leg_size=0.25,
        params=PnoParams(
            symbol="TEST/USDT",
            stage1_min_impulse_atr_pre=0.1,
            stage1_min_peak_bar_tr_atr_pre=0.1,
            stage1_min_path_efficiency=0.0,
            stage1_max_wick_share=1.0,
            stage1_min_body_share_mean=0.0,
            stage1_max_flat_body_share=1.0,
            stage1_min_body_wick_edge=-1.0,
            stage1_max_micro_flat_bar_share=1.0,
            stage1_max_red_body_share_5m=1.0,
            stage1_max_counterflow_ratio_5m=1.0,
            stage1_max_red_body_share_1m=1.0,
            stage1_max_counterflow_ratio_1m=1.0,
            stage1_min_cumulative_quote_volume=0.0,
        ),
    )

    assert quality is None
    assert rejection is not None
    assert rejection["reason"] == "pre_flow_not_sleepy_before_step"


def test_pno_run_records_stage1_rejection_once_per_near_pump_reason(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000], price=1.2)
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[1.5],
        lows=[0.9],
        closes=[1.3],
        ema20=[1.0],
    )
    diagnostics = engine._empty_diagnostics()
    rejection = {
        "reason": "volume_ratio_start_too_small",
        "key": (0, 0),
        "timestamp_ms": 120_000,
        "extra": {
            "pump_start_timestamp_ms": 0,
            "stage1_confirm_timestamp_ms": 0,
            "active_high_timestamp_ms": 120_000,
            "current_timestamp_ms": 120_000,
            "active_high": 1.5,
            "leg_start": 1.0,
            "leg_size": 0.5,
            "stage1_hold_price": 1.2,
        },
    }

    monkeypatch.setattr(engine, "_resolve_stage1_context", lambda **_kwargs: (None, rejection))

    trades = engine._run(
        one=one,
        five=five,
        params=PnoParams(symbol="TEST/USDT", min_data_1m=1, min_data_5m=1),
        diagnostics=diagnostics,
    )

    assert trades == []
    stage1_rejections = [row for row in diagnostics["stage_rejections"] if row.get("stage_id") == "stage_1_pump"]
    assert len(stage1_rejections) == 1
    assert stage1_rejections[0]["reason"] == "volume_ratio_start_too_small"


def test_pno_trade_chart_visuals_use_level_for_cross_without_exec_tag() -> None:
    display_entry_price, execution_tag_price, signal_timestamp_ms = pno_diagnostics._resolve_pno_trade_chart_entry_visuals(
        {
            "entry_confirmation_mode": "cross",
            "entry_signal_timestamp_ms": 60_000,
            "entry_plan": 10.02,
            "level": 10.0,
            "entry_price_actual": 10.01,
        }
    )

    assert display_entry_price == pytest.approx(10.0)
    assert execution_tag_price is None
    assert signal_timestamp_ms == 60_000


def test_parser_supports_pno_stage_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["pno-stage", "s4"])

    assert args.command == "pno-stage"
    assert args.preset == "s4"
    assert resolve_handler(args.command) is commands.run_pno_stage


def test_parser_supports_plot_backtest_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["plot-backtest", "--run-dir", "C:/tmp/run"])

    assert args.command == "plot-backtest"
    assert args.run_dir == "C:/tmp/run"
    assert resolve_handler(args.command) is commands.plot_backtest


def test_run_backtest_wraps_results_into_run_subdir(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="pno"),
        simulation=SimulationConfig(),
        backtest=BacktestConfig(
            log_level="INFO",
            cache_dir=tmp_path / "cache",
            logs_dir=tmp_path / "logs",
            results_dir=tmp_path / "results",
            results_file_name="results.csv",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        ),
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(commands.time, "strftime", lambda _fmt: "20240102_030405")

    def _fake_run_backtest_inner(scoped_config, scoped_args):
        captured["results_dir"] = scoped_config.backtest.results_dir
        captured["run_root"] = getattr(scoped_args, "backtest_run_root_dir", None)
        return 0

    monkeypatch.setattr(commands, "_run_backtest_inner", _fake_run_backtest_inner)

    exit_code = commands.run_backtest(
        config,
        argparse.Namespace(
            strategy="pno",
            plot_from_results=False,
            plot=False,
            symbols=None,
            levels_tf=None,
            entry_tf=None,
            pno_deposit=None,
            pno_risk_pct=None,
            pno_entry_confirmation_mode=None,
            top_n=None,
            days=None,
            end_timestamp_ms=None,
            pno_stage=None,
            pno_through_stage=None,
            results_input=None,
            id=None,
        ),
    )

    expected_root = tmp_path / "results" / "backtest_runs" / "20240102_030405_pno"
    assert exit_code == 0
    assert captured["results_dir"] == expected_root
    assert captured["run_root"] == str(expected_root)


def test_run_backtest_uses_m1_symbol_cache_for_pno_seconds_entry_pairs(tmp_path, monkeypatch) -> None:
    requested_timeframes: list[Timeframe] = []

    class _FakePreparer:
        def __init__(self, _cache_dir):
            pass

        def list_symbols(self, timeframe):
            requested_timeframes.append(timeframe)
            return ["DOGS/USDT:USDT"]

        def load_symbol_data(self, symbol, timeframe, days=None, end_timestamp_ms=None):
            del symbol, timeframe, days, end_timestamp_ms
            return pd.DataFrame()

        def get_symbol_last_timestamp_ms(self, symbol, timeframe):
            del symbol, timeframe
            return None

    monkeypatch.setattr(commands, "DataPreparer", _FakePreparer)

    config = AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="pno"),
        simulation=SimulationConfig(),
        backtest=BacktestConfig(
            log_level="INFO",
            cache_dir=tmp_path / "cache",
            logs_dir=tmp_path / "logs",
            results_dir=tmp_path / "results",
            results_file_name="results.csv",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        ),
    )

    exit_code = commands._run_backtest_inner(
        config,
        argparse.Namespace(
            strategy="pno",
            plot_from_results=False,
            plot=False,
            symbols=None,
            levels_tf="5m",
            entry_tf="30s",
            pno_deposit=None,
            pno_risk_pct=None,
            pno_entry_confirmation_mode="close_above",
            pno_category_mode="discovery",
            pno_all_tf_pairs=False,
            top_n=None,
            days=None,
            end_timestamp_ms=None,
            pno_stage=None,
            pno_through_stage=None,
            results_input=None,
            id=None,
            light_run=False,
            backtest_run_root_dir=None,
        ),
    )

    assert exit_code == 0
    assert requested_timeframes == [Timeframe.M1]


def test_render_pno_trade_charts_from_export_result_updates_diagnostics_json(tmp_path, monkeypatch) -> None:
    diagnostics_dir = tmp_path / "trade_plots" / "pno_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    (diagnostics_dir / "BTC_USDT_diagnostics.json").write_text('{"symbol":"BTC/USDT"}', encoding="utf-8")
    frame = pd.DataFrame(
        {
            "timestamp": [60_000],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }
    )
    export_result = {
        "diagnostics_payloads": [
            {
                "symbol": "BTC/USDT",
                "trade_rows": [{"trade_key": "k"}],
                "mtf_frames": SymbolMtfFrames(
                    levels_timeframe=Timeframe.M5,
                    entry_timeframe=Timeframe.M1,
                    levels_frame=frame,
                    entry_frame=frame,
                ),
            }
        ]
    }

    monkeypatch.setattr(
        commands,
        "_render_pno_trade_charts_for_symbol",
        lambda **_kwargs: ["chart_a.png", "chart_b.png"],
    )

    generated = commands._render_pno_trade_charts_from_export_result(
        diagnostics_dir=diagnostics_dir,
        export_result=export_result,
        logger=logging.getLogger("test-pno-trade-charts"),
        log_prefix="plot=false",
    )

    payload = json.loads((diagnostics_dir / "BTC_USDT_diagnostics.json").read_text(encoding="utf-8"))
    assert generated == 2
    assert payload["chart_paths"] == ["chart_a.png", "chart_b.png"]


def test_export_pno_grid_artifacts_without_stage_charts_splits_by_entry_mode(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    results = pd.DataFrame(
        [
            strategy.params_to_row(PnoParams(symbol="BTC/USDT", entry_confirmation_mode="cross", pno_variant_id="baseline_cross")),
            strategy.params_to_row(PnoParams(symbol="BTC/USDT", entry_confirmation_mode="close_above", pno_variant_id="baseline_close")),
        ]
    )
    captured_dirs: list[Path] = []

    monkeypatch.setattr(
        commands,
        "_export_pno_diagnostics_context_for_symbols",
        lambda **kwargs: captured_dirs.append(Path(kwargs["diagnostics_dir"])) or {"diagnostics_payloads": []},
    )
    monkeypatch.setattr(commands, "_render_pno_trade_charts_from_export_result", lambda **_kwargs: 0)

    commands._export_pno_grid_artifacts_without_stage_charts(
        config=SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path)),
        args=argparse.Namespace(output_dir=None),
        logger=logging.getLogger("test-pno-grid-artifacts"),
        strategy=strategy,
        symbol_frames={},
        results=results,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
    )

    assert captured_dirs == [
        tmp_path / "trade_plots" / "cross" / "pno_diagnostics",
        tmp_path / "trade_plots" / "close_above" / "pno_diagnostics",
    ]


def test_sync_shared_stage_reviews_copies_common_stages_and_merges_manifest(tmp_path) -> None:
    shared_dir = tmp_path / "shared" / "pno_diagnostics"
    target_dir = tmp_path / "close_above" / "pno_diagnostics"
    shared_stage_dir = shared_dir / "stage_reviews" / "stage_1_pump" / "passed"
    target_stage5_dir = target_dir / "stage_reviews" / commands.PNO_STAGE_5_TRADE / "passed"
    shared_stage_dir.mkdir(parents=True, exist_ok=True)
    target_stage5_dir.mkdir(parents=True, exist_ok=True)
    (shared_stage_dir / "events.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (target_stage5_dir / "events.csv").write_text("x,y\n3,4\n", encoding="utf-8")
    pd.DataFrame(
        [
            {
                "stage_id": "stage_1_pump",
                "passed_count": 1,
                "rejected_count": 0,
                "rejected_review_count": 0,
                "rejected_filtered_count": 0,
                "passed_events_path": str(shared_stage_dir / "events.csv"),
                "passed_charts_count": 0,
                "rejected_charts_count": 0,
            }
        ]
    ).to_csv(shared_dir / "stage_reviews" / "manifest.csv", index=False)
    pd.DataFrame(
        [
            {
                "stage_id": commands.PNO_STAGE_5_TRADE,
                "passed_count": 2,
                "rejected_count": 0,
                "rejected_review_count": 0,
                "rejected_filtered_count": 0,
                "passed_events_path": str(target_stage5_dir / "events.csv"),
                "passed_charts_count": 0,
                "rejected_charts_count": 0,
            }
        ]
    ).to_csv(target_dir / "stage_reviews" / "manifest.csv", index=False)

    commands._sync_shared_stage_reviews(
        shared_diagnostics_dir=shared_dir,
        target_diagnostics_dir=target_dir,
        shared_stage_ids=("stage_1_pump",),
    )

    merged_manifest = pd.read_csv(target_dir / "stage_reviews" / "manifest.csv")
    assert set(merged_manifest["stage_id"]) == {"stage_1_pump", commands.PNO_STAGE_5_TRADE}
    shared_row = merged_manifest.loc[merged_manifest["stage_id"] == "stage_1_pump"].iloc[0]
    assert Path(shared_row["passed_events_path"]) == target_dir / "stage_reviews" / "stage_1_pump" / "passed" / "events.csv"
    assert (target_dir / "stage_reviews" / "stage_1_pump" / "passed" / "events.csv").exists()


def test_plot_pno_grid_artifacts_reuses_shared_stage_reviews_for_second_mode(tmp_path, monkeypatch) -> None:
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    results = pd.DataFrame(
        [
            strategy.params_to_row(PnoParams(symbol="BTC/USDT", entry_confirmation_mode="cross", pno_variant_id="baseline_cross")),
            strategy.params_to_row(PnoParams(symbol="BTC/USDT", entry_confirmation_mode="close_above", pno_variant_id="baseline_close")),
        ]
    )
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        commands,
        "_plot_for_strategy_dispatch",
        lambda **kwargs: calls.append(("full", Path(kwargs["args"].output_dir).name)) or True,
    )
    monkeypatch.setattr(
        commands,
        "_plot_pno_diagnostics_with_shared_stage_reviews",
        lambda **kwargs: calls.append(("shared", Path(kwargs["args"].output_dir).name)),
    )

    ok = commands._plot_pno_grid_artifacts(
        config=SimpleNamespace(backtest=SimpleNamespace(results_dir=tmp_path)),
        args=argparse.Namespace(output_dir=None),
        logger=logging.getLogger("test-pno-grid-shared"),
        strategy=strategy,
        symbol_frames={},
        results=results,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        log_prefix="test",
    )

    assert ok is True
    assert calls == [("full", "cross"), ("shared", "close_above")]


def test_plot_backtest_reuses_saved_request(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "backtest_runs" / "20240102_030405_pno"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_context.json").write_text(
        json.dumps(
            {
                "strategy_id": "pno",
                "strategy_results_rel_dir": "strategy/pno",
                "trade_plots_rel_dir": "strategy/pno/trade_plots",
                "results_file_name": "results.csv",
                "levels_tf": "5m",
                "entry_tf": "1m",
                "days": 30,
                "end_timestamp_ms": 123456,
                "symbols": ["BTC/USDT", "ETH/USDT"],
                "plot_requested": True,
                "pno_category_mode": "discovery",
                "pno_stage": 1,
                "pno_through_stage": None,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "plot_request.json").write_text(
        json.dumps({"selected_row_number": 7}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def _fake_run_backtest_inner(_config, plot_args):
        captured["command"] = plot_args.command
        captured["plot_from_results"] = plot_args.plot_from_results
        captured["results_input"] = plot_args.results_input
        captured["output_dir"] = plot_args.output_dir
        captured["row_number"] = plot_args.row_number
        captured["symbols"] = plot_args.symbols
        captured["levels_tf"] = plot_args.levels_tf
        captured["entry_tf"] = plot_args.entry_tf
        captured["pno_category_mode"] = plot_args.pno_category_mode
        captured["pno_stage"] = plot_args.pno_stage
        return 0

    monkeypatch.setattr(commands, "_run_backtest_inner", _fake_run_backtest_inner)

    config = AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="pno"),
        simulation=SimulationConfig(),
        backtest=BacktestConfig(
            log_level="INFO",
            cache_dir=tmp_path / "cache",
            logs_dir=tmp_path / "logs",
            results_dir=tmp_path / "results",
            results_file_name="results.csv",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        ),
    )

    exit_code = commands.plot_backtest(config, argparse.Namespace(run_dir=str(run_dir)))

    assert exit_code == 0
    assert captured["command"] == "run-backtest"
    assert captured["plot_from_results"] is True
    assert captured["results_input"] == str(run_dir / "strategy" / "pno" / "results.csv")
    assert captured["output_dir"] == str(run_dir / "strategy" / "pno" / "trade_plots")
    assert captured["row_number"] == 7
    assert captured["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert captured["levels_tf"] == "5m"
    assert captured["entry_tf"] == "1m"
    assert captured["pno_category_mode"] == "discovery"
    assert captured["pno_stage"] == 1


def test_build_parser_accepts_pno_category_mode() -> None:
    parser = build_parser()

    args = parser.parse_args(["run-backtest", "--strategy", "pno", "--pno-category-mode", "discovery"])

    assert args.command == "run-backtest"
    assert args.pno_category_mode == "discovery"


def test_build_parser_accepts_pno_all_tf_pairs() -> None:
    parser = build_parser()

    args = parser.parse_args(["run-backtest", "--strategy", "pno", "--pno-all-tf-pairs"])

    assert args.command == "run-backtest"
    assert args.pno_all_tf_pairs is True


def test_run_backtest_pno_all_tf_pairs_saves_each_pair_into_own_subdir(tmp_path, monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def _fake_run_backtest_inner(scoped_config, scoped_args):
        captured.append(
            {
                "results_dir": Path(scoped_config.backtest.results_dir),
                "run_root": Path(scoped_args.backtest_run_root_dir),
                "levels_tf": scoped_args.levels_tf,
                "entry_tf": scoped_args.entry_tf,
            }
        )
        return 0

    monkeypatch.setattr(commands, "_run_backtest_inner", _fake_run_backtest_inner)

    config = AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="pno"),
        simulation=SimulationConfig(),
        backtest=BacktestConfig(
            log_level="INFO",
            cache_dir=tmp_path / "cache",
            logs_dir=tmp_path / "logs",
            results_dir=tmp_path / "results",
            results_file_name="results.csv",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        ),
    )

    exit_code = commands.run_backtest(
        config,
        argparse.Namespace(
            strategy="pno",
            plot_from_results=False,
            plot=False,
            symbols=None,
            levels_tf=None,
            entry_tf=None,
            top_n=None,
            days=15,
            end_timestamp_ms=None,
            pno_deposit=10_000.0,
            pno_risk_pct=0.05,
            pno_entry_confirmation_mode="close_above",
            pno_category_mode="discovery",
            pno_all_tf_pairs=True,
            pno_stage=None,
            pno_through_stage=None,
            light_run=False,
            results_input=None,
            id=None,
        ),
    )

    assert exit_code == 0
    assert len(captured) == len(PNO_BACKTEST_TIMEFRAME_PAIRS)
    assert [(item["levels_tf"], item["entry_tf"]) for item in captured] == [
        (levels_tf.value, entry_tf.value) for levels_tf, entry_tf in PNO_BACKTEST_TIMEFRAME_PAIRS
    ]
    pair_dirs = [item["run_root"].name for item in captured]
    assert pair_dirs == [f"{levels_tf.value}_{entry_tf.value}" for levels_tf, entry_tf in PNO_BACKTEST_TIMEFRAME_PAIRS]
    assert all(item["results_dir"] == item["run_root"] for item in captured)


def test_select_pno_plot_params_row_by_stage_falls_back_to_rejections() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }
    results = pd.DataFrame(
        [
            {"pno_variant_id": "baseline_cross", "trades_count": 0, "profit_factor": 0.0, "pno_stage_hits_stage_2_high_pullback": 0},
            {"pno_variant_id": "baseline_close", "trades_count": 0, "profit_factor": 0.0, "pno_stage_hits_stage_2_high_pullback": 0},
        ]
    )
    strategy = PnoStrategy(deposit=1_000.0, risk_pct=0.02)
    call_idx = {"value": 0}

    def _generate_events_multi_tf(**_kwargs):
        call_idx["value"] += 1
        return []

    def _consume_last_generation_diagnostics():
        if call_idx["value"] == 1:
            return {"stage_events": [], "stage_rejections": [], "trades_generated": 0}
        return {
            "stage_events": [],
            "stage_rejections": [{"stage_id": "stage_2_high_pullback", "reason": "new_main_high_before_pullback"}],
            "trades_generated": 0,
        }

    strategy.generate_events_multi_tf = _generate_events_multi_tf  # type: ignore[method-assign]
    strategy.consume_last_generation_diagnostics = _consume_last_generation_diagnostics  # type: ignore[method-assign]

    best_row = commands._select_pno_plot_params_row_by_stage(
        args=argparse.Namespace(pno_stage=2, pno_through_stage=None),
        strategy=strategy,
        symbol_frames=symbol_frames,
        results=results,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        logger=logging.getLogger("test-pno-stage-select"),
    )

    assert best_row is not None
    assert str(best_row["pno_variant_id"]) == "baseline_close"


def test_pno_engine_builds_confirmed_pivot_maps() -> None:
    engine = PnoEngine()
    highs = pd.Series([1.0, 2.0, 5.0, 4.8, 3.7, 4.0, 3.8], dtype="float64").to_numpy()
    lows = pd.Series([0.8, 1.6, 4.2, 3.6, 2.8, 3.3, 3.1], dtype="float64").to_numpy()
    v1 = pd.Series([1.0] * len(highs), dtype="float64").to_numpy()

    high_indices, high_confirmed_at = engine._build_confirmed_high_map(highs=highs, lows=lows, v1=v1)
    low_indices, low_confirmed_at = engine._build_confirmed_low_map(highs=highs, lows=lows, v1=v1)

    assert list(high_indices) == [2]
    assert list(high_confirmed_at) == [3]
    assert list(low_indices) == [4]
    assert list(low_confirmed_at) == [5]


def test_pno_cluster_rearm_requires_new_low_or_distance() -> None:
    engine = PnoEngine()
    stage3 = Stage3Context(
        active_high_idx=10,
        active_high_timestamp=10_000,
        active_high=10.0,
        pullback_start_idx=11,
        pullback_low_idx=15,
        pullback_low_timestamp=15_000,
        pullback_low=8.2,
        pullback_depth=1.8,
        pullback_age_bars=5,
        validation_timestamp=16_000,
    )
    retired = [
        RetiredCluster(
            active_high_idx=10,
            cluster_first_idx=12,
            cluster_last_idx=14,
            level=9.0,
            pullback_low_idx=15,
            pullback_low=8.2,
        )
    ]

    assert engine._is_cluster_rearm_allowed(
        active_high_idx=10,
        candidate_level=9.4,
        stage3=stage3,
        retired_clusters=retired,
        v1_now=1.0,
    ) is False
    assert engine._is_cluster_rearm_allowed(
        active_high_idx=10,
        candidate_level=10.2,
        stage3=stage3,
        retired_clusters=retired,
        v1_now=1.0,
    ) is True
    assert engine._is_cluster_rearm_allowed(
        active_high_idx=10,
        candidate_level=9.4,
        stage3=Stage3Context(
            active_high_idx=10,
            active_high_timestamp=10_000,
            active_high=10.0,
            pullback_start_idx=11,
            pullback_low_idx=18,
            pullback_low_timestamp=18_000,
            pullback_low=7.9,
            pullback_depth=2.1,
            pullback_age_bars=7,
            validation_timestamp=18_000,
        ),
        retired_clusters=retired,
        v1_now=1.0,
    ) is True


def test_stage3_allows_post_high_ema20_wick_touch_without_close_below() -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        pullback_valid_min_leg_fraction=0.5,
        stage1_active_context_min_start_fraction=0.0,
        stage1_active_context_min_baseline_ratio=0.0,
    )
    one = _build_test_one_frame(timestamp_ms=600_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.8, 10.7],
        highs=[11.0, 11.1, 11.0],
        lows=[9.8, 10.0, 10.5],
        closes=[10.9, 10.8, 10.75],
        ema20=[9.9, 10.1, 10.2],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=3,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=0,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert reason is None
    assert stage3.post_high_ema20_pierce_count == 1
    assert stage3.post_high_close_below_ema20_count == 0


def test_stage3_rejects_three_post_high_closes_below_ema20() -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT", pullback_valid_min_leg_fraction=0.5)
    one = _build_test_one_frame(timestamp_ms=600_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.8, 10.7, 10.6],
        highs=[11.0, 11.1, 11.0, 10.95],
        lows=[9.8, 10.0, 10.5, 10.4],
        closes=[10.9, 10.05, 10.15, 10.12],
        ema20=[9.9, 10.1, 10.2, 10.2],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=3,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=2,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=0,
        five_idx=3,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert stage3 is None
    assert reason == "post_high_closed_below_ema20"


def test_stage3_rejects_overlap_heavy_post_high_chop_walk() -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT", pullback_valid_min_leg_fraction=0.5)
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000, 120_000, 180_000, 240_000, 300_000, 360_000, 420_000, 480_000, 540_000],
                "open": [10.0, 10.60, 10.46, 10.58, 10.47, 10.57, 10.46, 10.56, 10.45, 10.55],
                "high": [11.0, 10.64, 10.62, 10.61, 10.60, 10.60, 10.59, 10.59, 10.58, 10.58],
                "low": [10.0, 10.44, 10.43, 10.44, 10.43, 10.44, 10.43, 10.44, 10.43, 10.44],
                "close": [10.9, 10.48, 10.57, 10.49, 10.56, 10.48, 10.55, 10.47, 10.54, 10.46],
                "volume": [10.0, 12.0, 11.0, 10.0, 9.0, 11.0, 10.0, 9.0, 10.0, 8.0],
            }
        )
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.60, 10.52],
        highs=[11.0, 10.64, 10.60],
        lows=[10.0, 10.43, 10.43],
        closes=[10.9, 10.48, 10.46],
        ema20=[9.9, 10.20, 10.22],
    )
    five.volumes[:] = np.array([100.0, 65.0, 60.0], dtype="float64")
    five.frame.loc[:, "volume"] = five.volumes
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=2,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=9,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert stage3 is None
    assert reason == "post_high_chop_walk"


def test_stage3_allows_overlap_heavy_post_high_when_5m_volume_support_is_strong() -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT", pullback_valid_min_leg_fraction=0.5)
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000, 120_000, 180_000, 240_000, 300_000, 360_000, 420_000, 480_000, 540_000],
                "open": [10.0, 10.60, 10.46, 10.58, 10.47, 10.57, 10.46, 10.56, 10.45, 10.55],
                "high": [11.0, 10.64, 10.62, 10.61, 10.60, 10.60, 10.59, 10.59, 10.58, 10.58],
                "low": [10.0, 10.44, 10.43, 10.44, 10.43, 10.44, 10.43, 10.44, 10.43, 10.44],
                "close": [10.9, 10.48, 10.57, 10.49, 10.56, 10.48, 10.55, 10.47, 10.54, 10.46],
                "volume": [10.0, 12.0, 11.0, 10.0, 9.0, 11.0, 10.0, 9.0, 10.0, 8.0],
            }
        )
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.60, 10.52],
        highs=[11.0, 10.64, 10.60],
        lows=[10.0, 10.43, 10.43],
        closes=[10.9, 10.48, 10.46],
        ema20=[9.9, 10.20, 10.22],
    )
    five.volumes[:] = np.array([100.0, 80.0, 60.0], dtype="float64")
    five.frame.loc[:, "volume"] = five.volumes
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=9,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert reason is None
    assert stage3 is not None


def test_stage3_rejects_when_no_post_high_5m_volume_support() -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        pullback_valid_min_leg_fraction=0.5,
        stage1_active_context_min_start_fraction=0.0,
        stage1_active_context_min_baseline_ratio=0.0,
    )
    one = _build_test_one_frame(timestamp_ms=600_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.8, 10.65, 10.55],
        highs=[11.0, 10.9, 10.72, 10.68],
        lows=[9.8, 10.3, 10.45, 10.42],
        closes=[10.9, 10.55, 10.58, 10.60],
        ema20=[9.9, 10.1, 10.2, 10.25],
    )
    five.volumes[:] = np.array([100.0, 35.0, 45.0, 40.0], dtype="float64")
    five.frame.loc[:, "volume"] = five.volumes
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=3,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=3,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=3,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=0,
        five_idx=3,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert stage3 is None
    assert reason == "post_high_no_supporting_5m_volume"


def test_stage3_allows_two_post_high_closes_below_ema20_when_base_is_clean() -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT", pullback_valid_min_leg_fraction=0.5)
    one = _build_test_one_frame(timestamp_ms=600_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.08, 10.25],
        highs=[11.0, 11.1, 10.7],
        lows=[9.8, 10.0, 10.18],
        closes=[10.9, 10.05, 10.15],
        ema20=[9.9, 10.1, 10.2],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=0,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert reason is None
    assert stage3.post_high_close_below_ema20_count == 2


def test_stage3_fast_reclaim_exception_allows_fresh_low_support_pullback() -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        pullback_valid_min_leg_fraction=0.5,
        stage3_min_post_high_5m_volume_support_fraction=0.35,
        stage3_fast_reclaim_min_post_high_5m_volume_support_fraction=0.30,
        stage3_fast_reclaim_max_pullback_age_bars=2,
        stage1_active_context_min_start_fraction=0.0,
        stage1_active_context_min_baseline_ratio=0.0,
    )
    one = _build_test_one_frame(timestamp_ms=600_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.8, 10.6],
        highs=[11.0, 10.85, 10.7],
        lows=[9.8, 10.45, 10.5],
        closes=[10.9, 10.55, 10.62],
        ema20=[9.9, 10.2, 10.25],
    )
    five.volumes[:] = np.array([100.0, 32.0, 28.0], dtype="float64")
    five.frame.loc[:, "volume"] = five.volumes
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=2,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=0,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert reason is None


def test_stage3_allows_fast_pullback_even_with_low_post_high_5m_volume() -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        pullback_valid_min_leg_fraction=0.5,
        stage3_min_post_high_5m_volume_support_fraction=0.50,
        stage1_active_context_min_start_fraction=0.0,
        stage1_active_context_min_baseline_ratio=0.0,
    )
    one = _build_test_one_frame(timestamp_ms=600_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.8, 10.6],
        highs=[11.0, 10.85, 10.7],
        lows=[9.8, 10.45, 10.5],
        closes=[10.9, 10.55, 10.62],
        ema20=[9.9, 10.2, 10.25],
    )
    five.volumes[:] = np.array([100.0, 18.0, 28.0], dtype="float64")
    five.frame.loc[:, "volume"] = five.volumes
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=2,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=300_000,
        pullback_low=10.4,
        pullback_depth=0.6,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=0,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert reason is None


def test_ideal_like_level_cluster_fallback_recovers_recent_reclaim_high() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000, 120_000, 180_000, 240_000, 300_000],
                "open": [10.0, 10.8, 10.45, 10.56, 10.60, 10.61],
                "high": [11.0, 10.82, 10.58, 10.68, 10.67, 10.66],
                "low": [9.8, 10.40, 10.42, 10.54, 10.58, 10.60],
                "close": [10.9, 10.44, 10.56, 10.66, 10.63, 10.64],
                "volume": [10.0, 15.0, 12.0, 11.0, 10.0, 9.0],
            }
        )
    )
    stage3 = Stage3Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=10.42,
        pullback_depth=0.58,
        pullback_age_bars=2,
        validation_timestamp=300_000,
    )
    params = PnoParams(
        symbol="TEST/USDT",
        ideal_like_level_latest_high_max_age_bars=60,
    )

    cluster = engine._resolve_ideal_like_level_cluster(
        one=one,
        idx=5,
        stage3=stage3,
        retired_clusters=[],
        params=params,
    )

    assert cluster is not None
    cluster_indices, cluster_prices = cluster
    assert cluster_indices == (3,)
    assert cluster_prices == (pytest.approx(10.68),)


def test_stage3_allows_wick_below_leg_start_if_closes_hold_above() -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        pullback_valid_min_leg_fraction=0.82,
        pullback_valid_max_leg_fraction=1.20,
        pullback_invalid_max_leg_fraction=1.25,
    )
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000, 120_000, 180_000, 240_000],
                "open": [10.0, 10.95, 10.25, 10.15, 10.3],
                "high": [11.0, 11.0, 10.35, 10.2, 10.4],
                "low": [10.0, 10.2, 9.92, 10.05, 10.2],
                "close": [10.9, 10.3, 10.05, 10.12, 10.28],
                "volume": [10.0, 10.0, 10.0, 10.0, 10.0],
            }
        )
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.9, 10.2],
        highs=[11.0, 11.0, 10.4],
        lows=[10.0, 10.1, 9.92],
        closes=[10.9, 10.2, 10.3],
        ema20=[9.9, 10.0, 10.05],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
    )
    stage2 = Stage2Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=11.0,
        red_after_high_idx=2,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=9.92,
        pullback_depth=1.08,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=4,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert reason is None
    assert stage3 is not None
    assert stage3.pullback_wick_broke_leg_start is True
    assert stage3.pullback_close_broke_leg_start is False


def test_stage3_rejects_pullback_close_below_leg_start() -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        pullback_valid_min_leg_fraction=0.82,
        pullback_valid_max_leg_fraction=1.20,
        pullback_invalid_max_leg_fraction=1.25,
    )
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000, 120_000, 180_000],
                "open": [10.0, 10.95, 10.25, 10.05],
                "high": [11.0, 11.0, 10.35, 10.2],
                "low": [10.0, 10.2, 9.9, 9.95],
                "close": [10.9, 10.3, 9.98, 10.08],
                "volume": [10.0, 10.0, 10.0, 10.0],
            }
        )
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.9, 10.2],
        highs=[11.0, 11.0, 10.4],
        lows=[10.0, 10.1, 9.9],
        closes=[10.9, 10.2, 10.1],
        ema20=[9.9, 10.0, 10.05],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=2,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.2,
        pump_range_5m=1.0,
    )
    stage2 = Stage2Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=11.0,
        red_after_high_idx=2,
        pullback_start_idx=1,
        pullback_low_idx=2,
        pullback_low_timestamp=120_000,
        pullback_low=9.9,
        pullback_depth=1.1,
        pullback_age_bars=2,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=3,
        five_idx=2,
        stage1=stage1,
        stage2=stage2,
        params=params,
    )

    assert stage3 is None
    assert reason is None


def test_pno_stage1_pump_candidate_rejection_captures_ema20_failure() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame(timestamp_ms=900_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.2, 10.4],
        highs=[10.3, 10.8, 10.7],
        lows=[9.9, 10.1, 10.0],
        closes=[10.25, 10.75, 10.05],
        ema20=[9.8, 10.0, 10.2],
    )
    rejection = engine._resolve_stage1_pump_candidate_rejection(
        one=one,
        five=five,
        idx=0,
        five_idx=2,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rejection is not None
    assert rejection["reason"] == "pump_candidate_below_ema20"
    assert rejection["key"] == (0,)


def test_pno_stage1_pump_candidate_rejection_ignores_non_stage1_noise() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame(timestamp_ms=900_000)
    five = _build_test_five_frame(
        opens=[10.0, 10.2, 10.4],
        highs=[10.3, 10.8, 10.7],
        lows=[9.9, 10.1, 10.0],
        closes=[10.25, 10.75, 10.05],
        ema20=[9.8, 10.0, 10.2],
    )
    five.inplay[:] = False
    five.pump_start_idx[:] = -1
    five.stage1_confirm_idx[:] = -1

    rejection = engine._resolve_stage1_pump_candidate_rejection(
        one=one,
        five=five,
        idx=0,
        five_idx=2,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rejection is None


def test_stage1_context_caps_hold_floor_to_actual_active_high(monkeypatch) -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        min_stage1_leg_v1=0.05,
        min_stage1_leg_v5_fraction=0.05,
        stage1_hold_fraction=0.5,
    )
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000], price=1.0)
    one.highs = pd.Series([1.0, 1.2], dtype="float64").to_numpy()
    one.lows = pd.Series([1.0, 1.05], dtype="float64").to_numpy()
    one.closes = pd.Series([1.0, 1.15], dtype="float64").to_numpy()
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[1.2],
        lows=[1.0],
        closes=[1.15],
        ema20=[1.0],
    )
    five.pump_start_idx[:] = 0
    five.sleep_start_idx[:] = 0
    five.sleep_end_idx[:] = 0
    five.stage1_confirm_idx[:] = 0

    monkeypatch.setattr(engine, "_resolve_leg_start", lambda **_kwargs: (0, 1.0))
    monkeypatch.setattr(
        engine,
        "_resolve_stage1_quality_metrics",
        lambda **_kwargs: (
            {
                "cumulative_quote_volume": 300_000.0,
                "pre_pump_ema_crosses_1h": 1,
                "pre_pump_barcode_fraction_1h": 0.1,
                "pre_pump_high_24h": 1.0,
                "pre_pump_high_1h": 1.0,
                "pump_pre_atr": 0.1,
                "pump_impulse_atr_pre": 2.0,
                "pump_peak_bar_tr_atr_pre": 1.5,
                "pump_volume_ratio_start": 4.0,
                "pump_volume_ratio_continue": 1.2,
                "pump_path_efficiency": 0.4,
                "pump_wick_share": 0.4,
                "pump_body_share_mean": 0.5,
                "pump_flat_body_share": 0.2,
                "pump_body_wick_edge": 0.1,
                "pump_micro_flat_bar_share": 0.2,
                "active_high_bar_body_share": 0.6,
                "active_high_bar_upper_wick_share": 0.3,
                "active_high_bar_close_position": 0.8,
                "pump_max_red_body_share_5m": 0.1,
                "pump_counterflow_ratio_5m": 0.1,
                "pump_max_red_body_share_1m": 0.1,
                "pump_counterflow_ratio_1m": 0.1,
                "reference_high": 1.4,
                "reference_high_weight": 1.0,
            },
            None,
        ),
    )

    stage1, rejection = engine._resolve_stage1_context(
        one=one,
        five=five,
        idx=1,
        five_idx=0,
        params=params,
    )

    assert rejection is None
    assert stage1 is not None
    assert stage1.hold_floor == pytest.approx(1.1)


def test_stage1_hold_status_uses_stage1_hold_price_as_floor() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000], price=1.0)
    one.closes = pd.Series([1.0, 1.06], dtype="float64").to_numpy()
    one.lows = pd.Series([1.0, 1.055], dtype="float64").to_numpy()
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        reference_high=1.2,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.2,
        reference_leg_size=0.2,
        hold_floor=1.10,
        stage1_hold_price=1.05,
        pump_range_5m=0.2,
    )

    assert engine._resolve_stage1_hold_status(one=one, idx=1, stage1=stage1) == "held_above_hold"


def test_stage1_hold_status_allows_shallow_hold_sweep() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000], price=1.0)
    one.closes = pd.Series([1.0, 1.141], dtype="float64").to_numpy()
    one.lows = pd.Series([1.0, 1.095], dtype="float64").to_numpy()
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        reference_high=1.2,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.2,
        reference_leg_size=0.2,
        hold_floor=1.15,
        stage1_hold_price=1.15,
        pump_range_5m=0.2,
    )

    assert engine._resolve_stage1_hold_status(one=one, idx=1, stage1=stage1) == "held_above_hold"


def test_stage1_hold_status_allows_hold_price_overshoot_when_reference_leg_stays_alive() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000], price=1.0)
    one.closes = pd.Series([1.0, 1.658], dtype="float64").to_numpy()
    one.lows = pd.Series([1.0, 1.63], dtype="float64").to_numpy()
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.70,
        reference_high=2.20,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.70,
        reference_leg_size=1.20,
        hold_floor=1.80,
        stage1_hold_price=1.80,
        pump_range_5m=0.70,
    )

    assert engine._resolve_stage1_hold_status(one=one, idx=1, stage1=stage1) == "held_above_hold"


def test_allows_pullback_search_after_inplay_end_when_stage1_is_fresh_and_holding() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=1.0)
    one.closes[:] = np.array([1.0, 1.18, 1.15, 1.14], dtype=np.float64)
    one.lows[:] = np.array([1.0, 1.12, 1.11, 1.10], dtype=np.float64)
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[1.2],
        lows=[1.0],
        closes=[1.13],
        ema20=[1.14],
    )
    five.v5[:] = np.array([0.05], dtype=np.float64)
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.18,
        reference_high=1.18,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.18,
        reference_leg_size=0.18,
        hold_floor=1.10,
        stage1_hold_price=1.09,
        pump_range_5m=0.18,
    )

    assert engine._allows_pullback_search_after_inplay_end(
        one=one,
        five=five,
        idx=3,
        five_idx=0,
        stage1=stage1,
    ) is True


def test_leg_start_status_allows_minor_undercut_inside_tolerance() -> None:
    engine = PnoEngine()
    five = _build_test_five_frame(
        opens=[1.0, 1.3],
        highs=[1.3, 1.32],
        lows=[1.0, 1.191],
        closes=[1.25, 1.191],
        ema20=[1.0, 1.1],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=1,
        active_high_timestamp=300_000,
        active_high=1.32,
        reference_high=1.32,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.2,
        leg_size=0.12,
        reference_leg_size=0.12,
        hold_floor=1.25,
        stage1_hold_price=1.24,
        pump_range_5m=0.12,
    )

    assert engine._resolve_leg_start_status(five=five, five_idx=1, stage1=stage1) == "held_above_leg_start"


def test_stage1_context_allows_leg_start_wick_touch_when_close_holds(monkeypatch) -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        min_stage1_leg_v1=0.05,
        min_stage1_leg_v5_fraction=0.05,
        stage1_hold_fraction=0.5,
    )
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000], price=1.0)
    one.highs = pd.Series([1.0, 1.2], dtype="float64").to_numpy()
    one.lows = pd.Series([1.0, 1.0], dtype="float64").to_numpy()
    one.closes = pd.Series([1.0, 1.15], dtype="float64").to_numpy()
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[1.2],
        lows=[1.0],
        closes=[1.15],
        ema20=[1.0],
    )
    five.pump_start_idx[:] = 0
    five.sleep_start_idx[:] = 0
    five.sleep_end_idx[:] = 0
    five.stage1_confirm_idx[:] = 0

    monkeypatch.setattr(engine, "_resolve_leg_start", lambda **_kwargs: (0, 1.0))
    monkeypatch.setattr(
        engine,
        "_resolve_stage1_quality_metrics",
        lambda **_kwargs: (
            {
                "cumulative_quote_volume": 300_000.0,
                "pre_pump_ema_crosses_1h": 1,
                "pre_pump_barcode_fraction_1h": 0.1,
                "pre_pump_high_24h": 1.0,
                "pre_pump_high_1h": 1.0,
                "pump_pre_atr": 0.1,
                "pump_impulse_atr_pre": 2.0,
                "pump_peak_bar_tr_atr_pre": 1.5,
                "pump_volume_ratio_start": 4.0,
                "pump_volume_ratio_continue": 1.2,
                "pump_path_efficiency": 0.4,
                "pump_wick_share": 0.4,
                "pump_body_share_mean": 0.5,
                "pump_flat_body_share": 0.2,
                "pump_body_wick_edge": 0.1,
                "pump_micro_flat_bar_share": 0.2,
                "active_high_bar_body_share": 0.6,
                "active_high_bar_upper_wick_share": 0.3,
                "active_high_bar_close_position": 0.8,
                "pump_max_red_body_share_5m": 0.1,
                "pump_counterflow_ratio_5m": 0.1,
                "pump_max_red_body_share_1m": 0.1,
                "pump_counterflow_ratio_1m": 0.1,
                "reference_high": 1.2,
                "reference_high_weight": 1.0,
            },
            None,
        ),
    )

    stage1, rejection = engine._resolve_stage1_context(
        one=one,
        five=five,
        idx=1,
        five_idx=0,
        params=params,
    )

    assert rejection is None
    assert stage1 is not None


def test_stage1_context_allows_small_confirm_close_below_hold_reject_when_hold_price_overshoots(monkeypatch) -> None:
    engine = PnoEngine()
    params = PnoParams(
        symbol="TEST/USDT",
        min_stage1_leg_v1=0.05,
        min_stage1_leg_v5_fraction=0.05,
        stage1_hold_fraction=0.5,
    )
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000], price=1.0)
    one.highs = pd.Series([1.0, 1.2], dtype="float64").to_numpy()
    one.lows = pd.Series([1.0, 1.19], dtype="float64").to_numpy()
    one.closes = pd.Series([1.0, 1.1995], dtype="float64").to_numpy()
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[1.2],
        lows=[1.0],
        closes=[1.1995],
        ema20=[1.0],
    )
    five.pump_start_idx[:] = 0
    five.sleep_start_idx[:] = 0
    five.sleep_end_idx[:] = 0
    five.stage1_confirm_idx[:] = 0
    five.stage1_hold_price[:] = 1.204

    monkeypatch.setattr(engine, "_resolve_leg_start", lambda **_kwargs: (0, 1.0))
    monkeypatch.setattr(
        engine,
        "_resolve_stage1_quality_metrics",
        lambda **_kwargs: (
            {
                "cumulative_quote_volume": 300_000.0,
                "pre_pump_ema_crosses_1h": 1,
                "pre_pump_barcode_fraction_1h": 0.1,
                "pre_pump_high_24h": 1.0,
                "pre_pump_high_1h": 1.0,
                "pump_pre_atr": 0.1,
                "pump_impulse_atr_pre": 2.0,
                "pump_peak_bar_tr_atr_pre": 1.5,
                "pump_volume_ratio_start": 4.0,
                "pump_volume_ratio_continue": 1.2,
                "pump_path_efficiency": 0.4,
                "pump_wick_share": 0.4,
                "pump_body_share_mean": 0.5,
                "pump_flat_body_share": 0.2,
                "pump_body_wick_edge": 0.1,
                "pump_micro_flat_bar_share": 0.2,
                "active_high_bar_body_share": 0.6,
                "active_high_bar_upper_wick_share": 0.3,
                "active_high_bar_close_position": 0.8,
                "pump_max_red_body_share_5m": 0.1,
                "pump_counterflow_ratio_5m": 0.1,
                "pump_max_red_body_share_1m": 0.1,
                "pump_counterflow_ratio_1m": 0.1,
                "reference_high": 1.24,
                "reference_high_weight": 1.0,
            },
            None,
        ),
    )

    stage1, rejection = engine._resolve_stage1_context(
        one=one,
        five=five,
        idx=1,
        five_idx=0,
        params=params,
    )

    assert rejection is None
    assert stage1 is not None


def test_run_keeps_confirmed_stage1_alive_when_rebuild_drops_but_hold_is_intact(monkeypatch) -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT")
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000], price=1.0)
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[1.2],
        lows=[1.0],
        closes=[1.15],
        ema20=[1.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        reference_high=1.2,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.2,
        reference_leg_size=0.2,
        hold_floor=1.1,
        pump_range_5m=0.2,
    )
    call_count = {"value": 0}

    def _fake_resolve_stage1_context(**_kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return stage1, None
        return None, None

    monkeypatch.setattr(engine, "_scale_required_bars", lambda **_kwargs: 1)
    monkeypatch.setattr(engine, "_resolve_stage1_context", _fake_resolve_stage1_context)
    monkeypatch.setattr(engine, "_resolve_stage1_hold_status", lambda **_kwargs: "held_above_hold")
    monkeypatch.setattr(engine, "_resolve_leg_start_status", lambda **_kwargs: "held_above_leg_start")
    monkeypatch.setattr(engine, "_resolve_stage1_pump_candidate_rejection", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "_resolve_stage2_context", lambda **_kwargs: None)

    diagnostics: dict[str, object] = {}
    trades = engine._run(one=one, five=five, params=params, diagnostics=diagnostics)

    assert trades == []
    assert int(diagnostics["stage_hits"]["stage_1_pump"]) == 1
    assert diagnostics.get("stage_rejections", []) == []


def test_run_rebuilds_same_stage4_cycle_on_later_bars(monkeypatch) -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT")
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000], price=1.0)
    one.opens[:] = np.array([1.0, 1.14, 1.15], dtype="float64")
    one.highs[:] = np.array([1.02, 1.16, 1.17], dtype="float64")
    one.lows[:] = np.array([0.99, 1.10, 1.11], dtype="float64")
    one.closes[:] = np.array([1.01, 1.15, 1.16], dtype="float64")
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    five = _build_test_five_frame(
        opens=[1.0],
        highs=[1.2],
        lows=[1.0],
        closes=[1.15],
        ema20=[1.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        reference_high=1.2,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.2,
        reference_leg_size=0.2,
        hold_floor=1.1,
        pump_range_5m=0.2,
    )
    stage2 = Stage2Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=1.05,
        pullback_depth=0.15,
        pullback_age_bars=1,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=1.05,
        pullback_depth=0.15,
        pullback_age_bars=1,
        validation_timestamp=60_000,
    )
    stage4 = _build_test_stage4_context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=1.05,
        pullback_depth=0.15,
        cluster_indices=(1,),
        cluster_prices=(1.12,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        level=1.12,
        level_valid_idx=1,
        level_valid_timestamp=60_000,
        entry_plan=1.13,
        sl_plan=0.8,
        low_last_red_plan=0.8,
        tp1=1.2,
        tp2=1.3,
    )
    rebuild_calls = {"value": 0}

    monkeypatch.setattr(engine, "_scale_required_bars", lambda **_kwargs: 1)
    monkeypatch.setattr(engine, "_resolve_stage1_context", lambda **_kwargs: (stage1, None))
    monkeypatch.setattr(engine, "_resolve_stage2_context", lambda **_kwargs: stage2 if _kwargs["idx"] >= 1 else None)
    monkeypatch.setattr(engine, "_resolve_stage3_context", lambda **_kwargs: (stage3, None) if _kwargs["idx"] >= 1 else (None, None))
    monkeypatch.setattr(engine, "_resolve_stage4_context", lambda **_kwargs: stage4 if _kwargs["idx"] >= 1 else None)

    def _fake_rebuild_stage4_scores(**kwargs):
        rebuild_calls["value"] += 1
        if rebuild_calls["value"] == 1:
            return kwargs["stage4"]
        return replace(kwargs["stage4"], hard_block=True, hard_block_reason="level_too_stale")

    monkeypatch.setattr(engine, "_rebuild_stage4_scores", _fake_rebuild_stage4_scores)
    monkeypatch.setattr(engine, "_try_enter_and_simulate", lambda **_kwargs: (None, _kwargs["armed"].entry_idx))

    diagnostics = engine._empty_diagnostics()
    trades = engine._run(one=one, five=five, params=params, diagnostics=diagnostics)

    assert trades == []
    assert rebuild_calls["value"] >= 2
    assert any(item.get("reason") == "level_too_stale" for item in diagnostics.get("stage_rejections", []))


def test_run_keeps_armed_stage4_stop_frozen_until_trade_or_invalidation(monkeypatch) -> None:
    engine = PnoEngine()
    params = PnoParams(symbol="TEST/USDT", entry_confirmation_mode="close_above")
    one = _build_test_one_frame_multi(timestamps_ms=[0, 60_000, 120_000, 180_000], price=1.0)
    one.opens[:] = np.array([1.0, 1.10, 1.11, 1.12], dtype="float64")
    one.highs[:] = np.array([1.01, 1.12, 1.13, 1.14], dtype="float64")
    one.lows[:] = np.array([0.99, 1.08, 1.09, 1.10], dtype="float64")
    one.closes[:] = np.array([1.0, 1.11, 1.12, 1.13], dtype="float64")
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    five = _build_test_five_frame(opens=[1.0], highs=[1.2], lows=[1.0], closes=[1.15], ema20=[1.0])
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        reference_high=1.2,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=1.0,
        leg_size=0.2,
        reference_leg_size=0.2,
        hold_floor=1.1,
        pump_range_5m=0.2,
    )
    stage2 = Stage2Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=1.05,
        pullback_depth=0.15,
        pullback_age_bars=1,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=1.05,
        pullback_depth=0.15,
        pullback_age_bars=1,
        validation_timestamp=60_000,
    )
    stage4 = _build_test_stage4_context(
        active_high_idx=1,
        active_high_timestamp=60_000,
        active_high=1.2,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=1.05,
        pullback_depth=0.15,
        cluster_indices=(1,),
        cluster_prices=(1.12,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        level=1.12,
        level_valid_idx=1,
        level_valid_timestamp=60_000,
        entry_plan=1.13,
        sl_plan=0.8,
        low_last_red_plan=0.8,
        tp1=1.2,
        tp2=1.3,
        is_valid_setup=True,
    )
    rebuild_calls = {"value": 0}
    observed_stops: list[float] = []

    monkeypatch.setattr(engine, "_scale_required_bars", lambda **_kwargs: 1)
    monkeypatch.setattr(engine, "_resolve_stage1_context", lambda **_kwargs: (stage1, None))
    monkeypatch.setattr(engine, "_resolve_stage2_context", lambda **_kwargs: stage2 if _kwargs["idx"] >= 1 else None)
    monkeypatch.setattr(engine, "_resolve_stage3_context", lambda **_kwargs: (stage3, None) if _kwargs["idx"] >= 1 else (None, None))
    monkeypatch.setattr(engine, "_resolve_stage4_context", lambda **_kwargs: stage4 if _kwargs["idx"] >= 1 else None)

    def _fake_rebuild_stage4_scores(**kwargs):
        rebuild_calls["value"] += 1
        if rebuild_calls["value"] == 1:
            return kwargs["stage4"]
        return replace(kwargs["stage4"], sl_plan=0.7, low_last_red_plan=0.7)

    def _fake_try_enter_and_simulate(**kwargs):
        observed_stops.append(float(kwargs["armed"].stage4.sl_plan))
        return None, kwargs["armed"].entry_idx

    monkeypatch.setattr(engine, "_rebuild_stage4_scores", _fake_rebuild_stage4_scores)
    monkeypatch.setattr(engine, "_try_enter_and_simulate", _fake_try_enter_and_simulate)

    diagnostics = engine._empty_diagnostics()
    trades = engine._run(one=one, five=five, params=params, diagnostics=diagnostics)

    assert trades == []
    assert rebuild_calls["value"] >= 2
    assert observed_stops
    assert all(stop == pytest.approx(0.8) for stop in observed_stops)


def test_export_stage_reviews_filters_stage1_rejections_to_borderline_cases(tmp_path, monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": [60_000, 120_000],
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "close": [1.1, 1.2],
            "volume": [10.0, 11.0],
        }
    )
    symbol_frames = {
        "BTC/USDT": SymbolMtfFrames(
            levels_timeframe=Timeframe.M5,
            entry_timeframe=Timeframe.M1,
            levels_frame=frame,
            entry_frame=frame,
        )
    }

    rendered_reasons: list[str] = []

    def _fake_render_stage_review_chart(**kwargs):
        rendered_reasons.append(str(kwargs["reason"]))
        return str(Path(kwargs["charts_dir"]) / "chart.png")

    monkeypatch.setattr(pno_diagnostics, "_render_pno_stage_review_chart", _fake_render_stage_review_chart)

    pno_diagnostics._export_pno_stage_reviews(
        diagnostics_dir=tmp_path,
        symbol_frames=symbol_frames,
        stage_rows_by_stage={},
        stage_rejections_by_stage={
            "stage_1_pump": {
                "volume_ratio_start_too_small": [
                    {
                        "symbol": "BTC/USDT",
                        "timestamp_ms": 60_000,
                        "reason": "volume_ratio_start_too_small",
                        "pump_volume_ratio_start": 2.95,
                        "stage1_min_volume_ratio_start": 3.0,
                    },
                    {
                        "symbol": "BTC/USDT",
                        "timestamp_ms": 120_000,
                        "reason": "volume_ratio_start_too_small",
                        "pump_volume_ratio_start": 0.8,
                        "stage1_min_volume_ratio_start": 3.0,
                    },
                ]
            }
        },
        selected_stage_ids=("stage_1_pump",),
    )

    manifest = pd.read_csv(tmp_path / "stage_reviews" / "manifest.csv")
    stage1_row = manifest.loc[manifest["stage_id"] == "stage_1_pump"].iloc[0]
    assert int(stage1_row["rejected_count"]) == 2
    assert int(stage1_row["rejected_review_count"]) == 1
    assert int(stage1_row["rejected_filtered_count"]) == 1
    assert int(stage1_row["rejected_charts_count"]) == 1

    events = pd.read_csv(
        tmp_path
        / "stage_reviews"
        / "stage_1_pump"
        / "rejected"
        / "volume_ratio_start_too_small"
        / "events.csv"
    )
    assert len(events) == 1
    assert float(events.iloc[0]["pump_volume_ratio_start"]) == pytest.approx(2.95)
    assert rendered_reasons == ["volume_ratio_start_too_small"]


def test_stage_review_sleep_baseline_uses_full_24h_but_chart_starts_24_bars_before_pump() -> None:
    pump_start_ms = 24 * 60 * 60_000
    step_ms = 5 * 60_000
    timestamps = list(range(0, pump_start_ms, step_ms))
    volumes = [10.0] * len(timestamps)
    volumes[-1] = 1000.0
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [1.0] * len(timestamps),
            "high": [1.0] * len(timestamps),
            "low": [1.0] * len(timestamps),
            "close": [1.0] * len(timestamps),
            "volume": volumes,
        }
    )

    baseline = pno_diagnostics._resolve_pno_sleep_volume_baseline(
        levels_frame=frame,
        entry_frame=frame,
        plot_frame=frame,
        pump_start_timestamp_ms=pump_start_ms,
        sleep_start_timestamp_ms=pump_start_ms - step_ms,
        use_levels_frame=True,
    )
    start_timestamp_ms = pno_diagnostics._resolve_pno_stage_review_start_timestamp(
        timestamp_ms=pump_start_ms,
        pump_start_timestamp_ms=pump_start_ms,
        sleep_start_timestamp_ms=pump_start_ms - step_ms,
        levels_step_ms=step_ms,
        entry_step_ms=step_ms,
        include_long_sleep_context=True,
    )

    assert baseline == pytest.approx(sum(volumes) / len(volumes))
    assert start_timestamp_ms == pump_start_ms - 24 * step_ms


def test_stage1_candidate_can_be_forced_to_literal_flow_anchor() -> None:
    engine = PnoEngine()
    timestamps = pd.Series([i * 5 * 60_000 for i in range(6)], dtype="int64").to_numpy()
    highs = pd.Series([1.0, 1.1, 2.0, 3.0, 2.6, 2.2], dtype="float64").to_numpy()
    lows = pd.Series([0.9, 1.0, 1.8, 2.8, 2.4, 2.1], dtype="float64").to_numpy()
    params = PnoParams(symbol="TEST/USDT:USDT")

    candidate = engine._resolve_htf_stage1_candidate_from_arrays(
        timestamps=timestamps,
        highs=highs,
        lows=lows,
        five_idx=5,
        params=params,
        levels_timeframe_ms=5 * 60_000,
        forced_pump_start_5m_idx=1,
    )

    assert candidate is not None
    assert int(candidate["pump_start_5m_idx"]) == 1
    assert int(candidate["pump_start_timestamp"]) == int(timestamps[1])


def test_stage1_candidate_rejects_impossible_forced_flow_anchor() -> None:
    engine = PnoEngine()
    timestamps = pd.Series([i * 5 * 60_000 for i in range(6)], dtype="int64").to_numpy()
    highs = pd.Series([1.0, 1.1, 2.0, 3.0, 2.6, 2.2], dtype="float64").to_numpy()
    lows = pd.Series([0.9, 1.0, 1.8, 2.8, 2.4, 2.1], dtype="float64").to_numpy()
    params = PnoParams(symbol="TEST/USDT:USDT")

    candidate = engine._resolve_htf_stage1_candidate_from_arrays(
        timestamps=timestamps,
        highs=highs,
        lows=lows,
        five_idx=5,
        params=params,
        levels_timeframe_ms=5 * 60_000,
        forced_pump_start_5m_idx=5,
    )

    assert candidate is None


def test_stage1_candidate_does_not_reject_future_deep_pullback() -> None:
    engine = PnoEngine()
    timestamps = pd.Series([i * 5 * 60_000 for i in range(5)], dtype="int64").to_numpy()
    highs = pd.Series([1.0, 1.1, 3.0, 2.6, 2.4], dtype="float64").to_numpy()
    lows = pd.Series([0.9, 1.0, 2.8, 1.2, 1.1], dtype="float64").to_numpy()
    params = PnoParams(symbol="TEST/USDT:USDT")

    candidate = engine._resolve_htf_stage1_candidate_from_arrays(
        timestamps=timestamps,
        highs=highs,
        lows=lows,
        five_idx=4,
        params=params,
        levels_timeframe_ms=5 * 60_000,
        forced_pump_start_5m_idx=1,
    )

    assert candidate is not None
    assert int(candidate["pump_start_5m_idx"]) == 1
    assert float(candidate["pullback_low"]) < float(candidate["min_allowed_low"])


def test_stage2_rejects_pullback_below_stage1_min_allowed_low() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000, 120_000],
                "open": [10.0, 11.0, 10.4],
                "high": [11.0, 11.1, 10.5],
                "low": [10.0, 10.8, 10.0],
                "close": [10.9, 10.9, 10.2],
                "volume": [10.0, 10.0, 10.0],
            }
        )
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.9],
        highs=[11.0, 10.5],
        lows=[10.0, 10.0],
        closes=[10.9, 10.2],
        ema20=[9.9, 10.0],
    )
    stage1 = _build_test_stage1_context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start=10.0,
        leg_size=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=2,
        htf_min_allowed_low=10.5,
    )

    stage2 = engine._resolve_stage2_context(
        one=one,
        five=five,
        idx=2,
        five_idx=1,
        stage1=stage1,
        params=PnoParams(symbol="TEST/USDT:USDT"),
    )

    assert stage2 is None


def test_stage3_rejects_pullback_close_below_leg_start() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000],
                "open": [10.0, 10.2],
                "high": [11.0, 10.4],
                "low": [10.0, 9.8],
                "close": [10.9, 9.9],
                "volume": [10.0, 10.0],
            }
        )
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.2],
        highs=[11.0, 10.4],
        lows=[10.0, 9.8],
        closes=[10.9, 9.9],
        ema20=[9.9, 10.0],
    )
    stage1 = _build_test_stage1_context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        reference_high=11.0,
        leg_start=10.0,
        leg_size=1.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=2,
        htf_min_allowed_low=0.0,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=11.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=60_000,
        pullback_low=9.8,
        pullback_depth=1.2,
        pullback_age_bars=1,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=1,
        five_idx=1,
        stage1=stage1,
        stage2=stage2,
        params=PnoParams(
            symbol="TEST/USDT:USDT",
            stage1_active_context_min_start_fraction=0.0,
            stage1_active_context_min_baseline_ratio=0.0,
        ),
    )

    assert stage3 is None
    assert reason == "pullback_closed_below_leg_start"


def test_stage1_state_confirms_flow_hold_only_after_closed_hold_bars() -> None:
    engine = PnoEngine()
    timestamps = pd.Series([i * 5 * 60_000 for i in range(7)], dtype="int64").to_numpy()
    levels_frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [1.0, 1.0, 1.00, 1.08, 1.14, 1.18, 1.19],
            "high": [1.0, 1.0, 1.12, 1.16, 1.21, 1.22, 1.23],
            "low": [0.99, 0.99, 1.00, 1.05, 1.10, 1.14, 1.15],
            "close": [1.0, 1.0, 1.10, 1.14, 1.19, 1.20, 1.21],
            "sleep": [False, True, False, False, False, False, False],
            "wake": [False, False, True, False, False, False, False],
            "ema9": [0.95] * 7,
            "ema20": [0.90] * 7,
            "quote_volume": [10.0, 10.0, 100.0, 40.0, 40.0, 20.0, 20.0],
            "trade_activity": [10.0, 10.0, 100.0, 40.0, 40.0, 20.0, 20.0],
            "pre_quote_median_24": [10.0] * 7,
            "pre_trade_median_24": [10.0] * 7,
            "activity_last6_quote": [np.nan] * 7,
            "activity_prev24_quote": [np.nan] * 7,
            "activity_last6_trade": [np.nan] * 7,
            "activity_prev24_trade": [np.nan] * 7,
        }
    )
    params = PnoParams(symbol="TEST/USDT:USDT", stage1_min_pump_pct=0.01)

    state = engine._build_fallback_stage1_state(
        levels_frame=levels_frame,
        entry_frame=pd.DataFrame(),
        timestamps=timestamps,
        ema20=levels_frame["ema20"].to_numpy(dtype=np.float64),
        params=params,
        levels_timeframe_ms=5 * 60_000,
        entry_timeframe_ms=60_000,
    )

    assert state.pump_start_idx[2] == -1
    assert state.pump_start_idx[3] == -1
    assert state.pump_start_idx[4] == 2
    assert state.stage1_confirm_idx[4] == 4


def test_stage4_compression_score_does_not_read_future_lows() -> None:
    engine = PnoEngine()
    base = pd.DataFrame(
        {
            "timestamp": [idx * 60_000 for idx in range(6)],
            "open": [10.0, 10.1, 10.0, 10.0, 10.0, 10.0],
            "high": [10.4, 10.5, 10.2, 10.1, 10.1, 10.1],
            "low": [10.0, 10.0, 9.95, 9.9, 9.8, 9.8],
            "close": [10.2, 10.2, 10.0, 10.0, 10.0, 10.0],
            "volume": [1.0] * 6,
        }
    )
    with_future_dump = base.copy()
    with_future_dump.loc[4:, "low"] = [1.0, 1.0]
    stage4 = _build_test_stage4_context(
        cluster_indices=(0, 1),
        cluster_prices=(10.4, 10.5),
        cluster_first_idx=0,
        cluster_last_idx=1,
    )

    clean_score = engine._resolve_compression_score(
        one=engine._prepare_1m_frame(base),
        idx=2,
        stage4=stage4,
        confirmed_lows=[],
    )
    future_dump_score = engine._resolve_compression_score(
        one=engine._prepare_1m_frame(with_future_dump),
        idx=2,
        stage4=stage4,
        confirmed_lows=[],
    )

    assert future_dump_score == clean_score


def test_pno_level_cluster_allows_clear_single_touch_level() -> None:
    engine = PnoEngine()
    highs = pd.Series([1.0, 2.0, 5.0, 4.8, 3.7, 4.0, 3.8], dtype="float64").to_numpy()
    lows = pd.Series([0.8, 1.6, 4.2, 3.6, 2.8, 3.3, 3.1], dtype="float64").to_numpy()
    opens = pd.Series([0.9, 1.8, 4.8, 4.2, 3.0, 3.6, 3.5], dtype="float64").to_numpy()
    closes = pd.Series([0.95, 1.9, 4.5, 3.8, 3.2, 3.7, 3.6], dtype="float64").to_numpy()
    timestamps = pd.Series([60_000, 120_000, 180_000, 240_000, 300_000, 360_000, 420_000], dtype="int64").to_numpy()
    v1 = pd.Series([1.0] * len(highs), dtype="float64").to_numpy()
    confirmed_high_indices, confirmed_high_confirmed_at = engine._build_confirmed_high_map(highs=highs, lows=lows, v1=v1)
    confirmed_low_indices, confirmed_low_confirmed_at = engine._build_confirmed_low_map(highs=highs, lows=lows, v1=v1)
    one = OneMinuteFrame(
        frame=pd.DataFrame(),
        timestamps=timestamps,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=pd.Series([1.0] * len(highs), dtype="float64").to_numpy(),
        quote_volume=pd.Series([1.0] * len(highs), dtype="float64").to_numpy(),
        cumulative_quote_volume=pd.Series(range(1, len(highs) + 1), dtype="float64").to_numpy(),
        tr=v1,
        v1=v1,
        red=pd.Series([False, False, True, True, True, False, False], dtype="bool").to_numpy(),
        confirmed_high_indices=confirmed_high_indices,
        confirmed_high_confirmed_at=confirmed_high_confirmed_at,
        confirmed_low_indices=confirmed_low_indices,
        confirmed_low_confirmed_at=confirmed_low_confirmed_at,
        low_range_tree=engine._build_range_tree(lows, is_min_tree=True),
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=120_000,
        active_high=10.0,
        pullback_start_idx=1,
        pullback_low_idx=4,
        pullback_low_timestamp=300_000,
        pullback_low=2.8,
        pullback_depth=1.8,
        pullback_age_bars=5,
        validation_timestamp=420_000,
    )

    cluster = engine._resolve_level_cluster(
        one=one,
        idx=6,
        stage3=stage3,
        confirmed_highs=[2],
        confirmed_lows=[4],
        params=PnoParams(symbol="TEST/USDT"),
        retired_clusters=[],
    )

    assert cluster == ((2,), (5.0,))


def test_pno_level_cluster_uses_local_peak_fallback_when_confirmed_highs_missing() -> None:
    engine = PnoEngine()
    highs = pd.Series([1.0, 2.0, 5.0, 4.6, 4.9, 4.2], dtype="float64").to_numpy()
    lows = pd.Series([0.8, 1.6, 4.2, 3.8, 4.0, 3.7], dtype="float64").to_numpy()
    opens = pd.Series([0.9, 1.8, 4.8, 4.2, 4.5, 3.9], dtype="float64").to_numpy()
    closes = pd.Series([0.95, 1.9, 4.6, 4.0, 4.7, 3.8], dtype="float64").to_numpy()
    timestamps = pd.Series([60_000, 120_000, 180_000, 240_000, 300_000, 360_000], dtype="int64").to_numpy()
    v1 = pd.Series([1.0] * len(highs), dtype="float64").to_numpy()
    one = OneMinuteFrame(
        frame=pd.DataFrame(),
        timestamps=timestamps,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=pd.Series([1.0] * len(highs), dtype="float64").to_numpy(),
        quote_volume=pd.Series([1.0] * len(highs), dtype="float64").to_numpy(),
        cumulative_quote_volume=pd.Series(range(1, len(highs) + 1), dtype="float64").to_numpy(),
        tr=v1,
        v1=v1,
        red=pd.Series([False, False, True, True, False, True], dtype="bool").to_numpy(),
        confirmed_high_indices=pd.Series([], dtype="int64").to_numpy(),
        confirmed_high_confirmed_at=pd.Series([], dtype="int64").to_numpy(),
        confirmed_low_indices=pd.Series([], dtype="int64").to_numpy(),
        confirmed_low_confirmed_at=pd.Series([], dtype="int64").to_numpy(),
        low_range_tree=engine._build_range_tree(lows, is_min_tree=True),
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=120_000,
        active_high=10.0,
        pullback_start_idx=1,
        pullback_low_idx=3,
        pullback_low_timestamp=240_000,
        pullback_low=3.8,
        pullback_depth=1.8,
        pullback_age_bars=4,
        validation_timestamp=360_000,
    )

    cluster = engine._resolve_level_cluster(
        one=one,
        idx=5,
        stage3=stage3,
        confirmed_highs=[],
        confirmed_lows=[],
        params=PnoParams(symbol="TEST/USDT", level_cluster_relaxed_spread_v1=0.05),
        retired_clusters=[],
    )

    assert cluster == ((2,), (5.0,))


def test_pno_incomplete_trade_is_not_emitted() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000],
                "open": [9.8, 9.9, 9.95],
                "high": [10.0, 10.1, 10.05],
                "low": [9.7, 9.85, 9.9],
                "close": [9.9, 9.95, 10.0],
                "volume": [10.0, 11.0, 12.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            cluster_indices=(0, 1),
            cluster_prices=(9.95, 10.0),
            level=9.95,
            level_pos=0.45,
            touches=2,
            cluster_first_idx=0,
            cluster_last_idx=1,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=10,
            score_e=7,
            score_tp2=5,
            final_score=80.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(symbol="TEST/USDT", pno_r_trade=20.0, min_entry_rr=0.0),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_ideal_like_impulse_can_ignore_decay_only_in_discovery_mode(monkeypatch) -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000],
                "open": [10.0, 10.1, 10.2],
                "high": [10.2, 10.25, 10.3],
                "low": [9.98, 10.05, 10.15],
                "close": [10.1, 10.2, 10.25],
                "volume": [10.0, 11.0, 12.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.8,
            reference_high=10.8,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=10.0,
            leg_size=0.8,
            reference_leg_size=0.8,
            pump_range_5m=0.8,
            hold_floor=10.2,
            pump_impulse_atr_pre=20.0,
            pump_peak_bar_tr_atr_pre=8.0,
            pump_volume_ratio_start=10.0,
            pump_path_efficiency=0.7,
            pump_wick_share=0.3,
            pump_body_share_mean=0.6,
            pump_body_wick_edge=0.2,
            pump_micro_flat_bar_share=0.0,
            active_high_bar_upper_wick_share=0.2,
            pump_counterflow_ratio_5m=0.0,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.8,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=10.0,
            pullback_depth=0.8,
            pullback_age_bars=2,
            validation_timestamp=120_000,
        ),
        stage4=_build_test_stage4_context(
            active_high=10.8,
            pullback_low=10.0,
            level=10.2,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            entry_plan=10.25,
            sl_plan=10.0,
            low_last_red_plan=10.0,
            tp1=10.8,
            tp2=11.6,
        ),
    )

    monkeypatch.setattr(engine, "_resolve_close_above_pre_signal_decay_reason", lambda **kwargs: "level_drifted_too_long_before_signal")

    blocked = engine._is_armed_entry_invalidated_before_trigger(
        one=one,
        idx=1,
        armed=armed,
        params=PnoParams(symbol="TEST/USDT"),
    )
    allowed = engine._is_armed_entry_invalidated_before_trigger(
        one=one,
        idx=1,
        armed=armed,
        params=PnoParams(symbol="TEST/USDT", ideal_like_impulse_enabled=True, ideal_like_ignore_decay_invalidation=True),
    )

    assert blocked is True
    assert allowed is False


def test_pno_close_above_confirmation_enters_on_next_bar() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.8, 9.9, 10.02, 10.05],
                "high": [10.0, 10.1, 10.1, 11.1],
                "low": [9.7, 9.85, 9.98, 10.0],
                "close": [9.9, 10.02, 10.05, 11.0],
                "volume": [10.0, 10.0, 30.0, 13.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._simulate_trade_path(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
            min_entry_rr=0.0,
        ),
        armed=armed,
        entry_idx=1,
        entry_price=10.0,
        stop_loss=9.7,
        position_size=1.0,
        metadata={
            "entry_confirmation_mode": "close_above",
            "pump_to_peak_bars": 1,
            "pump_to_peak_minutes": 1.0,
        },
    )

    assert trade is not None
    assert trade.entry_timestamp_ms == 180_000
    assert trade.result_type == TradeResultType.TP2
    assert trade.metadata["entry_confirmation_mode"] == "close_above"
    assert trade.metadata["entry_signal_kind"] == "close_above"
    assert trade.metadata["entry_signal_timestamp_ms"] == 120_000
    assert trade.metadata["base_final_score"] == pytest.approx(76.0)
    assert trade.metadata["final_score"] > trade.metadata["base_final_score"]
    assert trade.metadata["trigger_score_body_close"] >= -2
    assert trade.metadata["trigger_score_overhead"] > 0
    assert trade.metadata["entry_price_actual"] == pytest.approx(10.02)
    assert trade.metadata["sl_actual"] == pytest.approx(9.5)
    assert trade.metadata["tp1"] == pytest.approx(10.5)
    assert trade.metadata["tp2"] == pytest.approx(11.0)
    assert trade.metadata["be_protect_price"] >= trade.metadata["entry_price_actual"]
    assert trade.metadata["tp1"] > trade.metadata["entry_price_actual"]
    assert exit_idx == 3


def test_pno_close_above_uses_exact_last_red_extremum_as_stop() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.9, 10.0, 10.02, 10.95],
                "high": [10.0, 10.4, 11.1, 11.2],
                "low": [9.8, 9.4, 10.0, 10.9],
                "close": [9.95, 10.3, 10.95, 11.1],
                "volume": [10.0, 15.0, 12.0, 13.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.9,
            reference_high=10.9,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.9,
            reference_leg_size=1.9,
            pump_range_5m=1.9,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.9,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.6,
            pullback_age_bars=2,
            validation_timestamp=60_000,
            post_high_wick_share=0.35,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.9,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.6,
            cluster_indices=(0,),
            cluster_prices=(10.0,),
            level=10.0,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.02,
            sl_plan=9.7,
            low_last_red_plan=9.7,
            tp1=10.9,
            tp2=11.2,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._simulate_trade_path(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
            min_entry_rr=0.0,
        ),
        armed=armed,
        entry_idx=1,
        entry_price=10.0,
        stop_loss=9.7,
        position_size=1.0,
        metadata={
            "entry_confirmation_mode": "close_above",
            "pump_to_peak_bars": 1,
            "pump_to_peak_minutes": 1.0,
        },
    )

    assert trade is not None
    assert trade.metadata["sl_actual"] == pytest.approx(9.7)
    assert trade.metadata["initial_stop_loss"] == pytest.approx(9.7)
    assert exit_idx == 3


def test_pno_close_above_arms_be_at_80pct_to_tp1_before_tp1() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.9, 10.0, 10.0, 10.08],
                "high": [10.0, 10.1, 10.41, 10.12],
                "low": [9.8, 9.95, 10.18, 10.00],
                "close": [9.95, 10.05, 10.36, 10.02],
                "volume": [10.0, 15.0, 12.0, 13.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=_build_test_stage1_context(active_high=10.5, reference_high=10.5),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=_build_test_stage4_context(active_high=10.5, entry_plan=10.0, low_last_red_plan=9.7, tp1=10.5),
    )

    trade, exit_idx = engine._simulate_trade_path(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            min_entry_rr=0.0,
        ),
        armed=armed,
        entry_idx=1,
        entry_price=10.0,
        stop_loss=9.7,
        position_size=1.0,
        metadata={
            "entry_confirmation_mode": "close_above",
            "pump_to_peak_bars": 1,
            "pump_to_peak_minutes": 1.0,
        },
    )

    assert trade is not None
    assert trade.result_type == TradeResultType.TP1_BE
    assert trade.metadata["be_arm_fraction_at_trigger"] == pytest.approx(0.8)
    assert trade.metadata["runner_exit_reason"] == "be_arm_stop"
    assert exit_idx == 3


def test_pno_terminal_bos_requires_half_retrace_after_last_structure_high() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [idx * 60_000 for idx in range(12)],
                "open": [9.9, 9.4, 8.5, 8.1, 8.4, 8.8, 8.1, 7.9, 8.3, 8.7, 8.45, 8.9],
                "high": [10.0, 9.5, 8.6, 8.3, 8.6, 9.0, 8.3, 8.0, 8.5, 8.8, 8.55, 9.05],
                "low": [9.8, 9.2, 8.2, 8.0, 8.3, 8.7, 8.0, 7.8, 8.2, 8.6, 8.35, 8.85],
                "close": [9.9, 9.3, 8.4, 8.2, 8.5, 8.9, 8.2, 7.9, 8.4, 8.75, 8.5, 8.95],
                "volume": [10.0] * 12,
            }
        )
    )
    shallow_ok, shallow_low_idx, shallow_fraction = engine._has_terminal_retrace_after_high(
        one=one,
        high_idx=9,
        previous_low=7.8,
        break_idx=11,
        required_fraction=0.50,
    )

    assert not shallow_ok
    assert shallow_low_idx == 10
    assert shallow_fraction < 0.50


def test_stage3_accepts_close_bos_above_local_pullback_high_even_when_price_extended() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [idx * 30_000 for idx in range(12)],
                "open": [99.0, 97.0, 93.0, 95.0, 92.0, 91.0, 90.0, 92.0, 93.0, 90.0, 91.0, 94.0],
                "high": [100.0, 98.0, 94.0, 96.0, 93.0, 92.0, 91.0, 93.0, 94.0, 91.0, 92.0, 98.0],
                "low": [98.0, 92.0, 91.0, 94.0, 90.0, 89.5, 89.0, 91.0, 92.0, 88.0, 90.0, 92.0],
                "close": [99.0, 93.0, 92.5, 95.5, 91.0, 90.2, 90.5, 92.5, 93.5, 90.5, 91.5, 97.5],
                "volume": [100.0, 80.0, 70.0, 75.0, 70.0, 68.0, 66.0, 70.0, 72.0, 74.0, 78.0, 160.0],
            }
        )
    )
    one.v1[:] = 1.0
    five = _build_test_five_frame(
        opens=[99.0, 96.0],
        highs=[100.0, 98.0],
        lows=[88.0, 92.0],
        closes=[99.0, 97.5],
        ema20=[90.0, 91.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=100.0,
        reference_high=100.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=87.0,
        leg_size=13.0,
        reference_leg_size=13.0,
        hold_floor=95.0,
        pump_range_5m=13.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=1,
        htf_min_allowed_low=0.0,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=100.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=9,
        pullback_low_timestamp=270_000,
        pullback_low=88.0,
        pullback_depth=12.0,
        pullback_age_bars=1,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=11,
        five_idx=1,
        stage1=stage1,
        stage2=stage2,
        params=PnoParams(
            symbol="TEST/USDT",
            stage1_active_context_min_start_fraction=0.0,
            stage1_active_context_min_baseline_ratio=0.0,
            stage3_min_post_high_5m_volume_support_fraction=0.0,
        ),
    )

    assert reason is None
    assert stage3 is not None
    assert stage3.structure_high == pytest.approx(96.0)
    assert stage3.structure_break_close == pytest.approx(97.5)


def test_stage3_rejects_old_setup_when_main_high_was_crossed_before_bos() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [idx * 30_000 for idx in range(12)],
                "open": [99.0, 97.0, 93.0, 95.0, 92.0, 91.0, 90.0, 92.0, 93.0, 90.0, 91.0, 94.0],
                "high": [100.0, 98.0, 94.0, 96.0, 93.0, 92.0, 91.0, 93.0, 101.0, 91.0, 92.0, 98.0],
                "low": [98.0, 92.0, 91.0, 94.0, 90.0, 89.5, 89.0, 91.0, 92.0, 88.0, 90.0, 92.0],
                "close": [99.0, 93.0, 92.5, 95.5, 91.0, 90.2, 90.5, 92.5, 93.5, 90.5, 91.5, 97.5],
                "volume": [100.0, 80.0, 70.0, 75.0, 70.0, 68.0, 66.0, 70.0, 72.0, 74.0, 78.0, 160.0],
            }
        )
    )
    one.v1[:] = 1.0
    five = _build_test_five_frame(
        opens=[99.0, 96.0],
        highs=[101.0, 98.0],
        lows=[88.0, 92.0],
        closes=[99.0, 97.5],
        ema20=[90.0, 91.0],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=1,
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=100.0,
        reference_high=100.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=87.0,
        leg_size=13.0,
        reference_leg_size=13.0,
        hold_floor=95.0,
        pump_range_5m=13.0,
        active_high_5m_idx=0,
        htf_pullback_end_5m_idx=1,
        htf_min_allowed_low=0.0,
    )
    stage2 = Stage2Context(
        active_high_idx=0,
        active_high_timestamp=0,
        active_high=100.0,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=9,
        pullback_low_timestamp=270_000,
        pullback_low=88.0,
        pullback_depth=12.0,
        pullback_age_bars=1,
    )

    stage3, reason = engine._resolve_stage3_context(
        one=one,
        five=five,
        idx=11,
        five_idx=1,
        stage1=stage1,
        stage2=stage2,
        params=PnoParams(
            symbol="TEST/USDT",
            stage1_active_context_min_start_fraction=0.0,
            stage1_active_context_min_baseline_ratio=0.0,
            stage3_min_post_high_5m_volume_support_fraction=0.0,
        ),
    )

    assert stage3 is None
    assert reason == "main_high_crossed_before_bos"


def test_pno_rejects_entry_when_tp1_rr_is_not_above_one() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.9, 10.0, 10.02, 10.05],
                "high": [10.0, 10.2, 10.25, 10.3],
                "low": [9.8, 9.7, 10.0, 10.02],
                "close": [9.95, 10.1, 10.08, 10.1],
                "volume": [10.0, 15.0, 12.0, 13.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            reference_high=10.3,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.3,
            reference_leg_size=1.3,
            pump_range_5m=1.3,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.7,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
            post_high_wick_share=0.35,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.7,
            pullback_depth=0.4,
            cluster_indices=(0,),
            cluster_prices=(10.0,),
            level=10.0,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.7,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.02,
            sl_plan=9.7,
            low_last_red_plan=9.7,
            tp1=10.32,
            tp2=10.6,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
        ),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_pno_close_above_can_trigger_on_level_validation_bar(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[60_000, 120_000, 180_000], price=10.0)
    one.opens[:] = np.array([9.8, 9.9, 10.02], dtype="float64")
    one.highs[:] = np.array([9.9, 10.1, 10.25], dtype="float64")
    one.lows[:] = np.array([9.7, 9.85, 10.0], dtype="float64")
    one.closes[:] = np.array([9.82, 10.05, 10.2], dtype="float64")
    one.volumes[:] = np.array([10.0, 20.0, 15.0], dtype="float64")
    one.quote_volume[:] = one.closes * one.volumes
    one.cumulative_quote_volume[:] = np.cumsum(one.quote_volume)
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    one.frame.loc[:, "volume"] = one.volumes
    five = _build_test_five_frame(
        opens=[9.8],
        highs=[10.25],
        lows=[9.7],
        closes=[10.2],
        ema20=[9.9],
    )
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=60_000,
        pump_start_5m_idx=0,
        pump_start_timestamp=60_000,
        current_5m_idx=0,
        active_high_idx=1,
        active_high_timestamp=120_000,
        active_high=10.3,
        reference_high=10.3,
        leg_start_idx=0,
        leg_start_timestamp=60_000,
        leg_start=9.0,
        leg_size=1.3,
        reference_leg_size=1.3,
        pump_range_5m=1.3,
        hold_floor=9.7,
    )
    stage2 = Stage2Context(
        active_high_idx=1,
        active_high_timestamp=120_000,
        active_high=10.3,
        red_after_high_idx=1,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=120_000,
        pullback_low=9.85,
        pullback_depth=0.45,
        pullback_age_bars=1,
    )
    stage3 = Stage3Context(
        active_high_idx=1,
        active_high_timestamp=120_000,
        active_high=10.3,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=120_000,
        pullback_low=9.85,
        pullback_depth=0.45,
        pullback_age_bars=1,
        validation_timestamp=120_000,
    )
    stage4 = _build_test_stage4_context(
        active_high_idx=1,
        active_high_timestamp=120_000,
        active_high=10.3,
        pullback_low_idx=1,
        pullback_low_timestamp=120_000,
        pullback_low=9.85,
        pullback_depth=0.45,
        cluster_indices=(1,),
        cluster_prices=(9.95,),
        cluster_first_idx=1,
        cluster_last_idx=1,
        level=9.95,
        level_valid_idx=1,
        level_valid_timestamp=120_000,
        entry_plan=10.0,
        low_last_red_plan=9.7,
        sl_plan=9.7,
        tp1=10.3,
        tp2=10.6,
    )

    monkeypatch.setattr(engine, "_resolve_stage1_context", lambda **kwargs: (stage1, None))
    monkeypatch.setattr(engine, "_resolve_stage2_context", lambda **kwargs: stage2 if kwargs["idx"] >= 1 else None)
    monkeypatch.setattr(engine, "_resolve_stage3_context", lambda **kwargs: (stage3, None) if kwargs["idx"] >= 1 else (None, None))
    monkeypatch.setattr(engine, "_resolve_stage4_context", lambda **kwargs: stage4 if kwargs["idx"] == 1 else None)
    monkeypatch.setattr(engine, "_rebuild_stage4_scores", lambda **kwargs: kwargs["stage4"])

    attempted_entry_indices: list[int] = []

    def _fake_try_enter_and_simulate(*, one, params, armed):
        attempted_entry_indices.append(int(armed.entry_idx))
        return None, int(armed.entry_idx)

    monkeypatch.setattr(engine, "_try_enter_and_simulate", _fake_try_enter_and_simulate)

    engine._run(
        one=one,
        five=five,
        params=PnoParams(
            symbol="TEST/USDT",
            entry_confirmation_mode="close_above",
            min_data_1m=1,
            min_data_5m=1,
        ),
        diagnostics=engine._empty_diagnostics(),
    )

    assert attempted_entry_indices
    assert attempted_entry_indices[0] == 1


def test_pno_close_above_rejects_late_gap_when_entry_geometry_is_broken() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.8, 9.9, 10.6, 10.7],
                "high": [10.0, 10.2, 10.7, 10.8],
                "low": [9.7, 9.85, 10.55, 10.65],
                "close": [9.9, 10.05, 10.65, 10.75],
                "volume": [10.0, 11.0, 12.0, 13.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
        ),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_pno_cross_rejects_entry_when_actual_entry_is_too_late_in_leg() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000],
                "open": [9.8, 9.9, 10.1],
                "high": [9.95, 10.35, 10.4],
                "low": [9.7, 9.88, 10.0],
                "close": [9.9, 10.2, 10.3],
                "volume": [10.0, 12.0, 11.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.3,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.6,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(symbol="TEST/USDT", pno_r_trade=20.0, min_entry_rr=0.0),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_pno_cross_uses_only_open_and_trigger_without_same_bar_extremes() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.8, 9.96, 10.05, 10.2],
                "high": [9.9, 10.2, 10.6, 10.8],
                "low": [9.7, 9.2, 10.0, 10.1],
                "close": [9.85, 9.3, 10.5, 10.7],
                "volume": [10.0, 12.0, 11.0, 9.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.8,
            reference_high=10.8,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.8,
            reference_leg_size=1.8,
            pump_range_5m=1.8,
            hold_floor=9.7,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.8,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.5,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.8,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.5,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=10.8,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(symbol="TEST/USDT", pno_r_trade=20.0, min_entry_rr=0.0),
        armed=armed,
    )

    assert trade is not None
    assert trade.entry_timestamp_ms == 120_000
    assert trade.metadata["entry_confirmation_mode"] == "cross"
    assert trade.metadata["entry_price_actual"] == pytest.approx(10.0)
    assert trade.metadata["sl_actual"] == pytest.approx(9.5)
    assert trade.exit_timestamp_ms > trade.entry_timestamp_ms
    assert exit_idx >= 2


def test_pno_close_above_allows_weak_trigger_if_structure_is_valid() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.9, 10.0, 10.02, 10.03],
                "high": [10.0, 10.08, 10.12, 10.2],
                "low": [9.8, 9.96, 10.0, 10.01],
                "close": [9.95, 10.01, 10.04, 10.12],
                "volume": [10.0, 8.0, 12.0, 13.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            cluster_indices=(0,),
            cluster_prices=(9.98,),
            level=9.98,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=72.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
            overhead_resistance_score=0.7,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
        ),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_pno_close_above_uses_dynamic_be_ladder() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000, 360_000],
                "open": [9.8, 9.9, 10.02, 10.10, 10.18, 10.03],
                "high": [10.0, 10.1, 10.12, 10.18, 10.21, 10.08],
                "low": [9.7, 9.85, 9.98, 10.05, 10.01, 9.99],
                "close": [9.9, 10.02, 10.08, 10.16, 10.03, 10.01],
                "volume": [10.0, 10.0, 30.0, 13.0, 12.0, 11.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            reference_high=10.3,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.3,
            tp2=10.6,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_be_start_fraction=0.70,
            close_above_be_step_fraction=0.05,
            close_above_be_step_bars=1,
            close_above_be_min_fraction=0.25,
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
            min_entry_rr=0.0,
        ),
        armed=armed,
    )

    assert trade is not None
    assert trade.result_type == TradeResultType.BE
    assert trade.metadata["be_armed"] is True
    assert trade.metadata["be_arm_start_fraction"] == pytest.approx(0.7)
    assert trade.metadata["be_arm_fraction_at_trigger"] == pytest.approx(0.65)
    assert trade.metadata["be_arm_timestamp_ms"] == 300_000
    assert exit_idx == 3


def test_pno_tp1_be_starts_from_be_buffer_above_entry() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000],
                "open": [9.8, 9.9, 10.02, 10.32, 10.28],
                "high": [10.0, 10.1, 10.35, 10.34, 10.30],
                "low": [9.7, 9.85, 10.00, 10.16, 10.10],
                "close": [9.9, 10.02, 10.32, 10.28, 10.18],
                "volume": [10.0, 10.0, 30.0, 13.0, 12.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            reference_high=10.3,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.0,
            sl_plan=9.5,
                low_last_red_plan=9.70,
                tp1=10.3,
                tp2=10.6,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
        ),
    )

    trade, exit_idx = engine._simulate_trade_path(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
            min_entry_rr=0.0,
        ),
        armed=armed,
        entry_idx=1,
        entry_price=10.0,
        stop_loss=9.7,
        position_size=1.0,
        metadata={
            "entry_confirmation_mode": "close_above",
            "pump_to_peak_bars": 1,
            "pump_to_peak_minutes": 1.0,
        },
    )

    assert trade is not None
    assert trade.result_type == TradeResultType.TP1_BE
    assert trade.metadata["partial_exit_price"] == pytest.approx(10.3)
    assert trade.metadata["tp1_be_protect_price"] == pytest.approx(10.015)
    assert trade.metadata["runner_exit_price"] == pytest.approx(10.16)
    assert exit_idx == 3


def test_pno_close_above_filter_blocks_weak_post_high_and_volume_signal() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000],
                "open": [9.8, 9.9, 10.02, 10.05],
                "high": [10.0, 10.1, 10.1, 11.1],
                "low": [9.7, 9.85, 9.98, 10.0],
                "close": [9.9, 10.02, 10.05, 11.0],
                "volume": [10.0, 10.0, 10.0, 13.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=1,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            pullback_age_bars=2,
            validation_timestamp=60_000,
            post_high_wick_share=0.7,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
            entry_pos=0.56,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_max_entry_pos=0.55,
            close_above_max_pullback_fraction_of_leg=0.55,
            close_above_max_post_high_wick_share=0.60,
            close_above_min_signal_volume_vs_recent=0.75,
        ),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_pno_close_above_filter_blocks_exhausted_active_high() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000],
                "open": [10.00, 10.02, 10.03, 10.01, 10.04],
                "high": [10.05, 10.08, 10.10, 10.11, 10.13],
                "low": [9.98, 10.00, 10.01, 10.00, 10.02],
                "close": [10.02, 10.04, 10.05, 10.06, 10.09],
                "volume": [12.0, 13.0, 14.0, 18.0, 20.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=3,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=2,
            active_high_timestamp=180_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.5,
            leg_size=1.0,
            reference_leg_size=1.0,
            pump_range_5m=1.0,
            hold_floor=9.9,
            pump_volume_ratio_start=8.0,
            pump_path_efficiency=0.6,
            active_high_bar_upper_wick_share=0.65,
            active_high_bar_close_position=0.35,
        ),
        stage3=Stage3Context(
            active_high_idx=2,
            active_high_timestamp=180_000,
            active_high=10.5,
            pullback_start_idx=2,
            pullback_low_idx=2,
            pullback_low_timestamp=180_000,
            pullback_low=9.9,
            pullback_depth=0.6,
            pullback_age_bars=2,
            validation_timestamp=180_000,
            post_high_wick_share=0.20,
            post_high_body_overlap_rate=0.40,
        ),
        stage4=_build_test_stage4_context(
            level=10.05,
            entry_plan=10.06,
            sl_plan=9.95,
            low_last_red_plan=9.95,
            tp1=10.5,
            tp2=10.9,
            entry_pos=0.35,
            final_score=90.0,
            stage4_ready=True,
            is_valid_setup=True,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(symbol="TEST/USDT", entry_confirmation_mode="close_above", pno_r_trade=20.0),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 3


def test_pno_close_above_filter_blocks_flat_signal_without_ema_trend() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000],
                "open": [10.00, 10.00, 10.01, 10.02, 10.03],
                "high": [10.02, 10.03, 10.05, 10.06, 10.08],
                "low": [9.98, 9.99, 10.00, 10.01, 10.02],
                "close": [10.00, 10.01, 10.02, 10.03, 10.05],
                "volume": [10.0, 11.0, 12.0, 13.0, 14.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=2,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            pullback_age_bars=2,
            validation_timestamp=60_000,
            post_high_wick_share=0.35,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            cluster_indices=(0,),
            cluster_prices=(10.0,),
            level=10.0,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.02,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
            entry_pos=0.52,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.12,
            close_above_min_signal_ema_spread_pct=0.90,
        ),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 2


def test_pno_close_above_filter_allows_mildly_weak_ema_signal_when_structure_is_clean() -> None:
    engine = PnoEngine()
    stage1 = _build_test_stage1_context()
    stage3 = Stage3Context(
        active_high_idx=10,
        active_high_timestamp=60_000,
        active_high=10.5,
        pullback_start_idx=11,
        pullback_low_idx=12,
        pullback_low_timestamp=120_000,
        pullback_low=9.5,
        pullback_depth=1.0,
        pullback_age_bars=2,
        validation_timestamp=180_000,
        post_high_wick_share=0.42,
        post_high_body_overlap_rate=0.62,
        post_high_chop_alternation_rate=0.60,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.5,
        active_high_timestamp=60_000,
        pullback_low=9.5,
        pullback_low_timestamp=120_000,
        pullback_depth=1.0,
        entry_pos=0.50,
        touches=2,
        overhead_resistance_score=0.44,
        overhead_red_count=2,
    )

    reason = engine._resolve_close_trigger_filter_reason(
        params=PnoParams(
            symbol="TEST/USDT",
            close_above_min_signal_ema9_slope_3=0.0,
            close_above_min_signal_ema20_slope_3=0.12,
            close_above_min_signal_ema_spread_pct=0.90,
            close_above_min_signal_volume_vs_recent=0.0,
        ),
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        signal_context={
            "signal_bar_close_position": 0.82,
            "signal_bar_ema9_slope_3": 0.05,
            "signal_bar_ema20_slope_3": 0.09,
            "signal_bar_ema_spread_pct": 0.74,
            "signal_bar_volume_vs_recent": 0.50,
        },
    )

    assert reason is None


def test_pno_close_above_filter_blocks_low_close_inside_choppy_post_high() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000],
                "open": [10.00, 10.02, 10.04, 10.06, 10.05],
                "high": [10.05, 10.08, 10.10, 10.12, 10.11],
                "low": [9.98, 10.00, 10.02, 10.04, 10.01],
                "close": [10.03, 10.05, 10.07, 10.08, 10.02],
                "volume": [10.0, 12.0, 14.0, 16.0, 18.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=3,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            pullback_age_bars=2,
            validation_timestamp=60_000,
            post_high_body_overlap_rate=1.0,
            post_high_wick_share=0.45,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=1.0,
            cluster_indices=(0,),
            cluster_prices=(10.0,),
            level=10.0,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.03,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
            entry_pos=0.50,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
            close_above_min_signal_close_position_in_chop=0.25,
            close_above_choppy_overlap_threshold=0.95,
        ),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 3


def test_pno_close_above_filter_blocks_overlap_heavy_noisy_reclaim() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000],
                "open": [10.00, 10.04, 10.03, 10.05, 10.04],
                "high": [10.08, 10.10, 10.09, 10.12, 10.11],
                "low": [9.98, 10.00, 10.01, 10.02, 10.01],
                "close": [10.04, 10.03, 10.05, 10.04, 10.06],
                "volume": [10.0, 12.0, 14.0, 16.0, 18.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=3,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.75,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            pullback_age_bars=2,
            validation_timestamp=60_000,
            post_high_wick_share=0.61,
            post_high_body_overlap_rate=1.0,
        ),
        stage4=Stage4Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.3,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.4,
            cluster_indices=(0,),
            cluster_prices=(9.95,),
            level=9.95,
            level_pos=0.45,
            touches=1,
            cluster_first_idx=0,
            cluster_last_idx=0,
            level_valid_idx=0,
            level_valid_timestamp=60_000,
            level_low=9.5,
            level_low_minor_break=False,
            level_low_major_break=False,
            penalty_level_low_break=0,
            penalty_untested_highs=0,
            base_bonus=0,
            pno_index=1,
            maturity_penalty=0,
            pno_order_adj=8,
            score_a=10,
            score_b=10,
            score_c=10,
            score_d=4,
            score_e=7,
            score_tp2=5,
            final_score=76.0,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
            stage4_ready=True,
            hard_block=False,
            is_valid_setup=True,
            hard_block_reason=None,
            entry_pos=0.62,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
        ),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 3


def test_pno_level_maturity_fraction_is_based_on_time_since_main_high() -> None:
    engine = PnoEngine()

    assert engine._resolve_level_maturity_fraction(active_high_idx=10, cluster_first_idx=16, current_idx=17) == pytest.approx(1 / 7)
    assert engine._resolve_level_maturity_fraction(active_high_idx=10, cluster_first_idx=12, current_idx=18) == pytest.approx(0.75)


def test_pno_resolve_tp2_projects_from_main_high_to_entry_and_rounds_down() -> None:
    engine = PnoEngine()

    tp2 = engine._resolve_tp2(active_high=10.5, entry_price=10.0, pullback_height=1.0, v1=0.2)

    assert tp2 == pytest.approx(11.0)


def test_pno_resolve_tp2_uses_actual_entry_distance_even_if_pullback_was_deeper() -> None:
    engine = PnoEngine()

    tp2 = engine._resolve_tp2(active_high=10.5, entry_price=10.1, pullback_height=1.0, v1=0.2)

    assert tp2 == pytest.approx(10.8)


def test_pno_tp1_runner_trails_to_last_red_low_on_each_new_high() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [60_000, 120_000, 180_000, 240_000, 300_000, 360_000],
                "open": [9.8, 10.0, 10.35, 10.55, 10.48, 10.7],
                "high": [10.0, 10.35, 10.6, 10.58, 10.8, 10.78],
                "low": [9.7, 9.95, 10.3, 10.45, 10.46, 10.44],
                "close": [10.0, 10.32, 10.52, 10.48, 10.72, 10.5],
                "volume": [10.0, 20.0, 18.0, 12.0, 25.0, 15.0],
            }
        )
    )
    armed = ArmedContext(
        entry_idx=0,
        stage1=Stage1Context(
            start_idx=0,
            start_timestamp=60_000,
            pump_start_5m_idx=0,
            pump_start_timestamp=60_000,
            current_5m_idx=0,
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            reference_high=10.5,
            leg_start_idx=0,
            leg_start_timestamp=60_000,
            leg_start=9.0,
            leg_size=1.5,
            reference_leg_size=1.5,
            pump_range_5m=1.5,
            hold_floor=9.7,
        ),
        stage3=Stage3Context(
            active_high_idx=0,
            active_high_timestamp=60_000,
            active_high=10.5,
            pullback_start_idx=0,
            pullback_low_idx=0,
            pullback_low_timestamp=60_000,
            pullback_low=9.5,
            pullback_depth=0.5,
            pullback_age_bars=2,
            validation_timestamp=60_000,
        ),
        stage4=_build_test_stage4_context(
            active_high=10.5,
            entry_plan=10.0,
            sl_plan=9.5,
            low_last_red_plan=9.5,
            tp1=10.5,
            tp2=11.0,
        ),
    )

    trade, exit_idx = engine._try_enter_and_simulate(
        one=one,
        params=PnoParams(
            symbol="TEST/USDT",
            pno_r_trade=20.0,
            entry_confirmation_mode="close_above",
            close_above_min_signal_ema20_slope_3=0.0,
            close_above_min_signal_ema_spread_pct=0.0,
            min_entry_rr=0.0,
        ),
        armed=armed,
    )

    assert trade is not None
    assert trade.result_type == TradeResultType.TP1_BE
    assert trade.metadata["runner_exit_price"] == pytest.approx(10.45)
    assert trade.metadata["runner_stop_after_tp1"] == pytest.approx(10.45)
    assert exit_idx == 3


def test_pno_level_life_ema_spread_growth_share_tracks_pressing_into_level() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[60_000, 120_000, 180_000, 240_000], price=10.0)
    one.closes[:] = np.array([10.0, 10.0, 10.0, 10.0], dtype=np.float64)
    one.frame.loc[:, "ema9"] = np.array([10.10, 10.20, 10.35, 10.50], dtype=np.float64)
    one.frame.loc[:, "ema20"] = np.array([10.00, 10.05, 10.10, 10.15], dtype=np.float64)
    stage4 = _build_test_stage4_context(cluster_first_idx=0)

    growth_share = engine._resolve_level_life_ema_spread_growth_share(one=one, stage4=stage4, idx=3)

    assert growth_share == pytest.approx(1.0)


def test_pno_stage4_allows_early_single_touch_reclaim_before_full_maturity(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=list(range(0, 21 * 60_000, 60_000)), price=10.0)
    one.v1[:] = np.full_like(one.v1, 0.1)
    one.highs[:] = np.full_like(one.highs, 10.45)
    one.lows[:] = np.full_like(one.lows, 10.35)
    one.closes[:] = np.full_like(one.closes, 10.40)
    one.opens[:] = np.full_like(one.opens, 10.39)
    one.highs[17] = 10.52
    one.highs[16] = 10.5
    one.lows[17] = 10.47
    one.closes[17] = 10.51
    one.opens[17] = 10.48
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes

    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=0,
        active_high_idx=10,
        active_high_timestamp=10 * 60_000,
        active_high=10.8,
        reference_high=10.8,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=9.8,
        leg_size=1.0,
        reference_leg_size=1.0,
        hold_floor=10.1,
    )
    stage3 = Stage3Context(
        active_high_idx=10,
        active_high_timestamp=10 * 60_000,
        active_high=10.8,
        pullback_start_idx=11,
        pullback_low_idx=12,
        pullback_low_timestamp=12 * 60_000,
        pullback_low=10.2,
        pullback_depth=0.6,
        pullback_age_bars=5,
        validation_timestamp=17 * 60_000,
    )
    params = PnoParams(symbol="TEST/USDT", level_min_maturity_fraction=0.20)

    monkeypatch.setattr(engine, "_resolve_confirmed_highs", lambda **kwargs: np.array([16], dtype=np.int64))
    monkeypatch.setattr(engine, "_resolve_confirmed_lows", lambda **kwargs: np.array([], dtype=np.int64))
    monkeypatch.setattr(engine, "_resolve_level_cluster", lambda **kwargs: ((16,), (10.5,)))

    stage4 = engine._resolve_stage4_context(
        one=one,
        five=_build_test_five_frame(opens=[10.0], highs=[10.8], lows=[10.0], closes=[10.6], ema20=[10.2]),
        idx=17,
        five_idx=0,
        stage1=stage1,
        stage3=stage3,
        previous=None,
        params=params,
        pno_index=1,
        retired_clusters=[],
    )

    assert stage4 is not None
    assert stage4.level == pytest.approx(10.5)
    assert stage4.level_valid_idx == 17


def test_pno_stage4_ideal_like_uses_upper_tf_level_below_pullback_midpoint() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=list(range(0, 20 * 60_000, 60_000)), price=10.2)
    one.v1[:] = np.full_like(one.v1, 0.1)
    one.highs[:] = np.full_like(one.highs, 10.35)
    one.lows[:] = np.full_like(one.lows, 10.15)
    one.opens[:] = np.full_like(one.opens, 10.24)
    one.closes[:] = np.full_like(one.closes, 10.26)
    one.highs[12] = 10.45
    one.lows[12] = 10.00
    one.opens[12] = 10.08
    one.closes[12] = 10.30
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes

    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=3,
        active_high_idx=6,
        active_high_timestamp=300_000,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        pump_range_5m=1.0,
        hold_floor=10.4,
        pump_impulse_atr_pre=20.0,
        pump_peak_bar_tr_atr_pre=8.0,
        pump_volume_ratio_start=9.0,
        pump_path_efficiency=0.7,
        pump_wick_share=0.2,
        pump_body_share_mean=0.6,
        pump_body_wick_edge=0.2,
        pump_micro_flat_bar_share=0.0,
        active_high_bar_upper_wick_share=0.2,
        pump_counterflow_ratio_5m=0.0,
    )
    stage3 = Stage3Context(
        active_high_idx=6,
        active_high_timestamp=300_000,
        active_high=11.0,
        pullback_start_idx=11,
        pullback_low_idx=12,
        pullback_low_timestamp=600_000,
        pullback_low=10.0,
        pullback_depth=1.0,
        pullback_age_bars=2,
        validation_timestamp=12 * 60_000,
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.5, 10.10, 10.30],
        highs=[10.4, 11.0, 10.45, 10.80],
        lows=[9.9, 10.4, 10.0, 10.2],
        closes=[10.3, 10.9, 10.30, 10.7],
        ema20=[9.8, 10.1, 10.2, 10.4],
    )

    stage4 = engine._resolve_stage4_context(
        one=one,
        five=five,
        idx=19,
        five_idx=3,
        stage1=stage1,
        stage3=stage3,
        previous=None,
        params=PnoParams(symbol="TEST/USDT", ideal_like_impulse_enabled=True),
        pno_index=1,
        retired_clusters=[],
    )

    assert stage4 is not None
    assert stage4.cluster_indices == (15,)
    assert stage4.level == pytest.approx(10.35)


def test_pno_stage4_ideal_like_can_use_current_upper_tf_bar_without_lookahead() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=list(range(0, 30 * 60_000, 60_000)), price=110.0)
    one.v1[:] = np.full_like(one.v1, 0.2)
    one.opens[:] = np.full_like(one.opens, 110.0)
    one.closes[:] = np.full_like(one.closes, 110.0)
    one.highs[:] = np.full_like(one.highs, 110.1)
    one.lows[:] = np.full_like(one.lows, 109.9)
    # main_high bucket
    one.opens[5:10] = np.array([110.7, 110.62, 110.87, 111.58, 111.25], dtype=np.float64)
    one.highs[5:10] = np.array([111.01, 112.00, 111.84, 112.11, 111.46], dtype=np.float64)
    one.lows[5:10] = np.array([110.40, 110.59, 110.85, 111.22, 110.47], dtype=np.float64)
    one.closes[5:10] = np.array([110.62, 110.87, 111.57, 111.24, 110.57], dtype=np.float64)
    # +1 bucket => higher lower-high
    one.opens[10:15] = np.array([110.57, 110.95, 111.06, 111.18, 110.74], dtype=np.float64)
    one.highs[10:15] = np.array([111.32, 111.43, 111.46, 111.30, 110.82], dtype=np.float64)
    one.lows[10:15] = np.array([110.55, 110.84, 111.04, 110.72, 110.50], dtype=np.float64)
    one.closes[10:15] = np.array([110.96, 111.06, 111.17, 110.75, 110.65], dtype=np.float64)
    # current +2 bucket => lower lower-high already known by current idx
    one.opens[15:18] = np.array([110.66, 110.49, 110.52], dtype=np.float64)
    one.highs[15:18] = np.array([110.86, 110.69, 110.61], dtype=np.float64)
    one.lows[15:18] = np.array([110.24, 110.26, 110.10], dtype=np.float64)
    one.closes[15:18] = np.array([110.48, 110.52, 110.49], dtype=np.float64)
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes

    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=3,
        active_high_idx=8,
        active_high_timestamp=8 * 60_000,
        active_high=112.11,
        reference_high=112.11,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=109.5,
        leg_size=2.61,
        reference_leg_size=2.61,
        pump_range_5m=2.61,
        hold_floor=110.0,
        pump_impulse_atr_pre=20.0,
        pump_peak_bar_tr_atr_pre=8.0,
        pump_volume_ratio_start=10.0,
        pump_path_efficiency=0.7,
        pump_wick_share=0.2,
        pump_body_share_mean=0.6,
        pump_body_wick_edge=0.2,
        pump_micro_flat_bar_share=0.0,
        active_high_bar_upper_wick_share=0.2,
        pump_counterflow_ratio_5m=0.0,
    )
    stage3 = Stage3Context(
        active_high_idx=8,
        active_high_timestamp=8 * 60_000,
        active_high=112.11,
        pullback_start_idx=10,
        pullback_low_idx=19,
        pullback_low_timestamp=19 * 60_000,
        pullback_low=109.48,
        pullback_depth=2.63,
        pullback_age_bars=2,
        validation_timestamp=16 * 60_000,
    )
    five = _build_test_five_frame(
        opens=[110.0, 110.7, 110.57, 110.66, 109.93, 110.98],
        highs=[110.1, 112.11, 111.46, 110.86, 111.20, 112.21],
        lows=[109.9, 110.4, 110.5, 109.48, 109.88, 110.8],
        closes=[110.0, 110.57, 110.65, 109.93, 110.99, 111.5],
        ema20=[109.8, 110.0, 110.2, 110.1, 110.2, 110.4],
    )

    stage4 = engine._resolve_stage4_context(
        one=one,
        five=five,
        idx=17,
        five_idx=3,
        stage1=stage1,
        stage3=stage3,
        previous=None,
        params=PnoParams(symbol="TEST/USDT", ideal_like_impulse_enabled=True),
        pno_index=1,
        retired_clusters=[],
    )

    assert stage4 is not None
    assert stage4.level == pytest.approx(110.86)
    assert stage4.cluster_indices == (15,)


def test_pno_stage4_ideal_like_refreshes_to_newer_lower_upper_tf_high() -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=list(range(0, 25 * 60_000, 60_000)), price=10.2)
    one.v1[:] = np.full_like(one.v1, 0.1)
    one.highs[:] = np.full_like(one.highs, 10.50)
    one.lows[:] = np.full_like(one.lows, 10.20)
    one.opens[:] = np.full_like(one.opens, 10.32)
    one.closes[:] = np.full_like(one.closes, 10.36)
    one.highs[10:15] = 10.45
    one.highs[20:25] = 10.35
    one.highs[12] = 10.45
    one.lows[12] = 10.00
    one.opens[12] = 10.08
    one.closes[12] = 10.30
    one.highs[22] = 10.35
    one.lows[22] = 10.05
    one.opens[22] = 10.16
    one.closes[22] = 10.28
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=0,
        pump_start_5m_idx=0,
        pump_start_timestamp=0,
        current_5m_idx=4,
        active_high_idx=6,
        active_high_timestamp=300_000,
        active_high=11.0,
        reference_high=11.0,
        leg_start_idx=0,
        leg_start_timestamp=0,
        leg_start=10.0,
        leg_size=1.0,
        reference_leg_size=1.0,
        pump_range_5m=1.0,
        hold_floor=10.4,
        pump_impulse_atr_pre=20.0,
        pump_peak_bar_tr_atr_pre=8.0,
        pump_volume_ratio_start=9.0,
        pump_path_efficiency=0.7,
        pump_wick_share=0.2,
        pump_body_share_mean=0.6,
        pump_body_wick_edge=0.2,
        pump_micro_flat_bar_share=0.0,
        active_high_bar_upper_wick_share=0.2,
        pump_counterflow_ratio_5m=0.0,
    )
    stage3 = Stage3Context(
        active_high_idx=6,
        active_high_timestamp=300_000,
        active_high=11.0,
        pullback_start_idx=11,
        pullback_low_idx=12,
        pullback_low_timestamp=600_000,
        pullback_low=10.0,
        pullback_depth=1.0,
        pullback_age_bars=3,
        validation_timestamp=12 * 60_000,
    )
    five = _build_test_five_frame(
        opens=[10.0, 10.5, 10.10, 10.30, 10.20],
        highs=[10.4, 11.0, 10.45, 10.80, 10.35],
        lows=[9.9, 10.4, 10.0, 10.2, 10.05],
        closes=[10.3, 10.9, 10.30, 10.7, 10.28],
        ema20=[9.8, 10.1, 10.2, 10.4, 10.35],
    )
    params = PnoParams(symbol="TEST/USDT", ideal_like_impulse_enabled=True)

    previous = engine._resolve_stage4_context(
        one=one,
        five=five,
        idx=14,
        five_idx=2,
        stage1=stage1,
        stage3=stage3,
        previous=None,
        params=params,
        pno_index=1,
        retired_clusters=[],
    )
    assert previous is not None
    assert previous.level == pytest.approx(10.45)

    refreshed = engine._resolve_stage4_context(
        one=one,
        five=five,
        idx=24,
        five_idx=4,
        stage1=stage1,
        stage3=stage3,
        previous=previous,
        params=params,
        pno_index=1,
        retired_clusters=[],
    )

    assert refreshed is not None
    assert refreshed.level == pytest.approx(10.35)
    assert refreshed.cluster_indices == (20,)


def test_pno_stage1_cat_c_rejects_active_high_formed_on_weak_tail() -> None:
    engine = PnoEngine()
    five = _build_test_five_frame(
        opens=[9.90, 10.00, 10.70, 10.82],
        highs=[10.00, 10.80, 10.90, 10.98],
        lows=[9.80, 9.98, 10.68, 10.81],
        closes=[9.95, 10.72, 10.76, 10.86],
        ema20=[9.75, 9.90, 10.05, 10.20],
    )
    five.tr[:] = np.array([0.20, 0.82, 0.22, 0.17], dtype=np.float64)
    five.quote_volume[:] = np.array([100_000.0, 900_000.0, 320_000.0, 260_000.0], dtype=np.float64)

    assert engine._has_cat_c_late_weak_active_high(
        five=five,
        pump_start_5m_idx=1,
        active_high_5m_idx=3,
        pump_pre_atr=0.10,
    )


def test_pno_rebuild_stage4_uses_pullback_low_when_ema_spread_keeps_growing(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=[60_000, 120_000, 180_000, 240_000, 300_000, 360_000], price=10.0)
    one.opens[:] = np.array([9.92, 9.95, 9.98, 10.00, 10.03, 10.05], dtype=np.float64)
    one.closes[:] = np.array([9.95, 9.99, 10.02, 10.05, 10.08, 10.12], dtype=np.float64)
    one.highs[:] = np.array([9.98, 10.02, 10.06, 10.10, 10.14, 10.18], dtype=np.float64)
    one.lows[:] = np.array([9.90, 9.94, 9.97, 9.99, 10.01, 10.04], dtype=np.float64)
    one.v1[:] = np.full_like(one.v1, 0.08)
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    one.frame.loc[:, "ema9"] = np.array([9.93, 9.97, 10.01, 10.05, 10.10, 10.16], dtype=np.float64)
    one.frame.loc[:, "ema20"] = np.array([9.92, 9.95, 9.98, 10.00, 10.02, 10.04], dtype=np.float64)

    stage1 = Stage1Context(
        start_idx=0,
        start_timestamp=60_000,
        pump_start_5m_idx=0,
        pump_start_timestamp=60_000,
        current_5m_idx=1,
        active_high_idx=0,
        active_high_timestamp=60_000,
        active_high=10.6,
        reference_high=10.6,
        leg_start_idx=0,
        leg_start_timestamp=60_000,
        leg_start=9.6,
        leg_size=1.0,
        reference_leg_size=1.0,
        pump_range_5m=1.0,
        hold_floor=9.95,
        pump_impulse_atr_pre=20.0,
        pump_peak_bar_tr_atr_pre=8.0,
        pump_volume_ratio_start=10.0,
        pump_path_efficiency=0.7,
        pump_wick_share=0.2,
        pump_body_share_mean=0.6,
        pump_body_wick_edge=0.2,
        pump_micro_flat_bar_share=0.0,
        active_high_bar_upper_wick_share=0.2,
        pump_counterflow_ratio_5m=0.0,
    )
    stage3 = Stage3Context(
        active_high_idx=0,
        active_high_timestamp=60_000,
        active_high=10.6,
        pullback_start_idx=1,
        pullback_low_idx=1,
        pullback_low_timestamp=120_000,
        pullback_low=9.8,
        pullback_depth=0.8,
        pullback_age_bars=2,
        validation_timestamp=180_000,
    )
    stage4 = _build_test_stage4_context(
        active_high=10.6,
        active_high_timestamp=60_000,
        pullback_low=9.8,
        pullback_low_timestamp=120_000,
        pullback_depth=0.8,
        cluster_indices=(1,),
        cluster_prices=(10.0,),
        level=10.0,
        cluster_first_idx=1,
        cluster_last_idx=1,
        level_valid_idx=1,
        level_valid_timestamp=120_000,
        level_maturity_fraction=0.2,
    )
    five = _build_test_five_frame(
        opens=[9.8, 10.0],
        highs=[10.6, 10.3],
        lows=[9.7, 9.9],
        closes=[10.5, 10.2],
        ema20=[9.9, 10.0],
    )

    monkeypatch.setattr(engine, "_resolve_ideal_like_microstructure_stop", lambda **kwargs: None)

    rebuilt = engine._rebuild_stage4_scores(
        one=one,
        five=five,
        idx=5,
        five_idx=1,
        stage1=stage1,
        stage3=stage3,
        stage4=stage4,
        params=PnoParams(symbol="TEST/USDT", ideal_like_impulse_enabled=True),
    )

    assert rebuilt.low_last_red_plan == pytest.approx(9.8)
    assert rebuilt.tp1 == pytest.approx(10.6)
    assert rebuilt.tp2 == pytest.approx(11.4)
    assert rebuilt.hard_block_reason != "ideal_like_no_ltf_confirmation"


def test_pno_entry_pullback_fraction_measures_real_entry_position() -> None:
    engine = PnoEngine()

    assert engine._resolve_entry_pullback_fraction(pullback_low=8.0, active_high=10.0, entry_price=8.9) == pytest.approx(0.45)
    assert engine._resolve_entry_pullback_fraction(pullback_low=8.0, active_high=10.0, entry_price=9.4) == pytest.approx(0.7)


def test_stale_stage1_anchor_rearms_only_for_fresh_breakout() -> None:
    engine = PnoEngine()
    bars_count = 181
    wake = np.zeros(bars_count, dtype=bool)
    support = np.zeros(bars_count, dtype=bool)
    local_breakout = np.zeros(bars_count, dtype=bool)
    support[175:] = True
    local_breakout[175:] = True
    ema9 = np.full(bars_count, 2.0, dtype=np.float64)
    ema20 = np.full(bars_count, 1.0, dtype=np.float64)
    ema9[173] = 0.9
    ema20[173] = 1.0

    rearm_idx = engine._resolve_stale_pump_rearm_start_idx(
        active_start_idx=0,
        idx=180,
        levels_timeframe_ms=300_000,
        pump_start_lookback=6,
        wake_transition=False,
        self_sustain=True,
        wake=wake,
        support=support,
        local_breakout=local_breakout,
        ema9=ema9,
        ema20=ema20,
        pump_start_shift_lookback=24,
        pump_start_precursor_extension=3,
        closes=np.full(bars_count, 2.0, dtype=np.float64),
    )

    assert rearm_idx == 173
    assert (
        engine._resolve_stale_pump_rearm_start_idx(
            active_start_idx=100,
            idx=180,
            levels_timeframe_ms=300_000,
            pump_start_lookback=6,
            wake_transition=False,
            self_sustain=True,
            wake=wake,
            support=support,
            local_breakout=local_breakout,
            ema9=ema9,
            ema20=ema20,
            pump_start_shift_lookback=24,
            pump_start_precursor_extension=3,
            closes=np.full(bars_count, 2.0, dtype=np.float64),
        )
        is None
    )


def test_shift_pump_start_left_to_recent_ema_reset_stays_near_breakout_without_reset() -> None:
    ema9 = np.array([2.0, 2.0, 2.0, 2.0], dtype=np.float64)
    ema20 = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float64)

    assert (
        PnoEngine._shift_pump_start_left_to_ema_reset(
            ema9=ema9,
            ema20=ema20,
            pump_start_idx=3,
            max_shift_bars=2,
        )
        == 3
    )


def test_extend_pump_start_to_precursor_breakout_captures_previous_breakout_bar() -> None:
    local_breakout = np.array([False, False, True, True, True], dtype=bool)
    closes = np.array([1.0, 1.0, 1.2, 1.3, 1.4], dtype=np.float64)
    ema20 = np.array([1.0, 1.0, 1.1, 1.2, 1.3], dtype=np.float64)

    assert (
        PnoEngine._extend_pump_start_to_precursor_breakout(
            local_breakout=local_breakout,
            closes=closes,
            ema20=ema20,
            pump_start_idx=4,
            max_extension_bars=3,
        )
        == 2
    )


def test_allows_zero_pre_pump_ema_crosses_only_for_strong_sleep_wake_breakout() -> None:
    five = _build_test_five_frame(
        opens=[1.0, 1.1, 1.2],
        highs=[1.1, 1.3, 1.5],
        lows=[0.9, 1.05, 1.15],
        closes=[1.05, 1.25, 1.45],
        ema20=[1.0, 1.1, 1.2],
    )
    five.sleep_end_idx = pd.Series([-1, 0, 1], dtype="int64").to_numpy()

    assert PnoEngine._allows_zero_pre_pump_ema_crosses(
        five=five,
        pump_start_5m_idx=2,
        current_5m_idx=2,
        params=PnoParams(symbol="TEST/USDT"),
        pump_volume_ratio_start=5.5,
        pump_impulse_atr_pre=6.0,
        pump_path_efficiency=0.6,
        pump_body_wick_edge=0.2,
    )


def test_pno_stage1_ideal_like_allows_limited_zero_body_tape(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=list(range(0, 15 * 60_000, 60_000)), price=10.0)
    one.opens[5:11] = np.array([10.00, 10.08, 10.16, 10.24, 10.32, 10.40], dtype=np.float64)
    one.closes[5:11] = np.array([10.00, 10.14, 10.16, 10.30, 10.32, 10.48], dtype=np.float64)
    one.highs[5:11] = np.array([10.04, 10.18, 10.20, 10.34, 10.36, 10.52], dtype=np.float64)
    one.lows[5:11] = np.array([9.98, 10.06, 10.14, 10.20, 10.30, 10.38], dtype=np.float64)
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    five = _build_test_five_frame(
        opens=[9.9, 10.0, 10.25],
        highs=[10.0, 10.3, 10.6],
        lows=[9.8, 9.95, 10.2],
        closes=[9.95, 10.2, 10.55],
        ema20=[9.7, 9.8, 9.95],
    )

    def _raise_if_zero_body_gate_passed(**kwargs):
        del kwargs
        raise RuntimeError("passed_zero_body_gate")

    monkeypatch.setattr(engine, "_resolve_reference_high", _raise_if_zero_body_gate_passed)

    _, rejection = engine._resolve_stage1_quality_metrics(
        one=one,
        five=five,
        idx=10,
        five_idx=2,
        pump_start_5m_idx=1,
        start_idx=5,
        active_high_idx=10,
        active_high=10.6,
        leg_start=9.95,
        leg_size=0.65,
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rejection is not None
    assert rejection["reason"] == "zero_body_bar_present_1m"

    with pytest.raises(RuntimeError, match="passed_zero_body_gate"):
        engine._resolve_stage1_quality_metrics(
            one=one,
            five=five,
            idx=10,
            five_idx=2,
            pump_start_5m_idx=1,
            start_idx=5,
            active_high_idx=10,
            active_high=10.6,
            leg_start=9.95,
            leg_size=0.65,
            params=PnoParams(symbol="TEST/USDT", ideal_like_impulse_enabled=True),
        )


def test_pno_stage1_ideal_like_allows_long_low_tick_tape_when_recent_impulse_is_clean(monkeypatch) -> None:
    engine = PnoEngine()
    one = _build_test_one_frame_multi(timestamps_ms=list(range(0, 60 * 60_000, 60_000)), price=10.0)
    opens = np.linspace(10.0, 10.9, num=60, dtype=np.float64)
    closes = opens.copy()
    closes[12:] = opens[12:] + np.where(np.arange(48) % 2 == 0, 0.0, 0.03)
    highs = np.maximum(opens, closes) + 0.03
    lows = np.minimum(opens, closes) - 0.02
    one.opens[:] = opens
    one.closes[:] = closes
    one.highs[:] = highs
    one.lows[:] = lows
    one.frame.loc[:, "open"] = one.opens
    one.frame.loc[:, "high"] = one.highs
    one.frame.loc[:, "low"] = one.lows
    one.frame.loc[:, "close"] = one.closes
    five = _build_test_five_frame(
        opens=[9.9, 10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 11.0],
        highs=[10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 11.0, 11.1],
        lows=[9.8, 9.95, 10.05, 10.15, 10.25, 10.35, 10.45, 10.55, 10.65, 10.75, 10.85, 10.95],
        closes=[9.95, 10.05, 10.15, 10.25, 10.35, 10.45, 10.55, 10.65, 10.75, 10.85, 10.95, 11.05],
        ema20=[9.7, 9.8, 9.9, 10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8],
    )

    def _raise_if_zero_body_gate_passed(**kwargs):
        del kwargs
        raise RuntimeError("passed_zero_body_gate")

    monkeypatch.setattr(engine, "_resolve_reference_high", _raise_if_zero_body_gate_passed)

    _, rejection = engine._resolve_stage1_quality_metrics(
        one=one,
        five=five,
        idx=59,
        five_idx=11,
        pump_start_5m_idx=1,
        start_idx=0,
        active_high_idx=59,
        active_high=float(one.highs[59]),
        leg_start=9.95,
        leg_size=float(one.highs[59] - 9.95),
        params=PnoParams(symbol="TEST/USDT"),
    )

    assert rejection is not None
    assert rejection["reason"] == "zero_body_bar_present_1m"
    assert rejection["zero_body_bar_count_1m"] >= 36

    with pytest.raises(RuntimeError, match="passed_zero_body_gate"):
        engine._resolve_stage1_quality_metrics(
            one=one,
            five=five,
            idx=59,
            five_idx=11,
            pump_start_5m_idx=1,
            start_idx=0,
            active_high_idx=59,
            active_high=float(one.highs[59]),
            leg_start=9.95,
            leg_size=float(one.highs[59] - 9.95),
            params=PnoParams(symbol="TEST/USDT", ideal_like_impulse_enabled=True),
        )


def test_resolve_recent_shelf_highs_finds_fast_flat_shelf_before_confirmed_highs() -> None:
    engine = PnoEngine()
    one = engine._prepare_1m_frame(
        pd.DataFrame(
            {
                "timestamp": [0, 60_000, 120_000, 180_000, 240_000],
                "open": [10.00, 10.10, 10.14, 10.16, 10.18],
                "high": [10.02, 10.20, 10.19, 10.21, 10.17],
                "low": [9.96, 10.05, 10.10, 10.12, 10.14],
                "close": [9.99, 10.16, 10.17, 10.19, 10.15],
                "volume": [10.0, 11.0, 12.0, 13.0, 12.0],
            }
        )
    )

    shelf = engine._resolve_recent_shelf_highs(
        one=one,
        start_idx=1,
        end_idx=4,
        active_high=10.5,
        v1_now=0.2,
    )

    assert shelf == [1, 2, 3]


@pytest.mark.parametrize(
    (
        "case_id",
        "symbol",
        "category_mode",
        "row_number",
        "window_start_ms",
        "window_end_ms",
        "expected_entry_min_ms",
        "expected_entry_max_ms",
        "min_pnl_percent",
    ),
    [
        (
            case["case_id"],
            case["symbol"],
            case["category_mode"],
            case["row_number"],
            case["window_start_ms"],
            case["window_end_ms"],
            case["expected_entry_min_ms"],
            case["expected_entry_max_ms"],
            case["min_pnl_percent"],
        )
        for case in _PNO_GOLDEN_TRADE_CASES
    ],
)
def test_pno_golden_trade_cases_keep_positive_example_alive(
    case_id: str,
    symbol: str,
    category_mode: str,
    row_number: int,
    window_start_ms: int,
    window_end_ms: int,
    expected_entry_min_ms: int,
    expected_entry_max_ms: int,
    min_pnl_percent: float,
) -> None:
    del case_id
    params, mtf_frames = _load_pno_golden_case(
        symbol=symbol,
        row_number=row_number,
        window_end_ms=window_end_ms,
    )
    strategy = PnoStrategy(
        deposit=float(params.pno_deposit),
        risk_pct=float(params.pno_risk_pct),
        cache_dir=Path(tempfile.mkdtemp()),
        category_mode_filter=category_mode,
    )

    trades = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)

    matching_trades = [
        trade
        for trade in trades
        if window_start_ms <= int(trade.entry_timestamp_ms) <= window_end_ms
        and expected_entry_min_ms <= int(trade.entry_timestamp_ms) <= expected_entry_max_ms
        and float(trade.pnl_percent.value) >= float(min_pnl_percent)
    ]

    assert matching_trades, f"expected profitable golden trade for {symbol} inside regression window"


def test_pno_dogs_ideal_like_regression_window_produces_trade() -> None:
    cache_dir = Path(r"C:\Users\Ascf\PycharmProjects\mtf-trend-2\.output\cache")
    dogs_cache_dir = cache_dir / "DOGS%2FUSDT%3AUSDT"
    if not dogs_cache_dir.exists():
        pytest.skip("DOGS cache is not available locally")

    symbol = "DOGS/USDT:USDT"
    end_timestamp_ms = 1725062340000  # 2024-08-30 23:59 UTC
    preparer = DataPreparer(cache_dir)
    mtf_frames = SymbolMtfFrames(
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.S30,
        levels_frame=preparer.load_symbol_data(symbol, Timeframe.M5, days=4, end_timestamp_ms=end_timestamp_ms),
        entry_frame=preparer.load_symbol_data(symbol, Timeframe.M1, days=4, end_timestamp_ms=end_timestamp_ms),
    )
    params = PnoParams(
        symbol=symbol,
        entry_confirmation_mode="close_above",
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.S30,
        min_entry_rr=1.0,
    )
    strategy = PnoStrategy(
        deposit=float(params.pno_deposit),
        risk_pct=float(params.pno_risk_pct),
        cache_dir=Path(tempfile.mkdtemp()),
        category_mode_filter="discovery",
    )

    trades = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)

    matching_trades = [
        trade
        for trade in trades
        if 1724838000000 <= int(trade.entry_timestamp_ms) <= 1724838060000
        and str(trade.metadata.get("pno_category_id")) == "cat_d_category_4"
        and float(trade.pnl_percent.value) >= 0.0
    ]

    assert matching_trades, "expected DOGS continuation trade in 2024-08-28 regression window on 5m-30s"


def test_pno_dogs_human_bos_regression_window_produces_structured_1m_5s_trade() -> None:
    cache_dir = Path(r"C:\Users\Ascf\PycharmProjects\mtf-trend-2\.output\cache")
    dogs_cache_dir = cache_dir / "DOGS%2FUSDT%3AUSDT"
    if not dogs_cache_dir.exists():
        pytest.skip("DOGS cache is not available locally")

    symbol = "DOGS/USDT:USDT"
    end_timestamp_ms = 1724845000000
    preparer = DataPreparer(cache_dir)
    mtf_frames = SymbolMtfFrames(
        levels_timeframe=Timeframe.M1,
        entry_timeframe=Timeframe.S5,
        levels_frame=preparer.load_symbol_data(symbol, Timeframe.M1, days=1, end_timestamp_ms=end_timestamp_ms),
        entry_frame=preparer.load_symbol_data(symbol, Timeframe.M1, days=1, end_timestamp_ms=end_timestamp_ms),
    )
    params = PnoParams(
        symbol=symbol,
        entry_confirmation_mode="close_above",
        levels_timeframe=Timeframe.M1,
        entry_timeframe=Timeframe.S5,
        min_entry_rr=1.0,
    )
    strategy = PnoStrategy(
        deposit=float(params.pno_deposit),
        risk_pct=float(params.pno_risk_pct),
        cache_dir=Path(tempfile.mkdtemp()),
        category_mode_filter="discovery",
    )

    trades = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
    diagnostics = strategy.consume_last_generation_diagnostics()

    matching_trades = [
        trade
        for trade in trades
        if int(trade.entry_timestamp_ms) == 1724843700000
        and float(trade.pnl_percent.value) > 0.0
        and str(trade.metadata.get("structure_source")) == "human_bos"
    ]
    assert matching_trades, "expected DOGS 1m-5s profitable BOS trade in the 2024-08-28 regression window"

    trade = matching_trades[0]
    pivot_timestamps = tuple(int(value) for value in trade.metadata.get("structure_pivot_timestamps_ms", ()))
    pivot_prices = tuple(float(value) for value in trade.metadata.get("structure_pivot_prices", ()))
    pivot_kinds = tuple(str(value) for value in trade.metadata.get("structure_pivot_kinds", ()))

    assert int(trade.metadata.get("structure_break_timestamp_ms", 0)) == int(trade.entry_timestamp_ms)
    assert len(pivot_timestamps) >= 4
    assert len(pivot_timestamps) == len(pivot_prices) == len(pivot_kinds)
    assert pivot_timestamps == tuple(sorted(pivot_timestamps))
    assert set(pivot_kinds).issubset({"H", "L"})
    assert all(left != right for left, right in zip(pivot_kinds, pivot_kinds[1:], strict=False))
    assert pivot_kinds[-2:] == ("H", "L")
    assert pivot_timestamps[-2] == int(trade.metadata.get("structure_high_timestamp_ms", 0))
    assert pivot_timestamps[-1] == int(trade.metadata.get("structure_low_timestamp_ms", 0))

    stage3_rows = [
        row
        for row in diagnostics.get("stage_events", [])
        if row.get("stage_id") == "stage_3_valid_pullback"
        and int(row.get("structure_break_timestamp_ms") or 0) == int(trade.entry_timestamp_ms)
    ]
    assert stage3_rows, "expected matching stage3 BOS diagnostics row for DOGS 1m-5s regression"

    stage3_row = stage3_rows[0]
    assert tuple(int(value) for value in stage3_row.get("structure_pivot_timestamps_ms", ())) == pivot_timestamps
    assert tuple(str(value) for value in stage3_row.get("structure_pivot_kinds", ())) == pivot_kinds


def test_pno_seconds_cache_persists_across_temp_runtime_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persistent_cache_dir = tmp_path / "persistent-cache"
    monkeypatch.setenv("CACHE_DIR", str(persistent_cache_dir))
    expected_columns = ["timestamp", "open", "high", "low", "close", "volume"]

    start_timestamp_ms = 1_720_000_000_000
    end_timestamp_ms = start_timestamp_ms + 4_000
    seconds_frame = pd.DataFrame(
        {
            "timestamp": [start_timestamp_ms + offset * 1_000 for offset in range(5)],
            "open": [1.0, 1.1, 1.2, 1.3, 1.4],
            "high": [1.1, 1.2, 1.3, 1.4, 1.5],
            "low": [0.9, 1.0, 1.1, 1.2, 1.3],
            "close": [1.05, 1.15, 1.25, 1.35, 1.45],
            "volume": [10.0, 11.0, 12.0, 13.0, 14.0],
        }
    )

    provider = _PnoSecondsFrameProvider(cache_dir=Path(tempfile.mkdtemp()))
    provider._shared_window_cache.clear()
    provider._shared_day_cache.clear()

    fetch_calls = {"count": 0}

    def _fetch_seconds_for_day(*, symbol: str, utc_day: object) -> pd.DataFrame:
        del symbol, utc_day
        fetch_calls["count"] += 1
        return seconds_frame.copy()

    monkeypatch.setattr(provider, "_fetch_seconds_for_day", _fetch_seconds_for_day)

    first_frame = provider._ensure_seconds_window(
        symbol="TEST/USDT:USDT",
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
    )

    assert fetch_calls["count"] == 1
    pd.testing.assert_frame_equal(
        first_frame.loc[:, expected_columns].reset_index(drop=True),
        seconds_frame.loc[:, expected_columns].reset_index(drop=True),
    )

    persisted_frame = DataPreparer(persistent_cache_dir).load_symbol_data_range(
        "TEST/USDT:USDT",
        Timeframe.S1,
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
    )
    pd.testing.assert_frame_equal(
        persisted_frame.loc[:, expected_columns].reset_index(drop=True),
        seconds_frame.loc[:, expected_columns].reset_index(drop=True),
    )

    second_provider = _PnoSecondsFrameProvider(cache_dir=Path(tempfile.mkdtemp()))
    second_provider._shared_window_cache.clear()
    second_provider._shared_day_cache.clear()

    def _unexpected_fetch(*, symbol: str, utc_day: object) -> pd.DataFrame:
        del symbol, utc_day
        raise AssertionError("seconds data should be loaded from persistent cache without refetch")

    monkeypatch.setattr(second_provider, "_fetch_seconds_for_day", _unexpected_fetch)

    second_frame = second_provider._ensure_seconds_window(
        symbol="TEST/USDT:USDT",
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
    )

    pd.testing.assert_frame_equal(
        second_frame.loc[:, expected_columns].reset_index(drop=True),
        seconds_frame.loc[:, expected_columns].reset_index(drop=True),
    )


def test_pno_sparse_aggregated_window_cache_persists_across_temp_runtime_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persistent_cache_dir = tmp_path / "persistent-cache"
    monkeypatch.setenv("CACHE_DIR", str(persistent_cache_dir))
    provider = _PnoSecondsFrameProvider(cache_dir=Path(tempfile.mkdtemp()))
    provider._aggregated_window_cache.clear()
    provider._shared_aggregated_window_cache.clear()

    start_timestamp_ms = 1_720_000_000_000
    end_timestamp_ms = start_timestamp_ms + 14_000
    seconds_frame = pd.DataFrame(
        {
            "timestamp": [start_timestamp_ms + offset * 1_000 for offset in range(15)],
            "open": [1.0 + (0.01 * offset) for offset in range(15)],
            "high": [1.02 + (0.01 * offset) for offset in range(15)],
            "low": [0.98 + (0.01 * offset) for offset in range(15)],
            "close": [1.01 + (0.01 * offset) for offset in range(15)],
            "volume": [10.0 + offset for offset in range(15)],
        }
    )
    expected = PnoEngine._aggregate_frame(seconds_frame, target_timeframe_ms=Timeframe.S5.to_milliseconds())

    ensure_calls = {"count": 0}

    def _ensure_seconds_window(*, symbol: str, start_timestamp_ms: int, end_timestamp_ms: int) -> pd.DataFrame:
        del symbol, start_timestamp_ms, end_timestamp_ms
        ensure_calls["count"] += 1
        return seconds_frame.copy()

    monkeypatch.setattr(provider, "_ensure_seconds_window", _ensure_seconds_window)
    first = provider.load_aggregated_window(
        symbol="TEST/USDT:USDT",
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
        target_timeframe=Timeframe.S5,
    )

    assert ensure_calls["count"] == 1
    pd.testing.assert_frame_equal(first.reset_index(drop=True), expected.reset_index(drop=True))

    second_provider = _PnoSecondsFrameProvider(cache_dir=Path(tempfile.mkdtemp()))
    second_provider._aggregated_window_cache.clear()
    second_provider._shared_aggregated_window_cache.clear()

    def _unexpected_ensure(*, symbol: str, start_timestamp_ms: int, end_timestamp_ms: int) -> pd.DataFrame:
        del symbol, start_timestamp_ms, end_timestamp_ms
        raise AssertionError("aggregated sparse window should be loaded from persistent cache without seconds reload")

    monkeypatch.setattr(second_provider, "_ensure_seconds_window", _unexpected_ensure)
    second = second_provider.load_aggregated_window(
        symbol="TEST/USDT:USDT",
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
        target_timeframe=Timeframe.S5,
    )

    pd.testing.assert_frame_equal(second.reset_index(drop=True), expected.reset_index(drop=True))


def test_pno_aevo_cat_d_upper_tf_level_regression_window_produces_trade() -> None:
    cache_dir = Path(r"C:\Users\Ascf\PycharmProjects\mtf-trend-2\.output\cache")
    aevo_cache_dir = cache_dir / "AEVO%2FUSDT%3AUSDT"
    if not aevo_cache_dir.exists():
        pytest.skip("AEVO cache is not available locally")

    symbol = "AEVO/USDT:USDT"
    preparer = DataPreparer(cache_dir)
    mtf_frames = SymbolMtfFrames(
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        levels_frame=preparer.load_symbol_data(symbol, Timeframe.M5, days=30),
        entry_frame=preparer.load_symbol_data(symbol, Timeframe.M1, days=30),
    )
    params = PnoParams(symbol=symbol, entry_confirmation_mode="close_above", min_entry_rr=1.0)
    strategy = PnoStrategy(
        deposit=float(params.pno_deposit),
        risk_pct=float(params.pno_risk_pct),
        cache_dir=Path(tempfile.mkdtemp()),
        category_mode_filter="discovery",
    )

    trades = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)

    matching_trades = [
        trade
        for trade in trades
        if 1775583960000 <= int(trade.entry_timestamp_ms) <= 1775584140000
        and str(trade.metadata.get("pno_category_id")) == "cat_d_category_4"
        and abs(float(trade.metadata.get("level", 0.0)) - 0.02413) <= 0.00004
    ]

    assert matching_trades, "expected AEVO cat_d trade near the 2026-04-07 17:45 UTC upper-TF reclaim window"
