"""Startup REST warm-up for live2 aggTrade candle rings.

This module is startup-only. It hydrates the in-memory candle rings before the
WebSocket decision path starts. It is deliberately not available from the signal
hot path, so a late/missing decision never waits for REST.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from data.exchanges.ccxt_types import CcxtAggTradePayload
from domain.enums.timeframe import Timeframe

from ..clock import utc_now_ms
from ..contracts import Live2Component, Live2Event, Live2Severity
from ..state import SymbolLive2Status, SymbolState, SymbolStateStore
from .candles import Live2AggTradeEvent, Live2Candle
from .common import optional_float, optional_int, symbol_to_market_id


BINANCE_KLINE_PAGE_LIMIT = 1500
LIVE2_ROLLING_CONTEXT_MIN_RECENT_1M_CANDLES = 1800
LIVE2_ROLLING_CONTEXT_MAINTENANCE_SOURCE = "binance_futures_klines_maintenance_rest_1m_rolling_context"


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
        symbols_failed = max(0, len(limited_symbols) - symbols_warmed)
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
                rows = self._fetch_paginated_1m_klines(
                    symbol=symbol,
                    start_timestamp_ms=context_start_ms,
                    end_timestamp_ms=context_end_ms,
                    timeframe_ms=timeframe_ms,
                )
            except Exception as exc:  # pragma: no cover - exchange boundary
                if len(errors) < self.config.error_limit:
                    errors.append(f"{symbol}:{type(exc).__name__}:{str(exc)[:160]}")
                if self.config.request_sleep_seconds > 0:
                    time.sleep(self.config.request_sleep_seconds)
                continue
            candles = tuple(
                sorted(
                    (candle for row in rows if (candle := _parse_binance_kline_1m(row)) is not None),
                    key=lambda item: item.open_time_ms,
                )
            )
            recent_contiguous_count = _recent_contiguous_1m_count(
                candles=candles,
                context_end_ms=context_end_ms,
                timeframe_ms=timeframe_ms,
            )
            if candles:
                self.state_store.append_closed_candles(symbol=symbol, candles=candles)
                candles_loaded += len(candles)
            if recent_contiguous_count >= LIVE2_ROLLING_CONTEXT_MIN_RECENT_1M_CANDLES:
                symbols_warmed += 1
            else:
                if len(errors) < self.config.error_limit:
                    errors.append(
                        f"{symbol}:incomplete_1m_htf_baseline:"
                        f"recent_contiguous={recent_contiguous_count}/"
                        f"{LIVE2_ROLLING_CONTEXT_MIN_RECENT_1M_CANDLES}:loaded={len(candles)}"
                    )
            if self.config.request_sleep_seconds > 0:
                time.sleep(self.config.request_sleep_seconds)
        symbols_failed = max(0, len(limited_symbols) - symbols_warmed)
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

    def _fetch_paginated_1m_klines(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        timeframe_ms: int,
    ) -> tuple[list[object], ...]:
        if self.exchange_client is None:
            return ()
        return _fetch_paginated_1m_klines(
            exchange_client=self.exchange_client,
            symbol=symbol,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
            timeframe_ms=timeframe_ms,
        )



@dataclass(frozen=True, slots=True)
class Live2RollingContextRestRepairConfig:
    """Bounded REST repair for live rolling 1m context gaps."""

    lookback_minutes: int = 2880
    min_recent_1m_candles: int = LIVE2_ROLLING_CONTEXT_MIN_RECENT_1M_CANDLES
    min_retry_interval_ms: int = 60_000
    request_sleep_seconds: float = 0.02

    def __post_init__(self) -> None:
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be > 0")
        if self.min_recent_1m_candles <= 0:
            raise ValueError("min_recent_1m_candles must be > 0")
        if self.min_retry_interval_ms < 0:
            raise ValueError("min_retry_interval_ms must be >= 0")
        if self.request_sleep_seconds < 0:
            raise ValueError("request_sleep_seconds must be >= 0")


@dataclass(frozen=True, slots=True)
class Live2RollingContextRestRepairResult:
    status: str
    reason: str
    symbol: str
    before_ms: int
    started_at_ms: int
    completed_at_ms: int
    candles_loaded: int = 0
    recent_contiguous_count: int = 0
    source: str = "binance_futures_klines_emergency_rest_1m_rolling_context"
    error: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "symbol": self.symbol,
            "before_ms": self.before_ms,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "duration_ms": max(0, self.completed_at_ms - self.started_at_ms),
            "candles_loaded": self.candles_loaded,
            "recent_contiguous_count": self.recent_contiguous_count,
            "source": self.source,
            "error": self.error,
        }


class Live2RollingContextRestRepair:
    """Repair missing/stale 1m rolling context with official REST klines.

    This is not a trading fallback and it does not invent candles.  It only
    appends real Binance 1m klines, including exchange-reported zero-volume
    candles, then the signal path rechecks the same continuity contract.
    """

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        exchange_client: Live2StartupHtfBaselineExchange | None,
        config: Live2RollingContextRestRepairConfig,
    ) -> None:
        self.state_store = state_store
        self.exchange_client = exchange_client
        self.config = config
        self._last_attempt_at_ms_by_symbol: dict[str, int] = {}

    def repair(self, *, symbol: str, before_ms: int, now_ms: int | None = None) -> Live2RollingContextRestRepairResult:
        started_at_ms = utc_now_ms()
        effective_now_ms = started_at_ms if now_ms is None else int(now_ms)
        last_attempt_at_ms = self._last_attempt_at_ms_by_symbol.get(symbol)
        if (
            last_attempt_at_ms is not None
            and self.config.min_retry_interval_ms > 0
            and effective_now_ms - int(last_attempt_at_ms) < self.config.min_retry_interval_ms
        ):
            return Live2RollingContextRestRepairResult(
                status="throttled",
                reason="rolling_1m_context_rest_repair_throttled",
                symbol=symbol,
                before_ms=int(before_ms),
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
            )
        self._last_attempt_at_ms_by_symbol[symbol] = effective_now_ms
        if self.exchange_client is None:
            return Live2RollingContextRestRepairResult(
                status="not_ready",
                reason="rolling_1m_context_rest_repair_exchange_client_missing",
                symbol=symbol,
                before_ms=int(before_ms),
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
            )
        if not isinstance(self.exchange_client, Live2StartupHtfBaselineExchange):
            return Live2RollingContextRestRepairResult(
                status="not_ready",
                reason="rolling_1m_context_rest_repair_boundary_missing_fetch_binance_klines",
                symbol=symbol,
                before_ms=int(before_ms),
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
            )
        timeframe_ms = int(Timeframe.M1.to_milliseconds())
        context_end_ms = max(0, int(before_ms) - timeframe_ms)
        context_start_ms = max(0, context_end_ms - int(self.config.lookback_minutes) * timeframe_ms + 1)
        try:
            rows = _fetch_paginated_1m_klines(
                exchange_client=self.exchange_client,
                symbol=symbol,
                start_timestamp_ms=context_start_ms,
                end_timestamp_ms=context_end_ms,
                timeframe_ms=timeframe_ms,
            )
            candles = tuple(
                sorted(
                    (candle for row in rows if (candle := _parse_binance_kline_1m(row, source="binance_futures_klines_emergency_rest_1m_rolling_context")) is not None),
                    key=lambda item: item.open_time_ms,
                )
            )
            if candles:
                self.state_store.append_closed_candles(symbol=symbol, candles=candles)
            recent_contiguous_count = _recent_contiguous_1m_count(
                candles=candles,
                context_end_ms=context_end_ms,
                timeframe_ms=timeframe_ms,
            )
            if self.config.request_sleep_seconds > 0:
                time.sleep(self.config.request_sleep_seconds)
        except Exception as exc:  # pragma: no cover - exchange boundary
            return Live2RollingContextRestRepairResult(
                status="error",
                reason="rolling_1m_context_rest_repair_failed",
                symbol=symbol,
                before_ms=int(before_ms),
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
                error=f"{type(exc).__name__}:{str(exc)[:180]}",
            )
        if recent_contiguous_count >= int(self.config.min_recent_1m_candles):
            status = "ready"
            reason = "rolling_1m_context_rest_repair_ready"
        elif candles:
            status = "partial"
            reason = "rolling_1m_context_rest_repair_incomplete"
        else:
            status = "not_ready"
            reason = "rolling_1m_context_rest_repair_no_candles"
        return Live2RollingContextRestRepairResult(
            status=status,
            reason=reason,
            symbol=symbol,
            before_ms=int(before_ms),
            started_at_ms=started_at_ms,
            completed_at_ms=utc_now_ms(),
            candles_loaded=len(candles),
            recent_contiguous_count=recent_contiguous_count,
        )



@dataclass(frozen=True, slots=True)
class Live2RollingContextMaintenanceConfig:
    """Low-priority official 1m kline maintenance for rolling context.

    This worker is intentionally outside the signal hot path. It keeps closed
    1m context continuous for selected symbols, but live 30s flow still comes
    only from aggTrade WS buckets and entry execution still uses live guards.
    """

    enabled: bool = True
    poll_interval_seconds: float = 10.0
    symbol_cooldown_seconds: float = 60.0
    lookback_minutes: int = 180
    max_symbols_per_cycle: int = 8
    request_sleep_seconds: float = 0.02
    active_symbol_ttl_ms: int = 60_000
    closed_candle_lag_ms: int = 5_000

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.symbol_cooldown_seconds <= 0:
            raise ValueError("symbol_cooldown_seconds must be > 0")
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be > 0")
        if self.max_symbols_per_cycle <= 0:
            raise ValueError("max_symbols_per_cycle must be > 0")
        if self.request_sleep_seconds < 0:
            raise ValueError("request_sleep_seconds must be >= 0")
        if self.active_symbol_ttl_ms <= 0:
            raise ValueError("active_symbol_ttl_ms must be > 0")
        if self.closed_candle_lag_ms < 0:
            raise ValueError("closed_candle_lag_ms must be >= 0")


@dataclass(slots=True)
class Live2RollingContextMaintenanceStatus:
    source_id: str = LIVE2_ROLLING_CONTEXT_MAINTENANCE_SOURCE
    status: str = "not_started"
    reason: str = "not_started"
    ready: bool = False
    enabled: bool = True
    poll_interval_seconds: float = 0.0
    symbol_cooldown_seconds: float = 0.0
    lookback_minutes: int = 0
    max_symbols_per_cycle: int = 0
    request_sleep_seconds: float = 0.0
    active_symbol_ttl_ms: int = 0
    closed_candle_lag_ms: int = 0
    started_at_ms: int | None = None
    last_cycle_started_at_ms: int | None = None
    last_cycle_completed_at_ms: int | None = None
    last_success_at_ms: int | None = None
    last_error_at_ms: int | None = None
    last_error_type: str = ""
    last_error: str = ""
    total_cycles: int = 0
    total_requests: int = 0
    total_success: int = 0
    total_empty: int = 0
    total_errors: int = 0
    total_skipped_current: int = 0
    total_symbols_considered: int = 0
    last_target_symbols: tuple[str, ...] = ()
    last_polled_symbols: tuple[str, ...] = ()
    last_loaded_candles: int = 0
    last_expected_open_time_ms: int | None = None
    active_target_symbols: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "enabled": self.enabled,
            "poll_interval_seconds": self.poll_interval_seconds,
            "symbol_cooldown_seconds": self.symbol_cooldown_seconds,
            "lookback_minutes": self.lookback_minutes,
            "max_symbols_per_cycle": self.max_symbols_per_cycle,
            "request_sleep_seconds": self.request_sleep_seconds,
            "active_symbol_ttl_ms": self.active_symbol_ttl_ms,
            "closed_candle_lag_ms": self.closed_candle_lag_ms,
            "started_at_ms": self.started_at_ms,
            "last_cycle_started_at_ms": self.last_cycle_started_at_ms,
            "last_cycle_completed_at_ms": self.last_cycle_completed_at_ms,
            "last_success_at_ms": self.last_success_at_ms,
            "last_error_at_ms": self.last_error_at_ms,
            "last_error_type": self.last_error_type,
            "last_error": self.last_error,
            "total_cycles": self.total_cycles,
            "total_requests": self.total_requests,
            "total_success": self.total_success,
            "total_empty": self.total_empty,
            "total_errors": self.total_errors,
            "total_skipped_current": self.total_skipped_current,
            "total_symbols_considered": self.total_symbols_considered,
            "last_target_symbols": list(self.last_target_symbols),
            "last_polled_symbols": list(self.last_polled_symbols),
            "last_loaded_candles": self.last_loaded_candles,
            "last_expected_open_time_ms": self.last_expected_open_time_ms,
            "active_target_symbols": self.active_target_symbols,
        }


@dataclass(frozen=True, slots=True)
class Live2RollingContextMaintenanceResult:
    symbol: str
    status: str
    reason: str
    fetched_at_ms: int
    expected_open_time_ms: int | None = None
    start_open_time_ms: int | None = None
    end_open_time_ms: int | None = None
    candles_loaded: int = 0
    latest_open_time_ms: int | None = None
    recent_contiguous_count: int = 0
    source: str = LIVE2_ROLLING_CONTEXT_MAINTENANCE_SOURCE
    error: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ok"


class Live2RollingContextMaintenance:
    """Async maintenance of closed official 1m klines for rolling context.

    The worker fetches only closed Binance 1m klines and appends/upserts them in
    the 1m candle ring with an explicit maintenance source label. It never
    blocks the deadline loop, never writes current partial candles, and never
    substitutes for aggTrade WS decision flow.
    """

    source_id = LIVE2_ROLLING_CONTEXT_MAINTENANCE_SOURCE

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        exchange_client: Live2StartupHtfBaselineExchange | None,
        config: Live2RollingContextMaintenanceConfig,
    ) -> None:
        self.state_store = state_store
        self.exchange_client = exchange_client
        self.config = config
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._last_poll_by_symbol: dict[str, int] = {}
        self._status = Live2RollingContextMaintenanceStatus(
            enabled=config.enabled,
            poll_interval_seconds=config.poll_interval_seconds,
            symbol_cooldown_seconds=config.symbol_cooldown_seconds,
            lookback_minutes=config.lookback_minutes,
            max_symbols_per_cycle=config.max_symbols_per_cycle,
            request_sleep_seconds=config.request_sleep_seconds,
            active_symbol_ttl_ms=config.active_symbol_ttl_ms,
            closed_candle_lag_ms=config.closed_candle_lag_ms,
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            self._status.started_at_ms = utc_now_ms()
            if not self.config.enabled:
                self._status.status = "disabled"
                self._status.ready = True
                self._status.reason = "rolling_1m_context_maintenance_disabled"
                return
            if self.exchange_client is None or not isinstance(self.exchange_client, Live2StartupHtfBaselineExchange):
                self._status.status = "disabled"
                self._status.ready = False
                self._status.reason = "exchange_client_has_no_fetch_binance_klines_boundary"
                return
            self._status.status = "running"
            self._status.ready = True
            self._status.reason = "rolling_1m_context_maintenance_running"
        self._thread = threading.Thread(target=self._run_thread, name="live2-rolling-1m-maintenance", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def status(self) -> dict[str, object]:
        now_ms = utc_now_ms()
        active_targets = self._eligible_symbols(now_ms=now_ms)
        with self._lock:
            self._status.active_target_symbols = len(active_targets)
            self._status.last_target_symbols = active_targets[:25]
            if self._status.status == "running" and self._status.total_errors > 0 and self._status.total_success <= 0:
                self._status.status = "degraded"
                self._status.reason = "rolling_1m_context_maintenance_errors_without_success"
                self._status.ready = False
            elif self._status.status == "degraded" and self._status.total_success > 0:
                self._status.status = "running"
                self._status.reason = "rolling_1m_context_maintenance_running_after_success"
                self._status.ready = True
            return self._status.as_dict()

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
            except Exception as exc:  # defensive: maintenance must not die silently
                with self._lock:
                    self._status.status = "degraded"
                    self._status.ready = False
                    self._status.reason = "rolling_1m_context_maintenance_thread_error"
                    self._status.last_error_at_ms = utc_now_ms()
                    self._status.last_error_type = type(exc).__name__
                    self._status.last_error = str(exc)[:500]
                    self._status.total_errors += 1
            self._stop_event.wait(self.config.poll_interval_seconds)

    def _run_cycle(self) -> None:
        now_ms = utc_now_ms()
        targets = self._eligible_symbols(now_ms=now_ms)
        with self._lock:
            self._status.total_cycles += 1
            self._status.last_cycle_started_at_ms = now_ms
            self._status.last_target_symbols = targets[:25]
            self._status.total_symbols_considered += len(targets)
        polled: list[str] = []
        loaded_candles = 0
        expected_open_ms = _last_closed_1m_open_ms(now_ms=now_ms, closed_candle_lag_ms=self.config.closed_candle_lag_ms)
        for symbol in targets[: self.config.max_symbols_per_cycle]:
            if self._stop_event.is_set():
                break
            result = self._fetch_symbol(symbol=symbol, now_ms=utc_now_ms(), expected_open_ms=expected_open_ms)
            self._last_poll_by_symbol[symbol] = result.fetched_at_ms
            polled.append(symbol)
            loaded_candles += int(result.candles_loaded)
            with self._lock:
                self._status.total_requests += 1
                if result.status == "ok":
                    self._status.total_success += 1
                    self._status.last_success_at_ms = result.fetched_at_ms
                elif result.status == "current":
                    self._status.total_skipped_current += 1
                elif result.status == "empty":
                    self._status.total_empty += 1
                else:
                    self._status.total_errors += 1
                    self._status.last_error_at_ms = result.fetched_at_ms
                    self._status.last_error_type = result.status
                    self._status.last_error = (result.error or result.reason)[:500]
            if self.config.request_sleep_seconds > 0:
                self._stop_event.wait(self.config.request_sleep_seconds)
        completed_at_ms = utc_now_ms()
        with self._lock:
            self._status.last_cycle_completed_at_ms = completed_at_ms
            self._status.last_polled_symbols = tuple(polled[-25:])
            self._status.last_loaded_candles = loaded_candles
            self._status.last_expected_open_time_ms = expected_open_ms
            if self._status.status in {"running", "degraded"}:
                if self._status.total_success > 0 or not polled:
                    self._status.status = "running"
                    self._status.ready = True
                    self._status.reason = "rolling_1m_context_maintenance_running"
                elif self._status.total_errors > 0:
                    self._status.status = "degraded"
                    self._status.ready = False
                    self._status.reason = "rolling_1m_context_maintenance_errors_without_success"

    def _fetch_symbol(self, *, symbol: str, now_ms: int, expected_open_ms: int) -> Live2RollingContextMaintenanceResult:
        fetched_at_ms = utc_now_ms()
        if expected_open_ms <= 0:
            return Live2RollingContextMaintenanceResult(
                symbol=symbol,
                status="current",
                reason="no_fully_closed_1m_candle_after_lag",
                fetched_at_ms=fetched_at_ms,
                expected_open_time_ms=expected_open_ms,
            )
        state = self._state_for_symbol(symbol)
        latest_open_ms = _latest_closed_1m_open_ms(state)
        timeframe_ms = int(Timeframe.M1.to_milliseconds())
        lookback_start_ms = max(0, expected_open_ms - (int(self.config.lookback_minutes) - 1) * timeframe_ms)
        required_recent = int(self.config.lookback_minutes)
        recent_contiguous_count = _recent_contiguous_1m_count_from_state(
            state,
            expected_open_ms=expected_open_ms,
        )
        if (
            latest_open_ms is not None
            and latest_open_ms >= expected_open_ms
            and recent_contiguous_count >= required_recent
        ):
            self.state_store.update_rolling_context_maintenance(
                symbol=symbol,
                fetched_at_ms=fetched_at_ms,
                status="current",
                reason="rolling_1m_context_current_and_contiguous",
                source=self.source_id,
                candles_loaded=0,
                latest_open_time_ms=latest_open_ms,
                expected_open_time_ms=expected_open_ms,
                recent_contiguous_count=recent_contiguous_count,
            )
            return Live2RollingContextMaintenanceResult(
                symbol=symbol,
                status="current",
                reason="rolling_1m_context_current_and_contiguous",
                fetched_at_ms=fetched_at_ms,
                expected_open_time_ms=expected_open_ms,
                latest_open_time_ms=latest_open_ms,
                recent_contiguous_count=recent_contiguous_count,
            )
        if latest_open_ms is None or recent_contiguous_count < required_recent:
            # A symbol can have a fresh WS-built 1m tail while still having a
            # no-trade gap immediately before it.  Fetch the bounded official
            # kline lookback window to bridge those zero-volume minutes instead
            # of treating a fresh tail as complete context.
            start_open_ms = lookback_start_ms
        else:
            start_open_ms = max(int(latest_open_ms) + timeframe_ms, lookback_start_ms)
        if start_open_ms > expected_open_ms:
            self.state_store.update_rolling_context_maintenance(
                symbol=symbol,
                fetched_at_ms=fetched_at_ms,
                status="current",
                reason="rolling_1m_context_current_after_start_check",
                source=self.source_id,
                candles_loaded=0,
                latest_open_time_ms=latest_open_ms,
                expected_open_time_ms=expected_open_ms,
                recent_contiguous_count=recent_contiguous_count,
            )
            return Live2RollingContextMaintenanceResult(
                symbol=symbol,
                status="current",
                reason="rolling_1m_context_current_after_start_check",
                fetched_at_ms=fetched_at_ms,
                expected_open_time_ms=expected_open_ms,
                latest_open_time_ms=latest_open_ms,
                recent_contiguous_count=recent_contiguous_count,
            )
        if self.exchange_client is None or not isinstance(self.exchange_client, Live2StartupHtfBaselineExchange):
            return Live2RollingContextMaintenanceResult(
                symbol=symbol,
                status="error",
                reason="exchange_client_has_no_fetch_binance_klines_boundary",
                fetched_at_ms=fetched_at_ms,
                expected_open_time_ms=expected_open_ms,
                start_open_time_ms=start_open_ms,
                end_open_time_ms=expected_open_ms,
            )
        try:
            rows = _fetch_paginated_1m_klines(
                exchange_client=self.exchange_client,
                symbol=symbol,
                start_timestamp_ms=start_open_ms,
                end_timestamp_ms=expected_open_ms + timeframe_ms - 1,
                timeframe_ms=timeframe_ms,
            )
            candles = tuple(
                sorted(
                    (
                        candle
                        for row in rows
                        if (candle := _parse_binance_kline_1m(row, source=self.source_id)) is not None
                    ),
                    key=lambda item: item.open_time_ms,
                )
            )
        except Exception as exc:  # pragma: no cover - exchange boundary
            self.state_store.update_rolling_context_maintenance(
                symbol=symbol,
                fetched_at_ms=fetched_at_ms,
                status="error",
                reason="rolling_1m_context_maintenance_fetch_failed",
                source=self.source_id,
                candles_loaded=0,
                latest_open_time_ms=latest_open_ms,
                expected_open_time_ms=expected_open_ms,
                recent_contiguous_count=recent_contiguous_count,
            )
            return Live2RollingContextMaintenanceResult(
                symbol=symbol,
                status="error",
                reason="rolling_1m_context_maintenance_fetch_failed",
                fetched_at_ms=fetched_at_ms,
                expected_open_time_ms=expected_open_ms,
                start_open_time_ms=start_open_ms,
                end_open_time_ms=expected_open_ms,
                latest_open_time_ms=latest_open_ms,
                recent_contiguous_count=recent_contiguous_count,
                error=f"{type(exc).__name__}:{str(exc)[:180]}",
            )
        if candles:
            self.state_store.append_closed_candles(symbol=symbol, candles=candles)
            latest_loaded_open_ms = int(candles[-1].open_time_ms)
            status = "ok"
            reason = "rolling_1m_context_maintenance_loaded_closed_klines"
        else:
            latest_loaded_open_ms = latest_open_ms
            status = "empty"
            reason = "rolling_1m_context_maintenance_empty_response"
        refreshed_state = self._state_for_symbol(symbol)
        recent_contiguous_after = _recent_contiguous_1m_count_from_state(
            refreshed_state,
            expected_open_ms=expected_open_ms,
        )
        self.state_store.update_rolling_context_maintenance(
            symbol=symbol,
            fetched_at_ms=fetched_at_ms,
            status=status,
            reason=reason,
            source=self.source_id,
            candles_loaded=len(candles),
            latest_open_time_ms=latest_loaded_open_ms,
            expected_open_time_ms=expected_open_ms,
            recent_contiguous_count=recent_contiguous_after,
        )
        return Live2RollingContextMaintenanceResult(
            symbol=symbol,
            status=status,
            reason=reason,
            fetched_at_ms=fetched_at_ms,
            expected_open_time_ms=expected_open_ms,
            start_open_time_ms=start_open_ms,
            end_open_time_ms=expected_open_ms,
            candles_loaded=len(candles),
            latest_open_time_ms=latest_loaded_open_ms,
            recent_contiguous_count=recent_contiguous_after,
        )

    def _eligible_symbols(self, *, now_ms: int) -> tuple[str, ...]:
        expected_open_ms = _last_closed_1m_open_ms(now_ms=now_ms, closed_candle_lag_ms=self.config.closed_candle_lag_ms)
        if expected_open_ms <= 0:
            return ()
        scored: list[tuple[int, int, str]] = []
        cooldown_ms = int(float(self.config.symbol_cooldown_seconds) * 1000.0)
        for state in self.state_store.snapshot():
            if not state.universe_selected:
                continue
            symbol = state.symbol
            last_poll_ms = self._last_poll_by_symbol.get(symbol, 0)
            if last_poll_ms > 0 and int(now_ms) - int(last_poll_ms) < cooldown_ms:
                continue
            latest_open_ms = _latest_closed_1m_open_ms(state)
            recent_contiguous_count = _recent_contiguous_1m_count_from_state(
                state,
                expected_open_ms=expected_open_ms,
            )
            if (
                latest_open_ms is not None
                and latest_open_ms >= expected_open_ms
                and recent_contiguous_count >= int(self.config.lookback_minutes)
            ):
                continue
            priority = _rolling_context_maintenance_priority(state, now_ms=now_ms, active_ttl_ms=self.config.active_symbol_ttl_ms)
            scored.append((priority, int(last_poll_ms), symbol))
        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return tuple(symbol for _, _, symbol in scored)

    def _state_for_symbol(self, symbol: str) -> SymbolState | None:
        for state in self.state_store.snapshot():
            if state.symbol == symbol:
                return state
        return None


def _rolling_context_maintenance_priority(state: SymbolState, *, now_ms: int, active_ttl_ms: int) -> int:
    if state.status == SymbolLive2Status.IN_POSITION:
        return 0
    if state.status == SymbolLive2Status.ACTIONABLE or state.last_signal_category_id:
        return 1
    if state.last_actionable_ms is not None and int(now_ms) - int(state.last_actionable_ms) <= int(active_ttl_ms):
        return 2
    if state.last_verdict in {"data_dependency_not_ready", "flow_freshness_reject"}:
        return 3
    return 10


def _latest_closed_1m_open_ms(state: SymbolState | None) -> int | None:
    if state is None:
        return None
    ring = state.candle_book.rings.get(int(Timeframe.M1.to_milliseconds()))
    if ring is None or not ring.closed:
        return None
    return int(ring.closed[-1].open_time_ms)


def _recent_contiguous_1m_count_from_state(
    state: SymbolState | None,
    *,
    expected_open_ms: int,
) -> int:
    if state is None or expected_open_ms <= 0:
        return 0
    timeframe_ms = int(Timeframe.M1.to_milliseconds())
    ring = state.candle_book.rings.get(timeframe_ms)
    if ring is None or not ring.closed:
        return 0
    available = {int(item.open_time_ms) for item in ring.closed if int(item.open_time_ms) <= int(expected_open_ms)}
    count = 0
    cursor_ms = int(expected_open_ms)
    while cursor_ms in available:
        count += 1
        cursor_ms -= timeframe_ms
    return count


def _last_closed_1m_open_ms(*, now_ms: int, closed_candle_lag_ms: int) -> int:
    timeframe_ms = int(Timeframe.M1.to_milliseconds())
    effective_ms = max(0, int(now_ms) - int(closed_candle_lag_ms))
    current_open_ms = (effective_ms // timeframe_ms) * timeframe_ms
    return max(0, current_open_ms - timeframe_ms)

def _fetch_paginated_1m_klines(
    *,
    exchange_client: Live2StartupHtfBaselineExchange,
    symbol: str,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int,
) -> tuple[list[object], ...]:
    rows_by_open_time: dict[int, list[object]] = {}
    cursor_ms = int(start_timestamp_ms)
    end_ms = int(end_timestamp_ms)
    while cursor_ms <= end_ms:
        remaining_candles = max(1, ((end_ms - cursor_ms) // int(timeframe_ms)) + 1)
        limit = min(BINANCE_KLINE_PAGE_LIMIT, remaining_candles)
        page = exchange_client.fetch_binance_klines(
            symbol=symbol,
            timeframe=Timeframe.M1,
            start_timestamp_ms=cursor_ms,
            end_timestamp_ms=end_ms,
            limit=limit,
        )
        if not page:
            break
        page_open_times: list[int] = []
        for row in page:
            if not isinstance(row, list):
                continue
            open_time_ms = optional_int(row[0]) if row else None
            if open_time_ms is None:
                continue
            if int(open_time_ms) < int(start_timestamp_ms) or int(open_time_ms) > end_ms:
                continue
            rows_by_open_time[int(open_time_ms)] = row
            page_open_times.append(int(open_time_ms))
        if not page_open_times:
            break
        next_cursor_ms = max(page_open_times) + int(timeframe_ms)
        if next_cursor_ms <= cursor_ms:
            break
        cursor_ms = next_cursor_ms
    return tuple(rows_by_open_time[key] for key in sorted(rows_by_open_time))

def _parse_binance_kline_1m(row: list[object], *, source: str = "binance_futures_klines_startup_rest_1m_htf_baseline") -> Live2Candle | None:
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
    if (
        open_price <= 0
        or high <= 0
        or low <= 0
        or close <= 0
        or base_volume < 0
        or quote_volume < 0
        or number_of_trades < 0
        or (taker_buy_quote_volume is not None and taker_buy_quote_volume < 0)
    ):
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
        first_source=source,
        last_source=source,
    )


def _recent_contiguous_1m_count(*, candles: tuple[Live2Candle, ...], context_end_ms: int, timeframe_ms: int) -> int:
    if not candles:
        return 0
    available = {int(item.open_time_ms) for item in candles if int(item.timeframe_ms) == int(timeframe_ms)}
    expected_open_ms = (int(context_end_ms) // int(timeframe_ms)) * int(timeframe_ms)
    count = 0
    cursor_ms = expected_open_ms
    while cursor_ms in available:
        count += 1
        cursor_ms -= int(timeframe_ms)
    return count


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
