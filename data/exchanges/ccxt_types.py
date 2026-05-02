"""Модуль проекта."""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class CcxtClientOptions(TypedDict, total=False):
    defaultType: str
    fetchCurrencies: bool


class CcxtAggTradePayload(TypedDict, total=False):
    """Raw Binance aggTrade payload.

    Live endpoint fields are short Binance keys: a/p/q/T/m.
    Archive CSV fields may be normalized names.
    """
    a: int | str
    p: float | str
    q: float | str
    T: int | str
    m: bool | str
    agg_trade_id: int | str
    price: float | str
    quantity: float | str
    transact_time: int | str
    is_buyer_maker: bool | str


class CcxtFuturesApi(Protocol):
    markets: dict[str, dict[str, object]]

    def load_markets(self) -> object:
        """Описывает загрузку справочника рынков биржи."""
        ...

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        """Описывает загрузку свечей через API биржи."""
        ...

    def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, dict[str, object]]:
        """Описывает загрузку тикеров через API биржи."""
        ...

    def market_id(self, symbol: str) -> str:
        """Описывает получение exchange-specific market id."""
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
