"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums.trade_result_type import TradeResultType
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class TradeResult:
    entry_price: Price
    exit_price: Price
    entry_time: datetime
    exit_time: datetime
    result_type: TradeResultType
    pnl: float
    pnl_percent: Percentage

    # region Приватные
    def __post_init__(self) -> None:
        if self.entry_time is None or self.exit_time is None:
            msg = "Trade result entry/exit time is required."
            raise ValueError(msg)

        if self.exit_time < self.entry_time:
            msg = "Trade result exit_time cannot be earlier than entry_time."
            raise ValueError(msg)
    # endregion Приватные
