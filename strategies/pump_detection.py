from __future__ import annotations

from datetime import timedelta
from typing import Sequence

from config.config import AtrConfig
from domain.models.candle import Candle
from domain.models.pump import Pump


def detect_pump_candle_on_H(
    candles: Sequence[Candle],
    atr_values: Sequence[float],
    atr_config: AtrConfig,
) -> Pump | None:
    if not candles or not atr_values:
        return None
    if len(candles) != len(atr_values):
        raise ValueError("candles and atr_values must have the same length")

    candle = candles[-1]
    atr_value = atr_values[-1]
    if atr_value <= 0:
        return None
    if candle.close <= candle.open:
        return None

    body = candle.close - candle.open
    required_body = atr_config.atr_mult * atr_value
    if body < required_body:
        return None

    window_start = candle.timestamp - timedelta(days=7)
    for other_candle in candles:
        if other_candle.timestamp < window_start:
            continue
        if other_candle.high > candle.high:
            return None

    return Pump(candle=candle, h_main=candle.high)


__all__ = ["detect_pump_candle_on_H"]
