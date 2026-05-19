"""Typed contracts shared by anomaly live2 components."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .clock import utc_now_iso, utc_now_ms


class Live2Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Live2Component(StrEnum):
    RUNNER = "runner"
    MARKET_DATA = "market_data"
    STATE = "state"
    SIGNAL = "signal"
    EXECUTION = "execution"
    ARTIFACTS = "artifacts"


@dataclass(slots=True)
class Live2Event:
    """Single append-only live2 audit event."""

    event_type: str
    component: Live2Component
    severity: Live2Severity = Live2Severity.INFO
    symbol: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp_utc: str = field(default_factory=utc_now_iso)
    timestamp_ms: int = field(default_factory=utc_now_ms)


@dataclass(slots=True)
class Live2Readiness:
    """Readiness gates. New entries are allowed only when every gate is true."""

    market_data_ready: bool = False
    decision_latency_ready: bool = False
    artifact_writer_ready: bool = False
    exchange_boundary_ready: bool = False
    position_supervisor_ready: bool = False
    execution_ready: bool = False

    @property
    def new_entries_allowed(self) -> bool:
        return (
            self.market_data_ready
            and self.decision_latency_ready
            and self.artifact_writer_ready
            and self.exchange_boundary_ready
            and self.position_supervisor_ready
            and self.execution_ready
        )

    def as_dict(self) -> dict[str, bool]:
        return {
            "market_data_ready": self.market_data_ready,
            "decision_latency_ready": self.decision_latency_ready,
            "artifact_writer_ready": self.artifact_writer_ready,
            "exchange_boundary_ready": self.exchange_boundary_ready,
            "position_supervisor_ready": self.position_supervisor_ready,
            "execution_ready": self.execution_ready,
            "new_entries_allowed": self.new_entries_allowed,
        }
