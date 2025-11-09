from __future__ import annotations

from dataclasses import dataclass

from .candle import Candle


@dataclass(frozen=True)
class Pump:
    candle: Candle
    h_main: float


__all__ = ["Pump"]
