"""Модуль проекта."""

from enum import Enum


class LiquidityQualityState(str, Enum):
    OK = "ok"
    LOW_QUALITY = "low_quality"
    MISSING = "missing"
