from telegram import Bot
import asyncio

from crypto_screener.domain.notifier import Notifier
from crypto_screener.utils.logger import log


class TgNotifier(Notifier):
    def __init__(self, token: str, chat_id: str):
        self._bot = Bot(token=token)
        self._chat_id = chat_id

    def notify(self, message: str) -> None:
        try:
            asyncio.run(self._bot.send_message(chat_id=self._chat_id, text=message))
            log.d(f"Отправлено сообщение: {message}")
        except Exception as exception:
            log.e(f"Ошибка при отправке сообщения:\n{exception}")


class LogNotifier(Notifier):
    def notify(self, message: str) -> None:
        log.d(f"Запрос отправки сообщения: {message}")
