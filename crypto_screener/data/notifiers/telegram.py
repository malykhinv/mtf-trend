from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.utils.logger import log


class TgNotifier(Notifier):
    def __init__(
            self,
            event_token: str,
            order_token: str,
            chat_id: str
    ) -> None:
        self._event_bot = Bot(token=event_token)
        self._order_bot = Bot(token=order_token)
        self._chat_id = chat_id

    # region Private.
    async def _send(
            self,
            bot: Bot,
            message: str,
            image_path: Optional[Path],
            has_button: bool
    ) -> Optional[str]:
        reply_markup = None
        if has_button:
            reply_markup = InlineKeyboardMarkup.from_button(
                InlineKeyboardButton(text="Торговать", callback_data="capture-active")
            )
        if image_path:
            with image_path.open("rb") as photo:
                sent_message = await bot.send_photo(
                    chat_id=self._chat_id,
                    photo=photo,
                    caption=message,
                    reply_markup=reply_markup,
                )
            return str(sent_message.message_id)
        sent_message = await bot.send_message(
            chat_id=self._chat_id,
            text=message,
            reply_markup=reply_markup,
        )
        return str(sent_message.message_id)

    # endregion

    def notify(
            self,
            notification_type: NotificationType,
            message: str,
            image_path: Optional[Path] = None,
            has_button: bool = False
    ) -> Optional[str]:
        match notification_type:
            case NotificationType.EVENT:
                bot = self._event_bot
            case NotificationType.ORDER:
                bot = self._order_bot
        try:
            message_link = asyncio.run(self._send(bot, message, image_path, has_button))
            log.d(f"Отправлено сообщение ({notification_type.name.lower()}): {message}")
            return message_link
        except Exception as exception:
            log.e(f"Ошибка при отправке сообщения:\n{exception}")
        return None

    def remove_button(self, message_link: Optional[str]) -> None:
        if not message_link:
            return
        try:
            asyncio.run(
                self._event_bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_link),
                    reply_markup=None,
                )
            )
            asyncio.run(
                self._order_bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_link),
                    reply_markup=None,
                )
            )
            log.d(f"Удалена кнопка у сообщения {message_link}")
        except Exception as exception:
            log.e(f"Ошибка при удалении кнопки у сообщения {message_link}: {exception}")
