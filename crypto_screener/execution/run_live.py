from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from crypto_screener.domain.capture_state import CaptureState
from crypto_screener.domain.exchange import Exchange, FuturesSymbol
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.setup import Capture, Buy
from crypto_screener.domain.models.swing import Swing
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.utils.history import calculate_limit
from crypto_screener.utils.logger import log
from crypto_screener.utils.plotter import plot
from crypto_screener.utils.signals import handle_sig
from crypto_screener.utils.time import utc_now


# region Private.
def _fetch_filtered_symbols(
        exchange: Exchange,
        listing_period_days: int,
        volume_24h_new_usdt_min: int,
        volume_24h_old_usdt_min: int,
        trades_24h_min: int,
        trades_24h_btc_ratio_min: float
) -> list[FuturesSymbol]:
    symbols = exchange.get_futures_symbols()
    btc_trades_24h = next((symbol.trades_24h for symbol in symbols if symbol.symbol.startswith("BTC")), 0)
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
    filtered: list[FuturesSymbol] = []

    for symbol in symbols:
        listing_age_days = (utc_now() - symbol.listing_time).days
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


def _send_notification(
        notifier: Notifier,
        notification_type: NotificationType,
        message: str,
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        main_high_swing: Optional[Swing],
        cascade_swings: Optional[list[Swing]],
        resistance_swings: Optional[list[Swing]],
        support_swings: Optional[list[Swing]],
        subdir: Optional[str] = None
) -> None:
    try:
        image_path = plot(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            main_high_swing=main_high_swing,
            cascade_swings=cascade_swings,
            resistance_swings=resistance_swings,
            support_swings=support_swings,
            subdir=subdir
        )
        notifier.notify(notification_type, message, image_path)
    except Exception as exception:
        log.e(f"Ошибка при отправке уведомления: {exception}")


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

        active_symbols = [symbol for symbol in filtered_symbols
                          if not capture_state.symbol
                          or capture_state.symbol == symbol.symbol]
        for symbol in active_symbols:
            for timeframe in timeframes:
                timeframe_limit = calculate_limit(limit, timeframe)
                bars = exchange.get_ohlcv(symbol.symbol, timeframe, timeframe_limit)
                setup = detect_setup(symbol.symbol, bars, timeframe)
                match setup:
                    # Сетап не найден.
                    case None:
                        if capture_state.symbol == symbol.symbol:
                            capture_state.symbol = None
                        continue

                    # Найден базовый сетап.
                    case Capture(
                        main_high_swing=main_high_swing,
                        cascade_swings=cascade_swings,
                        resistance_swings=resistance_swings,
                        support_swings=support_swings,
                    ):
                        if capture_state.symbol is None:
                            capture_state.symbol = symbol.symbol
                        key = (symbol.symbol, timeframe, setup.name)
                        if key not in notified_once:
                            notified_once.add(key)
                            message = f"Включено слежение за {symbol.symbol} на {timeframe.tf}."
                            executor.submit(
                                _send_notification,
                                notifier=notifier,
                                notification_type=NotificationType.EVENT,
                                message=message,
                                symbol=symbol.symbol,
                                timeframe=timeframe,
                                bars=bars,
                                main_high_swing=main_high_swing,
                                cascade_swings=cascade_swings,
                                resistance_swings=resistance_swings,
                                support_swings=support_swings,
                                subdir='event'
                            )

                    # Найден торговый сетап.
                    case Buy(
                        main_high_swing=main_high_swing,
                        cascade_swings=cascade_swings,
                        resistance_swings=resistance_swings,
                        support_swings=support_swings,
                        take_profit_price=take_profit_price,
                        stop_loss_price=stop_loss_price,
                        partial_close_price=partial_close_price,
                        breakeven_price=breakeven_price
                    ):
                        message = f"Попытка открытия позиции в {symbol.symbol} на {timeframe.tf}."
                        log.i(message)
                        # TODO Открытие позиции на бирже.
                        executor.submit(
                            _send_notification,
                            notifier=notifier,
                            notification_type=NotificationType.ORDER,
                            message=message,
                            symbol=symbol.symbol,
                            timeframe=timeframe,
                            bars=bars,
                            main_high_swing=main_high_swing,
                            cascade_swings=cascade_swings,
                            resistance_swings=resistance_swings,
                            support_swings=support_swings,
                            subdir='order'
                        )
                        capture_state.symbol = None
