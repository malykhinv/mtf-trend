from datetime import datetime
from typing import Optional

from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.mode import PlotPolicy
from crypto_screener.domain.models.setup import Setup
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.domain.swing_detector import add_swings
from crypto_screener.utils.logger import log
from crypto_screener.utils.plotter import plot


# region Private.
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
        timeframe: Timeframe
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
        support_swings=[]
    )
    log.d("График сохранен.")


# endregion

def run_test_bars(
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        plot_policy: PlotPolicy,
) -> None:
    if not bars:
        return
    setup = _detect_setup(bars, timeframe)
    match plot_policy:
        case PlotPolicy.ON_ANY:
            _plot(symbol, bars, timeframe)
        case PlotPolicy.ON_SETUP:
            if setup:
                _plot(symbol, bars, timeframe)


def run_test_symbol(
        exchange: Exchange,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        end: datetime,
        plot_policy: PlotPolicy
) -> None:
    bars = exchange.get_ohlcv(symbol, timeframe, limit, end)
    run_test_bars(symbol, timeframe, bars, plot_policy)
