"""Модуль проекта."""

from enum import Enum


class SLMode(str, Enum):
    RETEST_EXTREME = "RETEST_EXTREME"
    LEVEL = "LEVEL"
    BREAKOUT_EXTREME = "BREAKOUT_EXTREME"
