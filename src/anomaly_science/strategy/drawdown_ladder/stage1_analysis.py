"""Frozen paired probability comparison for drawdown-ladder Stage 1."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.probability import (
    PairedProbabilityComparisonConfig,
    build_gate_rows,
    build_reliability_rows,
    compare_paired_oos_probabilities,
    load_binary_weekly_walk_forward_config,
)
from anomaly_science.strategy.drawdown_ladder.stage1_spec import (
    DrawdownLadderStage1Spec,
    build_stage1_paired_comparison_config,
    build_stage1_probability_config,
)


class Stage1AnalysisError(ValueError):
    """Raised when frozen Stage-1 probability artifacts are not comparable."""


def _read_frozen_predictions(
    directory: Path,
    *,
    expected_protocol_freeze_id: str,
) -> tuple[pd.DataFrame, Path]:
    metadata_path = directory / "binary_probability.metadata.json"
    protocol_path = directory / "frozen_probability_protocol.json"
    prediction_path = directory / "oos_predictions.parquet"
    audit_path = directory / "temporal_audit.csv"
    for path in (metadata_path, protocol_path, prediction_path, audit_path):
        if not path.is_file():
            raise Stage1AnalysisError(f"Stage-1 probability artifact is missing: {path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if metadata.get("evidence_status") != "FROZEN_DEVELOPMENT":
        raise Stage1AnalysisError(f"probability input is not frozen: {directory}")
    if metadata.get("pristine_holdout_accessed") is not False:
        raise Stage1AnalysisError(f"probability input accessed pristine holdout: {directory}")
    if protocol.get("protocol_freeze_id") != expected_protocol_freeze_id:
        raise Stage1AnalysisError(f"probability protocol mismatch: {directory}")
    audit = pd.read_csv(audit_path)
    if audit.empty or not audit["status"].eq("PASS").all():
        raise Stage1AnalysisError(f"probability temporal audit failed: {directory}")
    return pd.read_parquet(prediction_path), prediction_path


def _load_comparison_config(
    path: Path,
    *,
    expected: PairedProbabilityComparisonConfig,
) -> PairedProbabilityComparisonConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    config = PairedProbabilityComparisonConfig(**raw)
    if config != expected:
        raise Stage1AnalysisError("paired comparison config differs from frozen Stage-1 spec")
    return config


def _repository_state() -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return revision, dirty


def _write_csv(path: Path, frame: pd.DataFrame) -> Path:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)
    return path


def build_stage1_probability_comparison(
    *,
    structural_dir: str | Path,
    full_dir: str | Path,
    comparison_config_path: str | Path,
    output_dir: str | Path,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> Path:
    """Compare frozen structural and full causal predictions without retuning."""

    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise Stage1AnalysisError(f"comparison output must be absent or empty: {root}")
    revision, dirty = _repository_state()
    if dirty:
        raise Stage1AnalysisError("Stage-1 comparison requires a clean committed worktree")
    structural, structural_path = _read_frozen_predictions(
        Path(structural_dir),
        expected_protocol_freeze_id=f"{spec.protocol_freeze_id}:structural_baseline",
    )
    full, full_path = _read_frozen_predictions(
        Path(full_dir),
        expected_protocol_freeze_id=f"{spec.protocol_freeze_id}:full_causal",
    )
    config_path = Path(comparison_config_path)
    config = _load_comparison_config(
        config_path,
        expected=build_stage1_paired_comparison_config(spec),
    )
    result = compare_paired_oos_probabilities(structural, full, config)
    root.mkdir(parents=True, exist_ok=True)
    paths = [
        _write_csv(root / "paired_metrics.csv", result.metrics),
        _write_csv(root / "paired_inference.csv", result.inference),
        _write_csv(root / "paired_gates.csv", result.gates),
    ]
    all_pass = bool(len(result.gates) and result.gates["status"].eq("PASS").all())
    report_path = root / "comparison_report.json"
    report_path.write_text(
        json.dumps(
            {
                "protocol_freeze_id": config.protocol_freeze_id,
                "status": (
                    "IS_FULL_CAUSAL_INCREMENTAL_GATES_PASS"
                    if all_pass
                    else "IS_FULL_CAUSAL_INCREMENTAL_GATES_FAIL"
                ),
                "all_pre_registered_incremental_gates_pass": all_pass,
                "row_count": len(structural),
                "code_commit": revision,
                "working_tree_dirty": dirty,
                "oos_2026_accessed": False,
                "scientific_scope": "incremental calibrated prediction only; no EV/PnL claim",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    paths.append(report_path)
    protocol_path = root / "frozen_comparison_protocol.json"
    protocol_path.write_text(
        json.dumps(
            {
                "config": asdict(config),
                "code_commit": revision,
                "structural_predictions_sha256": sha256_file(structural_path),
                "full_predictions_sha256": sha256_file(full_path),
                "comparison_config_sha256": sha256_file(config_path),
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "post_hoc_gate_changes_forbidden": True,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    paths.append(protocol_path)
    manifest = build_manifest(
        run_id="drawdown-stage1-paired-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=paths,
        root=root,
    )
    write_manifest(root / "comparison.manifest.json", manifest)
    return root


def build_stage1_probability_gate_amendment(
    *,
    probability_dir: str | Path,
    probability_config_path: str | Path,
    arm: str,
    output_dir: str | Path,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> Path:
    """Re-evaluate gates after the reliability-threshold coverage bug fix."""

    if arm not in {"structural_baseline", "full_causal"}:
        raise Stage1AnalysisError("unknown Stage-1 probability arm")
    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise Stage1AnalysisError(f"gate-amendment output must be absent or empty: {root}")
    revision, dirty = _repository_state()
    if dirty:
        raise Stage1AnalysisError("Stage-1 gate amendment requires a clean committed worktree")
    source_dir = Path(probability_dir)
    predictions, prediction_path = _read_frozen_predictions(
        source_dir,
        expected_protocol_freeze_id=f"{spec.protocol_freeze_id}:{arm}",
    )
    config_path = Path(probability_config_path)
    config = load_binary_weekly_walk_forward_config(config_path)
    if config != build_stage1_probability_config(arm=arm, spec=spec):
        raise Stage1AnalysisError("probability config differs from frozen Stage-1 arm")
    source_paths = {
        "metrics": source_dir / "prediction_metrics.csv",
        "null_tests": source_dir / "null_tests.csv",
        "weekly_metadata": source_dir / "weekly_model_metadata.csv",
        "original_reliability": source_dir / "reliability.csv",
        "original_gates": source_dir / "gate_evaluation.csv",
    }
    for path in source_paths.values():
        if not path.is_file():
            raise Stage1AnalysisError(f"gate-amendment source is missing: {path}")
    metrics = pd.read_csv(source_paths["metrics"])
    null_tests = pd.read_csv(source_paths["null_tests"])
    weekly_metadata = pd.read_csv(source_paths["weekly_metadata"])
    reliability = build_reliability_rows(predictions, config)
    high = reliability.loc[
        reliability["kind"].eq("threshold")
        & np.isclose(
            reliability["threshold"],
            config.gates.high_probability_threshold,
            equal_nan=False,
        )
    ]
    if len(high) != 1:
        raise Stage1AnalysisError("corrected reliability did not produce exactly one gate threshold")
    gates = build_gate_rows(
        predictions,
        metrics,
        reliability,
        null_tests,
        weekly_metadata,
        config,
    )
    root.mkdir(parents=True, exist_ok=True)
    paths = [
        _write_csv(root / "corrected_reliability.csv", reliability),
        _write_csv(root / "corrected_gate_evaluation.csv", gates),
    ]
    all_pass = bool(len(gates) and gates["status"].eq("PASS").all())
    report_path = root / "gate_amendment_report.json"
    report_path.write_text(
        json.dumps(
            {
                "protocol_freeze_id": config.protocol_freeze_id,
                "arm": arm,
                "status": "PASS" if all_pass else "FAIL",
                "all_pre_registered_absolute_gates_pass": all_pass,
                "amendment_reason": (
                    "the frozen 0.92 high-probability gate was absent from the explicit "
                    "reliability threshold list; Core now always evaluates every gate threshold"
                ),
                "model_predictions_changed": False,
                "metrics_changed": False,
                "null_tests_changed": False,
                "oos_2026_accessed": False,
                "code_commit": revision,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    paths.append(report_path)
    protocol_path = root / "gate_amendment_provenance.json"
    protocol_path.write_text(
        json.dumps(
            {
                "config": asdict(config),
                "prediction_sha256": sha256_file(prediction_path),
                "probability_config_sha256": sha256_file(config_path),
                "source_artifact_sha256": {
                    name: sha256_file(path) for name, path in source_paths.items()
                },
                "code_commit": revision,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    paths.append(protocol_path)
    manifest = build_manifest(
        run_id="drawdown-stage1-gate-amendment-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=paths,
        root=root,
    )
    write_manifest(root / "gate_amendment.manifest.json", manifest)
    return root


__all__ = [
    "Stage1AnalysisError",
    "build_stage1_probability_comparison",
    "build_stage1_probability_gate_amendment",
]
