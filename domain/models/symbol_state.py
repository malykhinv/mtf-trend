from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .level import Level
from .pump import Pump
from .scenario_status import ScenarioStatus


@dataclass
class SymbolState:
    symbol: str
    status: ScenarioStatus
    has_open_position: bool
    cooldown_until: Optional[datetime]
    last_pump: Optional[Pump]
    last_level: Optional[Level]
    l_pullback: Optional[float]
    h_main: Optional[float]


__all__ = ["SymbolState"]
