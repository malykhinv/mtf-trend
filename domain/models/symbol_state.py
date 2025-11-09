from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from .active_order import ActiveOrder
from .band import Band
from .level import Level
from .order_params import OrderParams
from .order_role import OrderRole
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
    last_band: Optional[Band]
    l_pullback: Optional[float]
    h_main: Optional[float]
    active_orders: Dict[OrderRole, ActiveOrder] = field(default_factory=dict)
    last_order_params: Optional[OrderParams] = None
    open_quantity: float = 0.0


__all__ = ["SymbolState"]
