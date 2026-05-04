"""Модуль проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod

from domain.models.candle import Candle
from domain.models.position import Position
from domain.models.position_result import PositionResult


class PositionSimulator(ABC):
    """Класс."""
    @abstractmethod
    def process_candle(self, candle: Candle) -> PositionResult | None:
        """Метод."""
    @abstractmethod
    def open_position(self, position: Position) -> None:
        """Метод."""
    @abstractmethod
    def close_position(self, price: float, exit_timestamp_ms: int) -> PositionResult:
        """Метод."""
    @abstractmethod
    def update_stop(self, new_stop: float) -> None:
        """Метод."""