from __future__ import annotations

from typing import Final
from zoneinfo import ZoneInfo

BELGRADE_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Europe/Belgrade")

__all__ = ["BELGRADE_TIMEZONE"]
