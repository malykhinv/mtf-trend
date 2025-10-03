"""Timezone-aware datetime helpers."""

from __future__ import annotations

from datetime import datetime

from config.timezone import BELGRADE_TIMEZONE


def get_current_time() -> datetime:
    current: datetime = datetime.now(BELGRADE_TIMEZONE)
    return current


__all__ = ["get_current_time"]
