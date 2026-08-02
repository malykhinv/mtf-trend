from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _pre_entry_feature_columns(crossings: pd.DataFrame) -> list[str]:
    columns = [
        "trigger_horizon",
        "eligible_horizon_count",
        "crossed_horizon_count_at_snapshot",
        "crossing_minute_of_day",
    ]
    for name in crossings.columns:
        if not name.startswith("pre_crossing_"):
            continue
        if "baseline_days_" in name or "window_complete_" in name:
            continue
        if any(
            token in name
            for token in (
                "_log_ratio",
                "_robust_z",
                "_percentile",
                "mean_trade_notional",
                "taker_buy_share",
            )
        ):
            columns.append(name)
    return [name for name in dict.fromkeys(columns) if name in crossings]


def _standardized_mean_difference(wins: pd.Series, losses: pd.Series) -> float:
    win_values = wins.dropna().astype(float)
    loss_values = losses.dropna().astype(float)
    if len(win_values) < 2 or len(loss_values) < 2:
        return np.nan
    pooled = np.sqrt((win_values.var(ddof=1) + loss_values.var(ddof=1)) / 2.0)
    return float((win_values.mean() - loss_values.mean()) / pooled) if pooled > 0 else np.nan


def build_win_loss_feature_audit(
    crossings: pd.DataFrame,
    trades: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    flat = pd.DataFrame([{key: value for key, value in trade.items() if key != "stop_updates"} for trade in trades])
    feature_columns = _pre_entry_feature_columns(crossings)
    population = flat.merge(
        crossings[["event_id", "date", *feature_columns]],
        on="event_id",
        how="left",
        validate="many_to_one",
        suffixes=("", "_event"),
    )
    rows: list[dict[str, object]] = []
    for timeframe, group in population.groupby("tf", sort=True):
        wins = group.loc[group["outcome"].eq("win")]
        losses = group.loc[group["outcome"].eq("loss")]
        for feature in feature_columns:
            win_values = wins[feature].dropna().astype(float)
            loss_values = losses[feature].dropna().astype(float)
            if len(win_values) < 30 or len(loss_values) < 30:
                continue
            rows.append(
                {
                    "tf": timeframe,
                    "feature": feature,
                    "win_rows": len(win_values),
                    "loss_rows": len(loss_values),
                    "win_mean": float(win_values.mean()),
                    "loss_mean": float(loss_values.mean()),
                    "win_median": float(win_values.median()),
                    "loss_median": float(loss_values.median()),
                    "mean_difference": float(win_values.mean() - loss_values.mean()),
                    "median_difference": float(win_values.median() - loss_values.median()),
                    "standardized_mean_difference": _standardized_mean_difference(win_values, loss_values),
                    "winner_direction": "higher" if win_values.mean() > loss_values.mean() else "lower",
                }
            )
    audit = pd.DataFrame(rows).sort_values(
        ["tf", "standardized_mean_difference"],
        key=lambda values: values.abs() if values.name == "standardized_mean_difference" else values,
        ascending=[True, False],
        kind="stable",
    )
    consistency_rows: list[dict[str, object]] = []
    for feature, group in audit.groupby("feature", sort=True):
        signs = np.sign(group["standardized_mean_difference"].dropna())
        consistency_rows.append(
            {
                "feature": feature,
                "timeframes_observed": len(group),
                "winner_higher_timeframes": int((signs > 0).sum()),
                "winner_lower_timeframes": int((signs < 0).sum()),
                "consistent_direction": (
                    "higher" if len(signs) and (signs > 0).all()
                    else ("lower" if len(signs) and (signs < 0).all() else "mixed")
                ),
                "median_absolute_smd": float(group["standardized_mean_difference"].abs().median()),
                "mean_smd": float(group["standardized_mean_difference"].mean()),
            }
        )
    consistency = pd.DataFrame(consistency_rows).sort_values(
        ["consistent_direction", "median_absolute_smd"],
        ascending=[True, False],
        kind="stable",
    )
    context_rows: list[dict[str, object]] = []
    population["session"] = np.select(
        [population["crossing_minute_of_day"] < 480, population["crossing_minute_of_day"] < 960],
        ["asia", "europe"],
        default="us",
    )
    for timeframe, group in population.groupby("tf", sort=True):
        for dimension in ("session", "trigger_horizon"):
            for value, part in group.groupby(dimension, sort=True):
                context_rows.append(
                    {
                        "tf": timeframe,
                        "dimension": dimension,
                        "value": value,
                        "trades": len(part),
                        "win_rate": float(part["outcome"].eq("win").mean()),
                        "mean_net_r": float(part["net_r"].mean()),
                        "mean_return": float(part["return_pct"].mean()),
                    }
                )
    context = pd.DataFrame(context_rows)
    stop_rows: list[dict[str, object]] = []
    for timeframe, group in population.groupby("tf", sort=True):
        losses = group.loc[group["outcome"].eq("loss")]
        stop_rows.append(
            {
                "tf": timeframe,
                "trades": len(group),
                "losses": len(losses),
                "median_initial_risk_pct": float(group["initial_risk_pct"].median()),
                "median_holding_minutes": float(
                    ((group["exit_time_ms"] - group["fill_time_ms"]) / 60_000).median()
                ),
                "loss_stopped_then_recovered_entry_share": float(
                    losses["stopped_then_recovered_entry"].mean()
                ),
                "loss_stopped_then_positive_5d_close_share": float(
                    (losses["five_day_close_return"] > 0).mean()
                ),
                "all_stopped_then_recovered_entry_share": float(
                    group["stopped_then_recovered_entry"].mean()
                ),
                "mean_stop_updates": float(group["stop_update_count"].mean()),
            }
        )
    return audit.reset_index(drop=True), consistency.reset_index(drop=True), context, pd.DataFrame(stop_rows)


def write_win_loss_audits(
    audits: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame],
    output_dir: Path,
    *,
    artifact_prefix: str = "structural_trade",
) -> None:
    feature_audit, consistency, context, stop_adequacy = audits
    feature_audit.to_csv(output_dir / f"{artifact_prefix}_win_loss_features.csv", index=False)
    consistency.to_csv(output_dir / f"{artifact_prefix}_feature_consistency.csv", index=False)
    context.to_csv(output_dir / f"{artifact_prefix}_context_profiles.csv", index=False)
    stop_adequacy.to_csv(output_dir / f"{artifact_prefix}_stop_adequacy.csv", index=False)
