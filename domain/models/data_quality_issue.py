"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.data_quality_severity import DataQualitySeverity
from domain.enums.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    symbol: str
    timeframe: Timeframe
    issue_type: str
    severity: DataQualitySeverity
    timestamp: int
    description: str

    # region Приватные
    def __post_init__(self) -> None:
        if not self.symbol:
            msg = "DataQualityIssue symbol is required."
            raise ValueError(msg)

        if not self.issue_type:
            msg = "DataQualityIssue issue_type is required."
            raise ValueError(msg)

        if self.timestamp is None:
            msg = "DataQualityIssue timestamp is required."
            raise ValueError(msg)

        if self.timestamp < 0:
            msg = "DataQualityIssue timestamp must be >= 0."
            raise ValueError(msg)

        if not self.description:
            msg = "DataQualityIssue description is required."
            raise ValueError(msg)
    # endregion Приватные
