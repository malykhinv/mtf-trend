import asyncio

from telegram import Bot

from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.utils.logger import log


class TgNotifier(Notifier):
    def __init__(self, event_token: str, order_token: str, chat_id: str):
        self._event_bot = Bot(token=event_token)
        self._order_bot = Bot(token=order_token)
        self._chat_id = chat_id

    def notify(self, type: NotificationType, message: str) -> None:
        try:
            asyncio.run(self._event_bot.send_message(chat_id=self._chat_id, text=message))
            log.d(f"Отправлено сообщение: {message}")
        except Exception as exception:
            log.e(f"Ошибка при отправке сообщения:\n{exception}")
