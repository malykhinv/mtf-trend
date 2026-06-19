from __future__ import annotations

import csv
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from anomaly_science.contracts.artifacts import ArtifactSchema, get_artifact_schema, get_strategy_artifact_companion_names


class ArtifactWriteError(ValueError):
    """Raised when artifact rows do not match the declared schema."""


def _as_mapping(row: Mapping[str, Any] | object) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if is_dataclass(row):
        return asdict(row)
    raise TypeError(f"artifact rows must be mappings or dataclasses, got {type(row).__name__}")


def write_csv_artifact(path: Path, rows: list[Mapping[str, Any] | object], schema: ArtifactSchema) -> Path:
    """Write a schema-checked CSV artifact atomically.

    The writer is intentionally strict: missing required columns fail. Extra columns
    fail too, because MVP schemas are part of the scientific protocol contract.
    """
    if path.name != schema.name:
        raise ArtifactWriteError(f"path name {path.name!r} does not match schema name {schema.name!r}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(schema.required_columns)
    normalized_rows = [_as_mapping(row) for row in rows]

    for index, row in enumerate(normalized_rows):
        missing = [name for name in fieldnames if name not in row]
        extra = sorted(set(row) - set(fieldnames))
        if missing:
            raise ArtifactWriteError(f"row {index} for {schema.name} is missing required columns: {missing}")
        if extra:
            raise ArtifactWriteError(f"row {index} for {schema.name} has undeclared columns: {extra}")

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow({name: row[name] for name in fieldnames})
    os.replace(tmp_path, path)
    return path


def write_csv_artifact_with_aliases(
    path: Path,
    rows: list[Mapping[str, Any] | object],
    schema: ArtifactSchema,
) -> list[Path]:
    written = [write_csv_artifact(path, rows, schema)]
    for alias_name in get_strategy_artifact_companion_names(schema.name):
        written.append(write_csv_artifact(path.with_name(alias_name), rows, get_artifact_schema(alias_name)))
    return written
