from __future__ import annotations

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
