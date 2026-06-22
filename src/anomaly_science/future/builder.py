from __future__ import annotations

import csv
import math
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence, TypeVar

import pandas as pd

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.future import (
    BARRIER_RESOLUTION_NONE,
    BARRIER_RESOLUTION_STOP_LOSS_FIRST,
    FuturePathRow,
)
from anomaly_science.contracts.market import Candle1m, MarketDataContractError, ONE_MINUTE_MS
from anomaly_science.contracts.state import StrategyState1mRow
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.data.source import CANDLE_REQUIRED_COLUMNS, CsvDataSourceError, MarketDataSource
from anomaly_science.future.atr import AtrAsOfResult, AtrComputationError
from anomaly_science.future.config import FuturePathBuilderConfig


class AnomalyStateArtifactError(ValueError):
    """Raised when anomaly_state_1m.csv violates its strict artifact boundary."""


class AnomalyFutureArtifactError(ValueError):
    """Raised when anomaly_future_paths.csv violates its strict artifact boundary."""


StrategyStateArtifactError = AnomalyStateArtifactError
StrategyFutureArtifactError = AnomalyFutureArtifactError
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _SymbolFutureCandleIndex:
    candles: tuple[Candle1m, ...]
    available_times: tuple[int, ...]
    true_range_prefix_sums: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _FutureMetrics:
    future_return: tuple[float | None, ...]
    future_max: tuple[float | None, ...]
    future_min: tuple[float | None, ...]
    future_return_atr: tuple[float | None, ...]
    future_max_atr: tuple[float | None, ...]
    future_min_atr: tuple[float | None, ...]
    barrier_hit: tuple[bool | None, ...]
    reclaimed_running_high: tuple[bool | None, ...]
    time_to_new_high_minutes: int | None


def load_strategy_state_1m_csv(path: str | Path) -> tuple[StrategyState1mRow, ...]:
    """Read anomaly_state_1m.csv through the declared MVP1 artifact schema.

    The boundary is intentionally strict: the file must have exactly the schema
    columns. Nullable structural fields stay empty until a causal swing level
    is confirmed; they must not be proxied from running highs/lows.
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

    rows: list[StrategyState1mRow] = []
    for row_index, row in enumerate(frame.itertuples(index=False)):
        rows.append(_state_row_from_object(row=row, row_index=row_index, artifact_name=state_path.name))
    return tuple(rows)


load_anomaly_state_1m_csv = load_strategy_state_1m_csv


def iter_strategy_state_1m_csv(path: str | Path) -> Iterable[StrategyState1mRow]:
    """Stream state rows through the same strict artifact boundary as the tuple loader."""
    state_path = Path(path)
    if not state_path.exists():
        raise AnomalyStateArtifactError(f"state artifact is missing: {state_path}")

    schema = get_artifact_schema("strategy_state_1m.csv")
    expected_columns = list(schema.required_columns)
    with state_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = list(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise AnomalyStateArtifactError(
                f"state artifact columns must match {expected_columns}, got {actual_columns}"
            )
        for row_index, row in enumerate(reader):
            yield _state_row_from_mapping(row=row, row_index=row_index, artifact_name=state_path.name)


iter_anomaly_state_1m_csv = iter_strategy_state_1m_csv


def iter_candles_1m_csv(path: str | Path) -> Iterable[Candle1m]:
    """Stream normalized 1m candles from the explicit CSV boundary."""
    candles_path = Path(path)
    if not candles_path.exists():
        raise CsvDataSourceError(f"required dataset 'candles_1m.csv' is missing: {candles_path}")
    with candles_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = tuple(reader.fieldnames or ())
        missing = [name for name in CANDLE_REQUIRED_COLUMNS if name not in actual_columns]
        if missing:
            raise CsvDataSourceError(f"dataset 'candles_1m.csv' is missing required columns: {missing}")
        has_number_of_trades = "number_of_trades" in actual_columns
        has_taker_buy_quote_volume = "taker_buy_quote_volume" in actual_columns
        for row_index, row in enumerate(reader):
            try:
                yield Candle1m(
                    symbol=_required_str(row, "symbol"),
                    open_time_ms=_required_int(row, "open_time_ms"),
                    available_time_ms=_required_int(row, "available_time_ms"),
                    open=_required_float(row, "open"),
                    high=_required_float(row, "high"),
                    low=_required_float(row, "low"),
                    close=_required_float(row, "close"),
                    volume=_required_float(row, "volume"),
                    quote_volume=_required_float(row, "quote_volume"),
                    number_of_trades=_optional_float(row, "number_of_trades") if has_number_of_trades else None,
                    taker_buy_quote_volume=(
                        _optional_float(row, "taker_buy_quote_volume") if has_taker_buy_quote_volume else None
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise CsvDataSourceError(f"invalid candles_1m.csv row {row_index}: {exc}") from exc


def load_strategy_future_paths_csv(path: str | Path) -> tuple[FuturePathRow, ...]:
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
    for row_index, row_tuple in enumerate(frame.itertuples(index=False)):
        row = row_tuple._asdict()
        rows.append(_future_path_row_from_mapping(row=row, row_index=row_index, artifact_name=future_path.name))
    return tuple(rows)


load_anomaly_future_paths_csv = load_strategy_future_paths_csv


def iter_strategy_future_paths_artifact_csv(path: str | Path) -> Iterable[FuturePathRow]:
    """Stream future path rows through the same strict artifact boundary as the tuple loader."""
    future_path = Path(path)
    if not future_path.exists():
        raise AnomalyFutureArtifactError(f"future artifact is missing: {future_path}")

    schema = get_artifact_schema("strategy_future_paths.csv")
    expected_columns = list(schema.required_columns)
    with future_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = list(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise AnomalyFutureArtifactError(
                f"future artifact columns must match {expected_columns}, got {actual_columns}"
            )
        for row_index, row in enumerate(reader):
            yield _future_path_row_from_mapping(row=row, row_index=row_index, artifact_name=future_path.name)


iter_anomaly_future_paths_artifact_csv = iter_strategy_future_paths_artifact_csv


def _future_path_row_from_mapping(
    *,
    row: Mapping[str, object],
    row_index: int,
    artifact_name: str,
) -> FuturePathRow:
    try:
        return FuturePathRow(
            event_id=_required_str(row, "event_id"),
            symbol=_required_str(row, "symbol"),
            snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
            feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
            future_start_time_ms=_required_int(row, "future_start_time_ms"),
            atr_window_minutes=_required_int(row, "atr_window_minutes"),
            core_atr_1440=_optional_float(row, "ATR_1d_asof_t"),
            ATR_1d_pct_asof_t=_optional_float(row, "ATR_1d_pct_asof_t"),
            double_barrier_k_continuation=_optional_float(row, "double_barrier_k_continuation"),
            double_barrier_k_fade=_optional_float(row, "double_barrier_k_fade"),
            future_return_5m=_optional_float(row, "future_return_5m"),
            future_return_15m=_optional_float(row, "future_return_15m"),
            future_return_30m=_optional_float(row, "future_return_30m"),
            future_return_60m=_optional_float(row, "future_return_60m"),
            future_return_120m=_optional_float(row, "future_return_120m"),
            future_return_180m=_optional_float(row, "future_return_180m"),
            future_max_5m=_optional_float(row, "future_max_5m"),
            future_max_15m=_optional_float(row, "future_max_15m"),
            future_max_30m=_optional_float(row, "future_max_30m"),
            future_max_60m=_optional_float(row, "future_max_60m"),
            future_max_120m=_optional_float(row, "future_max_120m"),
            future_max_180m=_optional_float(row, "future_max_180m"),
            future_min_5m=_optional_float(row, "future_min_5m"),
            future_min_15m=_optional_float(row, "future_min_15m"),
            future_min_30m=_optional_float(row, "future_min_30m"),
            future_min_60m=_optional_float(row, "future_min_60m"),
            future_min_120m=_optional_float(row, "future_min_120m"),
            future_min_180m=_optional_float(row, "future_min_180m"),
            future_return_atr_5m=_optional_float(row, "future_return_atr_5m"),
            future_return_atr_15m=_optional_float(row, "future_return_atr_15m"),
            future_return_atr_30m=_optional_float(row, "future_return_atr_30m"),
            future_return_atr_60m=_optional_float(row, "future_return_atr_60m"),
            future_return_atr_120m=_optional_float(row, "future_return_atr_120m"),
            future_return_atr_180m=_optional_float(row, "future_return_atr_180m"),
            future_max_atr_5m=_optional_float(row, "future_max_atr_5m"),
            future_max_atr_15m=_optional_float(row, "future_max_atr_15m"),
            future_max_atr_30m=_optional_float(row, "future_max_atr_30m"),
            future_max_atr_60m=_optional_float(row, "future_max_atr_60m"),
            future_max_atr_120m=_optional_float(row, "future_max_atr_120m"),
            future_max_atr_180m=_optional_float(row, "future_max_atr_180m"),
            future_min_atr_5m=_optional_float(row, "future_min_atr_5m"),
            future_min_atr_15m=_optional_float(row, "future_min_atr_15m"),
            future_min_atr_30m=_optional_float(row, "future_min_atr_30m"),
            future_min_atr_60m=_optional_float(row, "future_min_atr_60m"),
            future_min_atr_120m=_optional_float(row, "future_min_atr_120m"),
            future_min_atr_180m=_optional_float(row, "future_min_atr_180m"),
            intracandle_double_barrier_hit_5m=_optional_bool(row, "intracandle_double_barrier_hit_5m"),
            intracandle_double_barrier_hit_15m=_optional_bool(row, "intracandle_double_barrier_hit_15m"),
            intracandle_double_barrier_hit_30m=_optional_bool(row, "intracandle_double_barrier_hit_30m"),
            intracandle_double_barrier_hit_60m=_optional_bool(row, "intracandle_double_barrier_hit_60m"),
            intracandle_double_barrier_hit_120m=_optional_bool(row, "intracandle_double_barrier_hit_120m"),
            intracandle_double_barrier_hit_180m=_optional_bool(row, "intracandle_double_barrier_hit_180m"),
            barrier_resolution_5m=_optional_str(row, "barrier_resolution_5m"),
            barrier_resolution_15m=_optional_str(row, "barrier_resolution_15m"),
            barrier_resolution_30m=_optional_str(row, "barrier_resolution_30m"),
            barrier_resolution_60m=_optional_str(row, "barrier_resolution_60m"),
            barrier_resolution_120m=_optional_str(row, "barrier_resolution_120m"),
            barrier_resolution_180m=_optional_str(row, "barrier_resolution_180m"),
            reclaimed_running_high_30m=_optional_bool(row, "reclaimed_running_high_30m"),
            reclaimed_running_high_60m=_optional_bool(row, "reclaimed_running_high_60m"),
            broke_structural_low_30m=_optional_bool(row, "broke_structural_low_30m"),
            broke_structural_low_60m=_optional_bool(row, "broke_structural_low_60m"),
            time_to_new_high_minutes=_optional_int(row, "time_to_new_high_minutes"),
            time_to_structural_break_minutes=_optional_int(row, "time_to_structural_break_minutes"),
        )
    except (TypeError, ValueError) as exc:
        raise AnomalyFutureArtifactError(f"invalid {artifact_name} row {row_index}: {exc}") from exc


def _read_artifact_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def build_strategy_future_paths(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    config: FuturePathBuilderConfig | None = None,
) -> tuple[FuturePathRow, ...]:
    return tuple(
        iter_strategy_future_paths(
            candles_1m=candles_1m,
            state_rows=state_rows,
            config=config,
            sort_state_rows=True,
        )
    )


def iter_strategy_future_paths(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    config: FuturePathBuilderConfig | None = None,
    sort_state_rows: bool = False,
) -> Iterable[FuturePathRow]:
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
    indexed_candles_by_symbol = {
        symbol: _build_symbol_future_candle_index(symbol_candles)
        for symbol, symbol_candles in candles_by_symbol.items()
    }

    ordered_state_rows = (
        sorted(state_rows, key=lambda item: (item.symbol, item.snapshot_time_ms, item.event_id))
        if sort_state_rows
        else state_rows
    )
    for state in ordered_state_rows:
        symbol_candles = indexed_candles_by_symbol.get(state.symbol, _empty_symbol_future_candle_index())
        yield _build_state_future_path(
            state=state,
            candle_index=symbol_candles,
            max_horizon=max_horizon,
            atr_window_minutes=cfg.atr_window_minutes,
            double_barrier_k_continuation=cfg.double_barrier_k_continuation,
            double_barrier_k_fade=cfg.double_barrier_k_fade,
        )


build_anomaly_future_paths = build_strategy_future_paths
iter_anomaly_future_paths = iter_strategy_future_paths


def build_strategy_future_paths_from_source(
    *,
    source: MarketDataSource,
    state_path: str | Path,
    config: FuturePathBuilderConfig | None = None,
) -> tuple[FuturePathRow, ...]:
    """Load normalized 1m candles via DataSource and state rows via artifact boundary."""
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    state_rows = load_strategy_state_1m_csv(state_path)
    return build_strategy_future_paths(
        candles_1m=normalize_candles_1m(frame),
        state_rows=state_rows,
        config=config,
    )


build_anomaly_future_paths_from_source = build_strategy_future_paths_from_source


def iter_strategy_future_paths_from_source(
    *,
    source: MarketDataSource,
    state_path: str | Path,
    config: FuturePathBuilderConfig | None = None,
) -> Iterable[FuturePathRow]:
    """Stream future path rows from normalized candles and a strict state artifact."""
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    return iter_strategy_future_paths(
        candles_1m=normalize_candles_1m(frame),
        state_rows=iter_strategy_state_1m_csv(state_path),
        config=config,
        sort_state_rows=False,
    )


iter_anomaly_future_paths_from_source = iter_strategy_future_paths_from_source


def iter_strategy_future_paths_from_csv(
    *,
    input_dir: str | Path,
    state_path: str | Path,
    config: FuturePathBuilderConfig | None = None,
) -> Iterable[FuturePathRow]:
    input_path = Path(input_dir)
    return iter_strategy_future_paths_from_grouped_csv(
        candles_path=input_path / "candles_1m.csv",
        state_path=state_path,
        config=config,
    )


iter_anomaly_future_paths_from_csv = iter_strategy_future_paths_from_csv


def iter_strategy_future_paths_from_grouped_csv(
    *,
    candles_path: str | Path,
    state_path: str | Path,
    config: FuturePathBuilderConfig | None = None,
) -> Iterable[FuturePathRow]:
    """Stream future rows while holding one symbol's candles and states in memory."""
    cfg = config or FuturePathBuilderConfig()
    max_horizon = max(cfg.future_return_horizons_minutes)
    empty_index = _empty_symbol_future_candle_index()
    state_groups = _symbol_groups(
        iter_strategy_state_1m_csv(state_path),
        symbol_getter=lambda row: row.symbol,
        source_name="strategy_state_1m.csv",
    )
    try:
        current_state_symbol, current_states = next(state_groups)
    except StopIteration:
        return

    for candle_symbol, symbol_candles in _symbol_groups(
        iter_candles_1m_csv(candles_path),
        symbol_getter=lambda row: row.symbol,
        source_name="candles_1m.csv",
    ):
        while current_state_symbol < candle_symbol:
            yield from _iter_state_group_future_rows(
                states=current_states,
                candle_index=empty_index,
                max_horizon=max_horizon,
                config=cfg,
            )
            try:
                current_state_symbol, current_states = next(state_groups)
            except StopIteration:
                return
        if current_state_symbol != candle_symbol:
            continue
        yield from _iter_state_group_future_rows(
            states=current_states,
            candle_index=_build_symbol_future_candle_index(symbol_candles),
            max_horizon=max_horizon,
            config=cfg,
        )
        try:
            current_state_symbol, current_states = next(state_groups)
        except StopIteration:
            return

    yield from _iter_state_group_future_rows(
        states=current_states,
        candle_index=empty_index,
        max_horizon=max_horizon,
        config=cfg,
    )
    for _, remaining_states in state_groups:
        yield from _iter_state_group_future_rows(
            states=remaining_states,
            candle_index=empty_index,
            max_horizon=max_horizon,
            config=cfg,
        )


def _iter_state_group_future_rows(
    *,
    states: Sequence[StrategyState1mRow],
    candle_index: _SymbolFutureCandleIndex,
    max_horizon: int,
    config: FuturePathBuilderConfig,
) -> Iterable[FuturePathRow]:
    for state in states:
        yield _build_state_future_path(
            state=state,
            candle_index=candle_index,
            max_horizon=max_horizon,
            atr_window_minutes=config.atr_window_minutes,
            double_barrier_k_continuation=config.double_barrier_k_continuation,
            double_barrier_k_fade=config.double_barrier_k_fade,
        )


def _symbol_groups(
    rows: Iterable[T],
    *,
    symbol_getter: Callable[[T], str],
    source_name: str,
) -> Iterable[tuple[str, list[T]]]:
    current_symbol: str | None = None
    current_rows: list[T] = []
    completed_symbols: set[str] = set()
    for row in rows:
        symbol = symbol_getter(row)
        if current_symbol is None:
            current_symbol = symbol
        if symbol != current_symbol:
            completed_symbols.add(current_symbol)
            yield current_symbol, current_rows
            current_rows = []
            current_symbol = symbol
            if current_symbol in completed_symbols:
                raise CsvDataSourceError(
                    f"{source_name} must be grouped by symbol for streaming future build; "
                    f"symbol {current_symbol!r} appears in multiple groups"
                )
        current_rows.append(row)
    if current_symbol is not None:
        yield current_symbol, current_rows


def future_rows_to_artifact(rows: Sequence[FuturePathRow]) -> list[dict[str, object]]:
    return [future_row_to_artifact(row) for row in rows]


def future_row_to_artifact(row: FuturePathRow) -> dict[str, object]:
    return future_row_to_artifact_for_columns(
        row=row,
        fieldnames=get_artifact_schema("strategy_future_paths.csv").required_columns,
    )


def future_row_to_artifact_for_columns(*, row: FuturePathRow, fieldnames: Sequence[str]) -> dict[str, object]:
    attribute_names = [future_row_attribute_name(fieldname) for fieldname in fieldnames]
    return future_row_to_artifact_for_attributes(row=row, fieldnames=fieldnames, attribute_names=attribute_names)


def future_row_to_artifact_for_attributes(
    *,
    row: FuturePathRow,
    fieldnames: Sequence[str],
    attribute_names: Sequence[str],
) -> dict[str, object]:
    return {
        fieldname: _csv_value(getattr(row, attribute_name))
        for fieldname, attribute_name in zip(fieldnames, attribute_names)
    }


def validate_future_row_fieldnames(fieldnames: Sequence[str]) -> None:
    row_fields = set(FuturePathRow.__dataclass_fields__)
    missing = [
        fieldname
        for fieldname in fieldnames
        if future_row_attribute_name(fieldname) not in row_fields
    ]
    if missing:
        raise ValueError(f"strategy_future_paths.csv schema has unknown row fields: {missing}")


def future_row_attribute_name(fieldname: str) -> str:
    if fieldname == "ATR_1d_asof_t":
        return "core_atr_1440"
    return fieldname


def _build_state_future_path(
    *,
    state: StrategyState1mRow,
    candle_index: _SymbolFutureCandleIndex,
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
    future_start_index = bisect_right(candle_index.available_times, state.snapshot_time_ms)
    future_end_index = bisect_right(candle_index.available_times, max_future_time_ms)
    atr_result = _compute_atr_when_history_available(
        state=state,
        candle_index=candle_index,
        atr_window_minutes=atr_window_minutes,
    )
    atr_value = atr_result.core_atr_1440 if atr_result is not None else None
    metrics = _future_metrics(
        state=state,
        candles=candle_index.candles,
        start_index=future_start_index,
        end_index=future_end_index,
        horizons=(5, 15, 30, 60, 120, 180),
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
        core_atr_1440=atr_value,
        ATR_1d_pct_asof_t=atr_result.atr_1d_pct_asof_t if atr_result is not None else None,
        double_barrier_k_continuation=double_barrier_k_continuation if atr_value is not None else None,
        double_barrier_k_fade=double_barrier_k_fade if atr_value is not None else None,
        future_return_5m=metrics.future_return[0],
        future_return_15m=metrics.future_return[1],
        future_return_30m=metrics.future_return[2],
        future_return_60m=metrics.future_return[3],
        future_return_120m=metrics.future_return[4],
        future_return_180m=metrics.future_return[5],
        future_max_5m=metrics.future_max[0],
        future_max_15m=metrics.future_max[1],
        future_max_30m=metrics.future_max[2],
        future_max_60m=metrics.future_max[3],
        future_max_120m=metrics.future_max[4],
        future_max_180m=metrics.future_max[5],
        future_min_5m=metrics.future_min[0],
        future_min_15m=metrics.future_min[1],
        future_min_30m=metrics.future_min[2],
        future_min_60m=metrics.future_min[3],
        future_min_120m=metrics.future_min[4],
        future_min_180m=metrics.future_min[5],
        future_return_atr_5m=metrics.future_return_atr[0],
        future_return_atr_15m=metrics.future_return_atr[1],
        future_return_atr_30m=metrics.future_return_atr[2],
        future_return_atr_60m=metrics.future_return_atr[3],
        future_return_atr_120m=metrics.future_return_atr[4],
        future_return_atr_180m=metrics.future_return_atr[5],
        future_max_atr_5m=metrics.future_max_atr[0],
        future_max_atr_15m=metrics.future_max_atr[1],
        future_max_atr_30m=metrics.future_max_atr[2],
        future_max_atr_60m=metrics.future_max_atr[3],
        future_max_atr_120m=metrics.future_max_atr[4],
        future_max_atr_180m=metrics.future_max_atr[5],
        future_min_atr_5m=metrics.future_min_atr[0],
        future_min_atr_15m=metrics.future_min_atr[1],
        future_min_atr_30m=metrics.future_min_atr[2],
        future_min_atr_60m=metrics.future_min_atr[3],
        future_min_atr_120m=metrics.future_min_atr[4],
        future_min_atr_180m=metrics.future_min_atr[5],
        intracandle_double_barrier_hit_5m=metrics.barrier_hit[0],
        intracandle_double_barrier_hit_15m=metrics.barrier_hit[1],
        intracandle_double_barrier_hit_30m=metrics.barrier_hit[2],
        intracandle_double_barrier_hit_60m=metrics.barrier_hit[3],
        intracandle_double_barrier_hit_120m=metrics.barrier_hit[4],
        intracandle_double_barrier_hit_180m=metrics.barrier_hit[5],
        barrier_resolution_5m=_barrier_resolution(metrics.barrier_hit[0]),
        barrier_resolution_15m=_barrier_resolution(metrics.barrier_hit[1]),
        barrier_resolution_30m=_barrier_resolution(metrics.barrier_hit[2]),
        barrier_resolution_60m=_barrier_resolution(metrics.barrier_hit[3]),
        barrier_resolution_120m=_barrier_resolution(metrics.barrier_hit[4]),
        barrier_resolution_180m=_barrier_resolution(metrics.barrier_hit[5]),
        reclaimed_running_high_30m=metrics.reclaimed_running_high[2],
        reclaimed_running_high_60m=metrics.reclaimed_running_high[3],
        broke_structural_low_30m=None,
        broke_structural_low_60m=None,
        time_to_new_high_minutes=metrics.time_to_new_high_minutes,
        time_to_structural_break_minutes=None,
    )


def _build_symbol_future_candle_index(candles: Sequence[Candle1m]) -> _SymbolFutureCandleIndex:
    ordered = tuple(sorted(candles, key=lambda item: (item.available_time_ms, item.open_time_ms)))
    prefix: list[float] = []
    running_sum = 0.0
    for index, candle in enumerate(ordered):
        if index == 0:
            prefix.append(0.0)
            continue
        running_sum += _true_range(candle=candle, previous_close=ordered[index - 1].close)
        prefix.append(running_sum)
    return _SymbolFutureCandleIndex(
        candles=ordered,
        available_times=tuple(candle.available_time_ms for candle in ordered),
        true_range_prefix_sums=tuple(prefix),
    )


def _empty_symbol_future_candle_index() -> _SymbolFutureCandleIndex:
    return _SymbolFutureCandleIndex(candles=(), available_times=(), true_range_prefix_sums=())


def _future_metrics(
    *,
    state: StrategyState1mRow,
    candles: Sequence[Candle1m],
    start_index: int = 0,
    end_index: int | None = None,
    horizons: Sequence[int],
    atr_value: float | None,
    k_continuation: float,
    k_fade: float,
) -> _FutureMetrics:
    sorted_horizons = tuple(sorted(horizons))
    metric_count = len(sorted_horizons)
    future_return: list[float | None] = [None] * metric_count
    future_max: list[float | None] = [None] * metric_count
    future_min: list[float | None] = [None] * metric_count
    future_return_atr: list[float | None] = [None] * metric_count
    future_max_atr: list[float | None] = [None] * metric_count
    future_min_atr: list[float | None] = [None] * metric_count
    barrier_hit: list[bool | None] = [None] * metric_count
    reclaimed_running_high: list[bool | None] = [None] * metric_count

    cursor = 0
    running_high: float | None = None
    running_low: float | None = None
    running_barrier_hit = False
    running_reclaimed_high = False
    time_to_new_high_minutes: int | None = None
    upper_barrier = state.current_close + k_continuation * atr_value if atr_value is not None else None
    lower_barrier = state.current_close - k_fade * atr_value if atr_value is not None else None
    limit = len(candles) if end_index is None else end_index

    for horizon_index, horizon in enumerate(sorted_horizons):
        horizon_end_ms = state.snapshot_time_ms + horizon * ONE_MINUTE_MS
        had_window = False
        cursor = max(cursor, start_index)
        while cursor < limit and candles[cursor].available_time_ms <= horizon_end_ms:
            candle = candles[cursor]
            had_window = True
            running_high = candle.high if running_high is None else max(running_high, candle.high)
            running_low = candle.low if running_low is None else min(running_low, candle.low)
            if candle.available_time_ms == horizon_end_ms and future_return[horizon_index] is None:
                future_return[horizon_index] = (candle.close / state.current_close) - 1.0
                if atr_value is not None:
                    future_return_atr[horizon_index] = (candle.close - state.current_close) / atr_value
            if atr_value is not None and upper_barrier is not None and lower_barrier is not None:
                running_barrier_hit = running_barrier_hit or (
                    candle.high >= upper_barrier and candle.low <= lower_barrier
                )
            running_reclaimed_high = running_reclaimed_high or candle.high > state.running_high_asof_t
            if time_to_new_high_minutes is None and candle.high > state.running_high_asof_t:
                time_to_new_high_minutes = _minutes_between(state.snapshot_time_ms, candle.available_time_ms)
            cursor += 1

        if running_high is not None and running_low is not None:
            future_max[horizon_index] = (running_high / state.current_close) - 1.0
            future_min[horizon_index] = (running_low / state.current_close) - 1.0
            if atr_value is not None:
                future_max_atr[horizon_index] = (running_high - state.current_close) / atr_value
                future_min_atr[horizon_index] = (running_low - state.current_close) / atr_value
                barrier_hit[horizon_index] = running_barrier_hit
            reclaimed_running_high[horizon_index] = running_reclaimed_high
        elif had_window and atr_value is not None:
            barrier_hit[horizon_index] = running_barrier_hit

    return _FutureMetrics(
        future_return=tuple(future_return),
        future_max=tuple(future_max),
        future_min=tuple(future_min),
        future_return_atr=tuple(future_return_atr),
        future_max_atr=tuple(future_max_atr),
        future_min_atr=tuple(future_min_atr),
        barrier_hit=tuple(barrier_hit),
        reclaimed_running_high=tuple(reclaimed_running_high),
        time_to_new_high_minutes=time_to_new_high_minutes,
    )


def _window(state: StrategyState1mRow, candles: Sequence[Candle1m], horizon_minutes: int) -> list[Candle1m]:
    horizon_end_ms = state.snapshot_time_ms + horizon_minutes * ONE_MINUTE_MS
    return [candle for candle in candles if candle.available_time_ms <= horizon_end_ms]


def _exact_horizon_candle(
    state: StrategyState1mRow,
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
    state: StrategyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> float | None:
    horizon_candle = _exact_horizon_candle(state, candles, horizon_minutes)
    if horizon_candle is None:
        return None
    return (horizon_candle.close / state.current_close) - 1.0


def _future_max(
    *,
    state: StrategyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> float | None:
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return (max(candle.high for candle in window) / state.current_close) - 1.0


def _future_min(
    *,
    state: StrategyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> float | None:
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return (min(candle.low for candle in window) / state.current_close) - 1.0


def _future_return_atr(
    *,
    state: StrategyState1mRow,
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
    state: StrategyState1mRow,
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
    state: StrategyState1mRow,
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
    state: StrategyState1mRow,
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
    state: StrategyState1mRow,
    candle_index: _SymbolFutureCandleIndex,
    atr_window_minutes: int,
) -> AtrAsOfResult | None:
    history_count = bisect_right(candle_index.available_times, state.snapshot_time_ms)
    if history_count < atr_window_minutes + 1:
        return None
    source_start_index = history_count - atr_window_minutes
    last_source_index = history_count - 1
    prefix_before_window = candle_index.true_range_prefix_sums[source_start_index - 1]
    prefix_at_window_end = candle_index.true_range_prefix_sums[last_source_index]
    atr = (prefix_at_window_end - prefix_before_window) / atr_window_minutes
    last_close = candle_index.candles[last_source_index].close
    if last_close <= 0:
        raise AtrComputationError("latest as-of close must be positive")
    if not math.isfinite(atr):
        raise AtrComputationError("computed ATR must be positive and finite")
    if atr <= 0:
        return None
    return AtrAsOfResult(
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        atr_window_minutes=atr_window_minutes,
        core_atr_1440=atr,
        atr_1d_pct_asof_t=atr / last_close,
        source_candle_count=atr_window_minutes,
        first_candle_available_time_ms=candle_index.candles[source_start_index].available_time_ms,
        last_candle_available_time_ms=candle_index.candles[last_source_index].available_time_ms,
    )


def _reclaimed_running_high(
    *,
    state: StrategyState1mRow,
    candles: Sequence[Candle1m],
    horizon_minutes: int,
) -> bool | None:
    window = _window(state, candles, horizon_minutes)
    if not window:
        return None
    return any(candle.high > state.running_high_asof_t for candle in window)


def _time_to_new_high_minutes(*, state: StrategyState1mRow, candles: Sequence[Candle1m]) -> int | None:
    for candle in candles:
        if candle.high > state.running_high_asof_t:
            return _minutes_between(state.snapshot_time_ms, candle.available_time_ms)
    return None


def _minutes_between(start_ms: int, end_ms: int) -> int:
    if end_ms <= start_ms:
        raise MarketDataContractError("future timestamp must be > snapshot timestamp")
    return (end_ms - start_ms) // ONE_MINUTE_MS


def _true_range(*, candle: Candle1m, previous_close: float) -> float:
    if previous_close <= 0:
        raise AtrComputationError("previous close must be positive")
    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )


def _state_row_from_object(*, row: object, row_index: int, artifact_name: str) -> StrategyState1mRow:
    try:
        return StrategyState1mRow(
            event_id=_tuple_required_str(row, "event_id"),
            symbol=_tuple_required_str(row, "symbol"),
            state_time_ms=_tuple_required_int(row, "state_time_ms"),
            snapshot_time_ms=_tuple_required_int(row, "snapshot_time_ms"),
            feature_cutoff_time_ms=_tuple_required_int(row, "feature_cutoff_time_ms"),
            minutes_since_event_start=_tuple_required_int(row, "minutes_since_event_start"),
            minutes_since_detection=_tuple_required_int(row, "minutes_since_detection"),
            event_alive=_tuple_required_bool(row, "event_alive"),
            running_high_asof_t=_tuple_required_float(row, "running_high_asof_t"),
            running_high_time_asof_t_ms=_tuple_required_int(row, "running_high_time_asof_t_ms"),
            running_low_asof_t=_tuple_required_float(row, "running_low_asof_t"),
            running_low_time_asof_t_ms=_tuple_required_int(row, "running_low_time_asof_t_ms"),
            time_since_running_high_minutes=_tuple_required_int(row, "time_since_running_high_minutes"),
            current_close=_tuple_required_float(row, "current_close"),
            current_return_from_start=_tuple_required_float(row, "current_return_from_start"),
            distance_to_running_high=_tuple_required_float(row, "distance_to_running_high"),
            distance_to_running_low=_tuple_required_float(row, "distance_to_running_low"),
            distance_to_structural_low=_tuple_optional_float(row, "distance_to_structural_low"),
            distance_to_structural_high=_tuple_optional_float(row, "distance_to_structural_high"),
            structural_low_asof_t=_tuple_optional_float(row, "structural_low_asof_t"),
            structural_low_time_asof_t_ms=_tuple_optional_int(row, "structural_low_time_asof_t_ms"),
            structural_high_asof_t=_tuple_optional_float(row, "structural_high_asof_t"),
            structural_high_time_asof_t_ms=_tuple_optional_int(row, "structural_high_time_asof_t_ms"),
        )
    except (TypeError, ValueError) as exc:
        raise AnomalyStateArtifactError(f"invalid {artifact_name} row {row_index}: {exc}") from exc


def _state_row_from_mapping(
    *,
    row: Mapping[str, object],
    row_index: int,
    artifact_name: str,
) -> StrategyState1mRow:
    try:
        return StrategyState1mRow(
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
            structural_low_asof_t=_optional_float(row, "structural_low_asof_t"),
            structural_low_time_asof_t_ms=_optional_int(row, "structural_low_time_asof_t_ms"),
            structural_high_asof_t=_optional_float(row, "structural_high_asof_t"),
            structural_high_time_asof_t_ms=_optional_int(row, "structural_high_time_asof_t_ms"),
        )
    except (TypeError, ValueError) as exc:
        raise AnomalyStateArtifactError(f"invalid {artifact_name} row {row_index}: {exc}") from exc


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


def _tuple_value(row: object, name: str) -> object:
    return getattr(row, name)


def _tuple_required_str(row: object, name: str) -> str:
    value = _tuple_value(row, name)
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _tuple_required_int(row: object, name: str) -> int:
    value = _tuple_value(row, name)
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _tuple_required_float(row: object, name: str) -> float:
    value = _tuple_value(row, name)
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _tuple_optional_float(row: object, name: str) -> float | None:
    value = _tuple_value(row, name)
    if _is_missing(value):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when provided")
    return result


def _tuple_optional_int(row: object, name: str) -> int | None:
    value = _tuple_value(row, name)
    if _is_missing(value):
        return None
    return int(value)


def _tuple_required_bool(row: object, name: str) -> bool:
    value = _tuple_value(row, name)
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


def _csv_value(value: object) -> object:
    return "" if value is None else value
