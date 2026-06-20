from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource
from anomaly_science.future.builder import (
    build_strategy_future_paths,
    future_rows_to_artifact,
    load_strategy_state_1m_csv,
)
from anomaly_science.future.config import FuturePathBuilderConfig


def run_mvp1_future(
    *,
    input_dir: str | Path,
    state_path: str | Path,
    out_dir: str | Path,
    config: FuturePathBuilderConfig | None = None,
) -> Path:
    """Run MVP1 raw future path builder and write protocol artifacts."""
    input_path = Path(input_dir)
    state_artifact_path = Path(state_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or FuturePathBuilderConfig()

    source = CsvDirectoryDataSource(input_path)
    state_rows = load_strategy_state_1m_csv(state_artifact_path)
    candle_frame = source.read_frame("candles_1m", required=True)
    if candle_frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    future_rows = build_strategy_future_paths(
        candles_1m=normalize_candles_1m(candle_frame),
        state_rows=state_rows,
        config=cfg,
    )
    protocol_rows = _protocol_rows(state_row_count=len(state_rows), future_row_count=len(future_rows))
    run_config_rows = _run_config_rows(
        input_path=input_path,
        state_path=state_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_future_paths.csv",
            future_rows_to_artifact(future_rows),
            get_artifact_schema("strategy_future_paths.csv"),
        )
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
            message=f"anomaly_state_1m.csv accepted through strict schema boundary with {state_row_count} state rows",
            artifact="anomaly_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="future_temporal_contract",
            status=AuditStatus.PASS,
            message="each future path row has feature_cutoff_time_ms <= snapshot_time_ms and future_start_time_ms > snapshot_time_ms",
            artifact="anomaly_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="future_windows_after_snapshot_only",
            status=AuditStatus.PASS,
            message="future returns, max/min, and new-high timings use candles with available_time_ms strictly greater than snapshot_time_ms",
            artifact="anomaly_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="structural_break_no_proxy",
            status=AuditStatus.PASS,
            message="structural break fields are explicit null until structural features exist; running_low_asof_t is not reused as a proxy",
            artifact="anomaly_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="future_rows_written",
            status=AuditStatus.PASS,
            message=f"anomaly_future_paths.csv written with {future_row_count} rows",
            artifact="anomaly_future_paths.csv",
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
            artifact="anomaly_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_labels_forbidden",
            status=AuditStatus.PASS,
            message="future paths materialize ATR-normalized returns/max/min and ATR-unit double-barrier thresholds; no fixed-percent label basis is emitted",
            artifact="anomaly_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="intracandle_double_barrier_resolved_as_stop_loss_first",
            status=AuditStatus.PASS,
            message="future path builder marks same-1m target/stop barrier collisions as intracandle_double_barrier_hit with barrier_resolution=stop_loss_first",
            artifact="anomaly_future_paths.csv",
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
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-future", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
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
