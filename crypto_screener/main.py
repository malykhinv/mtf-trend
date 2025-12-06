import os

from dotenv import load_dotenv

from crypto_screener.config.config import cfg
# noinspection PyUnusedImports
from crypto_screener.data.exchanges.binance import Binance
# noinspection PyUnusedImports
from crypto_screener.data.exchanges.bybit import Bybit
from crypto_screener.data.notifiers.log import LogNotifier
from crypto_screener.data.notifiers.telegram import TgNotifier
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.mode import Live, Mode, TestMarket, TestSymbols
from crypto_screener.domain.notifier import Notifier
from crypto_screener.execution.run_live import run_live
from crypto_screener.execution.run_test_market import run_test_market
from crypto_screener.execution.run_test_symbol import run_test_symbols


# region Private.
def _initialize_exchange() -> Exchange:
    api_key = os.getenv("API_KEY", None)
    api_secret = os.getenv("API_SECRET", None)
    return Bybit(api_key, api_secret)
    # return Binance(api_key, api_secret)


def _initialize_notifier() -> Notifier:
    event_token = os.getenv("TELEGRAM_EVENT_TOKEN", None)
    order_token = os.getenv("TELEGRAM_ORDER_TOKEN", None)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", None)
    if event_token and order_token and chat_id:
        return TgNotifier(event_token, order_token, chat_id)
    return LogNotifier()


# endregion

def main() -> None:
    load_dotenv()
    exchange = _initialize_exchange()
    mode: Mode = cfg.MODE
    match mode:
        # Работа с актуальным рынком.
        case Live(
            timeframes=timeframes,
            limit=limit,
            listing_period_days=listing_period_days,
            volume_24h_new_usdt_min=volume_24h_new_usdt_min,
            volume_24h_old_usdt_min=volume_24h_old_usdt_min,
            trades_24h_min=trades_24h_min,
            trades_24h_btc_ratio_min=trades_24h_btc_ratio_min
        ):
            notifier = _initialize_notifier()
            run_live(
                exchange,
                notifier,
                timeframes,
                limit,
                listing_period_days,
                volume_24h_new_usdt_min,
                volume_24h_old_usdt_min,
                trades_24h_min,
                trades_24h_btc_ratio_min
            )

        # Тест истории для всего рынка.
        case TestMarket(
            timeframes=timeframes,
            limit=limit,
            window=window,
            history_months=history_months,
            plot_policy=plot_policy,
            volume_24h_usdt_min=volume_24h_usdt_min,
            trades_24h_min=trades_24h_min,
            listing_age_days_min=listing_age_days_min
        ):
            run_test_market(
                exchange,
                timeframes,
                limit,
                window,
                history_months,
                plot_policy,
                volume_24h_usdt_min,
                trades_24h_min,
                listing_age_days_min,
            )

        # Тест набора символов.
        case TestSymbols(
            test_data=test_data,
            limit=limit,
            plot_policy=plot_policy
        ):
            run_test_symbols(exchange, test_data, limit, plot_policy)

        case _:
            # noinspection PyUnreachableCode
            raise ValueError(f"Режим {mode} не предусмотрен.")


if __name__ == "__main__":
    main()
