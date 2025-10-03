from __future__ import annotations

from typing import Final
from zoneinfo import ZoneInfo

CURRENT_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Europe/Belgrade")

__all__ = ["CURRENT_TIMEZONE"]
