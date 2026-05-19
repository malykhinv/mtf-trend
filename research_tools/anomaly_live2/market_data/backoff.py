"""Reconnect backoff helpers for live2 market-data sockets."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(slots=True)
class Live2ReconnectBackoff:
    """Small exponential backoff with bounded jitter.

    The object is intentionally stateful per WebSocket source/shard. It prevents
    tight reconnect loops during exchange outages or temporary IP throttling while
    still resetting quickly after a healthy connection.
    """

    initial_seconds: float = 1.0
    max_seconds: float = 60.0
    multiplier: float = 2.0
    jitter_fraction: float = 0.2
    _attempt: int = 0

    def __post_init__(self) -> None:
        if self.initial_seconds <= 0:
            raise ValueError("initial_seconds must be > 0")
        if self.max_seconds < self.initial_seconds:
            raise ValueError("max_seconds must be >= initial_seconds")
        if self.multiplier < 1.0:
            raise ValueError("multiplier must be >= 1")
        if self.jitter_fraction < 0:
            raise ValueError("jitter_fraction must be >= 0")

    @property
    def attempt(self) -> int:
        return self._attempt

    def reset(self) -> None:
        self._attempt = 0

    def next_delay_seconds(self) -> float:
        exponent = max(0, self._attempt)
        base = min(self.max_seconds, self.initial_seconds * (self.multiplier ** exponent))
        self._attempt += 1
        if self.jitter_fraction <= 0:
            return float(base)
        jitter = base * min(1.0, self.jitter_fraction)
        return max(0.0, float(base + random.uniform(-jitter, jitter)))
