from __future__ import annotations

import pandas as pd

from anomaly_science.strategy.triple_tap.research.sleep_pump_catboost import (
    SleepPumpCatBoostConfig,
    build_weekly_oos_predictions,
)
from anomaly_science.strategy.triple_tap.research.sleep_pump_learning import build_learning_frame


def _candidates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": "candidate_a",
                "symbol": "AUSDT",
                "proposal_time_ms": 1_000,
                "review_end_ms": 2_000,
                "pump_start_ms": 700,
                "culmination_ms": 900,
            },
            {
                "event_id": "candidate_b",
                "symbol": "BUSDT",
                "proposal_time_ms": 3_000,
                "review_end_ms": 4_000,
                "pump_start_ms": 2_700,
                "culmination_ms": 2_900,
            },
        ]
    )


def test_learning_target_is_card_level_and_keeps_expert_geometry_separate() -> None:
    labels = {
        "group_a": {
            "source_event_id": "candidate_a",
            "saved_at_ms": 12,
            "setups": [
                {
                    "notes": "BOT sleep→pump hypothesis",
                    "has_pump_transition": True,
                    "pump_start_ms": 700,
                    "culmination_ms": 900,
                },
                {
                    "notes": "",
                    "has_pump_transition": True,
                    "pump_start_ms": 720,
                    "culmination_ms": 880,
                    "has_level": True,
                    "family": "cap",
                    "quality": "ok",
                },
            ],
        },
        "group_b": {
            "source_event_id": "candidate_b",
            "saved_at_ms": 13,
            "setups": [{"notes": "BOT sleep→pump hypothesis", "has_pump_transition": False}],
        },
    }

    frame = build_learning_frame(_candidates(), labels)
    first, second = frame.to_dict("records")

    assert first["expert_has_sleep_pump"] is True
    assert first["expert_pump_count"] == 2
    assert first["expert_manual_setup_count"] == 1
    assert first["expert_structural_setup_count"] == 1
    assert first["annotation_outcome"] == "retained_with_expert_geometry"
    assert first["feature_cutoff_time_ms"] == 1_000
    assert first["label_observation_end_ms"] == 2_000
    assert second["expert_has_sleep_pump"] is False
    assert second["annotation_outcome"] == "rejected"


def test_weekly_catboost_oos_purges_labels_not_observed_before_test_week() -> None:
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2025-01-06", tz="UTC")
    for week in range(4):
        for item in range(4):
            cutoff = start + pd.Timedelta(days=7 * week, hours=item)
            rows.append(
                {
                    "event_id": f"event_{week}_{item}",
                    "symbol": "AUSDT",
                    "feature_cutoff_time_ms": int(cutoff.timestamp() * 1_000),
                    # The final row in week 1 is still unobserved at week 2.
                    "label_observation_end_ms": int((cutoff + pd.Timedelta(days=8 if (week, item) == (1, 3) else 1)).timestamp() * 1_000),
                    "expert_has_sleep_pump": bool((week + item) % 2),
                    "score": float(item),
                    "pump_pct": 0.1 + item,
                    "pump_bars": 10 + item,
                    "pump_hours": 0.5 + item,
                    "sleep_range_pct": 0.02 + item / 100,
                    "sleep_directional_drift_pct": 0.01 + item / 100,
                    "base_to_high_pct": 0.1 + item / 10,
                    "pump_path_efficiency": 0.5 + item / 10,
                    "pump_retrace_share": 0.05 + item / 100,
                    "pump_single_bar_range_share": 0.2 + item / 100,
                    "pump_upper_wick_share": 0.1 + item / 100,
                    "pump_over_sleep_vol": 1.0 + item,
                    "pump_over_sleep_trades": 1.5 + item,
                }
            )
    predictions, _ = build_weekly_oos_predictions(
        pd.DataFrame(rows),
        config=SleepPumpCatBoostConfig(min_train_rows=4, iterations=2, depth=2, thread_count=1),
    )

    assert not predictions.empty
    week_two = predictions.loc[predictions["test_week"] == "2025-W04"]
    assert not week_two.empty
    # Four rows from week 0 are available; the late week-1 label is correctly
    # purged rather than becoming training information for week 2.
    assert set(week_two["train_row_count"]) == {7}
