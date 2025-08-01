from __future__ import annotations

import logging
from typing import Any

from utils.futures_screener import screen_futures


class Screener:
    """Простая обёртка вокруг функции ``screen_futures``."""

    def __init__(self, exchange_name: str = "binanceusdm") -> None:
        self.exchange_name = exchange_name

    def screen(self, data: Any | None = None, max_symbols: int = 10) -> list[str]:
        """Возвращает список символов, которые проходят критерии скринера."""
        logging.info("Screening futures markets")
        metrics = screen_futures(
            exchange_name=self.exchange_name, max_positions=max_symbols
        )
        return [m.symbol for m in metrics]
