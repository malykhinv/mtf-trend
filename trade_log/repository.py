"""Repository protocol for persisting trade records."""

from __future__ import annotations

from typing import Protocol, Sequence

from .models import TradeRecord


class TradeLogRepository(Protocol):
    """Persistence interface for trade records."""

    def append(self, record: TradeRecord) -> None:
        """Persist a single trade record."""

    def fetch_all(self) -> Sequence[TradeRecord]:
        """Return the stored trade records in chronological order."""
