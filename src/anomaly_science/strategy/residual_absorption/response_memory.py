"""Resolved-only causal memory of prior residual responses."""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from typing import Sequence

import numpy as np

from anomaly_science.strategy.residual_absorption.residual_response import (
    ResidualResponseProfileRow,
)
from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
)

RESPONSE_MEMORY_SCHEMA_VERSION = "residual_response_memory_v1"


@dataclass(frozen=True, slots=True)
class ResponseMemoryRecord:
    event_id: str
    symbol: str
    event_snapshot_time_ms: int
    impulse_direction: int
    session_seq: int
    initial_direction_adjusted_underreaction_15m: float | None
    resolution_time_ms: int | None
    catchup_residual_change_15m: float | None
    catchup_residual_change_30m: float | None
    catchup_residual_change_60m: float | None
    catchup_residual_change_120m: float | None
    terminal_direction_adjusted_residual_120m: float | None
    maximum_catchup_residual_change_120m: float | None
    zero_crossed_expected_response_120m: bool | None
    time_to_half_catchup_minutes: float | None

    def __post_init__(self) -> None:
        if not self.event_id or not self.symbol:
            raise ValueError("response-memory event_id and symbol are required")
        if self.impulse_direction not in (-1, 1):
            raise ValueError("impulse_direction must be -1 or 1")
        if self.event_snapshot_time_ms < 0:
            raise ValueError("event_snapshot_time_ms must be non-negative")
        resolved_values = (
            self.catchup_residual_change_15m,
            self.catchup_residual_change_30m,
            self.catchup_residual_change_60m,
            self.catchup_residual_change_120m,
            self.terminal_direction_adjusted_residual_120m,
            self.maximum_catchup_residual_change_120m,
            self.zero_crossed_expected_response_120m,
        )
        if self.resolution_time_ms is None:
            if any(value is not None for value in (*resolved_values, self.time_to_half_catchup_minutes)):
                raise ValueError("unresolved response memory cannot expose future outcome fields")
            return
        if self.resolution_time_ms <= self.event_snapshot_time_ms:
            raise ValueError("response resolution must be strictly after event snapshot")
        if (
            self.initial_direction_adjusted_underreaction_15m is None
            or not math.isfinite(self.initial_direction_adjusted_underreaction_15m)
        ):
            raise ValueError("resolved response memory requires finite initial underreaction")
        if any(value is None for value in resolved_values):
            raise ValueError("resolved response memory requires complete outcome fields")
        numeric = (
            self.catchup_residual_change_15m,
            self.catchup_residual_change_30m,
            self.catchup_residual_change_60m,
            self.catchup_residual_change_120m,
            self.terminal_direction_adjusted_residual_120m,
            self.maximum_catchup_residual_change_120m,
        )
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("resolved response outcomes must be finite")
        if self.time_to_half_catchup_minutes is not None:
            if not 0.0 <= self.time_to_half_catchup_minutes <= 120.0:
                raise ValueError("time_to_half_catchup_minutes must be in [0, 120]")


@dataclass(frozen=True, slots=True)
class ResponseMemoryFeatures:
    schema_version: str
    resolved_count_5: int
    resolved_count_10: int
    resolved_count_20: int
    unresolved_prior_count: int
    same_direction_resolved_count_20: int
    same_session_resolved_count_20: int
    same_direction_session_resolved_count_20: int
    median_catchup_residual_change_60m_20: float | None
    positive_catchup_rate_60m_20: float | None
    median_terminal_direction_adjusted_residual_120m_20: float | None
    median_maximum_catchup_120m_20: float | None
    zero_cross_rate_120m_20: float | None
    median_time_to_half_catchup_minutes_20: float | None
    catchup_residual_mad_60m_20: float | None
    recent5_minus_long20_catchup_60m: float | None
    same_direction_median_catchup_60m_20: float | None
    same_session_median_catchup_60m_20: float | None
    same_direction_session_median_catchup_60m_20: float | None
    historical_underreaction_median_20: float | None
    current_underreaction_minus_history_median: float | None
    minutes_since_last_resolution: float | None
    has_resolved_memory: bool


def build_response_memory_features(
    records: Sequence[ResponseMemoryRecord],
    *,
    current: ResidualResponseProfileRow,
) -> ResponseMemoryFeatures:
    """Summarize only outcomes whose resolution was available by current snapshot."""

    snapshot = current.snapshot_time_ms
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(snapshot)
    relevant: list[ResponseMemoryRecord] = []
    unresolved = 0
    for record in records:
        RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(record.event_snapshot_time_ms)
        if record.symbol != current.symbol or record.event_id == current.event_id:
            continue
        if record.event_snapshot_time_ms > snapshot:
            continue
        if record.resolution_time_ms is None or record.resolution_time_ms > snapshot:
            unresolved += 1
            continue
        if record.resolution_time_ms >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms:
            raise ValueError("IS response memory cannot materialize an OOS resolution")
        relevant.append(record)
    relevant.sort(key=lambda item: (int(item.resolution_time_ms or -1), item.event_id))
    long = relevant[-20:]
    recent10 = relevant[-10:]
    recent5 = relevant[-5:]
    same_direction = [item for item in long if item.impulse_direction == current.impulse_direction]
    same_session = [item for item in long if item.session_seq == current.session_seq]
    same_both = [
        item
        for item in long
        if item.impulse_direction == current.impulse_direction
        and item.session_seq == current.session_seq
    ]

    catchup_long = _array(long, "catchup_residual_change_60m")
    catchup_recent = _array(recent5, "catchup_residual_change_60m")
    underreaction = np.asarray(
        [
            item.initial_direction_adjusted_underreaction_15m
            for item in long
            if item.initial_direction_adjusted_underreaction_15m is not None
        ],
        dtype=float,
    )
    historical_underreaction = _median(underreaction)
    current_underreaction = current.direction_adjusted_underreaction_15m
    last_resolution = int(relevant[-1].resolution_time_ms) if relevant else None
    return ResponseMemoryFeatures(
        schema_version=RESPONSE_MEMORY_SCHEMA_VERSION,
        resolved_count_5=len(recent5),
        resolved_count_10=len(recent10),
        resolved_count_20=len(long),
        unresolved_prior_count=unresolved,
        same_direction_resolved_count_20=len(same_direction),
        same_session_resolved_count_20=len(same_session),
        same_direction_session_resolved_count_20=len(same_both),
        median_catchup_residual_change_60m_20=_median(catchup_long),
        positive_catchup_rate_60m_20=_positive_rate(catchup_long),
        median_terminal_direction_adjusted_residual_120m_20=_median(
            _array(long, "terminal_direction_adjusted_residual_120m")
        ),
        median_maximum_catchup_120m_20=_median(
            _array(long, "maximum_catchup_residual_change_120m")
        ),
        zero_cross_rate_120m_20=_bool_rate(
            long,
            "zero_crossed_expected_response_120m",
        ),
        median_time_to_half_catchup_minutes_20=_median(
            _array(long, "time_to_half_catchup_minutes")
        ),
        catchup_residual_mad_60m_20=_mad(catchup_long),
        recent5_minus_long20_catchup_60m=_difference(
            _median(catchup_recent),
            _median(catchup_long),
        ),
        same_direction_median_catchup_60m_20=_median(
            _array(same_direction, "catchup_residual_change_60m")
        ),
        same_session_median_catchup_60m_20=_median(
            _array(same_session, "catchup_residual_change_60m")
        ),
        same_direction_session_median_catchup_60m_20=_median(
            _array(same_both, "catchup_residual_change_60m")
        ),
        historical_underreaction_median_20=historical_underreaction,
        current_underreaction_minus_history_median=(
            None
            if current_underreaction is None or historical_underreaction is None
            else current_underreaction - historical_underreaction
        ),
        minutes_since_last_resolution=(
            None if last_resolution is None else (snapshot - last_resolution) / 60_000.0
        ),
        has_resolved_memory=bool(relevant),
    )


def response_memory_feature_names() -> tuple[str, ...]:
    return tuple(item.name for item in fields(ResponseMemoryFeatures))


def _array(records: Sequence[ResponseMemoryRecord], name: str) -> np.ndarray:
    return np.asarray(
        [float(value) for item in records if (value := getattr(item, name)) is not None],
        dtype=float,
    )


def _median(values: np.ndarray) -> float | None:
    return None if len(values) == 0 else float(np.median(values))


def _mad(values: np.ndarray) -> float | None:
    median = _median(values)
    if median is None:
        return None
    return float(np.median(np.abs(values - median)))


def _positive_rate(values: np.ndarray) -> float | None:
    return None if len(values) == 0 else float(np.mean(values > 0.0))


def _bool_rate(records: Sequence[ResponseMemoryRecord], name: str) -> float | None:
    values = [bool(value) for item in records if (value := getattr(item, name)) is not None]
    return None if not values else float(np.mean(values))


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


__all__ = [
    "RESPONSE_MEMORY_SCHEMA_VERSION",
    "ResponseMemoryFeatures",
    "ResponseMemoryRecord",
    "build_response_memory_features",
    "response_memory_feature_names",
]
