"""Base abstraction for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

import pandas as pd

from domain.models.trade_result import TradeResult
from vectorbt_runner.mtf_frames import SymbolMtfFrames


StrategyParamsT = TypeVar("StrategyParamsT")


class BaseStrategy(ABC, Generic[StrategyParamsT]):
    """Abstract strategy contract used by backtest runner."""

    @abstractmethod
    def validate_config(self, params: StrategyParamsT) -> None:
        """Validate strategy parameters and raise ValueError for invalid configs."""

    @abstractmethod
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Prepare and enrich source market data."""

    @abstractmethod
    def generate_events(self, data: pd.DataFrame, params: StrategyParamsT) -> list[TradeResult]:
        """Run strategy simulation and return closed trades."""

    @abstractmethod
    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: StrategyParamsT,
    ) -> list[TradeResult]:
        """Run strategy simulation using dedicated higher/lower timeframe data."""
