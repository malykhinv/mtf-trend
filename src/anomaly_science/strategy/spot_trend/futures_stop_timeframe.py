"""Descriptive, paired IS audit for structural-stop timeframes."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd


def _flat_trades(trades: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [{key: value for key, value in trade.items() if key != "stop_updates"} for trade in trades]
    )
    frame["month"] = pd.to_datetime(frame["fill_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    frame["holding_minutes"] = (frame["exit_time_ms"] - frame["fill_time_ms"]) / 60_000
    return frame


def _scopes(frame: pd.DataFrame) -> tuple[tuple[str, pd.DataFrame], ...]:
    return (("all", frame),)


def build_stop_timeframe_audits(
    trades: list[dict[str, Any]],
    *,
    causal_event_count: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build coverage, calendar-block and same-event paired comparisons."""

    frame = _flat_trades(trades)
    summary_rows: list[dict[str, object]] = []
    monthly_rows: list[dict[str, object]] = []
    paired_rows: list[dict[str, object]] = []
    for scope, population in _scopes(frame):
        for timeframe, group in population.groupby("tf", sort=True):
            summary_rows.append(
                {
                    "scope": scope,
                    "tf": timeframe,
                    "trades": len(group),
                    "coverage_of_all_causal_events": len(group) / causal_event_count,
                    "win_rate": group["outcome"].eq("win").mean(),
                    "stop_hit_rate": group["exit_reason"].eq("structural_stop").mean(),
                    "mean_net_r": group["net_r"].mean(),
                    "median_net_r": group["net_r"].median(),
                    "p95_net_r": group["net_r"].quantile(0.95),
                    "maximum_net_r": group["net_r"].max(),
                    "mean_return": group["return_pct"].mean(),
                    "median_initial_risk_pct": group["initial_risk_pct"].median(),
                    "median_holding_minutes": group["holding_minutes"].median(),
                    "stopped_then_recovered_entry_share": group[
                        "stopped_then_recovered_entry"
                    ].mean(),
                    "mean_stop_updates": group["stop_update_count"].mean(),
                }
            )
        for (month, timeframe), group in population.groupby(["month", "tf"], sort=True):
            monthly_rows.append(
                {
                    "scope": scope,
                    "month": month,
                    "tf": timeframe,
                    "trades": len(group),
                    "mean_net_r": group["net_r"].mean(),
                    "mean_return": group["return_pct"].mean(),
                    "win_rate": group["outcome"].eq("win").mean(),
                }
            )
        event_context = population.drop_duplicates("event_id").set_index("event_id")[["month"]]
        wide = population.pivot(index="event_id", columns="tf", values="net_r")
        for left_tf, right_tf in combinations(sorted(wide.columns), 2):
            paired = wide[[left_tf, right_tf]].dropna()
            if paired.empty:
                continue
            differences = paired[left_tf] - paired[right_tf]
            paired_months = event_context.loc[paired.index, "month"]
            monthly_differences = differences.groupby(paired_months).mean()
            paired_rows.append(
                {
                    "scope": scope,
                    "left_tf": left_tf,
                    "right_tf": right_tf,
                    "paired_events": len(paired),
                    "mean_net_r_difference_left_minus_right": differences.mean(),
                    "median_net_r_difference_left_minus_right": differences.median(),
                    "left_better_event_share": differences.gt(0).mean(),
                    "calendar_months": len(monthly_differences),
                    "left_better_month_share": monthly_differences.gt(0).mean(),
                }
            )
    return pd.DataFrame(summary_rows), pd.DataFrame(monthly_rows), pd.DataFrame(paired_rows)


def write_stop_timeframe_audits(
    audits: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
    output_dir: Path,
    *,
    artifact_prefix: str = "structural_protected_stop",
) -> None:
    summary, monthly, paired = audits
    summary.to_csv(output_dir / f"{artifact_prefix}_timeframe_summary.csv", index=False)
    monthly.to_csv(output_dir / f"{artifact_prefix}_timeframe_monthly.csv", index=False)
    paired.to_csv(output_dir / f"{artifact_prefix}_timeframe_paired.csv", index=False)
