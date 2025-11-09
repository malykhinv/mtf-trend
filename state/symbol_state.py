from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict

from domain.models import ActiveOrder, OrderParams, OrderRole, ScenarioStatus


@dataclass
class SymbolState:
    """Mutable state maintained for each trading symbol."""

    symbol: str
    status: ScenarioStatus = ScenarioStatus.IDLE
    has_open_position: bool = False
    cooldown_until: datetime | None = None
    h_main: float | None = None
    l_pullback: float | None = None
    level_top: float | None = None
    level_low: float | None = None
    active_orders: Dict[OrderRole, ActiveOrder] = field(default_factory=dict)
    last_order_params: OrderParams | None = None
    open_quantity: float = 0.0

    def update_levels(self, *, level_top: float | None, level_low: float | None) -> None:
        self.level_top = level_top
        self.level_low = level_low

    def update_pump(self, *, h_main: float | None, l_pullback: float | None) -> None:
        self.h_main = h_main
        self.l_pullback = l_pullback

    def mark_monitoring(self) -> None:
        self.status = ScenarioStatus.MONITORING

    def mark_active(self) -> None:
        self.status = ScenarioStatus.ACTIVE

    def mark_idle(self) -> None:
        self.status = ScenarioStatus.IDLE

    def mark_cooldown(self, until: datetime) -> None:
        self.cooldown_until = until
        self.status = ScenarioStatus.COOLDOWN

    def clear_cooldown(self) -> None:
        self.cooldown_until = None
        if not self.has_open_position:
            self.status = ScenarioStatus.IDLE

    def mark_position_open(self, quantity: float) -> None:
        self.open_quantity = quantity
        self.has_open_position = quantity > 0
        if self.has_open_position:
            self.status = ScenarioStatus.ACTIVE

    def reduce_position(self, quantity: float) -> None:
        self.open_quantity = max(self.open_quantity - quantity, 0.0)
        self.has_open_position = self.open_quantity > 0

    def reset_orders(self) -> None:
        self.active_orders.clear()
        self.last_order_params = None

    def reset_position(self) -> None:
        self.open_quantity = 0.0
        self.has_open_position = False

    def reset_all(self) -> None:
        self.reset_orders()
        self.reset_position()
        self.mark_idle()


__all__ = ["SymbolState"]
