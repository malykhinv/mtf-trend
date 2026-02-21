"""Модуль проекта."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from cli import commands
from config import AppConfig
from constants import DEFAULT_FETCH_DAYS, DEFAULT_MIN_VOLUME_USD, DEFAULT_QUALITY_REPORT_OUTPUT_FILE, \
    DEFAULT_UPDATE_DAYS

Handler = Callable[[AppConfig, argparse.Namespace], int]


def positive_int(value: str, argument_name: str = "value") -> int:
    """Преобразует строку в положительное целое число."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{argument_name} must be > 0") from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{argument_name} must be > 0")
    return parsed


def _positive_int_for(argument_name: str) -> Callable[[str], int]:
    """Возвращает валидатор положительного целого для конкретного аргумента."""

    def _validator(value: str) -> int:
        return positive_int(value, argument_name=argument_name)

    return _validator


def build_parser() -> argparse.ArgumentParser:
    """Собирает и возвращает парсер аргументов CLI."""
    parser = argparse.ArgumentParser(prog="mtf-trend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data", help="Загрузка данных с бирж и CoinGecko")
    fetch.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    fetch.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_FETCH_DAYS)
    fetch.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    fetch.add_argument("--end-timestamp-ms", type=int, default=None, help="Якорный timestamp окончания периода (unix ms)")
    fetch.add_argument(
        "--ignore-coingecko",
        action="store_true",
        default=None,
        help="Не использовать CoinGecko при подборе символов",
    )

    update = subparsers.add_parser("update-cache", help="Инкрементальное обновление кэша")
    update.add_argument("--top-n", type=_positive_int_for("--top-n"), default=None)
    update.add_argument("--days", type=_positive_int_for("--days"), default=DEFAULT_UPDATE_DAYS)
    update.add_argument("--min-volume-usd", type=float, default=DEFAULT_MIN_VOLUME_USD)
    update.add_argument("--end-timestamp-ms", type=int, default=None, help="Якорный timestamp окончания периода (unix ms)")
    update.add_argument(
        "--ignore-coingecko",
        action="store_true",
        default=None,
        help="Не использовать CoinGecko при подборе символов",
    )

    run_bt = subparsers.add_parser("run-backtest", help="Запуск бектеста по данным в кэше")
    run_bt.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    run_bt.add_argument(
        "--top-n",
        type=_positive_int_for("--top-n"),
        default=None,
        help="Количество символов для отбора по среднему объёму старшего ТФ",
    )
    run_bt.add_argument(
        "--levels-tf",
        default=None,
        help="Таймфрейм уровней (например 1d). Приоритетнее LEVELS_TIMEFRAME из env",
    )
    run_bt.add_argument(
        "--entry-tf",
        default=None,
        help="Таймфрейм входов (например 15m). Приоритетнее ENTRY_TIMEFRAME из env",
    )
    run_bt.add_argument(
        "--strategy",
        choices=["breakout", "bee_bite"],
        default=None,
        help="Идентификатор стратегии. Приоритетнее STRATEGY_ID из env",
    )
    run_bt.add_argument("--plot", default=False, help="Строить графики сделок (true/false)")
    run_bt.add_argument(
        "--plot-from-results",
        action="store_true",
        help="Построить графики по параметрам из backtest_results.csv без полного бэктеста",
    )
    run_bt.add_argument(
        "--results-input",
        default=None,
        help="Путь к CSV с результатами для --plot-from-results",
    )

    report = subparsers.add_parser("make-report", help="Сформировать JSON-отчет по результатам бектеста")
    report.add_argument("--input", default=None, help="Путь к CSV с результатами")
    report.add_argument("--output", default=None, help="Путь к JSON отчету")

    quality = subparsers.add_parser("check-quality", help="Проверка качества кэша")
    quality.add_argument("--symbols", nargs="*", default=None, help="Список символов, например BTC/USDT ETH/USDT")
    quality.add_argument(
        "--output",
        default=None,
        help=(
            f"Путь к отчету качества (.json или .csv). По умолчанию: <results_dir>/{DEFAULT_QUALITY_REPORT_OUTPUT_FILE}"
        ),
    )

    subparsers.add_parser("clear-cache", help="Полная очистка директории кэша")


    plot_daily_levels = subparsers.add_parser(
        "plot-daily-levels",
        help="Построить графики с дневными уровнями",
    )
    plot_daily_levels.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Список символов, например BTC/USDT ETH/USDT",
    )
    plot_daily_levels.add_argument("--levels-tf", default="1d", help="Таймфрейм уровней")
    plot_daily_levels.add_argument("--entry-tf", default="15m", help="Таймфрейм входов")
    plot_daily_levels.add_argument("--output-dir", default=None, help="Директория для сохранения изображений")
    plot_daily_levels.add_argument("--limit", type=int, default=None, help="Ограничение числа свечей")

    plot_retests = subparsers.add_parser(
        "plot-retests",
        help="Построить графики ретестов уровней",
    )
    plot_retests.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Список символов, например BTC/USDT ETH/USDT",
    )
    plot_retests.add_argument("--levels-tf", default="1d", help="Таймфрейм уровней")
    plot_retests.add_argument("--entry-tf", default="15m", help="Таймфрейм входов")
    plot_retests.add_argument("--output-dir", default=None, help="Директория для сохранения изображений")
    plot_retests.add_argument("--limit", type=int, default=None, help="Ограничение числа событий")

    return parser


def resolve_handler(command_name: str) -> Handler:
    """Находит обработчик команды по разобранным аргументам."""
    handlers: dict[str, Handler] = {
        "fetch-data": commands.fetch_data,
        "update-cache": commands.update_cache,
        "run-backtest": commands.run_backtest,
        "make-report": commands.make_report,
        "check-quality": commands.check_quality,
        "clear-cache": commands.clear_cache,
        "plot-daily-levels": commands.plot_daily_levels,
        "plot-retests": commands.plot_retests,
    }
    return handlers[command_name]
