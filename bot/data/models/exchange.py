from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence, cast

from .ohlcv import OhlcvSnapshot


def parse_string(value: object, *, field: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    raise TypeError(f"Expected {field} to be str-compatible, got {type(value)!r}")


def parse_float(value: object, *, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid float value for {field}: {value!r}") from exc
    raise TypeError(f"Expected numeric value for {field}, got {type(value)!r}")


def parse_optional_float(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:  # pragma: no cover - defensive
            return None
    return None


def parse_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{field} must include timezone information")
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
    elif isinstance(value, str):
        try:
            timestamp = float(value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise TypeError(f"{field} must be a datetime or numeric timestamp") from exc
    else:
        raise TypeError(f"{field} must be a datetime or numeric timestamp")
    if timestamp > 1e12:
        timestamp /= 1000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


@dataclass(slots=True)
class BinanceSymbolInfoData:
    symbol: str
    status: str

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BinanceSymbolInfoData":
        return cls(
            symbol=parse_string(payload["symbol"], field="symbol"),
            status=parse_string(payload["status"], field="status"),
        )


@dataclass(slots=True)
class BinanceExchangeInfoResponse:
    symbols: tuple[BinanceSymbolInfoData, ...]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BinanceExchangeInfoResponse":
        raw_symbols = cast(Sequence[Mapping[str, object]], payload["symbols"])
        symbols = tuple(
            BinanceSymbolInfoData.decode(symbol_payload)
            for symbol_payload in raw_symbols
        )
        return cls(symbols=symbols)


@dataclass(slots=True)
class BinanceTicker24hData:
    symbol: str
    quote_volume: object

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BinanceTicker24hData":
        return cls(
            symbol=parse_string(payload["symbol"], field="symbol"),
            quote_volume=payload.get("quoteVolume"),
        )


@dataclass(slots=True)
class BinanceTickers24hResponse:
    tickers: tuple[BinanceTicker24hData, ...]

    @classmethod
    def decode(cls, payload: Sequence[Mapping[str, object]]) -> "BinanceTickers24hResponse":
        tickers = tuple(BinanceTicker24hData.decode(item) for item in payload)
        return cls(tickers=tickers)


@dataclass(slots=True)
class BinanceKlineData:
    open_time: object
    close_time: object
    open_price: object
    high_price: object
    low_price: object
    close_price: object
    volume: object
    quote_volume: object | None
    raw: Sequence[object]

    @classmethod
    def decode(cls, payload: Sequence[object]) -> "BinanceKlineData":
        close_source = payload[6] if len(payload) > 6 else payload[0]
        quote_volume = payload[7] if len(payload) > 7 else None
        return cls(
            open_time=payload[0],
            close_time=close_source,
            open_price=payload[1],
            high_price=payload[2],
            low_price=payload[3],
            close_price=payload[4],
            volume=payload[5],
            quote_volume=quote_volume,
            raw=payload,
        )


@dataclass(slots=True)
class BinanceKlinesResponse:
    entries: tuple[BinanceKlineData, ...]

    @classmethod
    def decode(cls, payload: Sequence[Sequence[object]]) -> "BinanceKlinesResponse":
        entries = tuple(BinanceKlineData.decode(item) for item in payload)
        return cls(entries=entries)


@dataclass(slots=True)
class BinanceBalanceData:
    asset: str
    balance: object | None
    available_balance: object | None
    cross_wallet_balance: object | None
    equity: object | None
    raw: Mapping[str, object]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BinanceBalanceData":
        return cls(
            asset=parse_string(payload.get("asset", ""), field="asset"),
            balance=payload.get("balance"),
            available_balance=payload.get("availableBalance"),
            cross_wallet_balance=payload.get("crossWalletBalance"),
            equity=payload.get("equity"),
            raw=payload,
        )


@dataclass(slots=True)
class BinanceBalancesResponse:
    balances: tuple[BinanceBalanceData, ...]

    @classmethod
    def decode(cls, payload: Sequence[Mapping[str, object]]) -> "BinanceBalancesResponse":
        balances = tuple(BinanceBalanceData.decode(item) for item in payload)
        return cls(balances=balances)


@dataclass(slots=True)
class BybitInstrumentData:
    symbol: str
    status: str

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitInstrumentData":
        return cls(
            symbol=parse_string(payload["symbol"], field="symbol"),
            status=parse_string(payload["status"], field="status"),
        )


@dataclass(slots=True)
class BybitInstrumentsResponse:
    instruments: tuple[BybitInstrumentData, ...]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitInstrumentsResponse":
        result_raw = payload.get("result") or {}
        result = cast(Mapping[str, object], result_raw)
        instrument_payloads = cast(Sequence[Mapping[str, object]], result.get("list", ()))
        instruments = tuple(BybitInstrumentData.decode(item) for item in instrument_payloads)
        return cls(instruments=instruments)


@dataclass(slots=True)
class BybitTickerData:
    symbol: str
    turnover24h: object | None
    turnover: object | None

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitTickerData":
        return cls(
            symbol=parse_string(payload["symbol"], field="symbol"),
            turnover24h=payload.get("turnover24h"),
            turnover=payload.get("turnover"),
        )


@dataclass(slots=True)
class BybitTickersResponse:
    tickers: tuple[BybitTickerData, ...]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitTickersResponse":
        result_raw = payload.get("result") or {}
        result = cast(Mapping[str, object], result_raw)
        raw_tickers = cast(Sequence[Mapping[str, object]], result.get("list", ()))
        tickers = tuple(BybitTickerData.decode(item) for item in raw_tickers)
        return cls(tickers=tickers)


@dataclass(slots=True)
class BybitKlineData:
    start: object
    close_time: object
    open_price: object
    high_price: object
    low_price: object
    close_price: object
    volume: object
    turnover: object | None
    raw: Sequence[object]

    @classmethod
    def decode(cls, payload: Sequence[object]) -> "BybitKlineData":
        turnover = payload[6] if len(payload) > 6 else None
        return cls(
            start=payload[0],
            close_time=payload[0],
            open_price=payload[1],
            high_price=payload[2],
            low_price=payload[3],
            close_price=payload[4],
            volume=payload[5],
            turnover=turnover,
            raw=payload,
        )


@dataclass(slots=True)
class BybitKlinesResponse:
    entries: tuple[BybitKlineData, ...]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitKlinesResponse":
        result_raw = payload.get("result") or {}
        result = cast(Mapping[str, object], result_raw)
        raw_entries = cast(Sequence[Sequence[object]], result.get("list", ()))
        entries = tuple(BybitKlineData.decode(item) for item in raw_entries)
        return cls(entries=entries)


@dataclass(slots=True)
class BybitCoinBalanceData:
    asset: str
    wallet_balance: object | None
    available_to_withdraw: object | None
    equity: object | None
    available_balance: object | None
    raw: Mapping[str, object]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitCoinBalanceData":
        return cls(
            asset=parse_string(payload.get("coin", ""), field="coin"),
            wallet_balance=payload.get("walletBalance"),
            available_to_withdraw=payload.get("availableToWithdraw"),
            equity=payload.get("equity"),
            available_balance=payload.get("availableBalance"),
            raw=payload,
        )


@dataclass(slots=True)
class BybitAccountBalanceData:
    coins: tuple[BybitCoinBalanceData, ...]
    raw: Mapping[str, object]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitAccountBalanceData":
        coin_payloads = cast(Sequence[Mapping[str, object]], payload.get("coin") or ())
        coins = tuple(BybitCoinBalanceData.decode(item) for item in coin_payloads)
        return cls(coins=coins, raw=payload)


@dataclass(slots=True)
class BybitWalletBalanceResponse:
    accounts: tuple[BybitAccountBalanceData, ...]

    @classmethod
    def decode(cls, payload: Mapping[str, object]) -> "BybitWalletBalanceResponse":
        result_raw = payload.get("result") or {}
        result = cast(Mapping[str, object], result_raw)
        entries = cast(Sequence[Mapping[str, object]], result.get("list", ()))
        accounts = tuple(BybitAccountBalanceData.decode(item) for item in entries)
        return cls(accounts=accounts)


@dataclass(slots=True)
class BinanceSymbolInfo:
    symbol: str
    status: str

    @classmethod
    def from_payload(cls, payload: BinanceSymbolInfoData) -> "BinanceSymbolInfo":
        return cls(symbol=payload.symbol.upper(), status=payload.status)


@dataclass(slots=True)
class BinanceExchangeInfoPayload:
    symbols: tuple[BinanceSymbolInfo, ...]

    @classmethod
    def from_http(cls, payload: BinanceExchangeInfoResponse) -> "BinanceExchangeInfoPayload":
        symbols = tuple(BinanceSymbolInfo.from_payload(item) for item in payload.symbols)
        return cls(symbols=symbols)


@dataclass(slots=True)
class BinanceTicker24h:
    symbol: str
    quote_volume: float

    @classmethod
    def from_payload(cls, payload: BinanceTicker24hData) -> "BinanceTicker24h":
        quote_volume = parse_float(payload.quote_volume, field="quoteVolume")
        return cls(symbol=payload.symbol.upper(), quote_volume=quote_volume)


@dataclass(slots=True)
class BinanceTickers24hPayload:
    tickers: tuple[BinanceTicker24h, ...]

    @classmethod
    def from_http(cls, payload: BinanceTickers24hResponse) -> "BinanceTickers24hPayload":
        tickers = tuple(BinanceTicker24h.from_payload(item) for item in payload.tickers)
        return cls(tickers=tickers)


@dataclass(slots=True)
class BinanceKline(OhlcvSnapshot):
    opened_at: datetime
    closed_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float | None
    raw: Sequence[object]

    @classmethod
    def from_payload(cls, payload: BinanceKlineData) -> "BinanceKline":
        opened_at = parse_datetime(payload.open_time, field="open time")
        closed_at = parse_datetime(payload.close_time, field="close time")
        open_price = parse_float(payload.open_price, field="open price")
        high_price = parse_float(payload.high_price, field="high price")
        low_price = parse_float(payload.low_price, field="low price")
        close_price = parse_float(payload.close_price, field="close price")
        volume = parse_float(payload.volume, field="volume")
        quote_volume = parse_optional_float(payload.quote_volume)
        return cls(
            opened_at=opened_at,
            closed_at=closed_at,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            quote_volume=quote_volume,
            raw=payload.raw,
        )


@dataclass(slots=True)
class BinanceKlinesPayload:
    entries: tuple[BinanceKline, ...]

    @classmethod
    def from_http(cls, payload: BinanceKlinesResponse) -> "BinanceKlinesPayload":
        entries = tuple(BinanceKline.from_payload(item) for item in payload.entries)
        return cls(entries=entries)


@dataclass(slots=True)
class BinanceBalance:
    asset: str
    balance: float | None
    available_balance: float | None
    cross_wallet_balance: float | None
    equity: float | None
    raw: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: BinanceBalanceData) -> "BinanceBalance":
        return cls(
            asset=payload.asset.upper(),
            balance=parse_optional_float(payload.balance),
            available_balance=parse_optional_float(payload.available_balance),
            cross_wallet_balance=parse_optional_float(payload.cross_wallet_balance),
            equity=parse_optional_float(payload.equity),
            raw=payload.raw,
        )


@dataclass(slots=True)
class BinanceBalancesPayload:
    balances: tuple[BinanceBalance, ...]

    @classmethod
    def from_http(cls, payload: BinanceBalancesResponse) -> "BinanceBalancesPayload":
        balances = tuple(BinanceBalance.from_payload(item) for item in payload.balances)
        return cls(balances=balances)


@dataclass(slots=True)
class BybitInstrument:
    symbol: str
    status: str

    @classmethod
    def from_payload(cls, payload: BybitInstrumentData) -> "BybitInstrument":
        return cls(symbol=payload.symbol.upper(), status=payload.status)


@dataclass(slots=True)
class BybitInstrumentsPayload:
    instruments: tuple[BybitInstrument, ...]

    @classmethod
    def from_http(cls, payload: BybitInstrumentsResponse) -> "BybitInstrumentsPayload":
        instruments = tuple(BybitInstrument.from_payload(item) for item in payload.instruments)
        return cls(instruments=instruments)


@dataclass(slots=True)
class BybitTicker:
    symbol: str
    turnover24h: float | None
    turnover: float | None

    @classmethod
    def from_payload(cls, payload: BybitTickerData) -> "BybitTicker":
        return cls(
            symbol=payload.symbol.upper(),
            turnover24h=parse_optional_float(payload.turnover24h),
            turnover=parse_optional_float(payload.turnover),
        )

    def quote_volume(self) -> float | None:
        if self.turnover24h is not None:
            return self.turnover24h
        return self.turnover


@dataclass(slots=True)
class BybitTickersPayload:
    tickers: tuple[BybitTicker, ...]

    @classmethod
    def from_http(cls, payload: BybitTickersResponse) -> "BybitTickersPayload":
        tickers = tuple(BybitTicker.from_payload(item) for item in payload.tickers)
        return cls(tickers=tickers)


@dataclass(slots=True)
class BybitKline(OhlcvSnapshot):
    opened_at: datetime
    closed_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float | None
    raw: Sequence[object]

    @classmethod
    def from_payload(cls, payload: BybitKlineData) -> "BybitKline":
        opened_at = parse_datetime(payload.start, field="open time")
        closed_at = parse_datetime(payload.close_time, field="close time")
        open_price = parse_float(payload.open_price, field="open price")
        high_price = parse_float(payload.high_price, field="high price")
        low_price = parse_float(payload.low_price, field="low price")
        close_price = parse_float(payload.close_price, field="close price")
        volume = parse_float(payload.volume, field="volume")
        quote_volume = parse_optional_float(payload.turnover)
        return cls(
            opened_at=opened_at,
            closed_at=closed_at,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            quote_volume=quote_volume,
            raw=payload.raw,
        )


@dataclass(slots=True)
class BybitKlinesPayload:
    entries: tuple[BybitKline, ...]

    @classmethod
    def from_http(cls, payload: BybitKlinesResponse) -> "BybitKlinesPayload":
        entries = tuple(BybitKline.from_payload(item) for item in payload.entries)
        return cls(entries=entries)


@dataclass(slots=True)
class BybitCoinBalance:
    asset: str
    wallet_balance: float | None
    available_to_withdraw: float | None
    equity: float | None
    available_balance: float | None
    raw: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: BybitCoinBalanceData) -> "BybitCoinBalance":
        return cls(
            asset=payload.asset.upper(),
            wallet_balance=parse_optional_float(payload.wallet_balance),
            available_to_withdraw=parse_optional_float(payload.available_to_withdraw),
            equity=parse_optional_float(payload.equity),
            available_balance=parse_optional_float(payload.available_balance),
            raw=payload.raw,
        )


@dataclass(slots=True)
class BybitAccountBalance:
    coins: tuple[BybitCoinBalance, ...]
    raw: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: BybitAccountBalanceData) -> "BybitAccountBalance":
        coins = tuple(BybitCoinBalance.from_payload(item) for item in payload.coins)
        return cls(coins=coins, raw=payload.raw)


@dataclass(slots=True)
class BybitWalletBalancePayload:
    accounts: tuple[BybitAccountBalance, ...]

    @classmethod
    def from_http(cls, payload: BybitWalletBalanceResponse) -> "BybitWalletBalancePayload":
        accounts = tuple(BybitAccountBalance.from_payload(item) for item in payload.accounts)
        return cls(accounts=accounts)


def _select_first_available(values: Iterable[float | None]) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None

