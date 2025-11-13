import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from dotenv import load_dotenv

from crypto_screener.config.config import cfg
from crypto_screener.data.exchanges.binance import Binance
from crypto_screener.data.notifiers.tg import LogNotifier, TgNotifier
from crypto_screener.domain.exchange import Exchange, FuturesSymbol
from crypto_screener.domain.capture_state import CaptureState
from crypto_screener.domain.models.mode import Mode
from crypto_screener.domain.models.setup import SetupType
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.utils.logger import log
from crypto_screener.utils.signals import handle_sig


def build_exchange(api_key: str, api_secret: str) -> Exchange:
    # return Bybit(api_key, api_secret)
    return Binance(api_key, api_secret)


def build_notifier(enabled: bool, token: Optional[str], chat_id: Optional[str]):
    if enabled and token and chat_id:
        return TgNotifier(token, chat_id)
    return LogNotifier()


def filter_symbols(symbols: list[FuturesSymbol], btc_trades_24h: int) -> list[FuturesSymbol]:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    filtered: list[FuturesSymbol] = []

    for symbol in symbols:
        listing_age_days = (now - symbol.listing_ts.astimezone(timezone.utc)).days
        if listing_age_days <= cfg.LISTING_PERIOD_DAYS:
            if symbol.volume_usdt_24h > cfg.VOLUME_24H_NEW_MIN:
                filtered.append(symbol)
        else:
            if (
                    symbol.volume_usdt_24h > cfg.VOLUME_24H_OLD_MIN
                    and symbol.trades_24h
                    > min(cfg.TRADES_24H_MIN, int(cfg.TRADES_24H_BTC_RATIO * btc_trades_24h))
            ):
                filtered.append(symbol)
    return filtered


def initialize_exchange() -> Exchange:
    api_key = os.getenv("API_KEY", None)
    api_secret = os.getenv("API_SECRET", None)
    return build_exchange(api_key, api_secret)


def initialize_notifier() -> LogNotifier | TgNotifier:
    tg_token = os.getenv("TG_TOKEN", None)
    tg_chat = os.getenv("TG_CHAT_ID", None)
    return build_notifier(cfg.NOTIFY_ENABLED, tg_token, tg_chat)


def fetch_symbols(exchange: Exchange) -> list[FuturesSymbol]:
    symbols = exchange.get_futures_symbols()
    return symbols


def fetch_filtered_symbols(exchange: Exchange) -> list[FuturesSymbol]:
    symbols = fetch_symbols(exchange)
    btc_trades_24h = next((s.trades_24h for s in symbols if s.symbol.startswith("BTC")), 0)
    filtered_symbols = filter_symbols(symbols, btc_trades_24h)
    log.i(f"Отобрано {len(filtered_symbols)} символов из {len(symbols)}.")
    return filtered_symbols


def run_live(
        exchange: Exchange,
        notifier: LogNotifier | TgNotifier,
        filtered_symbols: list[FuturesSymbol],
        tf_list: list[Timeframe],
) -> None:
    log.d("Запуск в живом режиме.")
    if not filtered_symbols or not tf_list:
        log.e("Не хватает данных для начала анализа.")
        return

    capture_state = CaptureState()
    executor = ThreadPoolExecutor(max_workers=2)
    handle_sig(executor)
    notified_once: set[tuple[str, Timeframe, SetupType]] = set()

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

                if setup.type == SetupType.CAPTURE:
                    if capture_state.symbol is None:
                        capture_state.symbol = symbol.symbol
                    key = (symbol.symbol, timeframe, setup.type)
                    if key not in notified_once:
                        notified_once.add(key)
                        message = f"Включено слежение за {symbol.symbol} на {timeframe.tf}."
                        executor.submit(notifier.notify, message)
                elif setup.type == SetupType.ORDER:
                    message = f"Попытка открытия позиции в {symbol.symbol} на {timeframe.tf}."
                    log.i(message)
                    # TODO Открытие позиции на бирже.
                    # TODO Создание изображения для уведомления.
                    executor.submit(notifier.notify, message)
                    capture_state.symbol = None


def run_backtest(exchange: Exchange, symbols: list[FuturesSymbol]) -> None:
    # TODO Реализовать позже.
    return None

def main() -> None:
    load_dotenv()
    exchange = initialize_exchange()
    tfs = cfg.TFS
    mode = cfg.MODE
    if mode == Mode.LIVE:
        notifier = initialize_notifier()
        filtered_symbols = fetch_filtered_symbols(exchange)
        run_live(exchange, notifier, filtered_symbols, tfs)
    elif mode == Mode.BACKTEST:
        symbols = fetch_symbols(exchange)
        run_backtest(exchange, symbols)
    else:
        log.e(f"Режим {mode} не предусмотрен.")


if __name__ == "__main__":
    main()
