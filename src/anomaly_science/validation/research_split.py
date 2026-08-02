"""Explicit calendar research boundaries with a locked OOS access contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Literal

from anomaly_science.contracts.time import validate_timestamp_ms

ResearchPartition = Literal["before_research", "is", "oos", "after_research"]


class ResearchSplitError(ValueError):
    """Raised when a run crosses its registered research boundary."""


def _date_start_ms(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp() * 1_000)


@dataclass(frozen=True, slots=True)
class CalendarResearchSplit:
    split_version: str
    is_start: date
    oos_start: date
    oos_end_exclusive: date

    def __post_init__(self) -> None:
        if not self.split_version:
            raise ResearchSplitError("split_version is required")
        if not self.is_start < self.oos_start < self.oos_end_exclusive:
            raise ResearchSplitError("dates must satisfy is_start < oos_start < oos_end_exclusive")

    @property
    def is_start_time_ms(self) -> int:
        return _date_start_ms(self.is_start)

    @property
    def oos_start_time_ms(self) -> int:
        return _date_start_ms(self.oos_start)

    @property
    def oos_end_time_ms_exclusive(self) -> int:
        return _date_start_ms(self.oos_end_exclusive)

    @property
    def is_max_input_time_ms_exclusive(self) -> int:
        return self.oos_start_time_ms

    def partition_for_timestamp_ms(self, timestamp_ms: int) -> ResearchPartition:
        validate_timestamp_ms(timestamp_ms, field_name="timestamp_ms")
        if timestamp_ms < self.is_start_time_ms:
            return "before_research"
        if timestamp_ms < self.oos_start_time_ms:
            return "is"
        if timestamp_ms < self.oos_end_time_ms_exclusive:
            return "oos"
        return "after_research"

    def require_is_timestamp_ms(self, timestamp_ms: int) -> None:
        partition = self.partition_for_timestamp_ms(timestamp_ms)
        if partition != "is":
            raise ResearchSplitError(
                f"IS-only run rejected timestamp in partition={partition}: {timestamp_ms}"
            )

    def require_oos_access(
        self,
        *,
        protocol_freeze_id: str,
        access_approved: bool,
    ) -> None:
        if not protocol_freeze_id:
            raise ResearchSplitError("protocol_freeze_id is required before OOS access")
        if not access_approved:
            raise ResearchSplitError("explicit OOS access approval is required")


__all__ = [
    "CalendarResearchSplit",
    "ResearchPartition",
    "ResearchSplitError",
]
