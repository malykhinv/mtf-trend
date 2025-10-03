from __future__ import annotations

import os
from typing import Final

from models.secrets import Secrets


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"Environment variable {name} is required")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"Environment variable {name} must not be empty")
    return stripped


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


SECRETS: Final[Secrets] = Secrets(
    tg_bot_token=_require_env("TG_BOT_TOKEN"),
    binance_api_key=_optional_env("BINANCE_API_KEY"),
    binance_api_secret=_optional_env("BINANCE_API_SECRET"),
    bybit_api_key=_optional_env("BYBIT_API_KEY"),
    bybit_api_secret=_optional_env("BYBIT_API_SECRET"),
)


__all__ = ["SECRETS"]
