"""Typed containers for multi-timeframe symbol data."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from domain.enums.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class SymbolMtfFrames:
    """Normalized pair of frames required by breakout MTF mode."""

    d1_frame: pd.DataFrame
    m15_frame: pd.DataFrame

    def get_frame(self, timeframe: Timeframe) -> pd.DataFrame:
        if timeframe == Timeframe.D1:
            return self.d1_frame
        if timeframe == Timeframe.M15:
            return self.m15_frame
        msg = f"Unsupported timeframe for SymbolMtfFrames: {timeframe.value}"
        raise ValueError(msg)
