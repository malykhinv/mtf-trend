from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.mode import PlotPolicy, TestData
from crypto_screener.domain.models.setup import Setup, Trade
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_result import TradeResult
from crypto_screener.domain.strategies.strategy import Strategy
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
        last_bar_time: datetime,
) -> list[Bar]:
    if last_bar_time.tzinfo is None:
        last_bar_time = last_bar_time.replace(tzinfo=timezone.utc)
    future_end = _future_end(last_bar_time, timeframe, limit)
    future_bars = exchange.get_ohlcv(symbol, timeframe, limit, future_end)
    return [bar for bar in future_bars if bar.time > last_bar_time]


def _run_test_symbol(
        strategy: Strategy,
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

        last_bar_time = bars[-1].time
        future_bars = _get_future_bars(exchange, symbol, timeframe, timeframe_limit, last_bar_time)
        if not future_bars:
            log.e(f"{symbol} {timeframe.tf}: не удалось получить будущие свечи после {last_bar_time}.")
            continue

        setup = run_test_bars(
            strategy,
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            plot_policy=plot_policy,
            context=Context.TEST,
            subdir='test_symbol',
            postmortem_bars=future_bars,
            detection_time=last_bar_time,
        )

        if not setup or not isinstance(setup, Trade):
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
        strategy: Strategy,
        symbol: str,
        bars: list[Bar],
        timeframe: Timeframe,
        context: Context,
) -> Setup:
    setup = strategy.detect_setup(symbol, bars, timeframe, context)
    if setup.is_filled:
        log.d(f"На {symbol} ({timeframe.tf}) обнаружен {setup.name.capitalize()}-сетап.")
    return setup


def _plot(
        setup: Setup,
        subdir: Optional[str] = None,
        postmortem_bars: Optional[list[Bar]] = None,
        detection_time: Optional[datetime] = None,
        *,
        context: Context,
):
    if postmortem_bars and isinstance(setup, Trade):
        output_path = plot_postmortem(
            setup=setup,
            detection_time=detection_time,
            subdir=subdir,
            postmortem_bars=postmortem_bars,
            context=context,
        )
    else:
        output_path = plot(
            setup=setup,
            detection_time=detection_time,
            subdir=subdir,
            context=context,
        )
    log.d(f"График {setup.data.symbol} сохранен (контекст {context.value}) в {output_path}".strip())


# endregion

def run_test_bars(
        strategy: Strategy,
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        plot_policy: PlotPolicy,
        context: Context,
        subdir: Optional[str] = None,
        postmortem_bars: Optional[list[Bar]] = None,
        detection_time: Optional[datetime] = None,
) -> Optional[Setup]:
    if not bars:
        return None
    detection_time = detection_time or bars[-1].time
    setup = _detect_setup(strategy, symbol, bars, timeframe, context)
    match plot_policy:
        case PlotPolicy.ON_ANY:
            _plot(setup, subdir, postmortem_bars, detection_time, context=context)
        case PlotPolicy.ON_FILLED_SETUP:
            if setup.is_filled:
                _plot(setup, subdir, postmortem_bars, detection_time, context=context)
        case PlotPolicy.ON_TRADE_SETUP:
            if setup.is_trade:
                _plot(setup, subdir, postmortem_bars, detection_time, context=context)
    return setup


def run_test_symbols(
        strategies: list[Strategy],
        exchange: Exchange,
        test_data: list[TestData],
        limit: int,
        plot_policy: PlotPolicy
) -> None:
    trade_results: list[float] = []
    trade_outcomes: defaultdict[TradeResult, int] = defaultdict(int)

    for strategy in strategies:
        for data in test_data:
            symbol_results, symbol_outcomes = _run_test_symbol(
                strategy,
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
