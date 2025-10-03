from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config.timezone import BELGRADE_TIMEZONE


def ensure_current_timezone(*values: datetime) -> None:
    for value in values:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        tzinfo = value.tzinfo
        match tzinfo:
            case ZoneInfo(key=key):
                if key != BELGRADE_TIMEZONE.key:
                    raise ValueError("datetime must use Europe/Belgrade timezone")
            case _:
                if tzinfo != BELGRADE_TIMEZONE:
                    raise ValueError("datetime must use Europe/Belgrade timezone")


__all__ = ["ensure_current_timezone"]
