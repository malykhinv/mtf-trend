from __future__ import annotations

import logging
import os
import time

from config.config import AppConfig, load_config, load_env


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")


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
    logging.info("Мониторинг символов ещё не реализован для %s", config.exchange.name)


def main() -> None:
    config = configure()
    start_symbol_monitoring(config)


if __name__ == "__main__":
    main()
