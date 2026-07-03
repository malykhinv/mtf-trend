"""Registered EV analysis for pump-long outcomes.

Applies the portfolio exposure rule (max one concurrent position per
recurrence chain), splits development from OOS by the registered boundary,
and produces the phase-2 development report: blind and oracle arms, tail
dependence, drawdown, weekly consistency, cost stress, and the registered
48h-memory stratification. Week-cluster bootstrap CIs use ISO weeks of the
entry time as the resampling unit — trades inside one week are correlated.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from anomaly_science.decision.portfolio import ArmSummary, apply_exposure_rule, summarize_arm

DEVELOPMENT_END_UTC_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000)


def build_pump_long_report(
    outcomes: pd.DataFrame,
    *,
    period: str,
    bootstrap_iterations: int = 1000,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build (arm summary, stratification) frames for one period slice.

    `period` selects "development" (< registered boundary) or "oos" (>=).
    The caller is responsible for only looking at OOS in phase 4.
    """

    filled = outcomes.loc[
        (outcomes["status"] == "filled") & outcomes["entry_eligible"].astype(bool)
    ].copy()
    boundary = filled["snapshot_time_ms"] < DEVELOPMENT_END_UTC_MS
    filled = filled.loc[boundary if period == "development" else ~boundary]
    portfolio = apply_exposure_rule(filled)

    summaries: list[ArmSummary] = []
    strata_rows: list[dict] = []
    for stop_variant, variant_frame in portfolio.groupby("stop_variant"):
        summaries.append(
            summarize_arm(variant_frame, arm="blind", stop_variant=str(stop_variant),
                          bootstrap_iterations=bootstrap_iterations)
        )
        oracle = variant_frame.loc[
            variant_frame["label_available"].astype(bool) & (variant_frame["y"] == 0)
        ]
        if len(oracle):
            summaries.append(
                summarize_arm(oracle, arm="oracle_continuation", stop_variant=str(stop_variant),
                              bootstrap_iterations=bootstrap_iterations)
            )
        for axis, values in _registered_strata(variant_frame):
            for value, subset in values:
                if len(subset) == 0:
                    continue
                strata_rows.append({
                    "stop_variant": stop_variant,
                    "axis": axis,
                    "value": value,
                    "trades": len(subset),
                    "ev_net_return": float(subset["net_return"].mean()),
                    "median_net_return": float(subset["net_return"].median()),
                    "mean_net_r": float(subset["net_r"].mean()),
                })
    summary_frame = pd.DataFrame([asdict(item) for item in summaries])
    return summary_frame, pd.DataFrame(strata_rows)


def _registered_strata(frame: pd.DataFrame):
    ordinal = frame["decision_index"].astype(int)
    ordinal_bucket = np.select(
        [ordinal <= 1, ordinal == 2, ordinal <= 5], ["1", "2", "3-5"], default="6+"
    )
    fresh = frame["n_prior_48h"].astype(float) == 0
    last_faded = frame["last_prior_faded"].astype(float)
    yield "ordinal", [(label, frame.loc[ordinal_bucket == label]) for label in ("1", "2", "3-5", "6+")]
    yield "chain_position", [
        ("first_in_48h", frame.loc[fresh]),
        ("repeat_in_48h", frame.loc[~fresh]),
    ]
    yield "last_prior_faded", [
        ("faded", frame.loc[last_faded == 1.0]),
        ("continued", frame.loc[last_faded == 0.0]),
        ("no_prior", frame.loc[last_faded.isna()]),
    ]


__all__ = [
    "ArmSummary",
    "DEVELOPMENT_END_UTC_MS",
    "apply_exposure_rule",
    "build_pump_long_report",
    "summarize_arm",
]
