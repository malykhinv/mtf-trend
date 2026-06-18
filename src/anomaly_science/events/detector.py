from __future__ import annotations

import hashlib
import math
from statistics import fmean, pstdev
from typing import Iterable, Sequence

from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m, ONE_MINUTE_MS
from anomaly_science.events.config import BroadAnomalyDetectorConfig


def detect_broad_anomaly_events(
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    *,
    config: BroadAnomalyDetectorConfig | None = None,
) -> tuple[AnomalyEvent, ...]:
    """Detect broad 1m anomaly events from closed candles only.

    Every decision for a seed candle uses only earlier candles of the same symbol
    plus the current closed seed candle. Future candles cannot affect an event's
    event_id, detection time, or initial metrics.
    """
    cfg = config or BroadAnomalyDetectorConfig()
    by_symbol: dict[str, list[Candle1m]] = {}
    for candle in candles_1m:
        by_symbol.setdefault(candle.symbol, []).append(candle)

    events: list[AnomalyEvent] = []
    for symbol in sorted(by_symbol):
        rows = sorted(by_symbol[symbol], key=lambda item: item.open_time_ms)
        last_event_start_ms: int | None = None
        detector_baseline: list[Candle1m] = []
        previous_open_time_ms: int | None = None
        for candle in rows:
            raw_gap_minutes = _raw_gap_minutes(candle.open_time_ms, previous_open_time_ms)
            previous_open_time_ms = candle.open_time_ms

            if _is_technical_noise_shock(raw_gap_minutes):
                continue

            baseline = detector_baseline[-cfg.baseline_bars:]
            if len(baseline) < cfg.min_baseline_bars:
                detector_baseline.append(candle)
                continue
            if _in_cooldown(candle.open_time_ms, last_event_start_ms, cfg.cooldown_minutes):
                detector_baseline.append(candle)
                continue

            metrics = _seed_metrics(candle, baseline)
            if not _is_broad_activity(metrics, cfg):
                detector_baseline.append(candle)
                continue

            event = AnomalyEvent(
                event_id=_event_id(cfg.detector_version, candle.symbol, candle.open_time_ms),
                symbol=candle.symbol,
                event_start_time_ms=candle.open_time_ms,
                event_detection_time_ms=candle.available_time_ms,
                seed_time_ms=candle.open_time_ms,
                seed_open=candle.open,
                seed_high=candle.high,
                seed_low=candle.low,
                seed_close=candle.close,
                initial_move_pct=metrics["move_pct"],
                initial_volume_zscore=metrics["volume_zscore"],
                initial_quote_volume_zscore=metrics["quote_volume_zscore"],
                initial_trade_count_zscore=metrics["trade_count_zscore"],
                technical_noise_shock=False,
                raw_candle_gap_minutes=raw_gap_minutes,
                excluded_by_data_quality_gate=False,
                detector_version=cfg.detector_version,
            )
            events.append(event)
            last_event_start_ms = candle.open_time_ms
            detector_baseline.append(candle)
    return tuple(events)


def events_to_artifact(events: Sequence[AnomalyEvent]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: (item.event_detection_time_ms, item.symbol, item.event_id)):
        state_time_ms = event.event_detection_time_ms
        rows.append(
            {
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
                "technical_noise_shock": event.technical_noise_shock,
                "raw_candle_gap_minutes": _csv_value(event.raw_candle_gap_minutes),
                "excluded_by_data_quality_gate": event.excluded_by_data_quality_gate,
                "detector_version": event.detector_version,
            }
        )
    return rows


def _seed_metrics(candle: Candle1m, baseline: Sequence[Candle1m]) -> dict[str, float | None]:
    baseline_ranges = [_range_pct(item) for item in baseline]
    return {
        "move_pct": (candle.close / candle.open) - 1.0,
        "range_pct": _range_pct(candle),
        "volume_zscore": _zscore(candle.volume, [item.volume for item in baseline]),
        "quote_volume_zscore": _zscore(candle.quote_volume, [item.quote_volume for item in baseline]),
        "trade_count_zscore": _zscore(
            candle.number_of_trades,
            [item.number_of_trades for item in baseline if item.number_of_trades is not None],
        ),
        "range_zscore": _zscore(_range_pct(candle), baseline_ranges),
    }


def _is_broad_activity(metrics: dict[str, float | None], config: BroadAnomalyDetectorConfig) -> bool:
    move_pct = metrics["move_pct"]
    if move_pct is not None and abs(move_pct) >= config.min_abs_return_pct:
        return True
    return any(
        _at_least(metrics[name], threshold)
        for name, threshold in (
            ("quote_volume_zscore", config.min_quote_volume_zscore),
            ("volume_zscore", config.min_volume_zscore),
            ("trade_count_zscore", config.min_trade_count_zscore),
            ("range_zscore", config.min_range_zscore),
        )
    )


def _range_pct(candle: Candle1m) -> float:
    return (candle.high / candle.low) - 1.0


def _raw_gap_minutes(open_time_ms: int, previous_open_time_ms: int | None) -> float | None:
    if previous_open_time_ms is None:
        return None
    return (open_time_ms - previous_open_time_ms) / ONE_MINUTE_MS


def _is_technical_noise_shock(raw_gap_minutes: float | None) -> bool:
    return raw_gap_minutes is not None and raw_gap_minutes > 3.0


def _zscore(value: float | int | None, baseline_values: Sequence[float | int | None]) -> float | None:
    if value is None:
        return None
    values = [float(item) for item in baseline_values if item is not None and math.isfinite(float(item))]
    if len(values) < 2:
        return None
    stdev = pstdev(values)
    if stdev <= 0.0:
        return None
    return (float(value) - fmean(values)) / stdev


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _in_cooldown(open_time_ms: int, last_event_start_ms: int | None, cooldown_minutes: int) -> bool:
    if last_event_start_ms is None or cooldown_minutes <= 0:
        return False
    return open_time_ms - last_event_start_ms < cooldown_minutes * ONE_MINUTE_MS


def _event_id(detector_version: str, symbol: str, seed_time_ms: int) -> str:
    raw = f"{detector_version}|{symbol}|{seed_time_ms}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"evt_{digest}"


def _csv_value(value: object) -> object:
    return "" if value is None else value
