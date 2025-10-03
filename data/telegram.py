"""Telegram messaging client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen

from config.models import TelegramSettings
from config.secrets import SECRETS


@dataclass(slots=True)
class TelegramClient:
    """Minimal client for sending messages via Telegram Bot API."""

    token: str
    chat_id: Optional[int]
    silent: bool
    request_timeout: float = 10.0

    def send_message(self, message: str) -> None:
        if self.chat_id is None:
            return
        if not message:
            return
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "disable_notification": self.silent,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url=f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.request_timeout):
            pass


def create_client(settings: TelegramSettings) -> TelegramClient:
    return TelegramClient(
        token=SECRETS.tg_bot_token,
        chat_id=settings.chat_id,
        silent=settings.silent,
    )


__all__ = ["TelegramClient", "create_client"]
