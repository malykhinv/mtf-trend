"""Модель диапазона ретеста для визуализации."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from domain.enums.position_side import PositionSide


@dataclass(frozen=True, slots=True)
class RetestPlotSpan:
    """Границы и исход ретеста для отрисовки диагностического прямоугольника."""

    symbol: str
    side: PositionSide
    level_price: float
    retest_low: float
    retest_high: float
    retest_start_time: pd.Timestamp
    retest_end_time: pd.Timestamp
    status: str

