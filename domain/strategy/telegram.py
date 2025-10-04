from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models import Signal
from .types import TelegramHandler


@dataclass
class TelegramNotifier:
    send_message: TelegramHandler

    def notify_uptick(self, symbol: str, signal: Signal, timestamp: datetime) -> None:
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self.send_message(f"{timestamp:%H:%M:%S} Обнаружен аптик {symbol} {direction}.")

    def notify_entry(self, symbol: str, signal: Signal, timestamp: datetime) -> None:
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self.send_message(f"{timestamp:%H:%M:%S} Открыт {direction} {symbol}.")

    def notify_exit(self, symbol: str, reason: str, timestamp: datetime) -> None:
        self.send_message(f"{timestamp:%H:%M:%S} Закрыт {symbol}. {reason}.")


__all__ = ["TelegramNotifier"]
