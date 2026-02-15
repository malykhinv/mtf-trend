"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.timeframe import Timeframe


@dataclass(slots=True)
class StrategyConfig:
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15
