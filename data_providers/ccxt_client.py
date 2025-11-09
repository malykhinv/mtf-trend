from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping, MutableMapping, Optional, Sequence, TypedDict

import ccxt


class OHLCV(TypedDict):
    """Normalized OHLCV candle returned by CCXT."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class TradingStats(TypedDict, total=False):
    """Trading statistics derived from CCXT ticker payload."""

    symbol: str
    last_price: float
    price_change_percent: float
    avg_daily_volume_usdt: float
    trades_24h: int


class _CcxtMarketInfo(TypedDict, total=False):
    """Subset of raw market information needed by the application."""

    symbol: str
    swap: bool
    future: bool
    active: bool
    info: "_CcxtExchangeSpecific"


class _CcxtExchangeSpecific(TypedDict, total=False):
    onboardDate: int


@dataclass(frozen=True)
class CcxtClientConfig:
    """Configuration required to bootstrap the CCXT client wrapper."""

    exchange_name: str
    api_key: str | None
    api_secret: str | None
    htf: str
    ltf: str
    rest_htf: bool
    rest_ltf: bool


class CcxtClient:
    """Thin wrapper around CCXT tailored for the application's needs."""

    _DEFAULT_LIMIT: Final[int] = 150

    def __init__(self, config: CcxtClientConfig) -> None:
        self._config = config
        self._exchange = self._create_exchange()
        self._markets: MutableMapping[str, _CcxtMarketInfo] | None = None

    def _create_exchange(self) -> ccxt.Exchange:
        exchange_class = getattr(ccxt, self._config.exchange_name)
        exchange: ccxt.Exchange = exchange_class({
            "apiKey": self._config.api_key,
            "secret": self._config.api_secret,
            "enableRateLimit": True,
        })
        return exchange

    # ------------------------------------------------------------------
    # Market helpers
    # ------------------------------------------------------------------
    def _ensure_markets_loaded(self) -> None:
        if self._markets is None:
            raw_markets = self._exchange.load_markets()
            self._markets = {symbol: _CcxtMarketInfo(**market) for symbol, market in raw_markets.items()}

    def get_futures_symbols(self) -> list[str]:
        """Return a sorted list of futures (swap) symbols available on the exchange."""

        self._ensure_markets_loaded()
        assert self._markets is not None
        symbols: list[str] = []
        for symbol, market in self._markets.items():
            if not market.get("active", True):
                continue
            if market.get("swap") or market.get("future"):
                symbols.append(symbol)
        symbols.sort()
        return symbols

    def get_market(self, symbol: str) -> _CcxtMarketInfo:
        """Return cached market metadata for ``symbol``."""

        self._ensure_markets_loaded()
        assert self._markets is not None
        market = self._markets.get(symbol)
        if market is None:
            raise KeyError(f"Маркет {symbol} не найден")
        return market

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------
    def _normalize_ohlcv(self, raw: Sequence[float | int]) -> OHLCV:
        if len(raw) < 6:
            raise ValueError("CCXT вернул некорректную свечу")
        timestamp, open_, high, low, close, volume = raw[:6]
        return OHLCV(
            timestamp=int(timestamp),
            open=float(open_),
            high=float(high),
            low=float(low),
            close=float(close),
            volume=float(volume),
        )

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        since: Optional[int] = None,
        limit: int | None = None,
    ) -> list[OHLCV]:
        """Fetch OHLCV candles and normalise them into dictionaries."""

        raw_candles = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit or self._DEFAULT_LIMIT)
        return [self._normalize_ohlcv(candle) for candle in raw_candles]

    def fetch_htf_ohlcv(self, symbol: str, *, since: Optional[int] = None, limit: int | None = None) -> list[OHLCV]:
        """Fetch OHLCV candles for the configured high timeframe."""

        if not self._config.rest_htf:
            return []
        return self.fetch_ohlcv(symbol, self._config.htf, since=since, limit=limit)

    def fetch_ltf_ohlcv(self, symbol: str, *, since: Optional[int] = None, limit: int | None = None) -> list[OHLCV]:
        """Fetch OHLCV candles for the configured low timeframe."""

        if not self._config.rest_ltf:
            return []
        return self.fetch_ohlcv(symbol, self._config.ltf, since=since, limit=limit)

    def fetch_daily_ohlcv(self, symbol: str, *, limit: int) -> list[OHLCV]:
        """Fetch daily candles used for averaging volume statistics."""

        if limit <= 0:
            raise ValueError("limit должен быть положительным")
        raw_candles = self._exchange.fetch_ohlcv(symbol, timeframe="1d", limit=limit)
        return [self._normalize_ohlcv(candle) for candle in raw_candles]

    # ------------------------------------------------------------------
    # Trading stats
    # ------------------------------------------------------------------
    def fetch_trading_stats(self, symbol: str, *, avg_days: int) -> TradingStats:
        """Return lightweight trading statistics for ``symbol``."""

        if avg_days <= 0:
            raise ValueError("avg_days должен быть положительным")

        ticker: Mapping[str, Any] = self._exchange.fetch_ticker(symbol)
        info = ticker.get("info", {}) if isinstance(ticker, Mapping) else {}

        last_price = float(ticker.get("last", info.get("lastPrice", 0.0))) if isinstance(ticker, Mapping) else 0.0
        change_percent = float(ticker.get("percentage", info.get("priceChangePercent", 0.0))) if isinstance(ticker, Mapping) else 0.0

        quote_volume_raw = ticker.get("quoteVolume") if isinstance(ticker, Mapping) else None
        if quote_volume_raw is None:
            quote_volume_raw = info.get("quoteVolume") if isinstance(info, Mapping) else 0.0
        quote_volume = float(quote_volume_raw or 0.0)

        trades_raw: Any = info.get("count") if isinstance(info, Mapping) else None
        if trades_raw is None and isinstance(ticker, Mapping):
            trades_raw = ticker.get("trades")
        trades = int(trades_raw or 0)

        daily_candles = self.fetch_daily_ohlcv(symbol, limit=avg_days)
        if not daily_candles:
            avg_daily_volume = quote_volume
        else:
            total_quote_volume = 0.0
            for candle in daily_candles:
                total_quote_volume += candle["close"] * candle["volume"]
            avg_daily_volume = total_quote_volume / len(daily_candles)

        return TradingStats(
            symbol=symbol,
            last_price=last_price,
            price_change_percent=change_percent,
            avg_daily_volume_usdt=avg_daily_volume,
            trades_24h=trades,
        )


__all__ = [
    "CcxtClient",
    "CcxtClientConfig",
    "OHLCV",
    "TradingStats",
]
