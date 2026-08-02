from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.residual_absorption.stage4_event_rank_wfa import (
    EventRankWFAConfig,
    build_event_rank_metrics,
    build_event_rank_weekly_wfa,
)


def _rank_rows() -> pd.DataFrame:
    rng = np.random.default_rng(17)
    start = pd.Timestamp("2025-01-01", tz="UTC")
    rows = []
    for event_index in range(55):
        snapshot = start + pd.Timedelta(days=event_index)
        snapshot_ms = int(snapshot.timestamp() * 1_000)
        for symbol_index in range(8):
            signal = rng.normal()
            catchup = signal + rng.normal(scale=0.4)
            rows.append(
                {
                    "event_id": f"event-{event_index}",
                    "symbol": f"S{symbol_index}",
                    "snapshot_time_ms": snapshot_ms,
                    "feature_cutoff_time_ms": snapshot_ms,
                    "nature_resolution_time_ms": snapshot_ms + 120 * 60_000,
                    "recurrence_chain_id": f"chain-{event_index}",
                    "catchup_residual_change_60m": catchup,
                    "signal": signal,
                    "event_underreaction_percentile": (symbol_index + 1) / 8,
                    "session_name": "ASIA",
                    "candidate_channel": "core_only",
                    "impulse_direction": 1,
                }
            )
    frame = pd.DataFrame(rows)
    frame["rank_target"] = frame.groupby("event_id")[
        "catchup_residual_change_60m"
    ].rank(pct=True)
    return frame


def _rank_config() -> EventRankWFAConfig:
    start = pd.Timestamp("2025-01-01", tz="UTC")
    return EventRankWFAConfig(
        development_start_ms=int(start.timestamp() * 1_000),
        evaluation_start_ms=int((start + pd.Timedelta(days=28)).timestamp() * 1_000),
        evaluation_end_ms=int((start + pd.Timedelta(days=55)).timestamp() * 1_000),
        minimum_train_rows=100,
        minimum_fit_events=10,
        minimum_validation_events=3,
        iterations=20,
        depth=3,
        early_stopping_rounds=5,
        thread_count=2,
    )


def test_event_rank_wfa_freezes_weekly_and_uses_only_resolved_labels() -> None:
    frame = _rank_rows()
    result = build_event_rank_weekly_wfa(
        frame,
        numeric_features=("signal", "event_underreaction_percentile"),
        categorical_features=(),
        config=_rank_config(),
    )

    assert not result.predictions.empty
    assert set(result.weekly_metadata["status"]) == {"FROZEN_AND_SCORED"}
    assert (
        result.weekly_metadata["latest_train_resolution_time_ms"]
        < result.weekly_metadata["weekly_model_freeze_time_ms"]
    ).all()
    assert (result.predictions.groupby("test_week")["weekly_model_freeze_time_ms"].nunique() == 1).all()


def test_unresolved_future_rank_labels_cannot_change_first_week_model() -> None:
    original = _rank_rows()
    changed = original.copy()
    first_freeze = _rank_config().evaluation_start_ms
    unresolved = changed["nature_resolution_time_ms"].ge(first_freeze)
    changed.loc[unresolved, "catchup_residual_change_60m"] *= -100.0
    changed["rank_target"] = changed.groupby("event_id")[
        "catchup_residual_change_60m"
    ].rank(pct=True)

    first = build_event_rank_weekly_wfa(
        original,
        numeric_features=("signal", "event_underreaction_percentile"),
        categorical_features=(),
        config=_rank_config(),
    ).predictions
    second = build_event_rank_weekly_wfa(
        changed,
        numeric_features=("signal", "event_underreaction_percentile"),
        categorical_features=(),
        config=_rank_config(),
    ).predictions
    first_week = sorted(first["test_week"].unique())[0]
    left = first.loc[first["test_week"].eq(first_week)].sort_values(["event_id", "symbol"])
    right = second.loc[second["test_week"].eq(first_week)].sort_values(["event_id", "symbol"])

    assert left[["event_id", "symbol"]].values.tolist() == right[["event_id", "symbol"]].values.tolist()
    assert np.allclose(left["model_score"], right["model_score"])


def test_event_rank_metrics_reward_correct_within_event_order() -> None:
    rows = []
    for event_index in range(4):
        for rank in range(10):
            rows.append(
                {
                    "event_id": f"event-{event_index}",
                    "test_week": "2025-W10",
                    "snapshot_time_ms": event_index,
                    "session_name": "ASIA",
                    "impulse_direction": 1,
                    "catchup_residual_change_60m": rank / 1_000,
                    "model_score": float(rank),
                    "baseline_score": float(9 - rank),
                }
            )
    metrics = build_event_rank_metrics(pd.DataFrame(rows))

    assert np.allclose(metrics["model_event_spearman"], 1.0)
    assert np.allclose(metrics["baseline_event_spearman"], -1.0)
    assert (metrics["model_top_bottom_catchup_spread"] > 0.0).all()
    assert (metrics["delta_event_spearman"] > 0.0).all()
