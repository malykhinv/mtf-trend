"""Causal BRK/CAP resistance and retrace geometry.

The lower support is deliberately *not* a fit over arbitrary local lows.  A
horizontal resistance structure defines the admissible retraces: every pair of
neighbouring resistance touches contributes exactly one low, the lowest candle
strictly between the two touches.  This is the geometric contract expressed in
the review comments and is independent of outcomes and trade PnL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class CandleGeometry(Protocol):
    """Minimal immutable candle contract needed for structural geometry."""

    timestamp: NDArray[np.int64]
    high: NDArray[np.float64]
    low: NDArray[np.float64]
    open: NDArray[np.float64]
    close: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class LevelGeometryPolicy:
    touch_tolerance_pct: float = 0.006
    min_touch_separation_bars: int = 2
    max_retrace_depth_increase_pct: float = 0.10
    support_touch_tolerance_pct: float = 0.005


DEFAULT_LEVEL_GEOMETRY_POLICY = LevelGeometryPolicy()


@dataclass(frozen=True, slots=True)
class ResistanceTouch:
    index: int
    time_ms: int
    price: float


@dataclass(frozen=True, slots=True)
class RetraceLow:
    left_touch_time_ms: int
    right_touch_time_ms: int
    index: int
    time_ms: int
    price: float
    depth_pct: float


@dataclass(frozen=True, slots=True)
class LevelGeometry:
    touches: tuple[ResistanceTouch, ...]
    retraces: tuple[RetraceLow, ...]
    terminal_retrace: RetraceLow | None
    support_slope_per_bar_pct: float | None
    retrace_depths_non_increasing: bool | None
    support_contact_indices: tuple[int, ...]
    body_above_support_share: float | None

    @property
    def has_pairwise_retraces(self) -> bool:
        return len(self.retraces) == max(0, len(self.touches) - 1)

    @property
    def support_points(self) -> tuple[RetraceLow, ...]:
        """Pairwise retraces plus the observed pullback after the final touch."""

        if self.terminal_retrace is None:
            return self.retraces
        return (*self.retraces, self.terminal_retrace)

    @property
    def ascending_support(self) -> bool | None:
        if self.support_slope_per_bar_pct is None:
            return None
        return self.support_slope_per_bar_pct > 0.0


def find_resistance_touches(
    candles: CandleGeometry,
    *,
    level_price: float,
    start_ms: int,
    end_ms: int,
    policy: LevelGeometryPolicy = DEFAULT_LEVEL_GEOMETRY_POLICY,
) -> tuple[ResistanceTouch, ...]:
    """Find separated local-high taps that are close to a resistance price."""

    if level_price <= 0.0:
        raise ValueError("level_price must be positive")
    if end_ms < start_ms:
        raise ValueError("end_ms must be >= start_ms")
    timestamp, high = candles.timestamp, candles.high
    raw: list[ResistanceTouch] = []
    for index in range(1, len(timestamp) - 1):
        time_ms = int(timestamp[index])
        if time_ms < start_ms or time_ms > end_ms:
            continue
        price = float(high[index])
        if abs(price - level_price) / level_price > policy.touch_tolerance_pct:
            continue
        if price < float(high[index - 1]) or price < float(high[index + 1]):
            continue
        raw.append(ResistanceTouch(index=index, time_ms=time_ms, price=price))

    separated: list[ResistanceTouch] = []
    for touch in raw:
        previous = separated[-1] if separated else None
        if previous is None or touch.index - previous.index >= policy.min_touch_separation_bars:
            separated.append(touch)
        elif touch.price > previous.price:
            separated[-1] = touch
    return tuple(separated)


def retrace_lows_between_touches(
    candles: CandleGeometry,
    *,
    level_price: float,
    touches: tuple[ResistanceTouch, ...],
) -> tuple[RetraceLow, ...]:
    """Return one deterministic retrace low strictly inside every touch pair."""

    if level_price <= 0.0:
        raise ValueError("level_price must be positive")
    retraces: list[RetraceLow] = []
    for left, right in zip(touches, touches[1:], strict=False):
        if right.index <= left.index + 1:
            continue
        window = candles.low[left.index + 1 : right.index]
        if not len(window):
            continue
        relative_index = int(np.argmin(window))
        index = left.index + 1 + relative_index
        price = float(candles.low[index])
        retraces.append(
            RetraceLow(
                left_touch_time_ms=left.time_ms,
                right_touch_time_ms=right.time_ms,
                index=index,
                time_ms=int(candles.timestamp[index]),
                price=price,
                depth_pct=(level_price - price) / level_price,
            )
        )
    return tuple(retraces)


def terminal_retrace_after_last_touch(
    candles: CandleGeometry,
    *,
    level_price: float,
    touches: tuple[ResistanceTouch, ...],
    stop_index: int | None,
) -> RetraceLow | None:
    """Return the real pullback after the last touch, if it is observed.

    ``stop_index`` is exclusive.  A strategy therefore passes the breakout
    candle itself as the stop when the level has broken, preventing that candle
    from being mistaken for a completed pullback.
    """

    if not touches or stop_index is None:
        return None
    last = touches[-1]
    if not (last.index + 1 < stop_index <= len(candles.timestamp)):
        return None
    window = candles.low[last.index + 1 : stop_index]
    if not len(window):
        return None
    relative_index = int(np.argmin(window))
    index = last.index + 1 + relative_index
    price = float(candles.low[index])
    if price >= level_price:
        return None
    return RetraceLow(
        left_touch_time_ms=last.time_ms,
        right_touch_time_ms=int(candles.timestamp[stop_index - 1]),
        index=index,
        time_ms=int(candles.timestamp[index]),
        price=price,
        depth_pct=(level_price - price) / level_price,
    )


def _support_line_contacts_and_coverage(
    candles: CandleGeometry,
    *,
    support_points: tuple[RetraceLow, ...],
    start_index: int,
    end_index: int,
    policy: LevelGeometryPolicy,
) -> tuple[tuple[int, ...], float]:
    """Count only candles that reach/cross the support band and body coverage above it."""

    x = np.asarray([point.index for point in support_points], dtype=np.float64)
    y = np.asarray([point.price for point in support_points], dtype=np.float64)
    slope, intercept = np.polyfit(x, y, 1)
    indices = np.arange(start_index, end_index + 1, dtype=np.int64)
    line = slope * indices + intercept
    low = candles.low[indices]
    high = candles.high[indices]
    tolerance = np.abs(line) * policy.support_touch_tolerance_pct
    contacts = indices[(low <= line + tolerance) & (high >= line - tolerance)]

    body_low = np.minimum(candles.open[indices], candles.close[indices])
    body_high = np.maximum(candles.open[indices], candles.close[indices])
    body_range = body_high - body_low
    above = np.empty_like(body_range, dtype=np.float64)
    has_body = body_range > 0.0
    np.divide(body_high - line, body_range, out=above, where=has_body)
    above[has_body] = np.clip(above[has_body], 0.0, 1.0)
    above[~has_body] = (body_high[~has_body] >= line[~has_body]).astype(np.float64)
    return tuple(int(index) for index in contacts), float(np.mean(above))


def analyze_level_geometry(
    candles: CandleGeometry,
    *,
    level_price: float,
    start_ms: int,
    end_ms: int,
    policy: LevelGeometryPolicy = DEFAULT_LEVEL_GEOMETRY_POLICY,
    selected_touches: tuple[ResistanceTouch, ...] | None = None,
    terminal_retrace_stop_index: int | None = None,
) -> LevelGeometry:
    """Measure pairwise retraces, support slope, and depth compression.

    ``selected_touches`` lets a strategy supply its pre-registered structural
    taps.  This keeps the lower-support calculation tied to the same *main*
    touches shown to the reviewer, instead of silently reintroducing every
    nearby local high.
    """

    if selected_touches is None:
        touches = find_resistance_touches(
            candles,
            level_price=level_price,
            start_ms=start_ms,
            end_ms=end_ms,
            policy=policy,
        )
    else:
        touches = tuple(selected_touches)
        if any(right.index <= left.index for left, right in zip(touches, touches[1:], strict=False)):
            raise ValueError("selected_touches must be strictly ordered by candle index")
        if any(touch.time_ms < start_ms or touch.time_ms > end_ms for touch in touches):
            raise ValueError("selected_touches must stay inside the level interval")
    retraces = retrace_lows_between_touches(candles, level_price=level_price, touches=touches)
    terminal_retrace = terminal_retrace_after_last_touch(
        candles,
        level_price=level_price,
        touches=touches,
        stop_index=terminal_retrace_stop_index,
    )
    support_points = retraces if terminal_retrace is None else (*retraces, terminal_retrace)
    if len(support_points) < 2:
        return LevelGeometry(touches, retraces, terminal_retrace, None, None, (), None)

    x = np.asarray([retrace.index for retrace in support_points], dtype=np.float64)
    y = np.asarray([retrace.price for retrace in support_points], dtype=np.float64)
    slope = float(np.polyfit(x, y, 1)[0] / level_price)
    depths = np.asarray([retrace.depth_pct for retrace in support_points], dtype=np.float64)
    allowed = depths[:-1] * (1.0 + policy.max_retrace_depth_increase_pct)
    non_increasing = bool(np.all(depths[1:] <= allowed))
    end_index = int(np.searchsorted(candles.timestamp, end_ms, side="right") - 1)
    start_index = touches[0].index if touches else 0
    contacts, body_above = _support_line_contacts_and_coverage(
        candles,
        support_points=support_points,
        start_index=start_index,
        end_index=end_index,
        policy=policy,
    )
    return LevelGeometry(touches, retraces, terminal_retrace, slope, non_increasing, contacts, body_above)


__all__ = [
    "DEFAULT_LEVEL_GEOMETRY_POLICY",
    "LevelGeometry",
    "LevelGeometryPolicy",
    "ResistanceTouch",
    "RetraceLow",
    "analyze_level_geometry",
    "find_resistance_touches",
    "retrace_lows_between_touches",
    "terminal_retrace_after_last_touch",
]
