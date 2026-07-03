"""Strategy-neutral serialization of accepted strategy events."""

from __future__ import annotations

from collections.abc import Sequence

from anomaly_science.contracts.events import StrategyEvent
from anomaly_science.contracts.market import ONE_MINUTE_MS


def events_to_artifact(events: Sequence[StrategyEvent]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: (item.event_detection_time_ms, item.symbol, item.event_id)):
        state_time_ms = event.event_detection_time_ms
        rows.append({
            "event_id": event.event_id,
            "symbol": event.symbol,
            "state_time_ms": state_time_ms,
            "event_start_time_ms": event.event_start_time_ms,
            "minutes_since_start": (state_time_ms - event.event_start_time_ms) // ONE_MINUTE_MS,
            "is_trigger": True,
            "event_detection_time_ms": event.event_detection_time_ms,
            "seed_time_ms": event.seed_time_ms,
            "seed_open": event.seed_open,
            "seed_high": event.seed_high,
            "seed_low": event.seed_low,
            "seed_close": event.seed_close,
            "initial_move_pct": event.initial_move_pct,
            "initial_volume_zscore": _csv_value(event.initial_volume_zscore),
            "initial_quote_volume_zscore": _csv_value(event.initial_quote_volume_zscore),
            "initial_trade_count_zscore": _csv_value(event.initial_trade_count_zscore),
            "trigger_component": event.trigger_component,
            "trigger_components": ";".join(event.trigger_components),
            "technical_noise_shock": event.technical_noise_shock,
            "raw_candle_gap_minutes": _csv_value(event.raw_candle_gap_minutes),
            "excluded_by_data_quality_gate": event.excluded_by_data_quality_gate,
            "daily_return_asof_t": _csv_value(event.daily_return_asof_t),
            "trade_count_market_percentile_asof_t": _csv_value(event.trade_count_market_percentile_asof_t),
            "detector_version": event.detector_version,
        })
    return rows


def _csv_value(value: object) -> object:
    return "" if value is None else value


__all__ = ["events_to_artifact"]
