from __future__ import annotations

import csv
import gc
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from anomaly_science.cache_export import CacheMvp1CsvExportConfig, export_cache_to_mvp1_csv, validate_reusable_mvp1_csv_input
from anomaly_science.data import run_mvp1_data_audit
from anomaly_science.events.run import run_mvp1_events
from anomaly_science.features import FeatureMatrixConfig, run_mvp1_feature_matrix, run_mvp1_features
from anomaly_science.future import run_mvp1_future
from anomaly_science.state import run_mvp1_state
from anomaly_science.research.run import _effective_holdout_days, _resolve_research_input_view
from anomaly_science.state.config import OnlineStateBuilderConfig
from anomaly_science.strategy.metadata import active_strategy_h_max_minutes
from anomaly_science.strategy.registry import get_strategy
from anomaly_science.progress import make_stderr_progress_callback

RESEARCH_DATASET_FORMAT_VERSION = "research_dataset_store_v1"
RESEARCH_DATASET_PHASE_CONTRACT_VERSION = "phase_cache_v1"
RESEARCH_DATASET_MANIFEST_NAME = "research_dataset_manifest.json"
RESEARCH_DATASET_PHASES_NAME = "research_dataset_phases.csv"
RESEARCH_DATASET_INPUT_DIR_NAME = "input"
RESEARCH_DATASET_STAGES_DIR_NAME = "stages"

PhaseName = Literal[
    "input",
    "data_audit",
    "events",
    "state",
    "future",
    "feature_catalog",
    "feature_matrix",
]
PHASE_ORDER: tuple[PhaseName, ...] = (
    "input",
    "data_audit",
    "events",
    "state",
    "future",
    "feature_catalog",
    "feature_matrix",
)


@dataclass(frozen=True, slots=True)
class ResearchDatasetBuildConfig:
    strategy_name: str
    cache_dir: Path
    out_dir: Path
    days: int | None = None
    max_phase: PhaseName = "input"
    research_mode: Literal["is", "frozen_holdout"] = "is"
    holdout_days: int = 60
    protocol_freeze_id: str = ""
    expected_days: int | None = None
    fail_on_missing_utc_days: bool = False
    fail_on_missing_1m_rows: bool = False
    fail_on_missing_open_interest: bool = False
    include_delivery_contracts: bool = False
    progress_every: int = 25
    parquet_use_threads: bool = False
    expected_event_lifetime_minutes: int = 60
    # Build only the per-event anchor snapshot (minutes_since_detection == 0) of the
    # online state, so state/future/feature_matrix carry one row per event instead
    # of the full per-minute window. The supervised gate (prediction/controls/EV)
    # already keeps only that anchor, so the supervised result is identical while
    # the heavy stages are ~H_max times smaller and faster. The per-minute online
    # window (needed by the decision-timing layer) is omitted in this mode.
    supervised_anchor_only: bool = False

    def __post_init__(self) -> None:
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if self.days is not None and self.days <= 0:
            raise ValueError("days must be positive when provided")
        if self.expected_days is not None and self.expected_days <= 0:
            raise ValueError("expected_days must be positive when provided")
        if self.progress_every < 0:
            raise ValueError("progress_every must be non-negative")
        if self.max_phase not in PHASE_ORDER:
            raise ValueError(f"max_phase must be one of {PHASE_ORDER}")
        if self.research_mode not in {"is", "frozen_holdout"}:
            raise ValueError("research_mode must be 'is' or 'frozen_holdout'")
        if self.holdout_days <= 0:
            raise ValueError("holdout_days must be positive")
        if self.research_mode == "frozen_holdout" and not self.protocol_freeze_id:
            raise ValueError("protocol_freeze_id is required for frozen_holdout mode")
        if self.expected_event_lifetime_minutes <= 0:
            raise ValueError("expected_event_lifetime_minutes must be positive")


@dataclass(frozen=True, slots=True)
class ResearchDatasetManifest:
    dataset_format_version: str
    phase_contract_version: str
    created_at_utc: str
    strategy_name: str
    cache_dir: str
    input_dir: str
    stages_dir: str
    requested_days: int | None
    expected_days: int | None
    research_mode: str
    holdout_days: int
    protocol_freeze_id: str
    effective_start_date: str | None
    effective_end_date: str | None
    max_input_time_ms: int | None
    max_phase: str
    phases: tuple[dict[str, Any], ...]

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ResearchDatasetManifest":
        phases = payload.get("phases")
        if not isinstance(phases, list):
            raise ValueError("research dataset manifest field 'phases' must be a list")
        return cls(
            dataset_format_version=str(payload.get("dataset_format_version", "")),
            phase_contract_version=str(payload.get("phase_contract_version", "")),
            created_at_utc=str(payload.get("created_at_utc", "")),
            strategy_name=str(payload.get("strategy_name", "")),
            cache_dir=str(payload.get("cache_dir", "")),
            input_dir=str(payload.get("input_dir", "")),
            stages_dir=str(payload.get("stages_dir", "")),
            requested_days=_optional_int(payload.get("requested_days")),
            expected_days=_optional_int(payload.get("expected_days")),
            research_mode=str(payload.get("research_mode", "")),
            holdout_days=int(payload.get("holdout_days", 0)),
            protocol_freeze_id=str(payload.get("protocol_freeze_id", "")),
            effective_start_date=_optional_str(payload.get("effective_start_date")),
            effective_end_date=_optional_str(payload.get("effective_end_date")),
            max_input_time_ms=_optional_int(payload.get("max_input_time_ms")),
            max_phase=str(payload.get("max_phase", "")),
            phases=tuple(_normalize_phase_payload(item) for item in phases),
        )


def build_research_dataset(config: ResearchDatasetBuildConfig) -> Path:
    """
    Build a reusable research dataset store up to an explicit phase boundary.

    This command intentionally stops before prediction, calibration, EV, simulation, and metrics.
    Those are experiment outputs, not reusable dataset truth.
    """
    get_strategy(config.strategy_name)
    dataset_dir = config.out_dir
    input_dir = dataset_dir / RESEARCH_DATASET_INPUT_DIR_NAME
    stages_dir = dataset_dir / RESEARCH_DATASET_STAGES_DIR_NAME
    dataset_dir.mkdir(parents=True, exist_ok=True)
    phases: list[dict[str, Any]] = []

    export_cache_to_mvp1_csv(
        CacheMvp1CsvExportConfig(
            cache_dir=config.cache_dir,
            out_dir=input_dir,
            days=config.days,
            expected_days=config.expected_days,
            fail_on_missing_utc_days=config.fail_on_missing_utc_days,
            fail_on_missing_1m_rows=config.fail_on_missing_1m_rows,
            fail_on_missing_open_interest=config.fail_on_missing_open_interest,
            include_delivery_contracts=config.include_delivery_contracts,
            progress_every=config.progress_every,
            parquet_use_threads=config.parquet_use_threads,
        )
    )
    validate_reusable_mvp1_csv_input(
        input_dir=input_dir,
        expected_cache_dir=config.cache_dir,
        expected_days=config.days,
    )
    cache_manifest = _read_json(input_dir / "cache_export_manifest.json")
    full_start_date, full_end_date = _input_date_range(input_dir / "candles_1m.csv")
    # Use the canonical run-research holdout/input-view contract so a store built
    # here exposes the identical IS window run-research expects (and the documented
    # weekly-WFA auto-scale), instead of a second, divergent boundary definition.
    effective_holdout_days = _effective_holdout_days(
        full_start_date=full_start_date,
        full_end_date=full_end_date,
        requested_holdout_days=config.holdout_days,
        research_mode=config.research_mode,
    )
    input_view = _resolve_research_input_view(
        full_start_date=full_start_date,
        full_end_date=full_end_date,
        holdout_days=effective_holdout_days,
        research_mode=config.research_mode,
    )
    protocol_freeze_id = config.protocol_freeze_id or _protocol_freeze_id(
        strategy_name=config.strategy_name,
        start_date=full_start_date,
        end_date=full_end_date,
    )
    phases.append(
        _phase_payload(
            phase_name="input",
            artifact_dir=RESEARCH_DATASET_INPUT_DIR_NAME,
            source_manifest="input/cache_export_manifest.json",
            boundary="mvp1_normalized_csv",
            rebuild_scope="cache_dir/days/export_config",
        )
    )
    _release_stage_memory()

    if _should_build(config.max_phase, "data_audit"):
        data_audit_dir = run_mvp1_data_audit(
            input_dir=input_dir,
            out_dir=stages_dir / "data_audit",
            max_input_time_ms=input_view.max_input_time_ms,
        )
        phases.append(
            _phase_payload(
                phase_name="data_audit",
                artifact_dir=_relpath(data_audit_dir, dataset_dir),
                source_manifest="stages/data_audit/artifact_manifest.json",
                boundary="data_quality_asof_input_view",
                rebuild_scope="input/research_mode/holdout_boundary",
            )
        )
        _release_stage_memory()

    events_dir: Path | None = None
    if _should_build(config.max_phase, "events"):
        events_dir = run_mvp1_events(
            input_dir=input_dir,
            out_dir=stages_dir / "events",
            strategy_name=config.strategy_name,
            max_input_time_ms=input_view.max_input_time_ms,
        )
        phases.append(
            _phase_payload(
                phase_name="events",
                artifact_dir=_relpath(events_dir, dataset_dir),
                source_manifest="stages/events/artifact_manifest.json",
                boundary="strategy_events_asof_input_view",
                rebuild_scope="input/strategy_version/research_mode/holdout_boundary",
            )
        )
        _release_stage_memory()

    state_dir: Path | None = None
    if _should_build(config.max_phase, "state"):
        if events_dir is None:
            events_dir = stages_dir / "events"
            _require_artifact(events_dir / "strategy_events.csv", phase="events")
        state_dir = run_mvp1_state(
            input_dir=input_dir,
            events_path=events_dir / "strategy_events.csv",
            out_dir=stages_dir / "state",
            config=OnlineStateBuilderConfig(
                max_state_minutes_after_detection=(
                    0 if config.supervised_anchor_only else active_strategy_h_max_minutes((config.strategy_name,))
                )
            ),
            max_input_time_ms=input_view.max_input_time_ms,
            progress_callback=make_stderr_progress_callback(stage_name="dataset state", unit="rows"),
        )
        phases.append(
            _phase_payload(
                phase_name="state",
                artifact_dir=_relpath(state_dir, dataset_dir),
                source_manifest="stages/state/artifact_manifest.json",
                boundary="online_state_asof_input_view",
                rebuild_scope="input/events/strategy_horizon/state_builder_version",
            )
        )
        _release_stage_memory()

    future_dir: Path | None = None
    if _should_build(config.max_phase, "future"):
        if state_dir is None:
            state_dir = stages_dir / "state"
            _require_artifact(state_dir / "strategy_state_1m.csv", phase="state")
        future_dir = run_mvp1_future(
            input_dir=input_dir,
            state_path=state_dir / "strategy_state_1m.csv",
            out_dir=stages_dir / "future",
            max_input_time_ms=input_view.max_input_time_ms,
            progress_callback=make_stderr_progress_callback(stage_name="dataset future", unit="rows"),
        )
        phases.append(
            _phase_payload(
                phase_name="future",
                artifact_dir=_relpath(future_dir, dataset_dir),
                source_manifest="stages/future/artifact_manifest.json",
                boundary="future_paths_for_closed_input_view",
                rebuild_scope="input/state/horizon_contract/future_builder_version",
            )
        )
        _release_stage_memory()

    if _should_build(config.max_phase, "feature_catalog"):
        feature_catalog_dir = run_mvp1_features(out_dir=stages_dir / "features")
        phases.append(
            _phase_payload(
                phase_name="feature_catalog",
                artifact_dir=_relpath(feature_catalog_dir, dataset_dir),
                source_manifest="stages/features/artifact_manifest.json",
                boundary="feature_catalog_contract",
                rebuild_scope="feature_contract_version",
            )
        )
        _release_stage_memory()

    if _should_build(config.max_phase, "feature_matrix"):
        if state_dir is None:
            state_dir = stages_dir / "state"
            _require_artifact(state_dir / "strategy_state_1m.csv", phase="state")
        feature_matrix_dir = run_mvp1_feature_matrix(
            input_dir=input_dir,
            state_path=state_dir / "strategy_state_1m.csv",
            out_dir=stages_dir / "feature_matrix",
            config=FeatureMatrixConfig(expected_event_lifetime_minutes=config.expected_event_lifetime_minutes),
            max_input_time_ms=input_view.max_input_time_ms,
            progress_callback=make_stderr_progress_callback(stage_name="dataset feature_matrix", unit="rows"),
        )
        phases.append(
            _phase_payload(
                phase_name="feature_matrix",
                artifact_dir=_relpath(feature_matrix_dir, dataset_dir),
                source_manifest="stages/feature_matrix/artifact_manifest.json",
                boundary="asof_feature_matrix_parquet_first",
                rebuild_scope="input/state/feature_schema/feature_builder_version",
            )
        )
        _release_stage_memory()

    manifest = {
        "dataset_format_version": RESEARCH_DATASET_FORMAT_VERSION,
        "phase_contract_version": RESEARCH_DATASET_PHASE_CONTRACT_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_name": config.strategy_name,
        "cache_dir": str(config.cache_dir),
        "input_dir": RESEARCH_DATASET_INPUT_DIR_NAME,
        "stages_dir": RESEARCH_DATASET_STAGES_DIR_NAME,
        "requested_days": config.days,
        "expected_days": config.expected_days,
        "research_mode": config.research_mode,
        "holdout_days": effective_holdout_days,
        "protocol_freeze_id": protocol_freeze_id,
        "effective_start_date": cache_manifest.get("effective_start_date") or full_start_date.isoformat(),
        "effective_end_date": input_view.end_date.isoformat(),
        "max_input_time_ms": input_view.max_input_time_ms,
        "max_phase": config.max_phase,
        "phases": phases,
    }
    _write_json_atomic(dataset_dir / RESEARCH_DATASET_MANIFEST_NAME, manifest)
    _write_phase_ledger(dataset_dir / RESEARCH_DATASET_PHASES_NAME, phases=tuple(phases))
    return dataset_dir


def validate_research_dataset_store(dataset_dir: str | Path) -> ResearchDatasetManifest:
    path = Path(dataset_dir)
    manifest_path = path / RESEARCH_DATASET_MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"research dataset manifest not found: {manifest_path}")
    manifest = ResearchDatasetManifest.from_json(_read_json(manifest_path))
    if manifest.dataset_format_version != RESEARCH_DATASET_FORMAT_VERSION:
        raise ValueError(
            f"unsupported research dataset format: {manifest.dataset_format_version!r}; "
            f"expected {RESEARCH_DATASET_FORMAT_VERSION!r}"
        )
    if manifest.phase_contract_version != RESEARCH_DATASET_PHASE_CONTRACT_VERSION:
        raise ValueError(
            f"unsupported research dataset phase contract: {manifest.phase_contract_version!r}; "
            f"expected {RESEARCH_DATASET_PHASE_CONTRACT_VERSION!r}"
        )
    if manifest.max_phase not in PHASE_ORDER:
        raise ValueError(f"research dataset has unsupported max_phase: {manifest.max_phase!r}")
    phase_names = {str(phase.get("phase_name", "")) for phase in manifest.phases}
    if "input" not in phase_names:
        raise ValueError("research dataset is missing required phase: input")
    validate_reusable_mvp1_csv_input(
        input_dir=_resolve_manifest_input_dir(dataset_dir=path, manifest=manifest),
        expected_cache_dir=Path(manifest.cache_dir),
        expected_days=manifest.requested_days,
    )
    for phase in manifest.phases:
        artifact_dir = phase.get("artifact_dir")
        if isinstance(artifact_dir, str) and artifact_dir:
            resolved = path / artifact_dir
            if not resolved.exists():
                raise FileNotFoundError(f"research dataset phase artifact dir not found: {resolved}")
    return manifest


def research_dataset_input_dir(dataset_dir: str | Path) -> Path:
    manifest = validate_research_dataset_store(dataset_dir)
    return _resolve_manifest_input_dir(dataset_dir=Path(dataset_dir), manifest=manifest)


def _should_build(max_phase: PhaseName, candidate: PhaseName) -> bool:
    return PHASE_ORDER.index(candidate) <= PHASE_ORDER.index(max_phase)


def _phase_payload(
    *,
    phase_name: str,
    artifact_dir: str,
    source_manifest: str,
    boundary: str,
    rebuild_scope: str,
) -> dict[str, Any]:
    return {
        "phase_name": phase_name,
        "status": "BUILT",
        "artifact_dir": artifact_dir,
        "source_manifest": source_manifest,
        "boundary": boundary,
        "is_reusable": True,
        "rebuild_scope": rebuild_scope,
    }


def _relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _require_artifact(path: Path, *, phase: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"required {phase} artifact does not exist: {path}")


def _resolve_manifest_input_dir(*, dataset_dir: Path, manifest: ResearchDatasetManifest) -> Path:
    input_dir = Path(manifest.input_dir)
    if input_dir.is_absolute():
        return input_dir
    return dataset_dir / input_dir


def _input_date_range(candles_path: Path) -> tuple[date, date]:
    min_ms: int | None = None
    max_ms: int | None = None
    with candles_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "open_time_ms" not in reader.fieldnames:
            raise ValueError(f"{candles_path} is missing open_time_ms")
        for row in reader:
            open_time_ms = int(row["open_time_ms"])
            min_ms = open_time_ms if min_ms is None else min(min_ms, open_time_ms)
            max_ms = open_time_ms if max_ms is None else max(max_ms, open_time_ms)
    if min_ms is None or max_ms is None:
        raise ValueError(f"{candles_path} has no candle rows")
    return _date_from_ms(min_ms), _date_from_ms(max_ms)


def _protocol_freeze_id(*, strategy_name: str, start_date: date, end_date: date) -> str:
    return f"{strategy_name}:{start_date.isoformat()}:{end_date.isoformat()}:dataset-store-v1"


def _date_from_ms(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _write_phase_ledger(path: Path, *, phases: tuple[dict[str, Any], ...]) -> None:
    fieldnames = [
        "phase_name",
        "status",
        "artifact_dir",
        "source_manifest",
        "boundary",
        "is_reusable",
        "rebuild_scope",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for phase in phases:
            writer.writerow({key: phase.get(key, "") for key in fieldnames})
    os.replace(tmp_path, path)


def _normalize_phase_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("research dataset phase entries must be objects")
    phase_name = str(payload.get("phase_name", ""))
    status = str(payload.get("status", ""))
    if not phase_name:
        raise ValueError("research dataset phase entry has empty phase_name")
    if status not in {"BUILT", "REUSED"}:
        raise ValueError(f"research dataset phase {phase_name!r} has unsupported status: {status!r}")
    return dict(payload)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _release_stage_memory() -> None:
    gc.collect()
