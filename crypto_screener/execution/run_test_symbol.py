from datetime import datetime
from typing import Optional

from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.mode import PlotPolicy, TestData
from crypto_screener.domain.models.symbol import Context
from crypto_screener.domain.models.setup import Setup
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.utils.history import calculate_limit_grid
from crypto_screener.utils.logger import log
from crypto_screener.utils.plotter import plot


# region Private.
def _run_test_symbol(
        exchange: Exchange,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        end: datetime,
        plot_policy: PlotPolicy
) -> None:
    log.d(f"Проверка {symbol} на {timeframe.tf}")
    for timeframe_limit in calculate_limit_grid(limit, timeframe):
        bars = exchange.get_ohlcv(symbol, timeframe, timeframe_limit, end)
        run_test_bars(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            plot_policy=plot_policy,
            context=Context.A,  # Для упрощения тестирования
            subdir='test_symbol'
        )
    log.d("Тест завершен.")

def _detect_setup(
        symbol: str,
        bars: list[Bar],
        timeframe: Timeframe,
        context: Context
) -> Setup:
    setup = detect_setup(symbol, bars, timeframe, context)
    if setup.is_filled:
        log.d(f"Обнаружен {setup.name.capitalize()}-сетап.")
    return setup


def _plot(
        setup: Setup,
        subdir: Optional[str] = None
):
    plot(
        symbol=setup.symbol,
        timeframe=setup.timeframe,
        bars=setup.bars,
        main_high_swing=setup.main_high_swing,
        cascade_swings=setup.cascade_swings,
        resistance_swings=setup.resistance_swings,
        support_swings=setup.support_swings,
        subdir=subdir
    )
    log.d("График сохранен.")


# endregion

def run_test_bars(
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        plot_policy: PlotPolicy,
        context: Context,
        subdir: Optional[str] = None
) -> None:
    if not bars:
        return
    setup = _detect_setup(symbol, bars, timeframe, context)
    match plot_policy:
        case PlotPolicy.ON_ANY:
            _plot(setup, subdir)
        case PlotPolicy.ON_FILLED_SETUP:
            if setup.is_filled:
                _plot(setup, subdir)


def run_test_symbols(
        exchange: Exchange,
        test_data: list[TestData],
        limit: int,
        plot_policy: PlotPolicy
) -> None:
    for data in test_data:
        _run_test_symbol(exchange, data.symbol, data.timeframe, limit, data.end, plot_policy)
    log.d("Тест завершен.")