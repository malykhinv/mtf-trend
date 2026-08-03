"""Causal HOLD-versus-EXIT continuation-nature experiment for ladder inventory."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Literal

import numpy as np
import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.probability import (
    BinaryProbabilityGates,
    BinaryWeeklyWalkForwardConfig,
    PairedProbabilityComparisonConfig,
)
from anomaly_science.strategy.drawdown_ladder.stage1_spec import (
    DrawdownLadderStage1Spec,
    build_mirrored_rally_stage1_spec,
    build_stage1_probability_config,
)


class ContinuationExperimentError(ValueError):
    """Raised when the continuation experiment violates its frozen contract."""


@dataclass(frozen=True, slots=True)
class ContinuationExperimentSpec:
    protocol_version: str = "drawdown_ladder_continuation_nature_v1"
    protocol_freeze_id: str = "drawdown_ladder_continuation_nature_20260803_v1"
    label_schema_version: str = "drawdown_ladder_hold_outperforms_exit_48h_v1"
    feature_schema_version: str = "drawdown_ladder_causal_features_v1"
    study_direction: Literal["long", "short"] = "long"
    primary_grid_step_pct: int = 3
    primary_deepest_level_pct: int = 6
    horizon_minutes: int = 2_880
    high_probability_threshold: float = 0.60
    post_selection_familywise_alpha: float = 0.0125
    min_oos_rows: int = 15_000
    min_train_rows: int = 3_000
    min_split_rows: int = 300
    min_class_rows_per_split: int = 30
    min_auc: float = 0.56
    min_log_loss_improvement: float = 0.003
    min_brier_improvement: float = 0.001
    max_ece: float = 0.05
    min_high_probability_rows: int = 500
    min_high_probability_observed_rate: float = 0.58
    min_high_probability_wilson_lower_95: float = 0.55
    max_high_probability_calibration_gap: float = 0.05
    null_permutations: int = 1_999

    def __post_init__(self) -> None:
        if (self.primary_grid_step_pct, self.primary_deepest_level_pct) != (3, 6):
            raise ValueError("continuation primary state is frozen to 3->6")
        if self.horizon_minutes != 2_880:
            raise ValueError("continuation horizon is frozen to 48 hours")
        if self.post_selection_familywise_alpha != 0.05 / 4.0:
            raise ValueError("alpha must adjust two viewed arms and two null endpoints")
        if self.null_permutations < 1_999:
            raise ValueError("continuation null requires at least 1999 permutations")
        if self.study_direction not in {"long", "short"}:
            raise ValueError("continuation direction must be long or short")


def build_mirrored_continuation_spec() -> ContinuationExperimentSpec:
    return ContinuationExperimentSpec(
        protocol_version="mirrored_rally_continuation_nature_v1",
        protocol_freeze_id="mirrored_rally_continuation_nature_20260803_v1",
        label_schema_version="mirrored_rally_hold_outperforms_exit_48h_v1",
        feature_schema_version="mirrored_rally_causal_features_v1",
        study_direction="short",
    )


def build_continuation_probability_config(
    *,
    arm: str,
    spec: ContinuationExperimentSpec = ContinuationExperimentSpec(),
    stage1_spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> BinaryWeeklyWalkForwardConfig:
    """Freeze structural-primary or full-causal challenger probability arms."""

    source = build_stage1_probability_config(
        arm=arm,
        spec=stage1_spec,
        direction=spec.study_direction,
    )
    return BinaryWeeklyWalkForwardConfig(
        protocol_freeze_id=f"{spec.protocol_freeze_id}:{arm}",
        strategy_name=f"{spec.study_direction}_ladder_continuation:{arm}",
        target_name="hold_outperforms_exit_48h",
        development_start_ms=stage1_spec.development_start_ms,
        oos_start_ms=stage1_spec.internal_wfa_start_ms,
        oos_end_ms=stage1_spec.internal_wfa_end_ms,
        numeric_features=source.numeric_features,
        categorical_features=source.categorical_features,
        breakdown_columns=source.breakdown_columns,
        required_true_columns=("continuation_label_available",),
        group_column="candidate_id",
        weight_group_column="parent_event_id",
        split_group_column="parent_event_id",
        symbol_column="symbol",
        snapshot_time_column="snapshot_time_ms",
        feature_cutoff_time_column="feature_cutoff_time_ms",
        future_start_time_column="future_start_time_ms",
        resolution_time_column="continuation_resolution_time_ms",
        label_column="hold_outperforms_exit_48h",
        label_available_column="continuation_label_available",
        row_filter_column="is_primary_continuation_state",
        required_label_schema_column="continuation_label_schema_version",
        required_label_schema_value=spec.label_schema_version,
        required_input_schema_column="feature_schema_version",
        required_input_schema_value=spec.feature_schema_version,
        label_only_columns=(
            *source.label_only_columns,
            "continuation_resolution_time_ms",
            "hold_minus_exit_48h",
            "hold_outperforms_exit_48h",
            "continuation_label_available",
        ),
        min_train_rows=spec.min_train_rows,
        min_split_rows=spec.min_split_rows,
        min_class_rows_per_split=spec.min_class_rows_per_split,
        fit_fraction=0.60,
        validation_fraction=0.20,
        catboost_iterations=stage1_spec.catboost_iterations,
        catboost_depth=stage1_spec.catboost_depth,
        catboost_learning_rate=stage1_spec.catboost_learning_rate,
        catboost_l2_leaf_reg=stage1_spec.catboost_l2_leaf_reg,
        catboost_early_stopping_rounds=stage1_spec.catboost_early_stopping_rounds,
        catboost_thread_count=stage1_spec.catboost_thread_count,
        calibration_method=stage1_spec.calibration_method,
        null_permutations=spec.null_permutations,
        gates=BinaryProbabilityGates(
            min_oos_rows=spec.min_oos_rows,
            max_skipped_oos_week_fraction=0.0,
            min_auc=spec.min_auc,
            min_log_loss_improvement=spec.min_log_loss_improvement,
            min_brier_improvement=spec.min_brier_improvement,
            max_ece=spec.max_ece,
            max_auc_permutation_p_value=spec.post_selection_familywise_alpha,
            max_log_loss_permutation_p_value=spec.post_selection_familywise_alpha,
            high_probability_threshold=spec.high_probability_threshold,
            min_high_probability_rows=spec.min_high_probability_rows,
            min_high_probability_observed_rate=spec.min_high_probability_observed_rate,
            min_high_probability_wilson_lower_95=(
                spec.min_high_probability_wilson_lower_95
            ),
            max_high_probability_calibration_gap=(
                spec.max_high_probability_calibration_gap
            ),
        ),
    )


def build_continuation_paired_config(
    spec: ContinuationExperimentSpec = ContinuationExperimentSpec(),
) -> PairedProbabilityComparisonConfig:
    return PairedProbabilityComparisonConfig(
        protocol_freeze_id=f"{spec.protocol_freeze_id}:full_minus_structural",
        bootstrap_iterations=4_000,
        sign_flip_iterations=1_999,
        alpha=0.05 / 3.0,
        min_auc_delta=0.01,
        min_log_loss_improvement=0.002,
        min_brier_improvement=0.001,
        random_seed=20260803,
    )


def write_continuation_protocol(
    directory: str | Path,
    spec: ContinuationExperimentSpec = ContinuationExperimentSpec(),
    stage1_spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> tuple[Path, Path, Path, Path]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    structural = build_continuation_probability_config(
        arm="structural_baseline", spec=spec, stage1_spec=stage1_spec
    )
    full = build_continuation_probability_config(
        arm="full_causal", spec=spec, stage1_spec=stage1_spec
    )
    paired = build_continuation_paired_config(spec)
    payloads: tuple[tuple[Path, object], ...] = (
        (root / "structural_probability_config.json", asdict(structural)),
        (root / "full_probability_config.json", asdict(full)),
        (root / "full_minus_structural_config.json", asdict(paired)),
        (
            root / "frozen_continuation_protocol.json",
            {
                "spec": asdict(spec),
                "stage1_spec": asdict(stage1_spec),
                "status": "FROZEN_POST_SELECTION_PREDICTION_DESIGN",
                "economic_target": (
                    f"signed_{spec.study_direction}_future_return_2880m minus "
                    f"signed_{spec.study_direction}_snapshot_exit_return"
                ),
                "primary_population": "exact 3% grid through 6% state",
                "primary_arm": "structural_baseline",
                "challenger_arm": "full_causal; may replace primary only if all paired gates pass",
                "scientific_sequence": (
                    "predict continuation sign; then and only then preregister magnitude EV"
                ),
                "selection_adjustment": "0.05 / 2 viewed arms / 2 null endpoints",
                "physical_exits_or_pnl_simulated": False,
                "oos_2026_accessed": False,
            },
        ),
    )
    paths: list[Path] = []
    for path, payload in payloads:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
        paths.append(path)
    return paths[0], paths[1], paths[2], paths[3]


def assemble_continuation_dataset(
    *,
    features: pd.DataFrame,
    outcomes: pd.DataFrame,
    spec: ContinuationExperimentSpec = ContinuationExperimentSpec(),
) -> pd.DataFrame:
    outcome = outcomes.rename(
        columns={
            "parent_event_id": "outcome_parent_event_id",
            "symbol": "outcome_symbol",
            "snapshot_time_ms": "outcome_snapshot_time_ms",
            "feature_cutoff_time_ms": "stage0_trigger_cutoff_time_ms",
            "future_start_time_ms": "outcome_future_start_time_ms",
        }
    )
    work = features.merge(outcome, on="candidate_id", how="left", validate="one_to_one")
    if len(work) != len(features) or work["horizon_complete"].isna().any():
        raise ContinuationExperimentError("continuation outcome join is incomplete")
    for left, right in (
        ("parent_event_id", "outcome_parent_event_id"),
        ("symbol", "outcome_symbol"),
        ("snapshot_time_ms", "outcome_snapshot_time_ms"),
        ("future_start_time_ms", "outcome_future_start_time_ms"),
    ):
        if not work[left].astype(str).eq(work[right].astype(str)).all():
            raise ContinuationExperimentError(f"continuation join mismatch: {left}")
    if work["untouched_2026_row_used"].astype(bool).any():
        raise ContinuationExperimentError("continuation source reports 2026 OOS access")
    if not work["feature_cutoff_time_ms"].le(work["snapshot_time_ms"]).all():
        raise ContinuationExperimentError("continuation feature cutoff exceeds snapshot")
    if not work["stage0_trigger_cutoff_time_ms"].le(work["snapshot_time_ms"]).all():
        raise ContinuationExperimentError("continuation trigger cutoff exceeds snapshot")
    if not work["future_start_time_ms"].gt(work["snapshot_time_ms"]).all():
        raise ContinuationExperimentError("continuation future starts at or before snapshot")
    primary = work["grid_step_pct"].eq(spec.primary_grid_step_pct) & work[
        "deepest_filled_level_pct"
    ].eq(spec.primary_deepest_level_pct)
    complete = work["horizon_complete"].astype(bool) & work[
        "label_available_2880m"
    ].astype(bool)
    work = work.loc[primary & complete].copy()
    if work.empty:
        raise ContinuationExperimentError("primary continuation population is empty")
    continuation_resolution = (
        work["snapshot_time_ms"].astype(np.int64) + spec.horizon_minutes * 60_000
    )
    oos_start = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000)
    if continuation_resolution.ge(oos_start).any():
        raise ContinuationExperimentError("continuation label reaches untouched 2026 OOS")
    direction_sign = 1.0 if spec.study_direction == "long" else -1.0
    snapshot_exit_return = (
        direction_sign * work["snapshot_close_to_entry"].astype(float)
    )
    advantage = work["future_return_2880m"].astype(float) - snapshot_exit_return
    if not np.isfinite(advantage).all():
        raise ContinuationExperimentError("continuation advantage contains non-finite values")
    work["continuation_label_schema_version"] = spec.label_schema_version
    work["is_primary_continuation_state"] = True
    work["continuation_resolution_time_ms"] = continuation_resolution
    work["hold_minus_exit_48h"] = advantage
    work["signed_snapshot_exit_return"] = snapshot_exit_return
    work["hold_outperforms_exit_48h"] = advantage.gt(0.0)
    work["continuation_label_available"] = True
    drop = [
        "outcome_parent_event_id",
        "outcome_symbol",
        "outcome_snapshot_time_ms",
        "outcome_future_start_time_ms",
        "horizon_complete",
        "label_available_2880m",
        "future_return_2880m",
        "untouched_2026_row_used",
    ]
    result = work.drop(columns=drop).sort_values(
        ["snapshot_time_ms", "symbol", "candidate_id"], kind="mergesort"
    )
    if result[["candidate_id", "snapshot_time_ms"]].duplicated().any():
        raise ContinuationExperimentError("continuation candidate snapshots are not unique")
    return result.reset_index(drop=True)


def build_continuation_dataset(
    *,
    stage1_dataset_path: str | Path,
    stage0_outcomes_path: str | Path,
    output_dir: str | Path,
    spec: ContinuationExperimentSpec = ContinuationExperimentSpec(),
    stage1_spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> Path:
    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise ContinuationExperimentError(f"continuation output must be absent or empty: {root}")
    revision, dirty = _repository_state()
    if dirty:
        raise ContinuationExperimentError(
            "continuation evidence requires a clean committed worktree"
        )
    stage1_path = Path(stage1_dataset_path)
    outcomes_path = Path(stage0_outcomes_path)
    features = pd.read_parquet(stage1_path)
    outcomes = pd.read_parquet(
        outcomes_path,
        columns=[
            "candidate_id",
            "parent_event_id",
            "symbol",
            "snapshot_time_ms",
            "feature_cutoff_time_ms",
            "future_start_time_ms",
            "horizon_complete",
            "label_available_2880m",
            "future_return_2880m",
            "untouched_2026_row_used",
        ],
    )
    dataset = assemble_continuation_dataset(features=features, outcomes=outcomes, spec=spec)
    root.mkdir(parents=True, exist_ok=True)
    dataset_path = root / "continuation_dataset.parquet"
    temporary = dataset_path.with_suffix(dataset_path.suffix + ".tmp")
    dataset.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, dataset_path)
    protocol_paths = write_continuation_protocol(
        root / "probability_configs", spec, stage1_spec
    )
    audit_path = root / "temporal_audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "row_count": len(dataset),
                "feature_cutoff_le_snapshot": True,
                "trigger_cutoff_le_snapshot": True,
                "future_start_gt_snapshot": True,
                "label_resolution_before_2026": True,
                "oos_2026_rows_read": 0,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    provenance_path = root / "source_provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "code_commit": revision,
                "working_tree_dirty": dirty,
                "source_sha256": {
                    "stage1": sha256_file(stage1_path),
                    "stage0_outcomes": sha256_file(outcomes_path),
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    paths = [dataset_path, audit_path, provenance_path, *protocol_paths]
    manifest = build_manifest(
        run_id="drawdown-continuation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=paths,
        root=root,
    )
    write_manifest(root / "continuation.manifest.json", manifest)
    return root


def _repository_state() -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout
    return revision, bool(status.strip())


__all__ = [
    "ContinuationExperimentError",
    "ContinuationExperimentSpec",
    "assemble_continuation_dataset",
    "build_continuation_dataset",
    "build_mirrored_continuation_spec",
    "build_continuation_paired_config",
    "build_continuation_probability_config",
    "write_continuation_protocol",
]
