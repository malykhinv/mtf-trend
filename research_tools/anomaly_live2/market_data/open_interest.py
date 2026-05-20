"""Open-interest polling context for anomaly live2.

This module intentionally polls only symbols that are already live2-active
(watching/actionable/in-position or recently seen in live aggTrade). It does not
scan the full universe and it never substitutes missing OI with zero.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import pandas as pd

from domain.enums.timeframe import Timeframe

from ..clock import utc_now_ms
from ..state import SymbolLive2Status, SymbolState, SymbolStateStore

LIVE2_OPEN_INTEREST_SOURCE = "binance_futures_open_interest_hist_5m_poll"
LIVE2_OPEN_INTEREST_TIMEFRAME = Timeframe.M5
LIVE2_OPEN_INTEREST_TIMEFRAME_MS = LIVE2_OPEN_INTEREST_TIMEFRAME.to_milliseconds()


@runtime_checkable
class Live2OpenInterestExchange(Protocol):
    """Typed boundary required by the live2 OI poller."""

    def fetch_open_interest(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Return historical open-interest rows for one symbol/timeframe."""
        ...


@dataclass(frozen=True, slots=True)
class Live2OpenInterestPollConfig:
    """Runtime throttles for live2 open-interest polling."""

    poll_interval_seconds: float = 5.0
    symbol_cooldown_seconds: float = 60.0
    stale_ms: int = 180_000
    lookback_minutes: int = 20
    max_symbols_per_cycle: int = 8
    radar_symbol_ttl_ms: int = 60_000

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.symbol_cooldown_seconds <= 0:
            raise ValueError("symbol_cooldown_seconds must be > 0")
        if self.stale_ms <= 0:
            raise ValueError("stale_ms must be > 0")
        if self.lookback_minutes < 15:
            raise ValueError("lookback_minutes must be >= 15")
        if self.max_symbols_per_cycle <= 0:
            raise ValueError("max_symbols_per_cycle must be > 0")
        if self.radar_symbol_ttl_ms <= 0:
            raise ValueError("radar_symbol_ttl_ms must be > 0")


@dataclass(frozen=True, slots=True)
class Live2OpenInterestSnapshot:
    """Computed OI context for one symbol."""

    symbol: str
    status: str
    reason: str
    fetched_at_ms: int
    latest_timestamp_ms: int | None = None
    previous_timestamp_ms: int | None = None
    open_interest: float | None = None
    previous_open_interest: float | None = None
    open_interest_change_pct_3x5m: float | None = None
    rows_received: int = 0
    source: str = LIVE2_OPEN_INTEREST_SOURCE

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
            "latest_timestamp_ms": self.latest_timestamp_ms,
            "previous_timestamp_ms": self.previous_timestamp_ms,
            "open_interest": self.open_interest,
            "previous_open_interest": self.previous_open_interest,
            "open_interest_change_pct_3x5m": self.open_interest_change_pct_3x5m,
            "rows_received": self.rows_received,
            "source": self.source,
        }


@dataclass(slots=True)
class Live2OpenInterestPollStatus:
    """Thread-safe status snapshot for the OI source."""

    source_id: str = LIVE2_OPEN_INTEREST_SOURCE
    status: str = "not_started"
    ready_symbols: int = 0
    tracked_symbols: int = 0
    active_target_symbols: int = 0
    poll_interval_seconds: float = 0.0
    symbol_cooldown_seconds: float = 0.0
    stale_ms: int = 0
    lookback_minutes: int = 0
    max_symbols_per_cycle: int = 0
    radar_symbol_ttl_ms: int = 0
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
            "lookback_minutes": self.lookback_minutes,
            "max_symbols_per_cycle": self.max_symbols_per_cycle,
            "radar_symbol_ttl_ms": self.radar_symbol_ttl_ms,
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


class Live2OpenInterestPoller:
    """Background OI source for symbols that are already active in live2."""

    source_id = LIVE2_OPEN_INTEREST_SOURCE

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        exchange_client: Live2OpenInterestExchange | None,
        config: Live2OpenInterestPollConfig,
    ) -> None:
        self.state_store = state_store
        self.exchange_client = exchange_client
        self.config = config
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._last_poll_by_symbol: dict[str, int] = {}
        self._status = Live2OpenInterestPollStatus(
            poll_interval_seconds=config.poll_interval_seconds,
            symbol_cooldown_seconds=config.symbol_cooldown_seconds,
            stale_ms=config.stale_ms,
            lookback_minutes=config.lookback_minutes,
            max_symbols_per_cycle=config.max_symbols_per_cycle,
            radar_symbol_ttl_ms=config.radar_symbol_ttl_ms,
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            now_ms = utc_now_ms()
            self._status.started_at_ms = now_ms
            if self.exchange_client is None or not isinstance(self.exchange_client, Live2OpenInterestExchange):
                self._status.status = "disabled"
                self._status.reason = "exchange_client_has_no_fetch_open_interest_boundary"
                self._ready_event.set()
                return
            self._status.status = "running"
            self._status.reason = "open_interest_poller_running_for_active_live2_symbols_only"
        self._thread = threading.Thread(target=self._run_thread, name="live2-open-interest-poller", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def wait_until_ready(self, timeout_seconds: float) -> bool:
        self._ready_event.wait(max(0.0, float(timeout_seconds)))
        return bool(self.status().get("ready_symbols", 0))

    def status(self) -> dict[str, object]:
        now_ms = utc_now_ms()
        counts = self.state_store.open_interest_counts(now_ms=now_ms, stale_ms=self.config.stale_ms)
        active_targets = self._target_symbols(now_ms=now_ms)
        with self._lock:
            status = self._status
            status.ready_symbols = int(counts.get("ok", 0))
            status.tracked_symbols = sum(int(value) for value in counts.values())
            status.active_target_symbols = len(active_targets)
            status.last_target_symbols = active_targets[:25]
            if status.status in {"running", "degraded"} and status.ready_symbols > 0:
                status.status = "ready"
                status.reason = "open_interest_context_ready_for_some_active_symbols"
            elif status.status == "ready" and status.ready_symbols <= 0:
                status.status = "running"
                status.reason = "waiting_for_open_interest_context_on_active_symbols"
            elif status.status == "running" and status.total_errors > 0 and status.total_success <= 0:
                status.status = "degraded"
                status.reason = "open_interest_poller_errors_without_success"
            return status.as_dict()

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
            except Exception as exc:  # defensive: the thread must not die silently
                with self._lock:
                    self._status.status = "degraded"
                    self._status.reason = "open_interest_poller_thread_error"
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
            self.state_store.update_open_interest(
                symbol=symbol,
                fetched_at_ms=snapshot.fetched_at_ms,
                latest_timestamp_ms=snapshot.latest_timestamp_ms,
                previous_timestamp_ms=snapshot.previous_timestamp_ms,
                open_interest=snapshot.open_interest,
                previous_open_interest=snapshot.previous_open_interest,
                open_interest_change_pct_3x5m=snapshot.open_interest_change_pct_3x5m,
                rows_received=snapshot.rows_received,
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
                self._status.reason = "open_interest_poll_cycle_completed"
            elif not polled and self._status.status in {"running", "ready", "degraded"}:
                self._status.reason = "no_active_symbols_due_for_open_interest_poll"

    def _eligible_symbols(self, *, now_ms: int) -> tuple[str, ...]:
        cooldown_ms = int(self.config.symbol_cooldown_seconds * 1000)
        due: list[tuple[int, str]] = []
        for symbol in self._target_symbols(now_ms=now_ms):
            last_poll_ms = self._last_poll_by_symbol.get(symbol)
            if last_poll_ms is not None and now_ms - last_poll_ms < cooldown_ms:
                continue
            priority = self._symbol_priority(symbol=symbol, now_ms=now_ms)
            due.append((priority, symbol))
        due.sort(key=lambda item: (item[0], item[1]))
        return tuple(symbol for _, symbol in due)

    def _target_symbols(self, *, now_ms: int) -> tuple[str, ...]:
        targets: list[str] = []
        for state in self.state_store.snapshot():
            if not state.universe_selected:
                continue
            if self._is_target_state(state, now_ms=now_ms):
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

    def _fetch_symbol(self, *, symbol: str, now_ms: int) -> Live2OpenInterestSnapshot:
        if self.exchange_client is None:
            return Live2OpenInterestSnapshot(
                symbol=symbol,
                status="error",
                reason="exchange_client_missing",
                fetched_at_ms=now_ms,
            )
        start_ms = max(0, now_ms - int(self.config.lookback_minutes * 60_000))
        try:
            frame = self.exchange_client.fetch_open_interest(
                symbol,
                LIVE2_OPEN_INTEREST_TIMEFRAME,
                start_ms,
                now_ms,
            )
        except Exception as exc:
            return Live2OpenInterestSnapshot(
                symbol=symbol,
                status="error",
                reason=f"fetch_open_interest_failed:{type(exc).__name__}:{str(exc)[:240]}",
                fetched_at_ms=now_ms,
            )
        return _build_open_interest_snapshot(symbol=symbol, frame=frame, fetched_at_ms=now_ms)


def _build_open_interest_snapshot(
    *,
    symbol: str,
    frame: pd.DataFrame,
    fetched_at_ms: int,
) -> Live2OpenInterestSnapshot:
    if frame is None or frame.empty:
        return Live2OpenInterestSnapshot(
            symbol=symbol,
            status="empty",
            reason="open_interest_history_empty",
            fetched_at_ms=fetched_at_ms,
            rows_received=0,
        )
    if "timestamp" not in frame.columns or "open_interest" not in frame.columns:
        return Live2OpenInterestSnapshot(
            symbol=symbol,
            status="invalid_schema",
            reason="open_interest_frame_missing_timestamp_or_open_interest",
            fetched_at_ms=fetched_at_ms,
            rows_received=len(frame),
        )
    normalized = frame.loc[:, ["timestamp", "open_interest"]].copy()
    normalized["timestamp"] = pd.to_numeric(normalized["timestamp"], errors="coerce")
    normalized["open_interest"] = pd.to_numeric(normalized["open_interest"], errors="coerce")
    normalized = normalized.dropna(subset=["timestamp", "open_interest"])
    normalized = normalized.loc[normalized["open_interest"] > 0]
    normalized = normalized.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    rows_received = len(normalized)
    if normalized.empty:
        return Live2OpenInterestSnapshot(
            symbol=symbol,
            status="invalid_rows",
            reason="open_interest_history_has_no_positive_numeric_rows",
            fetched_at_ms=fetched_at_ms,
            rows_received=0,
        )
    latest = normalized.iloc[-1]
    latest_ts = int(latest["timestamp"])
    latest_oi = float(latest["open_interest"])
    target_previous_ts = latest_ts - 3 * LIVE2_OPEN_INTEREST_TIMEFRAME_MS
    previous_candidates = normalized.loc[normalized["timestamp"] <= target_previous_ts]
    if previous_candidates.empty:
        return Live2OpenInterestSnapshot(
            symbol=symbol,
            status="not_enough_history",
            reason="open_interest_3x5m_baseline_missing",
            fetched_at_ms=fetched_at_ms,
            latest_timestamp_ms=latest_ts,
            open_interest=latest_oi,
            rows_received=rows_received,
        )
    previous = previous_candidates.iloc[-1]
    previous_ts = int(previous["timestamp"])
    previous_oi = float(previous["open_interest"])
    if previous_oi <= 0:
        return Live2OpenInterestSnapshot(
            symbol=symbol,
            status="invalid_rows",
            reason="open_interest_3x5m_previous_value_non_positive",
            fetched_at_ms=fetched_at_ms,
            latest_timestamp_ms=latest_ts,
            previous_timestamp_ms=previous_ts,
            open_interest=latest_oi,
            previous_open_interest=previous_oi,
            rows_received=rows_received,
        )
    change_pct = (latest_oi / previous_oi) - 1.0
    return Live2OpenInterestSnapshot(
        symbol=symbol,
        status="ok",
        reason="open_interest_3x5m_context_ready",
        fetched_at_ms=fetched_at_ms,
        latest_timestamp_ms=latest_ts,
        previous_timestamp_ms=previous_ts,
        open_interest=latest_oi,
        previous_open_interest=previous_oi,
        open_interest_change_pct_3x5m=change_pct,
        rows_received=rows_received,
    )
