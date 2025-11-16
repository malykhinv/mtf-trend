from concurrent.futures import ThreadPoolExecutor

from crypto_screener.domain.capture_state import CaptureState
from crypto_screener.domain.exchange import Exchange, FuturesSymbol
from crypto_screener.domain.models.setup import Capture, Buy
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.utils.logger import log
from crypto_screener.utils.signals import handle_sig


# region Private
def _fetch_filtered_symbols(
        exchange: Exchange,
        listing_period_days: int,
        volume_24h_new_usdt_min: int,
        volume_24h_old_usdt_min: int,
        trades_24h_min: int,
        trades_24h_btc_ratio_min: float
) -> list[FuturesSymbol]:
    symbols = exchange.get_futures_symbols()
    btc_trades_24h = next((s.trades_24h for s in symbols if s.symbol.startswith("BTC")), 0)
    filtered_symbols = _filter_symbols(
        symbols,
        listing_period_days,
        volume_24h_new_usdt_min,
        volume_24h_old_usdt_min,
        trades_24h_min,
        trades_24h_btc_ratio_min,
        btc_trades_24h
    )
    log.i(f"Отобрано {len(filtered_symbols)} символов из {len(symbols)}.")
    return filtered_symbols


def _filter_symbols(
        symbols: list[FuturesSymbol],
        listing_period_days: int,
        volume_24h_new_usdt_min: int,
        volume_24h_old_usdt_min: int,
        trades_24h_min: int,
        trades_24h_btc_ratio_min: float,
        btc_trades_24h: int
) -> list[FuturesSymbol]:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    filtered: list[FuturesSymbol] = []

    for symbol in symbols:
        listing_age_days = (now - symbol.listing_ts.astimezone(timezone.utc)).days
        if listing_age_days <= listing_period_days:
            if symbol.volume_usdt_24h < volume_24h_new_usdt_min:
                continue
            filtered.append(symbol)
        else:
            if symbol.volume_usdt_24h < volume_24h_old_usdt_min:
                continue
            if symbol.trades_24h < min(trades_24h_min, int(trades_24h_btc_ratio_min * btc_trades_24h)):
                continue
            filtered.append(symbol)
    return filtered


# endregion

def run_live(
        exchange: Exchange,
        notifier: Notifier,
        timeframes: list[Timeframe],
        limit: int,
        listing_period_days: int,
        volume_24h_new_usdt_min: int,
        volume_24h_old_usdt_min: int,
        trades_24h_min: int,
        trades_24h_btc_ratio_min: float
) -> None:
    log.d("Запуск в живом режиме.")

    if not timeframes:
        log.e("Не заданы таймфреймы.")
        return

    filtered_symbols = _fetch_filtered_symbols(
        exchange,
        listing_period_days,
        volume_24h_new_usdt_min,
        volume_24h_old_usdt_min,
        trades_24h_min,
        trades_24h_btc_ratio_min
    )
    if not filtered_symbols:
        log.e("Не хватает символов для начала анализа.")
        return

    capture_state = CaptureState()
    executor = ThreadPoolExecutor(max_workers=2)
    handle_sig(executor)
    notified_once: set[tuple[str, Timeframe, str]] = set()

    while True:
        if not capture_state.symbol:
            log.d("Запуск цикла анализа отобранных монет.")

        active_symbols = [s for s in filtered_symbols if not capture_state.symbol or capture_state.symbol == s.symbol]
        for symbol in active_symbols:
            for timeframe in timeframes:
                bars = exchange.get_ohlcv(symbol.symbol, timeframe, limit)
                setup = detect_setup(bars)
                match setup:
                    # Сетап не найден.
                    case None:
                        if capture_state.symbol == symbol.symbol:
                            capture_state.symbol = None
                        continue

                    # Найден базовый сетап.
                    case Capture():
                        if capture_state.symbol is None:
                            capture_state.symbol = symbol.symbol
                        key = (symbol.symbol, timeframe, setup.name)
                        if key not in notified_once:
                            notified_once.add(key)
                            message = f"Включено слежение за {symbol.symbol} на {timeframe.tf}."
                            # TODO Создание изображения для уведомления.
                            executor.submit(notifier.notify, NotificationType.EVENT, message)

                    # Найден торговый сетап.
                    case Buy(
                        take_profit_price=take_profit_price,
                        stop_loss_price=stop_loss_price,
                        partial_close_price=partial_close_price,
                        breakeven_price=breakeven_price
                    ):
                        message = f"Попытка открытия позиции в {symbol.symbol} на {timeframe.tf}."
                        log.i(message)
                        # TODO Открытие позиции на бирже.
                        # TODO Создание изображения для уведомления.
                        executor.submit(notifier.notify, NotificationType.ORDER, message)
                        capture_state.symbol = None
