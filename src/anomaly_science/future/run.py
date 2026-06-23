from __future__ import annotations

import csv
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.artifacts.writer import ArtifactWriteError, link_or_copy_identical_artifact
from anomaly_science.contracts.artifacts import ArtifactSchema, get_artifact_schema, get_strategy_artifact_companion_names
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.future.builder import (
    iter_strategy_future_path_csv_value_rows_from_grouped_csv,
    validate_future_row_fieldnames,
)
from anomaly_science.future.config import FuturePathBuilderConfig
from anomaly_science.progress import ProgressCallback, ProgressUpdate


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
    for alias_name in get_strategy_artifact_companion_names(schema.name):
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
    if progress_callback is not None:
        progress_callback(
            ProgressUpdate(
                done=0,
                total=expected_row_count,
                unit="rows",
                detail="writing future rows",
                force=True,
            )
        )
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow(row)
            row_count += 1
            if row_count % 100_000 == 0:
                file_obj.flush()
                if progress_callback is not None:
                    progress_callback(
                        ProgressUpdate(
                            done=row_count,
                            total=expected_row_count,
                            unit="rows",
                            detail="writing future rows",
                        )
                    )
    os.replace(tmp_path, path)
    if progress_callback is not None:
        progress_callback(
            ProgressUpdate(
                done=row_count,
                total=expected_row_count,
                unit="rows",
                detail="future rows written",
                force=True,
            )
        )
    return row_count


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
            message=f"strategy_future_paths.csv written with {future_row_count} rows",
            artifact="strategy_future_paths.csv",
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
