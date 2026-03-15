"""Shared stage-3 helpers for bee_bite reclaim validation."""

from __future__ import annotations

_EPSILON = 1e-12
_HOLD_RATIO = 0.4


def resolve_half_hold_price(*, high_pump: float | None, low_before_pump: float | None) -> float | None:
    if high_pump is None or low_before_pump is None:
        return None
    peak = float(high_pump)
    base = float(low_before_pump)
    if peak <= base:
        return None
    return base + ((peak - base) * _HOLD_RATIO)


def is_move_pct_smaller_than_range_pct(
    *,
    move_size: float,
    reclaim_boundary: float,
    range_high: float | None,
) -> bool:
    if range_high is None:
        return False
    boundary = float(reclaim_boundary)
    ceiling = float(range_high)
    if boundary <= 0.0 or ceiling <= boundary:
        return False
    move_pct = max(float(move_size), 0.0) / max(boundary, _EPSILON)
    range_pct = (ceiling - boundary) / max(boundary, _EPSILON)
    return move_pct < range_pct


def resolve_below_range_span(*, lowest_break: float | None, below_range_high: float | None) -> float | None:
    if lowest_break is None or below_range_high is None:
        return None
    low = float(lowest_break)
    high = float(below_range_high)
    if high <= low:
        return 0.0
    return high - low
