from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.mode import PlotPolicy
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.execution.run_test_symbol import run_test_symbol
from crypto_screener.utils.logger import log
from crypto_screener.utils.time import utc_now


def run_test_market(
        exchange: Exchange,
        timeframes: list[Timeframe],
        limit: int,
        window: int,
        plot_policy: PlotPolicy
) -> None:
    log.d("Запуск тестирования рынка.")

    if not timeframes:
        log.e("Не заданы таймфреймы.")
        return
    if limit <= 0 or window <= 0 or window > limit:
        log.e("Некорректные данные для количества свеч.")
        return

    symbols = exchange.get_futures_symbols()
    log.d(f"Получено {len(symbols)} символов.")

    for symbol in symbols:
        for timeframe in timeframes:
            log.e(f"Проверка {symbol.symbol} на {timeframe.tf}")
            try:
                bars = exchange.get_ohlcv(
                    symbol=symbol.symbol,
                    timeframe=timeframe,
                    limit=limit,
                    end=utc_now(),
                )
            except Exception as exception:
                log.e(f"{symbol.symbol} {timeframe.tf}: ошибка получения данных: {exception}")
                continue

            if not bars:
                log.e(f"{symbol.symbol} {timeframe.tf}: не удалось получить свечи.")
                continue

            if len(bars) < window:
                log.e(f"{symbol.symbol} {timeframe.tf}: для теста нужно минимум {window} свечей, получено {len(bars)}.")
                continue

            for i in range(window - 1, len(bars)):
                end = bars[i].time

                run_test_symbol(
                    exchange=exchange,
                    symbol=symbol.symbol,
                    timeframe=timeframe,
                    limit=window,
                    end=end,
                    plot_policy=plot_policy,
                )
