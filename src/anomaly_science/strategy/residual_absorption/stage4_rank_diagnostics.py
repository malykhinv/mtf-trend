"""Descriptive diagnostics for a frozen event-rank WFA result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

from anomaly_science.strategy.residual_absorption.stage3_paired_wfa import (
    select_coarse_numeric_features,
)


def build_rank_diagnostics(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    rank_root = root / "stage4_event_rank_wfa_v1"
    predictions = pd.read_parquet(rank_root / "rank_predictions_is.parquet")
    event_metrics = pd.read_parquet(rank_root / "event_rank_metrics_is.parquet")
    importance = pd.read_csv(rank_root / "feature_importance.csv")
    features = pd.read_parquet(root / "stage2_model_features_with_local_activity_is.parquet")
    numeric_features = select_coarse_numeric_features(features)
    join_features = tuple(
        feature for feature in numeric_features if feature not in predictions.columns
    )
    work = predictions.merge(
        features[["event_id", "symbol", *join_features]],
        on=["event_id", "symbol"],
        how="left",
        validate="one_to_one",
    )
    work["predicted_percentile"] = work.groupby("event_id")["model_score"].rank(pct=True)
    work["predicted_top"] = work["predicted_percentile"].gt(0.80)
    work["predicted_bottom"] = work["predicted_percentile"].le(0.20)
    work["actual_top"] = work["rank_target"].gt(0.80)

    contrasts = pd.DataFrame(
        [_selection_contrast(work, feature) for feature in numeric_features]
    ).sort_values("absolute_rank_biserial_effect", ascending=False)
    contrast_path = rank_root / "rank_selection_feature_contrasts_is.parquet"
    contrasts.to_parquet(contrast_path, index=False, compression="zstd")
    symbols = _symbol_diagnostics(work)
    diagnosis_path = root / "stage1_symbol_context_diagnostics_is.parquet"
    if diagnosis_path.is_file():
        diagnosis = pd.read_parquet(
            diagnosis_path,
            columns=[
                "symbol",
                "diagnosis",
                "median_factor_correlation_15m",
                "median_factor_r_squared_15m",
                "median_current_activity_ratio",
            ],
        )
        symbols = symbols.merge(diagnosis, on="symbol", how="left", validate="one_to_one")
    symbol_path = rank_root / "rank_symbol_diagnostics_enriched_is.parquet"
    symbols.to_parquet(symbol_path, index=False, compression="zstd")

    top = work.loc[work["predicted_top"], "catchup_residual_change_60m"]
    bottom = work.loc[work["predicted_bottom"], "catchup_residual_change_60m"]
    report = {
        "report_version": "residual_absorption_rank_diagnostics_v1",
        "partition": "exploratory_internal_is",
        "post_failure_descriptive_only": True,
        "untouched_2026_oos_accessed": False,
        "top_outcome": _distribution(top),
        "bottom_outcome": _distribution(bottom),
        "pooled_top_minus_bottom_mean": float(top.mean() - bottom.mean()),
        "pooled_top_minus_bottom_median": float(top.median() - bottom.median()),
        "top_actual_top_precision": float(work.loc[work["predicted_top"], "actual_top"].mean()),
        "unconditional_actual_top_rate": float(work["actual_top"].mean()),
        "actual_top_lift": float(
            work.loc[work["predicted_top"], "actual_top"].mean() / work["actual_top"].mean()
        ),
        "event_spread_concentration": _spread_concentration(event_metrics),
        "context_metrics": {
            "session": _context_metrics(event_metrics, "session_name"),
            "impulse_direction": _context_metrics(event_metrics, "impulse_direction"),
            "month": _month_metrics(event_metrics),
        },
        "feature_family_importance": _family_importance(importance),
        "strongest_selection_feature_contrasts": contrasts.head(30).to_dict(orient="records"),
        "symbol_concentration": _symbol_concentration(symbols),
        "feature_contrasts_path": str(contrast_path),
        "symbol_diagnostics_path": str(symbol_path),
        "interpretation_limit": (
            "These diagnostics explain the frozen failed ranker; they cannot define a rescue filter."
        ),
    }
    output = rank_root / "rank_diagnostics.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


def _selection_contrast(frame: pd.DataFrame, feature: str) -> dict[str, object]:
    values = pd.to_numeric(frame[feature], errors="coerce")
    top = values.loc[frame["predicted_top"]].dropna().to_numpy(dtype=float)
    bottom = values.loc[frame["predicted_bottom"]].dropna().to_numpy(dtype=float)
    if not len(top) or not len(bottom) or (np.unique(top).size < 2 and np.unique(bottom).size < 2):
        effect = np.nan
    else:
        statistic = mannwhitneyu(top, bottom, alternative="two-sided", method="asymptotic").statistic
        effect = 2.0 * float(statistic) / (len(top) * len(bottom)) - 1.0
    return {
        "feature": feature,
        "top_count": len(top),
        "bottom_count": len(bottom),
        "top_median": float(np.median(top)) if len(top) else np.nan,
        "bottom_median": float(np.median(bottom)) if len(bottom) else np.nan,
        "rank_biserial_effect": effect,
        "absolute_rank_biserial_effect": abs(effect) if np.isfinite(effect) else np.nan,
    }


def _distribution(values: pd.Series) -> dict[str, object]:
    quantiles = values.quantile([0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    return {
        "row_count": len(values),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "win_rate": float(values.gt(0.0).mean()),
        "quantiles": {str(index): float(value) for index, value in quantiles.items()},
    }


def _spread_concentration(events: pd.DataFrame) -> list[dict[str, object]]:
    values = events["model_top_bottom_catchup_spread"].astype(float)
    rows = []
    for removed_fraction in (0.0, 0.01, 0.025, 0.05, 0.10):
        retained = values
        if removed_fraction:
            cutoff = values.abs().quantile(1.0 - removed_fraction)
            retained = values.loc[values.abs().le(cutoff)]
        rows.append(
            {
                "removed_largest_absolute_fraction": removed_fraction,
                "event_count": len(retained),
                "mean_spread": float(retained.mean()),
                "median_spread": float(retained.median()),
                "positive_event_share": float(retained.gt(0.0).mean()),
            }
        )
    return rows


def _context_metrics(events: pd.DataFrame, column: str) -> list[dict[str, object]]:
    metric_columns = [
        "model_event_spearman",
        "model_top_bottom_catchup_spread",
        "model_top_bottom_win_rate_gap",
        "delta_event_spearman",
        "delta_top_bottom_catchup_spread",
        "delta_top_bottom_win_rate_gap",
    ]
    rows = []
    for value, group in events.groupby(column, sort=True):
        rows.append(
            {
                "value": str(value),
                "event_count": len(group),
                **{metric: float(group[metric].mean()) for metric in metric_columns},
            }
        )
    return rows


def _month_metrics(events: pd.DataFrame) -> list[dict[str, object]]:
    work = events.copy()
    work["month"] = pd.to_datetime(work["snapshot_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    return _context_metrics(work, "month")


def _feature_family(feature: str) -> str:
    if feature.startswith("local_"):
        return "local_activity"
    if feature.startswith(("posterior_", "median_catchup_", "mad_catchup_", "resolved_count_", "prior_resolved_")):
        return "causal_symbol_memory"
    if any(token in feature for token in ("volume", "liquidity", "activity", "trade_count")):
        return "liquidity_activity"
    if feature in {"session_name", "candidate_channel", "impulse_direction", "reference_confirmed", "core_eligible", "activity_expansion_eligible"}:
        return "categorical_context"
    return "factor_residual_response"


def _family_importance(importance: pd.DataFrame) -> list[dict[str, object]]:
    work = importance.copy()
    work["family"] = work["feature_name"].map(_feature_family)
    weekly = work.groupby(["test_week", "family"])["importance"].sum().reset_index()
    return (
        weekly.groupby("family")["importance"]
        .agg(["mean", "median", "min", "max"])
        .sort_values("mean", ascending=False)
        .reset_index()
        .to_dict(orient="records")
    )


def _symbol_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for symbol, group in frame.groupby("symbol", sort=True):
        predicted_top = group.loc[group["predicted_top"]]
        predicted_bottom = group.loc[group["predicted_bottom"]]
        rows.append(
            {
                "symbol": symbol,
                "row_count": len(group),
                "predicted_top_count": len(predicted_top),
                "predicted_bottom_count": len(predicted_bottom),
                "predicted_top_share": float(len(predicted_top) / len(group)),
                "top_actual_top_precision": float(predicted_top["actual_top"].mean()) if len(predicted_top) else np.nan,
                "top_mean_catchup_60m": float(predicted_top["catchup_residual_change_60m"].mean()) if len(predicted_top) else np.nan,
                "top_median_catchup_60m": float(predicted_top["catchup_residual_change_60m"].median()) if len(predicted_top) else np.nan,
                "top_win_rate": float(predicted_top["catchup_residual_change_60m"].gt(0.0).mean()) if len(predicted_top) else np.nan,
                "bottom_mean_catchup_60m": float(predicted_bottom["catchup_residual_change_60m"].mean()) if len(predicted_bottom) else np.nan,
                "bottom_median_catchup_60m": float(predicted_bottom["catchup_residual_change_60m"].median()) if len(predicted_bottom) else np.nan,
                "bottom_win_rate": float(predicted_bottom["catchup_residual_change_60m"].gt(0.0).mean()) if len(predicted_bottom) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _symbol_concentration(symbols: pd.DataFrame) -> dict[str, object]:
    counts = symbols["predicted_top_count"].sort_values(ascending=False).to_numpy(dtype=float)
    total = float(counts.sum())
    shares = counts / total if total > 0.0 else np.zeros_like(counts)
    return {
        "symbol_count": len(symbols),
        "symbols_with_predicted_top": int(np.sum(counts > 0.0)),
        "top_1pct_symbol_share_of_top_slots": float(shares[: max(1, int(np.ceil(len(shares) * 0.01)))].sum()),
        "top_5pct_symbol_share_of_top_slots": float(shares[: max(1, int(np.ceil(len(shares) * 0.05)))].sum()),
        "top_10pct_symbol_share_of_top_slots": float(shares[: max(1, int(np.ceil(len(shares) * 0.10)))].sum()),
        "herfindahl_index": float(np.sum(shares**2)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain a frozen event rank WFA.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(build_rank_diagnostics(stage1_dir=args.stage1_dir))


__all__ = ["build_rank_diagnostics"]


if __name__ == "__main__":
    main()
