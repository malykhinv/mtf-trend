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
from anomaly_science.probability.builder import (
    build_binary_weekly_walk_forward,
    build_gate_rows,
    build_null_test_rows,
    build_prediction_metrics,
    build_reliability_rows,
)
from anomaly_science.probability.config import BinaryWeeklyWalkForwardConfig


class BinaryProbabilityRunError(RuntimeError):
    """Raised when a frozen binary-probability run cannot be attested."""


def run_binary_weekly_walk_forward(
    *,
    input_path: Path,
    out_dir: Path,
    config: BinaryWeeklyWalkForwardConfig,
    allow_dirty_development: bool = False,
    weekly_jobs: int = 1,
) -> Path:
    revision, dirty = _repository_state()
    if dirty and not allow_dirty_development:
        raise BinaryProbabilityRunError(
            "binary probability protocol freeze requires a clean worktree; "
            "commit the protocol or use --allow-dirty-development for non-evidence output"
        )
    if out_dir.exists() and any(out_dir.iterdir()):
        raise BinaryProbabilityRunError(
            f"output directory must be absent or empty to prevent stale model artifacts: {out_dir}"
        )
    frame = pd.read_parquet(input_path) if input_path.suffix.lower() == ".parquet" else pd.read_csv(input_path)
    result = build_binary_weekly_walk_forward(frame, config, weekly_jobs=weekly_jobs)
    metrics = build_prediction_metrics(result.predictions, config)
    reliability = build_reliability_rows(result.predictions, config)
    null_tests = build_null_test_rows(result.predictions, config)
    gates = build_gate_rows(
        result.predictions,
        metrics,
        reliability,
        null_tests,
        result.weekly_metadata,
        config,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    model_dir = out_dir / "weekly_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    paths.append(_write_parquet(out_dir / "oos_predictions.parquet", result.predictions))
    paths.append(_write_csv(out_dir / "weekly_model_metadata.csv", result.weekly_metadata))
    paths.append(_write_csv(out_dir / "feature_importance.csv", result.feature_importance))
    paths.append(_write_csv(out_dir / "prediction_metrics.csv", metrics))
    paths.append(_write_csv(out_dir / "reliability.csv", reliability))
    paths.append(_write_csv(out_dir / "null_tests.csv", null_tests))
    paths.append(_write_csv(out_dir / "gate_evaluation.csv", gates))

    for frozen in result.frozen_models:
        model_path = model_dir / f"{frozen.test_week}.cbm"
        frozen.model.save_model(str(model_path), format="cbm")
        paths.append(model_path)
        calibrator_path = model_dir / f"{frozen.test_week}.isotonic.json"
        _write_json(
            calibrator_path,
            {
                "model_id": frozen.model_id,
                "feature_names": list(frozen.feature_names),
                "categorical_feature_names": list(frozen.categorical_feature_names),
                "x_thresholds": np.asarray(frozen.calibrator.X_thresholds_, dtype=float).tolist(),
                "y_thresholds": np.asarray(frozen.calibrator.y_thresholds_, dtype=float).tolist(),
                "out_of_bounds": "clip",
            },
        )
        paths.append(calibrator_path)

    temporal_audit = _temporal_audit(result.predictions, result.weekly_metadata)
    paths.append(_write_csv(out_dir / "temporal_audit.csv", temporal_audit))
    created_at = datetime.now(timezone.utc).isoformat()
    protocol_path = out_dir / "frozen_probability_protocol.json"
    _write_json(
        protocol_path,
        {
            "freeze_status": "UNFROZEN_DIRTY_DEVELOPMENT" if dirty else "FROZEN",
            "created_at_utc": created_at,
            "protocol_freeze_id": config.protocol_freeze_id,
            "code_commit": revision,
            "working_tree_dirty": dirty,
            "input_path": str(input_path.resolve()),
            "input_sha256": sha256_file(input_path),
            "config": asdict(config),
            "training_label_eligibility": (
                f"{config.resolution_time_column} < weekly_model_freeze_time_ms"
            ),
            "model_freeze_cadence": "one CatBoost and one isotonic calibrator per ISO week",
            "class_order": [0, 1],
            "within_week_refit": False,
            "weekly_parallel_jobs": weekly_jobs,
            "post_hoc_gate_changes_forbidden": True,
            "scientific_scope": "calibrated binary probability only; no EV, trade, or PnL claim",
        },
    )
    paths.append(protocol_path)
    metadata_path = out_dir / "binary_probability.metadata.json"
    _write_json(
        metadata_path,
        {
            "created_at_utc": created_at,
            "input_row_count": len(frame),
            "oos_prediction_row_count": len(result.predictions),
            "frozen_week_count": len(result.frozen_models),
            "weekly_parallel_jobs": weekly_jobs,
            "skipped_week_count": int((result.weekly_metadata.get("status") == "SKIPPED").sum())
            if not result.weekly_metadata.empty
            else 0,
            "all_pre_registered_gates_pass": bool(len(gates) and (gates["status"] == "PASS").all()),
            "evidence_status": "UNFROZEN_DIRTY_DEVELOPMENT" if dirty else "FROZEN_DEVELOPMENT",
            "pristine_holdout_accessed": False,
        },
    )
    paths.append(metadata_path)
    manifest = build_manifest(
        run_id="binary-weekly-wfa-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=paths,
        root=out_dir,
    )
    write_manifest(out_dir / "binary_probability.manifest.json", manifest)
    return out_dir


def _temporal_audit(predictions: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    checks = {
        "prediction_feature_cutoff_not_after_snapshot": bool(
            predictions.empty
            or (predictions["feature_cutoff_time_ms"] <= predictions["snapshot_time_ms"]).all()
        ),
        "prediction_future_strictly_after_snapshot": bool(
            predictions.empty
            or (predictions["future_start_time_ms"] > predictions["snapshot_time_ms"]).all()
        ),
        "weekly_model_frozen_not_after_prediction": bool(
            predictions.empty
            or (predictions["weekly_model_freeze_time_ms"] <= predictions["snapshot_time_ms"]).all()
        ),
        "train_labels_resolved_strictly_before_freeze": bool(
            metadata.empty
            or metadata.loc[metadata["status"] == "FROZEN_AND_SCORED"].apply(
                lambda row: int(row["latest_train_resolution_time_ms"])
                < int(row["weekly_model_freeze_time_ms"]),
                axis=1,
            ).all()
        ),
    }
    for name, passed in checks.items():
        rows.append({"check_name": name, "status": "PASS" if passed else "FAIL"})
    return pd.DataFrame(rows)


def _write_csv(path: Path, frame: pd.DataFrame) -> Path:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)
    return path


def _write_parquet(path: Path, frame: pd.DataFrame) -> Path:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)
    return path


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _repository_state() -> tuple[str, bool]:
    root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BinaryProbabilityRunError("cannot attest repository revision") from exc
    if not revision:
        raise BinaryProbabilityRunError("git returned an empty repository revision")
    return revision, bool(status.strip())


__all__ = ["BinaryProbabilityRunError", "run_binary_weekly_walk_forward"]
