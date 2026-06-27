from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.archetypes import (
    ArchetypeDiscoveryConfig,
    ArchetypeFeatureSpec,
    ArchetypeInputContract,
    ArchetypePopulationSpec,
    run_archetype_discovery,
)
from anomaly_science.archetypes.builder import (
    ArchetypeDiscoveryError,
    build_archetype_discovery,
    prepare_archetype_data,
)
from anomaly_science.contracts.time import TemporalContractError


def _config() -> ArchetypeDiscoveryConfig:
    return ArchetypeDiscoveryConfig(
        discovery_start_utc="2025-01-01T00:00:00Z",
        discovery_end_utc="2026-01-01T00:00:00Z",
        verification_start_utc="2026-01-01T00:00:00Z",
        verification_end_utc="2026-07-01T00:00:00Z",
        input=ArchetypeInputContract(
            group_column="group",
            symbol_column="symbol",
            label_column="y",
            snapshot_time_column="snapshot_time_ms",
            resolution_time_column="resolution_time_ms",
            feature_cutoff_time_column="feature_cutoff_time_ms",
            future_start_time_column="future_start_time_ms",
        ),
        features=ArchetypeFeatureSpec(
            numeric=("nature_signal", "noise"),
            categorical=("session",),
            derived_time=("minutes_from_round_hour",),
        ),
        population=ArchetypePopulationSpec(
            minimum_value_column="remaining_reward",
            minimum_value=0.03,
        ),
        random_seed=7,
        iterations=16,
        depth=2,
        learning_rate=0.15,
        l2_leaf_reg=2.0,
        min_discovery_events=20,
        min_verification_events=20,
        min_discovery_fade_rate=0.75,
        min_verification_fade_rate=0.75,
        min_discovery_lift=1.25,
        min_verification_lift=1.25,
        max_membership_jaccard=0.8,
        max_categories=8,
        fdr_alpha=0.05,
        stability_frequency="M",
        min_events_per_stability_period=4,
        min_positive_stability_fraction=0.75,
        shuffled_seeds=(19,),
    )


def _synthetic_rows() -> pd.DataFrame:
    rng = np.random.default_rng(123)
    rows: list[dict[str, object]] = []
    for split, start, groups in (
        ("discovery", pd.Timestamp("2025-02-01", tz="UTC"), 160),
        ("verification", pd.Timestamp("2026-01-10", tz="UTC"), 160),
    ):
        for group_number in range(groups):
            nature = group_number % 2
            month_offset = group_number % 5
            day_offset = group_number // 5
            group_start = start + pd.DateOffset(months=month_offset) + pd.Timedelta(days=day_offset)
            group = f"{split}_{group_number:04d}"
            for decision in range(2):
                snapshot = group_start + pd.Timedelta(minutes=decision)
                snapshot_ms = int(snapshot.timestamp() * 1000)
                rows.append(
                    {
                        "group": group,
                        "symbol": f"S{group_number % 12:02d}",
                        "snapshot_time_ms": snapshot_ms,
                        "feature_cutoff_time_ms": snapshot_ms,
                        "future_start_time_ms": snapshot_ms + 60_000,
                        "resolution_time_ms": snapshot_ms + 120_000,
                        "y": nature,
                        "nature_signal": float(nature),
                        "noise": float(rng.normal()),
                        "session": "asia" if group_number % 3 else "us",
                        "remaining_reward": 0.05,
                    }
                )
    return pd.DataFrame(rows)


def test_archetype_discovery_finds_frozen_rule_and_defeats_controls() -> None:
    frame = _synthetic_rows()
    result = build_archetype_discovery(frame, _config())

    verified = [row for row in result.category_rows if row.status == "VERIFIED"]
    assert verified
    assert any("nature_signal" in row.feature_names for row in verified)
    assert all(row.verification_event_count <= 160 for row in result.category_rows)
    assert result.verification_auc > 0.95
    controls = {(row.control_name, row.random_seed): row for row in result.control_rows}
    assert controls[("blind", 0)].verification_auc == 0.5
    assert controls[("group_shuffled_labels", 19)].verification_auc < 0.65
    assert controls[("group_shuffled_labels", 19)].verified_category_count == 0
    assert set(result.assignments["split"]) == {"discovery", "verification"}


def test_archetype_temporal_contract_rejects_future_feature_cutoff() -> None:
    frame = _synthetic_rows()
    frame.loc[0, "feature_cutoff_time_ms"] = frame.loc[0, "snapshot_time_ms"] + 1

    with pytest.raises(TemporalContractError, match="feature_cutoff_time"):
        prepare_archetype_data(frame, _config())


def test_archetype_feature_manifest_rejects_target_as_feature() -> None:
    config = replace(
        _config(),
        features=ArchetypeFeatureSpec(numeric=("nature_signal", "y")),
    )

    with pytest.raises(ArchetypeDiscoveryError, match="cannot be model features"):
        prepare_archetype_data(_synthetic_rows(), config)


def test_archetype_run_writes_reproducible_artifacts(tmp_path: Path) -> None:
    input_path = tmp_path / "rows.parquet"
    out_dir = tmp_path / "out"
    _synthetic_rows().to_parquet(input_path, index=False)

    result = run_archetype_discovery(
        input_path=input_path,
        out_dir=out_dir,
        config=_config(),
    )

    assert result == out_dir
    assert (out_dir / "anomaly_archetype_catalog.csv").is_file()
    assert (out_dir / "anomaly_archetype_controls.csv").is_file()
    assert (out_dir / "strategy_archetype_catalog.csv").is_file()
    assert (out_dir / "strategy_archetype_controls.csv").is_file()
    assert (out_dir / "anomaly_archetype_assignments.parquet").is_file()
    assert (out_dir / "archetype_rule_generator.cbm").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()
    run = json.loads((out_dir / "archetype_run.json").read_text(encoding="utf-8"))
    assert run["verification_groups"] == 160
    assert run["verified_category_count"] > 0
