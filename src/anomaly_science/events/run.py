from __future__ import annotations

from dataclasses import asdict

import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, write_csv_artifact, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.data.quality import has_critical_fail, rows_to_artifact, run_data_quality
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource
from anomaly_science.events.config import BroadAnomalyDetectorConfig
from anomaly_science.events.detector import detect_broad_anomaly_events, events_to_artifact


REQUIRED_DATASETS = ("candles_1m", "candles_5m")
OPTIONAL_DATASETS = ("open_interest_5m", "liquidations")
from anomaly_science.universe import build_symbol_universe_by_day, universe_rows_to_artifact


def run_mvp1_events(*, input_dir: str | Path, out_dir: str | Path, config: BroadAnomalyDetectorConfig | None = None) -> Path:
    """Run the MVP1 broad event detector and write protocol artifacts."""
    input_path = Path(input_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or BroadAnomalyDetectorConfig()

    frames, read_errors = _read_source_frames(input_path)
    data_quality = run_data_quality(frames, read_errors=read_errors)
    universe_rows = build_symbol_universe_by_day(
        candles_1m=frames.get("candles_1m"),
        candles_5m=frames.get("candles_5m"),
        open_interest_5m=frames.get("open_interest_5m"),
        liquidations=frames.get("liquidations"),
    )

    events = ()
    event_error: str | None = None
    if frames.get("candles_1m") is not None and not has_critical_fail(data_quality):
        try:
            events = detect_broad_anomaly_events(
                normalize_candles_1m(frames["candles_1m"]),
                config=cfg,
            )
        except (TypeError, ValueError) as exc:
            event_error = f"event detector failed: {exc}"

    protocol_rows = _protocol_rows(
        critical_fail=has_critical_fail(data_quality),
        universe_rows_present=bool(universe_rows),
        event_count=len(events),
        event_error=event_error,
    )
    run_config_rows = _run_config_rows(input_path=input_path, output_path=output_path, config=cfg)

    written: list[Path] = []
    written.append(write_csv_artifact(
        output_path / "anomaly_data_quality.csv",
        rows_to_artifact(data_quality),
        get_artifact_schema("anomaly_data_quality.csv"),
    ))
    written.append(write_csv_artifact(
        output_path / "symbol_universe_by_day.csv",
        universe_rows_to_artifact(universe_rows),
        get_artifact_schema("symbol_universe_by_day.csv"),
    ))
    written.append(write_csv_artifact(
        output_path / "anomaly_events.csv",
        events_to_artifact(events),
        get_artifact_schema("anomaly_events.csv"),
    ))
    written.append(write_csv_artifact(
        output_path / "anomaly_protocol_audit.csv",
        _protocol_rows_to_artifact(protocol_rows),
        get_artifact_schema("anomaly_protocol_audit.csv"),
    ))
    written.append(write_csv_artifact(
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
    return frames, errors


def _protocol_rows(
    *,
    critical_fail: bool,
    universe_rows_present: bool,
    event_count: int,
    event_error: str | None,
) -> list[ProtocolAuditRow]:
    rows = [
        ProtocolAuditRow(
            check_name="mvp1_events_scope",
            status=AuditStatus.PASS,
            message="data-source, data-quality, universe, broad events, run-config, and manifest only; state/future paths not run",
        ),
        ProtocolAuditRow(
            check_name="data_quality_critical_fail_gate",
            status=AuditStatus.FAIL if critical_fail else AuditStatus.PASS,
            message="critical data-quality FAIL present; detector output is not interpretable" if critical_fail else "no critical data-quality FAIL rows",
        ),
        ProtocolAuditRow(
            check_name="point_in_time_universe_written",
            status=AuditStatus.PASS if universe_rows_present else AuditStatus.WARN,
            message="symbol_universe_by_day.csv has rows" if universe_rows_present else "symbol_universe_by_day.csv is empty because no dated symbol data was available",
        ),
        ProtocolAuditRow(
            check_name="broad_anomaly_detector_written",
            status=AuditStatus.FAIL if event_error else AuditStatus.PASS,
            message=event_error or f"anomaly_events.csv written with {event_count} broad detector rows",
            artifact="anomaly_events.csv",
        ),
        ProtocolAuditRow(
            check_name="detector_not_trade_setup",
            status=AuditStatus.PASS,
            message="detector uses only current closed 1m candle plus earlier same-symbol baseline candles; no entry/exit/trade rules",
            artifact="anomaly_events.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 events uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    return rows



def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result

def _run_config_rows(*, input_path: Path, output_path: Path, config: BroadAnomalyDetectorConfig) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-events", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        RunConfigRow(key="git_commit", value="UNKNOWN", source="runtime"),
        RunConfigRow(key="stage", value="mvp1_events", source="runtime"),
        RunConfigRow(key="detector_version", value=config.detector_version, source="runtime"),
        RunConfigRow(key="detector_baseline_bars", value=str(config.baseline_bars), source="runtime"),
        RunConfigRow(key="detector_min_baseline_bars", value=str(config.min_baseline_bars), source="runtime"),
        RunConfigRow(key="detector_min_abs_return_pct", value=str(config.min_abs_return_pct), source="runtime"),
        RunConfigRow(key="detector_min_quote_volume_zscore", value=str(config.min_quote_volume_zscore), source="runtime"),
        RunConfigRow(key="detector_min_volume_zscore", value=str(config.min_volume_zscore), source="runtime"),
        RunConfigRow(key="detector_min_trade_count_zscore", value=str(config.min_trade_count_zscore), source="runtime"),
        RunConfigRow(key="detector_min_range_zscore", value=str(config.min_range_zscore), source="runtime"),
        RunConfigRow(key="detector_cooldown_minutes", value=str(config.cooldown_minutes), source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-events-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
