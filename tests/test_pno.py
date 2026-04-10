import argparse
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cli import commands
from cli.parser import build_parser, resolve_handler
from config.app_config import AppConfig
from config.backtest_config import BacktestConfig
from config.fetch_config import FetchConfig
from config.simulation_config import SimulationConfig
from config.strategy_config import StrategyConfig
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
    resolve_pno_default_timeframe_pair,
    validate_pno_params,
    validate_pno_timeframe_pair,
    with_pno_risk,
)
from strategy.pno.engine import (
    ArmedContext,
    OneMinuteFrame,
    PnoEngine,
    RetiredCluster,
    Stage1Context,
    Stage3Context,
    Stage4Context,
)
from vectorbt_runner.mtf_frames import SymbolMtfFrames


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


def test_resolve_backtest_timeframes_normalizes_pno_defaults() -> None:
    levels_tf, entry_tf = commands._resolve_backtest_timeframes(
        strategy_id="pno",
        args=argparse.Namespace(entry_tf=None, levels_tf=None),
        configured_levels_timeframe=Timeframe.D1,
        configured_entry_timeframe=Timeframe.M15,
    )

    assert levels_tf == Timeframe.M5
    assert entry_tf == Timeframe.M1


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
    assert resolve_pno_default_timeframe_pair(mode="backtest") == (Timeframe.M5, Timeframe.M1)
    assert resolve_pno_default_timeframe_pair(mode="live") == (Timeframe.M5, Timeframe.M1)


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
        pullback_min_pump_fraction_5m=0.22,
        pullback_valid_max_v5=4.0,
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
    assert rebuilt.pullback_min_pump_fraction_5m == original.pullback_min_pump_fraction_5m
    assert rebuilt.pullback_valid_max_v5 == original.pullback_valid_max_v5
    assert rebuilt.min_score == original.min_score
    assert rebuilt.strong_score == original.strong_score


def test_build_pno_grid_includes_cross_and_close_confirmation_variants() -> None:
    grid = build_pno_grid()

    assert [params.entry_confirmation_mode for params in grid] == ["cross", "close_above"]
    assert [params.pno_variant_id for params in grid] == ["baseline_cross", "baseline_close"]


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
    assert int(stage1_row["passed_charts_count"]) == 1


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


def test_parser_supports_pno_stage_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["pno-stage", "s4"])

    assert args.command == "pno-stage"
    assert args.preset == "s4"
    assert resolve_handler(args.command) is commands.run_pno_stage


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
            {"pno_variant_id": "baseline_cross", "trades_count": 0, "profit_factor": 0.0, "ppa_stage_hits_stage_2_high_pullback": 0},
            {"pno_variant_id": "baseline_close", "trades_count": 0, "profit_factor": 0.0, "ppa_stage_hits_stage_2_high_pullback": 0},
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
        params=PnoParams(symbol="TEST/USDT", pno_r_trade=20.0),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


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
        params=PnoParams(symbol="TEST/USDT", pno_r_trade=20.0, entry_confirmation_mode="close_above"),
        armed=armed,
    )

    assert trade is not None
    assert trade.entry_timestamp_ms == 180_000
    assert trade.result_type == TradeResultType.TP2
    assert trade.metadata["entry_confirmation_mode"] == "close_above"
    assert trade.metadata["entry_signal_kind"] == "close_above"
    assert trade.metadata["entry_signal_timestamp_ms"] == 120_000
    assert trade.metadata["base_final_score"] == pytest.approx(76.0)
    assert trade.metadata["final_score"] > trade.metadata["base_final_score"]
    assert trade.metadata["trigger_score_body_close"] > 0
    assert trade.metadata["trigger_score_overhead"] > 0
    assert trade.metadata["entry_price_actual"] == pytest.approx(10.02)
    assert trade.metadata["sl_actual"] == pytest.approx(9.5)
    assert trade.metadata["tp1"] == pytest.approx(10.5)
    assert trade.metadata["tp2"] == pytest.approx(11.0)
    assert trade.metadata["be_protect_price"] >= trade.metadata["entry_price_actual"]
    assert trade.metadata["tp1"] > trade.metadata["entry_price_actual"]
    assert exit_idx == 3


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
        params=PnoParams(symbol="TEST/USDT", pno_r_trade=20.0, entry_confirmation_mode="close_above"),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_pno_close_above_waits_for_better_trigger_when_signal_score_is_weak() -> None:
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
        params=PnoParams(symbol="TEST/USDT", pno_r_trade=20.0, entry_confirmation_mode="close_above"),
        armed=armed,
    )

    assert trade is None
    assert exit_idx == 1


def test_pno_level_maturity_fraction_is_based_on_time_since_main_high() -> None:
    engine = PnoEngine()

    assert engine._resolve_level_maturity_fraction(active_high_idx=10, cluster_first_idx=16, current_idx=17) == pytest.approx(1 / 7)
    assert engine._resolve_level_maturity_fraction(active_high_idx=10, cluster_first_idx=12, current_idx=18) == pytest.approx(0.75)


def test_pno_entry_pullback_fraction_measures_real_entry_position() -> None:
    engine = PnoEngine()

    assert engine._resolve_entry_pullback_fraction(pullback_low=8.0, active_high=10.0, entry_price=8.9) == pytest.approx(0.45)
    assert engine._resolve_entry_pullback_fraction(pullback_low=8.0, active_high=10.0, entry_price=9.4) == pytest.approx(0.7)
