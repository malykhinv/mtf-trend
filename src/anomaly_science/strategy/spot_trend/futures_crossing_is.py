from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .contracts import SpotTrendContractError


@dataclass(frozen=True, slots=True)
class CausalCrossingAuditConfig:
    minimum_cross_section: int = 5
    bootstrap_samples: int = 1_000
    bootstrap_seed: int = 20_250_721
    negative_control_seed: int = 20_250_722
    fdr_alpha: float = 0.10


def crossing_flow_feature_names(frame: pd.DataFrame) -> tuple[str, ...]:
    expected = tuple(
        f"pre_crossing_{measure}_{window}m_{representation}"
        for measure in ("quote_volume", "trade_count")
        for window in (5, 15, 30, 60)
        for representation in ("log_ratio", "robust_z", "percentile")
    )
    missing = [name for name in expected if name not in frame]
    if missing:
        raise SpotTrendContractError(f"causal crossing dataset missing anomaly features: {missing}")
    return expected


def crossing_outcomes(frame: pd.DataFrame) -> tuple[tuple[str, str, str], ...]:
    outcomes: list[tuple[str, str, str]] = [
        ("daily_close_confirmed", "confirmation", "daily"),
    ]
    for horizon in ("1h", "4h", "1d", "3d", "5d"):
        outcomes.extend(
            (
                (f"continuation_score_{horizon}", "continuation_score", horizon),
                (f"level_margin_{horizon}", "level_margin", horizon),
                (f"mfe_{horizon}", "mfe", horizon),
                (f"mae_{horizon}", "mae", horizon),
                (f"level_retained_{horizon}", "level_retention", horizon),
            )
        )
    missing = [name for name, _, _ in outcomes if name not in frame]
    if missing:
        raise SpotTrendContractError(f"causal crossing dataset missing path outcomes: {missing}")
    return tuple(outcomes)


def _daily_spearman(
    frame: pd.DataFrame,
    feature: str,
    target: str,
    minimum_cross_section: int,
) -> pd.Series:
    rows: dict[pd.Timestamp, float] = {}
    for day, group in frame[["date", feature, target]].dropna().groupby("date", sort=True):
        if (
            len(group) < minimum_cross_section
            or group[feature].nunique() < 3
            or group[target].nunique() < 2
        ):
            continue
        rows[day] = float(group[feature].corr(group[target], method="spearman"))
    return pd.Series(rows, dtype=float)


def _month_block_pvalue(series: pd.Series, samples: int, seed: int) -> tuple[float, int]:
    if series.empty:
        return np.nan, 0
    months = series.index.tz_localize(None).to_period("M")
    monthly = series.groupby(months).mean().dropna().to_numpy(dtype=float)
    if len(monthly) < 6:
        return np.nan, len(monthly)
    observed = float(monthly.mean())
    centered = monthly - observed
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(centered), size=(samples, len(centered)))
    null_means = centered[indices].mean(axis=1)
    pvalue = float((1 + np.count_nonzero(np.abs(null_means) >= abs(observed))) / (samples + 1))
    return pvalue, len(monthly)


def _bh(pvalues: pd.Series) -> pd.Series:
    output = pd.Series(np.nan, index=pvalues.index, dtype=float)
    valid = pvalues.dropna().sort_values()
    if valid.empty:
        return output
    raw = valid.to_numpy(dtype=float) * len(valid) / np.arange(1, len(valid) + 1)
    output.loc[valid.index] = np.minimum.accumulate(raw[::-1])[::-1].clip(0.0, 1.0)
    return output


def audit_causal_crossing_predictability(
    paths: pd.DataFrame,
    *,
    config: CausalCrossingAuditConfig = CausalCrossingAuditConfig(),
) -> pd.DataFrame:
    features = crossing_flow_feature_names(paths)
    outcomes = crossing_outcomes(paths)
    rows: list[dict[str, object]] = []
    for outcome_index, (outcome, outcome_family, horizon) in enumerate(outcomes):
        target_frame = paths.loc[paths[outcome].notna()].copy()
        rng = np.random.default_rng(config.negative_control_seed + outcome_index)
        shuffled_parts: list[pd.DataFrame] = []
        for _, group in target_frame.groupby("date", sort=True):
            part = group[["event_id", outcome]].copy()
            part["shuffled_outcome"] = rng.permutation(part[outcome].to_numpy())
            shuffled_parts.append(part[["event_id", "shuffled_outcome"]])
        target_frame = target_frame.merge(
            pd.concat(shuffled_parts, ignore_index=True),
            on="event_id",
            how="left",
            validate="one_to_one",
        )
        for feature_index, feature in enumerate(features):
            available = target_frame[["date", feature, outcome, "shuffled_outcome"]].dropna()
            daily_ic = _daily_spearman(
                available,
                feature,
                outcome,
                config.minimum_cross_section,
            )
            control_ic = _daily_spearman(
                available,
                feature,
                "shuffled_outcome",
                config.minimum_cross_section,
            )
            pvalue, month_blocks = _month_block_pvalue(
                daily_ic,
                config.bootstrap_samples,
                config.bootstrap_seed + outcome_index * len(features) + feature_index,
            )
            ranked = available.copy()
            ranked["rank"] = ranked.groupby("date")[feature].rank(method="average", pct=True)
            top = ranked.loc[ranked["rank"] > 0.8, outcome]
            bottom = ranked.loc[ranked["rank"] <= 0.2, outcome]
            observed_ic = float(daily_ic.mean()) if len(daily_ic) else np.nan
            negative_ic = float(control_ic.mean()) if len(control_ic) else np.nan
            rows.append(
                {
                    "feature": feature,
                    "outcome": outcome,
                    "outcome_family": outcome_family,
                    "horizon": horizon,
                    "resolved_outcome_rows": len(target_frame),
                    "available_rows": len(available),
                    "coverage": len(available) / len(target_frame) if len(target_frame) else 0.0,
                    "daily_ic_days": len(daily_ic),
                    "mean_daily_rank_ic": observed_ic,
                    "control_mean_daily_rank_ic": negative_ic,
                    "excess_absolute_ic_vs_control": (
                        abs(observed_ic) - abs(negative_ic)
                        if np.isfinite(observed_ic) and np.isfinite(negative_ic)
                        else np.nan
                    ),
                    "top_quintile_mean_outcome": float(top.mean()) if len(top) else np.nan,
                    "bottom_quintile_mean_outcome": float(bottom.mean()) if len(bottom) else np.nan,
                    "top_bottom_outcome_spread": (
                        float(top.mean() - bottom.mean()) if len(top) and len(bottom) else np.nan
                    ),
                    "month_blocks": month_blocks,
                    "block_bootstrap_pvalue": pvalue,
                }
            )
    audit = pd.DataFrame(rows)
    audit["fdr_qvalue"] = audit.groupby("outcome", group_keys=False)["block_bootstrap_pvalue"].apply(_bh)
    audit["passes_predictability_gate"] = (
        audit["coverage"].ge(0.60)
        & audit["daily_ic_days"].ge(30)
        & audit["excess_absolute_ic_vs_control"].gt(0)
        & audit["top_bottom_outcome_spread"].gt(0)
        & audit["fdr_qvalue"].le(config.fdr_alpha)
    )
    return audit.sort_values(
        ["passes_predictability_gate", "fdr_qvalue", "excess_absolute_ic_vs_control"],
        ascending=[False, True, False],
        kind="stable",
    ).reset_index(drop=True)


def summarize_causal_crossing_audit(audit: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (outcome_family, horizon), group in audit.groupby(["outcome_family", "horizon"], sort=True):
        valid = group.loc[group["mean_daily_rank_ic"].notna()]
        best = valid.loc[valid["excess_absolute_ic_vs_control"].idxmax()] if len(valid) else None
        rows.append(
            {
                "outcome_family": outcome_family,
                "horizon": horizon,
                "feature_tests": len(group),
                "resolved_outcome_rows": int(group["resolved_outcome_rows"].max()),
                "maximum_absolute_ic": float(valid["mean_daily_rank_ic"].abs().max()) if len(valid) else np.nan,
                "best_excess_feature": best["feature"] if best is not None else None,
                "best_excess_absolute_ic_vs_control": (
                    float(best["excess_absolute_ic_vs_control"]) if best is not None else np.nan
                ),
                "gate_pass_count": int(group["passes_predictability_gate"].sum()),
            }
        )
    return pd.DataFrame(rows)
