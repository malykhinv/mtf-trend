from dataclasses import dataclass


@dataclass(frozen=True)
class AdminSettings:
    host: str = "127.0.0.1"
    port: int = 8099
    request_timeout_s: float = 5.0


__all__ = ["AdminSettings"]
