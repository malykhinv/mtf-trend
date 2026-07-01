from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.regimes.builder import build_causal_regime_atlas
from anomaly_science.regimes.config import CausalRegimeAtlasConfig
from anomaly_science.regimes.contracts import (
    RegimeControlRow,
    RegimeEvidenceRow,
    RegimeScreeningRow,
    RegimeStabilityRow,
)


class RegimeAtlasRunError(RuntimeError):
    """Raised when reproducible regime-atlas artifacts cannot be produced."""


def run_causal_regime_atlas(
    *,
    input_path: Path,
    out_dir: Path,
    config: CausalRegimeAtlasConfig,
    allow_dirty_development: bool = False,
) -> Path:
    """Run frozen-bin regime inference and write auditable development artifacts."""

    revision, dirty = _repository_state()
    if dirty and not allow_dirty_development:
        raise RegimeAtlasRunError(
            "regime atlas freeze requires a clean worktree; commit the protocol first"
        )
    frame = pd.read_parquet(input_path)
    result = build_causal_regime_atlas(frame, config)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = out_dir / "regime_discovery_log.csv"
    stability_path = out_dir / "regime_stability.csv"
    controls_path = out_dir / "regime_controls.csv"
    screening_path = out_dir / "regime_screening.csv"
    frozen_path = out_dir / "frozen_regime_spec.json"
    holdout_path = out_dir / "holdout_access_log.csv"
    metadata_path = out_dir / "regime_atlas.metadata.json"

    _write_csv(
        evidence_path,
        [asdict(row) for row in result.evidence_rows],
        columns=_field_names(RegimeEvidenceRow),
    )
    _write_csv(
        stability_path,
        [asdict(row) for row in result.stability_rows],
        columns=_field_names(RegimeStabilityRow),
    )
    _write_csv(
        controls_path,
        [asdict(row) for row in result.control_rows],
        columns=_field_names(RegimeControlRow),
    )
    _write_csv(
        screening_path,
        [asdict(row) for row in result.screening_rows],
        columns=_field_names(RegimeScreeningRow),
    )
    created_at = datetime.now(timezone.utc).isoformat()
    _write_csv(
        holdout_path,
        [
            {
                "accessed_at_utc": created_at,
                "dataset_role": "development_verification",
                "start_time_ms": config.verification_start_ms,
                "end_time_ms": config.verification_end_ms,
                "pristine_holdout_accessed": False,
                "purpose": "frozen coarse-regime replication before ML",
            }
        ],
        columns=(
            "accessed_at_utc",
            "dataset_role",
            "start_time_ms",
            "end_time_ms",
            "pristine_holdout_accessed",
            "purpose",
        ),
    )
    frozen_payload = {
        "freeze_status": "UNFROZEN_DIRTY_DEVELOPMENT" if dirty else "FROZEN",
        "created_at_utc": created_at,
        "protocol_freeze_id": config.protocol_freeze_id,
        "code_commit": revision,
        "working_tree_dirty": dirty,
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "config": asdict(config),
        "frozen_bins": [asdict(row) for row in result.frozen_bins],
        "frozen_interactions": [asdict(row) for row in result.frozen_interactions],
        "allowed_decision_rule": (
            "descriptive regime evidence only; no threshold tuning, trade admission, "
            "EV, or simulation policy"
        ),
        "post_hoc_changes_forbidden": True,
    }
    _write_json(frozen_path, frozen_payload)
    metadata = {
        "created_at_utc": created_at,
        "protocol_freeze_id": config.protocol_freeze_id,
        "code_commit": revision,
        "working_tree_dirty": dirty,
        "input_row_count": len(frame),
        "discovery_row_count": result.discovery_row_count,
        "verification_row_count": result.verification_row_count,
        "frozen_bin_count": len(result.frozen_bins),
        "registered_interaction_count": len(result.frozen_interactions),
        "evaluated_single_candidate_count": sum(
            row.hypothesis_kind == "single" for row in result.evidence_rows
        ),
        "evaluated_interaction_candidate_count": sum(
            row.hypothesis_kind == "interaction" for row in result.evidence_rows
        ),
        "discovery_candidate_count": len(result.evidence_rows),
        "registered_hypothesis_count": len(result.screening_rows),
        "development_replicated_count": sum(
            row.status == "DEVELOPMENT_REPLICATED" for row in result.evidence_rows
        ),
        "pristine_holdout_accessed": False,
        "scientific_scope": (
            "coarse causal online-state regime inference; development verification only; "
            "not calibrated prediction and not trading evidence"
        ),
    }
    _write_json(metadata_path, metadata)
    manifest = build_manifest(
        run_id="causal-regime-atlas-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=(
            evidence_path,
            stability_path,
            controls_path,
            screening_path,
            frozen_path,
            holdout_path,
            metadata_path,
        ),
        root=out_dir,
    )
    write_manifest(out_dir / "regime_atlas.manifest.json", manifest)
    return out_dir


def _write_csv(
    path: Path,
    rows: list[dict[str, object]],
    *,
    columns: tuple[str, ...],
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    pd.DataFrame(rows, columns=list(columns)).to_csv(temporary, index=False)
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _repository_state() -> tuple[str, bool]:
    root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RegimeAtlasRunError("cannot attest repository revision for regime freeze") from exc
    if not revision:
        raise RegimeAtlasRunError("git returned an empty repository revision")
    return revision, bool(status.strip())


def _field_names(row_type: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(row_type))


__all__ = ["RegimeAtlasRunError", "run_causal_regime_atlas"]
