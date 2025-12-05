from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import UpdateType
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.notifier import Keyboard, Notifier, NotificationType
from crypto_screener.execution.trade_permission_service import TradePermissionService
from crypto_screener.utils.logger import log

TRADE_CALLBACK_PREFIX = "capture-active"


class _CallbackHandler:
    def __init__(
            self,
            event_token: str,
            trade_permission_service: TradePermissionService,
    ) -> None:
        self._trade_permission_service = trade_permission_service
        self._application = Application.builder().token(event_token).build()
        self._application.add_handler(
            CallbackQueryHandler(
                self._handle_trade_request,
                pattern=fr"^{TRADE_CALLBACK_PREFIX}",
            )
        )

    def start(self) -> None:
        thread = threading.Thread(target=self._run_polling, name="tg-callback-handler", daemon=True)
        thread.start()
        log.d("Запущен обработчик callback-кнопок Telegram.")

    def _run_polling(self) -> None:
        self._application.run_polling(allowed_updates=[UpdateType.CALLBACK_QUERY])

    async def _handle_trade_request(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if not query:
            return

        await query.answer()
        callback_data = (query.data or "").split(":")
        if len(callback_data) != 4:
            log.e(f"Не удалось разобрать callback_data: {query.data}")
            return

        _, symbol, timeframe_tf, context_value = callback_data
        try:
            timeframe = Timeframe.from_tf(timeframe_tf)
            symbol_context = Context(context_value)
        except ValueError as exception:
            log.e(f"Некорректные данные callback {query.data}: {exception}")
            return

        message = query.message
        if not message:
            log.e("Отсутствует сообщение для callback торговой кнопки.")
            return

        message_id = str(message.message_id)
        log.d(
            f"Получен запрос на торговлю {symbol} на {timeframe.tf} с контекстом {symbol_context.value}."
        )
        self._trade_permission_service.allow_symbol_for_trading(
            symbol=symbol,
            timeframe=timeframe,
            message_id=message_id,
            context=symbol_context,
        )


class TgNotifier(Notifier):
    def __init__(
            self,
            event_token: str,
            order_token: str,
            chat_id: str
    ) -> None:
        self._event_token = event_token
        self._event_bot = Bot(token=event_token)
        self._order_bot = Bot(token=order_token)
        self._chat_id = chat_id
        self._callback_handler: Optional[_CallbackHandler] = None

    def start_callback_handler(self, trade_permission_service: TradePermissionService) -> None:
        if self._callback_handler:
            return

        self._callback_handler = _CallbackHandler(
            event_token=self._event_token,
            trade_permission_service=trade_permission_service,
        )
        self._callback_handler.start()

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
