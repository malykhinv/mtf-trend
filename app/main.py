from __future__ import annotations

import logging
import os
import time

from config.config import AppConfig, load_config, load_env
from infrastructure import setup_logging
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


def start_symbol_monitoring(config: AppConfig) -> None:
    scanner = build_market_scanner(config)
    logging.info(
        "Запуск мониторинга символов для %s с периодом %s ч",
        config.exchange.name,
        config.scan.market_scan_interval_h,
    )
    scanner.run_forever()


def main() -> None:
    config = configure()
    start_symbol_monitoring(config)


if __name__ == "__main__":
    main()
