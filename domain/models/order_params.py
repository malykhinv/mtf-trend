from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderParams:
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    position_size: float


__all__ = ["OrderParams"]
