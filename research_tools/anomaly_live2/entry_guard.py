"""Executable-entry guards for anomaly live2.

The guard is hot-path safe and stream-only. It never fetches exchange data; it
only rejects a selected signal when the already-known live stream price makes the
entry stale, drifted, TP-touched, or RR-collapsed before execution can exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .signal import Live2SignalDecision
from .state import SymbolState


@dataclass(frozen=True, slots=True)
class Live2EntryGuardConfig:
    max_signal_age_ms: int = 5_000
    max_entry_price_drift_pct: float = 0.004
    min_rr_to_tp1: float = 0.70

    def __post_init__(self) -> None:
        if self.max_signal_age_ms <= 0:
            raise ValueError("max_signal_age_ms must be > 0")
        if self.max_entry_price_drift_pct < 0:
            raise ValueError("max_entry_price_drift_pct must be >= 0")
        if self.min_rr_to_tp1 <= 0:
            raise ValueError("min_rr_to_tp1 must be > 0")


@dataclass(frozen=True, slots=True)
class Live2EntryGuardResult:
    verdict: str
    reason: str
    live_price: float | None = None
    signal_age_ms: int | None = None
    entry_price_drift_pct: float | None = None
    rr_to_tp1_at_live_price: float | None = None
    features: dict[str, object] = field(default_factory=dict)


class Live2EntryGuardEngine:
    """Rejects selected signals that are no longer executable from stream state."""

    def __init__(self, *, config: Live2EntryGuardConfig | None = None) -> None:
        self.config = config or Live2EntryGuardConfig()
        self._total_checked = 0
        self._total_accepted = 0
        self._total_rejected = 0

    def evaluate(
        self,
        *,
        state: SymbolState,
        signal_decision: Live2SignalDecision,
        signal_timestamp_ms: int,
        now_ms: int,
    ) -> Live2EntryGuardResult:
        self._total_checked += 1
        signal_age_ms = now_ms - signal_timestamp_ms
        live_price = _latest_stream_price(state)
        signal_entry = signal_decision.signal_entry_price
        stop = signal_decision.initial_stop_at_decision
        tp1 = signal_decision.tp1_at_decision
        features = {
            "max_signal_age_ms": self.config.max_signal_age_ms,
            "max_entry_price_drift_pct": self.config.max_entry_price_drift_pct,
            "min_rr_to_tp1": self.config.min_rr_to_tp1,
            "signal_timestamp_ms": signal_timestamp_ms,
            "decision_timestamp_ms": now_ms,
            "signal_age_ms": signal_age_ms,
            "live_price": live_price,
            "signal_entry_price": signal_entry,
            "initial_stop_at_decision": stop,
            "tp1_at_decision": tp1,
        }
        if signal_age_ms > self.config.max_signal_age_ms:
            return self._reject("stale_signal_before_execution", live_price=live_price, signal_age_ms=signal_age_ms, features=features)
        if live_price is None or live_price <= 0:
            return self._reject("live_stream_price_not_available_for_entry_guard", live_price=live_price, signal_age_ms=signal_age_ms, features=features)
        if signal_entry is None or signal_entry <= 0 or stop is None or stop <= 0 or tp1 is None or tp1 <= 0:
            return self._reject("signal_risk_levels_not_available_for_entry_guard", live_price=live_price, signal_age_ms=signal_age_ms, features=features)
        drift_pct = (live_price / signal_entry) - 1.0
        features["entry_price_drift_pct"] = drift_pct
        if drift_pct > self.config.max_entry_price_drift_pct:
            return self._reject(
                "live_price_drift_above_entry_guard_max",
                live_price=live_price,
                signal_age_ms=signal_age_ms,
                drift_pct=drift_pct,
                features=features,
            )
        if live_price >= tp1:
            return self._reject(
                "tp1_already_touched_before_execution",
                live_price=live_price,
                signal_age_ms=signal_age_ms,
                drift_pct=drift_pct,
                rr_to_tp1=0.0,
                features={**features, "rr_to_tp1_at_live_price": 0.0},
            )
        risk = live_price - stop
        reward = tp1 - live_price
        rr = reward / risk if risk > 0 else 0.0
        features["rr_to_tp1_at_live_price"] = rr
        if rr < self.config.min_rr_to_tp1:
            return self._reject(
                "rr_collapsed_before_execution",
                live_price=live_price,
                signal_age_ms=signal_age_ms,
                drift_pct=drift_pct,
                rr_to_tp1=rr,
                features=features,
            )
        self._total_accepted += 1
        return Live2EntryGuardResult(
            verdict="accepted",
            reason="entry_guard_passed",
            live_price=live_price,
            signal_age_ms=signal_age_ms,
            entry_price_drift_pct=drift_pct,
            rr_to_tp1_at_live_price=rr,
            features=features,
        )

    def status(self) -> dict[str, object]:
        return {
            "status": "entry_guard_active",
            "total_checked": self._total_checked,
            "total_accepted": self._total_accepted,
            "total_rejected": self._total_rejected,
            "config": {
                "max_signal_age_ms": self.config.max_signal_age_ms,
                "max_entry_price_drift_pct": self.config.max_entry_price_drift_pct,
                "min_rr_to_tp1": self.config.min_rr_to_tp1,
            },
        }

    def _reject(
        self,
        reason: str,
        *,
        live_price: float | None,
        signal_age_ms: int,
        features: dict[str, object],
        drift_pct: float | None = None,
        rr_to_tp1: float | None = None,
    ) -> Live2EntryGuardResult:
        self._total_rejected += 1
        return Live2EntryGuardResult(
            verdict="rejected_entry_guard",
            reason=reason,
            live_price=live_price,
            signal_age_ms=signal_age_ms,
            entry_price_drift_pct=drift_pct,
            rr_to_tp1_at_live_price=rr_to_tp1,
            features=features,
        )


def _latest_stream_price(state: SymbolState) -> float | None:
    if state.aggtrade_last_price is not None and state.aggtrade_last_price > 0:
        return state.aggtrade_last_price
    if state.ticker_last_price is not None and state.ticker_last_price > 0:
        return state.ticker_last_price
    return None
