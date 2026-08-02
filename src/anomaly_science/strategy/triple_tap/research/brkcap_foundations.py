"""Typed, outcome-free contracts for BRK/CAP pump and level geometry.

This module deliberately knows nothing about PnL, labels, or the legacy
detector.  It answers the earlier scientific question: does a proposed setup
actually contain a sleep -> pump transition and a causally valid resistance
level of the declared family?
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

from anomaly_science.strategy.triple_tap.research.brkcap_geometry import analyze_level_geometry


BRKCAP_FOUNDATION_POLICY_VERSION = "brkcap_foundations_v2_2026-07-13"


class SetupFamily(StrEnum):
    BREAKOUT = "breakout"
    CAP = "cap"


class FoundationViolation(StrEnum):
    INVALID_TIME_ORDER = "invalid_time_order"
    INSUFFICIENT_SLEEP = "insufficient_sleep"
    SLEEP_RANGE_TOO_WIDE = "sleep_range_too_wide"
    SLEEP_DIRECTIONAL_DRIFT_TOO_LARGE = "sleep_directional_drift_too_large"
    DUMP_AT_PUMP_START = "dump_at_pump_start"
    PUMP_RISE_TOO_SMALL = "pump_rise_too_small"
    PUMP_RETRACE_TOO_LARGE = "pump_retrace_too_large"
    PUMP_TOO_SHORT = "pump_too_short"
    PUMP_ONE_BAR_DOMINATED = "pump_one_bar_dominated"
    PUMP_PATH_TOO_CHOPPY = "pump_path_too_choppy"
    PUMP_WICK_TOO_LARGE = "pump_wick_too_large"
    MISSING_LEVEL = "missing_level"
    BREAKOUT_LEVEL_NOT_PUMP_HIGH = "breakout_level_not_pump_high"
    CAP_LEVEL_NOT_AFTER_PULLBACK = "cap_level_not_after_pullback"
    CAP_FORMED_TOO_LATE = "cap_formed_too_late"
    POST_CULMINATION_DUMP = "post_culmination_dump"
    CAP_INSUFFICIENT_LEVEL_TOUCHES = "cap_insufficient_level_touches"
    CAP_DESCENDING_SUPPORT = "cap_descending_support"
    CAP_RETRACES_NOT_COMPRESSING = "cap_retraces_not_compressing"
    LEVEL_ALREADY_BROKEN = "level_already_broken"


@dataclass(frozen=True, slots=True)
class FoundationPolicy:
    """Pre-registered price-geometry policy; no outcome-derived thresholds."""

    sleep_bars: int = 120
    min_sleep_bars: int = 24
    max_sleep_range_pct: float = 0.10
    max_sleep_directional_drift_pct: float = 0.05
    max_start_below_sleep_median_pct: float = 0.12
    min_pump_rise_pct: float = 0.10
    max_pump_retrace_share: float = 0.35
    min_pump_bars: int = 3
    min_pump_path_efficiency: float = 0.45
    max_pump_single_bar_range_share: float = 0.70
    max_pump_upper_wick_share: float = 0.75
    family_anchor_tolerance_pct: float = 0.005
    max_cap_start_delay_bars: int = 48
    max_post_culmination_drawdown_share: float = 0.35
    min_cap_level_touches: int = 3


FOUNDATION_POLICY = FoundationPolicy()


# These rules establish whether a detector row has a coherent causal object at
# all.  The newer pump-shape and pairwise-retrace measurements are deliberately
# not in this set: they have expert motivation, but no independently calibrated
# operating threshold yet.  Treating them as a hard gate would confuse a
# measurement hypothesis with an established inclusion criterion.
REVIEW_BLOCKING_VIOLATIONS = frozenset(
    {
        FoundationViolation.INVALID_TIME_ORDER,
        FoundationViolation.INSUFFICIENT_SLEEP,
        FoundationViolation.SLEEP_RANGE_TOO_WIDE,
        FoundationViolation.DUMP_AT_PUMP_START,
        FoundationViolation.PUMP_RISE_TOO_SMALL,
        FoundationViolation.PUMP_RETRACE_TOO_LARGE,
        FoundationViolation.MISSING_LEVEL,
        FoundationViolation.BREAKOUT_LEVEL_NOT_PUMP_HIGH,
        FoundationViolation.CAP_LEVEL_NOT_AFTER_PULLBACK,
        FoundationViolation.LEVEL_ALREADY_BROKEN,
    }
)


@dataclass(frozen=True, slots=True)
class CandleSeries:
    timestamp: NDArray[np.int64]
    open: NDArray[np.float64]
    high: NDArray[np.float64]
    low: NDArray[np.float64]
    close: NDArray[np.float64]

    def __post_init__(self) -> None:
        lengths = {len(self.timestamp), len(self.open), len(self.high), len(self.low), len(self.close)}
        if len(lengths) != 1:
            raise ValueError("candle arrays must have equal lengths")
        if len(self.timestamp) and np.any(np.diff(self.timestamp) <= 0):
            raise ValueError("candle timestamps must be strictly increasing")

    def exact_index(self, timestamp_ms: int) -> int | None:
        pos = int(np.searchsorted(self.timestamp, timestamp_ms, side="left"))
        if pos >= len(self.timestamp) or int(self.timestamp[pos]) != timestamp_ms:
            return None
        return pos


@dataclass(frozen=True, slots=True)
class PumpTransition:
    start_ms: int
    start_price: float
    culmination_ms: int
    culmination_price: float


@dataclass(frozen=True, slots=True)
class ResistanceLevel:
    start_ms: int
    end_ms: int
    price: float


@dataclass(frozen=True, slots=True)
class BrkCapStructure:
    family: SetupFamily
    pump: PumpTransition
    level: ResistanceLevel | None


@dataclass(frozen=True, slots=True)
class FoundationMetrics:
    sleep_bars: int
    sleep_range_pct: float
    sleep_directional_drift_pct: float
    start_below_sleep_median_pct: float
    pump_rise_pct: float
    pump_retrace_share: float
    pump_bars: int
    pump_path_efficiency: float
    pump_single_bar_range_share: float
    pump_upper_wick_share: float
    first_close_above_level_ms: int | None
    cap_start_delay_bars: int | None
    post_culmination_drawdown_share: float | None
    level_touch_count: int | None
    support_slope_per_bar_pct: float | None
    retrace_depths_non_increasing: bool | None


@dataclass(frozen=True, slots=True)
class FoundationAudit:
    policy_version: str
    metrics: FoundationMetrics
    violations: tuple[FoundationViolation, ...]

    @property
    def accepted(self) -> bool:
        return not self.violations

    @property
    def review_eligible(self) -> bool:
        """Whether the row is structurally coherent enough for expert review."""

        return not any(violation in REVIEW_BLOCKING_VIOLATIONS for violation in self.violations)

    @property
    def quality_flags(self) -> tuple[FoundationViolation, ...]:
        """Uncalibrated, causal diagnostics; visible but not hard-gated."""

        return tuple(violation for violation in self.violations if violation not in REVIEW_BLOCKING_VIOLATIONS)


def _maximum_close_drawdown(close: NDArray[np.float64]) -> float:
    if len(close) < 2:
        return 0.0
    running_high = np.maximum.accumulate(close)
    return float(np.max(running_high - close))


def first_close_above_level_ms(
    candles: CandleSeries,
    *,
    level_price: float,
    level_start_ms: int,
) -> int | None:
    """Return the first *later* candle close above a resistance level."""

    start = int(np.searchsorted(candles.timestamp, level_start_ms, side="right"))
    if start >= len(candles.timestamp):
        return None
    hit = np.flatnonzero(candles.close[start:] > level_price)
    return int(candles.timestamp[start + int(hit[0])]) if len(hit) else None


def audit_structure(
    candles: CandleSeries,
    structure: BrkCapStructure,
    *,
    policy: FoundationPolicy = FOUNDATION_POLICY,
) -> FoundationAudit:
    """Audit observable geometry without consulting outcomes or review text."""

    pump = structure.pump
    start_idx = candles.exact_index(pump.start_ms)
    culmination_idx = candles.exact_index(pump.culmination_ms)
    violations: list[FoundationViolation] = []

    valid_order = (
        start_idx is not None
        and culmination_idx is not None
        and start_idx < culmination_idx
        and pump.start_price > 0
        and pump.culmination_price > 0
    )
    if not valid_order:
        violations.append(FoundationViolation.INVALID_TIME_ORDER)
        metrics = FoundationMetrics(0, np.nan, np.nan, np.nan, np.nan, np.nan, 0, np.nan, np.nan, np.nan, None, None, None, None, None, None)
        return FoundationAudit(BRKCAP_FOUNDATION_POLICY_VERSION, metrics, tuple(violations))

    assert start_idx is not None and culmination_idx is not None
    sleep_start = max(0, start_idx - policy.sleep_bars)
    sleep_count = start_idx - sleep_start
    sleep_low = float(np.min(candles.low[sleep_start:start_idx])) if sleep_count else np.nan
    sleep_high = float(np.max(candles.high[sleep_start:start_idx])) if sleep_count else np.nan
    sleep_median = float(np.median(candles.close[sleep_start:start_idx])) if sleep_count else np.nan
    sleep_range_pct = (sleep_high - sleep_low) / sleep_low if sleep_low > 0 else np.nan
    sleep_directional_drift_pct = (
        abs(float(candles.close[start_idx - 1]) - float(candles.close[sleep_start])) / float(candles.close[sleep_start])
        if sleep_count and float(candles.close[sleep_start]) > 0.0
        else np.nan
    )
    start_below_sleep_median_pct = (
        max(0.0, (sleep_median - pump.start_price) / sleep_median) if sleep_median > 0 else np.nan
    )
    pump_rise = pump.culmination_price - pump.start_price
    pump_rise_pct = pump_rise / pump.start_price
    pump_close = candles.close[start_idx : culmination_idx + 1]
    pump_retrace_share = _maximum_close_drawdown(pump_close) / pump_rise if pump_rise > 0 else np.inf
    pump_bars = culmination_idx - start_idx
    pump_path = float(np.sum(np.abs(np.diff(pump_close))))
    pump_path_efficiency = (float(pump_close[-1]) - float(pump_close[0])) / pump_path if pump_path > 0.0 else 0.0
    pump_high = candles.high[start_idx : culmination_idx + 1]
    pump_low = candles.low[start_idx : culmination_idx + 1]
    pump_open = candles.open[start_idx : culmination_idx + 1]
    pump_range = pump_high - pump_low
    pump_single_bar_range_share = float(np.max(pump_range) / pump_rise) if pump_rise > 0.0 else np.inf
    upper_wick = pump_high - np.maximum(pump_open, pump_close)
    valid_ranges = pump_range > 0.0
    pump_upper_wick_share = float(np.max(upper_wick[valid_ranges] / pump_range[valid_ranges])) if np.any(valid_ranges) else 0.0

    if sleep_count < policy.min_sleep_bars:
        violations.append(FoundationViolation.INSUFFICIENT_SLEEP)
    if not np.isfinite(sleep_range_pct) or sleep_range_pct > policy.max_sleep_range_pct:
        violations.append(FoundationViolation.SLEEP_RANGE_TOO_WIDE)
    if not np.isfinite(sleep_directional_drift_pct) or sleep_directional_drift_pct > policy.max_sleep_directional_drift_pct:
        violations.append(FoundationViolation.SLEEP_DIRECTIONAL_DRIFT_TOO_LARGE)
    if (
        not np.isfinite(start_below_sleep_median_pct)
        or start_below_sleep_median_pct > policy.max_start_below_sleep_median_pct
    ):
        violations.append(FoundationViolation.DUMP_AT_PUMP_START)
    if not np.isfinite(pump_rise_pct) or pump_rise_pct < policy.min_pump_rise_pct:
        violations.append(FoundationViolation.PUMP_RISE_TOO_SMALL)
    if not np.isfinite(pump_retrace_share) or pump_retrace_share > policy.max_pump_retrace_share:
        violations.append(FoundationViolation.PUMP_RETRACE_TOO_LARGE)
    if pump_bars < policy.min_pump_bars:
        violations.append(FoundationViolation.PUMP_TOO_SHORT)
    if not np.isfinite(pump_path_efficiency) or pump_path_efficiency < policy.min_pump_path_efficiency:
        violations.append(FoundationViolation.PUMP_PATH_TOO_CHOPPY)
    if not np.isfinite(pump_single_bar_range_share) or pump_single_bar_range_share > policy.max_pump_single_bar_range_share:
        violations.append(FoundationViolation.PUMP_ONE_BAR_DOMINATED)
    if not np.isfinite(pump_upper_wick_share) or pump_upper_wick_share > policy.max_pump_upper_wick_share:
        violations.append(FoundationViolation.PUMP_WICK_TOO_LARGE)

    level = structure.level
    first_break_ms: int | None = None
    cap_start_delay_bars: int | None = None
    post_culmination_drawdown_share: float | None = None
    level_touch_count: int | None = None
    support_slope_per_bar_pct: float | None = None
    retrace_depths_non_increasing: bool | None = None
    if level is None:
        violations.append(FoundationViolation.MISSING_LEVEL)
    else:
        first_break_ms = first_close_above_level_ms(
            candles,
            level_price=level.price,
            level_start_ms=level.start_ms,
        )
        tolerance = policy.family_anchor_tolerance_pct
        if structure.family is SetupFamily.BREAKOUT:
            same_time = level.start_ms == pump.culmination_ms
            same_price = abs(level.price - pump.culmination_price) / pump.culmination_price <= tolerance
            if not (same_time and same_price):
                violations.append(FoundationViolation.BREAKOUT_LEVEL_NOT_PUMP_HIGH)
        else:
            cap_start_idx = candles.exact_index(level.start_ms)
            cap_valid = (
                level.start_ms > pump.culmination_ms
                and level.price < pump.culmination_price * (1.0 - tolerance)
                and cap_start_idx is not None
            )
            if not cap_valid:
                violations.append(FoundationViolation.CAP_LEVEL_NOT_AFTER_PULLBACK)
            else:
                assert cap_start_idx is not None
                cap_start_delay_bars = cap_start_idx - culmination_idx
                if cap_start_delay_bars > policy.max_cap_start_delay_bars:
                    violations.append(FoundationViolation.CAP_FORMED_TOO_LATE)
                post_high_close = candles.close[culmination_idx : cap_start_idx + 1]
                post_culmination_drawdown_share = _maximum_close_drawdown(post_high_close) / pump_rise if pump_rise > 0 else np.inf
                if post_culmination_drawdown_share > policy.max_post_culmination_drawdown_share:
                    violations.append(FoundationViolation.POST_CULMINATION_DUMP)
                geometry = analyze_level_geometry(
                    candles,
                    level_price=level.price,
                    start_ms=level.start_ms,
                    end_ms=level.end_ms,
                )
                level_touch_count = len(geometry.touches)
                support_slope_per_bar_pct = geometry.support_slope_per_bar_pct
                retrace_depths_non_increasing = geometry.retrace_depths_non_increasing
                if level_touch_count < policy.min_cap_level_touches:
                    violations.append(FoundationViolation.CAP_INSUFFICIENT_LEVEL_TOUCHES)
                if geometry.ascending_support is not True:
                    violations.append(FoundationViolation.CAP_DESCENDING_SUPPORT)
                if geometry.retrace_depths_non_increasing is not True:
                    violations.append(FoundationViolation.CAP_RETRACES_NOT_COMPRESSING)
        if first_break_ms is not None and first_break_ms < level.end_ms:
            violations.append(FoundationViolation.LEVEL_ALREADY_BROKEN)

    metrics = FoundationMetrics(
        sleep_bars=sleep_count,
        sleep_range_pct=float(sleep_range_pct),
        sleep_directional_drift_pct=float(sleep_directional_drift_pct),
        start_below_sleep_median_pct=float(start_below_sleep_median_pct),
        pump_rise_pct=float(pump_rise_pct),
        pump_retrace_share=float(pump_retrace_share),
        pump_bars=pump_bars,
        pump_path_efficiency=float(pump_path_efficiency),
        pump_single_bar_range_share=float(pump_single_bar_range_share),
        pump_upper_wick_share=float(pump_upper_wick_share),
        first_close_above_level_ms=first_break_ms,
        cap_start_delay_bars=cap_start_delay_bars,
        post_culmination_drawdown_share=post_culmination_drawdown_share,
        level_touch_count=level_touch_count,
        support_slope_per_bar_pct=support_slope_per_bar_pct,
        retrace_depths_non_increasing=retrace_depths_non_increasing,
    )
    return FoundationAudit(BRKCAP_FOUNDATION_POLICY_VERSION, metrics, tuple(dict.fromkeys(violations)))


__all__ = [
    "BRKCAP_FOUNDATION_POLICY_VERSION",
    "FOUNDATION_POLICY",
    "BrkCapStructure",
    "CandleSeries",
    "FoundationAudit",
    "FoundationMetrics",
    "FoundationPolicy",
    "FoundationViolation",
    "PumpTransition",
    "REVIEW_BLOCKING_VIOLATIONS",
    "ResistanceLevel",
    "SetupFamily",
    "audit_structure",
    "first_close_above_level_ms",
]
