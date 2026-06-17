from __future__ import annotations

from dataclasses import dataclass

from .time import validate_timestamp_ms

ONE_MINUTE_MS = 60_000
FIVE_MINUTES_MS = 300_000


class MarketDataContractError(ValueError):
    """Raised when normalized market data violates the schema contract."""


def _validate_symbol(symbol: str) -> None:
    if not isinstance(symbol, str) or not symbol.strip():
        raise MarketDataContractError("symbol must be a non-empty string")


def _validate_finite_number(value: float | int | None, *, field_name: str, required: bool = True) -> None:
    if value is None:
        if required:
            raise MarketDataContractError(f"{field_name} is required")
        return
    if not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric, got {type(value).__name__}")
    if value != value or value in (float("inf"), float("-inf")):
        raise MarketDataContractError(f"{field_name} must be finite")


def _validate_non_negative(value: float | int | None, *, field_name: str, required: bool = True) -> None:
    _validate_finite_number(value, field_name=field_name, required=required)
    if value is not None and value < 0:
        raise MarketDataContractError(f"{field_name} must be non-negative")


def _validate_positive(value: float | int | None, *, field_name: str, required: bool = True) -> None:
    _validate_finite_number(value, field_name=field_name, required=required)
    if value is not None and value <= 0:
        raise MarketDataContractError(f"{field_name} must be positive")


def _validate_candle_ohlc(*, open: float, high: float, low: float, close: float) -> None:
    for field_name, value in {"open": open, "high": high, "low": low, "close": close}.items():
        _validate_positive(value, field_name=field_name)
    if high < max(open, close):
        raise MarketDataContractError("high must be >= max(open, close)")
    if low > min(open, close):
        raise MarketDataContractError("low must be <= min(open, close)")
    if low > high:
        raise MarketDataContractError("low must be <= high")


def _validate_candle_common(
    *,
    symbol: str,
    open_time_ms: int,
    available_time_ms: int,
    timeframe_ms: int,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    quote_volume: float,
    number_of_trades: float | None,
    taker_buy_quote_volume: float | None,
) -> None:
    _validate_symbol(symbol)
    validate_timestamp_ms(open_time_ms, field_name="open_time_ms")
    validate_timestamp_ms(available_time_ms, field_name="available_time_ms")
    if available_time_ms < open_time_ms + timeframe_ms:
        raise MarketDataContractError("available_time_ms must be at or after candle close")
    _validate_candle_ohlc(open=open, high=high, low=low, close=close)
    _validate_non_negative(volume, field_name="volume")
    _validate_non_negative(quote_volume, field_name="quote_volume")
    _validate_non_negative(number_of_trades, field_name="number_of_trades", required=False)
    _validate_non_negative(taker_buy_quote_volume, field_name="taker_buy_quote_volume", required=False)
    if taker_buy_quote_volume is not None and taker_buy_quote_volume > quote_volume:
        raise MarketDataContractError("taker_buy_quote_volume must be <= quote_volume")


@dataclass(frozen=True, slots=True)
class Candle1m:
    symbol: str
    open_time_ms: int
    available_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    number_of_trades: float | None = None
    taker_buy_quote_volume: float | None = None

    def __post_init__(self) -> None:
        _validate_candle_common(
            symbol=self.symbol,
            open_time_ms=self.open_time_ms,
            available_time_ms=self.available_time_ms,
            timeframe_ms=ONE_MINUTE_MS,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
            number_of_trades=self.number_of_trades,
            taker_buy_quote_volume=self.taker_buy_quote_volume,
        )


@dataclass(frozen=True, slots=True)
class Candle5m:
    symbol: str
    open_time_ms: int
    available_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    number_of_trades: float | None = None
    taker_buy_quote_volume: float | None = None

    def __post_init__(self) -> None:
        _validate_candle_common(
            symbol=self.symbol,
            open_time_ms=self.open_time_ms,
            available_time_ms=self.available_time_ms,
            timeframe_ms=FIVE_MINUTES_MS,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
            number_of_trades=self.number_of_trades,
            taker_buy_quote_volume=self.taker_buy_quote_volume,
        )


@dataclass(frozen=True, slots=True)
class OpenInterest5m:
    symbol: str
    timestamp_ms: int
    available_time_ms: int
    open_interest: float
    source: str

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        validate_timestamp_ms(self.timestamp_ms, field_name="timestamp_ms")
        validate_timestamp_ms(self.available_time_ms, field_name="available_time_ms")
        if self.available_time_ms < self.timestamp_ms + FIVE_MINUTES_MS:
            raise MarketDataContractError("closed 5m OI must be available only after the 5m bucket close")
        _validate_non_negative(self.open_interest, field_name="open_interest")
        if not self.source:
            raise MarketDataContractError("source is required")


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    symbol: str
    event_time_ms: int
    available_time_ms: int
    side: str
    price: float
    quantity: float
    quote_quantity: float
    source: str

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        validate_timestamp_ms(self.event_time_ms, field_name="event_time_ms")
        validate_timestamp_ms(self.available_time_ms, field_name="available_time_ms")
        if self.available_time_ms < self.event_time_ms:
            raise MarketDataContractError("available_time_ms must be >= event_time_ms")
        if self.side not in {"long", "short"}:
            raise MarketDataContractError("side must be 'long' or 'short'")
        _validate_positive(self.price, field_name="price")
        _validate_non_negative(self.quantity, field_name="quantity")
        _validate_non_negative(self.quote_quantity, field_name="quote_quantity")
        if not self.source:
            raise MarketDataContractError("source is required")


@dataclass(frozen=True, slots=True)
class SymbolDayUniverseRow:
    trade_date: str
    symbol: str
    listed_asof_day: bool
    delisted_asof_day: bool
    tradable_on_day: bool
    has_1m_data: bool
    has_5m_data: bool
    has_oi_data: bool
    has_liquidation_data: bool
    liquidity_eligible_on_day: bool
    reason_if_excluded: str = ""

    def __post_init__(self) -> None:
        if not self.trade_date:
            raise MarketDataContractError("trade_date is required")
        _validate_symbol(self.symbol)
        if not self.tradable_on_day and not self.reason_if_excluded:
            raise MarketDataContractError("reason_if_excluded is required when tradable_on_day is False")
