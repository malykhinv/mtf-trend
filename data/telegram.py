from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.models import TelegramSettings
from config.secrets import SECRETS


@dataclass(slots=True)
class TelegramClient:
    token: str
    chat_id: Optional[int]
    silent: bool
    request_timeout: float = 10.0
    max_retries: int = 3
    retry_delay: float = 0.5
    retry_backoff: float = 2.0

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
        retries_remaining = self.max_retries
        delay = max(0.0, float(self.retry_delay))
        backoff = max(1.0, float(self.retry_backoff))
        while True:
            try:
                with urlopen(request, timeout=self.request_timeout):
                    return
            except HTTPError:
                raise
            except (
                URLError,
                RemoteDisconnected,
                TimeoutError,
                socket.timeout,
                ConnectionError,
            ):
                if retries_remaining <= 0:
                    raise
                if delay > 0.0:
                    time.sleep(delay)
                retries_remaining -= 1
                delay *= backoff


def create_client(settings: TelegramSettings) -> TelegramClient:
    return TelegramClient(
        token=SECRETS.tg_bot_token,
        chat_id=settings.chat_id,
        silent=settings.silent,
    )


__all__ = ["TelegramClient", "create_client"]
