from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m, MarketDataContractError, ONE_MINUTE_MS
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.data.source import CsvDataSourceError, MarketDataSource
from anomaly_science.state.config import OnlineStateBuilderConfig


class AnomalyEventsArtifactError(ValueError):
    """Raised when anomaly_events.csv violates its strict artifact boundary."""


def load_anomaly_events_csv(path: str | Path) -> tuple[AnomalyEvent, ...]:
    """Read anomaly_events.csv through the declared MVP1 artifact schema.

    The boundary is intentionally strict: the file must have exactly the schema
    columns. Missing values are accepted only for nullable detector z-score
    fields represented by the contract as float | None.
    """
    event_path = Path(path)
    if not event_path.exists():
        raise AnomalyEventsArtifactError(f"events artifact is missing: {event_path}")

    frame = pd.read_csv(event_path)
    schema = get_artifact_schema("anomaly_events.csv")
    expected_columns = list(schema.required_columns)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise AnomalyEventsArtifactError(
            f"events artifact columns must match {expected_columns}, got {actual_columns}"
        )

    events: list[AnomalyEvent] = []
    for row_index, row in frame.iterrows():
        try:
            events.append(
                AnomalyEvent(
                    event_id=_required_str(row, "event_id"),
                    symbol=_required_str(row, "symbol"),
                    event_start_time_ms=_required_int(row, "event_start_time_ms"),
                    event_detection_time_ms=_required_int(row, "event_detection_time_ms"),
                    seed_time_ms=_required_int(row, "seed_time_ms"),
                    seed_open=_required_float(row, "seed_open"),
                    seed_high=_required_float(row, "seed_high"),
                    seed_low=_required_float(row, "seed_low"),
                    seed_close=_required_float(row, "seed_close"),
                    initial_move_pct=_required_float(row, "initial_move_pct"),
                    initial_volume_zscore=_optional_float(row, "initial_volume_zscore"),
                    initial_quote_volume_zscore=_optional_float(row, "initial_quote_volume_zscore"),
                    initial_trade_count_zscore=_optional_float(row, "initial_trade_count_zscore"),
                    detector_version=_required_str(row, "detector_version"),
                    technical_noise_shock=_optional_bool(row, "technical_noise_shock", default=False),
                    raw_candle_gap_minutes=_optional_float(row, "raw_candle_gap_minutes"),
                    excluded_by_data_quality_gate=_optional_bool(row, "excluded_by_data_quality_gate", default=False),
                )
            )
        except (TypeError, ValueError) as exc:
            raise AnomalyEventsArtifactError(f"invalid anomaly_events.csv row {row_index}: {exc}") from exc
    return tuple(events)


def build_online_anomaly_state_1m(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    events: Sequence[AnomalyEvent] | Iterable[AnomalyEvent],
    config: OnlineStateBuilderConfig | None = None,
) -> tuple[AnomalyState1mRow, ...]:
    """Build one online state row per available closed 1m candle per event.

    A state row at time t uses only candles whose available_time_ms <= t.
    The running high/low are computed only from event_start_time_ms through t,
    never from the final future path of the event.
    """
    cfg = config or OnlineStateBuilderConfig()
    candles_by_symbol: dict[str, list[Candle1m]] = {}
    for candle in candles_1m:
        candles_by_symbol.setdefault(candle.symbol, []).append(candle)
    for symbol in candles_by_symbol:
        candles_by_symbol[symbol].sort(key=lambda item: (item.available_time_ms, item.open_time_ms))

    rows: list[AnomalyState1mRow] = []
    for event in sorted(events, key=lambda item: (item.symbol, item.event_detection_time_ms, item.event_id)):
        if event.technical_noise_shock or event.excluded_by_data_quality_gate:
            continue
        symbol_candles = candles_by_symbol.get(event.symbol, [])
        if not symbol_candles:
            continue
        rows.extend(_build_event_rows(event=event, candles=symbol_candles, config=cfg))
    return tuple(rows)


def build_online_anomaly_state_1m_from_source(
    *,
    source: MarketDataSource,
    events_path: str | Path,
    config: OnlineStateBuilderConfig | None = None,
) -> tuple[AnomalyState1mRow, ...]:
    """Load normalized 1m candles via DataSource and events via artifact boundary."""
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    events = load_anomaly_events_csv(events_path)
    return build_online_anomaly_state_1m(
        candles_1m=normalize_candles_1m(frame),
        events=events,
        config=config,
    )


def state_rows_to_artifact(rows: Sequence[AnomalyState1mRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        result.append({key: _csv_value(value) for key, value in payload.items()})
    return result


def _build_event_rows(
    *,
    event: AnomalyEvent,
    candles: Sequence[Candle1m],
    config: OnlineStateBuilderConfig,
) -> list[AnomalyState1mRow]:
    candidate_candles = [
        candle
        for candle in candles
        if candle.open_time_ms >= event.event_start_time_ms
        and candle.available_time_ms >= event.event_detection_time_ms
        and _minutes_between(event.event_detection_time_ms, candle.available_time_ms)
        <= config.max_state_minutes_after_detection
    ]
    event_rows: list[AnomalyState1mRow] = []
    for current in candidate_candles:
        state_time_ms = current.available_time_ms
        asof_candles = [
            candle
            for candle in candles
            if candle.open_time_ms >= event.event_start_time_ms and candle.available_time_ms <= state_time_ms
        ]
        if not asof_candles:
            continue
        first_candle = min(asof_candles, key=lambda item: item.open_time_ms)
        running_high = max(asof_candles, key=lambda item: (item.high, -item.open_time_ms))
        running_low = min(asof_candles, key=lambda item: (item.low, item.open_time_ms))
        row = AnomalyState1mRow(
            event_id=event.event_id,
            symbol=event.symbol,
            state_time_ms=state_time_ms,
            snapshot_time_ms=state_time_ms,
            feature_cutoff_time_ms=state_time_ms,
            minutes_since_event_start=_minutes_between(event.event_start_time_ms, state_time_ms),
            minutes_since_detection=_minutes_between(event.event_detection_time_ms, state_time_ms),
            event_alive=True,
            running_high_asof_t=running_high.high,
            running_high_time_asof_t_ms=running_high.open_time_ms,
            running_low_asof_t=running_low.low,
            running_low_time_asof_t_ms=running_low.open_time_ms,
            time_since_running_high_minutes=_minutes_between(running_high.open_time_ms, state_time_ms),
            current_close=current.close,
            current_return_from_start=(current.close / first_candle.open) - 1.0,
            distance_to_running_high=(current.close / running_high.high) - 1.0,
            distance_to_running_low=(current.close / running_low.low) - 1.0,
            distance_to_structural_low=None,
            distance_to_structural_high=None,
        )
        if row.state_time_ms < event.event_detection_time_ms:
            raise MarketDataContractError("state_time_ms must be >= event_detection_time_ms")
        event_rows.append(row)
    return event_rows


def _minutes_between(start_ms: int, end_ms: int) -> int:
    if end_ms < start_ms:
        raise MarketDataContractError("end timestamp must be >= start timestamp")
    return (end_ms - start_ms) // ONE_MINUTE_MS


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_float(row: Mapping[str, object], name: str) -> float | None:
    value = row[name]
    if pd.isna(value) or value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when provided")
    return result


def _optional_bool(row: Mapping[str, object], name: str, *, default: bool) -> bool:
    value = row[name]
    if pd.isna(value) or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} must be boolean when provided")


def _csv_value(value: object) -> object:
    return "" if value is None else value
