"""Startup-only ticker snapshot hydration for anomaly live2.

The live2 hot decision path must remain stream-only, but universe selection must
not depend on the first partial !ticker@arr WebSocket payload. This module uses
one startup-only exchange boundary call to hydrate SymbolStateStore with a broad
24h futures ticker/liquidity snapshot before the stream takes over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..clock import utc_now_ms
from ..contracts import Live2Component, Live2Event, Live2Severity
from ..state import SymbolStateStore
from .common import optional_float, optional_int, symbol_to_market_id


@runtime_checkable
class Live2StartupTickerExchange(Protocol):
    """Typed startup-only boundary for broad futures ticker/liquidity snapshots."""

    def get_futures_symbols_with_liquidity_metrics(self) -> list[dict[str, object]]:
        ...


@dataclass(frozen=True, slots=True)
class Live2StartupTickerSnapshotResult:
    status: str
    reason: str
    started_at_ms: int
    completed_at_ms: int
    rows_received: int = 0
    rows_applied: int = 0
    rows_skipped: int = 0
    source: str = "exchange_startup_fetch_tickers"
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
            "rows_received": self.rows_received,
            "rows_applied": self.rows_applied,
            "rows_skipped": self.rows_skipped,
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
            event_type="startup_ticker_snapshot_completed",
            component=Live2Component.MARKET_DATA,
            severity=severity,
            message=self.reason,
            data=self.as_dict(),
        )


class Live2StartupTickerSnapshot:
    """Hydrate ticker/liquidity fields before selecting the live2 universe."""

    source_id = "exchange_startup_fetch_tickers"

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        exchange_client: Live2StartupTickerExchange | None,
    ) -> None:
        self.state_store = state_store
        self.exchange_client = exchange_client

    def run(self) -> Live2StartupTickerSnapshotResult:
        started_at_ms = utc_now_ms()
        if self.exchange_client is None:
            return Live2StartupTickerSnapshotResult(
                status="not_ready",
                reason="startup_ticker_snapshot_exchange_client_missing",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
            )
        if not isinstance(self.exchange_client, Live2StartupTickerExchange):
            return Live2StartupTickerSnapshotResult(
                status="not_ready",
                reason="startup_ticker_snapshot_boundary_missing_liquidity_metrics",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
            )
        try:
            rows = self.exchange_client.get_futures_symbols_with_liquidity_metrics()
        except Exception as exc:  # pragma: no cover - exchange boundary
            return Live2StartupTickerSnapshotResult(
                status="not_ready",
                reason="startup_ticker_snapshot_fetch_failed",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
                errors=(f"{type(exc).__name__}:{str(exc)[:200]}",),
            )
        if not isinstance(rows, list):
            return Live2StartupTickerSnapshotResult(
                status="not_ready",
                reason=f"startup_ticker_snapshot_invalid_payload:{type(rows).__name__}",
                started_at_ms=started_at_ms,
                completed_at_ms=utc_now_ms(),
            )

        applied = 0
        skipped = 0
        fetched_at_ms = utc_now_ms()
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                skipped += 1
                continue
            quote_volume = optional_float(row.get("quote_volume"))
            trade_count = optional_int(row.get("trade_count_24h"))
            if quote_volume is None:
                skipped += 1
                continue
            self.state_store.update_ticker(
                symbol=symbol,
                market_id=symbol_to_market_id(symbol),
                fetched_at_ms=fetched_at_ms,
                last_price=None,
                quote_volume_24h=quote_volume,
                trade_count_24h=trade_count,
                price_change_pct_24h=None,
                source=self.source_id,
                status="ok",
                reason="startup_liquidity_snapshot_no_last_price_stream_updates_follow",
            )
            applied += 1

        if applied == len(rows) and rows:
            status = "ready"
            reason = "startup_ticker_snapshot_ready"
        elif applied > 0:
            status = "partial"
            reason = "startup_ticker_snapshot_partial"
        else:
            status = "not_ready"
            reason = "startup_ticker_snapshot_loaded_no_usable_rows"
        return Live2StartupTickerSnapshotResult(
            status=status,
            reason=reason,
            started_at_ms=started_at_ms,
            completed_at_ms=utc_now_ms(),
            rows_received=len(rows),
            rows_applied=applied,
            rows_skipped=skipped,
        )
