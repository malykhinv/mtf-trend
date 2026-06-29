from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from anomaly_science.archetypes import (
    ArchetypeDiscoveryConfig,
    ArchetypeRequiredValue,
    limit_archetype_symbols,
    read_archetype_input_frame,
    run_prepared_archetype_discovery,
)
from anomaly_science.archetypes.builder import (
    PreparedArchetypeData,
    prepare_archetype_data_from_rows,
    prepare_archetype_rows,
)
from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.strategy.pump_fade.spec import PUMP_FADE_OI_MODEL_FEATURES


class PumpFadeOiExperimentError(ValueError):
    """Raised when the paired OI ablation would compare different populations."""


def run_pump_fade_oi_incremental_experiment(
    *,
    input_path: Path,
    out_dir: Path,
    base_config: ArchetypeDiscoveryConfig,
    limit_symbols: int | None = None,
    bootstrap_iterations: int = 2000,
) -> Path:
    """Run no-OI and with-OI discovery on the exact same OI-covered rows.

    Any incomplete OI evidence writes ``oi_incremental_summary.json`` with
    ``incremental_evidence_status=FAILED_PARTIAL`` and returns the output
    directory instead of crashing after partial artifacts. FAILED_PARTIAL remains
    non-evidential by contract.
    """
    selected = {
        *base_config.features.numeric,
        *base_config.features.decision_timing_numeric,
        *base_config.features.categorical,
    }
    overlap = sorted(selected & set(PUMP_FADE_OI_MODEL_FEATURES))
    if overlap:
        raise PumpFadeOiExperimentError(
            f"base config already contains OI features and cannot serve as the ablation: {overlap}"
        )
    required_values = (*base_config.population.required_values, ArchetypeRequiredValue("oi_available", True))
    if len({item.column for item in required_values}) != len(required_values):
        raise PumpFadeOiExperimentError("base population already constrains oi_available")
    required_finite_columns = (
        *base_config.population.required_finite_columns,
        *PUMP_FADE_OI_MODEL_FEATURES,
    )
    if len(set(required_finite_columns)) != len(required_finite_columns):
        raise PumpFadeOiExperimentError(
            "base population already constrains one or more OI model feature finite-availability columns"
        )
    covered_population = replace(
        base_config.population,
        required_values=required_values,
        required_finite_columns=required_finite_columns,
    )
    baseline_config = replace(base_config, population=covered_population)
    oi_config = replace(
        baseline_config,
        features=replace(
            baseline_config.features,
            numeric=(*baseline_config.features.numeric, *PUMP_FADE_OI_MODEL_FEATURES),
        ),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    input_sha256 = sha256_file(input_path)
    try:
        source_frame = read_archetype_input_frame(input_path)
        limited_frame = limit_archetype_symbols(source_frame, baseline_config, limit_symbols)
        shared_rows = prepare_archetype_rows(limited_frame, baseline_config)
        baseline_prepared = prepare_archetype_data_from_rows(shared_rows, baseline_config)
        oi_prepared = prepare_archetype_data_from_rows(shared_rows, oi_config)
        _assert_prepared_row_identity(baseline_prepared, oi_prepared, baseline_config)
    except Exception as exc:
        _write_failed_summary(
            input_path=input_path,
            input_sha256=input_sha256,
            out_dir=out_dir,
            limit_symbols=limit_symbols,
            baseline_dir=None,
            oi_dir=None,
            baseline_error=exc,
            oi_error=None,
            preparation_contract="shared_population_preparation_failed",
            catboost_thread_count=baseline_config.catboost_thread_count,
        )
        return out_dir
    input_rows_after_limit = len(limited_frame)
    try:
        baseline_dir = run_prepared_archetype_discovery(
            prepared=baseline_prepared,
            input_path=input_path,
            input_sha256=input_sha256,
            out_dir=out_dir / "baseline_same_oi_population",
            config=baseline_config,
            limit_symbols=limit_symbols,
            input_rows_after_limit=input_rows_after_limit,
            input_reuse_contract="paired_oi_shared_population_split_v1",
        )
    except Exception as exc:
        _write_failed_summary(
            input_path=input_path,
            input_sha256=input_sha256,
            out_dir=out_dir,
            limit_symbols=limit_symbols,
            baseline_dir=None,
            oi_dir=None,
            baseline_error=exc,
            oi_error=None,
            preparation_contract="paired_oi_shared_population_split_v1",
            catboost_thread_count=baseline_config.catboost_thread_count,
        )
        return out_dir
    try:
        oi_dir = run_prepared_archetype_discovery(
            prepared=oi_prepared,
            input_path=input_path,
            input_sha256=input_sha256,
            out_dir=out_dir / "with_open_interest",
            config=oi_config,
            limit_symbols=limit_symbols,
            input_rows_after_limit=input_rows_after_limit,
            input_reuse_contract="paired_oi_shared_population_split_v1",
        )
    except Exception as exc:
        _write_failed_summary(
            input_path=input_path,
            input_sha256=input_sha256,
            out_dir=out_dir,
            limit_symbols=limit_symbols,
            baseline_dir=baseline_dir,
            oi_dir=None,
            baseline_error=None,
            oi_error=exc,
            preparation_contract="paired_oi_shared_population_split_v1",
            catboost_thread_count=baseline_config.catboost_thread_count,
        )
        return out_dir
    baseline = _read_run(baseline_dir / "archetype_run.json")
    with_oi = _read_run(oi_dir / "archetype_run.json")
    for field in ("input_rows_after_limit", "discovery_rows", "verification_rows", "discovery_groups", "verification_groups"):
        if baseline[field] != with_oi[field]:
            raise PumpFadeOiExperimentError(
                f"paired OI experiment population mismatch for {field}: "
                f"baseline={baseline[field]} with_oi={with_oi[field]}"
            )

    paired = _paired_group_bootstrap(
        baseline_path=baseline_dir / "anomaly_archetype_verification_predictions.parquet",
        oi_path=oi_dir / "anomaly_archetype_verification_predictions.parquet",
        iterations=bootstrap_iterations,
        random_seed=20260629,
    )
    both_controls_passed = bool(baseline["controls_passed"]) and bool(with_oi["controls_passed"])
    if limit_symbols is not None:
        evidence_status = "SMOKE_ONLY"
    elif both_controls_passed and paired["auc_delta_lower_95"] > 0.0:
        evidence_status = "DEVELOPMENT_PAIRED_POSITIVE"
    else:
        evidence_status = "DEVELOPMENT_NOT_CONFIRMED"
    summary_path = out_dir / "oi_incremental_summary.json"
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": input_sha256,
        "limit_symbols": limit_symbols,
        "preparation_contract": "paired_oi_shared_population_split_v1",
        "catboost_thread_count": baseline_config.catboost_thread_count,
        "catboost_threading_contract": "explicit_bounded_thread_count_v1",
        "input_rows_after_limit": input_rows_after_limit,
        "shared_discovery_rows": len(shared_rows.discovery),
        "shared_verification_rows": len(shared_rows.verification),
        "population_contract": (
            "identical rows with oi_available=true and finite registered OI model features; "
            "oi_available is never a model feature"
        ),
        "raw_oi_stream_availability_column": "oi_available",
        "required_finite_oi_feature_names": list(PUMP_FADE_OI_MODEL_FEATURES),
        "oi_feature_names": list(PUMP_FADE_OI_MODEL_FEATURES),
        "discovery_groups": baseline["discovery_groups"],
        "verification_groups": baseline["verification_groups"],
        "baseline": _summary_fields(baseline),
        "with_open_interest": _summary_fields(with_oi),
        "verification_auc_delta_with_oi_minus_baseline": (
            float(with_oi["verification_auc"]) - float(baseline["verification_auc"])
        ),
        "paired_group_bootstrap": paired,
        "incremental_evidence_status": evidence_status,
        "interpretation": (
            "A symbol-limited run validates data and protocol only. Development evidence additionally "
            "requires both arms to pass blind/shuffled controls and the paired group-bootstrap lower bound "
            "to exceed zero; pristine evidence still requires a pre-registered untouched holdout."
        ),
    }
    _atomic_json(summary_path, payload)
    artifacts = (
        summary_path,
        *(path for path in baseline_dir.rglob("*") if path.is_file()),
        *(path for path in oi_dir.rglob("*") if path.is_file()),
    )
    manifest = build_manifest(
        run_id=f"pump-fade-oi-incremental-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        artifact_paths=artifacts,
        root=out_dir,
    )
    write_manifest(out_dir / "artifact_manifest.json", manifest)
    return out_dir



def _assert_prepared_row_identity(
    baseline: PreparedArchetypeData,
    with_oi: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
) -> None:
    key_columns = [
        config.input.group_column,
        config.input.symbol_column,
        "__snapshot_ms",
        config.input.label_column,
    ]
    for split_name, left, right in (
        ("discovery", baseline.discovery, with_oi.discovery),
        ("verification", baseline.verification, with_oi.verification),
    ):
        if len(left) != len(right):
            raise PumpFadeOiExperimentError(
                f"paired OI experiment {split_name} row-count mismatch after shared preparation: "
                f"baseline={len(left)} with_oi={len(right)}"
            )
        missing = [column for column in key_columns if column not in left or column not in right]
        if missing:
            raise PumpFadeOiExperimentError(
                f"paired OI experiment {split_name} prepared rows are missing identity columns: {missing}"
            )
        left_keys = left[key_columns].astype(str).reset_index(drop=True)
        right_keys = right[key_columns].astype(str).reset_index(drop=True)
        if not left_keys.equals(right_keys):
            raise PumpFadeOiExperimentError(
                f"paired OI experiment {split_name} prepared rows are not row-identical"
            )

def _write_failed_summary(
    *,
    input_path: Path,
    input_sha256: str,
    out_dir: Path,
    limit_symbols: int | None,
    baseline_dir: Path | None,
    oi_dir: Path | None,
    baseline_error: BaseException | None,
    oi_error: BaseException | None,
    preparation_contract: str,
    catboost_thread_count: int,
) -> Path:
    summary_path = out_dir / "oi_incremental_summary.json"
    baseline = _read_optional_run(baseline_dir)
    with_oi = _read_optional_run(oi_dir)
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": input_sha256,
        "limit_symbols": limit_symbols,
        "preparation_contract": preparation_contract,
        "catboost_thread_count": catboost_thread_count,
        "catboost_threading_contract": "explicit_bounded_thread_count_v1",
        "population_contract": (
            "identical rows with oi_available=true and finite registered OI model features; "
            "oi_available is never a model feature"
        ),
        "raw_oi_stream_availability_column": "oi_available",
        "required_finite_oi_feature_names": list(PUMP_FADE_OI_MODEL_FEATURES),
        "oi_feature_names": list(PUMP_FADE_OI_MODEL_FEATURES),
        "baseline": None if baseline is None else _summary_fields(baseline),
        "with_open_interest": None if with_oi is None else _summary_fields(with_oi),
        "baseline_error": _exception_payload(baseline_error),
        "with_open_interest_error": _exception_payload(oi_error),
        "incremental_evidence_status": "FAILED_PARTIAL",
        "interpretation": (
            "The paired experiment is non-evidential because at least one arm failed. "
            "A successful baseline arm alone must not be interpreted as OI evidence. "
            "The required population is raw OI stream availability plus finite values for every "
            "registered OI model feature, not oi_available alone."
        ),
    }
    _atomic_json(summary_path, payload)
    artifact_paths = [summary_path]
    for directory in (baseline_dir, oi_dir):
        if directory is not None and directory.exists():
            artifact_paths.extend(path for path in directory.rglob("*") if path.is_file())
    manifest = build_manifest(
        run_id=f"pump-fade-oi-incremental-failed-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        artifact_paths=tuple(artifact_paths),
        root=out_dir,
    )
    write_manifest(out_dir / "artifact_manifest.json", manifest)
    return summary_path


def _read_optional_run(directory: Path | None) -> dict[str, object] | None:
    if directory is None:
        return None
    path = directory / "archetype_run.json"
    if not path.exists():
        return None
    return _read_run(path)


def _exception_payload(exc: BaseException | None) -> dict[str, str] | None:
    if exc is None:
        return None
    return {"type": type(exc).__name__, "message": str(exc)}


def _read_run(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_fields(payload: dict[str, object]) -> dict[str, object]:
    return {
        name: payload[name]
        for name in (
            "run_id",
            "catboost_thread_count",
            "catboost_threading_contract",
            "controls_passed",
            "discovery_rows",
            "verification_rows",
            "verification_auc",
            "development_replicated_category_count",
            "control_failed_category_count",
            "auc_control_empirical_p",
            "category_count_control_empirical_p",
            "model_feature_names",
            "dropped_constant_features",
        )
    }


def _paired_group_bootstrap(
    *,
    baseline_path: Path,
    oi_path: Path,
    iterations: int,
    random_seed: int,
) -> dict[str, object]:
    if iterations < 1000:
        raise PumpFadeOiExperimentError("paired group bootstrap requires at least 1000 iterations")
    baseline = pd.read_parquet(baseline_path)
    with_oi = pd.read_parquet(oi_path)
    keys = ["group", "symbol", "snapshot_time_ms", "label"]
    paired = baseline.merge(
        with_oi,
        on=keys,
        how="outer",
        validate="one_to_one",
        suffixes=("_baseline", "_oi"),
        indicator=True,
    )
    if not paired["_merge"].eq("both").all():
        raise PumpFadeOiExperimentError("baseline and OI verification predictions are not row-identical")
    groups = tuple(sorted(paired["group"].astype(str).unique()))
    group_indices = {
        group: np.flatnonzero(paired["group"].astype(str).to_numpy() == group)
        for group in groups
    }
    labels = paired["label"].to_numpy(dtype=np.int8)
    baseline_probability = paired["probability_baseline"].to_numpy(dtype=float)
    oi_probability = paired["probability_oi"].to_numpy(dtype=float)
    weights = paired["group_inverse_weight_baseline"].to_numpy(dtype=float)
    rng = np.random.default_rng(random_seed)
    deltas: list[float] = []
    for _ in range(iterations):
        sampled = rng.choice(groups, size=len(groups), replace=True)
        indices = np.concatenate([group_indices[str(group)] for group in sampled])
        sampled_labels = labels[indices]
        if np.unique(sampled_labels).size < 2:
            continue
        baseline_auc = roc_auc_score(
            sampled_labels,
            baseline_probability[indices],
            sample_weight=weights[indices],
        )
        oi_auc = roc_auc_score(
            sampled_labels,
            oi_probability[indices],
            sample_weight=weights[indices],
        )
        deltas.append(float(oi_auc - baseline_auc))
    if len(deltas) < int(iterations * 0.95):
        raise PumpFadeOiExperimentError("too many paired bootstrap samples contained one label class")
    values = np.asarray(deltas, dtype=float)
    return {
        "random_seed": random_seed,
        "requested_iterations": iterations,
        "valid_iterations": len(values),
        "verification_group_count": len(groups),
        "auc_delta_mean": float(values.mean()),
        "auc_delta_lower_95": float(np.quantile(values, 0.025)),
        "auc_delta_upper_95": float(np.quantile(values, 0.975)),
        "one_sided_p_delta_le_zero": float((1 + np.count_nonzero(values <= 0.0)) / (len(values) + 1)),
        "resampling_unit": "verification_group",
    }


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


__all__ = [
    "PumpFadeOiExperimentError",
    "run_pump_fade_oi_incremental_experiment",
]
