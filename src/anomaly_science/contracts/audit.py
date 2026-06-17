from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AuditStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True, slots=True)
class DataQualityRow:
    check_name: str
    status: AuditStatus
    severity: str
    affected_rows: int
    message: str
    symbol: str = ""
    timestamp_ms: int | None = None
    previous_timestamp_ms: int | None = None
    gap_minutes: float | None = None
    technical_noise_shock: bool | None = None
    excluded_from_detector: bool | None = None
    excluded_from_ml_dataset: bool | None = None
    reason: str = ""
    artifact: str = "anomaly_data_quality.csv"

    def __post_init__(self) -> None:
        if not self.check_name:
            raise ValueError("check_name is required")
        if self.affected_rows < 0:
            raise ValueError("affected_rows must be non-negative")
        if not self.severity:
            raise ValueError("severity is required")


@dataclass(frozen=True, slots=True)
class ProtocolAuditRow:
    check_name: str
    status: AuditStatus
    message: str
    artifact: str = "anomaly_protocol_audit.csv"

    def __post_init__(self) -> None:
        if not self.check_name:
            raise ValueError("check_name is required")
        if not self.message:
            raise ValueError("message is required")


@dataclass(frozen=True, slots=True)
class RunConfigRow:
    key: str
    value: str
    source: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("key is required")
        if not self.source:
            raise ValueError("source is required")
