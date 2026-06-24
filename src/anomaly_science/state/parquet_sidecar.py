from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from anomaly_science.artifacts.manifest import sha256_file


STATE_1M_PARQUET_SIDECAR_VERSION = "state_1m_partitioned_parquet_v1"
STATE_1M_PARQUET_ORDER_COLUMN = "__row_index"


class StateParquetSidecarError(ValueError):
    """Raised when the strategy_state_1m Parquet sidecar is missing or invalid."""


def state_1m_parquet_sidecar_dir(csv_path: str | Path) -> Path:
    """Return the canonical partitioned Parquet sidecar directory for state rows."""
    return Path(csv_path).with_suffix(".parquet")


def state_1m_parquet_manifest_path(csv_path: str | Path) -> Path:
    """Return the strict manifest path for the partitioned state sidecar."""
    path = Path(csv_path)
    return path.with_name(path.stem + ".parquet_manifest.json")


def state_1m_parquet_sidecar_exists(csv_path: str | Path) -> bool:
    path = Path(csv_path)
    manifest_path = state_1m_parquet_manifest_path(path)
    sidecar_dir = state_1m_parquet_sidecar_dir(path)
    manifest_exists = manifest_path.is_file()
    sidecar_exists = sidecar_dir.is_dir()
    if manifest_exists != sidecar_exists:
        missing = sidecar_dir if manifest_exists else manifest_path
        raise StateParquetSidecarError(f"incomplete strategy_state_1m.parquet sidecar, missing {missing}")
    return manifest_exists


def load_and_validate_state_1m_parquet_manifest(
    *,
    csv_path: str | Path,
    expected_columns: Sequence[str],
) -> dict[str, object]:
    path = Path(csv_path)
    manifest_path = state_1m_parquet_manifest_path(path)
    sidecar_dir = state_1m_parquet_sidecar_dir(path)
    if not path.is_file():
        raise StateParquetSidecarError(f"state CSV schema stub is missing: {path}")
    if not manifest_path.is_file() or not sidecar_dir.is_dir():
        raise StateParquetSidecarError(f"strategy_state_1m.parquet sidecar is missing for {path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("sidecar_version") != STATE_1M_PARQUET_SIDECAR_VERSION:
        raise StateParquetSidecarError(
            f"{manifest_path.name} sidecar_version must be {STATE_1M_PARQUET_SIDECAR_VERSION!r}"
        )
    if payload.get("artifact_name") != path.name:
        raise StateParquetSidecarError(f"{manifest_path.name} artifact_name must be {path.name!r}")
    if payload.get("csv_path") != path.name:
        raise StateParquetSidecarError(f"{manifest_path.name} csv_path must be {path.name!r}")
    if payload.get("parquet_path") != sidecar_dir.name:
        raise StateParquetSidecarError(f"{manifest_path.name} parquet_path must be {sidecar_dir.name!r}")
    if int(payload.get("csv_size_bytes", -1)) != path.stat().st_size:
        raise StateParquetSidecarError(f"{manifest_path.name} csv_size_bytes does not match {path.name}")
    if payload.get("csv_sha256") != sha256_file(path):
        raise StateParquetSidecarError(f"{manifest_path.name} csv_sha256 does not match {path.name}")
    if list(payload.get("required_columns") or []) != list(expected_columns):
        raise StateParquetSidecarError(f"{manifest_path.name} required_columns do not match {path.name} schema")
    part_paths = payload.get("part_paths")
    if not isinstance(part_paths, list) or not all(isinstance(item, str) and item for item in part_paths):
        raise StateParquetSidecarError(f"{manifest_path.name} part_paths must be a string list")
    if payload.get("order_column") != STATE_1M_PARQUET_ORDER_COLUMN:
        raise StateParquetSidecarError(
            f"{manifest_path.name} order_column must be {STATE_1M_PARQUET_ORDER_COLUMN!r}"
        )
    return payload


def state_1m_parquet_manifest_row_count(*, csv_path: str | Path, expected_columns: Sequence[str]) -> int:
    payload = load_and_validate_state_1m_parquet_manifest(
        csv_path=csv_path,
        expected_columns=expected_columns,
    )
    try:
        return int(payload["row_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StateParquetSidecarError(
            f"invalid row_count in {state_1m_parquet_manifest_path(csv_path)}"
        ) from exc


def read_state_1m_parquet_sidecar_table(*, csv_path: str | Path, expected_columns: Sequence[str], columns: Sequence[str] | None = None):
    path = Path(csv_path)
    payload = load_and_validate_state_1m_parquet_manifest(csv_path=path, expected_columns=expected_columns)
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise StateParquetSidecarError(
            "pyarrow is required to read strategy_state_1m.parquet; install project dependencies"
        ) from exc

    selected_columns = list(expected_columns if columns is None else columns)
    unknown = [name for name in selected_columns if name not in expected_columns]
    if unknown:
        raise StateParquetSidecarError(f"requested state parquet columns are not in schema: {unknown}")
    part_paths = [path.parent / str(item) for item in payload["part_paths"]]
    if not part_paths:
        return pa.Table.from_pydict({name: [] for name in selected_columns})
    missing_parts = [part_path for part_path in part_paths if not part_path.is_file()]
    if missing_parts:
        raise StateParquetSidecarError(f"strategy_state_1m.parquet manifest points to missing part: {missing_parts[0]}")
    read_columns = [STATE_1M_PARQUET_ORDER_COLUMN, *selected_columns]
    tables = [pq.read_table(part_path, columns=read_columns) for part_path in part_paths]
    table = pa.concat_tables(tables, promote_options="default") if len(tables) > 1 else tables[0]
    if table.num_rows != int(payload["row_count"]):
        raise StateParquetSidecarError(
            f"strategy_state_1m.parquet row count mismatch: manifest={payload['row_count']} actual={table.num_rows}"
        )
    if table.num_rows:
        sort_indices = pc.sort_indices(table, sort_keys=[(STATE_1M_PARQUET_ORDER_COLUMN, "ascending")])
        table = table.take(sort_indices)
    return table.drop([STATE_1M_PARQUET_ORDER_COLUMN])


def iter_state_1m_parquet_sidecar_mappings(
    *,
    csv_path: str | Path,
    expected_columns: Sequence[str],
    columns: Sequence[str] | None = None,
) -> Iterable[dict[str, object]]:
    table = read_state_1m_parquet_sidecar_table(
        csv_path=csv_path,
        expected_columns=expected_columns,
        columns=columns,
    )
    yield from table.to_pylist()
