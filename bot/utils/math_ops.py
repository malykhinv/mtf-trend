from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, List, Sequence

from statistics import mean

from ..domain.models.entities import Candle


def true_range(current: Candle, previous: Candle | None) -> float:
    if previous is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def average_true_range(candles: Sequence[Candle], period: int) -> float:
    if len(candles) < 1:
        raise ValueError("candles sequence must not be empty")
    tr_values: List[float] = []
    prev = None
    for candle in candles[-period:]:
        tr_values.append(true_range(candle, prev))
        prev = candle
    return mean(tr_values)


def moving_average(values: Iterable[float], period: int) -> float:
    data = list(values)[-period:]
    if not data:
        raise ValueError("values must not be empty")
    return mean(data)


def rolling_volume(candles: Sequence[Candle], period: int) -> float:
    volumes = [candle.volume for candle in candles[-period:]]
    if not volumes:
        raise ValueError("candles must not be empty")
    return mean(volumes)


def ema(values: Iterable[float], period: int) -> float:
    values_list = list(values)
    if not values_list:
        raise ValueError("values must not be empty")
    k = 2 / (period + 1)
    ema_value = values_list[0]
    for value in values_list[1:]:
        ema_value = value * k + ema_value * (1 - k)
    return ema_value


def window(sequence: Sequence[Candle], size: int) -> Deque[Candle]:
    if size <= 0:
        raise ValueError("size must be positive")
    window_items: Deque[Candle] = deque(maxlen=size)
    for candle in sequence[-size:]:
        window_items.append(candle)
    return window_items
