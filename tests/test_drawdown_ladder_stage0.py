from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.future.recovery import RecoveryPathIndex
from anomaly_science.market_context.sessions import MS_PER_MINUTE
from anomaly_science.strategy.drawdown_ladder.stage0 import (
    SymbolStage0OutputPaths,
    build_symbol_stage0,
)
from anomaly_science.strategy.drawdown_ladder.analysis import build_stage0_recovery_summaries
from anomaly_science.strategy.drawdown_ladder.spec import MirroredRallyStage0Spec
from anomaly_science.strategy.drawdown_ladder.mirror_analysis import (
    MirrorComparisonSpec,
    build_mirror_comparison,
)
from anomaly_science.strategy.drawdown_ladder.matched_control import (
    build_symbol_prior_non_drawdown_controls,
)
from anomaly_science.strategy.drawdown_ladder.matched_analysis import (
    MatchedComparisonSpec,
    build_matched_control_comparison,
)


def _paths(root: Path) -> SymbolStage0OutputPaths:
    return SymbolStage0OutputPaths(
        level_candidates=root / "level_candidates.parquet",
        level_outcomes=root / "level_outcomes.parquet",
        ladder_state_candidates=root / "ladder_state_candidates.parquet",
        ladder_state_outcomes=root / "ladder_state_outcomes.parquet",
    )


def _minute_fixture(*, fill_after_activation: bool = True) -> pd.DataFrame:
    start = pd.Timestamp("2025-08-01T07:59:00Z")
    periods = 3 * 24 * 60
    timestamps = (
        start.value // 1_000_000 + np.arange(periods, dtype=np.int64) * MS_PER_MINUTE
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
        }
    )
    first_bar = int(pd.Timestamp("2025-08-01T08:00:00Z").value // 1_000_000)
    first_index = int(np.searchsorted(timestamps, first_bar))
    frame.loc[first_index, "low"] = 89.0
    if fill_after_activation:
        frame.loc[first_index + 1, "low"] = 94.90
        frame.loc[first_index + 2, ["open", "high", "low", "close"]] = [95.0, 95.10, 94.8, 95.0]
        frame.loc[first_index + 3, ["open", "high", "low", "close"]] = [95.0, 96.00, 94.8, 96.0]
    return frame


def _rally_fixture() -> pd.DataFrame:
    frame = _minute_fixture(fill_after_activation=False)
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64)
    first_bar = int(pd.Timestamp("2025-08-01T08:00:00Z").value // 1_000_000)
    first_index = int(np.searchsorted(timestamps, first_bar))
    frame.loc[first_index, ["open", "high", "low", "close"]] = [100.0, 111.0, 99.0, 100.0]
    frame.loc[first_index + 1, ["open", "high", "low", "close"]] = [100.0, 105.10, 99.8, 105.0]
    frame.loc[first_index + 2, ["open", "high", "low", "close"]] = [105.0, 105.20, 104.8, 105.0]
    frame.loc[first_index + 3, ["open", "high", "low", "close"]] = [105.0, 105.10, 104.0, 104.2]
    return frame


def _matched_control_fixture() -> pd.DataFrame:
    start = pd.Timestamp("2025-07-01T07:59:00Z")
    periods = 45 * 24 * 60
    timestamps = (
        start.value // 1_000_000 + np.arange(periods, dtype=np.int64) * MS_PER_MINUTE
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "quote_volume": 1_000.0,
        }
    )
    for day in pd.date_range("2025-07-02", "2025-08-07", freq="D", tz="UTC"):
        bar_time = int((day + pd.Timedelta(hours=8, minutes=1)).value // 1_000_000)
        index = int(np.searchsorted(timestamps, bar_time))
        frame.loc[index, ["open", "high", "low", "close"]] = [100.0, 105.1, 100.0, 105.0]
        frame.loc[index + 1, ["open", "high", "low", "close"]] = [105.0, 105.0, 100.0, 100.0]
    signal_time = int(pd.Timestamp("2025-08-10T08:01:00Z").value // 1_000_000)
    signal_index = int(np.searchsorted(timestamps, signal_time))
    frame.loc[signal_index, ["open", "high", "low", "close"]] = [100.0, 100.0, 94.9, 95.0]
    frame.loc[signal_index + 1, ["open", "high", "low", "close"]] = [95.0, 95.1, 94.8, 95.0]
    frame.loc[signal_index + 2, ["open", "high", "low", "close"]] = [95.0, 96.0, 94.8, 96.0]
    return frame


def test_recovery_path_starts_after_snapshot_and_reports_time_to_break_even() -> None:
    timestamps = np.arange(4, dtype=np.int64) * MS_PER_MINUTE
    index = RecoveryPathIndex(
        timestamps_ms=timestamps,
        high=np.asarray([100.0, 99.0, 100.2, 101.0]),
        low=np.asarray([99.0, 98.0, 99.0, 100.0]),
        close=np.asarray([99.5, 98.5, 100.0, 100.5]),
    )

    measured = index.measure(
        entry_price=100.0,
        snapshot_time_ms=MS_PER_MINUTE,
        first_future_bar_index=1,
        maximum_horizon_minutes=3,
        horizons_minutes=(1, 3),
        round_trip_cost_bps=(10, 25),
    )

    assert measured.future_start_time_ms == 2 * MS_PER_MINUTE
    assert measured.time_to_gross_break_even_minutes == 2
    assert measured.cost_break_even_times_minutes == (2, 3)
    assert measured.horizon_metrics[0].close_return == pytest.approx(-0.015)
    assert measured.horizon_complete


def test_short_recovery_uses_future_lows_and_reports_signed_returns() -> None:
    timestamps = np.arange(4, dtype=np.int64) * MS_PER_MINUTE
    index = RecoveryPathIndex(
        timestamps_ms=timestamps,
        high=np.asarray([101.0, 102.0, 101.0, 100.5]),
        low=np.asarray([100.0, 100.5, 99.8, 99.7]),
        close=np.asarray([100.5, 101.5, 100.0, 99.5]),
    )

    measured = index.measure(
        entry_price=100.0,
        snapshot_time_ms=MS_PER_MINUTE,
        first_future_bar_index=1,
        maximum_horizon_minutes=3,
        horizons_minutes=(1, 3),
        round_trip_cost_bps=(10, 25),
        direction="short",
    )

    assert measured.direction == "short"
    assert measured.time_to_gross_break_even_minutes == 2
    assert measured.cost_break_even_times_minutes == (2, 3)
    assert measured.horizon_metrics[0].close_return == pytest.approx(-0.015)
    assert measured.horizon_metrics[-1].maximum_return == pytest.approx(0.003)
    assert measured.horizon_metrics[-1].minimum_return == pytest.approx(-0.02)


def test_mirrored_rally_builds_causal_short_level_and_grid_states(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    _rally_fixture().to_parquet(source, index=False)
    output = _paths(tmp_path / "mirror")

    stats = build_symbol_stage0(
        source,
        shard_paths=output,
        spec=MirroredRallyStage0Spec(),
    )
    candidates = pd.read_parquet(output.level_candidates)
    outcomes = pd.read_parquet(output.level_outcomes)
    states = pd.read_parquet(output.ladder_state_candidates)

    five = candidates.loc[candidates["level_depth_pct"].eq(5)].iloc[0]
    five_outcome = outcomes.loc[outcomes["candidate_id"].eq(five["candidate_id"])].iloc[0]
    grid_five = states.loc[
        states["grid_step_pct"].eq(5)
        & states["deepest_filled_level_pct"].eq(5)
    ].iloc[0]

    assert stats.parent_event_count == 1
    assert set(candidates.loc[candidates["level_depth_pct"].le(5), "level_depth_pct"]) == {3, 4, 5}
    assert "same_bar_max_rally_pct" in candidates.columns
    assert "same_bar_max_drawdown_pct" not in candidates.columns
    assert five["limit_price"] == 105.0
    assert five_outcome["time_to_gross_break_even_minutes"] == 1
    assert five_outcome["time_to_break_even_25bps_minutes"] == 2
    assert grid_five["equal_notional_average_entry_price"] == pytest.approx(105.0)
    assert five_outcome["future_return_5m"] > 0.0


def test_mirrored_candidates_do_not_change_when_only_future_tail_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    original = _rally_fixture()
    original.to_parquet(source, index=False)
    first_output = _paths(tmp_path / "first_mirror")
    spec = MirroredRallyStage0Spec()
    build_symbol_stage0(source, shard_paths=first_output, spec=spec)

    changed = original.copy()
    mutation_time = int(pd.Timestamp("2025-08-01T08:02:00Z").value // 1_000_000)
    changed.loc[changed["timestamp"].eq(mutation_time), "low"] = 1.0
    changed.to_parquet(source, index=False)
    second_output = _paths(tmp_path / "second_mirror")
    build_symbol_stage0(source, shard_paths=second_output, spec=spec)

    pd.testing.assert_frame_equal(
        pd.read_parquet(first_output.level_candidates),
        pd.read_parquet(second_output.level_candidates),
    )
    assert not pd.read_parquet(first_output.level_outcomes).equals(
        pd.read_parquet(second_output.level_outcomes)
    )


def test_first_session_minute_is_not_eligible_for_fill(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    _minute_fixture(fill_after_activation=False).to_parquet(source, index=False)
    output = _paths(tmp_path / "out")

    stats = build_symbol_stage0(source, shard_paths=output)

    assert stats.level_candidate_count == 0
    assert pd.read_parquet(output.level_candidates).empty
    assert pd.read_parquet(output.level_outcomes).empty


def test_level_and_equal_notional_state_have_causal_recovery_rows(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    _minute_fixture().to_parquet(source, index=False)
    output = _paths(tmp_path / "out")

    stats = build_symbol_stage0(source, shard_paths=output)
    candidates = pd.read_parquet(output.level_candidates)
    outcomes = pd.read_parquet(output.level_outcomes)
    states = pd.read_parquet(output.ladder_state_candidates)
    state_outcomes = pd.read_parquet(output.ladder_state_outcomes)

    five = candidates.loc[candidates["level_depth_pct"].eq(5)].iloc[0]
    five_outcome = outcomes.loc[outcomes["candidate_id"].eq(five["candidate_id"])].iloc[0]
    grid_five = states.loc[
        states["grid_step_pct"].eq(5)
        & states["deepest_filled_level_pct"].eq(5)
    ].iloc[0]
    grid_five_outcome = state_outcomes.loc[
        state_outcomes["candidate_id"].eq(grid_five["candidate_id"])
    ].iloc[0]

    assert stats.parent_event_count == 1
    assert set(candidates.loc[candidates["level_depth_pct"].le(5), "level_depth_pct"]) == {3, 4, 5}
    assert candidates.loc[candidates["level_depth_pct"].le(5), "parent_event_id"].nunique() == 1
    assert five["feature_cutoff_time_ms"] < five["snapshot_time_ms"]
    assert five_outcome["future_start_time_ms"] > five_outcome["snapshot_time_ms"]
    assert bool(five_outcome["gross_break_even_reached"])
    assert five_outcome["time_to_gross_break_even_minutes"] == 1
    assert bool(five_outcome["break_even_10bps_reached"])
    assert five_outcome["time_to_break_even_25bps_minutes"] == 2
    assert grid_five["filled_level_count"] == 1
    assert grid_five["equal_notional_average_entry_price"] == 95.0
    assert grid_five_outcome["time_to_gross_break_even_minutes"] == 1
    assert not candidates["untouched_2026_row_used"].any()
    assert not outcomes["untouched_2026_row_used"].any()


def test_candidate_membership_is_invariant_to_future_tail_but_outcomes_change(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    original = _minute_fixture()
    original.to_parquet(source, index=False)
    first_output = _paths(tmp_path / "first")
    build_symbol_stage0(source, shard_paths=first_output)

    changed = original.copy()
    mutation_time = int(pd.Timestamp("2025-08-01T08:02:00Z").value // 1_000_000)
    changed.loc[changed["timestamp"].eq(mutation_time), "high"] = 200.0
    changed.to_parquet(source, index=False)
    second_output = _paths(tmp_path / "second")
    build_symbol_stage0(source, shard_paths=second_output)

    first_candidates = pd.read_parquet(first_output.level_candidates)
    second_candidates = pd.read_parquet(second_output.level_candidates)
    pd.testing.assert_frame_equal(first_candidates, second_candidates)
    first_outcomes = pd.read_parquet(first_output.level_outcomes)
    second_outcomes = pd.read_parquet(second_output.level_outcomes)
    assert not first_outcomes.equals(second_outcomes)


def test_oos_tail_is_physically_ignored(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    base = _minute_fixture()
    base.to_parquet(source, index=False)
    first_output = _paths(tmp_path / "first")
    build_symbol_stage0(source, shard_paths=first_output)

    oos = base.iloc[:10].copy()
    oos["timestamp"] = int(pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000) + np.arange(10) * MS_PER_MINUTE
    oos[["open", "high", "low", "close"]] = 1_000_000.0
    pd.concat([base, oos], ignore_index=True).to_parquet(source, index=False)
    second_output = _paths(tmp_path / "second")
    build_symbol_stage0(source, shard_paths=second_output)

    for left, right in (
        (first_output.level_candidates, second_output.level_candidates),
        (first_output.level_outcomes, second_output.level_outcomes),
        (first_output.ladder_state_candidates, second_output.ladder_state_candidates),
        (first_output.ladder_state_outcomes, second_output.ladder_state_outcomes),
    ):
        pd.testing.assert_frame_equal(pd.read_parquet(left), pd.read_parquet(right))


def test_recovery_summary_is_censor_aware_and_parent_clustered(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    _minute_fixture().to_parquet(source, index=False)
    output = _paths(tmp_path / "stage0")
    build_symbol_stage0(source, shard_paths=output)

    level_path, _, state_path = build_stage0_recovery_summaries(stage0_dir=tmp_path / "stage0")
    levels = pd.read_parquet(level_path)
    states = pd.read_parquet(state_path)
    five = levels.loc[levels["level_depth_pct"].eq(5)].iloc[0]

    assert five["unique_parent_event_count"] == 1
    assert bool(five["descriptive_only"])
    assert bool(five["parent_event_cluster_required"])
    assert five["km_gross_recovery_probability_60m"] == 1.0
    assert five["km_median_minutes_to_gross_recovery"] == 1.0
    assert set(states["summary_family"]) == {"equal_notional_ladder_state"}


def _write_comparison_arm(
    root: Path,
    *,
    prefix: str,
    recovered: list[bool],
    returns: list[float],
) -> None:
    root.mkdir(parents=True)
    count = len(recovered)
    candidates = pd.DataFrame(
        {
            "candidate_id": [f"{prefix}-{index}" for index in range(count)],
            "parent_event_id": [f"parent-{prefix}-{index}" for index in range(count)],
            "symbol": [f"S{index % 4}" for index in range(count)],
            "snapshot_time_ms": [
                int(pd.Timestamp("2025-08-04T08:00:00Z").value // 1_000_000)
                + index * 7 * 24 * 60 * MS_PER_MINUTE
                for index in range(count)
            ],
            "grid_step_pct": 3,
            "deepest_filled_level_pct": 6,
        }
    )
    outcomes = pd.DataFrame(
        {
            "candidate_id": candidates["candidate_id"],
            "horizon_complete": True,
            "break_even_25bps_reached": recovered,
            "future_return_2880m": returns,
        }
    )
    candidates.to_parquet(root / "ladder_state_candidates.parquet", index=False)
    outcomes.to_parquet(root / "ladder_state_outcomes.parquet", index=False)
    protocol = (
        "mirrored_rally_stage0_20260802_v1"
        if prefix == "short"
        else "drawdown_ladder_stage0_20260802_v1"
    )
    (root / "temporal_audit.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "protocol_freeze_id": protocol,
                "checks": {
                    "ladder_state": {"untouched_2026_rows_used": 0},
                },
            }
        ),
        encoding="utf-8",
    )


def test_mirror_comparison_uses_frozen_strata_cluster_bootstrap_and_holm(
    tmp_path: Path,
) -> None:
    long_root = tmp_path / "long"
    short_root = tmp_path / "short"
    _write_comparison_arm(
        long_root,
        prefix="long",
        recovered=[True] * 8,
        returns=[0.10] * 8,
    )
    _write_comparison_arm(
        short_root,
        prefix="short",
        recovered=[False] * 8,
        returns=[-0.10] * 8,
    )

    report = build_mirror_comparison(
        long_stage0_dir=long_root,
        mirror_stage0_dir=short_root,
        output_dir=tmp_path / "comparison",
        spec=MirrorComparisonSpec(
            primary_strata=((3, 6),),
            bootstrap_iterations=100,
            minimum_rows_per_arm=2,
            minimum_clusters_per_arm=2,
            minimum_month_rows_per_arm=1,
        ),
    )
    exact = pd.read_parquet(tmp_path / "comparison" / "mirror_exact_strata.parquet")
    row = exact.iloc[0]

    assert report.is_file()
    assert bool(row["primary_stratum"])
    assert bool(row["eligible_for_inference"])
    assert row["long_minus_mirror_recovery_25bps"] == 1.0
    assert row["recovery_25bps_cluster_ci_lower_95"] == 1.0
    assert row["long_minus_mirror_signed_return_48h"] == pytest.approx(0.2)
    assert row["primary_recovery_holm_p"] < 0.05


def test_prior_non_drawdown_control_is_causal_resolved_and_outcome_blind(
    tmp_path: Path,
) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    original = _matched_control_fixture()
    original.to_parquet(source, index=False)
    signal_paths = _paths(tmp_path / "signal")
    build_symbol_stage0(source, shard_paths=signal_paths)
    control_candidates = tmp_path / "control" / "candidates.parquet"
    control_outcomes = tmp_path / "control" / "outcomes.parquet"

    stats = build_symbol_prior_non_drawdown_controls(
        source_path=source,
        signal_candidates_path=signal_paths.ladder_state_candidates,
        output_candidates_path=control_candidates,
        output_outcomes_path=control_outcomes,
    )
    candidates = pd.read_parquet(control_candidates)
    outcomes = pd.read_parquet(control_outcomes)
    five = candidates.loc[
        candidates["grid_step_pct"].eq(5)
        & candidates["deepest_filled_level_pct"].eq(5)
    ].iloc[0]

    assert stats.matched_rows > 0
    assert bool(five["control_is_strictly_prior"])
    assert bool(five["control_outcome_resolved_before_signal"])
    assert bool(five["control_has_no_3pct_drawdown"])
    assert five["control_snapshot_time_ms"] + 2880 * MS_PER_MINUTE <= five["signal_snapshot_time_ms"]
    assert outcomes["future_start_time_ms"].gt(outcomes["control_snapshot_time_ms"]).all()
    assert not candidates["untouched_2026_row_used"].any()

    mutated = original.copy()
    after_signal = int(pd.Timestamp("2025-08-10T08:03:00Z").value // 1_000_000)
    mutated.loc[mutated["timestamp"].ge(after_signal), ["high", "close"]] = 1_000.0
    mutated.to_parquet(source, index=False)
    second_candidates = tmp_path / "control2" / "candidates.parquet"
    second_outcomes = tmp_path / "control2" / "outcomes.parquet"
    build_symbol_prior_non_drawdown_controls(
        source_path=source,
        signal_candidates_path=signal_paths.ladder_state_candidates,
        output_candidates_path=second_candidates,
        output_outcomes_path=second_outcomes,
    )
    pd.testing.assert_frame_equal(candidates, pd.read_parquet(second_candidates))
    pd.testing.assert_frame_equal(outcomes, pd.read_parquet(second_outcomes))


def test_matched_comparison_is_paired_clustered_and_multiplicity_adjusted(
    tmp_path: Path,
) -> None:
    long_root = tmp_path / "long_pair"
    _write_comparison_arm(
        long_root,
        prefix="signal",
        recovered=[True] * 8,
        returns=[0.10] * 8,
    )
    signal = pd.read_parquet(long_root / "ladder_state_candidates.parquet")
    control_root = tmp_path / "control_pair"
    control_root.mkdir()
    controls = pd.DataFrame(
        {
            "pair_id": [f"pair-{index}" for index in range(8)],
            "signal_candidate_id": signal["candidate_id"],
            "symbol": signal["symbol"],
            "grid_step_pct": 3,
            "deepest_filled_level_pct": 6,
            "signal_snapshot_time_ms": signal["snapshot_time_ms"],
            "control_snapshot_time_ms": signal["snapshot_time_ms"] - 7 * 24 * 60 * MS_PER_MINUTE,
            "match_score": 0.1,
            "realized_vol_log_distance": 0.05,
            "quote_volume_log_distance": 0.05,
            "abs_return_distance": 0.001,
        }
    )
    control_outcomes = pd.DataFrame(
        {
            "pair_id": controls["pair_id"],
            "horizon_complete": True,
            "break_even_25bps_reached": False,
            "future_return_2880m": -0.10,
        }
    )
    controls.to_parquet(control_root / "matched_candidates.parquet", index=False)
    control_outcomes.to_parquet(control_root / "matched_outcomes.parquet", index=False)
    (control_root / "temporal_audit.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "protocol_freeze_id": "prior_non_drawdown_control_20260802_v1",
                "untouched_2026_rows_used": 0,
            }
        ),
        encoding="utf-8",
    )

    report = build_matched_control_comparison(
        long_stage0_dir=long_root,
        control_dir=control_root,
        output_dir=tmp_path / "paired_result",
        spec=MatchedComparisonSpec(
            primary_strata=((3, 6),),
            bootstrap_iterations=100,
            minimum_matched_rows=2,
            minimum_coverage_fraction=0.5,
            minimum_week_clusters=2,
            minimum_symbol_clusters=2,
            minimum_month_pairs=1,
        ),
    )
    exact = pd.read_parquet(tmp_path / "paired_result" / "matched_exact_strata.parquet")
    row = exact.iloc[0]

    assert report.is_file()
    assert bool(row["eligible_for_inference"])
    assert row["matched_coverage"] == 1.0
    assert row["paired_recovery_25bps_delta"] == 1.0
    assert row["recovery_25bps_week_ci_lower_95"] == 1.0
    assert row["recovery_25bps_symbol_ci_lower_95"] == 1.0
    assert row["primary_recovery_conservative_holm_p"] < 0.05
