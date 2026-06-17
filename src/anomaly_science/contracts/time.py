from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

TimestampMs = int


class TemporalContractError(ValueError):
    """Raised when a row violates the anomaly-science time contract."""


def validate_timestamp_ms(value: int, *, field_name: str) -> int:
    """Validate the canonical timestamp representation: non-negative Unix ms."""
    if not isinstance(value, int):
        raise TypeError(f"{field_name} must be int unix milliseconds, got {type(value).__name__}")
    if value < 0:
        raise TemporalContractError(f"{field_name} must be non-negative, got {value}")
    return value


def datetime_to_utc_ms(value: datetime) -> int:
    """Convert an aware datetime to canonical Unix milliseconds in UTC."""
    if value.tzinfo is None:
        raise TemporalContractError("datetime must be timezone-aware")
    utc_value = value.astimezone(timezone.utc)
    return int(utc_value.timestamp() * 1000)


def utc_ms_to_datetime(value: int) -> datetime:
    """Convert canonical Unix milliseconds to a UTC datetime."""
    validate_timestamp_ms(value, field_name="timestamp_ms")
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class SnapshotTiming:
    """Temporal contract shared by feature/state/future rows.

    Features may only use data available at or before ``snapshot_time_ms``.
    Future labels/paths must start strictly after ``snapshot_time_ms``.
    """

    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int | None = None

    def __post_init__(self) -> None:
        validate_timestamp_ms(self.snapshot_time_ms, field_name="snapshot_time_ms")
        validate_timestamp_ms(self.feature_cutoff_time_ms, field_name="feature_cutoff_time_ms")
        if self.future_start_time_ms is not None:
            validate_timestamp_ms(self.future_start_time_ms, field_name="future_start_time_ms")
        enforce_snapshot_contract(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
            future_start_time_ms=self.future_start_time_ms,
        )


def enforce_snapshot_contract(
    *,
    snapshot_time_ms: int,
    feature_cutoff_time_ms: int,
    future_start_time_ms: int | None = None,
) -> None:
    """Enforce the core no-lookahead contract for one dataset row."""
    if feature_cutoff_time_ms > snapshot_time_ms:
        raise TemporalContractError(
            "feature_cutoff_time_ms must be <= snapshot_time_ms "
            f"({feature_cutoff_time_ms} > {snapshot_time_ms})"
        )
    if future_start_time_ms is not None and future_start_time_ms <= snapshot_time_ms:
        raise TemporalContractError(
            "future_start_time_ms must be > snapshot_time_ms "
            f"({future_start_time_ms} <= {snapshot_time_ms})"
        )
