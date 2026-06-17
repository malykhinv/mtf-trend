"""Модуль проекта."""

from data.quality.data_validator import DataValidator
from data.quality.deduplicator import Deduplicator
from data.quality.gap_detector import GapDetector

__all__ = ["DataValidator", "Deduplicator", "GapDetector"]
