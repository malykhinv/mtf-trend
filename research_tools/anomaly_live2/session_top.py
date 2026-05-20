"""Session top-growth tracker for live2 operator status.

The tracker is intentionally UI/audit-only. It records ticker prices observed
since the current crypto session metric baseline and exposes the top positive
movers. It never feeds the signal engine or execution path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Iterable

from .clock import utc_now_ms
from .state import SymbolState

LIVE2_SESSION_TOP_LIMIT = 3
HOUR_MS = 60 * 60 * 1000

LIVE2_CRYPTO_SESSION_WINDOWS_UTC: tuple[tuple[int, int, str, str, str, str], ...] = (
    (0, 7 * 60, "Азия", "core", "Азия", ""),
    (7 * 60, 9 * 60, "Азия → Европа", "transition", "Азия", "Европа"),
    (9 * 60, 13 * 60, "Европа", "core", "Европа", ""),
    (13 * 60, 16 * 60, "Европа + Америка", "overlap", "Европа", "Америка"),
    (16 * 60, 21 * 60, "Америка", "core", "Америка", ""),
    (21 * 60, 24 * 60, "Америка → Азия", "transition", "Америка", "Азия"),
)

LIVE2_CRYPTO_SESSION_METRIC_START_MINUTES_UTC: tuple[tuple[int, int, int], ...] = (
    (0, 7 * 60, 0),
    (7 * 60, 9 * 60, 0),
    (9 * 60, 13 * 60, 7 * 60),
    (13 * 60, 16 * 60, 9 * 60),
    (16 * 60, 21 * 60, 13 * 60),
    (21 * 60, 24 * 60, 16 * 60),
)


@dataclass(slots=True)
class Live2SessionTopSymbolState:
    symbol: str
    price_points: deque[tuple[int, float, str]] = field(default_factory=deque)


class Live2SessionTopTracker:
    """Tracks session top growth from already observed ticker prices."""

    def __init__(self, *, limit: int = LIVE2_SESSION_TOP_LIMIT) -> None:
        if int(limit) < 1:
            raise ValueError("limit must be >= 1")
        self.limit = int(limit)
        self._states: dict[str, Live2SessionTopSymbolState] = {}
        self._last_snapshot_ms = 0
        self._last_source = ""
        self._last_source_status = "not_started"
        self._last_source_reason = "ticker_snapshots_not_received_yet"

    def update_from_state_snapshot(self, states: Iterable[SymbolState], *, now_ms: int | None = None) -> None:
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        session_window = live2_session_metric_window_ms(effective_now_ms)
        cutoff_ms = int(session_window["metric_start_ms"])
        self._last_snapshot_ms = effective_now_ms
        self._last_source = "live2_ticker_state"
        self._last_source_status = "ok"
        self._last_source_reason = "ok"

        for state in list(self._states.values()):
            _prune_session_top_points(state.price_points, cutoff_ms=cutoff_ms)
        for symbol_key, state in list(self._states.items()):
            if not state.price_points:
                self._states.pop(symbol_key, None)

        for state in states:
            if not state.universe_selected:
                continue
            if state.ticker_status != "ok":
                continue
            price = _finite_positive_or_none(state.ticker_last_price)
            if price is None:
                continue
            fetched_at_ms = int(state.ticker_last_seen_ms or effective_now_ms)
            if fetched_at_ms < cutoff_ms or fetched_at_ms > effective_now_ms + 60_000:
                continue
            symbol_key = _symbol_key(state.symbol)
            if not symbol_key:
                continue
            top_state = self._states.get(symbol_key)
            if top_state is None:
                top_state = Live2SessionTopSymbolState(symbol=state.symbol)
                self._states[symbol_key] = top_state
            source = str(state.ticker_source or "")
            if top_state.price_points and int(top_state.price_points[-1][0]) == fetched_at_ms:
                top_state.price_points[-1] = (fetched_at_ms, float(price), source)
            elif not top_state.price_points or fetched_at_ms > int(top_state.price_points[-1][0]):
                top_state.price_points.append((fetched_at_ms, float(price), source))
            else:
                top_state.price_points.append((fetched_at_ms, float(price), source))
                top_state.price_points = deque(sorted(top_state.price_points, key=lambda row: int(row[0])))
            _prune_session_top_points(top_state.price_points, cutoff_ms=cutoff_ms)

    def snapshot(self, *, now_ms: int | None = None) -> dict[str, object]:
        effective_now_ms = utc_now_ms() if now_ms is None else int(now_ms)
        session = live2_session_metric_window_ms(effective_now_ms)
        cutoff_ms = int(session["metric_start_ms"])
        items: list[dict[str, object]] = []
        symbols_tracked = 0
        for state in list(self._states.values()):
            _prune_session_top_points(state.price_points, cutoff_ms=cutoff_ms)
            if not state.price_points:
                continue
            symbols_tracked += 1
            baseline_ts, baseline_price, baseline_source = state.price_points[0]
            last_ts, last_price, last_source = state.price_points[-1]
            if baseline_price <= 0.0:
                continue
            growth_fraction = (last_price - baseline_price) / baseline_price
            if growth_fraction <= 0.0:
                continue
            items.append(
                {
                    "symbol": state.symbol,
                    "growth_pct": growth_fraction * 100.0,
                    "growth_fraction": growth_fraction,
                    "baseline_price": baseline_price,
                    "last_price": last_price,
                    "baseline_timestamp_ms": int(baseline_ts),
                    "last_timestamp_ms": int(last_ts),
                    "baseline_price_source": baseline_source,
                    "last_price_source": last_source,
                }
            )
        items.sort(key=lambda item: float(item["growth_fraction"]), reverse=True)
        ranked_items = [{"rank": rank, **item} for rank, item in enumerate(items[: self.limit], start=1)]
        if ranked_items:
            status = "ok"
            reason = "ok"
        elif symbols_tracked:
            status = "empty"
            reason = "no_positive_growth_since_session_metric_baseline"
        else:
            status = "empty"
            reason = "no_usable_ticker_price_snapshots_since_session_metric_start"
        elapsed_hours = max(0.0, (effective_now_ms - cutoff_ms) / float(HOUR_MS))
        return {
            "snapshot_timestamp_ms": effective_now_ms,
            "session_label": session["label"],
            "session_phase": session["phase"],
            "session_primary": session["primary"],
            "session_secondary": session["secondary"],
            "session_start_ms": session["start_ms"],
            "session_end_ms": session["end_ms"],
            "top_window_label": session["metric_window_label"],
            "top_window_start_ms": cutoff_ms,
            "top_window_end_ms": effective_now_ms,
            "top_window_hours": round(elapsed_hours, 4),
            "status": status,
            "reason": reason,
            "source": self._last_source,
            "source_status": self._last_source_status,
            "source_reason": self._last_source_reason,
            "symbols_tracked": symbols_tracked,
            "symbols_with_positive_growth": len(items),
            "items": ranked_items,
        }


def live2_session_metric_window_ms(timestamp_ms: int) -> dict[str, object]:
    session = dict(_crypto_session_context_ms(timestamp_ms))
    moment = datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC)
    minute_of_day = moment.hour * 60 + moment.minute
    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    metric_start_minute: int | None = None
    for start_minute, end_minute, metric_start in LIVE2_CRYPTO_SESSION_METRIC_START_MINUTES_UTC:
        if int(start_minute) <= minute_of_day < int(end_minute):
            metric_start_minute = int(metric_start)
            break
    if metric_start_minute is None:
        raise ValueError(f"No crypto session metric window for UTC minute {minute_of_day}")
    metric_start_dt = day_start + timedelta(minutes=metric_start_minute)
    session["metric_start_ms"] = int(metric_start_dt.timestamp() * 1000)
    session["metric_end_ms"] = int(session["end_ms"])
    session["metric_window_label"] = ""
    return session


def _crypto_session_context_ms(timestamp_ms: int) -> dict[str, object]:
    moment = datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC)
    minute_of_day = moment.hour * 60 + moment.minute
    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    for start_minute, end_minute, label, phase, primary, secondary in LIVE2_CRYPTO_SESSION_WINDOWS_UTC:
        if int(start_minute) <= minute_of_day < int(end_minute):
            start_dt = day_start + timedelta(minutes=int(start_minute))
            end_dt = day_start + timedelta(minutes=int(end_minute))
            return {
                "label": label,
                "phase": phase,
                "primary": primary,
                "secondary": secondary,
                "start_ms": int(start_dt.timestamp() * 1000),
                "end_ms": int(end_dt.timestamp() * 1000),
            }
    raise ValueError(f"No crypto session window for UTC minute {minute_of_day}")


def _prune_session_top_points(points: deque[tuple[int, float, str]], *, cutoff_ms: int) -> None:
    while points and int(points[0][0]) < int(cutoff_ms):
        points.popleft()


def _finite_positive_or_none(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not isfinite(result) or result <= 0.0:
        return None
    return result


def _symbol_key(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "_").replace(":", "_").strip()
