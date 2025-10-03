from dataclasses import dataclass


@dataclass(frozen=True)
class Secrets:
    tg_bot_token: str


__all__ = ["Secrets"]
