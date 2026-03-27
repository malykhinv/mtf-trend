import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from cli import commands
from cli.parser import build_parser
from config import AppConfig, BacktestConfig, FetchConfig, SimulationConfig, StrategyConfig
from domain.enums.timeframe import Timeframe
from strategy.post_pump_absorption.research import (
    build_post_pump_absorption_research_artifacts,
    load_post_pump_absorption_research_run,
)


def _build_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        fetch=FetchConfig(binance_api_key="", binance_secret_key=""),
        strategy=StrategyConfig(strategy_id="post_pump_absorption"),
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


def _write_fake_run(base_dir: Path, timeframe: Timeframe, *, symbol: str, profit_factor: float, pnl_percent: float) -> Path:
    strategy_dir = base_dir / timeframe.value / "strategy" / "post_pump_absorption"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "ppa_profile_id": "balanced",
                "profit_factor": profit_factor,
                "pnl_percent": pnl_percent,
                "win_rate": 0.5,
                "trades_count": 2,
                "max_dd": 35.0,
                "max_drawdown_pct": 4.2,
                "sl_count": 0,
                "be_count": 0,
                "time_exit_profit_count": 0,
                "tp1_be_count": 1,
                "tp2_count": 1,
                "ppa_setup_lsb_count": 1,
                "ppa_setup_mbb_count": 1,
                "ppa_median_entry_range_fraction": 0.28,
                "ppa_median_aggression_ratio": 0.71,
                "ppa_median_aggression_volume_mult": 1.75,
                "ppa_median_aggression_ratio_threshold": 0.55,
                "ppa_median_aggression_volume_threshold": 1.15,
                "ppa_median_oi_delta": 12.0,
                "ppa_median_oi_delta_pct": 0.012,
                "ppa_median_entry_minute_of_hour": 30.0,
                "ppa_median_stop_distance_atr": 0.31,
                "ppa_median_mfe_r": 1.85,
                "ppa_median_mae_r": 0.42,
                "ppa_median_holding_bars": 6.0,
                "ppa_oi_available_count": 2,
                "ppa_oi_supportive_count": 2,
                "ppa_entry_on_1m_boundary_count": 2,
                "ppa_entry_on_5m_boundary_count": 1,
                "ppa_entry_on_30m_boundary_count": 1,
                "ppa_entry_on_60m_boundary_count": 0,
                "ppa_range_mid_hit_count": 2,
                "ppa_range_high_hit_count": 1,
                "ppa_tp1_hit_count": 2,
                "ppa_tp2_hit_count": 1,
            }
        ]
    ).to_csv(strategy_dir / "results.csv", index=False)

    diagnostics_dir = strategy_dir / "trade_plots" / "post_pump_absorption_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    trade_rows = [
        {
            "symbol": symbol,
            "setup_type": "LSB",
            "result_type": "TP2",
            "pnl": 30.0,
            "pnl_percent": 3.0,
            "entry_range_fraction": 0.22,
            "aggression_ratio": 0.69,
            "aggression_volume_mult": 1.7,
            "aggression_ratio_threshold": 0.55,
            "aggression_volume_threshold": 1.15,
            "oi_available": True,
            "oi_supportive": True,
            "oi_delta": 10.0,
            "oi_delta_pct": 0.01,
            "entry_minute_of_hour": 30,
            "entry_on_1m_boundary": True,
            "entry_on_5m_boundary": True,
            "entry_on_30m_boundary": True,
            "entry_on_60m_boundary": False,
            "stop_distance_atr": 0.29,
            "mfe_r": 2.1,
            "mae_r": 0.35,
            "holding_bars": 5,
            "range_mid_hit": True,
            "range_high_hit": True,
            "tp1_hit": True,
            "tp2_hit": True,
        },
        {
            "symbol": symbol,
            "setup_type": "MBB",
            "result_type": "TP1_BE",
            "pnl": 12.0,
            "pnl_percent": 1.2,
            "entry_range_fraction": 0.34,
            "aggression_ratio": 0.73,
            "aggression_volume_mult": 1.8,
            "aggression_ratio_threshold": 0.55,
            "aggression_volume_threshold": 1.15,
            "oi_available": True,
            "oi_supportive": True,
            "oi_delta": 14.0,
            "oi_delta_pct": 0.014,
            "entry_minute_of_hour": 35,
            "entry_on_1m_boundary": True,
            "entry_on_5m_boundary": True,
            "entry_on_30m_boundary": False,
            "entry_on_60m_boundary": False,
            "stop_distance_atr": 0.33,
            "mfe_r": 1.6,
            "mae_r": 0.48,
            "holding_bars": 7,
            "range_mid_hit": True,
            "range_high_hit": False,
            "tp1_hit": True,
            "tp2_hit": False,
        },
    ]
    pd.DataFrame(trade_rows).to_csv(diagnostics_dir / f"{symbol.replace('/', '_')}_trades.csv", index=False)
    payload = {
        "symbol": symbol,
        "trades_generated": len(trade_rows),
        "diagnostics": {
            "pumps_found": 3,
            "range_candidates": 2,
            "lower_zone_hits": 2,
            "aggression_hits": 2,
            "lsb_hits": 1,
            "mbb_hits": 1,
            "trades_generated": 2,
            "reentries_generated": 0,
            "missing_taker_data": 0,
        },
        "trades": trade_rows,
    }
    (diagnostics_dir / f"{symbol.replace('/', '_')}_diagnostics.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return strategy_dir


def test_build_post_pump_absorption_research_artifacts_writes_reports_and_charts(tmp_path: Path) -> None:
    base_dir = tmp_path / "runs"
    dir_m1 = _write_fake_run(base_dir, Timeframe.M1, symbol="AAA/USDT", profit_factor=1.6, pnl_percent=4.2)
    dir_m3 = _write_fake_run(base_dir, Timeframe.M3, symbol="BBB/USDT", profit_factor=1.2, pnl_percent=2.1)

    runs = [
        load_post_pump_absorption_research_run(timeframe=Timeframe.M1, strategy_results_dir=dir_m1),
        load_post_pump_absorption_research_run(timeframe=Timeframe.M3, strategy_results_dir=dir_m3),
    ]
    artifacts = build_post_pump_absorption_research_artifacts(
        output_dir=tmp_path / "research",
        runs=runs,
        run_context={"timeframes": ["1m", "3m"], "ppa_profile": "balanced"},
    )

    summary = pd.read_csv(artifacts["timeframe_summary_csv"])
    trades = pd.read_csv(artifacts["all_trades"])
    symbol_summary = pd.read_csv(artifacts["symbol_summary"])

    assert set(summary["timeframe"]) == {"1m", "3m"}
    assert len(trades) == 4
    assert set(symbol_summary["symbol"]) == {"AAA/USDT", "BBB/USDT"}
    assert artifacts["report"].exists()
    assert (artifacts["charts_dir"] / "performance_by_timeframe.png").exists()
    assert (artifacts["charts_dir"] / "outcomes_by_timeframe.png").exists()
    assert (artifacts["charts_dir"] / "setups_by_timeframe.png").exists()
    assert (artifacts["charts_dir"] / "quality_medians_by_timeframe.png").exists()
    assert (artifacts["charts_dir"] / "hit_rates_by_timeframe.png").exists()
    assert (artifacts["charts_dir"] / "context_markers_by_timeframe.png").exists()
    assert "Timeframe Summary" in artifacts["report"].read_text(encoding="utf-8")


def test_run_ppa_research_inner_runs_all_micro_timeframes(monkeypatch, tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    output_dir = tmp_path / "research_output"
    calls: list[str] = []

    def _fake_run_backtest_inner(scoped_config: AppConfig, scoped_args: argparse.Namespace) -> int:
        calls.append(str(scoped_args.entry_tf))
        strategy_dir = commands._resolve_results_dir_for_strategy(
            scoped_config.backtest.results_dir,
            "post_pump_absorption",
        )
        scoped_config.backtest.results_dir = strategy_dir
        strategy_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "ppa_profile_id": "balanced",
                    "profit_factor": 1.1,
                    "pnl_percent": 1.5,
                    "win_rate": 0.5,
                    "trades_count": 1,
                    "max_dd": 10.0,
                    "max_drawdown_pct": 1.0,
                    "sl_count": 0,
                    "be_count": 0,
                    "time_exit_profit_count": 0,
                    "tp1_be_count": 0,
                    "tp2_count": 1,
                    "ppa_oi_available_count": 1,
                    "ppa_oi_supportive_count": 1,
                }
            ]
        ).to_csv(strategy_dir / "results.csv", index=False)
        diagnostics_dir = strategy_dir / "trade_plots" / "post_pump_absorption_diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        trade_row = {
            "symbol": "AAA/USDT",
            "setup_type": "LSB",
            "result_type": "TP2",
            "pnl": 10.0,
            "pnl_percent": 1.0,
            "entry_range_fraction": 0.2,
            "aggression_ratio": 0.7,
            "aggression_volume_mult": 1.5,
            "oi_available": True,
            "oi_supportive": True,
            "entry_on_1m_boundary": True,
            "entry_on_5m_boundary": True,
            "entry_on_30m_boundary": False,
            "entry_on_60m_boundary": False,
            "stop_distance_atr": 0.3,
            "mfe_r": 1.7,
            "mae_r": 0.4,
            "holding_bars": 4,
            "range_mid_hit": True,
            "range_high_hit": True,
            "tp1_hit": True,
            "tp2_hit": True,
        }
        pd.DataFrame([trade_row]).to_csv(diagnostics_dir / "AAA_USDT_trades.csv", index=False)
        (diagnostics_dir / "AAA_USDT_diagnostics.json").write_text(
            json.dumps(
                {
                    "symbol": "AAA/USDT",
                    "trades_generated": 1,
                    "diagnostics": {
                        "pumps_found": 1,
                        "range_candidates": 1,
                        "lower_zone_hits": 1,
                        "aggression_hits": 1,
                        "lsb_hits": 1,
                        "trades_generated": 1,
                        "missing_taker_data": 0,
                    },
                    "trades": [trade_row],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(commands, "_run_backtest_inner", _fake_run_backtest_inner)

    args = argparse.Namespace(
        symbols=None,
        top_n=25,
        timeframes=None,
        ppa_profile="balanced",
        ppa_deposit=1000.0,
        ppa_risk_pct=0.02,
        output_dir=str(output_dir),
    )

    exit_code = commands._run_ppa_research_inner(config, args)

    assert exit_code == 0
    assert calls == ["1m", "3m", "5m"]
    assert (output_dir / "summary_by_timeframe.csv").exists()
    assert (output_dir / "research_report.md").exists()
    assert (output_dir / "charts" / "performance_by_timeframe.png").exists()


def test_cli_parser_supports_run_ppa_research_command() -> None:
    args = build_parser().parse_args(["run-ppa-research", "--timeframes", "1m", "5m"])

    assert args.command == "run-ppa-research"
    assert args.timeframes == ["1m", "5m"]


def test_run_ppa_stage_inner_runs_all_micro_timeframes(monkeypatch, tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    output_dir = tmp_path / "stage_output"
    calls: list[str] = []

    def _fake_run_backtest_inner(scoped_config: AppConfig, scoped_args: argparse.Namespace) -> int:
        calls.append(str(scoped_args.entry_tf))
        strategy_dir = commands._resolve_results_dir_for_strategy(
            scoped_config.backtest.results_dir,
            "post_pump_absorption",
        )
        diagnostics_dir = strategy_dir / "trade_plots" / "post_pump_absorption_diagnostics" / "stage_reviews"
        (diagnostics_dir / "stage_4_aggression").mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "stage_id": "stage_4_aggression",
                    "events_count": 3,
                    "events_path": str(diagnostics_dir / "stage_4_aggression" / "events.csv"),
                    "summary_path": str(diagnostics_dir / "stage_4_aggression" / "summary_by_symbol.csv"),
                }
            ]
        ).to_csv(diagnostics_dir / "manifest.csv", index=False)
        return 0

    monkeypatch.setattr(commands, "_run_backtest_inner", _fake_run_backtest_inner)

    args = argparse.Namespace(
        preset="s4",
        symbols=None,
        top_n=25,
        timeframes=None,
        ppa_profile="balanced",
        ppa_deposit=1000.0,
        ppa_risk_pct=0.02,
        output_dir=str(output_dir),
    )

    exit_code = commands._run_ppa_stage_inner(config, args)

    assert exit_code == 0
    assert sorted(calls) == ["1m", "3m", "5m"]
    assert (output_dir / "stage_review_summary.csv").exists()
    assert (output_dir / "stage_review_context.json").exists()


def test_select_ppa_plot_params_row_by_stage_uses_results_stage_columns_without_replay() -> None:
    class _StrategyStub:
        def generate_events_multi_tf(self, *args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("unexpected replay")

        def consume_last_generation_diagnostics(self):  # pragma: no cover - should not be called
            raise AssertionError("unexpected diagnostics replay")

    results = pd.DataFrame(
        [
            {
                "profit_factor": 1.1,
                "trades_count": 2,
                "ppa_stage_hits_stage_4_aggression": 3,
            },
            {
                "profit_factor": 1.0,
                "trades_count": 5,
                "ppa_stage_hits_stage_4_aggression": 7,
            },
        ]
    )

    selected = commands._select_ppa_plot_params_row_by_stage(
        args=argparse.Namespace(ppa_stage=4, ppa_through_stage=None),
        strategy=_StrategyStub(),  # type: ignore[arg-type]
        symbol_frames={"BTC/USDT": object()},  # type: ignore[dict-item]
        results=results,
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.M1,
        logger=logging.getLogger("test"),
    )

    assert selected is not None
    assert int(selected["ppa_stage_hits_stage_4_aggression"]) == 7
