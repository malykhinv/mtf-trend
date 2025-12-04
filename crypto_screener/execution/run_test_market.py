from collections import defaultdict
from datetime import timedelta

from crypto_screener.data.providers.coingecko import enrich_symbols_capitalization
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.mode import PlotPolicy
from crypto_screener.domain.models.setup import Buy
from crypto_screener.domain.models.symbol import FuturesSymbol, set_contexts
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_result import TradeResult
from crypto_screener.execution.run_test_symbol import run_test_bars
from crypto_screener.execution.test_result import evaluate_buy, log_test_summary
from crypto_screener.utils.history import calculate_limit, calculate_window
from crypto_screener.utils.logger import log
from crypto_screener.utils.time import utc_now


# region Private
def _filter_symbols(
        symbols: list[FuturesSymbol],
        volume_min: float | None,
        trades_min: int | None,
        listing_age_days_min: int | None,
) -> list[FuturesSymbol]:
    filtered = symbols

    if volume_min is not None:
        filtered = [symbol for symbol in filtered if (symbol.volume_usdt_24h or 0) >= volume_min]

    if trades_min is not None:
        filtered = [symbol for symbol in filtered if (symbol.trades_24h or 0) >= trades_min]

    if listing_age_days_min is not None and listing_age_days_min > 0:
        min_listing_time = utc_now() - timedelta(days=listing_age_days_min)
        filtered = [symbol for symbol in filtered if symbol.listing_time <= min_listing_time]

    return filtered


def _accumulate_history(
        exchange: Exchange,
        symbol: str,
        timeframe: Timeframe,
        history_months: int,
        batch_limit: int,
) -> list[Bar]:
    history_start = utc_now() - timedelta(days=history_months * 30)
    end = utc_now()
    bars: list[Bar] = []

    while True:
        batch = exchange.get_ohlcv(symbol=symbol, timeframe=timeframe, limit=batch_limit, end=end)
        if not batch:
            break

        earliest_batch_time = batch[0].time
        if bars and bars[0].time == earliest_batch_time:
            break

        bars = batch + bars

        if earliest_batch_time <= history_start:
            bars = [bar for bar in bars if bar.time >= history_start]
            break

        end = earliest_batch_time - timedelta(minutes=timeframe.minutes)

    return bars


# endregion

def run_test_market(
        exchange: Exchange,
        timeframes: list[Timeframe],
        limit: int,
        window: int,
        history_months: int,
        plot_policy: PlotPolicy,
        volume_24h_usdt_min: float,
        trades_24h_min: int,
        listing_age_days_min: int
) -> None:
    log.d("Запуск тестирования рынка.")

    if not timeframes:
        log.e("Не заданы таймфреймы.")
        return
    if limit <= 0 or window <= 0:
        log.e("Некорректные данные для количества свеч.")
        return
    if history_months <= 0:
        log.e("Некорректное значение длительности истории.")
        return

    symbols = exchange.get_futures_symbols()
    symbols = enrich_symbols_capitalization(symbols)
    symbols = set_contexts(symbols, listing_age_days_min)
    log.d(f"Получено {len(symbols)} символов до фильтрации.")

    symbols = _filter_symbols(
        symbols,
        volume_24h_usdt_min,
        trades_24h_min,
        listing_age_days_min,
    )
    log.d(f"Фильтры: объем ≥ {volume_24h_usdt_min}, сделки ≥ {trades_24h_min}, возраст ≥ {listing_age_days_min} дней")
    log.d(f"После фильтрации осталось {len(symbols)} символов.")

    trade_results: list[float] = []
    trade_outcomes: defaultdict[TradeResult, int] = defaultdict(int)

    for symbol in symbols:
        for timeframe in timeframes:
            batch_limit = calculate_limit(limit * 3, timeframe)
            try:
                bars = _accumulate_history(
                    exchange,
                    symbol.symbol,
                    timeframe,
                    history_months,
                    batch_limit,
                )
            except Exception as exception:
                log.e(f"{symbol.symbol} {timeframe.tf}: ошибка получения данных: {exception}")
                continue

            if not bars:
                log.e(f"{symbol.symbol} {timeframe.tf}: не удалось получить свечи.")
                continue

            timeframe_window = calculate_window(window, timeframe, len(bars))
            log.d(
                f"Проверка {symbol.symbol} (контекст {symbol.context.value}) на {timeframe.tf} "
                f"(history={history_months} мес., window={timeframe_window})"
            )

            if len(bars) < timeframe_window:
                log.e(
                    f"{symbol.symbol} {timeframe.tf}: "
                    f"для теста нужно минимум {timeframe_window} свечей, получено {len(bars)}."
                )
                continue

            for i in range(timeframe_window - 1, len(bars)):
                window_bars = bars[i - timeframe_window + 1:i + 1]
                future_bars = bars[i + 1:]
                setup = run_test_bars(
                    symbol=symbol.symbol,
                    timeframe=timeframe,
                    bars=window_bars,
                    plot_policy=plot_policy,
                    context=symbol.context,
                    subdir='test_market',
                    postmortem_bars=future_bars
                )

                if not setup or not isinstance(setup, Buy):
                    continue

                if not future_bars:
                    continue

                outcome = evaluate_buy(setup, future_bars)
                if not outcome:
                    continue

                trade_result, profit_pct = outcome
                trade_outcomes[trade_result] += 1
                trade_results.append(profit_pct)
                log.i(f"{symbol.symbol} {timeframe.tf}: {trade_result.value} ({profit_pct:+.2f}%)")

    log_test_summary(trade_outcomes, trade_results)

    log.d("Тест завершен.")
