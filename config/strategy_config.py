"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from constants import DEFAULT_POSITION_DEPOSIT
from domain.enums.timeframe import Timeframe
DEFAULT_STRATEGY_LEVELS_TIMEFRAME = Timeframe.M5
DEFAULT_STRATEGY_ENTRY_TIMEFRAME = Timeframe.S30


@dataclass(slots=True)
class StrategyConfig:
    strategy_id: str = "pno"
    levels_timeframe: Timeframe = DEFAULT_STRATEGY_LEVELS_TIMEFRAME
    entry_timeframe: Timeframe = DEFAULT_STRATEGY_ENTRY_TIMEFRAME
    pno_deposit: float = DEFAULT_POSITION_DEPOSIT
    pno_risk_pct: float = 0.05
    pno_entry_confirmation_mode: str | None = None
    pno_category_mode: str = "all"
