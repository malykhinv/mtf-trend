"""Модуль проекта."""

from enum import Enum


class PositionResultType(str, Enum):
    SL = "SL"
    BE = "BE"
    TIME_EXIT_PROFIT = "TIME_EXIT_PROFIT"
    TP1_BE = "TP1_BE"
    TP2 = "TP2"
