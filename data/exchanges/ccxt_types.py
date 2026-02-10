"""Модуль проекта."""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class CcxtClientOptions(TypedDict, total=False):
    defaultType: str
    fetchCurrencies: bool


class CcxtFuturesApi(Protocol):
    markets: dict[str, dict[str, object]]

    def load_markets(self) -> object:
        """Описывает загрузку справочника рынков биржи."""
        ...

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        """Описывает загрузку свечей через API биржи."""
        ...


@runtime_checkable
class CcxtOpenInterestApi(Protocol):
    def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str,
        since: int,
        limit: int,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Описывает загрузку истории open interest через API биржи."""
        ...
