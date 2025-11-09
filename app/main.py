from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Sequence

import ccxt

from config.config import AppConfig, load_config, load_env
from infrastructure import TelegramNotifier, setup_logging
from integrations import RawSwingsOutput, SwingsAdapter, SwingsExtractor
from domain.models import Candle
from services.exchange import set_leverage
from services import build_trading_loop


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


@dataclass
class _FallbackSwingsExtractor(SwingsExtractor):
    """Simple extractor returning an empty swings payload."""

    warned: bool = field(default=False, init=False)

    def extract(self, candles: Sequence[Candle]) -> RawSwingsOutput:
        if not self.warned:
            logging.warning(
                "SwingsExtractor не настроен, используется заглушка без сигналов"
            )
            self.warned = True
        return {
            "swings": [],
            "has_consolidation": False,
            "consolidation_band": None,
        }


def create_swings_adapter() -> SwingsAdapter:
    extractor: SwingsExtractor = _FallbackSwingsExtractor()
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
