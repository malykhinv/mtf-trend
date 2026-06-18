from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.data.normalized import normalize_market_data
from anomaly_science.data.quality import has_critical_fail, rows_to_artifact, run_data_quality
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource
from anomaly_science.universe import build_symbol_universe_by_day, universe_rows_to_artifact


REQUIRED_DATASETS = ("candles_1m", "candles_5m")
OPTIONAL_DATASETS = ("open_interest_5m", "liquidations")


def run_mvp1_data_audit(*, input_dir: str | Path, out_dir: str | Path) -> Path:
    """Run the MVP1 data-boundary audit without detector/trading logic."""
    input_path = Path(input_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    frames, read_errors = _read_source_frames(input_path)
    data_quality = run_data_quality(frames, read_errors=read_errors)
    universe_rows = build_symbol_universe_by_day(
        candles_1m=frames.get("candles_1m"),
        candles_5m=frames.get("candles_5m"),
        open_interest_5m=frames.get("open_interest_5m"),
        liquidations=frames.get("liquidations"),
    )
    protocol_rows = _protocol_rows(data_quality=data_quality, universe_rows=universe_rows)
    run_config_rows = _run_config_rows(input_path=input_path, output_path=output_path)

    written: list[Path] = []
    written.extend(write_csv_artifact_with_aliases(
        output_path / "anomaly_data_quality.csv",
        rows_to_artifact(data_quality),
        get_artifact_schema("anomaly_data_quality.csv"),
    ))
    written.append(write_csv_artifact(
        output_path / "symbol_universe_by_day.csv",
        universe_rows_to_artifact(universe_rows),
        get_artifact_schema("symbol_universe_by_day.csv"),
    ))
    written.extend(write_csv_artifact_with_aliases(
        output_path / "anomaly_protocol_audit.csv",
        _protocol_rows_to_artifact(protocol_rows),
        get_artifact_schema("anomaly_protocol_audit.csv"),
    ))
    written.extend(write_csv_artifact_with_aliases(
        output_path / "anomaly_run_config.csv",
        [asdict(row) for row in run_config_rows],
        get_artifact_schema("anomaly_run_config.csv"),
    ))
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _read_source_frames(input_path: Path) -> tuple[dict[str, pd.DataFrame | None], list[str]]:
    source = CsvDirectoryDataSource(input_path)
    frames: dict[str, pd.DataFrame | None] = {}
    errors: list[str] = []

    for dataset_name in REQUIRED_DATASETS:
        try:
            frames[dataset_name] = source.read_frame(dataset_name, required=True)
        except CsvDataSourceError as exc:
            frames[dataset_name] = None
            errors.append(str(exc))

    for dataset_name in OPTIONAL_DATASETS:
        try:
            frames[dataset_name] = source.read_frame(dataset_name, required=False)
        except CsvDataSourceError as exc:
            frames[dataset_name] = None
            errors.append(str(exc))

    if frames.get("candles_1m") is not None and frames.get("candles_5m") is not None:
        try:
            normalize_market_data(
                candles_1m=frames["candles_1m"],
                candles_5m=frames["candles_5m"],
                open_interest_5m=frames.get("open_interest_5m"),
                liquidations=frames.get("liquidations"),
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"normalization contract failed: {exc}")
    return frames, errors


def _protocol_rows(*, data_quality: list, universe_rows: list) -> list[ProtocolAuditRow]:
    critical_fail = has_critical_fail(data_quality)
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_data_audit_scope",
            status=AuditStatus.PASS,
            message="data-source, data-quality, universe, run-config, and manifest only; detector/state/future paths not run",
        ),
        ProtocolAuditRow(
            check_name="data_quality_critical_fail_gate",
            status=AuditStatus.FAIL if critical_fail else AuditStatus.PASS,
            message="critical data-quality FAIL present" if critical_fail else "no critical data-quality FAIL rows",
        ),
        ProtocolAuditRow(
            check_name="point_in_time_universe_written",
            status=AuditStatus.PASS if universe_rows else AuditStatus.WARN,
            message="symbol_universe_by_day.csv has rows" if universe_rows else "symbol_universe_by_day.csv is empty because no dated symbol data was available",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 data audit uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="technical_noise_shock_flag_computed_from_raw_timestamp_gaps",
            status=AuditStatus.PASS,
            message="candles_1m data-quality audit marks first candles after raw timestamp gaps > 3 minutes as technical_noise_shock rows",
            artifact="anomaly_data_quality.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_data_audit",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(*, input_path: Path, output_path: Path) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-data-audit", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        *runtime_reproducibility_rows(
            data_paths=(input_path,),
            extra_config={"command": "run-mvp1-data-audit", "stage": "mvp1_data_audit", "data_source": "csv_directory_v1"},
        ),
        RunConfigRow(key="stage", value="mvp1_data_audit", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-data-audit-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
