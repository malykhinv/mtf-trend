from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models import Signal, Wall
from .event_logger import EventLogger
from .types import PositionEntryHandler, PositionExitHandler, StopMoveHandler


@dataclass
class PositionController:
    enter_position: PositionEntryHandler
    exit_position: PositionExitHandler
    move_stop: StopMoveHandler
    logger: EventLogger

    def enter(self, symbol: str, signal: Signal, wall: Wall, timestamp: datetime) -> bool:
        if not self.enter_position(symbol, signal, wall):
            return False
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self.logger.log_trade(
            f"Вход {direction} {symbol} по стене {wall.price:g}.",
            timestamp,
        )
        return True

    def exit(self, symbol: str, reason: str, timestamp: datetime) -> bool:
        if not self.exit_position(symbol, reason):
            return False
        self.logger.log_trade(f"Выход {symbol}. Причина: {reason}.", timestamp)
        return True

    def adjust_stop(self, symbol: str, price: float, timestamp: datetime) -> bool:
        if not self.move_stop(symbol, price):
            return False
        self.logger.log_trade(f"Перенос стопа {symbol} на {price:g}.", timestamp)
        return True


__all__ = ["PositionController"]
