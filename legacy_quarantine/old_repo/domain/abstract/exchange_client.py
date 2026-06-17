"""Модуль проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod
import pandas as pd

from domain.enums.timeframe import Timeframe


class ExchangeClient(ABC):
    """Класс."""
    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Метод."""
    @abstractmethod
    def fetch_open_interest(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Метод."""
    @abstractmethod
    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        *,
        reduce_only: bool,
        client_order_id: str,
    ) -> dict[str, object]:
        """Создаёт лимитный ордер."""

    @abstractmethod
    def get_futures_symbols(self) -> list[str]:
        """Метод."""
