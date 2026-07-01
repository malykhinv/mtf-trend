from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np


PUMP_FADE_EVENT_MEMORY_SCHEMA_VERSION = "pump_fade_event_memory_v1"
PUMP_FADE_EVENT_MEMORY_LOOKBACK_MS = 48 * 60 * 60 * 1_000
PUMP_FADE_EVENT_MEMORY_HALF_LIFE_MS = 12 * 60 * 60 * 1_000
PUMP_FADE_EVENT_MEMORY_SLOT_COUNT = 3


@dataclass(frozen=True, slots=True)
class PumpEventMemoryRecord:
    """A prior event as it may be observed by a later online snapshot.

    Qualification fields are causal from ``qualification_time_ms`` onward.
    Every other event-summary field is embargoed until ``resolution_time_ms``.
    Keeping the embargo in this typed boundary prevents an offline-finalized
    peak from leaking into rows produced before the prior event resolved.
    """

    event_id: str
    chain_id: str
    ignition_time_ms: int
    qualification_time_ms: int
    base_level: float
    qualification_high: float
    qualification_size: float
    resolution_time_ms: int | None
    faded: int | None
    peak_level: float | None
    peak_time_ms: int | None
    resolution_close: float | None
    turnover_to_resolution: float | None
    trade_count_to_resolution: float | None
    average_trade_notional_to_resolution: float | None
    taker_buy_share_to_resolution: float | None
    path_efficiency_to_resolution: float | None
    max_1m_high_return_to_peak: float | None
    max_1m_close_return_to_peak: float | None
    mean_upper_wick_fraction_to_peak: float | None
    max_upper_wick_fraction_to_peak: float | None

    def __post_init__(self) -> None:
        if not self.event_id or not self.chain_id:
            raise ValueError("event-memory identifiers are required")
        if self.ignition_time_ms > self.qualification_time_ms:
            raise ValueError("event qualification cannot precede ignition")
        resolved = self.resolution_time_ms is not None
        resolved_fields = (
            self.faded,
            self.peak_level,
            self.peak_time_ms,
            self.resolution_close,
            self.turnover_to_resolution,
            self.trade_count_to_resolution,
            self.average_trade_notional_to_resolution,
            self.taker_buy_share_to_resolution,
            self.path_efficiency_to_resolution,
            self.max_1m_high_return_to_peak,
            self.max_1m_close_return_to_peak,
            self.mean_upper_wick_fraction_to_peak,
            self.max_upper_wick_fraction_to_peak,
        )
        if resolved and any(value is None for value in resolved_fields):
            raise ValueError("resolved event-memory records require a complete summary")
        if not resolved and any(value is not None for value in resolved_fields):
            raise ValueError("unresolved event-memory records cannot expose finalized fields")
        if resolved:
            assert self.resolution_time_ms is not None
            assert self.peak_time_ms is not None
            if self.faded not in (0, 1):
                raise ValueError("resolved event outcome must be binary")
            if self.peak_time_ms < self.ignition_time_ms:
                raise ValueError("event peak cannot precede ignition")
            if self.resolution_time_ms <= self.peak_time_ms:
                raise ValueError("event resolution must occur strictly after peak")


_SLOT_METRICS: tuple[str, ...] = (
    "faded",
    "age_since_resolution_min",
    "pump_size",
    "resolution_minutes",
    "peak_to_resolution_minutes",
    "fade_depth_from_peak",
    "current_vs_base",
    "current_vs_peak",
    "current_vs_resolution_close",
    "current_anchor_vs_peak",
    "log_turnover",
    "log_trade_count",
    "log_average_trade_notional",
    "taker_buy_share",
    "path_efficiency",
    "max_1m_high_return",
    "max_1m_close_return",
    "mean_upper_wick_fraction",
    "max_upper_wick_fraction",
)


PUMP_FADE_EVENT_MEMORY_FEATURES: tuple[str, ...] = (
    "n_prior_6h",
    "n_prior_12h",
    "n_resolved_prior_24h",
    "n_resolved_prior_48h",
    "n_prior_fades_24h",
    "n_prior_fades_48h",
    "n_prior_continuations_48h",
    "n_unresolved_prior_48h",
    "recency_weighted_prior_fade_rate_48h",
    "mean_prior_pump_size_48h",
    "max_prior_pump_size_48h",
    "mean_prior_resolution_minutes_48h",
    "mean_prior_peak_to_resolution_minutes_48h",
    "mean_prior_fade_depth_from_peak_48h",
    "max_prior_peak_over_current_48h",
    "last_prior_qualification_size",
    "current_pump_size_minus_last_resolved",
    *tuple(
        f"prior_{slot}_{metric}"
        for slot in range(1, PUMP_FADE_EVENT_MEMORY_SLOT_COUNT + 1)
        for metric in _SLOT_METRICS
    ),
)

PUMP_FADE_EVENT_MEMORY_FLAGS: tuple[str, ...] = (
    "has_event_memory_48h",
    *tuple(
        f"has_prior_{slot}_resolved"
        for slot in range(1, PUMP_FADE_EVENT_MEMORY_SLOT_COUNT + 1)
    ),
)

PUMP_FADE_EVENT_MEMORY_AVAILABILITY_FLAGS: dict[str, str] = {
    **{
        name: "has_event_memory_48h"
        for name in (
            "recency_weighted_prior_fade_rate_48h",
            "mean_prior_pump_size_48h",
            "max_prior_pump_size_48h",
            "mean_prior_resolution_minutes_48h",
            "mean_prior_peak_to_resolution_minutes_48h",
            "mean_prior_fade_depth_from_peak_48h",
            "max_prior_peak_over_current_48h",
            "current_pump_size_minus_last_resolved",
        )
    },
    "last_prior_qualification_size": "has_prior_event",
    **{
        f"prior_{slot}_{metric}": f"has_prior_{slot}_resolved"
        for slot in range(1, PUMP_FADE_EVENT_MEMORY_SLOT_COUNT + 1)
        for metric in _SLOT_METRICS
    },
}


def recurrence_chain_id(
    *,
    symbol: str,
    ignition_time_ms: int,
    prior_records: Sequence[PumpEventMemoryRecord],
) -> str:
    """Return a causal 48h connected-component identifier for split isolation."""

    if prior_records:
        previous = prior_records[-1]
        gap = ignition_time_ms - previous.ignition_time_ms
        if 0 <= gap <= PUMP_FADE_EVENT_MEMORY_LOOKBACK_MS:
            return previous.chain_id
    return f"{symbol}:recurrence:{ignition_time_ms}"


def build_event_memory_features(
    records: Sequence[PumpEventMemoryRecord],
    *,
    snapshot_time_ms: int,
    current_base: float,
    current_anchor_high: float,
    current_close: float,
) -> dict[str, float | bool]:
    """Build a bounded causal memory of prior pump outcomes and price structure."""

    qualified = [
        record
        for record in records
        if record.qualification_time_ms <= snapshot_time_ms
        and 0 <= snapshot_time_ms - record.ignition_time_ms <= PUMP_FADE_EVENT_MEMORY_LOOKBACK_MS
    ]
    resolved = [
        record
        for record in qualified
        if record.resolution_time_ms is not None
        and record.resolution_time_ms <= snapshot_time_ms
    ]
    resolved.sort(key=lambda item: (int(item.resolution_time_ms or -1), item.event_id))
    last_qualified = qualified[-1] if qualified else None
    current_pump_size = (current_anchor_high - current_base) / max(current_base, 1e-12)

    def count_since(hours: int) -> int:
        window = hours * 60 * 60 * 1_000
        return sum(snapshot_time_ms - item.ignition_time_ms <= window for item in qualified)

    resolved_24h = [
        item for item in resolved
        if snapshot_time_ms - item.ignition_time_ms <= 24 * 60 * 60 * 1_000
    ]
    sizes = np.asarray([_pump_size(item) for item in resolved], dtype=float)
    resolution_minutes = np.asarray([_resolution_minutes(item) for item in resolved], dtype=float)
    peak_resolution_minutes = np.asarray(
        [_peak_to_resolution_minutes(item) for item in resolved], dtype=float
    )
    fade_depths = np.asarray([_fade_depth(item) for item in resolved], dtype=float)
    if resolved:
        ages = np.asarray(
            [snapshot_time_ms - int(item.resolution_time_ms or 0) for item in resolved],
            dtype=float,
        )
        weights = np.power(0.5, ages / PUMP_FADE_EVENT_MEMORY_HALF_LIFE_MS)
        weighted_fade_rate = float(
            np.average(np.asarray([int(item.faded or 0) for item in resolved]), weights=weights)
        )
        max_peak_over_current = float(
            max(float(item.peak_level) / max(current_close, 1e-12) - 1.0 for item in resolved)
        )
        last_resolved_size = _pump_size(resolved[-1])
    else:
        weighted_fade_rate = math.nan
        max_peak_over_current = math.nan
        last_resolved_size = math.nan

    output: dict[str, float | bool] = {
        "n_prior_6h": float(count_since(6)),
        "n_prior_12h": float(count_since(12)),
        "n_resolved_prior_24h": float(len(resolved_24h)),
        "n_resolved_prior_48h": float(len(resolved)),
        "n_prior_fades_24h": float(sum(item.faded == 1 for item in resolved_24h)),
        "n_prior_fades_48h": float(sum(item.faded == 1 for item in resolved)),
        "n_prior_continuations_48h": float(sum(item.faded == 0 for item in resolved)),
        "n_unresolved_prior_48h": float(len(qualified) - len(resolved)),
        "recency_weighted_prior_fade_rate_48h": weighted_fade_rate,
        "mean_prior_pump_size_48h": _mean_or_nan(sizes),
        "max_prior_pump_size_48h": _max_or_nan(sizes),
        "mean_prior_resolution_minutes_48h": _mean_or_nan(resolution_minutes),
        "mean_prior_peak_to_resolution_minutes_48h": _mean_or_nan(peak_resolution_minutes),
        "mean_prior_fade_depth_from_peak_48h": _mean_or_nan(fade_depths),
        "max_prior_peak_over_current_48h": max_peak_over_current,
        "last_prior_qualification_size": (
            math.nan if last_qualified is None else float(last_qualified.qualification_size)
        ),
        "current_pump_size_minus_last_resolved": (
            math.nan if not math.isfinite(last_resolved_size) else current_pump_size - last_resolved_size
        ),
        "has_event_memory_48h": bool(resolved),
    }
    recent = tuple(reversed(resolved[-PUMP_FADE_EVENT_MEMORY_SLOT_COUNT:]))
    for slot in range(1, PUMP_FADE_EVENT_MEMORY_SLOT_COUNT + 1):
        record = recent[slot - 1] if slot <= len(recent) else None
        output[f"has_prior_{slot}_resolved"] = record is not None
        output.update(
            _slot_features(
                slot,
                record,
                snapshot_time_ms=snapshot_time_ms,
                current_close=current_close,
                current_anchor_high=current_anchor_high,
            )
        )
    return output


def _slot_features(
    slot: int,
    record: PumpEventMemoryRecord | None,
    *,
    snapshot_time_ms: int,
    current_close: float,
    current_anchor_high: float,
) -> dict[str, float]:
    prefix = f"prior_{slot}_"
    if record is None:
        return {prefix + metric: math.nan for metric in _SLOT_METRICS}
    assert record.resolution_time_ms is not None
    assert record.peak_level is not None
    assert record.resolution_close is not None
    return {
        prefix + "faded": float(record.faded),
        prefix + "age_since_resolution_min": (snapshot_time_ms - record.resolution_time_ms) / 60_000.0,
        prefix + "pump_size": _pump_size(record),
        prefix + "resolution_minutes": _resolution_minutes(record),
        prefix + "peak_to_resolution_minutes": _peak_to_resolution_minutes(record),
        prefix + "fade_depth_from_peak": _fade_depth(record),
        prefix + "current_vs_base": current_close / max(record.base_level, 1e-12) - 1.0,
        prefix + "current_vs_peak": current_close / max(record.peak_level, 1e-12) - 1.0,
        prefix + "current_vs_resolution_close": current_close / max(record.resolution_close, 1e-12) - 1.0,
        prefix + "current_anchor_vs_peak": current_anchor_high / max(record.peak_level, 1e-12) - 1.0,
        prefix + "log_turnover": math.log1p(float(record.turnover_to_resolution)),
        prefix + "log_trade_count": math.log1p(float(record.trade_count_to_resolution)),
        prefix + "log_average_trade_notional": math.log1p(float(record.average_trade_notional_to_resolution)),
        prefix + "taker_buy_share": float(record.taker_buy_share_to_resolution),
        prefix + "path_efficiency": float(record.path_efficiency_to_resolution),
        prefix + "max_1m_high_return": float(record.max_1m_high_return_to_peak),
        prefix + "max_1m_close_return": float(record.max_1m_close_return_to_peak),
        prefix + "mean_upper_wick_fraction": float(record.mean_upper_wick_fraction_to_peak),
        prefix + "max_upper_wick_fraction": float(record.max_upper_wick_fraction_to_peak),
    }


def _pump_size(record: PumpEventMemoryRecord) -> float:
    assert record.peak_level is not None
    return record.peak_level / max(record.base_level, 1e-12) - 1.0


def _resolution_minutes(record: PumpEventMemoryRecord) -> float:
    assert record.resolution_time_ms is not None
    return (record.resolution_time_ms - record.ignition_time_ms) / 60_000.0


def _peak_to_resolution_minutes(record: PumpEventMemoryRecord) -> float:
    assert record.resolution_time_ms is not None
    assert record.peak_time_ms is not None
    return (record.resolution_time_ms - record.peak_time_ms) / 60_000.0


def _fade_depth(record: PumpEventMemoryRecord) -> float:
    assert record.peak_level is not None
    assert record.resolution_close is not None
    return record.resolution_close / max(record.peak_level, 1e-12) - 1.0


def _mean_or_nan(values: np.ndarray) -> float:
    return math.nan if values.size == 0 else float(np.mean(values))


def _max_or_nan(values: np.ndarray) -> float:
    return math.nan if values.size == 0 else float(np.max(values))


__all__ = [
    "PUMP_FADE_EVENT_MEMORY_FEATURES",
    "PUMP_FADE_EVENT_MEMORY_AVAILABILITY_FLAGS",
    "PUMP_FADE_EVENT_MEMORY_FLAGS",
    "PUMP_FADE_EVENT_MEMORY_LOOKBACK_MS",
    "PUMP_FADE_EVENT_MEMORY_SCHEMA_VERSION",
    "PUMP_FADE_EVENT_MEMORY_SLOT_COUNT",
    "PumpEventMemoryRecord",
    "build_event_memory_features",
    "recurrence_chain_id",
]
