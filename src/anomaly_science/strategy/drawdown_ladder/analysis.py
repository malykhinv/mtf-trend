"""Descriptive, censor-aware Stage-0 recovery summaries.

These tables establish the raw phenomenon.  They are not trade simulations and
make no independent-observation claim: inference must cluster on parent_event_id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from anomaly_science.strategy.drawdown_ladder.spec import DrawdownLadderStage0Spec


class DrawdownLadderAnalysisError(ValueError):
    """Raised when Stage-0 analysis artifacts violate their join contract."""


def _kaplan_meier_probability(
    durations: np.ndarray,
    events: np.ndarray,
    *,
    evaluation_minutes: int,
) -> float | None:
    valid = np.isfinite(durations) & (durations > 0)
    duration = durations[valid].astype(float)
    event = events[valid].astype(bool)
    if not len(duration):
        return None
    survival = 1.0
    for time in np.unique(duration[event & (duration <= evaluation_minutes)]):
        at_risk = int(np.sum(duration >= time))
        observed = int(np.sum(event & (duration == time)))
        if at_risk:
            survival *= 1.0 - observed / at_risk
    return float(1.0 - survival)


def _kaplan_meier_median(durations: np.ndarray, events: np.ndarray) -> float | None:
    valid = np.isfinite(durations) & (durations > 0)
    duration = durations[valid].astype(float)
    event = events[valid].astype(bool)
    if not len(duration):
        return None
    survival = 1.0
    for time in np.unique(duration[event]):
        at_risk = int(np.sum(duration >= time))
        observed = int(np.sum(event & (duration == time)))
        if at_risk:
            survival *= 1.0 - observed / at_risk
        if survival <= 0.5:
            return float(time)
    return None


def _safe_mean(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return None if numeric.empty else float(numeric.mean())


def _safe_median(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return None if numeric.empty else float(numeric.median())


def _recovery_summary_rows(
    frame: pd.DataFrame,
    *,
    group_columns: tuple[str, ...],
    summary_family: str,
    spec: DrawdownLadderStage0Spec,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    grouped = frame.groupby(list(group_columns), sort=True, dropna=False)
    for keys, group in grouped:
        key_tuple = keys if isinstance(keys, tuple) else (keys,)
        available = pd.to_numeric(group["available_future_minutes"], errors="coerce").fillna(0).to_numpy(float)
        gross_event = group["gross_break_even_reached"].astype(bool).to_numpy()
        gross_time = pd.to_numeric(
            group["time_to_gross_break_even_minutes"], errors="coerce"
        ).to_numpy(float)
        durations = np.where(gross_event, gross_time, available)
        row: dict[str, object] = {
            "summary_family": summary_family,
            **dict(zip(group_columns, key_tuple, strict=True)),
            "row_count": len(group),
            "unique_parent_event_count": int(group["parent_event_id"].nunique()),
            "unique_symbol_count": int(group["symbol"].nunique()),
            "full_48h_path_count": int(group["horizon_complete"].astype(bool).sum()),
            "full_48h_path_share": float(group["horizon_complete"].astype(bool).mean()),
            "path_gap_share": float(group["censor_reason"].eq("path_gap").mean()),
            "symbol_history_end_share": float(
                group["censor_reason"].eq("symbol_history_ended").mean()
            ),
            "is_boundary_censor_share": float(
                group["censor_reason"].eq("is_boundary").mean()
            ),
            "conservative_observed_gross_recovery_share": float(gross_event.mean()),
            "gross_recovery_share_full_48h_only": (
                float(
                    group.loc[group["horizon_complete"].astype(bool), "gross_break_even_reached"]
                    .astype(bool)
                    .mean()
                )
                if bool(group["horizon_complete"].astype(bool).any())
                else None
            ),
            "observed_median_minutes_to_gross_recovery": _safe_median(
                group.loc[
                    group["gross_break_even_reached"].astype(bool),
                    "time_to_gross_break_even_minutes",
                ]
            ),
            "km_median_minutes_to_gross_recovery": _kaplan_meier_median(
                durations, gross_event
            ),
            "mean_future_return_48h": _safe_mean(group["future_return_2880m"]),
            "median_future_return_48h": _safe_median(group["future_return_2880m"]),
            "mean_future_max_return_48h": _safe_mean(group["future_max_return_2880m"]),
            "mean_future_min_return_48h": _safe_mean(group["future_min_return_2880m"]),
            "descriptive_only": True,
            "parent_event_cluster_required": True,
        }
        for horizon in (60, 240, 720, 1440, 2880):
            row[f"km_gross_recovery_probability_{horizon}m"] = _kaplan_meier_probability(
                durations,
                gross_event,
                evaluation_minutes=horizon,
            )
        for cost in spec.round_trip_cost_bps:
            event_column = f"break_even_{cost}bps_reached"
            time_column = f"time_to_break_even_{cost}bps_minutes"
            cost_event = group[event_column].astype(bool).to_numpy()
            cost_time = pd.to_numeric(group[time_column], errors="coerce").to_numpy(float)
            cost_duration = np.where(cost_event, cost_time, available)
            row[f"conservative_observed_recovery_{cost}bps_share"] = float(cost_event.mean())
            row[f"km_recovery_probability_{cost}bps_2880m"] = _kaplan_meier_probability(
                cost_duration,
                cost_event,
                evaluation_minutes=spec.maximum_horizon_minutes,
            )
            row[f"km_median_minutes_to_recovery_{cost}bps"] = _kaplan_meier_median(
                cost_duration,
                cost_event,
            )
        rows.append(row)
    return rows


def _load_join(
    *,
    candidate_path: Path,
    outcome_path: Path,
    candidate_columns: Iterable[str],
    spec: DrawdownLadderStage0Spec,
) -> pd.DataFrame:
    candidates = pd.read_parquet(
        candidate_path,
        columns=["candidate_id", "parent_event_id", "symbol", *candidate_columns],
    )
    outcome_fields = [
        "candidate_id",
        "available_future_minutes",
        "horizon_complete",
        "censor_reason",
        "gross_break_even_reached",
        "time_to_gross_break_even_minutes",
        "future_return_2880m",
        "future_max_return_2880m",
        "future_min_return_2880m",
    ]
    for cost in spec.round_trip_cost_bps:
        outcome_fields.extend(
            [
                f"break_even_{cost}bps_reached",
                f"time_to_break_even_{cost}bps_minutes",
            ]
        )
    outcomes = pd.read_parquet(outcome_path, columns=outcome_fields)
    joined = candidates.merge(
        outcomes,
        on="candidate_id",
        how="left",
        validate="one_to_one",
    )
    if len(joined) != len(candidates) or bool(joined["censor_reason"].isna().any()):
        raise DrawdownLadderAnalysisError("candidate/outcome recovery join is incomplete")
    return joined


def build_stage0_recovery_summaries(
    *,
    stage0_dir: str | Path,
    spec: DrawdownLadderStage0Spec = DrawdownLadderStage0Spec(),
) -> tuple[Path, Path, Path]:
    root = Path(stage0_dir)
    level = _load_join(
        candidate_path=root / "level_candidates.parquet",
        outcome_path=root / "level_outcomes.parquet",
        candidate_columns=("level_depth_pct", "session_seq", "session_name"),
        spec=spec,
    )
    states = _load_join(
        candidate_path=root / "ladder_state_candidates.parquet",
        outcome_path=root / "ladder_state_outcomes.parquet",
        candidate_columns=(
            "grid_step_pct",
            "filled_level_count",
            "deepest_filled_level_pct",
            "session_seq",
            "session_name",
        ),
        spec=spec,
    )
    level_rows = _recovery_summary_rows(
        level,
        group_columns=("level_depth_pct",),
        summary_family="individual_level_by_depth",
        spec=spec,
    )
    level_session_rows = _recovery_summary_rows(
        level,
        group_columns=("level_depth_pct", "session_seq", "session_name"),
        summary_family="individual_level_by_depth_and_session",
        spec=spec,
    )
    state_rows = _recovery_summary_rows(
        states,
        group_columns=("grid_step_pct", "deepest_filled_level_pct"),
        summary_family="equal_notional_ladder_state",
        spec=spec,
    )
    level_path = root / "level_recovery_summary.parquet"
    session_path = root / "level_session_recovery_summary.parquet"
    state_path = root / "ladder_state_recovery_summary.parquet"
    pd.DataFrame(level_rows).to_parquet(level_path, index=False, compression="zstd")
    pd.DataFrame(level_session_rows).to_parquet(session_path, index=False, compression="zstd")
    pd.DataFrame(state_rows).to_parquet(state_path, index=False, compression="zstd")
    report = {
        "protocol_freeze_id": spec.protocol_freeze_id,
        "study_side": spec.study_side,
        "event_family": spec.event_family,
        "stage": "raw_recovery_nature",
        "descriptive_only": True,
        "trade_simulation_performed": False,
        "tp_optimized": False,
        "parent_event_cluster_required_for_inference": True,
        "censoring_policy": {
            "primary_time_to_event_estimator": "Kaplan-Meier",
            "non_informative_censoring_claimed": False,
            "symbol_history_end_reported_separately": True,
            "conservative_unrecovered_treatment_reported": True,
        },
        "level_candidate_rows": len(level),
        "ladder_state_candidate_rows": len(states),
        "artifacts": [str(level_path), str(session_path), str(state_path)],
    }
    report_path = root / "recovery_summary_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return level_path, session_path, state_path


__all__ = ["DrawdownLadderAnalysisError", "build_stage0_recovery_summaries"]
