"""Модуль проекта."""

from enum import Enum


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
