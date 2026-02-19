"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class TradeSignal:
    formation_timestamp_ms: int
    entry_price: Price
    entry_timestamp_ms: int
    stop_loss: Price
    take_profit_1: Price
    take_profit_2: Price
    position_side: PositionSide
    symbol: str

    # region Приватные
    def __post_init__(self) -> None:
        if self.formation_timestamp_ms is None:
            msg = "Trade signal formation_timestamp_ms is required."
            raise ValueError(msg)

        if self.formation_timestamp_ms < 0:
            msg = "Trade signal formation_timestamp_ms must be >= 0."
            raise ValueError(msg)

        if self.entry_timestamp_ms is None:
            msg = "Trade signal entry_timestamp_ms is required."
            raise ValueError(msg)

        if not self.symbol:
            msg = "Trade signal symbol is required."
            raise ValueError(msg)

        if self.position_side == PositionSide.LONG:
            if not (self.stop_loss.value < self.entry_price.value < self.take_profit_1.value):
                msg = "Invalid LONG signal levels: stop < entry < tp1 is required."
                raise ValueError(msg)
        else:
            if not (self.stop_loss.value > self.entry_price.value > self.take_profit_1.value):
                msg = "Invalid SHORT signal levels: stop > entry > tp1 is required."
                raise ValueError(msg)

        if self.position_side == PositionSide.LONG and self.take_profit_2.value < self.take_profit_1.value:
            msg = "For LONG, take_profit_2 must be >= take_profit_1."
            raise ValueError(msg)

        if self.position_side == PositionSide.SHORT and self.take_profit_2.value > self.take_profit_1.value:
            msg = "For SHORT, take_profit_2 must be <= take_profit_1."
            raise ValueError(msg)
    # endregion Приватные
