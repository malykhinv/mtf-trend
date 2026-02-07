"""Exchange client abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from domain.enums.timeframe import Timeframe


class ExchangeClient(ABC):
    """Interface for futures exchange market data access."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> pd.DataFrame:
        """Return OHLCV candles for symbol and timeframe in [start_time, end_time]."""

    @abstractmethod
    def fetch_open_interest(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> pd.DataFrame:
        """Return open interest time-series aligned to timeframe in [start_time, end_time]."""

    @abstractmethod
    def get_futures_symbols(self) -> list[str]:
        """Return list of active futures symbols."""
