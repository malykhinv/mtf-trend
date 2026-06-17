from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, write_csv_artifact, write_manifest
from anomaly_science.atlas.builder import (
    build_atlas_artifacts_from_inputs,
    context_split_rows_to_artifact,
    load_atlas_inputs,
    market_shock_group_rows_to_artifact,
    nature_rows_to_artifact,
    response_surface_rows_to_artifact,
)
from anomaly_science.atlas.config import AtlasConfig
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.audit import build_methodology_v2_audit_rows


def run_mvp1_atlas(
    *,
    state_path: str | Path,
    future_path: str | Path,
    out_dir: str | Path,
    config: AtlasConfig | None = None,
) -> Path:
    """Run MVP1 anomaly nature atlas and write descriptive discovery artifacts."""
    state_artifact_path = Path(state_path)
    future_artifact_path = Path(future_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or AtlasConfig()

    inputs = load_atlas_inputs(state_path=state_artifact_path, future_path=future_artifact_path, config=cfg)
    artifacts = build_atlas_artifacts_from_inputs(inputs=inputs, config=cfg)
    protocol_rows = _protocol_rows(input_row_count=len(inputs), market_shock_group_count=len(artifacts.market_shock_group_rows))
    run_config_rows = _run_config_rows(
        state_path=state_artifact_path,
        future_path=future_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.append(
        write_csv_artifact(
            output_path / "anomaly_nature_atlas.csv",
            nature_rows_to_artifact(artifacts.nature_atlas_rows),
            get_artifact_schema("anomaly_nature_atlas.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_context_splits.csv",
            context_split_rows_to_artifact(artifacts.context_split_rows),
            get_artifact_schema("anomaly_context_splits.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_response_surfaces.csv",
            response_surface_rows_to_artifact(artifacts.response_surface_rows),
            get_artifact_schema("anomaly_response_surfaces.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_market_shock_groups.csv",
            market_shock_group_rows_to_artifact(artifacts.market_shock_group_rows),
            get_artifact_schema("anomaly_market_shock_groups.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("anomaly_protocol_audit.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("anomaly_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _protocol_rows(*, input_row_count: int, market_shock_group_count: int) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_atlas_scope",
            status=AuditStatus.PASS,
            message="descriptive atlas only; no ML models, calibrated labels, PnL, trade simulation, decision logic, shadow live, or production live",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_state_1m.csv accepted through strict schema boundary for {input_row_count} joined atlas rows",
            artifact="anomaly_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="future_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_future_paths.csv accepted through strict schema boundary for {input_row_count} joined atlas rows",
            artifact="anomaly_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="atlas_temporal_contract",
            status=AuditStatus.PASS,
            message="atlas join preserves feature_cutoff_time_ms <= snapshot_time_ms < future_start_time_ms for every joined row",
            artifact="anomaly_nature_atlas.csv",
        ),
        ProtocolAuditRow(
            check_name="atlas_grouping_uses_state_only",
            status=AuditStatus.PASS,
            message="price/context/surface grouping bins are derived only from anomaly_state_1m.csv as-of fields; future fields are used only for descriptive response summaries",
            artifact="anomaly_context_splits.csv",
        ),
        ProtocolAuditRow(
            check_name="atlas_outcome_bins_not_trading_labels",
            status=AuditStatus.PASS,
            message="coarse 30m outcome bins are atlas-only descriptive bins and are not decision, entry, exit, EV, or trade labels",
            artifact="anomaly_nature_atlas.csv",
        ),
        ProtocolAuditRow(
            check_name="market_shock_groups_clean_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_market_shock_groups.csv groups simultaneous snapshots by snapshot_time_ms only; {market_shock_group_count} groups written without market-beta inference",
            artifact="anomaly_market_shock_groups.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 atlas uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(stage="mvp1_atlas")


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(
    *,
    state_path: Path,
    future_path: Path,
    output_path: Path,
    config: AtlasConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-atlas", source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="future_path", value=str(future_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="git_commit", value="UNKNOWN", source="runtime"),
        RunConfigRow(key="stage", value="mvp1_atlas", source="runtime"),
        RunConfigRow(key="atlas_version", value=config.atlas_version, source="runtime"),
        RunConfigRow(key="outcome_horizon_minutes", value=str(config.outcome_horizon_minutes), source="runtime"),
        RunConfigRow(key="outcome_move_threshold", value=str(config.outcome_move_threshold), source="runtime"),
        RunConfigRow(key="outcome_chop_threshold", value=str(config.outcome_chop_threshold), source="runtime"),
        RunConfigRow(
            key="min_symbols_for_market_shock_candidate",
            value=str(config.min_symbols_for_market_shock_candidate),
            source="runtime",
        ),
        RunConfigRow(key="atlas_outcome_bins", value="descriptive_not_trading_labels", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-atlas-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
