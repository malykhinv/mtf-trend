import argparse

import launcher
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


def test_resolve_fetch_timeframes_preserves_user_order() -> None:
    args = argparse.Namespace(timeframes=["1m", "5m", "3m"])

    resolved = commands._resolve_fetch_timeframes(args, fallback=(Timeframe.M15, Timeframe.M5))

    assert resolved == (Timeframe.M1, Timeframe.M5, Timeframe.M3)


def test_launcher_task_namespace_parses_false_tf_all_from_batch_payload() -> None:
    cli_args = launcher._build_parser().parse_args(["--mode", launcher.MODE_PPA_RESEARCH])

    task_args = launcher._task_namespace({"tf_all": "false"}, cli_args)

    assert task_args.tf_all is False
