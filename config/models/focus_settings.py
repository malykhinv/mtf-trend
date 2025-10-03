"""Configuration model for focus settings."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FocusSettings:
    defocus_timeout_s: int


__all__ = ["FocusSettings"]
