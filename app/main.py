from __future__ import annotations

import logging
import os
import time

import ccxt

from config.config import AppConfig, load_config, load_env
from infrastructure import setup_logging
from services.exchange import set_leverage
from services.market_scan import build_market_scanner


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
    exchange_class = getattr(ccxt, config.exchange.name)
    exchange: ccxt.Exchange = exchange_class(
        {
            "apiKey": config.exchange.api_key,
            "secret": config.exchange.api_secret,
            "enableRateLimit": True,
        }
    )
    return exchange


def start_symbol_monitoring(config: AppConfig, exchange: ccxt.Exchange) -> None:
    scanner = build_market_scanner(config, exchange=exchange)
    logging.info(
        "Запуск мониторинга символов для %s с периодом %s ч",
        config.exchange.name,
        config.scan.market_scan_interval_h,
    )
    scanner.run_forever()


def main() -> None:
    config = configure()
    exchange = create_exchange(config)
    leverage_applied = set_leverage(config.leverage.leverage, config.leverage.margin_mode, exchange)
    if not leverage_applied:
        logging.warning(
            "Не удалось установить плечо %s и режим маржи %s на бирже %s",
            config.leverage.leverage,
            config.leverage.margin_mode,
            config.exchange.name,
        )
    start_symbol_monitoring(config, exchange)


if __name__ == "__main__":
    main()
