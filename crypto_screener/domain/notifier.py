from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Optional


class NotificationType(Enum):
    EVENT = "EVENT"
    ORDER = "ORDER"


class Notifier(ABC):
    @abstractmethod
    def notify(
            self,
            notification_type: NotificationType,
            message: str,
            image_path: Optional[Path] = None,
            has_button: bool = False
    ) -> Optional[str]:
        ...

    @abstractmethod
    def remove_button(self, message_link: Optional[str]) -> None:
        ...
