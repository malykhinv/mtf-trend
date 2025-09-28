"""Aggregates aggressive trade prints to compute imbalances."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque

from bot import config


@dataclass(slots=True)
class TradePrint:
    timestamp: datetime
    is_buy: bool
    quantity: float


class PrintsAggregator:
    def __init__(self) -> None:
        self._window = timedelta(seconds=config.AGGR_WINDOW_SEC)
        self._prints: Deque[TradePrint] = deque()
        self._total_qty: float = 0.0
        self._buy_qty: float = 0.0

    def add_print(self, trade_print: TradePrint) -> None:
        self._prints.append(trade_print)
        self._total_qty += trade_print.quantity
        if trade_print.is_buy:
            self._buy_qty += trade_print.quantity
        self._drop_expired(trade_print.timestamp)

    def imbalance(self, now: datetime | None = None) -> float:
        reference_time = now or datetime.now(tz=config.UTC)
        self._drop_expired(reference_time)
        if self._total_qty == 0:
            return 0.5
        return self._buy_qty / self._total_qty

    def clear(self) -> None:
        self._prints.clear()
        self._total_qty = 0.0
        self._buy_qty = 0.0

    def _drop_expired(self, now: datetime) -> None:
        cutoff = now - self._window
        while self._prints and self._prints[0].timestamp < cutoff:
            expired = self._prints.popleft()
            self._total_qty -= expired.quantity
            if expired.is_buy:
                self._buy_qty -= expired.quantity
