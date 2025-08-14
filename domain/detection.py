from __future__ import annotations

from domain.models.bar import Bar
from domain.models.signal import Signal
from domain.models.timeframe import Timeframe
from services.plotter import Plotter


def detect(bars: list[Bar], timeframe: Timeframe, plotter: Plotter) -> Signal | None:
    return None

