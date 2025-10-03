"""Configuration model for Telegram settings."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TelegramSettings:
    chat_id: Optional[int]
    silent: bool
    uptick_cooldown_min: int


__all__ = ["TelegramSettings"]
