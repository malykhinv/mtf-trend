"""Causal feature matrix and forward-stable response win/loss trait report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from scipy.stats import mannwhitneyu

TRAIT_REPORT_VERSION = "residual_response_win_loss_traits_v1"
FORWARD_VALIDATION_MONTHS = ("2025-08", "2025-09", "2025-10", "2025-11", "2025-12")

PROFILE_FEATURES = (
    "history_observation_count",
    "short_history_observation_count",
    "beta_15m",
    "beta_short_15m",
    "beta_recent_minus_long",
    "alpha_15m",
    "factor_correlation_15m",
    "factor_r_squared_15m",
    "current_symbol_return_15m",
    "current_leave_one_out_factor_return_15m",
    "expected_symbol_return_15m",
    "response_residual_15m",
    "direction_adjusted_residual_15m",
    "direction_adjusted_underreaction_15m",
)

CROSS_SECTION_FEATURES = (
    "quote_volume_5m",
    "return_5m",
    "return_30m",
    "session_elapsed_5m_bars",
    "current_session_quote_volume",
    "current_session_coverage",
    "prior_session_quote_volume_median",
    "prior_session_quote_volume_mad_ratio",
    "prior_session_history_count",
    "prior_elapsed_quote_volume_median",
    "prior_elapsed_history_count",
    "current_activity_ratio",
    "trailing_24h_quote_volume",
    "core_liquidity_rank",
    "current_liquidity_rank",
    "activity_expansion_rank",
)

EVENT_FEATURES = (
    "factor_robust_z_15m",
    "directional_breadth_15m",
    "cross_section_symbol_count",
    "reference_support_count",
)

DERIVED_FEATURES = (
    "absolute_underreaction_15m",
    "absolute_beta_recent_minus_long",
    "absolute_factor_robust_z_15m",
    "event_underreaction_percentile",
    "event_absolute_underreaction_percentile",
)


def build_stage1_trait_artifacts(*, stage1_dir: str | Path) -> tuple[Path, Path]:
    root = Path(stage1_dir)
    feature_path = root / "stage1_trait_features_is.parquet"
    label_path = root / "stage1_trait_labels_is.parquet"
    profiles = pd.read_parquet(root / "residual_response_profiles_is.parquet")
    event_times = sorted(profiles["snapshot_time_ms"].astype("int64").unique())
    current = (
        pl.scan_parquet(root / "cross_section_5m_is.parquet")
        .filter(
            pl.col("snapshot_time_ms").is_in(event_times)
            & pl.col("candidate_eligible")
        )
        .select(
            "symbol",
            "snapshot_time_ms",
            *CROSS_SECTION_FEATURES,
            "core_eligible",
            "activity_expansion_eligible",
        )
        .collect(engine="streaming")
        .to_pandas()
    )
    events = pd.read_parquet(root / "market_impulse_events_is.parquet")
    features = profiles[
        [
            "event_id",
            "symbol",
            "snapshot_time_ms",
            "feature_cutoff_time_ms",
            "session_seq",
            "session_name",
            "impulse_direction",
            *PROFILE_FEATURES,
        ]
    ].merge(
        current,
        on=["symbol", "snapshot_time_ms"],
        how="left",
        validate="one_to_one",
    ).merge(
        events[
            [
                "event_id",
                *EVENT_FEATURES,
                "reference_confirmed",
            ]
        ],
        on="event_id",
        validate="many_to_one",
    )
    if features[list(CROSS_SECTION_FEATURES)].isna().all(axis=1).any():
        raise ValueError("trait feature join missed a registered event/symbol row")
    features["absolute_underreaction_15m"] = features[
        "direction_adjusted_underreaction_15m"
    ].abs()
    features["absolute_beta_recent_minus_long"] = features[
        "beta_recent_minus_long"
    ].abs()
    features["absolute_factor_robust_z_15m"] = features["factor_robust_z_15m"].abs()
    features["event_underreaction_percentile"] = features.groupby("event_id")[
        "direction_adjusted_underreaction_15m"
    ].rank(method="average", pct=True)
    features["event_absolute_underreaction_percentile"] = features.groupby("event_id")[
        "absolute_underreaction_15m"
    ].rank(method="average", pct=True)
    features["candidate_channel"] = np.select(
        [
            features["core_eligible"] & features["activity_expansion_eligible"],
            features["core_eligible"],
            features["activity_expansion_eligible"],
        ],
        ["core_and_expansion", "core_only", "expansion_only"],
        default="invalid",
    )
    features["calendar_month"] = pd.to_datetime(
        features["snapshot_time_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    if bool((features["feature_cutoff_time_ms"] > features["snapshot_time_ms"]).any()):
        raise ValueError("trait feature cutoff exceeds snapshot")
    features.to_parquet(feature_path, index=False, compression="zstd")

    outcomes = pd.read_parquet(root / "response_outcomes_is.parquet")
    labels = outcomes[
        [
            "event_id",
            "symbol",
            "event_snapshot_time_ms",
            "resolution_time_ms",
            "catchup_residual_change_60m",
        ]
    ].copy()
    labels["response_win_60m"] = labels["catchup_residual_change_60m"] > 0.0
    if bool(
        (
            labels["resolution_time_ms"].notna()
            & (labels["resolution_time_ms"] <= labels["event_snapshot_time_ms"])
        ).any()
    ):
        raise ValueError("trait label must resolve strictly after its event")
    labels.to_parquet(label_path, index=False, compression="zstd")
    return feature_path, label_path


def build_stage1_trait_report(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    feature_path, label_path = build_stage1_trait_artifacts(stage1_dir=root)
    features = pd.read_parquet(feature_path)
    labels = pd.read_parquet(label_path)
    memory_path = root / "causal_symbol_context_memory_is.parquet"
    memory_features: tuple[str, ...] = ()
    if memory_path.is_file():
        memory = pd.read_parquet(memory_path)
        forbidden_memory_columns = {
            "schema_version",
            "event_id",
            "symbol",
            "snapshot_time_ms",
            "feature_cutoff_time_ms",
            "maximum_resolution_time_used_ms",
        }
        memory_features = tuple(
            column for column in memory.columns if column not in forbidden_memory_columns
        )
        features = features.merge(
            memory[["event_id", "symbol", *memory_features]],
            on=["event_id", "symbol"],
            validate="one_to_one",
        )
        features.to_parquet(
            root / "stage1_model_features_with_causal_memory_is.parquet",
            index=False,
            compression="zstd",
        )
    local_path = root / "stage2_local_activity_features_is.parquet"
    local_features: tuple[str, ...] = ()
    if local_path.is_file():
        local = pd.read_parquet(local_path)
        forbidden_local_columns = {
            "schema_version",
            "event_id",
            "symbol",
            "snapshot_time_ms",
            "feature_cutoff_time_ms",
            "impulse_direction",
        }
        local_features = tuple(
            column for column in local.columns if column not in forbidden_local_columns
        )
        features = features.merge(
            local[["event_id", "symbol", *local_features]],
            on=["event_id", "symbol"],
            validate="one_to_one",
        )
        features.to_parquet(
            root / "stage2_model_features_with_local_activity_is.parquet",
            index=False,
            compression="zstd",
        )
    frame = features.merge(
        labels[["event_id", "symbol", "response_win_60m"]],
        on=["event_id", "symbol"],
        validate="one_to_one",
    )
    numeric_features = (
        *PROFILE_FEATURES,
        *CROSS_SECTION_FEATURES,
        *EVENT_FEATURES,
        *DERIVED_FEATURES,
        *memory_features,
        *local_features,
    )
    rows = [_numeric_trait(frame, feature) for feature in numeric_features]
    _apply_benjamini_hochberg(rows)
    for row in rows:
        if not row["analyzable"]:
            row["monthly_effects"] = []
            row["forward_checks"] = []
            row["monthly_same_sign_count"] = 0
            row["forward_sign_agreement_count"] = 0
            row["stable_trait"] = False
            continue
        overall_sign = int(np.sign(float(row["rank_biserial_effect"])))
        monthly = _monthly_effects(frame, str(row["feature"]))
        forward = _forward_sign_checks(frame, str(row["feature"]))
        row["monthly_effects"] = monthly
        row["forward_checks"] = forward
        row["monthly_same_sign_count"] = sum(
            np.isfinite(float(item["rank_biserial_effect"]))
            and int(np.sign(item["rank_biserial_effect"])) == overall_sign
            and overall_sign != 0
            for item in monthly
        )
        row["forward_sign_agreement_count"] = sum(item["sign_agrees"] for item in forward)
        row["stable_trait"] = bool(
            float(row["coverage"]) >= 0.80
            and float(row["bh_q_value"]) <= 0.05
            and int(row["monthly_same_sign_count"]) >= 6
            and int(row["forward_sign_agreement_count"]) >= 4
        )
    categorical = [
        _categorical_trait(frame, feature)
        for feature in (
            "session_name",
            "impulse_direction",
            "reference_confirmed",
            "core_eligible",
            "activity_expansion_eligible",
            "candidate_channel",
        )
    ]
    stable = [
        {
            "feature": row["feature"],
            "rank_biserial_effect": row["rank_biserial_effect"],
            "win_median": row["win_median"],
            "loss_median": row["loss_median"],
            "monthly_same_sign_count": row["monthly_same_sign_count"],
            "forward_sign_agreement_count": row["forward_sign_agreement_count"],
            "bh_q_value": row["bh_q_value"],
        }
        for row in rows
        if row["stable_trait"]
    ]
    stable.sort(key=lambda item: abs(float(item["rank_biserial_effect"])), reverse=True)
    report = {
        "report_version": TRAIT_REPORT_VERSION,
        "partition": "is",
        "label_definition": "catchup_residual_change_60m > 0",
        "label_is_not_an_executed_trade": True,
        "row_count": len(frame),
        "win_count": int(frame["response_win_60m"].sum()),
        "loss_count": int((~frame["response_win_60m"]).sum()),
        "stable_trait_count": len(stable),
        "causal_memory_feature_count": len(memory_features),
        "local_activity_feature_count": len(local_features),
        "stable_traits": stable,
        "numeric_traits": rows,
        "categorical_traits": categorical,
        "selection_limit": (
            "Traits prioritize ablations and constraints; no outcome-derived threshold is authorized."
        ),
    }
    path = root / "stage1_win_loss_trait_report_is.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _numeric_trait(frame: pd.DataFrame, feature: str) -> dict[str, object]:
    values = pd.to_numeric(frame[feature], errors="coerce")
    valid = values.notna() & np.isfinite(values)
    wins = values.loc[valid & frame["response_win_60m"]].to_numpy(dtype=float)
    losses = values.loc[valid & ~frame["response_win_60m"]].to_numpy(dtype=float)
    if not len(wins) or not len(losses):
        return {
            "feature": feature,
            "coverage": float(valid.mean()),
            "win_count": len(wins),
            "loss_count": len(losses),
            "analyzable": False,
            "win_median": None,
            "loss_median": None,
            "rank_biserial_effect": None,
            "mann_whitney_p_value": None,
        }
    test = mannwhitneyu(wins, losses, alternative="two-sided", method="asymptotic")
    effect = 2.0 * float(test.statistic) / (len(wins) * len(losses)) - 1.0
    return {
        "feature": feature,
        "coverage": float(valid.mean()),
        "win_count": len(wins),
        "loss_count": len(losses),
        "analyzable": True,
        "win_median": float(np.median(wins)),
        "loss_median": float(np.median(losses)),
        "rank_biserial_effect": effect,
        "mann_whitney_p_value": float(test.pvalue),
    }


def _effect_for_subset(frame: pd.DataFrame, feature: str) -> float:
    values = pd.to_numeric(frame[feature], errors="coerce")
    valid = values.notna() & np.isfinite(values)
    wins = values.loc[valid & frame["response_win_60m"]].to_numpy(dtype=float)
    losses = values.loc[valid & ~frame["response_win_60m"]].to_numpy(dtype=float)
    if not len(wins) or not len(losses):
        return float("nan")
    statistic = mannwhitneyu(wins, losses, alternative="two-sided", method="asymptotic").statistic
    return 2.0 * float(statistic) / (len(wins) * len(losses)) - 1.0


def _monthly_effects(frame: pd.DataFrame, feature: str) -> list[dict[str, object]]:
    return [
        {
            "month": str(month),
            "row_count": len(group),
            "rank_biserial_effect": _effect_for_subset(group, feature),
        }
        for month, group in frame.groupby("calendar_month", sort=True)
    ]


def _forward_sign_checks(frame: pd.DataFrame, feature: str) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for month in FORWARD_VALIDATION_MONTHS:
        train = frame.loc[frame["calendar_month"].lt(month)]
        validation = frame.loc[frame["calendar_month"].eq(month)]
        train_effect = _effect_for_subset(train, feature)
        validation_effect = _effect_for_subset(validation, feature)
        checks.append(
            {
                "validation_month": month,
                "train_effect": train_effect,
                "validation_effect": validation_effect,
                "sign_agrees": bool(
                    np.isfinite(train_effect)
                    and np.isfinite(validation_effect)
                    and np.sign(train_effect) != 0
                    and np.sign(train_effect) == np.sign(validation_effect)
                ),
            }
        )
    return checks


def _apply_benjamini_hochberg(rows: list[dict[str, object]]) -> None:
    analyzable_indices = [index for index, row in enumerate(rows) if row["analyzable"]]
    p_values = np.asarray(
        [float(rows[index]["mann_whitney_p_value"]) for index in analyzable_indices]
    )
    order = np.argsort(p_values, kind="mergesort")
    adjusted = np.empty(len(analyzable_indices), dtype=float)
    running = 1.0
    for reverse_rank in range(len(analyzable_indices) - 1, -1, -1):
        original = order[reverse_rank]
        rank = reverse_rank + 1
        running = min(running, p_values[original] * len(analyzable_indices) / rank)
        adjusted[original] = min(running, 1.0)
    for row in rows:
        row["bh_q_value"] = None
    for index, q_value in zip(analyzable_indices, adjusted, strict=True):
        rows[index]["bh_q_value"] = float(q_value)


def _categorical_trait(frame: pd.DataFrame, feature: str) -> dict[str, object]:
    levels = []
    for value, group in frame.groupby(feature, dropna=False, sort=True):
        levels.append(
            {
                "value": None if pd.isna(value) else str(value),
                "count": len(group),
                "response_win_rate_60m": float(group["response_win_60m"].mean()),
            }
        )
    return {"feature": feature, "levels": levels}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build causal stage-1 win/loss traits.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(build_stage1_trait_report(stage1_dir=args.stage1_dir))


__all__ = [
    "TRAIT_REPORT_VERSION",
    "build_stage1_trait_artifacts",
    "build_stage1_trait_report",
]


if __name__ == "__main__":
    main()
