"""Модель данных для визуализации завершенной сделки."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide


@dataclass(frozen=True, slots=True)
class TradePlotSpan:
    symbol: str
    side: PositionSide
    level_start_timestamp_ms: int
    entry_timestamp_ms: int
    exit_timestamp_ms: int
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    result_type: str
