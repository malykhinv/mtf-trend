from __future__ import annotations

import pandas as pd
import pytest

from anomaly_science.strategy.pump_wave_short.lifecycle_review import (
    HOUR_MS,
    IS_END_EXCLUSIVE_MS,
    LifecycleReviewConfig,
    SOURCE_COLUMNS,
    SOURCE_POPULATION_SCOPE,
    build_lifecycle_population,
    sample_lifecycle_review_queue,
)
from anomaly_science.strategy.pump_wave_short.lifecycle_label_audit import (
    LifecycleLabelAuditConfig,
    audit_lifecycle_labels,
    pilot_identity_sha256,
)


def test_source_scope_explicitly_forbids_prevalence_claims() -> None:
    assert "high_score_review_frame" in SOURCE_POPULATION_SCOPE


def _source_row(event_id: str, culmination_ms: int, *, symbol: str = "AAAUSDT") -> dict[str, object]:
    return {
        "event_id": event_id,
        "symbol": symbol,
        "tf": "3m",
        "proposal_time_ms": culmination_ms + 12 * 60_000,
        "pump_start_ms": culmination_ms - HOUR_MS,
        "culmination_ms": culmination_ms,
        "pump_low_price": 100.0,
        "culmination_price": 120.0,
        "pump_pct": 0.2,
        "sleep_range_pct": 0.03,
        "sleep_directional_drift_pct": 0.01,
        "sleep_base_price": 100.0,
        "base_to_high_pct": 0.2,
        "pump_path_efficiency": 0.8,
        "pump_retrace_share": 0.1,
        "pump_single_bar_range_share": 0.2,
        "pump_upper_wick_share": 0.1,
        "pump_over_sleep_vol": 5.0,
        "pump_over_sleep_trades": 4.0,
    }


def test_lifecycle_membership_uses_only_confirmed_wave_one_and_stays_inside_is() -> None:
    anchor = int(pd.Timestamp("2025-08-01T12:00:00Z").timestamp() * 1_000)
    source = pd.DataFrame([_source_row("first", anchor)], columns=SOURCE_COLUMNS)

    population = build_lifecycle_population(source)

    assert len(population) == 1
    row = population.iloc[0]
    assert row["feature_cutoff_time_ms"] == source.iloc[0]["proposal_time_ms"]
    assert row["feature_cutoff_time_ms"] <= row["selection_snapshot_time_ms"] < row["review_end_ms"]
    assert row["review_end_ms"] < IS_END_EXCLUSIVE_MS
    assert not bool(row["untouched_2026_row_used"])


def test_tail_mutation_cannot_change_lifecycle_candidate_fields() -> None:
    anchor = int(pd.Timestamp("2025-08-01T12:00:00Z").timestamp() * 1_000)
    source = pd.DataFrame([_source_row("first", anchor)], columns=SOURCE_COLUMNS)
    original = build_lifecycle_population(source)
    changed = build_lifecycle_population(source.copy())
    pd.testing.assert_frame_equal(original, changed)


def test_outcome_columns_are_rejected() -> None:
    anchor = int(pd.Timestamp("2025-08-01T12:00:00Z").timestamp() * 1_000)
    source = pd.DataFrame([_source_row("first", anchor)], columns=SOURCE_COLUMNS).assign(win=True)

    with pytest.raises(ValueError, match="forbidden future/outcome"):
        build_lifecycle_population(source)


def test_month_balanced_sample_is_hash_deterministic() -> None:
    rows = []
    for month in (7, 8):
        base = int(pd.Timestamp(year=2025, month=month, day=2, tz="UTC").timestamp() * 1_000)
        rows.extend(_source_row(f"{month}-{i}", base + i * HOUR_MS, symbol=f"S{i}") for i in range(8))
    source = pd.DataFrame(rows, columns=SOURCE_COLUMNS)
    config = LifecycleReviewConfig(pilot_per_month=3)
    population = build_lifecycle_population(source, config=config)

    first = sample_lifecycle_review_queue(population, config=config)
    second = sample_lifecycle_review_queue(population.sample(frac=1.0, random_state=7), config=config)

    assert first.groupby("review_month").size().to_dict() == {"2025-07": 3, "2025-08": 3}
    assert first["event_id"].tolist() == second["event_id"].tolist()


def _lifecycle_candidates_for_audit(count: int = 4) -> pd.DataFrame:
    anchor = int(pd.Timestamp("2025-08-01T12:00:00Z").timestamp() * 1_000)
    source = pd.DataFrame(
        [_source_row(f"audit-{index}", anchor + index * HOUR_MS, symbol=f"S{index}") for index in range(count)],
        columns=SOURCE_COLUMNS,
    )
    return build_lifecycle_population(source)


def _manual_label(event_id: str, *, wave_count: int) -> dict[str, object]:
    waves = [
        {
            "wave_ordinal": index + 1,
            "start_ms": 1_000 + index * 2_000,
            "start_price": 1.0 + index,
            "culmination_ms": 2_000 + index * 2_000,
            "culmination_price": 1.5 + index,
        }
        for index in range(wave_count)
    ]
    sideways = [
        {
            "after_wave_ordinal": index + 1,
            "start_ms": waves[index]["culmination_ms"],
            "end_ms": waves[index + 1]["start_ms"],
            "lower_price": 1.0,
            "upper_price": 3.0,
        }
        for index in range(max(0, wave_count - 1))
    ]
    first = waves[0]
    return {
        "event_id": event_id,
        "symbol": "TESTUSDT",
        "tf": "3m",
        "source": "browser_level_labeler",
        "review_notes": "Clear expert explanation of the morphology.",
        "review_answers": {
            "sleep_before_w1": "clear",
            "wave_separation": "distinct_restart" if wave_count > 1 else "not_applicable",
            "elevated_base": "retained",
            "activity_driver": "coin_specific",
            "post_wave_state": "distribution_dump",
            "boundary_confidence": "high",
        },
        "setups": [
            {
                "family": "unknown",
                "quality": "good",
                "has_level": False,
                "has_pump_transition": True,
                "has_structure_break": False,
                "pump_waves": waves,
                "sideways_segments": sideways,
                "pump_start_ms": first["start_ms"],
                "pump_start_price": first["start_price"],
                "culmination_ms": first["culmination_ms"],
                "culmination_price": first["culmination_price"],
            }
        ],
    }


def test_label_audit_stays_incomplete_until_every_frozen_candidate_is_reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = _lifecycle_candidates_for_audit()
    monkeypatch.setattr(
        "anomaly_science.strategy.pump_wave_short.lifecycle_label_audit.REGISTERED_PILOT_IDENTITY_SHA256",
        pilot_identity_sha256(candidates),
    )
    first_id = str(candidates.iloc[0]["event_id"])
    report = audit_lifecycle_labels(candidates, {first_id: _manual_label(first_id, wave_count=2)})

    assert report.status == "ANNOTATION_INCOMPLETE"
    assert report.effective_label_rows == 1
    assert not report.gates["all_candidates_reviewed"]
    assert report.untouched_2026_rows_used == 0


def test_label_audit_requires_both_multiwave_and_non_multiwave_support(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = _lifecycle_candidates_for_audit()
    monkeypatch.setattr(
        "anomaly_science.strategy.pump_wave_short.lifecycle_label_audit.REGISTERED_PILOT_IDENTITY_SHA256",
        pilot_identity_sha256(candidates),
    )
    labels = {
        str(row.event_id): _manual_label(str(row.event_id), wave_count=2 if index < 2 else 1)
        for index, row in enumerate(candidates.itertuples(index=False))
    }
    report = audit_lifecycle_labels(
        candidates,
        labels,
        config=LifecycleLabelAuditConfig(minimum_multiwave_examples=2, minimum_non_multiwave_examples=2),
    )

    assert report.status == "READY_FOR_BLINDED_RELIABILITY_REVIEW"
    assert report.multiwave_examples == 2
    assert report.non_multiwave_examples == 2
    assert all(report.gates.values())
