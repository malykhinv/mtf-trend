"""Startup REST warm-up for live2 aggTrade candle rings.

This module is startup-only. It hydrates the in-memory candle rings before the
WebSocket decision path starts. It is deliberately not available from the signal
hot path, so a late/missing decision never waits for REST.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from data.exchanges.ccxt_types import CcxtAggTradePayload
from domain.enums.timeframe import Timeframe

from ..clock import utc_now_ms
from ..contracts import Live2Component, Live2Event, Live2Severity
from ..state import SymbolStateStore
from .candles import Live2AggTradeEvent, Live2Candle
from .common import optional_float, optional_int, symbol_to_market_id


@runtime_checkable
class Live2StartupAggTradeExchange(Protocol):
    """Typed startup-only boundary for Binance aggTrades warm-up."""

    def fetch_binance_agg_trades(
        self,
        *,
        symbol: str,
        params: dict[str, object],
    ) -> list[CcxtAggTradePayload]:
        ...


@runtime_checkable
class Live2StartupHtfBaselineExchange(Protocol):
    """Startup-only boundary for raw Binance HTF klines with flow columns."""

    def fetch_binance_klines(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        limit: int = 1000,
    ) -> list[list[object]]:
        ...


@dataclass(frozen=True, slots=True)
class Live2StartupWarmupProgress:
    current_index: int
    symbols_total: int
    symbol: str
    symbols_warmed: int
    symbols_failed: int
    trades_loaded: int
    last_error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "current_index": self.current_index,
            "symbols_total": self.symbols_total,
            "symbol": self.symbol,
            "symbols_warmed": self.symbols_warmed,
            "symbols_failed": self.symbols_failed,
            "trades_loaded": self.trades_loaded,
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class Live2StartupWarmupConfig:
    enabled: bool = True
    lookback_minutes: int = 15
    max_symbols: int = 240
    max_trades_per_symbol: int = 1000
    max_pages_per_symbol: int = 1
    request_sleep_seconds: float = 0.03
    error_limit: int = 20

    def __post_init__(self) -> None:
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be > 0")
        if self.max_symbols <= 0:
            raise ValueError("max_symbols must be > 0")
        if not 1 <= self.max_trades_per_symbol <= 1000:
            raise ValueError("max_trades_per_symbol must be in [1, 1000]")
        if self.max_pages_per_symbol <= 0:
            raise ValueError("max_pages_per_symbol must be > 0")
        if self.request_sleep_seconds < 0:
            raise ValueError("request_sleep_seconds must be >= 0")
        if self.error_limit <= 0:
            raise ValueError("error_limit must be > 0")


@dataclass(frozen=True, slots=True)
class Live2StartupWarmupResult:
    status: str
    reason: str
    started_at_ms: int
    completed_at_ms: int
    symbols_requested: int = 0
    symbols_warmed: int = 0
    symbols_failed: int = 0
    trades_loaded: int = 0
    pages_loaded: int = 0
    symbols_incomplete: int = 0
    lookback_minutes: int = 0
    max_trades_per_symbol: int = 0
    max_pages_per_symbol: int = 0
    source: str = "binance_futures_aggTrades_startup_rest"
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.status in {"ready", "partial"}

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "duration_ms": max(0, self.completed_at_ms - self.started_at_ms),
            "symbols_requested": self.symbols_requested,
            "symbols_warmed": self.symbols_warmed,
            "symbols_failed": self.symbols_failed,
            "trades_loaded": self.trades_loaded,
            "pages_loaded": self.pages_loaded,
            "symbols_incomplete": self.symbols_incomplete,
            "lookback_minutes": self.lookback_minutes,
            "max_trades_per_symbol": self.max_trades_per_symbol,
            "max_pages_per_symbol": self.max_pages_per_symbol,
            "source": self.source,
            "errors": list(self.errors),
        }

    def as_event(self) -> Live2Event:
        if self.status == "ready":
            severity = Live2Severity.INFO
        elif self.status == "partial":
            severity = Live2Severity.WARNING
        else:
            severity = Live2Severity.ERROR
        return Live2Event(
            event_type="startup_aggtrade_warmup_completed",
            component=Live2Component.MARKET_DATA,
            severity=severity,
            message=self.reason,
            data=self.as_dict(),
        )


class Live2StartupAggTradeWarmup:
    """Hydrate candle rings from recent real aggTrades at startup."""

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        exchange_client: Live2StartupAggTradeExchange | None,
        config: Live2StartupWarmupConfig,
    ) -> None:
        self.state_store = state_store
        self.exchange_client = exchange_client
        self.config = config

    def run(
        self,
        symbols: tuple[str, ...],
        *,
        now_ms: int | None = None,
        progress: Callable[[Live2StartupWarmupProgress], None] | None = None,
    ) -> Live2StartupWarmupResult:
        started_at_ms = utc_now_ms()
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        if not self.config.enabled:
            return Live2StartupWarmupResult(
                status="disabled",
                reason="startup_aggtrade_warmup_disabled",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
                symbols_requested=0,
                lookback_minutes=self.config.lookback_minutes,
                max_trades_per_symbol=self.config.max_trades_per_symbol,
                max_pages_per_symbol=self.config.max_pages_per_symbol,
            )
        if self.exchange_client is None:
            return Live2StartupWarmupResult(
                status="not_ready",
                reason="startup_aggtrade_warmup_exchange_client_missing",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
                symbols_requested=len(symbols),
                lookback_minutes=self.config.lookback_minutes,
                max_trades_per_symbol=self.config.max_trades_per_symbol,
                max_pages_per_symbol=self.config.max_pages_per_symbol,
            )
        if not isinstance(self.exchange_client, Live2StartupAggTradeExchange):
            return Live2StartupWarmupResult(
                status="not_ready",
                reason="startup_aggtrade_warmup_boundary_missing_fetch_binance_agg_trades",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
                symbols_requested=len(symbols),
                lookback_minutes=self.config.lookback_minutes,
                max_trades_per_symbol=self.config.max_trades_per_symbol,
                max_pages_per_symbol=self.config.max_pages_per_symbol,
            )
        limited_symbols = tuple(symbols[: self.config.max_symbols])
        start_time_ms = max(0, effective_now_ms - int(self.config.lookback_minutes) * 60_000)
        trades_loaded = 0
        pages_loaded = 0
        symbols_warmed = 0
        symbols_incomplete = 0
        errors: list[str] = []
        for index, symbol in enumerate(limited_symbols, start=1):
            last_error = ""
            page_start_ms = start_time_ms
            symbol_trades_loaded = 0
            symbol_incomplete = False
            try:
                for _page in range(self.config.max_pages_per_symbol):
                    rows = self.exchange_client.fetch_binance_agg_trades(
                        symbol=symbol,
                        params={
                            "symbol": symbol_to_market_id(symbol),
                            "startTime": page_start_ms,
                            "endTime": effective_now_ms,
                            "limit": self.config.max_trades_per_symbol,
                        },
                    )
                    pages_loaded += 1
                    parsed = tuple(_parse_aggtrade_row(symbol=symbol, row=row) for row in rows)
                    trades = tuple(trade for trade in parsed if trade is not None)
                    if trades:
                        self.state_store.update_aggtrade_many(trades, received_at_ms=started_at_ms)
                        trades_loaded += len(trades)
                        symbol_trades_loaded += len(trades)
                    if len(rows) < self.config.max_trades_per_symbol or not trades:
                        break
                    last_trade_time_ms = max(int(trade.trade_time_ms) for trade in trades)
                    next_start_ms = last_trade_time_ms + 1
                    if next_start_ms <= page_start_ms or next_start_ms > effective_now_ms:
                        break
                    page_start_ms = next_start_ms
                else:
                    symbol_incomplete = True
            except Exception as exc:  # pragma: no cover - exchange boundary
                last_error = f"{type(exc).__name__}:{str(exc)[:160]}"
                errors.append(f"{symbol}:{last_error}")
                if progress is not None:
                    progress(
                        Live2StartupWarmupProgress(
                            current_index=index,
                            symbols_total=len(limited_symbols),
                            symbol=symbol,
                            symbols_warmed=symbols_warmed,
                            symbols_failed=len(errors),
                            trades_loaded=trades_loaded,
                            last_error=last_error,
                        )
                    )
                if len(errors) >= self.config.error_limit:
                    break
                if self.config.request_sleep_seconds > 0:
                    time.sleep(self.config.request_sleep_seconds)
                continue
            if symbol_trades_loaded > 0:
                symbols_warmed += 1
            if symbol_incomplete:
                symbols_incomplete += 1
            if progress is not None and (index == 1 or index == len(limited_symbols) or index % 10 == 0 or last_error):
                progress(
                    Live2StartupWarmupProgress(
                        current_index=index,
                        symbols_total=len(limited_symbols),
                        symbol=symbol,
                        symbols_warmed=symbols_warmed,
                        symbols_failed=len(errors),
                        trades_loaded=trades_loaded,
                        last_error=last_error,
                    )
                )
            if self.config.request_sleep_seconds > 0:
                time.sleep(self.config.request_sleep_seconds)
        symbols_failed = len(errors)
        if symbols_warmed == len(limited_symbols) and not errors:
            status = "ready"
            reason = "startup_aggtrade_warmup_ready"
        elif symbols_warmed > 0:
            status = "partial"
            reason = "startup_aggtrade_warmup_partial"
        else:
            status = "not_ready"
            reason = "startup_aggtrade_warmup_loaded_no_trades"
        return Live2StartupWarmupResult(
            status=status,
            reason=reason,
            started_at_ms=started_at_ms,
            completed_at_ms=utc_now_ms(),
            symbols_requested=len(limited_symbols),
            symbols_warmed=symbols_warmed,
            symbols_failed=symbols_failed,
            trades_loaded=trades_loaded,
            pages_loaded=pages_loaded,
            symbols_incomplete=symbols_incomplete,
            lookback_minutes=self.config.lookback_minutes,
            max_trades_per_symbol=self.config.max_trades_per_symbol,
            max_pages_per_symbol=self.config.max_pages_per_symbol,
            errors=tuple(errors[: self.config.error_limit]),
        )


@dataclass(frozen=True, slots=True)
class Live2StartupHtfBaselineConfig:
    enabled: bool = True
    lookback_minutes: int = 75
    max_symbols: int = 600
    request_sleep_seconds: float = 0.02
    error_limit: int = 20

    def __post_init__(self) -> None:
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be > 0")
        if self.max_symbols <= 0:
            raise ValueError("max_symbols must be > 0")
        if self.request_sleep_seconds < 0:
            raise ValueError("request_sleep_seconds must be >= 0")
        if self.error_limit <= 0:
            raise ValueError("error_limit must be > 0")


@dataclass(frozen=True, slots=True)
class Live2StartupHtfBaselineResult:
    status: str
    reason: str
    started_at_ms: int
    completed_at_ms: int
    symbols_requested: int = 0
    symbols_warmed: int = 0
    symbols_failed: int = 0
    candles_loaded: int = 0
    lookback_minutes: int = 0
    source: str = "binance_futures_klines_startup_rest_1m_htf_baseline"
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.status in {"ready", "partial"}

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "duration_ms": max(0, self.completed_at_ms - self.started_at_ms),
            "symbols_requested": self.symbols_requested,
            "symbols_warmed": self.symbols_warmed,
            "symbols_failed": self.symbols_failed,
            "candles_loaded": self.candles_loaded,
            "lookback_minutes": self.lookback_minutes,
            "source": self.source,
            "errors": list(self.errors),
        }

    def as_event(self) -> Live2Event:
        if self.status == "ready":
            severity = Live2Severity.INFO
        elif self.status == "partial":
            severity = Live2Severity.WARNING
        else:
            severity = Live2Severity.ERROR
        return Live2Event(
            event_type="startup_htf_baseline_warmup_completed",
            component=Live2Component.MARKET_DATA,
            severity=severity,
            message=self.reason,
            data=self.as_dict(),
        )


class Live2StartupHtfBaselineWarmup:
    """Hydrate closed 1m HTF baseline candles without loading universal 5s history."""

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        exchange_client: Live2StartupHtfBaselineExchange | None,
        config: Live2StartupHtfBaselineConfig,
    ) -> None:
        self.state_store = state_store
        self.exchange_client = exchange_client
        self.config = config

    def run(self, symbols: tuple[str, ...], *, now_ms: int | None = None) -> Live2StartupHtfBaselineResult:
        started_at_ms = utc_now_ms()
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        if not self.config.enabled:
            return Live2StartupHtfBaselineResult(
                status="disabled",
                reason="startup_htf_baseline_warmup_disabled",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
            )
        if self.exchange_client is None:
            return Live2StartupHtfBaselineResult(
                status="not_ready",
                reason="startup_htf_baseline_exchange_client_missing",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
                symbols_requested=len(symbols),
                lookback_minutes=self.config.lookback_minutes,
            )
        if not isinstance(self.exchange_client, Live2StartupHtfBaselineExchange):
            return Live2StartupHtfBaselineResult(
                status="not_ready",
                reason="startup_htf_baseline_boundary_missing_fetch_binance_klines",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
                symbols_requested=len(symbols),
                lookback_minutes=self.config.lookback_minutes,
            )
        limited_symbols = tuple(symbols[: self.config.max_symbols])
        timeframe_ms = int(Timeframe.M1.to_milliseconds())
        context_end_ms = (effective_now_ms // timeframe_ms) * timeframe_ms - 1
        context_start_ms = max(0, context_end_ms - int(self.config.lookback_minutes) * 60_000 + 1)
        candles_loaded = 0
        symbols_warmed = 0
        errors: list[str] = []
        for symbol in limited_symbols:
            try:
                rows = self.exchange_client.fetch_binance_klines(
                    symbol=symbol,
                    timeframe=Timeframe.M1,
                    start_timestamp_ms=context_start_ms,
                    end_timestamp_ms=context_end_ms,
                    limit=max(1, self.config.lookback_minutes + 2),
                )
            except Exception as exc:  # pragma: no cover - exchange boundary
                errors.append(f"{symbol}:{type(exc).__name__}:{str(exc)[:160]}")
                if len(errors) >= self.config.error_limit:
                    break
                if self.config.request_sleep_seconds > 0:
                    time.sleep(self.config.request_sleep_seconds)
                continue
            candles = tuple(
                candle
                for row in rows
                if (candle := _parse_binance_kline_1m(row)) is not None
            )
            if candles:
                self.state_store.append_closed_candles(symbol=symbol, candles=candles)
                candles_loaded += len(candles)
                symbols_warmed += 1
            if self.config.request_sleep_seconds > 0:
                time.sleep(self.config.request_sleep_seconds)
        symbols_failed = len(errors)
        if symbols_warmed == len(limited_symbols) and not errors:
            status = "ready"
            reason = "startup_htf_baseline_warmup_ready"
        elif symbols_warmed > 0:
            status = "partial"
            reason = "startup_htf_baseline_warmup_partial"
        else:
            status = "not_ready"
            reason = "startup_htf_baseline_warmup_loaded_no_candles"
        return Live2StartupHtfBaselineResult(
            status=status,
            reason=reason,
            started_at_ms=started_at_ms,
            completed_at_ms=utc_now_ms(),
            symbols_requested=len(limited_symbols),
            symbols_warmed=symbols_warmed,
            symbols_failed=symbols_failed,
            candles_loaded=candles_loaded,
            lookback_minutes=self.config.lookback_minutes,
            errors=tuple(errors[: self.config.error_limit]),
        )


def _parse_binance_kline_1m(row: list[object]) -> Live2Candle | None:
    if len(row) < 11:
        return None
    open_time_ms = optional_int(row[0])
    open_price = optional_float(row[1])
    high = optional_float(row[2])
    low = optional_float(row[3])
    close = optional_float(row[4])
    base_volume = optional_float(row[5])
    quote_volume = optional_float(row[7])
    number_of_trades = optional_int(row[8])
    taker_buy_quote_volume = optional_float(row[10])
    if (
        open_time_ms is None
        or open_price is None
        or high is None
        or low is None
        or close is None
        or base_volume is None
        or quote_volume is None
        or number_of_trades is None
    ):
        return None
    if open_price <= 0 or high <= 0 or low <= 0 or close <= 0 or quote_volume <= 0 or number_of_trades <= 0:
        return None
    timeframe_ms = int(Timeframe.M1.to_milliseconds())
    return Live2Candle(
        timeframe_ms=timeframe_ms,
        open_time_ms=int(open_time_ms),
        close_time_ms=int(open_time_ms) + timeframe_ms,
        open=float(open_price),
        high=float(high),
        low=float(low),
        close=float(close),
        base_volume=float(base_volume),
        quote_volume=float(quote_volume),
        number_of_trades=int(number_of_trades),
        taker_buy_quote_volume=0.0 if taker_buy_quote_volume is None else float(taker_buy_quote_volume),
        first_trade_time_ms=int(open_time_ms),
        last_trade_time_ms=int(open_time_ms) + timeframe_ms - 1,
        first_source="binance_futures_klines_startup_rest_1m_htf_baseline",
        last_source="binance_futures_klines_startup_rest_1m_htf_baseline",
    )


def _parse_aggtrade_row(*, symbol: str, row: CcxtAggTradePayload) -> Live2AggTradeEvent | None:
    market_id = symbol_to_market_id(symbol)
    if not market_id:
        return None
    price = optional_float(row.get("p") if "p" in row else row.get("price"))
    quantity = optional_float(row.get("q") if "q" in row else row.get("quantity"))
    trade_time_ms = optional_int(row.get("T") if "T" in row else row.get("transact_time"))
    if price is None or quantity is None or trade_time_ms is None:
        return None
    if price <= 0.0 or quantity <= 0.0:
        return None
    buyer_is_maker = _parse_bool(row.get("m") if "m" in row else row.get("is_buyer_maker"))
    quote_quantity = price * quantity
    return Live2AggTradeEvent(
        symbol=symbol,
        market_id=market_id,
        aggregate_trade_id=optional_int(row.get("a") if "a" in row else row.get("agg_trade_id")),
        event_time_ms=trade_time_ms,
        trade_time_ms=trade_time_ms,
        price=price,
        quantity=quantity,
        quote_quantity=quote_quantity,
        taker_buy_quote_quantity=0.0 if buyer_is_maker else quote_quantity,
        buyer_is_maker=buyer_is_maker,
        source="binance_futures_aggTrades_startup_rest",
    )


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)
