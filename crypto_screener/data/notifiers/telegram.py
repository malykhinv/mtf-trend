from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from telegram import Bot

from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.utils.logger import log


class TgNotifier(Notifier):
    def __init__(
            self,
            event_token: str,
            order_token: str,
            chat_id: str
    ):
        self._event_bot = Bot(token=event_token)
        self._order_bot = Bot(token=order_token)
        self._chat_id = chat_id

    # region Private.
    async def _send(
            self,
            bot: Bot,
            message: str,
            image_path: Optional[Path]
    ) -> None:
        if image_path:
            with image_path.open("rb") as photo:
                await bot.send_photo(chat_id=self._chat_id, photo=photo, caption=message)
            return
        await bot.send_message(chat_id=self._chat_id, text=message)

    # endregion

    def notify(
            self,
            notification_type: NotificationType,
            message: str,
            image_path: Optional[Path] = None
    ) -> None:
        match notification_type:
            case NotificationType.EVENT:
                bot = self._event_bot
            case NotificationType.ORDER:
                bot = self._order_bot
        try:
            asyncio.run(self._send(bot, message, image_path))
            log.d(f"Отправлено сообщение ({notification_type.name.lower()}): {message}")
        except Exception as exception:
            log.e(f"Ошибка при отправке сообщения:\n{exception}")
