"""Lock helpers for thread-safe operations."""
from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator


@contextmanager
def locked(lock: Lock) -> Iterator[None]:
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
