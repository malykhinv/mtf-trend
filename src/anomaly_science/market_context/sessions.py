"""Versioned UTC liquidity-session calendar shared by research strategies."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from anomaly_science.contracts.time import validate_timestamp_ms

UTC_SESSION_CALENDAR_VERSION = "utc_liquidity_sessions_v1"
MS_PER_MINUTE = 60_000
MS_PER_HOUR = 3_600_000
MS_PER_DAY = 86_400_000


@dataclass(frozen=True, slots=True)
class UtcSessionBlock:
    seq: int
    name: str
    start_hour: int
    end_hour: int

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError("session seq must be non-negative")
        if not self.name:
            raise ValueError("session name is required")
        if not 0 <= self.start_hour < self.end_hour <= 24:
            raise ValueError("session hours must satisfy 0 <= start < end <= 24")

    @property
    def duration_minutes(self) -> int:
        return (self.end_hour - self.start_hour) * 60

    @property
    def is_overlap(self) -> bool:
        return self.name == "OVERLAP"


UTC_SESSION_BLOCKS: tuple[UtcSessionBlock, ...] = (
    UtcSessionBlock(0, "ASIA", 0, 8),
    UtcSessionBlock(1, "EU", 8, 13),
    UtcSessionBlock(2, "OVERLAP", 13, 16),
    UtcSessionBlock(3, "US", 16, 21),
    UtcSessionBlock(4, "LATE", 21, 24),
)
UTC_SESSION_BY_SEQ = {block.seq: block for block in UTC_SESSION_BLOCKS}
UTC_SESSION_COUNT = len(UTC_SESSION_BLOCKS)


@dataclass(frozen=True, slots=True)
class UtcSessionInstance:
    utc_day: int
    block: UtcSessionBlock
    start_time_ms: int
    end_time_ms: int

    def __post_init__(self) -> None:
        validate_timestamp_ms(self.start_time_ms, field_name="start_time_ms")
        validate_timestamp_ms(self.end_time_ms, field_name="end_time_ms")
        if self.start_time_ms >= self.end_time_ms:
            raise ValueError("session start_time_ms must be before end_time_ms")

    def elapsed_minutes_at(self, snapshot_time_ms: int) -> int:
        validate_timestamp_ms(snapshot_time_ms, field_name="snapshot_time_ms")
        if not self.start_time_ms <= snapshot_time_ms <= self.end_time_ms:
            raise ValueError("snapshot_time_ms is outside the session instance")
        return min(
            self.block.duration_minutes,
            max(0, (snapshot_time_ms - self.start_time_ms) // MS_PER_MINUTE),
        )


def block_seq_for_ms(ts_ms: np.ndarray) -> np.ndarray:
    """Return the session sequence for Unix-ms timestamps."""

    values = np.asarray(ts_ms, dtype=np.int64)
    if bool((values < 0).any()):
        raise ValueError("timestamps must be non-negative Unix milliseconds")
    hour = (values % MS_PER_DAY) // MS_PER_HOUR
    return np.select(
        (hour < 8, hour < 13, hour < 16, hour < 21),
        (0, 1, 2, 3),
        default=4,
    ).astype(np.int64)


def utc_day_for_ms(ts_ms: np.ndarray) -> np.ndarray:
    values = np.asarray(ts_ms, dtype=np.int64)
    if bool((values < 0).any()):
        raise ValueError("timestamps must be non-negative Unix milliseconds")
    return values // MS_PER_DAY


def session_instance_for_ms(timestamp_ms: int) -> UtcSessionInstance:
    validate_timestamp_ms(timestamp_ms, field_name="timestamp_ms")
    day = timestamp_ms // MS_PER_DAY
    seq = int(block_seq_for_ms(np.asarray([timestamp_ms], dtype=np.int64))[0])
    block = UTC_SESSION_BY_SEQ[seq]
    day_start_ms = day * MS_PER_DAY
    return UtcSessionInstance(
        utc_day=day,
        block=block,
        start_time_ms=day_start_ms + block.start_hour * MS_PER_HOUR,
        end_time_ms=day_start_ms + block.end_hour * MS_PER_HOUR,
    )


def session_bounds_ms(*, utc_day: int, seq: int) -> tuple[int, int]:
    if utc_day < 0:
        raise ValueError("utc_day must be non-negative")
    try:
        block = UTC_SESSION_BY_SEQ[seq]
    except KeyError as exc:
        raise ValueError(f"unknown session seq: {seq}") from exc
    day_start_ms = utc_day * MS_PER_DAY
    return (
        day_start_ms + block.start_hour * MS_PER_HOUR,
        day_start_ms + block.end_hour * MS_PER_HOUR,
    )


def predecessor_sequential(day: int, seq: int) -> tuple[int, int]:
    if seq not in UTC_SESSION_BY_SEQ:
        raise ValueError(f"unknown session seq: {seq}")
    if seq == 0:
        return day - 1, UTC_SESSION_COUNT - 1
    return day, seq - 1


def predecessor_same_type(day: int, seq: int) -> tuple[int, int]:
    if seq not in UTC_SESSION_BY_SEQ:
        raise ValueError(f"unknown session seq: {seq}")
    return day - 1, seq


__all__ = [
    "MS_PER_DAY",
    "MS_PER_HOUR",
    "MS_PER_MINUTE",
    "UTC_SESSION_BLOCKS",
    "UTC_SESSION_BY_SEQ",
    "UTC_SESSION_CALENDAR_VERSION",
    "UTC_SESSION_COUNT",
    "UtcSessionBlock",
    "UtcSessionInstance",
    "block_seq_for_ms",
    "predecessor_same_type",
    "predecessor_sequential",
    "session_bounds_ms",
    "session_instance_for_ms",
    "utc_day_for_ms",
]
