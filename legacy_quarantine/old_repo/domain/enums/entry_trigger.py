"""Модуль проекта."""

from enum import Enum


class EntryTrigger(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    PRICE_CONFIRMATION = "PRICE_CONFIRMATION"
