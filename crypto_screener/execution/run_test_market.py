from datetime import timedelta

from crypto_screener.domain.exchange import Exchange, FuturesSymbol
from crypto_screener.domain.models.mode import PlotPolicy
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.execution.run_test_symbol import run_test_bars
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
        filtered = [s for s in filtered if (s.volume_usdt_24h or 0) >= volume_min]

    if trades_min is not None:
        filtered = [s for s in filtered if (s.trades_24h or 0) >= trades_min]

    if listing_age_days_min is not None and listing_age_days_min > 0:
        min_listing_time = utc_now() - timedelta(days=listing_age_days_min)
        filtered = [s for s in filtered if s.listing_time <= min_listing_time]

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
        listing_age_days_min: int,
) -> None:
    log.d("Запуск тестирования рынка.")

    if not timeframes:
        log.e("Не заданы таймфреймы.")
        return
    if limit <= 0 or window <= 0 or window > limit:
        log.e("Некорректные данные для количества свеч.")
        return

    symbols = exchange.get_futures_symbols()
    log.d(f"Получено {len(symbols)} символов до фильтрации.")

    symbols = _filter_symbols(
        symbols,
        volume_24h_usdt_min,
        trades_24h_min,
        listing_age_days_min,
    )
    log.d(f"Фильтры: объем ≥ {volume_24h_usdt_min}, сделки ≥ {trades_24h_min}, возраст ≥ {listing_age_days_min} дней")
    log.d(f"После фильтрации осталось {len(symbols)} символов.")

    for symbol in symbols:
        for timeframe in timeframes:
            log.d(f"Проверка {symbol.symbol} на {timeframe.tf}")
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
                window_bars = bars[i - window + 1:i + 1]
                run_test_bars(
                    symbol=symbol.symbol,
                    timeframe=timeframe,
                    bars=window_bars,
                    plot_policy=plot_policy,
                )
    log.d("Тест завершен.")
