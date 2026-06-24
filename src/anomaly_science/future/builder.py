from __future__ import annotations

import csv
import json
import math
from bisect import bisect_left, bisect_right
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
from anomaly_science.state.parquet_sidecar import (
    StateParquetSidecarError,
    iter_state_1m_parquet_sidecar_mappings,
    state_1m_parquet_sidecar_exists,
)
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


@dataclass(slots=True)
class _SymbolFutureCandleIndex:
    candles: tuple[Candle1m, ...]
    available_times: tuple[int, ...]
    true_range_prefix_sums: tuple[float, ...]
    high_sparse: tuple[tuple[float, ...], ...]
    low_sparse: tuple[tuple[float, ...], ...]
    barrier_index: _BarrierRangeIndex | None


@dataclass(frozen=True, slots=True)
class _BarrierRangeIndex:
    size: int
    lows_by_node: tuple[tuple[float, ...], ...]
    highs_by_node: tuple[tuple[float, ...], ...]
    prefix_highs_by_node: tuple[tuple[float, ...], ...]


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


@dataclass(frozen=True, slots=True)
class _FutureStateProjection:
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    running_high_asof_t: float
    current_close: float


_FutureStateLike = StrategyState1mRow | _FutureStateProjection


def load_strategy_state_1m_csv(path: str | Path) -> tuple[StrategyState1mRow, ...]:
    """Read strategy_state_1m through the strict CSV header or partitioned Parquet sidecar.

    The CSV artifact may be a schema-only delivery when the canonical heavy
    state rows live in strategy_state_1m.parquet. Missing or partial sidecars
    fail explicitly; there is no silent fallback to an empty CSV body.
    """
    state_path = Path(path)
    if not state_path.exists():
        raise AnomalyStateArtifactError(f"state artifact is missing: {state_path}")

    schema = get_artifact_schema("strategy_state_1m.csv")
    expected_columns = list(schema.required_columns)
    try:
        if state_1m_parquet_sidecar_exists(state_path):
            rows: list[StrategyState1mRow] = []
            for row_index, row in enumerate(
                iter_state_1m_parquet_sidecar_mappings(csv_path=state_path, expected_columns=expected_columns)
            ):
                rows.append(_state_row_from_mapping(row=row, row_index=row_index, artifact_name=state_path.name))
            return tuple(rows)
    except StateParquetSidecarError as exc:
        raise AnomalyStateArtifactError(str(exc)) from exc

    frame = _read_artifact_csv(state_path)
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
    try:
        if state_1m_parquet_sidecar_exists(state_path):
            for row_index, row in enumerate(
                iter_state_1m_parquet_sidecar_mappings(csv_path=state_path, expected_columns=expected_columns)
            ):
                yield _state_row_from_mapping(row=row, row_index=row_index, artifact_name=state_path.name)
            return
    except StateParquetSidecarError as exc:
        raise AnomalyStateArtifactError(str(exc)) from exc

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


def _iter_future_state_projection_csv(path: str | Path) -> Iterable[_FutureStateProjection]:
    state_path = Path(path)
    if not state_path.exists():
        raise AnomalyStateArtifactError(f"state artifact is missing: {state_path}")

    schema = get_artifact_schema("strategy_state_1m.csv")
    expected_columns = list(schema.required_columns)
    try:
        if state_1m_parquet_sidecar_exists(state_path):
            projection_columns = [
                "event_id",
                "symbol",
                "state_time_ms",
                "snapshot_time_ms",
                "feature_cutoff_time_ms",
                "running_high_asof_t",
                "current_close",
            ]
            for row_index, row in enumerate(
                iter_state_1m_parquet_sidecar_mappings(
                    csv_path=state_path,
                    expected_columns=expected_columns,
                    columns=projection_columns,
                )
            ):
                yield _future_state_projection_from_mapping(
                    row=row,
                    row_index=row_index,
                    artifact_name=state_path.name,
                )
            return
    except StateParquetSidecarError as exc:
        raise AnomalyStateArtifactError(str(exc)) from exc

    with state_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = list(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise AnomalyStateArtifactError(
                f"state artifact columns must match {expected_columns}, got {actual_columns}"
            )
        for row_index, row in enumerate(reader):
            yield _future_state_projection_from_mapping(
                row=row,
                row_index=row_index,
                artifact_name=state_path.name,
            )


def iter_candles_1m_csv(
    path: str | Path,
    *,
    max_open_time_ms: int | None = None,
    symbol: str | None = None,
) -> Iterable[Candle1m]:
    """Stream normalized 1m candles from the explicit CSV boundary.

    Uses positional ``csv.reader`` access instead of ``DictReader`` to avoid one
    dict allocation and per-field dict lookups for every candle; this is the
    shared hot path for the state/future/feature-matrix/simulation candle scans.
    When ``symbol`` is given, non-matching rows are skipped before the typed
    candle is constructed so single-symbol context scans do not pay full
    validation for the whole market.
    """
    candles_path = Path(path)
    if not candles_path.exists():
        raise CsvDataSourceError(f"required dataset 'candles_1m.csv' is missing: {candles_path}")
    with candles_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.reader(file_obj)
        header = next(reader, None)
        actual_columns = tuple(header or ())
        missing = [name for name in CANDLE_REQUIRED_COLUMNS if name not in actual_columns]
        if missing:
            raise CsvDataSourceError(f"dataset 'candles_1m.csv' is missing required columns: {missing}")
        index_of = {name: position for position, name in enumerate(actual_columns)}
        i_symbol = index_of["symbol"]
        i_open_time = index_of["open_time_ms"]
        i_available = index_of["available_time_ms"]
        i_open = index_of["open"]
        i_high = index_of["high"]
        i_low = index_of["low"]
        i_close = index_of["close"]
        i_volume = index_of["volume"]
        i_quote = index_of["quote_volume"]
        i_trades = index_of.get("number_of_trades")
        i_taker = index_of.get("taker_buy_quote_volume")
        for row_index, values in enumerate(reader):
            if symbol is not None and values[i_symbol] != symbol:
                continue
            try:
                open_time_ms = _required_cell_int(values, i_open_time, "open_time_ms")
                if max_open_time_ms is not None and open_time_ms >= max_open_time_ms:
                    continue
                yield Candle1m(
                    symbol=_required_cell_str(values, i_symbol, "symbol"),
                    open_time_ms=open_time_ms,
                    available_time_ms=_required_cell_int(values, i_available, "available_time_ms"),
                    open=_required_cell_float(values, i_open, "open"),
                    high=_required_cell_float(values, i_high, "high"),
                    low=_required_cell_float(values, i_low, "low"),
                    close=_required_cell_float(values, i_close, "close"),
                    volume=_required_cell_float(values, i_volume, "volume"),
                    quote_volume=_required_cell_float(values, i_quote, "quote_volume"),
                    number_of_trades=_optional_cell_float(values, i_trades) if i_trades is not None else None,
                    taker_buy_quote_volume=(
                        _optional_cell_float(values, i_taker) if i_taker is not None else None
                    ),
                )
            except (TypeError, ValueError, IndexError) as exc:
                raise CsvDataSourceError(f"invalid candles_1m.csv row {row_index}: {exc}") from exc


def _required_cell_str(values: Sequence[str], index: int, name: str) -> str:
    value = values[index]
    if value == "":
        raise ValueError(f"{name} is required")
    return value


def _required_cell_int(values: Sequence[str], index: int, name: str) -> int:
    value = values[index]
    if value == "":
        raise ValueError(f"{name} is required")
    return int(value)


def _required_cell_float(values: Sequence[str], index: int, name: str) -> float:
    value = values[index]
    if value == "":
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_cell_float(values: Sequence[str], index: int) -> float | None:
    value = values[index]
    if value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("value must be finite when present")
    return result



FUTURE_PATHS_PARQUET_SIDECAR_VERSION = "future_paths_partitioned_parquet_v1"
FUTURE_PATHS_PARQUET_ORDER_COLUMN = "__row_index"
FUTURE_PATHS_SIDECAR_PYLIST_BATCH_ROWS = 50_000


def future_paths_parquet_sidecar_dir(csv_path: str | Path) -> Path:
    """Return the canonical partitioned Parquet sidecar directory for future paths."""
    return Path(csv_path).with_suffix(".parquet")


def future_paths_parquet_manifest_path(csv_path: str | Path) -> Path:
    """Return the strict manifest path for the partitioned future-path sidecar."""
    path = Path(csv_path)
    return path.with_name(path.stem + ".parquet_manifest.json")


def _future_paths_parquet_sidecar_exists(csv_path: Path) -> bool:
    manifest_path = future_paths_parquet_manifest_path(csv_path)
    sidecar_dir = future_paths_parquet_sidecar_dir(csv_path)
    manifest_exists = manifest_path.is_file()
    sidecar_exists = sidecar_dir.is_dir()
    if manifest_exists != sidecar_exists:
        missing = sidecar_dir if manifest_exists else manifest_path
        raise AnomalyFutureArtifactError(f"incomplete strategy_future_paths.parquet sidecar, missing {missing}")
    return manifest_exists


def read_future_paths_parquet_sidecar_table(*, csv_path: Path, expected_columns: Sequence[str]):
    """Public strict reader: ordered Arrow table of future paths from the sidecar."""
    return _read_future_paths_parquet_sidecar_table(csv_path=csv_path, expected_columns=expected_columns)


def _read_future_paths_parquet_sidecar_table(*, csv_path: Path, expected_columns: Sequence[str]):
    payload = _load_and_validate_future_paths_parquet_manifest(
        csv_path=csv_path,
        expected_columns=expected_columns,
    )
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise AnomalyFutureArtifactError(
            "pyarrow is required to read strategy_future_paths.parquet; install project dependencies"
        ) from exc

    part_paths = [csv_path.parent / str(item) for item in payload["part_paths"]]
    if not part_paths:
        return pa.Table.from_pydict({name: [] for name in expected_columns})
    missing_parts = [path for path in part_paths if not path.is_file()]
    if missing_parts:
        raise AnomalyFutureArtifactError(f"strategy_future_paths.parquet manifest points to missing part: {missing_parts[0]}")
    columns = [FUTURE_PATHS_PARQUET_ORDER_COLUMN, *expected_columns]
    tables = [pq.read_table(path, columns=columns) for path in part_paths]
    table = pa.concat_tables(tables, promote_options="default") if len(tables) > 1 else tables[0]
    if table.num_rows != int(payload["row_count"]):
        raise AnomalyFutureArtifactError(
            f"strategy_future_paths.parquet row count mismatch: manifest={payload['row_count']} actual={table.num_rows}"
        )
    if table.num_rows:
        sort_indices = pc.sort_indices(table, sort_keys=[(FUTURE_PATHS_PARQUET_ORDER_COLUMN, "ascending")])
        table = table.take(sort_indices)
    return table.drop([FUTURE_PATHS_PARQUET_ORDER_COLUMN])


def _load_and_validate_future_paths_parquet_manifest(*, csv_path: Path, expected_columns: Sequence[str]) -> dict[str, object]:
    manifest_path = future_paths_parquet_manifest_path(csv_path)
    sidecar_dir = future_paths_parquet_sidecar_dir(csv_path)
    if not csv_path.is_file():
        raise AnomalyFutureArtifactError(f"future CSV schema stub is missing: {csv_path}")
    if not manifest_path.is_file() or not sidecar_dir.is_dir():
        raise AnomalyFutureArtifactError(f"strategy_future_paths.parquet sidecar is missing for {csv_path}")
    try:
        from anomaly_science.artifacts.manifest import sha256_file
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise AnomalyFutureArtifactError("artifact hashing helper is unavailable") from exc
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("sidecar_version") != FUTURE_PATHS_PARQUET_SIDECAR_VERSION:
        raise AnomalyFutureArtifactError(
            f"{manifest_path.name} sidecar_version must be {FUTURE_PATHS_PARQUET_SIDECAR_VERSION!r}"
        )
    if payload.get("artifact_name") != csv_path.name:
        raise AnomalyFutureArtifactError(f"{manifest_path.name} artifact_name must be {csv_path.name!r}")
    if payload.get("parquet_path") != sidecar_dir.name:
        raise AnomalyFutureArtifactError(f"{manifest_path.name} parquet_path must be {sidecar_dir.name!r}")
    if payload.get("csv_path") != csv_path.name:
        raise AnomalyFutureArtifactError(f"{manifest_path.name} csv_path must be {csv_path.name!r}")
    if int(payload.get("csv_size_bytes", -1)) != csv_path.stat().st_size:
        raise AnomalyFutureArtifactError(f"{manifest_path.name} csv_size_bytes does not match {csv_path.name}")
    if payload.get("csv_sha256") != sha256_file(csv_path):
        raise AnomalyFutureArtifactError(f"{manifest_path.name} csv_sha256 does not match {csv_path.name}")
    required_columns = payload.get("required_columns")
    if list(required_columns or []) != list(expected_columns):
        raise AnomalyFutureArtifactError(f"{manifest_path.name} required_columns do not match {csv_path.name} schema")
    part_paths = payload.get("part_paths")
    if not isinstance(part_paths, list) or not all(isinstance(item, str) and item for item in part_paths):
        raise AnomalyFutureArtifactError(f"{manifest_path.name} part_paths must be a string list")
    if payload.get("order_column") != FUTURE_PATHS_PARQUET_ORDER_COLUMN:
        raise AnomalyFutureArtifactError(f"{manifest_path.name} order_column must be {FUTURE_PATHS_PARQUET_ORDER_COLUMN!r}")
    return payload

def load_strategy_future_paths_csv(path: str | Path) -> tuple[FuturePathRow, ...]:
    """Read strategy_future_paths through the strict CSV header or partitioned Parquet sidecar."""
    future_path = Path(path)
    if not future_path.exists():
        raise AnomalyFutureArtifactError(f"future artifact is missing: {future_path}")

    schema = get_artifact_schema("strategy_future_paths.csv")
    expected_columns = list(schema.required_columns)
    if _future_paths_parquet_sidecar_exists(future_path):
        table = _read_future_paths_parquet_sidecar_table(
            csv_path=future_path,
            expected_columns=expected_columns,
        )
        rows: list[FuturePathRow] = []
        for row_index, row in enumerate(table.to_pylist()):
            rows.append(_future_path_row_from_mapping(row=row, row_index=row_index, artifact_name=future_path.name))
        return tuple(rows)

    frame = _read_artifact_csv(future_path)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise AnomalyFutureArtifactError(
            f"future artifact columns must match {expected_columns}, got {actual_columns}"
        )

    rows = []
    for row_index, row_tuple in enumerate(frame.itertuples(index=False)):
        row = row_tuple._asdict()
        rows.append(_future_path_row_from_mapping(row=row, row_index=row_index, artifact_name=future_path.name))
    return tuple(rows)


load_anomaly_future_paths_csv = load_strategy_future_paths_csv


def iter_strategy_future_paths_artifact_csv(path: str | Path) -> Iterable[FuturePathRow]:
    """Stream future path rows through the strict artifact boundary.

    When the canonical partitioned Parquet sidecar is present, the CSV is treated
    as a schema stub and data rows are read from Parquet. Missing or partial
    sidecars fail explicitly; there is no silent fallback to a stale CSV body.
    """
    future_path = Path(path)
    if not future_path.exists():
        raise AnomalyFutureArtifactError(f"future artifact is missing: {future_path}")

    schema = get_artifact_schema("strategy_future_paths.csv")
    expected_columns = list(schema.required_columns)
    if _future_paths_parquet_sidecar_exists(future_path):
        table = _read_future_paths_parquet_sidecar_table(
            csv_path=future_path,
            expected_columns=expected_columns,
        )
        # Materialize dicts one Arrow batch at a time so this streaming reader does
        # not hold every future row as a dict at once for large universes.
        row_index = 0
        for batch in table.to_batches(max_chunksize=FUTURE_PATHS_SIDECAR_PYLIST_BATCH_ROWS):
            for row in batch.to_pylist():
                yield _future_path_row_from_mapping(row=row, row_index=row_index, artifact_name=future_path.name)
                row_index += 1
        return

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
    max_input_time_ms: int | None = None,
) -> Iterable[FuturePathRow]:
    input_path = Path(input_dir)
    return iter_strategy_future_paths_from_grouped_csv(
        candles_path=input_path / "candles_1m.csv",
        state_path=state_path,
        config=config,
        max_input_time_ms=max_input_time_ms,
    )


iter_anomaly_future_paths_from_csv = iter_strategy_future_paths_from_csv


def iter_strategy_future_paths_from_grouped_csv(
    *,
    candles_path: str | Path,
    state_path: str | Path,
    config: FuturePathBuilderConfig | None = None,
    max_input_time_ms: int | None = None,
) -> Iterable[FuturePathRow]:
    """Stream future rows while holding one symbol's candles and states in memory."""
    cfg = config or FuturePathBuilderConfig()
    max_horizon = max(cfg.future_return_horizons_minutes)
    empty_index = _empty_symbol_future_candle_index()
    state_groups = _symbol_groups(
        _iter_future_state_projection_csv(state_path),
        symbol_getter=lambda row: row.symbol,
        source_name="strategy_state_1m.csv",
    )
    try:
        current_state_symbol, current_states = next(state_groups)
    except StopIteration:
        return

    for candle_symbol, symbol_candles in _symbol_groups(
        iter_candles_1m_csv(candles_path, max_open_time_ms=max_input_time_ms),
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


def iter_strategy_future_path_csv_value_rows_from_grouped_csv(
    *,
    candles_path: str | Path,
    state_path: str | Path,
    fieldnames: Sequence[str],
    config: FuturePathBuilderConfig | None = None,
    max_input_time_ms: int | None = None,
) -> Iterable[list[object]]:
    """Stream canonical future-path CSV values without allocating FuturePathRow objects."""
    validate_future_row_fieldnames(fieldnames)
    cfg = config or FuturePathBuilderConfig()
    max_horizon = max(cfg.future_return_horizons_minutes)
    empty_index = _empty_symbol_future_candle_index()
    # The future builder only needs six state fields; read the lightweight
    # projection (six sidecar columns, no full StrategyState1mRow validation)
    # instead of materializing every state column per row.
    state_groups = _symbol_groups(
        _iter_future_state_projection_csv(state_path),
        symbol_getter=lambda row: row.symbol,
        source_name="strategy_state_1m.csv",
    )
    try:
        current_state_symbol, current_states = next(state_groups)
    except StopIteration:
        return

    for candle_symbol, symbol_candles in _symbol_groups(
        iter_candles_1m_csv(candles_path, max_open_time_ms=max_input_time_ms),
        symbol_getter=lambda row: row.symbol,
        source_name="candles_1m.csv",
    ):
        while current_state_symbol < candle_symbol:
            yield from _iter_state_group_future_csv_value_rows(
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
        yield from _iter_state_group_future_csv_value_rows(
            states=current_states,
            candle_index=_build_symbol_future_candle_index(symbol_candles),
            max_horizon=max_horizon,
            config=cfg,
        )
        try:
            current_state_symbol, current_states = next(state_groups)
        except StopIteration:
            return

    yield from _iter_state_group_future_csv_value_rows(
        states=current_states,
        candle_index=empty_index,
        max_horizon=max_horizon,
        config=cfg,
    )
    for _, remaining_states in state_groups:
        yield from _iter_state_group_future_csv_value_rows(
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


def _iter_state_group_future_csv_value_rows(
    *,
    states: Sequence[_FutureStateLike],
    candle_index: _SymbolFutureCandleIndex,
    max_horizon: int,
    config: FuturePathBuilderConfig,
) -> Iterable[list[object]]:
    for state in states:
        yield _build_state_future_path_csv_values(
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
        candle_index=candle_index,
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


def _build_state_future_path_csv_values(
    *,
    state: _FutureStateLike,
    candle_index: _SymbolFutureCandleIndex,
    max_horizon: int,
    atr_window_minutes: int,
    double_barrier_k_continuation: float,
    double_barrier_k_fade: float,
) -> list[object]:
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
        candle_index=candle_index,
        start_index=future_start_index,
        end_index=future_end_index,
        horizons=(5, 15, 30, 60, 120, 180),
        atr_value=atr_value,
        k_continuation=double_barrier_k_continuation,
        k_fade=double_barrier_k_fade,
    )
    return _future_csv_values(
        state=state,
        future_start_time_ms=future_start_time_ms,
        atr_window_minutes=atr_window_minutes,
        atr_result=atr_result,
        atr_value=atr_value,
        double_barrier_k_continuation=double_barrier_k_continuation,
        double_barrier_k_fade=double_barrier_k_fade,
        metrics=metrics,
    )


def _future_csv_values(
    *,
    state: _FutureStateLike,
    future_start_time_ms: int,
    atr_window_minutes: int,
    atr_result: AtrAsOfResult | None,
    atr_value: float | None,
    double_barrier_k_continuation: float,
    double_barrier_k_fade: float,
    metrics: _FutureMetrics,
) -> list[object]:
    return [
        state.event_id,
        state.symbol,
        state.snapshot_time_ms,
        state.feature_cutoff_time_ms,
        future_start_time_ms,
        atr_window_minutes,
        _csv_value(atr_value),
        _csv_value(atr_result.atr_1d_pct_asof_t if atr_result is not None else None),
        _csv_value(double_barrier_k_continuation if atr_value is not None else None),
        _csv_value(double_barrier_k_fade if atr_value is not None else None),
        _csv_value(metrics.future_return[0]),
        _csv_value(metrics.future_return[1]),
        _csv_value(metrics.future_return[2]),
        _csv_value(metrics.future_return[3]),
        _csv_value(metrics.future_return[4]),
        _csv_value(metrics.future_return[5]),
        _csv_value(metrics.future_max[0]),
        _csv_value(metrics.future_max[1]),
        _csv_value(metrics.future_max[2]),
        _csv_value(metrics.future_max[3]),
        _csv_value(metrics.future_max[4]),
        _csv_value(metrics.future_max[5]),
        _csv_value(metrics.future_min[0]),
        _csv_value(metrics.future_min[1]),
        _csv_value(metrics.future_min[2]),
        _csv_value(metrics.future_min[3]),
        _csv_value(metrics.future_min[4]),
        _csv_value(metrics.future_min[5]),
        _csv_value(metrics.future_return_atr[0]),
        _csv_value(metrics.future_return_atr[1]),
        _csv_value(metrics.future_return_atr[2]),
        _csv_value(metrics.future_return_atr[3]),
        _csv_value(metrics.future_return_atr[4]),
        _csv_value(metrics.future_return_atr[5]),
        _csv_value(metrics.future_max_atr[0]),
        _csv_value(metrics.future_max_atr[1]),
        _csv_value(metrics.future_max_atr[2]),
        _csv_value(metrics.future_max_atr[3]),
        _csv_value(metrics.future_max_atr[4]),
        _csv_value(metrics.future_max_atr[5]),
        _csv_value(metrics.future_min_atr[0]),
        _csv_value(metrics.future_min_atr[1]),
        _csv_value(metrics.future_min_atr[2]),
        _csv_value(metrics.future_min_atr[3]),
        _csv_value(metrics.future_min_atr[4]),
        _csv_value(metrics.future_min_atr[5]),
        _csv_value(metrics.barrier_hit[0]),
        _csv_value(metrics.barrier_hit[1]),
        _csv_value(metrics.barrier_hit[2]),
        _csv_value(metrics.barrier_hit[3]),
        _csv_value(metrics.barrier_hit[4]),
        _csv_value(metrics.barrier_hit[5]),
        _csv_value(_barrier_resolution(metrics.barrier_hit[0])),
        _csv_value(_barrier_resolution(metrics.barrier_hit[1])),
        _csv_value(_barrier_resolution(metrics.barrier_hit[2])),
        _csv_value(_barrier_resolution(metrics.barrier_hit[3])),
        _csv_value(_barrier_resolution(metrics.barrier_hit[4])),
        _csv_value(_barrier_resolution(metrics.barrier_hit[5])),
        _csv_value(metrics.reclaimed_running_high[2]),
        _csv_value(metrics.reclaimed_running_high[3]),
        "",
        "",
        _csv_value(metrics.time_to_new_high_minutes),
        "",
    ]


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
        high_sparse=_build_max_sparse_table(tuple(candle.high for candle in ordered)),
        low_sparse=_build_min_sparse_table(tuple(candle.low for candle in ordered)),
        barrier_index=None,
    )


def _empty_symbol_future_candle_index() -> _SymbolFutureCandleIndex:
    return _SymbolFutureCandleIndex(
        candles=(),
        available_times=(),
        true_range_prefix_sums=(),
        high_sparse=(),
        low_sparse=(),
        barrier_index=None,
    )


def _build_max_sparse_table(values: Sequence[float]) -> tuple[tuple[float, ...], ...]:
    if not values:
        return ()
    levels: list[tuple[float, ...]] = [tuple(values)]
    span = 2
    while span <= len(values):
        previous = levels[-1]
        half = span // 2
        current: list[float] = []
        for index in range(len(values) - span + 1):
            left = previous[index]
            right = previous[index + half]
            current.append(left if left >= right else right)
        levels.append(tuple(current))
        span *= 2
    return tuple(levels)


def _build_min_sparse_table(values: Sequence[float]) -> tuple[tuple[float, ...], ...]:
    if not values:
        return ()
    levels: list[tuple[float, ...]] = [tuple(values)]
    span = 2
    while span <= len(values):
        previous = levels[-1]
        half = span // 2
        current: list[float] = []
        for index in range(len(values) - span + 1):
            left = previous[index]
            right = previous[index + half]
            current.append(left if left <= right else right)
        levels.append(tuple(current))
        span *= 2
    return tuple(levels)


def _build_barrier_range_index(candles: Sequence[Candle1m]) -> _BarrierRangeIndex:
    if not candles:
        return _BarrierRangeIndex(size=0, lows_by_node=(), highs_by_node=(), prefix_highs_by_node=())
    size = 1
    while size < len(candles):
        size *= 2
    lows_by_node: list[tuple[float, ...]] = [()] * (2 * size)
    highs_by_node: list[tuple[float, ...]] = [()] * (2 * size)
    prefix_highs_by_node: list[tuple[float, ...]] = [()] * (2 * size)
    for index, candle in enumerate(candles):
        node = size + index
        lows_by_node[node] = (candle.low,)
        highs_by_node[node] = (candle.high,)
        prefix_highs_by_node[node] = (candle.high,)
    for node in range(size - 1, 0, -1):
        lows, highs = _merge_barrier_children(
            left_lows=lows_by_node[node * 2],
            left_highs=highs_by_node[node * 2],
            right_lows=lows_by_node[node * 2 + 1],
            right_highs=highs_by_node[node * 2 + 1],
        )
        if not lows:
            continue
        prefix: list[float] = []
        running = -math.inf
        for high in highs:
            running = max(running, high)
            prefix.append(running)
        lows_by_node[node] = lows
        highs_by_node[node] = highs
        prefix_highs_by_node[node] = tuple(prefix)
    return _BarrierRangeIndex(
        size=size,
        lows_by_node=tuple(lows_by_node),
        highs_by_node=tuple(highs_by_node),
        prefix_highs_by_node=tuple(prefix_highs_by_node),
    )


def _get_barrier_range_index(candle_index: _SymbolFutureCandleIndex) -> _BarrierRangeIndex:
    if candle_index.barrier_index is None:
        candle_index.barrier_index = _build_barrier_range_index(candle_index.candles)
    return candle_index.barrier_index


def _merge_barrier_children(
    *,
    left_lows: tuple[float, ...],
    left_highs: tuple[float, ...],
    right_lows: tuple[float, ...],
    right_highs: tuple[float, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if not left_lows:
        return right_lows, right_highs
    if not right_lows:
        return left_lows, left_highs
    lows: list[float] = []
    highs: list[float] = []
    left_index = 0
    right_index = 0
    while left_index < len(left_lows) and right_index < len(right_lows):
        if left_lows[left_index] <= right_lows[right_index]:
            lows.append(left_lows[left_index])
            highs.append(left_highs[left_index])
            left_index += 1
        else:
            lows.append(right_lows[right_index])
            highs.append(right_highs[right_index])
            right_index += 1
    lows.extend(left_lows[left_index:])
    highs.extend(left_highs[left_index:])
    lows.extend(right_lows[right_index:])
    highs.extend(right_highs[right_index:])
    return tuple(lows), tuple(highs)


def _range_high(candle_index: _SymbolFutureCandleIndex, start_index: int, end_index: int) -> float | None:
    if start_index >= end_index:
        return None
    length = end_index - start_index
    level_index = length.bit_length() - 1
    span = 1 << level_index
    level = candle_index.high_sparse[level_index]
    left = level[start_index]
    right = level[end_index - span]
    return left if left >= right else right


def _range_low(candle_index: _SymbolFutureCandleIndex, start_index: int, end_index: int) -> float | None:
    if start_index >= end_index:
        return None
    length = end_index - start_index
    level_index = length.bit_length() - 1
    span = 1 << level_index
    level = candle_index.low_sparse[level_index]
    left = level[start_index]
    right = level[end_index - span]
    return left if left <= right else right


def _first_high_greater(
    candle_index: _SymbolFutureCandleIndex,
    start_index: int,
    end_index: int,
    threshold: float,
) -> int | None:
    if start_index >= end_index:
        return None
    max_high = _range_high(candle_index, start_index, end_index)
    if max_high is None or max_high <= threshold:
        return None
    left = start_index
    right = end_index
    while left + 1 < right:
        middle = (left + right) // 2
        left_max = _range_high(candle_index, start_index, middle)
        if left_max is not None and left_max > threshold:
            right = middle
        else:
            left = middle
    return left if candle_index.candles[left].high > threshold else right


def _has_double_barrier_hit(
    barrier_index: _BarrierRangeIndex,
    start_index: int,
    end_index: int,
    *,
    upper_barrier: float,
    lower_barrier: float,
) -> bool:
    if barrier_index.size == 0 or start_index >= end_index:
        return False
    left = start_index + barrier_index.size
    right = end_index + barrier_index.size
    while left < right:
        if left % 2 == 1:
            if _barrier_node_has_hit(barrier_index, left, upper_barrier=upper_barrier, lower_barrier=lower_barrier):
                return True
            left += 1
        if right % 2 == 1:
            right -= 1
            if _barrier_node_has_hit(barrier_index, right, upper_barrier=upper_barrier, lower_barrier=lower_barrier):
                return True
        left //= 2
        right //= 2
    return False


def _barrier_node_has_hit(
    barrier_index: _BarrierRangeIndex,
    node: int,
    *,
    upper_barrier: float,
    lower_barrier: float,
) -> bool:
    lows = barrier_index.lows_by_node[node]
    if not lows:
        return False
    eligible_count = bisect_right(lows, lower_barrier)
    if eligible_count == 0:
        return False
    return barrier_index.prefix_highs_by_node[node][eligible_count - 1] >= upper_barrier


def _future_metrics(
    *,
    state: _FutureStateLike,
    candle_index: _SymbolFutureCandleIndex,
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

    time_to_new_high_minutes: int | None = None
    upper_barrier = state.current_close + k_continuation * atr_value if atr_value is not None else None
    lower_barrier = state.current_close - k_fade * atr_value if atr_value is not None else None
    limit = len(candle_index.candles) if end_index is None else end_index
    first_new_high_index = _first_high_greater(candle_index, start_index, limit, state.running_high_asof_t)
    if first_new_high_index is not None:
        time_to_new_high_minutes = _minutes_between(
            state.snapshot_time_ms,
            candle_index.candles[first_new_high_index].available_time_ms,
        )

    for horizon_index, horizon in enumerate(sorted_horizons):
        horizon_end_ms = state.snapshot_time_ms + horizon * ONE_MINUTE_MS
        horizon_end_index = bisect_right(candle_index.available_times, horizon_end_ms, lo=start_index, hi=limit)
        had_window = start_index < horizon_end_index
        exact_index = bisect_left(candle_index.available_times, horizon_end_ms, lo=start_index, hi=horizon_end_index)
        if exact_index < horizon_end_index and candle_index.available_times[exact_index] == horizon_end_ms:
            close = candle_index.candles[exact_index].close
            future_return[horizon_index] = (close / state.current_close) - 1.0
            if atr_value is not None:
                future_return_atr[horizon_index] = (close - state.current_close) / atr_value

        running_high = _range_high(candle_index, start_index, horizon_end_index)
        running_low = _range_low(candle_index, start_index, horizon_end_index)
        barrier_possible = False
        if running_high is not None and running_low is not None:
            future_max[horizon_index] = (running_high / state.current_close) - 1.0
            future_min[horizon_index] = (running_low / state.current_close) - 1.0
            reclaimed_running_high[horizon_index] = running_high > state.running_high_asof_t
            if atr_value is not None:
                future_max_atr[horizon_index] = (running_high - state.current_close) / atr_value
                future_min_atr[horizon_index] = (running_low - state.current_close) / atr_value
                if upper_barrier is not None and lower_barrier is not None:
                    barrier_possible = running_high >= upper_barrier and running_low <= lower_barrier
        if had_window and atr_value is not None and upper_barrier is not None and lower_barrier is not None:
            barrier_hit[horizon_index] = (
                _has_double_barrier_hit(
                    _get_barrier_range_index(candle_index),
                    start_index,
                    horizon_end_index,
                    upper_barrier=upper_barrier,
                    lower_barrier=lower_barrier,
                )
                if barrier_possible
                else False
            )

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


def _future_state_projection_from_mapping(
    *,
    row: Mapping[str, object],
    row_index: int,
    artifact_name: str,
) -> _FutureStateProjection:
    try:
        state_time_ms = _required_csv_int(row, "state_time_ms")
        snapshot_time_ms = _required_csv_int(row, "snapshot_time_ms")
        feature_cutoff_time_ms = _required_csv_int(row, "feature_cutoff_time_ms")
        if state_time_ms != snapshot_time_ms:
            raise ValueError("state_time_ms must equal snapshot_time_ms for MVP online 1m state rows")
        if feature_cutoff_time_ms > snapshot_time_ms:
            raise ValueError("feature_cutoff_time_ms must be <= snapshot_time_ms")
        running_high = _required_csv_float(row, "running_high_asof_t")
        current_close = _required_csv_float(row, "current_close")
        if running_high <= 0 or current_close <= 0:
            raise ValueError("running_high_asof_t and current_close must be positive")
        return _FutureStateProjection(
            event_id=_required_csv_str(row, "event_id"),
            symbol=_required_csv_str(row, "symbol"),
            snapshot_time_ms=snapshot_time_ms,
            feature_cutoff_time_ms=feature_cutoff_time_ms,
            running_high_asof_t=running_high,
            current_close=current_close,
        )
    except (TypeError, ValueError) as exc:
        raise AnomalyStateArtifactError(f"invalid {artifact_name} row {row_index}: {exc}") from exc


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_csv_str(row: Mapping[str, str], name: str) -> str:
    value = row[name]
    if value == "":
        raise ValueError(f"{name} is required")
    return value


def _required_csv_int(row: Mapping[str, str], name: str) -> int:
    value = row[name]
    if value == "":
        raise ValueError(f"{name} is required")
    return int(value)


def _required_csv_float(row: Mapping[str, str], name: str) -> float:
    value = row[name]
    if value == "":
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_csv_float(row: Mapping[str, str], name: str) -> float | None:
    value = row[name]
    if value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when present")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if _is_missing(value):
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
    # Hot per-row check across CSV (str), Parquet to_pylist (None) and pandas
    # itertuples (float NaN) sources. Avoid pandas.isna call overhead.
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _tuple_value(row: object, name: str) -> object:
    return getattr(row, name)


def _tuple_required_str(row: object, name: str) -> str:
    value = _tuple_value(row, name)
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _tuple_required_int(row: object, name: str) -> int:
    value = _tuple_value(row, name)
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _tuple_required_float(row: object, name: str) -> float:
    value = _tuple_value(row, name)
    if _is_missing(value):
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
