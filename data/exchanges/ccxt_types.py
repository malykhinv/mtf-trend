"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExchangeOrderFill:
    """Normalized executed order fill. Missing execution fields are not inferred from candles."""

    order_id: str
    status: str
    timestamp_ms: int
    average_price: float
    filled_amount: float
    cost: float | None = None
    fee_cost: float | None = None
    fee_currency: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeLiveAccountPreflight:
    """Explicit live account mode snapshot required before real-order live trading."""

    exchange: str
    position_mode: str
    hedge_mode_enabled: bool


@dataclass(frozen=True, slots=True)
class ExchangePositionSnapshot:
    """Signed exchange position amount for startup live cleanup."""

    symbol: str
    signed_amount: float
    side: str
    source: str




@dataclass(frozen=True, slots=True)
class ExchangeOpenInterestSnapshot:
    """Normalized current open-interest snapshot for live execution guards."""

    symbol: str
    exchange_symbol: str
    fetched_at_ms: int
    timestamp_ms: int | None
    open_interest: float | None
    source: str
    status: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeTickerSnapshot:
    """Normalized ticker snapshot for live scheduling priority only.

    These fields are not a trading signal and must not replace kline-based
    quote-volume / trade-count evidence. Missing values are explicit None/status
    fields instead of proxy fallbacks.
    """

    symbol: str
    fetched_at_ms: int
    last_price: float | None
    quote_volume_24h: float | None
    trade_count_24h: int | None
    last_price_source: str
    quote_volume_source: str
    trade_count_source: str
    status: str
    reason: str | None = None


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

    def fetch_open_orders(self, symbol: str) -> list[dict[str, object]]:
        """Описывает загрузку открытых ордеров по символу."""
        ...

    def fetch_open_stop_orders(self, symbol: str) -> list[dict[str, object]]:
        """Describes loading currently open conditional/algo stop orders for a symbol."""
        ...

    def fetch_stop_order_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, object]:
        """Describes loading an open conditional/algo stop order by client id."""
        ...

    def cancel_stop_order(self, symbol: str, order_id: str) -> dict[str, object]:
        """Describes cancelling a conditional/algo stop order by id."""
        ...

    def fetch_order_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, object]:
        """Описывает загрузку ордера по clientOrderId для проверки live-защитных ордеров."""
        ...

    def market_id(self, symbol: str) -> str:
        """Описывает получение exchange-specific market id."""
        ...




@runtime_checkable
class CcxtBinanceKlineApi(Protocol):
    def fapiPublicGetKlines(self, params: dict[str, object]) -> list[list[object]]:
        """Describes loading raw Binance USD-M futures kline payload."""
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
