"""Models representing trading positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_current_timezone
from .enums import Exchange, MarginMode, Side


@dataclass(frozen=True, slots=True)
class Position:
    """Open derivatives position tracked by the bot."""

    exchange: Exchange
    symbol: str
    side: Side
    entry_price: float
    quantity: float
    leverage: int
    margin_mode: MarginMode
    unrealized_pnl: float
    opened_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        ensure_current_timezone(self.opened_at, self.updated_at)


__all__ = ["Position"]
