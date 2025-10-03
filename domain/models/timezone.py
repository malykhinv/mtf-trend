from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config.timezone import CURRENT_TIMEZONE


def ensure_current_timezone(*values: datetime) -> None:
    for value in values:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        tzinfo = value.tzinfo
        match tzinfo:
            case ZoneInfo(key=key):
                if key != CURRENT_TIMEZONE.key:
                    raise ValueError(
                        f"datetime must use {CURRENT_TIMEZONE.key} timezone"
                    )
            case _:
                if tzinfo != CURRENT_TIMEZONE:
                    raise ValueError(
                        f"datetime must use {CURRENT_TIMEZONE.key} timezone"
                    )


__all__ = ["ensure_current_timezone"]
