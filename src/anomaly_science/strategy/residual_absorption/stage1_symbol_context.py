"""Symbol reliability diagnosis and resolved-only causal context memory."""

from __future__ import annotations

import argparse
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.stats import beta as beta_distribution

SYMBOL_CONTEXT_REPORT_VERSION = "residual_response_symbol_context_v1"
SYMBOL_CONTEXT_MEMORY_VERSION = "residual_response_symbol_context_memory_v1"
MINIMUM_SYMBOL_RESPONSES = 30
MINIMUM_CONTEXT_RESPONSES = 15


def build_symbol_context_report(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    features = pd.read_parquet(root / "stage1_trait_features_is.parquet")
    outcomes = pd.read_parquet(root / "response_outcomes_is.parquet")
    frame = features.merge(
        outcomes[["event_id", "symbol", "catchup_residual_change_60m"]],
        on=["event_id", "symbol"],
        validate="one_to_one",
    )
    frame["response_win_60m"] = frame["catchup_residual_change_60m"] > 0.0
    frame["underreaction_quintile"] = _quantile_labels(
        frame["direction_adjusted_underreaction_15m"], 5, "q"
    )
    frame["factor_r_squared_tercile"] = _quantile_labels(
        frame["factor_r_squared_15m"], 3, "q"
    )
    frame["activity_ratio_tercile"] = _quantile_labels(
        frame["current_activity_ratio"], 3, "q"
    )
    context_columns = (
        "impulse_direction",
        "session_name",
        "calendar_month",
        "candidate_channel",
        "reference_confirmed",
        "underreaction_quintile",
        "factor_r_squared_tercile",
        "activity_ratio_tercile",
    )
    context_rows: list[dict[str, object]] = []
    for context_name in context_columns:
        grouped = (
            frame.groupby(["symbol", context_name], dropna=False, sort=True)
            .agg(
                count=("catchup_residual_change_60m", "size"),
                win_count=("response_win_60m", "sum"),
                win_rate=("response_win_60m", "mean"),
                median_catchup_60m=("catchup_residual_change_60m", "median"),
                mean_catchup_60m=("catchup_residual_change_60m", "mean"),
            )
            .reset_index()
        )
        grouped["context_name"] = context_name
        grouped["context_value"] = grouped[context_name].astype("string").fillna("missing")
        context_rows.extend(
            grouped[
                [
                    "symbol",
                    "context_name",
                    "context_value",
                    "count",
                    "win_count",
                    "win_rate",
                    "median_catchup_60m",
                    "mean_catchup_60m",
                ]
            ].to_dict(orient="records")
        )
    context_frame = pd.DataFrame(context_rows)
    context_path = root / "stage1_symbol_context_levels_is.parquet"
    context_frame.to_parquet(context_path, index=False, compression="zstd")
    symbol_rows: list[dict[str, object]] = []
    for symbol, group in frame.groupby("symbol", sort=True):
        summary = _outcome_summary(group["catchup_residual_change_60m"])
        eligible_levels = context_frame.loc[
            context_frame["symbol"].eq(str(symbol))
            & context_frame["count"].ge(MINIMUM_CONTEXT_RESPONSES)
        ]
        opposite_contexts = bool(
            eligible_levels["median_catchup_60m"].gt(0.0).any()
            and eligible_levels["median_catchup_60m"].lt(0.0).any()
        )
        count = int(summary["count"])
        if count < MINIMUM_SYMBOL_RESPONSES:
            diagnosis = "insufficient"
        elif float(summary["median_catchup_60m"]) > 0.0 and float(
            summary["win_rate_wilson_lower_95"]
        ) > 0.5:
            diagnosis = "reliable"
        elif float(summary["median_catchup_60m"]) < 0.0 and float(
            summary["win_rate_wilson_upper_95"]
        ) < 0.5:
            diagnosis = "poor"
        elif opposite_contexts:
            diagnosis = "context_dependent"
        else:
            diagnosis = "noisy"
        symbol_rows.append(
            {
                "symbol": str(symbol),
                "diagnosis": diagnosis,
                **summary,
                "event_count": int(group["event_id"].nunique()),
                "median_factor_correlation_15m": float(
                    group["factor_correlation_15m"].median()
                ),
                "median_factor_r_squared_15m": float(
                    group["factor_r_squared_15m"].median()
                ),
                "median_current_activity_ratio": _finite_median(
                    group["current_activity_ratio"]
                ),
                "opposite_registered_context_signs": opposite_contexts,
            }
        )
    diagnostic_path = root / "stage1_symbol_context_diagnostics_is.parquet"
    flat = pd.DataFrame(symbol_rows)
    flat.to_parquet(diagnostic_path, index=False, compression="zstd")
    report = {
        "report_version": SYMBOL_CONTEXT_REPORT_VERSION,
        "partition": "is",
        "symbol_count": len(symbol_rows),
        "minimum_symbol_responses": MINIMUM_SYMBOL_RESPONSES,
        "minimum_context_responses": MINIMUM_CONTEXT_RESPONSES,
        "diagnosis_shares": _diagnosis_shares(frame, flat),
        "diagnosis_profiles": _diagnosis_profiles(flat),
        "context_dependent_dominant_driver_counts": _dominant_context_drivers(
            context_frame, flat
        ),
        "context_level_path": str(context_path),
        "diagnostic_path": str(diagnostic_path),
        "top_reliable_symbols": _top_symbols(flat, "reliable", ascending=False),
        "top_poor_symbols": _top_symbols(flat, "poor", ascending=True),
        "highest_noise_symbols": _top_noise_symbols(flat),
        "interpretation_limit": (
            "Ex-post diagnosis explains IS only and cannot be used as a static whitelist or blacklist."
        ),
    }
    path = root / "stage1_symbol_context_report_is.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def build_causal_symbol_context_memory(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    profiles = pd.read_parquet(root / "residual_response_profiles_is.parquet")
    outcomes = pd.read_parquet(root / "response_outcomes_is.parquet")
    rows: list[dict[str, object]] = []
    outcome_groups = {symbol: group.copy() for symbol, group in outcomes.groupby("symbol")}
    for symbol, currents in profiles.groupby("symbol", sort=True):
        history = outcome_groups[str(symbol)].sort_values(
            ["resolution_time_ms", "event_id"], kind="mergesort"
        )
        resolution = history["resolution_time_ms"].to_numpy(dtype=np.int64)
        catchup = history["catchup_residual_change_60m"].to_numpy(dtype=float)
        directions = history["impulse_direction"].to_numpy(dtype=np.int8)
        sessions = history["session_seq"].to_numpy(dtype=np.int8)
        for current in currents.sort_values("snapshot_time_ms").itertuples(index=False):
            snapshot = int(current.snapshot_time_ms)
            available_count = int(np.searchsorted(resolution, snapshot, side="right"))
            recent_start = max(0, available_count - 20)
            recent_values = catchup[recent_start:available_count]
            recent_directions = directions[recent_start:available_count]
            recent_sessions = sessions[recent_start:available_count]
            direction_mask = recent_directions == int(current.impulse_direction)
            session_mask = recent_sessions == int(current.session_seq)
            row: dict[str, object] = {
                "schema_version": SYMBOL_CONTEXT_MEMORY_VERSION,
                "event_id": str(current.event_id),
                "symbol": str(symbol),
                "snapshot_time_ms": snapshot,
                "feature_cutoff_time_ms": snapshot,
                "maximum_resolution_time_used_ms": (
                    int(resolution[available_count - 1]) if available_count else None
                ),
                "prior_resolved_count_all": available_count,
            }
            for limit in (5, 10, 20):
                row.update(
                    _memory_summary_array(
                        catchup[max(0, available_count - limit) : available_count],
                        suffix=f"last_{limit}",
                    )
                )
            row.update(
                _memory_summary_array(
                    recent_values[direction_mask], suffix="same_direction_last_20"
                )
            )
            row.update(
                _memory_summary_array(
                    recent_values[session_mask], suffix="same_session_last_20"
                )
            )
            row.update(
                _memory_summary_array(
                    recent_values[direction_mask & session_mask],
                    suffix="same_direction_session_last_20",
                )
            )
            rows.append(row)
    frame = pd.DataFrame(rows).sort_values(["snapshot_time_ms", "symbol"])
    invalid = frame["maximum_resolution_time_used_ms"].notna() & (
        frame["maximum_resolution_time_used_ms"] > frame["snapshot_time_ms"]
    )
    if bool(invalid.any()) or bool(frame.duplicated(["event_id", "symbol"]).any()):
        raise ValueError("causal symbol/context memory audit failed")
    path = root / "causal_symbol_context_memory_is.parquet"
    frame.to_parquet(path, index=False, compression="zstd")
    audit = {
        "audit_version": "causal_symbol_context_memory_audit_v1",
        "status": "PASS",
        "row_count": len(frame),
        "rows_without_history": int(frame["prior_resolved_count_all"].eq(0).sum()),
        "maximum_prior_history_count": int(frame["prior_resolved_count_all"].max()),
        "future_resolution_violations": int(invalid.sum()),
    }
    (root / "causal_symbol_context_memory_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    return path


def _memory_summary_array(values: np.ndarray, *, suffix: str) -> dict[str, object]:
    wins = int(np.sum(values > 0.0))
    count = len(values)
    posterior_mean, posterior_lower, posterior_upper = _posterior_summary(wins, count)
    return {
        f"resolved_count_{suffix}": count,
        f"posterior_win_probability_{suffix}": posterior_mean,
        f"posterior_win_probability_lower_10_{suffix}": posterior_lower,
        f"posterior_win_probability_upper_90_{suffix}": posterior_upper,
        f"median_catchup_60m_{suffix}": float(np.median(values)) if count else None,
        f"mad_catchup_60m_{suffix}": (
            float(np.median(np.abs(values - np.median(values)))) if count else None
        ),
    }


@lru_cache(maxsize=256)
def _posterior_summary(wins: int, count: int) -> tuple[float, float, float]:
    alpha = wins + 0.5
    beta = count - wins + 0.5
    return (
        float(alpha / (alpha + beta)),
        float(beta_distribution.ppf(0.10, alpha, beta)),
        float(beta_distribution.ppf(0.90, alpha, beta)),
    )


def _outcome_summary(values: Iterable[float]) -> dict[str, object]:
    array = np.asarray(tuple(values), dtype=float)
    count = len(array)
    wins = int(np.sum(array > 0.0))
    lower, upper = _wilson_interval(wins, count)
    return {
        "count": count,
        "win_count": wins,
        "win_rate": wins / count,
        "win_rate_wilson_lower_95": lower,
        "win_rate_wilson_upper_95": upper,
        "median_catchup_60m": float(np.median(array)),
        "mean_catchup_60m": float(np.mean(array)),
        "mad_catchup_60m": float(np.median(np.abs(array - np.median(array)))),
        "positive_catchup_mass": float(array[array > 0.0].sum()),
        "absolute_outcome_mass": float(np.abs(array).sum()),
    }


def _wilson_interval(wins: int, count: int, z: float = 1.959963984540054) -> tuple[float, float]:
    proportion = wins / count
    denominator = 1.0 + z**2 / count
    center = (proportion + z**2 / (2.0 * count)) / denominator
    radius = z / denominator * math.sqrt(
        proportion * (1.0 - proportion) / count + z**2 / (4.0 * count**2)
    )
    return center - radius, center + radius


def _quantile_labels(values: pd.Series, bins: int, prefix: str) -> pd.Series:
    finite = pd.to_numeric(values, errors="coerce")
    labels = pd.Series("missing", index=values.index, dtype="object")
    valid = finite.notna() & np.isfinite(finite)
    labels.loc[valid] = pd.qcut(
        finite.loc[valid], q=bins, labels=[f"{prefix}{index}" for index in range(1, bins + 1)], duplicates="drop"
    ).astype(str)
    return labels


def _finite_median(values: pd.Series) -> float | None:
    array = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    array = array[np.isfinite(array)]
    return None if not len(array) else float(np.median(array))


def _diagnosis_shares(frame: pd.DataFrame, diagnostics: pd.DataFrame) -> list[dict[str, object]]:
    joined = frame.merge(diagnostics[["symbol", "diagnosis"]], on="symbol", validate="many_to_one")
    total_positive = float(joined.loc[joined["catchup_residual_change_60m"] > 0.0, "catchup_residual_change_60m"].sum())
    total_absolute = float(joined["catchup_residual_change_60m"].abs().sum())
    rows = []
    for diagnosis, group in joined.groupby("diagnosis", sort=True):
        rows.append(
            {
                "diagnosis": str(diagnosis),
                "symbol_count": int(group["symbol"].nunique()),
                "symbol_share": float(group["symbol"].nunique() / diagnostics["symbol"].nunique()),
                "row_count": len(group),
                "row_share": float(len(group) / len(joined)),
                "win_share": float(group["response_win_60m"].sum() / joined["response_win_60m"].sum()),
                "positive_catchup_mass_share": float(
                    group.loc[group["catchup_residual_change_60m"] > 0.0, "catchup_residual_change_60m"].sum()
                    / total_positive
                ),
                "absolute_outcome_mass_share": float(
                    group["catchup_residual_change_60m"].abs().sum() / total_absolute
                ),
                "event_coverage_share": float(group["event_id"].nunique() / joined["event_id"].nunique()),
            }
        )
    return rows


def _top_symbols(
    diagnostics: pd.DataFrame,
    diagnosis: str,
    *,
    ascending: bool,
    limit: int = 20,
) -> list[dict[str, object]]:
    selected = diagnostics.loc[diagnostics["diagnosis"].eq(diagnosis)].sort_values(
        "median_catchup_60m", ascending=ascending
    ).head(limit)
    return selected[
        ["symbol", "count", "win_rate", "median_catchup_60m", "mad_catchup_60m"]
    ].to_dict(orient="records")


def _top_noise_symbols(diagnostics: pd.DataFrame, limit: int = 20) -> list[dict[str, object]]:
    selected = diagnostics.loc[
        diagnostics["diagnosis"].isin(["noisy", "context_dependent"])
    ].sort_values("mad_catchup_60m", ascending=False).head(limit)
    return selected[
        ["symbol", "diagnosis", "count", "win_rate", "median_catchup_60m", "mad_catchup_60m"]
    ].to_dict(orient="records")


def _diagnosis_profiles(diagnostics: pd.DataFrame) -> list[dict[str, object]]:
    grouped = diagnostics.groupby("diagnosis", sort=True).agg(
        symbol_count=("symbol", "size"),
        median_response_count=("count", "median"),
        median_win_rate=("win_rate", "median"),
        median_catchup_60m=("median_catchup_60m", "median"),
        median_mad_catchup_60m=("mad_catchup_60m", "median"),
        median_factor_correlation_15m=("median_factor_correlation_15m", "median"),
        median_factor_r_squared_15m=("median_factor_r_squared_15m", "median"),
        median_current_activity_ratio=("median_current_activity_ratio", "median"),
    )
    return grouped.reset_index().to_dict(orient="records")


def _dominant_context_drivers(
    contexts: pd.DataFrame,
    diagnostics: pd.DataFrame,
) -> list[dict[str, object]]:
    symbols = set(
        diagnostics.loc[diagnostics["diagnosis"].eq("context_dependent"), "symbol"]
    )
    eligible = contexts.loc[
        contexts["symbol"].isin(symbols)
        & contexts["count"].ge(MINIMUM_CONTEXT_RESPONSES)
    ]
    spreads = (
        eligible.groupby(["symbol", "context_name"])["median_catchup_60m"]
        .agg(lambda values: float(values.max() - values.min()))
        .rename("median_spread")
        .reset_index()
    )
    dominant = spreads.loc[spreads.groupby("symbol")["median_spread"].idxmax()]
    counts = dominant["context_name"].value_counts()
    return [
        {"context_name": str(name), "symbol_count": int(count)}
        for name, count in counts.items()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build symbol reliability and causal context memory.")
    parser.add_argument("--stage1-dir", type=Path, default=Path(".output/research/residual_absorption/stage1_is_v1"))
    args = parser.parse_args()
    print(build_symbol_context_report(stage1_dir=args.stage1_dir))
    print(build_causal_symbol_context_memory(stage1_dir=args.stage1_dir))


__all__ = [
    "SYMBOL_CONTEXT_MEMORY_VERSION",
    "SYMBOL_CONTEXT_REPORT_VERSION",
    "build_causal_symbol_context_memory",
    "build_symbol_context_report",
]


if __name__ == "__main__":
    main()
