from abc import ABC, abstractmethod
from enum import Enum


class NotificationType(Enum):
    EVENT = "EVENT"
    ORDER = "ORDER"

class Notifier(ABC):
    @abstractmethod
    def notify(self, type: NotificationType, message: str) -> None:
        ...
