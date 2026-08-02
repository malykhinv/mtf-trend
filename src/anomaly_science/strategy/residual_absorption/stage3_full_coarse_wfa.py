"""Full-population internal-IS weekly WFA for coarse residual features."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

import pandas as pd

from anomaly_science.probability import (
    build_binary_weekly_walk_forward,
    build_gate_rows,
    build_null_test_rows,
    build_prediction_metrics,
    build_reliability_rows,
)
from anomaly_science.strategy.residual_absorption.stage3_paired_wfa import (
    LABEL_SCHEMA_VERSION,
    event_recurrence_chains,
    paired_wfa_config,
    select_coarse_numeric_features,
)

FULL_COARSE_FREEZE_ID = "residual_absorption_internal_is_full_coarse_wfa_v1"


def build_full_coarse_wfa_input(
    *, stage1_dir: str | Path
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    root = Path(stage1_dir)
    features = pd.read_parquet(root / "stage2_model_features_with_local_activity_is.parquet")
    labels = pd.read_parquet(root / "stage1_trait_labels_is.parquet")
    frame = features.merge(
        labels[
            [
                "event_id",
                "symbol",
                "event_snapshot_time_ms",
                "resolution_time_ms",
                "response_win_60m",
            ]
        ],
        on=["event_id", "symbol"],
        how="inner",
        validate="one_to_one",
    )
    if len(frame) != len(features):
        raise ValueError("full coarse WFA lost feature rows during label join")
    if bool((frame["event_snapshot_time_ms"] != frame["snapshot_time_ms"]).any()):
        raise ValueError("full coarse WFA labels do not align to feature snapshots")
    frame = frame.merge(
        event_recurrence_chains(frame[["event_id", "snapshot_time_ms"]]),
        on="event_id",
        how="left",
        validate="many_to_one",
    )
    frame["group"] = frame["event_id"].astype(str) + "|" + frame["symbol"].astype(str)
    frame["nature_future_start_time_ms"] = frame["snapshot_time_ms"] + 5 * 60_000
    frame["nature_resolution_time_ms"] = frame["resolution_time_ms"].astype("int64")
    frame["nature_y"] = frame["response_win_60m"].astype("int8")
    frame["nature_label_available"] = True
    frame["nature_label_schema_version"] = LABEL_SCHEMA_VERSION
    frame["is_nature_anchor"] = True
    numeric_features = select_coarse_numeric_features(features)
    if not numeric_features:
        raise ValueError("full coarse WFA numeric feature family is empty")
    return frame, numeric_features


def run_stage3_full_coarse_wfa(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    output = root / "stage3_internal_is_full_coarse_wfa_v1"
    if output.exists():
        raise FileExistsError(f"full coarse WFA output already exists: {output}")
    frame, numeric_features = build_full_coarse_wfa_input(stage1_dir=root)
    config = replace(
        paired_wfa_config(arm="full_coarse", numeric_features=numeric_features),
        protocol_freeze_id=FULL_COARSE_FREEZE_ID,
        weight_group_column="event_id",
    )
    result = build_binary_weekly_walk_forward(frame, config)
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
    output.mkdir(parents=True)
    frame.to_parquet(output / "full_coarse_wfa_input_is.parquet", index=False, compression="zstd")
    result.predictions.to_parquet(
        output / "internal_is_predictions.parquet", index=False, compression="zstd"
    )
    result.weekly_metadata.to_csv(output / "weekly_metadata.csv", index=False)
    result.feature_importance.to_csv(output / "feature_importance.csv", index=False)
    metrics.to_csv(output / "prediction_metrics.csv", index=False)
    reliability.to_csv(output / "reliability.csv", index=False)
    null_tests.to_csv(output / "null_tests.csv", index=False)
    gates.to_csv(output / "gates.csv", index=False)
    (output / "config.json").write_text(
        json.dumps(asdict(config), indent=2), encoding="utf-8"
    )
    scored = result.weekly_metadata.loc[
        result.weekly_metadata["status"].eq("FROZEN_AND_SCORED")
    ]
    overall = metrics.loc[
        metrics["slice"].eq("overall") & metrics["slice_value"].eq("all")
    ]
    report = {
        "protocol_freeze_id": FULL_COARSE_FREEZE_ID,
        "partition": "internal_is_walk_forward",
        "untouched_2026_oos_accessed": False,
        "input_row_count": len(frame),
        "input_max_snapshot_time_ms": int(frame["snapshot_time_ms"].max()),
        "numeric_feature_count": len(numeric_features),
        "categorical_feature_count": len(config.categorical_features),
        "prediction_count": len(result.predictions),
        "frozen_week_count": len(result.frozen_models),
        "skipped_week_count": int(result.weekly_metadata["status"].eq("SKIPPED").sum()),
        "event_weight_normalization": True,
        "train_labels_resolved_before_freeze": bool(
            len(scored)
            and (
                scored["latest_train_resolution_time_ms"]
                < scored["weekly_model_freeze_time_ms"]
            ).all()
        ),
        "all_probability_gates_pass": bool(len(gates) and gates["status"].eq("PASS").all()),
        "overall_metrics": overall.to_dict(orient="records"),
        "gates": gates.to_dict(orient="records"),
        "scientific_scope": (
            "Full coarse response probability inside IS only; no EV, trade, or PnL claim."
        ),
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full-population coarse internal-IS WFA.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(run_stage3_full_coarse_wfa(stage1_dir=args.stage1_dir))


__all__ = ["build_full_coarse_wfa_input", "run_stage3_full_coarse_wfa"]


if __name__ == "__main__":
    main()
