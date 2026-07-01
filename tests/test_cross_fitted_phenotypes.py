from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.phenotypes import (
    CrossFittedPhenotypeConfig,
    PhenotypeCompositionConfig,
    PhenotypeDiscoveryError,
    PhenotypeGeneratorSpec,
    PhenotypeInputContract,
    build_cross_fitted_phenotypes,
    build_phenotype_compositions,
    build_phenotype_followup_registry,
    prepare_phenotype_data,
    run_cross_fitted_phenotype_discovery,
    run_phenotype_composition_discovery,
    run_phenotype_followup_registry,
)


def _config() -> CrossFittedPhenotypeConfig:
    return CrossFittedPhenotypeConfig(
        protocol_freeze_id="synthetic-cross-fitted-phenotype-v1",
        search_start_utc="2025-06-01T00:00:00Z",
        search_end_utc="2025-10-15T00:00:00Z",
        calibration_end_utc="2025-12-01T00:00:00Z",
        verification_end_utc="2026-02-15T00:00:00Z",
        input=PhenotypeInputContract(
            group_column="event_id",
            split_group_column="chain_id",
            symbol_column="symbol",
            label_column="y",
            snapshot_time_column="snapshot_time_ms",
            feature_cutoff_time_column="feature_cutoff_time_ms",
            future_start_time_column="future_start_time_ms",
            resolution_time_column="resolution_time_ms",
            label_available_column="label_available",
            label_schema_column="label_schema_version",
            required_label_schema_value="synthetic_fade_v1",
            label_only_columns=("y", "resolution_time_ms", "future_start_time_ms"),
        ),
        numeric_features=("x1", "x2"),
        categorical_features=("session",),
        generators=(
            PhenotypeGeneratorSpec(seed=11, depth=2),
            PhenotypeGeneratorSpec(seed=29, depth=3),
        ),
        cross_fit_folds=2,
        initial_train_fraction=0.45,
        iterations=20,
        min_fold_events=8,
        min_fold_fade_rate=0.60,
        min_fold_lift=1.08,
        min_fold_wilson_lower_95=0.40,
        consensus_membership_jaccard=0.45,
        min_fold_support_count=2,
        min_generator_support_count=2,
        max_frozen_phenotypes=10,
        calibration_min_events=15,
        calibration_candidate_probability=0.55,
        verification_min_events=15,
        verification_min_observed_rate=0.60,
        verification_min_wilson_lower_95=0.50,
        null_permutations=19,
        random_seed=73,
    )


def _rows() -> pd.DataFrame:
    rng = np.random.default_rng(20260701)
    times = pd.date_range("2025-06-01", "2026-02-14", freq="6h", tz="UTC")
    x1 = rng.normal(size=len(times))
    x2 = rng.normal(size=len(times))
    signal = (x1 > 0.35) & (x2 < 0.0)
    probability = np.where(signal, 0.88, 0.22)
    y = rng.binomial(1, probability)
    timestamp = (times.astype("int64") // 1_000_000).to_numpy(np.int64)
    frame = pd.DataFrame(
        {
            "event_id": [f"event_{index}" for index in range(len(times))],
            "chain_id": [f"chain_{index}" for index in range(len(times))],
            "symbol": np.where(np.arange(len(times)) % 2, "AAAUSDT", "BBBUSDT"),
            "snapshot_time_ms": timestamp,
            "feature_cutoff_time_ms": timestamp,
            "future_start_time_ms": timestamp + 60_000,
            "resolution_time_ms": timestamp + 120_000,
            "label_available": True,
            "label_schema_version": "synthetic_fade_v1",
            "y": y,
            "x1": x1,
            "x2": x2,
            "session": np.where(np.arange(len(times)) % 3, "eu", "us"),
        }
    )
    frame.loc[frame.index[::37], "x2"] = np.nan
    return frame


def test_cross_fitted_phenotypes_find_recurrent_high_probability_rule() -> None:
    result = build_cross_fitted_phenotypes(_rows(), _config())

    assert result.candidate_count > 0
    assert result.frozen_count > 0
    assert not result.screening.empty
    assert {"CALIBRATION_REJECTED", "VERIFICATION_REJECTED", "VERIFIED_PHENOTYPE", "HIGH_PROBABILITY_VERIFIED", "CONTROL_FAILED"} >= set(result.catalog["status"])
    assert (result.catalog["verification_count"] >= 0).all()
    assert set(result.coverage["coverage_kind"]) == {
        "all_frozen", "verified", "high_probability_verified"
    }
    assert set(result.followup_registry["phenotype_id"]) == set(
        result.catalog["phenotype_id"]
    )
    assert result.followup_registry["forbidden_action"].str.contains(
        "viewed calibration/verification", regex=False
    ).all()


def test_followup_registry_retains_failed_candidates_without_reclassifying_them() -> None:
    catalog = pd.DataFrame(
        [
            {
                "phenotype_id": "phenotype_001",
                "status": "VERIFICATION_REJECTED",
                "rule_text": "x1 > 0.5",
                "feature_names": "x1",
                "search_oof_count": 100,
                "search_oof_rate": 0.70,
                "calibration_count": 80,
                "calibrated_probability": 0.65,
                "verification_count": 70,
                "verification_rate": 0.54,
                "verification_wilson_lower_95": 0.42,
                "verification_calibration_gap": 0.11,
                "verification_q_value": 0.20,
                "verification_positive_week_fraction": 0.50,
            }
        ]
    )

    registry = build_phenotype_followup_registry(catalog, _config())

    assert registry.loc[0, "current_status"] == "VERIFICATION_REJECTED"
    assert registry.loc[0, "research_disposition"] == "FORWARD_STABILITY_CANDIDATE"
    assert registry.loc[0, "followup_priority"] == "MEDIUM"
    assert "new forward data" in registry.loc[0, "allowed_action"]


def test_phenotype_transform_is_search_fitted_and_explicit_about_missingness() -> None:
    prepared = prepare_phenotype_data(_rows(), _config())

    assert "x2__missing" in prepared.model_feature_names
    assert prepared.imputation_values["x2"] == pytest.approx(
        pd.to_numeric(prepared.search["x2"]).median()
    )
    assert set(prepared.search["chain_id"]).isdisjoint(prepared.calibration["chain_id"])
    assert set(prepared.calibration["chain_id"]).isdisjoint(prepared.verification["chain_id"])


def test_phenotype_temporal_contract_rejects_future_feature() -> None:
    rows = _rows()
    rows.loc[0, "feature_cutoff_time_ms"] = rows.loc[0, "snapshot_time_ms"] + 1

    with pytest.raises(PhenotypeDiscoveryError, match="features exceed snapshot"):
        prepare_phenotype_data(rows, _config())


def test_phenotype_run_writes_complete_artifacts(tmp_path: Path) -> None:
    input_path = tmp_path / "rows.parquet"
    _rows().to_parquet(input_path, index=False)
    progress: list[tuple[str, int, int]] = []
    out = run_cross_fitted_phenotype_discovery(
        input_path=input_path,
        out_dir=tmp_path / "out",
        config=_config(),
        allow_dirty_development=True,
        progress_callback=lambda stage, completed, total: progress.append(
            (stage, completed, total)
        ),
    )

    for name in (
        "phenotype_catalog.csv", "phenotype_screening.csv",
        "phenotype_followup_registry.csv",
        "phenotype_assignments.parquet", "phenotype_coverage.csv",
        "phenotype_controls.csv", "frozen_phenotype_rules.json",
        "phenotype_feature_transform.json", "phenotype_protocol.json",
        "phenotype.manifest.json",
    ):
        assert (out / name).is_file()
    assert ("real_search", 1, 1) in progress
    assert ("null_controls", 19, 19) in progress
    assert progress[-1] == ("calibration_verification", 1, 1)


def test_existing_catalog_can_receive_separate_governed_followup_memory(
    tmp_path: Path,
) -> None:
    result = build_cross_fitted_phenotypes(_rows(), _config())
    catalog_path = tmp_path / "phenotype_catalog.csv"
    result.catalog.to_csv(catalog_path, index=False)

    out = run_phenotype_followup_registry(
        catalog_path=catalog_path,
        out_dir=tmp_path / "followup",
        config=_config(),
    )

    assert (out / "phenotype_followup_registry.csv").is_file()
    assert (out / "phenotype_followup_protocol.json").is_file()
    assert (out / "phenotype_followup.manifest.json").is_file()


def _composition_config() -> PhenotypeCompositionConfig:
    return PhenotypeCompositionConfig(
        protocol_freeze_id="synthetic-compositions-v1",
        calibration_min_events=15,
        calibration_min_probability=0.55,
        calibration_min_wilson_lower_95=0.40,
        verification_min_events=15,
        verification_min_observed_rate=0.55,
        verification_min_wilson_lower_95=0.40,
        verification_max_calibration_gap=0.20,
        verification_min_positive_week_fraction=0.40,
        max_membership_jaccard=0.95,
    )


def _base_rules() -> tuple[dict[str, object], ...]:
    return (
        {
            "phenotype_id": "base_1",
            "rule": ({"feature": "x1", "operator": ">", "value": 0.35},),
        },
        {
            "phenotype_id": "base_2",
            "rule": ({"feature": "x2", "operator": "<=", "value": 0.0},),
        },
        {
            "phenotype_id": "base_3",
            "rule": ({"feature": "session==eu", "operator": ">", "value": 0.5},),
        },
    )


def test_composition_layer_finds_intersections_without_changing_base_rules() -> None:
    base_rules = _base_rules()
    result = build_phenotype_compositions(
        _rows(), base_rules, _config(), _composition_config()
    )

    assert len(result.screening) == 4
    assert result.screening["passed_calibration_gate"].any()
    assert set(result.catalog["status"]) <= {
        "HIGH_PROBABILITY_VERIFIED", "VERIFICATION_REJECTED", "CONTROL_FAILED"
    }
    assert result.catalog["research_disposition"].notna().all()
    assert result.catalog["forbidden_action"].str.contains(
        "viewed calibration/verification", regex=False
    ).all()
    assert base_rules == _base_rules()
    assert len(result.controls) == 19


def test_composition_run_writes_complete_artifacts(tmp_path: Path) -> None:
    input_path = tmp_path / "rows.parquet"
    _rows().to_parquet(input_path, index=False)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {
                "protocol_freeze_id": _config().protocol_freeze_id,
                "immutable_after_search": True,
                "rules": _base_rules(),
            }
        ),
        encoding="utf-8",
    )

    out = run_phenotype_composition_discovery(
        input_path=input_path,
        frozen_rules_path=rules_path,
        out_dir=tmp_path / "compositions",
        phenotype_config=_config(),
        composition_config=_composition_config(),
        allow_dirty_development=True,
    )

    for name in (
        "phenotype_composition_catalog.csv",
        "phenotype_composition_screening.csv",
        "phenotype_composition_controls.csv",
        "phenotype_composition_coverage.csv",
        "phenotype_composition_assignments.parquet",
        "phenotype_composition_protocol.json",
        "phenotype_composition.manifest.json",
    ):
        assert (out / name).is_file()
