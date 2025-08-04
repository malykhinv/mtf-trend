import asyncio
import logging
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from dotenv import load_dotenv

load_dotenv()

API_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID: str | None = os.getenv("TELEGRAM_CHAT_ID")

_BOT: Optional[Bot] = None

logger = logging.getLogger(__name__)

# Кешируем идентификаторы сообщений для обновления одного и того же поста
_MESSAGE_CACHE: Dict[str, int] = {}


def get_bot() -> Optional[Bot]:
    """Lazily create and cache a Telegram :class:`Bot` instance.

    Returns ``None`` if the required credentials are missing. Subsequent
    calls return the cached bot until :func:`shutdown` is executed.
    """

    global _BOT
    if _BOT is not None:
        return _BOT
    if not API_TOKEN or not CHAT_ID:
        return None
    try:
        _BOT = Bot(API_TOKEN, parse_mode="HTML")
    except Exception:  # pragma: no cover - defensive; aiogram raises many errors
        logger.exception("Failed to initialize Telegram bot")
        _BOT = None
    return _BOT


async def shutdown() -> None:
    """Close the bot session if it was created."""
    global _BOT
    bot = _BOT
    if bot is not None:
        await bot.session.close()
        _BOT = None


def format_duration(seconds: float) -> str:
    """Возвращает человекочитаемую длительность по количеству секунд."""
    seconds = max(int(seconds), 0)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


def format_decimal(value: float | Decimal, precision: int = 4, signed: bool = False) -> str:
    """Возвращает строку с числом, округлённым до ``precision`` знаков.

    Параметр ``signed`` добавляет ``+`` для положительных чисел, что удобно
    при отображении прибыли и процентов.
    """
    quant = Decimal("1").scaleb(-precision)
    try:
        number = Decimal(str(value))
    except Exception:
        logger.exception("Invalid decimal value: %s", value)
        return "NaN"
    if not number.is_finite():
        logger.error("Non-finite decimal value: %s", value)
        return "NaN"
    number = number.quantize(quant, rounding=ROUND_HALF_UP)
    result = f"{number:.{precision}f}"
    if signed and not result.startswith("-"):
        result = "+" + result
    return result


async def _send_message(text: str) -> Optional[int]:
    """Отправляет новое сообщение в Telegram и возвращает его ID.

    В случае ошибок предпринимает несколько попыток с экспоненциальной
    задержкой между ними.
    """
    bot = get_bot()
    if bot is None or CHAT_ID is None:
        # Если нет конфигурации, просто выходим
        return None

    delay = 1
    for attempt in range(3):
        try:
            message = await bot.send_message(CHAT_ID, text)
            return message.message_id
        except TelegramAPIError:
            logger.exception("Failed to send Telegram message (attempt %s)", attempt + 1)
            if attempt < 2:
                await asyncio.sleep(delay)
                delay *= 2
    return None


async def _edit_message(message_id: int, text: str) -> None:
    """Редактирует ранее отправленное сообщение.

    Аналогично :func:`_send_message`, выполняет несколько попыток
    при возникновении ошибок Telegram.
    """
    bot = get_bot()
    if bot is None or CHAT_ID is None:
        return

    delay = 1
    for attempt in range(3):
        try:
            await bot.edit_message_text(text, chat_id=CHAT_ID, message_id=message_id)
            return
        except TelegramAPIError:
            logger.exception("Failed to edit Telegram message (attempt %s)", attempt + 1)
            if attempt < 2:
                await asyncio.sleep(delay)
                delay *= 2


async def notify_open(position_id: str, text: str) -> None:
    """Уведомляет об открытии позиции."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is None:
        message_id = await _send_message(text)
        if message_id is not None:
            _MESSAGE_CACHE[position_id] = message_id
    else:
        await _edit_message(message_id, text)


async def notify_partial_close(position_id: str, text: str) -> None:
    """Уведомляет о частичном закрытии позиции."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is None:
        message_id = await _send_message(text)
        if message_id is not None:
            _MESSAGE_CACHE[position_id] = message_id
    else:
        await _edit_message(message_id, text)


async def notify_close(position_id: str, text: str) -> None:
    """Сообщает о полном закрытии позиции и удаляет запись из кеша."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is not None:
        await _edit_message(message_id, text)
        del _MESSAGE_CACHE[position_id]
    else:
        await _send_message(text)
