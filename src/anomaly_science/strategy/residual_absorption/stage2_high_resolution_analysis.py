"""Coverage bias and win/loss traits for the preregistered aggTrades layer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

FORWARD_MONTHS = ("2025-08", "2025-09", "2025-10", "2025-11", "2025-12")
REPORT_VERSION = "residual_absorption_aggtrades_analysis_v1"
CAUSAL_COVERAGE_FEATURES = (
    "direction_adjusted_underreaction_15m",
    "event_underreaction_percentile",
    "factor_correlation_15m",
    "factor_r_squared_15m",
    "current_activity_ratio",
    "trailing_24h_quote_volume",
    "local_15m_taker_imbalance",
    "local_15m_realized_volatility",
    "local_15m_aggressive_flow_per_return_floor",
    "posterior_win_probability_same_direction_last_20",
    "mad_catchup_60m_last_20",
)
_HIGH_RESOLUTION_KEYS = {
    "schema_version",
    "event_id",
    "symbol",
    "snapshot_time_ms",
    "feature_cutoff_time_ms",
    "impulse_direction",
    "aggtrades_primary_complete",
}


def build_stage2_high_resolution_analysis(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    coarse = pd.read_parquet(root / "stage2_model_features_with_local_activity_is.parquet")
    high_resolution = pd.read_parquet(root / "stage2_high_resolution_features_is.parquet")
    labels = pd.read_parquet(root / "stage1_trait_labels_is.parquet")
    outcomes = pd.read_parquet(
        root / "response_outcomes_is.parquet",
        columns=["event_id", "symbol", "catchup_residual_change_60m"],
    )
    diagnostics_path = root / "stage1_symbol_context_diagnostics_is.parquet"
    symbol_diagnostics = (
        pd.read_parquet(diagnostics_path)
        if diagnostics_path.is_file()
        else pd.DataFrame(columns=["symbol", "diagnosis"])
    )
    context_levels_path = root / "stage1_symbol_context_levels_is.parquet"
    context_levels = (
        pd.read_parquet(context_levels_path)
        if context_levels_path.is_file()
        else pd.DataFrame(
            columns=["symbol", "context_name", "context_value", "count", "median_catchup_60m"]
        )
    )

    high_features = tuple(
        column
        for column in high_resolution.columns
        if column not in _HIGH_RESOLUTION_KEYS
        and not column.endswith("_missing")
        and not column.endswith("_minute_count")
        and not column.endswith("_coverage")
    )
    merged = coarse.merge(
        high_resolution[
            ["event_id", "symbol", "aggtrades_primary_complete", *high_features]
        ],
        on=["event_id", "symbol"],
        how="left",
        validate="one_to_one",
    ).merge(
        labels[["event_id", "symbol", "response_win_60m"]],
        on=["event_id", "symbol"],
        validate="one_to_one",
    ).merge(
        outcomes,
        on=["event_id", "symbol"],
        validate="one_to_one",
    )
    merged["calendar_month"] = pd.to_datetime(
        merged["snapshot_time_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    merged["underreaction_quintile"] = _quantile_codes(
        merged["event_underreaction_percentile"], bins=5
    )
    merged["factor_r_squared_tercile"] = _quantile_codes(
        merged["factor_r_squared_15m"], bins=3
    )
    merged["activity_ratio_tercile"] = _quantile_codes(
        merged["current_activity_ratio"], bins=3
    )
    merged = merged.merge(
        symbol_diagnostics[["symbol", "diagnosis"]],
        on="symbol",
        how="left",
        validate="many_to_one",
    )
    merged["diagnosis"] = merged["diagnosis"].fillna("unclassified")
    merged["aggtrades_primary_complete"] = merged["aggtrades_primary_complete"].fillna(False).astype(bool)

    covered = merged.loc[merged["aggtrades_primary_complete"]].copy()
    uncovered = merged.loc[~merged["aggtrades_primary_complete"]].copy()
    trait_rows = [_numeric_trait(covered, feature) for feature in high_features]
    _apply_bh(trait_rows)
    for row in trait_rows:
        feature = str(row["feature"])
        if not row["analyzable"]:
            row.update(
                monthly_effects=[],
                forward_checks=[],
                monthly_same_sign_count=0,
                forward_sign_agreement_count=0,
                stable_trait=False,
            )
            continue
        sign = int(np.sign(float(row["rank_biserial_effect"])))
        monthly = _monthly_effects(covered, feature)
        forward = _forward_checks(covered, feature)
        row["monthly_effects"] = monthly
        row["forward_checks"] = forward
        row["monthly_same_sign_count"] = sum(
            np.isfinite(item["rank_biserial_effect"])
            and int(np.sign(item["rank_biserial_effect"])) == sign
            and sign != 0
            for item in monthly
        )
        row["forward_sign_agreement_count"] = sum(item["sign_agrees"] for item in forward)
        row["stable_trait"] = bool(
            float(row["coverage_within_complete_rows"]) >= 0.80
            and float(row["bh_q_value"]) <= 0.05
            and int(row["monthly_same_sign_count"]) >= 6
            and int(row["forward_sign_agreement_count"]) >= 4
        )

    stable = sorted(
        (
            {
                "feature": row["feature"],
                "rank_biserial_effect": row["rank_biserial_effect"],
                "win_median": row["win_median"],
                "loss_median": row["loss_median"],
                "bh_q_value": row["bh_q_value"],
                "monthly_same_sign_count": row["monthly_same_sign_count"],
                "forward_sign_agreement_count": row["forward_sign_agreement_count"],
            }
            for row in trait_rows
            if row["stable_trait"]
        ),
        key=lambda item: abs(float(item["rank_biserial_effect"])),
        reverse=True,
    )
    coverage_traits = [_coverage_trait(merged, feature) for feature in CAUSAL_COVERAGE_FEATURES]
    coverage_contexts = {
        feature: _coverage_levels(merged, feature)
        for feature in (
            "calendar_month",
            "session_name",
            "impulse_direction",
            "candidate_channel",
            "underreaction_quintile",
            "factor_r_squared_tercile",
            "activity_ratio_tercile",
            "diagnosis",
        )
    }
    symbol_table = _symbol_diagnostics(
        merged,
        ex_post_diagnostics=symbol_diagnostics,
        dominant_contexts=_dominant_contexts(context_levels),
    )
    symbol_path = root / "stage2_high_resolution_symbol_diagnostics_is.parquet"
    symbol_table.to_parquet(symbol_path, index=False, compression="zstd")

    model_path = root / "stage2_model_features_with_high_resolution_is.parquet"
    model_columns = [column for column in merged.columns if column not in {
        "response_win_60m", "catchup_residual_change_60m", "diagnosis",
        "underreaction_quintile", "factor_r_squared_tercile", "activity_ratio_tercile",
    }]
    merged[model_columns].to_parquet(model_path, index=False, compression="zstd")
    report = {
        "report_version": REPORT_VERSION,
        "partition": "is",
        "row_count": len(merged),
        "complete_15m_row_count": len(covered),
        "complete_15m_row_share": float(len(covered) / len(merged)),
        "covered_outcome": _outcome_summary(covered),
        "uncovered_outcome": _outcome_summary(uncovered),
        "coverage_is_not_random_warning": (
            "Archive was collected for another strategy; covered/uncovered outcome differences "
            "are selection diagnostics, not high-resolution causal effects."
        ),
        "coverage_causal_feature_diagnostics": coverage_traits,
        "coverage_contexts": coverage_contexts,
        "high_resolution_feature_count": len(high_features),
        "stable_high_resolution_trait_count": len(stable),
        "stable_high_resolution_traits": stable,
        "high_resolution_numeric_traits": trait_rows,
        "symbol_diagnostics_path": str(symbol_path),
        "model_features_path": str(model_path),
        "paired_model_ablation_status": "pending_weekly_walk_forward",
        "selection_limit": (
            "IS traits may prioritize paired ablations; they cannot define a static symbol list "
            "or outcome-derived threshold."
        ),
    }
    output = root / "stage2_high_resolution_analysis_is.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


def _quantile_codes(values: pd.Series, *, bins: int) -> pd.Series:
    ranked = pd.to_numeric(values, errors="coerce").rank(method="average", pct=True)
    return np.minimum(np.ceil(ranked * bins), bins).astype("Int64")


def _numeric_trait(frame: pd.DataFrame, feature: str) -> dict[str, object]:
    values = pd.to_numeric(frame[feature], errors="coerce")
    valid = values.notna() & np.isfinite(values)
    wins = values.loc[valid & frame["response_win_60m"]].to_numpy(dtype=float)
    losses = values.loc[valid & ~frame["response_win_60m"]].to_numpy(dtype=float)
    base = {
        "feature": feature,
        "coverage_within_complete_rows": float(valid.mean()),
        "win_count": len(wins),
        "loss_count": len(losses),
    }
    if not len(wins) or not len(losses) or np.unique(values.loc[valid]).size < 2:
        return {
            **base,
            "analyzable": False,
            "win_median": None,
            "loss_median": None,
            "rank_biserial_effect": None,
            "mann_whitney_p_value": None,
        }
    test = mannwhitneyu(wins, losses, alternative="two-sided", method="asymptotic")
    return {
        **base,
        "analyzable": True,
        "win_median": float(np.median(wins)),
        "loss_median": float(np.median(losses)),
        "rank_biserial_effect": 2.0 * float(test.statistic) / (len(wins) * len(losses)) - 1.0,
        "mann_whitney_p_value": float(test.pvalue),
    }


def _effect(frame: pd.DataFrame, feature: str) -> float:
    row = _numeric_trait(frame, feature)
    return float(row["rank_biserial_effect"]) if row["analyzable"] else float("nan")


def _monthly_effects(frame: pd.DataFrame, feature: str) -> list[dict[str, object]]:
    return [
        {"month": str(month), "row_count": len(group), "rank_biserial_effect": _effect(group, feature)}
        for month, group in frame.groupby("calendar_month", sort=True)
    ]


def _forward_checks(frame: pd.DataFrame, feature: str) -> list[dict[str, object]]:
    checks = []
    for month in FORWARD_MONTHS:
        train_effect = _effect(frame.loc[frame["calendar_month"].lt(month)], feature)
        validation_effect = _effect(frame.loc[frame["calendar_month"].eq(month)], feature)
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


def _apply_bh(rows: list[dict[str, object]]) -> None:
    indices = [index for index, row in enumerate(rows) if row["analyzable"]]
    for row in rows:
        row["bh_q_value"] = None
    if not indices:
        return
    p_values = np.asarray([float(rows[index]["mann_whitney_p_value"]) for index in indices])
    order = np.argsort(p_values, kind="mergesort")
    adjusted = np.empty(len(indices), dtype=float)
    running = 1.0
    for reverse_rank in range(len(indices) - 1, -1, -1):
        original = order[reverse_rank]
        rank = reverse_rank + 1
        running = min(running, p_values[original] * len(indices) / rank)
        adjusted[original] = min(running, 1.0)
    for index, q_value in zip(indices, adjusted, strict=True):
        rows[index]["bh_q_value"] = float(q_value)


def _coverage_trait(frame: pd.DataFrame, feature: str) -> dict[str, object]:
    values = pd.to_numeric(frame[feature], errors="coerce")
    covered = values.loc[frame["aggtrades_primary_complete"]].dropna().to_numpy(dtype=float)
    uncovered = values.loc[~frame["aggtrades_primary_complete"]].dropna().to_numpy(dtype=float)
    test = mannwhitneyu(covered, uncovered, alternative="two-sided", method="asymptotic")
    return {
        "feature": feature,
        "covered_median": float(np.median(covered)),
        "uncovered_median": float(np.median(uncovered)),
        "coverage_rank_biserial_effect": 2.0 * float(test.statistic) / (len(covered) * len(uncovered)) - 1.0,
        "mann_whitney_p_value": float(test.pvalue),
    }


def _coverage_levels(frame: pd.DataFrame, feature: str) -> list[dict[str, object]]:
    return [
        {
            "value": None if pd.isna(value) else str(value),
            "row_count": len(group),
            "covered_count": int(group["aggtrades_primary_complete"].sum()),
            "covered_share": float(group["aggtrades_primary_complete"].mean()),
            "covered_win_rate": (
                float(group.loc[group["aggtrades_primary_complete"], "response_win_60m"].mean())
                if bool(group["aggtrades_primary_complete"].any())
                else None
            ),
        }
        for value, group in frame.groupby(feature, dropna=False, sort=True)
    ]


def _symbol_diagnostics(
    frame: pd.DataFrame,
    *,
    ex_post_diagnostics: pd.DataFrame,
    dominant_contexts: pd.DataFrame,
) -> pd.DataFrame:
    microstructure_features = (
        "aggtrades_15m_trade_count",
        "aggtrades_15m_trade_notional_p95",
        "aggtrades_15m_top1pct_notional_share",
        "aggtrades_15m_notional_gini",
        "aggtrades_15m_same_side_run_mean",
        "aggtrades_15m_side_flip_rate",
        "aggtrades_15m_inter_arrival_cv",
        "aggtrades_15m_impact_asymmetry",
    )
    rows = []
    for symbol, group in frame.groupby("symbol", sort=True):
        covered = group.loc[group["aggtrades_primary_complete"]]
        row = {
                "symbol": symbol,
                "diagnosis": str(group["diagnosis"].iloc[0]),
                "row_count": len(group),
                "covered_count": len(covered),
                "covered_share": float(len(covered) / len(group)),
                "covered_win_rate": float(covered["response_win_60m"].mean()) if len(covered) else np.nan,
                "covered_median_catchup_60m": float(covered["catchup_residual_change_60m"].median()) if len(covered) else np.nan,
                "covered_mad_catchup_60m": float(
                    np.median(np.abs(covered["catchup_residual_change_60m"] - covered["catchup_residual_change_60m"].median()))
                ) if len(covered) else np.nan,
            }
        for feature in microstructure_features:
            row[f"covered_median_{feature}"] = (
                float(pd.to_numeric(covered[feature], errors="coerce").median())
                if len(covered)
                else np.nan
            )
        rows.append(row)
    table = pd.DataFrame(rows)
    diagnostic_columns = [
        "symbol",
        "win_rate",
        "median_catchup_60m",
        "mad_catchup_60m",
        "median_factor_correlation_15m",
        "median_factor_r_squared_15m",
        "median_current_activity_ratio",
        "opposite_registered_context_signs",
    ]
    available = [column for column in diagnostic_columns if column in ex_post_diagnostics]
    renamed = ex_post_diagnostics[available].rename(
        columns={column: f"all_is_{column}" for column in available if column != "symbol"}
    )
    return table.merge(renamed, on="symbol", how="left", validate="one_to_one").merge(
        dominant_contexts, on="symbol", how="left", validate="one_to_one"
    )


def _dominant_contexts(levels: pd.DataFrame) -> pd.DataFrame:
    """Describe, ex post, the registered context with the widest symbol contrast."""

    eligible = levels.loc[
        pd.to_numeric(levels["count"], errors="coerce").ge(15)
        & pd.to_numeric(levels["median_catchup_60m"], errors="coerce").notna()
    ].copy()
    rows = []
    for symbol, symbol_group in eligible.groupby("symbol", sort=True):
        candidates = []
        for context_name, context_group in symbol_group.groupby("context_name", sort=True):
            if len(context_group) < 2:
                continue
            ordered = context_group.sort_values("median_catchup_60m", kind="mergesort")
            worst = ordered.iloc[0]
            best = ordered.iloc[-1]
            candidates.append(
                {
                    "dominant_context_name": str(context_name),
                    "dominant_context_range_catchup_60m": float(
                        best["median_catchup_60m"] - worst["median_catchup_60m"]
                    ),
                    "dominant_context_best_value": str(best["context_value"]),
                    "dominant_context_best_median_catchup_60m": float(best["median_catchup_60m"]),
                    "dominant_context_worst_value": str(worst["context_value"]),
                    "dominant_context_worst_median_catchup_60m": float(worst["median_catchup_60m"]),
                }
            )
        if candidates:
            rows.append(
                {"symbol": symbol, **max(candidates, key=lambda item: item["dominant_context_range_catchup_60m"])}
            )
    return pd.DataFrame(rows)


def _outcome_summary(frame: pd.DataFrame) -> dict[str, object]:
    return {
        "row_count": len(frame),
        "win_rate": float(frame["response_win_60m"].mean()),
        "median_catchup_60m": float(frame["catchup_residual_change_60m"].median()),
        "mean_catchup_60m": float(frame["catchup_residual_change_60m"].mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze event-scoped aggTrades coverage and traits.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(build_stage2_high_resolution_analysis(stage1_dir=args.stage1_dir))


__all__ = ["build_stage2_high_resolution_analysis"]


if __name__ == "__main__":
    main()
