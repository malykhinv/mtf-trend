import argparse
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

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
        fee_rate=0.0007,
        min_stage1_leg_v1=1.25,
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
    assert rebuilt.fee_rate == original.fee_rate
    assert rebuilt.min_stage1_leg_v1 == original.min_stage1_leg_v1
    assert rebuilt.pullback_valid_max_v5 == original.pullback_valid_max_v5
    assert rebuilt.min_score == original.min_score
    assert rebuilt.strong_score == original.strong_score


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
        metadata={"category": "tp2", "final_score": 82.0},
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
    assert (diagnostics_dir / "stage_reviews" / "manifest.csv").exists()
    manifest = pd.read_csv(diagnostics_dir / "stage_reviews" / "manifest.csv")
    assert "stage_1_pump" in set(manifest["stage_id"])
    assert "stage_5_trade" in set(manifest["stage_id"])


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
