from __future__ import annotations

import secrets
import uuid


def uuid_str() -> str:
    return str(uuid.uuid4())


def short_id(length: int = 12) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
