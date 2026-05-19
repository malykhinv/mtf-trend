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

from ..clock import utc_now_ms
from ..contracts import Live2Component, Live2Event, Live2Severity
from ..state import SymbolStateStore
from .candles import Live2AggTradeEvent
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
    request_sleep_seconds: float = 0.03
    error_limit: int = 20

    def __post_init__(self) -> None:
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be > 0")
        if self.max_symbols <= 0:
            raise ValueError("max_symbols must be > 0")
        if not 1 <= self.max_trades_per_symbol <= 1000:
            raise ValueError("max_trades_per_symbol must be in [1, 1000]")
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
    lookback_minutes: int = 0
    max_trades_per_symbol: int = 0
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
            "lookback_minutes": self.lookback_minutes,
            "max_trades_per_symbol": self.max_trades_per_symbol,
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
            )
        limited_symbols = tuple(symbols[: self.config.max_symbols])
        start_time_ms = max(0, effective_now_ms - int(self.config.lookback_minutes) * 60_000)
        trades_loaded = 0
        symbols_warmed = 0
        errors: list[str] = []
        for index, symbol in enumerate(limited_symbols, start=1):
            last_error = ""
            try:
                rows = self.exchange_client.fetch_binance_agg_trades(
                    symbol=symbol,
                    params={
                        "symbol": symbol_to_market_id(symbol),
                        "startTime": start_time_ms,
                        "endTime": effective_now_ms,
                        "limit": self.config.max_trades_per_symbol,
                    },
                )
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
            parsed = tuple(_parse_aggtrade_row(symbol=symbol, row=row) for row in rows)
            trades = tuple(trade for trade in parsed if trade is not None)
            if trades:
                self.state_store.update_aggtrade_many(trades, received_at_ms=started_at_ms)
                trades_loaded += len(trades)
                symbols_warmed += 1
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
            lookback_minutes=self.config.lookback_minutes,
            max_trades_per_symbol=self.config.max_trades_per_symbol,
            errors=tuple(errors[: self.config.error_limit]),
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
