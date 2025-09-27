"""Notifier interface used by the orchestrator."""
from __future__ import annotations

from typing import Protocol

from bot.domain.models.signal import Signal


class Notifier(Protocol):
    def send_signal(self, signal: Signal) -> None:  # pragma: no cover - interface definition
        ...
