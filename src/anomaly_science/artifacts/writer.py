from __future__ import annotations

import csv
import os
import shutil
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from anomaly_science.contracts.artifacts import (
    ArtifactSchema,
    get_artifact_schema,
    get_strategy_artifact_companion_names,
    should_materialize_strategy_artifact_alias,
)


class ArtifactWriteError(ValueError):
    """Raised when artifact rows do not match the declared schema."""


def _as_mapping(row: Mapping[str, Any] | object) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if is_dataclass(row):
        # Artifact rows are flat (scalar fields plus immutable containers), so a
        # shallow field read produces the same CSV cells as dataclasses.asdict
        # without its recursive per-field deepcopy, which dominates row writing.
        return {field.name: getattr(row, field.name) for field in fields(row)}
    raise TypeError(f"artifact rows must be mappings or dataclasses, got {type(row).__name__}")


def write_csv_artifact(path: Path, rows: Iterable[Mapping[str, Any] | object], schema: ArtifactSchema) -> Path:
    """Write a schema-checked CSV artifact atomically.

    The writer is intentionally strict: missing required columns fail. Extra columns
    fail too, because MVP schemas are part of the scientific protocol contract.
    """
    if path.name != schema.name:
        raise ArtifactWriteError(f"path name {path.name!r} does not match schema name {schema.name!r}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(schema.required_columns)
    expected_columns = set(fieldnames)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for index, raw_row in enumerate(rows):
            row = _as_mapping(raw_row)
            missing = [name for name in fieldnames if name not in row]
            extra = sorted(set(row) - expected_columns)
            if missing:
                raise ArtifactWriteError(f"row {index} for {schema.name} is missing required columns: {missing}")
            if extra:
                raise ArtifactWriteError(f"row {index} for {schema.name} has undeclared columns: {extra}")
            writer.writerow({name: row[name] for name in fieldnames})
    os.replace(tmp_path, path)
    return path


def write_csv_artifact_with_aliases(
    path: Path,
    rows: Iterable[Mapping[str, Any] | object],
    schema: ArtifactSchema,
) -> list[Path]:
    alias_schemas = [
        (alias_name, get_artifact_schema(alias_name))
        for alias_name in get_strategy_artifact_companion_names(schema.name)
    ]
    if alias_schemas and any(tuple(alias_schema.required_columns) != tuple(schema.required_columns) for _, alias_schema in alias_schemas):
        rows = tuple(rows)
    written = [write_csv_artifact(path, rows, schema)]
    for alias_name, alias_schema in alias_schemas:
        if not should_materialize_strategy_artifact_alias(schema.name, alias_name):
            continue
        alias_path = path.with_name(alias_name)
        if tuple(alias_schema.required_columns) == tuple(schema.required_columns):
            link_or_copy_identical_artifact(path, alias_path)
            written.append(alias_path)
        else:
            written.append(write_csv_artifact(alias_path, rows, alias_schema))
    return written


def link_or_copy_identical_artifact(source_path: Path, alias_path: Path) -> Path:
    """Create a same-schema compatibility alias without duplicating bytes when possible."""
    if alias_path.exists():
        alias_path.unlink()
    try:
        os.link(source_path, alias_path)
    except OSError:
        shutil.copyfile(source_path, alias_path)
    return alias_path
