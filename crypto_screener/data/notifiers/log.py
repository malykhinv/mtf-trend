from __future__ import annotations

from pathlib import Path
from typing import Optional

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.notifier import Keyboard, Notifier, NotificationType
from crypto_screener.utils.logger import log


class LogNotifier(Notifier):
    def notify(
            self,
            notification_type: NotificationType,
            message: str,
            image_path: Optional[Path] = None,
            keyboard: Optional[Keyboard] = None,
            context: Optional[Context] = None,
    ) -> Optional[str]:
        log.d(f"Запрос отправки сообщения с типом {notification_type.name.capitalize()}: {message}")
        return None

    def edit_message(
            self,
            notification_type: NotificationType,
            message_id: str,
            message: Optional[str] = None,
            image_path: Optional[Path] = None,
            keyboard: Optional[Keyboard] = None,
            context: Optional[Context] = None,
    ) -> Optional[str]:
        log.d(
            f"Запрос на редактирование сообщения {message_id}"
            f" с типом {notification_type.name.capitalize()}"
        )
        return None

    def remove_button(self, message_id: Optional[str]) -> None:
        log.d(f"Запрос на удаление кнопки у сообщения {message_id}")
