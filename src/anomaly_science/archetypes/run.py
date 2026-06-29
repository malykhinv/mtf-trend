from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from anomaly_science.archetypes.builder import (
    ArchetypeDiscoveryError,
    build_archetype_discovery,
)
from anomaly_science.archetypes.config import (
    ArchetypeDiscoveryConfig,
    config_to_json_dict,
)
from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.artifacts.writer import write_csv_artifact_with_aliases
from anomaly_science.contracts.artifacts import get_artifact_schema


def _read_dataset(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ArchetypeDiscoveryError("archetype input must be parquet or CSV")


def _atomic_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def run_archetype_discovery(
    *,
    input_path: Path,
    out_dir: Path,
    config: ArchetypeDiscoveryConfig,
    limit_symbols: int | None = None,
) -> Path:
    if limit_symbols is not None and limit_symbols <= 0:
        raise ArchetypeDiscoveryError("limit_symbols must be positive when provided")
    frame = _read_dataset(input_path)
    if limit_symbols is not None:
        symbol_column = config.input.symbol_column
        if symbol_column not in frame:
            raise ArchetypeDiscoveryError(f"symbol column {symbol_column!r} is missing")
        symbols = sorted(str(value) for value in frame[symbol_column].dropna().unique())[:limit_symbols]
        frame = frame[frame[symbol_column].astype(str).isin(symbols)].copy()
    result = build_archetype_discovery(frame, config)
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "anomaly_archetype_catalog.csv"
    controls_path = out_dir / "anomaly_archetype_controls.csv"
    coverage_path = out_dir / "anomaly_archetype_coverage.csv"
    funnel_path = out_dir / "anomaly_archetype_candidate_funnel.csv"
    threshold_stability_path = out_dir / "anomaly_archetype_threshold_stability.csv"
    assignments_path = out_dir / "anomaly_archetype_assignments.parquet"
    predictions_path = out_dir / "anomaly_archetype_verification_predictions.parquet"
    model_path = out_dir / "archetype_rule_generator.cbm"
    model_json_path = out_dir / "archetype_rule_generator.json"
    run_path = out_dir / "archetype_run.json"
    catalog_paths = write_csv_artifact_with_aliases(
        catalog_path,
        result.category_rows,
        get_artifact_schema(catalog_path.name),
    )
    control_paths = write_csv_artifact_with_aliases(
        controls_path,
        result.control_rows,
        get_artifact_schema(controls_path.name),
    )
    coverage_paths = write_csv_artifact_with_aliases(
        coverage_path,
        result.coverage_rows,
        get_artifact_schema(coverage_path.name),
    )
    funnel_paths = write_csv_artifact_with_aliases(
        funnel_path,
        result.candidate_funnel_rows,
        get_artifact_schema(funnel_path.name),
    )
    threshold_stability_paths = write_csv_artifact_with_aliases(
        threshold_stability_path,
        result.threshold_stability_rows,
        get_artifact_schema(threshold_stability_path.name),
    )
    result.assignments.to_parquet(assignments_path, index=False)
    result.verification_predictions.to_parquet(predictions_path, index=False)
    result.model.save_model(str(model_path))
    _atomic_json(model_json_path, result.model_json)
    run_id = f"archetype-discovery-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_payload = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "limit_symbols": limit_symbols,
        "input_rows_after_limit": len(frame),
        "discovery_rows": len(result.prepared.discovery),
        "verification_rows": len(result.prepared.verification),
        "discovery_groups": int(
            result.prepared.discovery[config.input.group_column].nunique()
        ),
        "verification_groups": int(
            result.prepared.verification[config.input.group_column].nunique()
        ),
        "model_feature_names": list(result.prepared.model_feature_names),
        "dropped_constant_features": list(result.prepared.dropped_constant_features),
        "unknown_verification_categories": list(
            result.prepared.unknown_verification_categories
        ),
        "discovery_candidate_count": result.discovery_candidate_count,
        "distinct_candidate_count": result.distinct_candidate_count,
        "generator_fit_count_including_nulls": result.generator_fit_count,
        "search_truncated": result.search_truncated,
        "controls_passed": result.controls_passed,
        "auc_control_empirical_p": result.auc_control_empirical_p,
        "category_count_control_empirical_p": result.category_count_control_empirical_p,
        "threshold_stability_row_count": len(result.threshold_stability_rows),
        "threshold_stability_methodology": (
            "Frozen CatBoost thresholds are not changed. The audit measures original, rounded, nearby, "
            "and discovery-only coarse-quantile variants on discovery and later verification rows so noisy "
            "float cutpoints can be identified before any evidence claim."
        ),
        "verified_category_count": sum(
            row.status == "PRISTINE_VERIFIED" for row in result.category_rows
        ),
        "development_replicated_category_count": sum(
            row.status == "DEVELOPMENT_REPLICATED" for row in result.category_rows
        ),
        "control_failed_category_count": sum(
            row.status == "CONTROL_FAILED" for row in result.category_rows
        ),
        "verification_auc": result.verification_auc,
        "methodology": (
            "A registered ensemble of shallow CatBoost fits across expanding rolling origins generates "
            "interpretable predictive-phenotype rules. Consensus and marginal-coverage pruning are frozen "
            "before the later interval. Development evidence is never reported as "
            "blind verification; PRISTINE_VERIFIED requires a pre-registered freeze id and untouched forward "
            "holdout. The search makes no claim about causal mechanisms or phenotypes outside the registered "
            "feature/model/support space. Weekly WFA remains required for a probability model used in trading."
        ),
        "config": config_to_json_dict(config),
    }
    _atomic_json(run_path, run_payload)
    artifact_paths = [
        *catalog_paths,
        *control_paths,
        *coverage_paths,
        *funnel_paths,
        *threshold_stability_paths,
        assignments_path,
        predictions_path,
        model_path,
        model_json_path,
        run_path,
    ]
    manifest = build_manifest(run_id=run_id, artifact_paths=artifact_paths, root=out_dir)
    write_manifest(out_dir / "artifact_manifest.json", manifest)
    return out_dir


__all__ = ["ArchetypeDiscoveryError", "run_archetype_discovery"]
