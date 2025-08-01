import os
from typing import Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

_BOT: Optional[Bot] = Bot(API_TOKEN, parse_mode="HTML") if API_TOKEN else None

# Кешируем идентификаторы сообщений для обновления одного и того же поста
_MESSAGE_CACHE: Dict[str, int] = {}


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


async def _send_message(text: str) -> Optional[int]:
    """Отправляет новое сообщение в Telegram и возвращает его ID."""
    if _BOT is None or CHAT_ID is None:
        # Если нет конфигурации, просто выходим
        return None
    try:
        message = await _BOT.send_message(CHAT_ID, text)
        return message.message_id
    except TelegramAPIError:
        return None


async def _edit_message(message_id: int, text: str) -> None:
    """Редактирует ранее отправленное сообщение."""
    if _BOT is None or CHAT_ID is None:
        return
    try:
        await _BOT.edit_message_text(text, chat_id=CHAT_ID, message_id=message_id)
    except TelegramAPIError:
        return


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
