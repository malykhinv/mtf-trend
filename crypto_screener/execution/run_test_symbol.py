from datetime import datetime
from typing import Optional

from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.mode import PlotPolicy, TestData
from crypto_screener.domain.models.setup import Setup
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.domain.swing_detector import add_swings
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
    bars = exchange.get_ohlcv(symbol, timeframe, limit, end)
    run_test_bars(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        plot_policy=plot_policy,
        subdir='test_symbol'
    )
    log.d("Тест завершен.")

def _detect_setup(
        bars: list[Bar],
        timeframe: Timeframe,
) -> Optional[Setup]:
    setup = detect_setup(bars, timeframe)
    if setup:
        log.d(f"Обнаружен {setup.name.capitalize()}-сетап.")
    return setup


def _plot(
        symbol: str,
        bars: list[Bar],
        timeframe: Timeframe,
        subdir: Optional[str] = None
):
    bars = add_swings(bars, timeframe)
    swings = [bar.swing for bar in bars if bar.swing]
    plot(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        main_high_swing=None,
        cascade_swings=swings,
        resistance_swings=[],
        support_swings=[],
        subdir=subdir
    )
    log.d("График сохранен.")


# endregion

def run_test_bars(
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        plot_policy: PlotPolicy,
        subdir: Optional[str] = None
) -> None:
    if not bars:
        return
    setup = _detect_setup(bars, timeframe)
    match plot_policy:
        case PlotPolicy.ON_ANY:
            _plot(symbol, bars, timeframe, subdir)
        case PlotPolicy.ON_SETUP:
            if setup:
                _plot(symbol, bars, timeframe, subdir)


def run_test_symbols(
        exchange: Exchange,
        test_data: list[TestData],
        limit: int,
        plot_policy: PlotPolicy
) -> None:
    for data in test_data:
        _run_test_symbol(exchange, data.symbol, data.timeframe, limit, data.end, plot_policy)
    log.d("Тест завершен.")