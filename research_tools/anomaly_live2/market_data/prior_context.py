"""24h prior pump-context polling for anomaly live2.

The poller keeps the selected live2 universe fresh with active/actionable
symbols prioritized first. It does not run inside the signal hot path and never
substitutes missing context with zero. The legacy category contract still uses
``*_72h`` field names; live2 populates those checks from this explicit 24h
context and exposes the source label in artifacts.
"""

from __future__ import annotations

import copy
import math
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import pandas as pd

from domain.enums.timeframe import Timeframe

from ..clock import utc_now_ms
from .candles import Live2Candle
from ..state import SymbolLive2Status, SymbolState, SymbolStateStore

LIVE2_PRIOR_CONTEXT_SOURCE = "binance_futures_ohlcv_5m_prior_context_24h_poll"
LIVE2_PRIOR_CONTEXT_ROLLING_WS_SOURCE = "binance_futures_aggtrade_ws_5m_rolling_prior_context"
LIVE2_PRIOR_CONTEXT_TIMEFRAME = Timeframe.M5
LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS = LIVE2_PRIOR_CONTEXT_TIMEFRAME.to_milliseconds()
LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS = 24
LIVE2_PRIOR_CONTEXT_MIN_COVERAGE_RATIO = 0.80
LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_IDS_PER_CANDLE = 5
LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_ID_RATIO = 0.10


@runtime_checkable
class Live2PriorContextExchange(Protocol):
    """Typed boundary required by the live2 prior-context poller."""

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Return OHLCV rows for one symbol/timeframe."""
        ...


@dataclass(frozen=True, slots=True)
class Live2PriorContextPollConfig:
    """Runtime throttles and thresholds for 24h prior-context polling."""

    poll_interval_seconds: float = 10.0
    symbol_cooldown_seconds: float = 600.0
    stale_ms: int = 1_200_000
    lookback_hours: int = LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS
    max_symbols_per_cycle: int = 10
    radar_symbol_ttl_ms: int = 60_000
    spike_return_pct: float = 0.03
    fast_fade_retrace_fraction: float = 0.55

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.symbol_cooldown_seconds <= 0:
            raise ValueError("symbol_cooldown_seconds must be > 0")
        if self.stale_ms <= 0:
            raise ValueError("stale_ms must be > 0")
        if self.lookback_hours != LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS:
            raise ValueError("live2 prior context lookback must be exactly 24h")
        if self.max_symbols_per_cycle <= 0:
            raise ValueError("max_symbols_per_cycle must be > 0")
        if self.radar_symbol_ttl_ms <= 0:
            raise ValueError("radar_symbol_ttl_ms must be > 0")
        if self.spike_return_pct <= 0:
            raise ValueError("spike_return_pct must be > 0")
        if not 0.0 <= self.fast_fade_retrace_fraction <= 1.0:
            raise ValueError("fast_fade_retrace_fraction must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class Live2PriorContextCandle:
    """One closed 5m candle accepted into the rolling prior-context buffer."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    source: str
    missing_aggtrade_ids: int = 0
    gap_tolerance: int = 0
    gap_tolerated: bool = False


@dataclass(frozen=True, slots=True)
class Live2PriorContextSnapshot:
    """Computed 24h prior context for one symbol."""

    symbol: str
    status: str
    reason: str
    fetched_at_ms: int
    context_start_ms: int | None = None
    context_end_ms: int | None = None
    rows_received: int = 0
    rows_used: int = 0
    prior_spike_count_24h: int | None = None
    prior_fast_fade_count_24h: int | None = None
    prior_high_24h: float | None = None
    prior_low_before_high_24h: float | None = None
    prior_low_after_high_24h: float | None = None
    spike_return_pct: float | None = None
    fast_fade_retrace_fraction: float | None = None
    source: str = LIVE2_PRIOR_CONTEXT_SOURCE
    rolling_candles: tuple[Live2PriorContextCandle, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "fetched_at_ms": self.fetched_at_ms,
            "context_start_ms": self.context_start_ms,
            "context_end_ms": self.context_end_ms,
            "rows_received": self.rows_received,
            "rows_used": self.rows_used,
            "prior_spike_count_24h": self.prior_spike_count_24h,
            "prior_fast_fade_count_24h": self.prior_fast_fade_count_24h,
            "prior_high_24h": self.prior_high_24h,
            "prior_low_before_high_24h": self.prior_low_before_high_24h,
            "prior_low_after_high_24h": self.prior_low_after_high_24h,
            "spike_return_pct": self.spike_return_pct,
            "fast_fade_retrace_fraction": self.fast_fade_retrace_fraction,
            "source": self.source,
            "lookback_hours": LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS,
            "timeframe": LIVE2_PRIOR_CONTEXT_TIMEFRAME.value,
        }


@dataclass(slots=True)
class Live2PriorContextPollStatus:
    """Thread-safe status snapshot for the prior-context source."""

    source_id: str = LIVE2_PRIOR_CONTEXT_SOURCE
    status: str = "not_started"
    ready_symbols: int = 0
    tracked_symbols: int = 0
    active_target_symbols: int = 0
    poll_interval_seconds: float = 0.0
    symbol_cooldown_seconds: float = 0.0
    stale_ms: int = 0
    lookback_hours: int = LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS
    max_symbols_per_cycle: int = 0
    radar_symbol_ttl_ms: int = 0
    spike_return_pct: float = 0.0
    fast_fade_retrace_fraction: float = 0.0
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
    total_ws_5m_candles_appended: int = 0
    total_ws_5m_gap_tolerated: int = 0
    total_ws_5m_gap_rejected: int = 0
    last_ws_5m_update_at_ms: int | None = None
    last_ws_5m_update_symbol: str = ""
    last_ws_5m_gap_reason: str = ""
    last_polled_symbols: tuple[str, ...] = ()
    last_target_symbols: tuple[str, ...] = ()
    reason: str = "not_started"

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "ready": self.status in {"running", "ready", "degraded"},
            "ready_symbols": self.ready_symbols,
            "tracked_symbols": self.tracked_symbols,
            "active_target_symbols": self.active_target_symbols,
            "poll_interval_seconds": self.poll_interval_seconds,
            "symbol_cooldown_seconds": self.symbol_cooldown_seconds,
            "stale_ms": self.stale_ms,
            "lookback_hours": self.lookback_hours,
            "max_symbols_per_cycle": self.max_symbols_per_cycle,
            "radar_symbol_ttl_ms": self.radar_symbol_ttl_ms,
            "spike_return_pct": self.spike_return_pct,
            "fast_fade_retrace_fraction": self.fast_fade_retrace_fraction,
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
            "total_ws_5m_candles_appended": self.total_ws_5m_candles_appended,
            "total_ws_5m_gap_tolerated": self.total_ws_5m_gap_tolerated,
            "total_ws_5m_gap_rejected": self.total_ws_5m_gap_rejected,
            "last_ws_5m_update_at_ms": self.last_ws_5m_update_at_ms,
            "last_ws_5m_update_symbol": self.last_ws_5m_update_symbol,
            "last_ws_5m_gap_reason": self.last_ws_5m_gap_reason,
            "maintenance_mode": "startup_rest_bootstrap_plus_live_ws_5m_rolling_append",
            "ws_5m_gap_tolerance": {
                "max_missing_aggtrade_ids_per_candle": LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_IDS_PER_CANDLE,
                "max_missing_aggtrade_id_ratio": LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_ID_RATIO,
            },
            "last_polled_symbols": list(self.last_polled_symbols),
            "last_target_symbols": list(self.last_target_symbols),
            "reason": self.reason,
        }


class Live2PriorContextPoller:
    """Background 24h context source for symbols that are already active in live2."""

    source_id = LIVE2_PRIOR_CONTEXT_SOURCE

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        exchange_client: Live2PriorContextExchange | None,
        config: Live2PriorContextPollConfig,
    ) -> None:
        self.state_store = state_store
        self.exchange_client = exchange_client
        self.config = config
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._last_poll_by_symbol: dict[str, int] = {}
        self._rolling_candles_by_symbol: dict[str, dict[int, Live2PriorContextCandle]] = {}
        self._last_live_5m_open_by_symbol: dict[str, int] = {}
        self._status = Live2PriorContextPollStatus(
            poll_interval_seconds=config.poll_interval_seconds,
            symbol_cooldown_seconds=config.symbol_cooldown_seconds,
            stale_ms=config.stale_ms,
            lookback_hours=config.lookback_hours,
            max_symbols_per_cycle=config.max_symbols_per_cycle,
            radar_symbol_ttl_ms=config.radar_symbol_ttl_ms,
            spike_return_pct=config.spike_return_pct,
            fast_fade_retrace_fraction=config.fast_fade_retrace_fraction,
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            now_ms = utc_now_ms()
            self._status.started_at_ms = now_ms
            if self.exchange_client is None or not isinstance(self.exchange_client, Live2PriorContextExchange):
                self._status.status = "disabled"
                self._status.reason = "exchange_client_has_no_fetch_ohlcv_boundary"
                self._ready_event.set()
                return
            self._status.status = "running"
            self._status.reason = "prior_context_poller_running_for_selected_universe"
        self._thread = threading.Thread(target=self._run_thread, name="live2-prior-context-poller", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def poll_symbols_once(
        self,
        symbols: tuple[str, ...],
        *,
        request_sleep_seconds: float = 0.0,
        error_limit: int | None = None,
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        """Synchronously hydrate 24h prior context for an explicit symbol set."""

        unique_symbols = tuple(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        started_at_ms = utc_now_ms()
        summary: dict[str, object] = {
            "source_id": self.source_id,
            "mode": "startup_explicit_symbol_prewarm",
            "lookback_hours": self.config.lookback_hours,
            "symbols_requested": len(unique_symbols),
            "symbols_polled": 0,
            "ok": 0,
            "empty": 0,
            "error": 0,
            "stopped_early": False,
            "started_at_ms": started_at_ms,
            "completed_at_ms": None,
            "ready": False,
            "reason": "not_started",
        }
        if not unique_symbols:
            summary.update({"completed_at_ms": utc_now_ms(), "reason": "no_symbols_requested"})
            return summary
        if self.exchange_client is None or not isinstance(self.exchange_client, Live2PriorContextExchange):
            with self._lock:
                self._status.status = "disabled"
                self._status.reason = "exchange_client_has_no_fetch_ohlcv_boundary"
                self._status.started_at_ms = self._status.started_at_ms or started_at_ms
                self._ready_event.set()
            summary.update({
                "completed_at_ms": utc_now_ms(),
                "reason": "exchange_client_has_no_fetch_ohlcv_boundary",
            })
            return summary
        with self._lock:
            self._status.started_at_ms = self._status.started_at_ms or started_at_ms
            self._status.status = "bootstrapping"
            self._status.reason = "startup_prior_24h_context_prewarm_running"
        polled: list[str] = []
        for index, symbol in enumerate(unique_symbols, start=1):
            if self._stop_event.is_set():
                summary["stopped_early"] = True
                break
            snapshot = self._fetch_symbol(symbol=symbol, now_ms=utc_now_ms())
            polled.append(symbol)
            self._apply_snapshot(snapshot)
            self._last_poll_by_symbol[symbol] = snapshot.fetched_at_ms
            with self._lock:
                self._status.total_requests += 1
                self._status.last_polled_symbols = tuple(polled[-25:])
                if snapshot.status == "ok":
                    summary["ok"] = int(summary["ok"]) + 1
                    self._status.total_success += 1
                    self._status.last_success_at_ms = snapshot.fetched_at_ms
                    self._ready_event.set()
                elif snapshot.status == "empty":
                    summary["empty"] = int(summary["empty"]) + 1
                    self._status.total_empty += 1
                else:
                    summary["error"] = int(summary["error"]) + 1
                    self._status.total_errors += 1
                    self._status.last_error_at_ms = snapshot.fetched_at_ms
                    self._status.last_error_type = snapshot.status
                    self._status.last_error = snapshot.reason[:500]
            summary["symbols_polled"] = len(polled)
            if progress is not None:
                progress({
                    **summary,
                    "current_index": index,
                    "symbol": symbol,
                    "last_status": snapshot.status,
                    "last_reason": snapshot.reason,
                })
            if error_limit is not None and int(summary["error"]) >= int(error_limit):
                summary["stopped_early"] = True
                break
            if request_sleep_seconds > 0:
                self._stop_event.wait(float(request_sleep_seconds))
        completed_at_ms = utc_now_ms()
        ready = int(summary["ok"]) > 0
        with self._lock:
            self._status.last_cycle_completed_at_ms = completed_at_ms
            self._status.last_polled_symbols = tuple(polled[-25:])
            if ready:
                self._status.status = "ready"
                self._status.reason = "startup_prior_24h_context_prewarm_completed"
            elif int(summary["error"]) > 0:
                self._status.status = "degraded"
                self._status.reason = "startup_prior_24h_context_prewarm_errors_without_success"
            else:
                self._status.status = "running"
                self._status.reason = "startup_prior_24h_context_prewarm_completed_without_ready_symbols"
        summary.update({
            "completed_at_ms": completed_at_ms,
            "ready": ready,
            "reason": self._status.reason,
        })
        return summary

    def wait_until_ready(self, timeout_seconds: float) -> bool:
        self._ready_event.wait(max(0.0, float(timeout_seconds)))
        return bool(self.status().get("ready_symbols", 0))

    def status(self) -> dict[str, object]:
        now_ms = utc_now_ms()
        counts = self.state_store.prior_context_counts(now_ms=now_ms, stale_ms=self.config.stale_ms)
        with self._lock:
            status = copy.copy(self._status)
            status.ready_symbols = int(counts.get("ok", 0))
            status.tracked_symbols = sum(int(value) for value in counts.values())
            status.active_target_symbols = len(self._target_symbols(now_ms=now_ms))
            if status.status == "running" and status.ready_symbols > 0:
                status.status = "ready"
                status.reason = "prior_24h_context_ready_for_some_active_symbols"
            elif status.status == "ready" and status.ready_symbols <= 0:
                status.status = "running"
                status.reason = "waiting_for_prior_24h_context_on_active_symbols"
            elif status.status == "running" and status.total_errors > 0 and status.total_success <= 0:
                status.status = "degraded"
                status.reason = "prior_context_poller_errors_without_success"
            return status.as_dict()

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
            except Exception as exc:  # defensive: the thread must not die silently
                with self._lock:
                    self._status.status = "degraded"
                    self._status.reason = "prior_context_poller_thread_error"
                    self._status.last_error_at_ms = utc_now_ms()
                    self._status.last_error_type = type(exc).__name__
                    self._status.last_error = str(exc)[:500]
                    self._status.total_errors += 1
            self._stop_event.wait(self.config.poll_interval_seconds)

    def _run_cycle(self) -> None:
        now_ms = utc_now_ms()
        live_append_count = self._apply_live_closed_5m_candles(now_ms=now_ms)
        targets = self._eligible_symbols(now_ms=now_ms)
        with self._lock:
            self._status.total_cycles += 1
            self._status.last_cycle_started_at_ms = now_ms
            self._status.last_target_symbols = targets[:25]
        polled: list[str] = []
        for symbol in targets[: self.config.max_symbols_per_cycle]:
            if self._stop_event.is_set():
                break
            snapshot = self._fetch_symbol(symbol=symbol, now_ms=utc_now_ms())
            polled.append(symbol)
            self._apply_snapshot(snapshot)
            self._last_poll_by_symbol[symbol] = snapshot.fetched_at_ms
            with self._lock:
                self._status.total_requests += 1
                if snapshot.status == "ok":
                    self._status.total_success += 1
                    self._status.last_success_at_ms = snapshot.fetched_at_ms
                    self._ready_event.set()
                elif snapshot.status == "empty":
                    self._status.total_empty += 1
                else:
                    self._status.total_errors += 1
                    self._status.last_error_at_ms = snapshot.fetched_at_ms
                    self._status.last_error_type = snapshot.status
                    self._status.last_error = snapshot.reason[:500]
        with self._lock:
            self._status.last_polled_symbols = tuple(polled)
            self._status.last_cycle_completed_at_ms = utc_now_ms()
            if polled and self._status.status in {"running", "ready", "degraded"}:
                self._status.reason = "prior_context_poll_cycle_completed"
            elif live_append_count > 0 and self._status.status in {"running", "ready", "degraded"}:
                self._status.reason = "prior_context_live_ws_5m_roll_forward_completed"
            elif not polled and self._status.status in {"running", "ready", "degraded"}:
                self._status.reason = "no_active_symbols_due_for_prior_context_poll"

    def _eligible_symbols(self, *, now_ms: int) -> tuple[str, ...]:
        cooldown_ms = int(self.config.symbol_cooldown_seconds * 1000)
        due: list[tuple[int, int, str]] = []
        for symbol in self._target_symbols(now_ms=now_ms):
            state = self.state_store.get_or_create(symbol)
            context_age_ms = None
            if state.prior_context_last_seen_ms is not None:
                context_age_ms = int(now_ms) - int(state.prior_context_last_seen_ms)
            if (
                symbol in self._rolling_candles_by_symbol
                and state.prior_context_status == "ok"
                and context_age_ms is not None
                and context_age_ms <= self.config.stale_ms
            ):
                continue
            last_poll_ms = self._last_poll_by_symbol.get(symbol)
            if last_poll_ms is not None and now_ms - last_poll_ms < cooldown_ms:
                continue
            priority = self._symbol_priority(symbol=symbol, now_ms=now_ms)
            oldest_first_ms = -1 if last_poll_ms is None else int(last_poll_ms)
            due.append((priority, oldest_first_ms, symbol))
        due.sort(key=lambda item: (item[0], item[1], item[2]))
        return tuple(symbol for _, _, symbol in due)

    def _apply_snapshot(
        self,
        snapshot: Live2PriorContextSnapshot,
        *,
        maintenance_source: str = "",
        live_5m_candle: Live2PriorContextCandle | None = None,
        live_5m_gap_rejected: bool = False,
    ) -> None:
        if snapshot.ready and snapshot.rolling_candles:
            self._rolling_candles_by_symbol[snapshot.symbol] = {
                candle.timestamp: candle for candle in snapshot.rolling_candles
            }
        self.state_store.update_prior_context(
            symbol=snapshot.symbol,
            fetched_at_ms=snapshot.fetched_at_ms,
            context_start_ms=snapshot.context_start_ms,
            context_end_ms=snapshot.context_end_ms,
            rows_received=snapshot.rows_received,
            rows_used=snapshot.rows_used,
            prior_spike_count_24h=snapshot.prior_spike_count_24h,
            prior_fast_fade_count_24h=snapshot.prior_fast_fade_count_24h,
            prior_high_24h=snapshot.prior_high_24h,
            prior_low_before_high_24h=snapshot.prior_low_before_high_24h,
            prior_low_after_high_24h=snapshot.prior_low_after_high_24h,
            spike_return_pct=snapshot.spike_return_pct,
            fast_fade_retrace_fraction=snapshot.fast_fade_retrace_fraction,
            source=snapshot.source,
            status=snapshot.status,
            reason=snapshot.reason,
            maintenance_source=maintenance_source,
            live_5m_open_time_ms=None if live_5m_candle is None else live_5m_candle.timestamp,
            live_5m_close_time_ms=None if live_5m_candle is None else live_5m_candle.timestamp + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS,
            live_5m_gap_tolerated=False if live_5m_candle is None else live_5m_candle.gap_tolerated,
            live_5m_gap_rejected=live_5m_gap_rejected,
            live_5m_missing_aggtrade_ids=0 if live_5m_candle is None else live_5m_candle.missing_aggtrade_ids,
            live_5m_gap_tolerance=0 if live_5m_candle is None else live_5m_candle.gap_tolerance,
        )

    def _apply_live_closed_5m_candles(self, *, now_ms: int) -> int:
        appended = 0
        rejected = 0
        tolerated = 0
        for state in self.state_store.snapshot():
            if not state.universe_selected:
                continue
            ring = state.candle_book.rings.get(LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS)
            if ring is None:
                continue
            for candle in ring.closed_snapshot():
                last_open = self._last_live_5m_open_by_symbol.get(state.symbol)
                if last_open is not None and candle.open_time_ms <= last_open:
                    continue
                if candle.live_ws_trade_count <= 0:
                    continue
                prior_candle, reject_reason = _prior_context_candle_from_live_5m(candle)
                self._last_live_5m_open_by_symbol[state.symbol] = candle.open_time_ms
                if reject_reason:
                    rejected += 1
                    self._mark_live_5m_gap_rejected(
                        symbol=state.symbol,
                        now_ms=now_ms,
                        live_candle=prior_candle,
                        reason=reject_reason,
                    )
                    continue
                buffer = self._rolling_candles_by_symbol.setdefault(state.symbol, {})
                buffer[prior_candle.timestamp] = prior_candle
                context_end_ms = prior_candle.timestamp + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS - 1
                context_start_ms = context_end_ms - self.config.lookback_hours * 60 * 60 * 1000 + 1
                old_keys = [timestamp for timestamp in buffer if timestamp < context_start_ms]
                for timestamp in old_keys:
                    buffer.pop(timestamp, None)
                snapshot = _build_prior_context_snapshot_from_candles(
                    symbol=state.symbol,
                    candles=tuple(buffer.values()),
                    fetched_at_ms=now_ms,
                    context_start_ms=context_start_ms,
                    context_end_ms=context_end_ms,
                    spike_return_pct=self.config.spike_return_pct,
                    fast_fade_retrace_fraction=self.config.fast_fade_retrace_fraction,
                    source=LIVE2_PRIOR_CONTEXT_ROLLING_WS_SOURCE,
                    ready_reason="prior_24h_context_ready_from_startup_rest_plus_live_ws_5m_roll_forward",
                )
                self._apply_snapshot(
                    snapshot,
                    maintenance_source=LIVE2_PRIOR_CONTEXT_ROLLING_WS_SOURCE,
                    live_5m_candle=prior_candle,
                )
                appended += 1
                if prior_candle.gap_tolerated:
                    tolerated += 1
        if appended or rejected:
            with self._lock:
                self._status.total_ws_5m_candles_appended += appended
                self._status.total_ws_5m_gap_tolerated += tolerated
                self._status.total_ws_5m_gap_rejected += rejected
                self._status.last_ws_5m_update_at_ms = now_ms
        return appended

    def _mark_live_5m_gap_rejected(
        self,
        *,
        symbol: str,
        now_ms: int,
        live_candle: Live2PriorContextCandle,
        reason: str,
    ) -> None:
        snapshot = Live2PriorContextSnapshot(
            symbol=symbol,
            status="ws_gap_exceeds_tolerance",
            reason=reason,
            fetched_at_ms=now_ms,
            context_start_ms=None,
            context_end_ms=live_candle.timestamp + LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS - 1,
            rows_received=0,
            rows_used=0,
            spike_return_pct=self.config.spike_return_pct,
            fast_fade_retrace_fraction=self.config.fast_fade_retrace_fraction,
            source=LIVE2_PRIOR_CONTEXT_ROLLING_WS_SOURCE,
        )
        self._apply_snapshot(
            snapshot,
            maintenance_source=LIVE2_PRIOR_CONTEXT_ROLLING_WS_SOURCE,
            live_5m_candle=live_candle,
            live_5m_gap_rejected=True,
        )
        with self._lock:
            self._status.last_ws_5m_update_symbol = symbol
            self._status.last_ws_5m_gap_reason = reason

    def _target_symbols(self, *, now_ms: int) -> tuple[str, ...]:
        targets: list[str] = []
        for state in self.state_store.snapshot():
            if not state.universe_selected:
                continue
            targets.append(state.symbol)
        return tuple(dict.fromkeys(targets))

    def _symbol_priority(self, *, symbol: str, now_ms: int) -> int:
        state = self.state_store.get_or_create(symbol)
        if state.status == SymbolLive2Status.IN_POSITION:
            return 0
        if state.status == SymbolLive2Status.ACTIONABLE:
            return 1
        if state.last_verdict == "selected" or state.last_signal_category_id:
            return 2
        if state.status == SymbolLive2Status.WATCHING:
            return 3
        if state.live_aggtrade_last_seen_ms is not None and now_ms - state.live_aggtrade_last_seen_ms <= self.config.radar_symbol_ttl_ms:
            return 4
        return 9

    def _is_target_state(self, state: SymbolState, *, now_ms: int) -> bool:
        if state.status in {
            SymbolLive2Status.WATCHING,
            SymbolLive2Status.ACTIONABLE,
            SymbolLive2Status.IN_POSITION,
        }:
            return True
        if state.last_signal_category_id:
            return True
        if state.live_aggtrade_last_seen_ms is not None and now_ms - state.live_aggtrade_last_seen_ms <= self.config.radar_symbol_ttl_ms:
            return True
        return False

    def _fetch_symbol(self, *, symbol: str, now_ms: int) -> Live2PriorContextSnapshot:
        if self.exchange_client is None:
            return Live2PriorContextSnapshot(
                symbol=symbol,
                status="error",
                reason="exchange_client_missing",
                fetched_at_ms=now_ms,
            )
        # Exclude the latest forming 5m bucket. Prior context must describe the
        # already-matured 24h window before the current live impulse, not the live
        # impulse itself.
        context_end_ms = (now_ms // LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS) * LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS - 1
        context_start_ms = context_end_ms - self.config.lookback_hours * 60 * 60 * 1000 + 1
        if context_start_ms <= 0 or context_end_ms <= context_start_ms:
            return Live2PriorContextSnapshot(
                symbol=symbol,
                status="error",
                reason="invalid_prior_context_window",
                fetched_at_ms=now_ms,
                context_start_ms=max(0, context_start_ms),
                context_end_ms=max(0, context_end_ms),
            )
        try:
            frame = self.exchange_client.fetch_ohlcv(
                symbol,
                LIVE2_PRIOR_CONTEXT_TIMEFRAME,
                context_start_ms,
                context_end_ms,
            )
        except Exception as exc:
            return Live2PriorContextSnapshot(
                symbol=symbol,
                status="error",
                reason=f"fetch_ohlcv_prior_context_failed:{type(exc).__name__}:{str(exc)[:240]}",
                fetched_at_ms=now_ms,
                context_start_ms=context_start_ms,
                context_end_ms=context_end_ms,
            )
        return _build_prior_context_snapshot(
            symbol=symbol,
            frame=frame,
            fetched_at_ms=now_ms,
            context_start_ms=context_start_ms,
            context_end_ms=context_end_ms,
            spike_return_pct=self.config.spike_return_pct,
            fast_fade_retrace_fraction=self.config.fast_fade_retrace_fraction,
        )


def _build_prior_context_snapshot(
    *,
    symbol: str,
    frame: pd.DataFrame,
    fetched_at_ms: int,
    context_start_ms: int,
    context_end_ms: int,
    spike_return_pct: float,
    fast_fade_retrace_fraction: float,
) -> Live2PriorContextSnapshot:
    rows_received = 0 if frame is None else len(frame)
    normalized_result = _normalize_prior_context_frame(
        frame=frame,
        context_start_ms=context_start_ms,
        context_end_ms=context_end_ms,
    )
    if normalized_result["status"] != "ok":
        return Live2PriorContextSnapshot(
            symbol=symbol,
            status=str(normalized_result["status"]),
            reason=str(normalized_result["reason"]),
            fetched_at_ms=fetched_at_ms,
            context_start_ms=context_start_ms,
            context_end_ms=context_end_ms,
            rows_received=rows_received,
            rows_used=int(normalized_result.get("rows_used") or 0),
            spike_return_pct=spike_return_pct,
            fast_fade_retrace_fraction=fast_fade_retrace_fraction,
        )
    candles = tuple(normalized_result["candles"])
    return _build_prior_context_snapshot_from_candles(
        symbol=symbol,
        candles=candles,
        fetched_at_ms=fetched_at_ms,
        context_start_ms=context_start_ms,
        context_end_ms=context_end_ms,
        spike_return_pct=spike_return_pct,
        fast_fade_retrace_fraction=fast_fade_retrace_fraction,
        source=LIVE2_PRIOR_CONTEXT_SOURCE,
        rows_received=rows_received,
        ready_reason="prior_24h_context_ready_from_closed_5m_ohlcv",
    )


def _normalize_prior_context_frame(
    *,
    frame: pd.DataFrame,
    context_start_ms: int,
    context_end_ms: int,
) -> dict[str, object]:
    if frame is None or frame.empty:
        return {
            "status": "empty",
            "reason": "prior_context_ohlcv_empty",
            "rows_used": 0,
            "candles": (),
        }
    required = ["timestamp", "open", "high", "low", "close"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        return {
            "status": "invalid_schema",
            "reason": f"prior_context_ohlcv_missing_columns:{','.join(missing)}",
            "rows_used": 0,
            "candles": (),
        }
    normalized = frame.loc[:, required].copy()
    for column in required:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=required)
    normalized = normalized.loc[
        (normalized["timestamp"] >= context_start_ms)
        & (normalized["timestamp"] <= context_end_ms)
        & (normalized["open"] > 0)
        & (normalized["high"] > 0)
        & (normalized["low"] > 0)
        & (normalized["close"] > 0)
    ]
    normalized = normalized.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    rows_used = len(normalized)
    if normalized.empty:
        return {
            "status": "invalid_rows",
            "reason": "prior_context_ohlcv_has_no_positive_numeric_rows",
            "rows_used": 0,
            "candles": (),
        }
    candles = tuple(
        Live2PriorContextCandle(
            timestamp=int(row.timestamp),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            source=LIVE2_PRIOR_CONTEXT_SOURCE,
        )
        for row in normalized.itertuples(index=False)
    )
    return {
        "status": "ok",
        "reason": "prior_context_ohlcv_ready",
        "rows_used": rows_used,
        "candles": candles,
    }


def _build_prior_context_snapshot_from_candles(
    *,
    symbol: str,
    candles: tuple[Live2PriorContextCandle, ...],
    fetched_at_ms: int,
    context_start_ms: int,
    context_end_ms: int,
    spike_return_pct: float,
    fast_fade_retrace_fraction: float,
    source: str,
    ready_reason: str,
    rows_received: int | None = None,
) -> Live2PriorContextSnapshot:
    normalized = sorted(
        (
            candle
            for candle in candles
            if context_start_ms <= candle.timestamp <= context_end_ms
            and candle.open > 0
            and candle.high > 0
            and candle.low > 0
            and candle.close > 0
        ),
        key=lambda candle: candle.timestamp,
    )
    rows_used = len(normalized)
    expected_rows = max(1, int((LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS * 60) / 5))
    min_expected_rows = max(1, int(expected_rows * LIVE2_PRIOR_CONTEXT_MIN_COVERAGE_RATIO))
    if rows_used < min_expected_rows:
        return Live2PriorContextSnapshot(
            symbol=symbol,
            status="insufficient_coverage",
            reason=f"prior_context_24h_coverage_below_80pct:{rows_used}/{min_expected_rows}",
            fetched_at_ms=fetched_at_ms,
            context_start_ms=context_start_ms,
            context_end_ms=context_end_ms,
            rows_received=rows_used if rows_received is None else rows_received,
            rows_used=rows_used,
            spike_return_pct=spike_return_pct,
            fast_fade_retrace_fraction=fast_fade_retrace_fraction,
            source=source,
            rolling_candles=tuple(normalized),
        )
    spike_count = 0
    fast_fade_count = 0
    highest_price = -math.inf
    high_pos = -1
    for index, candle in enumerate(normalized):
        spike_return = (candle.high / candle.open) - 1.0
        if spike_return >= float(spike_return_pct):
            spike_count += 1
            spike_leg = candle.high - candle.open
            retrace_fraction = math.inf if spike_leg <= 0 else (candle.high - candle.close) / spike_leg
            if retrace_fraction >= float(fast_fade_retrace_fraction):
                fast_fade_count += 1
        if candle.high > highest_price:
            highest_price = candle.high
            high_pos = index
    prior_high = float(normalized[high_pos].high) if high_pos >= 0 else None
    low_before_high = min(candle.low for candle in normalized[: high_pos + 1]) if high_pos >= 0 else None
    low_after_high = min(candle.low for candle in normalized[high_pos:]) if high_pos >= 0 else None
    return Live2PriorContextSnapshot(
        symbol=symbol,
        status="ok",
        reason=ready_reason,
        fetched_at_ms=fetched_at_ms,
        context_start_ms=context_start_ms,
        context_end_ms=context_end_ms,
        rows_received=rows_used if rows_received is None else rows_received,
        rows_used=rows_used,
        prior_spike_count_24h=spike_count,
        prior_fast_fade_count_24h=fast_fade_count,
        prior_high_24h=prior_high,
        prior_low_before_high_24h=low_before_high,
        prior_low_after_high_24h=low_after_high,
        spike_return_pct=spike_return_pct,
        fast_fade_retrace_fraction=fast_fade_retrace_fraction,
        source=source,
        rolling_candles=tuple(normalized),
    )


def _prior_context_candle_from_live_5m(candle: Live2Candle) -> tuple[Live2PriorContextCandle, str]:
    missing_ids = max(0, int(candle.missing_agg_trade_id_count))
    tolerance = max(
        LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_IDS_PER_CANDLE,
        int(candle.number_of_trades * LIVE2_PRIOR_CONTEXT_WS_MAX_MISSING_AGGTRADE_ID_RATIO),
    )
    gap_tolerated = 0 < missing_ids <= tolerance
    prior_candle = Live2PriorContextCandle(
        timestamp=int(candle.open_time_ms),
        open=float(candle.open),
        high=float(candle.high),
        low=float(candle.low),
        close=float(candle.close),
        source=LIVE2_PRIOR_CONTEXT_ROLLING_WS_SOURCE,
        missing_aggtrade_ids=missing_ids,
        gap_tolerance=tolerance,
        gap_tolerated=gap_tolerated,
    )
    if missing_ids > tolerance:
        return (
            prior_candle,
            (
                "live_ws_5m_aggtrade_id_gap_exceeds_tolerance:"
                f"missing={missing_ids}:tolerance={tolerance}:trades={candle.number_of_trades}"
            ),
        )
    return prior_candle, ""
