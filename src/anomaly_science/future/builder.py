from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.future import (
    BARRIER_RESOLUTION_NONE,
    BARRIER_RESOLUTION_STOP_LOSS_FIRST,
    FuturePathRow,
)
from anomaly_science.contracts.market import Candle1m, MarketDataContractError, ONE_MINUTE_MS
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.data.source import CsvDataSourceError, MarketDataSource
from anomaly_science.future.atr import AtrAsOfResult, compute_atr_1d_asof
from anomaly_science.future.config import FuturePathBuilderConfig


class AnomalyStateArtifactError(ValueError):
    """Raised when anomaly_state_1m.csv violates its strict artifact boundary."""


class AnomalyFutureArtifactError(ValueError):
    """Raised when anomaly_future_paths.csv violates its strict artifact boundary."""


def load_anomaly_state_1m_csv(path: str | Path) -> tuple[AnomalyState1mRow, ...]:
    """Read anomaly_state_1m.csv through the declared MVP1 artifact schema.

    The boundary is intentionally strict: the file must have exactly the schema
    columns. Nullable structural fields may be empty, because Patch 5 did not
    compute structural features and Patch 6 must not create proxy fields.
    """
    state_path = Path(path)
    if not state_path.exists():
        raise AnomalyStateArtifactError(f"state artifact is missing: {state_path}")

    frame = _read_artifact_csv(state_path)
    schema = get_artifact_schema("anomaly_state_1m.csv")
    expected_columns = list(schema.required_columns)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise AnomalyStateArtifactError(
            f"state artifact columns must match {expected_columns}, got {actual_columns}"
        )

    rows: list[AnomalyState1mRow] = []
    for row_index, row in frame.iterrows():
        try:
            rows.append(
                AnomalyState1mRow(
                    event_id=_required_str(row, "event_id"),
                    symbol=_required_str(row, "symbol"),
                    state_time_ms=_required_int(row, "state_time_ms"),
                    snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
                    feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
                    minutes_since_event_start=_required_int(row, "minutes_since_event_start"),
                    minutes_since_detection=_required_int(row, "minutes_since_detection"),
                    event_alive=_required_bool(row, "event_alive"),
                    running_high_asof_t=_required_float(row, "running_high_asof_t"),
                    running_high_time_asof_t_ms=_required_int(row, "running_high_time_asof_t_ms"),
                    running_low_asof_t=_required_float(row, "running_low_asof_t"),
                    running_low_time_asof_t_ms=_required_int(row, "running_low_time_asof_t_ms"),
                    time_since_running_high_minutes=_required_int(row, "time_since_running_high_minutes"),
                    current_close=_required_float(row, "current_close"),
                    current_return_from_start=_required_float(row, "current_return_from_start"),
                    distance_to_running_high=_required_float(row, "distance_to_running_high"),
                    distance_to_running_low=_required_float(row, "distance_to_running_low"),
                    distance_to_structural_low=_optional_float(row, "distance_to_structural_low"),
                    distance_to_structural_high=_optional_float(row, "distance_to_structural_high"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise AnomalyStateArtifactError(f"invalid anomaly_state_1m.csv row {row_index}: {exc}") from exc
    return tuple(rows)


def load_anomaly_future_paths_csv(path: str | Path) -> tuple[FuturePathRow, ...]:
    """Read anomaly_future_paths.csv through the declared MVP1 artifact schema."""
    future_path = Path(path)
    if not future_path.exists():
        raise AnomalyFutureArtifactError(f"future artifact is missing: {future_path}")

    frame = _read_artifact_csv(future_path)
    schema = get_artifact_schema("anomaly_future_paths.csv")
    expected_columns = list(schema.required_columns)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise AnomalyFutureArtifactError(
            f"future artifact columns must match {expected_columns}, got {actual_columns}"
        )

    rows: list[FuturePathRow] = []
    for row_index, row in frame.iterrows():
        try:
            rows.append(
                FuturePathRow(
                    event_id=_required_str(row, "event_id"),
                    symbol=_required_str(row, "symbol"),
                    snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
                    feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
                    future_start_time_ms=_required_int(row, "future_start_time_ms"),
                    atr_window_minutes=_required_int(row, "atr_window_minutes"),
                    ATR_1d_asof_t=_optional_float(row, "ATR_1d_asof_t"),
                    ATR_1d_pct_asof_t=_optional_float(row, "ATR_1d_pct_asof_t"),
                    double_barrier_k_continuation=_optional_float(row, "double_barrier_k_continuation"),
                    double_barrier_k_fade=_optional_float(row, "double_barrier_k_fade"),
                    future_return_5m=_optional_float(row, "future_return_5m"),
                    future_return_15m=_optional_float(row, "future_return_15m"),
                    future_return_30m=_optional_float(row, "future_return_30m"),
                    future_return_60m=_optional_float(row, "future_return_60m"),
                    future_return_120m=_optional_float(row, "future_return_120m"),
                    future_max_5m=_optional_float(row, "future_max_5m"),
                    future_max_15m=_optional_float(row, "future_max_15m"),
                    future_max_30m=_optional_float(row, "future_max_30m"),
                    future_max_60m=_optional_float(row, "future_max_60m"),
                    future_max_120m=_optional_float(row, "future_max_120m"),
                    future_min_5m=_optional_float(row, "future_min_5m"),
                    future_min_15m=_optional_float(row, "future_min_15m"),
                    future_min_30m=_optional_float(row, "future_min_30m"),
                    future_min_60m=_optional_float(row, "future_min_60m"),
                    future_min_120m=_optional_float(row, "future_min_120m"),
                    future_return_atr_5m=_optional_float(row, "future_return_atr_5m"),
                    future_return_atr_15m=_optional_float(row, "future_return_atr_15m"),
                    future_return_atr_30m=_optional_float(row, "future_return_atr_30m"),
                    future_return_atr_60m=_optional_float(row, "future_return_atr_60m"),
                    future_return_atr_120m=_optional_float(row, "future_return_atr_120m"),
                    future_max_atr_5m=_optional_float(row, "future_max_atr_5m"),
                    future_max_atr_15m=_optional_float(row, "future_max_atr_15m"),
                    future_max_atr_30m=_optional_float(row, "future_max_atr_30m"),
                    future_max_atr_60m=_optional_float(row, "future_max_atr_60m"),
                    future_max_atr_120m=_optional_float(row, "future_max_atr_120m"),
                    future_min_atr_5m=_optional_float(row, "future_min_atr_5m"),
                    future_min_atr_15m=_optional_float(row, "future_min_atr_15m"),
                    future_min_atr_30m=_optional_float(row, "future_min_atr_30m"),
                    future_min_atr_60m=_optional_float(row, "future_min_atr_60m"),
                    future_min_atr_120m=_optional_float(row, "future_min_atr_120m"),
                    intracandle_double_barrier_hit_5m=_optional_bool(row, "intracandle_double_barrier_hit_5m"),
                    intracandle_double_barrier_hit_15m=_optional_bool(row, "intracandle_double_barrier_hit_15m"),
                    intracandle_double_barrier_hit_30m=_optional_bool(row, "intracandle_double_barrier_hit_30m"),
                    intracandle_double_barrier_hit_60m=_optional_bool(row, "intracandle_double_barrier_hit_60m"),
                    intracandle_double_barrier_hit_120m=_optional_bool(row, "intracandle_double_barrier_hit_120m"),
                    barrier_resolution_5m=_optional_str(row, "barrier_resolution_5m"),
                    barrier_resolution_15m=_optional_str(row, "barrier_resolution_15m"),
                    barrier_resolution_30m=_optional_str(row, "barrier_resolution_30m"),
                    barrier_resolution_60m=_optional_str(row, "barrier_resolution_60m"),
                    barrier_resolution_120m=_optional_str(row, "barrier_resolution_120m"),
                    reclaimed_running_high_30m=_optional_bool(row, "reclaimed_running_high_30m"),
                    reclaimed_running_high_60m=_optional_bool(row, "reclaimed_running_high_60m"),
                    broke_structural_low_30m=_optional_bool(row, "broke_structural_low_30m"),
                    broke_structural_low_60m=_optional_bool(row, "broke_structural_low_60m"),
                    time_to_new_high_minutes=_optional_int(row, "time_to_new_high_minutes"),
                    time_to_structural_break_minutes=_optional_int(row, "time_to_structural_break_minutes"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise AnomalyFutureArtifactError(f"invalid anomaly_future_paths.csv row {row_index}: {exc}") from exc
    return tuple(rows)


def _read_artifact_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def build_anomaly_future_paths(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    config: FuturePathBuilderConfig | None = None,
) -> tuple[FuturePathRow, ...]:
    """Build raw future paths strictly after each online state snapshot.

    Feature-side state fields are never recomputed here. The only state values
    used are the snapshot close and as-of running high, both already materialized
    by anomaly_state_1m.csv. Future candles must have available_time_ms greater
    than snapshot_time_ms to enter an outcome window.
    """
    cfg = config or FuturePathBuilderConfig()
    max_horizon = max(cfg.future_return_horizons_minutes)

    candles_by_symbol: dict[str, list[Candle1m]] = {}
    for candle in candles_1m:
        candles_by_symbol.setdefault(candle.symbol, []).append(candle)
    for symbol in candles_by_symbol:
        candles_by_symbol[symbol].sort(key=lambda item: (item.available_time_ms, item.open_time_ms))

    rows: list[FuturePathRow] = []
    for state in sorted(state_rows, key=lambda item: (item.symbol, item.snapshot_time_ms, item.event_id)):
        symbol_candles = candles_by_symbol.get(state.symbol, [])
        rows.append(
            _build_state_future_path(
                state=state,
                candles=symbol_candles,
                max_horizon=max_horizon,
                atr_window_minutes=cfg.atr_window_minutes,
                double_barrier_k_continuation=cfg.double_barrier_k_continuation,
                double_barrier_k_fade=cfg.double_barrier_k_fade,
            )
        )
    return tuple(rows)


def build_anomaly_future_paths_from_source(
    *,
    source: MarketDataSource,
    state_path: str | Path,
    config: FuturePathBuilderConfig | None = None,
) -> tuple[FuturePathRow, ...]:
    """Load normalized 1m candles via DataSource and state rows via artifact boundary."""
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    state_rows = load_anomaly_state_1m_csv(state_path)
    return build_anomaly_future_paths(
        candles_1m=normalize_candles_1m(frame),
        state_rows=state_rows,
        config=config,
    )


def future_rows_to_artifact(rows: Sequence[FuturePathRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        result.append({key: _csv_value(value) for key, value in payload.items()})
    return result


def _build_state_future_path(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    max_horizon: int,
    atr_window_minutes: int,
    double_barrier_k_continuation: float,
    double_barrier_k_fade: float,
) -> FuturePathRow:
    if state.feature_cutoff_time_ms > state.snapshot_time_ms:
        raise MarketDataContractError("state feature_cutoff_time_ms must be <= snapshot_time_ms")
    if state.current_close <= 0:
        raise MarketDataContractError("state current_close must be positive")

    future_start_time_ms = state.snapshot_time_ms + ONE_MINUTE_MS
    max_future_time_ms = state.snapshot_time_ms + max_horizon * ONE_MINUTE_MS
    future_candles = [
        candle
        for candle in candles
        if candle.available_time_ms > state.snapshot_time_ms and candle.available_time_ms <= max_future_time_ms
    ]
    atr_result = _compute_atr_when_history_available(
        state=state,
        candles=candles,
        atr_window_minutes=atr_window_minutes,
    )
    atr_value = atr_result.atr_1d_asof_t if atr_result is not None else None
    barrier_5m = _intracandle_double_barrier(
        state=state,
        candles=future_candles,
        horizon_minutes=5,
        atr_value=atr_value,
        k_continuation=double_barrier_k_continuation,
        k_fade=double_barrier_k_fade,
    )
    barrier_15m = _intracandle_double_barrier(
        state=state,
        candles=future_candles,
        horizon_minutes=15,
        atr_value=atr_value,
        k_continuation=double_barrier_k_continuation,
        k_fade=double_barrier_k_fade,
    )
    barrier_30m = _intracandle_double_barrier(
        state=state,
        candles=future_candles,
        horizon_minutes=30,
        atr_value=atr_value,
        k_continuation=double_barrier_k_continuation,
        k_fade=double_barrier_k_fade,
    )
    barrier_60m = _intracandle_double_barrier(
        state=state,
        candles=future_candles,
        horizon_minutes=60,
        atr_value=atr_value,
        k_continuation=double_barrier_k_continuation,
        k_fade=double_barrier_k_fade,
    )
    barrier_120m = _intracandle_double_barrier(
        state=state,
        candles=future_candles,
        horizon_minutes=120,
        atr_value=atr_value,
        k_continuation=double_barrier_k_continuation,
        k_fade=double_barrier_k_fade,
    )

    return FuturePathRow(
        event_id=state.event_id,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        feature_cutoff_time_ms=state.feature_cutoff_time_ms,
        future_start_time_ms=future_start_time_ms,
        atr_window_minutes=atr_window_minutes,
        ATR_1d_asof_t=atr_value,
        ATR_1d_pct_asof_t=atr_result.atr_1d_pct_asof_t if atr_result is not None else None,
        double_barrier_k_continuation=double_barrier_k_continuation if atr_value is not None else None,
        double_barrier_k_fade=double_barrier_k_fade if atr_value is not None else None,
        future_return_5m=_future_return(state=state, candles=future_candles, horizon_minutes=5),
        future_return_15m=_future_return(state=state, candles=future_candles, horizon_minutes=15),
        future_return_30m=_future_return(state=state, candles=future_candles, horizon_minutes=30),
        future_return_60m=_future_return(state=state, candles=future_candles, horizon_minutes=60),
        future_return_120m=_future_return(state=state, candles=future_candles, horizon_minutes=120),
        future_max_5m=_future_max(state=state, candles=future_candles, horizon_minutes=5),
        future_max_15m=_future_max(state=state, candles=future_candles, horizon_minutes=15),
        future_max_30m=_future_max(state=state, candles=future_candles, horizon_minutes=30),
        future_max_60m=_future_max(state=state, candles=future_candles, horizon_minutes=60),
        future_max_120m=_future_max(state=state, candles=future_candles, horizon_minutes=120),
        future_min_5m=_future_min(state=state, candles=future_candles, horizon_minutes=5),
        future_min_15m=_future_min(state=state, candles=future_candles, horizon_minutes=15),
        future_min_30m=_future_min(state=state, candles=future_candles, horizon_minutes=30),
        future_min_60m=_future_min(state=state, candles=future_candles, horizon_minutes=60),
        future_min_120m=_future_min(state=state, candles=future_candles, horizon_minutes=120),
        future_return_atr_5m=_future_return_atr(state=state, candles=future_candles, horizon_minutes=5, atr_value=atr_value),
        future_return_atr_15m=_future_return_atr(state=state, candles=future_candles, horizon_minutes=15, atr_value=atr_value),
        future_return_atr_30m=_future_return_atr(state=state, candles=future_candles, horizon_minutes=30, atr_value=atr_value),
        future_return_atr_60m=_future_return_atr(state=state, candles=future_candles, horizon_minutes=60, atr_value=atr_value),
        future_return_atr_120m=_future_return_atr(state=state, candles=future_candles, horizon_minutes=120, atr_value=atr_value),
        future_max_atr_5m=_future_max_atr(state=state, candles=future_candles, horizon_minutes=5, atr_value=atr_value),
        future_max_atr_15m=_future_max_atr(state=state, candles=future_candles, horizon_minutes=15, atr_value=atr_value),
        future_max_atr_30m=_future_max_atr(state=state, candles=future_candles, horizon_minutes=30, atr_value=atr_value),
        future_max_atr_60m=_future_max_atr(state=state, candles=future_candles, horizon_minutes=60, atr_value=atr_value),
        future_max_atr_120m=_future_max_atr(state=state, candles=future_candles, horizon_minutes=120, atr_value=atr_value),
        future_min_atr_5m=_future_min_atr(state=state, candles=future_candles, horizon_minutes=5, atr_value=atr_value),
        future_min_atr_15m=_future_min_atr(state=state, candles=future_candles, horizon_minutes=15, atr_value=atr_value),
        future_min_atr_30m=_future_min_atr(state=state, candles=future_candles, horizon_minutes=30, atr_value=atr_value),
        future_min_atr_60m=_future_min_atr(state=state, candles=future_candles, horizon_minutes=60, atr_value=atr_value),
        future_min_atr_120m=_future_min_atr(state=state, candles=future_candles, horizon_minutes=120, atr_value=atr_value),
        intracandle_double_barrier_hit_5m=barrier_5m,
        intracandle_double_barrier_hit_15m=barrier_15m,
        intracandle_double_barrier_hit_30m=barrier_30m,
        intracandle_double_barrier_hit_60m=barrier_60m,
        intracandle_double_barrier_hit_120m=barrier_120m,
        barrier_resolution_5m=_barrier_resolution(barrier_5m),
        barrier_resolution_15m=_barrier_resolution(barrier_15m),
        barrier_resolution_30m=_barrier_resolution(barrier_30m),
        barrier_resolution_60m=_barrier_resolution(barrier_60m),
        barrier_resolution_120m=_barrier_resolution(barrier_120m),
        reclaimed_running_high_30m=_reclaimed_running_high(state=state, candles=future_candles, horizon_minutes=30),
        reclaimed_running_high_60m=_reclaimed_running_high(state=state, candles=future_candles, horizon_minutes=60),
        broke_structural_low_30m=None,
        broke_structural_low_60m=None,
        time_to_new_high_minutes=_time_to_new_high_minutes(state=state, candles=future_candles),
        time_to_structural_break_minutes=None,
    )


def _window(state: AnomalyState1mRow, candles: Sequence[Candle1m], horizon_minutes: int) -> list[Candle1m]:
    horizon_end_ms = state.snapshot_time_ms + horizon_minutes * ONE_MINUTE_MS
    return [candle for candle in candles if candle.available_time_ms <= horizon_end_ms]


def _exact_horizon_candle(
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> Candle1m | None:
    horizon_end_ms = state.snapshot_time_ms + horizon_minutes * ONE_MINUTE_MS
    for candle in candles:
        if candle.available_time_ms == horizon_end_ms:
            return candle
    return None


def _future_return(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> float | None:
    horizon_candle = _exact_horizon_candle(state, candles, horizon_minutes)
    if horizon_candle is None:
        return None
    return (horizon_candle.close / state.current_close) - 1.0


def _future_max(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> float | None:
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return (max(candle.high for candle in window) / state.current_close) - 1.0


def _future_min(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> float | None:
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return (min(candle.low for candle in window) / state.current_close) - 1.0


def _future_return_atr(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
    atr_value: float | None,
) -> float | None:
    if atr_value is None:
        return None
    horizon_candle = _exact_horizon_candle(state, candles, horizon_minutes)
    if horizon_candle is None:
        return None
    return (horizon_candle.close - state.current_close) / atr_value


def _future_max_atr(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
    atr_value: float | None,
) -> float | None:
    if atr_value is None:
        return None
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return (max(candle.high for candle in window) - state.current_close) / atr_value


def _future_min_atr(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
    atr_value: float | None,
) -> float | None:
    if atr_value is None:
        return None
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return (min(candle.low for candle in window) - state.current_close) / atr_value



def _intracandle_double_barrier(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
    atr_value: float | None,
    k_continuation: float,
    k_fade: float,
) -> bool | None:
    if atr_value is None:
        return None
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    upper_barrier = state.current_close + k_continuation * atr_value
    lower_barrier = state.current_close - k_fade * atr_value
    return any(candle.high >= upper_barrier and candle.low <= lower_barrier for candle in window)


def _barrier_resolution(hit: bool | None) -> str | None:
    if hit is None:
        return None
    if hit:
        return BARRIER_RESOLUTION_STOP_LOSS_FIRST
    return BARRIER_RESOLUTION_NONE

def _compute_atr_when_history_available(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    atr_window_minutes: int,
) -> AtrAsOfResult | None:
    asof_candle_count = sum(
        1
        for candle in candles
        if candle.symbol == state.symbol and candle.available_time_ms <= state.snapshot_time_ms
    )
    if asof_candle_count < atr_window_minutes + 1:
        return None
    return compute_atr_1d_asof(
        candles_1m=candles,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        atr_window_minutes=atr_window_minutes,
    )


def _reclaimed_running_high(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> bool | None:
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return any(candle.high > state.running_high_asof_t for candle in window)


def _time_to_new_high_minutes(*, state: AnomalyState1mRow, candles: Sequence[Candle1m]) -> int | None:
    for candle in candles:
        if candle.high > state.running_high_asof_t:
            return _minutes_between(state.snapshot_time_ms, candle.available_time_ms)
    return None


def _minutes_between(start_ms: int, end_ms: int) -> int:
    if end_ms <= start_ms:
        raise MarketDataContractError("future timestamp must be > snapshot timestamp")
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


def _optional_str(row: Mapping[str, object], name: str) -> str | None:
    value = row[name]
    if _is_missing(value):
        return None
    result = str(value)
    if not result:
        return None
    return result


def _optional_float(row: Mapping[str, object], name: str) -> float | None:
    value = row[name]
    if _is_missing(value):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when provided")
    return result


def _optional_int(row: Mapping[str, object], name: str) -> int | None:
    value = row[name]
    if _is_missing(value):
        return None
    return int(value)


def _required_bool(row: Mapping[str, object], name: str) -> bool:
    value = row[name]
    if isinstance(value, bool):
        return value
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _optional_bool(row: Mapping[str, object], name: str) -> bool | None:
    value = row[name]
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be a boolean when provided")


def _is_missing(value: object) -> bool:
    if value == "":
        return True
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def _csv_value(value: object) -> object:
    return "" if value is None else value
