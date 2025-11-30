from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.mode import PlotPolicy, TestData
from crypto_screener.domain.models.setup import Setup, Buy
from crypto_screener.domain.models.symbol import Context
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_result import TradeResult
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.execution.test_result import evaluate_buy, log_test_summary
from crypto_screener.utils.history import calculate_limit_grid
from crypto_screener.utils.logger import log
from crypto_screener.utils.plotter import plot, plot_postmortem


# region Private.
def _future_end(end: datetime, timeframe: Timeframe, limit: int) -> datetime:
    return end + timedelta(minutes=timeframe.minutes * limit)


def _get_future_bars(
        exchange: Exchange,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        end: datetime,
) -> list[Bar]:
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    future_end = _future_end(end, timeframe, limit)
    future_bars = exchange.get_ohlcv(symbol, timeframe, limit, future_end)
    return [bar for bar in future_bars if bar.time > end]


def _run_test_symbol(
        exchange: Exchange,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        end: datetime,
        plot_policy: PlotPolicy
) -> tuple[list[float], defaultdict[TradeResult, int]]:
    log.d(f"Проверка {symbol} на {timeframe.tf}")
    trade_results: list[float] = []
    trade_outcomes: defaultdict[TradeResult, int] = defaultdict(int)

    for timeframe_limit in calculate_limit_grid(limit, timeframe):
        try:
            bars = exchange.get_ohlcv(symbol, timeframe, timeframe_limit, end)
        except Exception as exception:
            log.e(f"{symbol} {timeframe.tf}: ошибка получения данных: {exception}")
            continue

        if not bars:
            log.e(f"{symbol} {timeframe.tf}: не удалось получить свечи.")
            continue

        future_bars = _get_future_bars(exchange, symbol, timeframe, timeframe_limit, end)
        if not future_bars:
            log.e(f"{symbol} {timeframe.tf}: не удалось получить будущие свечи после {end}.")
            continue

        setup = run_test_bars(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            plot_policy=plot_policy,
            context=Context.TEST,
            subdir='test_symbol',
            postmortem_bars=future_bars,
        )

        if not setup or not isinstance(setup, Buy):
            continue

        outcome = evaluate_buy(setup, future_bars)
        if not outcome:
            continue

        trade_result, profit_pct = outcome
        trade_outcomes[trade_result] += 1
        trade_results.append(profit_pct)
        log.i(f"{symbol} {timeframe.tf}: {trade_result.value} ({profit_pct:+.2f}%)")

    return trade_results, trade_outcomes


def _detect_setup(
        symbol: str,
        bars: list[Bar],
        timeframe: Timeframe,
        context: Context
) -> Setup:
    setup = detect_setup(symbol, bars, timeframe, context)
    if setup.is_filled:
        log.d(f"На {symbol} ({timeframe.tf}) обнаружен {setup.name.capitalize()}-сетап.")
    return setup


def _plot(
        setup: Setup,
        subdir: Optional[str] = None,
        postmortem_bars: Optional[list[Bar]] = None,
        detection_time: Optional[datetime] = None,
):
    if postmortem_bars and isinstance(setup, Buy):
        plot_postmortem(
            symbol=f"{setup.symbol} {setup.name.capitalize()} ",
            timeframe=setup.timeframe,
            bars=setup.bars,
            detection_time=detection_time,
            main_low_swing=setup.main_low_swing,
            main_high_swing=setup.main_high_swing,
            cascade_swings=setup.cascade_swings,
            resistance_swings=setup.resistance_swings,
            support_swings=setup.support_swings,
            subdir=subdir,
            setup_name=setup.name.capitalize(),
            postmortem_bars=postmortem_bars,
            trade_levels=setup.trade_levels,
        )
    else:
        plot(
            symbol=f"{setup.symbol} {setup.name.capitalize()} ",
            timeframe=setup.timeframe,
            bars=setup.bars,
            detection_time=detection_time,
            main_low_swing=setup.main_low_swing,
            main_high_swing=setup.main_high_swing,
            cascade_swings=setup.cascade_swings,
            resistance_swings=setup.resistance_swings,
            support_swings=setup.support_swings,
            subdir=subdir,
            setup_name=setup.name.capitalize(),
        )
    log.d(f"График {setup.symbol} сохранен.")


# endregion

def run_test_bars(
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        plot_policy: PlotPolicy,
        context: Context,
        subdir: Optional[str] = None,
        postmortem_bars: Optional[list[Bar]] = None,
) -> Optional[Setup]:
    if not bars:
        return None
    setup = _detect_setup(symbol, bars, timeframe, context)
    match plot_policy:
        case PlotPolicy.ON_ANY:
            _plot(setup, subdir, postmortem_bars, bars[-1].time)
        case PlotPolicy.ON_FILLED_SETUP:
            if setup.is_filled:
                _plot(setup, subdir, postmortem_bars, bars[-1].time)
    return setup


def run_test_symbols(
        exchange: Exchange,
        test_data: list[TestData],
        limit: int,
        plot_policy: PlotPolicy
) -> None:
    trade_results: list[float] = []
    trade_outcomes: defaultdict[TradeResult, int] = defaultdict(int)

    for data in test_data:
        symbol_results, symbol_outcomes = _run_test_symbol(
            exchange,
            data.symbol,
            data.timeframe,
            limit,
            data.end,
            plot_policy,
        )
        trade_results.extend(symbol_results)
        for result, count in symbol_outcomes.items():
            trade_outcomes[result] += count

    log_test_summary(trade_outcomes, trade_results)
    log.d("Тест завершен.")
