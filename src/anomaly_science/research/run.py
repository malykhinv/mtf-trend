from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from anomaly_science.atlas import run_mvp1_atlas
from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_manifest
from anomaly_science.artifacts.writer import write_csv_artifact_with_aliases
from anomaly_science.audit import build_independent_forensic_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, RunConfigRow
from anomaly_science.cache_export import CacheMvp1CsvExportConfig, export_cache_to_mvp1_csv
from anomaly_science.controls import ControlsConfig, run_mvp1_controls
from anomaly_science.data import run_mvp1_data_audit
from anomaly_science.decision import ExpectedValueConfig, run_mvp1_expected_value
from anomaly_science.events.run import run_mvp1_events
from anomaly_science.features import run_mvp1_feature_matrix, run_mvp1_features
from anomaly_science.future import run_mvp1_future
from anomaly_science.labels import run_mvp1_labels
from anomaly_science.prediction import WalkForwardPredictionConfig, run_mvp1_prediction
from anomaly_science.simulation import TradeSimulationConfig, run_mvp1_trade_simulation
from anomaly_science.state import run_mvp1_state
from anomaly_science.strategy.metadata import active_strategy_h_max_minutes, strategy_metadata_run_config_rows
from anomaly_science.strategy.registry import get_strategy
from anomaly_science.validation import run_mvp1_holdout_governance


DEFAULT_RESEARCH_OUTPUT_ROOT = Path(".output/results/research_runs")


@dataclass(frozen=True, slots=True)
class ResearchRunConfig:
    strategy_name: str
    cache_dir: Path
    days: int | None = None
    output_root: Path = DEFAULT_RESEARCH_OUTPUT_ROOT
    research_mode: Literal["is", "frozen_holdout"] = "is"
    holdout_days: int = 60
    protocol_freeze_id: str = ""

    def __post_init__(self) -> None:
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if self.days is not None and self.days <= 0:
            raise ValueError("days must be positive when provided")
        if self.research_mode not in {"is", "frozen_holdout"}:
            raise ValueError("research_mode must be 'is' or 'frozen_holdout'")
        if self.holdout_days <= 0:
            raise ValueError("holdout_days must be positive")
        if self.research_mode == "frozen_holdout" and not self.protocol_freeze_id:
            raise ValueError("protocol_freeze_id is required for frozen_holdout mode")


def run_research_pipeline(config: ResearchRunConfig) -> Path:
    strategy = get_strategy(config.strategy_name)
    run_dir = config.output_root / _run_id(strategy_name=config.strategy_name)
    input_dir = run_dir / "input"
    stages_dir = run_dir / "stages"

    export_cache_to_mvp1_csv(
        CacheMvp1CsvExportConfig(
            cache_dir=config.cache_dir,
            out_dir=input_dir,
            days=config.days,
        )
    )
    full_start_date, full_end_date = _input_date_range(input_dir / "candles_1m.csv")
    protocol_freeze_id = config.protocol_freeze_id or _protocol_freeze_id(
        strategy_name=config.strategy_name,
        start_date=full_start_date,
        end_date=full_end_date,
    )
    governance_dir = run_mvp1_holdout_governance(
        out_dir=stages_dir / "holdout_governance",
        start_date=full_start_date,
        end_date=full_end_date,
        protocol_freeze_id=protocol_freeze_id,
        holdout_days=config.holdout_days,
        research_mode=config.research_mode,
        holdout_access_artifact="run-research downstream input boundary",
    )
    research_start_date, research_end_date = _apply_holdout_lock_to_input(
        input_dir=input_dir,
        full_start_date=full_start_date,
        full_end_date=full_end_date,
        holdout_days=config.holdout_days,
        research_mode=config.research_mode,
    )

    run_mvp1_data_audit(input_dir=input_dir, out_dir=stages_dir / "data_audit")
    events_dir = run_mvp1_events(input_dir=input_dir, out_dir=stages_dir / "events")
    state_dir = run_mvp1_state(
        input_dir=input_dir,
        events_path=events_dir / "strategy_events.csv",
        out_dir=stages_dir / "state",
    )
    future_dir = run_mvp1_future(
        input_dir=input_dir,
        state_path=state_dir / "strategy_state_1m.csv",
        out_dir=stages_dir / "future",
    )
    run_mvp1_features(out_dir=stages_dir / "features")
    feature_matrix_dir = run_mvp1_feature_matrix(
        input_dir=input_dir,
        state_path=state_dir / "strategy_state_1m.csv",
        out_dir=stages_dir / "feature_matrix",
    )
    run_mvp1_atlas(
        state_path=state_dir / "strategy_state_1m.csv",
        future_path=future_dir / "strategy_future_paths.csv",
        feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
        out_dir=stages_dir / "atlas",
    )
    labels_dir = run_mvp1_labels(
        state_path=state_dir / "strategy_state_1m.csv",
        future_path=future_dir / "strategy_future_paths.csv",
        out_dir=stages_dir / "labels",
    )
    prediction_dir = run_mvp1_prediction(
        state_path=state_dir / "strategy_state_1m.csv",
        labels_path=labels_dir / "strategy_outcome_labels.csv",
        feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
        out_dir=stages_dir / "prediction",
        config=WalkForwardPredictionConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    run_mvp1_controls(
        state_path=state_dir / "strategy_state_1m.csv",
        labels_path=labels_dir / "strategy_outcome_labels.csv",
        feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
        out_dir=stages_dir / "controls",
        config=ControlsConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    ev_dir = run_mvp1_expected_value(
        state_path=state_dir / "strategy_state_1m.csv",
        labels_path=labels_dir / "strategy_outcome_labels.csv",
        predictions_path=prediction_dir / "strategy_oos_predictions.csv",
        out_dir=stages_dir / "expected_value",
        config=ExpectedValueConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    run_mvp1_trade_simulation(
        input_dir=input_dir,
        decision_timing_path=ev_dir / "strategy_decision_timing.csv",
        out_dir=stages_dir / "simulation",
        config=TradeSimulationConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    _write_research_run_manifest(
        run_dir=run_dir,
        config=config,
        start_date=research_start_date,
        end_date=research_end_date,
        full_start_date=full_start_date,
        full_end_date=full_end_date,
        governance_dir=governance_dir,
        protocol_freeze_id=protocol_freeze_id,
        forensic_audit_dir=stages_dir / "forensic_audit",
        forensic_status="PENDING",
        forensic_fail_count=-1,
        forensic_warn_count=-1,
    )
    forensic_audit_dir, forensic_status, forensic_fail_count, forensic_warn_count, forensic_failed_checks = _write_forensic_audit(run_dir)
    _write_summary(
        run_dir=run_dir,
        config=config,
        start_date=research_start_date,
        end_date=research_end_date,
        governance_dir=governance_dir,
        protocol_freeze_id=protocol_freeze_id,
        forensic_audit_dir=forensic_audit_dir,
        forensic_status=forensic_status,
        forensic_fail_count=forensic_fail_count,
        forensic_warn_count=forensic_warn_count,
    )
    _write_research_run_manifest(
        run_dir=run_dir,
        config=config,
        start_date=research_start_date,
        end_date=research_end_date,
        full_start_date=full_start_date,
        full_end_date=full_end_date,
        governance_dir=governance_dir,
        protocol_freeze_id=protocol_freeze_id,
        forensic_audit_dir=forensic_audit_dir,
        forensic_status=forensic_status,
        forensic_fail_count=forensic_fail_count,
        forensic_warn_count=forensic_warn_count,
    )
    if forensic_fail_count:
        raise RuntimeError(
            f"independent forensic protocol audit failed for {run_dir}: "
            f"{forensic_fail_count} FAIL row(s): {forensic_failed_checks}"
        )
    return run_dir



def _write_research_run_manifest(
    *,
    run_dir: Path,
    config: ResearchRunConfig,
    start_date: date,
    end_date: date,
    full_start_date: date,
    full_end_date: date,
    governance_dir: Path,
    protocol_freeze_id: str,
    forensic_audit_dir: Path,
    forensic_status: str,
    forensic_fail_count: int,
    forensic_warn_count: int,
) -> None:
    strategy = get_strategy(config.strategy_name)
    manifest_path = run_dir / "artifact_manifest.json"
    active_h_max = active_strategy_h_max_minutes((config.strategy_name,))
    extra_config = {
        "strategy_name": config.strategy_name,
        "research_mode": config.research_mode,
        "holdout_days": config.holdout_days,
        "protocol_freeze_id": protocol_freeze_id,
        "target_horizon_minutes": strategy.metadata.horizon_minutes,
        "active_h_max_minutes": active_h_max,
    }
    rows: list[RunConfigRow] = []
    rows.extend(strategy_metadata_run_config_rows(strategy))
    rows.extend(
        [
            RunConfigRow(key="run_dir", value=str(run_dir), source="run_research"),
            RunConfigRow(key="cache_dir", value=str(config.cache_dir), source="run_research"),
            RunConfigRow(key="days", value="" if config.days is None else str(config.days), source="run_research"),
            RunConfigRow(key="research_start_date", value=start_date.isoformat(), source="run_research"),
            RunConfigRow(key="research_end_date", value=end_date.isoformat(), source="run_research"),
            RunConfigRow(key="full_input_start_date", value=full_start_date.isoformat(), source="run_research"),
            RunConfigRow(key="full_input_end_date", value=full_end_date.isoformat(), source="run_research"),
            RunConfigRow(key="research_mode", value=config.research_mode, source="run_research"),
            RunConfigRow(key="holdout_days", value=str(config.holdout_days), source="run_research"),
            RunConfigRow(key="protocol_freeze_id", value=protocol_freeze_id, source="run_research"),
            RunConfigRow(key="holdout_governance_dir", value=str(governance_dir), source="run_research"),
            RunConfigRow(key="forensic_audit_dir", value=str(forensic_audit_dir), source="run_research"),
            RunConfigRow(key="forensic_audit_status", value=forensic_status, source="run_research"),
            RunConfigRow(key="forensic_audit_fail_count", value=str(forensic_fail_count), source="run_research"),
            RunConfigRow(key="forensic_audit_warn_count", value=str(forensic_warn_count), source="run_research"),
            RunConfigRow(key="target_horizon_minutes", value=str(strategy.metadata.horizon_minutes), source="run_research"),
            RunConfigRow(key="active_h_max_minutes", value=str(active_h_max), source="run_research"),
            RunConfigRow(key="artifact_manifest_path", value=str(manifest_path), source="run_research"),
            RunConfigRow(key="methodology_gap_ledger_status", value=_methodology_gap_ledger_status(), source="research_ledger"),
        ]
    )
    rows.extend(
        runtime_reproducibility_rows(
            data_paths=(run_dir / "input",),
            config=asdict(config),
            extra_config=extra_config,
        )
    )
    written = write_csv_artifact_with_aliases(
        run_dir / "strategy_run_config.csv",
        rows,
        get_artifact_schema("strategy_run_config.csv"),
    )
    artifact_paths = _collect_research_run_artifacts(run_dir=run_dir, exclude={manifest_path})
    for path in written:
        if path not in artifact_paths:
            artifact_paths.append(path)
    write_manifest(
        manifest_path,
        build_manifest(run_id=run_dir.name, artifact_paths=artifact_paths, root=run_dir),
    )


def _collect_research_run_artifacts(*, run_dir: Path, exclude: set[Path]) -> list[Path]:
    excluded = {path.resolve() for path in exclude}
    artifacts = []
    for path in sorted(run_dir.rglob("*"), key=lambda item: str(item.relative_to(run_dir))):
        if not path.is_file():
            continue
        if path.resolve() in excluded:
            continue
        if path.suffix.lower() not in {".csv", ".json"}:
            continue
        artifacts.append(path)
    return artifacts


def _methodology_gap_ledger_status() -> str:
    ledger_path = Path("research/METHODOLOGY_GAP_LEDGER.md")
    if not ledger_path.exists():
        return "UNKNOWN:missing research/METHODOLOGY_GAP_LEDGER.md"
    text = ledger_path.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("|") and "| :---" not in line]
    missing_count = sum(1 for line in rows if "| MISSING |" in line)
    partial_count = sum(1 for line in rows if "| PARTIAL |" in line)
    return f"MISSING={missing_count};PARTIAL={partial_count}"

def _write_summary(
    *,
    run_dir: Path,
    config: ResearchRunConfig,
    start_date: date,
    end_date: date,
    governance_dir: Path,
    protocol_freeze_id: str,
    forensic_audit_dir: Path,
    forensic_status: str,
    forensic_fail_count: int,
    forensic_warn_count: int,
) -> None:
    candles = pd.read_csv(run_dir / "input" / "candles_1m.csv", usecols=["open_time_ms"])
    min_time = int(candles["open_time_ms"].min()) if not candles.empty else 0
    max_time = int(candles["open_time_ms"].max()) if not candles.empty else 0
    lines = [
        "key,value",
        f"strategy_name,{config.strategy_name}",
        f"days,{'' if config.days is None else config.days}",
        f"cache_dir,{config.cache_dir}",
        f"run_dir,{run_dir}",
        f"input_min_open_time_ms,{min_time}",
        f"input_max_open_time_ms,{max_time}",
        f"research_start_date,{start_date.isoformat()}",
        f"research_end_date,{end_date.isoformat()}",
        f"research_mode,{config.research_mode}",
        f"holdout_days,{config.holdout_days}",
        f"protocol_freeze_id,{protocol_freeze_id}",
        f"holdout_governance_dir,{governance_dir}",
        f"forensic_audit_dir,{forensic_audit_dir}",
        f"forensic_audit_status,{forensic_status}",
        f"forensic_audit_fail_count,{forensic_fail_count}",
        f"forensic_audit_warn_count,{forensic_warn_count}",
    ]
    (run_dir / "research_run_summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_forensic_audit(run_dir: Path) -> tuple[Path, str, int, int, str]:
    rows = build_independent_forensic_audit_rows(run_dir)
    forensic_audit_dir = run_dir / "stages" / "forensic_audit"
    write_csv_artifact_with_aliases(
        forensic_audit_dir / "strategy_protocol_audit.csv",
        rows,
        get_artifact_schema("strategy_protocol_audit.csv"),
    )

    fail_count = sum(1 for row in rows if row.status is AuditStatus.FAIL)
    warn_count = sum(1 for row in rows if row.status is AuditStatus.WARN)
    status = "FAIL" if fail_count else "WARN" if warn_count else "PASS"
    failed_checks = ", ".join(row.check_name for row in rows if row.status is AuditStatus.FAIL)
    return forensic_audit_dir, status, fail_count, warn_count, failed_checks


def _apply_holdout_lock_to_input(
    *,
    input_dir: Path,
    full_start_date: date,
    full_end_date: date,
    holdout_days: int,
    research_mode: str,
) -> tuple[date, date]:
    final_holdout_start = _final_holdout_start_date(
        start_date=full_start_date,
        end_date=full_end_date,
        holdout_days=holdout_days,
    )
    if research_mode == "frozen_holdout":
        return full_start_date, full_end_date
    if research_mode != "is":
        raise ValueError("research_mode must be 'is' or 'frozen_holdout'")

    research_end_date = final_holdout_start - timedelta(days=1)
    if research_end_date < full_start_date:
        raise ValueError(
            "IS research mode has no non-holdout rows: "
            f"full_start_date={full_start_date.isoformat()} "
            f"full_end_date={full_end_date.isoformat()} holdout_days={holdout_days}. "
            "Use frozen_holdout mode with an explicit protocol_freeze_id to run on this period."
        )
    _filter_input_csv_by_end_date(input_dir / "candles_1m.csv", time_column="open_time_ms", end_date=research_end_date)
    _filter_input_csv_by_end_date(input_dir / "candles_5m.csv", time_column="open_time_ms", end_date=research_end_date)
    _filter_input_csv_by_end_date(input_dir / "open_interest_5m.csv", time_column="timestamp_ms", end_date=research_end_date)
    return _input_date_range(input_dir / "candles_1m.csv")


def _filter_input_csv_by_end_date(path: Path, *, time_column: str, end_date: date) -> None:
    frame = pd.read_csv(path)
    if frame.empty:
        frame.to_csv(path, index=False)
        return
    if time_column not in frame.columns:
        raise ValueError(f"{path} is missing required time column {time_column!r}")
    end_exclusive_ms = int(
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp() * 1000
    )
    filtered = frame[frame[time_column].astype("int64") < end_exclusive_ms].copy()
    if path.name == "candles_1m.csv" and filtered.empty:
        raise ValueError(f"holdout lock removed all rows from required input artifact: {path}")
    filtered.to_csv(path, index=False)


def _final_holdout_start_date(*, start_date: date, end_date: date, holdout_days: int) -> date:
    if holdout_days <= 0:
        raise ValueError("holdout_days must be positive")
    return max(start_date, end_date - timedelta(days=holdout_days - 1))


def _input_date_range(candles_path: Path) -> tuple[date, date]:
    candles = pd.read_csv(candles_path, usecols=["open_time_ms"])
    if candles.empty:
        raise ValueError(f"cannot derive research date range from empty candles file: {candles_path}")
    timestamps = pd.to_datetime(candles["open_time_ms"].astype("int64"), unit="ms", utc=True)
    return timestamps.min().date(), timestamps.max().date()


def _protocol_freeze_id(*, strategy_name: str, start_date: date, end_date: date) -> str:
    return f"run_research_{strategy_name}_{start_date.isoformat()}_{end_date.isoformat()}_protocol_freeze_v1"


def _run_id(*, strategy_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}_{strategy_name}"
