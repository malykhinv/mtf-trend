from __future__ import annotations

from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.events.deduplication import suppress_event_cascade


def _event(event_id: str, symbol: str, detection_ms: int) -> AnomalyEvent:
    start_ms = detection_ms - 60_000
    return AnomalyEvent(
        event_id=event_id,
        symbol=symbol,
        event_start_time_ms=start_ms,
        event_detection_time_ms=detection_ms,
        seed_time_ms=start_ms,
        seed_open=1.0,
        seed_high=1.1,
        seed_low=0.9,
        seed_close=1.0,
        initial_move_pct=0.0,
        initial_volume_zscore=None,
        initial_quote_volume_zscore=None,
        initial_trade_count_zscore=None,
        detector_version="test",
    )


def test_suppress_event_cascade_blocks_same_symbol_inside_horizon_only() -> None:
    result = suppress_event_cascade(
        [
            _event("a", "AAA", 60_000),
            _event("b", "AAA", 2 * 60_000),
            _event("c", "BBB", 2 * 60_000),
            _event("d", "AAA", 32 * 60_000),
        ],
        horizon_minutes=30,
    )

    assert [event.event_id for event in result.accepted_events] == ["a", "d", "c"]
    assert [event.event_id for event in result.suppressed_events] == ["b"]
