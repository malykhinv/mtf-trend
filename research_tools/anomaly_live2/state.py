"""In-memory symbol state for anomaly live2."""

from __future__ import annotations

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
    queues. Later patches will attach ticker/aggTrade/candle snapshots here.
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


class SymbolStateStore:
    """Container with exactly one state record per symbol."""

    def __init__(self, symbols: tuple[str, ...]) -> None:
        unique_symbols = tuple(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        self._states: dict[str, SymbolState] = {
            symbol: SymbolState(symbol=symbol)
            for symbol in unique_symbols
        }

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._states)

    def __len__(self) -> int:
        return len(self._states)

    def get_or_create(self, symbol: str) -> SymbolState:
        normalized = symbol.strip()
        if not normalized:
            raise ValueError("symbol must not be empty")
        state = self._states.get(normalized)
        if state is None:
            state = SymbolState(symbol=normalized)
            self._states[normalized] = state
        return state

    def counts_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in SymbolLive2Status}
        for state in self._states.values():
            counts[state.status.value] = counts.get(state.status.value, 0) + 1
        return counts
