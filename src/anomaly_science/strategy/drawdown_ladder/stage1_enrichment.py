"""Causal breadth, concurrency, and resolved-history enrichment for Stage 1."""

from __future__ import annotations

from collections import defaultdict, deque
import heapq
import math

import numpy as np
import pandas as pd

from anomaly_science.strategy.drawdown_ladder.stage1_spec import (
    DrawdownLadderStage1Spec,
    build_stage1_feature_catalog,
)


class Stage1EnrichmentError(ValueError):
    """Raised when Stage-1 enrichment changes population or violates time."""


_BREADTH_COLUMNS = (
    "market_symbol_count",
    "market_mean_quote_activity_60m",
    "market_share_quote_activity_gt_3x",
    "market_share_quote_activity_gt_10x",
)


def attach_stage1_breadth_and_concurrency(
    frame: pd.DataFrame,
    breadth: pd.DataFrame,
    *,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> pd.DataFrame:
    required_breadth = {
        "snapshot_time_ms",
        "breadth_feature_cutoff_time_ms",
        "market_breadth_schema_version",
        *_BREADTH_COLUMNS,
    }
    for lag in spec.breadth_return_lags_minutes:
        required_breadth.update(
            f"market_{suffix}_{lag}m"
            for suffix in (
                "mean_return",
                "return_dispersion",
                "share_negative",
                "share_down_1pct",
                "share_down_3pct",
                "share_up_1pct",
                "share_up_3pct",
            )
        )
    missing = sorted(required_breadth - set(breadth.columns))
    if missing:
        raise Stage1EnrichmentError(f"minute breadth is missing columns: {missing}")
    if breadth["snapshot_time_ms"].duplicated().any():
        raise Stage1EnrichmentError("minute breadth snapshot keys must be unique")
    feature_names = sorted(required_breadth - {
        "snapshot_time_ms",
        "breadth_feature_cutoff_time_ms",
        "market_breadth_schema_version",
    })
    left = frame.drop(columns=feature_names, errors="ignore")
    joined = left.merge(
        breadth.loc[:, ["snapshot_time_ms", "breadth_feature_cutoff_time_ms", *feature_names]],
        on="snapshot_time_ms",
        how="left",
        validate="many_to_one",
    )
    if len(joined) != len(frame):
        raise Stage1EnrichmentError("breadth join changed Stage-1 population")
    if bool(joined["breadth_feature_cutoff_time_ms"].isna().any()):
        raise Stage1EnrichmentError("breadth join did not cover every Stage-1 snapshot")
    if not bool(
        joined["breadth_feature_cutoff_time_ms"].le(joined["snapshot_time_ms"]).all()
    ):
        raise Stage1EnrichmentError("breadth feature cutoff exceeds snapshot")
    counts = (
        joined.groupby("snapshot_time_ms", sort=False)
        .agg(
            simultaneous_ladder_signal_count=("candidate_id", "size"),
            simultaneous_ladder_parent_count=("parent_event_id", "nunique"),
        )
        .reset_index()
    )
    joined = joined.drop(
        columns=["simultaneous_ladder_signal_count", "simultaneous_ladder_parent_count"],
        errors="ignore",
    ).merge(counts, on="snapshot_time_ms", how="left", validate="many_to_one")
    joined = joined.drop(columns=["breadth_feature_cutoff_time_ms"])
    return _ordered(joined, spec)


def _history_values(
    history: deque[tuple[float, float]],
    count: int,
) -> tuple[float, float]:
    if not history:
        return math.nan, math.nan
    selected = list(history)[-count:]
    recovery = float(np.mean([value[0] for value in selected]))
    median_time = float(np.median([value[1] for value in selected]))
    return recovery, median_time


def attach_stage1_prior_reactions(
    frame: pd.DataFrame,
    *,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> pd.DataFrame:
    result_parts: list[pd.DataFrame] = []
    max_history = max(spec.prior_reaction_windows)
    for _, symbol_frame in frame.groupby("symbol", sort=True):
        work = symbol_frame.sort_values(["snapshot_time_ms", "candidate_id"], kind="mergesort").copy()
        symbol_history: deque[tuple[float, float]] = deque(maxlen=max_history)
        exact_history: dict[tuple[int, int], deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=max_history)
        )
        pending: list[tuple[int, str, int, int, float, float]] = []
        output: dict[str, np.ndarray] = {
            "prior_symbol_resolved_count": np.empty(len(work), dtype=float),
            "prior_exact_state_resolved_count": np.empty(len(work), dtype=float),
        }
        for window in spec.prior_reaction_windows:
            output[f"prior_symbol_recovery_rate_{window}"] = np.empty(len(work), dtype=float)
            output[f"prior_exact_state_recovery_rate_{window}"] = np.empty(len(work), dtype=float)
            output[f"prior_symbol_median_recovery_minutes_{window}"] = np.empty(
                len(work), dtype=float
            )
        total_symbol_resolved = 0
        total_exact_resolved: dict[tuple[int, int], int] = defaultdict(int)
        snapshot_values = work["snapshot_time_ms"].to_numpy(dtype=np.int64)
        candidate_values = work["candidate_id"].astype(str).to_numpy()
        step_values = work["grid_step_pct"].to_numpy(dtype=np.int64)
        depth_values = work["deepest_filled_level_pct"].to_numpy(dtype=np.int64)
        available_values = work["label_available"].to_numpy(dtype=bool)
        resolution_values = work["label_resolution_time_ms"].to_numpy(dtype=np.int64)
        recovery_values = work["recovery_25bps_48h"].to_numpy(dtype=bool)
        recovery_time_values = work["recovery_time_or_horizon_minutes"].to_numpy(
            dtype=float
        )
        start = 0
        while start < len(work):
            snapshot = int(snapshot_values[start])
            stop = start + 1
            while stop < len(work) and int(snapshot_values[stop]) == snapshot:
                stop += 1
            while pending and pending[0][0] < snapshot:
                _, _, step, depth, recovered, time_value = heapq.heappop(pending)
                symbol_history.append((recovered, time_value))
                exact_history[(step, depth)].append((recovered, time_value))
                total_symbol_resolved += 1
                total_exact_resolved[(step, depth)] += 1
            symbol_values = {
                window: _history_values(symbol_history, window)
                for window in spec.prior_reaction_windows
            }
            exact_values: dict[tuple[int, int], dict[int, tuple[float, float]]] = {}
            for position in range(start, stop):
                key = (int(step_values[position]), int(depth_values[position]))
                if key not in exact_values:
                    exact_values[key] = {
                        window: _history_values(exact_history[key], window)
                        for window in spec.prior_reaction_windows
                    }
                output["prior_symbol_resolved_count"][position] = float(
                    total_symbol_resolved
                )
                output["prior_exact_state_resolved_count"][position] = float(
                    total_exact_resolved[key]
                )
                for window in spec.prior_reaction_windows:
                    symbol_rate, symbol_median = symbol_values[window]
                    exact_rate, _ = exact_values[key][window]
                    output[f"prior_symbol_recovery_rate_{window}"][position] = symbol_rate
                    output[f"prior_exact_state_recovery_rate_{window}"][position] = exact_rate
                    output[f"prior_symbol_median_recovery_minutes_{window}"][position] = (
                        symbol_median
                    )
            for position in range(start, stop):
                if not bool(available_values[position]):
                    continue
                resolution = int(resolution_values[position])
                if resolution <= snapshot:
                    raise Stage1EnrichmentError("label resolution is not strictly after snapshot")
                heapq.heappush(
                    pending,
                    (
                        resolution,
                        candidate_values[position],
                        int(step_values[position]),
                        int(depth_values[position]),
                        float(recovery_values[position]),
                        float(recovery_time_values[position]),
                    ),
                )
            start = stop
        for column, values in output.items():
            work[column] = values
        result_parts.append(work)
    result = pd.concat(result_parts, ignore_index=True)
    if result["candidate_id"].nunique() != len(frame):
        raise Stage1EnrichmentError("event-memory enrichment changed candidate keys")
    return _ordered(result, spec)


def _ordered(frame: pd.DataFrame, spec: DrawdownLadderStage1Spec) -> pd.DataFrame:
    columns = [definition.name for definition in build_stage1_feature_catalog(spec)]
    missing = sorted(set(columns) - set(frame.columns))
    extra = sorted(set(frame.columns) - set(columns))
    if missing or extra:
        raise Stage1EnrichmentError(
            f"Stage-1 enriched schema mismatch; missing={missing}; extra={extra}"
        )
    return frame.loc[:, columns]


__all__ = [
    "Stage1EnrichmentError",
    "attach_stage1_breadth_and_concurrency",
    "attach_stage1_prior_reactions",
]
