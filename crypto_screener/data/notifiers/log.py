from __future__ import annotations

from pathlib import Path
from typing import Optional

from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.utils.logger import log


class LogNotifier(Notifier):
    def notify(
            self,
            notification_type: NotificationType,
            message: str,
            image_path: Optional[Path] = None,
            has_button: bool = False
    ) -> Optional[str]:
        log.d(f"Запрос отправки сообщения с типом {notification_type.name.capitalize()}: {message}")
        return None

    def remove_button(self, message_link: Optional[str]) -> None:
        log.d(f"Запрос на удаление кнопки у сообщения {message_link}")
