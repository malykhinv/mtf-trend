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

MODE_LABELS: dict[str, str] = {
    MODE_FETCH_CACHE: "Сбор кэша",
    MODE_UPDATE_CACHE: "Обновление кэша",
    MODE_BACKTEST: "Анализ кэша стратегией",
    MODE_REPORT: "Построение отчета",
    MODE_QUALITY: "Проверка качества кэша",
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
    parser.add_argument("--top-n", type=int, default=100, help="Количество топ монет для fetch/update")
    parser.add_argument("--min-volume-usd", type=float, default=None, help="Минимальный суточный объем в USD")
    parser.add_argument("--days", type=int, default=30, help="Число дней для fetch/update")
    parser.add_argument(
        "--ignore-coingecko",
        action="store_true",
        default=None,
        help="Не использовать CoinGecko при подборе символов для fetch/update",
    )
    parser.add_argument("--symbols", nargs="*", default=None, help="Список символов для backtest/check-quality")
    parser.add_argument("--input", default=None, help="Входной CSV для отчета")
    parser.add_argument("--output", default=None, help="Выходной путь JSON/CSV")
    return parser


def _task_namespace(task: dict[str, Any], cli_args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        top_n=int(task.get("top_n", cli_args.top_n)),
        min_volume_usd=task.get("min_volume_usd", cli_args.min_volume_usd),
        days=int(task.get("days", cli_args.days)),
        symbols=task.get("symbols", cli_args.symbols),
        input=task.get("input", cli_args.input),
        output=task.get("output", cli_args.output),
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
