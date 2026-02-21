"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.timeframe import Timeframe
from strategy.bee_bite.config import BeeBiteGridMode, BeeBiteProfileId


@dataclass(slots=True)
class StrategyConfig:
    strategy_id: str = "breakout"
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15
    bee_bite_profile: BeeBiteProfileId = "A"
    bee_bite_grid_mode: BeeBiteGridMode = "baseline"
