from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update, MaybeInaccessibleMessage
from telegram.constants import UpdateType
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.notifier import Keyboard, Notifier, NotificationType
from crypto_screener.execution.trade_permission_service import TradePermissionService
from crypto_screener.utils.logger import log

TRADE_CALLBACK_PREFIX = "capture-active"
SKIP_CALLBACK_PREFIX = "capture-skip"


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
        self._application.add_handler(
            CallbackQueryHandler(
                self._handle_skip_request,
                pattern=fr"^{SKIP_CALLBACK_PREFIX}",
            )
        )

    def start(self) -> None:
        thread = threading.Thread(target=self._run_polling, name="tg-callback-handler", daemon=True)
        thread.start()
        log.d("Запущен обработчик callback-кнопок Telegram.")

    def _run_polling(self) -> None:
        self._application.run_polling(allowed_updates=[UpdateType.CALLBACK_QUERY])

    @staticmethod
    async def _get_message_data(update: Update) -> tuple[
        Optional[str],
        Optional[MaybeInaccessibleMessage],
        Optional[Timeframe],
        Optional[Context],
    ]:
        symbol = None
        message = None
        timeframe = None
        symbol_context = None
        query = update.callback_query
        if not query:
            return symbol, message, timeframe, symbol_context

        await query.answer()
        callback_data = (query.data or "").split(":")
        if len(callback_data) != 4:
            log.e(f"Не удалось разобрать callback_data: {query.data}")
            return symbol, message, timeframe, symbol_context

        _, symbol, timeframe_tf, context_value = callback_data
        try:
            timeframe = Timeframe.from_tf(timeframe_tf)
            symbol_context = Context(context_value)
        except ValueError as exception:
            log.e(f"Некорректные данные callback {query.data}: {exception}")
            return symbol, message, timeframe, symbol_context

        message = query.message
        return symbol, message, timeframe, symbol_context

    # noinspection PyUnusedLocal
    async def _handle_trade_request(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        symbol, message, timeframe, symbol_context = await self._get_message_data(update)
        if not message:
            log.e("Отсутствует сообщение для callback торговой кнопки.")
            return

        message_id = str(message.message_id)
        log.d(f"Получен запрос на торговлю {symbol} на {timeframe.tf} с контекстом {symbol_context.value}.")
        self._trade_permission_service.allow_symbol_for_trading(
            symbol=symbol,
            timeframe=timeframe,
            message_id=message_id,
            context=symbol_context,
        )
        await self._show_skip_only_keyboard(message, symbol, timeframe, symbol_context)

    # noinspection PyUnusedLocal
    async def _handle_skip_request(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        symbol, message, timeframe, symbol_context = await self._get_message_data(update)
        if not message:
            log.e("Отсутствует сообщение для callback кнопки игнорирования.")
            return

        message_id = str(message.message_id)
        log.d(f"Получен запрос на игнорирование {symbol} на {timeframe.tf} с контекстом {symbol_context.value}.")
        self._trade_permission_service.ignore_symbol(
            symbol=symbol,
            timeframe=timeframe,
            message_id=message_id,
            context=symbol_context,
        )
        self._trade_permission_service.clear_allowance(symbol, timeframe)

    async def _show_skip_only_keyboard(
            self,
            message: MaybeInaccessibleMessage,
            symbol: Optional[str],
            timeframe: Optional[Timeframe],
            symbol_context: Optional[Context],
    ) -> None:
        if not (symbol and timeframe and symbol_context):
            return
        skip_callback_data = f"{SKIP_CALLBACK_PREFIX}:{symbol}:{timeframe.tf}:{symbol_context.value}"
        skip_keyboard = InlineKeyboardMarkup.from_button(
            InlineKeyboardButton(text="Пропустить", callback_data=skip_callback_data)
        )
        try:
            await message.edit_reply_markup(reply_markup=skip_keyboard)
        except Exception as exception:
            log.e(f"Не удалось обновить кнопки для {symbol} на {timeframe.tf}: {exception}")


class TgNotifier(Notifier):
    def __init__(
            self,
            token: str,
            chat_id: str
    ) -> None:
        self._token = token
        self._bot = Bot(token=token)
        self._chat_id = chat_id
        self._callback_handler: Optional[_CallbackHandler] = None

    def start_callback_handler(self, trade_permission_service: TradePermissionService) -> None:
        if self._callback_handler:
            return

        self._callback_handler = _CallbackHandler(
            event_token=self._token,
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

    @staticmethod
    def _get_keyboard(keyboard: Optional[Keyboard]) -> Optional[InlineKeyboardMarkup]:
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
        try:
            message_id = asyncio.run(self._send(self._bot, message, image_path, keyboard))
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
        bot = self._bot
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

    def remove_button(
            self,
            message_id: Optional[str]
    ) -> None:
        if not message_id:
            return
        try:
            asyncio.run(
                self._bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_id),
                    reply_markup=None,
                )
            )
            log.d(f"Удалена кнопка у сообщения {message_id} в event-боте")
        except Exception as exception:
            log.e(f"Ошибка при удалении кнопки у сообщения {message_id} в event-боте: {exception}")

        try:
            asyncio.run(
                self._bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_id),
                    reply_markup=None,
                )
            )
            log.d(f"Удалена кнопка у сообщения {message_id} в order-боте")
        except Exception as exception:
            log.e(f"Ошибка при удалении кнопки у сообщения {message_id} в order-боте: {exception}")
