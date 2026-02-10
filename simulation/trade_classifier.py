"""Классификация результатов сделки и расчеты прибыли/убытка."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums.trade_result_type import TradeResultType
from domain.models.position import Position
from domain.models.trade_result import TradeResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class TradeClassifier:
    """Формирует итоговые результаты сделки из состояния симулятора."""

    @staticmethod
    def classify_result_type(*, tp1_done: bool, exit_at_breakeven: bool, exit_at_tp2: bool) -> TradeResultType:
        """Определяет тип исхода сделки по её параметрам."""
        if exit_at_tp2:
            return TradeResultType.TP2
        if exit_at_breakeven:
            return TradeResultType.TP1_BE if tp1_done else TradeResultType.BE
        return TradeResultType.SL

    @staticmethod
    def build_result(
            *,
        position: Position,
        exit_price: float,
        exit_time: datetime,
        result_type: TradeResultType,
        pnl: float,
    ) -> TradeResult:
        """Формирует объект результата классификации сделки."""
        entry_notional = position.entry_price.value * position.size.value
        pnl_percent_value = 0.0 if entry_notional == 0 else (pnl / entry_notional) * 100
        return TradeResult(
            entry_price=position.entry_price,
            exit_price=Price(exit_price),
            entry_time=position.entry_time,
            exit_time=exit_time,
            result_type=result_type,
            pnl=pnl,
            pnl_percent=Percentage(pnl_percent_value),
        )
