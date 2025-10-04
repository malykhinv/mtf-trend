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

    def enter(self, symbol: str, signal: Signal, wall: Wall, timestamp: datetime) -> None:
        self.enter_position(symbol, signal, wall)
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self.logger.log(
            f"Вход {direction} {symbol} по стене {wall.price:g}.",
            timestamp,
        )

    def exit(self, symbol: str, reason: str, timestamp: datetime) -> None:
        self.exit_position(symbol, reason)
        self.logger.log(f"Выход {symbol}. Причина: {reason}.", timestamp)

    def adjust_stop(self, symbol: str, price: float, timestamp: datetime) -> None:
        self.move_stop(symbol, price)
        self.logger.log(f"Перенос стопа {symbol} на {price:g}.", timestamp)


__all__ = ["PositionController"]
