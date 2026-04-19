"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from constants import DEFAULT_BEE_BITE_DEPOSIT, DEFAULT_BEE_BITE_RISK_PCT
from domain.enums.timeframe import Timeframe
from strategy.pno.config import PNO_DEFAULT_ENTRY_TIMEFRAME, PNO_DEFAULT_LEVELS_TIMEFRAME


@dataclass(slots=True)
class StrategyConfig:
    strategy_id: str = "pno"
    levels_timeframe: Timeframe = PNO_DEFAULT_LEVELS_TIMEFRAME
    entry_timeframe: Timeframe = PNO_DEFAULT_ENTRY_TIMEFRAME
    pno_deposit: float = DEFAULT_BEE_BITE_DEPOSIT
    pno_risk_pct: float = 0.05
    pno_entry_confirmation_mode: str | None = None
    pno_category_mode: str = "all"
