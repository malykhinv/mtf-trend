from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.utils.logger import log


class LogNotifier(Notifier):
    def notify(
            self,
            notification_type: NotificationType,
            message: str
    ) -> None:
        log.d(f"Запрос отправки сообщения с типом {notification_type.name.capitalize()}: {message}")
