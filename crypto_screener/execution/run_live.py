from concurrent.futures import ThreadPoolExecutor

from crypto_screener.config.config import cfg
from crypto_screener.domain.capture_state import CaptureState
from crypto_screener.domain.exchange import Exchange, FuturesSymbol
from crypto_screener.domain.models.setup import Setup
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.utils.logger import log
from crypto_screener.utils.signals import handle_sig


def run_live(
        exchange: Exchange,
        notifier: Notifier,
        filtered_symbols: list[FuturesSymbol],
        tf_list: list[Timeframe]
) -> None:
    log.d("Запуск в живом режиме.")
    if not filtered_symbols or not tf_list:
        log.e("Не хватает данных для начала анализа.")
        return

    capture_state = CaptureState()
    executor = ThreadPoolExecutor(max_workers=2)
    handle_sig(executor)
    notified_once: set[tuple[str, Timeframe, Setup]] = set()

    while True:
        if not capture_state.symbol:
            log.d("Запуск цикла анализа отобранных монет.")

        active_symbols = [s for s in filtered_symbols if not capture_state.symbol or capture_state.symbol == s.symbol]
        for symbol in active_symbols:
            for timeframe in tf_list:
                bars = exchange.get_ohlcv(symbol.symbol, timeframe, cfg.OHLCV_LIMIT)
                setup = detect_setup(bars)
                if setup is None:
                    if capture_state.symbol == symbol.symbol:
                        capture_state.symbol = None
                    continue

                if setup == Setup.CAPTURE:
                    if capture_state.symbol is None:
                        capture_state.symbol = symbol.symbol
                    key = (symbol.symbol, timeframe, setup)
                    if key not in notified_once:
                        notified_once.add(key)
                        message = f"Включено слежение за {symbol.symbol} на {timeframe.tf}."
                        executor.submit(notifier.notify, NotificationType.EVENT, message)
                elif setup == Setup.ORDER:
                    message = f"Попытка открытия позиции в {symbol.symbol} на {timeframe.tf}."
                    log.i(message)
                    # TODO Открытие позиции на бирже.
                    # TODO Создание изображения для уведомления.
                    executor.submit(notifier.notify, NotificationType.ORDER, message)
                    capture_state.symbol = None
