from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

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
from anomaly_science.contracts.state import StrategyState1mRow
from anomaly_science.state.builder import (
    iter_online_strategy_state_1m_from_grouped_csv,
    load_strategy_events_csv,
)
from anomaly_science.state.config import OnlineStateBuilderConfig
from anomaly_science.state.parquet_sidecar import (
    STATE_1M_PARQUET_ORDER_COLUMN,
    STATE_1M_PARQUET_SIDECAR_VERSION,
    state_1m_parquet_manifest_path,
    state_1m_parquet_sidecar_dir,
)
from anomaly_science.progress import ProgressCallback, ProgressUpdate


STATE_1M_PARQUET_BATCH_SIZE = 100_000
STATE_1M_CSV_DELIVERY = "schema_header_only"


def run_mvp1_state(
    *,
    input_dir: str | Path,
    events_path: str | Path,
    out_dir: str | Path,
    config: OnlineStateBuilderConfig | None = None,
    max_input_time_ms: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """Run MVP1 online 1m state builder and write protocol artifacts."""
    input_path = Path(input_dir)
    events_artifact_path = Path(events_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or OnlineStateBuilderConfig()

    events = load_strategy_events_csv(events_artifact_path)
    written: list[Path] = []
    state_written, state_row_count = _write_state_rows_with_aliases(
        output_path / "strategy_state_1m.csv",
        iter_online_strategy_state_1m_from_grouped_csv(
            candles_path=input_path / "candles_1m.csv",
            events=events,
            config=cfg,
            max_open_time_ms=max_input_time_ms,
        ),
        get_artifact_schema("strategy_state_1m.csv"),
        progress_callback=progress_callback,
    )
    written.extend(state_written)
    excluded_event_count = sum(1 for event in events if event.technical_noise_shock or event.excluded_by_data_quality_gate)
    protocol_rows = _protocol_rows(
        event_count=len(events),
        state_row_count=state_row_count,
        excluded_event_count=excluded_event_count,
    )
    run_config_rows = _run_config_rows(
        input_path=input_path,
        events_path=events_artifact_path,
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


def _write_state_rows_with_aliases(
    path: Path,
    rows: Iterable[StrategyState1mRow],
    schema: ArtifactSchema,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[Path], int]:
    if path.name != schema.name:
        raise ValueError(f"path name {path.name!r} does not match schema name {schema.name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = _write_state_rows(path=path, rows=rows, schema=schema, progress_callback=progress_callback)
    written = [path]
    sidecar_manifest = state_1m_parquet_manifest_path(path)
    if sidecar_manifest.is_file():
        written.append(sidecar_manifest)
    sidecar_dir = state_1m_parquet_sidecar_dir(path)
    if sidecar_dir.is_dir():
        written.extend(sorted(sidecar_dir.rglob("*.parquet")))
    for alias_name in get_strategy_artifact_companion_names(schema.name):
        if not should_materialize_strategy_artifact_alias(schema.name, alias_name):
            continue
        alias_path = path.with_name(alias_name)
        alias_schema = get_artifact_schema(alias_name)
        if tuple(alias_schema.required_columns) != tuple(schema.required_columns):
            raise ValueError(f"{schema.name} streaming alias {alias_name} must have identical columns")
        link_or_copy_identical_artifact(path, alias_path)
        written.append(alias_path)
    return written, row_count


def _write_state_rows(
    *,
    path: Path,
    rows: Iterable[StrategyState1mRow],
    schema: ArtifactSchema,
    progress_callback: ProgressCallback | None = None,
) -> int:
    fieldnames = list(schema.required_columns)
    _validate_state_row_fieldnames(fieldnames)
    attribute_names = [_state_row_attribute_name(fieldname) for fieldname in fieldnames]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    row_count = 0
    sidecar_writer = _State1mPartitionedParquetSidecarWriter(
        csv_path=path,
        fieldnames=fieldnames,
        csv_delivery=STATE_1M_CSV_DELIVERY,
    )
    if progress_callback is not None:
        progress_callback(ProgressUpdate(done=0, unit="rows", detail="writing state rows to partitioned parquet", force=True))
    try:
        with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.writer(file_obj)
            writer.writerow(fieldnames)
            for row in rows:
                values = _state_row_values_for_attributes(row=row, attribute_names=attribute_names)
                sidecar_writer.append(values)
                row_count += 1
                if progress_callback is not None and row_count % 100_000 == 0:
                    progress_callback(ProgressUpdate(done=row_count, unit="rows", detail="writing state rows to partitioned parquet"))
        os.replace(tmp_path, path)
        sidecar_writer.close(expected_row_count=row_count)
    except Exception:
        sidecar_writer.abort()
        _remove_path(tmp_path)
        _remove_path(path)
        raise
    if progress_callback is not None:
        progress_callback(ProgressUpdate(done=row_count, unit="rows", detail="state rows written to partitioned parquet", force=True))
    return row_count



class _State1mPartitionedParquetSidecarWriter:
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
        self.sidecar_dir = state_1m_parquet_sidecar_dir(csv_path)
        self.tmp_sidecar_dir = csv_path.with_name(self.sidecar_dir.name + ".tmp")
        self.manifest_path = state_1m_parquet_manifest_path(csv_path)
        self._pa, self._pq = _import_pyarrow_for_state_sidecar()
        self._schema = _state_1m_arrow_schema(self.fieldnames)
        self._columns: dict[str, list[object]] = {
            STATE_1M_PARQUET_ORDER_COLUMN: [],
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
            raise ArtifactWriteError("strategy_state_1m.parquet sidecar writer is already closed")
        if len(row_values) != len(self.fieldnames):
            raise ArtifactWriteError(
                f"strategy_state_1m.parquet row has {len(row_values)} values, expected {len(self.fieldnames)}"
            )
        self._columns[STATE_1M_PARQUET_ORDER_COLUMN].append(self._row_count)
        for fieldname, value in zip(self.fieldnames, row_values, strict=True):
            self._columns[fieldname].append(_state_parquet_value(value))
        self._row_count += 1
        if self._row_count % STATE_1M_PARQUET_BATCH_SIZE == 0:
            self.flush()

    def flush(self) -> None:
        batch_size = len(self._columns[STATE_1M_PARQUET_ORDER_COLUMN])
        if batch_size == 0:
            return
        arrays = [
            self._pa.array(self._columns[name], type=self._schema.field(name).type)
            for name in (STATE_1M_PARQUET_ORDER_COLUMN, *self.fieldnames)
        ]
        table = self._pa.Table.from_arrays(arrays, schema=self._schema)
        snapshot_col_index = table.schema.get_field_index("snapshot_time_ms")
        snapshot_values = table.column(snapshot_col_index).to_pylist()
        rows_by_date: dict[str, list[int]] = {}
        for row_index, snapshot_time_ms in enumerate(snapshot_values):
            if snapshot_time_ms is None:
                raise ArtifactWriteError("strategy_state_1m.parquet snapshot_time_ms must not be null")
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
            STATE_1M_PARQUET_ORDER_COLUMN: [],
            **{fieldname: [] for fieldname in self.fieldnames},
        }

    def close(self, *, expected_row_count: int) -> tuple[Path, Path]:
        if self._closed:
            return self.sidecar_dir, self.manifest_path
        self.flush()
        if self._row_count != expected_row_count:
            raise ArtifactWriteError(
                f"strategy_state_1m.parquet row count {self._row_count} does not match expected {expected_row_count}"
            )
        _remove_path(self.sidecar_dir)
        os.replace(self.tmp_sidecar_dir, self.sidecar_dir)
        final_part_paths = [
            self.sidecar_dir / part_path.relative_to(self.tmp_sidecar_dir)
            for part_path in self._part_paths
        ]
        _write_state_1m_parquet_manifest(
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


def _write_state_1m_parquet_manifest(
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
        "sidecar_version": STATE_1M_PARQUET_SIDECAR_VERSION,
        "artifact_name": csv_path.name,
        "csv_path": csv_path.name,
        "csv_size_bytes": csv_path.stat().st_size,
        "csv_sha256": sha256_file(csv_path),
        "csv_delivery": csv_delivery,
        "parquet_path": sidecar_dir.name,
        "parquet_delivery": "canonical_partitioned_state_1m",
        "partitioning": "snapshot_date_utc",
        "order_column": STATE_1M_PARQUET_ORDER_COLUMN,
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


def _state_1m_arrow_schema(fieldnames: Sequence[str]):
    pa, _pq = _import_pyarrow_for_state_sidecar()
    return pa.schema(
        [pa.field(STATE_1M_PARQUET_ORDER_COLUMN, pa.int64(), nullable=False)]
        + [pa.field(fieldname, _state_arrow_type(fieldname, pa), nullable=True) for fieldname in fieldnames]
    )


def _state_arrow_type(fieldname: str, pa):
    if fieldname in {"event_id", "symbol"}:
        return pa.string()
    if fieldname == "event_alive":
        return pa.bool_()
    if fieldname.endswith("_ms") or fieldname in {
        "minutes_since_event_start",
        "minutes_since_detection",
        "time_since_running_high_minutes",
    }:
        return pa.int64()
    return pa.float64()


def _state_parquet_value(value: object) -> object:
    return None if value == "" else value


def _utc_date_from_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date().isoformat()


def _import_pyarrow_for_state_sidecar():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise ArtifactWriteError(
            "pyarrow is required to write strategy_state_1m.parquet; install project dependencies"
        ) from exc
    return pa, pq


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()

def _state_row_values_for_attributes(*, row: StrategyState1mRow, attribute_names: Sequence[str]) -> list[object]:
    return ["" if (value := getattr(row, attribute_name)) is None else value for attribute_name in attribute_names]


def _state_row_to_artifact_for_attributes(
    *,
    row: StrategyState1mRow,
    fieldnames: Sequence[str],
    attribute_names: Sequence[str],
) -> dict[str, object]:
    return {
        fieldname: "" if (value := getattr(row, attribute_name)) is None else value
        for fieldname, attribute_name in zip(fieldnames, attribute_names)
    }


def _validate_state_row_fieldnames(fieldnames: Sequence[str]) -> None:
    row_fields = set(StrategyState1mRow.__dataclass_fields__)
    missing = [
        fieldname
        for fieldname in fieldnames
        if _state_row_attribute_name(fieldname) not in row_fields
    ]
    if missing:
        raise ValueError(f"strategy_state_1m.csv schema has unknown row fields: {missing}")


def _state_row_attribute_name(fieldname: str) -> str:
    return fieldname


def _protocol_rows(*, event_count: int, state_row_count: int, excluded_event_count: int) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_state_scope",
            status=AuditStatus.PASS,
            message="online 1m state only; no future paths, labels, ML, PnL, trade simulation, or live execution",
        ),
        ProtocolAuditRow(
            check_name="events_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_events.csv accepted through strict schema boundary with {event_count} event rows",
            artifact="strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="state_temporal_contract",
            status=AuditStatus.PASS,
            message="each state row uses closed 1m candles with available_time_ms <= state_time_ms; feature_cutoff_time_ms <= state_time_ms",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="running_high_low_asof_only",
            status=AuditStatus.PASS,
            message="running high/low are computed from event_start_time_ms through each state_time_ms, not from future event extremes",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="state_rows_written",
            status=AuditStatus.PASS,
            message=f"strategy_state_1m.parquet partitioned sidecar written with {state_row_count} rows; strategy_state_1m.csv is schema header only",
            artifact="strategy_state_1m.parquet_manifest.json",
        ),
        ProtocolAuditRow(
            check_name="state_parquet_first_delivery",
            status=AuditStatus.PASS,
            message="canonical online state data is stored in typed partitioned Parquet; CSV delivery is schema_header_only",
            artifact="strategy_state_1m.parquet",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 state uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
            status=AuditStatus.PASS,
            message=(
                f"state builder excluded {excluded_event_count} technical-noise/data-quality-gated events before "
                "state/label/prediction/control artifacts can be built"
            ),
            artifact="strategy_state_1m.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_state",
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
    events_path: Path,
    output_path: Path,
    config: OnlineStateBuilderConfig,
    max_input_time_ms: int | None = None,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-state", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="events_path", value=str(events_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="max_input_time_ms", value="" if max_input_time_ms is None else str(max_input_time_ms), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        *runtime_reproducibility_rows(
            data_paths=(input_path, events_path),
            config=config,
            extra_config={"command": "run-mvp1-state", "stage": "mvp1_state"},
        ),
        RunConfigRow(key="stage", value="mvp1_state", source="runtime"),
        RunConfigRow(key="state_builder_version", value=config.state_builder_version, source="runtime"),
        RunConfigRow(key="state_1m_csv_delivery", value=STATE_1M_CSV_DELIVERY, source="runtime"),
        RunConfigRow(key="state_1m_parquet_delivery", value="canonical_partitioned_state_1m", source="runtime"),
        RunConfigRow(
            key="max_state_minutes_after_detection",
            value=str(config.max_state_minutes_after_detection),
            source="runtime",
        ),
        RunConfigRow(key="structural_features", value="not_computed_explicit_null", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-state-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
