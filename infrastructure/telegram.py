from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from config.config import AppConfig, TelegramConfig


@dataclass
class TelegramNotifier:
    """Simple Telegram client for sending plain text notifications."""

    token: str
    chat_id: str
    session: requests.Session = field(default_factory=requests.Session)
    timeout: float = 10.0
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    @classmethod
    def from_config(cls, config: AppConfig) -> "TelegramNotifier":
        return cls(token=config.telegram.token, chat_id=config.telegram.chat_id)

    @classmethod
    def from_settings(cls, settings: TelegramConfig) -> "TelegramNotifier":
        return cls(token=settings.token, chat_id=settings.chat_id)

    def send(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message}
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:  # pragma: no cover - network interaction
            self.logger.warning("Не удалось отправить сообщение в Telegram: %s", exc)


__all__ = ["TelegramNotifier"]
