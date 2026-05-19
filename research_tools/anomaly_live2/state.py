"""In-memory symbol state for anomaly live2."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from .clock import utc_now_ms


class SymbolLive2Status(StrEnum):
    INACTIVE = "inactive"
    WATCHING = "watching"
    ACTIONABLE = "actionable"
    IN_POSITION = "in_position"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass(slots=True)
class SymbolState:
    """Single mutable state record per symbol.

    Live2 deliberately keeps one state object per symbol instead of warm/radar
    queues. Ticker updates mutate this record in place; later aggTrade/candle
    patches will attach flow and bucket snapshots here.
    """

    symbol: str
    status: SymbolLive2Status = SymbolLive2Status.INACTIVE
    created_ms: int = 0
    updated_ms: int = 0
    dirty_since_ms: int | None = None
    actionable_since_ms: int | None = None
    decision_deadline_ms: int | None = None
    last_decision_bucket_ms: int | None = None
    last_verdict: str = "not_evaluated"
    ticker_market_id: str = ""
    ticker_first_seen_ms: int | None = None
    ticker_last_seen_ms: int | None = None
    ticker_update_count: int = 0
    ticker_last_price: float | None = None
    ticker_quote_volume_24h: float | None = None
    ticker_trade_count_24h: int | None = None
    ticker_price_change_pct_24h: float | None = None
    ticker_source: str = ""
    ticker_status: str = "not_seen"
    ticker_reason: str = ""

    def __post_init__(self) -> None:
        now_ms = utc_now_ms()
        if self.created_ms <= 0:
            self.created_ms = now_ms
        if self.updated_ms <= 0:
            self.updated_ms = now_ms

    def mark_dirty(self, *, now_ms: int | None = None) -> None:
        effective_now = utc_now_ms() if now_ms is None else now_ms
        self.updated_ms = effective_now
        if self.dirty_since_ms is None:
            self.dirty_since_ms = effective_now

    def update_ticker(
        self,
        *,
        market_id: str,
        fetched_at_ms: int,
        last_price: float | None,
        quote_volume_24h: float | None,
        trade_count_24h: int | None,
        price_change_pct_24h: float | None,
        source: str,
        status: str,
        reason: str,
    ) -> None:
        self.updated_ms = fetched_at_ms
        self.ticker_market_id = market_id
        if self.ticker_first_seen_ms is None:
            self.ticker_first_seen_ms = fetched_at_ms
        self.ticker_last_seen_ms = fetched_at_ms
        self.ticker_update_count += 1
        self.ticker_last_price = last_price
        self.ticker_quote_volume_24h = quote_volume_24h
        self.ticker_trade_count_24h = trade_count_24h
        self.ticker_price_change_pct_24h = price_change_pct_24h
        self.ticker_source = source
        self.ticker_status = status
        self.ticker_reason = reason
        self.mark_dirty(now_ms=fetched_at_ms)


class SymbolStateStore:
    """Container with exactly one state record per symbol."""

    def __init__(self, symbols: tuple[str, ...]) -> None:
        unique_symbols = tuple(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        self._lock = threading.RLock()
        self._states: dict[str, SymbolState] = {
            symbol: SymbolState(symbol=symbol)
            for symbol in unique_symbols
        }

    @property
    def symbols(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._states)

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)

    def get_or_create(self, symbol: str) -> SymbolState:
        normalized = symbol.strip()
        if not normalized:
            raise ValueError("symbol must not be empty")
        with self._lock:
            state = self._states.get(normalized)
            if state is None:
                state = SymbolState(symbol=normalized)
                self._states[normalized] = state
            return state

    def update_ticker(
        self,
        *,
        symbol: str,
        market_id: str,
        fetched_at_ms: int,
        last_price: float | None,
        quote_volume_24h: float | None,
        trade_count_24h: int | None,
        price_change_pct_24h: float | None,
        source: str,
        status: str,
        reason: str,
    ) -> None:
        with self._lock:
            state = self.get_or_create(symbol)
            state.update_ticker(
                market_id=market_id,
                fetched_at_ms=fetched_at_ms,
                last_price=last_price,
                quote_volume_24h=quote_volume_24h,
                trade_count_24h=trade_count_24h,
                price_change_pct_24h=price_change_pct_24h,
                source=source,
                status=status,
                reason=reason,
            )

    def snapshot(self) -> tuple[SymbolState, ...]:
        with self._lock:
            return tuple(self._states.values())

    def counts_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in SymbolLive2Status}
        with self._lock:
            for state in self._states.values():
                counts[state.status.value] = counts.get(state.status.value, 0) + 1
        return counts

    def ticker_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for state in self._states.values():
                key = state.ticker_status or "unknown"
                counts[key] = counts.get(key, 0) + 1
        return counts
