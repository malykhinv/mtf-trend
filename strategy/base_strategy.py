"""Модуль проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

import pandas as pd

from domain.models.trade_result import TradeResult
from vectorbt_runner.mtf_frames import SymbolMtfFrames

StrategyParamsT = TypeVar("StrategyParamsT")


class BaseStrategy(ABC, Generic[StrategyParamsT]):
    """Класс."""
    @abstractmethod
    def validate_config(self, params: StrategyParamsT) -> None:
        """Метод."""
    @abstractmethod
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Метод."""
    @abstractmethod
    def generate_events(self, data: pd.DataFrame, params: StrategyParamsT) -> list[TradeResult]:
        """Метод."""
    @abstractmethod
    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: StrategyParamsT,
    ) -> list[TradeResult]:
        """Метод."""