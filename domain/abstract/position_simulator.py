"""Модуль проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod

from domain.models.candle import Candle
from domain.models.position import Position
from domain.models.trade_result import TradeResult


class PositionSimulator(ABC):
    """Класс."""
    @abstractmethod
    def process_candle(self, candle: Candle) -> TradeResult | None:
        """Метод."""
    @abstractmethod
    def open_position(self, position: Position) -> None:
        """Метод."""
    @abstractmethod
    def close_position(self, price: float, exit_timestamp_ms: int) -> TradeResult:
        """Метод."""
    @abstractmethod
    def update_stop(self, new_stop: float) -> None:
        """Метод."""