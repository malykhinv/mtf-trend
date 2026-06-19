from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from anomaly_science.contracts.events import StrategyEvent
from anomaly_science.contracts.market import ONE_MINUTE_MS


class TriggerCascadeError(ValueError):
    """Raised when trigger cascade suppression cannot be applied cleanly."""


@dataclass(frozen=True, slots=True)
class TriggerCascadeSuppressionResult:
    accepted_events: tuple[StrategyEvent, ...]
    suppressed_events: tuple[StrategyEvent, ...]


def suppress_event_cascade(
    events: Sequence[StrategyEvent] | Iterable[StrategyEvent],
    *,
    horizon_minutes: int,
) -> TriggerCascadeSuppressionResult:
    """Drop repeated trigger events for the same symbol inside the horizon cooldown.

    The first event for ``symbol`` is accepted. Subsequent events whose detection
    time is <= accepted detection time + horizon are audit-only and must not create
    train rows or simulated positions. This is the Core anti-pyramiding dataset gate.
    """
    if type(horizon_minutes) is not int or horizon_minutes <= 0:
        raise TriggerCascadeError("horizon_minutes must be one positive int")

    horizon_ms = horizon_minutes * ONE_MINUTE_MS
    cooldown_until_by_symbol: dict[str, int] = {}
    accepted: list[StrategyEvent] = []
    suppressed: list[StrategyEvent] = []

    for event in sorted(events, key=lambda item: (item.symbol, item.event_detection_time_ms, item.event_id)):
        cooldown_until_ms = cooldown_until_by_symbol.get(event.symbol)
        if cooldown_until_ms is not None and event.event_detection_time_ms <= cooldown_until_ms:
            suppressed.append(event)
            continue
        accepted.append(event)
        cooldown_until_by_symbol[event.symbol] = event.event_detection_time_ms + horizon_ms

    return TriggerCascadeSuppressionResult(accepted_events=tuple(accepted), suppressed_events=tuple(suppressed))
