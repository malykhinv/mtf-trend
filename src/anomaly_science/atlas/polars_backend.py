from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from anomaly_science.atlas.builder import (
    AtlasArtifacts,
    AtlasInputError,
    _add_atlas_context_columns,
    _atlas_feature_usecols,
    _atlas_future_usecols,
    _atlas_state_usecols,
    _build_atlas_artifacts_from_frame,
    _enforce_atlas_frame_temporal_contract,
)
from anomaly_science.atlas.config import AtlasConfig
from anomaly_science.contracts.artifacts import get_artifact_schema

_JOIN_COLUMNS = ("event_id", "symbol", "snapshot_time_ms", "feature_cutoff_time_ms")


def _import_polars_for_atlas_backend():
    try:
        import polars as pl
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise AtlasInputError(
            "polars is required for the explicit atlas Polars backend; "
            "install project dependencies instead of falling back to the legacy backend"
        ) from exc
    return pl


def build_atlas_artifacts_polars_from_csv_paths(
    *,
    state_path: str | Path,
    future_path: str | Path,
    feature_matrix_path: str | Path,
    config: AtlasConfig | None = None,
) -> AtlasArtifacts:
    """Build atlas artifacts through an explicit Polars input backend.

    This is the first migration seam for replacing the legacy pandas/Python
    accumulator pipeline. It keeps the existing Atlas math/output path for now,
    but moves strict input reads, row-alignment checks, and feature Parquet-sidecar
    selection behind a Polars boundary that can be expanded slice-by-slice.
    The normal run-research path is intentionally not switched here.
    """
    cfg = config or AtlasConfig()
    state_frame = _read_strict_polars_csv_frame(
        path=Path(state_path),
        usecols=_atlas_state_usecols(),
    )
    future_frame = _read_strict_polars_csv_frame(
        path=Path(future_path),
        usecols=_atlas_future_usecols(cfg.outcome_horizon_minutes_list),
    )
    feature_frame = _read_feature_matrix_polars_frame(
        csv_path=Path(feature_matrix_path),
        usecols=_atlas_feature_usecols(),
    )
    joined = _join_row_aligned_polars_frames(
        state_frame=state_frame,
        future_frame=future_frame,
        feature_frame=feature_frame,
    )
    _enforce_atlas_polars_temporal_contract(joined)
    joined_pandas = joined.to_pandas()
    _enforce_atlas_frame_temporal_contract(joined_pandas)
    _add_atlas_context_columns(joined_pandas)
    artifacts = _build_atlas_artifacts_from_frame(joined=joined_pandas, config=cfg)
    return _with_streaming_median_semantics(artifacts)


def _read_strict_polars_csv_frame(*, path: Path, usecols: Sequence[str]):
    _validate_strict_csv_header(path=path)
    pl = _import_polars_for_atlas_backend()
    return pl.read_csv(path, columns=list(usecols))


def _read_feature_matrix_polars_frame(*, csv_path: Path, usecols: Sequence[str]):
    _validate_strict_csv_header(path=csv_path)
    sidecar_path = csv_path.with_suffix(".parquet")
    manifest_path = csv_path.with_name(csv_path.stem + ".parquet_manifest.json")
    manifest_exists = manifest_path.is_file()
    sidecar_exists = sidecar_path.is_file()
    if manifest_exists != sidecar_exists:
        missing = sidecar_path if manifest_exists else manifest_path
        raise AtlasInputError(f"incomplete strategy_feature_matrix.parquet sidecar, missing {missing}")
    if not manifest_exists:
        pl = _import_polars_for_atlas_backend()
        return pl.read_csv(csv_path, columns=list(usecols))

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    parquet_name = payload.get("parquet_path")
    if not isinstance(parquet_name, str) or not parquet_name:
        raise AtlasInputError(f"{manifest_path.name} must contain non-empty parquet_path")
    declared_columns = payload.get("columns")
    if declared_columns is not None and list(declared_columns) != list(get_artifact_schema(csv_path.name).required_columns):
        raise AtlasInputError(f"{manifest_path.name} columns do not match {csv_path.name} schema")
    parquet_path = manifest_path.parent / parquet_name
    if parquet_path != sidecar_path:
        raise AtlasInputError(f"{manifest_path.name} parquet_path must point to {sidecar_path.name}")
    pl = _import_polars_for_atlas_backend()
    return pl.read_parquet(parquet_path, columns=list(usecols))


def _validate_strict_csv_header(*, path: Path) -> None:
    schema = get_artifact_schema(path.name)
    pl = _import_polars_for_atlas_backend()
    actual_columns = pl.read_csv(path, n_rows=0).columns
    expected_columns = list(schema.required_columns)
    if actual_columns != expected_columns:
        raise AtlasInputError(f"{path.name} columns must match {expected_columns}, got {actual_columns}")


def _join_row_aligned_polars_frames(
    *,
    state_frame,
    future_frame,
    feature_frame,
):
    pl = _import_polars_for_atlas_backend()
    if state_frame.height != future_frame.height or state_frame.height != feature_frame.height:
        raise AtlasInputError("state/future/feature atlas frames must have identical row counts")
    for column in _JOIN_COLUMNS:
        if state_frame.get_column(column).to_list() != future_frame.get_column(column).to_list():
            raise AtlasInputError(f"state/future atlas frames must be row-aligned on {column}")
        if state_frame.get_column(column).to_list() != feature_frame.get_column(column).to_list():
            raise AtlasInputError(f"state/feature atlas frames must be row-aligned on {column}")
    return pl.concat(
        [
            state_frame,
            future_frame.drop(list(_JOIN_COLUMNS)),
            feature_frame.drop(list(_JOIN_COLUMNS)),
        ],
        how="horizontal",
    )


def _enforce_atlas_polars_temporal_contract(frame) -> None:
    pl = _import_polars_for_atlas_backend()
    violations = frame.filter(
        (pl.col("feature_cutoff_time_ms") > pl.col("snapshot_time_ms"))
        | (pl.col("future_start_time_ms") <= pl.col("snapshot_time_ms"))
    )
    if violations.height:
        raise AtlasInputError("atlas joined frame violates feature/future temporal contract")


def _with_streaming_median_semantics(artifacts: AtlasArtifacts) -> AtlasArtifacts:
    return AtlasArtifacts(
        nature_atlas_rows=tuple(
            replace(row, median_future_return_atr=None, median_future_return=None)
            for row in artifacts.nature_atlas_rows
        ),
        context_split_rows=artifacts.context_split_rows,
        response_surface_rows=artifacts.response_surface_rows,
        market_shock_group_rows=artifacts.market_shock_group_rows,
    )
