"""Модель диапазона ретеста для визуализации."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide


@dataclass(frozen=True, slots=True)
class RetestPlotSpan:
    """Границы и исход ретеста для отрисовки диагностического прямоугольника."""

    symbol: str
    side: PositionSide
    level_start_timestamp_ms: int
    level_price: float
    retest_low: float
    retest_high: float
    retest_start_timestamp_ms: int
    retest_end_timestamp_ms: int
    status: str
