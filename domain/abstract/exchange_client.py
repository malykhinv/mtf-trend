"""Модуль проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from domain.enums.timeframe import Timeframe


class ExchangeClient(ABC):
    """Класс."""
    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> pd.DataFrame:
        """Метод."""
    @abstractmethod
    def fetch_open_interest(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime,
        end_time: datetime,
    ) -> pd.DataFrame:
        """Метод."""
    @abstractmethod
    def get_futures_symbols(self) -> list[str]:
        """Метод."""