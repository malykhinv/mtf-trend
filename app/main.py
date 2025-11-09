from __future__ import annotations

import logging
import os
import time

import ccxt

from config.config import AppConfig, load_config, load_env
from infrastructure import TelegramNotifier, setup_logging
from integrations import RealSwingsExtractor, SwingsAdapter
from services.exchange import set_leverage
from services import build_trading_loop
from data_providers.ccxt_client import get_exchange_class


def configure_logging() -> None:
    setup_logging(level=logging.INFO)


def set_timezone(name: str) -> None:
    os.environ["TZ"] = name
    try:
        time.tzset()
    except AttributeError:
        logging.warning("tzset недоступен в текущей среде")


def configure() -> AppConfig:
    load_env()
    config = load_config()

    configure_logging()
    set_timezone(config.runtime.timezone)

    return config


def create_exchange(config: AppConfig) -> ccxt.Exchange:
    exchange_class = get_exchange_class(config.exchange.name)
    exchange: ccxt.Exchange = exchange_class(
        {
            "apiKey": config.exchange.api_key,
            "secret": config.exchange.api_secret,
            "enableRateLimit": True,
        }
    )
    return exchange


def create_swings_adapter() -> SwingsAdapter:
    extractor = RealSwingsExtractor()
    return SwingsAdapter(extractor)


def create_notifier(config: AppConfig) -> TelegramNotifier | None:
    token = (config.telegram.token or "").strip()
    chat_id = (config.telegram.chat_id or "").strip()
    if not token or not chat_id:
        return None
    return TelegramNotifier.from_config(config)


def main() -> None:
    config = configure()
    exchange = create_exchange(config)
    notifier = create_notifier(config)
    leverage_applied = set_leverage(config.leverage.leverage, config.leverage.margin_mode, exchange)
    if not leverage_applied:
        logging.warning(
            "Не удалось установить плечо %s и режим маржи %s на бирже %s",
            config.leverage.leverage,
            config.leverage.margin_mode,
            config.exchange.name,
        )
    swings_adapter = create_swings_adapter()
    trading_loop = build_trading_loop(
        config,
        exchange=exchange,
        swings_adapter=swings_adapter,
        notifier=notifier,
    )
    trading_loop.run_forever()


if __name__ == "__main__":
    main()
