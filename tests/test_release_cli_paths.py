import argparse
from types import SimpleNamespace

import launcher
import pandas as pd
import pytest
from cli import commands
from cli.parser import build_parser, resolve_handler
from domain.enums.timeframe import Timeframe


def test_fetch_data_parser_accepts_symbols_timeframes_and_skip_open_interest() -> None:
    args = build_parser().parse_args(
        [
            "fetch-data",
            "--symbols",
            "BTC/USDT",
            "ETH/USDT",
            "--timeframes",
            "1m",
            "3m",
            "5m",
            "--skip-open-interest",
        ]
    )

    assert args.command == "fetch-data"
    assert args.symbols == ["BTC/USDT", "ETH/USDT"]
    assert args.timeframes == ["1m", "3m", "5m"]
    assert args.skip_open_interest is True


def test_update_cache_parser_accepts_symbols_timeframes_and_skip_open_interest() -> None:
    args = build_parser().parse_args(
        [
            "update-cache",
            "--symbols",
            "BTC/USDT",
            "--timeframes",
            "1m",
            "--skip-open-interest",
        ]
    )

    assert args.command == "update-cache"
    assert args.symbols == ["BTC/USDT"]
    assert args.timeframes == ["1m"]
    assert args.skip_open_interest is True


def test_launcher_parser_supports_ppa_research_mode() -> None:
    args = launcher._build_parser().parse_args(
        [
            "--mode",
            launcher.MODE_PPA_RESEARCH,
            "--ppa-profile",
            "strict",
            "--timeframes",
            "1m",
            "5m",
        ]
    )

    assert args.mode == launcher.MODE_PPA_RESEARCH
    assert args.ppa_profile == "strict"
    assert args.timeframes == ["1m", "5m"]


def test_launcher_run_mode_routes_ppa_research(monkeypatch) -> None:
    called: list[str] = []

    def _fake_run_ppa_research(_config: object, _args: argparse.Namespace) -> int:
        called.append("run_ppa_research")
        return 0

    monkeypatch.setattr(commands, "run_ppa_research", _fake_run_ppa_research)

    exit_code = launcher._run_mode(
        config=object(),  # type: ignore[arg-type]
        mode=launcher.MODE_PPA_RESEARCH,
        task_args=argparse.Namespace(),
    )

    assert exit_code == 0
    assert called == ["run_ppa_research"]


def test_resolve_handler_supports_run_ppa_research() -> None:
    handler = resolve_handler("run-ppa-research")

    assert handler is commands.run_ppa_research


def test_run_backtest_parser_accepts_ppa_stage_arguments() -> None:
    args = build_parser().parse_args(
        [
            "run-backtest",
            "--strategy",
            "post_pump_absorption",
            "--entry-tf",
            "1m",
            "--ppa-through-stage",
            "4",
        ]
    )

    assert args.command == "run-backtest"
    assert args.strategy == "post_pump_absorption"
    assert args.ppa_through_stage == 4


def test_resolve_ppa_stage_ids_supports_single_stage_and_cumulative_mode() -> None:
    assert commands._resolve_ppa_stage_ids(argparse.Namespace(ppa_stage=4, ppa_through_stage=None)) == (
        "stage_4_aggression",
    )
    assert commands._resolve_ppa_stage_ids(argparse.Namespace(ppa_stage=None, ppa_through_stage=3)) == (
        "stage_1_pump",
        "stage_2_range",
        "stage_3_lower_zone",
    )


def test_cli_parser_rejects_removed_legacy_review_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["review-stage1"])


def test_launcher_parser_rejects_removed_legacy_mode() -> None:
    with pytest.raises(SystemExit):
        launcher._build_parser().parse_args(["--mode", "review-stage1"])


def test_resolve_fetch_timeframes_preserves_user_order() -> None:
    args = argparse.Namespace(timeframes=["1m", "5m", "3m"])

    resolved = commands._resolve_fetch_timeframes(args, fallback=(Timeframe.M15, Timeframe.M5))

    assert resolved == (Timeframe.M1, Timeframe.M5, Timeframe.M3)


def test_launcher_task_namespace_preserves_skip_open_interest_from_batch_payload() -> None:
    cli_args = launcher._build_parser().parse_args(["--mode", launcher.MODE_PPA_RESEARCH])

    task_args = launcher._task_namespace({"skip_open_interest": "true"}, cli_args)

    assert task_args.skip_open_interest is True


def test_resolve_backtest_timeframes_normalizes_ppa_to_micro_single_tf() -> None:
    levels_tf, entry_tf = commands._resolve_backtest_timeframes(
        strategy_id="post_pump_absorption",
        args=argparse.Namespace(entry_tf=None, levels_tf=None),
        configured_levels_timeframe=Timeframe.D1,
        configured_entry_timeframe=Timeframe.M15,
    )

    assert levels_tf == Timeframe.M5
    assert entry_tf == Timeframe.M3


def test_resolve_backtest_timeframes_allows_ppa_explicit_entry_tf_as_levels_source() -> None:
    levels_tf, entry_tf = commands._resolve_backtest_timeframes(
        strategy_id="post_pump_absorption",
        args=argparse.Namespace(entry_tf="1m", levels_tf="1m"),
        configured_levels_timeframe=Timeframe.D1,
        configured_entry_timeframe=Timeframe.M15,
    )

    assert levels_tf == Timeframe.M1
    assert entry_tf == Timeframe.M1


def test_resolve_backtest_timeframes_rejects_non_micro_ppa_entry_tf() -> None:
    with pytest.raises(ValueError, match="supports only micro timeframes"):
        commands._resolve_backtest_timeframes(
            strategy_id="post_pump_absorption",
            args=argparse.Namespace(entry_tf="15m", levels_tf="15m"),
            configured_levels_timeframe=Timeframe.D1,
            configured_entry_timeframe=Timeframe.M15,
        )


def test_run_backtest_reuses_preloaded_entry_frame_for_bee_bite_non_m15(monkeypatch, tmp_path) -> None:
    load_calls: list[tuple[str, Timeframe]] = []
    frame = pd.DataFrame(
        {
            "timestamp": [1, 2, 3],
            "open": [1.0, 1.1, 1.2],
            "high": [1.1, 1.2, 1.3],
            "low": [0.9, 1.0, 1.1],
            "close": [1.05, 1.15, 1.25],
            "volume": [10.0, 11.0, 12.0],
        }
    )

    class _FakePreparer:
        def __init__(self, _cache_dir: object) -> None:
            pass

        def list_symbols(self, _timeframe: Timeframe) -> list[str]:
            return ["BTC/USDT"]

        def load_symbol_data(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
            load_calls.append((symbol, timeframe))
            return frame.copy()

    class _FakeStage1Selector:
        @classmethod
        def for_timeframe(cls, _timeframe: Timeframe) -> "_FakeStage1Selector":
            return cls()

        def evaluate_symbol(self, symbol: str, frame: pd.DataFrame) -> SimpleNamespace:
            return SimpleNamespace(
                passed=True,
                reason="passed",
                rolling_volume_usdt=1000.0,
                pump_percent=0.1,
                retain_ratio=0.6,
                symbol=symbol,
            )

    class _FakeRunner:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self, *_args: object, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

        def build_summary(self, _results: pd.DataFrame) -> SimpleNamespace:
            return SimpleNamespace(total_combinations=0, profitable_combinations=0, best_pf=0.0)

    monkeypatch.setattr(commands, "DataPreparer", _FakePreparer)
    monkeypatch.setattr(commands, "BeeBiteStage1Selector", _FakeStage1Selector)
    monkeypatch.setattr(commands, "build_strategy", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(commands, "BacktestRunner", _FakeRunner)

    config = SimpleNamespace(
        strategy=SimpleNamespace(
            strategy_id="bee_bite",
            levels_timeframe=Timeframe.D1,
            entry_timeframe=Timeframe.M5,
            bee_bite_profile="A",
            bee_bite_grid_mode="baseline",
            bee_bite_reclaim_mode="strict",
            bee_bite_retest_mode="confirmation",
            bee_bite_cooldown_hours=8,
            bee_bite_max_age_range_hours=24,
            bee_bite_portfolio_top_n=None,
            bee_bite_deposit=1000.0,
            bee_bite_risk_pct=1.0,
            post_pump_absorption_profile="balanced",
            post_pump_absorption_deposit=1000.0,
            post_pump_absorption_risk_pct=1.0,
        ),
        backtest=SimpleNamespace(
            log_level="INFO",
            cache_dir=tmp_path,
            logs_dir=tmp_path,
            results_dir=tmp_path,
            results_file_name="results.csv",
        ),
    )
    args = argparse.Namespace(
        strategy="bee_bite",
        bee_bite_grid=None,
        bee_bite_reclaim_mode=None,
        bee_bite_retest_mode=None,
        bee_bite_cooldown_hours=None,
        bee_bite_max_age_range_hours=None,
        bee_bite_deposit=None,
        bee_bite_risk_pct=None,
        ppa_profile=None,
        ppa_deposit=None,
        ppa_risk_pct=None,
        levels_tf="1d",
        entry_tf="5m",
        symbols=["BTC/USDT"],
        top_n=None,
        plot=False,
        plot_from_results=False,
        results_input=None,
        id=None,
        output_dir=None,
    )

    exit_code = commands._run_backtest_inner(config, args)

    assert exit_code == 0
    assert load_calls.count(("BTC/USDT", Timeframe.M5)) == 1
    assert load_calls.count(("BTC/USDT", Timeframe.D1)) == 1
