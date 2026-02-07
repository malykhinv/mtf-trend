"""Position simulator abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from domain.models.candle import Candle
from domain.models.position import Position
from domain.models.trade_result import TradeResult


class PositionSimulator(ABC):
    """Interface for trade position lifecycle simulation."""

    @abstractmethod
    def process_candle(self, candle: Candle) -> TradeResult | None:
        """Process new candle and return trade result if position is closed."""

    @abstractmethod
    def open_position(self, position: Position) -> None:
        """Open new trading position."""

    @abstractmethod
    def close_position(self, price: float) -> TradeResult:
        """Close current position at given price and return the result."""

    @abstractmethod
    def update_stop(self, new_stop: float) -> None:
        """Update stop-loss for active position."""
