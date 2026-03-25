import argparse

import launcher
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

    assert levels_tf == Timeframe.M3
    assert entry_tf == Timeframe.M3


def test_resolve_backtest_timeframes_rejects_non_micro_ppa_entry_tf() -> None:
    with pytest.raises(ValueError, match="supports only micro timeframes"):
        commands._resolve_backtest_timeframes(
            strategy_id="post_pump_absorption",
            args=argparse.Namespace(entry_tf="15m", levels_tf="15m"),
            configured_levels_timeframe=Timeframe.D1,
            configured_entry_timeframe=Timeframe.M15,
        )
