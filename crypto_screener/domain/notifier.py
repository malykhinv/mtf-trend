from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from crypto_screener.domain.models.context import Context

Keyboard = list[list[tuple[str, str]]]

if TYPE_CHECKING:
    pass


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
            keyboard: Optional[Keyboard] = None,
            context: Optional[Context] = None,
    ) -> Optional[str]:
        ...

    @abstractmethod
    def edit_message(
            self,
            notification_type: NotificationType,
            message_id: str,
            message: Optional[str] = None,
            image_path: Optional[Path] = None,
            keyboard: Optional[Keyboard] = None,
            context: Optional[Context] = None,
    ) -> Optional[str]:
        ...

    @abstractmethod
    def remove_button(self, message_id: Optional[str]) -> None:
        ...

    @abstractmethod
    def start_callback_handler(self, trade_permission_service: "TradePermissionService") -> None:
        ...
