"""Strategy layer configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import tzinfo

from constants import DEFAULT_TIMEFRAME, DEFAULT_TIMEZONE
from domain.enums.timeframe import Timeframe
from utils.formatters import resolve_timezone


@dataclass(slots=True)
class StrategyConfig:
    timezone: str = DEFAULT_TIMEZONE
    default_timeframe: Timeframe = DEFAULT_TIMEFRAME
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15

    @property
    def tzinfo(self) -> tzinfo:
        return resolve_timezone(self.timezone)
