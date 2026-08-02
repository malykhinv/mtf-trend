"""Causal zone-measurement contract for IS discovery.

Birth features end at the departure candle.  Pre-touch and touch features end
at the relevant touch candle.  Outcome is deliberately a separate future-only
label and must never enter candidate admission, feature selection, or scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


ZoneOutcome = Literal["rebound", "consumed", "crossed", "unresolved", "unknown"]


@dataclass(frozen=True, slots=True)
class ZoneMetricPolicy:
    baseline_bars: int = 20
    outcome_bars: int = 48
    round_number_bps: float = 15.0


def _round_distance_bps(price: float) -> float:
    """Distance to the nearest 1/2/5 decade level, scale invariant."""
    decade = 10.0 ** np.floor(np.log10(price))
    levels = np.asarray([1.0, 2.0, 5.0, 10.0]) * decade
    return float(np.min(np.abs(levels - price) / price) * 10_000.0)


def zone_birth_features(*, high: np.ndarray, low: np.ndarray, open_price: np.ndarray, close: np.ndarray,
                        volume: np.ndarray, trades: np.ndarray, start: int, end: int, lower: float,
                        upper: float, tf_minutes: int, policy: ZoneMetricPolicy = ZoneMetricPolicy()) -> dict[str, float | int | bool]:
    """Features known immediately after the zone's departure candle closes."""
    if not (0 <= start < end < len(close) and 0 < lower < upper):
        raise ValueError("invalid zone bounds or indices")
    base = slice(start, end + 1)
    left = slice(max(0, start - policy.baseline_bars), start)
    right_index = min(end + 1, len(close) - 1)
    base_range = high[base] - low[base]
    bodies = np.abs(close[base] - open_price[base])
    base_height = upper - lower
    left_vol = float(np.mean(volume[left])) if start else np.nan
    left_trades = float(np.mean(trades[left])) if start else np.nan
    midpoint = (lower + upper) / 2.0
    return {
        "feature_cutoff_index": right_index,
        "zone_tf_minutes": tf_minutes,
        "zone_base_bars": end - start + 1,
        "zone_height_pct": base_height / midpoint,
        "zone_round_distance_bps": _round_distance_bps(midpoint),
        "zone_is_round_number": _round_distance_bps(midpoint) <= policy.round_number_bps,
        "zone_base_range_mean_pct": float(np.mean(base_range / close[base])),
        "zone_base_body_share": float(np.mean(np.divide(bodies, base_range, out=np.zeros_like(bodies), where=base_range > 0))),
        "zone_base_volume_vs_left": float(np.mean(volume[base]) / left_vol) if left_vol > 0 else np.nan,
        "zone_departure_volume_vs_left": float(volume[right_index] / left_vol) if left_vol > 0 else np.nan,
        "zone_base_trades_vs_left": float(np.mean(trades[base]) / left_trades) if left_trades > 0 else np.nan,
        "zone_departure_trades_vs_left": float(trades[right_index] / left_trades) if left_trades > 0 else np.nan,
        "zone_departure_return": float(close[right_index] / close[end] - 1.0),
    }


def first_touch_index(*, high: np.ndarray, low: np.ndarray, start: int, lower: float, upper: float) -> int | None:
    for index in range(start, len(low)):
        if high[index] >= lower and low[index] <= upper:
            return index
    return None


def touch_features(*, high: np.ndarray, low: np.ndarray, open_price: np.ndarray, close: np.ndarray,
                   volume: np.ndarray, trades: np.ndarray, departure_index: int, touch_index: int,
                   lower: float, upper: float, policy: ZoneMetricPolicy = ZoneMetricPolicy()) -> dict[str, float | int]:
    """Features available only when the touch candle has closed."""
    if not (departure_index < touch_index < len(close)):
        raise ValueError("touch must occur after departure")
    prior = slice(max(departure_index + 1, touch_index - policy.baseline_bars), touch_index)
    baseline_volume = float(np.mean(volume[prior]))
    baseline_trades = float(np.mean(trades[prior]))
    candle_range = float(high[touch_index] - low[touch_index])
    zone_height = upper - lower
    return {
        "feature_cutoff_index": touch_index,
        "bars_to_touch": touch_index - departure_index,
        "pre_touch_volume_vs_base": float(np.mean(volume[prior]) / volume[departure_index]) if volume[departure_index] > 0 else np.nan,
        "pre_touch_trades_vs_base": float(np.mean(trades[prior]) / trades[departure_index]) if trades[departure_index] > 0 else np.nan,
        "touch_volume_vs_pre": float(volume[touch_index] / baseline_volume) if baseline_volume > 0 else np.nan,
        "touch_trades_vs_pre": float(trades[touch_index] / baseline_trades) if baseline_trades > 0 else np.nan,
        "touch_range_vs_zone": candle_range / zone_height,
        "touch_penetration_share": max(0.0, min(1.0, (upper - low[touch_index]) / zone_height)),
        "touch_close_position": (close[touch_index] - lower) / zone_height,
        "touch_body_share": abs(close[touch_index] - open_price[touch_index]) / candle_range if candle_range > 0 else 0.0,
    }


def zone_outcome(*, high: np.ndarray, low: np.ndarray, close: np.ndarray, touch_index: int,
                 lower: float, upper: float, kind: str, policy: ZoneMetricPolicy = ZoneMetricPolicy()) -> ZoneOutcome:
    """Future-only competing outcome label after the first touch."""
    if kind not in {"demand", "supply"}:
        return "unknown"
    final = min(len(close), touch_index + policy.outcome_bars + 1)
    for index in range(touch_index, final):
        if kind == "demand":
            if close[index] < lower: return "crossed"
            if close[index] > upper: return "rebound"
        else:
            if close[index] > upper: return "crossed"
            if close[index] < lower: return "rebound"
    touches = sum(high[i] >= lower and low[i] <= upper for i in range(touch_index, final))
    return "consumed" if touches >= 2 else "unresolved"
