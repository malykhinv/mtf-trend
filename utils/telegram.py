import os
from typing import Dict, Optional

import aiohttp

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_URL = f"https://api.telegram.org/bot{API_TOKEN}"

# Cache message IDs for each position so that updates edit the same message.
_MESSAGE_CACHE: Dict[str, int] = {}


def format_duration(seconds: float) -> str:
    """Return a human readable duration given ``seconds``."""
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
    """Send a new Telegram message and return the message ID."""
    if not API_TOKEN or not CHAT_ID:
        # Fail silently if configuration is missing.
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{API_URL}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            ) as response:
                response.raise_for_status()
                payload = await response.json()
                return payload.get("result", {}).get("message_id")
    except Exception:
        return None


async def _edit_message(message_id: int, text: str) -> None:
    """Edit an existing Telegram message."""
    if not API_TOKEN or not CHAT_ID:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{API_URL}/editMessageText",
                json={
                    "chat_id": CHAT_ID,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            ) as response:
                response.raise_for_status()
    except Exception:
        return


async def notify_open(position_id: str, text: str) -> None:
    """Notify that a position has been opened."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is None:
        message_id = await _send_message(text)
        if message_id is not None:
            _MESSAGE_CACHE[position_id] = message_id
    else:
        await _edit_message(message_id, text)


async def notify_partial_close(position_id: str, text: str) -> None:
    """Notify about a partial position closure."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is None:
        message_id = await _send_message(text)
        if message_id is not None:
            _MESSAGE_CACHE[position_id] = message_id
    else:
        await _edit_message(message_id, text)


async def notify_close(position_id: str, text: str) -> None:
    """Notify that a position has been fully closed and remove it from cache."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is not None:
        await _edit_message(message_id, text)
        del _MESSAGE_CACHE[position_id]
    else:
        await _send_message(text)
