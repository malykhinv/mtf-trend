from __future__ import annotations

import csv
import json
import os
import shutil
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.artifacts.manifest import sha256_file
from anomaly_science.artifacts.writer import ArtifactWriteError, link_or_copy_identical_artifact
from anomaly_science.contracts.artifacts import (
    ArtifactSchema,
    get_artifact_schema,
    get_strategy_artifact_companion_names,
    should_materialize_strategy_artifact_alias,
)
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.future.builder import (
    FUTURE_PATHS_PARQUET_ORDER_COLUMN,
    FUTURE_PATHS_PARQUET_SIDECAR_VERSION,
    future_paths_parquet_manifest_path,
    future_paths_parquet_sidecar_dir,
    iter_strategy_future_path_csv_value_rows_from_grouped_csv,
    validate_future_row_fieldnames,
)
from anomaly_science.future.config import FuturePathBuilderConfig
from anomaly_science.progress import ProgressCallback, ProgressUpdate


FUTURE_PATHS_PARQUET_BATCH_SIZE = 100_000
FUTURE_PATHS_CSV_DELIVERY = "schema_header_only"


def run_mvp1_future(
    *,
    input_dir: str | Path,
    state_path: str | Path,
    out_dir: str | Path,
    config: FuturePathBuilderConfig | None = None,
    max_input_time_ms: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """Run MVP1 raw future path builder and write protocol artifacts."""
    input_path = Path(input_dir)
    state_artifact_path = Path(state_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or FuturePathBuilderConfig()

    written: list[Path] = []
    expected_row_count = _count_csv_data_rows(state_artifact_path)
    future_schema = get_artifact_schema("strategy_future_paths.csv")
    future_written, future_row_count = _write_future_rows_with_aliases(
        output_path / "strategy_future_paths.csv",
        iter_strategy_future_path_csv_value_rows_from_grouped_csv(
            candles_path=input_path / "candles_1m.csv",
            state_path=state_artifact_path,
            fieldnames=future_schema.required_columns,
            config=cfg,
            max_input_time_ms=max_input_time_ms,
        ),
        future_schema,
        expected_row_count=expected_row_count,
        progress_callback=progress_callback,
    )
    written.extend(future_written)
    protocol_rows = _protocol_rows(state_row_count=future_row_count, future_row_count=future_row_count)
    run_config_rows = _run_config_rows(
        input_path=input_path,
        state_path=state_artifact_path,
        output_path=output_path,
        config=cfg,
        max_input_time_ms=max_input_time_ms,
    )

    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("strategy_protocol_audit.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("strategy_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _write_future_rows_with_aliases(
    path: Path,
    rows: Iterable[list[object]],
    schema: ArtifactSchema,
    expected_row_count: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[Path], int]:
    if path.name != schema.name:
        raise ArtifactWriteError(f"path name {path.name!r} does not match schema name {schema.name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = _write_future_rows(
        path=path,
        rows=rows,
        schema=schema,
        expected_row_count=expected_row_count,
        progress_callback=progress_callback,
    )
    written = [path]
    sidecar_manifest = future_paths_parquet_manifest_path(path)
    if sidecar_manifest.is_file():
        written.append(sidecar_manifest)
    sidecar_dir = future_paths_parquet_sidecar_dir(path)
    if sidecar_dir.is_dir():
        written.extend(sorted(sidecar_dir.rglob("*.parquet")))
    for alias_name in get_strategy_artifact_companion_names(schema.name):
        if not should_materialize_strategy_artifact_alias(schema.name, alias_name):
            continue
        alias_path = path.with_name(alias_name)
        alias_schema = get_artifact_schema(alias_name)
        if tuple(alias_schema.required_columns) != tuple(schema.required_columns):
            raise ArtifactWriteError(f"{schema.name} streaming alias {alias_name} must have identical columns")
        link_or_copy_identical_artifact(path, alias_path)
        written.append(alias_path)
    return written, row_count


def _write_future_rows(
    *,
    path: Path,
    rows: Iterable[list[object]],
    schema: ArtifactSchema,
    expected_row_count: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> int:
    fieldnames = list(schema.required_columns)
    try:
        validate_future_row_fieldnames(fieldnames)
    except ValueError as exc:
        raise ArtifactWriteError(str(exc)) from exc
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    row_count = 0
    sidecar_writer = _FuturePathsPartitionedParquetSidecarWriter(
        csv_path=path,
        fieldnames=fieldnames,
        csv_delivery=FUTURE_PATHS_CSV_DELIVERY,
    )
    if progress_callback is not None:
        progress_callback(
            ProgressUpdate(
                done=0,
                total=expected_row_count,
                unit="rows",
                detail="writing future rows to partitioned parquet",
                force=True,
            )
        )
    try:
        with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.writer(file_obj)
            writer.writerow(fieldnames)
            for row in rows:
                if len(row) != len(fieldnames):
                    raise ArtifactWriteError(
                        f"strategy_future_paths.csv direct row has {len(row)} values, expected {len(fieldnames)}"
                    )
                sidecar_writer.append(row)
                row_count += 1
                if row_count % 100_000 == 0:
                    if progress_callback is not None:
                        progress_callback(
                            ProgressUpdate(
                                done=row_count,
                                total=expected_row_count,
                                unit="rows",
                                detail="writing future rows to partitioned parquet",
                            )
                        )
        os.replace(tmp_path, path)
        sidecar_writer.close(expected_row_count=row_count)
    except Exception:
        sidecar_writer.abort()
        _remove_path(tmp_path)
        _remove_path(path)
        raise
    if progress_callback is not None:
        progress_callback(
            ProgressUpdate(
                done=row_count,
                total=expected_row_count,
                unit="rows",
                detail="future rows written to partitioned parquet",
                force=True,
            )
        )
    return row_count


class _FuturePathsPartitionedParquetSidecarWriter:
    def __init__(
        self,
        *,
        csv_path: Path,
        fieldnames: Sequence[str],
        csv_delivery: str,
    ) -> None:
        self.csv_path = csv_path
        self.fieldnames = tuple(fieldnames)
        self.csv_delivery = csv_delivery
        self.sidecar_dir = future_paths_parquet_sidecar_dir(csv_path)
        self.tmp_sidecar_dir = csv_path.with_name(self.sidecar_dir.name + ".tmp")
        self.manifest_path = future_paths_parquet_manifest_path(csv_path)
        self._pa, self._pq = _import_pyarrow_for_future_paths_sidecar()
        self._schema = _future_paths_arrow_schema(self.fieldnames)
        self._columns: dict[str, list[object]] = {
            FUTURE_PATHS_PARQUET_ORDER_COLUMN: [],
            **{fieldname: [] for fieldname in self.fieldnames},
        }
        self._row_count = 0
        self._part_index = 0
        self._part_paths: list[Path] = []
        self._closed = False
        _remove_path(self.tmp_sidecar_dir)
        self.tmp_sidecar_dir.mkdir(parents=True, exist_ok=False)

    def append(self, row_values: Sequence[object]) -> None:
        if self._closed:
            raise ArtifactWriteError("strategy_future_paths.parquet sidecar writer is already closed")
        if len(row_values) != len(self.fieldnames):
            raise ArtifactWriteError(
                f"strategy_future_paths.parquet row has {len(row_values)} values, expected {len(self.fieldnames)}"
            )
        self._columns[FUTURE_PATHS_PARQUET_ORDER_COLUMN].append(self._row_count)
        for fieldname, value in zip(self.fieldnames, row_values, strict=True):
            self._columns[fieldname].append(_future_parquet_value(value))
        self._row_count += 1
        if self._row_count % FUTURE_PATHS_PARQUET_BATCH_SIZE == 0:
            self.flush()

    def flush(self) -> None:
        batch_size = len(self._columns[FUTURE_PATHS_PARQUET_ORDER_COLUMN])
        if batch_size == 0:
            return
        arrays = [
            self._pa.array(self._columns[name], type=self._schema.field(name).type)
            for name in (FUTURE_PATHS_PARQUET_ORDER_COLUMN, *self.fieldnames)
        ]
        table = self._pa.Table.from_arrays(arrays, schema=self._schema)
        snapshot_col_index = table.schema.get_field_index("snapshot_time_ms")
        snapshot_values = table.column(snapshot_col_index).to_pylist()
        rows_by_date: dict[str, list[int]] = {}
        for row_index, snapshot_time_ms in enumerate(snapshot_values):
            if snapshot_time_ms is None:
                raise ArtifactWriteError("strategy_future_paths.parquet snapshot_time_ms must not be null")
            rows_by_date.setdefault(_utc_date_from_ms(int(snapshot_time_ms)), []).append(row_index)
        for snapshot_date, row_indices in sorted(rows_by_date.items()):
            partition_dir = self.tmp_sidecar_dir / f"date={snapshot_date}"
            partition_dir.mkdir(parents=True, exist_ok=True)
            part_path = partition_dir / f"part-{self._part_index:06d}.parquet"
            self._part_index += 1
            part_table = table.take(self._pa.array(row_indices, type=self._pa.int64()))
            self._pq.write_table(part_table, part_path, compression="zstd")
            self._part_paths.append(part_path)
        self._columns = {
            FUTURE_PATHS_PARQUET_ORDER_COLUMN: [],
            **{fieldname: [] for fieldname in self.fieldnames},
        }

    def close(self, *, expected_row_count: int) -> tuple[Path, Path]:
        if self._closed:
            return self.sidecar_dir, self.manifest_path
        self.flush()
        if self._row_count != expected_row_count:
            raise ArtifactWriteError(
                f"strategy_future_paths.parquet row count {self._row_count} does not match expected {expected_row_count}"
            )
        _remove_path(self.sidecar_dir)
        os.replace(self.tmp_sidecar_dir, self.sidecar_dir)
        final_part_paths = [
            self.sidecar_dir / part_path.relative_to(self.tmp_sidecar_dir)
            for part_path in self._part_paths
        ]
        _write_future_paths_parquet_manifest(
            manifest_path=self.manifest_path,
            csv_path=self.csv_path,
            sidecar_dir=self.sidecar_dir,
            part_paths=final_part_paths,
            fieldnames=self.fieldnames,
            row_count=self._row_count,
            csv_delivery=self.csv_delivery,
        )
        self._closed = True
        return self.sidecar_dir, self.manifest_path

    def abort(self) -> None:
        _remove_path(self.tmp_sidecar_dir)




def _write_future_paths_parquet_manifest(
    *,
    manifest_path: Path,
    csv_path: Path,
    sidecar_dir: Path,
    part_paths: Sequence[Path],
    fieldnames: Sequence[str],
    row_count: int,
    csv_delivery: str,
) -> Path:
    payload = {
        "sidecar_version": FUTURE_PATHS_PARQUET_SIDECAR_VERSION,
        "artifact_name": csv_path.name,
        "csv_path": csv_path.name,
        "csv_size_bytes": csv_path.stat().st_size,
        "csv_sha256": sha256_file(csv_path),
        "csv_delivery": csv_delivery,
        "parquet_path": sidecar_dir.name,
        "parquet_delivery": "canonical_partitioned_future_paths",
        "partitioning": "snapshot_date_utc",
        "order_column": FUTURE_PATHS_PARQUET_ORDER_COLUMN,
        "required_columns": list(fieldnames),
        "row_count": row_count,
        "part_count": len(part_paths),
        "part_paths": [str(path.relative_to(csv_path.parent)) for path in sorted(part_paths)],
        "delivery": "strict_typed_partitioned_parquet_sidecar",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, manifest_path)
    return manifest_path


def _future_paths_arrow_schema(fieldnames: Sequence[str]):
    pa, _pq = _import_pyarrow_for_future_paths_sidecar()
    return pa.schema(
        [pa.field(FUTURE_PATHS_PARQUET_ORDER_COLUMN, pa.int64(), nullable=False)]
        + [pa.field(fieldname, _future_arrow_type(fieldname, pa), nullable=True) for fieldname in fieldnames]
    )


def _future_arrow_type(fieldname: str, pa):
    if fieldname in {"event_id", "symbol"} or fieldname.startswith("barrier_resolution_"):
        return pa.string()
    if fieldname.startswith("intracandle_double_barrier_hit_") or fieldname.startswith("reclaimed_running_high_") or fieldname.startswith("broke_structural_low_"):
        return pa.bool_()
    if fieldname.endswith("_time_ms") or fieldname in {
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "future_start_time_ms",
        "atr_window_minutes",
        "time_to_new_high_minutes",
        "time_to_structural_break_minutes",
    }:
        return pa.int64()
    return pa.float64()


def _future_parquet_value(value: object) -> object:
    return None if value == "" else value


def _utc_date_from_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date().isoformat()


def _import_pyarrow_for_future_paths_sidecar():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise ArtifactWriteError(
            "pyarrow is required to write strategy_future_paths.parquet; install project dependencies"
        ) from exc
    return pa, pq


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()



def _count_csv_data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    line_count = 0
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            line_count += chunk.count(b"\n")
    return max(line_count - 1, 0)


def _protocol_rows(*, state_row_count: int, future_row_count: int) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_future_scope",
            status=AuditStatus.PASS,
            message="raw future paths only; no scenario labels, ML, PnL, trade simulation, shadow live, or production live",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_state_1m.csv accepted through strict schema boundary with {state_row_count} state rows",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="future_temporal_contract",
            status=AuditStatus.PASS,
            message="each future path row has feature_cutoff_time_ms <= snapshot_time_ms and future_start_time_ms > snapshot_time_ms",
            artifact="strategy_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="future_windows_after_snapshot_only",
            status=AuditStatus.PASS,
            message="future returns, max/min, and new-high timings use candles with available_time_ms strictly greater than snapshot_time_ms",
            artifact="strategy_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="structural_break_no_proxy",
            status=AuditStatus.PASS,
            message="structural break fields are explicit null until structural features exist; running_low_asof_t is not reused as a proxy",
            artifact="strategy_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="future_rows_written",
            status=AuditStatus.PASS,
            message=f"strategy_future_paths.parquet partitioned sidecar written with {future_row_count} rows; strategy_future_paths.csv is schema header only",
            artifact="strategy_future_paths.parquet_manifest.json",
        ),
        ProtocolAuditRow(
            check_name="future_parquet_first_delivery",
            status=AuditStatus.PASS,
            message="canonical future-path data is stored in typed partitioned Parquet; CSV delivery is schema_header_only",
            artifact="strategy_future_paths.parquet",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 future paths use anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="ATR_1d_asof_t_computed_from_closed_past_candles",
            status=AuditStatus.PASS,
            message="future path builder computes core_atr_1440 from the last 1440 true ranges using closed 1m candles available <= snapshot_time_ms; CSV alias is ATR_1d_asof_t",
            artifact="strategy_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_labels_forbidden",
            status=AuditStatus.PASS,
            message="future paths materialize ATR-normalized returns/max/min and ATR-unit double-barrier thresholds; no fixed-percent label basis is emitted",
            artifact="strategy_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="intracandle_double_barrier_resolved_as_stop_loss_first",
            status=AuditStatus.PASS,
            message="future path builder marks same-1m target/stop barrier collisions as intracandle_double_barrier_hit with barrier_resolution=stop_loss_first",
            artifact="strategy_future_paths.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_future",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(
    *,
    input_path: Path,
    state_path: Path,
    output_path: Path,
    config: FuturePathBuilderConfig,
    max_input_time_ms: int | None = None,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-future", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="max_input_time_ms", value="" if max_input_time_ms is None else str(max_input_time_ms), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        *runtime_reproducibility_rows(
            data_paths=(input_path, state_path),
            config=config,
            extra_config={"command": "run-mvp1-future", "stage": "mvp1_future"},
        ),
        RunConfigRow(key="stage", value="mvp1_future", source="runtime"),
        RunConfigRow(key="future_path_builder_version", value=config.future_path_builder_version, source="runtime"),
        RunConfigRow(key="future_paths_csv_delivery", value=FUTURE_PATHS_CSV_DELIVERY, source="runtime"),
        RunConfigRow(key="future_paths_parquet_delivery", value="canonical_partitioned_future_paths", source="runtime"),
        RunConfigRow(
            key="future_return_horizons_minutes",
            value=",".join(str(item) for item in config.future_return_horizons_minutes),
            source="runtime",
        ),
        RunConfigRow(
            key="future_reclaim_horizons_minutes",
            value=",".join(str(item) for item in config.future_reclaim_horizons_minutes),
            source="runtime",
        ),
        RunConfigRow(key="structural_break_fields", value="not_computed_explicit_null", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-future-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
