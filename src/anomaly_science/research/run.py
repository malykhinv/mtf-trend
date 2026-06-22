from __future__ import annotations

import csv
import gc
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal

from anomaly_science.atlas import run_mvp1_atlas
from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_manifest
from anomaly_science.artifacts.writer import write_csv_artifact_with_aliases
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
from anomaly_science.rejection import write_rejection_funnel
from anomaly_science.simulation import TradeSimulationConfig, run_mvp1_trade_simulation
from anomaly_science.state import run_mvp1_state
from anomaly_science.state.config import OnlineStateBuilderConfig
from anomaly_science.strategy.metadata import active_strategy_h_max_minutes, strategy_metadata_run_config_rows
from anomaly_science.strategy.registry import get_strategy
from anomaly_science.validation import run_mvp1_holdout_governance


DEFAULT_RESEARCH_OUTPUT_ROOT = Path(".output/results/research_runs")
MIN_IS_RESEARCH_DAYS_FOR_WEEKLY_WFA = 8


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
    timings = _StageTimingRecorder(run_dir / "strategy_stage_timings.csv")

    with timings.stage("cache_export"):
        export_cache_to_mvp1_csv(
            CacheMvp1CsvExportConfig(
                cache_dir=config.cache_dir,
                out_dir=input_dir,
                days=config.days,
            )
        )
        _release_stage_memory()

    with timings.stage("input_date_range"):
        full_start_date, full_end_date = _input_date_range(input_dir / "candles_1m.csv")
        effective_holdout_days = _effective_holdout_days(
            full_start_date=full_start_date,
            full_end_date=full_end_date,
            requested_holdout_days=config.holdout_days,
            research_mode=config.research_mode,
        )
        protocol_freeze_id = config.protocol_freeze_id or _protocol_freeze_id(
            strategy_name=config.strategy_name,
            start_date=full_start_date,
            end_date=full_end_date,
        )

    with timings.stage("holdout_governance"):
        governance_dir = run_mvp1_holdout_governance(
            out_dir=stages_dir / "holdout_governance",
            start_date=full_start_date,
            end_date=full_end_date,
            protocol_freeze_id=protocol_freeze_id,
            holdout_days=effective_holdout_days,
            research_mode=config.research_mode,
            holdout_access_artifact="run-research downstream input boundary",
        )

    with timings.stage("holdout_lock"):
        research_start_date, research_end_date = _apply_holdout_lock_to_input(
            input_dir=input_dir,
            full_start_date=full_start_date,
            full_end_date=full_end_date,
            holdout_days=effective_holdout_days,
            research_mode=config.research_mode,
        )
        _release_stage_memory()

    with timings.stage("data_audit"):
        run_mvp1_data_audit(input_dir=input_dir, out_dir=stages_dir / "data_audit")
        _release_stage_memory()

    with timings.stage("events"):
        events_dir = run_mvp1_events(input_dir=input_dir, out_dir=stages_dir / "events", strategy_name=config.strategy_name)
        _release_stage_memory()

    with timings.stage("state"):
        state_dir = run_mvp1_state(
            input_dir=input_dir,
            events_path=events_dir / "strategy_events.csv",
            out_dir=stages_dir / "state",
            config=_state_config_for_strategy(strategy_name=config.strategy_name),
        )
        _release_stage_memory()

    with timings.stage("future"):
        future_dir = run_mvp1_future(
            input_dir=input_dir,
            state_path=state_dir / "strategy_state_1m.csv",
            out_dir=stages_dir / "future",
        )
        _release_stage_memory()

    with timings.stage("feature_catalog"):
        run_mvp1_features(out_dir=stages_dir / "features")
        _release_stage_memory()

    with timings.stage("feature_matrix"):
        feature_matrix_dir = run_mvp1_feature_matrix(
            input_dir=input_dir,
            state_path=state_dir / "strategy_state_1m.csv",
            out_dir=stages_dir / "feature_matrix",
        )
        _release_stage_memory()

    with timings.stage("atlas"):
        run_mvp1_atlas(
            state_path=state_dir / "strategy_state_1m.csv",
            future_path=future_dir / "strategy_future_paths.csv",
            feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
            out_dir=stages_dir / "atlas",
        )
        _release_stage_memory()

    with timings.stage("labels"):
        labels_dir = run_mvp1_labels(
            state_path=state_dir / "strategy_state_1m.csv",
            future_path=future_dir / "strategy_future_paths.csv",
            out_dir=stages_dir / "labels",
        )
        _release_stage_memory()

    with timings.stage("prediction"):
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
        _release_stage_memory()

    with timings.stage("controls"):
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
        _release_stage_memory()

    with timings.stage("expected_value"):
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
        _release_stage_memory()

    with timings.stage("simulation"):
        run_mvp1_trade_simulation(
            input_dir=input_dir,
            decision_timing_path=ev_dir / "strategy_decision_timing.csv",
            out_dir=stages_dir / "simulation",
            config=TradeSimulationConfig(
                strategy_name=config.strategy_name,
                target_horizon_minutes=strategy.metadata.horizon_minutes,
            ),
        )
        _release_stage_memory()

    with timings.stage("rejection_funnel"):
        write_rejection_funnel(run_dir=run_dir, out_dir=stages_dir / "rejection_funnel")
        _release_stage_memory()

    with timings.stage("provisional_manifest"):
        _write_research_run_manifest(
            run_dir=run_dir,
            config=config,
            start_date=research_start_date,
            end_date=research_end_date,
            full_start_date=full_start_date,
            full_end_date=full_end_date,
            effective_holdout_days=effective_holdout_days,
            governance_dir=governance_dir,
            protocol_freeze_id=protocol_freeze_id,
            forensic_audit_dir=stages_dir / "forensic_audit",
            forensic_status="PENDING",
            forensic_fail_count=-1,
            forensic_warn_count=-1,
        )

    with timings.stage("forensic_audit"):
        forensic_audit_dir, forensic_status, forensic_fail_count, forensic_warn_count, forensic_failed_checks = _write_forensic_audit(run_dir)

    with timings.stage("summary"):
        _write_summary(
            run_dir=run_dir,
            config=config,
            start_date=research_start_date,
            end_date=research_end_date,
            effective_holdout_days=effective_holdout_days,
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
        effective_holdout_days=effective_holdout_days,
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


def _release_stage_memory() -> None:
    gc.collect()



@dataclass(slots=True)
class _StageTimingRecorder:
    path: Path
    _rows: list[dict[str, str]] = field(default_factory=list, init=False)

    @contextmanager
    def stage(self, stage_name: str) -> Iterator[None]:
        started_at = datetime.now(timezone.utc)
        start_counter = perf_counter()
        status = "PASS"
        notes = ""
        try:
            yield
        except Exception as exc:
            status = "FAIL"
            notes = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            finished_at = datetime.now(timezone.utc)
            self._rows.append(
                {
                    "stage_name": stage_name,
                    "status": status,
                    "started_at_utc": started_at.isoformat(),
                    "finished_at_utc": finished_at.isoformat(),
                    "duration_seconds": f"{perf_counter() - start_counter:.6f}",
                    "notes": notes,
                }
            )
            write_csv_artifact_with_aliases(
                self.path,
                self._rows,
                get_artifact_schema("strategy_stage_timings.csv"),
            )


def _write_research_run_manifest(
    *,
    run_dir: Path,
    config: ResearchRunConfig,
    start_date: date,
    end_date: date,
    full_start_date: date,
    full_end_date: date,
    effective_holdout_days: int,
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
        "requested_holdout_days": config.holdout_days,
        "effective_holdout_days": effective_holdout_days,
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
            RunConfigRow(key="holdout_days", value=str(effective_holdout_days), source="run_research"),
            RunConfigRow(key="requested_holdout_days", value=str(config.holdout_days), source="run_research"),
            RunConfigRow(key="effective_holdout_days", value=str(effective_holdout_days), source="run_research"),
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
    effective_holdout_days: int,
    governance_dir: Path,
    protocol_freeze_id: str,
    forensic_audit_dir: Path,
    forensic_status: str,
    forensic_fail_count: int,
    forensic_warn_count: int,
) -> None:
    time_bounds = _csv_time_bounds(run_dir / "input" / "candles_1m.csv", time_column="open_time_ms")
    min_time, max_time = time_bounds if time_bounds is not None else (0, 0)
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
        f"holdout_days,{effective_holdout_days}",
        f"requested_holdout_days,{config.holdout_days}",
        f"effective_holdout_days,{effective_holdout_days}",
        f"protocol_freeze_id,{protocol_freeze_id}",
        f"holdout_governance_dir,{governance_dir}",
        f"forensic_audit_dir,{forensic_audit_dir}",
        f"forensic_audit_status,{forensic_status}",
        f"forensic_audit_fail_count,{forensic_fail_count}",
        f"forensic_audit_warn_count,{forensic_warn_count}",
    ]
    (run_dir / "research_run_summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_forensic_audit(run_dir: Path) -> tuple[Path, str, int, int, str]:
    from anomaly_science.audit import build_independent_forensic_audit_rows

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


def _state_config_for_strategy(*, strategy_name: str) -> OnlineStateBuilderConfig:
    return OnlineStateBuilderConfig(max_state_minutes_after_detection=active_strategy_h_max_minutes((strategy_name,)))


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


def _effective_holdout_days(
    *,
    full_start_date: date,
    full_end_date: date,
    requested_holdout_days: int,
    research_mode: str,
) -> int:
    if requested_holdout_days <= 0:
        raise ValueError("holdout_days must be positive")
    if full_end_date < full_start_date:
        raise ValueError("full_end_date must be >= full_start_date")
    if research_mode == "frozen_holdout":
        return requested_holdout_days
    if research_mode != "is":
        raise ValueError("research_mode must be 'is' or 'frozen_holdout'")

    total_days = (full_end_date - full_start_date).days + 1
    min_research_days = min(total_days - 1, MIN_IS_RESEARCH_DAYS_FOR_WEEKLY_WFA)
    if total_days <= requested_holdout_days:
        if total_days < 2:
            raise ValueError(
                "IS research mode requires at least 2 calendar days when the requested holdout covers the whole period"
            )
        return total_days - min_research_days
    if total_days - requested_holdout_days < MIN_IS_RESEARCH_DAYS_FOR_WEEKLY_WFA:
        return max(1, total_days - MIN_IS_RESEARCH_DAYS_FOR_WEEKLY_WFA)
    return requested_holdout_days


def _filter_input_csv_by_end_date(path: Path, *, time_column: str, end_date: date) -> None:
    end_exclusive_ms = int(
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp() * 1000
    )
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    kept_count = 0
    with path.open(encoding="utf-8-sig", newline="") as source_obj:
        reader = csv.DictReader(source_obj)
        fieldnames = list(reader.fieldnames or [])
        if time_column not in fieldnames:
            raise ValueError(f"{path} is missing required time column {time_column!r}")
        with tmp_path.open("w", encoding="utf-8-sig", newline="") as target_obj:
            writer = csv.DictWriter(target_obj, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            for row in reader:
                if int(row[time_column]) >= end_exclusive_ms:
                    continue
                writer.writerow(row)
                kept_count += 1
    if path.name == "candles_1m.csv" and kept_count == 0:
        tmp_path.unlink(missing_ok=True)
        raise ValueError(f"holdout lock removed all rows from required input artifact: {path}")
    os.replace(tmp_path, path)


def _final_holdout_start_date(*, start_date: date, end_date: date, holdout_days: int) -> date:
    if holdout_days <= 0:
        raise ValueError("holdout_days must be positive")
    return max(start_date, end_date - timedelta(days=holdout_days - 1))


def _input_date_range(candles_path: Path) -> tuple[date, date]:
    time_bounds = _csv_time_bounds(candles_path, time_column="open_time_ms")
    if time_bounds is None:
        raise ValueError(f"cannot derive research date range from empty candles file: {candles_path}")
    min_time, max_time = time_bounds
    return _utc_date_from_ms(min_time), _utc_date_from_ms(max_time)


def _csv_time_bounds(path: Path, *, time_column: str) -> tuple[int, int] | None:
    min_time: int | None = None
    max_time: int | None = None
    with path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        fieldnames = list(reader.fieldnames or [])
        if time_column not in fieldnames:
            raise ValueError(f"{path} is missing required time column {time_column!r}")
        for row in reader:
            value = int(row[time_column])
            min_time = value if min_time is None else min(min_time, value)
            max_time = value if max_time is None else max(max_time, value)
    if min_time is None or max_time is None:
        return None
    return min_time, max_time


def _utc_date_from_ms(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()


def _protocol_freeze_id(*, strategy_name: str, start_date: date, end_date: date) -> str:
    return f"run_research_{strategy_name}_{start_date.isoformat()}_{end_date.isoformat()}_protocol_freeze_v1"


def _run_id(*, strategy_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}_{strategy_name}"
