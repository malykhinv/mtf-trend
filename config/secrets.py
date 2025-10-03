"""Secret values loaded from environment variables."""

from __future__ import annotations

import os
from typing import Final

from .models.secrets import Secrets


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"Environment variable {name} is required")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"Environment variable {name} must not be empty")
    return stripped


SECRETS: Final[Secrets] = Secrets(tg_bot_token=_require_env("TG_BOT_TOKEN"))


__all__ = ["SECRETS"]
