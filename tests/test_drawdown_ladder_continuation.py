from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from anomaly_science.strategy.drawdown_ladder.continuation import (
    ContinuationExperimentError,
    ContinuationExperimentSpec,
    assemble_continuation_dataset,
    build_continuation_probability_config,
    build_mirrored_continuation_spec,
    write_continuation_protocol,
)


def _inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    snapshot = int(pd.Timestamp("2025-08-05T12:00:00Z").timestamp() * 1_000)
    features = pd.DataFrame(
        {
            "feature_schema_version": ["drawdown_ladder_causal_features_v1"] * 2,
            "candidate_id": ["primary", "secondary"],
            "parent_event_id": ["p1", "p2"],
            "symbol": ["AAAUSDT", "BBBUSDT"],
            "snapshot_time_ms": [snapshot, snapshot + 60_000],
            "feature_cutoff_time_ms": [snapshot, snapshot + 60_000],
            "future_start_time_ms": [snapshot + 60_000, snapshot + 120_000],
            "grid_step_pct": [3, 5],
            "deepest_filled_level_pct": [6, 10],
            "snapshot_close_to_entry": [-0.02, -0.04],
        }
    )
    outcomes = pd.DataFrame(
        {
            "candidate_id": ["primary", "secondary"],
            "parent_event_id": ["p1", "p2"],
            "symbol": ["AAAUSDT", "BBBUSDT"],
            "snapshot_time_ms": features["snapshot_time_ms"],
            "feature_cutoff_time_ms": [snapshot - 3_600_000] * 2,
            "future_start_time_ms": features["future_start_time_ms"],
            "horizon_complete": [True, True],
            "label_available_2880m": [True, True],
            "future_return_2880m": [0.01, -0.10],
            "untouched_2026_row_used": [False, False],
        }
    )
    return features, outcomes


def test_continuation_target_is_future_hold_minus_causal_exit() -> None:
    features, outcomes = _inputs()

    result = assemble_continuation_dataset(features=features, outcomes=outcomes)

    assert result["candidate_id"].tolist() == ["primary"]
    assert result.loc[0, "hold_minus_exit_48h"] == pytest.approx(0.03)
    assert bool(result.loc[0, "hold_outperforms_exit_48h"])
    assert result.loc[0, "continuation_resolution_time_ms"] == (
        result.loc[0, "snapshot_time_ms"] + 2_880 * 60_000
    )


def test_future_mutation_changes_label_but_not_feature_cutoff() -> None:
    features, outcomes = _inputs()
    original = assemble_continuation_dataset(features=features, outcomes=outcomes)
    mutated_outcomes = outcomes.copy()
    mutated_outcomes.loc[0, "future_return_2880m"] = -0.03
    mutated = assemble_continuation_dataset(
        features=features, outcomes=mutated_outcomes
    )

    assert not bool(mutated.loc[0, "hold_outperforms_exit_48h"])
    assert mutated.loc[0, "feature_cutoff_time_ms"] == original.loc[
        0, "feature_cutoff_time_ms"
    ]


def test_continuation_rejects_horizon_reaching_2026() -> None:
    features, outcomes = _inputs()
    late = int(pd.Timestamp("2025-12-31T00:01:00Z").timestamp() * 1_000)
    for frame in (features, outcomes):
        frame.loc[0, "snapshot_time_ms"] = late
        frame.loc[0, "future_start_time_ms"] = late + 60_000
    features.loc[0, "feature_cutoff_time_ms"] = late

    with pytest.raises(ContinuationExperimentError, match="reaches untouched 2026"):
        assemble_continuation_dataset(features=features, outcomes=outcomes)


def test_continuation_probability_contract_uses_primary_state_and_full_picture() -> None:
    structural = build_continuation_probability_config(arm="structural_baseline")
    full = build_continuation_probability_config(arm="full_causal")

    assert structural.label_column == "hold_outperforms_exit_48h"
    assert structural.row_filter_column == "is_primary_continuation_state"
    assert structural.gates.max_auc_permutation_p_value == pytest.approx(0.05 / 4)
    assert "snapshot_close_to_entry" in structural.numeric_features
    assert len(full.numeric_features) > len(structural.numeric_features)
    assert "btc_return_60m" in full.numeric_features


def test_continuation_protocol_forbids_ev_and_oos(tmp_path: Path) -> None:
    paths = write_continuation_protocol(tmp_path)
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))

    assert len(paths) == 4
    assert payload["physical_exits_or_pnl_simulated"] is False
    assert payload["oos_2026_accessed"] is False
    assert payload["primary_arm"] == "structural_baseline"


def test_mirrored_continuation_uses_signed_short_snapshot_exit() -> None:
    features, outcomes = _inputs()
    features.loc[0, "snapshot_close_to_entry"] = 0.02
    outcomes.loc[0, "future_return_2880m"] = 0.01
    spec = build_mirrored_continuation_spec()

    result = assemble_continuation_dataset(
        features=features,
        outcomes=outcomes,
        spec=spec,
    )

    assert result.loc[0, "signed_snapshot_exit_return"] == pytest.approx(-0.02)
    assert result.loc[0, "hold_minus_exit_48h"] == pytest.approx(0.03)
    assert bool(result.loc[0, "hold_outperforms_exit_48h"])
