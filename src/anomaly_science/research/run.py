from __future__ import annotations

import csv
import gc
import json
import os
import shutil
import sys
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
from anomaly_science.cache_export import (
    CacheMvp1CsvExportConfig,
    export_cache_to_mvp1_csv,
    validate_reusable_mvp1_csv_input,
)
from anomaly_science.controls import ControlsConfig, run_mvp1_controls
from anomaly_science.data import run_mvp1_data_audit
from anomaly_science.decision import ExpectedValueConfig, run_mvp1_expected_value
from anomaly_science.events.run import run_mvp1_events
from anomaly_science.features import FeatureMatrixConfig, run_mvp1_feature_matrix, run_mvp1_features
from anomaly_science.future import run_mvp1_future
from anomaly_science.labels import run_mvp1_labels
from anomaly_science.prediction import WalkForwardPredictionConfig, run_mvp1_prediction
from anomaly_science.progress import HumanProgressReporter, ProgressCallback, ProgressUpdate
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
    prepared_input_dir: Path | None = None
    research_mode: Literal["is", "frozen_holdout"] = "is"
    holdout_days: int = 60
    protocol_freeze_id: str = ""
    dataset_store_dir: Path | None = None

    def __post_init__(self) -> None:
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if self.days is not None and self.days <= 0:
            raise ValueError("days must be positive when provided")
        if self.research_mode not in {"is", "frozen_holdout"}:
            raise ValueError("research_mode must be 'is' or 'frozen_holdout'")
        if self.prepared_input_dir is not None and self.research_mode != "frozen_holdout":
            raise ValueError(
                "prepared_input_dir is only supported in frozen_holdout mode; "
                "IS mode must let run-research own the input view and final holdout boundary"
            )
        if self.prepared_input_dir is not None and self.dataset_store_dir is not None:
            raise ValueError("prepared_input_dir and dataset_store_dir are mutually exclusive")
        if self.holdout_days <= 0:
            raise ValueError("holdout_days must be positive")
        if self.research_mode == "frozen_holdout" and not self.protocol_freeze_id:
            raise ValueError("protocol_freeze_id is required for frozen_holdout mode")


@dataclass(frozen=True, slots=True)
class ResearchInputView:
    start_date: date
    end_date: date
    max_input_time_ms: int | None
    mode: Literal["full_input", "is_excludes_final_holdout"]


def run_research_pipeline(config: ResearchRunConfig) -> Path:
    strategy = get_strategy(config.strategy_name)
    run_dir = config.output_root / _run_id(strategy_name=config.strategy_name)
    input_dir = config.prepared_input_dir or (run_dir / "input")
    stages_dir = run_dir / "stages"
    timings = _StageTimingRecorder(
        run_dir / "strategy_stage_timings.csv",
        log_path=run_dir / "research_run.log",
    )

    reused_max_input_time_ms: int | None = None
    if config.dataset_store_dir is not None:
        with timings.stage("dataset_store_reuse"):
            input_dir, reused_max_input_time_ms = _link_dataset_store_into_run(
                config=config, run_dir=run_dir, stages_dir=stages_dir
            )
            _release_stage_memory()
    elif config.prepared_input_dir is None:
        with timings.stage("cache_export"):
            export_cache_to_mvp1_csv(
                CacheMvp1CsvExportConfig(
                    cache_dir=config.cache_dir,
                    out_dir=input_dir,
                    days=config.days,
                )
            )
            _release_stage_memory()
    else:
        with timings.stage("input_reuse_validation"):
            validate_reusable_mvp1_csv_input(
                input_dir=input_dir,
                expected_cache_dir=config.cache_dir,
                expected_days=config.days,
            )
            _write_reused_input_pointer(run_dir=run_dir, input_dir=input_dir)
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

    with timings.stage("research_input_view"):
        input_view = _resolve_research_input_view(
            full_start_date=full_start_date,
            full_end_date=full_end_date,
            holdout_days=effective_holdout_days,
            research_mode=config.research_mode,
        )
        research_start_date = input_view.start_date
        research_end_date = input_view.end_date
        if config.dataset_store_dir is not None and input_view.max_input_time_ms != reused_max_input_time_ms:
            raise RuntimeError(
                "reused dataset store input boundary does not match this run's research input view: "
                f"store max_input_time_ms={reused_max_input_time_ms} "
                f"run max_input_time_ms={input_view.max_input_time_ms}; "
                "rebuild the dataset store for these days/research-mode/holdout settings"
            )
        _write_research_input_view(run_dir=run_dir, input_view=input_view, input_dir=input_dir)
        _release_stage_memory()

    events_dir = stages_dir / "events"
    state_dir = stages_dir / "state"
    future_dir = stages_dir / "future"
    feature_matrix_dir = stages_dir / "feature_matrix"
    if config.dataset_store_dir is None:
        with timings.stage("data_audit"):
            run_mvp1_data_audit(
                input_dir=input_dir,
                out_dir=stages_dir / "data_audit",
                max_input_time_ms=input_view.max_input_time_ms,
            )
            _release_stage_memory()

        with timings.stage("events"):
            events_dir = run_mvp1_events(
                input_dir=input_dir,
                out_dir=stages_dir / "events",
                strategy_name=config.strategy_name,
                max_input_time_ms=input_view.max_input_time_ms,
            )
            _release_stage_memory()

        with timings.stage("state"):
            state_dir = run_mvp1_state(
                input_dir=input_dir,
                events_path=events_dir / "strategy_events.csv",
                out_dir=stages_dir / "state",
                config=_state_config_for_strategy(strategy_name=config.strategy_name),
                max_input_time_ms=input_view.max_input_time_ms,
                progress_callback=timings.progress_callback("state", unit="rows"),
            )
            _release_stage_memory()

        with timings.stage("future"):
            future_dir = run_mvp1_future(
                input_dir=input_dir,
                state_path=state_dir / "strategy_state_1m.csv",
                out_dir=stages_dir / "future",
                max_input_time_ms=input_view.max_input_time_ms,
                progress_callback=timings.progress_callback("future", unit="rows"),
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
                config=FeatureMatrixConfig(strategy_name=config.strategy_name),
                max_input_time_ms=input_view.max_input_time_ms,
                progress_callback=timings.progress_callback("feature_matrix", unit="rows"),
            )
            _release_stage_memory()

    with timings.stage("atlas"):
        run_mvp1_atlas(
            state_path=state_dir / "strategy_state_1m.csv",
            future_path=future_dir / "strategy_future_paths.csv",
            feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
            out_dir=stages_dir / "atlas",
            progress_callback=timings.progress_callback("atlas", unit="rows"),
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
            max_input_time_ms=input_view.max_input_time_ms,
        )
        _release_stage_memory()

    with timings.stage("rejection_funnel"):
        write_rejection_funnel(run_dir=run_dir, out_dir=stages_dir / "rejection_funnel")
        _release_stage_memory()

    with timings.stage("provisional_manifest"):
        _write_research_run_manifest(
            run_dir=run_dir,
            config=config,
            input_dir=input_dir,
            start_date=research_start_date,
            end_date=research_end_date,
            full_start_date=full_start_date,
            full_end_date=full_end_date,
            effective_holdout_days=effective_holdout_days,
            governance_dir=governance_dir,
            protocol_freeze_id=protocol_freeze_id,
            input_view=input_view,
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
            input_dir=input_dir,
            start_date=research_start_date,
            end_date=research_end_date,
            effective_holdout_days=effective_holdout_days,
            governance_dir=governance_dir,
            protocol_freeze_id=protocol_freeze_id,
            input_view=input_view,
            forensic_audit_dir=forensic_audit_dir,
            forensic_status=forensic_status,
            forensic_fail_count=forensic_fail_count,
            forensic_warn_count=forensic_warn_count,
        )

    _write_research_run_manifest(
        run_dir=run_dir,
        config=config,
        input_dir=input_dir,
        start_date=research_start_date,
        end_date=research_end_date,
        full_start_date=full_start_date,
        full_end_date=full_end_date,
        effective_holdout_days=effective_holdout_days,
        governance_dir=governance_dir,
        protocol_freeze_id=protocol_freeze_id,
        input_view=input_view,
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


def _input_boundary_mode(config: ResearchRunConfig) -> str:
    if config.dataset_store_dir is not None:
        return "reused_dataset_store"
    if config.prepared_input_dir is not None:
        return "reused_prepared_input"
    return "fresh_cache_export"


def _link_dataset_store_into_run(
    *,
    config: ResearchRunConfig,
    run_dir: Path,
    stages_dir: Path,
) -> tuple[Path, int | None]:
    """Hardlink a prebuilt research dataset store into this run directory.

    The deterministic heavy phases (input + data_audit/events/state/future/
    feature_matrix) are pure functions of cache snapshot, strategy and window, so
    a weekly walk-forward only needs to build them once. Hardlinking them into the
    run directory keeps every downstream stage, the rejection funnel and the
    independent forensic audit reading a single run tree with no data copy.
    """
    from anomaly_science.research.dataset_store import validate_research_dataset_store

    store_dir = Path(config.dataset_store_dir)  # type: ignore[arg-type]
    manifest = validate_research_dataset_store(store_dir)
    _require_dataset_store_match(config=config, manifest=manifest)

    input_dir = run_dir / "input"
    _hardlink_tree(store_dir / manifest.input_dir, input_dir)
    for phase_dir_name in ("data_audit", "events", "state", "future", "features", "feature_matrix"):
        source_phase_dir = store_dir / manifest.stages_dir / phase_dir_name
        if not source_phase_dir.is_dir():
            raise FileNotFoundError(
                f"research dataset store is missing required phase directory: {source_phase_dir}"
            )
        _hardlink_tree(source_phase_dir, stages_dir / phase_dir_name)
    return input_dir, manifest.max_input_time_ms


def _require_dataset_store_match(*, config: ResearchRunConfig, manifest) -> None:
    if manifest.max_phase != "feature_matrix":
        raise ValueError(
            "research dataset store must be built through the feature_matrix phase for run-research reuse; "
            f"found max_phase={manifest.max_phase!r}"
        )
    mismatches: list[str] = []
    if manifest.strategy_name != config.strategy_name:
        mismatches.append(f"strategy_name store={manifest.strategy_name!r} run={config.strategy_name!r}")
    if Path(manifest.cache_dir).resolve() != config.cache_dir.resolve():
        mismatches.append(f"cache_dir store={manifest.cache_dir!r} run={str(config.cache_dir)!r}")
    if manifest.requested_days != config.days:
        mismatches.append(f"days store={manifest.requested_days!r} run={config.days!r}")
    if manifest.research_mode != config.research_mode:
        mismatches.append(f"research_mode store={manifest.research_mode!r} run={config.research_mode!r}")
    if mismatches:
        raise ValueError("research dataset store does not match this run: " + "; ".join(mismatches))


def _hardlink_tree(source_dir: Path, dest_dir: Path) -> None:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"research dataset store source directory is missing: {source_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    for source_path in sorted(source_dir.rglob("*")):
        relative = source_path.relative_to(source_dir)
        target = dest_dir / relative
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        try:
            os.link(source_path, target)
        except OSError:
            # Cross-volume stores cannot be hardlinked; copy the identical bytes.
            shutil.copyfile(source_path, target)


def _write_reused_input_pointer(*, run_dir: Path, input_dir: Path) -> None:
    pointer_path = run_dir / "prepared_input_reuse.json"
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = input_dir / "cache_export_manifest.json"
    payload = {
        "boundary": "mvp1_normalized_csv",
        "mode": "reused_prepared_input",
        "input_dir": str(input_dir),
        "cache_export_manifest_path": str(manifest_path),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    tmp_path = pointer_path.with_suffix(pointer_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, pointer_path)


@dataclass(slots=True)
class _StageTimingRecorder:
    path: Path
    log_path: Path | None = None
    _rows: list[dict[str, str]] = field(default_factory=list, init=False)
    _resource_rows: list[dict[str, str]] = field(default_factory=list, init=False)
    _progress_by_stage: dict[str, tuple[int, int | None, str]] = field(default_factory=dict, init=False)

    @contextmanager
    def stage(self, stage_name: str) -> Iterator[None]:
        started_at = datetime.now(timezone.utc)
        self._write_live_log(stage_name=stage_name, status="START", occurred_at=started_at)
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
            duration_seconds = perf_counter() - start_counter
            self._rows.append(
                {
                    "stage_name": stage_name,
                    "status": status,
                    "started_at_utc": started_at.isoformat(),
                    "finished_at_utc": finished_at.isoformat(),
                    "duration_seconds": f"{duration_seconds:.6f}",
                    "notes": notes,
                }
            )
            write_csv_artifact_with_aliases(
                self.path,
                self._rows,
                get_artifact_schema("strategy_stage_timings.csv"),
            )
            self._record_stage_resource_usage(
                stage_name=stage_name,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                notes=notes,
            )
            self._write_live_log(
                stage_name=stage_name,
                status=status,
                occurred_at=finished_at,
                duration_seconds=duration_seconds,
                notes=notes,
            )

    def progress_callback(
        self,
        stage_name: str,
        *,
        total: int | None = None,
        unit: str = "items",
        min_interval_seconds: float = 30.0,
    ) -> ProgressCallback:
        reporter = HumanProgressReporter(
            stage_name=stage_name,
            total=total,
            unit=unit,
            min_interval_seconds=min_interval_seconds,
            sink=self._write_progress_log,
        )

        def _recording_update(update: ProgressUpdate) -> None:
            resolved_total = update.total if update.total is not None else total
            resolved_unit = update.unit or unit
            self._progress_by_stage[stage_name] = (max(0, int(update.done)), resolved_total, resolved_unit)
            reporter.update(update)

        return _recording_update

    def _record_stage_resource_usage(
        self,
        *,
        stage_name: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        duration_seconds: float,
        notes: str,
    ) -> None:
        progress_done, progress_total, progress_unit = self._progress_by_stage.get(stage_name, (0, None, ""))
        run_dir_size_bytes, file_count, largest_files = _summarize_directory(self.path.parent)
        self._resource_rows.append(
            {
                "stage_name": stage_name,
                "status": status,
                "started_at_utc": started_at.isoformat(),
                "finished_at_utc": finished_at.isoformat(),
                "duration_seconds": f"{duration_seconds:.6f}",
                "progress_done": str(progress_done),
                "progress_total": "" if progress_total is None else str(progress_total),
                "progress_unit": progress_unit,
                "process_rss_mb": _format_optional_mb(_process_rss_bytes()),
                "run_dir_size_mb": _format_mb(run_dir_size_bytes),
                "run_dir_file_count": str(file_count),
                "largest_files": largest_files,
                "notes": notes,
            }
        )
        write_csv_artifact_with_aliases(
            self.path.with_name("strategy_resource_usage.csv"),
            self._resource_rows,
            get_artifact_schema("strategy_resource_usage.csv"),
        )

    def _write_progress_log(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    def _write_live_log(
        self,
        *,
        stage_name: str,
        status: str,
        occurred_at: datetime,
        duration_seconds: float | None = None,
        notes: str = "",
    ) -> None:
        duration_part = "" if duration_seconds is None else f" duration={duration_seconds:.2f}s"
        notes_part = "" if not notes else f" notes={notes}"
        message = f"run-research stage {status} {stage_name} at={occurred_at.isoformat()}{duration_part}{notes_part}"
        print(message, file=sys.stderr, flush=True)
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")



def _summarize_directory(root: Path, *, largest_limit: int = 5) -> tuple[int, int, str]:
    if not root.exists():
        return 0, 0, ""
    total_bytes = 0
    file_count = 0
    largest: list[tuple[int, str]] = []
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        file_count += 1
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        largest.append((size, rel))
    largest.sort(reverse=True, key=lambda item: item[0])
    largest_text = ";".join(f"{rel}:{_format_mb(size)}MB" for size, rel in largest[:largest_limit])
    return total_bytes, file_count, largest_text


def _format_mb(size_bytes: int | float) -> str:
    return f"{float(size_bytes) / 1_048_576:.3f}"


def _format_optional_mb(size_bytes: int | None) -> str:
    return "" if size_bytes is None else _format_mb(size_bytes)


def _process_rss_bytes() -> int | None:
    if os.name == "nt":
        return _process_rss_bytes_windows()
    try:
        import resource
    except ImportError:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except (OSError, ValueError):
        return None
    rss = int(usage.ru_maxrss)
    if rss <= 0:
        return None
    # Linux reports KiB, macOS reports bytes.
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _process_rss_bytes_windows() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    try:
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    return int(counters.WorkingSetSize)

def _write_research_run_manifest(
    *,
    run_dir: Path,
    config: ResearchRunConfig,
    input_dir: Path,
    start_date: date,
    end_date: date,
    full_start_date: date,
    full_end_date: date,
    effective_holdout_days: int,
    governance_dir: Path,
    protocol_freeze_id: str,
    input_view: ResearchInputView,
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
        "input_view_mode": input_view.mode,
        "input_view_max_input_time_ms": input_view.max_input_time_ms,
    }
    rows: list[RunConfigRow] = []
    rows.extend(strategy_metadata_run_config_rows(strategy))
    rows.extend(
        [
            RunConfigRow(key="run_dir", value=str(run_dir), source="run_research"),
            RunConfigRow(key="cache_dir", value=str(config.cache_dir), source="run_research"),
            RunConfigRow(key="input_dir", value=str(input_dir), source="run_research"),
            RunConfigRow(
                key="prepared_input_dir",
                value="" if config.prepared_input_dir is None else str(config.prepared_input_dir),
                source="run_research",
            ),
            RunConfigRow(
                key="input_boundary_mode",
                value=_input_boundary_mode(config),
                source="run_research",
            ),
            RunConfigRow(key="input_view_mode", value=input_view.mode, source="run_research"),
            RunConfigRow(
                key="input_view_max_input_time_ms",
                value="" if input_view.max_input_time_ms is None else str(input_view.max_input_time_ms),
                source="run_research",
            ),
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
            data_paths=(input_dir,),
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
    input_dir: Path,
    start_date: date,
    end_date: date,
    effective_holdout_days: int,
    governance_dir: Path,
    protocol_freeze_id: str,
    input_view: ResearchInputView,
    forensic_audit_dir: Path,
    forensic_status: str,
    forensic_fail_count: int,
    forensic_warn_count: int,
) -> None:
    time_bounds = _csv_time_bounds(input_dir / "candles_1m.csv", time_column="open_time_ms")
    min_time, max_time = time_bounds if time_bounds is not None else (0, 0)
    lines = [
        "key,value",
        f"strategy_name,{config.strategy_name}",
        f"days,{'' if config.days is None else config.days}",
        f"cache_dir,{config.cache_dir}",
        f"run_dir,{run_dir}",
        f"input_dir,{input_dir}",
        f"prepared_input_dir,{'' if config.prepared_input_dir is None else config.prepared_input_dir}",
        f"input_boundary_mode,{_input_boundary_mode(config)}",
        f"input_view_mode,{input_view.mode}",
        f"input_view_max_input_time_ms,{'' if input_view.max_input_time_ms is None else input_view.max_input_time_ms}",
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


def _resolve_research_input_view(
    *,
    full_start_date: date,
    full_end_date: date,
    holdout_days: int,
    research_mode: str,
) -> ResearchInputView:
    final_holdout_start = _final_holdout_start_date(
        start_date=full_start_date,
        end_date=full_end_date,
        holdout_days=holdout_days,
    )
    if research_mode == "frozen_holdout":
        return ResearchInputView(
            start_date=full_start_date,
            end_date=full_end_date,
            max_input_time_ms=None,
            mode="full_input",
        )
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
    return ResearchInputView(
        start_date=full_start_date,
        end_date=research_end_date,
        max_input_time_ms=_end_exclusive_ms(research_end_date),
        mode="is_excludes_final_holdout",
    )


def _write_research_input_view(*, run_dir: Path, input_view: ResearchInputView, input_dir: Path) -> None:
    payload = {
        "boundary": "mvp1_normalized_csv",
        "mode": input_view.mode,
        "input_dir": str(input_dir),
        "research_start_date": input_view.start_date.isoformat(),
        "research_end_date": input_view.end_date.isoformat(),
        "max_input_time_ms": input_view.max_input_time_ms,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    path = run_dir / "research_input_view.json"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _end_exclusive_ms(end_date: date) -> int:
    return int(datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp() * 1000)

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
