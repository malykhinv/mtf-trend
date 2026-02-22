"""Модуль проекта."""
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
MODE_QUALITY = "check-quality"
MODE_CLEAR_CACHE = "clear-cache"
MODE_PLOT_DAILY_LEVELS = "plot-daily-levels"
MODE_PLOT_RETESTS = "plot-retests"

MODE_LABELS: dict[str, str] = {
    MODE_FETCH_CACHE: "Сбор кэша",
    MODE_UPDATE_CACHE: "Обновление кэша",
    MODE_BACKTEST: "Анализ кэша стратегией",
    MODE_REPORT: "Построение отчета",
    MODE_QUALITY: "Проверка качества кэша",
    MODE_CLEAR_CACHE: "Очистка кэша",
    MODE_PLOT_DAILY_LEVELS: "Построение дневных уровней",
    MODE_PLOT_RETESTS: "Построение ретестов",
}


# region Приватные


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
    raise ValueError(f"Некорректное булево значение: {value}")


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
    parser = argparse.ArgumentParser(prog="launcher", description="Запуск режимов без прямого ввода CLI-команд")
    parser.add_argument("--env", default=".env", help="Путь к env-файлу")
    parser.add_argument("--mode", choices=tuple(MODE_LABELS.keys()), default=None, help="Одиночный режим запуска")
    parser.add_argument("--config", default=None, help="JSON-файл с пачкой задач")
    parser.add_argument("--top-n", type=int, default=None, help="Количество топ монет для fetch/update")
    parser.add_argument("--min-volume-usd", type=float, default=None, help="Минимальный суточный объем в USD")
    parser.add_argument("--days", type=int, default=30, help="Число дней для fetch/update")
    parser.add_argument("--end-timestamp-ms", type=int, default=None, help="Якорный timestamp окончания периода (unix ms)")
    parser.add_argument(
        "--ignore-coingecko",
        action="store_true",
        default=None,
        help="Не использовать CoinGecko при подборе символов для fetch/update",
    )
    parser.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    parser.add_argument(
        "--levels-tf",
        default=None,
        help="Таймфрейм уровней (например 1d). Приоритетнее LEVELS_TIMEFRAME из env",
    )
    parser.add_argument(
        "--entry-tf",
        default=None,
        help="Таймфрейм входов (например 15m). Приоритетнее ENTRY_TIMEFRAME из env",
    )
    parser.add_argument(
        "--strategy",
        choices=["retest", "breakout", "bee_bite"],
        default=None,
        help="Идентификатор стратегии. Приоритетнее STRATEGY_ID из env",
    )
    parser.add_argument(
        "--bee-bite-profile",
        choices=["A", "B", "C"],
        default=None,
        help="Профиль bee_bite (A/B/C). Используется как baseline.",
    )
    parser.add_argument(
        "--bee-bite-grid",
        choices=["baseline", "expanded"],
        default=None,
        help="Режим сетки bee_bite: baseline (узкий) или expanded (широкий).",
    )
    parser.add_argument(
        "--bee-bite-reclaim-mode",
        choices=["strict", "balanced", "aggressive"],
        default=None,
        help="Режим reclaim в bee_bite (валидируется против выбранного профиля).",
    )
    parser.add_argument(
        "--bee-bite-retest-mode",
        choices=["confirmation", "immediate"],
        default=None,
        help="Режим retest в bee_bite (валидируется против выбранного профиля).",
    )
    parser.add_argument(
        "--bee-bite-cooldown-bars",
        type=int,
        default=None,
        help="Cooldown (в барах) для профиля bee_bite.",
    )
    parser.add_argument(
        "--bee-bite-max-age-range",
        type=int,
        default=None,
        help="Максимальный возраст range (в барах) для профиля bee_bite.",
    )
    parser.add_argument("--output-dir", default=None, help="Директория сохранения изображений для plot-режимов")
    parser.add_argument("--limit", type=int, default=None, help="Ограничение числа свечей/событий для plot-режимов")
    parser.add_argument("--input", default=None, help="Входной CSV для отчета")
    parser.add_argument("--output", default=None, help="Выходной путь JSON/CSV")
    parser.add_argument("--plot", default=None, help="Строить графики сделок (true/false)")
    parser.add_argument(
        "--strategy",
        choices=("retest", "breakout", "bee_bite"),
        default=None,
        help="Идентификатор стратегии (breakout = alias для retest)",
    )
    parser.add_argument("--id", type=int, default=None, help="ID комбинации для режима plot-from-results")
    parser.add_argument(
        "--plot-from-results",
        action="store_true",
        help="Построить графики по параметрам из backtest_results.csv без полного бэктеста",
    )
    parser.add_argument(
        "--results-input",
        default=None,
        help="Путь к CSV с результатами для --plot-from-results",
    )
    return parser


def _task_namespace(task: dict[str, Any], cli_args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        top_n=(
            int(task["top_n"])
            if "top_n" in task and task.get("top_n") is not None
            else cli_args.top_n
        ),
        min_volume_usd=task.get("min_volume_usd", cli_args.min_volume_usd),
        days=int(task.get("days", cli_args.days)),
        end_timestamp_ms=task.get("end_timestamp_ms", cli_args.end_timestamp_ms),
        symbols=task.get("symbols", cli_args.symbols),
        levels_tf=task.get("levels_tf", cli_args.levels_tf),
        entry_tf=task.get("entry_tf", cli_args.entry_tf),
        strategy=task.get("strategy", cli_args.strategy),
        bee_bite_profile=task.get("bee_bite_profile", cli_args.bee_bite_profile),
        bee_bite_grid=task.get("bee_bite_grid", cli_args.bee_bite_grid),
        bee_bite_reclaim_mode=task.get("bee_bite_reclaim_mode", cli_args.bee_bite_reclaim_mode),
        bee_bite_retest_mode=task.get("bee_bite_retest_mode", cli_args.bee_bite_retest_mode),
        bee_bite_cooldown_bars=(
            int(task["bee_bite_cooldown_bars"])
            if "bee_bite_cooldown_bars" in task and task.get("bee_bite_cooldown_bars") is not None
            else cli_args.bee_bite_cooldown_bars
        ),
        bee_bite_max_age_range=(
            int(task["bee_bite_max_age_range"])
            if "bee_bite_max_age_range" in task and task.get("bee_bite_max_age_range") is not None
            else cli_args.bee_bite_max_age_range
        ),
        output_dir=task.get("output_dir", cli_args.output_dir),
        limit=(
            int(task["limit"])
            if "limit" in task and task.get("limit") is not None
            else cli_args.limit
        ),
        input=task.get("input", cli_args.input),
        output=task.get("output", cli_args.output),
        results_input=task.get("results_input", cli_args.results_input),
        plot=(
            _to_bool(task.get("plot"), fallback=cli_args.plot)
            if "plot" in task
            else _to_bool(cli_args.plot, fallback=False)
        ),
        plot_from_results=(
            _to_bool(task.get("plot_from_results"), fallback=cli_args.plot_from_results)
            if "plot_from_results" in task
            else cli_args.plot_from_results
        ),
        id=(
            int(task["id"])
            if "id" in task and task.get("id") is not None
            else cli_args.id
        ),
        ignore_coingecko=(
            _to_bool(task.get("ignore_coingecko"), fallback=cli_args.ignore_coingecko)
            if "ignore_coingecko" in task
            else cli_args.ignore_coingecko
        ),
    )


def _run_mode(config: AppConfig, mode: str, task_args: argparse.Namespace) -> int:
    handlers = {
        MODE_FETCH_CACHE: commands.fetch_data,
        MODE_UPDATE_CACHE: commands.update_cache,
        MODE_BACKTEST: commands.run_backtest,
        MODE_REPORT: commands.make_report,
        MODE_QUALITY: commands.check_quality,
        MODE_CLEAR_CACHE: commands.clear_cache,
        MODE_PLOT_DAILY_LEVELS: commands.plot_daily_levels,
        MODE_PLOT_RETESTS: commands.plot_retests,
    }
    return handlers[mode](config, task_args)


def _prompt_menu() -> str:
    print("Выберите режим запуска:")
    modes = list(MODE_LABELS.items())
    for index, (_, label) in enumerate(modes, start=1):
        print(f"  {index}. {label}")

    while True:
        raw = input("Введите номер режима: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(modes):
            return modes[int(raw) - 1][0]
        print("Некорректный номер. Попробуйте снова.")


def _load_tasks(config_path: Path) -> dict[str, Any]:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _run_batch(config: AppConfig, cli_args: argparse.Namespace, payload: dict[str, Any]) -> int:
    tasks = payload.get("tasks", [])
    if not tasks:
        print("В config-файле нет задач: ключ tasks пуст.")
        return 1

    continue_on_error = bool(payload.get("continue_on_error", False))

    for index, task in enumerate(tasks, start=1):
        mode = str(task.get("mode", "")).strip()
        if mode not in MODE_LABELS:
            print(f"[{index}] Неизвестный режим '{mode}'")
            if continue_on_error:
                continue
            return 1

        print(f"[{index}] {MODE_LABELS[mode]}: старт")
        task_args = _task_namespace(task, cli_args)
        code = _run_mode(config, mode, task_args)
        print(f"[{index}] {MODE_LABELS[mode]}: завершено с code={code}")

        if code != 0 and not continue_on_error:
            return code

    return 0


# endregion Приватные
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
