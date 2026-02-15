"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from constants import DEFAULT_TIMEZONE
from domain.enums.timeframe import Timeframe


@dataclass(slots=True)
class StrategyConfig:
    timezone: str = DEFAULT_TIMEZONE
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15
