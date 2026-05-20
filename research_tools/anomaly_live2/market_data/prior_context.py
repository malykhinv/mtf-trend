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
from ..state import SymbolLive2Status, SymbolState, SymbolStateStore

LIVE2_PRIOR_CONTEXT_SOURCE = "binance_futures_ohlcv_5m_prior_context_24h_poll"
LIVE2_PRIOR_CONTEXT_TIMEFRAME = Timeframe.M5
LIVE2_PRIOR_CONTEXT_TIMEFRAME_MS = LIVE2_PRIOR_CONTEXT_TIMEFRAME.to_milliseconds()
LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS = 24


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
            self.state_store.update_prior_context(
                symbol=symbol,
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
            )
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
            self.state_store.update_prior_context(
                symbol=symbol,
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
            )
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
            elif not polled and self._status.status in {"running", "ready", "degraded"}:
                self._status.reason = "no_active_symbols_due_for_prior_context_poll"

    def _eligible_symbols(self, *, now_ms: int) -> tuple[str, ...]:
        cooldown_ms = int(self.config.symbol_cooldown_seconds * 1000)
        due: list[tuple[int, int, str]] = []
        for symbol in self._target_symbols(now_ms=now_ms):
            last_poll_ms = self._last_poll_by_symbol.get(symbol)
            if last_poll_ms is not None and now_ms - last_poll_ms < cooldown_ms:
                continue
            priority = self._symbol_priority(symbol=symbol, now_ms=now_ms)
            oldest_first_ms = -1 if last_poll_ms is None else int(last_poll_ms)
            due.append((priority, oldest_first_ms, symbol))
        due.sort(key=lambda item: (item[0], item[1], item[2]))
        return tuple(symbol for _, _, symbol in due)

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
    if frame is None or frame.empty:
        return Live2PriorContextSnapshot(
            symbol=symbol,
            status="empty",
            reason="prior_context_ohlcv_empty",
            fetched_at_ms=fetched_at_ms,
            context_start_ms=context_start_ms,
            context_end_ms=context_end_ms,
            rows_received=0,
            spike_return_pct=spike_return_pct,
            fast_fade_retrace_fraction=fast_fade_retrace_fraction,
        )
    required = ["timestamp", "open", "high", "low", "close"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        return Live2PriorContextSnapshot(
            symbol=symbol,
            status="invalid_schema",
            reason=f"prior_context_ohlcv_missing_columns:{','.join(missing)}",
            fetched_at_ms=fetched_at_ms,
            context_start_ms=context_start_ms,
            context_end_ms=context_end_ms,
            rows_received=len(frame),
            spike_return_pct=spike_return_pct,
            fast_fade_retrace_fraction=fast_fade_retrace_fraction,
        )
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
    rows_received = len(frame)
    rows_used = len(normalized)
    min_expected_rows = max(1, int((LIVE2_PRIOR_CONTEXT_LOOKBACK_HOURS * 60) / 5 * 0.80))
    if normalized.empty:
        return Live2PriorContextSnapshot(
            symbol=symbol,
            status="invalid_rows",
            reason="prior_context_ohlcv_has_no_positive_numeric_rows",
            fetched_at_ms=fetched_at_ms,
            context_start_ms=context_start_ms,
            context_end_ms=context_end_ms,
            rows_received=rows_received,
            rows_used=0,
            spike_return_pct=spike_return_pct,
            fast_fade_retrace_fraction=fast_fade_retrace_fraction,
        )
    if rows_used < min_expected_rows:
        return Live2PriorContextSnapshot(
            symbol=symbol,
            status="insufficient_coverage",
            reason=f"prior_context_24h_coverage_below_80pct:{rows_used}/{min_expected_rows}",
            fetched_at_ms=fetched_at_ms,
            context_start_ms=context_start_ms,
            context_end_ms=context_end_ms,
            rows_received=rows_received,
            rows_used=rows_used,
            spike_return_pct=spike_return_pct,
            fast_fade_retrace_fraction=fast_fade_retrace_fraction,
        )
    open_price = normalized["open"].astype(float)
    high = normalized["high"].astype(float)
    low = normalized["low"].astype(float)
    close = normalized["close"].astype(float)
    spike_return = (high / open_price) - 1.0
    spike_mask = spike_return.ge(float(spike_return_pct))
    spike_count = int(spike_mask.sum())
    spike_leg = high - open_price
    retrace_fraction = (high - close) / spike_leg.replace(0.0, math.nan)
    fast_fade_count = int((spike_mask & retrace_fraction.ge(float(fast_fade_retrace_fraction))).sum())

    high_pos = int(high.to_numpy().argmax()) if not high.empty else -1
    prior_high = float(high.iloc[high_pos]) if high_pos >= 0 else None
    low_before_high = float(low.iloc[: high_pos + 1].min()) if high_pos >= 0 else None
    low_after_high = float(low.iloc[high_pos:].min()) if high_pos >= 0 else None
    return Live2PriorContextSnapshot(
        symbol=symbol,
        status="ok",
        reason="prior_24h_context_ready_from_closed_5m_ohlcv",
        fetched_at_ms=fetched_at_ms,
        context_start_ms=context_start_ms,
        context_end_ms=context_end_ms,
        rows_received=rows_received,
        rows_used=rows_used,
        prior_spike_count_24h=spike_count,
        prior_fast_fade_count_24h=fast_fade_count,
        prior_high_24h=prior_high,
        prior_low_before_high_24h=low_before_high,
        prior_low_after_high_24h=low_after_high,
        spike_return_pct=spike_return_pct,
        fast_fade_retrace_fraction=fast_fade_retrace_fraction,
    )
