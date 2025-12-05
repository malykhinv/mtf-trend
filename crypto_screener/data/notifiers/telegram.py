from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.notifier import Keyboard, Notifier, NotificationType
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
            keyboard: Optional[Keyboard]
    ) -> Optional[str]:
        reply_markup = self._get_keyboard(keyboard)
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

    async def _edit(
            self,
            bot: Bot,
            message_id: str,
            message: Optional[str],
            image_path: Optional[Path],
            keyboard: Optional[Keyboard]
    ) -> Optional[str]:
        reply_markup = self._get_keyboard(keyboard)
        if image_path:
            with image_path.open("rb") as photo:
                media = InputMediaPhoto(media=photo, caption=message)
                sent_message = await bot.edit_message_media(
                    chat_id=self._chat_id,
                    message_id=int(message_id),
                    media=media,
                    reply_markup=reply_markup,
                )
                return str(sent_message.message_id)
        if message:
            await bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=int(message_id),
                text=message,
                reply_markup=reply_markup,
            )
            return message_id
        if reply_markup:
            await bot.edit_message_reply_markup(
                chat_id=self._chat_id,
                message_id=int(message_id),
                reply_markup=reply_markup,
            )
            return message_id
        return None

    def _get_keyboard(self, keyboard: Optional[Keyboard]) -> Optional[InlineKeyboardMarkup]:
        if not keyboard:
            return None
        inline_rows = [
            [InlineKeyboardButton(text=text, callback_data=callback) for text, callback in row]
            for row in keyboard
        ]
        return InlineKeyboardMarkup(inline_rows)

    # endregion

    def notify(
            self,
            notification_type: NotificationType,
            message: str,
            image_path: Optional[Path] = None,
            keyboard: Optional[Keyboard] = None,
            context: Optional[Context] = None,
    ) -> Optional[str]:
        match notification_type:
            case NotificationType.EVENT:
                bot = self._event_bot
            case NotificationType.ORDER:
                bot = self._order_bot
        try:
            message_id = asyncio.run(self._send(bot, message, image_path, keyboard))
            log.d(f"Отправлено сообщение ({notification_type.name.lower()}): {message}")
            return message_id
        except Exception as exception:
            log.e(f"Ошибка при отправке сообщения:\n{exception}")
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
        match notification_type:
            case NotificationType.EVENT:
                bot = self._event_bot
            case NotificationType.ORDER:
                bot = self._order_bot
        try:
            return asyncio.run(
                self._edit(
                    bot=bot,
                    message_id=message_id,
                    message=message,
                    image_path=image_path,
                    keyboard=keyboard,
                )
            )
        except Exception as exception:
            log.e(f"Ошибка при редактировании сообщения {message_id}:\n{exception}")
        return None

    def remove_button(self, message_id: Optional[str]) -> None:
        if not message_id:
            return
        try:
            asyncio.run(
                self._event_bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_id),
                    reply_markup=None,
                )
            )
            asyncio.run(
                self._order_bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_id),
                    reply_markup=None,
                )
            )
            log.d(f"Удалена кнопка у сообщения {message_id}")
        except Exception as exception:
            log.e(f"Ошибка при удалении кнопки у сообщения {message_id}: {exception}")
