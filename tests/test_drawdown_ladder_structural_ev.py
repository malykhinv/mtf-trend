from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from anomaly_science.strategy.drawdown_ladder.structural_ev import (
    StructuralEVError,
    StructuralEVSpec,
    assemble_structural_ev_rows,
    write_structural_ev_protocol,
)


def _causal_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    snapshot = pd.Timestamp("2025-08-04T12:00:00Z")
    snapshot_ms = int(snapshot.timestamp() * 1_000)
    candidate_ids = ["c1", "c2", "c3"]
    parent_ids = ["p1", "p2", "p3"]
    symbols = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    probabilities = [0.92, 0.919, 0.95]
    states = [(3, 6), (5, 10), (10, 20)]
    predictions = pd.DataFrame(
        {
            "group": candidate_ids,
            "parent_event_id": parent_ids,
            "symbol": symbols,
            "snapshot_time_ms": [snapshot_ms + index * 60_000 for index in range(3)],
            "feature_cutoff_time_ms": [
                snapshot_ms + index * 60_000 for index in range(3)
            ],
            "future_start_time_ms": [
                snapshot_ms + (index + 1) * 60_000 for index in range(3)
            ],
            "test_week": ["2025-W32"] * 3,
            "weekly_model_freeze_time_ms": [snapshot_ms - 86_400_000] * 3,
            "model_id": ["structural"] * 3,
            "grid_step_pct": [state[0] for state in states],
            "deepest_filled_level_pct": [state[1] for state in states],
            "session_name": ["us"] * 3,
            "calibrated_probability": probabilities,
            "target": [True, False, True],
        }
    )
    features = pd.DataFrame(
        {
            "candidate_id": candidate_ids,
            "stage1_feature_cutoff_time_ms": predictions["feature_cutoff_time_ms"],
            "snapshot_close_to_entry": [-0.02, -0.03, -0.04],
            "feature_schema_version": ["v1"] * 3,
        }
    )
    outcomes = pd.DataFrame(
        {
            "candidate_id": candidate_ids,
            "parent_event_id": parent_ids,
            "symbol": symbols,
            "snapshot_time_ms": predictions["snapshot_time_ms"],
            "stage0_trigger_cutoff_time_ms": [snapshot_ms - 3_600_000] * 3,
            "future_start_time_ms": predictions["future_start_time_ms"],
            "horizon_complete": [True] * 3,
            "break_even_25bps_reached": [True, False, True],
            "label_available_2880m": [True] * 3,
            "future_return_2880m": [0.04, -0.10, 0.03],
            "future_max_return_2880m": [0.08, 0.01, 0.06],
            "future_min_return_2880m": [-0.05, -0.15, -0.09],
            "untouched_2026_row_used": [False] * 3,
        }
    )
    return predictions, features, outcomes


def test_structural_ev_decision_is_causal_and_threshold_is_inclusive() -> None:
    predictions, features, outcomes = _causal_inputs()

    rows = assemble_structural_ev_rows(
        predictions=predictions,
        features=features,
        outcomes=outcomes,
    ).set_index("candidate_id")

    assert rows.loc["c1", "decision"] == "HOLD_48H"
    assert rows.loc["c2", "decision"] == "EXIT_AT_SNAPSHOT"
    assert rows.loc["c1", "policy_net_return_25bps"] == pytest.approx(0.0375)
    assert rows.loc["c2", "policy_net_return_25bps"] == pytest.approx(-0.0325)
    assert rows.loc["c2", "policy_minus_hold_return_25bps"] == pytest.approx(0.07)


def test_future_return_mutation_cannot_change_frozen_decision() -> None:
    predictions, features, outcomes = _causal_inputs()
    original = assemble_structural_ev_rows(
        predictions=predictions,
        features=features,
        outcomes=outcomes,
    )
    mutated_outcomes = outcomes.copy()
    mutated_outcomes["future_return_2880m"] *= -1.0
    mutated = assemble_structural_ev_rows(
        predictions=predictions,
        features=features,
        outcomes=mutated_outcomes,
    )

    assert mutated["decision"].tolist() == original["decision"].tolist()
    assert mutated["policy_net_return_25bps"].tolist() != original[
        "policy_net_return_25bps"
    ].tolist()


def test_structural_ev_rejects_future_start_at_snapshot() -> None:
    predictions, features, outcomes = _causal_inputs()
    predictions.loc[0, "future_start_time_ms"] = predictions.loc[
        0, "snapshot_time_ms"
    ]
    outcomes.loc[0, "future_start_time_ms"] = outcomes.loc[0, "snapshot_time_ms"]

    with pytest.raises(StructuralEVError, match="future starts"):
        assemble_structural_ev_rows(
            predictions=predictions,
            features=features,
            outcomes=outcomes,
        )


def test_structural_ev_rejects_prediction_stage1_cutoff_mismatch() -> None:
    predictions, features, outcomes = _causal_inputs()
    features.loc[0, "stage1_feature_cutoff_time_ms"] -= 60_000

    with pytest.raises(StructuralEVError, match="Stage-1 feature cutoff"):
        assemble_structural_ev_rows(
            predictions=predictions,
            features=features,
            outcomes=outcomes,
        )


def test_structural_ev_rejects_stage0_trigger_cutoff_after_snapshot() -> None:
    predictions, features, outcomes = _causal_inputs()
    outcomes.loc[0, "stage0_trigger_cutoff_time_ms"] = (
        outcomes.loc[0, "snapshot_time_ms"] + 60_000
    )

    with pytest.raises(StructuralEVError, match="Stage-0 trigger cutoff"):
        assemble_structural_ev_rows(
            predictions=predictions,
            features=features,
            outcomes=outcomes,
        )


def test_structural_ev_rejects_horizon_that_reaches_untouched_2026() -> None:
    predictions, features, outcomes = _causal_inputs()
    late_snapshot = int(pd.Timestamp("2025-12-31T00:01:00Z").timestamp() * 1_000)
    predictions.loc[0, "snapshot_time_ms"] = late_snapshot
    predictions.loc[0, "feature_cutoff_time_ms"] = late_snapshot
    predictions.loc[0, "future_start_time_ms"] = late_snapshot + 60_000
    outcomes.loc[0, "snapshot_time_ms"] = late_snapshot
    features.loc[0, "stage1_feature_cutoff_time_ms"] = late_snapshot
    outcomes.loc[0, "future_start_time_ms"] = late_snapshot + 60_000

    with pytest.raises(StructuralEVError, match="reaches untouched 2026"):
        assemble_structural_ev_rows(
            predictions=predictions,
            features=features,
            outcomes=outcomes,
        )


def test_protocol_adjusts_for_arms_endpoints_and_viewed_states(tmp_path: Path) -> None:
    spec = StructuralEVSpec()
    path = write_structural_ev_protocol(tmp_path / "protocol.json", spec)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert spec.familywise_alpha == pytest.approx(0.05 / 12)
    assert payload["oos_2026_accessed"] is False
    assert "3 viewed structural states" in payload["inference"]["selection_adjustment"]
