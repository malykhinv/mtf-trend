from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping

from anomaly_science.contracts.artifacts import MVP1_ARTIFACT_SCHEMAS, STRATEGY_ARTIFACT_ALIASES
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow


_CANONICAL_AUDIT_ARTIFACT = "strategy_protocol_audit.csv"


class ForensicAuditError(ValueError):
    """Raised when forensic audit inputs are invalid before artifact checks run."""


def build_independent_forensic_audit_rows(root_dir: str | Path) -> list[ProtocolAuditRow]:
    """Build independent protocol-audit rows by reading already-written artifacts.

    This layer is intentionally artifact-driven. It does not trust stage-local
    PASS rows as proof of methodology correctness; it re-reads CSV artifacts and
    checks schema, temporal, purge, horizon-identity, and alias consistency from
    their explicit fields.
    """
    root = Path(root_dir)
    if not root.exists():
        raise ForensicAuditError(f"forensic audit root does not exist: {root}")
    if not root.is_dir():
        raise ForensicAuditError(f"forensic audit root must be a directory: {root}")

    found = _index_known_csv_artifacts(root)
    rows: list[ProtocolAuditRow] = []
    rows.append(_schema_columns_row(found))
    rows.append(_root_run_manifest_completeness_row(root))
    rows.append(_temporal_contract_row(found))
    rows.append(_model_metadata_purge_row(found))
    rows.append(_prediction_oos_cutoff_row(found))
    rows.append(_prediction_model_horizon_identity_row(found))
    rows.append(_canonical_alias_consistency_row(root))
    rows.append(_stage_audit_fail_summary_row(found))
    rows.append(_forensic_gate_row(rows))
    return rows


def _index_known_csv_artifacts(root: Path) -> dict[str, tuple[Path, ...]]:
    indexed: dict[str, list[Path]] = {}
    for path in root.rglob("*.csv"):
        if path.name not in MVP1_ARTIFACT_SCHEMAS:
            continue
        indexed.setdefault(path.name, []).append(path)
    return {name: tuple(sorted(paths)) for name, paths in indexed.items()}


def _schema_columns_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for name, paths in sorted(found.items()):
        schema = MVP1_ARTIFACT_SCHEMAS[name]
        expected = list(schema.required_columns)
        for path in paths:
            checked += 1
            header = _read_csv_header(path)
            missing = [column for column in expected if column not in header]
            extra = [column for column in header if column not in expected]
            if missing or extra:
                failures.append(f"{path}: missing={missing}, extra={extra}")
    if failures:
        return _row(
            "forensic_artifact_schema_columns_verified",
            AuditStatus.FAIL,
            f"{len(failures)} schema mismatch(es): " + "; ".join(failures[:5]),
        )
    if checked == 0:
        return _row(
            "forensic_artifact_schema_columns_verified",
            AuditStatus.WARN,
            "no known CSV artifacts were found for independent schema verification",
        )
    return _row(
        "forensic_artifact_schema_columns_verified",
        AuditStatus.PASS,
        f"independently verified required/extra columns for {checked} known CSV artifact(s)",
    )



def _root_run_manifest_completeness_row(root: Path) -> ProtocolAuditRow:
    run_config_path = root / "strategy_run_config.csv"
    manifest_path = root / "artifact_manifest.json"
    if not run_config_path.exists():
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            f"missing root research run manifest: {run_config_path}",
            artifact="strategy_run_config.csv",
        )
    if not manifest_path.exists():
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            f"missing root artifact manifest: {manifest_path}",
            artifact="artifact_manifest.json",
        )
    required_keys = {
        "strategy_name",
        "strategy_version",
        "strategy_contract_version",
        "strategy_family",
        "target_horizon_minutes",
        "active_h_max_minutes",
        "research_mode",
        "holdout_days",
        "protocol_freeze_id",
        "research_start_date",
        "research_end_date",
        "forensic_audit_status",
        "data_snapshot_hash",
        "config_hash",
        "dependency_versions",
        "artifact_manifest_path",
        "methodology_gap_ledger_status",
    }
    rows = tuple(_read_csv_rows(run_config_path))
    keys = {row.get("key", "") for row in rows}
    missing = sorted(required_keys - keys)
    if missing:
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            "root strategy_run_config.csv is missing required reproducibility keys: " + ", ".join(missing),
            artifact="strategy_run_config.csv",
        )
    empty_required_values = sorted(
        row.get("key", "") for row in rows if row.get("key", "") in required_keys and row.get("value", "") == ""
    )
    if empty_required_values:
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            "root strategy_run_config.csv has empty required values: " + ", ".join(empty_required_values),
            artifact="strategy_run_config.csv",
        )
    return _row(
        "forensic_root_research_run_manifest_complete",
        AuditStatus.PASS,
        f"root research manifest has {len(required_keys)} required reproducibility key(s) and artifact_manifest.json is present",
        artifact="strategy_run_config.csv",
    )

def _temporal_contract_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for artifact_name in (
        "strategy_state_1m.csv",
        "strategy_feature_matrix.csv",
        "strategy_future_paths.csv",
        "strategy_outcome_labels.csv",
        "strategy_oos_predictions.csv",
        "strategy_decision_timing.csv",
        "strategy_trade_simulation.csv",
    ):
        for path in found.get(artifact_name, ()):  # canonical only is enough for forensic proof
            for row_index, row in enumerate(_read_csv_rows(path), start=2):
                if "snapshot_time_ms" not in row or "feature_cutoff_time_ms" not in row:
                    continue
                checked += 1
                snapshot = _to_int(row.get("snapshot_time_ms"))
                cutoff = _to_int(row.get("feature_cutoff_time_ms"))
                future_start = _to_int(row.get("future_start_time_ms")) if "future_start_time_ms" in row else None
                if snapshot is None or cutoff is None:
                    failures.append(f"{path}:{row_index} has non-integer snapshot/feature_cutoff")
                    continue
                if cutoff > snapshot:
                    failures.append(f"{path}:{row_index} feature_cutoff_time_ms={cutoff} > snapshot_time_ms={snapshot}")
                if future_start is not None and future_start <= snapshot:
                    failures.append(f"{path}:{row_index} future_start_time_ms={future_start} <= snapshot_time_ms={snapshot}")
    if failures:
        return _row(
            "forensic_temporal_contract_verified_from_artifacts",
            AuditStatus.FAIL,
            f"{len(failures)} temporal violation(s): " + "; ".join(failures[:5]),
        )
    if checked == 0:
        return _row(
            "forensic_temporal_contract_verified_from_artifacts",
            AuditStatus.WARN,
            "no temporal rows were available for independent artifact verification",
        )
    return _row(
        "forensic_temporal_contract_verified_from_artifacts",
        AuditStatus.PASS,
        f"verified feature_cutoff_time_ms <= snapshot_time_ms and future_start_time_ms > snapshot_time_ms for {checked} row(s)",
    )


def _model_metadata_purge_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for path in found.get("strategy_model_metadata.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            train_cutoff = _to_int(row.get("train_cutoff_time_ms"))
            freeze = _to_int(row.get("weekly_model_freeze_time_ms"))
            h_max = _to_int(row.get("active_h_max_minutes"))
            if train_cutoff is None or freeze is None or h_max is None:
                failures.append(f"{path}:{row_index} has non-integer train_cutoff/freeze/active_h_max")
                continue
            checked += 1
            required_cutoff = train_cutoff + h_max * 60_000
            if required_cutoff > freeze:
                failures.append(
                    f"{path}:{row_index} train_cutoff_time_ms + active_h_max_minutes exceeds weekly_model_freeze_time_ms "
                    f"({train_cutoff} + {h_max}m > {freeze})"
                )
    if failures:
        return _row(
            "forensic_model_metadata_purge_hmax_verified",
            AuditStatus.FAIL,
            f"{len(failures)} purge violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_model_metadata.csv",
        )
    if checked == 0:
        return _row(
            "forensic_model_metadata_purge_hmax_verified",
            AuditStatus.WARN,
            "no strategy_model_metadata.csv rows were available for independent purge verification",
            artifact="strategy_model_metadata.csv",
        )
    return _row(
        "forensic_model_metadata_purge_hmax_verified",
        AuditStatus.PASS,
        f"verified train_cutoff_time_ms + active_h_max_minutes <= weekly_model_freeze_time_ms for {checked} frozen model row(s)",
        artifact="strategy_model_metadata.csv",
    )


def _prediction_oos_cutoff_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for path in found.get("strategy_oos_predictions.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            snapshot = _to_int(row.get("snapshot_time_ms"))
            train_cutoff = _to_int(row.get("train_cutoff_time_ms"))
            if snapshot is None or train_cutoff is None:
                failures.append(f"{path}:{row_index} has non-integer snapshot/train_cutoff")
                continue
            checked += 1
            if train_cutoff >= snapshot:
                failures.append(f"{path}:{row_index} train_cutoff_time_ms={train_cutoff} >= snapshot_time_ms={snapshot}")
    if failures:
        return _row(
            "forensic_prediction_rows_are_oos_after_train_cutoff",
            AuditStatus.FAIL,
            f"{len(failures)} OOS cutoff violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_oos_predictions.csv",
        )
    if checked == 0:
        return _row(
            "forensic_prediction_rows_are_oos_after_train_cutoff",
            AuditStatus.WARN,
            "no prediction rows were available for independent OOS cutoff verification",
            artifact="strategy_oos_predictions.csv",
        )
    return _row(
        "forensic_prediction_rows_are_oos_after_train_cutoff",
        AuditStatus.PASS,
        f"verified train_cutoff_time_ms < snapshot_time_ms for {checked} OOS prediction row(s)",
        artifact="strategy_oos_predictions.csv",
    )


def _prediction_model_horizon_identity_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    metadata_by_key: dict[str, dict[str, str]] = {}
    duplicate_keys: list[str] = []
    failures: list[str] = []
    for path in found.get("strategy_model_metadata.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            key = row.get("model_key", "")
            if not key:
                failures.append(f"{path}:{row_index} has empty model_key")
                continue
            if key in metadata_by_key:
                duplicate_keys.append(key)
            metadata_by_key[key] = row
    if duplicate_keys:
        failures.append("duplicate model metadata keys: " + ", ".join(sorted(set(duplicate_keys))[:5]))

    checked = 0
    for path in found.get("strategy_oos_predictions.csv", ()):
        for row_index, prediction in enumerate(_read_csv_rows(path), start=2):
            key = prediction.get("model_key", "")
            metadata = metadata_by_key.get(key)
            if metadata is None:
                failures.append(f"{path}:{row_index} model_key={key!r} missing from strategy_model_metadata.csv")
                continue
            checked += 1
            for field_name in (
                "strategy_name",
                "strategy_version",
                "strategy_contract_version",
                "target_horizon_minutes",
                "target_label_column",
                "active_h_max_minutes",
            ):
                if prediction.get(field_name) != metadata.get(field_name):
                    failures.append(
                        f"{path}:{row_index} {field_name}={prediction.get(field_name)!r} differs from model metadata {metadata.get(field_name)!r}"
                    )
    if failures:
        return _row(
            "forensic_prediction_model_horizon_identity_consistent",
            AuditStatus.FAIL,
            f"{len(failures)} identity mismatch(es): " + "; ".join(failures[:5]),
            artifact="strategy_oos_predictions.csv",
        )
    if checked == 0:
        return _row(
            "forensic_prediction_model_horizon_identity_consistent",
            AuditStatus.WARN,
            "no joined prediction/model rows were available for independent horizon identity verification",
            artifact="strategy_oos_predictions.csv",
        )
    return _row(
        "forensic_prediction_model_horizon_identity_consistent",
        AuditStatus.PASS,
        f"verified prediction rows match frozen model metadata identity for {checked} row(s)",
        artifact="strategy_oos_predictions.csv",
    )


def _canonical_alias_consistency_row(root: Path) -> ProtocolAuditRow:
    failures: list[str] = []
    checked = 0
    for anomaly_name, strategy_name in sorted(STRATEGY_ARTIFACT_ALIASES.items()):
        anomaly_by_parent = {path.parent: path for path in root.rglob(anomaly_name)}
        strategy_by_parent = {path.parent: path for path in root.rglob(strategy_name)}
        if not anomaly_by_parent and not strategy_by_parent:
            continue
        checked += 1
        missing_strategy = sorted(set(anomaly_by_parent) - set(strategy_by_parent))
        missing_anomaly = sorted(set(strategy_by_parent) - set(anomaly_by_parent))
        if missing_strategy:
            failures.append(f"{anomaly_name}/{strategy_name} missing strategy aliases in: " + ", ".join(str(path) for path in missing_strategy[:5]))
        if missing_anomaly:
            failures.append(f"{anomaly_name}/{strategy_name} missing anomaly aliases in: " + ", ".join(str(path) for path in missing_anomaly[:5]))
        for parent in sorted(set(anomaly_by_parent) & set(strategy_by_parent)):
            anomaly_path = anomaly_by_parent[parent]
            strategy_path = strategy_by_parent[parent]
            if anomaly_path.read_bytes() != strategy_path.read_bytes():
                failures.append(f"{anomaly_path} content differs from {strategy_path}")
                continue
    if failures:
        return _row(
            "forensic_canonical_alias_artifacts_match",
            AuditStatus.FAIL,
            f"{len(failures)} alias mismatch(es): " + "; ".join(failures[:5]),
        )
    if checked == 0:
        return _row(
            "forensic_canonical_alias_artifacts_match",
            AuditStatus.WARN,
            "no canonical/alias artifact pairs were found for independent comparison",
        )
    return _row(
        "forensic_canonical_alias_artifacts_match",
        AuditStatus.PASS,
        f"verified {checked} canonical strategy_* artifact family alias pair(s)",
    )


def _stage_audit_fail_summary_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    fail_rows: list[str] = []
    for path in found.get("strategy_protocol_audit.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            checked += 1
            if row.get("status") == AuditStatus.FAIL.value:
                fail_rows.append(f"{path}:{row_index} {row.get('check_name', '')}")
    if fail_rows:
        return _row(
            "forensic_stage_protocol_audit_has_no_fail_rows",
            AuditStatus.FAIL,
            f"stage protocol audit contains {len(fail_rows)} FAIL row(s): " + "; ".join(fail_rows[:5]),
            artifact="strategy_protocol_audit.csv",
        )
    if checked == 0:
        return _row(
            "forensic_stage_protocol_audit_has_no_fail_rows",
            AuditStatus.WARN,
            "no stage strategy_protocol_audit.csv rows were found for FAIL summary",
            artifact="strategy_protocol_audit.csv",
        )
    return _row(
        "forensic_stage_protocol_audit_has_no_fail_rows",
        AuditStatus.PASS,
        f"scanned {checked} stage protocol audit row(s) and found no FAIL statuses",
        artifact="strategy_protocol_audit.csv",
    )


def _forensic_gate_row(rows: Iterable[ProtocolAuditRow]) -> ProtocolAuditRow:
    items = tuple(rows)
    fail_count = sum(1 for row in items if row.status is AuditStatus.FAIL)
    warn_count = sum(1 for row in items if row.status is AuditStatus.WARN)
    if fail_count:
        return _row(
            "forensic_protocol_interpretation_gate",
            AuditStatus.FAIL,
            f"independent forensic audit found {fail_count} FAIL row(s); downstream results must not be interpreted",
        )
    if warn_count:
        return _row(
            "forensic_protocol_interpretation_gate",
            AuditStatus.WARN,
            f"independent forensic audit found {warn_count} WARN row(s); artifacts are incomplete for full forensic proof",
        )
    return _row(
        "forensic_protocol_interpretation_gate",
        AuditStatus.PASS,
        "independent forensic audit found no FAIL or WARN rows",
    )


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.reader(file_obj)
        try:
            return [str(column) for column in next(reader)]
        except StopIteration:
            return []


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _row(check_name: str, status: AuditStatus, message: str, artifact: str = _CANONICAL_AUDIT_ARTIFACT) -> ProtocolAuditRow:
    return ProtocolAuditRow(check_name=check_name, status=status, message=message, artifact=artifact)
