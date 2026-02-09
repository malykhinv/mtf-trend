"""Data quality issue model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums.data_quality_severity import DataQualitySeverity
from domain.enums.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    symbol: str
    timeframe: Timeframe
    issue_type: str
    severity: DataQualitySeverity
    timestamp: datetime
    description: str

    # область Приватные
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

        if not self.description:
            msg = "DataQualityIssue description is required."
            raise ValueError(msg)
    # конец области Приватные
