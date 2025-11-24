from collections import defaultdict
from datetime import timedelta

from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.mode import PlotPolicy
from crypto_screener.domain.models.setup import Buy
from crypto_screener.domain.models.symbol import FuturesSymbol, set_contexts
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_result import TradeResult
from crypto_screener.execution.run_test_symbol import run_test_bars
from crypto_screener.execution.test_result import evaluate_buy, log_test_summary
from crypto_screener.utils.history import calculate_limit_grid, calculate_window
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


# endregion

def run_test_market(
        exchange: Exchange,
        timeframes: list[Timeframe],
        limit: int,
        window: int,
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

    symbols = exchange.get_futures_symbols()
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
            for timeframe_limit in calculate_limit_grid(limit, timeframe):
                timeframe_window = calculate_window(window, timeframe, timeframe_limit)
                log.d(f"Проверка {symbol.symbol} (контекст {symbol.context.value}) на {timeframe.tf} "
                      f"(limit={timeframe_limit}, window={timeframe_window})")
                try:
                    bars = exchange.get_ohlcv(
                        symbol=symbol.symbol,
                        timeframe=timeframe,
                        limit=timeframe_limit,
                        end=utc_now(),
                    )
                except Exception as exception:
                    log.e(f"{symbol.symbol} {timeframe.tf}: ошибка получения данных: {exception}")
                    continue

                if not bars:
                    log.e(f"{symbol.symbol} {timeframe.tf}: не удалось получить свечи.")
                    continue

                if len(bars) < timeframe_window:
                    log.e(f"{symbol.symbol} {timeframe.tf}: "
                          f"для теста нужно минимум {timeframe_window} свечей, получено {len(bars)}.")
                    continue

                for i in range(timeframe_window - 1, len(bars)):
                    window_bars = bars[i - timeframe_window + 1:i + 1]
                    setup = run_test_bars(
                        symbol=symbol.symbol,
                        timeframe=timeframe,
                        bars=window_bars,
                        plot_policy=plot_policy,
                        context=symbol.context,
                        subdir='test_market'
                    )

                    if not setup or not isinstance(setup, Buy):
                        continue

                    future_bars = bars[i + 1:]
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
