from abc import ABC, abstractmethod
from enum import Enum


class NotificationType(Enum):
    EVENT = "EVENT"
    ORDER = "ORDER"


class Notifier(ABC):
    @abstractmethod
    def notify(
            self,
            notification_type: NotificationType,
            message: str
    ) -> None:
        ...
