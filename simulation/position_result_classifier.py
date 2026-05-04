"""Классификация результатов позиции и расчеты прибыли/убытка."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_result_type import PositionResultType
from domain.models.position import Position
from domain.models.position_result import PositionResult
from domain.value_objects.percentage import Percentage
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class PositionResultClassifier:
    """Формирует итоговые результаты позиции из состояния симулятора."""

    @staticmethod
    def classify_result_type(*, tp1_done: bool, exit_at_breakeven: bool, exit_at_tp2: bool) -> PositionResultType:
        """Определяет тип исхода позиции по её параметрам."""
        if exit_at_tp2:
            return PositionResultType.TP2
        if exit_at_breakeven:
            return PositionResultType.TP1_BE if tp1_done else PositionResultType.BE
        return PositionResultType.SL

    @staticmethod
    def build_result(
            *,
        position: Position,
        exit_price: float,
        exit_timestamp_ms: int,
        result_type: PositionResultType,
        pnl: float,
    ) -> PositionResult:
        """Формирует объект результата классификации позиции."""
        entry_notional = position.entry_price.value * position.size.value
        pnl_percent_value = 0.0 if entry_notional == 0 else (pnl / entry_notional) * 100
        return PositionResult(
            entry_price=position.entry_price,
            exit_price=Price(exit_price),
            entry_timestamp_ms=position.entry_timestamp_ms,
            exit_timestamp_ms=exit_timestamp_ms,
            result_type=result_type,
            pnl=pnl,
            pnl_percent=Percentage(pnl_percent_value),
            breakout_timestamp_ms=position.breakout_timestamp_ms,
            retest_timestamp_ms=position.retest_timestamp_ms,
        )
