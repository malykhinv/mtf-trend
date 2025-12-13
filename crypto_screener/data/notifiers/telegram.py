from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Awaitable, Dict, Optional, TypeVar, cast
from urllib.parse import unquote

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message, Update, \
    MaybeInaccessibleMessage
from telegram.constants import ParseMode, UpdateType
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
        callback_data = (query.data or "").split(":", 3)
        if len(callback_data) != 4:
            log.e(f"Не удалось разобрать callback_data: {query.data}")
            return symbol, message, timeframe, symbol_context

        _, encoded_symbol, timeframe_tf, encoded_context = callback_data
        symbol = unquote(encoded_symbol)
        context_value = unquote(encoded_context)
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

    @staticmethod
    async def _show_skip_only_keyboard(
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
        if not hasattr(message, "edit_reply_markup"):
            log.w(
                "Получено недоступное сообщение для обновления кнопок, edit_reply_markup отсутствует."
            )
            return

        editable_message = cast(Message, message)

        try:
            await editable_message.edit_reply_markup(reply_markup=skip_keyboard)
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
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="tg-notifier-loop",
            daemon=True,
        )
        self._loop_thread.start()
        self._loop_ready.wait()
        self._message_states: Dict[str, tuple[Optional[str], Optional[tuple[tuple[tuple[str, str], ...], ...]]]] = {}
        self._state_lock = threading.Lock()

    def start_callback_handler(
            self,
            trade_permission_service: TradePermissionService
    ) -> None:
        if self._callback_handler:
            return

        self._callback_handler = _CallbackHandler(
            event_token=self._token,
            trade_permission_service=trade_permission_service,
        )
        self._callback_handler.start()

    # region Private.
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    T = TypeVar("T")

    def _submit(
            self,
            coro: Awaitable[T]
    ) -> T:
        if not self._loop_ready.is_set():
            self._loop_ready.wait()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    @staticmethod
    def _normalize_keyboard(keyboard: Optional[Keyboard]) -> Optional[tuple[tuple[tuple[str, str], ...], ...]]:
        if not keyboard:
            return None
        return tuple(tuple((text, callback) for text, callback in row) for row in keyboard)

    def _should_skip_edit(
            self,
            message_id: str,
            message: Optional[str],
            keyboard: Optional[Keyboard],
            image_path: Optional[Path],
    ) -> bool:
        if image_path is not None:
            return False
        with self._state_lock:
            state = self._message_states.get(message_id)
            if not state:
                return False
            current_message, current_keyboard = state
            if message is not None and message != current_message:
                return False
            normalized_keyboard = self._normalize_keyboard(keyboard)
            return normalized_keyboard == current_keyboard

    def _update_state(
            self,
            message_id: str,
            message: Optional[str],
            keyboard: Optional[Keyboard],
    ) -> None:
        normalized_keyboard = self._normalize_keyboard(keyboard)
        with self._state_lock:
            current_message, _ = self._message_states.get(message_id, (None, None))
            new_message = current_message if message is None else message
            self._message_states[message_id] = (new_message, normalized_keyboard)

    def _clear_keyboard_state(
            self,
            message_id: str
    ) -> None:
        with self._state_lock:
            if message_id in self._message_states:
                current_message, _ = self._message_states[message_id]
                self._message_states[message_id] = (current_message, None)

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
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            return str(sent_message.message_id)
        sent_message = await bot.send_message(
            chat_id=self._chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
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
                media = InputMediaPhoto(media=photo, caption=message, parse_mode=ParseMode.HTML)
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
                parse_mode=ParseMode.HTML,
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
            message_id = self._submit(self._send(self._bot, message, image_path, keyboard))
            log.d(f"Отправлено сообщение ({notification_type.name.lower()}): {message}")
            if message_id:
                self._update_state(message_id, message, keyboard)
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
            if self._should_skip_edit(message_id, message, keyboard, image_path):
                return message_id
            edited_message_id = self._submit(
                self._edit(
                    bot=bot,
                    message_id=message_id,
                    message=message,
                    image_path=image_path,
                    keyboard=keyboard,
                )
            )
            if edited_message_id:
                self._update_state(message_id, message, keyboard)
            return edited_message_id
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
            self._submit(
                self._bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_id),
                    reply_markup=None,
                )
            )
            self._clear_keyboard_state(message_id)
            log.d(f"Удалена кнопка у сообщения {message_id} в event-боте")
        except Exception as exception:
            log.e(f"Ошибка при удалении кнопки у сообщения {message_id} в event-боте: {exception}")

        try:
            self._submit(
                self._bot.edit_message_reply_markup(
                    chat_id=self._chat_id,
                    message_id=int(message_id),
                    reply_markup=None,
                )
            )
            self._clear_keyboard_state(message_id)
            log.d(f"Удалена кнопка у сообщения {message_id} в order-боте")
        except Exception as exception:
            log.e(f"Ошибка при удалении кнопки у сообщения {message_id} в order-боте: {exception}")
