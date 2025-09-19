from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from .ohlcv import OhlcvSnapshot


def _ensure_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected {field} to be str, got {type(value)!r}")
    return value


def _ensure_float(value: object, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid float value for {field}: {value!r}") from exc
    raise TypeError(f"Expected numeric value for {field}, got {type(value)!r}")


def _ensure_datetime(value: object, field: str) -> datetime:
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


def _ensure_optional_float(value: object | None) -> float | None:
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


def _extract_first(raw: Mapping[str, object], keys: Sequence[str], field: str) -> object:
    for key in keys:
        if key in raw:
            return raw[key]
    raise KeyError(f"{field} not found in payload")


@dataclass(slots=True)
class BinanceSymbolInfo:
    symbol: str
    status: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BinanceSymbolInfo":
        symbol = _ensure_str(payload["symbol"], "symbol").upper()
        status = _ensure_str(payload["status"], "status")
        return cls(symbol=symbol, status=status)


@dataclass(slots=True)
class BinanceExchangeInfoPayload:
    symbols: tuple[BinanceSymbolInfo, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BinanceExchangeInfoPayload":
        if not isinstance(payload, Mapping):
            raise TypeError("Binance exchange info payload must be a mapping")
        symbols_raw = payload.get("symbols")
        if not isinstance(symbols_raw, Sequence):
            raise TypeError("Binance exchange info symbols must be a sequence")
        symbols: list[BinanceSymbolInfo] = []
        for item in symbols_raw:
            if isinstance(item, Mapping):
                try:
                    symbols.append(BinanceSymbolInfo.from_payload(item))
                except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
                    continue
        return cls(tuple(symbols))


@dataclass(slots=True)
class BinanceTicker24h:
    symbol: str
    quote_volume: float

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BinanceTicker24h":
        symbol = _ensure_str(payload["symbol"], "symbol").upper()
        quote_volume = _ensure_float(payload["quoteVolume"], "quoteVolume")
        return cls(symbol=symbol, quote_volume=quote_volume)


@dataclass(slots=True)
class BinanceTickers24hPayload:
    tickers: tuple[BinanceTicker24h, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BinanceTickers24hPayload":
        if not isinstance(payload, Sequence):
            raise TypeError("Binance ticker payload must be a sequence")
        tickers: list[BinanceTicker24h] = []
        for item in payload:
            if isinstance(item, Mapping):
                try:
                    tickers.append(BinanceTicker24h.from_payload(item))
                except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
                    continue
        return cls(tuple(tickers))


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
    def from_payload(cls, payload: Sequence[object]) -> "BinanceKline":
        if len(payload) < 6:
            raise ValueError("Binance kline payload must include at least 6 entries")
        opened_at = _ensure_datetime(payload[0], "open time")
        close_source = payload[6] if len(payload) > 6 else payload[0]
        closed_at = _ensure_datetime(close_source, "close time")
        open_price = _ensure_float(payload[1], "open price")
        high_price = _ensure_float(payload[2], "high price")
        low_price = _ensure_float(payload[3], "low price")
        close_price = _ensure_float(payload[4], "close price")
        volume = _ensure_float(payload[5], "volume")
        quote_volume = _ensure_optional_float(payload[7] if len(payload) > 7 else None)
        return cls(
            opened_at=opened_at,
            closed_at=closed_at,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            quote_volume=quote_volume,
            raw=payload,
        )


@dataclass(slots=True)
class BinanceKlinesPayload:
    entries: tuple[BinanceKline, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BinanceKlinesPayload":
        if not isinstance(payload, Sequence):
            raise TypeError("Binance klines payload must be a sequence")
        entries: list[BinanceKline] = []
        for item in payload:
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                try:
                    entries.append(BinanceKline.from_payload(item))
                except (ValueError, TypeError):  # pragma: no cover - defensive
                    continue
        return cls(tuple(entries))


@dataclass(slots=True)
class BinanceBalance:
    asset: str
    balance: float | None
    available_balance: float | None
    cross_wallet_balance: float | None
    equity: float | None
    raw: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BinanceBalance":
        asset = str(payload.get("asset") or "").upper()
        balance = _ensure_optional_float(payload.get("balance"))
        available_balance = _ensure_optional_float(payload.get("availableBalance"))
        cross_wallet_balance = _ensure_optional_float(payload.get("crossWalletBalance"))
        equity = _ensure_optional_float(payload.get("equity"))
        return cls(
            asset=asset,
            balance=balance,
            available_balance=available_balance,
            cross_wallet_balance=cross_wallet_balance,
            equity=equity,
            raw=payload,
        )


@dataclass(slots=True)
class BinanceBalancesPayload:
    balances: tuple[BinanceBalance, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BinanceBalancesPayload":
        if not isinstance(payload, Sequence):
            raise TypeError("Binance balances payload must be a sequence")
        balances: list[BinanceBalance] = []
        for item in payload:
            if isinstance(item, Mapping):
                balances.append(BinanceBalance.from_payload(item))
        return cls(tuple(balances))


@dataclass(slots=True)
class BybitInstrument:
    symbol: str
    status: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BybitInstrument":
        symbol = _ensure_str(payload["symbol"], "symbol").upper()
        status = _ensure_str(payload["status"], "status")
        return cls(symbol=symbol, status=status)


@dataclass(slots=True)
class BybitInstrumentsPayload:
    instruments: tuple[BybitInstrument, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BybitInstrumentsPayload":
        if not isinstance(payload, Mapping):
            raise TypeError("Bybit instruments payload must be a mapping")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise TypeError("Bybit instruments result must be a mapping")
        instruments_raw = result.get("list")
        if not isinstance(instruments_raw, Sequence):
            raise TypeError("Bybit instruments list must be a sequence")
        instruments: list[BybitInstrument] = []
        for item in instruments_raw:
            if isinstance(item, Mapping):
                try:
                    instruments.append(BybitInstrument.from_payload(item))
                except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
                    continue
        return cls(tuple(instruments))


@dataclass(slots=True)
class BybitTicker:
    symbol: str
    turnover24h: float | None
    turnover: float | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BybitTicker":
        symbol = _ensure_str(payload["symbol"], "symbol").upper()
        turnover24h = _ensure_optional_float(payload.get("turnover24h"))
        turnover = _ensure_optional_float(payload.get("turnover"))
        return cls(symbol=symbol, turnover24h=turnover24h, turnover=turnover)

    def quote_volume(self) -> float | None:
        if self.turnover24h is not None:
            return self.turnover24h
        return self.turnover


@dataclass(slots=True)
class BybitTickersPayload:
    tickers: tuple[BybitTicker, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BybitTickersPayload":
        if not isinstance(payload, Mapping):
            raise TypeError("Bybit tickers payload must be a mapping")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise TypeError("Bybit tickers result must be a mapping")
        tickers_raw = result.get("list")
        if not isinstance(tickers_raw, Sequence):
            raise TypeError("Bybit tickers list must be a sequence")
        tickers: list[BybitTicker] = []
        for item in tickers_raw:
            if isinstance(item, Mapping):
                try:
                    tickers.append(BybitTicker.from_payload(item))
                except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
                    continue
        return cls(tuple(tickers))


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
    raw: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BybitKline":
        opened_value = _extract_first(
            payload,
            ("startTime", "start", "openTime", "open_time", "timestamp"),
            "open time",
        )
        closed_value = (
            payload.get("endTime")
            or payload.get("end")
            or payload.get("closeTime")
            or opened_value
        )
        opened_at = _ensure_datetime(opened_value, "open time")
        closed_at = _ensure_datetime(closed_value, "close time")
        open_price = _ensure_float(
            _extract_first(payload, ("openPrice", "open"), "open price"), "open price"
        )
        high_price = _ensure_float(
            _extract_first(payload, ("highPrice", "high"), "high price"), "high price"
        )
        low_price = _ensure_float(
            _extract_first(payload, ("lowPrice", "low"), "low price"), "low price"
        )
        close_price = _ensure_float(
            _extract_first(payload, ("closePrice", "close"), "close price"), "close price"
        )
        volume = _ensure_float(
            _extract_first(payload, ("volume", "vol", "turnover"), "volume"), "volume"
        )
        quote_volume = _ensure_optional_float(
            payload.get("turnover") or payload.get("quoteVolume") or payload.get("quote_volume")
        )
        return cls(
            opened_at=opened_at,
            closed_at=closed_at,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            quote_volume=quote_volume,
            raw=payload,
        )


def _normalize_bybit_kline_entry(entry: object) -> Mapping[str, object] | None:
    if isinstance(entry, Mapping):
        return entry
    if isinstance(entry, Sequence) and not isinstance(entry, (str, bytes, bytearray)):
        keys = ("start", "open", "high", "low", "close", "volume", "turnover")
        normalized: dict[str, object] = {}
        for index, key in enumerate(keys):
            if index < len(entry):
                normalized[key] = entry[index]
        if normalized:
            return normalized
    return None


@dataclass(slots=True)
class BybitKlinesPayload:
    entries: tuple[BybitKline, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BybitKlinesPayload":
        if not isinstance(payload, Mapping):
            raise TypeError("Bybit klines payload must be a mapping")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise TypeError("Bybit klines result must be a mapping")
        raw_entries = result.get("list")
        if not isinstance(raw_entries, Sequence):
            raise TypeError("Bybit klines list must be a sequence")
        entries: list[BybitKline] = []
        for item in raw_entries:
            normalized = _normalize_bybit_kline_entry(item)
            if normalized is not None:
                try:
                    entries.append(BybitKline.from_payload(normalized))
                except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
                    continue
        return cls(tuple(entries))


@dataclass(slots=True)
class BybitCoinBalance:
    asset: str
    wallet_balance: float | None
    available_to_withdraw: float | None
    equity: float | None
    available_balance: float | None
    raw: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BybitCoinBalance":
        asset = str(payload.get("coin") or "").upper()
        wallet_balance = _ensure_optional_float(payload.get("walletBalance"))
        available_to_withdraw = _ensure_optional_float(payload.get("availableToWithdraw"))
        equity = _ensure_optional_float(payload.get("equity"))
        available_balance = _ensure_optional_float(payload.get("availableBalance"))
        return cls(
            asset=asset,
            wallet_balance=wallet_balance,
            available_to_withdraw=available_to_withdraw,
            equity=equity,
            available_balance=available_balance,
            raw=payload,
        )


@dataclass(slots=True)
class BybitAccountBalance:
    coins: tuple[BybitCoinBalance, ...]
    raw: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BybitAccountBalance":
        coins_raw = payload.get("coin")
        coins: list[BybitCoinBalance] = []
        if isinstance(coins_raw, Sequence):
            for item in coins_raw:
                if isinstance(item, Mapping):
                    coins.append(BybitCoinBalance.from_payload(item))
        return cls(coins=tuple(coins), raw=payload)


@dataclass(slots=True)
class BybitWalletBalancePayload:
    accounts: tuple[BybitAccountBalance, ...]

    @classmethod
    def from_http(cls, payload: object) -> "BybitWalletBalancePayload":
        if not isinstance(payload, Mapping):
            raise TypeError("Bybit wallet balance payload must be a mapping")
        result = payload.get("result")
        accounts: list[BybitAccountBalance] = []
        if isinstance(result, Mapping):
            entries = result.get("list")
            if isinstance(entries, Sequence):
                for item in entries:
                    if isinstance(item, Mapping):
                        accounts.append(BybitAccountBalance.from_payload(item))
        return cls(tuple(accounts))


def _select_first_available(values: Iterable[float | None]) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None
