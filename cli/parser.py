"""CLI parser."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from cli import commands
from config import AppConfig
from constants import (
    DEFAULT_FETCH_DAYS,
    DEFAULT_MIN_VOLUME_USD,
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
    DEFAULT_UPDATE_DAYS,
)

Handler = Callable[[AppConfig, argparse.Namespace], int]


def positive_int(value: str, argument_name: str = "value") -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{argument_name} must be > 0") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{argument_name} must be > 0")
    return parsed


def _positive_int_for(argument_name: str) -> Callable[[str], int]:
    def _validator(value: str) -> int:
        return positive_int(value, argument_name=argument_name)

    return _validator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtf-trend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data", help="Загрузка данных с бирж и CoinGecko")
    fetch.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    fetch.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_FETCH_DAYS)
    fetch.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    fetch.add_argument("--end-timestamp-ms", type=int, default=None, help="Якорный timestamp окончания периода (unix ms)")
    fetch.add_argument("--ignore-coingecko", action="store_true", default=None, help="Не использовать CoinGecko при подборе символов")

    update = subparsers.add_parser("update-cache", help="Инкрементальное обновление кэша")
    update.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    update.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_UPDATE_DAYS)
    update.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    update.add_argument("--end-timestamp-ms", type=int, default=None, help="Якорный timestamp окончания периода (unix ms)")
    update.add_argument("--ignore-coingecko", action="store_true", default=None, help="Не использовать CoinGecko при подборе символов")

    run_bt = subparsers.add_parser("run-backtest", help="Запуск бэктеста по данным в кэше")
    run_bt.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    run_bt.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None, help="Количество символов после stage-1 фильтра bee_bite")
    run_bt.add_argument("--levels-tf", default=None, help="Таймфрейм уровней (например 1d). Приоритетнее LEVELS_TIMEFRAME из env")
    run_bt.add_argument("--entry-tf", default=None, help="Таймфрейм входов (например 15m). Приоритетнее ENTRY_TIMEFRAME из env")
    run_bt.add_argument("--strategy", choices=["bee_bite"], default=None, help="В проекте оставлена только стратегия bee_bite")
    run_bt.add_argument("--bee-bite-grid", choices=["baseline", "expanded", "research"], default=None, help="Режим сетки bee_bite")
    run_bt.add_argument("--bee-bite-reclaim-mode", choices=["strict", "balanced", "aggressive"], default=None, help="Режим reclaim в bee_bite")
    run_bt.add_argument("--bee-bite-retest-mode", choices=["confirmation", "immediate"], default=None, help="Режим входа в bee_bite")
    run_bt.add_argument("--bee-bite-cooldown-hours", "--bee-bite-cooldown-bars", dest="bee_bite_cooldown_hours", type=_positive_int_for("--bee-bite-cooldown-hours"), default=None, help="Cooldown для bee_bite в часах")
    run_bt.add_argument("--bee-bite-max-age-range-hours", "--bee-bite-max-age-range", dest="bee_bite_max_age_range_hours", type=_positive_int_for("--bee-bite-max-age-range-hours"), default=None, help="Максимальный возраст range для bee_bite в часах")
    run_bt.add_argument("--plot", default=False, help="Сохранять диагностические файлы по лучшей комбинации (true/false)")
    run_bt.add_argument("--plot-from-results", action="store_true", help="Построить диагностику по параметрам из results.csv без полного бэктеста")
    run_bt.add_argument("--results-input", default=None, help="Путь к CSV с результатами для --plot-from-results")
    run_bt.add_argument("--id", type=_positive_int_for("--id"), default=None, help="ID комбинации в CSV")

    report = subparsers.add_parser("make-report", help="Сформировать JSON-отчёт по результатам бэктеста")
    report.add_argument("--input", default=None, help="Путь к CSV с результатами")
    report.add_argument("--output", default=None, help="Путь к JSON отчёту")

    stage1 = subparsers.add_parser("review-stage1", help="Найти historical stage-1 события bee_bite и сохранить png-графики")
    stage1.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    stage1.add_argument("--plot-limit", type=_positive_int_for("--plot-limit"), default=20, help="Максимум png-графиков по последним stage-1 событиям")
    stage1.add_argument("--output", default=None, help="Путь к CSV с detected stage-1 событиями")

    quality = subparsers.add_parser("check-quality", help="Проверка качества кэша")
    quality.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    quality.add_argument("--output", default=None, help=f"Путь к отчёту качества (.json или .csv). По умолчанию: <results_dir>/{DEFAULT_QUALITY_REPORT_OUTPUT_FILE}")

    subparsers.add_parser("clear-cache", help="Полная очистка директории кэша")
    return parser


def resolve_handler(command_name: str) -> Handler:
    handlers: dict[str, Handler] = {
        "fetch-data": commands.fetch_data,
        "update-cache": commands.update_cache,
        "run-backtest": commands.run_backtest,
        "make-report": commands.make_report,
        "review-stage1": commands.review_stage1,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
    }
    return handlers[command_name]
