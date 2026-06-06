"""Executable-entry guards for anomaly live2.

The guard is hot-path safe and stream-only. It never fetches exchange data; it
only rejects a selected signal when the already-known live stream price makes the
entry stale, drifted, TP-touched, or RR-collapsed before execution can exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from .signal import Live2SignalDecision
from .state import SymbolState


@dataclass(frozen=True, slots=True)
class Live2EntryGuardConfig:
    max_signal_age_ms: int = 5_000
    max_entry_price_drift_pct: float = 0.004
    min_rr_to_tp1: float = 0.70
    max_current_oi_drop_from_first_ok_pct: float = 0.005
    max_oi_3x5m_drop_pct: float = 0.005
    max_current_oi_age_ms: int = 120_000

    def __post_init__(self) -> None:
        if self.max_signal_age_ms <= 0:
            raise ValueError("max_signal_age_ms must be > 0")
        if self.max_entry_price_drift_pct < 0:
            raise ValueError("max_entry_price_drift_pct must be >= 0")
        if self.min_rr_to_tp1 <= 0:
            raise ValueError("min_rr_to_tp1 must be > 0")
        if self.max_current_oi_drop_from_first_ok_pct < 0:
            raise ValueError("max_current_oi_drop_from_first_ok_pct must be >= 0")
        if self.max_oi_3x5m_drop_pct < 0:
            raise ValueError("max_oi_3x5m_drop_pct must be >= 0")
        if self.max_current_oi_age_ms <= 0:
            raise ValueError("max_current_oi_age_ms must be > 0")


@dataclass(frozen=True, slots=True)
class Live2EntryGuardResult:
    verdict: str
    reason: str
    live_price: float | None = None
    live_price_source: str = ""
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
        live_price, live_price_source = _latest_stream_price_with_source(state)
        signal_entry = signal_decision.signal_entry_price
        stop = signal_decision.initial_stop_at_decision
        tp1 = signal_decision.tp1_at_decision
        features = {
            "max_signal_age_ms": self.config.max_signal_age_ms,
            "max_entry_price_drift_pct": self.config.max_entry_price_drift_pct,
            "min_rr_to_tp1": self.config.min_rr_to_tp1,
            "max_current_oi_drop_from_first_ok_pct": self.config.max_current_oi_drop_from_first_ok_pct,
            "max_oi_3x5m_drop_pct": self.config.max_oi_3x5m_drop_pct,
            "max_current_oi_age_ms": self.config.max_current_oi_age_ms,
            "signal_timestamp_ms": signal_timestamp_ms,
            "decision_timestamp_ms": now_ms,
            "signal_age_ms": signal_age_ms,
            "live_price": live_price,
            "live_price_source": live_price_source,
            "signal_entry_price": signal_entry,
            "initial_stop_at_decision": stop,
            "tp1_at_decision": tp1,
        }
        if signal_age_ms > self.config.max_signal_age_ms:
            return self._reject("stale_signal_before_execution", live_price=live_price, live_price_source=live_price_source, signal_age_ms=signal_age_ms, features=features)
        if live_price is None or live_price <= 0:
            return self._reject("live_stream_price_not_available_for_entry_guard", live_price=live_price, live_price_source=live_price_source, signal_age_ms=signal_age_ms, features=features)
        if signal_entry is None or signal_entry <= 0 or stop is None or stop <= 0 or tp1 is None or tp1 <= 0:
            return self._reject("signal_risk_levels_not_available_for_entry_guard", live_price=live_price, live_price_source=live_price_source, signal_age_ms=signal_age_ms, features=features)
        oi_guard_reason = _oi_entry_guard_reject_reason(state=state, now_ms=now_ms, config=self.config, features=features)
        if oi_guard_reason:
            return self._reject(
                oi_guard_reason,
                live_price=live_price,
                live_price_source=live_price_source,
                signal_age_ms=signal_age_ms,
                features=features,
            )
        drift_pct = (live_price / signal_entry) - 1.0
        features["entry_price_drift_pct"] = drift_pct
        if drift_pct > self.config.max_entry_price_drift_pct:
            return self._reject(
                "live_price_drift_above_entry_guard_max",
                live_price=live_price,
                live_price_source=live_price_source,
                signal_age_ms=signal_age_ms,
                drift_pct=drift_pct,
                features=features,
            )
        if live_price >= tp1:
            return self._reject(
                "tp1_already_touched_before_execution",
                live_price=live_price,
                live_price_source=live_price_source,
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
                live_price_source=live_price_source,
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
            live_price_source=live_price_source,
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
                "max_current_oi_drop_from_first_ok_pct": self.config.max_current_oi_drop_from_first_ok_pct,
                "max_oi_3x5m_drop_pct": self.config.max_oi_3x5m_drop_pct,
                "max_current_oi_age_ms": self.config.max_current_oi_age_ms,
            },
        }

    def _reject(
        self,
        reason: str,
        *,
        live_price: float | None,
        live_price_source: str,
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
            live_price_source=live_price_source,
            signal_age_ms=signal_age_ms,
            entry_price_drift_pct=drift_pct,
            rr_to_tp1_at_live_price=rr_to_tp1,
            features=features,
        )


def _latest_stream_price(state: SymbolState) -> float | None:
    price, _source = _latest_stream_price_with_source(state)
    return price


def _latest_stream_price_with_source(state: SymbolState) -> tuple[float | None, str]:
    if state.aggtrade_last_price is not None and state.aggtrade_last_price > 0:
        return state.aggtrade_last_price, "aggtrade_last_price"
    if state.ticker_last_price is not None and state.ticker_last_price > 0:
        return state.ticker_last_price, "ticker_last_price"
    return None, "unavailable"


def _oi_entry_guard_reject_reason(
    *,
    state: SymbolState,
    now_ms: int,
    config: Live2EntryGuardConfig,
    features: dict[str, object],
) -> str:
    """Reject obvious short-cover / OI-collapse long entries.

    Binance historical OI is coarse 5m data, while live current OI is a point
    snapshot. This guard therefore lives in the executable-entry layer instead
    of the source-neutral signal core. Missing OI remains diagnostic; only a
    comparable and material OI drop blocks execution.
    """

    features.update(
        {
            "current_oi_status": state.current_oi_status,
            "current_oi_open_interest": state.current_oi_open_interest,
            "current_oi_timestamp_ms": state.current_oi_timestamp_ms,
            "current_oi_last_seen_ms": state.current_oi_last_seen_ms,
            "current_oi_first_ok_status": state.current_oi_first_ok_status,
            "current_oi_first_ok_open_interest": state.current_oi_first_ok_open_interest,
            "current_oi_first_ok_timestamp_ms": state.current_oi_first_ok_timestamp_ms,
            "current_oi_first_ok_seen_ms": state.current_oi_first_ok_seen_ms,
            "oi_status": state.oi_status,
            "oi_open_interest": state.oi_open_interest,
            "oi_previous_open_interest": state.oi_previous_open_interest,
            "oi_change_pct_3x5m": state.oi_change_pct_3x5m,
            "oi_latest_timestamp_ms": state.oi_latest_timestamp_ms,
        }
    )
    current_age_ms = None
    if state.current_oi_last_seen_ms is not None:
        current_age_ms = max(0, int(now_ms) - int(state.current_oi_last_seen_ms))
    features["current_oi_age_ms"] = current_age_ms
    current = _finite_positive_float(state.current_oi_open_interest)
    first_ok = _finite_positive_float(state.current_oi_first_ok_open_interest)
    current_is_fresh = (
        state.current_oi_status == "ok"
        and state.current_oi_first_ok_status == "ok"
        and current_age_ms is not None
        and current_age_ms <= int(config.max_current_oi_age_ms)
    )
    if current_is_fresh and current is not None and first_ok is not None:
        drop_from_first_ok = (current / first_ok) - 1.0
        features["current_oi_change_pct_from_first_ok"] = drop_from_first_ok
        if drop_from_first_ok < -float(config.max_current_oi_drop_from_first_ok_pct):
            features["oi_entry_guard_reject_reason"] = "current_oi_drop_from_first_ok_before_execution"
            return "current_oi_drop_from_first_ok_before_execution"
    oi_3x5m_change = _finite_float(state.oi_change_pct_3x5m)
    if state.oi_status == "ok" and oi_3x5m_change is not None:
        features["oi_3x5m_change_pct_for_entry_guard"] = oi_3x5m_change
        if oi_3x5m_change < -float(config.max_oi_3x5m_drop_pct):
            features["oi_entry_guard_reject_reason"] = "oi_3x5m_drop_before_execution"
            return "oi_3x5m_drop_before_execution"
    features["oi_entry_guard_reject_reason"] = ""
    return ""


def _finite_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _finite_positive_float(value: object) -> float | None:
    parsed = _finite_float(value)
    return parsed if parsed is not None and parsed > 0.0 else None
