from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Iterable, Sequence

from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m, ONE_MINUTE_MS
from anomaly_science.events.config import BroadAnomalyDetectorConfig


_MetricValue = float | int | bool | None


@dataclass(frozen=True, slots=True)
class _SeedContext:
    candle: Candle1m
    raw_gap_minutes: float | None
    metrics: dict[str, _MetricValue]


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

    contexts_by_symbol: dict[str, list[_SeedContext]] = {}
    all_contexts: list[_SeedContext] = []
    for symbol in sorted(by_symbol):
        rows = sorted(by_symbol[symbol], key=lambda item: item.open_time_ms)
        contexts = _build_seed_contexts(rows, cfg)
        contexts_by_symbol[symbol] = contexts
        all_contexts.extend(contexts)
    market_wide_times = _market_wide_impulse_times(all_contexts, cfg)

    events: list[AnomalyEvent] = []
    for symbol in sorted(contexts_by_symbol):
        last_event_start_ms: int | None = None
        for context in contexts_by_symbol[symbol]:
            candle = context.candle
            if _in_cooldown(candle.open_time_ms, last_event_start_ms, cfg.cooldown_minutes):
                continue

            trigger_components = _broad_activity_components(
                context.metrics,
                cfg,
                market_wide_impulse=candle.open_time_ms in market_wide_times,
            )
            if not trigger_components:
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
                initial_move_pct=float(context.metrics["move_pct"] or 0.0),
                initial_volume_zscore=_metric_float(context.metrics["volume_zscore"]),
                initial_quote_volume_zscore=_metric_float(context.metrics["quote_volume_zscore"]),
                initial_trade_count_zscore=_metric_float(context.metrics["trade_count_zscore"]),
                trigger_component=trigger_components[0],
                trigger_components=trigger_components,
                technical_noise_shock=False,
                raw_candle_gap_minutes=context.raw_gap_minutes,
                excluded_by_data_quality_gate=False,
                detector_version=cfg.detector_version,
            )
            events.append(event)
            last_event_start_ms = candle.open_time_ms
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
                "trigger_component": event.trigger_component,
                "trigger_components": _trigger_components_csv(event.trigger_components),
                "technical_noise_shock": event.technical_noise_shock,
                "raw_candle_gap_minutes": _csv_value(event.raw_candle_gap_minutes),
                "excluded_by_data_quality_gate": event.excluded_by_data_quality_gate,
                "daily_return_asof_t": _csv_value(event.daily_return_asof_t),
                "trade_count_market_percentile_asof_t": _csv_value(event.trade_count_market_percentile_asof_t),
                "detector_version": event.detector_version,
            }
        )
    return rows


def _build_seed_contexts(rows: Sequence[Candle1m], config: BroadAnomalyDetectorConfig) -> list[_SeedContext]:
    contexts: list[_SeedContext] = []
    detector_baseline: list[Candle1m] = []
    previous_open_time_ms: int | None = None
    for candle in rows:
        raw_gap_minutes = _raw_gap_minutes(candle.open_time_ms, previous_open_time_ms)
        previous_open_time_ms = candle.open_time_ms

        if _is_technical_noise_shock(raw_gap_minutes):
            continue

        baseline = detector_baseline[-config.baseline_bars:]
        if len(baseline) >= config.min_baseline_bars:
            contexts.append(
                _SeedContext(
                    candle=candle,
                    raw_gap_minutes=raw_gap_minutes,
                    metrics=_seed_metrics(candle, baseline, config),
                )
            )
        detector_baseline.append(candle)
    return contexts


def _seed_metrics(candle: Candle1m, baseline: Sequence[Candle1m], config: BroadAnomalyDetectorConfig) -> dict[str, _MetricValue]:
    baseline_ranges = [_range_pct(item) for item in baseline]
    fast_window = _window_candles(candle, baseline, config.fast_burst_window_minutes)
    grind_window = _window_candles(candle, baseline, config.grind_pump_window_minutes)
    breakout_baseline = baseline[-config.breakout_lookback_minutes:]
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
        "fast_burst_return_pct": _window_return_pct(fast_window),
        "grind_pump_return_pct": _window_return_pct(grind_window),
        "grind_pump_positive_candles": _positive_candle_count(grind_window),
        "grind_pump_max_abs_move_pct": _max_abs_move_pct(grind_window),
        "breakout_pct": _breakout_pct(candle, breakout_baseline),
        "inside_baseline_range": _inside_baseline_range(candle, baseline),
        "minutes_since_session_start": _minutes_since_session_start(
            candle.open_time_ms,
            config.session_activity_start_minutes_utc,
            config.session_activity_window_minutes,
        ),
    }


def _broad_activity_components(
    metrics: dict[str, _MetricValue],
    config: BroadAnomalyDetectorConfig,
    *,
    market_wide_impulse: bool = False,
) -> tuple[str, ...]:
    """Return deterministic causal trigger components for the current seed candle.

    These tags explain why the broad detector accepted the row. They are audit
    metadata, not trade rules. Components are computed from the current closed
    seed candle, the already-built same-symbol baseline, and optionally the
    same-minute cross-section of other closed seed candles.
    """
    price_components: list[str] = []
    flow_components: list[str] = []
    context_components: list[str] = []

    move_pct = _metric_float(metrics["move_pct"])
    if move_pct is not None and abs(move_pct) >= config.min_abs_return_pct:
        price_components.append("one_shot_spike")
    if _at_least(metrics["range_zscore"], config.min_range_zscore):
        price_components.append("range_expansion")
    if _at_least(metrics["fast_burst_return_pct"], config.min_fast_burst_return_pct):
        price_components.append("fast_burst")
    if (
        _at_least(metrics["grind_pump_return_pct"], config.min_grind_pump_return_pct)
        and _metric_int(metrics["grind_pump_positive_candles"]) >= config.min_grind_pump_positive_candles
        and _metric_float(metrics["grind_pump_max_abs_move_pct"]) <= config.max_grind_pump_single_candle_abs_return_pct
    ):
        price_components.append("grind_pump")
    if _at_least(metrics["breakout_pct"], config.min_breakout_pct):
        price_components.append("breakout")
    if (
        move_pct is not None
        and move_pct >= config.min_pump_inside_noise_return_pct
        and bool(metrics["inside_baseline_range"])
        and _at_least(metrics["quote_volume_zscore"], config.min_quote_volume_zscore)
    ):
        price_components.append("pump_inside_noise")

    if _at_least(metrics["quote_volume_zscore"], config.min_quote_volume_zscore):
        flow_components.append("quote_volume_spike")
    if _at_least(metrics["volume_zscore"], config.min_volume_zscore):
        flow_components.append("base_volume_spike")
    if _at_least(metrics["trade_count_zscore"], config.min_trade_count_zscore):
        flow_components.append("trade_count_spike")

    if (
        _metric_float(metrics["minutes_since_session_start"]) is not None
        and _at_least(metrics["quote_volume_zscore"], config.min_session_activity_quote_volume_zscore)
    ):
        context_components.append("session_activity_burst")
    if market_wide_impulse:
        context_components.append("market_wide_impulse")

    derived_components: list[str] = []
    if flow_components and not price_components:
        derived_components.append("volume_only_anomaly")

    return tuple(_unique_preserve_order([*derived_components, *price_components, *flow_components, *context_components]))


def _is_broad_activity(metrics: dict[str, _MetricValue], config: BroadAnomalyDetectorConfig) -> bool:
    return bool(_broad_activity_components(metrics, config))


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


def _at_least(value: _MetricValue, threshold: float) -> bool:
    numeric_value = _metric_float(value)
    return numeric_value is not None and numeric_value >= threshold


def _metric_float(value: _MetricValue) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    return float(value)


def _metric_int(value: _MetricValue) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


def _window_candles(candle: Candle1m, baseline: Sequence[Candle1m], window_minutes: int) -> tuple[Candle1m, ...]:
    if window_minutes <= 1:
        return (candle,)
    prior = list(baseline[-(window_minutes - 1) :])
    if len(prior) < window_minutes - 1:
        return ()
    return tuple([*prior, candle])


def _window_return_pct(window: Sequence[Candle1m]) -> float | None:
    if len(window) < 2:
        return None
    return (window[-1].close / window[0].open) - 1.0


def _positive_candle_count(window: Sequence[Candle1m]) -> int:
    return sum(1 for item in window if item.close > item.open)


def _max_abs_move_pct(window: Sequence[Candle1m]) -> float | None:
    if not window:
        return None
    return max(abs((item.close / item.open) - 1.0) for item in window)


def _breakout_pct(candle: Candle1m, baseline: Sequence[Candle1m]) -> float | None:
    if not baseline:
        return None
    prior_high = max(item.high for item in baseline)
    if prior_high <= 0:
        return None
    return (candle.close / prior_high) - 1.0


def _inside_baseline_range(candle: Candle1m, baseline: Sequence[Candle1m]) -> bool:
    if not baseline:
        return False
    return candle.high <= max(item.high for item in baseline) and candle.low >= min(item.low for item in baseline)


def _minutes_since_session_start(open_time_ms: int, start_minutes_utc: Sequence[int], window_minutes: int) -> int | None:
    minute_of_day = (open_time_ms // ONE_MINUTE_MS) % 1_440
    distances = [(minute_of_day - start_minute) % 1_440 for start_minute in start_minutes_utc]
    nearest = min(distances) if distances else None
    if nearest is None or nearest >= window_minutes:
        return None
    return nearest


def _market_wide_impulse_times(
    contexts: Sequence[_SeedContext],
    config: BroadAnomalyDetectorConfig,
) -> set[int]:
    qualifying_counts_by_time: dict[int, int] = {}
    for context in contexts:
        move_pct = _metric_float(context.metrics["move_pct"])
        if move_pct is None or abs(move_pct) < config.min_market_wide_abs_return_pct:
            continue
        if not _at_least(context.metrics["quote_volume_zscore"], config.min_market_wide_quote_volume_zscore):
            continue
        qualifying_counts_by_time[context.candle.open_time_ms] = qualifying_counts_by_time.get(context.candle.open_time_ms, 0) + 1
    return {
        open_time_ms
        for open_time_ms, count in qualifying_counts_by_time.items()
        if count >= config.market_wide_window_min_symbols
    }


def _in_cooldown(open_time_ms: int, last_event_start_ms: int | None, cooldown_minutes: int) -> bool:
    if last_event_start_ms is None or cooldown_minutes <= 0:
        return False
    return open_time_ms - last_event_start_ms < cooldown_minutes * ONE_MINUTE_MS


def _unique_preserve_order(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _trigger_components_csv(values: Sequence[str]) -> str:
    return ";".join(values)


def _event_id(detector_version: str, symbol: str, seed_time_ms: int) -> str:
    raw = f"{detector_version}|{symbol}|{seed_time_ms}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"evt_{digest}"


def _csv_value(value: object) -> object:
    return "" if value is None else value
