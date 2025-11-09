from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from domain.models.candle import Candle
from domain.models.pump import Pump


@dataclass(frozen=True)
class PullbackValidationResult:
    is_valid: bool
    l_pullback: float | None
    cancel_reason: str | None


class PullbackCancelReason:
    HIGHER_HIGH = "higher_high"
    DEEP_PULLBACK = "deep_pullback"
    INVALID_PUMP_BODY = "invalid_pump_body"


def validate_pullback_on_H(
    candles_after_pump: Sequence[Candle],
    pump: Pump,
    max_pullback_fraction: float,
) -> PullbackValidationResult:
    body = pump.candle.close - pump.candle.open
    if body <= 0:
        return PullbackValidationResult(False, None, PullbackCancelReason.INVALID_PUMP_BODY)

    if not candles_after_pump:
        return PullbackValidationResult(False, None, None)

    l_pullback = candles_after_pump[0].low
    for candle in candles_after_pump:
        if candle.high > pump.h_main:
            return PullbackValidationResult(False, l_pullback, PullbackCancelReason.HIGHER_HIGH)
        if candle.low < l_pullback:
            l_pullback = candle.low

    depth = (pump.h_main - l_pullback) / body
    if depth > max_pullback_fraction:
        return PullbackValidationResult(False, l_pullback, PullbackCancelReason.DEEP_PULLBACK)

    return PullbackValidationResult(True, l_pullback, None)


__all__ = ["PullbackValidationResult", "PullbackCancelReason", "validate_pullback_on_H"]
