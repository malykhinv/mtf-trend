from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest

from anomaly_science.regimes import (
    CausalRegimeAtlasConfig,
    RegimeAxisSpec,
    RegimeInteractionComponent,
    RegimeInteractionSpec,
    build_causal_regime_atlas,
    load_causal_regime_atlas_config,
    run_causal_regime_atlas,
)
from anomaly_science.regimes.builder import RegimeAtlasInputError
from anomaly_science.strategy.pump_fade.regimes import (
    PUMP_FADE_REGIME_AXES,
    PUMP_FADE_REGIME_INTERACTIONS,
)


DISCOVERY_START = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp() * 1000)
DISCOVERY_END = int(pd.Timestamp("2025-04-01", tz="UTC").timestamp() * 1000)
VERIFICATION_START = DISCOVERY_END
VERIFICATION_END = int(pd.Timestamp("2025-07-01", tz="UTC").timestamp() * 1000)


def _config(*, kind: str = "fixed") -> CausalRegimeAtlasConfig:
    axis = (
        RegimeAxisSpec("signal_feature", "fixed", (0.5,), candidate_bins=(1,))
        if kind == "fixed"
        else RegimeAxisSpec("signal_feature", "quantile", (0.5,), candidate_bins=(1,))
    )
    return CausalRegimeAtlasConfig(
        discovery_start_ms=DISCOVERY_START,
        discovery_end_ms=DISCOVERY_END,
        verification_start_ms=VERIFICATION_START,
        verification_end_ms=VERIFICATION_END,
        axes=(axis,),
        protocol_freeze_id="regime-atlas-unit-v1",
        activity_match_column="activity",
        activity_match_quantiles=2,
        min_matched_stratum_controls=2,
        min_matched_coverage_fraction=0.9,
        min_discovery_events=40,
        min_verification_events=40,
        min_discovery_delta=0.2,
        min_verification_delta=0.2,
        bootstrap_iterations=200,
        shuffled_iterations=99,
        min_month_stability_events=10,
        min_symbol_stability_events=10,
        min_eligible_months=3,
        min_eligible_symbols=4,
        min_positive_stability_fraction=0.75,
        random_seed=17,
    )


def _rows(*, null: bool = False) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for month in range(1, 7):
        for symbol_index in range(4):
            symbol = f"S{symbol_index}"
            for activity in (0.0, 1.0):
                for signal in (False, True):
                    for index in range(10):
                        day = 1 + index * 2
                        snapshot = pd.Timestamp(
                            year=2025,
                            month=month,
                            day=day,
                            hour=symbol_index,
                            tz="UTC",
                        )
                        snapshot_ms = int(snapshot.timestamp() * 1000)
                        label = int(index < (2 if null or not signal else 8))
                        group = (
                            f"2025-{month:02d}:{symbol}:{int(activity)}:"
                            f"{int(signal)}:{index}"
                        )
                        rows.append(
                            {
                                "group": group,
                                "symbol": symbol,
                                "snapshot_time_ms": snapshot_ms,
                                "feature_cutoff_time_ms": snapshot_ms,
                                "nature_future_start_time_ms": snapshot_ms + 60_000,
                                "nature_y": label,
                                "nature_label_available": True,
                                "is_nature_anchor": True,
                                "activity": activity,
                                "signal_feature": 0.9 if signal else 0.1,
                            }
                        )
    return pd.DataFrame(rows)


def test_causal_regime_atlas_replicates_frozen_matched_signal() -> None:
    result = build_causal_regime_atlas(_rows(), _config())

    assert result.discovery_row_count == 480
    assert result.verification_row_count == 480
    assert len(result.evidence_rows) == 1
    evidence = result.evidence_rows[0]
    assert evidence.regime_id == "signal_feature:bin_1"
    assert evidence.evidence_scope == "development_replication_not_pristine"
    assert evidence.pristine_claim_allowed is False
    assert evidence.discovery_delta == pytest.approx(0.6)
    assert evidence.verification_delta == pytest.approx(0.6)
    assert evidence.discovery_ci_lower_95 > 0.0
    assert evidence.verification_ci_lower_95 > 0.0
    assert evidence.shuffled_p_value <= 0.02
    assert evidence.fdr_q_value <= 0.05
    assert evidence.positive_month_fraction == 1.0
    assert evidence.positive_symbol_fraction == 1.0
    assert evidence.status == "DEVELOPMENT_REPLICATED"
    assert {row.dimension for row in result.stability_rows} == {"month", "symbol"}
    assert result.control_rows[0].control_name == "within_matched_stratum_shuffled_labels"
    assert len(result.screening_rows) == 1
    assert result.screening_rows[0].status == "SELECTED_FOR_VERIFICATION"


def test_causal_regime_atlas_rejects_null_before_verification_claim() -> None:
    result = build_causal_regime_atlas(_rows(null=True), _config())

    assert result.evidence_rows == ()
    assert result.control_rows[0].candidate_count == 0
    assert result.control_rows[0].replicated_regime_count == 0


def test_causal_regime_atlas_tests_registered_interaction_with_singles() -> None:
    frame = _rows()
    frame["second_feature"] = frame["signal_feature"]
    config = replace(
        _config(),
        axes=(
            RegimeAxisSpec("signal_feature", "fixed", (0.5,), candidate_bins=(1,)),
            RegimeAxisSpec("second_feature", "fixed", (0.5,), candidate_bins=(1,)),
        ),
        interactions=(
            RegimeInteractionSpec(
                interaction_id="registered_pair",
                components=(
                    RegimeInteractionComponent("signal_feature", 1),
                    RegimeInteractionComponent("second_feature", 1),
                ),
                rationale="Synthetic paired mechanism.",
            ),
        ),
    )

    result = build_causal_regime_atlas(frame, config)

    assert {row.hypothesis_kind for row in result.evidence_rows} == {
        "single",
        "interaction",
    }
    pair = next(row for row in result.evidence_rows if row.regime_id == "registered_pair")
    assert pair.component_features == "signal_feature|second_feature"
    assert pair.component_bins == "1|1"
    assert result.frozen_interactions[0].rationale == "Synthetic paired mechanism."


def test_quantile_thresholds_are_frozen_from_discovery_only() -> None:
    original = _rows()
    changed = original.copy()
    changed.loc[
        changed["snapshot_time_ms"] >= VERIFICATION_START,
        "signal_feature",
    ] *= 10_000.0

    first = build_causal_regime_atlas(original, _config(kind="quantile"))
    second = build_causal_regime_atlas(changed, _config(kind="quantile"))

    assert first.frozen_bins == second.frozen_bins
    assert first.frozen_bins[1].lower_bound == pytest.approx(0.5)


def test_regime_atlas_rejects_future_feature_cutoff() -> None:
    frame = _rows()
    frame.loc[0, "feature_cutoff_time_ms"] = frame.loc[0, "snapshot_time_ms"] + 1

    with pytest.raises(RegimeAtlasInputError, match="feature_cutoff_time"):
        build_causal_regime_atlas(frame, _config())


def test_regime_atlas_exact_population_allows_one_row_per_event_variant() -> None:
    first = _rows()
    first["state_ordinal"] = 1
    second = first.copy()
    second["state_ordinal"] = 2
    second["snapshot_time_ms"] += 60_000
    second["feature_cutoff_time_ms"] += 60_000
    second["nature_future_start_time_ms"] += 60_000
    frame = pd.concat((first, second), ignore_index=True)

    result = build_causal_regime_atlas(
        frame,
        replace(
            _config(),
            population_exact_column="state_ordinal",
            population_exact_value=2,
        ),
    )

    assert result.discovery_row_count == 480
    assert result.verification_row_count == 480


def test_pump_fade_declares_coarse_axes_without_core_branching() -> None:
    by_name = {axis.feature_name: axis for axis in PUMP_FADE_REGIME_AXES}

    assert by_name["rel_vol_phase"].kind == "quantile"
    assert by_name["rel_vol_phase"].cuts == (0.90, 0.95, 0.98, 0.99)
    assert by_name["atr_mult"].cuts == (1.0, 2.0, 3.0, 5.0)
    assert by_name["pump_elapsed_min"].cuts == (15.0, 30.0, 60.0, 120.0)
    registered = load_causal_regime_atlas_config(
        Path("research/pump_fade_regime_atlas.json")
    )
    assert registered.axes == PUMP_FADE_REGIME_AXES
    assert registered.interactions == PUMP_FADE_REGIME_INTERACTIONS
    assert registered.protocol_freeze_id.endswith("development-v4")


def test_regime_config_requires_non_overlapping_windows() -> None:
    with pytest.raises(ValueError, match="ordered and disjoint"):
        replace(_config(), verification_start_ms=DISCOVERY_END - 1)


def test_regime_runner_writes_frozen_audit_artifacts(tmp_path: Path) -> None:
    input_path = tmp_path / "nature.parquet"
    out_dir = tmp_path / "atlas"
    _rows().to_parquet(input_path, index=False)

    result = run_causal_regime_atlas(
        input_path=input_path,
        out_dir=out_dir,
        config=_config(),
        allow_dirty_development=True,
    )

    assert result == out_dir
    expected = {
        "regime_discovery_log.csv",
        "regime_stability.csv",
        "regime_controls.csv",
        "regime_screening.csv",
        "frozen_regime_spec.json",
        "holdout_access_log.csv",
        "regime_atlas.metadata.json",
        "regime_atlas.manifest.json",
    }
    assert expected <= {path.name for path in out_dir.iterdir()}
    frozen = json.loads((out_dir / "frozen_regime_spec.json").read_text(encoding="utf-8"))
    assert frozen["protocol_freeze_id"] == "regime-atlas-unit-v1"
    assert frozen["post_hoc_changes_forbidden"] is True
    assert frozen["code_commit"]
    access = pd.read_csv(out_dir / "holdout_access_log.csv")
    assert not bool(access.loc[0, "pristine_holdout_accessed"])


def test_regime_config_loader_is_strict_and_human_readable(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "discovery_start_utc": "2025-01-01T00:00:00Z",
                "discovery_end_utc": "2025-04-01T00:00:00Z",
                "verification_start_utc": "2025-04-01T00:00:00Z",
                "verification_end_utc": "2025-07-01T00:00:00Z",
                "protocol_freeze_id": "loader-v1",
                "activity_match_column": "activity",
                "axes": [
                    {
                        "feature_name": "signal_feature",
                        "kind": "fixed",
                        "cuts": [0.5],
                        "candidate_bins": [1],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_causal_regime_atlas_config(path)

    assert loaded.discovery_start_ms == DISCOVERY_START
    assert loaded.verification_end_ms == VERIFICATION_END
    assert loaded.axes[0].feature_name == "signal_feature"
