from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from collections.abc import Callable
import json
import os
from pathlib import Path

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.phenotypes.builder import build_cross_fitted_phenotypes
from anomaly_science.phenotypes.composition import (
    PhenotypeCompositionConfig,
    build_phenotype_compositions,
)
from anomaly_science.phenotypes.config import CrossFittedPhenotypeConfig
from anomaly_science.phenotypes.memory import build_phenotype_followup_registry


def run_cross_fitted_phenotype_discovery(
    *,
    input_path: Path,
    out_dir: Path,
    config: CrossFittedPhenotypeConfig,
    allow_dirty_development: bool = False,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> Path:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"phenotype output must be absent or empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.read_parquet(input_path) if input_path.suffix.lower() == ".parquet" else pd.read_csv(input_path)
    result = build_cross_fitted_phenotypes(
        frame, config, progress_callback=progress_callback
    )
    paths: list[Path] = []
    for name, table in (
        ("phenotype_catalog.csv", result.catalog),
        ("phenotype_followup_registry.csv", result.followup_registry),
        ("phenotype_screening.csv", result.screening),
        ("phenotype_coverage.csv", result.coverage),
        ("phenotype_controls.csv", result.controls),
    ):
        path = out_dir / name
        _write_csv(path, table)
        paths.append(path)
    assignments_path = out_dir / "phenotype_assignments.parquet"
    _write_parquet(assignments_path, result.assignments)
    paths.append(assignments_path)
    rules_path = out_dir / "frozen_phenotype_rules.json"
    _write_json(
        rules_path,
        {
            "protocol_freeze_id": config.protocol_freeze_id,
            "rules": result.frozen_rules,
            "immutable_after_search": True,
        },
    )
    paths.append(rules_path)
    transform_path = out_dir / "phenotype_feature_transform.json"
    _write_json(transform_path, result.feature_transform)
    paths.append(transform_path)
    protocol_path = out_dir / "phenotype_protocol.json"
    _write_json(
        protocol_path,
        {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_freeze_id": config.protocol_freeze_id,
            "input_path": str(input_path.resolve()),
            "input_sha256": sha256_file(input_path),
            "working_tree_policy": (
                "UNFROZEN_DIRTY_DEVELOPMENT"
                if allow_dirty_development else "CLEAN_EVIDENCE_REQUIRED"
            ),
            "config": asdict(config),
            "search_row_count": result.search_row_count,
            "calibration_row_count": result.calibration_row_count,
            "verification_row_count": result.verification_row_count,
            "screened_leaf_count": len(result.screening),
            "cross_fold_candidate_count": result.candidate_count,
            "frozen_phenotype_count": result.frozen_count,
            "verified_phenotype_count": result.verified_count,
            "high_probability_verified_count": result.high_probability_verified_count,
            "scientific_scope": (
                "cross-fitted rule search; untouched calibration probability; later temporal "
                "verification; no EV, execution, or production claim"
            ),
        },
    )
    paths.append(protocol_path)
    manifest = build_manifest(
        run_id="cross-fitted-phenotypes-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=paths,
        root=out_dir,
    )
    write_manifest(out_dir / "phenotype.manifest.json", manifest)
    return out_dir


def run_phenotype_followup_registry(
    *,
    catalog_path: Path,
    out_dir: Path,
    config: CrossFittedPhenotypeConfig,
) -> Path:
    """Derive governed research memory without changing source classifications."""
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"follow-up output must be absent or empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = build_phenotype_followup_registry(pd.read_csv(catalog_path), config)
    registry_path = out_dir / "phenotype_followup_registry.csv"
    _write_csv(registry_path, registry)
    protocol_path = out_dir / "phenotype_followup_protocol.json"
    _write_json(
        protocol_path,
        {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_catalog_path": str(catalog_path.resolve()),
            "source_catalog_sha256": sha256_file(catalog_path),
            "source_protocol_freeze_id": config.protocol_freeze_id,
            "source_verification_end_utc": config.verification_end_utc,
            "registry_scope": "research memory and forward-study governance only",
            "changes_source_classification": False,
            "permits_viewed_period_retuning": False,
            "phenotype_count": len(registry),
        },
    )
    manifest = build_manifest(
        run_id="phenotype-followup-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=[registry_path, protocol_path],
        root=out_dir,
    )
    write_manifest(out_dir / "phenotype_followup.manifest.json", manifest)
    return out_dir


def run_phenotype_composition_discovery(
    *,
    input_path: Path,
    frozen_rules_path: Path,
    out_dir: Path,
    phenotype_config: CrossFittedPhenotypeConfig,
    composition_config: PhenotypeCompositionConfig,
    allow_dirty_development: bool = False,
) -> Path:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"composition output must be absent or empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.read_parquet(input_path) if input_path.suffix.lower() == ".parquet" else pd.read_csv(input_path)
    rule_payload = json.loads(frozen_rules_path.read_text(encoding="utf-8"))
    base_rules = tuple(rule_payload.get("rules", ()))
    if not base_rules or not bool(rule_payload.get("immutable_after_search")):
        raise ValueError("composition requires immutable frozen phenotype rules")
    if rule_payload.get("protocol_freeze_id") != phenotype_config.protocol_freeze_id:
        raise ValueError("frozen rules and phenotype config protocol ids differ")
    result = build_phenotype_compositions(
        frame, base_rules, phenotype_config, composition_config
    )
    paths: list[Path] = []
    for name, table in (
        ("phenotype_composition_catalog.csv", result.catalog),
        ("phenotype_composition_screening.csv", result.screening),
        ("phenotype_composition_controls.csv", result.controls),
        ("phenotype_composition_coverage.csv", result.coverage),
    ):
        path = out_dir / name
        _write_csv(path, table)
        paths.append(path)
    assignments_path = out_dir / "phenotype_composition_assignments.parquet"
    _write_parquet(assignments_path, result.assignments)
    paths.append(assignments_path)
    protocol_path = out_dir / "phenotype_composition_protocol.json"
    _write_json(
        protocol_path,
        {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_freeze_id": composition_config.protocol_freeze_id,
            "source_phenotype_protocol_freeze_id": phenotype_config.protocol_freeze_id,
            "input_path": str(input_path.resolve()),
            "input_sha256": sha256_file(input_path),
            "frozen_rules_path": str(frozen_rules_path.resolve()),
            "frozen_rules_sha256": sha256_file(frozen_rules_path),
            "working_tree_policy": (
                "UNFROZEN_DIRTY_DEVELOPMENT"
                if allow_dirty_development else "CLEAN_EVIDENCE_REQUIRED"
            ),
            "config": asdict(composition_config),
            "screened_composition_count": len(result.screening),
            "calibration_candidate_count": int(
                result.screening["passed_calibration_gate"].sum()
            ),
            "frozen_composition_count": len(result.catalog),
            "high_probability_verified_count": int(
                (result.catalog["status"] == "HIGH_PROBABILITY_VERIFIED").sum()
            ) if not result.catalog.empty else 0,
            "claim_boundary": (
                "conditional intersections of already frozen base phenotypes; "
                "calibration-selected and later verified; no base-rule retuning"
            ),
        },
    )
    paths.append(protocol_path)
    manifest = build_manifest(
        run_id="phenotype-compositions-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=paths,
        root=out_dir,
    )
    write_manifest(out_dir / "phenotype_composition.manifest.json", manifest)
    return out_dir


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


__all__ = [
    "run_cross_fitted_phenotype_discovery",
    "run_phenotype_composition_discovery",
    "run_phenotype_followup_registry",
]
