import os
from typing import Dict, Optional

import requests

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_URL = f"https://api.telegram.org/bot{API_TOKEN}"

# Cache message IDs for each position so that updates edit the same message.
_MESSAGE_CACHE: Dict[str, int] = {}


def _send_message(text: str) -> Optional[int]:
    """Send a new Telegram message and return the message ID."""
    if not API_TOKEN or not CHAT_ID:
        # Fail silently if configuration is missing.
        return None
    response = requests.post(
        f"{API_URL}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("result", {}).get("message_id")


def _edit_message(message_id: int, text: str) -> None:
    """Edit an existing Telegram message."""
    if not API_TOKEN or not CHAT_ID:
        return
    response = requests.post(
        f"{API_URL}/editMessageText",
        json={
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=10,
    )
    response.raise_for_status()


def notify_open(position_id: str, text: str) -> None:
    """Notify that a position has been opened."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is None:
        message_id = _send_message(text)
        if message_id is not None:
            _MESSAGE_CACHE[position_id] = message_id
    else:
        _edit_message(message_id, text)


def notify_partial_close(position_id: str, text: str) -> None:
    """Notify about a partial position closure."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is None:
        message_id = _send_message(text)
        if message_id is not None:
            _MESSAGE_CACHE[position_id] = message_id
    else:
        _edit_message(message_id, text)


def notify_close(position_id: str, text: str) -> None:
    """Notify that a position has been fully closed and remove it from cache."""
    message_id = _MESSAGE_CACHE.get(position_id)
    if message_id is not None:
        _edit_message(message_id, text)
        del _MESSAGE_CACHE[position_id]
    else:
        _send_message(text)
