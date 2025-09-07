"""Utility helpers for application modules."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Callable


async def _with_backoff(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute ``func`` with exponential backoff on failure.

    The ``func`` is expected to be a synchronous callable. Any exception will
    trigger a retry with exponential delay capped at 60 seconds. Jitter of
    ±20% is applied to each delay.
    """

    delay = 1.0
    while True:
        try:
            return func(*args, **kwargs)
        except Exception:
            await asyncio.sleep(delay * random.uniform(0.8, 1.2))
            delay = min(delay * 2, 60.0)


__all__ = ["_with_backoff"]

