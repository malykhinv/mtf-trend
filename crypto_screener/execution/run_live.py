from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from crypto_screener.config.config import AppConfig as cfg
from crypto_screener.data.notifiers.telegram import TRADE_CALLBACK_PREFIX
from crypto_screener.data.providers.coingecko import enrich_symbols_capitalization
from crypto_screener.domain.capture_state import CaptureState
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Capture, Buy, Unfilled
from crypto_screener.domain.models.swing import Swing
from crypto_screener.domain.models.symbol import FuturesSymbol, set_contexts
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.notifier import Keyboard, Notifier, NotificationType
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.execution.trade_permission_service import TradePermissionService
from crypto_screener.utils.history import calculate_limit_grid
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
    symbols = enrich_symbols_capitalization(symbols)
    symbols = set_contexts(symbols, listing_period_days)
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
        main_low_swing: Optional[Swing],
        main_high_swing: Optional[Swing],
        cascade_swings: Optional[list[Swing]],
        resistance_swings: Optional[list[Swing]],
        support_swings: Optional[list[Swing]],
        subdir: Optional[str] = None,
        context: Optional[Context] = None,
        keyboard: Optional[Keyboard] = None,
) -> Optional[str]:
    try:
        image_path = plot(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            main_low_swing=main_low_swing,
            main_high_swing=main_high_swing,
            cascade_swings=cascade_swings,
            resistance_swings=resistance_swings,
            support_swings=support_swings,
            subdir=subdir,
            context=context,
        )
        return notifier.notify(
            notification_type,
            message,
            image_path,
            keyboard=keyboard,
            context=context,
        )
    except Exception as exception:
        log.e(f"Ошибка при отправке уведомления: {exception}")
    return None


def _edit_notification(
        notifier: Notifier,
        notification_type: NotificationType,
        message_id: str,
        message: str,
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        main_low_swing: Optional[Swing],
        main_high_swing: Optional[Swing],
        cascade_swings: Optional[list[Swing]],
        resistance_swings: Optional[list[Swing]],
        support_swings: Optional[list[Swing]],
        subdir: Optional[str] = None,
        context: Optional[Context] = None,
        keyboard: Optional[Keyboard] = None,
) -> Optional[str]:
    try:
        image_path = plot(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            main_low_swing=main_low_swing,
            main_high_swing=main_high_swing,
            cascade_swings=cascade_swings,
            resistance_swings=resistance_swings,
            support_swings=support_swings,
            subdir=subdir,
            context=context,
        )
        return notifier.edit_message(
            notification_type=notification_type,
            message_id=message_id,
            message=message,
            image_path=image_path,
            keyboard=keyboard,
            context=context,
        )
    except Exception as exception:
        log.e(f"Ошибка при редактировании уведомления: {exception}")
    return None


def _build_trade_keyboard(symbol: str, timeframe: Timeframe, context: Context) -> Keyboard:
    trade_text = cfg.TRADE_KEYBOARD[0][0][0] if cfg.TRADE_KEYBOARD else "Торговать"
    callback_data = f"{TRADE_CALLBACK_PREFIX}:{symbol}:{timeframe.tf}:{context.value}"
    return [[(trade_text, callback_data)]]


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
    trade_permission_service = TradePermissionService()
    notifier.start_callback_handler(trade_permission_service)
    executor = ThreadPoolExecutor(max_workers=2)
    handle_sig(executor)
    notified_once: set[tuple[str, Timeframe, str]] = set()

    while True:
        now = utc_now()
        expired_captures = [
            (symbol_timeframe, active_capture)
            for symbol_timeframe, active_capture in capture_state.captures.items()
            if now >= active_capture.deadline
        ]
        for (symbol_name, timeframe), active_capture in expired_captures:
            capture_state.remove_capture(symbol_name, timeframe)
            notifier.remove_button(active_capture.message_id)
            trade_permission_service.clear_allowance(symbol_name, timeframe)
            log.i(f"Истек срок слежения за {symbol_name} на {timeframe.tf}, кнопка удалена.")
            notified_once.discard((symbol_name, timeframe, "Capture"))
            if capture_state.symbol == symbol_name and not capture_state.has_symbol_capture(symbol_name):
                capture_state.symbol = None

        if not capture_state.symbol:
            log.d("Запуск цикла анализа отобранных монет.")

        active_symbols = [symbol for symbol in filtered_symbols
                          if not capture_state.symbol
                          or capture_state.symbol == symbol.symbol]
        for symbol in active_symbols:
            for timeframe in timeframes:
                setup = None
                bars = []
                for timeframe_limit in calculate_limit_grid(limit, timeframe):
                    bars = exchange.get_ohlcv(symbol.symbol, timeframe, timeframe_limit)
                    setup = detect_setup(symbol.symbol, bars, timeframe, symbol.context)
                    if setup:
                        break
                match setup:
                    # Сетап не найден.
                    case Unfilled():
                        removed_capture = capture_state.remove_capture(symbol.symbol, timeframe)
                        if removed_capture:
                            notifier.remove_button(removed_capture.message_id)
                            log.i(
                                f"Сетап {symbol.symbol} на {timeframe.tf} потерян, кнопка удалена."
                            )
                            trade_permission_service.clear_allowance(symbol.symbol, timeframe)
                            notified_once.discard((symbol.symbol, timeframe, "Capture"))
                        if capture_state.symbol == symbol.symbol and not capture_state.has_symbol_capture(symbol.symbol):
                            capture_state.symbol = None
                        continue

                    # Найден базовый сетап.
                    case Capture(
                        main_low_swing=main_low_swing,
                        main_high_swing=main_high_swing,
                        cascade_swings=cascade_swings,
                        resistance_swings=resistance_swings,
                        support_swings=support_swings,
                    ):
                        capture_key = (symbol.symbol, timeframe)
                        message = f"Включено слежение за {symbol.symbol} на {timeframe.tf}."
                        if capture_key in capture_state.captures:
                            active_capture = capture_state.captures[capture_key]
                            if active_capture.message_id:
                                executor.submit(
                                    _edit_notification,
                                    notifier=notifier,
                                    notification_type=NotificationType.EVENT,
                                    message_id=active_capture.message_id,
                                    message=message,
                                    symbol=symbol.symbol,
                                    timeframe=timeframe,
                                    bars=bars,
                                    main_low_swing=main_low_swing,
                                    main_high_swing=main_high_swing,
                                    cascade_swings=cascade_swings,
                                    resistance_swings=resistance_swings,
                                    support_swings=support_swings,
                                    subdir='event',
                                    context=symbol.context,
                                    keyboard=_build_trade_keyboard(symbol.symbol, timeframe, symbol.context),
                                ).result()
                            continue
                        if capture_state.symbol is None:
                            capture_state.symbol = symbol.symbol
                        capture_notification_key = (symbol.symbol, timeframe, setup.name)
                        if capture_notification_key not in notified_once:
                            message_id = executor.submit(
                                _send_notification,
                                notifier=notifier,
                                notification_type=NotificationType.EVENT,
                                message=message,
                                symbol=symbol.symbol,
                                timeframe=timeframe,
                                bars=bars,
                                main_low_swing=main_low_swing,
                                main_high_swing=main_high_swing,
                                cascade_swings=cascade_swings,
                                resistance_swings=resistance_swings,
                                support_swings=support_swings,
                                subdir='event',
                                context=symbol.context,
                                keyboard=_build_trade_keyboard(symbol.symbol, timeframe, symbol.context),
                            ).result()
                            capture_state.add_capture(
                                symbol=symbol.symbol,
                                timeframe=timeframe,
                                message_id=message_id,
                                timeout_multiplier=cfg.CAPTURE_TIMEOUT_MULTIPLIER,
                            )
                            notified_once.add(capture_notification_key)

                    # Найден торговый сетап.
                    case Buy(
                        main_low_swing=main_low_swing,
                        main_high_swing=main_high_swing,
                        cascade_swings=cascade_swings,
                        resistance_swings=resistance_swings,
                        support_swings=support_swings,
                    ):
                        message = f"Попытка открытия позиции в {symbol.symbol} на {timeframe.tf}."
                        log.i(message)
                        # TODO Фактическое открытие позиции на бирже.
                        executor.submit(
                            _send_notification,
                            notifier=notifier,
                            notification_type=NotificationType.ORDER,
                            message=message,
                            symbol=symbol.symbol,
                            timeframe=timeframe,
                            bars=bars,
                            main_low_swing=main_low_swing,
                            main_high_swing=main_high_swing,
                            cascade_swings=cascade_swings,
                            resistance_swings=resistance_swings,
                            support_swings=support_swings,
                            subdir='order',
                            context=symbol.context
                        ).result()
                        removed_capture = capture_state.remove_capture(symbol.symbol, timeframe)
                        if removed_capture:
                            notifier.remove_button(removed_capture.message_id)
                            log.i(
                                f"Сетап {symbol.symbol} на {timeframe.tf} закрыт из-за сигнала Buy, кнопка удалена."
                            )
                            notified_once.discard((symbol.symbol, timeframe, "Capture"))
                        trade_permission_service.clear_allowance(symbol.symbol, timeframe)
                        capture_state.symbol = None
