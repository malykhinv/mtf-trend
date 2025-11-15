import os

from dotenv import load_dotenv

from crypto_screener.config.config import cfg
from crypto_screener.data.exchanges.binance import Binance
from crypto_screener.data.notifiers.log import LogNotifier
from crypto_screener.data.notifiers.telegram import TgNotifier
from crypto_screener.domain.exchange import Exchange, FuturesSymbol
from crypto_screener.domain.models.mode import Live, Mode, TestMarket, TestSymbol
from crypto_screener.domain.notifier import Notifier
from crypto_screener.execution.run_live import run_live
from crypto_screener.execution.run_test_market import run_test_market
from crypto_screener.execution.run_test_symbol import run_test_symbol
from crypto_screener.utils.logger import log


# region Private
def _initialize_exchange() -> Exchange:
    api_key = os.getenv("API_KEY", None)
    api_secret = os.getenv("API_SECRET", None)
    # return Bybit(api_key, api_secret)
    return Binance(api_key, api_secret)


def _initialize_notifier() -> Notifier:
    is_notifier_enabled = cfg.IS_NOTIFIER_ENABLED
    event_token = os.getenv("TELEGRAM_EVENT_TOKEN", None)
    order_token = os.getenv("TELEGRAM_ORDER_TOKEN", None)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", None)
    if is_notifier_enabled and event_token and order_token and chat_id:
        return TgNotifier(event_token, order_token, chat_id)
    return LogNotifier()


def _fetch_symbols(exchange: Exchange) -> list[FuturesSymbol]:
    symbols = exchange.get_futures_symbols()
    return symbols


def _filter_symbols(
        symbols: list[FuturesSymbol],
        btc_trades_24h: int
) -> list[FuturesSymbol]:
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


def _fetch_filtered_symbols(exchange: Exchange) -> list[FuturesSymbol]:
    symbols = _fetch_symbols(exchange)
    btc_trades_24h = next((s.trades_24h for s in symbols if s.symbol.startswith("BTC")), 0)
    filtered_symbols = _filter_symbols(symbols, btc_trades_24h)
    log.i(f"Отобрано {len(filtered_symbols)} символов из {len(symbols)}.")
    return filtered_symbols


# endregion

def main() -> None:
    load_dotenv()
    exchange = _initialize_exchange()
    tfs = cfg.TFS
    mode: Mode = cfg.MODE
    match mode:
        case Live():
            notifier = _initialize_notifier()
            filtered_symbols = _fetch_filtered_symbols(exchange)
            run_live(exchange, notifier, filtered_symbols, tfs)

        case TestMarket():
            symbols = _fetch_symbols(exchange)
            run_test_market(exchange, symbols, tfs)

        case TestSymbol(symbol=symbol, timeframe=timeframe, limit=limit, end=end):
            run_test_symbol(exchange, symbol, timeframe, limit, end)

        case _:
            # noinspection PyUnreachableCode
            raise ValueError(f"Режим {mode} не предусмотрен.")


if __name__ == "__main__":
    main()
