"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from domain.enums.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class SymbolMtfFrames:
    """Класс."""
    levels_timeframe: Timeframe
    entry_timeframe: Timeframe
    levels_frame: pd.DataFrame
    entry_frame: pd.DataFrame

    def get_frame(self, timeframe: Timeframe) -> pd.DataFrame:
        """Возвращает набор данных для выбранного таймфрейма."""
        if timeframe == self.levels_timeframe:
            return self.levels_frame
        if timeframe == self.entry_timeframe:
            return self.entry_frame
        msg = (
            "Unsupported timeframe for SymbolMtfFrames: "
            f"{timeframe.value}. Available: {self.levels_timeframe.value}, {self.entry_timeframe.value}"
        )
        raise ValueError(msg)
