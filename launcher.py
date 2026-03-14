"""Run predefined modes without typing the raw CLI command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from cli import commands
from config import AppConfig, load_config

MODE_FETCH_CACHE = "fetch-cache"
MODE_UPDATE_CACHE = "update-cache"
MODE_BACKTEST = "analyze-cache"
MODE_REPORT = "make-report"
MODE_STAGE1_REVIEW = "review-stage1"
MODE_STAGE2_REVIEW = "review-stage2"
MODE_QUALITY = "check-quality"
MODE_CLEAR_CACHE = "clear-cache"

MODE_LABELS: dict[str, str] = {
    MODE_FETCH_CACHE: "Cache fetch",
    MODE_UPDATE_CACHE: "Cache update",
    MODE_BACKTEST: "Analyze cache with strategy",
    MODE_REPORT: "Build report",
    MODE_STAGE1_REVIEW: "Review historical stage-1",
    MODE_STAGE2_REVIEW: "Review stage-2 balances",
    MODE_QUALITY: "Check cache quality",
    MODE_CLEAR_CACHE: "Clear cache",
}


def _to_bool(value: Any, *, fallback: bool | None = False) -> bool | None:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _force_single_thread_mode() -> None:
    single_thread_env = {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    for key, value in single_thread_env.items():
        os.environ[key] = value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="launcher", description="Run project modes without typing raw CLI commands")
    parser.add_argument("--env", default=".env", help="Path to env file")
    parser.add_argument("--mode", choices=tuple(MODE_LABELS.keys()), default=None, help="Single run mode")
    parser.add_argument("--config", default=None, help="JSON file with a batch of tasks")
    parser.add_argument("--top-n", type=int, default=None, help="Top symbols for fetch/update or limit after stage-1 filter")
    parser.add_argument("--min-volume-usd", type=float, default=None, help="Minimum 24h volume in USD")
    parser.add_argument("--days", type=int, default=30, help="Number of days for fetch/update")
    parser.add_argument("--end-timestamp-ms", type=int, default=None, help="Anchor end timestamp for the period (unix ms)")
    parser.add_argument("--ignore-coingecko", action="store_true", default=None, help="Skip CoinGecko in fetch/update symbol selection")
    parser.add_argument("--symbols", nargs="*", default=None, help="List of symbols, e.g. BTC/USDT ETH/USDT")
    parser.add_argument("--tf", default=None, help="Review timeframe for stage review modes")
    parser.add_argument("--levels-tf", default=None, help="Levels timeframe")
    parser.add_argument("--entry-tf", default=None, help="Entry timeframe")
    parser.add_argument("--strategy", choices=["bee_bite"], default=None, help="Only bee_bite strategy is available")
    parser.add_argument("--bee-bite-profile", choices=["A", "B", "C"], default=None, help="Bee bite profile")
    parser.add_argument("--bee-bite-grid", choices=["baseline", "expanded", "research"], default=None, help="Bee bite grid mode")
    parser.add_argument("--bee-bite-reclaim-mode", choices=["strict", "balanced", "aggressive"], default=None, help="Bee bite reclaim mode")
    parser.add_argument("--bee-bite-retest-mode", choices=["confirmation", "immediate"], default=None, help="Bee bite entry mode")
    parser.add_argument("--bee-bite-cooldown-hours", "--bee-bite-cooldown-bars", dest="bee_bite_cooldown_hours", type=int, default=None, help="Cooldown for bee_bite in hours")
    parser.add_argument("--bee-bite-max-age-range-hours", "--bee-bite-max-age-range", dest="bee_bite_max_age_range_hours", type=int, default=None, help="Max range age for bee_bite in hours")
    parser.add_argument("--output-dir", default=None, help="Directory for diagnostic files")
    parser.add_argument("--plot-limit", type=int, default=20, help="Maximum number of review plots")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of candles/events")
    parser.add_argument("--input", default=None, help="Input CSV for report")
    parser.add_argument("--output", default=None, help="Output JSON/CSV path")
    parser.add_argument("--plot", default=None, help="Save diagnostic files for the best combination (true/false)")
    parser.add_argument("--id", type=int, default=None, help="Combination ID for plot-from-results mode")
    parser.add_argument("--plot-from-results", action="store_true", help="Build diagnostics from results.csv without a full backtest")
    parser.add_argument("--results-input", default=None, help="Path to CSV with results for --plot-from-results")
    return parser


def _task_namespace(task: dict[str, Any], cli_args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        top_n=int(task["top_n"]) if "top_n" in task and task.get("top_n") is not None else cli_args.top_n,
        min_volume_usd=task.get("min_volume_usd", cli_args.min_volume_usd),
        days=int(task.get("days", cli_args.days)),
        end_timestamp_ms=task.get("end_timestamp_ms", cli_args.end_timestamp_ms),
        symbols=task.get("symbols", cli_args.symbols),
        tf=task.get("tf", cli_args.tf),
        levels_tf=task.get("levels_tf", cli_args.levels_tf),
        entry_tf=task.get("entry_tf", cli_args.entry_tf),
        strategy=task.get("strategy", cli_args.strategy),
        bee_bite_profile=task.get("bee_bite_profile", cli_args.bee_bite_profile),
        bee_bite_grid=task.get("bee_bite_grid", cli_args.bee_bite_grid),
        bee_bite_reclaim_mode=task.get("bee_bite_reclaim_mode", cli_args.bee_bite_reclaim_mode),
        bee_bite_retest_mode=task.get("bee_bite_retest_mode", cli_args.bee_bite_retest_mode),
        bee_bite_cooldown_hours=int(task["bee_bite_cooldown_hours"]) if "bee_bite_cooldown_hours" in task and task.get("bee_bite_cooldown_hours") is not None else (int(task["bee_bite_cooldown_bars"]) if "bee_bite_cooldown_bars" in task and task.get("bee_bite_cooldown_bars") is not None else cli_args.bee_bite_cooldown_hours),
        bee_bite_max_age_range_hours=int(task["bee_bite_max_age_range_hours"]) if "bee_bite_max_age_range_hours" in task and task.get("bee_bite_max_age_range_hours") is not None else (int(task["bee_bite_max_age_range"]) if "bee_bite_max_age_range" in task and task.get("bee_bite_max_age_range") is not None else cli_args.bee_bite_max_age_range_hours),
        output_dir=task.get("output_dir", cli_args.output_dir),
        plot_limit=int(task["plot_limit"]) if "plot_limit" in task and task.get("plot_limit") is not None else cli_args.plot_limit,
        limit=int(task["limit"]) if "limit" in task and task.get("limit") is not None else cli_args.limit,
        input=task.get("input", cli_args.input),
        output=task.get("output", cli_args.output),
        results_input=task.get("results_input", cli_args.results_input),
        plot=_to_bool(task.get("plot"), fallback=cli_args.plot) if "plot" in task else _to_bool(cli_args.plot, fallback=False),
        plot_from_results=_to_bool(task.get("plot_from_results"), fallback=cli_args.plot_from_results) if "plot_from_results" in task else cli_args.plot_from_results,
        id=int(task["id"]) if "id" in task and task.get("id") is not None else cli_args.id,
        ignore_coingecko=_to_bool(task.get("ignore_coingecko"), fallback=cli_args.ignore_coingecko) if "ignore_coingecko" in task else cli_args.ignore_coingecko,
    )


def _run_mode(config: AppConfig, mode: str, task_args: argparse.Namespace) -> int:
    handlers = {
        MODE_FETCH_CACHE: commands.fetch_data,
        MODE_UPDATE_CACHE: commands.update_cache,
        MODE_BACKTEST: commands.run_backtest,
        MODE_REPORT: commands.make_report,
        MODE_STAGE1_REVIEW: commands.review_stage1,
        MODE_STAGE2_REVIEW: commands.review_stage2,
        MODE_QUALITY: commands.check_quality,
        MODE_CLEAR_CACHE: commands.clear_cache,
    }
    return handlers[mode](config, task_args)


def _prompt_menu() -> str:
    print("Select run mode:")
    modes = list(MODE_LABELS.items())
    for index, (_, label) in enumerate(modes, start=1):
        print(f"  {index}. {label}")
    while True:
        raw = input("Enter mode number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(modes):
            return modes[int(raw) - 1][0]
        print("Invalid number. Try again.")


def _load_tasks(config_path: Path) -> dict[str, Any]:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _run_batch(config: AppConfig, cli_args: argparse.Namespace, payload: dict[str, Any]) -> int:
    tasks = payload.get("tasks", [])
    if not tasks:
        print("Config file has no tasks: key 'tasks' is empty.")
        return 1
    continue_on_error = bool(payload.get("continue_on_error", False))
    for index, task in enumerate(tasks, start=1):
        mode = str(task.get("mode", "")).strip()
        if mode not in MODE_LABELS:
            print(f"[{index}] Unknown mode '{mode}'")
            if continue_on_error:
                continue
            return 1
        print(f"[{index}] {MODE_LABELS[mode]}: start")
        task_args = _task_namespace(task, cli_args)
        code = _run_mode(config, mode, task_args)
        print(f"[{index}] {MODE_LABELS[mode]}: completed with code={code}")
        if code != 0 and not continue_on_error:
            return code
    return 0


def main() -> int:
    _force_single_thread_mode()
    parser = _build_parser()
    args = parser.parse_args()
    config_payload = None
    env_path = args.env
    if args.config:
        config_payload = _load_tasks(Path(args.config))
        env_path = str(config_payload.get("env_path", env_path))
    app_config = load_config(env_path)
    if config_payload:
        return _run_batch(app_config, args, config_payload)
    mode = args.mode or _prompt_menu()
    task_args = _task_namespace({}, args)
    return _run_mode(app_config, mode, task_args)


if __name__ == "__main__":
    raise SystemExit(main())
