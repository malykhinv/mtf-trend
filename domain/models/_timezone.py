"""Utilities for working with timezone-aware datetimes in domain models."""

from __future__ import annotations

from datetime import datetime

from config.timezone import BELGRADE_TIMEZONE


def ensure_current_timezone(*values: datetime) -> None:
    """Ensure that all provided datetimes use the configured timezone."""

    for value in values:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        tz_key = getattr(value.tzinfo, "key", None)
        if tz_key is None:
            if value.tzinfo != BELGRADE_TIMEZONE:
                raise ValueError("datetime must use Europe/Belgrade timezone")
        elif tz_key != BELGRADE_TIMEZONE.key:
            raise ValueError("datetime must use Europe/Belgrade timezone")


__all__ = ["ensure_current_timezone"]
