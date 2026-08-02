"""Post-gate explanatory diagnostics for drawdown-ladder Stage 1."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import polars as pl
from sklearn.metrics import log_loss, roc_auc_score

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.strategy.drawdown_ladder.stage1_spec import DrawdownLadderStage1Spec


class Stage1DiagnosticError(ValueError):
    """Raised when explanatory diagnostics could mix populations or imply a gate."""


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


def _read_prediction_arm(directory: Path, expected_id: str) -> tuple[pd.DataFrame, Path]:
    prediction_path = directory / "oos_predictions.parquet"
    protocol_path = directory / "frozen_probability_protocol.json"
    audit_path = directory / "temporal_audit.csv"
    metadata_path = directory / "binary_probability.metadata.json"
    if not all(
        path.is_file()
        for path in (prediction_path, protocol_path, audit_path, metadata_path)
    ):
        raise Stage1DiagnosticError(f"incomplete probability arm: {directory}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if protocol.get("protocol_freeze_id") != expected_id:
        raise Stage1DiagnosticError(f"probability protocol mismatch: {directory}")
    if metadata.get("evidence_status") != "FROZEN_DEVELOPMENT":
        raise Stage1DiagnosticError(f"probability arm is not frozen: {directory}")
    audit = pd.read_csv(audit_path)
    if audit.empty or not audit["status"].eq("PASS").all():
        raise Stage1DiagnosticError(f"probability temporal audit failed: {directory}")
    return pd.read_parquet(prediction_path), prediction_path


def _paired_predictions(
    structural: pd.DataFrame,
    full: pd.DataFrame,
) -> pd.DataFrame:
    keys = [
        "group",
        "symbol",
        "snapshot_time_ms",
        "test_week",
        "target",
        "weekly_model_freeze_time_ms",
        "parent_event_id",
        "grid_step_pct",
        "deepest_filled_level_pct",
        "session_name",
    ]
    for name, frame in (("structural", structural), ("full", full)):
        missing = sorted(set(keys + ["calibrated_probability"]) - set(frame.columns))
        if missing:
            raise Stage1DiagnosticError(f"{name} diagnostic input missing columns: {missing}")
        if frame[keys[:4]].duplicated().any():
            raise Stage1DiagnosticError(f"{name} diagnostic prediction keys are duplicated")
    paired = structural.loc[:, [*keys, "calibrated_probability"]].rename(
        columns={"calibrated_probability": "structural_probability"}
    ).merge(
        full.loc[:, [*keys, "calibrated_probability"]].rename(
            columns={"calibrated_probability": "full_probability"}
        ),
        on=keys,
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    if not paired["_merge"].eq("both").all():
        raise Stage1DiagnosticError("diagnostic probability populations differ")
    return paired.drop(columns="_merge")


def _metric_row(
    frame: pd.DataFrame,
    *,
    context_type: str,
    context_value: str,
) -> dict[str, object]:
    target = frame["target"].to_numpy(dtype=int)
    structural = frame["structural_probability"].to_numpy(dtype=float)
    full = frame["full_probability"].to_numpy(dtype=float)
    class_counts = np.bincount(target, minlength=2)
    auc_defined = bool((class_counts > 0).all())
    structural_auc = float(roc_auc_score(target, structural)) if auc_defined else math.nan
    full_auc = float(roc_auc_score(target, full)) if auc_defined else math.nan
    structural_log = float(log_loss(target, structural, labels=[0, 1]))
    full_log = float(log_loss(target, full, labels=[0, 1]))
    structural_brier = float(np.mean((structural - target) ** 2))
    full_brier = float(np.mean((full - target) ** 2))
    return {
        "context_type": context_type,
        "context_value": context_value,
        "row_count": len(frame),
        "parent_event_count": frame["parent_event_id"].nunique(),
        "week_count": frame["test_week"].nunique(),
        "recovery_count": int(target.sum()),
        "failure_count": int((1 - target).sum()),
        "recovery_rate": float(target.mean()),
        "auc_defined": auc_defined,
        "structural_auc": structural_auc,
        "full_auc": full_auc,
        "full_minus_structural_auc": full_auc - structural_auc,
        "structural_log_loss": structural_log,
        "full_log_loss": full_log,
        "full_minus_structural_log_loss": full_log - structural_log,
        "structural_brier": structural_brier,
        "full_brier": full_brier,
        "full_minus_structural_brier": full_brier - structural_brier,
    }


def _context_diagnostics(paired: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = paired.copy()
    work["month"] = pd.to_datetime(
        work["snapshot_time_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    work["exact_state"] = (
        work["grid_step_pct"].astype(str)
        + "->"
        + work["deepest_filled_level_pct"].astype(str)
    )
    rows = [_metric_row(work, context_type="overall", context_value="all")]
    for context_type, column in (
        ("week", "test_week"),
        ("month", "month"),
        ("session", "session_name"),
        ("exact_state", "exact_state"),
    ):
        for value, group in work.groupby(column, sort=True):
            rows.append(
                _metric_row(group, context_type=context_type, context_value=str(value))
            )
    context = pd.DataFrame(rows)
    total_failures = max(1, int((1 - work["target"]).sum()))
    symbol_rows = []
    for symbol, group in work.groupby("symbol", sort=True):
        row = _metric_row(group, context_type="symbol", context_value=str(symbol))
        row["failure_share_of_all_failures"] = row["failure_count"] / total_failures
        row["month_count"] = group["month"].nunique()
        symbol_rows.append(row)
    return context, pd.DataFrame(symbol_rows)


def _win_loss_feature_traits(
    dataset_path: Path,
    catalog: pd.DataFrame,
    spec: DrawdownLadderStage1Spec,
) -> pd.DataFrame:
    numeric = catalog.loc[
        catalog["model_feature"].astype(bool) & ~catalog["dtype"].eq("string")
    ]
    feature_names = numeric["name"].astype(str).tolist()
    label = pl.col("recovery_25bps_48h").cast(pl.Boolean)
    expressions: list[pl.Expr] = []
    for name in feature_names:
        value = pl.col(name).cast(pl.Float64, strict=False)
        finite = value.is_finite()
        for outcome_name, outcome in (("win", label), ("loss", ~label)):
            selected = pl.when(outcome & finite).then(value).otherwise(None)
            expressions.extend(
                (
                    selected.count().alias(f"{name}__{outcome_name}_count"),
                    selected.mean().alias(f"{name}__{outcome_name}_mean"),
                    selected.std(ddof=1).alias(f"{name}__{outcome_name}_std"),
                    (1.0 - finite.filter(outcome).mean()).alias(
                        f"{name}__{outcome_name}_missing_fraction"
                    ),
                )
            )
    values = (
        pl.scan_parquet(dataset_path)
        .filter(
            (pl.col("snapshot_time_ms") >= spec.internal_wfa_start_ms)
            & (pl.col("snapshot_time_ms") < spec.internal_wfa_end_ms)
            & pl.col("label_available")
        )
        .select(expressions)
        .collect()
        .to_dicts()[0]
    )
    catalog_by_name = catalog.set_index("name")
    rows = []
    for name in feature_names:
        win_mean_raw = values[f"{name}__win_mean"]
        loss_mean_raw = values[f"{name}__loss_mean"]
        win_std_raw = values[f"{name}__win_std"]
        loss_std_raw = values[f"{name}__loss_std"]
        win_mean = float(win_mean_raw) if win_mean_raw is not None else math.nan
        loss_mean = float(loss_mean_raw) if loss_mean_raw is not None else math.nan
        win_std = float(win_std_raw) if win_std_raw is not None else math.nan
        loss_std = float(loss_std_raw) if loss_std_raw is not None else math.nan
        pooled = math.sqrt((win_std * win_std + loss_std * loss_std) / 2.0)
        standardized = (
            (win_mean - loss_mean) / pooled
            if math.isfinite(pooled) and pooled > 0.0
            else math.nan
        )
        definition = catalog_by_name.loc[name]
        rows.append(
            {
                "feature_name": name,
                "family": definition["family"],
                "dtype": definition["dtype"],
                "win_count": values[f"{name}__win_count"],
                "loss_count": values[f"{name}__loss_count"],
                "win_mean": win_mean,
                "loss_mean": loss_mean,
                "win_minus_loss_mean": win_mean - loss_mean,
                "pooled_standard_deviation": pooled,
                "standardized_win_minus_loss": standardized,
                "absolute_standardized_difference": abs(standardized),
                "win_missing_fraction": values[f"{name}__win_missing_fraction"],
                "loss_missing_fraction": values[f"{name}__loss_missing_fraction"],
            }
        )
    return pd.DataFrame(rows).sort_values(
        "absolute_standardized_difference",
        ascending=False,
        na_position="last",
    )


def _importance_diagnostics(
    importance_path: Path,
    catalog: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    importance = pd.read_csv(importance_path)
    by_feature = (
        importance.groupby("feature_name", as_index=False)
        .agg(
            mean_importance=("importance", "mean"),
            median_importance=("importance", "median"),
            importance_std=("importance", "std"),
            active_week_count=("test_week", "nunique"),
        )
        .merge(
            catalog.loc[:, ["name", "family"]].rename(columns={"name": "feature_name"}),
            on="feature_name",
            how="left",
            validate="one_to_one",
        )
        .sort_values("mean_importance", ascending=False)
    )
    with_family = importance.merge(
        catalog.loc[:, ["name", "family"]].rename(columns={"name": "feature_name"}),
        on="feature_name",
        how="left",
        validate="many_to_one",
    )
    weekly_family = with_family.groupby(["test_week", "family"], as_index=False)[
        "importance"
    ].sum()
    by_family = (
        weekly_family.groupby("family", as_index=False)
        .agg(
            mean_weekly_importance=("importance", "mean"),
            median_weekly_importance=("importance", "median"),
            weekly_importance_std=("importance", "std"),
            active_week_count=("test_week", "nunique"),
        )
        .sort_values("mean_weekly_importance", ascending=False)
    )
    return by_feature, by_family


def build_stage1_post_gate_diagnostics(
    *,
    dataset_path: str | Path,
    feature_catalog_path: str | Path,
    structural_dir: str | Path,
    full_dir: str | Path,
    output_dir: str | Path,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> Path:
    """Describe wins/losses and degradation without authorizing a filter."""

    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise Stage1DiagnosticError(f"diagnostic output must be absent or empty: {root}")
    revision, dirty = _repository_state()
    if dirty:
        raise Stage1DiagnosticError("Stage-1 diagnostics require a clean committed worktree")
    structural, structural_path = _read_prediction_arm(
        Path(structural_dir),
        f"{spec.protocol_freeze_id}:structural_baseline",
    )
    full, full_path = _read_prediction_arm(
        Path(full_dir),
        f"{spec.protocol_freeze_id}:full_causal",
    )
    paired = _paired_predictions(structural, full)
    dataset = Path(dataset_path)
    catalog_path = Path(feature_catalog_path)
    catalog = pd.read_parquet(catalog_path)
    internal_rows = (
        pl.scan_parquet(dataset)
        .filter(
            (pl.col("snapshot_time_ms") >= spec.internal_wfa_start_ms)
            & (pl.col("snapshot_time_ms") < spec.internal_wfa_end_ms)
            & pl.col("label_available")
        )
        .select(pl.len())
        .collect()
        .item()
    )
    if int(internal_rows) != len(paired):
        raise Stage1DiagnosticError("feature and prediction diagnostic populations differ")
    context, symbols = _context_diagnostics(paired)
    traits = _win_loss_feature_traits(dataset, catalog, spec)
    by_feature, by_family = _importance_diagnostics(
        Path(full_dir) / "feature_importance.csv",
        catalog,
    )
    root.mkdir(parents=True, exist_ok=True)
    paths = [
        _write_csv(root / "win_loss_feature_traits.csv", traits),
        _write_csv(root / "feature_importance_by_feature.csv", by_feature),
        _write_csv(root / "feature_importance_by_family.csv", by_family),
        _write_csv(root / "context_diagnostics.csv", context),
        _write_csv(root / "symbol_diagnostics.csv", symbols),
    ]
    supported = symbols.loc[
        symbols["row_count"].ge(100)
        & symbols["recovery_count"].ge(10)
        & symbols["failure_count"].ge(10)
    ]
    report_path = root / "diagnostic_report.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "POST_GATE_EXPLANATORY_ONLY_NO_FILTER_AUTHORITY",
                "protocol_freeze_id": spec.protocol_freeze_id,
                "row_count": len(paired),
                "symbol_count": paired["symbol"].nunique(),
                "failure_count": int((1 - paired["target"]).sum()),
                "supported_symbol_definition": (
                    "descriptive only: >=100 states, >=10 recoveries, >=10 failures"
                ),
                "supported_symbol_count": len(supported),
                "supported_symbol_share_full_auc_better": float(
                    supported["full_minus_structural_auc"].gt(0.0).mean()
                ),
                "supported_symbol_share_full_log_loss_better": float(
                    supported["full_minus_structural_log_loss"].lt(0.0).mean()
                ),
                "supported_symbol_share_full_brier_better": float(
                    supported["full_minus_structural_brier"].lt(0.0).mean()
                ),
                "interpretation_contract": (
                    "all traits are post-gate associations; none may be promoted to an IS filter"
                ),
                "oos_2026_accessed": False,
                "code_commit": revision,
                "working_tree_dirty": dirty,
                "source_sha256": {
                    "dataset": sha256_file(dataset),
                    "feature_catalog": sha256_file(catalog_path),
                    "structural_predictions": sha256_file(structural_path),
                    "full_predictions": sha256_file(full_path),
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    paths.append(report_path)
    manifest = build_manifest(
        run_id="drawdown-stage1-diagnostics-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=paths,
        root=root,
    )
    write_manifest(root / "diagnostics.manifest.json", manifest)
    return root


__all__ = ["Stage1DiagnosticError", "build_stage1_post_gate_diagnostics"]
